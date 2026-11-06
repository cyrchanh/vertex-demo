"""Milestone 4: PPO self-play with a league, over one or more arena servers.

  python -m vertex_training arena train [--servers 2] [--decisions 2000000] [--resume RUN]

Each server is one arena (two bots). Every decision, the learner network acts for each side it
controls; the other side is its current self, a frozen snapshot or a scripted bot (league.py).
After `rollout` decisions per arena, PPO updates the network on the learner's own experience.

Stored under %LOCALAPPDATA%\\vertex-training\\selfplay\\<run>\\:
  checkpoints/latest.pt        weights + optimizer + counters, every `save_every` updates (resume
                               from here); update-NNNNN.pt kept every `keep_every` updates
  league/gen-NNNN.pt           frozen snapshots the learner fights
  league/league.json           Elo ratings and recent scores
  config.json                  the settings of the run
MLflow (experiment vertex-selfplay) records the curves; at the end the champion gate runs and, if
passed, the policy is registered as vertex-sword-policy@champion and copied to selfplay/champion.pt.
"""

import collections
import concurrent.futures
import datetime
import json
import shutil
import time

import mlflow
import numpy as np
import torch
from mlflow import MlflowClient

from .. import config
from . import kickstart, play
from . import policy as pol
from .env import ACTION_NVEC, OBS_SIZES, SwordArenaEnv, outcome_score
from .league import League

EXPERIMENT = "vertex-selfplay"
REGISTERED = "vertex-sword-policy"
CHAMPION = "champion"

DEFAULTS = dict(
    servers=1, decisions=2_000_000,       # decisions per arena × arenas, in total
    rollout=256, epochs=4, minibatches=4, lr=2.5e-4, gamma=0.995, gae_lambda=0.95, clip=0.2,
    ent_coef=0.01, vf_coef=0.5, max_grad_norm=0.5, hidden=256,
    save_every=10, keep_every=100, snapshot_every=25,
    max_ticks=600, repeat=2, reaction_steps=2, aim_noise=1.0, seed=0,
    kickstart_decisions=60_000, kickstart_epochs=8,   # behaviour cloning of the scripted bots first
    kickstart_kl=1.0, kickstart_fade=0.3,              # distillation weight, gone after 30% of updates
    gate_fights=60, gate_score=0.6,                   # 20 fights left about ±0.1 of luck
    clone=True,                                        # the owner's you-clone joins the league if it exists
    curriculum_share=None, focus_style=None,           # after handover: more curriculum fights / one style
    obs_layout=2,                                      # env.OBS_SIZES; a resumed layout-1 run is upgraded
    whiff_cost=0.0,                                    # reward taken off for each swing at nothing (training
                                                       # only; 0.02 ≈ 0.4 HP): not zero-sum, like tiredness
    human_kl=0.0,                                      # pull toward the you-clone (human-regularised RL, as in
                                                       # Cicero's piKL): KL(clone ‖ learner), mass-covering, so
                                                       # what the owner does stays possible; the reward decides
    entropy_floor=0.0, entropy_floor_coef=0.05,        # per control: penalise entropy below floor × its maximum,
                                                       # so no key (S, jump, …) can die out
)
HEAD_MAX_ENTROPY = torch.log(torch.tensor(ACTION_NVEC, dtype=torch.float32))
SELF_PLAY_START = (2.0, 10.0)   # start distances for self-play and snapshot fights


def arena_port(i):
    return 25570 + 10 * i


class Arena:
    """One server's fight: who controls which side."""

    def __init__(self, env):
        self.env = env
        self.kind = None          # "self" | "snapshot" | "curriculum"
        self.opponent = None      # snapshot name or curriculum bucket
        self.learner_side = 0
        self.bot = None           # scripted fighter
        self.frozen = None        # snapshot policy
        self.stats = None

    def learns(self, side):
        return self.kind == "self" or side == self.learner_side


def _setup_mlflow():
    config.HOME.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(config.TRACKING_URI)
    if mlflow.get_experiment_by_name(EXPERIMENT) is None:
        mlflow.create_experiment(EXPERIMENT, artifact_location=config.ARTIFACTS_DIR.as_uri())
    mlflow.set_experiment(EXPERIMENT)


def train(log=print, resume=None, **overrides):
    saved = {}
    if resume and (play.SELFPLAY_DIR / resume / "config.json").exists():
        saved = json.loads((play.SELFPLAY_DIR / resume / "config.json").read_text(encoding="utf-8"))
    if overrides.get("arch", saved.get("arch")) == "lstm":
        from . import train_rnn      # the policy with memory has its own loop
        return train_rnn.train(log=log, resume=resume, **overrides)
    if resume:
        run_dir = play.SELFPLAY_DIR / resume
        cfg = {**DEFAULTS, **json.loads((run_dir / "config.json").read_text(encoding="utf-8"))}
        cfg.update({k: v for k, v in overrides.items() if v is not None})
    else:
        cfg = {**DEFAULTS, **{k: v for k, v in overrides.items() if v is not None}}
        run_dir = play.SELFPLAY_DIR / datetime.datetime.now().strftime("run-%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    ckpt_dir = run_dir / "checkpoints"

    torch.manual_seed(cfg["seed"])
    torch.set_num_threads(2)   # leave the cores to the servers
    rng = np.random.default_rng(cfg["seed"])
    learner = pol.SwordPolicy(hidden=cfg["hidden"], obs_layout=cfg["obs_layout"])
    optimizer = torch.optim.Adam(learner.parameters(), lr=cfg["lr"], eps=1e-5)
    start_update, decisions_done, mlflow_run_id = 0, 0, None
    if resume and (ckpt_dir / "latest.pt").exists():
        learner, ckpt = pol.load(ckpt_dir / "latest.pt")
        optimizer = torch.optim.Adam(learner.parameters(), lr=cfg["lr"], eps=1e-5)
        if learner.obs_layout != cfg["obs_layout"]:
            log(f"upgrading the learner from observation layout {learner.obs_layout} to {cfg['obs_layout']} "
                f"(it plays the same at first; fresh optimizer)")
            learner = pol.upgrade(learner, cfg["obs_layout"])
            optimizer = torch.optim.Adam(learner.parameters(), lr=cfg["lr"], eps=1e-5)
        else:
            optimizer.load_state_dict(ckpt["optimizer"])
        learner.train()
        start_update, decisions_done, mlflow_run_id = ckpt["update"], ckpt["decisions"], ckpt.get("mlflow_run_id")
        log(f"resumed {run_dir.name} at update {start_update} ({decisions_done:,} decisions)")
    league = League(run_dir / "league", seed=cfg["seed"] + start_update,
                    curriculum_share=cfg["curriculum_share"], focus=cfg["focus_style"])
    from .duel import CLONE_FILE
    if cfg["clone"] and CLONE_FILE.exists():
        league.use_clone(CLONE_FILE)
        log(f"the you-clone joins the league ({CLONE_FILE})")

    E = cfg["servers"]
    S = 2 * E
    T = cfg["rollout"]
    n_updates = max(1, cfg["decisions"] // (T * E))
    arenas = [Arena(SwordArenaEnv(port=arena_port(i), max_ticks=cfg["max_ticks"], repeat=cfg["repeat"],
                                  reaction_steps=cfg["reaction_steps"], seed=cfg["seed"] * 1000 + i * 100_000,
                                  aim_noise=cfg["aim_noise"], obs_layout=cfg["obs_layout"])) for i in range(E)]
    memory = pol.ActionMemory(S)
    obs = np.zeros((S, OBS_SIZES[cfg["obs_layout"]]), dtype=np.float32)
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
        a.stats = {"hits": [0, 0], "crits": [0, 0], "sprint_hits": [0, 0], "damage": [0.0, 0.0],
                   "swings": [0, 0], "whiffs": [0, 0]}
        start = league.curriculum.start_distance(a.opponent) if a.kind == "curriculum" else SELF_PLAY_START
        o, _ = a.env.reset(options={"start_distance": start})
        for side, agent in enumerate(a.env.possible_agents):
            obs[2 * e + side] = o[agent]
            memory.reset(2 * e + side)

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
        sides = [0, 1] if a.kind == "self" else [a.learner_side]
        for side in sides:
            st = a.stats
            fight_stats.append({"ticks": a.env.state["tick"], "hits": st["hits"][side], "crits": st["crits"][side],
                                "sprint_hits": st["sprint_hits"][side], "damage": st["damage"][side],
                                "swings": st["swings"][side], "whiffs": st["whiffs"][side]})
        if a.kind == "curriculum":
            score = outcome_score(a.env.state, a.learner_side)
            results["curriculum"].append(score)
            results["style_" + league.curriculum.buckets[a.opponent]["style"]].append(score)
            if league.record_curriculum(a.opponent, score):
                log("  curriculum complete: every bucket at full skill; handing over to the league")
        elif a.kind == "snapshot":
            score = outcome_score(a.env.state, a.learner_side)
            league.record(a.opponent, score)
            results["you_clone" if a.opponent == "you-clone" else "snapshots"].append(score)

    def save_checkpoint(path, update):
        pol.save(path, learner, optimizer=optimizer.state_dict(), update=update, decisions=decisions_done,
                 mlflow_run_id=mlflow_run.info.run_id, config=json.dumps(cfg))

    _setup_mlflow()
    pool = concurrent.futures.ThreadPoolExecutor(E)
    buf_x = np.zeros((T, S, learner.input_size), dtype=np.float32)
    buf_a = np.zeros((T, S, len(ACTION_NVEC)), dtype=np.int64)
    buf_logp = np.zeros((T, S), dtype=np.float32)
    buf_v = np.zeros((T, S), dtype=np.float32)
    buf_r = np.zeros((T, S), dtype=np.float32)
    buf_done = np.zeros((T, S), dtype=np.float32)
    buf_mask = np.zeros((T, S), dtype=bool)
    next_done = np.zeros(S, dtype=np.float32)

    with mlflow.start_run(run_id=mlflow_run_id, run_name=None if mlflow_run_id else run_dir.name) as mlflow_run:
        if not mlflow_run_id:
            mlflow.set_tags({"stage": "selfplay", "run_dir": str(run_dir)})
            mlflow.log_params(cfg)
        teacher = None
        teacher_file = run_dir / "kickstart.pt"
        if cfg["kickstart_decisions"] > 0 and not teacher_file.exists() and start_update == 0:
            log(f"kickstart: {cfg['kickstart_decisions']:,} decisions of scripted-bot fights to copy")
            kx, ka, kr = kickstart.collect([arena_port(i) for i in range(E)], cfg["kickstart_decisions"],
                                           seed=cfg["seed"], gamma=cfg["gamma"], max_ticks=cfg["max_ticks"])
            mlflow.log_metrics(kickstart.train_bc(learner, kx, ka, kr, epochs=cfg["kickstart_epochs"],
                                                  seed=cfg["seed"], log=log))
            pol.save(teacher_file, learner)
            optimizer = torch.optim.Adam(learner.parameters(), lr=cfg["lr"], eps=1e-5)   # fresh for PPO
        prior = None
        if cfg["human_kl"] > 0:
            from .duel import CLONE_FILE
            if CLONE_FILE.exists():
                prior = pol.load(CLONE_FILE)[0]
                if prior.obs_layout != learner.obs_layout:
                    log("the you-clone uses another observation layout: no human prior (re-run arena clone)")
                    prior = None
                else:
                    log(f"human prior: KL to the you-clone with weight {cfg['human_kl']}")
        if cfg["kickstart_decisions"] > 0 and teacher_file.exists():
            teacher = pol.load(teacher_file)[0]
            if teacher.obs_layout != learner.obs_layout:
                teacher = None     # its distillation has faded out by now anyway
        for e in range(E):
            new_fight(e)
        log(f"{run_dir.name}: {n_updates} updates of {T} decisions × {E} arena(s); checkpoints in {ckpt_dir}")
        for update in range(start_update, n_updates):
            t0 = time.perf_counter()
            for g in optimizer.param_groups:
                g["lr"] = cfg["lr"] * (1.0 - update / n_updates)
            learner.eval()
            for t in range(T):
                x = memory.features(obs)
                if not np.isfinite(x).all():
                    raise FloatingPointError(f"non-finite observation at update {update + 1}: {np.argwhere(~np.isfinite(x))[:5]}")
                with torch.no_grad():
                    act, logp, _, value = learner.act(torch.from_numpy(x))
                act = act.numpy()
                for e, a in enumerate(arenas):
                    if a.kind == "snapshot":
                        side = 1 - a.learner_side
                        slot = 2 * e + side
                        xf = x[slot:slot + 1]
                        if a.frozen.obs_layout != learner.obs_layout:   # an older snapshot sees its own layout
                            xf = memory.features(a.env.observation(side, a.frozen.obs_layout)[None], rows=[slot])
                        with torch.no_grad():
                            act[slot] = a.frozen.act(torch.from_numpy(xf))[0].numpy()[0]
                buf_x[t], buf_a[t], buf_logp[t], buf_v[t] = x, act, logp.numpy(), value.numpy()
                buf_done[t] = next_done
                buf_mask[t] = [arenas[s // 2].learns(s % 2) for s in range(S)]
                outcomes = list(pool.map(step, range(E), [act] * E))
                memory.push(act)
                for e, (o, rewards, terms, truncs, infos) in enumerate(outcomes):
                    a = arenas[e]
                    for side, agent in enumerate(a.env.possible_agents):
                        buf_r[t, 2 * e + side] = rewards[agent]
                        obs[2 * e + side] = o[agent]
                    for ev in infos["bot_0"]["events"]:
                        if ev["type"] == "swing":
                            k = ev["bot"]
                            a.stats["swings"][k] += 1
                            if not ev["hit"]:
                                a.stats["whiffs"][k] += 1
                                buf_r[t, 2 * e + k] -= cfg["whiff_cost"]
                        if ev["type"] == "hit":
                            k = ev["attacker"]
                            a.stats["hits"][k] += 1
                            a.stats["crits"][k] += int(ev["crit"])
                            a.stats["sprint_hits"][k] += int(ev["sprint_hit"])
                            a.stats["damage"][k] += ev["damage"]
                    done = not a.env.agents
                    next_done[2 * e:2 * e + 2] = float(done)
                    if done:
                        end_fight(e)
                        new_fight(e)
                decisions_done += E

            # ---- PPO update on the learner's own experience ----
            with torch.no_grad():
                next_value = learner.value(torch.from_numpy(memory.features(obs))).numpy()
            adv = np.zeros((T, S), dtype=np.float32)
            last = np.zeros(S, dtype=np.float32)
            for t in reversed(range(T)):
                nonterminal = 1.0 - (next_done if t == T - 1 else buf_done[t + 1])
                nv = next_value if t == T - 1 else buf_v[t + 1]
                delta = buf_r[t] + cfg["gamma"] * nv * nonterminal - buf_v[t]
                last = delta + cfg["gamma"] * cfg["gae_lambda"] * nonterminal * last
                adv[t] = last
            ret = adv + buf_v
            m = buf_mask.reshape(-1)
            bx = torch.from_numpy(buf_x.reshape(-1, learner.input_size)[m])
            ba = torch.from_numpy(buf_a.reshape(-1, len(ACTION_NVEC))[m])
            blogp = torch.from_numpy(buf_logp.reshape(-1)[m])
            badv = torch.from_numpy(adv.reshape(-1)[m])
            bret = torch.from_numpy(ret.reshape(-1)[m])
            bval = torch.from_numpy(buf_v.reshape(-1)[m])
            n = bx.shape[0]
            # Normalised over the whole batch: per minibatch, a leftover chunk of one sample has no
            # standard deviation (NaN), which once poisoned every weight.
            badv = (badv - badv.mean()) / (badv.std() + 1e-8)
            learner.train()
            clipfracs, kls = [], []
            kd_coef = 0.0 if teacher is None else (
                cfg["kickstart_kl"] * max(0.0, 1.0 - update / max(1.0, cfg["kickstart_fade"] * n_updates)))
            kd_loss = torch.tensor(0.0)
            human_loss = torch.tensor(0.0)
            for _ in range(cfg["epochs"]):
                for idx in torch.randperm(n).chunk(cfg["minibatches"]):
                    _, newlogp, entropy, newv = learner.act(bx[idx], ba[idx])
                    ratio = (newlogp - blogp[idx]).exp()
                    a_ = badv[idx]
                    pg_loss = torch.max(-a_ * ratio, -a_ * ratio.clamp(1 - cfg["clip"], 1 + cfg["clip"])).mean()
                    v_loss = 0.5 * ((newv - bret[idx]) ** 2).mean()
                    loss = pg_loss - cfg["ent_coef"] * entropy.mean() + cfg["vf_coef"] * v_loss
                    if prior is not None:
                        with torch.no_grad():
                            prior_logits = prior.actor(bx[idx])
                        human_loss = kickstart.distill_kl(learner.actor(bx[idx]), prior_logits)
                        loss = loss + cfg["human_kl"] * human_loss
                    if cfg["entropy_floor"] > 0:
                        head_h = learner.head_entropy(bx[idx]).mean(0)
                        floor_loss = torch.relu(cfg["entropy_floor"] * HEAD_MAX_ENTROPY - head_h).sum()
                        loss = loss + cfg["entropy_floor_coef"] * floor_loss
                    if kd_coef > 0:
                        # Kickstarting: stay near the cloned teacher at first; the weight fades out.
                        with torch.no_grad():
                            teacher_logits = teacher.actor(bx[idx])
                        kd_loss = kickstart.distill_kl(learner.actor(bx[idx]), teacher_logits)
                        loss = loss + kd_coef * kd_loss
                    optimizer.zero_grad()
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(learner.parameters(), cfg["max_grad_norm"])
                    optimizer.step()
                    with torch.no_grad():
                        kls.append(((ratio - 1) - (newlogp - blogp[idx])).mean().item())
                        clipfracs.append(((ratio - 1).abs() > cfg["clip"]).float().mean().item())
            if not all(torch.isfinite(p).all() for p in learner.parameters()):
                raise FloatingPointError(f"non-finite weights after update {update + 1}; resume from the last checkpoint")
            var = bret.var().item()
            explained = 1 - (bret - bval).var().item() / var if var > 0 else float("nan")

            # ---- bookkeeping ----
            u = update + 1
            seconds = time.perf_counter() - t0
            metrics = {"decisions": decisions_done, "decisions_per_s": T * E / seconds,
                       "policy_loss": pg_loss.item(), "value_loss": v_loss.item(), "entropy": entropy.mean().item(),
                       "approx_kl": float(np.mean(kls)), "clipfrac": float(np.mean(clipfracs)),
                       "explained_variance": explained, "elo": league.learner["elo"],
                       "lr": optimizer.param_groups[0]["lr"], "kickstart_coef": kd_coef,
                       "kickstart_kl": kd_loss.item(), "human_kl": human_loss.item(),
                       # how often the learner presses S and jump (they had died out by 1M decisions)
                       "key_back_share": float((buf_a[..., 0][buf_mask] == 2).mean()),
                       "key_jump_share": float((buf_a[..., 2][buf_mask] == 1).mean()),
                       "curriculum_mean_skill": league.curriculum.mean_skill(),
                       "curriculum_handed_over": float(league.curriculum.handed_over)}
            for key, b in league.curriculum.buckets.items():
                metrics["skill_" + key.replace("@", "_")] = b["skill"]
            for name, q in results.items():
                metrics[f"score_vs_{name}"] = float(np.mean(q))
            if fight_stats:
                hits = sum(f["hits"] for f in fight_stats)
                metrics["fight_seconds"] = float(np.mean([f["ticks"] for f in fight_stats])) / 20
                metrics["hits_per_fight"] = hits / len(fight_stats)
                metrics["crit_share"] = sum(f["crits"] for f in fight_stats) / max(hits, 1)
                metrics["sprint_hit_share"] = sum(f["sprint_hits"] for f in fight_stats) / max(hits, 1)
                metrics["whiff_share"] = (sum(f.get("whiffs", 0) for f in fight_stats)
                                          / max(1, sum(f.get("swings", 0) for f in fight_stats)))
            mlflow.log_metrics(metrics, step=u)
            scores = "  ".join(f"{k} {v:.2f}" for k, v in sorted(metrics.items()) if k.startswith("score_vs_"))
            log(f"update {u}/{n_updates}  {decisions_done:,} decisions  {metrics['decisions_per_s']:.0f}/s  "
                f"elo {league.learner['elo']:.0f}  skill {league.curriculum.mean_skill():.2f}  entropy {metrics['entropy']:.2f}  "
                f"hits/fight {metrics.get('hits_per_fight', 0):.1f}  whiffs {metrics.get('whiff_share', 0):.0%}  "
                f"crits {metrics.get('crit_share', 0):.0%}  S {metrics['key_back_share']:.0%}  "
                f"jump {metrics['key_jump_share']:.0%}  {scores}")
            if u % cfg["snapshot_every"] == 0:
                league.add_snapshot(f"gen-{u:05d}", lambda path: pol.save(path, learner))
                log(f"  snapshot gen-{u:05d} joined the league")
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
        gate(ckpt_dir / "latest.pt", cfg, log=log)
    return run_dir


def score_interval(per_fight, level=0.90, n_boot=2000, seed=0):
    """Bootstrap interval of the mean fight score."""
    x = np.asarray(per_fight, dtype=np.float64)
    means = np.random.default_rng(seed).choice(x, (n_boot, len(x))).mean(1)
    return float(np.quantile(means, (1 - level) / 2)), float(np.quantile(means, (1 + level) / 2))


def gate(checkpoint, cfg=None, log=print):
    """The champion gate: the policy must score at least `gate_score` against the chase, crit and
    punish bots and (if there is one) the current champion, over `gate_fights` fights each with swapped sides.
    Runs inside an active MLflow run."""
    cfg = {**DEFAULTS, **(cfg or {})}
    opponents = ["chase", "crit", "punish"] + (["champion"] if play.CHAMPION_FILE.exists() else [])
    scores = {}
    for opp in opponents:
        log(f"gate: {cfg['gate_fights']} fights against {opp}")
        per_fight = []
        scores[opp] = play.fights(str(checkpoint), opp, episodes=cfg["gate_fights"], log=None, max_ticks=cfg["max_ticks"],
                                  per_fight=per_fight)
        lo, hi = score_interval(per_fight)
        mlflow.log_metrics({f"gate_vs_{opp}_lo": lo, f"gate_vs_{opp}_hi": hi})
        log(f"  score {scores[opp]:.2f} (90% interval {lo:.2f}-{hi:.2f})")
    passed = all(s >= cfg["gate_score"] for s in scores.values())
    mlflow.log_metrics({f"gate_vs_{k}": v for k, v in scores.items()})
    mlflow.set_tags({"gate": "pass" if passed else "fail"})
    if passed:
        policy, _ = pol.load(checkpoint)
        # Saved as a Python object (pickle), not a traced graph, so any batch size works; it returns the
        # action logits.
        info = mlflow.pytorch.log_model(policy, name="policy", registered_model_name=REGISTERED,
                                        serialization_format="pickle",
                                        input_example=np.zeros((1, policy.input_size), dtype=np.float32))
        MlflowClient().set_registered_model_alias(REGISTERED, CHAMPION, info.registered_model_version)
        shutil.copy(checkpoint, play.CHAMPION_FILE)
        log(f"gate passed: {REGISTERED} v{info.registered_model_version} is @{CHAMPION} ({play.CHAMPION_FILE})")
    else:
        log("gate not passed: the champion stays as it is")
    return passed, scores


def gate_run(name, log=print):
    """Re-run the champion gate on a finished run's latest checkpoint, inside its MLflow run."""
    run_dir = play.SELFPLAY_DIR / name
    checkpoint = run_dir / "checkpoints" / "latest.pt"
    cfg = {**DEFAULTS, **json.loads((run_dir / "config.json").read_text(encoding="utf-8"))}
    _, ckpt = pol.load(checkpoint)
    _setup_mlflow()
    with mlflow.start_run(run_id=ckpt.get("mlflow_run_id")):
        return gate(checkpoint, cfg, log=log)
