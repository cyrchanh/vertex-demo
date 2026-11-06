package com.vertexpvp.vertex.core.detect;

/**
 * One recognised fact inside an interaction.
 *
 * @param actor    participant index the observation is about (who did it / who it happened to)
 * @param inferred true when it relies on estimated data (e.g. the opponent's movement keys, which
 *                 a client can't see directly) rather than exact data
 * @param detail   short human-readable specifics, e.g. "range 3.1 blocks"
 */
public record Observation(int tick, Technique technique, int actor, boolean inferred, String detail) {
}
