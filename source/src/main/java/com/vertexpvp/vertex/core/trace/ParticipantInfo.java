package com.vertexpvp.vertex.core.trace;

/**
 * One side of a recorded fight.
 *
 * @param index      0 or 1; the position of this participant in every {@link TickFrame}
 * @param name       display name at recording time
 * @param player     true for a player, false for a mob
 * @param entityType registry id of the entity type, e.g. {@code minecraft:player}
 * @param recorder   true for the player whose client made this recording (their inputs are exact)
 */
public record ParticipantInfo(int index, String name, boolean player, String entityType, boolean recorder) {
}
