"""You vs a strategist's bot, first to N, on the agentic server (join localhost:25596 first).

  python -m vertex_training agentic duel [--bot best|rusher|rusher:v01] [--first-to 5] [--seconds 120]

A point is a kill; a fight that reaches the time limit goes to whoever leads on health (a draw on
health gives no point). The bot sees what it sees in the tournament (opponent 200 ms late, no hidden
state). Saved in the run's duels/ folder (not selfplay/duels, so the RL trainer's you-clone data is
untouched), with the `arena analyse` breakdown from your side.
"""

import datetime
import gzip
import json

from ..arena.env import SwordArenaEnv, outcome_score
from . import match, sandbox
from .loop import RUNS_DIR, _read

COUNTDOWN_TICKS = 60
MAX_FIGHTS_PER_POINT = 3     # stop after first_to * 3 fights even if nobody got there (draws)


def pick_bot(spec="best", run=None):
    """→ (run dir, bot name, version). spec: best (highest anchor score played), NAME (its current
    version), or NAME:vNN."""
    runs = sorted(p for p in RUNS_DIR.glob("*") if (p / "state.json").exists()) if RUNS_DIR.exists() else []
    if not runs:
        raise FileNotFoundError(f"no agentic runs in {RUNS_DIR}: run `agentic run` first")
    path = RUNS_DIR / run if run else runs[-1]
    state = _read(path / "state.json", {})
    if spec == "best":
        played = [(info["anchors"], name, int(v)) for name, vs in state["versions"].items()
                  for v, info in vs.items() if info.get("anchors") is not None]
        if not played:
            name = sorted(state["current"])[0]
            return path, name, state["current"][name]
        _, name, version = max(played)
        return path, name, version
    name, _, v = spec.partition(":")
    if name not in state["current"]:
        raise ValueError(f"no bot {name!r} in {path.name}; bots: {', '.join(sorted(state['current']))}")
    return path, name, int(v.lstrip("v")) if v else state["current"][name]


def duel(bot="best", first_to=5, seconds=120, port=25600, run=None, log=print):
    path, name, version = pick_bot(bot, run)
    code_file = path / "bots" / name / f"v{version:02d}.py"
    code = code_file.read_text(encoding="utf-8")
    sandbox.check(code)
    fighter_cls = sandbox.load(code, name)
    meta = _read(code_file.with_suffix(".json"), {})
    fight_ticks = int(seconds * 20)
    env = SwordArenaEnv(port=port, max_ticks=fight_ticks, seed=int(datetime.datetime.now().timestamp()))
    env.client = None
    points = [0, 0]           # [bot, you]
    results, traces = [], []
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    ctl = None
    try:
        env.reset()           # opens the connection (a normal bot fight, never stepped)
        env.client.duel(True)
        env.client.realtime(True)
        log(f"duel: you vs {name} v{version:02d} ({path.name}), first to {first_to}, fights up to {seconds} s")
        if meta.get("hypothesis"):
            log(f"  its latest idea: {meta['hypothesis'][:200]}")
        f = 0
        while max(points) < first_to and f < first_to * MAX_FIGHTS_PER_POINT:
            env.reset(options={"countdown": COUNTDOWN_TICKS, "start_distance": (6.0, 10.0)})
            if not env.state.get("duel"):
                log("you left the server: duel over")
                break
            ctl = match.ProgramController(fighter_cls, seed=f, limits=_read(path / "config.json", {}).get("human_limits", dict(match.HUMAN_LIMITS)))
            steps = [env.state]
            while env.agents:
                env.step({"bot_0": ctl(env, 0), "bot_1": {}})
                steps.append(env.state)
            if not env.state.get("duel"):
                log("you left the server: duel over")
                break
            score = outcome_score(env.state, 1)
            hits = [sum(1 for st in steps[1:] for e in st["events"] if e["type"] == "hit" and e["attacker"] == i)
                    for i in (0, 1)]
            if score > 0.5:
                points[1] += 1
            elif score < 0.5:
                points[0] += 1
            how = ("you killed it" if score == 1 else "it killed you" if score == 0 else
                   f"time, {'yours' if score > 0.5 else 'its' if score < 0.5 else 'draw'} on health")
            log(f"  fight {f + 1}: {how} ({hits[1]} hits to {hits[0]}, {env.state['tick'] / 20:.1f} s)  "
                f"you {points[1]} - {points[0]} {name}"
                + (f"  [bot errors: {ctl.errors}]" if ctl.errors else ""))
            traces.append({"fight": f, "steps": steps})
            results.append({"fight": f, "you_score": score, "seconds": env.state["tick"] / 20,
                            "your_hits": hits[1], "bot_hits": hits[0], "bot_errors": ctl.errors})
            f += 1
    finally:
        try:
            if env.client is not None:
                env.client.duel(False)
        finally:
            env.close()
    if not results:
        return results
    winner = "you" if points[1] > points[0] else name if points[0] > points[1] else "nobody"
    log(f"result: you {points[1]} - {points[0]} {name} v{version:02d}: {winner} "
        f"{'won' if winner != 'nobody' else 'won (draw)'}")
    out = path / "duels"
    out.mkdir(parents=True, exist_ok=True)
    with gzip.open(out / f"duel-{stamp}.trace.json.gz", "wt", encoding="utf-8") as fh:
        json.dump({"bot": 0, "you": 1, "repeat": env.repeat, "bot_name": name, "version": version,
                   "fights": traces}, fh)
    report = match.breakdown([(t["steps"], 1) for t in traces])
    log_file = out / "results.json"
    history = _read(log_file, [])
    history.append({"duel": stamp, "bot": name, "version": version, "first_to": first_to, "seconds": seconds,
                    "points": {"you": points[1], "bot": points[0]}, "fights": results, "breakdown": report})
    log_file.write_text(json.dumps(history, indent=1), encoding="utf-8")
    log("\nwhere the fights went (you vs the bot):")
    for label, opp, me in report["rows"]:
        log(f"  {label:<44} you {me:<10} bot {opp}")
    log(f"saved in {out}")
    from .loop import Run
    verdict = Run(name=path.name, log=log).check_owner(name, version)
    if verdict:
        log(f"its prediction about you: {verdict}")
    log("the strategists read this duel in their next briefing (agentic run --resume " + path.name + ")")
    return results
