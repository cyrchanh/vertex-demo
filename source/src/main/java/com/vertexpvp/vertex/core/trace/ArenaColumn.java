package com.vertexpvp.vertex.core.trace;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * The blocks of one 16x16 chunk column over a vertical range, captured during a fight so the arena
 * can be rebuilt later without the original server.
 *
 * <p>Blocks are stored as vanilla block-state strings ({@code minecraft:oak_stairs[facing=east,...]})
 * in a palette, with run-length encoding over the flattened volume. Arenas are mostly air or a few
 * repeated blocks, so this stays small. Index order: {@code ((y - minY) * 16 + localZ) * 16 + localX}.
 *
 * @param runs pairs of (palette index, run length)
 */
public record ArenaColumn(int chunkX, int chunkZ, int minY, int height, List<String> palette, int[] runs) {

	public static final String AIR = "minecraft:air";

	public static ArenaColumn encode(int chunkX, int chunkZ, int minY, int height, String[] states) {
		if (states.length != 16 * 16 * height) {
			throw new IllegalArgumentException("expected " + (16 * 16 * height) + " states, got " + states.length);
		}
		List<String> palette = new ArrayList<>();
		Map<String, Integer> index = new HashMap<>();
		List<Integer> runs = new ArrayList<>();
		int current = -1;
		int count = 0;
		for (String s : states) {
			int id = index.computeIfAbsent(s == null ? AIR : s, k -> {
				palette.add(k);
				return palette.size() - 1;
			});
			if (id == current) {
				count++;
			} else {
				if (count > 0) {
					runs.add(current);
					runs.add(count);
				}
				current = id;
				count = 1;
			}
		}
		if (count > 0) {
			runs.add(current);
			runs.add(count);
		}
		return new ArenaColumn(chunkX, chunkZ, minY, height, List.copyOf(palette),
				runs.stream().mapToInt(Integer::intValue).toArray());
	}

	public String[] decode() {
		String[] states = new String[16 * 16 * height];
		int pos = 0;
		for (int i = 0; i + 1 < runs.length; i += 2) {
			String s = palette.get(runs[i]);
			for (int n = 0; n < runs[i + 1] && pos < states.length; n++) {
				states[pos++] = s;
			}
		}
		while (pos < states.length) {
			states[pos++] = AIR;
		}
		return states;
	}

	public static int indexOf(int localX, int y, int localZ, int minY) {
		return ((y - minY) * 16 + localZ) * 16 + localX;
	}

	/** Block at world coordinates, or {@code null} if outside this column. */
	public String blockAt(int x, int y, int z) {
		if (Math.floorDiv(x, 16) != chunkX || Math.floorDiv(z, 16) != chunkZ || y < minY || y >= minY + height) {
			return null;
		}
		return decode()[indexOf(Math.floorMod(x, 16), y, Math.floorMod(z, 16), minY)];
	}

	public boolean allAir() {
		return palette.size() == 1 && palette.get(0).equals(AIR);
	}
}
