package com.vertexpvp.vertex.replay;

import com.mojang.authlib.GameProfile;
import com.vertexpvp.vertex.core.trace.ParticipantFrame;
import com.vertexpvp.vertex.core.view.SideCamera;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.player.RemotePlayer;
import net.minecraft.util.Mth;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.InterpolationHandler;
import net.minecraft.world.phys.Vec3;

import java.util.UUID;

/**
 * The side-on fight camera. Minecraft can only look through an entity, so this is an invisible,
 * client-only entity (like the ghosts) that {@link SideCamera} steers each tick. It's moved with the
 * same one-tick interpolation as the ghosts, so it glides smoothly at any frame rate. Minecraft never
 * draws the entity it's looking through, so it never shows up on screen.
 */
final class CameraRig {

	private static final int ID = -1_000_100;

	private final SideCamera side = new SideCamera();
	private RemotePlayer entity;
	private float lastYaw;

	Entity entity() {
		return entity;
	}

	void setFraming(double shiftLeft, double tanHalfFovX) {
		side.setFraming(shiftLeft, tanHalfFovX);
	}

	void spawn(ClientLevel level, ParticipantFrame a, ParticipantFrame b) {
		if (entity != null) {
			return;
		}
		entity = new RemotePlayer(level, new GameProfile(UUID.randomUUID(), "vertex-camera"));
		entity.setId(ID);
		entity.setInvisible(true);
		side.reset();
		update(a, b, true);
		level.addEntity(entity);
		InterpolationHandler interpolation = entity.getInterpolation();
		if (interpolation != null) {
			interpolation.setInterpolationLength(1);
		}
	}

	void update(ParticipantFrame a, ParticipantFrame b, boolean snap) {
		if (entity == null) {
			return;
		}
		SideCamera.Pose pose = side.update(a, b, snap);
		double feetY = pose.y() - entity.getEyeHeight();
		// Keep yaw continuous (e.g. 179 -> 181 instead of 179 -> -179) so interpolation never spins
		// the long way round.
		float yaw = snap ? pose.yaw() : lastYaw + Mth.wrapDegrees(pose.yaw() - lastYaw);
		lastYaw = yaw;
		if (snap) {
			InterpolationHandler interpolation = entity.getInterpolation();
			if (interpolation != null) {
				interpolation.cancel();
			}
			entity.snapTo(pose.x(), feetY, pose.z(), yaw, pose.pitch());
			entity.setOldPosAndRot();
			entity.setYHeadRot(yaw);
		} else {
			entity.moveOrInterpolateTo(new Vec3(pose.x(), feetY, pose.z()), yaw, pose.pitch());
			entity.lerpHeadTo(yaw, 1);
		}
	}

	void despawn(ClientLevel level) {
		if (entity != null) {
			level.removeEntity(entity.getId(), Entity.RemovalReason.DISCARDED);
			entity = null;
		}
	}
}
