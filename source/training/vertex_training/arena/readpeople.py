"""Reading people: the memory policy's opponent prediction, trained and scored on duels.

Against bots the memory improves the policy's prediction of its opponent by 4-5 points; against the
owner it added nothing (v3, duel 6) or hurt (duel 7): it learned bot patterns. Here the duels are
replayed from the bot's side, and the person's actual keys are the prediction target, so the memory
can learn what people do. Duels are split by session: the held-out duel is never trained on.

Scored on the movement keys (W/S, A/D, jump, sprint), with the memory and with it wiped at every
decision: "reads you" means the gain from memory is positive on a duel it never saw.
"""

import gzip
import json

import numpy as np
import torch

from . import policy as pol
from .duel import DUELS_DIR, FIGHT_TICKS, human_action
from .env import ACTION_NVEC, SwordArenaEnv

MOVE_CONTROLS = 4      # W/S, A/D, jump, sprint
SEGMENT, BURN_IN = 64, 16


def duel_stamps():
    """Duels with a server trace, oldest first."""
    return [f.name[len("duel-"):-len(".trace.json.gz")] for f in sorted(DUELS_DIR.glob("duel-*.trace.json.gz"))]


def human_fights(stamps, layout):
    """Each fight of these duels from the bot's side: its inputs (with its own action memory), the
    person's action at each decision (the target) and the tick."""
    env = SwordArenaEnv(obs_layout=layout, max_ticks=FIGHT_TICKS)
    fights = []
    for stamp in stamps:
        with gzip.open(DUELS_DIR / f"duel-{stamp}.trace.json.gz", "rt", encoding="utf-8") as fh:
            data = json.load(fh)
        for fight in data["fights"]:
            steps = fight["steps"]
            env.begin(steps[0], fight_ticks=FIGHT_TICKS)
            memory = pol.ActionMemory(1)
            xs, opp, ticks = [], [], []
            for k in range(1, len(steps)):
                xs.append(memory.features(env.observation(0)[None])[0])
                ticks.append(steps[k - 1]["tick"])
                env.advance(steps[k])
                memory.push(human_action(steps[k - 1], steps[k], steps[k]["events"], 0, data["repeat"])[None])
                opp.append(human_action(steps[k - 1], steps[k], steps[k]["events"], 1, data["repeat"]))
            fights.append({"x": np.array(xs, np.float32), "opp": np.array(opp, np.int64),
                           "tick": np.array(ticks, np.int64)})
    return fights


def segments(fights):
    """Pieces of SEGMENT decisions with up to BURN_IN decisions of context before each."""
    out = []
    for f in fights:
        n = len(f["x"])
        for start in range(0, n, SEGMENT):
            lo = max(0, start - BURN_IN)
            piece = {k: v[lo:start + SEGMENT] for k, v in f.items()}
            piece["counted"] = np.arange(lo, min(n, start + SEGMENT)) >= start
            out.append(piece)
    return out


def batch(pieces, input_size):
    """→ x [T, n, in], opp [T, n, 8], counted [T, n] (padding and burn-in don't count)."""
    T, n = max(len(p["x"]) for p in pieces), len(pieces)
    x = np.zeros((T, n, input_size), np.float32)
    opp = np.zeros((T, n, len(ACTION_NVEC)), np.int64)
    counted = np.zeros((T, n), bool)
    for j, p in enumerate(pieces):
        x[: len(p["x"]), j], opp[: len(p["x"]), j], counted[: len(p["x"]), j] = p["x"], p["opp"], p["counted"]
    return torch.from_numpy(x), torch.from_numpy(opp), torch.from_numpy(counted)


def reading_loss(policy, pieces):
    """Negative log-likelihood of the person's actions under the opponent head, per control."""
    x, opp, counted = batch(pieces, policy.input_size)
    out, _ = policy.core(x, policy.initial_state(x.shape[1]), torch.zeros(x.shape[:2]))
    _, logp, _ = pol._choose(policy.opponent_head(out), opp)
    return -logp[counted].mean() / len(ACTION_NVEC)


@torch.no_grad()
def read_score(policy, fights):
    """→ (movement-key accuracy with memory, with the memory wiped every decision)."""
    hits = {"memory": [], "wiped": []}
    for f in fights:
        x = torch.from_numpy(f["x"])
        out, _ = policy.core(x[:, None], policy.initial_state(1), torch.zeros(len(x), 1))
        wiped, _ = policy.core(x[None], policy.initial_state(len(x)), torch.zeros(1, len(x)))
        for name, o in (("memory", out[:, 0]), ("wiped", wiped[0])):
            parts = torch.split(policy.opponent_head(o), ACTION_NVEC, -1)
            pred = torch.stack([p.argmax(-1) for p in parts[:MOVE_CONTROLS]], -1).numpy()
            hits[name].append((pred == f["opp"][:, :MOVE_CONTROLS]).mean(-1))
    return float(np.concatenate(hits["memory"]).mean()), float(np.concatenate(hits["wiped"]).mean())
