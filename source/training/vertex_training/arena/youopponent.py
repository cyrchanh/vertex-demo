"""The you-model as a league opponent: it plays as the person would, deciding from their view of
the fight, the bot's last action and its memory of the fight so far (youmodel.YouModel).

Reading the person is only worth something to the bot if it practises against someone who plays
like them; against bots, a memory that knows the person's habits earns nothing. With this opponent
in the league, reading the person and answering it (backing off when they're charged, punishing
their weak swings) can pay off in training.

  build(exclude)   trains a you-model on every duel except `exclude` (kept for the reading test)
                   with youmodel.train, saved as selfplay/you-model-league.pt
  YouOpponent      plays it one decision at a time, sampling its predicted keys
"""

import numpy as np
import torch

from . import play
from . import policy as pol
from . import youmodel

LEAGUE_FILE = play.SELFPLAY_DIR / "you-model-league.pt"


def build(exclude=(), base="champion", log=print):
    """→ path of a you-model trained on every duel with a trace except those in `exclude`."""
    base_policy, _ = pol.load(play.resolve(base))
    fights = [f for f in youmodel.load_fights(base_policy.obs_layout)
              if not any(stamp in f["duel"] for stamp in exclude)]
    duels = sorted({f["duel"] for f in fights})
    model, step = youmodel.train(base_policy, fights, memory=True, context=True)
    torch.save({"format": "you-model-1", "hidden": model.hidden, "obs_layout": model.obs_layout,
                "base_input": model.base_input, "base": str(play.resolve(base)), "duels": duels,
                "model": model.state_dict()}, LEAGUE_FILE)
    log(f"you-model for the league: {len(fights)} fights from {len(duels)} duels (left out: "
        f"{', '.join(exclude) or 'none'}), step {step}, saved as {LEAGUE_FILE.name}")
    return LEAGUE_FILE


class YouOpponent:
    """One fighter playing the you-model. Its input row is the same as a policy's (the person's
    observation with their own last actions); it also needs the other fighter's last action."""

    recurrent = True

    def __init__(self, path):
        ckpt = torch.load(path, map_location="cpu", weights_only=True)
        base = pol.RecurrentSwordPolicy(hidden=ckpt["hidden"], obs_layout=ckpt["obs_layout"])
        self.model = youmodel.YouModel(base)
        self.model.load_state_dict(ckpt["model"])
        self.model.eval()
        self.obs_layout = ckpt["obs_layout"]
        self.input_size = ckpt["base_input"]

    def initial_state(self, n):
        return (torch.zeros(1, n, self.model.hidden), torch.zeros(1, n, self.model.hidden))

    @torch.no_grad()
    def step(self, x, state, other_last_action):
        """x [n, in] (numpy or tensor), other_last_action [n, 8] ints → (action [n, 8], new state)."""
        x = torch.as_tensor(x, dtype=torch.float32)
        other = np.zeros((len(x), pol.ACTION_ONE_HOT), np.float32)
        np.put_along_axis(other, np.asarray(other_last_action) + pol._OFFSETS, 1.0, axis=1)
        h = self.model.encoder(torch.cat([x, torch.from_numpy(other)], -1))
        out, state = self.model.lstm(h[None], state)
        action, _, _ = pol._choose(self.model.you_head(out[0]))
        return action.numpy(), state


# ---- the you-model as a trainable policy ----

PRIOR_FILE = play.SELFPLAY_DIR / "you-prior.pt"


def build_prior(exclude=(), base="champion", log=print):
    """A you-model without the other fighter's action as input (memory only), so it is an ordinary
    RecurrentSwordPolicy: same inputs, same memory. Its actor head plays like the person; its
    opponent head predicts the bot; the critic starts from the base policy's. Saved as
    selfplay/you-prior.pt: the frozen prior a stronger you-like fighter is trained against."""
    base_policy, _ = pol.load(play.resolve(base))
    fights = [f for f in youmodel.load_fights(base_policy.obs_layout)
              if not any(stamp in f["duel"] for stamp in exclude)]
    model, step = youmodel.train(base_policy, fights, memory=True, context=False)
    prior = pol.RecurrentSwordPolicy(hidden=base_policy.hidden, obs_layout=base_policy.obs_layout)
    state = prior.state_dict()
    with torch.no_grad():
        state["encoder.0.weight"] = model.encoder[0].weight[:, :base_policy.input_size].clone()
        state["encoder.0.bias"] = model.encoder[0].bias.clone()
        for name, part in (("lstm", model.lstm), ("actor_head", model.you_head),
                           ("opponent_head", model.champ_head), ("critic_head", base_policy.critic_head)):
            for k, v in part.state_dict().items():
                state[f"{name}.{k}"] = v.clone()
    prior.load_state_dict(state)
    pol.save(PRIOR_FILE, prior)
    log(f"you-prior: {len(fights)} fights, step {step}, saved as {PRIOR_FILE.name}")
    return PRIOR_FILE


def style_fights(stamps, layout):
    """The person's decisions in these duels, from their side (inputs and actions)."""
    return [f for f in youmodel.load_fights(layout) if any(stamp in f["duel"] for stamp in stamps)]


@torch.no_grad()
def style_score(policy, fights):
    """How much a policy plays like the person on their own decisions: → (NLL per decision summed
    over the 8 controls, share of movement and attack choices where its likeliest one is theirs).
    Controls the policy doesn't have (policy.disable) are left out of both."""
    keep = [k for k in range(len(pol.ACTION_NVEC)) if k not in getattr(policy, "disabled", [])]
    nlls, same = [], []
    for f in fights:
        x = torch.from_numpy(f["x"])[:, None]
        out, _ = policy.core(x, policy.initial_state(1), torch.zeros(len(x), 1))
        logits = policy.actor_head(out[:, 0])
        a = torch.from_numpy(f["you"])
        nlls.append(youmodel.nll(logits, a)[:, keep].sum(-1).numpy())
        pred = torch.stack([p.argmax(-1) for p in torch.split(logits, pol.ACTION_NVEC, -1)], -1).numpy()
        moves = [k for k in keep if k < 6]
        same.append((pred[:, moves] == f["you"][:, moves]).mean(-1))
    return float(np.concatenate(nlls).mean()), float(np.concatenate(same).mean())
