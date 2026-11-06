"""Fights between trained policies and scripted bots: for evaluation, the champion gate and watching.

A side is a name: "chase", "crit", "random" (scripted bots), "champion", "latest" (newest run's
latest checkpoint), a run name, or a path to a .pt checkpoint.
"""

import time
from pathlib import Path

from .. import config
from . import policy as pol
from .env import SwordArenaEnv, outcome_score
from .league import SCRIPTED

SELFPLAY_DIR = config.HOME / "selfplay"
CHAMPION_FILE = SELFPLAY_DIR / "champion.pt"


def resolve(name):
    """A policy name → checkpoint path, or None for a scripted bot."""
    if name in SCRIPTED:
        return None
    if name == "champion":
        if not CHAMPION_FILE.exists():
            raise FileNotFoundError("no champion yet: train a run that passes the gate first")
        return CHAMPION_FILE
    if name == "you-clone":
        clone = SELFPLAY_DIR / "you-clone.pt"
        if not clone.exists():
            raise FileNotFoundError("no you-clone yet: duel the bot (arena duel), then arena clone")
        return clone
    if name == "latest":
        runs = sorted((p for p in SELFPLAY_DIR.glob("*/checkpoints/latest.pt")), key=lambda p: p.stat().st_mtime)
        if not runs:
            raise FileNotFoundError(f"no checkpoints in {SELFPLAY_DIR}")
        return runs[-1]
    if name.startswith("probe-") and (SELFPLAY_DIR / f"{name}.pt").exists():
        return SELFPLAY_DIR / f"{name}.pt"     # frozen test opponents (docs/paper/results/probe-*.json)
    if (SELFPLAY_DIR / name / "checkpoints" / "latest.pt").exists():
        return SELFPLAY_DIR / name / "checkpoints" / "latest.pt"
    path = Path(name)
    if path.exists():
        return path
    raise FileNotFoundError(f"no scripted bot, run or checkpoint called {name!r}")


def _controller(name, seed):
    path = resolve(name)
    if path is None:
        bot = SCRIPTED[name][0](seed)
        return lambda env, side, obs: bot(env.delayed_state, side)
    fighter = pol.PolicyFighter(path)
    return lambda env, side, obs: fighter(env.observation(side, fighter.obs_layout))


def fights(a, b, episodes=20, port=25570, seed=10_000, watch=False, pause=3.0, log=print, max_ticks=600,
           per_fight=None):
    """Plays `a` against `b`, swapping sides every fight. → a's average score (1 win, 0 loss; at the
    time limit, scored on health lead: env.outcome_score). Each fight's score is appended to
    `per_fight` if given."""
    env = SwordArenaEnv(port=port, max_ticks=max_ticks, seed=seed)
    score = 0.0
    try:
        for ep in range(episodes):
            if watch and ep > 0:
                time.sleep(pause)
            sides = [_controller(a, seed + ep), _controller(b, seed + ep + 1)]
            if ep % 2:
                sides.reverse()
            obs, _ = env.reset()
            if watch and ep == 0:
                env.client.realtime(True)
            while env.agents:
                actions = {agent: sides[k](env, k, obs[agent]) for k, agent in enumerate(env.possible_agents)}
                obs, *_ , infos = env.step(actions)
            s_ = outcome_score(env.state, ep % 2)
            score += s_
            if per_fight is not None:
                per_fight.append(s_)
            if log:
                how = "won" if s_ == 1 else "lost" if s_ == 0 else f"timed out, scored {s_:.2f} on health lead"
                log(f"  fight {ep + 1}: {a} {how} in {env.state['tick'] / 20:.1f} s")
    finally:
        env.close()
    return score / episodes
