"""Kickstarting: before reinforcement learning, the network copies the scripted bots' key presses
(behaviour cloning), so PPO starts from a bot that already walks in and swings instead of from
random mashing. During PPO, a distillation term keeps it near this teacher at first and fades out
(Schmitt et al., "Kickstarting deep reinforcement learning", 2018).

The teachers are the full-skill chase and crit bots, playing under the same 200 ms delay and 2 ticks
per decision as the learner, against each other and against curriculum opponents of random skill.
Their continuous turns are rounded to the policy's turn steps. The critic is trained on the same
fights' returns, so PPO's value estimates start sensible too.
"""

import concurrent.futures

import numpy as np
import torch

from . import policy as pol
from . import scripted
from .curriculum import DISTANCES
from .env import ACTION_NVEC, PITCH_BINS, YAW_BINS, SwordArenaEnv


def encode_action(a):
    """A scripted bot's action dict → the policy's MultiDiscrete action (turns rounded to the nearest step)."""
    fb = 1 if a.get("forward") and not a.get("back") else 2 if a.get("back") and not a.get("forward") else 0
    lr = 1 if a.get("left") and not a.get("right") else 2 if a.get("right") and not a.get("left") else 0
    return np.array([fb, lr, int(bool(a.get("jump"))), int(bool(a.get("sprint"))), int(bool(a.get("sneak"))),
                     int(bool(a.get("attack"))), int(np.abs(YAW_BINS - a.get("dyaw", 0.0)).argmin()),
                     int(np.abs(PITCH_BINS - a.get("dpitch", 0.0)).argmin())], dtype=np.int64)


def _collect_one(port, decisions, seed, gamma, max_ticks):
    """Fights on one server; returns (inputs, actions, returns) for the teacher sides."""
    rng = np.random.default_rng(seed)
    env = SwordArenaEnv(port=port, max_ticks=max_ticks, seed=seed)
    xs, acts, rets = [], [], []
    done_decisions = 0
    try:
        while done_decisions < decisions:
            styles = rng.choice(["chase", "crit"], size=2)
            vs_teacher = rng.random() < 0.5
            bots = [scripted.ChaseFighter(seed=int(rng.integers(1 << 30)), crits=styles[0] == "crit", repeat=2, delay=2),
                    scripted.ChaseFighter(seed=int(rng.integers(1 << 30)), crits=styles[1] == "crit", repeat=2, delay=2,
                                          skill=1.0 if vs_teacher else float(rng.random()))]
            recorded = [0, 1] if vs_teacher else [0]
            dist = DISTANCES[int(rng.integers(len(DISTANCES)))]
            obs, _ = env.reset(options={"start_distance": dist})
            memory = pol.ActionMemory(2)
            fight = {k: ([], [], []) for k in recorded}
            while env.agents:
                o = np.stack([obs[a] for a in env.possible_agents])
                x = memory.features(o)
                raw = [bots[k](env.delayed_state, k) for k in range(2)]
                encoded = np.stack([encode_action(r) for r in raw])
                obs, rewards, *_ = env.step({agent: raw[k] for k, agent in enumerate(env.possible_agents)})
                memory.push(encoded)
                for k in recorded:
                    fight[k][0].append(x[k])
                    fight[k][1].append(encoded[k])
                    fight[k][2].append(rewards[env.possible_agents[k]])
                done_decisions += 1
            for k in recorded:
                g, returns = 0.0, []
                for r in reversed(fight[k][2]):
                    g = r + gamma * g
                    returns.append(g)
                xs += fight[k][0]
                acts += fight[k][1]
                rets += returns[::-1]
    finally:
        env.close()
    return np.array(xs, np.float32), np.array(acts, np.int64), np.array(rets, np.float32)


def collect(ports, decisions, seed=0, gamma=0.995, max_ticks=600):
    """Teacher fights on all servers in parallel; `decisions` is the total."""
    per = max(1, decisions // len(ports))
    with concurrent.futures.ThreadPoolExecutor(len(ports)) as pool:
        parts = list(pool.map(_collect_one, ports, [per] * len(ports), [seed + 7919 * i for i in range(len(ports))],
                              [gamma] * len(ports), [max_ticks] * len(ports)))
    return tuple(np.concatenate([p[i] for p in parts]) for i in range(3))


def train_bc(policy, x, a, ret, epochs=8, batch=512, lr=1e-3, seed=0, log=print):
    """Behaviour cloning (cross-entropy per control) plus value regression. → accuracy per control."""
    gen = torch.Generator().manual_seed(seed)
    n = len(x)
    perm = torch.randperm(n, generator=gen)
    split = int(n * 0.9)
    tr, va = perm[:split], perm[split:]
    X, A, R = torch.from_numpy(x), torch.from_numpy(a), torch.from_numpy(ret)
    opt = torch.optim.Adam(policy.parameters(), lr=lr)
    policy.train()
    for epoch in range(epochs):
        for idx in tr[torch.randperm(len(tr), generator=gen)].split(batch):
            _, logp, _, v = policy.act(X[idx], A[idx])
            loss = -logp.mean() + 0.5 * ((v - R[idx]) ** 2).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
        with torch.no_grad():
            greedy, *_ = policy.act(X[va], greedy=True)
            acc = (greedy == A[va]).float().mean(0).numpy()
            v_err = ((policy.value(X[va]) - R[va]) ** 2).mean().item()
        log(f"  kickstart epoch {epoch + 1}/{epochs}: held-out accuracy {acc.mean():.3f} "
            f"(attack {acc[5]:.3f}, turn {acc[6]:.3f}), value error {v_err:.3f}")
    policy.eval()
    names = ["forward_back", "left_right", "jump", "sprint", "sneak", "attack", "turn", "look"]
    return {f"bc_acc_{k}": float(v) for k, v in zip(names, acc)} | {"bc_value_mse": v_err, "bc_samples": n}


def distill_kl(student_logits, teacher_logits, skip=(), reverse=False):
    """KL(teacher ‖ student), summed over the controls (except those in `skip`), averaged over the batch.
    reverse=True: KL(student ‖ teacher), which lets the student be sharper than the teacher (aim more
    precisely than a noisy copy of a person) while staying on choices the teacher would make."""
    total = 0.0
    for k, (s, t) in enumerate(zip(torch.split(student_logits, ACTION_NVEC, dim=-1),
                                   torch.split(teacher_logits, ACTION_NVEC, dim=-1))):
        if k in skip:
            continue
        tl, sl = torch.log_softmax(t, -1), torch.log_softmax(s, -1)
        total = total + ((sl.exp() * (sl - tl)) if reverse else (tl.exp() * (tl - sl))).sum(-1)
    return total.mean()
