package com.vertexpvp.vertex.core.trace;

import java.util.List;

/**
 * A complete recorded fight between exactly two participants.
 *
 * <p>{@code frames.get(t)} is the state at the end of tick {@code t}; every {@link CombatEvent#tick()}
 * uses the same numbering, so the state just <em>before</em> an event at tick {@code t} is
 * {@code frames.get(t - 1)}. Frames before the first hit are the approach ("pre-roll").
 *
 * @param formatVersion bumped whenever this format changes incompatibly
 * @param source        who recorded it: {@code "client"} today; a future server "lab" recorder
 *                      would write {@code "server"} in the same format
 * @param dimension     dimension id where the fight happened, e.g. {@code minecraft:overworld}
 * @param winner        participant index of the winner, or -1 if nobody died
 * @param arena         blocks around the fight (format 2+), or {@code null} for older replays,
 *                      which can then only be watched where the fight happened
 * @param context       game version, Vertex version and server (format 3+), or {@code null} for older replays
 */
public record DuelTrace(
		int formatVersion,
		String id,
		String source,
		long startedAtEpochMs,
		String dimension,
		List<ParticipantInfo> participants,
		List<TickFrame> frames,
		List<CombatEvent> events,
		EndReason endReason,
		int winner,
		Arena arena,
		RecordingContext context
) {
	/**
	 * 2: added {@link #arena}. 3: added {@link #context}, and gear, absorption, item use, hotbar slot,
	 * ping and effects to {@link ParticipantFrame}.
	 */
	public static final int FORMAT_VERSION = 3;

	/** A trace without a recording context. */
	public DuelTrace(int formatVersion, String id, String source, long startedAtEpochMs, String dimension,
			List<ParticipantInfo> participants, List<TickFrame> frames, List<CombatEvent> events,
			EndReason endReason, int winner, Arena arena) {
		this(formatVersion, id, source, startedAtEpochMs, dimension, participants, frames, events, endReason, winner,
				arena, null);
	}

	public boolean hasArena() {
		return arena != null && !arena.columns().isEmpty();
	}

	public enum EndReason {
		IDLE,
		DEATH,
		DISCONNECT,
		OPPONENT_LOST,
		MAX_LENGTH,
		STOPPED
	}

	public int lastTick() {
		return frames.size() - 1;
	}

	/** Frame at {@code tick}, clamped to the recorded range. */
	public TickFrame frame(int tick) {
		if (frames.isEmpty()) {
			throw new IllegalStateException("trace has no frames");
		}
		return frames.get(Math.max(0, Math.min(tick, frames.size() - 1)));
	}

	public ParticipantFrame state(int tick, int participant) {
		return frame(tick).of(participant);
	}

	public int opponentOf(int participant) {
		return participant == 0 ? 1 : 0;
	}

	public int recorderIndex() {
		for (ParticipantInfo info : participants) {
			if (info.recorder()) {
				return info.index();
			}
		}
		return -1;
	}
}
