"""Human in the loop: the owner duels a trained policy, and the bot learns from it through a copy.

  arena duel  [--policy champion] [--fights 5]   fight it on localhost:25566; your play is recorded
  arena clone                                    train the "you-clone" on every recorded duel
  arena train --resume RUN --decisions N         the clone joins that run's league (CLONE_SHARE of
                                                 fights); the bot practises against it at 15× speed

One duel is ~300 decisions, far too few to train the bot directly; instead each duel teaches a
behaviour-cloned copy of the person (DAgger-style: every new duel is against the latest bot, so the
clone learns the situations the bot now creates). The clone starts from the champion's weights, so
it already knows the game and only has to learn the person's choices.

What is recorded, per decision (2 ticks), in the policy's own format:
  the person's observation   the same 37 numbers a policy would see from their side (200 ms delay)
  the person's action        movement keys from their client's input packets (exact), swings read
                             from their attack cooldown, turns from their rotation (rounded to the
                             policy's turn steps)
Stored in selfplay/duels/duel-*.npz (no names) and selfplay/duels/results.json, plus
duel-*.trace.json.gz: every step's full server state for both fighters (exact position, health,
charge, keys, hits, swings), for analysing where the bot loses (`arena analyse`).
"""

import datetime
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import torch

from . import kickstart, play
from . import policy as pol
from .env import PITCH_BINS, YAW_BINS, SwordArenaEnv, outcome_score, wrap_degrees

DUELS_DIR = play.SELFPLAY_DIR / "duels"
CLONE_FILE = play.SELFPLAY_DIR / "you-clone.pt"
FIGHT_TICKS = 600          # what policies train on
COUNTDOWN_TICKS = 60


def human_action(before, after, events, side, repeat):
    """What the person did during one step → the policy's MultiDiscrete action."""
    k = after["bots"][side]["keys"]
    fb = 1 if k["forward"] and not k["back"] else 2 if k["back"] and not k["forward"] else 0
    lr = 1 if k["left"] and not k["right"] else 2 if k["right"] and not k["left"] else 0
    swung = any(e["type"] == "swing" and e["bot"] == side for e in events)
    dyaw = wrap_degrees(after["bots"][side]["yaw"] - before["bots"][side]["yaw"]) / repeat
    dpitch = (after["bots"][side]["pitch"] - before["bots"][side]["pitch"]) / repeat
    return np.array([fb, lr, int(k["jump"]), int(k["sprint"]), int(k["sneak"]), int(swung),
                     int(np.abs(YAW_BINS - dyaw).argmin()), int(np.abs(PITCH_BINS - dpitch).argmin())], dtype=np.int64)


def duel(policy="champion", fights=5, port=25570, log=print):
    path = play.resolve(policy)
    policy_sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()   # which checkpoint, even if the file changes later
    if path is None:
        raise ValueError("duel a trained policy (champion, latest, a run or a .pt), not a scripted bot")
    bot = pol.PolicyFighter(path)
    env = SwordArenaEnv(port=port, max_ticks=FIGHT_TICKS, seed=int(datetime.datetime.now().timestamp()))
    env.client = None
    xs, acts, rewards, fight_ids, results, traces = [], [], [], [], [], []
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        obs, _ = env.reset()             # opens the connection (a normal bot fight, never stepped)
        env.client.duel(True)
        env.client.realtime(True)
        log(f"duel: you vs {policy}, {fights} fights of {FIGHT_TICKS // 20} s; the countdown starts now")
        for f in range(fights):
            obs, _ = env.reset(options={"countdown": COUNTDOWN_TICKS, "start_distance": (6.0, 10.0)})
            if not env.state.get("duel"):
                log("you left the server: duel over")
                break
            memory = pol.ActionMemory(1)
            bot.reset()                      # a recurrent bot starts each fight with an empty memory
            hits = [0, 0]
            trace = [env.state]
            while env.agents:
                x = memory.features(obs["bot_1"][None])[0]
                before = env.state
                obs, r, *_ = env.step({"bot_0": bot(env.observation(0, bot.obs_layout)), "bot_1": {}})
                a = human_action(before, env.state, env.state["events"], 1, env.repeat)
                memory.push(a[None])
                trace.append(env.state)
                xs.append(x)
                acts.append(a)
                rewards.append(r["bot_1"])
                fight_ids.append(f)
                hits = [h + sum(1 for e in env.state["events"] if e["type"] == "hit" and e["attacker"] == i)
                        for i, h in enumerate(hits)]
            if not env.state.get("duel"):
                log("you left the server: duel over")
                break
            score = outcome_score(env.state, 1)
            traces.append({"fight": f, "policy": str(path), "policy_sha": policy_sha, "steps": trace})
            results.append({"fight": f, "you_score": score, "seconds": env.state["tick"] / 20,
                            "your_hits": hits[1], "bot_hits": hits[0], "policy": str(path)})
            how = "won" if score == 1 else "lost" if score == 0 else f"time, {score:.2f} on health lead"
            log(f"  fight {f + 1}: you {how} ({hits[1]} hits to {hits[0]}, {env.state['tick'] / 20:.1f} s)")
    finally:
        try:
            if env.client is not None:
                env.client.duel(False)
        finally:
            env.close()
    if xs:
        DUELS_DIR.mkdir(parents=True, exist_ok=True)
        rets = _returns(np.array(rewards, np.float32), np.array(fight_ids))
        np.savez_compressed(DUELS_DIR / f"duel-{stamp}.npz", x=np.array(xs, np.float32), a=np.array(acts, np.int64),
                            ret=rets, fight=np.array(fight_ids), layout=np.array(env.obs_layout))
        with gzip.open(DUELS_DIR / f"duel-{stamp}.trace.json.gz", "wt", encoding="utf-8") as fh:
            json.dump({"bot": 0, "you": 1, "repeat": env.repeat, "fights": traces}, fh)
        log_file = DUELS_DIR / "results.json"
        history = json.loads(log_file.read_text(encoding="utf-8")) if log_file.exists() else []
        history.append({"duel": stamp, "fights": results})
        log_file.write_text(json.dumps(history, indent=1), encoding="utf-8")
        if results:
            log(f"your average score {np.mean([r['you_score'] for r in results]):.2f} "
                f"over {len(results)} fights; {len(xs)} decisions recorded in {DUELS_DIR}")
    return results


def _returns(rewards, fights, gamma=0.995):
    out = np.zeros_like(rewards)
    g = 0.0
    for t in reversed(range(len(rewards))):
        if t == len(rewards) - 1 or fights[t + 1] != fights[t]:
            g = 0.0
        g = rewards[t] + gamma * g
        out[t] = g
    return out


def replay_trace(trace_file, layout=None):
    """Your decisions from a duel's server trace, rebuilt in observation `layout` (default the
    current one): the env replays the recorded states offline, as if the duel were live."""
    from .env import OBS_LAYOUT
    layout = layout or OBS_LAYOUT
    with gzip.open(trace_file, "rt", encoding="utf-8") as fh:
        data = json.load(fh)
    env = SwordArenaEnv(obs_layout=layout, max_ticks=FIGHT_TICKS)
    xs, acts, rewards, fight_ids = [], [], [], []
    for fight in data["fights"]:
        steps = fight["steps"]
        env.begin(steps[0], fight_ticks=FIGHT_TICKS)
        memory = pol.ActionMemory(1)
        for k in range(1, len(steps)):
            x = memory.features(env.observation(1)[None])[0]
            _, r, *_ = env.advance(steps[k])
            a = human_action(steps[k - 1], steps[k], steps[k]["events"], 1, data["repeat"])
            memory.push(a[None])
            xs.append(x)
            acts.append(a)
            rewards.append(r["bot_1"])
            fight_ids.append(fight["fight"])
    rets = _returns(np.array(rewards, np.float32), np.array(fight_ids))
    return np.array(xs, np.float32), np.array(acts, np.int64), rets


def clone_data(log=print):
    """Every duel's decisions in the current observation layout: recorded ones as they are, older
    ones rebuilt from their server traces; duels with neither are left out."""
    from .env import OBS_LAYOUT
    xs, acts, rets, used, skipped = [], [], [], 0, 0
    for f in sorted(DUELS_DIR.glob("duel-*.npz")):
        part = np.load(f)
        layout = int(part["layout"]) if "layout" in part else 1      # older duels: layout 1
        trace = f.with_name(f.stem + ".trace.json.gz")
        if layout == OBS_LAYOUT:
            x, a, r = part["x"], part["a"], part["ret"]
        elif trace.exists():
            x, a, r = replay_trace(trace)
        else:
            skipped += 1
            continue
        xs.append(x)
        acts.append(a)
        rets.append(r)
        used += 1
    if skipped:
        log(f"you-clone: {skipped} early duel(s) have no server trace in this layout and are left out")
    if not xs:
        raise FileNotFoundError(f"no usable duels in {DUELS_DIR}: run `arena duel` first")
    return np.concatenate(xs), np.concatenate(acts), np.concatenate(rets), used


def train_clone(start="champion", epochs=30, log=print):
    """Behaviour cloning on every recorded duel, starting from `start`'s weights. → metrics."""
    x, a, ret, n_duels = clone_data(log=log)
    files = range(n_duels)
    log(f"you-clone: {len(x)} of your decisions from {len(files)} duel(s), starting from {start}")
    clone = pol.upgrade(pol.load(play.resolve(start))[0])
    metrics = kickstart.train_bc(clone, x, a, ret, epochs=epochs, lr=3e-4, log=log)
    pol.save(CLONE_FILE, clone)
    _log_clone_run(metrics, len(files))
    log(f"saved {CLONE_FILE}")
    return metrics


def _log_clone_run(metrics, duels):
    import mlflow
    from .train import _setup_mlflow
    _setup_mlflow()
    with mlflow.start_run(run_name=datetime.datetime.now().strftime("you-clone-%Y%m%d-%H%M%S")):
        mlflow.set_tags({"stage": "you-clone"})
        mlflow.log_params({"duels": duels})
        mlflow.log_metrics(metrics)
        mlflow.log_artifact(str(CLONE_FILE), "clone")
