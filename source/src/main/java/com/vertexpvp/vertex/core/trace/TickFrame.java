package com.vertexpvp.vertex.core.trace;

import java.util.List;

/** Both participants' state at the end of one tick; {@code participants.get(i)} matches {@link ParticipantInfo#index()}. */
public record TickFrame(int tick, List<ParticipantFrame> participants) {
	public ParticipantFrame of(int participantIndex) {
		return participants.get(participantIndex);
	}
}
