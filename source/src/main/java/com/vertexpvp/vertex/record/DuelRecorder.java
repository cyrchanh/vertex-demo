package com.vertexpvp.vertex.record;

import com.vertexpvp.vertex.core.ReplayDocument;
import com.vertexpvp.vertex.core.detect.Interaction;
import com.vertexpvp.vertex.core.detect.InteractionDetector;
import com.vertexpvp.vertex.core.explain.Explainer;
import com.vertexpvp.vertex.core.explain.Explanation;
import com.vertexpvp.vertex.core.trace.Arena;
import com.vertexpvp.vertex.core.trace.CombatEvent;
import com.vertexpvp.vertex.core.trace.CombatEvent.HitKind;
import com.vertexpvp.vertex.core.trace.DuelTrace;
import com.vertexpvp.vertex.core.trace.ParticipantFrame;
import com.vertexpvp.vertex.core.trace.ParticipantInfo;
import com.vertexpvp.vertex.core.trace.RecordingContext;
import com.vertexpvp.vertex.core.trace.TickFrame;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.SharedConstants;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.multiplayer.ClientPacketListener;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.network.protocol.game.ClientboundAnimatePacket;
import net.minecraft.resources.Identifier;
import net.minecraft.sounds.SoundEvents;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.player.Player;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.text.SimpleDateFormat;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Date;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Consumer;

/**
 * Records 1v1 fights from this client's point of view. Main thread only, except the analysis step
 * at the end of a fight, which runs on its own background thread on plain data.
 *
 * <p><b>Where each fact comes from</b>
 * <ul>
 *     <li>Your movement keys, velocity, attack charge: read directly from your own client (exact).</li>
 *     <li>Who hit whom: the server's damage-event packet, which names the attacker (exact).</li>
 *     <li>Crit / sprint hit / strong / weak: the attack sounds the server broadcasts from the
 *     attacker's position, plus the crit animation packet (exact, as decided by the server).</li>
 *     <li>Knockback: the velocity packet the server sends to the victim (exact).</li>
 *     <li>Opponent position, rotation, sprint/crouch: what the server sends this client (exact as
 *     of the last packet, smoothed by vanilla interpolation). Their held keys are unknown.</li>
 * </ul>
 *
 * <p>A fight starts when you hit, or are hit by, another living entity while recording is on. It
 * ends after {@link Settings#idleTimeoutTicks} with no hits, shortly after someone dies, or when
 * the opponent disappears. The {@link Settings#preRollTicks} before the first hit are included so
 * the approach (the neutral game) is part of the replay.
 */
public final class DuelRecorder {

	private static final Logger LOG = LoggerFactory.getLogger("Vertex");

	public static final class Settings {
		public int idleTimeoutTicks = 100;
		public int deathTailTicks = 20;
		public int preRollTicks = 60;
		public int maxTicks = 20 * 60 * 10;
		public int trailingTicksAfterLastHit = 40;
		public double nearbyRadius = 12.0;
		public int clickMatchWindowTicks = 20;
		public double soundAttributionRadius = 3.0;
	}

	private enum AttackSound { CRIT, KNOCKBACK, STRONG, WEAK, SWEEP, NODAMAGE }

	private record DamageSignal(long tick, int victim, int cause) {
	}

	private record SoundSignal(long tick, AttackSound kind, double x, double y, double z) {
	}

	private record CritSignal(long tick, int victim) {
	}

	private record MotionSignal(long tick, int entity, double x, double y, double z) {
	}

	private record PendingClick(long tick, int targetId, HitKind predicted, double range) {
	}

	private record Snapshot(long tick, Map<Integer, ParticipantFrame> frames) {
	}

	private static final class ActiveDuel {
		final String id;
		final long startedAt;
		final String dimension;
		final List<ParticipantInfo> participants;
		final int[] ids;
		final long firstTick;
		final List<TickFrame> frames = new ArrayList<>();
		final List<CombatEvent> events = new ArrayList<>();
		long lastActivityTick;
		long endAtTick = -1;
		int winner = -1;
		ArenaRecorder arena;
		RecordingContext context;

		ActiveDuel(String id, long startedAt, String dimension, List<ParticipantInfo> participants, int[] ids,
				long firstTick, long now) {
			this.id = id;
			this.startedAt = startedAt;
			this.dimension = dimension;
			this.participants = participants;
			this.ids = ids;
			this.firstTick = firstTick;
			this.lastActivityTick = now;
		}

		int index(long tick) {
			return (int) Math.max(0, tick - firstTick);
		}

		int participantOf(int entityId) {
			return entityId == ids[0] ? 0 : entityId == ids[1] ? 1 : -1;
		}
	}

	private final Settings settings = new Settings();
	private final ReplayStore store;
	private final Explainer explainer;
	private final InteractionDetector detector = new InteractionDetector();
	private final ExecutorService analysis = Executors.newSingleThreadExecutor(r -> {
		Thread t = new Thread(r, "vertex-analysis");
		t.setDaemon(true);
		return t;
	});
	private final List<Consumer<ReplayDocument>> listeners = new CopyOnWriteArrayList<>();
	private final FrameSampler sampler = new FrameSampler();
	private final ArrayDeque<Snapshot> preRoll = new ArrayDeque<>();

	private final List<DamageSignal> damageSignals = new ArrayList<>();
	private final List<SoundSignal> soundSignals = new ArrayList<>();
	private final List<CritSignal> critSignals = new ArrayList<>();
	private final List<MotionSignal> motionSignals = new ArrayList<>();
	private final List<PendingClick> pendingClicks = new ArrayList<>();

	private boolean enabled;
	private long tick;
	private ActiveDuel active;

	public DuelRecorder(ReplayStore store, Explainer explainer) {
		this.store = store;
		this.explainer = explainer;
	}

	public Settings settings() {
		return settings;
	}

	public ReplayStore store() {
		return store;
	}

	/** Called on the main thread with every finished, analysed and saved replay. */
	public void addListener(Consumer<ReplayDocument> listener) {
		listeners.add(listener);
	}

	public boolean isEnabled() {
		return enabled;
	}

	public void setEnabled(boolean enabled) {
		this.enabled = enabled;
		if (!enabled && active != null) {
			finish(DuelTrace.EndReason.STOPPED);
		}
	}

	public boolean isRecordingFight() {
		return active != null;
	}

	// ---------------------------------------------------------------- inputs (main thread)

	/** From the client-side attack callback, before the attack is sent to the server. */
	public void onLocalAttack(LocalPlayer player, Entity target) {
		if (!(target instanceof LivingEntity living) || !living.isAlive()) {
			return;
		}
		pendingClicks.add(new PendingClick(tick, target.getId(), ClientCritRules.predict(player, target),
				player.distanceTo(target)));
		if (active == null && enabled) {
			start(player, living);
		}
	}

	void onDamagePacket(int victimId, int causeId) {
		damageSignals.add(new DamageSignal(tick, victimId, causeId));
	}

	void onAnimatePacket(int entityId, int action) {
		if (action == ClientboundAnimatePacket.CRITICAL_HIT) {
			critSignals.add(new CritSignal(tick, entityId));
		}
	}

	void onSoundPacket(Identifier soundId, double x, double y, double z) {
		AttackSound kind = attackSound(soundId);
		if (kind != null) {
			soundSignals.add(new SoundSignal(tick, kind, x, y, z));
		}
	}

	void onMotionPacket(int entityId, double x, double y, double z) {
		motionSignals.add(new MotionSignal(tick, entityId, x, y, z));
	}

	private static AttackSound attackSound(Identifier id) {
		if (id.equals(SoundEvents.PLAYER_ATTACK_CRIT.location())) return AttackSound.CRIT;
		if (id.equals(SoundEvents.PLAYER_ATTACK_KNOCKBACK.location())) return AttackSound.KNOCKBACK;
		if (id.equals(SoundEvents.PLAYER_ATTACK_STRONG.location())) return AttackSound.STRONG;
		if (id.equals(SoundEvents.PLAYER_ATTACK_WEAK.location())) return AttackSound.WEAK;
		if (id.equals(SoundEvents.PLAYER_ATTACK_SWEEP.location())) return AttackSound.SWEEP;
		if (id.equals(SoundEvents.PLAYER_ATTACK_NODAMAGE.location())) return AttackSound.NODAMAGE;
		return null;
	}

	/** Leaving a world: save whatever was in progress and clear all per-world state. */
	public void onDisconnect() {
		if (active != null) {
			finish(DuelTrace.EndReason.DISCONNECT);
		}
		clearSignals();
		preRoll.clear();
		sampler.reset();
	}

	// ---------------------------------------------------------------- per tick

	public void onEndClientTick(Minecraft mc) {
		LocalPlayer player = mc.player;
		ClientLevel level = mc.level;
		if (player == null || level == null) {
			if (active != null) {
				finish(DuelTrace.EndReason.DISCONNECT);
			}
			clearSignals();
			preRoll.clear();
			sampler.reset();
			tick++;
			return;
		}

		Map<Integer, ParticipantFrame> snap = new HashMap<>();
		snap.put(player.getId(), sampler.sample(player, true));
		for (Entity e : level.entitiesForRendering()) {
			if (e != player && e instanceof LivingEntity living && living.isAlive()
					&& e.distanceTo(player) <= settings.nearbyRadius) {
				snap.put(e.getId(), sampler.sample(living, false));
			}
		}
		if (active != null && !snap.containsKey(active.ids[1])
				&& level.getEntity(active.ids[1]) instanceof LivingEntity opponent) {
			snap.put(opponent.getId(), sampler.sample(opponent, false));
		}
		sampler.retainOnly(snap.keySet());
		preRoll.addLast(new Snapshot(tick, snap));
		while (preRoll.size() > settings.preRollTicks + 1) {
			preRoll.removeFirst();
		}

		resolveSignals(player, level);

		if (active != null) {
			appendFrameAndCheckEnd(player, level, snap);
		}

		pendingClicks.removeIf(c -> tick - c.tick() > settings.clickMatchWindowTicks);
		tick++;
	}

	private void appendFrameAndCheckEnd(LocalPlayer player, ClientLevel level, Map<Integer, ParticipantFrame> snap) {
		ActiveDuel duel = active;
		ParticipantFrame me = snap.get(duel.ids[0]);
		ParticipantFrame them = snap.get(duel.ids[1]);
		Entity opponent = level.getEntity(duel.ids[1]);
		if (me == null || them == null || opponent == null || opponent.isRemoved()) {
			finish(duel.endAtTick >= 0 ? DuelTrace.EndReason.DEATH : DuelTrace.EndReason.OPPONENT_LOST);
			return;
		}
		if (!level.dimension().identifier().toString().equals(duel.dimension)) {
			finish(DuelTrace.EndReason.DISCONNECT);
			return;
		}
		duel.frames.add(new TickFrame(duel.frames.size(), List.of(me, them)));
		duel.arena.request(me.x(), me.z());
		duel.arena.request(them.x(), them.z());
		duel.arena.tick(level);

		if (duel.endAtTick < 0) {
			if (player.isDeadOrDying()) {
				recordDeath(duel, 0);
			} else if (opponent instanceof LivingEntity living && living.isDeadOrDying()) {
				recordDeath(duel, 1);
			}
		}

		if (duel.endAtTick >= 0 && tick >= duel.endAtTick) {
			finish(DuelTrace.EndReason.DEATH);
		} else if (tick - duel.lastActivityTick >= settings.idleTimeoutTicks) {
			finish(DuelTrace.EndReason.IDLE);
		} else if (duel.frames.size() >= settings.maxTicks) {
			finish(DuelTrace.EndReason.MAX_LENGTH);
		}
	}

	private void recordDeath(ActiveDuel duel, int victim) {
		duel.events.add(new CombatEvent(duel.index(tick), CombatEvent.Type.DEATH, 1 - victim, victim,
				HitKind.UNKNOWN, null, true, Double.NaN, Double.NaN, Double.NaN, Double.NaN));
		duel.winner = 1 - victim;
		duel.endAtTick = tick + settings.deathTailTicks;
	}

	// ---------------------------------------------------------------- packet resolution

	/**
	 * Damage, sounds and the crit animation for one attack normally arrive in the same tick, but
	 * can straddle a tick boundary. So damage is resolved one tick after it arrives, looking at
	 * sounds from its own tick and the next. Event ticks still use the arrival tick.
	 */
	private void resolveSignals(LocalPlayer player, ClientLevel level) {
		int me = player.getId();

		Iterator<DamageSignal> damages = damageSignals.iterator();
		while (damages.hasNext()) {
			DamageSignal d = damages.next();
			if (d.tick() >= tick) {
				continue;
			}
			damages.remove();

			boolean involvesMe = d.victim() == me || d.cause() == me;
			int otherId = d.victim() == me ? d.cause() : d.victim();
			if (!involvesMe || otherId < 0 || otherId == me) {
				continue;
			}
			if (active == null && enabled && level.getEntity(otherId) instanceof LivingEntity other) {
				start(player, other);
			}
			if (active == null || active.participantOf(otherId) != 1) {
				continue;
			}
			recordHit(active, d, level);
		}

		resolveNoDamage();
		resolveKnockback();

		long keepFrom = tick - 3;
		soundSignals.removeIf(s -> s.tick() < keepFrom);
		critSignals.removeIf(c -> c.tick() < keepFrom);
		motionSignals.removeIf(m -> m.tick() < keepFrom);
	}

	private void recordHit(ActiveDuel duel, DamageSignal d, ClientLevel level) {
		int attacker = duel.participantOf(d.cause());
		int victim = duel.participantOf(d.victim());
		Entity attackerEntity = level.getEntity(d.cause());

		HitKind kind;
		boolean confirmed;
		if (attackerEntity instanceof Player) {
			kind = classify(duel, attacker, d);
			confirmed = kind != HitKind.UNKNOWN;
		} else {
			kind = HitKind.MOB;
			confirmed = true;
		}

		int eventTick = duel.index(d.tick());
		HitKind predicted = null;
		double range = distanceAt(duel, d.tick());
		if (attacker == 0) {
			PendingClick click = takeClick(duel.ids[1]);
			if (click != null) {
				eventTick = duel.index(click.tick());
				predicted = click.predicted();
				range = click.range();
			}
		}
		duel.events.add(new CombatEvent(eventTick, CombatEvent.Type.HIT, attacker, victim, kind, predicted, confirmed,
				range, Double.NaN, Double.NaN, Double.NaN));
		duel.lastActivityTick = tick;
	}

	/** The server's own verdict, from the sounds it played at the attacker and the crit animation on the victim. */
	private HitKind classify(ActiveDuel duel, int attacker, DamageSignal d) {
		boolean crit = false, knockback = false, weak = false, strong = false;
		for (CritSignal c : critSignals) {
			if (c.victim() == d.victim() && c.tick() >= d.tick() && c.tick() <= d.tick() + 1) {
				crit = true;
			}
		}
		for (SoundSignal s : soundSignals) {
			if (s.tick() < d.tick() || s.tick() > d.tick() + 1 || nearestParticipant(duel, s) != attacker) {
				continue;
			}
			switch (s.kind()) {
				case CRIT -> crit = true;
				case KNOCKBACK -> knockback = true;
				case WEAK -> weak = true;
				case STRONG, SWEEP -> strong = true;
				default -> {
				}
			}
		}
		if (crit) return HitKind.CRIT;
		if (knockback) return HitKind.SPRINT_HIT;
		if (weak) return HitKind.WEAK;
		if (strong) return HitKind.STRONG;
		return HitKind.UNKNOWN;
	}

	/** An attack connected but did no damage (e.g. inside the victim's invulnerability window). */
	private void resolveNoDamage() {
		if (active == null) {
			return;
		}
		Iterator<SoundSignal> it = soundSignals.iterator();
		while (it.hasNext()) {
			SoundSignal s = it.next();
			if (s.kind() != AttackSound.NODAMAGE || s.tick() >= tick) {
				continue;
			}
			it.remove();
			int attacker = nearestParticipant(active, s);
			if (attacker < 0) {
				continue;
			}
			int eventTick = active.index(s.tick());
			if (attacker == 0) {
				PendingClick click = takeClick(active.ids[1]);
				if (click != null) {
					eventTick = active.index(click.tick());
				}
			}
			active.events.add(new CombatEvent(eventTick, CombatEvent.Type.NO_DAMAGE, attacker, 1 - attacker,
					HitKind.UNKNOWN, null, true, distanceAt(active, s.tick()), Double.NaN, Double.NaN, Double.NaN));
			active.lastActivityTick = tick;
		}
	}

	/** Attach the server's knockback velocity to the most recent hit on that participant that lacks one. */
	private void resolveKnockback() {
		if (active == null) {
			return;
		}
		Iterator<MotionSignal> it = motionSignals.iterator();
		while (it.hasNext()) {
			MotionSignal m = it.next();
			if (m.tick() >= tick) {
				continue;
			}
			int victim = active.participantOf(m.entity());
			if (victim < 0) {
				it.remove();
				continue;
			}
			int latest = active.index(m.tick());
			for (int i = active.events.size() - 1; i >= 0; i--) {
				CombatEvent e = active.events.get(i);
				if (e.type() == CombatEvent.Type.HIT && e.victim() == victim && !e.hasKnockback()
						&& latest - e.tick() <= 10) {
					active.events.set(i, new CombatEvent(e.tick(), e.type(), e.attacker(), e.victim(), e.kind(),
							e.predictedKind(), e.serverConfirmed(), e.range(), m.x(), m.y(), m.z()));
					break;
				}
			}
			it.remove();
		}
	}

	private PendingClick takeClick(int targetId) {
		for (int i = 0; i < pendingClicks.size(); i++) {
			if (pendingClicks.get(i).targetId() == targetId) {
				return pendingClicks.remove(i);
			}
		}
		return null;
	}

	/** Which participant a sound came from: whoever stood closest to it, within a few blocks. */
	private int nearestParticipant(ActiveDuel duel, SoundSignal s) {
		Snapshot snap = snapshotAt(s.tick());
		if (snap == null) {
			return -1;
		}
		int best = -1;
		double bestDist = settings.soundAttributionRadius;
		for (int p = 0; p < 2; p++) {
			ParticipantFrame f = snap.frames().get(duel.ids[p]);
			if (f == null) {
				continue;
			}
			double dx = f.x() - s.x(), dy = f.y() - s.y(), dz = f.z() - s.z();
			double dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
			if (dist < bestDist) {
				bestDist = dist;
				best = p;
			}
		}
		return best;
	}

	private double distanceAt(ActiveDuel duel, long atTick) {
		Snapshot snap = snapshotAt(atTick);
		if (snap == null) {
			return Double.NaN;
		}
		ParticipantFrame a = snap.frames().get(duel.ids[0]);
		ParticipantFrame b = snap.frames().get(duel.ids[1]);
		return a == null || b == null ? Double.NaN : a.distanceTo(b);
	}

	private Snapshot snapshotAt(long atTick) {
		Snapshot fallback = null;
		for (Iterator<Snapshot> it = preRoll.descendingIterator(); it.hasNext(); ) {
			Snapshot s = it.next();
			if (s.tick() <= atTick) {
				return s;
			}
			fallback = s;
		}
		return fallback;
	}

	// ---------------------------------------------------------------- lifecycle

	private void start(LocalPlayer player, LivingEntity opponent) {
		List<Snapshot> history = new ArrayList<>();
		for (Iterator<Snapshot> it = preRoll.descendingIterator(); it.hasNext(); ) {
			Snapshot s = it.next();
			if (s.tick() >= tick) {
				continue;
			}
			long expected = history.isEmpty() ? tick - 1 : history.get(history.size() - 1).tick() - 1;
			if (s.tick() != expected || !s.frames().containsKey(player.getId())
					|| !s.frames().containsKey(opponent.getId())) {
				break;
			}
			history.add(s);
		}
		history.sort(Comparator.comparingLong(Snapshot::tick));
		long firstTick = history.isEmpty() ? tick : history.get(0).tick();

		List<ParticipantInfo> participants = List.of(
				new ParticipantInfo(0, player.getName().getString(), true, "minecraft:player", true),
				new ParticipantInfo(1, opponent.getName().getString(), opponent instanceof Player,
						EntityType.getKey(opponent.getType()).toString(), false));
		String id = new SimpleDateFormat("yyyyMMdd-HHmmss").format(new Date()) + "-"
				+ UUID.randomUUID().toString().substring(0, 6);
		active = new ActiveDuel(id, System.currentTimeMillis(), player.level().dimension().identifier().toString(),
				participants, new int[] {player.getId(), opponent.getId()}, firstTick, tick);
		ClientLevel level = Minecraft.getInstance().level;
		active.arena = new ArenaRecorder(level, player.getY());
		active.context = recordingContext();
		for (Snapshot s : history) {
			ParticipantFrame me = s.frames().get(player.getId());
			ParticipantFrame them = s.frames().get(opponent.getId());
			active.frames.add(new TickFrame(active.frames.size(), List.of(me, them)));
			active.arena.request(me.x(), me.z());
			active.arena.request(them.x(), them.z());
		}
		active.arena.request(player.getX(), player.getZ());
		active.arena.request(opponent.getX(), opponent.getZ());
		LOG.info("Vertex: recording fight {} vs {}", participants.get(0).name(), participants.get(1).name());
	}

	private void finish(DuelTrace.EndReason reason) {
		ActiveDuel duel = active;
		active = null;
		pendingClicks.clear();
		if (duel == null) {
			return;
		}
		boolean anyHit = duel.events.stream().anyMatch(e -> e.type() == CombatEvent.Type.HIT);
		if (!anyHit || duel.frames.isEmpty()) {
			LOG.info("Vertex: discarded a fight with no landed hits");
			return;
		}

		List<TickFrame> frames = duel.frames;
		if (reason == DuelTrace.EndReason.IDLE) {
			int lastEvent = duel.events.stream().mapToInt(CombatEvent::tick).max().orElse(frames.size() - 1);
			int keep = Math.min(frames.size(), lastEvent + 1 + settings.trailingTicksAfterLastHit);
			frames = new ArrayList<>(frames.subList(0, keep));
		}
		int lastIndex = frames.size() - 1;
		List<CombatEvent> events = new ArrayList<>();
		for (CombatEvent e : duel.events) {
			if (e.tick() <= lastIndex) {
				events.add(e);
			}
		}
		events.sort(Comparator.comparingInt(CombatEvent::tick));

		Arena arena = duel.arena == null ? null : duel.arena.finish(Minecraft.getInstance().level);
		DuelTrace trace = new DuelTrace(DuelTrace.FORMAT_VERSION, duel.id, "client", duel.startedAt, duel.dimension,
				duel.participants, List.copyOf(frames), List.copyOf(events), reason, duel.winner, arena, duel.context);
		analysis.submit(() -> analyseAndSave(trace));
	}

	private static RecordingContext recordingContext() {
		Minecraft mc = Minecraft.getInstance();
		String modVersion = FabricLoader.getInstance().getModContainer("vertex")
				.map(m -> m.getMetadata().getVersion().getFriendlyString()).orElse(null);
		ServerData server = mc.getCurrentServer();
		ClientPacketListener connection = mc.getConnection();
		boolean singleplayer = mc.hasSingleplayerServer();
		return new RecordingContext(SharedConstants.getCurrentVersion().name(), modVersion,
				singleplayer || server == null ? null : server.ip,
				connection == null ? null : connection.serverBrand(), singleplayer);
	}

	private void analyseAndSave(DuelTrace trace) {
		try {
			List<Interaction> interactions = detector.detect(trace);
			List<Explanation> explanations = explainer.explain(trace, interactions);
			ReplayDocument doc = new ReplayDocument(trace, interactions, explanations, explainer.id());
			store.save(doc);
			LOG.info("Vertex: saved replay {} ({} interactions)", trace.id(), interactions.size());
			Minecraft.getInstance().execute(() -> listeners.forEach(l -> l.accept(doc)));
		} catch (Exception e) {
			LOG.error("Vertex: failed to analyse/save replay {}", trace.id(), e);
		}
	}

	private void clearSignals() {
		damageSignals.clear();
		soundSignals.clear();
		critSignals.clear();
		motionSignals.clear();
		pendingClicks.clear();
	}
}
