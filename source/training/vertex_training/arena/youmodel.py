"""The you-model: an in-context copy of the person, conditioned on what the champion is doing.

  python -m vertex_training arena youmodel [--policy champion]

The champion is the test opponent; the person is what's being modelled. Every recorded duel is
replayed offline from its server trace, and a network learns to predict the person's next keys
from three things:

  the moment          the person's observation (layout 2) and the keys they just pressed
  the champion        the champion's exact action on the previous decision (from the trace)
  the fight so far    an LSTM memory, so "after it missed twice" or "once behind on health" can matter

It starts from the champion's own weights (encoder, LSTM, heads: it already knows the game; the
new champion-action inputs start at zero), and its second head predicts the champion's next action
from the person's side.

Is the in-context part real? Four versions are trained on the same data and judged by leaving one
whole duel out at a time (a new session, never seen): with/without memory × with/without the
champion's action, plus a per-control frequency baseline. Lower NLL (nats per decision, summed over
the 8 controls) is better; the gain of memory should grow later in fights if it reads the fight.

Then the habit report: in situations the champion creates (it just missed, it is closing in, it
just hit you, ...) what the person did, what the held-out model expected, and the net damage over
the next 2 s with and without that response. Outcomes there are correlations from real fights, not
proof that a response causes them; arena rollouts from the same states would test that.

The final model, trained on every duel, is saved as selfplay/you-model.pt.
"""

import copy
import ctypes
import datetime
import gzip
import json
import math
import sys
from pathlib import Path

import numpy as np
import torch
from torch import nn

from . import play
from . import policy as pol
from .analyse import load
from .duel import FIGHT_TICKS, human_action
from .env import ACTION_NVEC, SwordArenaEnv

MODEL_FILE = play.SELFPLAY_DIR / "you-model.pt"
CONTROLS = ["forward_back", "left_right", "jump", "sprint", "sneak", "attack", "turn", "look"]
VARIANTS = {                       # name: (memory, champion's action, LSTM frozen at the champion's)
    "memory + champion": (True, True, False),
    "memory only": (True, False, False),
    "champion only": (False, True, False),
    "neither": (False, False, False),     # the old you-clone's setup: this moment only
    "frozen LSTM": (True, True, True),    # fewer weights to fit: only the inputs and heads learn
}
MOVE_CONTROLS = 4                  # W/S, A/D, jump, sprint: the paper plan's pre-set reading metric
RESULTS_DIR = Path(__file__).resolve().parents[3] / "docs" / "paper" / "results"
OPP_COEF = 0.25                    # weight of the champion-prediction head
LR = 1e-3
MAX_STEPS = 300                    # full-batch steps; the best one on inner validation fights is kept
EVAL_EVERY = 10
EARLY_S, LATE_S = 3.0, 6.0         # fights against the owner are short (~12 s)
NEXT_TICKS = 40                    # outcome window: net damage over the next 2 s
BOOTSTRAP = 2000
_OFF = pol._OFFSETS


def one_hot(a):
    out = np.zeros((len(a), pol.ACTION_ONE_HOT), np.float32)
    np.put_along_axis(out, a + _OFF, 1.0, axis=1)
    return out


# ---- data ----

def fight_data(env, steps, repeat, layout):
    """One recorded fight → the person's inputs and actions and the champion's actions, per decision."""
    env.begin(steps[0], fight_ticks=FIGHT_TICKS)
    memory = pol.ActionMemory(1)
    xs, you, champ, ticks = [], [], [], []
    for k in range(1, len(steps)):
        xs.append(memory.features(env.observation(1, layout)[None])[0])
        env.advance(steps[k])
        a = human_action(steps[k - 1], steps[k], steps[k]["events"], 1, repeat)
        b = human_action(steps[k - 1], steps[k], steps[k]["events"], 0, repeat)
        memory.push(a[None])
        you.append(a)
        champ.append(b)
        ticks.append(steps[k - 1]["tick"])
    champ = np.array(champ)
    prev = np.vstack([pol.IDLE_ACTION[None], champ[:-1]])   # what the champion did on the decision before
    ticks = np.array(ticks)
    return {"x": np.array(xs, np.float32), "you": np.array(you), "champ": champ, "champ_prev": one_hot(prev),
            "t": (ticks - ticks[0]) / 20.0, "steps": steps}


def load_fights(layout):
    _, files = load("all")
    env = SwordArenaEnv(obs_layout=layout, max_ticks=FIGHT_TICKS)
    fights = []
    for file in files:
        with gzip.open(file, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
        for fight in data["fights"]:
            f = fight_data(env, fight["steps"], data["repeat"], layout)
            f["duel"] = file.name.split(".")[0]
            f["opponent"] = fight["policy"].replace("\\", "/").split("selfplay/")[-1]
            fights.append(f)
    return fights


def batch(fights):
    """Pad whole fights to one [T, n, …] batch (padding at the end never reaches earlier steps)."""
    T = max(len(f["x"]) for f in fights)

    def pad(key, dtype):
        out = np.zeros((T, len(fights)) + fights[0][key].shape[1:], dtype)
        for i, f in enumerate(fights):
            out[:len(f[key]), i] = f[key]
        return torch.from_numpy(out)

    mask = np.zeros((T, len(fights)), np.float32)
    for i, f in enumerate(fights):
        mask[:len(f["x"]), i] = 1.0
    return pad("x", np.float32), pad("champ_prev", np.float32), pad("you", np.int64), pad("champ", np.int64), \
        torch.from_numpy(mask)


# ---- model ----

class YouModel(nn.Module):
    """The champion's recurrent network re-aimed at the person: one more input (the champion's last
    action, weights starting at zero), the actor head now predicts the person, the opponent head the
    champion. memory=False runs every decision from an empty memory; context=False zeroes the
    champion's action."""

    def __init__(self, base, memory=True, context=True):
        super().__init__()
        self.memory, self.context = memory, context
        self.hidden, self.obs_layout, self.base_input = base.hidden, base.obs_layout, base.input_size
        self.encoder = nn.Sequential(nn.Linear(base.input_size + pol.ACTION_ONE_HOT, base.hidden), nn.Tanh())
        with torch.no_grad():
            w = self.encoder[0].weight
            w.zero_()
            w[:, :base.input_size] = base.encoder[0].weight
            self.encoder[0].bias.copy_(base.encoder[0].bias)
        self.lstm = copy.deepcopy(base.lstm)
        self.you_head = copy.deepcopy(base.actor_head)
        self.champ_head = copy.deepcopy(base.opponent_head)

    def forward(self, x, champ_prev):
        """x [T, n, in], champ_prev [T, n, 28] → (person logits, champion logits), each [T, n, 28]."""
        if not self.context:
            champ_prev = torch.zeros_like(champ_prev)
        h = self.encoder(torch.cat([x, champ_prev], -1))
        T, n, _ = h.shape
        if self.memory:
            out, _ = self.lstm(h)
        else:
            out, _ = self.lstm(h.reshape(1, T * n, -1))
            out = out.reshape(T, n, -1)
        return self.you_head(out), self.champ_head(out)


def nll(logits, a):
    """→ negative log-likelihood of each control's actual choice [T, n, 8]."""
    parts = torch.split(logits, ACTION_NVEC, -1)
    return torch.stack([-torch.log_softmax(l, -1).gather(-1, a[..., k:k + 1]).squeeze(-1)
                        for k, l in enumerate(parts)], -1)


def probs(logits):
    return torch.cat([torch.softmax(l, -1) for l in torch.split(logits, ACTION_NVEC, -1)], -1)


def _loss(model, b):
    x, c, a, ch, m = b
    you, champ = model(x, c)
    per = nll(you, a).sum(-1) + OPP_COEF * nll(champ, ch).sum(-1)
    return (per * m).sum() / m.sum(), (nll(you, a).sum(-1) * m).sum() / m.sum()


def train(base, fights, memory, context, seed=0, freeze=False):
    """Full-batch Adam on whole fights; the last fight of each duel is inner validation (the held-out
    duel is never seen) and the step with the best validation NLL is kept. freeze: keep the
    champion's LSTM as it is (only the encoder and heads learn)."""
    torch.manual_seed(seed)
    model = YouModel(base, memory, context)
    if freeze:
        for p in model.lstm.parameters():
            p.requires_grad_(False)
    duels = sorted({f["duel"] for f in fights})
    last = {d: max(i for i, f in enumerate(fights) if f["duel"] == d) for d in duels}
    val_idx = set(last.values()) if len(fights) - len(last) >= 4 else set()
    tr = batch([f for i, f in enumerate(fights) if i not in val_idx])
    va = batch([f for i, f in enumerate(fights) if i in val_idx]) if val_idx else None
    opt = torch.optim.Adam([p for p in model.parameters() if p.requires_grad], lr=LR)
    best, best_state, best_step = math.inf, copy.deepcopy(model.state_dict()), 0
    for step in range(1, MAX_STEPS + 1):
        model.train()
        loss, _ = _loss(model, tr)
        opt.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 0.5)
        opt.step()
        if va is not None and step % EVAL_EVERY == 0:
            model.eval()
            with torch.no_grad():
                _, v = _loss(model, va)
            if v.item() < best:
                best, best_state, best_step = v.item(), copy.deepcopy(model.state_dict()), step
    if va is not None:
        model.load_state_dict(best_state)
    else:
        best_step = MAX_STEPS
    model.eval()
    return model, best_step


@torch.no_grad()
def predict(model, fight):
    """→ (NLL per decision and control [T, 8], probabilities [T, 28]) for one fight from an empty memory."""
    x = torch.from_numpy(fight["x"])[:, None]
    c = torch.from_numpy(fight["champ_prev"])[:, None]
    you, _ = model(x, c)
    return nll(you, torch.from_numpy(fight["you"])[:, None])[:, 0].numpy(), probs(you)[:, 0].numpy()


def marginal(train_fights, fight):
    """Per-control frequencies in the training duels (add-one smoothed) → NLL [T, 8], probabilities."""
    a = np.concatenate([f["you"] for f in train_fights])
    p = np.concatenate([(np.bincount(a[:, k], minlength=n) + 1) / (len(a) + n) for k, n in enumerate(ACTION_NVEC)])
    pr = np.tile(p, (len(fight["you"]), 1)).astype(np.float32)
    return -np.log(pr[np.arange(len(fight["you"]))[:, None], fight["you"] + _OFF]), pr


# ---- habits ----

def _dist(s):
    a, b = s["bots"]
    return math.hypot(a["x"] - b["x"], a["z"] - b["z"])


SITUATIONS = [
    ("champion just missed, within 4 blocks", lambda c: c["champ_whiff"] and c["d"] < 4.0),
    ("champion closing in from 3-5 blocks", lambda c: 3.0 <= c["d"] < 5.0 and c["closing"] > 0.2),
    ("champion's sword charged, 3-4 blocks apart", lambda c: c["champ_charge"] >= 0.95 and 3.0 <= c["d"] < 4.0),
    ("champion just hit you", lambda c: c["got_hit"]),
    ("in reach, your sword under 0.9 charge", lambda c: c["d"] < 3.0 and c["your_charge"] < 0.9),
]
RESPONSES = [   # name, did it (from the action), model probability (from the 28 probabilities)
    ("swing", lambda a: a[5] == 1, lambda p: p[_OFF[5] + 1]),
    ("back off (S)", lambda a: a[0] == 2, lambda p: p[_OFF[0] + 2]),
    ("sprint in (W + sprint)", lambda a: a[0] == 1 and a[3] == 1, lambda p: p[_OFF[0] + 1] * p[_OFF[3] + 1]),
    ("jump", lambda a: a[2] == 1, lambda p: p[_OFF[2] + 1]),
]


def contexts(fight):
    """Per decision: what the person saw when choosing (the state before it) and the net damage over
    the next 2 s from their side."""
    steps = fight["steps"]
    hits = [(e["tick"], e["attacker"], e["damage"]) for s in steps for e in s["events"] if e["type"] == "hit"]
    out = []
    for k in range(1, len(steps)):
        s, p = steps[k - 1], steps[k - 2] if k >= 2 else None
        recent = s["events"] + (p["events"] if p is not None else [])
        t0 = s["tick"]
        out.append({
            "d": _dist(s), "closing": (_dist(p) - _dist(s)) if p is not None else 0.0,
            "champ_whiff": any(e["type"] == "swing" and e["bot"] == 0 and not e["hit"] for e in recent),
            "got_hit": any(e["type"] == "hit" and e["attacker"] == 0 for e in s["events"]),
            "your_charge": s["bots"][1]["charge"], "champ_charge": s["bots"][0]["charge"],
            "net": sum(d if a == 1 else -d for tk, a, d in hits if t0 < tk <= t0 + NEXT_TICKS)})
    return out


def habits(fights, oof_probs, log=print):
    log("\nhabits: what you do when the champion does something (model = held-out duel's prediction)")
    for name, cond in SITUATIONS:
        rows = []
        for f, pr in zip(fights, oof_probs):
            for k, c in enumerate(contexts(f)):
                if cond(c):
                    rows.append((f["you"][k], pr[k], c["net"]))
        if len(rows) < 10:
            log(f"  {name}: {len(rows)} moments, too few")
            continue
        log(f"  {name}: {len(rows)} moments")
        for rname, did, model_p in RESPONSES:
            d = np.array([did(a) for a, _, _ in rows])
            mp = np.mean([model_p(p) for _, p, _ in rows])
            net = np.array([n for _, _, n in rows])
            when = f"{net[d].mean():+.2f}" if d.sum() >= 3 else "  -  "
            other = f"{net[~d].mean():+.2f}" if (~d).sum() >= 3 else "  -  "
            log(f"    {rname:24} you {d.mean():5.0%}  model {mp:5.0%}   net HP next 2 s: when you do {when}, "
                f"when you don't {other}")


# ---- the whole check ----

def _boot(diff, seed=0):
    rng = np.random.default_rng(seed)
    means = [diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(BOOTSTRAP)]
    return np.percentile(means, [5, 95])


def move_acc(pr, a):
    """Probabilities [T, 28] and actions [T, 8] → share of the movement keys (W/S, A/D, jump, sprint)
    whose likeliest choice is the one taken, per decision [T]."""
    parts = np.split(pr, np.cumsum(ACTION_NVEC)[:-1], axis=-1)
    pred = np.stack([q.argmax(-1) for q in parts[:MOVE_CONTROLS]], -1)
    return (pred == a[:, :MOVE_CONTROLS]).mean(-1)


def _be_polite():
    """Share the machine with training: 2 threads, below-normal priority."""
    torch.set_num_threads(2)
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)


def run(policy_name="champion", log=print, save_model=True):
    _be_polite()
    lines = []

    def out(msg=""):
        lines.append(msg)
        log(msg)

    path = play.resolve(policy_name)
    base, _ = pol.load(path)
    if not getattr(base, "recurrent", False):
        raise ValueError("the you-model starts from a recurrent policy (the champion is one since v3)")
    fights = load_fights(base.obs_layout)
    duels = sorted({f["duel"] for f in fights})
    n_dec = sum(len(f["x"]) for f in fights)
    out(f"you-model: {len(fights)} fights ({n_dec} of your decisions) from {len(duels)} duels, "
        f"starting from {path.name}; leave one duel out")
    if len(duels) < 2:
        raise ValueError("need at least 2 duels with traces")

    names = list(VARIANTS) + ["frequencies"]
    res = {v: [None] * len(fights) for v in names}       # per fight: NLL [T, 8]
    acc = {v: [None] * len(fights) for v in names}       # per fight: movement-key accuracy [T]
    wiped = [None] * len(fights)                         # the full model with its memory wiped every decision
    oof = [None] * len(fights)                           # the full model's held-out probabilities
    for d in duels:
        held = [i for i, f in enumerate(fights) if f["duel"] == d]
        rest = [f for f in fights if f["duel"] != d]
        steps = []
        for v, (memory, context, freeze) in VARIANTS.items():
            model, step = train(base, rest, memory, context, freeze=freeze)
            steps.append(step)
            for i in held:
                res[v][i], pr = predict(model, fights[i])
                acc[v][i] = move_acc(pr, fights[i]["you"])
                if v == "memory + champion":
                    oof[i] = pr
                    model.memory = False
                    _, pw = predict(model, fights[i])
                    model.memory = True
                    wiped[i] = move_acc(pw, fights[i]["you"])
        for i in held:
            res["frequencies"][i], pr = marginal(rest, fights[i])
            acc["frequencies"][i] = move_acc(pr, fights[i]["you"])
        full = np.concatenate([res["memory + champion"][i] for i in held]).sum(1).mean()
        none = np.concatenate([res["neither"][i] for i in held]).sum(1).mean()
        out(f"  held out {d} ({len(held)} fights vs {fights[held[0]]['opponent']}): memory + champion {full:.3f}, "
            f"neither {none:.3f} nats/decision  (steps kept {steps})")

    def pooled(v, sel=lambda f: slice(None), cols=slice(None)):
        parts = [res[v][i][sel(f)][:, cols].sum(1) for i, f in enumerate(fights)]
        x = np.concatenate(parts)
        return x.mean() if len(x) else float("nan")

    def pooled_acc(arrs, sel=lambda f: slice(None)):
        x = np.concatenate([a[sel(f)] for a, f in zip(arrs, fights)])
        return x.mean() if len(x) else float("nan")

    early = lambda f: f["t"] < EARLY_S
    late = lambda f: f["t"] >= LATE_S
    out(f"\nheld-out NLL, nats per decision (lower is better); first {EARLY_S:g} s / after {LATE_S:g} s of fights;")
    out("movement-key accuracy (W/S, A/D, jump, sprint; higher is better)")
    out(f"  {'':18} {'all 8 controls':>15} {'attack':>8} {'W/S':>8} {'turn':>8} {'early':>8} {'late':>8} {'move acc':>9}")
    for v in names:
        out(f"  {v:18} {pooled(v):15.3f} {pooled(v, cols=[5]):8.3f} {pooled(v, cols=[0]):8.3f} "
            f"{pooled(v, cols=[6]):8.3f} {pooled(v, early):8.3f} {pooled(v, late):8.3f} {pooled_acc(acc[v]):9.1%}")
    for a, b, what in (("memory + champion", "champion only", "memory"),
                       ("memory + champion", "memory only", "the champion's action"),
                       ("memory + champion", "neither", "both"),
                       ("frozen LSTM", "neither", "both, LSTM frozen")):
        per_fight = np.array([res[b][i].sum(1).mean() - res[a][i].sum(1).mean() for i in range(len(fights))])
        lo, hi = _boot(per_fight)
        gain_e = pooled(b, early) - pooled(a, early)
        gain_l = pooled(b, late) - pooled(a, late)
        out(f"  NLL gain from {what:22} {per_fight.mean():+.3f} [{lo:+.3f}, {hi:+.3f}] per fight (90%, by fight); "
            f"early {gain_e:+.3f}, late {gain_l:+.3f}")
    # The paper's pre-set reading metric: the same trained model, memory carried vs wiped every decision.
    g = np.array([acc["memory + champion"][i].mean() - wiped[i].mean() for i in range(len(fights))])
    lo, hi = _boot(g)
    out(f"  reading (memory carried minus wiped, same model, movement keys): {pooled_acc(acc['memory + champion']):.1%} vs "
        f"{pooled_acc(wiped):.1%}; per fight {g.mean():+.1%} [{lo:+.1%}, {hi:+.1%}]; early "
        f"{pooled_acc(acc['memory + champion'], early) - pooled_acc(wiped, early):+.1%}, late "
        f"{pooled_acc(acc['memory + champion'], late) - pooled_acc(wiped, late):+.1%}")

    habits(fights, oof, out)

    if save_model:
        model, step = train(base, fights, True, True)
        torch.save({"format": "you-model-1", "hidden": model.hidden, "obs_layout": model.obs_layout,
                    "base_input": model.base_input, "base": str(path), "duels": duels, "model": model.state_dict()},
                   MODEL_FILE)
        out(f"\nsaved {MODEL_FILE} (all {len(duels)} duels, step {step})")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report = RESULTS_DIR / f"youmodel-{datetime.date.today():%Y-%m-%d}.txt"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"report: {report}")
    return res, fights


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "champion")
