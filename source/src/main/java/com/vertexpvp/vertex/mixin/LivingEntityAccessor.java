package com.vertexpvp.vertex.mixin;

import net.minecraft.world.entity.LivingEntity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

/** Lets the replay set your (local) player's attack charge, so vanilla's own cooldown indicator shows the fighter's. */
@Mixin(LivingEntity.class)
public interface LivingEntityAccessor {

	@Accessor("attackStrengthTicker")
	void vertex$setAttackStrengthTicker(int ticks);
}
