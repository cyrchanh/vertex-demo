package com.vertexpvp.vertex.core.explain;

import java.util.List;

/**
 * Coaching for one interaction, from one participant's point of view: what happened, why it went
 * that way, what to do instead, and why that works.
 *
 * @param interaction index into {@code ReplayDocument.interactions()}
 * @param perspective participant index this explanation is written for
 * @param source      where the advice comes from (guide and section), so it can be checked
 * @param notes       secondary observations worth knowing, one short line each
 * @param inferred    true when the main point relies on estimated data (e.g. opponent's motion)
 */
public record Explanation(
		int interaction,
		int perspective,
		Verdict verdict,
		String headline,
		String what,
		String why,
		String instead,
		String insteadWhy,
		String source,
		List<String> notes,
		boolean inferred
) {
	public enum Verdict {
		GOOD,
		MISTAKE,
		NEUTRAL
	}
}
