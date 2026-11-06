# Vertex training

The Python side of the AI plan ([logs/2026-11-04-ai-training-plan.md](../logs/2026-11-04-ai-training-plan.md)).
It reads the mod's recordings, never changes them, and tracks every run with **MLflow**.

## Setup (once)

```powershell
.\training\setup.ps1
```

This creates a virtual environment in `%LOCALAPPDATA%\vertex-training\venv`, outside OneDrive,
because synced folders lock files (the same reason the Gradle build dir lives outside it). After
that, `.\training\vt.ps1 <command>` runs anything below from any folder.

## The end-to-end run

```powershell
.\training\vt.ps1 run              # both tasks; --task window|exchange, --no-promote, --rebuild
```

| Stage | What happens |
|---|---|
| 1. Dataset | Recordings → features → `%LOCALAPPDATA%\vertex-training\datasets\<version>\{window,exchange}.parquet` + `manifest.json`. The version is a hash of the fight ids and the feature version, so the same recordings always give the same dataset. Only fight ids are stored, never player names |
| 2. Splits | Each fight is placed by a hash of its id: **70% train, 20% test (the gate), 10% final**. A fight never moves between splits when new recordings arrive |
| 3. Candidates | Baselines (base rate, momentum rule) and candidates (logistic, boosted trees), cross-validated on train with whole fights held out. One nested MLflow run each |
| 4. Challenger | The candidate with the best cross-validated Brier score, refit on all of train |
| 5. Gate | Challenger vs. baselines and the current **champion** on the test split. Passes if its Brier score is better than every one of them; a 95% interval (resampling whole fights) shows whether the difference is significant |
| 6. Registry | Registered as `vertex-<task>-ahead` in the MLflow model registry with its gate result. It becomes the **`champion`** alias only if it passed |

The two tasks (details in [step 0 log](../logs/2026-11-04-step0-first-training.md)):
`window` = who comes out ahead over the next 2 s from an engaged moment; `exchange` = who
comes out ahead in the next exchange, judged just before its first hit.

## Looking at results

```powershell
.\training\vt.ps1 ui               # MLflow UI at http://127.0.0.1:5000
```

- **Experiments → vertex-game-sense:** one parent run per task and pipeline run, with nested
  candidate runs. Compare runs by `cv_brier`, `test_challenger_brier`, `gate_vs_*`.
- **A run's Artifacts:** `gate.json` (every score and verdict), `calibration_test.png`,
  `feature_importance.json`, and the model itself.
- **Models:** `vertex-window-ahead` and `vertex-exchange-ahead`, every version tagged with its
  gate result, dataset and feature version; `@champion` marks the current one.
- **Inputs:** the dataset each run trained on.

```powershell
.\training\vt.ps1 timeline "$env:APPDATA\.minecraft\vertex-replays\<id>.json.gz"
```

A text preview of the replay viewer's planned win-chance strip: the champion's chance that the
recorder comes out ahead, every second of a fight.

## Calling the model from other applications

```powershell
.\training\vt.ps1 serve --task window      # REST API on http://127.0.0.1:5001
```

```powershell
$body = '{"dataframe_split": {"columns": ["dist", "..."], "data": [[2.1, "..."]]}}'
Invoke-RestMethod -Uri http://127.0.0.1:5001/invocations -Method Post -ContentType application/json -Body $body
```

Returns `[P(behind), P(ahead)]` per row. An `/invocations` example with every column is printed
by the run's model page in the UI ("input example"). From Python:
`mlflow.pyfunc.load_model("models:/vertex-window-ahead@champion").predict(df)`. This is how the
MCP server's planned `evaluate_moment` tool, or an agent, will call game sense.

## Self-play arena

Two bot players fight in arena A0 (a flat 24×24 box with glass walls) on a real headless 26.2
server, controlled from Python one decision at a time. Kit: full diamond armour with Protection
III and an unenchanted diamond sword. The server mod is in [arena/](../arena/).

```powershell
.\training\arena.ps1 start                 # builds and starts the server (about 45 s); stop | status
.\training\vt.ps1 arena bench              # scripted bots fight; outcomes and ticks per second
```

| Part | What it does |
|---|---|
| `arena/` (Java) | `BotPlayer`: a server player with no client that runs its own physics, with movement, sprint, crosshair and whiff rules copied from the vanilla client. `ArenaSession`: lockstep JSON-lines protocol on 127.0.0.1:25570; the server waits for Python every tick and otherwise runs flat out |
| `vertex_training/arena/client.py` | Socket client: `hello`, `reset`, `step`, `realtime`, `close` |
| `vertex_training/arena/env.py` | `SwordArenaEnv`, a PettingZoo parallel environment. Each policy sees only what a client could (the opponent's charge is estimated from swings). Actions: move, jump, sprint, sneak, attack, turn, look. Reward: zero-sum net damage per step plus ±1 for a kill. Default 2 ticks per decision and a 200 ms reaction delay |
| `vertex_training/arena/scripted.py` | Baselines: random, chase (sprint in, attack on full charge, W-tap, strafe), crit (jump crits) |

**Watching:** `.\training\vt.ps1 arena watch` (default `--matchup crit-vs-chase --episodes 10`)
plays fights at 20 ticks per second with a 3 s pause between them. In Minecraft 26.2, go to
Multiplayer → Direct Connection → `localhost:25566` (offline-mode server, any account works). Anyone
who joins is put in spectator mode above the south wall; each bot's health shows under its name,
and the result of each fight appears in chat. From your own code: `ArenaClient.realtime()`.

### Training a policy (self-play with a league)

```powershell
.\training\arena.ps1 start -Count 2               # two arenas (trainer ports 25570, 25580)
.\training\vt.ps1 arena train --servers 2 --decisions 2000000
.\training\vt.ps1 arena train --resume run-20261104-200144   # continue a run
.\training\vt.ps1 arena eval --policy latest --opponent crit  # 20 fights, sides swapped
.\training\vt.ps1 arena watch --policy latest --opponent chase   # join localhost:25566
.\training\vt.ps1 arena gate --resume run-20261104-210844        # re-run a finished run's gate
```

| Part | What it does |
|---|---|
| `arena/policy.py` | The network (two 256-unit layers each for actor and critic). Input: the 37 observation numbers plus its own last two actions (its view is 200 ms old). Checkpoint format |
| `arena/train.py` | PPO (in the style of CleanRL). Every decision the learner acts for each side it controls, in every arena at once; every 256 decisions per arena it updates on its own experience. Then the champion gate |
| `arena/kickstart.py` | Before PPO: the network copies the full-skill chase and crit bots' key presses (behaviour cloning, 60k decisions of their fights, critic trained on the same returns). During PPO a distillation term keeps it near this teacher, fading out over the first 30% of updates |
| `arena/curriculum.py` | **Autocurriculum.** Buckets = opponent style (chase, crit) × start distance (2–4 … 8–10 blocks), each with an opponent skill dial (0 stands still, 1 is the full bot). Skill follows the learner's score (aims for ~60% wins); buckets are sampled by learning progress (how fast their score is changing) |
| `arena/league.py` | Who it fights. Until every bucket reaches full skill: curriculum 85%, its current self 15%. After the handover: self 50% (both sides learn), frozen past snapshots 30% (picked more often if they still beat it), curriculum 20%. Elo for everyone, chase bot fixed at 1000 |
| `arena/play.py` | Policy vs. policy or scripted bot, for `eval`, `watch` and the gate |

Fights last 30 s. At the time limit, whoever is ahead on health wins by that margin
(`env.outcome_score`: full win at 10 HP ahead), and the terminal reward follows it, still
zero-sum: running away no longer earns an even result unless you're ahead.

Human-like limits: one click per decision (at most 10 clicks a second), 50° per tick turn cap,
200 ms reaction delay, 1° aim noise, whiff lockout. Scripted bots play under the same delay
(they remember their own turns and clicks, as a player does).

Where training is stored (`%LOCALAPPDATA%\vertex-training\selfplay\<run>\`):

| Path | Contents |
|---|---|
| `checkpoints/latest.pt` | Weights, optimizer state and counters, every 10 updates: `--resume` continues from here |
| `checkpoints/update-NNNNN.pt` | Kept every 100 updates |
| `league/gen-NNNNN.pt`, `league/league.json` | Frozen snapshots (one every 25 updates) and the Elo table |
| `config.json` | The run's settings |
| `kickstart.pt` | The behaviour-cloned starting point (also the distillation teacher) |
| `league/league.json` → `curriculum` | Each bucket's skill, recent scores and fight count; whether the handover happened |
| `selfplay/champion.pt` | The current champion |

MLflow experiment **vertex-selfplay** (`vt.ps1 ui`): curves for Elo, score against the curriculum
and against snapshots, `curriculum_mean_skill` and `skill_<style>_<distance>` (what the curriculum
is teaching, and when), `curriculum_handed_over`, `kickstart_coef`, entropy, hits per fight, crit
and sprint-hit share, PPO losses; `bc_acc_*` for how well the kickstart copied the bots.
**Champion gate** at the end of a run: at least 0.6 score over 20 fights each against the chase
bot, the crit bot and the current champion. If passed, it's registered as
**`vertex-sword-policy@champion`**. Load from Python with `policy.load(path)`, or
`mlflow.pytorch.load_model("models:/vertex-sword-policy@champion")`.

### Duel the bot yourself, and let it learn from you

```powershell
.\training\arena.ps1 start -Count 2           # if not running
# Minecraft 26.2 → Multiplayer → Direct Connection → localhost:25566, then:
.\training\vt.ps1 arena duel --fights 5       # you vs the champion; 3-2-1 countdown, 30 s fights
.\training\vt.ps1 arena clone                 # train the you-clone on all your duels
.\training\vt.ps1 arena train --resume run-20261104-210844 --decisions 500000   # bot practises vs the clone
.\training\vt.ps1 arena eval --policy latest --opponent you-clone               # how it does against "you"
```

In a duel, fighter 1 is you (bot B steps aside as a spectator). You get the same kit; the sword
stays in your hand; during the countdown you're held on your spot and nobody can be hurt. The
policy plays bot A with its usual limits (200 ms reaction, 10 clicks/s, turn cap).

**How it learns from you** (`arena/duel.py`): your fights are far too few to train the bot directly
(one fight ≈ 300 decisions; it trained on 400,000), so each duel teaches a **you-clone**: a
behaviour-cloned copy of you, starting from the champion's weights. The server records, per
decision, what you saw (the same 37 numbers a policy sees from your side, 200 ms late) and what you
did: your movement keys exactly (your client's input packets), your swings (from your attack
cooldown), your turns (from your rotation, rounded to the policy's turn steps). Your crits are
recognised by damage; sprint hits approximately. The clone then joins the league (25% of fights
after the handover), so the bot practises against "you" at 15× speed. Duel the new bot, re-clone,
repeat: each round the clone learns the situations the new bot creates (DAgger).

| Path | Contents |
|---|---|
| `selfplay/duels/duel-*.npz` | Your decisions from each duel session (no names) |
| `selfplay/duels/results.json` | Every duel fight: your score, hits, length, which policy |
| `selfplay/you-clone.pt` | The current you-clone (also logged to MLflow as a `you-clone-*` run) |

### Where the bot loses: `arena analyse`

Each duel also saves a server trace (`selfplay/duels/duel-*.trace.json.gz`: both fighters' exact
state every decision). `arena analyse [--duel latest|all|STAMP]` compares you and the bot side by
side: hits, damage per hit, crits, sprint hits, whiffs, charge when swinging, range at hits,
exchanges opened and won, combo lengths, aim error and time at each distance.

### Training settings (`arena train --set KEY=VALUE`)

| Setting | Default | What it does |
|---|---|---|
| `obs_layout` | 2 | 1: everything 200 ms late. 2: own body live, crosshair-on-opponent and reach 100 ms late, a live crit-ready flag, the opponent 200 ms late (how a player perceives). A resumed layout-1 run is upgraded so it plays the same at first |
| `curriculum_share` | 0.2 | After handover, the share of fights from the curriculum |
| `focus_style` | none | Curriculum styles picked 4× as often, e.g. `eager,punish` |
| `whiff_cost` | 0 | Reward off per swing at nothing (training only; 0.02 ≈ 0.4 HP). Not zero-sum |
| `human_kl` | 0 | Pull toward the you-clone (KL). Tried at 0.1 and 0.02: both hurt (see the log) |
| `entropy_floor` | 0 | Per control, penalise entropy below this share of its maximum, so no key dies out |
| `arch` | mlp | `lstm`: the policy with memory (below) |

Scripted styles: `chase`, `crit`, `punish` (holds spacing, punishes whiffs, combos, leads its aim),
`eager` (swings at the edge of reach, as people do). All play with the 200 ms delay and remember
their own turns and clicks.

### The policy with memory (`--set arch=lstm`)

```powershell
.\training\vt.ps1 arena train --servers 2 --decisions 500000 --set arch=lstm `
  --set teacher=<path to a memoryless checkpoint> --set 'seed_opponents={"v3": "<selfplay>/champion-v3.pt"}'
```

`RecurrentSwordPolicy`: the same inputs → an LSTM that carries a running memory of the fight → the
action, the value, and a prediction of the opponent's action at each decision (so the memory has to
track the opponent). `train_rnn.py`:

1. **Distillation:** the teacher plays `distill_decisions` (60,000) decisions of league fights; the
   LSTM is trained offline on whole fights to match the teacher's action probabilities, predict the
   opponent and estimate the value (`distilled.pt` in the run folder).
2. **Recurrent PPO:** each arena side's rollout is replayed whole from its starting memory. The KL
   to the teacher fades over `teacher_fade` (30%) of updates. The league starts with
   `seed_opponents` and the curriculum starts complete (`start_skill` 1.0). Seeded opponents get
   a fixed `seeded_share` (15%) of fights, so the learner's own snapshots can't crowd them out.
   Seed with a pinned file, not `champion`: `champion.pt` is replaced when a new policy passes the
   gate. `start_from=<distilled.pt>` skips distillation and starts from an earlier run's copy.

Logged: `opp_acc_early` / `opp_acc_late` (opponent prediction in the first 3 s vs after 10 s of a
fight: if it reads the opponent, late is better), `key_back_share`, `key_jump_share`,
`whiff_share`, `teacher_kl`, plus everything the memoryless trainer logs. Runs are `selfplay/rnn-*`.

## Other commands

| Command | What |
|---|---|
| `vt.ps1 dataset` | Build or show the dataset and its split counts |
| `vt.ps1 final` | Score the champion on the **final** split. Use rarely, e.g. once per phase: every look makes it less honest |

## Where things live

| Path | Contents |
|---|---|
| `training/vertex_training/` | The package (committed) |
| `%LOCALAPPDATA%\vertex-training\mlflow.db` | MLflow runs and model registry (SQLite) |
| `%LOCALAPPDATA%\vertex-training\mlartifacts\` | Models, plots, gate reports |
| `%LOCALAPPDATA%\vertex-training\datasets\` | Versioned Parquet datasets |

Override with `VERTEX_RECORDINGS`, `VERTEX_TRAINING_HOME` or `MLFLOW_TRACKING_URI` (e.g. a shared
MLflow server later). Bump `FEATURE_VERSION` in `config.py` whenever `features.py` changes what a
sample contains.

| Module | Role |
|---|---|
| `recordings.py` | Loads `.json.gz` recordings into per-fighter arrays |
| `features.py` | ~30 observable features per moment, judged from both sides; the targets |
| `dataset.py` | Versioned Parquet dataset, hash-locked splits |
| `models.py` | Baselines and candidates |
| `evaluate.py` | Vertex Bench layer A scores, calibration, bootstrap interval |
| `pipeline.py` | The end-to-end run, gate and registry |
| `timeline.py` | Win chance over a fight |
