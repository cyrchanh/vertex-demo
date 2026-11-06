# Vertex — Pivot Log: From Client-Side Telemetry to AI-Explained Fight Replays

This log records *why* the project changed direction, what was decided, what was considered and
rejected, and the plan. Read it before making architectural changes, so the reasoning isn't lost
or re-argued. Later revisions are added on top; earlier sections are kept as written so the
reasoning trail stays intact.

> Note: this file went missing from the project folder on 2026-10-28 (never committed to git) and
> was restored from the working session's copy, with Revision 3 added. Revisions 2 and 3 are
> complete; the original morning sections are condensed to their decisions and reasoning.

---

## Revision 6 (2026-11-04, later) — review of the training plan

**Status:** plan only, nothing built. Details: [logs/2026-11-04-ai-training-plan.md](logs/2026-11-04-ai-training-plan.md#review-additions-to-the-plan);
first-draft training maps: [logs/2026-11-04-training-maps.md](logs/2026-11-04-training-maps.md).

1. **RLHF only where opinions are the right signal.** *Decision:* DPO on player preferences for
   the explainer's clarity, an automatic fact check against the value model and events, and "did
   the advice help" as the strongest signal. The value model keeps learning from outcomes, not
   opinions.
2. **Start without RL.** *Decision:* new step 0, a supervised baseline on existing recordings
   that predicts the next exchange's net damage (point 6). Every later model must beat it.
3. **Correlation is not cause.** Human data alone can credit habits that only come with skill.
   *Decision:* **counterfactual rollouts**: restore a recorded moment in the bot arena and play it
   out with different actions. This becomes a required arena feature.
4. **The value model only sees what a client sees.** Full server state is allowed only for the
   training critic. Ping, server rules, game version and skill estimates become model inputs, and
   the arena randomises ping and rules (sim-to-real).
5. **Fair play.** *Decision:* the AI does **post-fight analysis only**, never live advice during
   combat, which would make Vertex an assist client.
6. **The sword objective is net damage (owner's framing).** Sword is a zero-sum game of winning
   exchanges with whatever the game and kit allow. *Decision:* the value model predicts expected
   net damage (plus fight-win chance, since a heart matters more near death); an exchange is won
   when its net damage is positive; self-play rewards win/loss plus per-tick net damage, which is
   exactly potential-based shaping with Φ = health difference. Creativity comes from a general
   action space (all items), not item rules. Other modes keep their own learned goals.
7. **Training risks have planned fixes:** a league against self-play cycles, a second untouched
   test set against gate overfitting, every mode checked against forgetting, ensembles for
   uncertainty, and orchestrator budget caps.

---

## Revision 5 (2026-11-04) — the AI training plan

**Status:** plan agreed, nothing built yet. Full plan: [logs/2026-11-04-ai-training-plan.md](logs/2026-11-04-ai-training-plan.md).

1. **Goal: understanding, not playing.** The target is game sense for analysing human fights.
   *Decision:* the core is a **value model** (win chance from any moment), like a chess engine's
   evaluation. Coaching comes from where the win chance rose or fell, explained by an LLM that's
   grounded in those numbers.
2. **Data: all three sources, each for what it's good at.** Self-play learns what wins, human
   data (recordings, then permitted video) learns what humans do, and **piKL** keeps self-play
   strategies human-like. Vertex recordings are the bridge (labels for video) and the test.
3. **Scaling to other PvP modes (owner's requirement): no hand-written rules per mode.**
   *Decision:* the **hand-written sword simulator from the first brainstorm is rejected.** Use the
   real game as the environment (headless local servers, Fabric bots, `/tick sprint`) plus a
   learned world model for speed, a general recorder, one model across modes, and win conditions
   learned from game events.
4. **"An AI that knows which training source to seek and self-improves":** an LLM orchestrator
   agent finds weak spots, chooses a source (self-play, video, player recordings, mechanics docs)
   and launches training. A new model is kept only if it beats the old one on a **held-out set of
   real human fights scored on prediction**, which the loop can't train on. New data sources need
   the owner's sign-off.
5. **Order:** prove the pipeline on sword first (exact rules and recordings exist), then add a
   second mode *only through the orchestrator* as the test of point 3. The rule-based explainer
   stays as the baseline to beat.

---

## Revision 4 (2026-10-29) — after the first real-player test

**Status:** replay changes implemented, see [logs/2026-10-29-replay-world-library-side-camera.md](logs/2026-10-29-replay-world-library-side-camera.md).

The project owner tested v2 against a real player. The findings and what was decided:

1. **The coaching is too simple to read a real fight.** *Decision: don't patch it with more
   rules.* Better reasoning is the job of the AI training step (Revision 3). The rule-based
   explainer stays as the offline baseline and as the yardstick a trained model must beat.
2. **The replay wasn't a real replay.** It placed two ghosts into whatever world you were in, so
   it broke as soon as a server warped you to another sub-server or you logged off. *Decision:
   work like Replay Mod.* The recording now also captures the blocks around both fighters, and
   watching rebuilds that arena in a private replay world. A **library screen** (title screen,
   pause menu, J key, `/vertex library`) lists, plays and deletes recordings while offline.
   "Watch in place" was removed rather than kept as an option, because it was the broken
   behaviour.
3. **The camera should be side-on**, between you and the opponent, not behind either fighter.
   *Decision:* the side view is now the default camera; first/third person remain as options.
4. **Keys must be configurable**, especially the camera key. *Decision:* all replay keys are
   normal Minecraft key bindings (Controls → Key Binds → Vertex Replay).
5. **Author:** the mod is published by **cyrchanh** (https://github.com/cyrchanh).
6. **Long-term:** once a model has been trained well on "how to play MC PvP", **embed it in the
   mod itself**. Until then, the MCP server stays the bridge to external AI. The `Explainer`
   interface is the seam where an embedded model plugs in, so recording, storage and the viewer
   won't change. Open questions for that step: model size vs. running on players' machines, and
   whether inference runs locally or as an optional download.

---

## Revision 3 (2026-10-28, evening) — the AI side: how agents, video and MCP fit

**Status:** plan agreed; step 1 (MCP replay server) built, see [logs/](logs/).

### The correction this plan rests on

"An agent watches thousands of YouTube videos and learns PvP itself" is not how agents work. An
agent runs on a model whose knowledge is fixed; watching a clip lets it *write notes*, not
*learn*. Getting better from video happens in only two ways:

1. **Training:** turn video into examples, then train or fine-tune a model on them (the VPT
   route). It needs recordings with real key presses first, which is what Vertex replays are.
2. **Retrieval:** the model stays fixed, but at analysis time it looks things up: the guides,
   notes agents wrote about videos, and similar moments from past fights. Cheap, and it works now.

So **agents watching video = a labelling step** (producing notes and examples). The learning
happens through training or retrieval. Footage needs the creator's permission or must be your own.

### What MCP is, and where it fits

**MCP (Model Context Protocol)** is an open standard (created by Anthropic) for connecting AI models
to tools and data. An *MCP server* exposes "tools" (e.g. `list_replays`, `get_exchange`); any
MCP-capable AI app (Claude Code, Claude Desktop, ...) can call them while it reasons. MCP is
plumbing for **access**, not a training technology.

| Place | MCP useful? | Why |
|---|---|---|
| An AI coach that reasons over your recorded fights | **Yes, step 1** | A Vertex MCP server over the replay folder lets an AI list fights, pull exchanges and frames, search moments across fights, and compare outcomes, grounded in real data. Reuses `core` directly. |
| Inside the mod for players | No | Players would need a local AI app + API key; use the `Explainer` interface instead |
| Model training | No | Training is a data pipeline; MCP doesn't train anything |
| Agents labelling video / data | Maybe later | Agents doing labelling could use MCP tools to read replays and write labels |

### Order of work

| Step | What | Uses |
|---|---|---|
| 1 | **MCP replay server**: an AI coach over your own recorded fights, today, no training | `core` (replay format, detector, explainer) |
| 2 | **AI `Explainer`**: an AI writes the in-game coaching, through the existing interface | Rules' output + guides as grounding |
| 3 | **Evaluation set**: your corrections and expert annotations measure whether AI coaching is right | Questions/corrections from steps 1-2 |
| 4 | **Video labelling pipeline**: agents annotate permitted footage into notes/examples (retrieval material) | MCP tools, vision-capable model |
| 5 | **Training**: VPT-style inverse dynamics on our recordings → extract traces from permitted video → outcome model ("what wins in similar situations") | Replays (own inputs exact) + optional server lab recordings |

---

## Revision 2 (2026-10-28, later the same day) — client-only, packet-exact. **Implemented as v2.**

**Status:** built. v1 was removed (backed up in `archive/`), and the codebase is now v2 as
described in [README.md](README.md). This revision **supersedes D1, D5 and D6** below; the rest
still applies.

### What changed and why

1. **Server-first was dropped as the product (D1 superseded).** The concern: a server recorder
   only learns from fights on servers running it, which needs a large player base we don't have.
2. **The key finding that made client-only viable:** the server already sends every nearby client
   exact combat facts, verified against the real 26.2 jars:
   - `ClientboundDamageEventPacket`: victim **and attacker** entity ids (exact who-hit-whom);
   - `ClientboundAnimatePacket` `CRITICAL_HIT`: the server confirmed a crit;
   - attack sounds broadcast from the attacker's position with no excluded player
     (`PLAYER_ATTACK_CRIT` / `_KNOCKBACK` / `_STRONG` / `_WEAK` / `_SWEEP` / `_NODAMAGE`): the
     server's own crit / sprint-hit / strong / weak verdict, for **both** players;
   - `ClientboundSetEntityMotionPacket`: the exact knockback velocity applied.

   v1 "guessed" only because it never read these. Reading them needs a small read-only mixin on
   `ClientPacketListener`, which breaks v1's no-mixin rule in a narrow, contained way.
3. **What's still estimated client-side:** only the opponent's raw key presses and attack charge.
   The detector never guesses rules that need those (jump reset, backing off, S-tap, no response)
   for the opponent.
4. **Scale through distribution:** a client-only mod on Modrinth works on any server, and every
   user records **their own** inputs exactly. With opt-in sharing later, that's a labelled dataset
   that grows with the user base, which is the cold-start answer.
5. **Pixels are not the input.** "The AI interprets the camera view" was considered and rejected:
   the rendered image is drawn *from* the data the client receives, so recording that data is
   strictly better than reconstructing it from video.

### Decisions taken in this revision

| # | Decision | Notes |
|---|---|---|
| R1 | v1 removed entirely (live HUD, V-key dashboard, Match Mode reports) | Backed up: `archive/vertex-v1-source.zip`, `archive/vertex-v1-0.1.0.jar`, `archive/README-v1.md` |
| R2 | Full 3D replay viewer now | Client-only ghost entities re-enact both fighters at the recorded spot; camera rides either one |
| R3 | Pluggable explainer, offline first | `Explainer` interface; `RuleBasedExplainer` encodes the Sword guides; an AI model can replace it later |
| R4 | Recording: on in singleplayer/LAN host, opt-in per connection on servers | Same per-connection opt-in philosophy as v1's Match Mode |
| R5 | One trace format for any recorder | `DuelTrace.source` is `"client"` today; a future server "lab" recorder writes `"server"` in the same format so the two are directly comparable |
| R6 | Build/test setup | Client-only mod, `runClientGameTest` (real client) + JUnit for the Minecraft-free core |

### Guide authorship (risk resolved)

The three Sword guides are written by the project owner, so using their content in the
rule-based explainer, as retrieval material, or as training data is not a rights issue.

### Long-term direction: learning from video, recorded honestly

The project owner's stated goal: at scale, the analysis should come from an AI trained on thousands
of PvP videos, not hand-written guides. The path agreed:

1. **Now:** rules + the guides, no training (works on day one).
2. **Collect:** client recordings, where your own inputs are exact, plus optional server "lab"
   recordings between consenting players for full two-sided ground truth.
3. **Video at scale, VPT-style:** train an inverse-dynamics model on our own recordings paired
   with rendered replays, then use it to extract traces from videos we have permission to use.
   The recorder comes first either way, because video training needs real recorded inputs to
   learn from.
4. **Outcome model:** learn "what wins in similar situations" from the corpus; an AI explains it.

---

## Original pivot (2026-10-28, morning) — sections 1-12

## 1. Where v1 stood

v1 was a **client-only** Fabric mod for Minecraft 26.2 (now archived in `archive/README-v1.md`). It:

- sampled the local player's state every tick, plus the crosshair-targeted entity's public state;
- classified landed hits as crit / sprint hit / plain, with range, opponent momentum and crouch state;
- showed a live HUD and audio cues in Practice Mode (singleplayer/LAN/allowlisted servers);
- produced post-match diagnostics: a numeric dashboard (`V`), a plain-English Match Mode report
  (`/vertex report`), and `.jsonl` log files;
- had opt-in Match Mode recording (`/vertex record`) and client-side commands.

It built and was tested in-game by the project owner: the HUD worked in singleplayer, and the log
files were written.

## 2. Why pivot

### 2.1 Client-only data had an interpretability ceiling (as v1 was built)

| v1 limitation | Root cause |
|---|---|
| Damage was attributed to "whoever you last targeted within 6 blocks" (the 1v1 assumption) | v1 had no hook telling it who actually hit you |
| Crit / sprint hit was a proxy (airborne + descending + not sprinting) | v1 couldn't read the server's actual crit decision |
| The opponent's *inputs* were invisible; only their resulting velocity/flags were seen | Inputs only exist on the opponent's own client and the server |
| Intent (bait vs. miss vs. hesitation) is unmeasurable | Not observable state from one side |
| Reports were statistics, so average players struggled to interpret them | Numbers without the "why" and "what instead" |

(Revision 2 later showed the first two rows were fixable client-side by reading server packets.)

### 2.2 An AI *opponent* isn't the goal

Training a strong AI bot to fight against is not useful: players want to fight real humans, not
"clankers." The value is in an AI that **explains** fights between humans.

## 3. The vision

1. Record a duel with both players' exact state and the server's actual combat decisions.
2. Let players watch a **replay of the duel from player 1's or player 2's perspective**.
3. At **each interaction or event**, show an AI annotation covering **what is happening**, **why it
   happened that way**, **what the player should have done instead**, and **why**.

## 4. Decisions and rationale (original)

- **D1 — Use server-side authoritative data.** *(Superseded by Revision 2: client-only, reading
  the server's packets.)*
- **D2 — Do NOT train on YouTube video as the foundation.** No labels, 50 ms ticks vs. frame
  sampling, one perspective, no inputs, mixed 1.8/1.9+ rulesets, YouTube ToS, unverifiable. The
  precedent studied was OpenAI's VPT (2022): contractors' keypresses → inverse dynamics model →
  pseudo-labelled ~70,000 hours of YouTube → imitation. Lessons: even VPT needed a ground-truth
  input dataset first, and it learned to *imitate*, not to *explain*.
- **D3 — How "the AI developing its own reasoning" works.** Imitation learns *what* pros do; RL
  learns *what wins* (a policy/value function, the "clanker"); explanations come from human-written
  reasoning. Combine them: a learned model finds what works, an LLM explains it grounded in that
  evidence and the guides.
- **D4 — LLM is the explainer, not the detector.** A deterministic detector labels techniques; the
  explainer writes "what → why → instead → why"; a critic pass can check claims against the data.
- **D5 — Fabric first, portable to Paper.** *(Superseded: v2 is client-only. The portability
  principle survives as "`core` has no Minecraft imports".)*
- **D6 — No paid server needed to start.** *(Superseded: client-only needs no server at all.)*

## 5. Training and evaluation loop

Start with no training (rules + guides), then: an expert evaluation set; in-app feedback; iterate
prompts/rules before training anything; fine-tune small models only where rules fail; long-term an
outcome model; optionally VPT-style video expansion on permitted footage.

## 6. Risks and tradeoffs

| Risk | Mitigation |
|---|---|
| LLM cost per annotation | Post-match only, batch and cache, cheaper model for bulk work |
| LLM hallucination | Grounded inputs (structured events + guides), critic pass, expert eval set |
| Recording other players | Client-only records only what your client receives; files stay on your computer |
| Limited expert time for labelling | Start with a few hundred annotations; in-app feedback grows the set |

## 7. Open questions

- Which LLM provider/model for the AI explainer, and what per-annotation cost budget?
- Opt-in sharing design for building a dataset from users' recordings.
- Who are the first expert annotators, and how many annotations to start with?
