"""E1 of the paper plan (RQ1): does self-play judgement and opponent reading transfer to human fights?

  python -m vertex_training.arena.transfer [--out docs/paper/results]

Offline only: reads checkpoints, the teacher's saved bot fights and the duel server traces; starts
no server and writes nothing under selfplay/. Runs at below-normal priority on 2 threads, so it can
share the machine with training.

For every final checkpoint of every run (plus the champions), on two kinds of fights:

  bot fights     the teacher's 280 self-play fights (teacher_fights.pkl, layout 2 only)
  human fights   every traced duel, judged from the bot's seat and from the person's seat

two pre-set measures (paper plan, "Metrics fixed in advance"):

  judgement   value EV minus health-bar EV. EV = 1 − Var(return − estimate) / Var(return); the
              health bar is least squares of the return on the health lead the policy sees
              (observation input 34), fitted on the same moments, so it's an optimistic baseline.
              Positive: the value head knows more than the health bar.
  reading     opponent-head accuracy on the opponent's movement keys (W/S, A/D, jump, sprint),
              memory carried minus memory wiped every decision (recurrent policies only).

Held out: a checkpoint whose training used human data (human_read_coef, human_kl, the you-clone in
its league, or a teacher/anchor/start built from duels) is scored only on duels recorded after its
run started. Every checkpoint is also scored on all duels, marked as such.
"""

import argparse
import csv
import ctypes
import datetime
import gzip
import json
import pickle
import re
import sys
from pathlib import Path

import numpy as np
import torch

from . import play
from . import policy as pol
from .duel import DUELS_DIR, FIGHT_TICKS, human_action
from .env import ACTION_NVEC, SwordArenaEnv

LEAD_INPUT = 34            # (own health − opponent health) / 20, as the policy sees it
MOVE = 4                   # W/S, A/D, jump, sprint
BOOTSTRAP = 1000
REPO = Path(__file__).resolve().parents[3]
TEACHER_FIGHTS = play.SELFPLAY_DIR / "rnn-20261105-024340" / "teacher_fights.pkl"


def be_polite():
    torch.set_num_threads(2)
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # below normal


# ---- checkpoints ----

def checkpoints(skip_live=True):
    """(label, path, cutoff stamp or None): each run's last checkpoint, plus champions. cutoff: duels
    recorded before it may have been seen (run start; a copied file's own time)."""
    out = []
    for name in ("champion-v2.pt", "champion-v3.pt"):
        p = play.SELFPLAY_DIR / name
        if p.exists():
            out.append((name[:-3], p, datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y%m%d-%H%M%S")))
    runs = sorted(d for d in play.SELFPLAY_DIR.glob("*-2026*") if (d / "checkpoints").exists())
    live = [d for d in runs if skip_live and time_since_write(d) < 3600]   # still being written: leave alone
    for d in runs:
        if d in live:
            continue
        cks = sorted((d / "checkpoints").glob("update-*.pt"))
        last = d / "checkpoints" / "latest.pt" if (d / "checkpoints" / "latest.pt").exists() else (cks[-1] if cks else None)
        if last is None:
            continue
        m = re.search(r"(\d{8}-\d{6})", d.name)
        out.append((f"{d.name}/{last.stem}", last, m.group(1) if m else None))
    return out, live


def time_since_write(run_dir):
    newest = max((p.stat().st_mtime for p in run_dir.rglob("*") if p.is_file()), default=0)
    return datetime.datetime.now().timestamp() - newest


def config_of(ckpt):
    cfg = ckpt.get("config") or {}
    return json.loads(cfg) if isinstance(cfg, str) else cfg


def uses_human_data(cfg):
    if cfg.get("human_read_coef", 0) or cfg.get("human_kl", 0) or cfg.get("clone"):
        return True
    return any(isinstance(v, str) and ("you-" in v or "duels" in v) for v in cfg.values()) or \
        "you-" in json.dumps(cfg.get("anchor") or "")


# ---- fights ----

def human_fights(layout):
    """Every traced duel's fights, both seats: inputs [T, 2, in], actions [T, 2, 8], rewards [T, 2],
    whiffs [T, 2]. Seat 0 is the bot, seat 1 the person."""
    env = SwordArenaEnv(obs_layout=layout, max_ticks=FIGHT_TICKS)
    fights = []
    for file in sorted(DUELS_DIR.glob("duel-*.trace.json.gz")):
        stamp = file.name[len("duel-"):-len(".trace.json.gz")]
        with gzip.open(file, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
        for fight in data["fights"]:
            steps = fight["steps"]
            env.begin(steps[0], fight_ticks=FIGHT_TICKS)
            memory = pol.ActionMemory(2)
            xs, acts, rew, whiff = [], [], [], []
            for k in range(1, len(steps)):
                xs.append(memory.features(np.stack([env.observation(i, layout) for i in (0, 1)])))
                _, r, *_ = env.advance(steps[k])
                a = np.stack([human_action(steps[k - 1], steps[k], steps[k]["events"], i, data["repeat"]) for i in (0, 1)])
                memory.push(a)
                acts.append(a)
                rew.append([r["bot_0"], r["bot_1"]])
                w = [0.0, 0.0]
                for e in steps[k]["events"]:
                    if e["type"] == "swing" and not e["hit"]:
                        w[e["bot"]] += 1.0
                whiff.append(w)
            fights.append({"duel": stamp, "opponent": fight["policy"].replace("\\", "/").split("selfplay/")[-1],
                           "x": np.array(xs, np.float32), "a": np.array(acts), "r": np.array(rew, np.float32),
                           "whiff": np.array(whiff, np.float32)})
    return fights


def bot_fights():
    if not TEACHER_FIGHTS.exists():
        return []
    with TEACHER_FIGHTS.open("rb") as f:
        return pickle.load(f)


# ---- measures ----

def discounted(r, gamma):
    out, g = np.zeros_like(r), np.zeros(r.shape[1:], np.float32)
    for t in reversed(range(len(r))):
        g = r[t] + gamma * g
        out[t] = g
    return out


@torch.no_grad()
def heads(policy, x, wiped=False):
    """x [T, in] one seat's inputs → (value [T], opponent logits [T, 28] or None)."""
    xt = torch.from_numpy(x)
    if not getattr(policy, "recurrent", False):
        return policy.value(xt).numpy(), None
    if wiped:
        out, _ = policy.core(xt[None], policy.initial_state(len(x)), torch.zeros(1, len(x)))
        out = out[0]
    else:
        out, _ = policy.core(xt[:, None], policy.initial_state(1), torch.zeros(len(x), 1))
        out = out[:, 0]
    return policy.critic_head(out).squeeze(-1).numpy(), policy.opponent_head(out).numpy()


def move_hits(opp_logits, target):
    parts = np.split(opp_logits, np.cumsum(ACTION_NVEC)[:-1], axis=-1)
    pred = np.stack([p.argmax(-1) for p in parts[:MOVE]], -1)
    return (pred == target[:, :MOVE]).mean(-1)


def ev(ret, est):
    v = np.var(ret)
    return float("nan") if v < 1e-9 else float(1.0 - np.var(ret - est) / v)


def judgement(rows):
    """rows: per fight (return, value, lead) arrays → value EV − health-bar EV, both EVs."""
    ret = np.concatenate([r[0] for r in rows])
    val = np.concatenate([r[1] for r in rows])
    lead = np.concatenate([r[2] for r in rows])
    a = np.stack([lead, np.ones_like(lead)], 1)
    hb = a @ np.linalg.lstsq(a, ret, rcond=None)[0]
    v, h = ev(ret, val), ev(ret, hb)
    return v - h, v, h


def interval(rows, fn, seed=0):
    rng = np.random.default_rng(seed)
    vals = [fn([rows[i] for i in rng.integers(0, len(rows), len(rows))]) for _ in range(BOOTSTRAP)]
    vals = [v for v in vals if np.isfinite(v)]
    return np.percentile(vals, [5, 95]) if vals else (float("nan"), float("nan"))


def score(policy, cfg, human, bots):
    gamma, whiff_cost = cfg.get("gamma", 0.995), cfg.get("whiff_cost", 0.0)
    res = {}
    if bots and policy.obs_layout == 2:
        rows, reads = [], []
        for f in bots:
            x = np.asarray(f["x"], np.float32)
            v, opp = heads(policy, x)
            rows.append((discounted(np.asarray(f["r"], np.float32)[:, None], gamma)[:, 0], v, x[:, LEAD_INPUT]))
            if opp is not None:
                _, wiped = heads(policy, x, wiped=True)
                target = np.asarray(f["opp"])
                reads.append((move_hits(opp, target), move_hits(wiped, target)))
        res["bot_judgement"], res["bot_value_ev"], res["bot_health_ev"] = judgement(rows)
        if reads:
            res["bot_read_memory"] = float(np.concatenate([r[0] for r in reads]).mean())
            res["bot_read_gain"] = res["bot_read_memory"] - float(np.concatenate([r[1] for r in reads]).mean())
    for seat, name in ((0, "bot_seat"), (1, "person_seat")):
        rows, reads = [], []
        for f in human:
            x = f["x"][:, seat]
            v, opp = heads(policy, x)
            r = f["r"][:, seat] - whiff_cost * f["whiff"][:, seat]
            rows.append((discounted(r[:, None], gamma)[:, 0], v, x[:, LEAD_INPUT]))
            if opp is not None and seat == 0:
                _, wiped = heads(policy, x, wiped=True)
                reads.append((move_hits(opp, f["a"][:, 1]), move_hits(wiped, f["a"][:, 1])))
        if not rows:
            continue
        res[f"{name}_judgement"], res[f"{name}_value_ev"], res[f"{name}_health_ev"] = judgement(rows)
        lo, hi = interval(rows, lambda s: judgement(s)[0])
        res[f"{name}_judgement_90"] = [float(lo), float(hi)]
        if reads:
            mem = [r[0] for r in reads]
            gain = [r[0].mean() - r[1].mean() for r in reads]
            res["human_read_memory"] = float(np.concatenate(mem).mean())
            res["human_read_gain"] = float(np.concatenate(mem).mean() - np.concatenate([r[1] for r in reads]).mean())
            g = np.array(gain)
            rng = np.random.default_rng(0)
            boots = [g[rng.integers(0, len(g), len(g))].mean() for _ in range(BOOTSTRAP)]
            res["human_read_gain_90_by_fight"] = [float(np.percentile(boots, 5)), float(np.percentile(boots, 95))]
    res["fights"] = len(human)
    res["duels"] = sorted({f["duel"] for f in human})
    return res


# ---- report ----

def fmt(x, d=2):
    return "–" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:+.{d}f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "docs" / "paper" / "results"))
    args = ap.parse_args()
    be_polite()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    cks, live = checkpoints()
    print(f"{len(cks)} checkpoints; skipping runs still being written: {', '.join(d.name for d in live) or '-'}")
    human = {1: human_fights(1), 2: human_fights(2)}
    bots = bot_fights()
    print(f"{len(human[2])} human fights from {len({f['duel'] for f in human[2]})} duels; {len(bots)} bot fights")

    results = []
    for label, path, cutoff in cks:
        policy, ckpt = pol.load(path)
        cfg = config_of(ckpt)
        hd = uses_human_data(cfg)
        fights = human[policy.obs_layout]
        row = {"checkpoint": label, "arch": "lstm" if getattr(policy, "recurrent", False) else "mlp",
               "layout": policy.obs_layout, "human_data": hd, "cutoff": cutoff}
        row["all"] = score(policy, cfg, fights, bots)
        unseen = [f for f in fights if cutoff and f["duel"] > cutoff]
        row["unseen"] = score(policy, cfg, unseen, []) if unseen else None
        results.append(row)
        u = row["unseen"] or {}
        print(f"  {label:45} {row['arch']} L{row['layout']} human data {'yes' if hd else 'no '}  "
              f"bot judgement {fmt(row['all'].get('bot_judgement'))}  human (bot seat) {fmt(row['all'].get('bot_seat_judgement'))}  "
              f"unseen {fmt(u.get('bot_seat_judgement'))} ({u.get('fights', 0)} fights)  "
              f"read gain bots {fmt(row['all'].get('bot_read_gain'), 3)} / human {fmt(row['all'].get('human_read_gain'), 3)}")

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    (out / "e1-transfer-audit.json").write_text(json.dumps({"made": stamp, "results": results}, indent=1), encoding="utf-8")
    with (out / "e1-transfer-audit.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["checkpoint", "arch", "layout", "human_data", "duels_scored", "fights",
                    "bot_value_ev", "bot_health_ev", "bot_judgement",
                    "human_botseat_value_ev", "human_botseat_health_ev", "human_botseat_judgement", "lo90", "hi90",
                    "human_personseat_judgement", "bot_read_gain", "human_read_memory", "human_read_gain"])
        for row in results:
            # The paper's number: unseen duels for checkpoints trained on human data, all duels otherwise.
            s = row["unseen"] if row["human_data"] else row["all"]
            if s is None:
                continue
            b = row["all"]
            w.writerow([row["checkpoint"], row["arch"], row["layout"], row["human_data"],
                        "unseen" if row["human_data"] else "all", s["fights"],
                        b.get("bot_value_ev"), b.get("bot_health_ev"), b.get("bot_judgement"),
                        s.get("bot_seat_value_ev"), s.get("bot_seat_health_ev"), s.get("bot_seat_judgement"),
                        *(s.get("bot_seat_judgement_90") or [None, None]),
                        s.get("person_seat_judgement"), b.get("bot_read_gain"), s.get("human_read_memory"),
                        s.get("human_read_gain")])
    print(f"wrote {out / 'e1-transfer-audit.csv'} and .json")


def majority_read(eval_fights, other_fights):
    """The no-reading baseline: each movement key's most common choice by the person in the other
    duels, guessed at every decision. → accuracy on eval_fights."""
    a = np.concatenate([f["a"][:, 1] for f in other_fights])
    guess = np.array([np.bincount(a[:, k], minlength=n).argmax() for k, n in enumerate(ACTION_NVEC[:MOVE])])
    return float(np.concatenate([(f["a"][:, 1, :MOVE] == guess).mean(-1) for f in eval_fights]).mean())


def common(stamp, out):
    """Every checkpoint on one duel that none of them saw, so they're compared on the same fights;
    plus the majority-key baseline for reading and a rank correlation of bot vs. human judgement
    (bot judgement from e1-transfer-audit.json)."""
    from scipy.stats import spearmanr
    be_polite()
    cks, live = checkpoints()
    human = {1: human_fights(1), 2: human_fights(2)}
    full = {r["checkpoint"]: r for r in json.loads((out / "e1-transfer-audit.json").read_text(encoding="utf-8"))["results"]}
    rows = []
    for label, path, cutoff in cks:
        if cutoff and stamp <= cutoff:
            print(f"  {label}: duel {stamp} may have been seen, left out")
            continue
        policy, ckpt = pol.load(path)
        fights = [f for f in human[policy.obs_layout] if f["duel"] == stamp]
        s = score(policy, config_of(ckpt), fights, [])
        s["bot_judgement"] = full.get(label, {}).get("all", {}).get("bot_judgement")
        rows.append({"checkpoint": label, **s})
    eval_f = [f for f in human[2] if f["duel"] == stamp]
    base = majority_read(eval_f, [f for f in human[2] if f["duel"] != stamp])
    paired = [(r["bot_judgement"], r["bot_seat_judgement"]) for r in rows if r.get("bot_judgement") is not None]
    rho, p = spearmanr(*zip(*paired)) if len(paired) >= 4 else (float("nan"), float("nan"))
    lines = [f"Common test duel {stamp} ({len(eval_f)} fights), unseen by every checkpoint listed.",
             f"Reading baseline (the person's most common movement keys from the other duels): {base:.1%}.",
             f"Spearman(bot-fight judgement, human-fight judgement) over {len(paired)} layout-2 checkpoints: "
             f"rho {rho:+.2f} (p {p:.2f}).", "",
             "| Checkpoint | Bot judgement | Human judgement, bot seat [90%] | Person seat | Reads you (memory) | vs wiped | vs baseline |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        ci = r.get("bot_seat_judgement_90") or [float("nan")] * 2
        rm = r.get("human_read_memory")
        lines.append(f"| {r['checkpoint']} | {fmt(r.get('bot_judgement'))} | {fmt(r.get('bot_seat_judgement'))} "
                     f"[{fmt(ci[0])}, {fmt(ci[1])}] | {fmt(r.get('person_seat_judgement'))} | "
                     f"{'–' if rm is None else f'{rm:.1%}'} | {fmt(r.get('human_read_gain'), 3)} | "
                     f"{'–' if rm is None else f'{rm - base:+.3f}'} |")
    text = "\n".join(lines) + "\n"
    (out / f"e1-common-{stamp}.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--common":
        common(sys.argv[2], REPO / "docs" / "paper" / "results")
    else:
        main()
