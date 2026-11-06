package com.vertexpvp.vertex.mixin;

import com.vertexpvp.vertex.record.CombatPackets;
import net.minecraft.client.multiplayer.ClientPacketListener;
import net.minecraft.network.protocol.game.ClientboundAnimatePacket;
import net.minecraft.network.protocol.game.ClientboundDamageEventPacket;
import net.minecraft.network.protocol.game.ClientboundSetEntityMotionPacket;
import net.minecraft.network.protocol.game.ClientboundSoundPacket;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Reads combat facts the server already sends to every nearby client. Read-only: nothing here
 * changes, cancels or sends any packet.
 *
 * <p>Injected at RETURN on purpose: vanilla handlers first bounce the packet from the network
 * thread to the main thread by throwing, so the network-thread call never reaches a return and
 * these hooks only run once, on the main thread, after vanilla has applied the packet.
 */
@Mixin(ClientPacketListener.class)
public abstract class ClientPacketListenerMixin {

	@Inject(method = "handleDamageEvent", at = @At("RETURN"))
	private void vertex$onDamageEvent(ClientboundDamageEventPacket packet, CallbackInfo ci) {
		CombatPackets.damage(packet.entityId(), packet.sourceCauseId());
	}

	@Inject(method = "handleAnimate", at = @At("RETURN"))
	private void vertex$onAnimate(ClientboundAnimatePacket packet, CallbackInfo ci) {
		CombatPackets.animate(packet.getId(), packet.getAction());
	}

	@Inject(method = "handleSoundEvent", at = @At("RETURN"))
	private void vertex$onSound(ClientboundSoundPacket packet, CallbackInfo ci) {
		CombatPackets.sound(packet.getSound().value().location(), packet.getX(), packet.getY(), packet.getZ());
	}

	@Inject(method = "handleSetEntityMotion", at = @At("RETURN"))
	private void vertex$onMotion(ClientboundSetEntityMotionPacket packet, CallbackInfo ci) {
		CombatPackets.motion(packet.id(), packet.movement().x, packet.movement().y, packet.movement().z);
	}
}
