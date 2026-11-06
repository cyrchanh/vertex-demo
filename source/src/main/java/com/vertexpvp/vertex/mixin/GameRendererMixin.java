package com.vertexpvp.vertex.mixin;

import com.vertexpvp.vertex.replay.ReplayScreen;
import net.minecraft.client.renderer.GameRenderer;
import net.minecraft.client.renderer.state.level.CameraRenderState;
import org.joml.Matrix4fc;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Vanilla draws your own first-person hand whenever the camera mode is first person. The replay's
 * side camera also uses first-person mode (it looks through an invisible entity), so the hand is
 * skipped there; it's only drawn when you're actually watching from a fighter's eyes.
 */
@Mixin(GameRenderer.class)
abstract class GameRendererMixin {

	@Inject(method = "renderItemInHand", at = @At("HEAD"), cancellable = true)
	private void vertex$hideHandOutsideFirstPersonReplay(CameraRenderState camera, float partialTick, Matrix4fc projection,
			CallbackInfo ci) {
		if (ReplayScreen.hidesHand()) {
			ci.cancel();
		}
	}
}
