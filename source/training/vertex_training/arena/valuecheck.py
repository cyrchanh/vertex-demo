"""Does a policy's value head judge fights against a person? The first check before using it to coach.

  python -m vertex_training arena valuecheck [--policy champion] [--duel all|latest|STAMP]

Coaching would use the value head as a win-chance meter: where did a player's chances drop, and
what did that. That only works if its judgement carries over from bot fights (what it was trained
on) to human ones. This replays every recorded duel offline from its server trace (no server
needed), asks the policy's value head at every decision how the fight is going for each side, and
compares that with what actually happened next (the discounted return it was trained to predict:
net damage, the result, minus the whiff cost).

Two judges are scored on the same moments:
  value head   the policy's own estimate, with its memory of the fight so far
  health bar   the best a straight line through the current health lead can do (fitted on these
               very fights, so it's an optimistic baseline)
If the value head doesn't beat the health bar, it knows nothing about a human fight beyond what
the health bar shows, and a coach can't lean on it.

Both sides are scored: "bot" is the policy judging its own fight against the person; "you" is the
policy imagining itself in the person's place (further from its training: the person's actions fill
its action memory). Explained variance (EV) is 1 − Var(return − estimate) / Var(return): 1 is perfect,
0 is no better than guessing the average, below 0 is worse. 90% intervals resample whole fights.
"""

import gzip
import json

import numpy as np
import torch

from . import play
from . import policy as pol
from .analyse import load
from .duel import FIGHT_TICKS, human_action
from .env import SwordArenaEnv, outcome_score

LATE_SECONDS = 10.0
BOOTSTRAP = 2000


def fight_series(env, policy, fight, repeat, whiff_cost):
    """One recorded fight → per-decision arrays: value [T, 2], reward [T, 2], health lead [T, 2]
    (each side's own view), tick [T]."""
    steps = fight["steps"]
    env.begin(steps[0], fight_ticks=FIGHT_TICKS)
    memory = pol.ActionMemory(2)
    state = policy.initial_state(2)
    no_reset = torch.zeros(1, 2)
    values, rewards, leads, ticks = [], [], [], []
    for k in range(1, len(steps)):
        before = steps[k - 1]
        obs = np.stack([env.observation(i, policy.obs_layout) for i in (0, 1)])
        x = torch.from_numpy(memory.features(obs))
        with torch.no_grad():
            out, state = policy.core(x.unsqueeze(0), state, no_reset)
            v = policy.critic_head(out[0]).squeeze(-1).numpy()
        _, r, *_ = env.advance(steps[k])
        r = np.array([r["bot_0"], r["bot_1"]], dtype=np.float32)
        for e in steps[k]["events"]:
            if e["type"] == "swing" and not e["hit"]:
                r[e["bot"]] -= whiff_cost
        memory.push(np.stack([human_action(before, steps[k], steps[k]["events"], i, repeat) for i in (0, 1)]))
        h = [before["bots"][i]["health"] for i in (0, 1)]
        values.append(v)
        rewards.append(r)
        leads.append([(h[0] - h[1]) / 20.0, (h[1] - h[0]) / 20.0])
        ticks.append(before["tick"])
    return np.array(values), np.array(rewards), np.array(leads, np.float32), np.array(ticks)


def returns(rewards, gamma):
    out = np.zeros_like(rewards)
    g = np.zeros(rewards.shape[1], dtype=np.float32)
    for t in reversed(range(len(rewards))):
        g = rewards[t] + gamma * g
        out[t] = g
    return out


def _ev(ret, est):
    var = np.var(ret)
    return float("nan") if var < 1e-9 else 1.0 - np.var(ret - est) / var


def _health_fit(ret, lead):
    """The health-bar judge: least squares of return on [lead, 1], fitted on the same moments."""
    a = np.stack([lead, np.ones_like(lead)], axis=1)
    coef, *_ = np.linalg.lstsq(a, ret, rcond=None)
    return a @ coef


def scores(fights, side, mask_fn=None):
    """EV of value head and health bar, and the value head's bias, over the chosen moments."""
    ret, v, lead = [], [], []
    for f in fights:
        m = mask_fn(f) if mask_fn else slice(None)
        ret.append(f["ret"][m, side])
        v.append(f["value"][m, side])
        lead.append(f["lead"][m, side])
    ret, v, lead = np.concatenate(ret), np.concatenate(v), np.concatenate(lead)
    if len(ret) < 10:
        return None
    return {"value_ev": _ev(ret, v), "health_ev": _ev(ret, _health_fit(ret, lead)),
            "bias": float(np.mean(v - ret)), "n": len(ret)}


def _interval(fights, side, key, mask_fn=None, seed=0):
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(BOOTSTRAP):
        sample = [fights[i] for i in rng.integers(0, len(fights), len(fights))]
        s = scores(sample, side, mask_fn)
        if s is not None and np.isfinite(s[key]):
            out.append(s[key])
    return np.percentile(out, [5, 95]) if out else (float("nan"), float("nan"))


def check(policy_name="champion", which="all", log=print):
    path = play.resolve(policy_name)
    policy, ckpt = pol.load(path)
    if not getattr(policy, "recurrent", False):
        raise ValueError("valuecheck reads a recurrent policy's memory; this one has none")
    cfg = json.loads(ckpt.get("config", "{}")) if isinstance(ckpt.get("config"), str) else ckpt.get("config", {})
    gamma, whiff_cost = cfg.get("gamma", 0.995), cfg.get("whiff_cost", 0.0)
    _, files = load(which)
    env = SwordArenaEnv(obs_layout=policy.obs_layout, max_ticks=FIGHT_TICKS)
    fights = []
    for file in files:
        with gzip.open(file, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
        for fight in data["fights"]:
            v, r, lead, ticks = fight_series(env, policy, fight, data["repeat"], whiff_cost)
            fights.append({"duel": file.name.split(".")[0], "value": v, "ret": returns(r, gamma),
                           "lead": lead, "ticks": ticks, "steps": fight["steps"]})
    late = lambda f: (f["ticks"] - f["ticks"][0]) / 20.0 >= LATE_SECONDS
    early = lambda f: (f["ticks"] - f["ticks"][0]) / 20.0 < LATE_SECONDS

    log(f"{path.name}: value head vs health bar on {len(fights)} human fights from {len(files)} duel(s) "
        f"(gamma {gamma}, whiff cost {whiff_cost})")
    log(f"  {'side':5} {'moments':8} {'value EV':>22} {'health-bar EV':>15} {'value bias':>12}")
    results = {}
    for side, name in ((0, "bot"), (1, "you")):
        for label, fn in (("all", None), ("<10 s", early), (">=10 s", late)):
            s = scores(fights, side, fn)
            if s is None:
                continue
            lo, hi = _interval(fights, side, "value_ev", fn)
            results[(name, label)] = {**s, "value_ev_90": (float(lo), float(hi))}
            log(f"  {name:5} {label:8} {s['value_ev']:8.2f} [{lo:5.2f}, {hi:5.2f}]  {s['health_ev']:15.2f} "
                f"{s['bias']:+12.2f}")
    v = np.concatenate([f["value"] for f in fights])
    log(f"  zero-sum check: corr(bot value, -your value) {np.corrcoef(v[:, 0], -v[:, 1])[0, 1]:.2f}, "
        f"mean(bot + you) {np.mean(v.sum(1)):+.2f} (0 if it sees the fight as zero-sum)")

    log("  per fight, your side: value at 0 / 5 / 10 / 20 s -> what you actually got")
    for f in fights:
        t = (f["ticks"] - f["ticks"][0]) / 20.0
        at = [f["value"][min(np.searchsorted(t, s), len(t) - 1), 1] for s in (0, 5, 10, 20)]
        log(f"    {f['duel']}  " + "  ".join(f"{a:+.2f}" for a in at)
            + f"  -> return {f['ret'][0, 1]:+.2f}, your score {outcome_score(f['steps'][-1], 1):.2f}, "
              f"{t[-1]:.0f} s")
    return results, fights
