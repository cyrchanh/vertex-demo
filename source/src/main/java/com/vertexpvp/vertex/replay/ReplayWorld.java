package com.vertexpvp.vertex.replay;

import com.mojang.logging.LogUtils;
import com.vertexpvp.vertex.VertexClient;
import com.vertexpvp.vertex.core.ReplayDocument;
import com.vertexpvp.vertex.core.trace.Arena;
import com.vertexpvp.vertex.core.trace.ArenaColumn;
import com.vertexpvp.vertex.core.trace.DuelTrace;
import com.vertexpvp.vertex.core.trace.ParticipantFrame;
import com.vertexpvp.vertex.core.trace.TickFrame;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.ConfirmScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.commands.arguments.blocks.BlockStateParser;
import net.minecraft.core.BlockPos;
import net.minecraft.core.HolderLookup;
import net.minecraft.core.registries.Registries;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.Difficulty;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.LevelSettings;
import net.minecraft.world.level.WorldDataConfiguration;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.gamerules.GameRules;
import net.minecraft.world.level.levelgen.FlatLevelSource;
import net.minecraft.world.level.levelgen.WorldOptions;
import net.minecraft.world.level.levelgen.flat.FlatLevelGeneratorPresets;
import net.minecraft.world.level.levelgen.presets.WorldPresets;
import org.slf4j.Logger;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;
import java.util.HashMap;
import java.util.Map;
import java.util.Set;
import java.util.stream.Stream;

/**
 * Watching a replay the way Replay Mod does: in a private world of its own, so it works after the
 * server has moved you elsewhere, after you've logged out, or from the main menu.
 *
 * <ol>
 *     <li>Leave whatever world you're in.</li>
 *     <li>Create a fresh, empty void world (adventure mode, invisible and flying, peaceful, time frozen at noon).</li>
 *     <li>Rebuild the recorded arena block by block on the built-in server. Recordings from before
 *     arenas were captured get a plain floor instead.</li>
 *     <li>Once the blocks have reached the client, open {@link ReplayScreen}. Closing it leaves the
 *     world and deletes it again.</li>
 * </ol>
 *
 * The world lives in the normal saves folder as {@value #LEVEL_ID} only while you're watching.
 * Recording is off inside it. Main thread only, except {@link #build}, which runs on the server thread.
 */
public final class ReplayWorld {

	public static final String LEVEL_ID = "vertex-replay";

	private static final Logger LOG = LogUtils.getLogger();
	private static final int CAMERA_HEIGHT = 30;
	private static final int MAX_WAIT_TICKS = 200;

	/**
	 * SWITCHING = leaving a world in order to create the replay world; LEAVING = leaving the replay
	 * world. Both wait for the old world to be fully gone (its built-in server stopped) before going
	 * on, because a disconnect doesn't always finish within the call.
	 */
	private enum Stage { IDLE, SWITCHING, CREATING, BUILDING, WAITING, WATCHING, LEAVING }

	private static Stage stage = Stage.IDLE;
	private static ReplayDocument doc;
	private static int waited;

	private ReplayWorld() {
	}

	/** True from the moment a replay world is requested until it's been left again. */
	public static boolean isActive() {
		return stage != Stage.IDLE;
	}

	/**
	 * Watch a replay, asking first if that means leaving the world or server you're in. {@code back}
	 * is shown if you say no.
	 */
	public static void requestWatch(ReplayDocument replay, Screen back) {
		Minecraft mc = Minecraft.getInstance();
		if (mc.level == null || isActive()) {
			watch(replay);
			return;
		}
		String where = mc.hasSingleplayerServer() ? "This world will be saved and closed." : "You'll be disconnected from this server.";
		mc.setScreenAndShow(new ConfirmScreen(yes -> {
			if (yes) {
				watch(replay);
			} else {
				mc.setScreenAndShow(back);
			}
		}, Component.literal("Leave to watch this replay?"),
				Component.literal(where + " Replays play in a private replay world so they always look right, "
						+ "even after the server has moved you. You'll return to the main menu when you're done.")));
	}

	/** Leave the current world (if any) and open the replay in a fresh replay world. */
	public static void watch(ReplayDocument replay) {
		Minecraft mc = Minecraft.getInstance();
		doc = replay;
		waited = 0;
		stage = Stage.SWITCHING;
		if (mc.level != null) {
			mc.disconnectFromWorld(Component.literal("Opening replay"));
		}
		if (worldGone(mc)) {
			create(mc);
		}
		// Otherwise tick() creates it once the old world has finished closing.
	}

	private static boolean worldGone(Minecraft mc) {
		return mc.level == null && !mc.hasSingleplayerServer();
	}

	private static void create(Minecraft mc) {
		deleteSave(mc);
		stage = Stage.CREATING;

		LevelSettings settings = new LevelSettings("Vertex Replay", GameType.ADVENTURE,
				new LevelSettings.DifficultySettings(Difficulty.PEACEFUL, false, true), true, WorldDataConfiguration.DEFAULT);
		WorldOptions options = new WorldOptions(0L, false, false);
		mc.createWorldOpenFlows().createFreshLevel(LEVEL_ID, settings, options, registries -> {
			var voidPreset = registries.lookupOrThrow(Registries.FLAT_LEVEL_GENERATOR_PRESET)
					.getOrThrow(FlatLevelGeneratorPresets.THE_VOID).value();
			return WorldPresets.createNormalWorldDimensions(registries)
					.replaceOverworldGenerator(registries, new FlatLevelSource(voidPreset.settings()));
		}, new ReplayLibraryScreen(new TitleScreen()));
	}

	/** Called when this client joins any world. Returns true if it's the replay world being set up. */
	public static boolean onJoin(Minecraft mc) {
		if (stage != Stage.CREATING) {
			return false;
		}
		MinecraftServer server = mc.getSingleplayerServer();
		if (server == null) {
			stage = Stage.IDLE;
			return false;
		}
		stage = Stage.BUILDING;
		ReplayDocument replay = doc;
		server.execute(() -> {
			try {
				build(server, replay.trace());
			} catch (RuntimeException e) {
				LOG.error("Vertex: couldn't rebuild the replay arena", e);
			}
			mc.execute(() -> {
				if (stage == Stage.BUILDING) {
					stage = Stage.WAITING;
				}
			});
		});
		return true;
	}

	/** Each client tick: finish switching/leaving, and open the viewer once the arena has reached the client. */
	public static void tick(Minecraft mc) {
		if (stage == Stage.SWITCHING && worldGone(mc)) {
			create(mc);
			return;
		}
		if (stage == Stage.LEAVING && worldGone(mc)) {
			finishLeaving(mc);
			return;
		}
		if (stage != Stage.WAITING || mc.level == null || mc.player == null) {
			return;
		}
		waited++;
		ParticipantFrame f = doc.trace().state(0, 0);
		int cx = (int) Math.floor(f.x()) >> 4;
		int cz = (int) Math.floor(f.z()) >> 4;
		boolean arrived = mc.level.hasChunk(cx, cz) && Math.abs(mc.player.getX() - f.x()) < 64
				&& Math.abs(mc.player.getZ() - f.z()) < 64;
		if ((arrived && waited >= 20) || waited >= MAX_WAIT_TICKS) {
			stage = Stage.WATCHING;
			mc.setScreenAndShow(new ReplayScreen(doc));
		}
	}

	/** Leave the replay world, delete it, and show the replay library. */
	public static void leave() {
		Minecraft mc = Minecraft.getInstance();
		stage = Stage.LEAVING;
		doc = null;
		if (mc.level != null) {
			mc.disconnectFromWorld(Component.literal("Leaving replay"));
		}
		if (worldGone(mc)) {
			finishLeaving(mc);
		}
	}

	private static void finishLeaving(Minecraft mc) {
		stage = Stage.IDLE;
		deleteSave(mc);
		mc.setScreenAndShow(new ReplayLibraryScreen(new TitleScreen()));
	}

	/** The connection dropped some other way (crash, quit to title): forget the replay world. */
	public static void onDisconnect() {
		if (stage == Stage.WATCHING || stage == Stage.WAITING || stage == Stage.BUILDING) {
			stage = Stage.IDLE;
			doc = null;
		}
	}

	// ------------------------------------------------------------------ server thread

	private static void build(MinecraftServer server, DuelTrace trace) {
		ServerLevel level = server.overworld();
		GameRules rules = server.getGameRules();
		rules.set(GameRules.ADVANCE_TIME, false, server);
		rules.set(GameRules.ADVANCE_WEATHER, false, server);
		rules.set(GameRules.SPAWN_MOBS, false, server);
		server.getCommands().performPrefixedCommand(server.createCommandSourceStack().withSuppressedOutput(), "time set noon");

		int placed = trace.hasArena() ? placeArena(server, level, trace.arena()) : 0;
		if (placed == 0) {
			placeFloor(level, trace);
		}

		ParticipantFrame a = trace.state(0, 0);
		ParticipantFrame b = trace.state(0, trace.opponentOf(0));
		double x = (a.x() + b.x()) / 2.0;
		double z = (a.z() + b.z()) / 2.0;
		double y = Math.max(a.y(), b.y()) + CAMERA_HEIGHT;
		// The viewer looks through its own camera, so you just need to be close enough for the arena's
		// chunks to load: high above, out of shot, invisible and hovering. Adventure mode rather than
		// spectator, because vanilla only draws the first-person hand, held item and attack cooldown
		// outside spectator mode, and the first-person replay borrows those.
		for (ServerPlayer player : server.getPlayerList().getPlayers()) {
			player.getAbilities().mayfly = true;
			player.getAbilities().flying = true;
			player.getAbilities().invulnerable = true;
			player.onUpdateAbilities();
			player.setInvisible(true);
			player.teleportTo(level, x, y, z, Set.of(), 0f, 90f, false);
		}
		LOG.info("Vertex: replay world ready ({} arena blocks)", placed);
	}

	private static int placeArena(MinecraftServer server, ServerLevel level, Arena arena) {
		HolderLookup<Block> blocks = server.registryAccess().lookupOrThrow(Registries.BLOCK);
		Map<String, BlockState> parsed = new HashMap<>();
		BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
		int flags = Block.UPDATE_CLIENTS | Block.UPDATE_KNOWN_SHAPE | Block.UPDATE_SUPPRESS_DROPS;
		int placed = 0;
		for (ArenaColumn column : arena.columns()) {
			String[] states = column.decode();
			for (int y = column.minY(); y < column.minY() + column.height(); y++) {
				for (int lz = 0; lz < 16; lz++) {
					for (int lx = 0; lx < 16; lx++) {
						String name = states[ArenaColumn.indexOf(lx, y, lz, column.minY())];
						if (ArenaColumn.AIR.equals(name)) {
							continue;
						}
						BlockState state = parsed.computeIfAbsent(name, n -> parse(blocks, n));
						if (state.isAir()) {
							continue;
						}
						pos.set((column.chunkX() << 4) + lx, y, (column.chunkZ() << 4) + lz);
						level.setBlock(pos, state, flags);
						placed++;
					}
				}
			}
		}
		return placed;
	}

	private static BlockState parse(HolderLookup<Block> blocks, String name) {
		try {
			return BlockStateParser.parseForBlock(blocks, name, false).blockState();
		} catch (Exception e) {
			// A block from a mod or version this game doesn't have.
			return Blocks.AIR.defaultBlockState();
		}
	}

	/** No recorded arena (older replay): a plain floor under the fight so the fighters aren't in the void. */
	private static void placeFloor(ServerLevel level, DuelTrace trace) {
		double minX = Double.MAX_VALUE, maxX = -Double.MAX_VALUE, minZ = Double.MAX_VALUE, maxZ = -Double.MAX_VALUE;
		double floorY = Double.MAX_VALUE;
		for (TickFrame frame : trace.frames()) {
			for (ParticipantFrame f : frame.participants()) {
				minX = Math.min(minX, f.x());
				maxX = Math.max(maxX, f.x());
				minZ = Math.min(minZ, f.z());
				maxZ = Math.max(maxZ, f.z());
				floorY = Math.min(floorY, f.y());
			}
		}
		if (floorY == Double.MAX_VALUE) {
			return;
		}
		int y = (int) Math.floor(floorY) - 1;
		BlockState floor = Blocks.SMOOTH_STONE.defaultBlockState();
		BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
		for (int x = (int) Math.floor(minX) - 6; x <= (int) Math.floor(maxX) + 6; x++) {
			for (int z = (int) Math.floor(minZ) - 6; z <= (int) Math.floor(maxZ) + 6; z++) {
				level.setBlock(pos.set(x, y, z), floor, Block.UPDATE_CLIENTS);
			}
		}
	}

	// ------------------------------------------------------------------ save folder

	private static void deleteSave(Minecraft mc) {
		Path dir = mc.getLevelSource().getBaseDir().resolve(LEVEL_ID);
		if (!Files.exists(dir)) {
			return;
		}
		try (Stream<Path> files = Files.walk(dir)) {
			for (Path p : files.sorted(Comparator.reverseOrder()).toList()) {
				Files.deleteIfExists(p);
			}
		} catch (IOException e) {
			LOG.warn("Vertex: couldn't delete the old replay world at {}", dir, e);
		}
	}

	static void closeViewer() {
		VertexClient.runNextTick(ReplayWorld::leave);
	}
}
