"""The sword policy: a small network from what a client sees (plus the keys it just pressed) to key
presses, and the checkpoint format its weights are stored in.

Input: the env's 37 observation numbers plus the policy's own last two actions. It needs those
because its observations are 200 ms old; a player also knows which keys they're holding.
Output: one choice per control (forward/back, left/right, jump, sprint, sneak, attack, turn, look),
and a value estimate (expected future reward) used by PPO.
"""

from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.distributions import Categorical

from .env import ACTION_NVEC, OBS_LAYOUT, OBS_SIZES, SELF_FEATURES

CHECKPOINT_FORMAT = 2
ACTION_MEMORY = 2
ACTION_ONE_HOT = sum(ACTION_NVEC)


def input_size(layout=OBS_LAYOUT):
    return OBS_SIZES[layout] + ACTION_MEMORY * ACTION_ONE_HOT


INPUT_SIZE = input_size()
# No keys, no turn (index 4 of YAW_BINS), no look (index 2 of PITCH_BINS).
IDLE_ACTION = np.array([0, 0, 0, 0, 0, 0, 4, 2], dtype=np.int64)
_OFFSETS = np.concatenate([[0], np.cumsum(ACTION_NVEC)[:-1]])


class ActionMemory:
    """The last ACTION_MEMORY actions of each slot (one slot = one side of one arena)."""

    def __init__(self, slots):
        self.past = np.tile(IDLE_ACTION, (slots, ACTION_MEMORY, 1))

    def reset(self, slot):
        self.past[slot] = IDLE_ACTION

    def push(self, actions):
        self.past = np.concatenate([actions[:, None, :], self.past[:, :-1]], axis=1)

    def features(self, obs, rows=None):
        """obs [slots, obs size] → network input [slots, obs size + action memory]. rows: which
        slots' memories `obs` belongs to (default: all, in order)."""
        slots = obs.shape[0]
        past = self.past if rows is None else self.past[rows]
        one_hot = np.zeros((slots, ACTION_MEMORY, ACTION_ONE_HOT), dtype=np.float32)
        idx = past + _OFFSETS
        np.put_along_axis(one_hot, idx, 1.0, axis=2)
        return np.concatenate([obs, one_hot.reshape(slots, -1)], axis=1)


def _layer(n_in, n_out, std=np.sqrt(2)):
    layer = nn.Linear(n_in, n_out)
    nn.init.orthogonal_(layer.weight, std)
    nn.init.constant_(layer.bias, 0.0)
    return layer


class SwordPolicy(nn.Module):
    """Separate actor and critic MLPs (as in CleanRL's PPO)."""

    def __init__(self, hidden=256, obs_layout=OBS_LAYOUT):
        super().__init__()
        self.hidden = hidden
        self.obs_layout = obs_layout
        self.input_size = input_size(obs_layout)
        n = self.input_size
        self.actor = nn.Sequential(_layer(n, hidden), nn.Tanh(), _layer(hidden, hidden), nn.Tanh(),
                                   _layer(hidden, ACTION_ONE_HOT, std=0.01))
        self.critic = nn.Sequential(_layer(n, hidden), nn.Tanh(), _layer(hidden, hidden), nn.Tanh(),
                                    _layer(hidden, 1, std=1.0))

    def forward(self, x):
        """Logits of every control, concatenated (what MLflow's saved model returns)."""
        return self.actor(x)

    def value(self, x):
        return self.critic(x).squeeze(-1)

    def head_entropy(self, x):
        """Entropy of each control's choice [n, 8], to keep any one control from dying out."""
        logits = torch.split(self.actor(x), ACTION_NVEC, dim=-1)
        return torch.stack([Categorical(logits=l).entropy() for l in logits], dim=-1)

    def act(self, x, action=None, greedy=False):
        """Returns (action [n, 8], log-probability, entropy, value)."""
        return (*_choose(self.actor(x), action, greedy), self.value(x))


def _choose(logits, action=None, greedy=False):
    """Logits [..., 28] → (action [..., 8], log-probability, entropy): one choice per control."""
    parts = torch.split(logits, ACTION_NVEC, dim=-1)
    dists = [Categorical(logits=l) for l in parts]
    if action is None:
        if greedy:
            action = torch.stack([l.argmax(-1) for l in parts], dim=-1)
        else:
            action = torch.stack([d.sample() for d in dists], dim=-1)
    logp = torch.stack([d.log_prob(action[..., k]) for k, d in enumerate(dists)], dim=-1).sum(-1)
    entropy = torch.stack([d.entropy() for d in dists], dim=-1).sum(-1)
    return action, logp, entropy


class RecurrentSwordPolicy(nn.Module):
    """The sword policy with memory: the same inputs, then an LSTM that carries a running summary of
    the fight, so it can read the opponent (how they space, when they swing, whether they punish) and
    change its play within a fight, instead of playing one fixed strategy against everyone.

    Three outputs from the memory: the action, the value, and a prediction of the opponent's action
    this step (an auxiliary task, trained on what they actually did, which makes the memory track
    the opponent; how well it predicts late in a fight vs early shows whether it reads them)."""

    recurrent = True

    def __init__(self, hidden=256, obs_layout=OBS_LAYOUT):
        super().__init__()
        self.hidden = hidden
        self.obs_layout = obs_layout
        self.input_size = input_size(obs_layout)
        self.encoder = nn.Sequential(_layer(self.input_size, hidden), nn.Tanh())
        self.lstm = nn.LSTM(hidden, hidden)
        for name, param in self.lstm.named_parameters():
            if "bias" in name:
                nn.init.constant_(param, 0.0)
            else:
                nn.init.orthogonal_(param, 1.0)
        self.actor_head = _layer(hidden, ACTION_ONE_HOT, std=0.01)
        self.critic_head = _layer(hidden, 1, std=1.0)
        self.opponent_head = _layer(hidden, ACTION_ONE_HOT, std=0.01)

    def initial_state(self, n):
        return (torch.zeros(1, n, self.hidden), torch.zeros(1, n, self.hidden))

    def core(self, x, state, done):
        """x [T, n, in], done [T, n] (1 = a new fight starts at this step: memory cleared first)
        → memory outputs [T, n, hidden] and the state after the last step."""
        h = self.encoder(x)
        # Run each stretch between fight starts in one LSTM call (same result as step by step, far
        # fewer calls): split only where some fighter's memory must be cleared.
        cuts = [0] + [t for t in range(1, x.shape[0]) if bool(done[t].any())] + [x.shape[0]]
        outs = []
        for a, b in zip(cuts[:-1], cuts[1:]):
            keep = (1.0 - done[a]).view(1, -1, 1)
            out, state = self.lstm(h[a:b], (state[0] * keep, state[1] * keep))
            outs.append(out)
        return torch.cat(outs), state

    def evaluate(self, x, state, done):
        """A whole sequence → (action logits, values, opponent-prediction logits), each [T, n, …]."""
        out, _ = self.core(x, state, done)
        return self.actor_head(out), self.critic_head(out).squeeze(-1), self.opponent_head(out)

    def step(self, x, state, done, greedy=False):
        """One decision for n fighters: x [n, in], done [n] → (action, log-prob, value, new state)."""
        out, state = self.core(x.unsqueeze(0), state, done.unsqueeze(0))
        out = out[0]
        action, logp, _ = _choose(self.actor_head(out), None, greedy)
        return action, logp, self.critic_head(out).squeeze(-1), state

    def value_of(self, x, state, done):
        out, _ = self.core(x.unsqueeze(0), state, done.unsqueeze(0))
        return self.critic_head(out[0]).squeeze(-1)

    def forward(self, x):
        """Action logits for single moments from an empty memory (what MLflow's saved model returns)."""
        out, _ = self.core(x.unsqueeze(0), self.initial_state(x.shape[0]), torch.zeros(1, x.shape[0]))
        return self.actor_head(out[0])

    def actor(self, x):
        return self.forward(x)


class _Disabled:
    """Forward hook on the action logits: options of disabled controls are never chosen (a key the
    bot doesn't have). A hook, not new weights, so checkpoints and MLflow models stay as they are."""

    def __init__(self, mask):
        self.mask = mask

    def __call__(self, module, inputs, output):
        return output + self.mask


def disable(policy, controls):
    """Takes keys away: each listed control is held at option 0 (off). Sneak (4) is almost never right
    in a sword fight, and the bots kept pressing it because it rarely decides a fight."""
    controls = sorted(set(controls))
    if not controls or getattr(policy, "disabled", []) == controls:
        return policy
    mask = torch.zeros(ACTION_ONE_HOT)
    start = 0
    for k, n in enumerate(ACTION_NVEC):
        if k in controls:
            mask[start + 1:start + n] = -1e9
        start += n
    module = policy.actor_head if getattr(policy, "recurrent", False) else policy.actor
    module.register_forward_hook(_Disabled(mask))
    policy.disabled = controls
    return policy


def save(path, policy, **extra):
    """A checkpoint: weights plus whatever the trainer needs to resume (optimizer state, counters)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    torch.save({"format": CHECKPOINT_FORMAT, "hidden": policy.hidden, "input_size": policy.input_size,
                "obs_layout": policy.obs_layout, "arch": "lstm" if getattr(policy, "recurrent", False) else "mlp",
                "action_nvec": list(ACTION_NVEC), "model": policy.state_dict(),
                "disabled": list(getattr(policy, "disabled", [])), **extra}, tmp)
    tmp.replace(path)   # never leave a half-written checkpoint


def load(path):
    """→ (policy, checkpoint dict)."""
    ckpt = torch.load(path, map_location="cpu", weights_only=True)
    layout = ckpt.get("obs_layout", 1)          # format-1 checkpoints are all layout 1
    if ckpt["input_size"] != input_size(layout) or ckpt["action_nvec"] != list(ACTION_NVEC):
        raise ValueError(f"{path} was trained for a different observation or action layout")
    cls = RecurrentSwordPolicy if ckpt.get("arch") == "lstm" else SwordPolicy
    policy = cls(hidden=ckpt["hidden"], obs_layout=layout)
    policy.load_state_dict(ckpt["model"])
    disable(policy, ckpt.get("disabled", []))
    policy.eval()
    return policy, ckpt


def upgrade(policy, layout=OBS_LAYOUT):
    """A copy of `policy` for a newer observation layout that plays exactly as before at first: the
    first layers' weights for the new inputs start at zero, and the action-memory columns move to
    their new place. (Layout 2 keeps layout 1's numbers in the same order, own state now live, and
    appends 3.) Training then learns to use the new inputs."""
    if policy.obs_layout == layout:
        return policy
    assert (policy.obs_layout, layout) == (1, 2), "only layout 1 → 2 is known"
    new = SwordPolicy(hidden=policy.hidden, obs_layout=layout)
    new.load_state_dict({k: v for k, v in policy.state_dict().items() if not k.endswith("0.weight")}, strict=False)
    old_obs, new_obs = OBS_SIZES[policy.obs_layout], OBS_SIZES[layout]
    with torch.no_grad():
        for old_net, new_net in ((policy.actor, new.actor), (policy.critic, new.critic)):
            w_old, w_new = old_net[0].weight, new_net[0].weight
            w_new.zero_()
            w_new[:, :old_obs] = w_old[:, :old_obs]
            w_new[:, new_obs:] = w_old[:, old_obs:]
    return new


class PolicyFighter:
    """A trained policy as a fighter for one side of an env, e.g. to watch or evaluate it."""

    def __init__(self, path, greedy=False):
        self.policy, _ = load(path)
        self.obs_layout = self.policy.obs_layout   # ask the env for this layout: env.observation(i, layout)
        self.greedy = greedy
        self.reset()

    def reset(self):
        """A new fight: forget the last one."""
        self.memory = ActionMemory(1)
        self.state = self.policy.initial_state(1) if getattr(self.policy, "recurrent", False) else None

    @torch.no_grad()
    def __call__(self, obs):
        x = torch.from_numpy(self.memory.features(obs[None, :]))
        if self.state is not None:
            action, _, _, self.state = self.policy.step(x, self.state, torch.zeros(1), greedy=self.greedy)
        else:
            action, *_ = self.policy.act(x, greedy=self.greedy)
        action = action.numpy()
        self.memory.push(action)
        return action[0]
