package com.vertexpvp.vertex.core.detect;

import com.vertexpvp.vertex.core.trace.CombatEvent;
import com.vertexpvp.vertex.core.trace.ControlInput;
import com.vertexpvp.vertex.core.trace.DuelTrace;
import com.vertexpvp.vertex.core.trace.ParticipantFrame;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/**
 * Deterministic rules that split a fight into exchanges and label what happened in each, using
 * the Sword guides' vocabulary. No learning, no randomness: the same trace always gives the same
 * result, which is what makes it testable.
 *
 * <p>Rules that need the opponent's held keys (jump reset, backing off, S-tap, no response) only
 * fire for a participant whose inputs were recorded exactly. They are never guessed for the
 * opponent from their motion, because knockback motion makes those guesses unreliable.
 */
public final class InteractionDetector {

	public static final int EXCHANGE_GAP_TICKS = 20;
	static final int CONTEXT_BEFORE = 10;
	static final int CONTEXT_AFTER = 10;
	static final int RESPONSE_WINDOW = 6;
	static final int HIT_BACK_WINDOW = 12;
	static final int WHIFF_PUNISH_WINDOW = 15;
	static final int JUMP_IN_LOOKBACK = 15;
	static final double THREE_BLOCK_MIN = 2.6;
	static final double CLOSE_RANGE_MAX = 1.6;
	static final double WHIFF_MAX_DISTANCE = 5.0;
	static final double RISING_MIN_VY = 0.05;
	static final double JUMP_START_MIN_VY = 0.2;

	public List<Interaction> detect(DuelTrace trace) {
		List<CombatEvent> events = trace.events();
		List<Integer> hits = new ArrayList<>();
		for (int i = 0; i < events.size(); i++) {
			CombatEvent e = events.get(i);
			if (e.type() == CombatEvent.Type.HIT && e.attacker() >= 0 && e.victim() >= 0) {
				hits.add(i);
			}
		}
		hits.sort(Comparator.comparingInt(i -> events.get(i).tick()));
		if (hits.isEmpty() || trace.frames().isEmpty()) {
			return List.of();
		}

		List<List<Integer>> groups = new ArrayList<>();
		List<Integer> current = new ArrayList<>();
		int lastTick = Integer.MIN_VALUE;
		for (int idx : hits) {
			int tick = events.get(idx).tick();
			if (!current.isEmpty() && tick - lastTick > EXCHANGE_GAP_TICKS) {
				groups.add(current);
				current = new ArrayList<>();
			}
			current.add(idx);
			lastTick = tick;
		}
		groups.add(current);

		List<Observation> whiffs = findWhiffs(trace);
		List<Interaction> result = new ArrayList<>();
		int firstHitEvent = hits.get(0);

		for (int g = 0; g < groups.size(); g++) {
			List<Integer> group = groups.get(g);
			CombatEvent first = events.get(group.get(0));
			CombatEvent last = events.get(group.get(group.size() - 1));
			int start = Math.max(0, first.tick() - CONTEXT_BEFORE);
			int end = Math.min(trace.lastTick(), last.tick() + CONTEXT_AFTER);

			List<Observation> obs = new ArrayList<>();
			int[] hitsBy = new int[trace.participants().size()];
			CombatEvent[] previousBy = new CombatEvent[trace.participants().size()];

			for (int idx : group) {
				CombatEvent e = events.get(idx);
				hitsBy[e.attacker()]++;
				if (idx == firstHitEvent) {
					obs.add(new Observation(e.tick(), Technique.FIRST_HIT, e.attacker(), false, ""));
				}
				obs.addAll(hitObservations(trace, e, previousBy[e.attacker()], events));
				previousBy[e.attacker()] = e;
			}

			addCombos(group, events, obs);
			if (hitsBy.length == 2 && hitsBy[0] > 0 && hitsBy[1] > 0) {
				obs.add(new Observation(first.tick(), Technique.TRADE, -1, false,
						hitsBy[0] + " vs " + hitsBy[1] + " hits"));
			}
			addWhiffs(trace, whiffs, group, events, start, end, obs);

			obs.sort(Comparator.comparingInt(Observation::tick));
			result.add(new Interaction(g, start, end, first.tick(), hitsBy, List.copyOf(group), List.copyOf(obs)));
		}
		return result;
	}

	private List<Observation> hitObservations(DuelTrace trace, CombatEvent e, CombatEvent previousSameAttacker,
			List<CombatEvent> events) {
		List<Observation> obs = new ArrayList<>();
		int a = e.attacker();
		int v = e.victim();
		int t = e.tick();
		ParticipantFrame attackerBefore = trace.state(t - 1, a);
		ParticipantFrame victimBefore = trace.state(t - 1, v);
		boolean attackerIsPlayer = trace.participants().get(a).player();

		switch (e.kind()) {
			case CRIT -> obs.add(new Observation(t, Technique.CRIT_LANDED, a, false, ""));
			case SPRINT_HIT -> {
				Technique tech = attackerBefore.descending() ? Technique.FALLING_SPRINT_HIT : Technique.SPRINT_HIT_LANDED;
				obs.add(new Observation(t, tech, a, false, ""));
				if (previousSameAttacker != null) {
					obs.add(new Observation(t, Technique.SPRINT_RESET, a, false, ""));
				}
			}
			case WEAK -> obs.add(new Observation(t, Technique.WEAK_HIT, a, false, ""));
			case STRONG -> {
				if (attackerIsPlayer && previousSameAttacker != null) {
					obs.add(new Observation(t, Technique.NO_SPRINT_RESET, a, false,
							"follow-up " + Math.max(1, t - previousSameAttacker.tick()) + " ticks after your last hit"));
				}
			}
			default -> {
			}
		}

		if (e.predictedKind() != null && e.serverConfirmed() && e.predictedKind() != e.kind()
				&& (isSpecial(e.predictedKind()) || isSpecial(e.kind()))) {
			obs.add(new Observation(t, Technique.SERVER_DISAGREED, a, false,
					"expected " + label(e.predictedKind()) + ", server counted " + label(e.kind())));
		}

		if (attackerIsPlayer && e.kind() != CombatEvent.HitKind.MOB) {
			if (e.range() >= THREE_BLOCK_MIN) {
				obs.add(new Observation(t, Technique.THREE_BLOCK_HIT, a, false, String.format("range %.1f blocks", e.range())));
			} else if (e.range() <= CLOSE_RANGE_MAX) {
				obs.add(new Observation(t, Technique.CLOSE_RANGE_HIT, a, false, String.format("range %.1f blocks", e.range())));
			}
		}

		if (isSTap(trace, a, t)) {
			obs.add(new Observation(t, Technique.S_TAP, a, false, ""));
		}

		boolean victimInputsKnown = victimBefore.input() != null;
		if (victimBefore.ascending() && victimBefore.vy() > RISING_MIN_VY) {
			obs.add(new Observation(t, Technique.HIT_WHILE_RISING, v, !victimInputsKnown, ""));
		}
		if (isFailedJumpIn(trace, v, t, events)) {
			obs.add(new Observation(t, Technique.FAILED_JUMP_IN, v, !victimInputsKnown, ""));
		}
		if (victimBefore.sneaking()) {
			obs.add(new Observation(t, Technique.CROUCHING_WHEN_HIT, v, false, ""));
		}

		if (victimInputsKnown) {
			boolean jumpReset = isJumpReset(trace, v, t);
			boolean backedOff = heldBack(trace, v, t + 1, t + RESPONSE_WINDOW, 2);
			boolean hitBack = landsHitBetween(events, v, a, t + 1, t + HIT_BACK_WINDOW);
			if (jumpReset) {
				obs.add(new Observation(t, Technique.JUMP_RESET, v, false, ""));
			}
			if (backedOff) {
				obs.add(new Observation(t, Technique.BACKED_OFF_AFTER_HIT, v, false, ""));
			}
			if (e.kind() == CombatEvent.HitKind.SPRINT_HIT && !jumpReset && !backedOff && !hitBack) {
				obs.add(new Observation(t, Technique.NO_RESPONSE, v, false, ""));
			}
		}
		return obs;
	}

	private static boolean isSpecial(CombatEvent.HitKind kind) {
		return kind == CombatEvent.HitKind.CRIT || kind == CombatEvent.HitKind.SPRINT_HIT;
	}

	static String label(CombatEvent.HitKind kind) {
		return switch (kind) {
			case CRIT -> "a crit";
			case SPRINT_HIT -> "a sprint hit";
			case STRONG -> "a normal hit";
			case WEAK -> "a weak hit";
			case MOB -> "a mob hit";
			case UNKNOWN -> "an unclassified hit";
		};
	}

	/** Back pressed for 1-4 ticks within the 8 ticks before the hit, and released by the hit. */
	private static boolean isSTap(DuelTrace trace, int p, int t) {
		ControlInput atHit = trace.state(t - 1, p).input();
		if (atHit == null || atHit.back()) {
			return false;
		}
		int held = 0;
		for (int u = t - 8; u <= t - 1; u++) {
			ControlInput in = trace.state(u, p).input();
			if (in != null && in.back()) {
				held++;
			}
		}
		return held >= 1 && held <= 4;
	}

	/** Grounded just before the hit and jump held within one tick before to two ticks after it. */
	private static boolean isJumpReset(DuelTrace trace, int v, int t) {
		if (!trace.state(t - 1, v).onGround() && !trace.state(t - 2, v).onGround()) {
			return false;
		}
		for (int u = t - 1; u <= t + 2; u++) {
			ControlInput in = trace.state(u, v).input();
			if (in != null && in.jump()) {
				return true;
			}
		}
		return false;
	}

	private static boolean heldBack(DuelTrace trace, int p, int from, int to, int minTicks) {
		int held = 0;
		for (int u = from; u <= Math.min(to, trace.lastTick()); u++) {
			ControlInput in = trace.state(u, p).input();
			if (in != null && in.back()) {
				held++;
			}
		}
		return held >= minTicks;
	}

	private static boolean landsHitBetween(List<CombatEvent> events, int attacker, int victim, int from, int to) {
		for (CombatEvent e : events) {
			if (e.type() == CombatEvent.Type.HIT && e.attacker() == attacker && e.victim() == victim
					&& e.tick() >= from && e.tick() <= to) {
				return true;
			}
		}
		return false;
	}

	/** Left the ground with real upward speed within the lookback, still airborne, and landed nothing since. */
	private static boolean isFailedJumpIn(DuelTrace trace, int v, int t, List<CombatEvent> events) {
		if (trace.state(t - 1, v).onGround()) {
			return false;
		}
		for (int j = t - 1; j >= Math.max(1, t - JUMP_IN_LOOKBACK); j--) {
			ParticipantFrame before = trace.state(j - 1, v);
			ParticipantFrame at = trace.state(j, v);
			if (before.onGround() && !at.onGround() && at.vy() > JUMP_START_MIN_VY) {
				return !landsHitBetween(events, v, trace.opponentOf(v), j, t - 1);
			}
			if (at.onGround()) {
				return false;
			}
		}
		return false;
	}

	private static void addCombos(List<Integer> group, List<CombatEvent> events, List<Observation> obs) {
		int streakAttacker = -1;
		int streak = 0;
		int streakStart = 0;
		for (int idx : group) {
			CombatEvent e = events.get(idx);
			if (e.attacker() == streakAttacker) {
				streak++;
			} else {
				if (streak >= 3) {
					obs.add(new Observation(streakStart, Technique.COMBO, streakAttacker, false, streak + " hits in a row"));
				}
				streakAttacker = e.attacker();
				streak = 1;
				streakStart = e.tick();
			}
		}
		if (streak >= 3) {
			obs.add(new Observation(streakStart, Technique.COMBO, streakAttacker, false, streak + " hits in a row"));
		}
	}

	/** A swing that matched no hit/no-damage event by the same participant within two ticks, with the opponent close. */
	private static List<Observation> findWhiffs(DuelTrace trace) {
		List<Observation> whiffs = new ArrayList<>();
		for (int p = 0; p < trace.participants().size(); p++) {
			if (!trace.participants().get(p).player()) {
				continue;
			}
			int opp = trace.opponentOf(p);
			for (int t = 0; t <= trace.lastTick(); t++) {
				ParticipantFrame f = trace.state(t, p);
				if (!f.swingStart() || f.distanceTo(trace.state(t, opp)) > WHIFF_MAX_DISTANCE) {
					continue;
				}
				boolean matched = false;
				for (CombatEvent e : trace.events()) {
					if (e.attacker() == p && e.type() != CombatEvent.Type.DEATH && Math.abs(e.tick() - t) <= 2) {
						matched = true;
						break;
					}
				}
				if (!matched) {
					whiffs.add(new Observation(t, Technique.WHIFF, p, false,
							String.format("opponent %.1f blocks away", f.distanceTo(trace.state(t, opp)))));
				}
			}
		}
		return whiffs;
	}

	private static void addWhiffs(DuelTrace trace, List<Observation> whiffs, List<Integer> group,
			List<CombatEvent> events, int start, int end, List<Observation> obs) {
		for (Observation w : whiffs) {
			int punishIdx = -1;
			for (int i = 0; i < events.size(); i++) {
				CombatEvent e = events.get(i);
				if (e.type() == CombatEvent.Type.HIT && e.attacker() == trace.opponentOf(w.actor())
						&& e.tick() > w.tick() && e.tick() <= w.tick() + WHIFF_PUNISH_WINDOW) {
					punishIdx = i;
					break;
				}
			}
			if (punishIdx >= 0) {
				if (group.contains(punishIdx)) {
					CombatEvent punish = events.get(punishIdx);
					obs.add(w);
					obs.add(new Observation(punish.tick(), Technique.WHIFF_PUNISHED, w.actor(), false,
							(punish.tick() - w.tick()) + " ticks after the miss"));
				}
			} else if (w.tick() >= start && w.tick() <= end) {
				obs.add(w);
			}
		}
	}
}
