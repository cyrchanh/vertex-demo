package com.vertexpvp.vertex.core;

import com.vertexpvp.vertex.core.detect.Interaction;
import com.vertexpvp.vertex.core.explain.Explanation;
import com.vertexpvp.vertex.core.trace.DuelTrace;

import java.util.List;
import java.util.Optional;

/**
 * Everything a replay needs: the raw trace, the detected interactions, and the explanations, plus
 * which explainer wrote them. This is exactly what's saved to disk.
 */
public record ReplayDocument(DuelTrace trace, List<Interaction> interactions, List<Explanation> explanations,
		String explainerId) {

	public Optional<Explanation> explanationFor(int interaction, int perspective) {
		for (Explanation e : explanations) {
			if (e.interaction() == interaction && e.perspective() == perspective) {
				return Optional.of(e);
			}
		}
		return Optional.empty();
	}
}
