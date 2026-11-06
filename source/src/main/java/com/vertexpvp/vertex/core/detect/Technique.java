package com.vertexpvp.vertex.core.detect;

/**
 * Things the detector can recognise, named after the Sword guides' vocabulary. Each one is tied to
 * observable trace data; see {@link InteractionDetector} for the exact rule behind each.
 */
public enum Technique {
	/** A server-confirmed crit landed. */
	CRIT_LANDED,
	/** A server-confirmed sprint hit (extra knockback) landed. */
	SPRINT_HIT_LANDED,
	/** A sprint hit thrown while airborne and falling. */
	FALLING_SPRINT_HIT,
	/** A hit thrown before the attack recharged. */
	WEAK_HIT,
	/** A fully charged follow-up that was neither crit nor sprint hit: sprint was not reset since the last hit. */
	NO_SPRINT_RESET,
	/** A follow-up sprint hit after re-sprinting (W-tap / sprint reset). */
	SPRINT_RESET,
	/** A hit landed from roughly 3 blocks, the edge of hit range. */
	THREE_BLOCK_HIT,
	/** A hit landed from very close, where sprint knockback buys little spacing. */
	CLOSE_RANGE_HIT,
	/** Spacing tap: back pressed briefly just before a hit. */
	S_TAP,

	/** The victim was rising in a jump when hit (jump-arc vulnerability). */
	HIT_WHILE_RISING,
	/** The victim jumped in and was hit before landing anything (failed jump-in / failed first crit). */
	FAILED_JUMP_IN,
	/** The victim was crouching when hit. */
	CROUCHING_WHEN_HIT,
	/** The victim jumped right as the hit landed, cutting knockback. */
	JUMP_RESET,
	/** The victim backed off (held S / moved away) right after being hit. */
	BACKED_OFF_AFTER_HIT,
	/** The victim took a sprint hit and neither jump-reset, backed off, nor hit back. */
	NO_RESPONSE,

	/** Three or more hits in a row from the same attacker without being hit back. */
	COMBO,
	/** Both sides landed hits inside the same exchange. */
	TRADE,
	/** The first hit of the fight. */
	FIRST_HIT,

	/** A swing that hit nothing while the opponent was close. */
	WHIFF,
	/** A whiff followed quickly by the opponent landing a hit. */
	WHIFF_PUNISHED,
	/** This client predicted a crit/sprint hit but the server counted something else (latency/desync). */
	SERVER_DISAGREED
}
