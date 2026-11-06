package com.vertexpvp.vertex.core.view;

import com.vertexpvp.vertex.core.trace.DuelTrace;
import com.vertexpvp.vertex.core.trace.ParticipantFrame;

import java.util.Objects;

/**
 * How charged a fighter's attack was on a given tick (vanilla's attack-strength scale, 0..1), for
 * the replay's cooldown indicator.
 *
 * <p>For the recording player this is the exact recorded value. Nobody's client receives the
 * opponent's charge, so for them it's estimated the way vanilla computes it: it resets to 0 on
 * every swing (hit or miss) and on switching items, then refills over {@code delayTicks}
 * (20 / attack speed; 12.5 for a sword). Pure maths, no Minecraft imports.
 */
public final class AttackCharge {

	/** Delay for a sword (attack speed 1.6) when the held item is unknown. */
	public static final float SWORD_DELAY_TICKS = 12.5f;

	private AttackCharge() {
	}

	public static boolean exact(DuelTrace trace, int participant, int tick) {
		return trace.state(tick, participant).attackStrength() >= 0f;
	}

	public static float at(DuelTrace trace, int participant, int tick, float delayTicks) {
		ParticipantFrame now = trace.state(tick, participant);
		if (now.attackStrength() >= 0f) {
			return now.attackStrength();
		}
		int since = -1;
		for (int t = tick; t >= 0; t--) {
			ParticipantFrame f = trace.state(t, participant);
			if (f.swingStart()) {
				since = tick - t;
				break;
			}
			if (t > 0) {
				String before = trace.state(t - 1, participant).mainHand();
				if (f.mainHand() != null && before != null && !Objects.equals(f.mainHand(), before)) {
					since = tick - t;
					break;
				}
			}
		}
		if (since < 0) {
			return 1f;
		}
		// Vanilla: getAttackStrengthScale(0.5) = clamp((ticksSinceReset + 0.5) / delay, 0, 1).
		return Math.max(0f, Math.min(1f, (since + 0.5f) / Math.max(1f, delayTicks)));
	}
}
