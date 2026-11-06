"""The owner as evidence: their duels against strategists' bots, and how they play, measured.

Strategists see (1) their own bot's duels vs the owner as one more opponent ("owner") with the same
breakdown as any other, and (2) a shared section, "how the owner plays", measured from every duel in the
run: what the owner presses at each range, how their hits are set up (crit, punishing a whiff, right
after the bot swung), what they do after being hit, and their combos. The owner's play is not private
to anyone (unlike bot code), so every strategist can build on it.
"""

import gzip
import json
import math
import statistics

from . import match


def duel_files(run_dir):
    return sorted((run_dir / "duels").glob("duel-*.trace.json.gz")) if (run_dir / "duels").exists() else []


def load(run_dir, bot=None, version=None):
    """→ [(steps, bot_side, you_side, bot name, version)] for every duel fight in the run (optionally one
    bot / version)."""
    out = []
    for f in duel_files(run_dir):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            d = json.load(fh)
        if bot is not None and d.get("bot_name") != bot:
            continue
        if version is not None and d.get("version") != version:
            continue
        for fight in d["fights"]:
            out.append((fight["steps"], d["bot"], d["you"], d.get("bot_name"), d.get("version")))
    return out


def as_opponent(fights):
    """The bot's results vs the owner, in the summary format (score from the bot's side)."""
    from ..arena.env import outcome_score
    scores = [outcome_score(steps[-1], bot) for steps, bot, *_ in fights]
    view = match.breakdown([(steps, bot) for steps, bot, *_ in fights])
    return {"score": sum(scores) / len(scores), "fights": len(scores), "scores": scores, **view}


def _dist(a, b):
    return math.hypot(a["x"] - b["x"], a["z"] - b["z"])


def _band(d):
    return "in reach (<3)" if d < 3.0 else "edge (3-4.5)" if d < 4.5 else "far (>4.5)"


def habits(fights):
    """How the owner plays, from duel fights [(steps, bot_side, you_side, ...)] → list of text lines."""
    if not fights:
        return []
    keys = ("forward", "back", "left", "right", "jump", "sprint", "sneak")
    time_in = {}                       # band -> [count, {key: count}]
    hits = crit = sprint = on_whiff = after_swing = in_air_taken = taken = 0
    jump_dist = []                     # distance when the owner's jump started, for their crits
    after_hit = {"back": 0, "strafe": 0, "jump": 0, "forward": 0, "n": 0}
    reply_ticks = []                   # owner's time from being hit to hitting back (within 40 ticks)
    combos, cur = [], 0
    opening = {"you": 0, "you_crit": 0, "bot": 0, "bot_crit": 0, "ahead": 0, "jump_you": 0, "jump_bot": 0}
    for steps, b, y, *_ in fights:
        hits_all = [e for st in steps[1:] for e in st["events"] if e["type"] == "hit"]
        if hits_all:
            first = hits_all[0]
            side = "you" if first["attacker"] == y else "bot"
            opening[side] += 1
            opening[side + "_crit"] += int(first["crit"])
            net = sum(h["damage"] if h["attacker"] == y else -h["damage"] for h in hits_all
                      if h["tick"] - first["tick"] <= 20)
            opening["ahead"] += int(net > 0.5)
            before = [st["bots"] for st in steps[1:] if st["tick"] <= first["tick"]]
            opening["jump_you"] += int(any(not s[y]["on_ground"] and s[y]["vy"] > 0 for s in before))
            opening["jump_bot"] += int(any(not s[b]["on_ground"] and s[b]["vy"] > 0 for s in before))
        last_jump_dist, last_bot_swing, pending_reply = None, -99, None
        for i, st in enumerate(steps[1:], 1):
            me, bot = st["bots"][y], st["bots"][b]
            d = _dist(me, bot)
            band = time_in.setdefault(_band(d), [0, {k: 0 for k in keys}])
            band[0] += 1
            for k in keys:       # sprint: actually sprinting (the key itself is often a toggle)
                band[1][k] += int(me["sprinting"] if k == "sprint" else me["keys"].get(k, False))
            prev = steps[i - 1]["bots"][y]
            if prev["on_ground"] and not me["on_ground"] and me["vy"] > 0:
                last_jump_dist = d
            for e in st["events"]:
                if e["type"] == "swing" and e["bot"] == b:
                    last_bot_swing = e["tick"]
                if e["type"] != "hit":
                    continue
                if e["attacker"] == y:
                    hits += 1
                    crit += int(e["crit"])
                    sprint += int(e["sprint_hit"])
                    on_whiff += int(steps[i - 1]["bots"][b]["miss_cooldown"] > 0)
                    after_swing += int(e["tick"] - last_bot_swing <= 10)
                    if e["crit"] and last_jump_dist is not None:
                        jump_dist.append(last_jump_dist)
                    cur += 1
                    if pending_reply is not None and e["tick"] - pending_reply <= 40:
                        reply_ticks.append(e["tick"] - pending_reply)
                    pending_reply = None
                else:
                    taken += 1
                    in_air_taken += int(not steps[i - 1]["bots"][y]["on_ground"])
                    if cur:
                        combos.append(cur)
                    cur = 0
                    pending_reply = e["tick"]
                    window = steps[i + 1:i + 6]            # next ~10 ticks
                    if window:
                        after_hit["n"] += 1
                        frac = lambda k: sum(s["bots"][y]["keys"].get(k, False) for s in window) / len(window)
                        after_hit["back"] += frac("back")
                        after_hit["strafe"] += max(frac("left"), frac("right"))
                        after_hit["jump"] += frac("jump")
                        after_hit["forward"] += frac("forward")
        if cur:
            combos.append(cur)
            cur = 0
    pct = lambda a, n: f"{a / n:.0%}" if n else "-"
    lines = [f"Measured over {len(fights)} duel fights against this run's bots.", "",
             "| range | share of time | forward | back (S) | strafe | jump | sprint | sneak |", "|---|---|---|---|---|---|---|---|"]
    total = sum(v[0] for v in time_in.values())
    for band in ("far (>4.5)", "edge (3-4.5)", "in reach (<3)"):
        if band not in time_in:
            continue
        n, k = time_in[band]
        lines.append(f"| {band} | {pct(n, total)} | {pct(k['forward'], n)} | {pct(k['back'], n)} | "
                     f"{pct(max(k['left'], k['right']), n)} (L {pct(k['left'], n)} / R {pct(k['right'], n)}) | "
                     f"{pct(k['jump'], n)} | {pct(k['sprint'], n)} | {pct(k['sneak'], n)} |")
    n_open = opening["you"] + opening["bot"]
    lines += ["", f"- Openings (the first hit of each fight, {n_open} fights): the owner landed it in "
                  f"{opening['you']} ({opening['you_crit']} as crits), the bots in {opening['bot']} "
                  f"({opening['bot_crit']} as crits); the owner was ahead on damage after the opening exchange "
                  f"(first 20 ticks after the first hit) in {pct(opening['ahead'], n_open)}. Before the first hit "
                  f"the owner jumped in {opening['jump_you']} fights, the bots in {opening['jump_bot']}."
              if n_open else ""]
    own = match.numbers([{"steps": s if y == 1 else match._swap(s)} for s, b, y, *_ in fights])
    if own.get("swing_gap") is not None:
        lines += [f"- Owner's swing tempo: median {own['swing_gap']:.0f} ticks between swings, "
                  f"{own['swing_gap_after_hit'] or float('nan'):.0f} right after being hit (charge refills in ~11-12 ticks); "
                  f"inside their own combos they often swing a little before full charge (0.85-0.9 did 0.94 HP vs 1.09 at full)."]
    if own.get("crit_chain_share") is not None:
        lines += [f"- Owner's crit chains: {own['crit_chain_share']:.0%} of their crits come right after another crit of theirs "
                  f"(within 16 ticks, no hit taken): they chain jump crits at close range."]
    if own.get("rush_share") is not None:
        lines += [f"- Owner's rush share: {own['rush_share']:.0%} (when the bot is 3-6 blocks away and not coming in, "
                  f"how often the owner closes in)."]
    lines += [# a person's sprint hit is read from their sprint ending at the hit, which their client
              # reports later: unreliable for people, so not shown as a measurement
              f"- Owner's hits: {hits}; crits {pct(crit, hits)} (a person's sprint hits can't be measured "
              f"reliably here); "
              f"{pct(on_whiff, hits)} landed while the bot was in whiff lockout; {pct(after_swing, hits)} within "
              f"10 ticks after the bot swung (its charge was low).",
              f"- Crit setup: the owner's jump started at a median {statistics.median(jump_dist):.2f} blocks "
              f"(range {min(jump_dist):.2f}-{max(jump_dist):.2f})." if jump_dist else "- No crits measured.",
              f"- Hits the owner took: {taken}; {pct(in_air_taken, taken)} while airborne.",
              (f"- After being hit (next ~10 ticks, share of time): back {after_hit['back'] / after_hit['n']:.0%}, "
               f"strafe {after_hit['strafe'] / after_hit['n']:.0%}, jump {after_hit['jump'] / after_hit['n']:.0%}, "
               f"forward {after_hit['forward'] / after_hit['n']:.0%}.") if after_hit["n"] else "",
              f"- Owner hit back within 2 s of being hit {len(reply_ticks)} times, median after "
              f"{statistics.median(reply_ticks):.0f} ticks." if reply_ticks else "",
              f"- Owner's combos (hits before the bot hits back): longest {max(combos)}, mean "
              f"{statistics.mean(combos):.1f}, {sum(c >= 3 for c in combos)} of {len(combos)} were 3+." if combos else ""]
    return [l for l in lines if l is not None]


def notes_file(run_dir):
    return run_dir / "owner_notes.json"


def notes(run_dir):
    f = notes_file(run_dir)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def add_note(text, run=None, log=print):
    """The owner's own observation, shown to every strategist from the next rewrite on."""
    import datetime
    from .loop import RUNS_DIR
    if not text or not text.strip():
        raise ValueError('write the note in quotes: agentic note "the bot never jump-crits the first hit"')
    runs = sorted(p for p in RUNS_DIR.glob("*") if (p / "state.json").exists()) if RUNS_DIR.exists() else []
    if not runs:
        raise FileNotFoundError("no agentic runs yet")
    run_dir = RUNS_DIR / run if run else runs[-1]
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    items = notes(run_dir)
    items.append({"text": text.strip()[:2000], "round": state["round"],
                  "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")})
    notes_file(run_dir).write_text(json.dumps(items, indent=1), encoding="utf-8")
    log(f"note {len(items)} saved for {run_dir.name}; every strategist reads it at the next rewrite")
