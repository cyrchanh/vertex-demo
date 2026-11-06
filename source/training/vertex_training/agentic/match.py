"""The tournament: fights between bot programs and anchors on one arena server, in a child process.

  python -m vertex_training.agentic.match SPEC.json OUT.json

A child process because bot code is untrusted: a crash or an endless loop takes down only this
process, and the parent (loop.py) kills it at its time limit. SPEC:

  {"port": 25600, "fight_ticks": 600, "seed": 1, "trace_file": "...json.gz" or null,
   "entrants": {"name": {"kind": "program", "code": "..."} | {"kind": "scripted", "style": "chase"}
                        | {"kind": "policy", "path": "...pt"}},
   "matchups": [["a", "b", fights], ...]}

OUT: per matchup, a's mean score (1 win, 0 loss, time-outs on health lead), and for each side that is
a program: errors raised by its act(), its mean time per decision, and the `arena analyse` breakdown
of the fights from its side.
"""

import gzip
import math
import json
import sys
import time
import traceback

from ..arena import analyse
from ..arena import policy as pol
from ..arena.env import SwordArenaEnv, outcome_score
from ..arena.league import SCRIPTED
from . import sandbox
from .view import client_view

MAX_ERRORS_KEPT = 3


# Human limits (owner's choice, 2026-11-06): applied to every strategist bot after its act(), so its own
# code can't avoid them. Anchors are left as they are (the fixed scale). First guesses, to be calibrated
# against the owner's measured hit ranges:
#   click_delay_p  chance a click lands one decision (100 ms) late, as human click timing varies
#   aim_sd         degrees of noise on each decision's turn (the RL trainer uses 1.0)
#   drift_sd       degrees: a slowly wandering offset of the crosshair (AR(1), ~1 s memory)
HUMAN_LIMITS = {"click_delay_p": 0.35, "aim_sd": 1.0, "drift_sd": 1.5}


class HumanLimits:
    def __init__(self, seed, click_delay_p=0.0, aim_sd=0.0, drift_sd=0.0):
        import random
        self.rng = random.Random(seed * 7 + 13)
        self.p, self.aim_sd, self.drift_sd = click_delay_p, aim_sd, drift_sd
        self.pending = False
        self.drift = [0.0, 0.0]
        self.delayed = self.clicks = 0

    def __call__(self, action):
        click = action.get("attack", False)
        action["attack"] = self.pending
        self.pending = False
        if click:
            self.clicks += 1
            if self.rng.random() < self.p:
                self.pending = True          # this click lands next decision instead
                self.delayed += 1
            else:
                action["attack"] = True
        keep = 0.9                           # AR(1) per decision: ~1 s memory
        for i, key in enumerate(("dyaw", "dpitch")):
            new = keep * self.drift[i] + self.rng.gauss(0.0, self.drift_sd * (1 - keep * keep) ** 0.5)
            # turns are per tick for 2 ticks, so a change of offset d needs d/2 per tick
            action[key] += (new - self.drift[i]) / 2 + self.rng.gauss(0.0, self.aim_sd)
            self.drift[i] = new
        return action


class ProgramController:
    def __init__(self, fighter_cls, seed, limits=None):
        self.bot = fighter_cls(seed)
        self.limits = HumanLimits(seed, **limits) if limits else None
        self.errors = 0
        self.first_errors = []
        self.seconds = 0.0
        self.calls = 0

    def __call__(self, env, side):
        view = client_view(env, side)
        start = time.perf_counter()
        try:
            action = self.bot.act(view)
        except Exception:
            self.errors += 1
            if len(self.first_errors) < MAX_ERRORS_KEPT:
                self.first_errors.append(traceback.format_exc(limit=4)[-1500:])
            action = {}
        self.seconds += time.perf_counter() - start
        self.calls += 1
        action = sandbox.sanitize(action)
        if self.limits:
            action = self.limits(action)
            for key, cap in (("dyaw", sandbox.MAX_TURN), ("dpitch", sandbox.MAX_LOOK)):
                action[key] = max(-cap, min(cap, action[key]))
        return action


def make_controller(entry, classes, name, seed, limits=None):
    if entry["kind"] == "program":
        return ProgramController(classes[name], seed, limits)
    if entry["kind"] == "scripted":
        bot = SCRIPTED[entry["style"]][0](seed)
        return lambda env, side: bot(env.delayed_state, side)
    if entry["kind"] == "policy":
        fighter = pol.PolicyFighter(entry["path"])
        return lambda env, side: fighter(env.observation(side, fighter.obs_layout))
    raise ValueError(f"unknown entrant kind {entry['kind']}")


def _swap(steps):
    """A fight's states with the two fighters' indices exchanged (so `analyse` can read either side
    as 'you', which it puts at index 1)."""
    out = []
    for st in steps:
        events = []
        for e in st["events"]:
            e = dict(e)
            for k in ("attacker", "victim", "bot"):
                if k in e:
                    e[k] = 1 - e[k]
            events.append(e)
        winner = st["winner"] if st["winner"] < 0 else 1 - st["winner"]
        out.append({**st, "bots": [st["bots"][1], st["bots"][0]], "events": events, "winner": winner})
    return out


def numbers(oriented):
    """The measurable quantities predictions are checked on, for side 1 of oriented fights."""
    hits = crits = sprint_hits = swings = whiffs = 0
    won = total = 0
    first = first_crit = opened = 0
    wait = rush = 0
    dealt = taken = 0.0
    for f in oriented:
        timeline = []
        for st in f["steps"][1:]:
            w, r = _rushing(st["bots"][1], st["bots"][0])
            wait += w
            rush += r
            for e in st["events"]:
                if e["type"] == "hit":
                    timeline.append((e["tick"], e["attacker"], e["damage"]))
                    if e["victim"] == 1:
                        taken += e["damage"]
                    if e["attacker"] == 1:
                        dealt += e["damage"]
                        hits += 1
                        crits += int(e["crit"])
                        sprint_hits += int(e["sprint_hit"])
                elif e["type"] == "swing" and e["bot"] == 1:
                    swings += 1
                    whiffs += int(not e["hit"])
        hit_events = [e for st in f["steps"][1:] for e in st["events"] if e["type"] == "hit"]
        if hit_events:
            opened += 1
            first += int(hit_events[0]["attacker"] == 1)
            first_crit += int(hit_events[0]["attacker"] == 1 and hit_events[0]["crit"])
        ex = []
        for h in sorted(timeline, key=lambda h: h[0]):
            if ex and h[0] - ex[-1][-1][0] <= analyse.EXCHANGE_GAP_TICKS:
                ex[-1].append(h)
            else:
                ex.append([h])
        for x in ex:
            net = sum(d if a == 1 else -d for _, a, d in x)
            total += 1
            won += int(net > 0.5)
    n = max(1, len(oriented))
    return {"hits_per_fight": hits / n, "whiff_share": whiffs / max(1, swings), "crit_share": crits / max(1, hits),
            "sprint_hit_share": sprint_hits / max(1, hits), "exchanges_won_share": won / max(1, total),
            "first_hit_share": first / max(1, opened), "first_hit_crit_share": first_crit / max(1, opened),
            "rush_share": rush / wait if wait else None, **tempo(oriented), "crit_chain_share": _crit_chains(oriented),
            "net_damage_per_fight": (dealt - taken) / n, "jump_punished_share": _jumps_punished(oriented)}


def _rushing(me, op):
    """(1, 1) when the opponent is 3-6 blocks away and not coming in (and we aren't in hurt time) and we
    close in on them anyway; (1, 0) when we hold or back off; (0, 0) when the situation doesn't apply."""
    dx, dz = op["x"] - me["x"], op["z"] - me["z"]
    d = math.hypot(dx, dz)
    if not 3.0 <= d <= 6.0 or me["hurt_time"] > 0:
        return 0, 0
    op_in = (-op["vx"] * dx - op["vz"] * dz) / d > 0.05
    if op_in:
        return 0, 0
    return 1, int((me["vx"] * dx + me["vz"] * dz) / d > 0.05)


def air_swings(oriented):
    """Why side 1's swings in the air did or didn't crit: the conditions the server checks."""
    out = {"air_swings": 0, "crits": 0, "still_sprinting": 0, "still_rising": 0, "landed_first": 0, "missed": 0,
           "low_charge": 0, "ground_swings": 0}
    for f in oriented:
        steps = f["steps"]
        for i, st in enumerate(steps[1:], 1):
            prev, cur = steps[i - 1]["bots"][1], st["bots"][1]
            for e in st["events"]:
                if e["type"] != "swing" or e["bot"] != 1:
                    continue
                if prev["on_ground"]:
                    out["ground_swings"] += 1
                    continue
                out["air_swings"] += 1
                if any(h["type"] == "hit" and h["attacker"] == 1 and h["crit"] for h in st["events"]):
                    out["crits"] += 1
                    continue
                out["still_sprinting"] += int(prev["sprinting"])
                out["still_rising"] += int(prev["vy"] >= 0 and cur["vy"] >= -0.08)
                out["landed_first"] += int(cur["on_ground"])
                out["missed"] += int(not e["hit"])
                out["low_charge"] += int(e["charge"] < 0.9)
    return out


def breakdown(fights):
    """`analyse` rows for one entrant, from fights [(steps, its side)]: (label, opponent, this bot)."""
    oriented = [{"steps": steps if side == 1 else _swap(steps)} for steps, side in fights]
    rows, spacing, _ = analyse.analyse(oriented)
    return {"rows": [[label, opp, me] for label, opp, me in rows], "numbers": numbers(oriented), "air": air_swings(oriented),
            "spacing": {f"{lo:g}-{hi:g}" if hi < 99 else f">{lo:g}": share for (lo, hi), share in spacing.items()}}


def run(spec):
    classes = {}
    load_errors = {}
    for name, entry in spec["entrants"].items():
        if entry["kind"] == "program":
            try:
                classes[name] = sandbox.load(entry["code"], name)
            except Exception as e:
                load_errors[name] = f"{type(e).__name__}: {e}"
    env = SwordArenaEnv(port=spec["port"], max_ticks=spec.get("fight_ticks", 600), seed=spec.get("seed", 0))
    results, traces = [], []
    seed = spec.get("seed", 0) * 1000
    try:
        for a, b, n in spec["matchups"]:
            if a in load_errors or b in load_errors:
                results.append({"a": a, "b": b, "fights": 0, "skipped": "load error"})
                continue
            scores, fights_a, fights_b = [], [], []
            prog = {a: {"errors": 0, "first_errors": [], "seconds": 0.0, "calls": 0},
                    b: {"errors": 0, "first_errors": [], "seconds": 0.0, "calls": 0}}
            for k in range(n):
                seed += 1
                a_side = k % 2
                ctl = [None, None]
                ctl[a_side] = make_controller(spec["entrants"][a], classes, a, seed, spec.get("human_limits"))
                ctl[1 - a_side] = make_controller(spec["entrants"][b], classes, b, seed + 7919, spec.get("human_limits"))
                env.reset()
                steps = [env.state]
                while env.agents:
                    env.step({agent: ctl[s](env, s) for s, agent in enumerate(env.possible_agents)})
                    steps.append(env.state)
                scores.append(outcome_score(env.state, a_side))
                fights_a.append((steps, a_side))
                fights_b.append((steps, 1 - a_side))
                for name, c in ((a, ctl[a_side]), (b, ctl[1 - a_side])):
                    if isinstance(c, ProgramController):
                        p = prog[name]
                        p["errors"] += c.errors
                        p["seconds"] += c.seconds
                        p["calls"] += c.calls
                        p["first_errors"] = (p["first_errors"] + c.first_errors)[:MAX_ERRORS_KEPT]
                if spec.get("trace_file"):
                    traces.append({"a": a, "b": b, "a_side": a_side, "score_a": scores[-1], "steps": steps})
            row = {"a": a, "b": b, "fights": n, "score_a": sum(scores) / max(1, n), "scores_a": scores}
            for name, fights, key in ((a, fights_a, "a_view"), (b, fights_b, "b_view")):
                if spec["entrants"][name]["kind"] == "program":
                    p = prog[name]
                    row[key] = {**breakdown(fights), "errors": p["errors"], "first_errors": p["first_errors"],
                                "ms_per_decision": 1000 * p["seconds"] / max(1, p["calls"])}
            results.append(row)
            print(f"  {a} vs {b}: {row['score_a']:.2f} over {n} fights", flush=True)
    finally:
        env.close()
    if spec.get("trace_file") and traces:
        with gzip.open(spec["trace_file"], "wt", encoding="utf-8") as fh:
            json.dump({"repeat": env.repeat, "fights": traces}, fh)
    return {"load_errors": load_errors, "matchups": results}


def tempo(oriented):
    """Side 1's swing tempo: median ticks since its own previous swing, overall and right after being hit
    (a swing within 12 ticks of taking a hit). Charge refills in ~11-12 ticks, so ~12 is as fast as full-charge
    swings go."""
    import statistics
    gaps, after_hit = [], []
    for f in oriented:
        last, hurt = None, -99
        for st in f["steps"][1:]:
            for e in st["events"]:
                if e["type"] == "hit" and e["victim"] == 1:
                    hurt = e["tick"]
            for e in st["events"]:
                if e["type"] == "swing" and e["bot"] == 1:
                    if last is not None:
                        gaps.append(e["tick"] - last)
                        if e["tick"] - hurt <= 12:
                            after_hit.append(e["tick"] - last)
                    last = e["tick"]
    med = lambda xs: statistics.median(xs) if xs else None
    return {"swing_gap": med(gaps), "swing_gap_after_hit": med(after_hit)}


def _crit_chains(oriented):
    """Share of side 1's crits that follow another crit of its own within 16 ticks with no hit taken in between
    (crit chains / crit spam). None if it landed no crits."""
    crits = chained = 0
    for f in oriented:
        last_crit = None
        for st in f["steps"][1:]:
            for e in st["events"]:
                if e["type"] != "hit":
                    continue
                if e["victim"] == 1:
                    last_crit = None
                elif e["attacker"] == 1:
                    if e["crit"]:
                        crits += 1
                        chained += int(last_crit is not None and e["tick"] - last_crit <= 16)
                        last_crit = e["tick"]
                    else:
                        last_crit = None
    return chained / crits if crits else None

def _jumps_punished(oriented):
    """Of side 1's real jumps (left the ground upward, not from knockback), the share where it was hit before
    landing a crit or touching down again. None if it never jumped."""
    jumps = punished = 0
    for f in oriented:
        steps = f["steps"]
        i = 1
        while i < len(steps):
            p, c = steps[i - 1]["bots"][1], steps[i]["bots"][1]
            if p["on_ground"] and not c["on_ground"] and c["vy"] > 0.3 and c["hurt_time"] == 0:
                jumps += 1
                k = i
                while k < len(steps):
                    ev = steps[k]["events"]
                    if any(e["type"] == "hit" and e["attacker"] == 1 and e["crit"] for e in ev):
                        break
                    if any(e["type"] == "hit" and e["victim"] == 1 for e in ev):
                        punished += 1
                        break
                    if k > i and steps[k]["bots"][1]["on_ground"]:
                        break
                    k += 1
                i = k + 1
            else:
                i += 1
    return punished / jumps if jumps else None

if __name__ == "__main__":
    spec_file, out_file = sys.argv[1], sys.argv[2]
    with open(spec_file, encoding="utf-8") as fh:
        spec = json.load(fh)
    out = run(spec)
    with open(out_file, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
