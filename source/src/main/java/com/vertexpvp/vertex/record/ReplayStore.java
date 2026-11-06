package com.vertexpvp.vertex.record;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.reflect.TypeToken;
import com.vertexpvp.vertex.core.ReplayCodec;
import com.vertexpvp.vertex.core.ReplayDocument;
import com.vertexpvp.vertex.core.explain.SummaryBuilder;
import com.vertexpvp.vertex.core.trace.DuelTrace;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Optional;

/**
 * Saves replays as {@code <game dir>/vertex-replays/<id>.json.gz}, with a small {@code index.json}
 * so the replay list doesn't have to open every file. Everything stays on this computer.
 * Thread-safe: saves happen on the analysis thread, reads on the main thread.
 */
public final class ReplayStore {

	/** {@code hasArena} is false for recordings made before the surrounding blocks were captured. */
	public record Entry(String id, long startedAtEpochMs, String opponent, int hitsLanded, int hitsTaken, String result,
			boolean hasArena) {
	}

	private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();
	private static final String INDEX = "index.json";

	private final Path dir;

	public ReplayStore(Path dir) {
		this.dir = dir;
	}

	public Path directory() {
		return dir;
	}

	public synchronized void save(ReplayDocument doc) throws IOException {
		Files.createDirectories(dir);
		Files.write(dir.resolve(doc.trace().id() + ".json.gz"), ReplayCodec.encode(doc));

		DuelTrace trace = doc.trace();
		int me = Math.max(0, trace.recorderIndex());
		SummaryBuilder.Summary s = SummaryBuilder.forParticipant(doc, me);
		List<Entry> entries = new ArrayList<>(readIndex());
		entries.removeIf(e -> e.id().equals(trace.id()));
		entries.add(new Entry(trace.id(), trace.startedAtEpochMs(),
				trace.participants().get(trace.opponentOf(me)).name(), s.hitsLanded(), s.hitsTaken(), s.result(),
				trace.hasArena()));
		Files.writeString(dir.resolve(INDEX), GSON.toJson(entries), StandardCharsets.UTF_8);
	}

	public synchronized Optional<ReplayDocument> load(String id) {
		if (!id.matches("[A-Za-z0-9_-]+")) {
			return Optional.empty();
		}
		Path file = dir.resolve(id + ".json.gz");
		if (!Files.exists(file)) {
			return Optional.empty();
		}
		try {
			return Optional.of(ReplayCodec.decode(Files.readAllBytes(file)));
		} catch (IOException | RuntimeException e) {
			return Optional.empty();
		}
	}

	/** Removes a replay file and its index entry. */
	public synchronized void delete(String id) {
		if (!id.matches("[A-Za-z0-9_-]+")) {
			return;
		}
		try {
			Files.deleteIfExists(dir.resolve(id + ".json.gz"));
			List<Entry> entries = new ArrayList<>(readIndex());
			if (entries.removeIf(e -> e.id().equals(id))) {
				Files.writeString(dir.resolve(INDEX), GSON.toJson(entries), StandardCharsets.UTF_8);
			}
		} catch (IOException e) {
			// Leave it; the library just shows it again.
		}
	}

	/** Newest first. */
	public synchronized List<Entry> list() {
		List<Entry> entries = new ArrayList<>(readIndex());
		entries.sort(Comparator.comparingLong(Entry::startedAtEpochMs).reversed());
		return entries;
	}

	public Optional<Entry> latest() {
		List<Entry> entries = list();
		return entries.isEmpty() ? Optional.empty() : Optional.of(entries.get(0));
	}

	private List<Entry> readIndex() {
		Path file = dir.resolve(INDEX);
		if (!Files.exists(file)) {
			return List.of();
		}
		try {
			List<Entry> entries = GSON.fromJson(Files.readString(file, StandardCharsets.UTF_8),
					new TypeToken<List<Entry>>() {}.getType());
			return entries == null ? List.of() : entries;
		} catch (IOException | RuntimeException e) {
			return List.of();
		}
	}
}
