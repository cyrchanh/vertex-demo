"""Export a few real Vertex fights into the demo's compact, anonymized data file.

Usage: python export_demo.py <replays_dir> <duels_dir> <out.js> <results_dir>
"""
import gzip
import json
import re
import sys
from pathlib import Path

REPLAYS, DUELS, OUT, RESULTS = map(Path, sys.argv[1:5])

SEEN_NAMES = set()  # every real name met, checked against the output at the end


def anon(text, opp, owner):
    """Replace player names in coaching text: the opponent becomes "your opponent"."""
    if text is None:
        return None
    text = re.sub(r"(^|[.!?]\s+)" + re.escape(opp), lambda m: m.group(1) + "Your opponent", text)
    text = text.replace(opp, "your opponent")
    return text.replace(owner, "the recorder")


def keybits(k):
    if k is None:
        return -1
    order = ["forward", "back", "left", "right", "jump", "shift", "sprint"]
    if "sneak" in k and "shift" not in k:
        k = dict(k, shift=k["sneak"])
    return sum(1 << i for i, n in enumerate(order) if k.get(n))


def r2(v):
    return round(float(v), 2)


def mod_fight(rid, title, blurb):
    d = json.load(gzip.open(REPLAYS / f"{rid}.json.gz"))
    t = d["trace"]
    owner, opp = t["participants"][0]["name"], t["participants"][1]["name"]
    SEEN_NAMES.update([owner, opp])
    f0 = t["frames"][0]["participants"]
    cx = (f0[0]["x"] + f0[1]["x"]) / 2
    cz = (f0[0]["z"] + f0[1]["z"]) / 2
    cy = min(p["y"] for p in f0)
    frames = []
    for fr in t["frames"]:
        row = []
        for p in fr["participants"]:
            flags = (1 if p["onGround"] else 0) | (2 if p["sprinting"] else 0) | (4 if p["sneaking"] else 0) \
                | (8 if p["swingStart"] else 0) | (16 if p["hurtTime"] == 10 else 0)
            row.append([r2(p["x"] - cx), r2(p["y"] - cy), r2(p["z"] - cz), round(p["yaw"], 1),
                        r2(p["health"]), r2(p["attackStrength"]), flags, keybits(p.get("input"))])
        frames.append(row)
    hits = [{"t": e["tick"], "a": e["attacker"], "v": e["victim"], "kind": e["kind"],
             "range": r2(e["range"]) if e.get("range") is not None else None}
            for e in t["events"] if e["type"] == "HIT"]
    exps = {}
    for e in d["explanations"]:
        exps.setdefault(e["interaction"], {})[e["perspective"]] = {
            "verdict": e["verdict"], "headline": anon(e["headline"], opp, owner), "what": anon(e["what"], opp, owner),
            "why": anon(e["why"], opp, owner), "instead": anon(e["instead"], opp, owner), "insteadWhy": anon(e["insteadWhy"], opp, owner),
            "source": e["source"], "notes": [anon(n, opp, owner) for n in e["notes"]], "inferred": e["inferred"]}
    exchanges = []
    for it in d["interactions"]:
        exchanges.append({"start": it["startTick"], "end": it["endTick"], "anchor": it["anchorTick"],
                          "hitsBy": it["hitsBy"],
                          "techniques": sorted({o["technique"] for o in it["observations"]}),
                          "coaching": exps.get(it["index"], {})})
    return {"id": rid, "kind": "mod", "title": title, "blurb": blurb, "tps": 20, "frameTicks": 1,
            "names": ["You", "Opponent"], "keysKnown": [True, False],
            "source": "Recorded client-side by the Vertex mod (vs. another player on a real server)",
            "frames": frames, "hits": hits, "exchanges": exchanges}


def duel_fight(stamp, idx, title, blurb, label):
    d = json.load(gzip.open(DUELS / f"duel-{stamp}.trace.json.gz"))
    you, bot = d["you"], d["bot"]
    steps = [s for s in d["fights"][idx]["steps"] if s["type"] == "obs"]
    order = [you, bot]
    b0 = steps[0]["bots"]
    cx = (b0[0]["x"] + b0[1]["x"]) / 2
    cz = (b0[0]["z"] + b0[1]["z"]) / 2
    cy = min(b["y"] for b in b0)
    step = steps[1]["tick"] - steps[0]["tick"]
    t0 = steps[0]["tick"]
    frames, hits, swings_hit = [], [], []
    prev_hp = None
    for s in steps:
        row = []
        swung = {e["bot"] for e in s["events"] if e["type"] == "swing"}
        for i in order:
            b = s["bots"][i]
            flags = (1 if b["on_ground"] else 0) | (2 if b["sprinting"] else 0) | (4 if b["sneaking"] else 0) \
                | (8 if i in swung else 0) | (16 if b["hurt_time"] >= 9 else 0)
            row.append([r2(b["x"] - cx), r2(b["y"] - cy), r2(b["z"] - cz), round(b["yaw"], 1),
                        r2(b["health"]), r2(b["charge"]), flags, keybits(b["keys"])])
        frames.append(row)
        for e in s["events"]:
            if e["type"] == "hit":
                kind = "CRIT" if e["crit"] else "SPRINT_HIT" if e["sprint_hit"] else "STRONG"
                hits.append({"t": e["tick"] - t0, "a": order.index(e["attacker"]), "v": order.index(e["victim"]),
                             "kind": kind, "range": None})
    # exchanges: landed hits with gaps of <= 20 ticks, padded by 10 (same rule as the mod's detector)
    exchanges, cur = [], []
    for h in hits:
        if cur and h["t"] - cur[-1]["t"] > 20:
            exchanges.append(cur); cur = []
        cur.append(h)
    if cur:
        exchanges.append(cur)
    ex = [{"start": max(0, c[0]["t"] - 10), "end": c[-1]["t"] + 10, "anchor": c[0]["t"],
           "hitsBy": [sum(1 for h in c if h["a"] == 0), sum(1 for h in c if h["a"] == 1)],
           "techniques": sorted({"CRIT_LANDED" for h in c if h["kind"] == "CRIT"} |
                                {"SPRINT_HIT_LANDED" for h in c if h["kind"] == "SPRINT_HIT"}),
           "coaching": {}} for c in exchanges]
    return {"id": f"duel-{stamp}-{idx}", "kind": "duel", "title": title, "blurb": blurb, "tps": 20,
            "frameTicks": step, "names": ["You", label], "keysKnown": [True, True],
            "source": "Recorded server-side in the self-play arena (you vs. the trained RL policy)",
            "frames": frames, "hits": hits, "exchanges": ex}


fights = [
    mod_fight("20261029-221643-722a84", "A clean win",
              "12 hits to 5. Mostly green: the coach finds what went right, and the three moments that didn't."),
    mod_fight("20261029-220101-2c76e1", "Dead even, 20–20",
              "A long trade-for-trade fight. Every kind of verdict shows up: good, mistake, neutral."),
    mod_fight("20261029-222629-cd0d9d", "A tough loss",
              "5 hits to 12. The coaching explains how the opponent kept the combo going and what breaks it."),
    duel_fight("20261105-210549", 0, "You vs. your own RL bot",
               "One of the bot's closer fights: a 30 s time-out, 16 hits to 10. Both fighters' keys are exact here.",
               "RL bot"),
]

# research data for the charts
results = json.load(open(DUELS / "results.json"))
duels = []
for n, dd in enumerate(results, 1):
    fs = dd["fights"]
    pol = fs[0]["policy"]
    # duel 6 was against the v3 champion (an LSTM) although its file is plain champion.pt (see the logs)
    arch = "LSTM" if "rnn-" in pol or n >= 6 else "MLP"
    duels.append({"duel": n, "you": sum(f["your_hits"] for f in fs), "bot": sum(f["bot_hits"] for f in fs),
                  "fights": len(fs), "arch": arch,
                  "score": round(sum(f["you_score"] for f in fs) / len(fs), 2)})

audit = []
for line in (RESULTS / "e1-common-20261106-043023.md").read_text(encoding="utf-8").splitlines():
    m = re.match(r"\| (\S+) \| ([+\-][\d.]+) \| ([+\-][\d.]+) \[", line)
    if m:
        name = m.group(1).replace("/latest", "")
        audit.append({"checkpoint": name, "bot": float(m.group(2)), "human": float(m.group(3))})

OUT.parent.mkdir(parents=True, exist_ok=True)
payload = {"fights": fights, "duels": duels, "audit": audit}
OUT.write_text("window.VERTEX_DATA = " + json.dumps(payload, separators=(",", ":")) + ";\n", encoding="utf-8")
print(OUT, OUT.stat().st_size // 1024, "KB;", len(fights), "fights,", len(duels), "duels,", len(audit), "audit points")
blob = OUT.read_text(encoding="utf-8")
assert not any(n in blob for n in SEEN_NAMES), "name leak"
