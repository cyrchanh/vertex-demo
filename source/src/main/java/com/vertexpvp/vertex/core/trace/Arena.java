package com.vertexpvp.vertex.core.trace;

import java.util.List;

/**
 * The blocks around a fight: every chunk column either fighter came near, captured when they first
 * did. Lets the replay rebuild the arena in a private world after you've left the server.
 * Block changes after a column was captured (e.g. blocks placed mid-fight) are not tracked.
 */
public record Arena(List<ArenaColumn> columns) {

	/** Block at world coordinates, or {@code null} if that spot wasn't captured. */
	public String blockAt(int x, int y, int z) {
		for (ArenaColumn c : columns) {
			String s = c.blockAt(x, y, z);
			if (s != null) {
				return s;
			}
		}
		return null;
	}

	public long nonAirBlocks() {
		long n = 0;
		for (ArenaColumn c : columns) {
			for (String s : c.decode()) {
				if (!s.equals(ArenaColumn.AIR)) {
					n++;
				}
			}
		}
		return n;
	}
}
