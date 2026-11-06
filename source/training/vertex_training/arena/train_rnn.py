"""Self-play training for the policy with memory (RecurrentSwordPolicy): it reads the opponent
during a fight and adapts, instead of playing one fixed strategy against everyone.

  python -m vertex_training arena train --set arch=lstm --set teacher=PATH [--servers 2] [--decisions N]
  python -m vertex_training arena train --resume rnn-RUN

The same league, curriculum, rewards and gate as train.py, with three differences:

  recurrent PPO       the memory runs through every decision of every fight in order; for the update
                      each arena side's whole rollout is replayed from the memory it started with
                      (CleanRL's ppo_lstm), so the memory learns what to remember
  opponent prediction an extra output predicts the opponent's action at each decision (their keys,
                      swing and turn, read from the server, the same way a person's are in duels),
                      trained on what they actually did. Logged early (first 3 s) and late (after
                      10 s) in fights: if it reads the opponent, late predictions get better
  a teacher           the network starts empty, so it is pulled toward an existing policy's choices
                      (KL, fading out over `teacher_fade` of the updates) and the league starts with
                      strong opponents (`seed_opponents`)

Stored like train.py's runs, in selfplay/rnn-<date>/.
"""

import collections
import concurrent.futures
import datetime
import json
import pickle
import shutil
import time

import mlflow
import numpy as np
import torch

from . import kickstart, play
from . import policy as pol
from . import readpeople
from .duel import CLONE_FILE, human_action
from .env import ACTION_NVEC, OBS_SIZES, SwordArenaEnv, outcome_score
from .league import League
from .train import DEFAULTS, HEAD_MAX_ENTROPY, Arena, _setup_mlflow, arena_port, gate

RNN_DEFAULTS = dict(
    arch="lstm",
    teacher=None, teacher_kl=1.0, teacher_fade=0.3,   # a policy to copy at first (name or path)
    teacher_floor=0.0,                                # the pull never fades below this (a safety net: with
                                                      # none, the first run slid once the pull reached 0)
    seed_opponents=None,                              # {"name": policy} joining the league from the start
    seeded_share=0.15,                                # fixed share of fights against them (else the
                                                      # learner's own snapshots crowd them out: ~2% in v3's run)
    start_from=None,                                  # a distilled.pt to start from instead of distilling again
    teacher_skip=[],                                  # controls the teacher doesn't pull on (0 W/S, 2 jump):
                                                      # a teacher that never presses a key would veto it
    option_floor=0.0,                                 # every option of the controls in `option_floor_controls`
    option_floor_controls=[0, 1, 2, 3, 4],            # keeps at least this chance on average over the batch
    option_floor_coef=2.0,                            # (unlike the entropy floor, it can't be met by never
                                                      # picking one option); aim and attack are left alone
    combo_eps=0.0,                                    # chance per decision of starting a held movement combo
    combo_len=[2, 8],                                 # decisions it's held for (0.2-0.8 s)
    anchor=None, anchor_kl=1.0,                       # a second, constant pull (a policy with memory) on
    anchor_controls=[5, 6, 7],                        # these controls only: e.g. the teacher shapes the movement
                                                      # like a person, v3 keeps aim and clicks where they were
    teacher_reverse_kl=False,                         # KL(learner ‖ teacher) instead (kickstart.distill_kl)
    clone_path=None,                                  # another policy in the clone's place (a you-like fighter)
    clone_share=0.25,                                 # its share of fights after the handover
    style_holdout=[],                                 # duels to score "plays like the person" on (youopponent)
    run_gate=True,                                    # off for a sparring partner, not a champion candidate
    timeout_loss=False,                               # a time-out is a loss for both: only winning counts
    disabled_controls=[],                             # keys the bot doesn't have (4 = sneak): policy.disable
    human_read_coef=0.0,                              # weight of predicting the person in recorded duels
                                                      # (readpeople.py): memory learns people, not just bots
    human_read_batch=16,                              # pieces of duel fights per update
    human_read_holdout=1,                             # latest duels never trained on: the reading test
    human_read_pretrain=10,                           # epochs of reading alone before PPO starts
    start_skill=1.0,                                  # the curriculum starts complete (the teacher is strong)
    opponent_coef=0.25,                               # weight of the opponent-prediction task
    rollout=128, minibatches=2,                       # minibatches split the arena sides, not the steps
    snapshot_every=25,
)
EARLY_TICKS, LATE_TICKS = 60, 200
RNN_DEFAULTS.update(distill_decisions=60_000, distill_epochs=12)  # copy the teacher offline before PPO
SEGMENT, BURN_IN = 64, 16   # distillation trains on 64-decision pieces of fights, after 16 decisions of
                            # context that warm the memory up but don't count in the loss


COMBO_CONTROLS = 4   # W/S, A/D, jump, sprint
EXCHANGE_GAP = 20    # ticks without a hit that end an exchange (as in arena analyse)
WEAK_CHARGE = 0.9    # a swing below this charge is a weak hit


def new_stats():
    """Per-fight counts for both sides."""
    return {"hits": [0, 0], "crits": [0, 0], "sprint_hits": [0, 0], "damage": [0.0, 0.0], "swings": [0, 0],
            "whiffs": [0, 0], "weak_swings": [0, 0], "hits_taken": [0, 0], "air_hits_taken": [0, 0],
            "opened": [0, 0], "exchanges": [0, 0], "last_hit": [-1000]}


def tally(stats, before, after):
    """Adds one step's events to `stats` → the sides that whiffed. Fight skills, not just results:
    who opens each exchange (the first hit after 1 s without one), hits taken while in the air (a
    jump in from too close), swings below full charge."""
    whiffed = []
    # hits in tick order, same-tick trades credited to side 0 first (as arena analyse does)
    events = sorted(after["events"], key=lambda ev: (ev["type"] == "hit", ev.get("tick", 0), ev.get("attacker", 0)))
    for ev in events:
        if ev["type"] == "swing":
            k = ev["bot"]
            stats["swings"][k] += 1
            stats["weak_swings"][k] += int(ev["charge"] < WEAK_CHARGE)
            if not ev["hit"]:
                stats["whiffs"][k] += 1
                whiffed.append(k)
        elif ev["type"] == "hit":
            k = ev["attacker"]
            stats["hits"][k] += 1
            stats["crits"][k] += int(ev["crit"])
            stats["sprint_hits"][k] += int(ev["sprint_hit"])
            stats["damage"][k] += ev["damage"]
            stats["hits_taken"][1 - k] += 1
            stats["air_hits_taken"][1 - k] += int(not before["bots"][1 - k]["on_ground"])
            if ev["tick"] - stats["last_hit"][0] > EXCHANGE_GAP:
                stats["opened"][k] += 1
                stats["exchanges"][0] += 1
                stats["exchanges"][1] += 1
            stats["last_hit"][0] = ev["tick"]
    return whiffed


class Combos:
    """Temporally extended exploration (ez-greedy, Dabney et al. 2021). Now and then a learner side
    holds one random movement combo (W/S, A/D, jump, sprint) for a few decisions while it keeps
    aiming and clicking by itself, so whole manoeuvres get tried (a sprint hop in from 3 blocks,
    S + jump out of a combo) instead of single random key presses that undo each other.

    The held keys aren't the policy's choice, so they count as probability 1 in the stored
    log-probability: PPO's ratio is then pi(combo) / 1, which its clipping turns into "raise the
    combo's chance if it went better than expected, ignore it otherwise"."""

    def __init__(self, n, eps, length, rng):
        self.eps, self.lo, self.hi, self.rng = eps, length[0], length[1], rng
        self.left = np.zeros(n, np.int64)
        self.keys = np.zeros((n, COMBO_CONTROLS), np.int64)

    def clear(self, slot):
        self.left[slot] = 0

    def apply(self, act, learns):
        """Overwrites the movement keys of the sides holding a combo -> which sides were held."""
        start = learns & (self.left == 0) & (self.rng.random(len(self.left)) < self.eps)
        for s in np.flatnonzero(start):
            self.left[s] = self.rng.integers(self.lo, self.hi + 1)
            self.keys[s] = [self.rng.integers(n) for n in ACTION_NVEC[:COMBO_CONTROLS]]
        held = learns & (self.left > 0)
        act[held, :COMBO_CONTROLS] = self.keys[held]
        self.left[held] -= 1
        return held


def _control_logp(logits, action):
    """Log-probability of each control's choice: [..., 8]."""
    return torch.stack([torch.distributions.Categorical(logits=l).log_prob(action[..., k])
                        for k, l in enumerate(torch.split(logits, ACTION_NVEC, dim=-1))], dim=-1)


def _collect_teacher(port, decisions, seed, teacher_path, cfg):
    """The teacher fights (itself, the scripted styles, the champion); for each side it plays: the
    inputs, its action logits, the opponent's action and the returns, one sequence per fight."""
    from .league import SCRIPTED
    rng = np.random.default_rng(seed)
    teacher = pol.load(teacher_path)[0]
    env = SwordArenaEnv(port=port, max_ticks=cfg["max_ticks"], seed=seed, obs_layout=cfg["obs_layout"],
                        aim_noise=cfg["aim_noise"])
    fights, done_decisions = [], 0
    try:
        while done_decisions < decisions:
            kind = rng.choice(["self", "scripted", "champion"], p=[0.4, 0.4, 0.2])
            style = rng.choice(["chase", "crit", "punish", "eager"])
            other = None
            if kind == "scripted":
                other = SCRIPTED[style][0](int(rng.integers(1 << 30)))
            elif kind == "champion":
                other = pol.PolicyFighter(play.resolve("champion"))
            recorded = [0, 1] if kind == "self" else [0]
            env.reset(options={"start_distance": (2.0, 10.0)})
            memory = pol.ActionMemory(2)
            seq = {k: {"x": [], "logits": [], "opp": [], "r": [], "tick": []} for k in recorded}
            while env.agents:
                x = memory.features(np.stack([env.observation(k) for k in range(2)]))
                with torch.no_grad():
                    logits = teacher.actor(torch.from_numpy(x))
                    act = pol._choose(logits)[0].numpy()
                actions = {"bot_0": act[0]}
                if kind == "self":
                    actions["bot_1"] = act[1]
                elif kind == "scripted":
                    actions["bot_1"] = other(env.delayed_state, 1)
                else:
                    actions["bot_1"] = other(env.observation(1, other.obs_layout))
                tick = env.state["tick"]
                _, rewards, *_ = env.step(actions)
                before, after = env.replies[-2], env.replies[-1]
                taken = act.copy()
                if kind != "self":
                    taken[1] = human_action(before, after, after["events"], 1, env.repeat)
                memory.push(taken)
                for k in recorded:
                    s = seq[k]
                    s["x"].append(x[k])
                    s["logits"].append(logits[k].numpy())
                    s["opp"].append(human_action(before, after, after["events"], 1 - k, env.repeat))
                    s["r"].append(rewards[env.possible_agents[k]])
                    s["tick"].append(tick)
                done_decisions += 1
            for k in recorded:
                s = {key: np.asarray(v) for key, v in seq[k].items()}
                g, rets = 0.0, np.zeros(len(s["r"]), np.float32)
                for t in reversed(range(len(s["r"]))):
                    g = s["r"][t] + cfg["gamma"] * g
                    rets[t] = g
                s["ret"] = rets
                fights.append(s)
    finally:
        env.close()
    return fights


def _segments(fights):
    """Fights → pieces of SEGMENT decisions, each with up to BURN_IN decisions before it as context.
    (Whole fights as batch items gave only ~11 weight updates per pass: far too few.)"""
    out = []
    for f in fights:
        n = len(f["x"])
        for start in range(0, n, SEGMENT):
            lo = max(0, start - BURN_IN)
            piece = {k: v[lo:start + SEGMENT] for k, v in f.items()}
            piece["counted"] = np.arange(lo, min(n, start + SEGMENT)) >= start
            out.append(piece)
    return out


def distill(learner, fights, epochs=12, batch=32, lr=1e-3, seed=0, log=print):
    """Train the memory policy offline on the teacher's fights: match the teacher's choices (KL),
    predict the opponent, estimate the value. Trained on pieces of fights; held-out fights are scored
    whole, so early vs late opponent prediction is measured over full fights. → held-out metrics."""
    gen = np.random.default_rng(seed)
    order = gen.permutation(len(fights))
    held = [fights[i] for i in order[: max(1, len(fights) // 10)]]
    for f in held:
        f["counted"] = np.ones(len(f["x"]), bool)
    train_set = _segments([fights[i] for i in order[max(1, len(fights) // 10):]])
    log(f"  distill: {len(train_set)} pieces of fights for training, {len(held)} whole fights held out")
    opt = torch.optim.Adam(learner.parameters(), lr=lr)

    def batch_tensors(group):
        T = max(len(f["x"]) for f in group)
        n = len(group)

        def pad(key, dtype, tail=()):
            out = np.zeros((T, n, *tail), dtype)
            for j, f in enumerate(group):
                out[: len(f[key]), j] = f[key]
            return torch.from_numpy(out)
        valid = np.zeros((T, n), bool)
        for j, f in enumerate(group):
            valid[: len(f["x"]), j] = f["counted"]   # burn-in context and padding don't count
        return (pad("x", np.float32, (learner.input_size,)), pad("logits", np.float32, (pol.ACTION_ONE_HOT,)),
                pad("opp", np.int64, (len(ACTION_NVEC),)), pad("ret", np.float32), pad("tick", np.int64),
                torch.from_numpy(valid))

    def run(group):
        x, tl, opp, ret, tick, valid = batch_tensors(group)
        logits, values, opp_logits = learner.evaluate(x, learner.initial_state(x.shape[1]), torch.zeros(x.shape[:2]))
        kl = kickstart.distill_kl(logits[valid], tl[valid])
        v = ((values - ret) ** 2)[valid].mean()
        _, opp_logp, _ = pol._choose(opp_logits, opp)
        o = -(opp_logp[valid]).mean() / len(ACTION_NVEC)
        return kl, v, o, (logits, tl, opp_logits, opp, tick, valid)

    learner.train()
    for epoch in range(epochs):
        idx = gen.permutation(len(train_set))
        for b in range(0, len(idx), batch):
            kl, v, o, _ = run([train_set[i] for i in idx[b:b + batch]])
            loss = kl + 0.5 * v + 0.25 * o
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(learner.parameters(), 0.5)
            opt.step()
        with torch.no_grad():
            kl, v, o, (logits, tl, opp_logits, opp, tick, valid) = run(held)
            split = lambda z: torch.split(z, ACTION_NVEC, dim=-1)
            agree = torch.stack([a.argmax(-1) == b.argmax(-1) for a, b in zip(split(logits), split(tl))], -1).float().mean(-1)
            pred = torch.stack([l.argmax(-1) for l in split(opp_logits)], -1)
            opp_acc = (pred == opp).float().mean(-1)
            early, late = valid & (tick < EARLY_TICKS), valid & (tick > LATE_TICKS)
            metrics = {"distill_kl": kl.item(), "distill_agreement": agree[valid].mean().item(),
                       "distill_value_mse": v.item(), "distill_opp_acc_early": opp_acc[early].mean().item(),
                       "distill_opp_acc_late": opp_acc[late].mean().item()}
        if epoch == epochs // 2:
            for g in opt.param_groups:
                g["lr"] = lr / 3        # finer steps for the second half
        log(f"  distill epoch {epoch + 1}/{epochs}: agrees with the teacher {metrics['distill_agreement']:.1%}, "
            f"KL {metrics['distill_kl']:.3f}, reads opponent {metrics['distill_opp_acc_early']:.2f} early / "
            f"{metrics['distill_opp_acc_late']:.2f} late")
    learner.eval()
    return metrics | {"distill_fights": len(fights)}


def train(log=print, resume=None, **overrides):
    if resume:
        run_dir = play.SELFPLAY_DIR / resume
        cfg = {**DEFAULTS, **RNN_DEFAULTS, **json.loads((run_dir / "config.json").read_text(encoding="utf-8"))}
        cfg.update({k: v for k, v in overrides.items() if v is not None})
    else:
        cfg = {**DEFAULTS, **RNN_DEFAULTS, **{k: v for k, v in overrides.items() if v is not None}}
        run_dir = play.SELFPLAY_DIR / datetime.datetime.now().strftime("rnn-%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    ckpt_dir = run_dir / "checkpoints"

    torch.manual_seed(cfg["seed"])
    torch.set_num_threads(2)
    rng = np.random.default_rng(cfg["seed"])
    learner = pol.RecurrentSwordPolicy(hidden=cfg["hidden"], obs_layout=cfg["obs_layout"])
    optimizer = torch.optim.Adam(learner.parameters(), lr=cfg["lr"], eps=1e-5)
    start_update, decisions_done, mlflow_run_id = 0, 0, None
    if resume and (ckpt_dir / "latest.pt").exists():
        learner, ckpt = pol.load(ckpt_dir / "latest.pt")
        optimizer = torch.optim.Adam(learner.parameters(), lr=cfg["lr"], eps=1e-5)
        optimizer.load_state_dict(ckpt["optimizer"])
        start_update, decisions_done, mlflow_run_id = ckpt["update"], ckpt["decisions"], ckpt.get("mlflow_run_id")
        log(f"resumed {run_dir.name} at update {start_update} ({decisions_done:,} decisions)")
    learner.train()

    league = League(run_dir / "league", seed=cfg["seed"] + start_update, curriculum_share=cfg["curriculum_share"],
                    focus=cfg["focus_style"], start_skill=cfg["start_skill"], seeded_share=cfg["seeded_share"])
    for name, policy_name in (cfg["seed_opponents"] or {}).items():
        league.add_opponent(name, play.resolve(policy_name))
    league.clone_share = cfg["clone_share"]
    if cfg["clone_path"]:
        league.use_clone(play.resolve(cfg["clone_path"]))
    elif cfg["clone"] and CLONE_FILE.exists():
        league.use_clone(CLONE_FILE)
    teacher = pol.load(play.resolve(cfg["teacher"]))[0] if cfg["teacher"] else None
    teacher_recurrent = getattr(teacher, "recurrent", False)
    if teacher is not None and teacher.obs_layout != learner.obs_layout:
        raise ValueError("the teacher must use the learner's observation layout")
    if teacher_recurrent and not cfg["start_from"]:
        raise ValueError("a teacher with memory can't be distilled offline: give start_from")
    anchor = pol.load(play.resolve(cfg["anchor"]))[0] if cfg["anchor"] else None
    if anchor is not None and not getattr(anchor, "recurrent", False):
        raise ValueError("the anchor must be a policy with memory")
    anchor_skip = [k for k in range(len(ACTION_NVEC)) if k not in cfg["anchor_controls"]]
    style_held = []
    if cfg["style_holdout"]:
        from . import youopponent
        style_held = youopponent.style_fights(cfg["style_holdout"], cfg["obs_layout"])
    human_pieces, human_held = [], []
    if cfg["human_read_coef"] > 0:
        stamps = readpeople.duel_stamps()
        cut = len(stamps) - cfg["human_read_holdout"]
        human_pieces = readpeople.segments(readpeople.human_fights(stamps[:cut], cfg["obs_layout"]))
        human_held = readpeople.human_fights(stamps[cut:], cfg["obs_layout"])
        log(f"reading people: {len(human_pieces)} pieces of {cut} duels to learn from, "
            f"{len(human_held)} fights of {', '.join(stamps[cut:])} held out")
    prior = None
    if cfg["human_kl"] > 0 and CLONE_FILE.exists():
        prior = pol.load(CLONE_FILE)[0]

    E, T = cfg["servers"], cfg["rollout"]
    S = 2 * E
    n_updates = max(1, cfg["decisions"] // (T * E))
    arenas = [Arena(SwordArenaEnv(port=arena_port(i), max_ticks=cfg["max_ticks"], repeat=cfg["repeat"],
                                  reaction_steps=cfg["reaction_steps"], seed=cfg["seed"] * 1000 + i * 100_000,
                                  aim_noise=cfg["aim_noise"], obs_layout=cfg["obs_layout"])) for i in range(E)]
    for a in arenas:
        a.env.timeout_loss = cfg["timeout_loss"]
    memory = pol.ActionMemory(S)
    combos = Combos(S, cfg["combo_eps"], cfg["combo_len"], np.random.default_rng(cfg["seed"] + 4242))
    obs = np.zeros((S, OBS_SIZES[cfg["obs_layout"]]), dtype=np.float32)
    lstm_state = learner.initial_state(S)
    teacher_state = teacher.initial_state(S) if teacher_recurrent else None
    anchor_state = anchor.initial_state(S) if anchor is not None else None
    frozen_cache = {}
    results = collections.defaultdict(lambda: collections.deque(maxlen=100))
    fight_stats = collections.deque(maxlen=200)

    def new_fight(e):
        a = arenas[e]
        a.kind, a.opponent = league.sample()
        a.learner_side = int(rng.integers(2))
        a.bot = league.curriculum_fighter(a.opponent) if a.kind == "curriculum" else None
        if a.kind == "snapshot":
            if a.opponent not in frozen_cache:
                frozen_cache[a.opponent] = pol.load(league.snapshot_path(a.opponent))[0]
            a.frozen = frozen_cache[a.opponent]
            a.frozen_state = a.frozen.initial_state(1) if getattr(a.frozen, "recurrent", False) else None
        a.stats = new_stats()
        start = league.curriculum.start_distance(a.opponent) if a.kind == "curriculum" else (2.0, 10.0)
        o, _ = a.env.reset(options={"start_distance": start})
        for side, agent in enumerate(a.env.possible_agents):
            obs[2 * e + side] = o[agent]
            memory.reset(2 * e + side)
            combos.clear(2 * e + side)

    def step(e, acts):
        a = arenas[e]
        actions = {}
        for side, agent in enumerate(a.env.possible_agents):
            if a.kind == "curriculum" and side != a.learner_side:
                actions[agent] = a.bot(a.env.delayed_state, side)
            else:
                actions[agent] = acts[2 * e + side]
        return a.env.step(actions)

    def end_fight(e):
        a = arenas[e]
        for side in ([0, 1] if a.kind == "self" else [a.learner_side]):
            st = a.stats
            fight_stats.append({k: st[k][side] for k in st if k != "last_hit"} | {"ticks": a.env.state["tick"]})
        score = outcome_score(a.env.state, a.learner_side, timeout_loss=cfg["timeout_loss"])
        results["timeouts"].append(float(a.env.state["winner"] < 0))
        if a.kind == "curriculum":
            results["curriculum"].append(score)
            results["style_" + league.curriculum.buckets[a.opponent]["style"]].append(score)
            league.record_curriculum(a.opponent, score)
        elif a.kind == "snapshot":
            league.record(a.opponent, score)
            results["you_clone" if a.opponent == "you-clone" else f"vs_{a.opponent}" if not a.opponent.startswith("gen-")
                    and not a.opponent.startswith("rnn-") else "snapshots"].append(score)

    def save_checkpoint(path, update):
        pol.save(path, learner, optimizer=optimizer.state_dict(), update=update, decisions=decisions_done,
                 mlflow_run_id=mlflow_run.info.run_id, config=json.dumps(cfg))

    _setup_mlflow()
    pool = concurrent.futures.ThreadPoolExecutor(E)
    n_in, n_act = learner.input_size, len(ACTION_NVEC)
    buf = {"x": np.zeros((T, S, n_in), np.float32), "a": np.zeros((T, S, n_act), np.int64),
           "logp": np.zeros((T, S), np.float32), "v": np.zeros((T, S), np.float32),
           "r": np.zeros((T, S), np.float32), "done": np.zeros((T, S), np.float32),
           "mask": np.zeros((T, S), bool), "opp": np.zeros((T, S, n_act), np.int64),
           "tick": np.zeros((T, S), np.int64), "held": np.zeros((T, S), bool)}
    next_done = np.zeros(S, dtype=np.float32)

    with mlflow.start_run(run_id=mlflow_run_id, run_name=None if mlflow_run_id else run_dir.name) as mlflow_run:
        if not mlflow_run_id:
            mlflow.set_tags({"stage": "selfplay", "arch": "lstm", "run_dir": str(run_dir)})
            mlflow.log_params({k: (json.dumps(v) if isinstance(v, dict) else v) for k, v in cfg.items()})
        distilled = run_dir / "distilled.pt"
        if cfg["start_from"] and not distilled.exists():
            shutil.copy(cfg["start_from"], distilled)
        if teacher is not None and cfg["distill_decisions"] > 0 and start_update == 0:
            fights_file = run_dir / "teacher_fights.pkl"
            if distilled.exists():
                learner = pol.load(distilled)[0]
            else:
                if fights_file.exists():
                    fights = pickle.loads(fights_file.read_bytes())
                    log(f"distill: reusing {len(fights)} recorded teacher fights ({fights_file.name})")
                else:
                    log(f"distill: the teacher plays {cfg['distill_decisions']:,} decisions; the memory policy copies it")
                    ports = [arena_port(i) for i in range(E)]
                    with concurrent.futures.ThreadPoolExecutor(E) as collect_pool:
                        parts = list(collect_pool.map(_collect_teacher, ports, [cfg["distill_decisions"] // E] * E,
                                                      [cfg["seed"] + 7919 * i for i in range(E)],
                                                      [str(play.resolve(cfg["teacher"]))] * E, [cfg] * E))
                    fights = [f for part in parts for f in part]
                    fights_file.write_bytes(pickle.dumps(fights))
                mlflow.log_metrics(distill(learner, fights, epochs=cfg["distill_epochs"], seed=cfg["seed"], log=log))
                pol.save(distilled, learner)
            learner.train()
            optimizer = torch.optim.Adam(learner.parameters(), lr=cfg["lr"], eps=1e-5)
        pol.disable(learner, cfg["disabled_controls"])
        if human_pieces and start_update == 0 and cfg["human_read_pretrain"] > 0:
            learner.train()
            read_opt = torch.optim.Adam(learner.parameters(), lr=3e-4)
            read_rng = np.random.default_rng(cfg["seed"] + 77)
            for epoch in range(cfg["human_read_pretrain"]):
                for idx in np.array_split(read_rng.permutation(len(human_pieces)), max(1, len(human_pieces) // 16)):
                    read_loss = readpeople.reading_loss(learner, [human_pieces[i] for i in idx])
                    read_opt.zero_grad()
                    read_loss.backward()
                    torch.nn.utils.clip_grad_norm_(learner.parameters(), cfg["max_grad_norm"])
                    read_opt.step()
            learner.eval()
            mem, wiped = readpeople.read_score(learner, human_held)
            log(f"reading people: after {cfg['human_read_pretrain']} epochs, held-out duel {mem:.1%} with memory, "
                f"{wiped:.1%} wiped")
            optimizer = torch.optim.Adam(learner.parameters(), lr=cfg["lr"], eps=1e-5)
        for e in range(E):
            new_fight(e)
        log(f"{run_dir.name}: memory policy, {n_updates} updates of {T} decisions × {E} arena(s); "
            f"teacher {cfg['teacher']}; league opponents {', '.join(league.snapshots()) or 'none yet'}")
        for update in range(start_update, n_updates):
            t0 = time.perf_counter()
            for g in optimizer.param_groups:
                g["lr"] = cfg["lr"] * (1.0 - update / n_updates)
            start_state = (lstm_state[0].clone(), lstm_state[1].clone())
            if teacher_recurrent:
                teacher_start = (teacher_state[0].clone(), teacher_state[1].clone())
            if anchor is not None:
                anchor_start = (anchor_state[0].clone(), anchor_state[1].clone())
            learner.eval()
            for t in range(T):
                x = memory.features(obs)
                with torch.no_grad():
                    out, lstm_state = learner.core(torch.from_numpy(x)[None], lstm_state, torch.from_numpy(next_done)[None])
                    logits, value = learner.actor_head(out[0]), learner.critic_head(out[0]).squeeze(-1)
                    act, logp, _ = pol._choose(logits)
                    if teacher_recurrent:   # the teacher's memory follows the same fights
                        _, teacher_state = teacher.core(torch.from_numpy(x)[None], teacher_state,
                                                        torch.from_numpy(next_done)[None])
                    if anchor is not None:
                        _, anchor_state = anchor.core(torch.from_numpy(x)[None], anchor_state,
                                                      torch.from_numpy(next_done)[None])
                act = act.numpy()
                learns = np.array([arenas[s // 2].learns(s % 2) for s in range(S)])
                held = combos.apply(act, learns) if cfg["combo_eps"] > 0 else np.zeros(S, bool)
                if held.any():   # the held keys count as certain; the rest is the policy's own choice
                    with torch.no_grad():
                        h = torch.from_numpy(held)
                        logp[h] = _control_logp(logits[h], torch.from_numpy(act[held]))[:, COMBO_CONTROLS:].sum(-1)
                for e, a in enumerate(arenas):
                    if a.kind == "snapshot":
                        side = 1 - a.learner_side
                        slot = 2 * e + side
                        xf = x[slot:slot + 1]
                        if a.frozen.obs_layout != learner.obs_layout:
                            xf = memory.features(a.env.observation(side, a.frozen.obs_layout)[None], rows=[slot])
                        with torch.no_grad():
                            if a.frozen_state is not None:
                                fa, _, _, a.frozen_state = a.frozen.step(torch.from_numpy(xf), a.frozen_state,
                                                                         torch.zeros(1))
                            else:
                                fa = a.frozen.act(torch.from_numpy(xf))[0]
                        act[slot] = fa.numpy()[0]
                buf["x"][t], buf["a"][t], buf["logp"][t], buf["v"][t] = x, act, logp.numpy(), value.numpy()
                buf["done"][t] = next_done
                buf["mask"][t] = learns
                buf["held"][t] = held
                buf["tick"][t] = [arenas[s // 2].env.state["tick"] for s in range(S)]
                outcomes = list(pool.map(step, range(E), [act] * E))
                memory.push(act)
                for e, (o, rewards, terms, truncs, infos) in enumerate(outcomes):
                    a = arenas[e]
                    before, after = a.env.replies[-2], a.env.replies[-1]
                    for side, agent in enumerate(a.env.possible_agents):
                        buf["r"][t, 2 * e + side] = rewards[agent]
                        obs[2 * e + side] = o[agent]
                        # what the opponent actually did this decision: the prediction target
                        buf["opp"][t, 2 * e + side] = human_action(before, after, after["events"], 1 - side, a.env.repeat)
                    for k in tally(a.stats, before, after):
                        buf["r"][t, 2 * e + k] -= cfg["whiff_cost"]
                    done = not a.env.agents
                    next_done[2 * e:2 * e + 2] = float(done)
                    if done:
                        end_fight(e)
                        new_fight(e)
                decisions_done += E

            # ---- recurrent PPO ----
            with torch.no_grad():
                next_value = learner.value_of(torch.from_numpy(memory.features(obs)), lstm_state,
                                              torch.from_numpy(next_done)).numpy()
            adv = np.zeros((T, S), dtype=np.float32)
            last = np.zeros(S, dtype=np.float32)
            for t in reversed(range(T)):
                nonterminal = 1.0 - (next_done if t == T - 1 else buf["done"][t + 1])
                nv = next_value if t == T - 1 else buf["v"][t + 1]
                delta = buf["r"][t] + cfg["gamma"] * nv * nonterminal - buf["v"][t]
                last = delta + cfg["gamma"] * cfg["gae_lambda"] * nonterminal * last
                adv[t] = last
            ret = adv + buf["v"]
            mask = buf["mask"]
            adv_n = (adv - adv[mask].mean()) / (adv[mask].std() + 1e-8)
            X, A = torch.from_numpy(buf["x"]), torch.from_numpy(buf["a"])
            LOGP, ADV, RET = torch.from_numpy(buf["logp"]), torch.from_numpy(adv_n), torch.from_numpy(ret)
            D, M, OPP = torch.from_numpy(buf["done"]), torch.from_numpy(mask), torch.from_numpy(buf["opp"])
            HELD = torch.from_numpy(buf["held"])
            kd_coef = 0.0 if teacher is None else max(cfg["teacher_floor"],
                cfg["teacher_kl"] * max(0.0, 1.0 - update / max(1.0, cfg["teacher_fade"] * n_updates)))
            learner.train()
            clipfracs, kls = [], []
            losses = collections.defaultdict(float)
            read_now = bool(human_pieces)    # one batch of people's duels per update
            for _ in range(cfg["epochs"]):
                for idx in torch.randperm(S).chunk(cfg["minibatches"]):
                    logits, newv, opp_logits = learner.evaluate(X[:, idx], (start_state[0][:, idx], start_state[1][:, idx]),
                                                                D[:, idx])
                    m = M[:, idx]
                    if not m.any():
                        continue
                    _, newlogp, entropy = pol._choose(logits, A[:, idx])
                    ratio = (newlogp - LOGP[:, idx]).exp()
                    a_ = ADV[:, idx]
                    pg_loss = torch.max(-a_ * ratio, -a_ * ratio.clamp(1 - cfg["clip"], 1 + cfg["clip"]))[m].mean()
                    v_loss = 0.5 * ((newv - RET[:, idx]) ** 2)[m].mean()
                    _, opp_logp, _ = pol._choose(opp_logits, OPP[:, idx])
                    opp_loss = -opp_logp.mean() / len(ACTION_NVEC)
                    loss = (pg_loss - cfg["ent_coef"] * entropy[m].mean() + cfg["vf_coef"] * v_loss
                            + cfg["opponent_coef"] * opp_loss)
                    flat_x, flat_logits = X[:, idx][m], logits[m]
                    if kd_coef > 0:
                        with torch.no_grad():
                            if teacher_recurrent:
                                teacher_logits = teacher.evaluate(X[:, idx], (teacher_start[0][:, idx],
                                                                              teacher_start[1][:, idx]), D[:, idx])[0][m]
                            else:
                                teacher_logits = teacher.actor(flat_x)
                        kd_loss = kickstart.distill_kl(flat_logits, teacher_logits, skip=cfg["teacher_skip"],
                                                       reverse=cfg["teacher_reverse_kl"])
                        loss = loss + kd_coef * kd_loss
                        losses["teacher_kl"] += kd_loss.item()
                    if anchor is not None:
                        with torch.no_grad():
                            anchor_logits = anchor.evaluate(X[:, idx], (anchor_start[0][:, idx], anchor_start[1][:, idx]),
                                                            D[:, idx])[0][m]
                        anchor_loss = kickstart.distill_kl(flat_logits, anchor_logits, skip=anchor_skip)
                        loss = loss + cfg["anchor_kl"] * anchor_loss
                        losses["anchor_kl"] += anchor_loss.item()
                    if prior is not None:
                        with torch.no_grad():
                            prior_logits = prior.actor(flat_x)
                        human_loss = kickstart.distill_kl(flat_logits, prior_logits)
                        loss = loss + cfg["human_kl"] * human_loss
                        losses["human_kl"] += human_loss.item()
                    if cfg["entropy_floor"] > 0:
                        head_h = torch.stack([torch.distributions.Categorical(logits=l).entropy().mean()
                                              for l in torch.split(flat_logits, ACTION_NVEC, dim=-1)])
                        loss = loss + cfg["entropy_floor_coef"] * torch.relu(
                            cfg["entropy_floor"] * HEAD_MAX_ENTROPY - head_h).sum()
                    if read_now:
                        pick = rng.choice(len(human_pieces), min(cfg["human_read_batch"], len(human_pieces)), replace=False)
                        read_loss = readpeople.reading_loss(learner, [human_pieces[i] for i in pick])
                        loss = loss + cfg["human_read_coef"] * read_loss
                        losses["human_read_loss"] += read_loss.item()
                        read_now = False
                    if cfg["option_floor"] > 0:
                        parts = torch.split(flat_logits, ACTION_NVEC, dim=-1)
                        mean_p = torch.cat([torch.softmax(parts[k], -1).mean(0) for k in cfg["option_floor_controls"]])
                        loss = loss + cfg["option_floor_coef"] * torch.relu(cfg["option_floor"] - mean_p).sum()
                    optimizer.zero_grad()
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(learner.parameters(), cfg["max_grad_norm"])
                    optimizer.step()
                    with torch.no_grad():
                        own = m & ~HELD[:, idx]     # held combos aren't the policy's choice
                        if own.any():
                            lr_ = (newlogp - LOGP[:, idx])[own]
                            kls.append(((lr_.exp() - 1) - lr_).mean().item())
                            clipfracs.append(((ratio[own] - 1).abs() > cfg["clip"]).float().mean().item())
                    losses["policy_loss"] += pg_loss.item()
                    losses["value_loss"] += v_loss.item()
                    losses["opponent_loss"] += opp_loss.item()
                    losses["entropy"] += entropy[m].mean().item()
                    losses["n"] += 1
            if not np.isfinite([p.detach().sum().item() for p in learner.parameters()]).all():
                raise FloatingPointError(f"non-finite weights after update {update + 1}; resume from the last checkpoint")

            # how well it reads the opponent: greedy prediction accuracy, early vs late in fights
            with torch.no_grad():
                _, _, opp_logits = learner.evaluate(X, start_state, D)
                pred = torch.stack([l.argmax(-1) for l in torch.split(opp_logits, ACTION_NVEC, dim=-1)], dim=-1)
                correct = (pred == OPP).float().mean(-1).numpy()
            tick = buf["tick"]
            own_steps = mask & ~buf["held"]
            u = update + 1
            n_mb = max(1, losses.pop("n"))
            metrics = {k: v / n_mb for k, v in losses.items()}
            metrics.update({"decisions": decisions_done, "decisions_per_s": T * E / (time.perf_counter() - t0),
                            "approx_kl": float(np.mean(kls)) if kls else 0.0,
                            "clipfrac": float(np.mean(clipfracs)) if clipfracs else 0.0,
                            "elo": league.learner["elo"], "teacher_coef": kd_coef, "lr": optimizer.param_groups[0]["lr"],
                            "opp_acc_early": float(correct[tick < EARLY_TICKS].mean()) if (tick < EARLY_TICKS).any() else 0.0,
                            "opp_acc_late": float(correct[tick > LATE_TICKS].mean()) if (tick > LATE_TICKS).any() else 0.0,
                            # the policy's own key choices (held combos left out)
                            "key_back_share": float((buf["a"][..., 0][own_steps] == 2).mean()),
                            "key_jump_share": float((buf["a"][..., 2][own_steps] == 1).mean()),
                            "combo_share": float(buf["held"][mask].mean())})
            for name, q in results.items():
                metrics[f"score_vs_{name}"] = float(np.mean(q))
            if fight_stats:
                hits = sum(f["hits"] for f in fight_stats)
                metrics["hits_per_fight"] = hits / len(fight_stats)
                metrics["crit_share"] = sum(f["crits"] for f in fight_stats) / max(hits, 1)
                metrics["whiff_share"] = sum(f["whiffs"] for f in fight_stats) / max(1, sum(f["swings"] for f in fight_stats))
                total = lambda key: sum(f[key] for f in fight_stats)
                metrics["first_hit_share"] = total("opened") / max(1, total("exchanges"))
                metrics["air_hit_taken_share"] = total("air_hits_taken") / max(1, total("hits_taken"))
                metrics["weak_swing_share"] = total("weak_swings") / max(1, total("swings"))
            if style_held and (u % 25 == 0 or u == start_update + 1):
                learner.eval()
                metrics["style_nll"], metrics["style_same"] = youopponent.style_score(learner, style_held)
            if human_held and (u % 25 == 0 or u == start_update + 1):
                learner.eval()
                mem, wiped = readpeople.read_score(learner, human_held)
                metrics.update({"human_read_acc": mem, "human_read_gain": mem - wiped})
            mlflow.log_metrics(metrics, step=u)
            scores = "  ".join(f"{k[9:]} {v:.2f}" for k, v in sorted(metrics.items()) if k.startswith("score_vs_"))
            log(f"update {u}/{n_updates}  {decisions_done:,} decisions  {metrics['decisions_per_s']:.0f}/s  "
                f"elo {league.learner['elo']:.0f}  teacher {kd_coef:.2f}  hits/fight {metrics.get('hits_per_fight', 0):.1f}  "
                f"whiffs {metrics.get('whiff_share', 0):.0%}  crits {metrics.get('crit_share', 0):.0%}  "
                f"S {metrics['key_back_share']:.0%}  jump {metrics['key_jump_share']:.0%}  "
                f"opens {metrics.get('first_hit_share', 0):.0%}  hit in air {metrics.get('air_hit_taken_share', 0):.0%}  "
                f"weak {metrics.get('weak_swing_share', 0):.0%}  "
                + (f"reads you {metrics['human_read_acc']:.1%} ({metrics['human_read_gain']:+.1%} from memory)  "
                   if "human_read_acc" in metrics else "")
                + (f"plays like you {metrics['style_same']:.1%} (nll {metrics['style_nll']:.2f})  "
                   if "style_same" in metrics else "") +
                f"reads opponent {metrics['opp_acc_early']:.2f} early / {metrics['opp_acc_late']:.2f} late  {scores}")
            if u % cfg["snapshot_every"] == 0:
                league.add_snapshot(f"rnn-gen-{u:05d}", lambda path: pol.save(path, learner))
            if u % cfg["save_every"] == 0 or u == n_updates:
                save_checkpoint(ckpt_dir / "latest.pt", u)
                league.save()
            if u % cfg["keep_every"] == 0:
                shutil.copy(ckpt_dir / "latest.pt", ckpt_dir / f"update-{u:05d}.pt")

        pool.shutdown()
        for a in arenas:
            a.env.close()
        mlflow.log_dict({"league": league.table()}, "league.json")
        mlflow.log_artifact(str(ckpt_dir / "latest.pt"), "checkpoint")
        if cfg["run_gate"]:
            gate(ckpt_dir / "latest.pt", cfg, log=log)
    return run_dir
