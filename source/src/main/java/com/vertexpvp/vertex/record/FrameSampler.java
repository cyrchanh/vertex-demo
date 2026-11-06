package com.vertexpvp.vertex.record;

import com.vertexpvp.vertex.core.trace.ControlInput;
import com.vertexpvp.vertex.core.trace.EffectState;
import com.vertexpvp.vertex.core.trace.ParticipantFrame;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientPacketListener;
import net.minecraft.client.multiplayer.PlayerInfo;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.effect.MobEffectInstance;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.player.Input;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Turns a live entity into a {@link ParticipantFrame} once per client tick. Main thread only.
 *
 * <p>For both fighters, velocity is how far they actually moved since the previous tick. The local
 * player's held keys, attack charge, fall distance, hotbar slot and effects are exact; other
 * entities only expose what the server sends this client, so those are left unknown for them.
 * Equipment, absorption, item use and ping are known for both.
 */
final class FrameSampler {

	private record Previous(Vec3 position, boolean swinging, int swingTime, int hurtTime) {
	}

	private final Map<Integer, Previous> previous = new HashMap<>();

	ParticipantFrame sample(LivingEntity e, boolean isLocalPlayer) {
		Vec3 pos = e.position();
		Previous prev = previous.get(e.getId());

		// Velocity = how far the entity actually moved since last tick, for BOTH fighters. (Vanilla's
		// own getDeltaMovement() on the local player has friction/drag already applied for the next
		// tick, so it reads ~0.15 while sprinting at ~0.28 blocks/tick and wouldn't be comparable
		// with the opponent, whose velocity can only be measured from position.)
		Vec3 velocity = prev == null ? Vec3.ZERO : pos.subtract(prev.position());
		ControlInput input = null;
		float attackStrength = -1f;
		double fallDistance = -1.0;
		if (isLocalPlayer && e instanceof LocalPlayer local) {
			Input keys = local.input.keyPresses;
			input = new ControlInput(keys.forward(), keys.backward(), keys.left(), keys.right(),
					keys.jump(), keys.shift(), keys.sprint());
			attackStrength = local.getAttackStrengthScale(0.5f);
			fallDistance = local.fallDistance;
		}

		// A swing starts when vanilla restarts the swing counter (it goes back to 0 from higher, or
		// swinging turns on). This works the same for us and for the opponent.
		boolean swingStart = e.swinging && (prev == null || !prev.swinging() || e.swingTime < prev.swingTime());

		previous.put(e.getId(), new Previous(pos, e.swinging, e.swingTime, e.hurtTime));

		// Held item, off hand and armour: the server syncs every nearby entity's equipment, so these
		// are known for both fighters. Absorption and the using-item flag are synced entity data too.
		ItemStack held = e.getMainHandItem();
		String mainHand = itemId(held);
		List<String> armor = List.of(itemId(e.getItemBySlot(EquipmentSlot.HEAD)), itemId(e.getItemBySlot(EquipmentSlot.CHEST)),
				itemId(e.getItemBySlot(EquipmentSlot.LEGS)), itemId(e.getItemBySlot(EquipmentSlot.FEET)));

		// Hotbar slot and effects only for the local player: servers send each player only their own
		// effects, so an opponent's would always look empty.
		Integer selectedSlot = null;
		List<EffectState> effects = null;
		if (isLocalPlayer && e instanceof LocalPlayer local) {
			selectedSlot = local.getInventory().getSelectedSlot();
			effects = new ArrayList<>();
			for (MobEffectInstance effect : local.getActiveEffects()) {
				effects.add(new EffectState(effect.getEffect().getRegisteredName(), effect.getAmplifier(),
						effect.isInfiniteDuration() ? -1 : effect.getDuration()));
			}
		}

		return new ParticipantFrame(
				pos.x, pos.y, pos.z,
				e.getYRot(), e.getXRot(), e.getYHeadRot(), e.yBodyRot,
				velocity.x, velocity.y, velocity.z,
				e.onGround(),
				e.isSprinting(),
				e.isShiftKeyDown(),
				input,
				e.getHealth(),
				attackStrength,
				fallDistance,
				swingStart,
				e.hurtTime,
				mainHand,
				held.hasFoil(),
				itemId(e.getOffhandItem()),
				armor,
				e.getAbsorptionAmount(),
				e.isUsingItem(),
				selectedSlot,
				pingMs(e),
				effects
		);
	}

	private static String itemId(ItemStack stack) {
		return BuiltInRegistries.ITEM.getKey(stack.getItem()).toString();
	}

	/** Latency from the server's player list (the tab list), for any player; null for mobs. */
	private static Integer pingMs(LivingEntity e) {
		ClientPacketListener connection = Minecraft.getInstance().getConnection();
		if (!(e instanceof Player) || connection == null) {
			return null;
		}
		PlayerInfo info = connection.getPlayerInfo(e.getUUID());
		return info == null ? null : info.getLatency();
	}

	/** Drop state for entities no longer being sampled, so the map doesn't grow forever. */
	void retainOnly(Set<Integer> ids) {
		previous.keySet().retainAll(ids);
	}

	void reset() {
		previous.clear();
	}
}
