package com.vertexpvp.vertex.gametest;

import com.vertexpvp.vertex.VertexClient;
import com.vertexpvp.vertex.core.ReplayDocument;
import com.vertexpvp.vertex.core.trace.CombatEvent;
import com.vertexpvp.vertex.core.trace.CombatEvent.HitKind;
import com.vertexpvp.vertex.core.trace.ParticipantFrame;
import com.vertexpvp.vertex.replay.ReplayLibraryScreen;
import com.vertexpvp.vertex.replay.ReplayScreen;
import com.vertexpvp.vertex.replay.ReplayWorld;
import net.fabricmc.fabric.api.client.gametest.v1.FabricClientGameTest;
import net.fabricmc.fabric.api.client.gametest.v1.context.ClientGameTestContext;
import net.fabricmc.fabric.api.client.gametest.v1.context.TestServerContext;
import net.fabricmc.fabric.api.client.gametest.v1.context.TestSingleplayerContext;
import net.fabricmc.fabric.api.client.gametest.v1.screenshot.TestScreenshotOptions;
import net.fabricmc.fabric.api.client.screen.v1.Screens;
import net.minecraft.SharedConstants;
import net.minecraft.client.gui.components.SpriteIconButton;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.world.item.Items;
import net.minecraft.core.BlockPos;
import net.minecraft.world.level.block.Blocks;
import org.lwjgl.glfw.GLFW;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.file.Path;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;
import java.util.stream.Collectors;

/**
 * End-to-end test in a real Minecraft client: build a platform, spawn a husk (doesn't burn in
 * daylight, has no AI so it stands still), land a normal hit, a jump crit and a sprint hit with real
 * key presses, then check that the recorder captured all three with the server's own
 * classification, and that analysis produced a saved replay with coaching and the arena's blocks.
 * Then, after leaving the world: the replay library lists it, and watching it opens a private
 * replay world with the platform rebuilt, both ghosts and the side camera; Esc leaves and deletes it.
 */
public class VertexClientGameTest implements FabricClientGameTest {

	private static final Logger LOG = LoggerFactory.getLogger("VertexGameTest");
	private static final double TARGET_Z = 2.7;

	@Override
	public void runTest(ClientGameTestContext context) {
		AtomicReference<ReplayDocument> saved = new AtomicReference<>();
		context.runOnClient(mc -> {
			// Longer than the scripted gaps between hits, so all three land in one fight.
			VertexClient.recorder().settings().idleTimeoutTicks = 120;
			VertexClient.recorder().addListener(saved::set);
		});

		try (TestSingleplayerContext world = context.worldBuilder().create()) {
			TestServerContext server = world.getServer();
			server.runCommand("difficulty easy");
			server.runCommand("gamemode survival @a");
			server.runCommand("time set day");
			server.runCommand("fill -4 120 -8 4 120 8 minecraft:stone");
			server.runCommand("summon minecraft:husk 0.5 121 " + TARGET_Z + " {NoAI:1b,PersistenceRequired:1b,Silent:1b}");
			// A wooden sword: a held item to record, and weak enough that the husk survives all three hits.
			server.runCommand("item replace entity @p weapon.mainhand with minecraft:wooden_sword");
			// Gear and an effect to record. Luck changes nothing about movement, so the fight is unchanged.
			server.runCommand("item replace entity @p armor.head with minecraft:iron_helmet");
			server.runCommand("item replace entity @p weapon.offhand with minecraft:totem_of_undying");
			server.runCommand("effect give @p minecraft:luck 600 1 true");
			server.runCommand("item replace entity @e[type=minecraft:husk] armor.head with minecraft:golden_helmet");
			context.waitTicks(10);

			check(context.computeOnClient(mc -> VertexClient.recorder().isEnabled()),
					"recording should switch on automatically in singleplayer");

			// 1. A normal, fully charged hit.
			standFacingTarget(context, server, 0.0);
			context.getInput().pressKey(options -> options.keyAttack);
			context.waitTicks(30);

			// 2. A crit: jump in place and swing on the way down.
			standFacingTarget(context, server, 0.0);
			context.getInput().holdKeyFor(options -> options.keyJump, 3);
			// Vanilla needs fallDistance > 0 at the click, and the click is processed before that tick's
			// movement - so swinging on the very first falling tick (the apex) is NOT a crit.
			context.waitFor(mc -> !mc.player.onGround() && mc.player.fallDistance > 0.1, 40);
			context.getInput().pressKey(options -> options.keyAttack);
			context.waitTicks(30);

			// 3. A sprint hit: run at the target and swing.
			standFacingTarget(context, server, -3.0);
			context.getInput().holdKey(options -> options.keyUp);
			context.getInput().holdKey(options -> options.keySprint);
			context.waitFor(mc -> mc.player.isSprinting() && mc.player.getZ() >= -0.3, 60);
			context.getInput().pressKey(options -> options.keyAttack);
			context.waitTicks(3);
			context.getInput().releaseKey(options -> options.keyUp);
			context.getInput().releaseKey(options -> options.keySprint);

			// The fight ends after 120 idle ticks; analysis then runs in the background.
			context.waitFor(mc -> saved.get() != null, 600);
			verifyRecording(context, saved.get());
		}
		// The test world is gone now, like after a server warps you away or you log off: the replay
		// has to work from the main menu.
		verifyLibraryAndReplayWorld(context, saved.get());
	}

	private static void standFacingTarget(ClientGameTestContext context, TestServerContext server, double z) {
		// Pitch 20 (looking slightly down) keeps the husk under the crosshair even at the top of a jump.
		server.runCommand("tp @p 0.5 121 " + z + " 0 20");
		server.runCommand("tp @e[type=minecraft:husk] 0.5 121 " + TARGET_Z + " 0 0");
		context.waitTicks(15);
	}

	private static void verifyRecording(ClientGameTestContext context, ReplayDocument doc) {
		check(doc != null, "a replay should be saved after the fight ends");
		var trace = doc.trace();
		List<CombatEvent> myHits = trace.events().stream()
				.filter(e -> e.type() == CombatEvent.Type.HIT && e.attacker() == 0)
				.toList();
		String described = myHits.stream()
				.map(e -> e.kind() + (e.serverConfirmed() ? "" : "?") + "(predicted " + e.predictedKind()
						+ ", t=" + e.tick() + ", range " + String.format("%.2f", e.range())
						+ (e.hasKnockback() ? String.format(", kb %.2f", e.horizontalKnockback()) : "") + ")")
				.collect(Collectors.joining(", "));
		LOG.info("Vertex test: recorded hits: {}", described);
		LOG.info("Vertex test: {} frames, {} interactions, {} explanations, end={}",
				trace.frames().size(), doc.interactions().size(), doc.explanations().size(), trace.endReason());

		check(trace.participants().get(1).entityType().equals("minecraft:husk"), "opponent should be the husk");
		check(!trace.participants().get(1).player(), "husk is not a player");
		check(myHits.size() >= 3, "expected 3 hits, got: " + described);
		List<HitKind> kinds = myHits.stream().map(CombatEvent::kind).toList();
		check(myHits.stream().allMatch(CombatEvent::serverConfirmed), "every hit should be classified by the server: " + described);
		check(kinds.contains(HitKind.STRONG), "expected a normal (strong) hit: " + described);
		check(kinds.contains(HitKind.CRIT), "expected a server-confirmed crit: " + described);
		check(kinds.contains(HitKind.SPRINT_HIT), "expected a server-confirmed sprint hit: " + described);
		check(myHits.stream().anyMatch(CombatEvent::hasKnockback), "knockback from the server should be attached: " + described);
		check(myHits.get(0).tick() > 0, "the approach before the first hit (pre-roll) should be recorded");
		check(!doc.interactions().isEmpty(), "detector should find interactions");
		check(doc.explanations().stream().anyMatch(e -> e.perspective() == 0), "explainer should coach the player");
		check(context.computeOnClient(mc -> VertexClient.recorder().store().load(trace.id()).isPresent()),
				"replay should be readable back from disk");
		check(trace.hasArena() && "minecraft:stone".equals(trace.arena().blockAt(0, 120, 0)),
				"the platform's blocks should be captured with the fight");
		LOG.info("Vertex test: arena has {} columns, {} solid blocks", trace.arena().columns().size(),
				trace.arena().nonAirBlocks());
		check("minecraft:wooden_sword".equals(trace.state(myHits.get(0).tick(), 0).mainHand()),
				"the held item should be recorded");
		check("minecraft:air".equals(trace.state(myHits.get(0).tick(), 1).mainHand()), "the husk holds nothing");

		ParticipantFrame me = trace.state(myHits.get(0).tick(), 0);
		ParticipantFrame husk = trace.state(myHits.get(0).tick(), 1);
		check(me.armor() != null && "minecraft:iron_helmet".equals(me.armor().get(0)), "the player's armour should be recorded");
		check("minecraft:totem_of_undying".equals(me.offHand()), "the off-hand item should be recorded");
		check(me.effects() != null && me.effects().stream().anyMatch(e -> e.id().equals("minecraft:luck") && e.amplifier() == 1),
				"the player's own effects should be recorded");
		check(me.selectedSlot() != null && me.pingMs() != null, "hotbar slot and ping should be recorded for the player");
		check(husk.armor() != null && "minecraft:golden_helmet".equals(husk.armor().get(0)),
				"the opponent's armour should be recorded");
		check(husk.effects() == null && husk.pingMs() == null && husk.selectedSlot() == null,
				"what the client can't know about the opponent should stay unknown");
		check(trace.context() != null && SharedConstants.getCurrentVersion().name().equals(trace.context().gameVersion())
				&& trace.context().singleplayer() && trace.context().server() == null,
				"the game version and singleplayer context should be recorded");
		LOG.info("Vertex test: context {}, player ping {} ms", trace.context(), me.pingMs());
	}

	private static void verifyLibraryAndReplayWorld(ClientGameTestContext context, ReplayDocument doc) {
		check(context.computeOnClient(mc -> mc.level == null), "should be back at the main menu");
		check(context.computeOnClient(mc -> mc.gui.screen() instanceof TitleScreen title
						&& Screens.getWidgets(title).stream().anyMatch(w -> w instanceof SpriteIconButton
						&& w.getMessage().getString().equals("Vertex Replays") && w.getWidth() == 20)),
				"the title screen should have the Vertex Replays icon button");
		screenshot(context, "vertex-title-screen-button");

		context.setScreen(() -> new ReplayLibraryScreen(new TitleScreen()));
		context.waitTicks(5);
		check(context.computeOnClient(mc -> mc.gui.screen() instanceof ReplayLibraryScreen library
				&& library.entryCount() >= 1), "the library should list the recording");
		screenshot(context, "vertex-library");

		// What the Watch button does when you're not in a world.
		context.runOnClient(mc -> ReplayWorld.watch(doc));
		context.waitForScreen(ReplayScreen.class);
		context.waitTicks(30);

		check(context.computeOnClient(mc -> mc.hasSingleplayerServer()
						&& mc.getSingleplayerServer().getWorldData().getLevelName().equals("Vertex Replay")),
				"the replay should open in its own replay world");
		check(context.computeOnClient(mc -> mc.level.getBlockState(new BlockPos(0, 120, 0)).is(Blocks.STONE)
						&& mc.level.getBlockState(new BlockPos(4, 120, 8)).is(Blocks.STONE)),
				"the recorded platform should be rebuilt in the replay world");
		check(context.computeOnClient(mc -> !VertexClient.recorder().isEnabled()), "no recording inside the replay world");
		check(context.computeOnClient(mc -> ReplayScreen.active() != null && ReplayScreen.active().ghostCount() == 2
						&& ReplayScreen.active().cursor() > 0),
				"replay should be playing with 2 ghosts");
		check(context.computeOnClient(mc -> ReplayScreen.active().cameraMode() == ReplayScreen.CameraMode.SIDE
						&& mc.getCameraEntity() != null && mc.getCameraEntity().getId() == -1_000_100),
				"the default camera should be the side view");
		screenshot(context, "vertex-replay-side-view");

		context.getInput().pressKey(GLFW.GLFW_KEY_C);
		context.waitTicks(5);
		check(context.computeOnClient(mc -> ReplayScreen.active().cameraMode() == ReplayScreen.CameraMode.FIRST_PERSON
				&& mc.getCameraEntity().getId() == -1_000_000), "C (default binding) switches to first person");
		check(context.computeOnClient(mc -> mc.level.getEntity(-1_000_100) == null), "side camera removed in other modes");

		// First person shows the fighter's held item and attack cooldown: pause 3 ticks after the first hit.
		int afterHit = doc.trace().events().stream().filter(e -> e.type() == CombatEvent.Type.HIT && e.attacker() == 0)
				.findFirst().orElseThrow().tick() + 3;
		context.runOnClient(mc -> ReplayScreen.active().pauseAt(afterHit));
		context.waitTicks(3);
		check(context.computeOnClient(mc -> mc.player.getMainHandItem().is(Items.WOODEN_SWORD)),
				"first person should hold the recorded wooden sword");
		check(context.computeOnClient(mc -> mc.player.getAttackStrengthScale(0f) < 0.9f),
				"the attack cooldown should be recharging just after a hit");
		check(context.computeOnClient(mc -> !ReplayScreen.hidesHand()), "hand and crosshair shown in first person");
		screenshot(context, "vertex-replay-first-person-cooldown");
		// Fully charged, before the first hit: vanilla raises the item back into view.
		context.runOnClient(mc -> ReplayScreen.active().pauseAt(afterHit - 10));
		context.waitTicks(10);
		check(context.computeOnClient(mc -> mc.player.getAttackStrengthScale(0f) >= 1f), "fully charged before the first hit");
		screenshot(context, "vertex-replay-first-person");

		context.getInput().pressKey(GLFW.GLFW_KEY_C);
		context.waitTicks(5);
		screenshot(context, "vertex-replay-third-person");

		context.getInput().pressKey(GLFW.GLFW_KEY_C);
		context.getInput().pressKey(GLFW.GLFW_KEY_N);
		context.getInput().pressKey(GLFW.GLFW_KEY_N);
		context.waitTicks(8);
		screenshot(context, "vertex-replay-annotation");

		// Leaving: what Esc does. Called through runOnClient (as Fabric's own world close does) rather
		// than by pressing Esc, because leaving blocks until the built-in server has stopped, and the
		// test harness only lets the server tick while the test thread is waiting in runOnClient.
		// In normal play the server runs freely, exactly like vanilla's pause-menu "Save and Quit".
		context.runOnClient(mc -> ReplayWorld.leave());
		context.waitForScreen(ReplayLibraryScreen.class);
		check(context.computeOnClient(mc -> mc.level == null && !ReplayWorld.isActive()), "should have left the replay world");
		check(context.computeOnClient(mc -> !mc.getLevelSource().levelExists(ReplayWorld.LEVEL_ID)),
				"the replay world should be deleted afterwards");
		context.setScreen(TitleScreen::new);
		context.waitTicks(3);
	}

	private static void screenshot(ClientGameTestContext context, String name) {
		Path path = context.takeScreenshot(TestScreenshotOptions.of(name));
		LOG.info("Vertex test: screenshot {}", path.toAbsolutePath());
	}

	private static void check(boolean condition, String message) {
		if (!condition) {
			throw new AssertionError("Vertex test failed: " + message);
		}
	}
}
