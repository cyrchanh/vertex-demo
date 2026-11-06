"""The rounds: tournament â†’ per-bot results â†’ verdicts on last round's predictions â†’ new versions.

Storage, under selfplay/agentic/<run>/:
  config.json                 strategists (name, persona, backend, model, effort), fights per matchup, port
  state.json                  round reached, each strategist's current version, scores per version, API usage
  notebook.json               the shared notebook: every hypothesis with its prediction and measured verdict
  bots/<name>/vNN.py          each version's code; vNN.json its diagnosis, hypothesis, prediction, change
  rounds/round-NN.json        the tournament's full results; round-NN.trace.json.gz every fight's states
"""

import datetime
import json
import subprocess
import sys
import time
from pathlib import Path

from .. import config as project_config
from ..arena import play
from . import owner, sandbox, strategist, techniques
from .view import client_view  # noqa: F401  (documents the view the strategists are told about)

RUNS_DIR = play.SELFPLAY_DIR / "agentic"
TRAINING_DIR = Path(__file__).resolve().parents[2]
STARTER = Path(__file__).with_name("starter.py")
ANCHORS = {"chase": {"kind": "scripted", "style": "chase"}, "crit": {"kind": "scripted", "style": "crit"},
           "punish": {"kind": "scripted", "style": "punish"}, "eager": {"kind": "scripted", "style": "eager"}}
DEFAULT_STRATEGISTS = [
    {"name": "rusher", "backend": "claude", "model": "claude-opus-5-5", "effort": "high",
     "persona": "Your style is pressure: close distance fast, win the first hit, and chain hits into combos with "
                "sprint resets. Stay recognisably aggressive; improve within that style."},
    {"name": "spacer", "backend": "claude", "model": "claude-opus-5-5", "effort": "high",
     "persona": "Your style is spacing and punishing: hover just outside reach, bait swings, and strike during the "
                "opponent's whiff lockout or recovery. Stay recognisably patient; improve within that style."},
    {"name": "trickster", "backend": "claude", "model": "claude-opus-5-5", "effort": "high",
     "persona": "Your style is variety and reading the opponent: crits, strafes, changes of rhythm, and adapting "
                "within a fight to what this opponent keeps doing. Stay recognisably unpredictable; improve within "
                "that style."},
]
NOTEBOOK_SHOWN = 30
MAX_ATTEMPTS = 3
# Keep or revert (as in autoresearch): each strategist has a base version, the last one kept. A new
# version plays the anchors in the same round as its base (same seed, paired) and plays the base
# head to head. It replaces the base only if its anchor mean is at most ANCHOR_SLACK below the
# base's and it scores at least 0.5 head to head; otherwise the next version is written from the
# base's code. "No worse" rather than "better": the anchors are near their ceiling, so demanding a
# gain would freeze the bots, while this stops the drift (rusher and spacer v02-v06 all scored below
# their v01). ANCHOR_SLACK is a guess at the paired noise of 5 anchors x `fights`; the decision line
# prints how much the base's own score moved between rounds, which is the check on it.
ANCHOR_SLACK = 0.03
OWNER_MARGIN = 1.0     # HP per fight better than the base vs the owner
OWNER_FLOOR = 0.08      # ...while at most this far below the base vs the anchors
# A prediction must ask for at least this much change from where the bot is now (else noise can "support" it)
MIN_STEP = {"score": 0.05, "hits_per_fight": 1.0, "net_damage_per_fight": 1.0, "swing_gap": 1.0, "swing_gap_after_hit": 1.0}
MIN_STEP_SHARE = 0.05   # for every share metric


def match_limits():
    from .match import HUMAN_LIMITS
    return dict(HUMAN_LIMITS)


def _now():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def _read(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


class Run:
    def __init__(self, name=None, strategists=None, fights=6, port=25600, backend=None, champion=True, log=print):
        self.log = log
        self.allow_untested = False
        if name and (RUNS_DIR / name / "config.json").exists():
            self.dir = RUNS_DIR / name
            self.config = _read(self.dir / "config.json", {})
            if backend:       # e.g. resume a claude run with noop to re-check the tournament only
                for s in self.config["strategists"]:
                    s["backend"] = backend
        else:
            self.dir = RUNS_DIR / (name or f"agentic-{_now()}")
            strategists = [dict(s) for s in (strategists or DEFAULT_STRATEGISTS)]
            if backend:
                for s in strategists:
                    s["backend"] = backend
            anchors = dict(ANCHORS)
            if champion and play.CHAMPION_FILE.exists():
                anchors["champion"] = {"kind": "policy", "path": str(play.CHAMPION_FILE)}
            self.config = {"strategists": strategists, "fights": fights, "port": port, "anchors": anchors,
                           "created": _now()}
            _write(self.dir / "config.json", self.config)
        self.state = _read(self.dir / "state.json", {"round": 0, "current": {}, "versions": {}, "usage": []})
        self.notebook = _read(self.dir / "notebook.json", [])
        for s in self.config["strategists"]:
            if s["name"] not in self.state["current"]:
                self._save_version(s["name"], 0, STARTER.read_text(encoding="utf-8"),
                                   {"hypothesis": "starter bot", "change": "none", "prediction": None}, round_=0)
        base = self.state.setdefault("base", {})
        for s in self.config["strategists"]:
            name = s["name"]
            if name not in base:      # runs from before keep/revert: the best played version so far
                played = [(i["anchors"], int(v)) for v, i in self.state["versions"][name].items()
                          if i.get("anchors") is not None]
                base[name] = max(played)[1] if played else self.state["current"][name]
        self._save_state()

    # ---- storage ----

    def _bot_file(self, name, version):
        return self.dir / "bots" / name / f"v{version:02d}.py"

    def _save_version(self, name, version, code, meta, round_):
        path = self._bot_file(name, version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
        _write(path.with_suffix(".json"), {**meta, "round_written": round_})
        self.state["current"][name] = version
        self.state["versions"].setdefault(name, {})[str(version)] = {"written": round_}

    def _code(self, name, version):
        return self._bot_file(name, version).read_text(encoding="utf-8")

    def _meta(self, name, version):
        return _read(self._bot_file(name, version).with_suffix(".json"), {})

    def _save_state(self):
        _write(self.dir / "state.json", self.state)
        _write(self.dir / "notebook.json", self.notebook)

    # ---- one round ----

    def tournament(self, round_):
        cfg, n = self.config, self.config["fights"]
        names = [s["name"] for s in cfg["strategists"]]
        entrants, matchups = dict(cfg["anchors"]), []
        for name in names:
            v, b = self.state["current"][name], self.state["base"][name]
            entrants[name] = {"kind": "program", "code": self._code(name, v)}
            if b != v:       # the new version vs its base: head to head, and the base vs the anchors
                key = self._base_key(name)
                entrants[key] = {"kind": "program", "code": self._code(name, b)}
                matchups.append([name, key, n])
                matchups += [[key, anchor, n] for anchor in cfg["anchors"]]
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                matchups.append([a, b, n])
            for anchor in cfg["anchors"]:
                matchups.append([a, anchor, n])
        rounds_dir = self.dir / "rounds"
        rounds_dir.mkdir(parents=True, exist_ok=True)
        spec = {"port": cfg["port"], "fight_ticks": 600, "seed": round_ + 1, "entrants": entrants,
                "matchups": matchups, "human_limits": cfg.get("human_limits", match_limits()), "trace_file": str(rounds_dir / f"round-{round_:02d}.trace.json.gz")}
        spec_file, out_file = rounds_dir / f"round-{round_:02d}.spec.json", rounds_dir / f"round-{round_:02d}.json"
        _write(spec_file, spec)
        fights = sum(m[2] for m in matchups)
        self.log(f"round {round_}: tournament, {len(matchups)} matchups, {fights} fights on port {cfg['port']}")
        start = time.time()
        proc = subprocess.run([sys.executable, "-m", "vertex_training.agentic.match", str(spec_file), str(out_file)],
                              cwd=TRAINING_DIR, timeout=120 + 30 * fights, text=True, capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError(f"tournament failed (is the agentic arena server up on port {cfg['port']}? "
                               f"training/agentic-arena.ps1 start):\n{proc.stderr[-3000:]}")
        self.log(f"  done in {time.time() - start:.0f} s")
        return _read(out_file, {})

    def summarise(self, results):
        """â†’ {strategist: {"opponents": {opp: {score, fights, numbers, rows, ...}}, "anchors": mean, "pool": mean}}."""
        # "vs_prev" is the score vs its base; "base" is the base's own summary when it played this round
        names = [s["name"] for s in self.config["strategists"]]
        out = {name: {"opponents": {}} for name in names}
        bases = {self._base_key(n): n for n in names if self.state["base"][n] != self.state["current"][n]}
        out.update({key: {"opponents": {}} for key in bases})
        for m in results["matchups"]:
            if not m.get("fights"):
                continue
            for me, opp, key, score, per in ((m["a"], m["b"], "a_view", m["score_a"], m["scores_a"]),
                                             (m["b"], m["a"], "b_view", 1 - m["score_a"], [1 - x for x in m["scores_a"]])):
                if me in out:
                    out[me]["opponents"][opp] = {"score": score, "fights": m["fights"], "scores": per, **m.get(key, {})}
        for who in out:         # the owner's duels against that version, if any
            name = bases.get(who, who)
            fights = owner.load(self.dir, name, self.state["base" if who in bases else "current"][name])
            if fights:
                out[who]["opponents"]["owner"] = owner.as_opponent(fights)
        for who, s in out.items():
            name = bases.get(who, who)
            anchors = [s["opponents"][a]["score"] for a in self.config["anchors"] if a in s["opponents"]]
            pool = [s["opponents"][o]["score"] for o in names if o in s["opponents"] and o != name]
            s["anchors"] = sum(anchors) / len(anchors) if anchors else None
            s["pool"] = sum(pool) / len(pool) if pool else None
            prev = [o for o in s["opponents"] if o.startswith(f"{name}@")]
            s["vs_prev"] = s["opponents"][prev[0]]["score"] if prev else None
        for key, name in bases.items():
            out[name]["base"] = out.pop(key)
        return out

    def _base_key(self, name):
        return f"{name}@v{self.state['base'][name]:02d}"

    def _source(self, summary, name):
        """Results of the version the next one is written from: the base (after decide())."""
        if self.state["base"][name] == self.state["current"][name] or "base" not in summary[name]:
            return summary[name]
        return summary[name]["base"]

    def decide(self, round_, summary):
        """Keep or revert each new version against its base (see ANCHOR_SLACK)."""
        f = lambda x: "-" if x is None else f"{x:.2f}"
        for s in self.config["strategists"]:
            name = s["name"]
            v, b = self.state["current"][name], self.state["base"][name]
            if v == b or "base" not in summary[name]:
                continue
            cand, base = summary[name]["anchors"], summary[name]["base"]["anchors"]
            h2h = summary[name]["vs_prev"]
            keep = None not in (cand, base, h2h) and cand >= base - ANCHOR_SLACK and h2h >= 0.5
            # The owner is the target (owner's choice, 2026-11-06): a version that out-trades its base against the
            # owner by OWNER_MARGIN HP per fight is kept too, if it stays within OWNER_FLOOR of the base vs anchors.
            own = summary[name]["opponents"].get("owner"), summary[name]["base"]["opponents"].get("owner")
            nd = [o["numbers"].get("net_damage_per_fight") if o else None for o in own]
            keep_owner = (not keep and None not in (cand, base, *nd) and nd[0] >= nd[1] + OWNER_MARGIN
                          and cand >= base - OWNER_FLOOR)
            keep = keep or keep_owner
            earlier =self.state["versions"][name].get(str(b), {}).get("anchors")
            need = None if base is None else base - ANCHOR_SLACK
            text = (f"v{v:02d} anchors {f(cand)} vs base v{b:02d} {f(base)} this round (need >= {f(need)}), "
                    f"head to head {f(h2h)} (need >= 0.50): "
                    + ("KEPT, new base" if keep else f"NOT KEPT, base stays v{b:02d}")
                    + f" [noise check: v{b:02d} scored {f(earlier)} vs anchors when it first played]")
            own = summary[name]["opponents"].get("owner"), summary[name]["base"]["opponents"].get("owner")
            if all(own):
                text += (f" [vs the owner: net damage per fight v{v:02d} {nd[0]:+.1f}, v{b:02d} {nd[1]:+.1f}"
                         + (f"; KEPT for the owner result (needs +{OWNER_MARGIN:g} HP and anchors >= {f(base - OWNER_FLOOR)})"
                            if keep_owner else "") + "]")
            self.state["versions"][name][str(v)].update({"kept": keep, "decision": text})
            self.state["versions"][name].setdefault(str(b), {}).setdefault("rechecks", []).append(
                {"round": round_, "anchors": base})
            for e in self.notebook:
                if e["strategist"] == name and e["version"] == v and e.get("from") != "owner duel":
                    e["kept"] = keep
            if keep:
                self.state["base"][name] = v
            self.log(f"  {name}: {text}")

    def _metric(self, summary, opponent, metric):
        anchors = list(self.config["anchors"])
        opps = anchors if opponent == "anchors" else [opponent]
        vals = []
        for o in opps:
            r = summary["opponents"].get(o)
            if r is None:
                continue
            vals.append(r["score"] if metric == "score" else r.get("numbers", {}).get(metric))
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    def _baseline(self, name, summary, pred):
        """Where the bot is now on the predicted metric: this round's value, or for the owner, the most
        recent version the owner has duelled."""
        value = self._metric(self._source(summary, name), pred["opponent"], pred["metric"])
        if value is None:     # the base didn't play this opponent (it plays only the anchors and the new version)
            value = self._metric(summary[name], pred["opponent"], pred["metric"])
        if value is None and pred["opponent"] == "owner":
            for v in range(self.state["base"][name], -1, -1):
                fights = owner.load(self.dir, name, v)
                if fights:
                    return self._metric({"opponents": {"owner": owner.as_opponent(fights)}}, "owner", pred["metric"])
        return value

    def _owner_duelled(self, name):
        """True once the owner has duelled any version of this bot: from then on its predictions are about
        the owner (bots are the practice; the owner is the target)."""
        return bool(owner.load(self.dir, name))

    def _owner_rule(self, name, summary, pred):
        if self._owner_duelled(name) and pred["opponent"] != "owner":
            return ("the owner has duelled your bot, so your prediction must be about the owner "
                    "(opponent 'owner'); keep the anchor and pool scores in mind, but the claim to test is "
                    "what this version does against the owner")
        return None

    def check_owner(self, name, version):
        """After a duel: the verdict on this version's prediction if it was about the owner. â†’ text or None."""
        meta = self._meta(name, version)
        pred = meta.get("prediction")
        if not pred or pred["opponent"] != "owner" or meta.get("verdict"):
            return None
        fights = owner.load(self.dir, name, version)
        value = self._metric({"opponents": {"owner": owner.as_opponent(fights)}}, "owner", pred["metric"])
        ok = value > pred["threshold"] if pred["direction"] == "up" else value < pred["threshold"]
        verdict = "supported" if ok else "refuted"
        entry = {"round": self.state["round"], "strategist": name, "version": version, "hypothesis": meta.get("hypothesis"),
                 "prediction": pred, "baseline": meta.get("baseline"), "measured": value, "verdict": verdict,
                 "anchor_score": self.state["versions"][name].get(str(version), {}).get("anchors"),
                 "vs_previous_version": None, "from": "owner duel"}
        meta["verdict"] = entry
        _write(self._bot_file(name, version).with_suffix(".json"), meta)
        self.notebook.append(entry)
        self._save_state()
        return (f"{name} v{version:02d} predicted {pred['metric']} vs you {pred['direction']} past "
                f"{pred['threshold']}: measured {value:.2f} -> {verdict}")

    def verdicts(self, round_, summary):
        """Checks each current version's prediction (made when it was written) against this round."""
        for s in self.config["strategists"]:
            name, v = s["name"], self.state["current"][s["name"]]
            meta = self._meta(name, v)
            pred = meta.get("prediction")
            if not pred or meta.get("verdict"):
                continue
            value = self._metric(summary[name], pred["opponent"], pred["metric"])
            if value is None and pred["opponent"] == "owner":
                continue          # waits for the owner to duel this version (checked at the duel's end)
            if value is None:
                verdict = "untestable (no such opponent or metric this round)"
            else:
                ok = value > pred["threshold"] if pred["direction"] == "up" else value < pred["threshold"]
                verdict = "supported" if ok else "refuted"
            before = meta.get("baseline")
            entry = {"round": round_, "strategist": name, "version": v, "hypothesis": meta.get("hypothesis"),
                     "prediction": pred, "baseline": before, "measured": value, "verdict": verdict,
                     "anchor_score": summary[name]["anchors"], "vs_previous_version": summary[name]["vs_prev"]}
            meta["verdict"] = entry
            _write(self._bot_file(name, v).with_suffix(".json"), meta)
            self.notebook.append(entry)
            measured = "n/a" if value is None else f"{value:.2f}"
            self.log(f"  {name} v{v:02d}: {verdict} ({pred['metric']} vs {pred['opponent']} {pred['direction']} "
                     f"past {pred['threshold']}: measured {measured})")

    def leaderboard(self, round_, summary):
        self.log(f"  {'bot':12} {'version':>7} {'vs anchors':>11} {'vs pool':>8} {'vs prev':>8}   per anchor")
        for name, s in sorted(summary.items(), key=lambda kv: -(kv[1]["anchors"] or 0)):
            per = "  ".join(f"{a} {s['opponents'][a]['score']:.2f}" for a in self.config["anchors"] if a in s["opponents"])
            f = lambda x: "-" if x is None else f"{x:.2f}"
            self.log(f"  {name:12} {'v%02d' % self.state['current'][name]:>7} {f(s['anchors']):>11} "
                     f"{f(s['pool']):>8} {f(s['vs_prev']):>8}   {per}")
            self.state["versions"][name][str(self.state["current"][name])].update(
                {"round_played": round_, "anchors": s["anchors"], "pool": s["pool"], "vs_prev": s["vs_prev"]})

    # ---- reasoning ----

    def briefing(self, s, round_, summary):
        name = s["name"]
        v = self.state["current"][name]
        meta = self._meta(name, v)
        mine = summary[name]
        lines = [f"# Round {round_}: briefing for strategist '{name}'", "", "## Your style", s["persona"], ""]
        b = self.state["base"][name]
        lines += ["## Your versions so far",
                  "| version | anchors | pool | vs its base (earlier: vs previous) | hypothesis verdict | kept |",
                  "|---|---|---|---|---|---|"]
        f = lambda x: "-" if x is None else f"{x:.2f}"
        for ver, info in sorted(self.state["versions"][name].items(), key=lambda kv: int(kv[0])):
            vm = self._meta(name, int(ver))
            verdict = (vm.get("verdict") or {}).get("verdict", "-")
            kept = {True: "yes", False: "no"}.get(info.get("kept"), "-") + (" (base)" if int(ver) == b else "")
            lines.append(f"| v{int(ver):02d} | {f(info.get('anchors'))} | {f(info.get('pool'))} | "
                         f"{f(info.get('vs_prev'))} | {verdict} | {kept} |")
        lines += ["", f"## The version that just played (v{v:02d})",
                  f"- hypothesis: {meta.get('hypothesis')}", f"- change: {meta.get('change')}"]
        if meta.get("verdict"):
            vd = meta["verdict"]
            lines.append(f"- verdict: {vd['verdict']} (measured {f(vd['measured'])}, prediction {json.dumps(vd['prediction'])})")
        decision = self.state["versions"][name].get(str(v), {}).get("decision")
        if decision:
            lines += [f"- keep or revert: {decision}",
                      "  (A version replaces your base only if it scores no more than "
                      f"{ANCHOR_SLACK} below the base vs the anchors in the same round and at least 0.5 head to head. "
                      "A supported prediction is not enough: the change must not make the bot worse overall.)"]
            if b != v and "base" in mine:
                per = ", ".join(f"{a} {mine['base']['opponents'][a]['score']:.2f}"
                                for a in self.config["anchors"] if a in mine["base"]["opponents"])
                lines.append(f"- your base v{b:02d} this round vs the anchors: {per}")
        if b != v:
            lines.append(f"\n**Your next version is written from your base v{b:02d} (its code is below), not from v{v:02d}.** "
                         f"v{v:02d}'s results are shown so you can learn from what went wrong.")
        lines += ["", "## Results this round (score: 1 win, 0 loss; time-outs scored on health lead)"]
        for opp, r in sorted(mine["opponents"].items(), key=lambda kv: kv[1]["score"]):
            kind = ("the owner: a skilled human, real-time duels" if opp == "owner" else "anchor"
                    if opp in self.config["anchors"] else ("your previous version" if "@" in opp else "strategist"))
            lines += ["", f"### vs {opp} ({kind}): score {r['score']:.2f} over {r['fights']} fights "
                          f"(per fight: {', '.join(f'{x:.2f}' for x in r.get('scores', [])) or '-'})"]
            if r.get("rows"):
                lines += ["| | opponent | you |", "|---|---|---|"] + [f"| {a} | {b} | {c} |" for a, b, c in r["rows"]]
                lines.append("time at each distance: " + ", ".join(f"{k} blocks {x:.0%}" for k, x in r["spacing"].items()))
                rs = r.get("numbers", {}).get("rush_share")
                if rs is not None:
                    lines.append(f"rush share (yours): {rs:.0%} of the time they were 3-6 blocks away and not coming in, "
                                 "you closed in on them anyway")
                sg, sga = r.get("numbers", {}).get("swing_gap"), r.get("numbers", {}).get("swing_gap_after_hit")
                if sg is not None:
                    lines.append(f"swing tempo (yours): median {sg:.0f} ticks between swings"
                                 + (f", {sga:.0f} right after being hit" if sga is not None else "")
                                 + " (charge refills in ~11-12 ticks)")
                nd = r.get("numbers", {}).get("net_damage_per_fight")
                if nd is not None:
                    lines.append(f"net damage (yours): {nd:+.1f} HP per fight (dealt minus taken)")
                jp = r.get("numbers", {}).get("jump_punished_share")
                if jp is not None:
                    lines.append(f"jumps punished (yours): {jp:.0%} of your jumps got hit before a crit or landing")
                cc = r.get("numbers", {}).get("crit_chain_share")
                if cc is not None:
                    lines.append(f"crit chains (yours): {cc:.0%} of your crits came right after another crit of yours")
                a = r.get("air")
                if a and a["air_swings"]:
                    lines.append(f"air swings (yours): {a['air_swings']} of {a['air_swings'] + a['ground_swings']} swings; "
                                 f"{a['crits']} crits. Non-crit air swings were: still sprinting {a['still_sprinting']}, "
                                 f"still rising {a['still_rising']}, landed first {a['landed_first']}, missed {a['missed']}, "
                                 f"charge < 0.9 {a['low_charge']} (one swing can have several reasons)")
            if r.get("errors"):
                lines.append(f"**your act() raised {r['errors']} times**; first: ```{r['first_errors'][0]}```")
            if r.get("ms_per_decision", 0) > 5:
                lines.append(f"**slow: {r['ms_per_decision']:.1f} ms per decision**")
        lines.append(f"\nMean vs anchors {f(mine['anchors'])}, vs other strategists {f(mine['pool'])}, "
                     f"vs your previous version {f(mine['vs_prev'])}.")
        all_duels = owner.load(self.dir)
        lines += ["", "## How the owner plays (shared: measured from every duel in this run, all bots)"]
        if all_duels:
            mine_duels = [d for d in all_duels if d[3] == name]
            lines.append(f"Your bot's duels vs the owner: {len(mine_duels)} fights over all versions; the version "
                         f"that just played: {mine['opponents'].get('owner', {}).get('fights', 0)}. "
                         "Your score vs the owner, and predictions about the owner, are measured when the owner "
                         "next duels your newest version.")
            if mine_duels:
                lines.append("**The owner has duelled your bot: this round your prediction must name opponent "
                             "'owner'.** Use the bot results to stay sound, but aim the change at the owner.")
            lines += owner.habits(all_duels)
        else:
            lines.append("(no duels yet)")
        lines += [""] + techniques.briefing_lines()
        owner_notes = owner.notes(self.dir)
        if owner_notes:
            lines += ["", "## The owner's notes (a skilled player's own observations after duelling the bots: "
                          "strong hints, but test them like any hypothesis)"]
            lines += [f"- ({n['at']}, after round {n['round']}) {n['text']}" for n in owner_notes[-10:]]
        shown = self.notebook[-NOTEBOOK_SHOWN:]
        lines += ["", "## Shared notebook (every strategist's hypotheses and their measured verdicts; code stays private)"]
        if not shown:
            lines.append("(empty so far)")
        for e in shown:
            kept = {True: "; kept", False: "; not kept"}.get(e.get("kept"), "")
            lines.append(f"- r{e['round']} {e['strategist']} v{e['version']:02d}: \"{e['hypothesis']}\" -> {e['verdict']} "
                         f"(measured {f(e['measured'])}; anchors {f(e['anchor_score'])}{kept})")
        lines += ["", f"## Your base code (v{b:02d}): write the next version from this", "```python", self._code(name, b),
                  "```", "", "Diagnose, hypothesise, and write the next version."]
        return "\n".join(lines)

    def untested_owner(self, name):
        """The current version's prediction is about the owner and no duel has checked it yet."""
        v = self.state["current"][name]
        pred = self._meta(name, v).get("prediction") or {}
        return pred.get("opponent") == "owner" and not self._meta(name, v).get("verdict") and not owner.load(self.dir, name, v)

    def reason(self, round_, summary):
        for s in self.config["strategists"]:
            name = s["name"]
            if not self.allow_untested and self.untested_owner(name):
                self.log(f"  {name}: v{self.state['current'][name]:02d}'s prediction about you is untested, so no rewrite "
                         f"(duel it: agentic duel --bot {name}; or pass --allow-untested)")
                continue
            backend =strategist.backend(s["backend"], **({"model": s["model"], "effort": s["effort"]}
                                                          if s["backend"] == "claude" else {}))
            briefing = self.briefing(s, round_, summary)
            note = ""
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    proposal, usage = backend.propose(strategist.SYSTEM, briefing + note)
                except Exception as e:
                    self.log(f"  {name}: strategist call failed ({type(e).__name__}: {e}); keeps its version")
                    self.state.setdefault("failures", []).append(
                        {"round": round_, "strategist": name, "error": f"{type(e).__name__}: {e}"[:2000], "at": _now()})
                    proposal = None
                    break
                if usage:
                    self.state["usage"].append({"round": round_, "strategist": name, **usage})
                problem = _check_prediction(proposal["prediction"], self._baseline(name, summary, proposal["prediction"]),
                                            sorted(set(summary[name]["opponents"]) | {"anchors", "owner"})) \
                    or self._owner_rule(name, summary, proposal["prediction"]) or validate(proposal["code"])
                if problem is None:
                    break
                self.log(f"  {name}: attempt {attempt} rejected: {problem}")
                note = f"\n\n## Your last reply was rejected\n{problem}\nFix it and reply again in full."
                proposal = None
            if proposal is None:
                continue
            if proposal["code"].strip() == self._code(name, self.state["base"][name]).strip():
                self.log(f"  {name}: no change")
                continue
            v = self.state["current"][name] + 1
            pred = proposal["prediction"]
            baseline = self._baseline(name, summary, pred)
            meta = {k: proposal[k] for k in ("diagnosis", "hypothesis", "prediction", "change")}
            meta["baseline"] = baseline
            meta["written_from"] = self.state["base"][name]
            self._save_version(name, v, proposal["code"], meta, round_)
            self.log(f"  {name} -> v{v:02d}: {proposal['hypothesis'][:160]}")
        self._save_state()

    # ---- driver ----

    def run(self, rounds, allow_untested=False):
        self.allow_untested = allow_untested
        start = self.state["round"]
        names = [s["name"] for s in self.config["strategists"]]
        pending = [n for n in names if self.untested_owner(n)]
        if pending and not allow_untested:
            self.log("untested predictions about you: " + ", ".join(f"{n} v{self.state['current'][n]:02d}" for n in pending)
                     + ". Their rewrites are skipped until you duel them (agentic duel --bot NAME).")
            if len(pending) == len(names):
                self.log("all bots are waiting for a duel: nothing to rewrite, so no round was played (no cost). "
                         "Duel them first, or pass --allow-untested.")
                return
        for r in range(start, start + rounds):
            results = self.tournament(r)
            summary = self.summarise(results)
            for name, err in results.get("load_errors", {}).items():
                self.log(f"  {name}: failed to load: {err}")
            self.verdicts(r, summary)
            self.leaderboard(r, summary)
            self.decide(r, summary)
            self.state["round"] = r + 1
            self._save_state()
            # also after the last round: the new versions are what you duel, and a resumed run plays them
            self.reason(r, summary)
        self.log(f"run folder: {self.dir}")
        cost = cost_estimate(self.state["usage"])
        if cost:
            self.log(f"API usage so far: {cost}")


def _check_prediction(pred, baseline, opponents):
    """A prediction must name an opponent that plays and demand change from where the bot is now
    (else 'supported' would be free)."""
    if pred["opponent"] not in opponents:
        return f"prediction names opponent {pred['opponent']!r}; use one of {opponents}"
    if baseline is None:
        return None
    step = MIN_STEP.get(pred["metric"], MIN_STEP_SHARE)
    if pred["direction"] == "up" and pred["threshold"] < baseline + step:
        return (f"prediction threshold {pred['threshold']} for {pred['metric']} vs {pred['opponent']} asks for too little: "
                f"it measured {baseline:.2f} this round; predict at least {baseline + step:.2f} (a smaller change is noise)")
    if pred["direction"] == "down" and pred["threshold"] > baseline - step:
        return (f"prediction threshold {pred['threshold']} for {pred['metric']} vs {pred['opponent']} asks for too little: "
                f"it measured {baseline:.2f} this round; predict at most {baseline - step:.2f} (a smaller change is noise)")
    return None


VALIDATE_SECONDS = 20
SLOW_CALL_MS = 50


def _validate_child(code, queue):
    try:
        cls = sandbox.load(code, "candidate")
        bot = cls(1)
        for view in synthetic_views():
            start = time.perf_counter()
            sandbox.sanitize(bot.act(view))
            ms = 1000 * (time.perf_counter() - start)
            if ms > SLOW_CALL_MS:
                queue.put(f"act() took {ms:.0f} ms on one call (limit {SLOW_CALL_MS} ms; aim for under 5)")
                return
    except Exception as e:
        queue.put(f"{type(e).__name__}: {e}")
        return
    queue.put(None)


def validate(code):
    """None if the code passes the sandbox check and survives a few calls on synthetic views, in a
    child process (an endless loop in the candidate can't hang the loop)."""
    import multiprocessing
    try:
        sandbox.check(code)
    except sandbox.BotCodeError as e:
        return f"BotCodeError: {e}"
    queue = multiprocessing.Queue()
    proc = multiprocessing.Process(target=_validate_child, args=(code, queue), daemon=True)
    proc.start()
    proc.join(VALIDATE_SECONDS)
    if proc.is_alive():
        proc.terminate()
        return f"the bot did not finish its test calls within {VALIDATE_SECONDS} s (an endless loop?)"
    try:
        return queue.get(timeout=1)
    except Exception:
        return "the test process ended without a result (crashed)"


def synthetic_views():
    """A few plausible views (far, close, just hit, just whiffed, opponent swung) for a smoke test."""
    def bot(x, z, **kw):
        b = {"x": x, "y": 100.0, "z": z, "vx": 0.0, "vy": 0.0, "vz": 0.0, "yaw": 0.0, "pitch": 0.0, "on_ground": True,
             "sprinting": False, "sneaking": False, "health": 20.0, "absorption": 0.0, "hurt_time": 0}
        b.update(kw)
        return b
    out = []
    for tick, dist, events, extra in ((0, 8.0, [], {}), (40, 2.5, [], {"charge": 1.0}),
                                      (60, 2.0, [{"type": "hit", "by_me": True, "damage": 6.0, "crit": False,
                                                  "sprint_hit": True, "tick": 58}], {"charge": 0.1}),
                                      (80, 3.5, [{"type": "swing", "by_me": True, "hit": False, "tick": 78, "charge": 1.0}],
                                       {"miss_cooldown": 8, "charge": 0.0}),
                                      (100, 3.0, [{"type": "swing", "by_me": False, "hit": False, "tick": 98}], {"charge": 0.6})):
        me = bot(0.0, 0.0, **{"invulnerable_time": 0, "charge": 0.5, "fall_distance": 0.0, "miss_cooldown": 0,
                              "food": 20, "aim_on_target": dist < 3, "reach_distance": max(0.1, dist - 0.3), **extra})
        op = bot(0.0, dist, yaw=180.0)
        rel = {"dx": 0.0, "dy": 0.0, "dz": dist, "dist": dist, "yaw_off": 0.0, "pitch_off": 0.0}
        out.append({"tick": tick, "seen_tick": tick - 4, "fight_ticks": 600, "me": me, "op": op, "events": events, "rel": rel})
    return out


PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20), "claude-sonnet-5-5": (2.0, 10.0, 0.20),
          "claude-haiku-5-5": (0.10, 0.50, 0.01), "claude-fable-5-1": (10.0, 50.0, 0.25)}


def cost_estimate(usage):
    """Rough dollars from token counts (input, output, cache read; cache writes counted as input)."""
    if not usage:
        return None
    total, tokens = 0.0, 0
    for u in usage:
        p = PRICES.get(u.get("model", ""), PRICES["claude-opus-5-5"])
        total += (u["input"] + u["cache_write"]) * p[0] / 1e6 + u["output"] * p[1] / 1e6 + u["cache_read"] * p[2] / 1e6
        tokens += u["input"] + u["output"] + u["cache_read"] + u["cache_write"]
    return f"~${total:.2f} over {len(usage)} calls, {tokens / 1e6:.2f} M tokens (list prices; check the console for the bill)"


def status(name=None, log=print):
    runs = sorted(p for p in RUNS_DIR.glob("*") if (p / "state.json").exists()) if RUNS_DIR.exists() else []
    if not runs:
        log(f"no agentic runs in {RUNS_DIR}")
        return
    path = RUNS_DIR / name if name else runs[-1]
    state, notebook = _read(path / "state.json", {}), _read(path / "notebook.json", [])
    log(f"{path.name}: {state['round']} round(s) played")
    for bot, versions in state["versions"].items():
        cur = state["current"][bot]
        played = [(int(v), i) for v, i in versions.items() if i.get("anchors") is not None]
        best = max(played, key=lambda p: p[1]["anchors"], default=None)
        last = max(played, default=None)
        f = lambda i, k: "-" if i is None or i.get(k) is None else f"{i[k]:.2f}"
        log(f"  {bot}: current v{cur:02d}; last played v{last[0]:02d} anchors {f(last[1], 'anchors')} pool "
            f"{f(last[1], 'pool')}; best v{best[0]:02d} anchors {f(best[1], 'anchors')}; "
            f"base v{state.get('base', {}).get(bot, cur):02d}" if last else
            f"  {bot}: current v{cur:02d}, not played yet")
    supported = sum(1 for e in notebook if e["verdict"] == "supported")
    log(f"  notebook: {len(notebook)} hypotheses, {supported} supported")
    cost = cost_estimate(state.get("usage", []))
    if cost:
        log(f"  API usage: {cost}")
