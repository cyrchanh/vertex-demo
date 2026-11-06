package com.vertexpvp.vertex.mcp;

import com.vertexpvp.vertex.core.ReplayCodec;
import com.vertexpvp.vertex.core.ReplayDocument;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Stream;

/**
 * Read-only view of a Vertex replay folder ({@code <.minecraft>/vertex-replays}). Decoded replays
 * are cached and reloaded only when the file changes, so new fights show up without restarting.
 */
final class ReplayLibrary {

	private record Cached(long modified, ReplayDocument doc) {
	}

	private final Path dir;
	private final Map<Path, Cached> cache = new HashMap<>();

	ReplayLibrary(Path dir) {
		this.dir = dir;
	}

	Path directory() {
		return dir;
	}

	/** All readable replays, newest first. Unreadable files are skipped. */
	synchronized List<ReplayDocument> all() {
		List<ReplayDocument> docs = new ArrayList<>();
		if (!Files.isDirectory(dir)) {
			return docs;
		}
		try (Stream<Path> files = Files.list(dir)) {
			for (Path file : files.filter(f -> f.getFileName().toString().endsWith(".json.gz")).toList()) {
				load(file).ifPresent(docs::add);
			}
		} catch (IOException e) {
			return docs;
		}
		docs.sort(Comparator.comparingLong((ReplayDocument d) -> d.trace().startedAtEpochMs()).reversed());
		return docs;
	}

	/** A replay by id, or the newest one for {@code "latest"}. */
	synchronized Optional<ReplayDocument> find(String id) {
		if (id == null || id.isBlank() || id.equalsIgnoreCase("latest")) {
			List<ReplayDocument> docs = all();
			return docs.isEmpty() ? Optional.empty() : Optional.of(docs.get(0));
		}
		if (!id.matches("[A-Za-z0-9_-]+")) {
			return Optional.empty();
		}
		return load(dir.resolve(id + ".json.gz"));
	}

	private Optional<ReplayDocument> load(Path file) {
		try {
			if (!Files.isRegularFile(file)) {
				return Optional.empty();
			}
			long modified = Files.getLastModifiedTime(file).toMillis();
			Cached cached = cache.get(file);
			if (cached != null && cached.modified() == modified) {
				return Optional.of(cached.doc());
			}
			ReplayDocument doc = ReplayCodec.decode(Files.readAllBytes(file));
			cache.put(file, new Cached(modified, doc));
			return Optional.of(doc);
		} catch (IOException | RuntimeException e) {
			return Optional.empty();
		}
	}
}
