package com.vertexpvp.vertex.core.detect;

import java.util.List;

/**
 * One exchange: a run of landed hits with no gap longer than
 * {@link InteractionDetector#EXCHANGE_GAP_TICKS}, plus a little context either side.
 *
 * @param anchorTick   tick of the first hit, where the replay jumps to for this interaction
 * @param hitsBy       landed hits per participant index inside this exchange
 * @param eventIndices indices into {@code DuelTrace.events()} of the hits in this exchange
 */
public record Interaction(
		int index,
		int startTick,
		int endTick,
		int anchorTick,
		int[] hitsBy,
		List<Integer> eventIndices,
		List<Observation> observations
) {
	public boolean has(Technique technique, int actor) {
		for (Observation o : observations) {
			if (o.technique() == technique && o.actor() == actor) {
				return true;
			}
		}
		return false;
	}

	public long count(Technique technique, int actor) {
		return observations.stream().filter(o -> o.technique() == technique && o.actor() == actor).count();
	}

	public Observation first(Technique technique, int actor) {
		for (Observation o : observations) {
			if (o.technique() == technique && o.actor() == actor) {
				return o;
			}
		}
		return null;
	}

	public boolean contains(double tick) {
		return tick >= startTick && tick <= endTick;
	}
}
