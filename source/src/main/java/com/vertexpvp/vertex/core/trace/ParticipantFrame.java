package com.vertexpvp.vertex.core.trace;

import java.util.List;

/**
 * One participant's state at the end of one client tick.
 *
 * <p>For the recording player everything here is exact. For the opponent, position/rotation/flags
 * are what the server told this client (exact as of the last packet, smoothed by vanilla
 * interpolation), and {@code input} and {@code attackStrength} are unknown.
 *
 * <p>Velocity ({@code vx, vy, vz}) is the actual movement since the previous tick, in blocks per
 * tick, for both fighters, so the two are directly comparable. (Not vanilla's internal
 * {@code getDeltaMovement()}, which has next-tick friction applied.) A teleport shows up as one
 * large spike.
 *
 * @param input          exact held keys, or {@code null} when unknown (the opponent)
 * @param attackStrength vanilla attack-strength scale 0..1, or -1 when unknown
 * @param swingStart     the participant started an arm swing on this tick (hit or miss)
 * @param hurtTime       vanilla hurt-animation counter (10 on the tick damage lands, counting down)
 * @param mainHand       held item id ({@code "minecraft:diamond_sword"}, {@code "minecraft:air"} = empty
 *                       hand), or {@code null} when unknown (recordings made before this was saved)
 * @param mainHandFoil   the held item has the enchantment glint
 * @param offHand        off-hand item id, or {@code null} when not recorded (format 3+). Known for both
 *                       fighters: the server syncs equipment to every nearby client
 * @param armor          worn armour item ids, head to feet ({@code "minecraft:air"} = empty slot), or
 *                       {@code null} when not recorded. Known for both fighters
 * @param absorption     absorption hearts in health points (golden apples); known for both fighters
 * @param usingItem      eating, drinking, blocking or drawing a bow on this tick; known for both
 * @param selectedSlot   hotbar slot 0..8, or {@code null} when unknown (the opponent, older replays)
 * @param pingMs         this player's latency from the server's player list, or {@code null} when
 *                       unknown (mobs, older replays). The server updates it every few seconds
 * @param effects        active potion effects, or {@code null} when unknown. Servers only send a
 *                       player their own effects, so this is the recorder's alone
 */
public record ParticipantFrame(
		double x, double y, double z,
		float yaw, float pitch, float headYaw, float bodyYaw,
		double vx, double vy, double vz,
		boolean onGround,
		boolean sprinting,
		boolean sneaking,
		ControlInput input,
		float health,
		float attackStrength,
		double fallDistance,
		boolean swingStart,
		int hurtTime,
		String mainHand,
		boolean mainHandFoil,
		String offHand,
		List<String> armor,
		float absorption,
		boolean usingItem,
		Integer selectedSlot,
		Integer pingMs,
		List<EffectState> effects
) {
	/** A frame without the format 3 fields (gear, ping, effects unknown). */
	public ParticipantFrame(double x, double y, double z, float yaw, float pitch, float headYaw, float bodyYaw,
			double vx, double vy, double vz, boolean onGround, boolean sprinting, boolean sneaking, ControlInput input,
			float health, float attackStrength, double fallDistance, boolean swingStart, int hurtTime,
			String mainHand, boolean mainHandFoil) {
		this(x, y, z, yaw, pitch, headYaw, bodyYaw, vx, vy, vz, onGround, sprinting, sneaking, input, health,
				attackStrength, fallDistance, swingStart, hurtTime, mainHand, mainHandFoil,
				null, null, 0f, false, null, null, null);
	}

	/** A frame with the held item unknown. */
	public ParticipantFrame(double x, double y, double z, float yaw, float pitch, float headYaw, float bodyYaw,
			double vx, double vy, double vz, boolean onGround, boolean sprinting, boolean sneaking, ControlInput input,
			float health, float attackStrength, double fallDistance, boolean swingStart, int hurtTime) {
		this(x, y, z, yaw, pitch, headYaw, bodyYaw, vx, vy, vz, onGround, sprinting, sneaking, input, health,
				attackStrength, fallDistance, swingStart, hurtTime, null, false);
	}

	public boolean ascending() {
		return !onGround && vy > 0.0;
	}

	public boolean descending() {
		return !onGround && vy < 0.0;
	}

	public double horizontalSpeed() {
		return Math.sqrt(vx * vx + vz * vz);
	}

	public double horizontalDistanceTo(ParticipantFrame other) {
		double dx = other.x - x;
		double dz = other.z - z;
		return Math.sqrt(dx * dx + dz * dz);
	}

	public double distanceTo(ParticipantFrame other) {
		double dx = other.x - x;
		double dy = other.y - y;
		double dz = other.z - z;
		return Math.sqrt(dx * dx + dy * dy + dz * dz);
	}

	/**
	 * Horizontal speed toward {@code other} (positive = closing in, negative = backing off),
	 * in blocks per tick.
	 */
	public double radialSpeedToward(ParticipantFrame other) {
		double dx = other.x - x;
		double dz = other.z - z;
		double len = Math.sqrt(dx * dx + dz * dz);
		if (len < 1.0e-6) {
			return 0.0;
		}
		return (vx * dx + vz * dz) / len;
	}
}
