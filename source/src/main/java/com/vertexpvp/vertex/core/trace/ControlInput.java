package com.vertexpvp.vertex.core.trace;

/** The exact movement keys a player had held on a tick (mirrors vanilla's {@code Input} record). */
public record ControlInput(
		boolean forward,
		boolean back,
		boolean left,
		boolean right,
		boolean jump,
		boolean shift,
		boolean sprint
) {
	public static final ControlInput NONE = new ControlInput(false, false, false, false, false, false, false);
}
