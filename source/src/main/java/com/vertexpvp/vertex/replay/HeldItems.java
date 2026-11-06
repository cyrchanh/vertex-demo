package com.vertexpvp.vertex.replay;

import com.vertexpvp.vertex.core.trace.ParticipantFrame;
import com.vertexpvp.vertex.core.view.AttackCharge;
import net.minecraft.core.component.DataComponents;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.component.ItemAttributeModifiers;

/** Turns a recorded held-item id back into an item, and works out its attack cooldown. */
final class HeldItems {

	private HeldItems() {
	}

	/** Armour slots in the order {@link ParticipantFrame#armor()} records them. */
	static final EquipmentSlot[] ARMOR_SLOTS = {EquipmentSlot.HEAD, EquipmentSlot.CHEST, EquipmentSlot.LEGS, EquipmentSlot.FEET};

	/** The recorded held item, or empty when it wasn't recorded (older replays) or isn't a known item. */
	static ItemStack stack(ParticipantFrame f) {
		ItemStack stack = item(f.mainHand());
		if (f.mainHandFoil() && !stack.isEmpty()) {
			stack.set(DataComponents.ENCHANTMENT_GLINT_OVERRIDE, true);
		}
		return stack;
	}

	/** The recorded item for an equipment slot, or empty when it wasn't recorded (older replays). */
	static ItemStack stack(ParticipantFrame f, EquipmentSlot slot) {
		if (slot == EquipmentSlot.MAINHAND) {
			return stack(f);
		}
		if (slot == EquipmentSlot.OFFHAND) {
			return item(f.offHand());
		}
		for (int i = 0; i < ARMOR_SLOTS.length; i++) {
			if (ARMOR_SLOTS[i] == slot) {
				return f.armor() == null || f.armor().size() <= i ? ItemStack.EMPTY : item(f.armor().get(i));
			}
		}
		return ItemStack.EMPTY;
	}

	private static ItemStack item(String itemId) {
		if (itemId == null) {
			return ItemStack.EMPTY;
		}
		Identifier id = Identifier.tryParse(itemId);
		if (id == null || !BuiltInRegistries.ITEM.containsKey(id)) {
			return ItemStack.EMPTY;
		}
		return new ItemStack(BuiltInRegistries.ITEM.getValue(id));
	}

	/** Ticks for a full charge with this item in a player's main hand (20 / attack speed). */
	static float attackDelayTicks(ParticipantFrame f) {
		if (f.mainHand() == null) {
			return AttackCharge.SWORD_DELAY_TICKS;
		}
		ItemStack stack = stack(f);
		double speed = stack.getOrDefault(DataComponents.ATTRIBUTE_MODIFIERS, ItemAttributeModifiers.EMPTY)
				.compute(Attributes.ATTACK_SPEED, Attributes.DEFAULT_ATTACK_SPEED, EquipmentSlot.MAINHAND);
		return speed > 0 ? (float) (20.0 / speed) : AttackCharge.SWORD_DELAY_TICKS;
	}
}
