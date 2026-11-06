package com.vertexpvp.arena;

import net.minecraft.core.BlockPos;
import net.minecraft.core.Holder;
import net.minecraft.core.registries.Registries;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.util.Mth;
import net.minecraft.util.RandomSource;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.item.enchantment.Enchantment;
import net.minecraft.world.item.enchantment.Enchantments;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.Vec3;

/**
 * Arena A0 from the training maps: a flat 24×24 smooth-stone box with 3-high glass walls, high
 * above the world, and the sword kit (full diamond armour with Protection III, an unenchanted
 * diamond sword, nothing else).
 */
final class ArenaLayout {

	static final int HALF = 12;
	static final int FLOOR_Y = 100;
	static final int WALL_HEIGHT = 3;
	/** Closest and farthest the fighters start apart, in blocks, unless a reset asks otherwise. */
	static final double MIN_START = 6.0;
	static final double MAX_START = 10.0;
	/** Limits for a requested range: not inside each other, and inside the walls. */
	static final double MIN_ALLOWED = 1.0;
	static final double MAX_ALLOWED = 2 * HALF - 4.0;

	private ArenaLayout() {
	}

	static void build(ServerLevel level) {
		BlockState floor = Blocks.SMOOTH_STONE.defaultBlockState();
		BlockState wall = Blocks.GLASS.defaultBlockState();
		for (int x = -HALF - 1; x <= HALF; x++) {
			for (int z = -HALF - 1; z <= HALF; z++) {
				boolean edge = x == -HALF - 1 || x == HALF || z == -HALF - 1 || z == HALF;
				level.setBlock(new BlockPos(x, FLOOR_Y - 1, z), floor, Block.UPDATE_CLIENTS);
				for (int y = 0; y < WALL_HEIGHT; y++) {
					level.setBlock(new BlockPos(x, FLOOR_Y + y, z), edge ? wall : Blocks.AIR.defaultBlockState(), Block.UPDATE_CLIENTS);
				}
			}
		}
	}

	/** Two start positions facing each other, at a random distance in [min, max] and a random angle. */
	static Vec3[] starts(RandomSource random, double min, double max) {
		min = Math.clamp(min, MIN_ALLOWED, MAX_ALLOWED);
		max = Math.clamp(max, min, MAX_ALLOWED);
		double distance = min + random.nextDouble() * (max - min);
		double angle = random.nextDouble() * Math.PI * 2;
		double dx = Math.cos(angle) * distance / 2;
		double dz = Math.sin(angle) * distance / 2;
		return new Vec3[] {new Vec3(dx, FLOOR_Y, dz), new Vec3(-dx, FLOOR_Y, -dz)};
	}

	/** Minecraft yaw that looks from {@code from} towards {@code to}. */
	static float yawTowards(Vec3 from, Vec3 to) {
		return (float) (Mth.atan2(to.z - from.z, to.x - from.x) * Mth.RAD_TO_DEG) - 90f;
	}

	static void equipKit(ServerPlayer bot) {
		Holder<Enchantment> protection = bot.level().registryAccess().lookupOrThrow(Registries.ENCHANTMENT)
				.getOrThrow(Enchantments.PROTECTION);
		bot.getInventory().clearContent();
		bot.setItemSlot(EquipmentSlot.HEAD, armour(Items.DIAMOND_HELMET, protection));
		bot.setItemSlot(EquipmentSlot.CHEST, armour(Items.DIAMOND_CHESTPLATE, protection));
		bot.setItemSlot(EquipmentSlot.LEGS, armour(Items.DIAMOND_LEGGINGS, protection));
		bot.setItemSlot(EquipmentSlot.FEET, armour(Items.DIAMOND_BOOTS, protection));
		bot.getInventory().setSelectedSlot(0);
		bot.setItemSlot(EquipmentSlot.MAINHAND, new ItemStack(Items.DIAMOND_SWORD));
	}

	private static ItemStack armour(Item item, Holder<Enchantment> protection) {
		ItemStack stack = new ItemStack(item);
		stack.enchant(protection, 3);
		return stack;
	}
}
