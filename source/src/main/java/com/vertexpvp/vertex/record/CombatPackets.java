package com.vertexpvp.vertex.record;

import net.minecraft.resources.Identifier;

/**
 * Entry point for the packet mixin ({@code ClientPacketListenerMixin}). The mixin calls these after
 * vanilla has handled each packet on the main thread; they forward to the active recorder.
 * Kept as plain static calls so the mixin itself stays tiny.
 */
public final class CombatPackets {

	private static volatile DuelRecorder recorder;

	private CombatPackets() {
	}

	public static void bind(DuelRecorder r) {
		recorder = r;
	}

	/** Server says {@code victimId} took damage; {@code causeId} is the attacking entity, or -1. */
	public static void damage(int victimId, int causeId) {
		DuelRecorder r = recorder;
		if (r != null) {
			r.onDamagePacket(victimId, causeId);
		}
	}

	/** Server sent an entity animation; {@code action} is a {@code ClientboundAnimatePacket} action code. */
	public static void animate(int entityId, int action) {
		DuelRecorder r = recorder;
		if (r != null) {
			r.onAnimatePacket(entityId, action);
		}
	}

	/** Server played a sound at a position. */
	public static void sound(Identifier soundId, double x, double y, double z) {
		DuelRecorder r = recorder;
		if (r != null) {
			r.onSoundPacket(soundId, x, y, z);
		}
	}

	/** Server set an entity's velocity (this is how knockback reaches the client). */
	public static void motion(int entityId, double x, double y, double z) {
		DuelRecorder r = recorder;
		if (r != null) {
			r.onMotionPacket(entityId, x, y, z);
		}
	}
}
