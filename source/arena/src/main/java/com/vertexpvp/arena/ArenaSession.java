package com.vertexpvp.arena;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.minecraft.network.chat.Component;
import net.minecraft.network.protocol.game.ClientboundSetHeldSlotPacket;
import net.minecraft.network.protocol.game.ClientboundSetTitleTextPacket;
import net.minecraft.network.protocol.game.ClientboundSetTitlesAnimationPacket;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.util.RandomSource;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.Relative;
import net.minecraft.world.entity.player.Input;
import net.minecraft.world.level.GameType;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.Vec3;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * Lockstep control of two fighters from one local client (the Python environment), as JSON lines.
 *
 * <pre>
 * -> {"cmd":"hello"}                                   <- {"type":"hello", ...}
 * -> {"cmd":"reset", "seed":1, "max_ticks":1200, "min_dist":6, "max_dist":10, "countdown":60}
 *                                                      <- {"type":"obs", ...}  (after the countdown)
 * -> {"cmd":"step", "actions":[{...},{...}], "repeat":2} <- {"type":"obs", ...}   (after `repeat` ticks)
 * -> {"cmd":"realtime", "on":true}                     <- {"type":"ok"}  (20 ticks/s, to watch)
 * -> {"cmd":"duel", "on":true}                         <- {"type":"ok", "human":"name"}
 * -> {"cmd":"close"}
 * </pre>
 *
 * Normally both fighters are bots. In a duel, fighter 1 is the first person on the server (not a
 * bot) and bot B waits in spectator mode; the person plays with their own client, so their action in
 * a step is ignored, and their keys (the movement keys their client sends, and swings) are reported
 * in the observation so their play can be learned from.
 *
 * While a client is connected, the server thread waits at the start of every tick for the next
 * command, so the game advances exactly as fast as the trainer asks. Between commands the server
 * runs in sprint mode (as fast as it can), or at 20 ticks per second in realtime mode.
 */
final class ArenaSession {

	private static final Logger LOG = LoggerFactory.getLogger("vertex-arena");
	private static final Gson GSON = new Gson();
	/** If the trainer goes quiet this long, let the server run on its own again. */
	private static final long COMMAND_TIMEOUT_SECONDS = 120;
	/** Highest damage a full-charge hit with the kit's sword does without a crit (before armour). */
	private static final float MAX_NON_CRIT_DAMAGE = 7.01f;

	private final MinecraftServer server;
	private final BotPlayer[] bots;
	/** Who fights: both bots, or bot A and a person (duel). */
	private final ServerPlayer[] fighters = new ServerPlayer[2];
	private final BlockingQueue<JsonObject> commands = new LinkedBlockingQueue<>();
	private volatile PrintWriter out;
	private volatile boolean connected;

	private RandomSource random = RandomSource.create();
	private boolean stepping;
	private int repeatLeft;
	private int episodeTick;
	private int maxTicks = 1200;
	private int winner = -1;
	private boolean done = true;
	private int countdownLeft;
	private final Vec3[] startPosition = new Vec3[2];
	private final Vec3[] previousPosition = new Vec3[2];
	private final List<JsonObject> events = new ArrayList<>();

	// A person's swings and sprint, tracked from what the server sees (bots report their own).
	private final float[] chargeAfterLastTick = new float[2];
	private final boolean[] sprintingAfterLastTick = new boolean[2];
	private final boolean[] hitThisTick = new boolean[2];

	ArenaSession(MinecraftServer server, BotPlayer[] bots) {
		this.server = server;
		this.bots = bots;
		fighters[0] = bots[0];
		fighters[1] = bots[1];
	}

	/** Accepts one trainer at a time on 127.0.0.1:{@code port}, on a background thread. */
	void listen(int port) {
		Thread thread = new Thread(() -> {
			try (ServerSocket socket = new ServerSocket(port, 1, InetAddress.getLoopbackAddress())) {
				LOG.info("Vertex arena: waiting for a trainer on 127.0.0.1:{}", port);
				while (!socket.isClosed()) {
					try (Socket client = socket.accept()) {
						client.setTcpNoDelay(true);
						serve(client);
					} catch (IOException e) {
						LOG.warn("Vertex arena: trainer connection ended: {}", e.getMessage());
					} finally {
						disconnect();
					}
				}
			} catch (IOException e) {
				LOG.error("Vertex arena: could not listen on port {}", port, e);
			}
		}, "vertex-arena-socket");
		thread.setDaemon(true);
		thread.start();
	}

	private void serve(Socket client) throws IOException {
		BufferedReader in = new BufferedReader(new InputStreamReader(client.getInputStream(), StandardCharsets.UTF_8));
		out = new PrintWriter(new OutputStreamWriter(client.getOutputStream(), StandardCharsets.UTF_8), false);
		commands.clear();
		realtime = false;   // each trainer starts at full speed
		connected = true;
		LOG.info("Vertex arena: trainer connected");
		String line;
		while ((line = in.readLine()) != null) {
			if (!line.isBlank()) {
				commands.add(JsonParser.parseString(line).getAsJsonObject());
			}
		}
	}

	private void disconnect() {
		connected = false;
		out = null;
		commands.add(close());
		LOG.info("Vertex arena: trainer disconnected");
	}

	private static JsonObject close() {
		JsonObject o = new JsonObject();
		o.addProperty("cmd", "close");
		return o;
	}

	// ---- server thread ----

	/** Start of a server tick: wait for the trainer's next instruction. */
	void beforeTick() {
		if (isDuel() && fighters[1].hasDisconnected()) {
			endDuel();
		}
		if (isDuel()) {
			holdSwordSlot(fighters[1]);
		}
		if (countdownLeft > 0) {
			tickCountdown();
			if (countdownLeft > 0) {
				return;   // the countdown runs on its own; the trainer is waiting for the reset's reply
			}
			startFight();
			send(observation());
		}
		if (stepping && repeatLeft > 0) {
			repeatLeft--;
			return;
		}
		if (!connected && commands.isEmpty()) {
			return;
		}
		if (!server.tickRateManager().isSprinting() && connected && !realtime) {
			server.tickRateManager().requestGameToSprint(Integer.MAX_VALUE);
		}
		while (true) {
			JsonObject cmd;
			try {
				cmd = commands.poll(COMMAND_TIMEOUT_SECONDS, TimeUnit.SECONDS);
			} catch (InterruptedException e) {
				Thread.currentThread().interrupt();
				return;
			}
			if (cmd == null) {
				LOG.warn("Vertex arena: no command for {} s, running freely", COMMAND_TIMEOUT_SECONDS);
				return;
			}
			switch (cmd.get("cmd").getAsString()) {
				case "hello" -> send(hello());
				case "reset" -> {
					reset(cmd);
					if (countdownLeft > 0) {
						return;   // reply when the countdown ends
					}
					send(observation());
				}
				case "realtime" -> {
					setRealtime(cmd.get("on").getAsBoolean());
					send(ok());
				}
				case "duel" -> send(setDuel(cmd.get("on").getAsBoolean()));
				case "step" -> {
					if (done) {
						send(error("episode is over: send reset"));
						continue;
					}
					JsonArray actions = cmd.getAsJsonArray("actions");
					for (int i = 0; i < 2; i++) {
						if (fighters[i] instanceof BotPlayer bot) {
							bot.setAction(BotAction.fromJson(actions.get(i).getAsJsonObject()));
						}
					}
					repeatLeft = Math.max(1, cmd.has("repeat") ? cmd.get("repeat").getAsInt() : 1) - 1;
					stepping = true;
					return;   // let this tick run
				}
				case "close" -> {
					stepping = false;
					repeatLeft = 0;
					countdownLeft = 0;
					done = true;
					for (BotPlayer bot : bots) {
						bot.setAction(BotAction.IDLE);
					}
					if (isDuel()) {
						endDuel();
					}
					server.tickRateManager().stopSprinting();   // idle at normal speed until the next trainer
					return;
				}
				default -> send(error("unknown command"));
			}
		}
	}

	/** End of a server tick: report when the step is complete. */
	void afterTick() {
		if (!stepping) {
			return;
		}
		settlePendingHits();
		episodeTick++;
		for (int i = 0; i < 2; i++) {
			boolean swung;
			boolean hit;
			float charge;
			if (fighters[i] instanceof BotPlayer bot) {
				swung = bot.swungLastTick;
				hit = bot.attackedLastTick;
				charge = bot.chargeAtLastSwing;
			} else {
				// A person's swing resets their attack cooldown (ServerPlayer.swing, from their client's
				// swing packet); the charge only ever rises otherwise. The sword stays selected (holdSwordSlot).
				float now = fighters[i].getAttackStrengthScale(0f);
				swung = now < chargeAfterLastTick[i] - 1e-4f;
				hit = hitThisTick[i];
				charge = chargeAfterLastTick[i];
			}
			if (swung) {
				JsonObject e = new JsonObject();
				e.addProperty("type", "swing");
				e.addProperty("tick", episodeTick);
				e.addProperty("bot", i);
				e.addProperty("hit", hit);
				e.addProperty("charge", charge);
				events.add(e);
			}
		}
		if (winner >= 0 || episodeTick >= maxTicks) {
			done = true;
			repeatLeft = 0;
			if (realtime) {
				announce();
			}
		}
		if (repeatLeft == 0) {
			stepping = false;
			send(observation());
			events.clear();
		}
		trackFighters();   // after the observation: its velocities are measured from the last tick
	}

	private void trackFighters() {
		for (int i = 0; i < 2; i++) {
			previousPosition[i] = fighters[i].position();
			healthAtTickStart[i] = fighters[i].getHealth() + fighters[i].getAbsorptionAmount();
			chargeAfterLastTick[i] = fighters[i].getAttackStrengthScale(0f);
			sprintingAfterLastTick[i] = fighters[i].isSprinting();
			hitThisTick[i] = false;
		}
	}

	/**
	 * A fighter took damage from the other: one hit event, with the attacker's crit and sprint flags.
	 * {@code rawDamage} is before armour and Protection (Fabric's event gives no later value, and fires
	 * before the health change lands), so the damage actually taken is filled in at the end of the
	 * tick from health and absorption. Hurt invulnerability allows at most one hit per victim per tick.
	 */
	void onDamage(LivingEntity victim, LivingEntity attacker, float rawDamage) {
		int v = indexOf(victim);
		int a = indexOf(attacker);
		if (v < 0 || a < 0 || !fightLive()) {
			return;
		}
		hitThisTick[a] = true;
		pendingHits[v] = hitEvent(a, v, 0f, rawDamage, false);
	}

	private final JsonObject[] pendingHits = new JsonObject[2];

	private void settlePendingHits() {
		for (int v = 0; v < 2; v++) {
			if (pendingHits[v] != null) {
				float now = fighters[v].getHealth() + fighters[v].getAbsorptionAmount();
				pendingHits[v].addProperty("damage", Math.max(0f, healthAtTickStart[v] - now));
				pendingHits[v] = null;
			}
		}
	}

	/** A fighter would die: the fight is over. Returns false to cancel the death and keep them. */
	boolean onDeath(LivingEntity entity, LivingEntity attacker, float rawDamage) {
		int i = indexOf(entity);
		if (i < 0) {
			return true;
		}
		if (fightLive() && indexOf(attacker) == 1 - i) {
			pendingHits[i] = null;
			hitThisTick[1 - i] = true;
			hitEvent(1 - i, i, healthAtTickStart[i], rawDamage, true);   // the killing blow takes what was left
		}
		winner = 1 - i;
		entity.setHealth(entity.getMaxHealth());
		return false;
	}

	private JsonObject hitEvent(int attacker, int victim, float damage, float rawDamage, boolean lethal) {
		JsonObject e = new JsonObject();
		e.addProperty("type", "hit");
		e.addProperty("tick", episodeTick + 1);
		e.addProperty("attacker", attacker);
		e.addProperty("victim", victim);
		e.addProperty("damage", damage);
		e.addProperty("raw_damage", rawDamage);
		if (fighters[attacker] instanceof BotPlayer bot) {
			e.addProperty("crit", bot.critAttackLastTick);
			e.addProperty("sprint_hit", bot.sprintAttackLastTick);
		} else {
			// A person's attack is handled from their packet, before we can look: a crit is the only way
			// past a full-charge non-crit's damage; a sprint hit ends their sprint (knockback attack).
			e.addProperty("crit", rawDamage > MAX_NON_CRIT_DAMAGE);
			e.addProperty("sprint_hit", sprintingAfterLastTick[attacker] && !fighters[attacker].isSprinting());
		}
		e.addProperty("lethal", lethal);
		events.add(e);
		return e;
	}

	/** Health and absorption at the end of the last tick: hits since then are measured from it. */
	private final float[] healthAtTickStart = new float[2];

	/**
	 * A fight is on (not only while a step runs): a person's attack arrives as a packet between ticks,
	 * while the server waits for the trainer's next step, and must still count.
	 */
	private boolean fightLive() {
		return !done && countdownLeft == 0;
	}

	private int indexOf(Object entity) {
		return entity == fighters[0] ? 0 : entity == fighters[1] ? 1 : -1;
	}

	private void reset(JsonObject cmd) {
		if (cmd.has("seed")) {
			random = RandomSource.create(cmd.get("seed").getAsLong());
		}
		maxTicks = cmd.has("max_ticks") ? cmd.get("max_ticks").getAsInt() : 1200;
		Vec3[] starts = ArenaLayout.starts(random,
				cmd.has("min_dist") ? cmd.get("min_dist").getAsDouble() : ArenaLayout.MIN_START,
				cmd.has("max_dist") ? cmd.get("max_dist").getAsDouble() : ArenaLayout.MAX_START);
		for (int i = 0; i < 2; i++) {
			ServerPlayer f = fighters[i];
			f.setGameMode(GameType.ADVENTURE);   // can fight but can't break the arena
			ArenaLayout.equipKit(f);
			f.removeAllEffects();
			f.setHealth(f.getMaxHealth());
			f.setAbsorptionAmount(0f);
			f.getFoodData().setFoodLevel(20);
			f.getFoodData().setSaturation(5f);
			f.resetFallDistance();
			f.clearFire();
			f.setSprinting(false);
			f.setShiftKeyDown(false);
			f.setDeltaMovement(Vec3.ZERO);
			f.hurtTime = 0;
			f.invulnerableTime = 0;
			float yaw = ArenaLayout.yawTowards(starts[i], starts[1 - i]);
			if (f instanceof BotPlayer bot) {
				bot.resetCombatState();
				bot.snapTo(starts[i].x, starts[i].y, starts[i].z, yaw, 0f);
				bot.setYHeadRot(yaw);
				bot.level().getChunkSource().move(bot);
			} else {
				f.resetAttackStrengthTicker();
				f.teleportTo(level(), starts[i].x, starts[i].y, starts[i].z, Set.of(), yaw, 0f, true);
			}
			startPosition[i] = starts[i];
		}
		episodeTick = 0;
		winner = -1;
		done = false;
		stepping = false;
		repeatLeft = 0;
		events.clear();
		trackFighters();
		countdownLeft = cmd.has("countdown") ? Math.max(0, cmd.get("countdown").getAsInt()) : 0;
		if (countdownLeft > 0) {
			for (ServerPlayer f : fighters) {
				f.setInvulnerable(true);
			}
		}
	}

	/**
	 * One countdown tick: titles at 3, 2, 1, and a person is held on their start spot (they can look
	 * around). Bots stand still. Nobody can be hurt.
	 */
	private void tickCountdown() {
		if (countdownLeft % 20 == 0) {
			title(String.valueOf(countdownLeft / 20), 0, 25, 0);
		}
		for (int i = 0; i < 2; i++) {
			if (!(fighters[i] instanceof BotPlayer)) {
				Vec3 p = startPosition[i];
				fighters[i].teleportTo(level(), p.x, p.y, p.z, Relative.ROTATION, 0f, 0f, false);
			}
		}
		countdownLeft--;
	}

	private void startFight() {
		for (ServerPlayer f : fighters) {
			f.setInvulnerable(false);
			f.setDeltaMovement(Vec3.ZERO);
		}
		title("Fight!", 0, 15, 5);
		episodeTick = 0;
		events.clear();
		trackFighters();
	}

	private void title(String text, int fadeIn, int stay, int fadeOut) {
		server.getPlayerList().broadcastAll(new ClientboundSetTitlesAnimationPacket(fadeIn, stay, fadeOut));
		server.getPlayerList().broadcastAll(new ClientboundSetTitleTextPacket(Component.literal(text)));
	}

	// ---- duel ----

	private boolean isDuel() {
		return !(fighters[1] instanceof BotPlayer);
	}

	private JsonObject setDuel(boolean on) {
		if (!on) {
			if (isDuel()) {
				endDuel();
			}
			return ok();
		}
		ServerPlayer human = server.getPlayerList().getPlayers().stream()
				.filter(p -> !(p instanceof BotPlayer)).findFirst().orElse(null);
		if (human == null) {
			return error("nobody to duel: join the arena server in Minecraft first");
		}
		fighters[1] = human;
		bots[1].setAction(BotAction.IDLE);
		bots[1].setGameMode(GameType.SPECTATOR);   // out of the way until the duel ends
		bots[1].snapTo(0.5, ArenaLayout.FLOOR_Y + 12, 0.5, 0f, 90f);
		done = true;
		LOG.info("Vertex arena: duel with {}", human.getPlainTextName());
		JsonObject o = ok();
		o.addProperty("human", human.getPlainTextName());
		return o;
	}

	private void endDuel() {
		ServerPlayer human = fighters[1];
		fighters[1] = bots[1];
		bots[1].setGameMode(GameType.ADVENTURE);
		countdownLeft = 0;
		if (!done) {
			done = true;
			stepping = false;
			repeatLeft = 0;
			send(observation());   // the trainer sees the fight end (no winner)
		}
		if (!human.hasDisconnected()) {
			human.setInvulnerable(false);
			human.setGameMode(GameType.SPECTATOR);
		}
		LOG.info("Vertex arena: duel ended");
	}

	/** The kit is one sword: keep it in hand, so a person's swings can be read from their cooldown. */
	private static void holdSwordSlot(ServerPlayer player) {
		if (player.getInventory().getSelectedSlot() != 0) {
			player.getInventory().setSelectedSlot(0);
			player.connection.send(new ClientboundSetHeldSlotPacket(0));
		}
	}

	/** For spectators: the result of a fight in chat. */
	private void announce() {
		String result = winner < 0
				? String.format("Time! Draw after %.1f s", episodeTick / 20.0)
				: String.format("%s wins in %.1f s with %.1f HP left", fighters[winner].getPlainTextName(),
						episodeTick / 20.0, fighters[winner].getHealth());
		server.getPlayerList().broadcastSystemMessage(Component.literal(result), false);
	}

	private volatile boolean realtime;

	private void setRealtime(boolean on) {
		realtime = on;
		if (on) {
			server.tickRateManager().stopSprinting();
		} else {
			server.tickRateManager().requestGameToSprint(Integer.MAX_VALUE);
		}
	}

	private JsonObject observation() {
		JsonObject o = new JsonObject();
		o.addProperty("type", "obs");
		o.addProperty("tick", episodeTick);
		o.addProperty("done", done);
		o.addProperty("winner", winner);
		o.addProperty("duel", isDuel());
		JsonArray list = new JsonArray();
		for (int i = 0; i < 2; i++) {
			list.add(state(i));
		}
		o.add("bots", list);
		JsonArray ev = new JsonArray();
		events.forEach(ev::add);
		o.add("events", ev);
		return o;
	}

	/** Everything the server knows about one fighter. The trainer decides what each policy may see. */
	private JsonObject state(int i) {
		ServerPlayer b = fighters[i];
		Vec3 pos = b.position();
		Vec3 prev = previousPosition[i] == null ? pos : previousPosition[i];
		JsonObject s = new JsonObject();
		s.addProperty("human", !(b instanceof BotPlayer));
		s.addProperty("x", pos.x);
		s.addProperty("y", pos.y);
		s.addProperty("z", pos.z);
		s.addProperty("vx", pos.x - prev.x);
		s.addProperty("vy", pos.y - prev.y);
		s.addProperty("vz", pos.z - prev.z);
		s.addProperty("yaw", b.getYRot());
		s.addProperty("pitch", b.getXRot());
		s.addProperty("on_ground", b.onGround());
		s.addProperty("sprinting", b.isSprinting());
		s.addProperty("sneaking", b.isShiftKeyDown());
		s.addProperty("health", b.getHealth());
		s.addProperty("absorption", b.getAbsorptionAmount());
		s.addProperty("hurt_time", b.hurtTime);
		s.addProperty("invulnerable_time", b.invulnerableTime);
		s.addProperty("charge", b.getAttackStrengthScale(0.5f));
		s.addProperty("fall_distance", b.fallDistance);
		s.addProperty("horizontal_collision", b.horizontalCollision);
		s.addProperty("miss_cooldown", b instanceof BotPlayer bot ? bot.missCooldown() : 0);
		s.addProperty("food", b.getFoodData().getFoodLevel());
		ServerPlayer op = fighters[1 - i];
		s.addProperty("aim_on_target", BotPlayer.crosshair(b) instanceof EntityHitResult hit && hit.getEntity() == op);
		s.addProperty("reach_distance", BotPlayer.reachDistance(b, op));
		s.add("keys", keys(b));
		return s;
	}

	/** Movement keys held: a bot's action, or what a person's client last sent (ServerboundPlayerInputPacket). */
	private static JsonObject keys(ServerPlayer p) {
		JsonObject k = new JsonObject();
		if (p instanceof BotPlayer bot) {
			BotAction a = bot.action();
			k.addProperty("forward", a.forward());
			k.addProperty("back", a.back());
			k.addProperty("left", a.left());
			k.addProperty("right", a.right());
			k.addProperty("jump", a.jump());
			k.addProperty("sneak", a.sneak());
			k.addProperty("sprint", a.sprint());
		} else {
			Input in = p.getLastClientInput();
			k.addProperty("forward", in.forward());
			k.addProperty("back", in.backward());
			k.addProperty("left", in.left());
			k.addProperty("right", in.right());
			k.addProperty("jump", in.jump());
			k.addProperty("sneak", in.shift());
			k.addProperty("sprint", in.sprint());
		}
		return k;
	}

	private JsonObject hello() {
		JsonObject o = new JsonObject();
		o.addProperty("type", "hello");
		o.addProperty("protocol", 3);
		o.addProperty("arena_half", ArenaLayout.HALF);
		o.addProperty("floor_y", ArenaLayout.FLOOR_Y);
		o.addProperty("max_turn_per_tick", BotPlayer.MAX_TURN_PER_TICK);
		o.addProperty("kit", "full diamond armour, Protection III; unenchanted diamond sword");
		return o;
	}

	private static JsonObject ok() {
		JsonObject o = new JsonObject();
		o.addProperty("type", "ok");
		return o;
	}

	private static JsonObject error(String message) {
		JsonObject o = new JsonObject();
		o.addProperty("type", "error");
		o.addProperty("message", message);
		return o;
	}

	private void send(JsonObject message) {
		PrintWriter w = out;
		if (w != null) {
			w.println(GSON.toJson(message));
			w.flush();
		}
	}

	ServerLevel level() {
		return bots[0].level();
	}
}
