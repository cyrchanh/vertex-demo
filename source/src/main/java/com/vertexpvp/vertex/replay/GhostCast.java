package com.vertexpvp.vertex.replay;

import com.mojang.authlib.GameProfile;
import com.vertexpvp.vertex.core.trace.DuelTrace;
import com.vertexpvp.vertex.core.trace.ParticipantFrame;
import com.vertexpvp.vertex.core.trace.ParticipantInfo;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.player.RemotePlayer;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntitySpawnReason;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.InterpolationHandler;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.Pose;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

/**
 * The two "ghost" entities that act out a replay. They exist only in this client's world (negative
 * ids, random UUIDs) - the server never knows about them, and they're removed when the replay closes.
 *
 * <p>Players are played back as {@link RemotePlayer}s, the same class vanilla uses for other
 * players, and moved with the same interpolation vanilla uses for network movement. Because their
 * UUIDs are fresh, they show the default skin rather than the real one.
 */
final class GhostCast {

	private static final int ID_BASE = -1_000_000;
	/** Slots a ghost shows from the recording: held items and worn armour. */
	private static final EquipmentSlot[] EQUIPMENT = {EquipmentSlot.MAINHAND, EquipmentSlot.OFFHAND,
			EquipmentSlot.HEAD, EquipmentSlot.CHEST, EquipmentSlot.LEGS, EquipmentSlot.FEET};

	private final DuelTrace trace;
	private final List<LivingEntity> ghosts = new ArrayList<>();
	private final int[] lastHurtTime;

	GhostCast(DuelTrace trace) {
		this.trace = trace;
		this.lastHurtTime = new int[trace.participants().size()];
	}

	void spawn(ClientLevel level) {
		for (ParticipantInfo info : trace.participants()) {
			LivingEntity ghost = create(level, info);
			ghost.setId(ID_BASE - info.index());
			ghost.setUUID(UUID.randomUUID());
			ghosts.add(ghost);
			apply(info.index(), trace.state(0, info.index()), true);
			level.addEntity(ghost);
			InterpolationHandler interpolation = ghost.getInterpolation();
			if (interpolation != null) {
				interpolation.setInterpolationLength(1);
			}
		}
	}

	private static LivingEntity create(ClientLevel level, ParticipantInfo info) {
		if (!info.player()) {
			EntityType<?> type = BuiltInRegistries.ENTITY_TYPE.getValue(Identifier.parse(info.entityType()));
			Entity entity = type.create(level, EntitySpawnReason.LOAD);
			if (entity instanceof LivingEntity living) {
				return living;
			}
		}
		String name = info.name().length() > 16 ? info.name().substring(0, 16) : info.name();
		return new RemotePlayer(level, new GameProfile(UUID.randomUUID(), name));
	}

	void despawn(ClientLevel level) {
		for (LivingEntity ghost : ghosts) {
			level.removeEntity(ghost.getId(), Entity.RemovalReason.DISCARDED);
		}
		ghosts.clear();
	}

	List<LivingEntity> ghosts() {
		return ghosts;
	}

	LivingEntity ghost(int participant) {
		return ghosts.get(participant);
	}

	/** Pose a ghost on a recorded frame. {@code snap} jumps there instantly (seeking); otherwise it moves smoothly. */
	void apply(int participant, ParticipantFrame f, boolean snap) {
		LivingEntity g = ghosts.get(participant);
		Vec3 pos = new Vec3(f.x(), f.y(), f.z());
		if (snap) {
			InterpolationHandler interpolation = g.getInterpolation();
			if (interpolation != null) {
				interpolation.cancel();
			}
			g.snapTo(f.x(), f.y(), f.z(), f.yaw(), f.pitch());
			g.setOldPosAndRot();
			g.setYHeadRot(f.headYaw());
			g.yBodyRot = f.bodyYaw();
		} else {
			g.moveOrInterpolateTo(pos, f.yaw(), f.pitch());
			g.lerpHeadTo(f.headYaw(), 1);
		}
		g.setOnGround(f.onGround());
		g.setSprinting(f.sprinting());
		g.setShiftKeyDown(f.sneaking());
		g.setPose(f.sneaking() ? Pose.CROUCHING : Pose.STANDING);
		g.setHealth(Math.max(1f, f.health()));
		for (EquipmentSlot slot : EQUIPMENT) {
			ItemStack item = HeldItems.stack(f, slot);
			if (!ItemStack.matches(g.getItemBySlot(slot), item)) {
				g.setItemSlot(slot, item);
			}
		}
		if (!snap && f.swingStart()) {
			g.swing(InteractionHand.MAIN_HAND);
		}
		if (!snap && f.hurtTime() > lastHurtTime[participant]) {
			g.animateHurt(f.yaw());
		}
		lastHurtTime[participant] = f.hurtTime();
	}
}
