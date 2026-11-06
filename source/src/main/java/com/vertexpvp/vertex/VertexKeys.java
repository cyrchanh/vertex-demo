package com.vertexpvp.vertex;

import com.mojang.blaze3d.platform.InputConstants;
import net.fabricmc.fabric.api.client.keymapping.v1.KeyMappingHelper;
import net.minecraft.client.KeyMapping;
import net.minecraft.resources.Identifier;
import org.lwjgl.glfw.GLFW;

/**
 * Vertex's key bindings. They show up under "Vertex Replay" in Options > Controls > Key Binds (and
 * via the Controls button in the replay library), where any of them can be rebound. The replay keys
 * only do anything while the replay viewer is open, so sharing a key with vanilla (Space, 1, 2) is
 * fine: vanilla's binding is ignored while a screen is open.
 */
public final class VertexKeys {

	public static final KeyMapping.Category CATEGORY =
			KeyMapping.Category.register(Identifier.fromNamespaceAndPath(VertexClient.MOD_ID, "replay"));

	public static KeyMapping openLibrary;
	public static KeyMapping playPause;
	public static KeyMapping back;
	public static KeyMapping forward;
	public static KeyMapping faster;
	public static KeyMapping slower;
	public static KeyMapping view1;
	public static KeyMapping view2;
	public static KeyMapping camera;
	public static KeyMapping nextExchange;
	public static KeyMapping previousExchange;

	private VertexKeys() {
	}

	static void register() {
		openLibrary = key("open_library", GLFW.GLFW_KEY_J);
		playPause = key("play_pause", GLFW.GLFW_KEY_SPACE);
		back = key("back", GLFW.GLFW_KEY_LEFT);
		forward = key("forward", GLFW.GLFW_KEY_RIGHT);
		faster = key("faster", GLFW.GLFW_KEY_UP);
		slower = key("slower", GLFW.GLFW_KEY_DOWN);
		view1 = key("view_1", GLFW.GLFW_KEY_1);
		view2 = key("view_2", GLFW.GLFW_KEY_2);
		camera = key("camera", GLFW.GLFW_KEY_C);
		nextExchange = key("next_exchange", GLFW.GLFW_KEY_N);
		previousExchange = key("previous_exchange", GLFW.GLFW_KEY_P);
	}

	private static KeyMapping key(String name, int defaultKey) {
		return KeyMappingHelper.registerKeyMapping(
				new KeyMapping("key.vertex." + name, InputConstants.Type.KEYSYM, defaultKey, CATEGORY));
	}

	/** The key's current binding as short text for on-screen hints, e.g. "Space", "C" or "Left". */
	public static String label(KeyMapping mapping) {
		return mapping.getTranslatedKeyMessage().getString().replace(" Arrow", "");
	}
}
