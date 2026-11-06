package com.vertexpvp.vertex.core.explain;

import com.vertexpvp.vertex.core.ReplayDocument;
import com.vertexpvp.vertex.core.explain.Explanation.Verdict;
import com.vertexpvp.vertex.core.trace.CombatEvent;
import com.vertexpvp.vertex.core.trace.DuelTrace;

import java.util.LinkedHashMap;
import java.util.Map;

/** One-glance fight summary for a participant, used for the chat message after a fight. */
public final class SummaryBuilder {

	private SummaryBuilder() {
	}

	public record Summary(
			int hitsLanded,
			int hitsTaken,
			int critsLanded,
			int critsTaken,
			int sprintHitsLanded,
			int sprintHitsTaken,
			int seconds,
			String result,
			String biggestMistake,
			String bestMoment
	) {
		public String statsLine() {
			return String.format("%ds - landed %d (%d crit, %d sprint), took %d (%d crit, %d sprint)",
					seconds, hitsLanded, critsLanded, sprintHitsLanded, hitsTaken, critsTaken, sprintHitsTaken);
		}
	}

	public static Summary forParticipant(ReplayDocument doc, int p) {
		DuelTrace trace = doc.trace();
		int o = trace.opponentOf(p);
		int landed = 0, taken = 0, critsLanded = 0, critsTaken = 0, sprintLanded = 0, sprintTaken = 0;
		for (CombatEvent e : trace.events()) {
			if (e.type() != CombatEvent.Type.HIT) {
				continue;
			}
			if (e.attacker() == p) {
				landed++;
				if (e.kind() == CombatEvent.HitKind.CRIT) critsLanded++;
				if (e.kind() == CombatEvent.HitKind.SPRINT_HIT) sprintLanded++;
			} else if (e.victim() == p) {
				taken++;
				if (e.kind() == CombatEvent.HitKind.CRIT) critsTaken++;
				if (e.kind() == CombatEvent.HitKind.SPRINT_HIT) sprintTaken++;
			}
		}

		String result;
		if (trace.winner() == p) {
			result = "You won";
		} else if (trace.winner() == o) {
			result = "You lost";
		} else {
			result = landed > taken ? "You out-traded them" : landed < taken ? "They out-traded you" : "Even fight";
		}

		return new Summary(landed, taken, critsLanded, critsTaken, sprintLanded, sprintTaken,
				Math.round(trace.frames().size() / 20f), result,
				mostCommon(doc, p, Verdict.MISTAKE), mostCommon(doc, p, Verdict.GOOD));
	}

	private static String mostCommon(ReplayDocument doc, int p, Verdict verdict) {
		Map<String, Integer> counts = new LinkedHashMap<>();
		for (Explanation e : doc.explanations()) {
			if (e.perspective() == p && e.verdict() == verdict) {
				counts.merge(stripCount(e.headline()), 1, Integer::sum);
			}
		}
		return counts.entrySet().stream()
				.max(Map.Entry.comparingByValue())
				.map(en -> en.getValue() > 1 ? en.getKey() + " (x" + en.getValue() + ")" : en.getKey())
				.orElse(null);
	}

	/** "You comboed them (4 hits in a row)" -> "You comboed them", so repeats group together. */
	private static String stripCount(String headline) {
		int paren = headline.indexOf(" (");
		return paren > 0 ? headline.substring(0, paren) : headline;
	}
}
