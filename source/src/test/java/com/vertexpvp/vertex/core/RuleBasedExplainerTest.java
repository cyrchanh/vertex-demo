package com.vertexpvp.vertex.core;

import com.vertexpvp.vertex.core.detect.InteractionDetector;
import com.vertexpvp.vertex.core.explain.Explanation;
import com.vertexpvp.vertex.core.explain.Explanation.Verdict;
import com.vertexpvp.vertex.core.explain.RuleBasedExplainer;
import com.vertexpvp.vertex.core.trace.CombatEvent.HitKind;
import com.vertexpvp.vertex.core.trace.DuelTrace;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class RuleBasedExplainerTest {

	private static ReplayDocument analyse(DuelTrace trace) {
		var interactions = new InteractionDetector().detect(trace);
		var explainer = new RuleBasedExplainer();
		return new ReplayDocument(trace, interactions, explainer.explain(trace, interactions), explainer.id());
	}

	@Test
	void failedJumpInIsTheMainMistakeAndCitesTheBaitingGuide() {
		ReplayDocument doc = analyse(new TraceBuilder(60).jump(0, 10).hit(14, 1, HitKind.STRONG).build());

		Explanation mine = doc.explanationFor(0, 0).orElseThrow();

		assertEquals(Verdict.MISTAKE, mine.verdict());
		assertEquals("You jumped in and got hit before landing anything", mine.headline());
		assertTrue(mine.source().contains("Baiting"));
		assertFalse(mine.instead().isBlank());
		assertFalse(mine.insteadWhy().isBlank());
	}

	@Test
	void firstCritIsGoodForTheAttackerAndAdviceForTheVictim() {
		ReplayDocument doc = analyse(new TraceBuilder(60).hit(20, 0, HitKind.CRIT).build());

		Explanation mine = doc.explanationFor(0, 0).orElseThrow();
		Explanation theirs = doc.explanationFor(0, 1).orElseThrow();

		assertEquals(Verdict.GOOD, mine.verdict());
		assertEquals("You landed the first crit", mine.headline());
		assertTrue(mine.instead().startsWith("Hold W briefly"), "follow-up advice from Scenario 1");
		assertEquals("They crit you", theirs.headline());
	}

	@Test
	void mistakesOutrankStrengths() {
		// I land a crit, but I'd also jumped in earlier and got punished - the correction comes first.
		ReplayDocument doc = analyse(new TraceBuilder(80)
				.jump(0, 10)
				.hit(14, 1, HitKind.STRONG)
				.hit(25, 0, HitKind.CRIT)
				.build());

		assertEquals(Verdict.MISTAKE, doc.explanationFor(0, 0).orElseThrow().verdict());
	}

	@Test
	void comboedPlayerIsToldToStopSwinging() {
		ReplayDocument doc = analyse(new TraceBuilder(80)
				.hit(20, 1, HitKind.SPRINT_HIT)
				.hit(30, 1, HitKind.SPRINT_HIT)
				.hit(40, 1, HitKind.SPRINT_HIT)
				.build());

		// NO_RESPONSE outranks being comboed, and both are mistakes for me.
		Explanation mine = doc.explanationFor(0, 0).orElseThrow();
		assertEquals(Verdict.MISTAKE, mine.verdict());
		assertTrue(mine.notes().stream().anyMatch(n -> n.startsWith("They comboed you")));
	}

	@Test
	void mobOpponentsGetNoExplanations() {
		ReplayDocument doc = analyse(new TraceBuilder(60).opponentIsMob().hit(20, 0, HitKind.CRIT).build());

		List<Explanation> forMob = doc.explanations().stream().filter(e -> e.perspective() == 1).toList();

		assertTrue(forMob.isEmpty());
		assertTrue(doc.explanationFor(0, 0).isPresent());
	}

	@Test
	void everyExplanationHasAllFourParts() {
		ReplayDocument doc = analyse(new TraceBuilder(200)
				.hit(20, 0, HitKind.SPRINT_HIT)
				.hit(30, 0, HitKind.STRONG)
				.swing(1, 60)
				.hit(66, 0, HitKind.CRIT)
				.hit(120, 1, HitKind.WEAK)
				.hit(160, 0, HitKind.STRONG)
				.hit(162, 1, HitKind.STRONG)
				.build());

		assertFalse(doc.explanations().isEmpty());
		for (Explanation e : doc.explanations()) {
			assertFalse(e.headline().isBlank(), e.toString());
			assertFalse(e.what().isBlank(), e.toString());
			assertFalse(e.why().isBlank(), e.toString());
			assertFalse(e.instead().isBlank(), e.toString());
			assertFalse(e.insteadWhy().isBlank(), e.toString());
			assertFalse(e.source().isBlank(), e.toString());
		}
	}
}
