package com.vertexpvp.vertex.core.explain;

import com.vertexpvp.vertex.core.detect.Interaction;
import com.vertexpvp.vertex.core.detect.Observation;
import com.vertexpvp.vertex.core.detect.Technique;
import com.vertexpvp.vertex.core.explain.Explanation.Verdict;
import com.vertexpvp.vertex.core.trace.DuelTrace;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/**
 * Offline explainer: an ordered list of rules written from the Sword guides (Baiting, Starting an
 * Engagement, Mid-Engagement). For each interaction and each player, the first rule that matches
 * becomes the main explanation; other recognised techniques become short notes. Mistakes are
 * checked before strengths so the most useful correction surfaces first.
 */
public final class RuleBasedExplainer implements Explainer {

	public static final String ID = "rules-v1";

	private static final String GUIDE_BAITING = "Sword Guide: Baiting";
	private static final String GUIDE_FIRST_HIT = "Sword Guide: Starting an Engagement";
	private static final String GUIDE_MID = "Sword Guide: Mid-Engagement";

	@Override
	public String id() {
		return ID;
	}

	@Override
	public List<Explanation> explain(DuelTrace trace, List<Interaction> interactions) {
		List<Explanation> out = new ArrayList<>();
		for (Interaction interaction : interactions) {
			for (var participant : trace.participants()) {
				if (!participant.player()) {
					continue;
				}
				int p = participant.index();
				int o = trace.opponentOf(p);
				String opp = trace.participants().get(o).name();
				explainFor(interaction, p, o, opp).ifPresent(out::add);
			}
		}
		return out;
	}

	private Optional<Explanation> explainFor(Interaction in, int p, int o, String opp) {
		List<String> notes = notes(in, p, o);

		Observation failedJump = in.first(Technique.FAILED_JUMP_IN, p);
		if (failedJump != null) {
			return make(in, p, Verdict.MISTAKE,
					"You jumped in and got hit before landing anything",
					"You left the ground going for a hit, and " + opp + " hit you while you were still in the air.",
					"Once you're airborne your arc is visible and they have full ground control, so they can time a hit "
							+ "onto you before yours connects. A failed jump-in hands them the first hit for free.",
					"Only jump when the hit is confirmed. Hold S until you're at crittable range and then counter-jump "
							+ "(crit bait), or stay grounded and sprint-hit them while they are the one rising.",
					"Staying grounded keeps every option open. The player who commits to a jump without a guaranteed "
							+ "hit is the one who gets punished.",
					GUIDE_BAITING + " (When Not to Jump); " + GUIDE_FIRST_HIT + " (Scenario 3)", notes, failedJump.inferred());
		}

		Observation rising = in.first(Technique.HIT_WHILE_RISING, p);
		if (rising != null) {
			return make(in, p, Verdict.MISTAKE,
					"You got hit while rising in a jump",
					opp + " hit you on the way up, before you could swing a crit.",
					"On the way up you can't land a crit yet and you have no ground to react from, so the hit lands clean "
							+ "and you eat the full knockback.",
					"Don't jump into their range unless the crit is guaranteed. If you're already airborne and they hold "
							+ "S, switch to a falling sprint hit instead of forcing the crit.",
					"A falling sprint hit uses their backward momentum against them, while a forced crit into a retreating "
							+ "player usually whiffs or lands weak.",
					GUIDE_FIRST_HIT + " (Scenario 2)", notes, rising.inferred());
		}

		Observation whiffPunished = in.first(Technique.WHIFF_PUNISHED, p);
		if (whiffPunished != null) {
			Observation whiff = in.first(Technique.WHIFF, p);
			String where = whiff != null && !whiff.detail().isEmpty() ? " (" + whiff.detail() + ")" : "";
			return make(in, p, Verdict.MISTAKE,
					"Your swing missed and they punished it",
					"You swung and hit nothing" + where + ", and " + opp + " hit you " + whiffPunished.detail() + ".",
					"A whiff is a commitment with no payoff. The recovery after it is exactly the window a bait is built "
							+ "to create, whether they meant to bait you or not.",
					"Swing when they're inside about 3 blocks. If they're backing off, don't chase with swings: check for "
							+ "bait before you commit.",
					"Holding your swing until it can land keeps the recovery window on their side instead of yours.",
					GUIDE_BAITING + " (S-Bait, When Not to Jump)", notes, false);
		}

		Observation noResponse = in.first(Technique.NO_RESPONSE, p);
		if (noResponse != null) {
			return make(in, p, Verdict.MISTAKE,
					"You took a sprint hit without reacting",
					opp + " sprint-hit you and you didn't jump-reset, back off, or hit back.",
					"A sprint hit throws you into their 3-block spacing. Doing nothing lets them repeat the same hit.",
					"Hold S first (lowest commitment, highest reliability). Jump reset only on the first 1-2 sprint hits, "
							+ "and if you can't hit back within 1-2 exchanges, go for distance instead of damage.",
					"Holding S or a well-timed jump reset cuts the knockback that their combo depends on.",
					GUIDE_MID + " (Scenario B)", notes, false);
		}

		Observation theirCombo = in.first(Technique.COMBO, o);
		if (theirCombo != null) {
			return make(in, p, Verdict.MISTAKE,
					"They comboed you (" + theirCombo.detail() + ")",
					opp + " landed " + theirCombo.detail() + " without you hitting back.",
					"Each sprint hit stuns you and resets their spacing. Swinging back while stunned usually loses the "
							+ "trade and keeps you in range.",
					"Hold S for distance, jump reset only the first 1-2 sprint hits, and stop swinging if nothing lands. "
							+ "Reset to neutral and check for bait before re-engaging.",
					"Getting out of their 3-block spacing ends the combo; trading while stunned extends it.",
					GUIDE_MID + " (Scenario B, Knowing when to stop swinging)", notes, false);
		}

		Observation weak = in.first(Technique.WEAK_HIT, p);
		if (weak != null) {
			return make(in, p, Verdict.MISTAKE,
					"You swung before your attack recharged",
					"At least one of your hits landed before your attack cooldown was full.",
					"Hits at 90% charge or less do reduced damage and can't be a crit or a sprint hit, so they give away "
							+ "the follow-up.",
					"Time your swings to the attack reset instead of clicking faster.",
					"A fully charged hit is what makes crits and sprint knockback possible at all.",
					GUIDE_MID + " (Combo fundamentals)", notes, false);
		}

		Observation noReset = in.first(Technique.NO_SPRINT_RESET, p);
		if (noReset != null) {
			return make(in, p, Verdict.MISTAKE,
					"Your follow-up hit wasn't a sprint hit",
					"You landed a follow-up, but you hadn't re-sprinted, so it was a normal hit.",
					"The game ends your sprint after a sprint hit. Without re-sprinting, the next hit has no extra "
							+ "knockback and they walk straight back into range.",
					"Release and re-press W (W-tap), or S-tap, between hits so each one lands as a sprint hit at about "
							+ "3 blocks.",
					"Hit timing and 3-block spacing are the baseline of a combo; each sprint hit re-creates the spacing "
							+ "for the next.",
					GUIDE_MID + " (Scenario A)", notes, false);
		}

		Observation crit = in.first(Technique.CRIT_LANDED, p);
		if (crit != null && in.has(Technique.FIRST_HIT, p)) {
			return make(in, p, Verdict.GOOD,
					"You landed the first crit",
					"You opened the fight with a crit on " + opp + ".",
					"Whoever lands the first crit gets the follow-up chain: they're stunned and you have the initiative.",
					"Hold W briefly right after it. Don't jump reset straight away; react to what they do next "
							+ "(deflect a crit, no-KB crit a close sprint hit, falling sprint hit at range).",
					"Holding W keeps you grounded and cuts your knockback, while jumping immediately is readable and lets "
							+ "them time a p-crit onto your landing.",
					GUIDE_FIRST_HIT + " (Scenario 1)", notes, false);
		}

		Observation myCombo = in.first(Technique.COMBO, p);
		if (myCombo != null) {
			long resets = in.count(Technique.SPRINT_RESET, p);
			return make(in, p, Verdict.GOOD,
					"You comboed them (" + myCombo.detail() + ")",
					"You landed " + myCombo.detail() + " on " + opp
							+ (resets > 0 ? ", re-sprinting between hits " + resets + " time(s)." : "."),
					"Keeping them in a stun at your spacing means they can't trade back.",
					"If they start jump-resetting, delay your next hit slightly or switch to s-tap combos. If they hold "
							+ "S, convert to crit spam or a falling sprint hit.",
					"Adjusting to their escape attempt is what keeps a combo going instead of resetting to neutral.",
					GUIDE_MID + " (Scenario A)", notes, false);
		}

		Observation reset = in.first(Technique.JUMP_RESET, p);
		Observation backed = in.first(Technique.BACKED_OFF_AFTER_HIT, p);
		if (reset != null || backed != null) {
			String did = reset != null ? "jump-reset" : "backed off with S";
			return make(in, p, Verdict.GOOD,
					"You reacted to the hit correctly",
					"After getting hit, you " + did + ".",
					reset != null
							? "A jump reset at the moment of the hit cancels much of the horizontal knockback."
							: "Holding S is the lowest-commitment way out of their spacing.",
					reset != null
							? "Use it on the first 1-2 sprint hits only, never against crits, and bail if it isn't working."
							: "Once you have space, you're back at neutral: check for bait before charging back in.",
					"Staying out of a stun is how you turn a disadvantage back into neutral.",
					GUIDE_MID + " (Scenario B)", notes, false);
		}

		if (crit != null) {
			return make(in, p, Verdict.GOOD,
					"You landed a crit",
					"You crit " + opp + ".",
					"A crit does 1.5x damage with little knockback, so they stay close enough to follow up on.",
					"If they jump right after, sprint-hit them while they rise. If they delay, reposition or set up an "
							+ "s-hold deflection for their p-crit.",
					"Reading their response to the crit decides whether you keep the advantage.",
					GUIDE_FIRST_HIT + " (Scenario 1)", notes, false);
		}

		if (in.has(Technique.FALLING_SPRINT_HIT, p)) {
			return make(in, p, Verdict.GOOD,
					"You landed a falling sprint hit",
					"You sprint-hit " + opp + " while coming down from a jump.",
					"Falling, you take less knockback back, and the hit still sends them away with full sprint knockback.",
					"Good against strafers and players holding S. Keep using it when the range is right.",
					"It wins on range, physics and safety against a strafing opponent.",
					GUIDE_FIRST_HIT + " (Scenario 7)", notes, false);
		}

		if (in.has(Technique.SPRINT_HIT_LANDED, p) && in.has(Technique.THREE_BLOCK_HIT, p)) {
			return make(in, p, Verdict.GOOD,
					"Clean 3-block sprint hit",
					"You sprint-hit " + opp + " from the edge of hit range.",
					"Landing at about 3 blocks means their swing can't reach you back.",
					"Jump reset with an uppercut motion at the 3-block mark to keep the pressure going.",
					"The 3-block hit is the goal of the sprint-hit race.",
					GUIDE_FIRST_HIT + " (Scenario 5)", notes, false);
		}

		Observation theirCrit = in.first(Technique.CRIT_LANDED, o);
		if (theirCrit != null) {
			return make(in, p, Verdict.NEUTRAL,
					"They crit you",
					opp + " landed a crit on you.",
					"A crit means they were airborne and falling with a full charge, usually because you let them close "
							+ "the gap while you were grounded and predictable.",
					"At mid-to-long range, use an s-hold deflection: hold S, absorb some knockback, then sprint-hit them "
							+ "as they land. At short range, double crit then deflect.",
					"Deflecting or double-critting breaks their follow-up chain instead of letting them press.",
					GUIDE_FIRST_HIT + " (Scenario 3)", notes, false);
		}

		int mine = in.hitsBy()[p];
		int theirs = in.hitsBy()[o];
		if (mine == 0 && theirs == 0) {
			return Optional.empty();
		}
		if (mine > theirs) {
			return make(in, p, Verdict.GOOD,
					"You won this exchange (" + mine + "-" + theirs + ")",
					"You landed more hits than " + opp + " in this exchange.",
					"You were the one setting the spacing here.",
					"Keep fixing your line after the first hit so the knockback goes somewhere you can follow up.",
					"Knockback that goes sideways is hard to follow up on.",
					GUIDE_MID + " (Line and angle correction)", notes, false);
		}
		if (mine < theirs) {
			return make(in, p, Verdict.MISTAKE,
					"You lost this exchange (" + mine + "-" + theirs + ")",
					opp + " landed more hits than you in this exchange.",
					"They controlled the spacing in this exchange.",
					"Check which state you're in before acting: if you're stunned, the goal is distance, not damage.",
					"Forcing hits from disadvantage usually extends their combo.",
					GUIDE_MID + " (State-Based Reading)", notes, false);
		}
		return make(in, p, Verdict.NEUTRAL,
				"Even trade (" + mine + "-" + theirs + ")",
				"You and " + opp + " traded hits evenly.",
				"Neither side established spacing control.",
				"Look for the window: whoever lands the next clean sprint hit at 3 blocks takes the advantage.",
				"Even trades reset to neutral; the next commitment decides it.",
				GUIDE_MID + " (Scenario D)", notes, false);
	}

	private static Optional<Explanation> make(Interaction in, int p, Verdict verdict, String headline, String what,
			String why, String instead, String insteadWhy, String source, List<String> notes, boolean inferred) {
		return Optional.of(new Explanation(in.index(), p, verdict, headline, what, why, instead, insteadWhy, source,
				List.copyOf(notes), inferred));
	}

	/** Short lines for everything recognised about this player in this interaction. */
	private static List<String> notes(Interaction in, int p, int o) {
		List<String> notes = new ArrayList<>();
		for (Observation obs : in.observations()) {
			String line = noteFor(obs, p, o);
			if (line != null && !notes.contains(line)) {
				notes.add(line);
			}
		}
		return notes;
	}

	private static String noteFor(Observation obs, int p, int o) {
		String detail = obs.detail().isEmpty() ? "" : " (" + obs.detail() + ")";
		if (obs.actor() == p) {
			return switch (obs.technique()) {
				case CRIT_LANDED -> "You landed a crit";
				case SPRINT_HIT_LANDED -> "You landed a sprint hit";
				case FALLING_SPRINT_HIT -> "You landed a falling sprint hit";
				case WEAK_HIT -> "You threw a weak (uncharged) hit";
				case NO_SPRINT_RESET -> "Follow-up hit without re-sprinting" + detail;
				case SPRINT_RESET -> "Re-sprinted between hits";
				case THREE_BLOCK_HIT -> "Hit from the 3-block edge" + detail;
				case CLOSE_RANGE_HIT -> "Hit from very close" + detail;
				case S_TAP -> "S-tapped before a hit";
				case HIT_WHILE_RISING -> "Got hit while rising in a jump";
				case FAILED_JUMP_IN -> "Got hit after jumping in";
				case CROUCHING_WHEN_HIT -> "Was crouching when hit";
				case JUMP_RESET -> "Jump-reset a hit";
				case BACKED_OFF_AFTER_HIT -> "Backed off after a hit";
				case NO_RESPONSE -> "Didn't react to a sprint hit";
				case COMBO -> "Combo" + detail;
				case FIRST_HIT -> "Landed the first hit of the fight";
				case WHIFF -> "Missed a swing" + detail;
				case WHIFF_PUNISHED -> "Got punished for a missed swing";
				case SERVER_DISAGREED -> "Server disagreed with your client" + detail;
				case TRADE -> null;
			};
		}
		if (obs.actor() == o) {
			return switch (obs.technique()) {
				case CRIT_LANDED -> "They landed a crit on you";
				case SPRINT_HIT_LANDED -> "They sprint-hit you";
				case FALLING_SPRINT_HIT -> "They falling-sprint-hit you";
				case COMBO -> "They comboed you" + detail;
				case FIRST_HIT -> "They landed the first hit of the fight";
				case WHIFF_PUNISHED -> "You punished their missed swing";
				case HIT_WHILE_RISING -> "You caught them rising in a jump";
				case FAILED_JUMP_IN -> "You punished their jump-in";
				default -> null;
			};
		}
		return null;
	}
}
