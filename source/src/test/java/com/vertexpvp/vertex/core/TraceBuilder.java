package com.vertexpvp.vertex.core;

import com.vertexpvp.vertex.core.trace.CombatEvent;
import com.vertexpvp.vertex.core.trace.ControlInput;
import com.vertexpvp.vertex.core.trace.DuelTrace;
import com.vertexpvp.vertex.core.trace.ParticipantFrame;
import com.vertexpvp.vertex.core.trace.ParticipantInfo;
import com.vertexpvp.vertex.core.trace.TickFrame;

import java.util.ArrayList;
import java.util.List;

/**
 * Builds synthetic fights for tests. Participant 0 is the recording player (exact inputs),
 * participant 1 is the opponent (inputs unknown), standing 3 blocks apart on flat ground.
 */
final class TraceBuilder {

	static final class F {
		double x, y, z, vx, vy, vz;
		boolean onGround = true, sprinting, sneaking, swingStart;
		ControlInput input;
		float health = 20f;
		int hurtTime;
	}

	private final F[][] frames;
	private final List<CombatEvent> events = new ArrayList<>();
	private boolean opponentIsPlayer = true;
	private int winner = -1;

	TraceBuilder(int ticks) {
		frames = new F[ticks][2];
		for (int t = 0; t < ticks; t++) {
			F me = new F();
			me.input = ControlInput.NONE;
			F them = new F();
			them.x = 3.0;
			frames[t][0] = me;
			frames[t][1] = them;
		}
	}

	F at(int tick, int participant) {
		return frames[tick][participant];
	}

	TraceBuilder opponentIsMob() {
		opponentIsPlayer = false;
		return this;
	}

	TraceBuilder winner(int p) {
		winner = p;
		return this;
	}

	TraceBuilder hit(int tick, int attacker, CombatEvent.HitKind kind) {
		return hit(tick, attacker, kind, null, 3.0);
	}

	TraceBuilder hit(int tick, int attacker, CombatEvent.HitKind kind, CombatEvent.HitKind predicted, double range) {
		events.add(new CombatEvent(tick, CombatEvent.Type.HIT, attacker, 1 - attacker, kind, predicted, true, range,
				Double.NaN, Double.NaN, Double.NaN));
		return this;
	}

	/** Set exact held keys for the recording player over [from, to]. */
	TraceBuilder keys(int from, int to, ControlInput input) {
		for (int t = from; t <= to; t++) {
			frames[t][0].input = input;
		}
		return this;
	}

	/** A jump for participant p starting at tick {@code start}: airborne for 11 ticks with a vanilla-like arc. */
	TraceBuilder jump(int p, int start) {
		double vy = 0.42;
		double y = 0;
		for (int t = start; t < Math.min(frames.length, start + 11); t++) {
			y += vy;
			F f = frames[t][p];
			f.onGround = false;
			f.vy = vy;
			f.y = Math.max(0, y);
			vy = (vy - 0.08) * 0.98;
		}
		return this;
	}

	TraceBuilder swing(int p, int tick) {
		frames[tick][p].swingStart = true;
		return this;
	}

	DuelTrace build() {
		List<TickFrame> tickFrames = new ArrayList<>();
		for (int t = 0; t < frames.length; t++) {
			List<ParticipantFrame> ps = new ArrayList<>();
			for (F f : frames[t]) {
				ps.add(new ParticipantFrame(f.x, f.y, f.z, 0f, 0f, 0f, 0f, f.vx, f.vy, f.vz, f.onGround, f.sprinting,
						f.sneaking, f.input, f.health, f.input == null ? -1f : 1f, 0.0, f.swingStart, f.hurtTime));
			}
			tickFrames.add(new TickFrame(t, ps));
		}
		List<ParticipantInfo> participants = List.of(
				new ParticipantInfo(0, "Me", true, "minecraft:player", true),
				new ParticipantInfo(1, opponentIsPlayer ? "Rival" : "Zombie", opponentIsPlayer,
						opponentIsPlayer ? "minecraft:player" : "minecraft:zombie", false));
		return new DuelTrace(DuelTrace.FORMAT_VERSION, "test-trace", "client", 0L, "minecraft:overworld",
				participants, tickFrames, List.copyOf(events), DuelTrace.EndReason.IDLE, winner, null);
	}

	static ControlInput keys(boolean forward, boolean back, boolean jump) {
		return new ControlInput(forward, back, false, false, jump, false, false);
	}
}
