"""Where does the bot lose? Side-by-side numbers for the person and the bot from duel traces.

  python -m vertex_training arena analyse [--duel latest|all|STAMP]

Reads selfplay/duels/duel-*.trace.json.gz (every step's exact server state for both fighters) and
compares the two fighters on the things that decide sword fights: who lands hits and how
(crits, sprint hits, range), how charged their swings are and how often they whiff, who opens and
who wins each exchange (hits with no gap over 1 s, as in Vertex's coaching), how long combos run,
how far apart they are, and how well each keeps the other in their crosshair.
"""

import collections
import gzip
import json
import math

import numpy as np

from .duel import DUELS_DIR
from .env import relative_view

EXCHANGE_GAP_TICKS = 20
NAMES = {0: "bot", 1: "you"}
DIST_BANDS = [(0.0, 2.0), (2.0, 3.0), (3.0, 4.0), (4.0, 99.0)]


def load(which="latest"):
    files = sorted(DUELS_DIR.glob("duel-*.trace.json.gz"))
    if not files:
        raise FileNotFoundError(f"no duel traces in {DUELS_DIR}: duel the bot (arena duel) first")
    if which == "latest":
        files = files[-1:]
    elif which != "all":
        files = [f for f in files if which in f.name]
    fights = []
    for f in files:
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            fights += json.load(fh)["fights"]
    return fights, files


def _dist(state):
    a, b = state["bots"]
    return math.hypot(a["x"] - b["x"], a["z"] - b["z"])


def analyse(fights):
    s = {k: collections.Counter() for k in (0, 1)}
    hit_range = {0: [], 1: []}
    swing_charge = {0: [], 1: []}
    aim_error = {0: [], 1: []}
    exchanges = []
    combos = {0: [], 1: []}
    dist_time = collections.Counter()
    total_steps = 0
    for fight in fights:
        steps = fight["steps"]
        hits = []
        prev = steps[0]
        for st in steps[1:]:
            d = _dist(prev)
            total_steps += 1
            for lo, hi in DIST_BANDS:
                if lo <= d < hi:
                    dist_time[(lo, hi)] += 1
            if d < 3.5:
                for i in (0, 1):
                    aim_error[i].append(abs(relative_view(st["bots"][i], st["bots"][1 - i])["yaw_off"]))
            for e in st["events"]:
                if e["type"] == "hit":
                    a = e["attacker"]
                    s[a]["hits"] += 1
                    s[a]["crits"] += int(e["crit"])
                    s[a]["sprint_hits"] += int(e["sprint_hit"])
                    s[a]["damage"] += e["damage"]
                    s[a]["kills"] += int(e["lethal"])
                    hit_range[a].append((_dist(prev) + _dist(st)) / 2)   # the hit lands during this step
                    hits.append((e["tick"], a, e["damage"]))
                elif e["type"] == "swing":
                    i = e["bot"]
                    s[i]["swings"] += 1
                    s[i]["whiffs"] += int(not e["hit"])
                    swing_charge[i].append(e["charge"])
            prev = st
        # exchanges and combos
        cur = []
        for h in sorted(hits, key=lambda h: h[0]):   # by tick only: same-tick hits keep server order
            if cur and h[0] - cur[-1][0] > EXCHANGE_GAP_TICKS:
                exchanges.append(cur)
                cur = []
            cur.append(h)
        if cur:
            exchanges.append(cur)
        run, who = 0, None
        for _, a, _ in sorted(hits, key=lambda h: h[0]):
            if a == who:
                run += 1
            else:
                if who is not None:
                    combos[who].append(run)
                who, run = a, 1
        if who is not None:
            combos[who].append(run)
    opened = collections.Counter(ex[0][1] for ex in exchanges)
    won = collections.Counter()
    for ex in exchanges:
        net = sum(d if a == 1 else -d for _, a, d in ex)
        if abs(net) > 0.5:
            won[1 if net > 0 else 0] += 1
    rows = []

    def row(label, f):
        rows.append((label, f(0), f(1)))

    row("hits landed", lambda i: f"{s[i]['hits']}")
    row("damage dealt (HP)", lambda i: f"{s[i]['damage']:.1f}")
    row("damage per hit", lambda i: f"{s[i]['damage'] / max(1, s[i]['hits']):.2f}")
    row("crits (share of hits)", lambda i: f"{s[i]['crits']} ({s[i]['crits'] / max(1, s[i]['hits']):.0%})")
    row("sprint hits (share)", lambda i: f"{s[i]['sprint_hits']} ({s[i]['sprint_hits'] / max(1, s[i]['hits']):.0%})")
    row("swings", lambda i: f"{s[i]['swings']}")
    row("whiffs (share of swings)", lambda i: f"{s[i]['whiffs']} ({s[i]['whiffs'] / max(1, s[i]['swings']):.0%})")
    row("charge when swinging (median)", lambda i: f"{np.median(swing_charge[i]):.2f}" if swing_charge[i] else "-")
    row("swings below 0.9 charge", lambda i: f"{np.mean(np.array(swing_charge[i]) < 0.9):.0%}" if swing_charge[i] else "-")
    row("range at hits (median, blocks)", lambda i: f"{np.median(hit_range[i]):.2f}" if hit_range[i] else "-")
    row("exchanges opened", lambda i: f"{opened[i]} of {len(exchanges)}")
    row("exchanges won (net damage)", lambda i: f"{won[i]} of {len(exchanges)}")
    row("longest combo (hits in a row)", lambda i: f"{max(combos[i], default=0)}")
    row("average combo", lambda i: f"{np.mean(combos[i]):.1f}" if combos[i] else "-")
    row("aim error within 3.5 blocks (median, deg)", lambda i: f"{np.median(aim_error[i]):.1f}" if aim_error[i] else "-")
    spacing = {band: dist_time[band] / max(1, total_steps) for band in DIST_BANDS}
    return rows, spacing, len(fights)


def report(which="latest", log=print):
    fights, files = load(which)
    rows, spacing, n = analyse(fights)
    log(f"{n} fights from {', '.join(f.name for f in files)}")
    w = max(len(r[0]) for r in rows)
    log(f"  {'':{w}}  {'bot':>14}  {'you':>14}")
    for label, b, y in rows:
        log(f"  {label:{w}}  {b:>14}  {y:>14}")
    log("  time at each distance: " + ", ".join(
        f"{lo:g}-{hi:g} blocks {share:.0%}" if hi < 99 else f"over {lo:g} {share:.0%}" for (lo, hi), share in spacing.items()))
    return rows, spacing
