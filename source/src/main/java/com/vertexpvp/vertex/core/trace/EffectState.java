package com.vertexpvp.vertex.core.trace;

/**
 * One active potion effect on a tick.
 *
 * @param id            effect id, e.g. {@code minecraft:speed}
 * @param amplifier     0 for level I, 1 for level II, ...
 * @param durationTicks remaining ticks, or -1 for an infinite effect
 */
public record EffectState(String id, int amplifier, int durationTicks) {
}
