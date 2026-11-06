package com.vertexpvp.vertex.core.trace;

/**
 * Something that happened between the two participants on a tick.
 *
 * <p>Hit facts come from what the server sends every nearby client: the damage-event packet
 * (who hurt whom), the crit animation, and the attack sounds vanilla broadcasts from the
 * attacker's position (crit / sprint-knockback / strong / weak / no-damage).
 * {@link #serverConfirmed()} is true when {@link #kind()} came from those server signals rather than
 * from this client's own prediction.
 *
 * @param attacker      participant index of the attacker (-1 if not a participant)
 * @param victim        participant index of the victim
 * @param predictedKind for the recording player's own hits, what this client predicted using
 *                      vanilla's rules at the moment of the click; {@code null} otherwise
 * @param range         distance between the two participants when the hit landed, in blocks
 * @param knockbackX    knockback velocity the server applied to the victim, or NaN if not seen
 */
public record CombatEvent(
		int tick,
		Type type,
		int attacker,
		int victim,
		HitKind kind,
		HitKind predictedKind,
		boolean serverConfirmed,
		double range,
		double knockbackX,
		double knockbackY,
		double knockbackZ
) {
	public enum Type {
		/** Damage landed. */
		HIT,
		/** The attack connected but the server dealt no damage (invulnerability window, shield, ...). */
		NO_DAMAGE,
		/** The victim died. */
		DEATH
	}

	public enum HitKind {
		/** Airborne, falling, not sprinting, fully charged: 1.5x damage, little knockback. */
		CRIT,
		/** Sprinting and fully charged: extra knockback. */
		SPRINT_HIT,
		/** Fully charged, neither crit nor sprint (includes sword sweeps). */
		STRONG,
		/** Swung before the attack recharged (attack strength 0.9 or below). */
		WEAK,
		/** Damage from a non-player attacker. */
		MOB,
		/** Damage landed but no server classification arrived. */
		UNKNOWN
	}

	public boolean hasKnockback() {
		return !Double.isNaN(knockbackX);
	}

	public double horizontalKnockback() {
		return hasKnockback() ? Math.sqrt(knockbackX * knockbackX + knockbackZ * knockbackZ) : Double.NaN;
	}
}
