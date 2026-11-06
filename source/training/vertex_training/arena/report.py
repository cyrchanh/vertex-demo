"""The report card: one fixed set of checks for any policy, so runs are judged against people and
not only by Elo against bots (every failure on 2026-11-05/06 looked fine against bots and showed up
in a duel within minutes).

  python -m vertex_training arena report --policy NAME [--duel STAMP] [--episodes 60] [--port 25570]

Offline (no server needed):
  reading      how well its memory predicts the person's movement keys in a duel (default: the
               latest), with the memory vs wiped every decision
  duels        every recorded duel against this exact checkpoint: the person's score, hits, and the
               behaviour table below from the bot's side

Live (needs a free arena server; skipped if none answers):
  fights       vs v3 (`episodes` fights, 90% interval), the you-like partner and the punisher (a
               third as many each), with the behaviour table from those fights

Behaviour table: how often it backs off (S) and jumps, overall, in a combo, when the opponent is
charged and close, and by distance; whiffs, weak swings, hits taken in the air, exchanges opened.

Written to selfplay/reports/<name>.md and printed.
"""

import collections
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from . import play, readpeople
from .duel import DUELS_DIR
from .env import SwordArenaEnv, outcome_score
from .train import score_interval
from .train_rnn import new_stats, tally

REPORTS_DIR = play.SELFPLAY_DIR / "reports"
OPPONENTS = {"v3": play.SELFPLAY_DIR / "champion-v3.pt", "partner": play.SELFPLAY_DIR / "you-partner.pt",
             "punisher": "punish"}
ROWS = ["all", "in a combo", "opponent charged, < 3 blocks", "under 3 blocks", "3-4 blocks", "over 4 blocks"]


def behaviour(fights, side):
    """fights: lists of server states; side: the fighter to describe → {row: (S share, jump share)} and
    fight-skill shares."""
    keys = collections.defaultdict(lambda: [0, 0, 0])      # S, jump, decisions
    stats = new_stats()
    for steps in fights:
        st = new_stats()
        for prev, cur in zip(steps, steps[1:]):
            tally(st, prev, cur)
            me, op = prev["bots"][side], prev["bots"][1 - side]
            d = math.hypot(me["x"] - op["x"], me["z"] - op["z"])
            k = cur["bots"][side]["keys"]
            rows = ["all"]
            if me["hurt_time"] > 0:
                rows.append("in a combo")
            if op["charge"] >= 0.9 and d < 3:
                rows.append("opponent charged, < 3 blocks")
            rows.append("under 3 blocks" if d < 3 else "3-4 blocks" if d < 4 else "over 4 blocks")
            for r in rows:
                keys[r][0] += bool(k["back"])
                keys[r][1] += bool(k["jump"])
                keys[r][2] += 1
        for name, v in st.items():
            if name != "last_hit":
                stats[name] = [a + b for a, b in zip(stats[name], v)]
    share = lambda a, b: a / b if b else float("nan")
    skills = {"whiffs": share(stats["whiffs"][side], stats["swings"][side]),
              "weak swings": share(stats["weak_swings"][side], stats["swings"][side]),
              "hits taken in the air": share(stats["air_hits_taken"][side], stats["hits_taken"][side]),
              "exchanges opened": share(stats["opened"][side], stats["exchanges"][side]),
              "hits a fight": share(stats["hits"][side], len(fights)),
              "hits taken a fight": share(stats["hits"][1 - side], len(fights))}
    return {r: (share(keys[r][0], keys[r][2]), share(keys[r][1], keys[r][2]), keys[r][2]) for r in ROWS}, skills


def _table(beh, skills, out):
    out.append("| Situation | S | Jump | Decisions |")
    out.append("|---|---|---|---|")
    for r in ROWS:
        s, j, n = beh[r]
        out.append(f"| {r} | {s:.1%} | {j:.1%} | {n} |")
    out.append("")
    out.append(" · ".join(f"{k} {v:.1f}" if k.endswith("a fight") else f"{k} {v:.0%}" for k, v in skills.items()))
    out.append("")


def live_fights(policy_path, opponent, episodes, port, seed=90_000):
    """→ (per-fight scores, fights as lists of states with the policy on side 0 or 1, its sides)."""
    env = SwordArenaEnv(port=port, max_ticks=600, seed=seed)
    scores, traces, sides = [], [], []
    try:
        for ep in range(episodes):
            side = ep % 2
            me = play._controller(str(policy_path), seed + ep)
            op = play._controller(str(opponent), seed + ep + 1)
            obs, _ = env.reset(seed=seed + ep)
            steps = [env.state]
            while env.agents:
                acts = {}
                for k, agent in enumerate(env.possible_agents):
                    acts[agent] = (me if k == side else op)(env, k, obs[agent])
                obs, *_ = env.step(acts)
                steps.append(env.state)
            scores.append(outcome_score(env.state, side))
            traces.append(steps)
            sides.append(side)
    finally:
        env.close()
    return scores, traces, sides


def report(policy="champion", duel="latest", episodes=60, port=25570, log=print):
    path = Path(play.resolve(policy))
    from . import policy as pol
    net, ckpt = pol.load(path)
    name = (path.parent.parent.name + "-" + path.stem) if path.parent.name == "checkpoints" else path.stem
    out = [f"# Report card: {name}", "", f"Checkpoint `{path}`" + (f", update {ckpt['update']}" if "update" in ckpt else ""), ""]

    # ---- reading ----
    stamps = readpeople.duel_stamps()
    stamp = stamps[-1] if duel == "latest" else duel
    if getattr(net, "recurrent", False):
        mem, wiped = readpeople.read_score(net, readpeople.human_fights([stamp], net.obs_layout))
        out += ["## Reading the owner", "",
                f"Duel {stamp}: predicts the owner's movement keys {mem:.1%} with memory, {wiped:.1%} with it "
                f"wiped ({mem - wiped:+.1%}). Only meaningful if the policy never trained on this duel.", ""]

    # ---- duels against this checkpoint ----
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    played = []      # duels against this exact checkpoint (by its fingerprint; older duels by path)
    for f in sorted(DUELS_DIR.glob("duel-*.trace.json.gz")):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
        mine = [x for x in data["fights"] if _same_policy(x, path, sha)]
        if mine:
            played.append((f.name[5:-14], [x["steps"] for x in mine]))
    out += ["## Duels against the owner", ""]
    if not played:
        out += ["None yet: `arena duel --policy " + str(path) + "`.", ""]
    for stamp_d, fights in played:
        owner = np.mean([outcome_score(s[-1], 1) for s in fights])
        out.append(f"### Duel {stamp_d}: owner scores {owner:.2f} over {len(fights)} fights")
        out.append("")
        _table(*behaviour(fights, 0), out)

    out += ["## Fights", ""]
    if episodes <= 0:
        out += ["Skipped (episodes 0). Don't point this at a server a training run is using.", ""]
    else:
        _fights_section(path, episodes, port, out, log)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    file = REPORTS_DIR / f"{name}.md"
    file.write_text("\n".join(out), encoding="utf-8")
    log("\n".join(out))
    log(f"\nsaved {file}")
    return file


def _same_policy(fight, path, sha):
    """Duel fights record the checkpoint's fingerprint (older ones only its path, which may have held
    another policy then: champion.pt was v1, v2 and v3 in turn)."""
    if "policy_sha" in fight:
        return fight["policy_sha"] == sha
    return Path(fight["policy"]).resolve() == path.resolve()


def _fights_section(path, episodes, port, out, log):
    try:
        all_traces, all_sides = [], []
        for opp, target in OPPONENTS.items():
            n = episodes if opp == "v3" else max(10, episodes // 3)
            scores, traces, sides = live_fights(path, target, n, port)
            lo, hi = score_interval(scores)
            out.append(f"- vs {opp}: {np.mean(scores):.2f} (90% interval {lo:.2f}-{hi:.2f}, {n} fights)")
            log(f"  vs {opp}: {np.mean(scores):.2f} ({lo:.2f}-{hi:.2f})")
            all_traces += traces
            all_sides += sides
        out += ["", "Behaviour in these fights:", ""]
        flipped = [[_flip(s) for s in t] if side == 1 else t for t, side in zip(all_traces, all_sides)]
        _table(*behaviour(flipped, 0), out)
    except (ConnectionError, OSError) as e:
        out += [f"Skipped: no arena server answered on port {port} ({e.__class__.__name__}).", ""]


def _flip(state):
    """A state with the two fighters swapped (events re-labelled), so the policy is always side 0."""
    swap = {0: 1, 1: 0}
    events = []
    for e in state["events"]:
        e = dict(e)
        for key in ("bot", "attacker"):
            if key in e:
                e[key] = swap[e[key]]
        events.append(e)
    return {**state, "bots": [state["bots"][1], state["bots"][0]], "events": events}
