package com.vertexpvp.vertex.core.explain;

import com.vertexpvp.vertex.core.detect.Interaction;
import com.vertexpvp.vertex.core.trace.DuelTrace;

import java.util.List;

/**
 * Turns detected interactions into coaching. The built-in {@link RuleBasedExplainer} works offline;
 * an AI-backed implementation can replace it later without touching the recorder, detector or
 * replay viewer, as long as it returns the same {@link Explanation} shape.
 */
public interface Explainer {

	/** Stable id stored in each replay, so you can tell which explainer wrote its annotations. */
	String id();

	/** One explanation per (interaction, player participant) pair where there's something to say. */
	List<Explanation> explain(DuelTrace trace, List<Interaction> interactions);
}
