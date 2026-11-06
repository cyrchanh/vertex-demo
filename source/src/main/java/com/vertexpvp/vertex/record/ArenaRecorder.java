package com.vertexpvp.vertex.record;

import com.vertexpvp.vertex.core.trace.Arena;
import com.vertexpvp.vertex.core.trace.ArenaColumn;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.commands.arguments.blocks.BlockStateParser;
import net.minecraft.core.BlockPos;
import net.minecraft.world.level.block.state.BlockState;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Captures the blocks around a fight from this client's loaded world, so the arena can be rebuilt
 * after you've left the server. Main thread only.
 *
 * <p>Whenever a fighter is near a 16x16 chunk column that hasn't been captured yet, that column
 * (and its neighbours) is queued, and a few queued columns are read per tick so capturing never
 * causes a frame hitch. The vertical range is fixed when the fight starts: {@link #BELOW} blocks
 * under the recorder's feet to {@link #ABOVE} above.
 */
final class ArenaRecorder {

	static final int BELOW = 12;
	static final int ABOVE = 20;
	static final int RADIUS_CHUNKS = 1;
	static final int COLUMNS_PER_TICK = 3;
	static final int MAX_COLUMNS = 64;

	private final int minY;
	private final int height;
	private final Map<Long, ArenaColumn> captured = new LinkedHashMap<>();
	private final Set<Long> queued = new HashSet<>();
	private final ArrayDeque<Long> queue = new ArrayDeque<>();
	private final Map<BlockState, String> names = new IdentityHashMap<>();

	ArenaRecorder(ClientLevel level, double feetY) {
		int floor = (int) Math.floor(feetY);
		int min = Math.max(level.getMinY(), floor - BELOW);
		int max = Math.min(level.getMaxY(), floor + ABOVE);
		this.minY = min;
		this.height = Math.max(1, max - min + 1);
	}

	/** Make sure the columns around this position get captured. */
	void request(double x, double z) {
		int cx = (int) Math.floor(x) >> 4;
		int cz = (int) Math.floor(z) >> 4;
		for (int dx = -RADIUS_CHUNKS; dx <= RADIUS_CHUNKS; dx++) {
			for (int dz = -RADIUS_CHUNKS; dz <= RADIUS_CHUNKS; dz++) {
				long key = key(cx + dx, cz + dz);
				if (!captured.containsKey(key) && queued.add(key)) {
					queue.addLast(key);
				}
			}
		}
	}

	/** Capture a few queued columns that are loaded. */
	void tick(ClientLevel level) {
		int budget = COLUMNS_PER_TICK;
		int attempts = queue.size();
		while (budget > 0 && attempts-- > 0 && !queue.isEmpty() && captured.size() < MAX_COLUMNS) {
			long key = queue.pollFirst();
			int cx = (int) (key >> 32);
			int cz = (int) key;
			if (!level.hasChunk(cx, cz)) {
				queue.addLast(key);
				continue;
			}
			queued.remove(key);
			captured.put(key, capture(level, cx, cz));
			budget--;
		}
	}

	/** Capture whatever is still queued and loaded, then return the arena. {@code level} may be null. */
	Arena finish(ClientLevel level) {
		if (level != null) {
			for (Long key : List.copyOf(queue)) {
				int cx = (int) (key >> 32);
				int cz = (int) (long) key;
				if (captured.size() < MAX_COLUMNS && level.hasChunk(cx, cz)) {
					captured.put(key, capture(level, cx, cz));
				}
			}
		}
		queue.clear();
		queued.clear();
		return new Arena(new ArrayList<>(captured.values()));
	}

	private ArenaColumn capture(ClientLevel level, int cx, int cz) {
		String[] states = new String[16 * 16 * height];
		BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
		for (int y = minY; y < minY + height; y++) {
			for (int lz = 0; lz < 16; lz++) {
				for (int lx = 0; lx < 16; lx++) {
					pos.set((cx << 4) + lx, y, (cz << 4) + lz);
					BlockState state = level.getBlockState(pos);
					states[ArenaColumn.indexOf(lx, y, lz, minY)] = state.isAir()
							? ArenaColumn.AIR
							: names.computeIfAbsent(state, BlockStateParser::serialize);
				}
			}
		}
		return ArenaColumn.encode(cx, cz, minY, height, states);
	}

	private static long key(int cx, int cz) {
		return ((long) cx << 32) | (cz & 0xFFFFFFFFL);
	}
}
