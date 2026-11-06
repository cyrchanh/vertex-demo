package com.vertexpvp.vertex.core;

import com.vertexpvp.vertex.core.detect.Interaction;
import com.vertexpvp.vertex.core.detect.InteractionDetector;
import com.vertexpvp.vertex.core.detect.Observation;
import com.vertexpvp.vertex.core.detect.Technique;
import com.vertexpvp.vertex.core.trace.CombatEvent.HitKind;
import com.vertexpvp.vertex.core.trace.ControlInput;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class InteractionDetectorTest {

	private final InteractionDetector detector = new InteractionDetector();

	@Test
	void hitsFarApartBecomeSeparateExchanges() {
		var trace = new TraceBuilder(120)
				.hit(20, 0, HitKind.SPRINT_HIT)
				.hit(30, 0, HitKind.SPRINT_HIT)
				.hit(80, 1, HitKind.STRONG)
				.build();

		List<Interaction> result = detector.detect(trace);

		assertEquals(2, result.size());
		assertEquals(2, result.get(0).hitsBy()[0]);
		assertEquals(1, result.get(1).hitsBy()[1]);
		assertTrue(result.get(0).has(Technique.FIRST_HIT, 0));
		assertFalse(result.get(1).has(Technique.FIRST_HIT, 1), "only the very first hit of the fight is FIRST_HIT");
	}

	@Test
	void consecutiveSprintHitsMeanTheSprintWasReset() {
		var trace = new TraceBuilder(60)
				.hit(20, 0, HitKind.SPRINT_HIT)
				.hit(30, 0, HitKind.SPRINT_HIT)
				.build();

		Interaction in = detector.detect(trace).get(0);

		assertEquals(2, in.count(Technique.SPRINT_HIT_LANDED, 0));
		assertTrue(in.has(Technique.SPRINT_RESET, 0));
		assertFalse(in.has(Technique.NO_SPRINT_RESET, 0));
	}

	@Test
	void chargedFollowUpWithoutSprintIsNoSprintReset() {
		var trace = new TraceBuilder(60)
				.hit(20, 0, HitKind.SPRINT_HIT)
				.hit(30, 0, HitKind.STRONG)
				.build();

		assertTrue(detector.detect(trace).get(0).has(Technique.NO_SPRINT_RESET, 0));
	}

	@Test
	void threeHitsInARowIsACombo() {
		var trace = new TraceBuilder(80)
				.hit(20, 0, HitKind.SPRINT_HIT)
				.hit(30, 0, HitKind.SPRINT_HIT)
				.hit(40, 0, HitKind.SPRINT_HIT)
				.build();

		Observation combo = detector.detect(trace).get(0).first(Technique.COMBO, 0);

		assertNotNull(combo);
		assertEquals("3 hits in a row", combo.detail());
	}

	@Test
	void opponentHitAfterJumpingInIsAFailedJumpInMarkedAsEstimated() {
		var trace = new TraceBuilder(60)
				.jump(1, 10)
				.hit(14, 0, HitKind.STRONG)
				.build();

		Interaction in = detector.detect(trace).get(0);
		Observation failed = in.first(Technique.FAILED_JUMP_IN, 1);

		assertNotNull(failed);
		assertTrue(failed.inferred(), "opponent motion is estimated client-side, so the label must say so");
		assertTrue(in.has(Technique.HIT_WHILE_RISING, 1));
	}

	@Test
	void recorderFailedJumpInIsExact() {
		var trace = new TraceBuilder(60)
				.jump(0, 10)
				.hit(14, 1, HitKind.STRONG)
				.build();

		Observation failed = detector.detect(trace).get(0).first(Technique.FAILED_JUMP_IN, 0);

		assertNotNull(failed);
		assertFalse(failed.inferred());
	}

	@Test
	void jumpAtTheMomentOfASprintHitIsAJumpReset() {
		var builder = new TraceBuilder(60).hit(20, 1, HitKind.SPRINT_HIT);
		builder.keys(20, 20, TraceBuilder.keys(false, false, true));

		Interaction in = detector.detect(builder.build()).get(0);

		assertTrue(in.has(Technique.JUMP_RESET, 0));
		assertFalse(in.has(Technique.NO_RESPONSE, 0));
	}

	@Test
	void doingNothingAfterASprintHitIsNoResponse() {
		var trace = new TraceBuilder(60).hit(20, 1, HitKind.SPRINT_HIT).build();

		assertTrue(detector.detect(trace).get(0).has(Technique.NO_RESPONSE, 0));
	}

	@Test
	void holdingBackAfterAHitIsBackingOff() {
		var builder = new TraceBuilder(60).hit(20, 1, HitKind.SPRINT_HIT);
		builder.keys(21, 24, TraceBuilder.keys(false, true, false));

		Interaction in = detector.detect(builder.build()).get(0);

		assertTrue(in.has(Technique.BACKED_OFF_AFTER_HIT, 0));
		assertFalse(in.has(Technique.NO_RESPONSE, 0));
	}

	@Test
	void opponentResponseIsNeverGuessed() {
		var trace = new TraceBuilder(60).hit(20, 0, HitKind.SPRINT_HIT).build();

		Interaction in = detector.detect(trace).get(0);

		assertFalse(in.has(Technique.NO_RESPONSE, 1), "opponent keys are unknown, so no-response must not be claimed");
		assertFalse(in.has(Technique.JUMP_RESET, 1));
	}

	@Test
	void missedSwingFollowedByTheirHitIsAPunishedWhiff() {
		var trace = new TraceBuilder(60)
				.swing(0, 10)
				.hit(18, 1, HitKind.STRONG)
				.build();

		Interaction in = detector.detect(trace).get(0);

		assertTrue(in.has(Technique.WHIFF, 0));
		assertTrue(in.has(Technique.WHIFF_PUNISHED, 0));
	}

	@Test
	void swingThatLandedIsNotAWhiff() {
		var trace = new TraceBuilder(60)
				.swing(0, 20)
				.hit(20, 0, HitKind.STRONG)
				.build();

		assertFalse(detector.detect(trace).get(0).has(Technique.WHIFF, 0));
	}

	@Test
	void serverDisagreeingWithTheClientPredictionIsFlagged() {
		var trace = new TraceBuilder(60)
				.hit(20, 0, HitKind.STRONG, HitKind.CRIT, 2.5)
				.build();

		Observation o = detector.detect(trace).get(0).first(Technique.SERVER_DISAGREED, 0);

		assertNotNull(o);
		assertEquals("expected a crit, server counted a normal hit", o.detail());
	}

	@Test
	void briefBackPressReleasedBeforeTheHitIsAnSTap() {
		var builder = new TraceBuilder(60).hit(18, 0, HitKind.SPRINT_HIT);
		builder.keys(14, 15, TraceBuilder.keys(false, true, false));
		builder.keys(16, 17, new ControlInput(true, false, false, false, false, false, true));

		assertTrue(detector.detect(builder.build()).get(0).has(Technique.S_TAP, 0));
	}

	@Test
	void rangeLabels() {
		var trace = new TraceBuilder(60)
				.hit(20, 0, HitKind.SPRINT_HIT, null, 2.9)
				.hit(30, 0, HitKind.SPRINT_HIT, null, 1.2)
				.build();

		Interaction in = detector.detect(trace).get(0);

		assertTrue(in.has(Technique.THREE_BLOCK_HIT, 0));
		assertTrue(in.has(Technique.CLOSE_RANGE_HIT, 0));
	}

	@Test
	void noHitsMeansNoInteractions() {
		assertTrue(detector.detect(new TraceBuilder(40).swing(0, 5).build()).isEmpty());
	}
}
