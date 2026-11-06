"""Vertex training command line.

  python -m vertex_training run [--task window|exchange|all] [--no-promote] [--rebuild]
  python -m vertex_training dataset [--rebuild]
  python -m vertex_training timeline RECORDING.json.gz [--task window]
  python -m vertex_training final [--task ...]
  python -m vertex_training ui [--port 5000]
  python -m vertex_training serve [--task window] [--port 5001]
  python -m vertex_training arena bench [--matchup crit-vs-chase] [--episodes 4]
  python -m vertex_training arena watch [--matchup crit-vs-chase] [--episodes 10]   (join localhost:25566)
  python -m vertex_training arena watch --policy latest [--opponent chase]
  python -m vertex_training arena train [--servers 2] [--decisions 2000000] [--resume RUN]
  python -m vertex_training arena eval --policy latest [--opponent chase] [--episodes 20]
  python -m vertex_training arena gate --resume RUN   (re-run a finished run's champion gate)
  python -m vertex_training arena duel [--policy champion] [--fights 5]   (join localhost:25566 first)
  python -m vertex_training arena clone               (train the you-clone on your recorded duels)
  python -m vertex_training arena analyse [--duel latest|all|STAMP]   (where the bot loses)
  python -m vertex_training arena report [--policy NAME] [--episodes 60]   (report card: reading, duels, fights)
  python -m vertex_training arena youmodel [--policy champion]   (in-context copy of you + habit report)
  python -m vertex_training agentic run [--rounds 5] [--backend claude|noop] [--resume RUN]   (LLM strategists spar)
  python -m vertex_training agentic status [--resume RUN]
  python -m vertex_training agentic note "the bot never jump-crits the first hit"   (your notes, read by every strategist)
  python -m vertex_training agentic duel [--bot best|rusher|rusher:v01] [--first-to 5]   (join localhost:25596 first)
"""

import argparse
import os
import subprocess
import sys

from . import config


def main():
    parser = argparse.ArgumentParser(prog="vertex_training", description="Vertex game-sense training")
    parser.add_argument("--recordings", default=str(config.RECORDINGS_DIR))
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="end-to-end: dataset, candidates, gate, registry")
    p.add_argument("--task", choices=config.TASKS + ("all",), default="all")
    p.add_argument("--no-promote", action="store_true", help="register but never make it champion")
    p.add_argument("--rebuild", action="store_true", help="rebuild the dataset even if it exists")

    p = sub.add_parser("dataset", help="build or show the dataset")
    p.add_argument("--rebuild", action="store_true")

    p = sub.add_parser("timeline", help="champion's win chance over one recorded fight")
    p.add_argument("recording")
    p.add_argument("--task", choices=config.TASKS, default="window")

    p = sub.add_parser("final", help="score the champion on the final split (rarely)")
    p.add_argument("--task", choices=config.TASKS + ("all",), default="all")

    p = sub.add_parser("ui", help="open the MLflow web UI on this project's runs")
    p.add_argument("--port", type=int, default=5000)

    p = sub.add_parser("arena", help="self-play arena (start the server with training/arena.ps1 start)")
    p.add_argument("action", choices=["bench", "watch", "train", "eval", "gate", "duel", "clone", "analyse", "valuecheck", "youmodel", "report"])
    p.add_argument("--matchup", default="all", help="scripted bots, e.g. crit-vs-chase, or all")
    p.add_argument("--policy", help="a trained policy: latest, champion, a run name or a .pt path")
    p.add_argument("--opponent", default="chase", help="chase, crit, random, champion, latest, run or .pt")
    p.add_argument("--episodes", type=int, default=None, help="default 4 for bench, 10 for watch, 20 for eval")
    p.add_argument("--port", type=int, default=25570)
    p.add_argument("--servers", type=int, help="train: arenas to use (start them with arena.ps1 start -Count N)")
    p.add_argument("--decisions", type=int, help="train: total decisions, all arenas together")
    p.add_argument("--resume", help="train: continue this run (folder name under selfplay/)")
    p.add_argument("--fights", type=int, default=5, help="duel: fights in a row")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="train: override a setting, e.g. --set curriculum_share=0.5 --set focus_style=punish")
    p.add_argument("--duel", default="latest", help="analyse: latest, all, or a duel's date stamp")

    p = sub.add_parser("agentic", help="LLM strategists write bots, spar and reason (start its server with "
                                       "training/agentic-arena.ps1 start)")
    p.add_argument("action", choices=["run", "status", "duel", "note"])
    p.add_argument("text", nargs="?", help="note: your observation for the strategists, in quotes")
    p.add_argument("--bot", default="best", help="duel: best (highest anchor score), a strategist name, or NAME:vNN")
    p.add_argument("--first-to", type=int, default=5, help="duel: points to win (a kill, or the health lead at the time limit)")
    p.add_argument("--seconds", type=int, default=120, help="duel: time limit per fight")
    p.add_argument("--rounds", type=int, default=5, help="run: tournaments to play (strategists rewrite between them)")
    p.add_argument("--fights", type=int, default=6, help="run: fights per matchup (new runs only)")
    p.add_argument("--port", type=int, default=25600, help="run: the agentic arena's port (new runs only)")
    p.add_argument("--backend", choices=["claude", "noop"], help="run: override every strategist's backend")
    p.add_argument("--resume", help="continue (run) or show (status) this run, by folder name")
    p.add_argument("--allow-untested", action="store_true", help="run: rewrite bots even if their prediction about you has not been duelled yet")

    p = sub.add_parser("serve", help="serve the champion as a REST API")
    p.add_argument("--task", choices=config.TASKS, default="window")
    p.add_argument("--port", type=int, default=5001)

    args = parser.parse_args()
    tasks = config.TASKS if getattr(args, "task", None) == "all" else (getattr(args, "task", None),)

    # MLflow starts helpers (uvicorn) by name, so the venv's Scripts folder must be on PATH even
    # when the venv isn't activated.
    env = {**os.environ, "MLFLOW_TRACKING_URI": config.TRACKING_URI, "MLFLOW_DISABLE_AGENT_HINT": "1",
           "PATH": os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")}
    if args.command == "ui":
        # The same database and artifact folder the pipeline writes to.
        sys.exit(subprocess.call([sys.executable, "-m", "mlflow", "ui", "--backend-store-uri", config.TRACKING_URI,
                                  "--default-artifact-root", config.ARTIFACTS_DIR.as_uri(), "--port", str(args.port)],
                                 env=env))
    if args.command == "serve":
        sys.exit(subprocess.call([sys.executable, "-m", "mlflow", "models", "serve", "-m",
                                  f"models:/{config.registered_model(args.task)}@champion",
                                  "--env-manager", "local", "--port", str(args.port)], env=env))

    if args.command == "agentic":
        from .agentic import loop
        if args.action == "note":
            from .agentic import owner
            owner.add_note(args.text, args.resume)
            return
        if args.action == "duel":
            from .agentic import duel
            duel.duel(bot=args.bot, first_to=args.first_to, seconds=args.seconds, port=args.port, run=args.resume)
            return
        if args.action == "status":
            loop.status(args.resume)
            return
        loop.Run(name=args.resume, fights=args.fights, port=args.port, backend=args.backend).run(args.rounds, allow_untested=args.allow_untested)
        return

    if args.command == "arena":
        if args.action == "duel":
            from .arena import duel
            duel.duel(policy=args.policy or "champion", fights=args.fights, port=args.port)
            return
        if args.action == "report":
            from .arena import report
            report.report(policy=args.policy or "champion", duel=args.duel,
                          episodes=60 if args.episodes is None else args.episodes, port=args.port)
            return
        if args.action == "analyse":
            from .arena import analyse
            analyse.report(args.duel)
            return
        if args.action == "valuecheck":
            from .arena import valuecheck
            valuecheck.check(policy_name=args.policy or "champion",
                             which="all" if args.duel == "latest" else args.duel)
            return
        if args.action == "youmodel":
            from .arena import youmodel
            youmodel.run(policy_name=args.policy or "champion")
            return
        if args.action == "clone":
            from .arena import duel
            duel.train_clone()
            return
        if args.action == "gate":
            from .arena import train
            train.gate_run(args.resume)
            return
        if args.action == "train":
            from .arena import train
            import json as _json
            extra = {}
            for item in args.set:
                key, _, value = item.partition("=")
                try:
                    extra[key] = _json.loads(value)
                except ValueError:
                    extra[key] = value      # a plain word, e.g. punish
            train.train(resume=args.resume, servers=args.servers, decisions=args.decisions, **extra)
            return
        if args.policy or args.action == "eval":
            from .arena import play
            name = args.policy or "latest"
            watch = args.action == "watch"
            if watch:
                print(f"{name} vs {args.opponent} at 20 ticks/s: join localhost:{args.port - 4} to spectate")
            score = play.fights(name, args.opponent, episodes=args.episodes or (10 if watch else 20),
                                port=args.port, watch=watch, max_ticks=600)   # the length policies train on
            print(f"{name} scored {score:.2f} against {args.opponent} (1 = won every fight)")
            return
        from .arena import bench
        if args.action == "watch":
            matchup = "crit-vs-chase" if args.matchup == "all" else args.matchup
            print(f"[{matchup}] at 20 ticks/s: join localhost:25566 in Minecraft (multiplayer) to spectate")
            bench.run(episodes=args.episodes or 10, matchup=matchup, port=args.port, max_ticks=2400, watch=True)
            return
        for matchup in (bench.MATCHUPS if args.matchup == "all" else [args.matchup]):
            print(f"[{matchup}]")
            bench.run(episodes=args.episodes or 4, matchup=matchup, port=args.port)
        return

    from . import dataset, pipeline
    pipeline.setup()

    if args.command == "timeline":
        from . import timeline
        timeline.show(args.recording, args.task)
        return

    data = dataset.build(args.recordings, rebuild=getattr(args, "rebuild", False))
    print(f"dataset {data.version} at {data.path}")
    for task, counts in data.manifest["counts"].items():
        print(f"  {task:8s} " + "  ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    if args.command == "dataset":
        return
    for task in tasks:
        print(f"\n[{task}]")
        if args.command == "run":
            pipeline.run(task, data, promote=not args.no_promote)
        else:
            pipeline.final(task, data)
    if args.command == "run":
        print(f"\nTracking: {config.TRACKING_URI}\nSee the runs with:  python -m vertex_training ui")


if __name__ == "__main__":
    main()
