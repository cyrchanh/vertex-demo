"""Strategists: the LLMs that read their bot's results and write its next version.

A backend turns (system prompt, briefing) into a Proposal. Two ship here:

  claude   the Anthropic API (anthropic SDK; credentials from ANTHROPIC_API_KEY or an `ant auth login`
           profile). Model and effort per strategist.
  noop     returns the current code unchanged, for dry-running the loop without an API.

Another provider is one more class with the same `propose` method; strategists then name it in their
config ("backend": ...). Each strategist keeps its own code private; what it reads from the others is
only the shared notebook of hypotheses and their measured verdicts.
"""

import json

SYSTEM = """You are a strategist in a research loop about Minecraft sword PvP (1.9+ combat, attack cooldown). You maintain one bot, written as a Python program, that fights other strategists' bots and fixed reference bots in a real server arena. Each round you get your bot's measured results, diagnose why it won or lost, state a falsifiable hypothesis, and write the next version. Your hypothesis is checked against the next round's numbers and the verdict is shared with every strategist, so claims must be measurable and honest; a refuted hypothesis is useful, a vague one is not.

## The game, as the arena implements it
- Two players, 20 HP each, armoured, flat arena: a full-charge hit deals about 1 HP (measured: median 0.98) and a crit about 1.7, so a kill takes ~20 hits and many fights end at the time limit. A fight lasts at most 600 ticks (30 s, 20 ticks/s). Killing wins (score 1). At the time limit, score = 0.5 + 0.5 * clamp(health lead / 10 HP, -1, 1).
- Your bot decides every 2 ticks (10 decisions/s); one decision = at most one click.
- Attack charge (me["charge"], 0..1) refills over ~12.5 ticks after any swing. Every swing resets it to 0, hit or miss. Damage grows steeply with charge; swings under 0.9 charge are weak.
- Reach: a click hits only if the crosshair is on the opponent within 3.0 blocks from your eyes to their hitbox (me["aim_on_target"], me["reach_distance"]).
- Whiff: a click with nothing under the crosshair locks your clicks out for 10 ticks (me["miss_cooldown"] counts down) and still resets charge. Whiffs are what skilled players punish.
- Critical hit (x1.5): charge > 0.9, falling (vy < 0, fall_distance > 0), not on ground, NOT sprinting. A jump peaks after ~6 ticks and lands after ~12.
- Order inside each tick: knockback lands, you turn, your click is handled, THEN your movement keys are read (sprint starts/stops), then physics. So the sprint state a click sees is the one from before this decision: stop sprinting at least one decision before a crit swing.
- Sprint hit: charge > 0.9 while sprinting gives extra knockback; landing a hit ends your sprint, so you must re-sprint (release forward briefly, "W-tap", or S-tap) for the next sprint hit.
- After taking damage a player can't be damaged again for ~10 ticks (op["hurt_time"] > 0).
- Knockback pushes the victim away; a combo is landing hits in a row before the opponent hits back.
- Sprinting needs forward held; moving back or sneaking is slower. Releasing the sprint key does NOT end a sprint (as in vanilla): a sprint ends only when forward is released, a hit lands, or you run into a wall. And holding forward with sprint held starts a sprint again at once, even in the air: to be unsprinted when a jump swing lands, release sprint and keep it released (forward alone is fine once the sprint has ended). me["sprinting"] shows the live state. The breakdown's "air swings" line tells you why your jump swings did or didn't crit. Turning is capped at 40 deg/tick for yaw and 10 for pitch.

## What your bot sees: act(view) is called each decision with
view = {
  "tick": int,               # now
  "seen_tick": int,          # when the op/events info is from (200 ms ago: human reaction time)
  "fight_ticks": 600,
  "me": {x, y, z, vx, vy, vz, yaw, pitch, on_ground, sprinting, sneaking, health, absorption, hurt_time,
         invulnerable_time, charge, fall_distance, miss_cooldown, food, aim_on_target, reach_distance},   # live; aim 100 ms late
  "op": {x, y, z, vx, vy, vz, yaw, pitch, on_ground, sprinting, sneaking, health, absorption, hurt_time},  # 200 ms late
  "events": [ {"type": "hit", "by_me": bool, "damage", "crit", "sprint_hit", "tick"},
              {"type": "swing", "by_me": bool, "hit": bool, "tick", "charge" (own swings only)} ],      # 200 ms late, each event shown once
  "rel": {"dist", "dy", "dx", "dz", "yaw_off", "pitch_off"}   # op as seen from me: horizontal distance; degrees to turn to face their eyes (aim ~8 deg lower for the body)
}
You never see the opponent's keys, charge or lockout: infer them (e.g. from their swings you saw). Yaw: degrees, the game's convention; yaw_off is already wrapped to [-180, 180].
Because op is 200 ms old, predicting where they are now (op velocity * ~4 ticks) usually helps.

## Human limits (applied to your bot after act(), in every fight; the anchors don't have them)
Your bot plays with a person's hands: about 1 in 3 clicks lands one decision (100 ms) late, every turn gets ~1 deg of noise, and the crosshair drifts slowly by ~1.5 deg. So exact edge-of-reach timing is unreliable (a click timed for 3.0 blocks may land at 2.4-2.9, or whiff if they are leaving), and a hitbox at 3 blocks (about +-6 deg) is not a sure hit. Win with decisions (spacing, baits, timing windows, reading the opponent), with margin for these errors, not with precision.

## What act returns
A dict: forward, back, left, right, jump, sprint, sneak, attack (bools; missing = False), dyaw, dpitch (degrees per tick for the next 2 ticks; clamped to 40 and 10). "attack": True is one click this decision.

## The program
- One Python module defining `class Fighter` with `__init__(self, seed)` and `act(self, view)`. Keep state on self (it lives for one fight).
- Only `import math`, `random`, `collections`. No file, network, eval/exec, getattr, dunder attributes, global statements.
- Must be fast (well under 5 ms per call) and must never raise: an exception makes the bot idle for that decision and is reported to you.
- Write the whole program every time, complete and runnable.

## Opponents
- Other strategists' bots (you never see their code; you see your results against them).
- Your own previous version (to measure whether your change helped).
- Fixed anchors, the same every round: chase (sprints in, swings at full charge, W-taps), crit (jump crits), punish (spaces at 3.2-3.9 blocks, rushes in during your whiff lockout, combos), eager (swings early at the edge of reach, whiffs a lot), champion (a reinforcement-learning policy with memory, the strongest reference). Your mean score vs anchors is the fixed scale every bot is ranked on.
- The owner ("owner"): a skilled human who duels the bots in real time between rounds. Beating bots is a means; the real target is playing well against people like the owner. Fights vs the owner last up to 120 s (view["fight_ticks"] tells you). The briefing shows your results vs the owner and a shared, measured description of how the owner plays: use it to explain *why* the owner wins (which mechanic, which habit), not only to counter it. Predictions may name "owner"; they are checked when the owner next duels your newest version (possibly not every round).

## Each round, reply with
- diagnosis: why the bot won or lost, citing the numbers you were given (whiff share, exchanges opened/won, range at hits, combos, per-opponent scores, errors).
- hypothesis: one causal claim with a measurable prediction for next round, e.g. "whiffs vs punish fall from 45% to under 25%, and score vs punish rises above 0.5". first_hit_share = share of fights where you land the first hit; first_hit_crit_share = share where that first hit is yours and a crit. rush_share = when the opponent is 3-6 blocks away and not coming in, the share of that time you close in on them anyway (lower = more patient). swing_gap = median ticks between your swings (lower = faster; charge refills in ~11-12 ticks); swing_gap_after_hit = the same for swings within 12 ticks of being hit. crit_chain_share = share of your crits that follow another crit of yours within 16 ticks with no hit taken in between (crit chains). net_damage_per_fight = HP you dealt minus HP you took, per fight (what decides fights; time-outs are scored on it). jump_punished_share = share of your jumps where you got hit before landing a crit or touching down (lower = better-timed jumps).
- change: what you changed in the code and how it tests the hypothesis. Prefer one main idea per round so the verdict means something.
- code: the full program.
"""

PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "diagnosis": {"type": "string"},
        "hypothesis": {"type": "string"},
        "prediction": {
            "type": "object",
            "description": "the measurable part of the hypothesis, checked automatically next round",
            "properties": {
                "opponent": {"type": "string", "description": "an opponent name, or 'anchors' for the anchor mean"},
                "metric": {"type": "string", "enum": ["score", "whiff_share", "exchanges_won_share", "crit_share",
                                                      "sprint_hit_share", "hits_per_fight", "first_hit_share",
                                                      "first_hit_crit_share", "rush_share", "swing_gap",
                                                      "swing_gap_after_hit", "crit_chain_share",
                                                      "net_damage_per_fight", "jump_punished_share"]},
                "direction": {"type": "string", "enum": ["up", "down"]},
                "threshold": {"type": "number", "description": "the value it should pass"},
            },
            "required": ["opponent", "metric", "direction", "threshold"],
            "additionalProperties": False,
        },
        "change": {"type": "string"},
        "code": {"type": "string"},
    },
    "required": ["diagnosis", "hypothesis", "prediction", "change", "code"],
    "additionalProperties": False,
}


class ClaudeBackend:
    def __init__(self, model="claude-opus-5-5", effort="high", max_tokens=32000):
        import anthropic
        self.anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model, self.effort, self.max_tokens = model, effort, max_tokens

    def propose(self, system, briefing):
        """→ (proposal dict, usage dict)."""
        with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": PROPOSAL_SCHEMA}},
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": briefing}],
        ) as stream:
            response = stream.get_final_message()
        if response.stop_reason == "refusal":
            raise RuntimeError(f"strategist declined: {getattr(response.stop_details, 'category', None)}")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("strategist ran out of output tokens; raise max_tokens or lower effort")
        text = next(b.text for b in response.content if b.type == "text")
        u = response.usage
        usage = {"model": response.model, "input": u.input_tokens, "output": u.output_tokens,
                 "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
                 "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0}
        return json.loads(text), usage


class NoopBackend:
    """Keeps the code as it is: exercises the loop (server, tournament, analysis, storage) for free."""

    def __init__(self, **_):
        pass

    def propose(self, system, briefing):
        code = briefing.split("```python\n", 1)[1].split("\n```", 1)[0]
        return {"diagnosis": "noop backend", "hypothesis": "no change", "change": "none", "code": code,
                "prediction": {"opponent": "anchors", "metric": "score", "direction": "up", "threshold": 0.0}}, {}


BACKENDS = {"claude": ClaudeBackend, "noop": NoopBackend}


def backend(name, **kwargs):
    return BACKENDS[name](**kwargs)
