package com.vertexpvp.vertex;

import com.vertexpvp.vertex.command.VertexCommands;
import com.vertexpvp.vertex.core.ReplayDocument;
import com.vertexpvp.vertex.core.explain.RuleBasedExplainer;
import com.vertexpvp.vertex.core.explain.SummaryBuilder;
import com.vertexpvp.vertex.record.CombatPackets;
import com.vertexpvp.vertex.record.DuelRecorder;
import com.vertexpvp.vertex.record.ReplayStore;
import com.vertexpvp.vertex.replay.ReplayLibraryScreen;
import com.vertexpvp.vertex.replay.ReplayWorld;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.fabricmc.fabric.api.client.rendering.v1.hud.HudElementRegistry;
import net.fabricmc.fabric.api.client.rendering.v1.hud.VanillaHudElements;
import com.vertexpvp.vertex.replay.ReplayScreen;
import net.minecraft.client.gui.components.AbstractWidget;
import net.minecraft.client.gui.components.SpriteIconButton;
import net.minecraft.resources.Identifier;
import net.fabricmc.fabric.api.client.screen.v1.ScreenEvents;
import net.fabricmc.fabric.api.client.screen.v1.Screens;
import net.fabricmc.fabric.api.event.player.AttackEntityCallback;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.ChatFormatting;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.screens.PauseScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.MutableComponent;
import net.minecraft.world.InteractionResult;

import java.util.ArrayList;
import java.util.List;
import java.util.function.Supplier;

/**
 * Client entrypoint. Vertex is client-only: it records fights from what your client already
 * receives, saves replays to your own disk, and never sends anything to a server.
 *
 * <p>Recording is on automatically in singleplayer / your own LAN world, and off by default every
 * time you join a server (a one-line chat notice offers to turn it on).
 */
public final class VertexClient implements ClientModInitializer {

	public static final String MOD_ID = "vertex";

	private static DuelRecorder recorder;
	private static Supplier<Screen> pendingScreen;
	private static final List<Runnable> pendingTasks = new ArrayList<>();

	public static DuelRecorder recorder() {
		return recorder;
	}

	/**
	 * Opens a screen at the end of the current tick. Needed when opening from a chat command or
	 * chat link: the chat screen closes itself right after running the command, which would
	 * otherwise close the newly opened screen too.
	 */
	public static void openNextTick(Supplier<Screen> screen) {
		pendingScreen = screen;
	}

	/** Runs something at the end of the current tick, outside whatever screen or packet handling is going on. */
	public static void runNextTick(Runnable task) {
		pendingTasks.add(task);
	}

	@Override
	public void onInitializeClient() {
		ReplayStore store = new ReplayStore(FabricLoader.getInstance().getGameDir().resolve("vertex-replays"));
		recorder = new DuelRecorder(store, new RuleBasedExplainer());
		CombatPackets.bind(recorder);
		recorder.addListener(VertexClient::announce);
		VertexCommands.register(recorder);
		VertexKeys.register();

		ClientTickEvents.END_CLIENT_TICK.register(client -> {
			recorder.onEndClientTick(client);
			ReplayWorld.tick(client);
			while (VertexKeys.openLibrary.consumeClick()) {
				if (client.gui.screen() == null) {
					client.setScreenAndShow(new ReplayLibraryScreen(null));
				}
			}
			if (pendingScreen != null) {
				Supplier<Screen> screen = pendingScreen;
				pendingScreen = null;
				client.setScreenAndShow(screen.get());
			}
			while (!pendingTasks.isEmpty()) {
				pendingTasks.remove(0).run();
			}
		});

		// The replay library is reachable from the title screen (an icon button next to vanilla's small
		// square ones) and the pause menu, so recordings can be watched even when you're not on a server.
		ScreenEvents.AFTER_INIT.register((client, screen, width, height) -> {
			if (screen instanceof TitleScreen) {
				addTitleScreenButton(client, screen, width, height);
			} else if (screen instanceof PauseScreen && !ReplayWorld.isActive()) {
				Screens.getWidgets(screen).add(Button.builder(Component.literal("Vertex Replays"),
								b -> client.setScreenAndShow(new ReplayLibraryScreen(screen)))
						.bounds(4, 4, 98, 20).build());
			}
		});

		// While a replay plays, your own health, food, XP, effects and hotbar mean nothing (and the
		// timeline covers the hotbar), so they're hidden. The crosshair stays in first person: it carries
		// vanilla's attack-cooldown indicator.
		for (Identifier element : List.of(VanillaHudElements.HOTBAR, VanillaHudElements.HEALTH_BAR, VanillaHudElements.ARMOR_BAR,
				VanillaHudElements.FOOD_BAR, VanillaHudElements.AIR_BAR, VanillaHudElements.MOUNT_HEALTH,
				VanillaHudElements.EXPERIENCE_LEVEL, VanillaHudElements.INFO_BAR, VanillaHudElements.MOB_EFFECTS,
				VanillaHudElements.HELD_ITEM_TOOLTIP)) {
			HudElementRegistry.replaceElement(element, vanilla -> (graphics, delta) -> {
				if (!ReplayScreen.isWatching()) {
					vanilla.extractRenderState(graphics, delta);
				}
			});
		}
		HudElementRegistry.replaceElement(VanillaHudElements.CROSSHAIR, vanilla -> (graphics, delta) -> {
			if (!ReplayScreen.hidesHand()) {
				vanilla.extractRenderState(graphics, delta);
			}
		});

		// In singleplayer the integrated server runs in this same game, so this callback also fires
		// for the server-side player; only react to the client side.
		AttackEntityCallback.EVENT.register((player, level, hand, entity, hitResult) -> {
			if (level.isClientSide() && player instanceof LocalPlayer local && local == Minecraft.getInstance().player) {
				recorder.onLocalAttack(local, entity);
			}
			return InteractionResult.PASS;
		});

		ClientPlayConnectionEvents.JOIN.register((handler, sender, client) -> {
			if (ReplayWorld.onJoin(client)) {
				// Ghosts and the camera in the replay world must never become a recorded "fight".
				recorder.setEnabled(false);
			} else if (client.hasSingleplayerServer()) {
				recorder.setEnabled(true);
			} else {
				recorder.setEnabled(false);
				chat(VertexCommands.prefix()
						.append(Component.literal("Fights aren't being recorded on this server. ").withStyle(ChatFormatting.GRAY))
						.append(VertexCommands.link("[Start recording]", "/vertex record on"))
						.append(Component.literal(" (replays stay on your computer)").withStyle(ChatFormatting.DARK_GRAY)));
			}
		});
		ClientPlayConnectionEvents.DISCONNECT.register((handler, client) -> {
			recorder.onDisconnect();
			ReplayWorld.onDisconnect();
		});
	}

	private static final Identifier LIBRARY_ICON = Identifier.fromNamespaceAndPath(MOD_ID, "icon/replays");

	/**
	 * A 20x20 icon button in the row of small square buttons (language, accessibility, ...), right
	 * after the last one. Positions are read from the buttons already on the screen, so it still lands
	 * in the right place when other mods add or move title-screen buttons.
	 */
	private static void addTitleScreenButton(Minecraft client, Screen screen, int width, int height) {
		List<AbstractWidget> widgets = Screens.getWidgets(screen);
		AbstractWidget last = null;
		for (AbstractWidget w : widgets) {
			if (w.getWidth() == 20 && w.getHeight() == 20 && w.getY() > height / 4) {
				if (last == null || w.getY() > last.getY() || (w.getY() == last.getY() && w.getX() > last.getX())) {
					last = w;
				}
			}
		}
		int x = last != null ? last.getX() + 24 : width / 2 + 104;
		int y = last != null ? last.getY() : height / 4 + 48;
		SpriteIconButton button = SpriteIconButton.builder(Component.literal("Vertex Replays"),
						b -> client.setScreenAndShow(new ReplayLibraryScreen(screen)), true)
				.sprite(LIBRARY_ICON, 15, 15)
				.size(20, 20)
				.withTootip()
				.build();
		button.setPosition(x, y);
		widgets.add(button);
	}

	/** Chat message right after a fight is saved: the result, the biggest takeaway, and links to the replay. */
	private static void announce(ReplayDocument doc) {
		int me = Math.max(0, doc.trace().recorderIndex());
		String opponent = doc.trace().participants().get(doc.trace().opponentOf(me)).name();
		SummaryBuilder.Summary s = SummaryBuilder.forParticipant(doc, me);

		MutableComponent line = VertexCommands.prefix()
				.append(Component.literal(s.result() + " vs " + opponent + " - " + s.statsLine() + " ").withStyle(ChatFormatting.WHITE))
				.append(VertexCommands.link("[Watch replay]", "/vertex watch " + doc.trace().id()))
				.append(Component.literal(" "))
				.append(VertexCommands.link("[Summary]", "/vertex summary " + doc.trace().id()));
		chat(line);
		if (s.biggestMistake() != null) {
			chat(Component.literal("   Biggest thing to fix: ").withStyle(ChatFormatting.GRAY)
					.append(Component.literal(s.biggestMistake()).withStyle(ChatFormatting.RED)));
		}
		if (s.bestMoment() != null) {
			chat(Component.literal("   Best moment: ").withStyle(ChatFormatting.GRAY)
					.append(Component.literal(s.bestMoment()).withStyle(ChatFormatting.GREEN)));
		}
	}

	private static void chat(Component message) {
		Minecraft mc = Minecraft.getInstance();
		if (mc.gui != null) {
			mc.gui.hud.getChat().addClientSystemMessage(message);
		}
	}
}
