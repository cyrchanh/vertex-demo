package com.vertexpvp.vertex.mcp;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.vertexpvp.vertex.core.ReplayDocument;
import com.vertexpvp.vertex.core.detect.Interaction;
import com.vertexpvp.vertex.core.detect.Observation;
import com.vertexpvp.vertex.core.detect.Technique;
import com.vertexpvp.vertex.core.explain.Explanation;
import com.vertexpvp.vertex.core.explain.SummaryBuilder;
import com.vertexpvp.vertex.core.trace.CombatEvent;
import com.vertexpvp.vertex.core.trace.ControlInput;
import com.vertexpvp.vertex.core.trace.DuelTrace;
import com.vertexpvp.vertex.core.trace.ParticipantFrame;
import com.vertexpvp.vertex.core.trace.ParticipantInfo;

import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.EnumMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * The tools this MCP server offers. Every tool is read-only and returns JSON text. "me" always
 * means the player who recorded the replay (whose keys are exact); "opponent" is the other fighter.
 */
final class ReplayTools {

	/** A tool call that failed in a way the AI should see and can fix (bad id, bad range...). */
	static final class ToolError extends Exception {
		ToolError(String message) {
			super(message);
		}
	}

	private static final Gson GSON = new GsonBuilder().setPrettyPrinting().serializeSpecialFloatingPointValues().create();
	private static final DateTimeFormatter DATE = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm").withZone(ZoneId.systemDefault());
	private static final int MAX_FRAMES = 200;

	private static final Map<Technique, String> DEFINITIONS = new EnumMap<>(Technique.class);

	static {
		DEFINITIONS.put(Technique.FIRST_HIT, "The first landed hit of the fight.");
		DEFINITIONS.put(Technique.CRIT_LANDED, "Server-confirmed crit (airborne, falling, not sprinting, fully charged): 1.5x damage, little knockback.");
		DEFINITIONS.put(Technique.SPRINT_HIT_LANDED, "Server-confirmed sprint hit (sprinting, fully charged): extra knockback.");
		DEFINITIONS.put(Technique.FALLING_SPRINT_HIT, "Sprint hit thrown while airborne and falling.");
		DEFINITIONS.put(Technique.WEAK_HIT, "Hit thrown before the attack recharged (strength <= 0.9): reduced damage, no crit/sprint bonus.");
		DEFINITIONS.put(Technique.SPRINT_RESET, "Follow-up sprint hit by the same attacker: they re-sprinted (W-tap), since vanilla ends sprint after a sprint hit.");
		DEFINITIONS.put(Technique.NO_SPRINT_RESET, "Fully charged follow-up that was neither crit nor sprint hit: sprint not reset since the last hit.");
		DEFINITIONS.put(Technique.THREE_BLOCK_HIT, "Hit landed from >= 2.6 blocks: the edge of hit range.");
		DEFINITIONS.put(Technique.CLOSE_RANGE_HIT, "Hit landed from <= 1.6 blocks, where sprint knockback buys little spacing.");
		DEFINITIONS.put(Technique.S_TAP, "Back held 1-4 of the 8 ticks before a hit and released by the hit. Needs exact keys (recorder only).");
		DEFINITIONS.put(Technique.HIT_WHILE_RISING, "Victim airborne with upward speed > 0.05 when hit (jump-arc vulnerability). Estimated for the opponent.");
		DEFINITIONS.put(Technique.FAILED_JUMP_IN, "Victim jumped in within the last 15 ticks, was still airborne, and had landed nothing since. Estimated for the opponent.");
		DEFINITIONS.put(Technique.CROUCHING_WHEN_HIT, "Victim was crouching when hit.");
		DEFINITIONS.put(Technique.JUMP_RESET, "Victim grounded just before the hit and held jump from 1 tick before to 2 after. Needs exact keys (recorder only).");
		DEFINITIONS.put(Technique.BACKED_OFF_AFTER_HIT, "Victim held back >= 2 of the 6 ticks after a hit. Needs exact keys (recorder only).");
		DEFINITIONS.put(Technique.NO_RESPONSE, "Took a sprint hit with no jump reset, backing off, or hit back within 12 ticks. Needs exact keys (recorder only).");
		DEFINITIONS.put(Technique.COMBO, "3+ hits in a row by one side without being hit back.");
		DEFINITIONS.put(Technique.TRADE, "Both sides landed hits in the same exchange.");
		DEFINITIONS.put(Technique.WHIFF, "A swing that matched no hit within 2 ticks while the opponent was within 5 blocks.");
		DEFINITIONS.put(Technique.WHIFF_PUNISHED, "A whiff followed by the opponent landing a hit within 15 ticks.");
		DEFINITIONS.put(Technique.SERVER_DISAGREED, "The recorder's client predicted a crit/sprint hit (or not) and the server counted something else: latency/desync.");
	}

	private final ReplayLibrary library;

	ReplayTools(ReplayLibrary library) {
		this.library = library;
	}

	// ------------------------------------------------------------------ tool catalogue

	JsonArray definitions() {
		JsonArray tools = new JsonArray();
		tools.add(tool("list_replays",
				"List recorded fights, newest first: id, date, opponent, result, hit counts (crits/sprint hits) and length.",
				props(prop("limit", "integer", "Max replays to return (default 20).")), List.of()));
		tools.add(tool("get_replay",
				"Overview of one fight: participants, result, every hit (who, kind as decided by the server, range, knockback), "
						+ "and every exchange with its coaching headline for both fighters. Use id \"latest\" for the newest.",
				props(prop("replay_id", "string", "Replay id from list_replays, or \"latest\".")), List.of("replay_id")));
		tools.add(tool("get_exchange",
				"Full detail of one exchange: recognised techniques, the coaching (what happened / why / do instead / why) for both "
						+ "fighters, its hits, and a tick-by-tick timeline of both fighters (position, vertical speed, ground, sprint, "
						+ "crouch, held keys, attack charge, swings, distance).",
				props(prop("replay_id", "string", "Replay id or \"latest\"."),
						prop("exchange", "integer", "Exchange number, starting at 1.")), List.of("replay_id", "exchange")));
		tools.add(tool("get_frames",
				"Raw tick-by-tick timeline of both fighters for a tick range (max " + MAX_FRAMES + " ticks; 20 ticks = 1 second).",
				props(prop("replay_id", "string", "Replay id or \"latest\"."),
						prop("from_tick", "integer", "First tick (inclusive)."),
						prop("to_tick", "integer", "Last tick (inclusive).")), List.of("replay_id", "from_tick", "to_tick")));
		tools.add(tool("find_moments",
				"Search all replays for one technique (see list_techniques), e.g. every time I got hit while rising. "
						+ "Returns each occurrence with replay, exchange, tick and that exchange's coaching verdict for me.",
				props(prop("technique", "string", "Technique name, e.g. FAILED_JUMP_IN, NO_SPRINT_RESET, CRIT_LANDED."),
						prop("who", "string", "\"me\" (default), \"opponent\", or \"any\"."),
						prop("limit", "integer", "Max results (default 30).")), List.of("technique")));
		tools.add(tool("get_stats",
				"Totals across recent fights: wins/losses, hits by kind, how often each technique happened for me and opponents, "
						+ "and my most common mistakes and strengths.",
				props(prop("last_n", "integer", "Only the newest N fights (default: all).")), List.of()));
		tools.add(tool("list_techniques",
				"Definitions of every technique the detector recognises, with the exact rule and whether it needs exact keys.",
				props(), List.of()));
		return tools;
	}

	private static JsonObject tool(String name, String description, JsonObject properties, List<String> required) {
		JsonObject schema = new JsonObject();
		schema.addProperty("type", "object");
		schema.add("properties", properties);
		JsonArray req = new JsonArray();
		required.forEach(req::add);
		schema.add("required", req);
		JsonObject t = new JsonObject();
		t.addProperty("name", name);
		t.addProperty("description", description);
		t.add("inputSchema", schema);
		return t;
	}

	private static JsonObject props(JsonObject... entries) {
		JsonObject o = new JsonObject();
		for (JsonObject e : entries) {
			o.add(e.get("_name").getAsString(), e.get("_schema"));
		}
		return o;
	}

	private static JsonObject prop(String name, String type, String description) {
		JsonObject schema = new JsonObject();
		schema.addProperty("type", type);
		schema.addProperty("description", description);
		JsonObject wrapper = new JsonObject();
		wrapper.addProperty("_name", name);
		wrapper.add("_schema", schema);
		return wrapper;
	}

	// ------------------------------------------------------------------ dispatch

	boolean has(String name) {
		for (JsonElement t : definitions()) {
			if (t.getAsJsonObject().get("name").getAsString().equals(name)) {
				return true;
			}
		}
		return false;
	}

	String call(String name, JsonObject args) throws ToolError {
		JsonElement result = switch (name) {
			case "list_replays" -> listReplays(intArg(args, "limit", 20));
			case "get_replay" -> getReplay(doc(args));
			case "get_exchange" -> getExchange(doc(args), intArg(args, "exchange", -1));
			case "get_frames" -> getFrames(doc(args), intArg(args, "from_tick", -1), intArg(args, "to_tick", -1));
			case "find_moments" -> findMoments(stringArg(args, "technique", ""), stringArg(args, "who", "me"), intArg(args, "limit", 30));
			case "get_stats" -> getStats(intArg(args, "last_n", Integer.MAX_VALUE));
			case "list_techniques" -> listTechniques();
			default -> throw new ToolError("Unknown tool: " + name);
		};
		return GSON.toJson(result);
	}

	private ReplayDocument doc(JsonObject args) throws ToolError {
		String id = stringArg(args, "replay_id", "latest");
		return library.find(id).orElseThrow(() -> new ToolError(
				"No replay '" + id + "' in " + library.directory() + ". Use list_replays to see available ids."));
	}

	// ------------------------------------------------------------------ tools

	private JsonElement listReplays(int limit) {
		JsonArray out = new JsonArray();
		for (ReplayDocument doc : library.all()) {
			if (out.size() >= Math.max(1, limit)) {
				break;
			}
			DuelTrace t = doc.trace();
			int me = me(t);
			SummaryBuilder.Summary s = SummaryBuilder.forParticipant(doc, me);
			JsonObject o = new JsonObject();
			o.addProperty("id", t.id());
			o.addProperty("date", DATE.format(Instant.ofEpochMilli(t.startedAtEpochMs())));
			o.addProperty("me", t.participants().get(me).name());
			o.addProperty("opponent", t.participants().get(t.opponentOf(me)).name());
			o.addProperty("opponent_is_player", t.participants().get(t.opponentOf(me)).player());
			o.addProperty("result", s.result());
			o.addProperty("seconds", round(t.frames().size() / 20.0));
			o.addProperty("summary", s.statsLine());
			o.addProperty("exchanges", doc.interactions().size());
			o.addProperty("end_reason", t.endReason().name());
			out.add(o);
		}
		if (out.isEmpty()) {
			JsonObject empty = new JsonObject();
			empty.addProperty("message", "No replays found in " + library.directory());
			return empty;
		}
		return out;
	}

	private JsonElement getReplay(ReplayDocument doc) {
		DuelTrace t = doc.trace();
		int me = me(t);
		SummaryBuilder.Summary s = SummaryBuilder.forParticipant(doc, me);
		JsonObject o = new JsonObject();
		o.addProperty("id", t.id());
		o.addProperty("date", DATE.format(Instant.ofEpochMilli(t.startedAtEpochMs())));
		o.addProperty("ticks", t.frames().size());
		o.addProperty("seconds", round(t.frames().size() / 20.0));
		o.addProperty("dimension", t.dimension());
		o.addProperty("end_reason", t.endReason().name());
		o.addProperty("winner", t.winner() < 0 ? "nobody died" : t.participants().get(t.winner()).name());
		o.addProperty("result_for_me", s.result());
		o.addProperty("summary_for_me", s.statsLine());
		o.addProperty("biggest_mistake", s.biggestMistake());
		o.addProperty("best_moment", s.bestMoment());
		o.addProperty("explainer", doc.explainerId());
		o.add("participants", participants(t));

		JsonArray hits = new JsonArray();
		for (CombatEvent e : t.events()) {
			hits.add(event(t, e));
		}
		o.add("events", hits);

		JsonArray exchanges = new JsonArray();
		for (Interaction in : doc.interactions()) {
			JsonObject x = new JsonObject();
			x.addProperty("exchange", in.index() + 1);
			x.addProperty("ticks", in.startTick() + "-" + in.endTick());
			x.addProperty("hits", hitsLine(t, in));
			for (ParticipantInfo p : t.participants()) {
				doc.explanationFor(in.index(), p.index()).ifPresent(e ->
						x.addProperty("coaching_for_" + role(t, p.index()), e.verdict() + ": " + e.headline()));
			}
			exchanges.add(x);
		}
		o.add("exchanges", exchanges);
		o.addProperty("notes", "20 ticks = 1 second. 'me' = " + t.participants().get(me).name()
				+ " (recorded this; keys exact). Opponent keys are unknown; opponent motion is as seen by my client.");
		return o;
	}

	private JsonElement getExchange(ReplayDocument doc, int number) throws ToolError {
		if (number < 1 || number > doc.interactions().size()) {
			throw new ToolError("Exchange must be 1.." + doc.interactions().size() + " for replay " + doc.trace().id());
		}
		DuelTrace t = doc.trace();
		Interaction in = doc.interactions().get(number - 1);
		JsonObject o = new JsonObject();
		o.addProperty("replay_id", t.id());
		o.addProperty("exchange", number);
		o.addProperty("of", doc.interactions().size());
		o.addProperty("ticks", in.startTick() + "-" + in.endTick());
		o.addProperty("first_hit_tick", in.anchorTick());
		o.addProperty("hits", hitsLine(t, in));

		JsonArray observations = new JsonArray();
		for (Observation ob : in.observations()) {
			JsonObject j = new JsonObject();
			j.addProperty("tick", ob.tick());
			j.addProperty("technique", ob.technique().name());
			j.addProperty("who", ob.actor() < 0 ? "both" : role(t, ob.actor()));
			if (!ob.detail().isEmpty()) {
				j.addProperty("detail", ob.detail());
			}
			if (ob.inferred()) {
				j.addProperty("estimated", true);
			}
			observations.add(j);
		}
		o.add("techniques", observations);

		JsonArray coaching = new JsonArray();
		for (ParticipantInfo p : t.participants()) {
			doc.explanationFor(in.index(), p.index()).ifPresent(e -> coaching.add(explanation(t, e)));
		}
		o.add("coaching", coaching);

		JsonArray hits = new JsonArray();
		for (int idx : in.eventIndices()) {
			hits.add(event(t, t.events().get(idx)));
		}
		o.add("hit_events", hits);
		o.add("timeline", timeline(t, in.startTick(), in.endTick()));
		o.addProperty("timeline_legend", legend());
		return o;
	}

	private JsonElement getFrames(ReplayDocument doc, int from, int to) throws ToolError {
		DuelTrace t = doc.trace();
		if (from < 0 || to < from || from > t.lastTick()) {
			throw new ToolError("Tick range must satisfy 0 <= from_tick <= to_tick, within 0.." + t.lastTick());
		}
		int end = Math.min(Math.min(to, t.lastTick()), from + MAX_FRAMES - 1);
		JsonObject o = new JsonObject();
		o.addProperty("replay_id", t.id());
		o.addProperty("from_tick", from);
		o.addProperty("to_tick", end);
		if (end < to) {
			o.addProperty("truncated", "Only " + MAX_FRAMES + " ticks per call; ask again from tick " + (end + 1));
		}
		o.add("timeline", timeline(t, from, end));
		o.addProperty("timeline_legend", legend());
		return o;
	}

	private JsonElement findMoments(String techniqueName, String who, int limit) throws ToolError {
		Technique technique;
		try {
			technique = Technique.valueOf(techniqueName.trim().toUpperCase(Locale.ROOT));
		} catch (IllegalArgumentException e) {
			throw new ToolError("Unknown technique '" + techniqueName + "'. Use list_techniques for valid names.");
		}
		String whoNorm = who == null ? "me" : who.toLowerCase(Locale.ROOT);
		JsonArray out = new JsonArray();
		int total = 0;
		for (ReplayDocument doc : library.all()) {
			DuelTrace t = doc.trace();
			int me = me(t);
			for (Interaction in : doc.interactions()) {
				for (Observation ob : in.observations()) {
					if (ob.technique() != technique || !matchesWho(ob.actor(), me, t, whoNorm)) {
						continue;
					}
					total++;
					if (out.size() >= Math.max(1, limit)) {
						continue;
					}
					JsonObject j = new JsonObject();
					j.addProperty("replay_id", t.id());
					j.addProperty("date", DATE.format(Instant.ofEpochMilli(t.startedAtEpochMs())));
					j.addProperty("opponent", t.participants().get(t.opponentOf(me)).name());
					j.addProperty("exchange", in.index() + 1);
					j.addProperty("tick", ob.tick());
					j.addProperty("who", ob.actor() < 0 ? "both" : role(t, ob.actor()));
					if (!ob.detail().isEmpty()) {
						j.addProperty("detail", ob.detail());
					}
					if (ob.inferred()) {
						j.addProperty("estimated", true);
					}
					doc.explanationFor(in.index(), me).ifPresent(e ->
							j.addProperty("my_coaching", e.verdict() + ": " + e.headline()));
					out.add(j);
				}
			}
		}
		JsonObject o = new JsonObject();
		o.addProperty("technique", technique.name());
		o.addProperty("definition", DEFINITIONS.get(technique));
		o.addProperty("who", whoNorm);
		o.addProperty("total_found", total);
		o.add("moments", out);
		return o;
	}

	private JsonElement getStats(int lastN) {
		List<ReplayDocument> docs = library.all();
		docs = docs.subList(0, Math.min(docs.size(), Math.max(1, lastN)));
		int wins = 0, losses = 0, landed = 0, taken = 0, critsL = 0, critsT = 0, sprintL = 0, sprintT = 0;
		Map<String, Integer> mine = new LinkedHashMap<>();
		Map<String, Integer> theirs = new LinkedHashMap<>();
		Map<String, Integer> mistakes = new LinkedHashMap<>();
		Map<String, Integer> strengths = new LinkedHashMap<>();
		int predictedCrits = 0, confirmedPredictedCrits = 0;

		for (ReplayDocument doc : docs) {
			DuelTrace t = doc.trace();
			int me = me(t);
			SummaryBuilder.Summary s = SummaryBuilder.forParticipant(doc, me);
			if (t.winner() == me) wins++;
			else if (t.winner() == t.opponentOf(me)) losses++;
			landed += s.hitsLanded();
			taken += s.hitsTaken();
			critsL += s.critsLanded();
			critsT += s.critsTaken();
			sprintL += s.sprintHitsLanded();
			sprintT += s.sprintHitsTaken();
			for (CombatEvent e : t.events()) {
				if (e.attacker() == me && e.predictedKind() == CombatEvent.HitKind.CRIT) {
					predictedCrits++;
					if (e.kind() == CombatEvent.HitKind.CRIT) confirmedPredictedCrits++;
				}
			}
			for (Interaction in : doc.interactions()) {
				for (Observation ob : in.observations()) {
					if (ob.actor() == me) mine.merge(ob.technique().name(), 1, Integer::sum);
					else if (ob.actor() >= 0) theirs.merge(ob.technique().name(), 1, Integer::sum);
				}
				doc.explanationFor(in.index(), me).ifPresent(e -> {
					String headline = e.headline().replaceAll(" \\(.*\\)$", "");
					if (e.verdict() == Explanation.Verdict.MISTAKE) mistakes.merge(headline, 1, Integer::sum);
					if (e.verdict() == Explanation.Verdict.GOOD) strengths.merge(headline, 1, Integer::sum);
				});
			}
		}
		JsonObject o = new JsonObject();
		o.addProperty("fights", docs.size());
		o.addProperty("wins", wins);
		o.addProperty("losses", losses);
		o.addProperty("no_death", docs.size() - wins - losses);
		o.addProperty("hits_landed", landed);
		o.addProperty("hits_taken", taken);
		o.addProperty("crits_landed", critsL);
		o.addProperty("crits_taken", critsT);
		o.addProperty("sprint_hits_landed", sprintL);
		o.addProperty("sprint_hits_taken", sprintT);
		o.addProperty("crit_rate_of_my_hits", landed == 0 ? null : round(100.0 * critsL / landed) + "%");
		o.addProperty("my_crit_attempts_the_server_agreed_with",
				predictedCrits == 0 ? "no crit attempts" : confirmedPredictedCrits + "/" + predictedCrits);
		o.add("my_techniques", GSON.toJsonTree(sortByCount(mine)));
		o.add("opponent_techniques", GSON.toJsonTree(sortByCount(theirs)));
		o.add("my_mistakes", GSON.toJsonTree(sortByCount(mistakes)));
		o.add("my_strengths", GSON.toJsonTree(sortByCount(strengths)));
		return o;
	}

	private JsonElement listTechniques() {
		JsonArray out = new JsonArray();
		for (Technique t : Technique.values()) {
			JsonObject j = new JsonObject();
			j.addProperty("name", t.name());
			j.addProperty("rule", DEFINITIONS.get(t));
			out.add(j);
		}
		return out;
	}

	// ------------------------------------------------------------------ helpers

	private static int me(DuelTrace t) {
		return Math.max(0, t.recorderIndex());
	}

	private static String role(DuelTrace t, int participant) {
		return participant == me(t) ? "me" : "opponent";
	}

	private static boolean matchesWho(int actor, int me, DuelTrace t, String who) {
		return switch (who) {
			case "any" -> true;
			case "opponent" -> actor == t.opponentOf(me);
			default -> actor == me;
		};
	}

	private static JsonArray participants(DuelTrace t) {
		JsonArray out = new JsonArray();
		for (ParticipantInfo p : t.participants()) {
			JsonObject j = new JsonObject();
			j.addProperty("role", role(t, p.index()));
			j.addProperty("name", p.name());
			j.addProperty("player", p.player());
			j.addProperty("entity_type", p.entityType());
			j.addProperty("keys_recorded", p.recorder());
			out.add(j);
		}
		return out;
	}

	private static JsonObject event(DuelTrace t, CombatEvent e) {
		JsonObject j = new JsonObject();
		j.addProperty("tick", e.tick());
		j.addProperty("type", e.type().name());
		j.addProperty("attacker", e.attacker() < 0 ? "?" : role(t, e.attacker()));
		j.addProperty("victim", role(t, e.victim()));
		if (e.type() != CombatEvent.Type.DEATH) {
			j.addProperty("kind", e.kind().name());
			j.addProperty("server_confirmed", e.serverConfirmed());
			if (e.predictedKind() != null) {
				j.addProperty("my_client_predicted", e.predictedKind().name());
			}
			if (!Double.isNaN(e.range())) {
				j.addProperty("range_blocks", round(e.range()));
			}
			if (e.hasKnockback()) {
				j.addProperty("knockback_horizontal", round(e.horizontalKnockback()));
				j.addProperty("knockback_vertical", round(e.knockbackY()));
			}
		}
		return j;
	}

	private static JsonObject explanation(DuelTrace t, Explanation e) {
		JsonObject j = new JsonObject();
		j.addProperty("for", role(t, e.perspective()));
		j.addProperty("verdict", e.verdict().name());
		j.addProperty("headline", e.headline());
		j.addProperty("what_happened", e.what());
		j.addProperty("why", e.why());
		j.addProperty("do_instead", e.instead());
		j.addProperty("why_that_works", e.insteadWhy());
		j.addProperty("source", e.source());
		j.addProperty("partly_estimated", e.inferred());
		JsonArray notes = new JsonArray();
		e.notes().forEach(notes::add);
		j.add("notes", notes);
		return j;
	}

	private static String hitsLine(DuelTrace t, Interaction in) {
		int me = me(t);
		return "me " + in.hitsBy()[me] + " - opponent " + in.hitsBy()[t.opponentOf(me)];
	}

	/** One compact row per tick: "me" and "opponent" columns. */
	private static JsonArray timeline(DuelTrace t, int from, int to) {
		JsonArray rows = new JsonArray();
		int me = me(t);
		int opp = t.opponentOf(me);
		for (int tick = from; tick <= Math.min(to, t.lastTick()); tick++) {
			ParticipantFrame a = t.state(tick, me);
			ParticipantFrame b = t.state(tick, opp);
			JsonObject row = new JsonObject();
			row.addProperty("t", tick);
			row.addProperty("dist", round(a.distanceTo(b)));
			row.addProperty("me", frame(a));
			row.addProperty("opp", frame(b));
			rows.add(row);
		}
		return rows;
	}

	private static String frame(ParticipantFrame f) {
		StringBuilder s = new StringBuilder();
		s.append(f.onGround() ? "ground" : (f.vy() > 0 ? "rising" : "falling"));
		s.append(String.format(Locale.ROOT, " vy=%.2f", f.vy()));
		s.append(String.format(Locale.ROOT, " spd=%.2f", f.horizontalSpeed()));
		if (f.sprinting()) s.append(" SPRINT");
		if (f.sneaking()) s.append(" CROUCH");
		s.append(" keys=").append(keys(f.input()));
		if (f.attackStrength() >= 0) s.append(String.format(Locale.ROOT, " charge=%.2f", f.attackStrength()));
		if (f.swingStart()) s.append(" SWING");
		if (f.hurtTime() > 0) s.append(" hurt=").append(f.hurtTime());
		s.append(String.format(Locale.ROOT, " hp=%.1f", f.health()));
		if (f.absorption() > 0) s.append(String.format(Locale.ROOT, " absorb=%.1f", f.absorption()));
		if (f.usingItem()) s.append(" USING");
		if (f.offHand() != null && !f.offHand().equals("minecraft:air")) s.append(" offhand=").append(f.offHand());
		if (f.pingMs() != null) s.append(" ping=").append(f.pingMs());
		if (f.effects() != null && !f.effects().isEmpty()) {
			s.append(" effects=");
			f.effects().forEach(e -> s.append(e.id()).append(':').append(e.amplifier() + 1).append(' '));
			s.setLength(s.length() - 1);
		}
		return s.toString();
	}

	private static String keys(ControlInput in) {
		if (in == null) {
			return "?";
		}
		StringBuilder s = new StringBuilder();
		if (in.forward()) s.append('W');
		if (in.left()) s.append('A');
		if (in.back()) s.append('S');
		if (in.right()) s.append('D');
		if (in.jump()) s.append("+jump");
		if (in.shift()) s.append("+crouch");
		if (in.sprint()) s.append("+sprintkey");
		return s.isEmpty() ? "-" : s.toString();
	}

	private static String legend() {
		return "Each row is one tick (20/s). dist = blocks between fighters. ground/rising/falling and vy = vertical "
				+ "speed; spd = horizontal speed (blocks/tick); keys = held keys (W/A/S/D, +jump, +crouch, +sprintkey), "
				+ "'?' = not visible (opponent), '-' = nothing held; charge = attack charge 0-1 (>0.9 needed for "
				+ "crits/sprint hits); SWING = a swing started; hurt = hurt animation counter (10 on the tick damage lands).";
	}

	private static Map<String, Integer> sortByCount(Map<String, Integer> counts) {
		Map<String, Integer> sorted = new LinkedHashMap<>();
		counts.entrySet().stream()
				.sorted(Map.Entry.<String, Integer>comparingByValue().reversed())
				.forEach(e -> sorted.put(e.getKey(), e.getValue()));
		return sorted;
	}

	private static double round(double v) {
		return Math.round(v * 100.0) / 100.0;
	}

	private static int intArg(JsonObject args, String name, int fallback) throws ToolError {
		if (args == null || !args.has(name) || args.get(name).isJsonNull()) {
			return fallback;
		}
		try {
			return args.get(name).getAsInt();
		} catch (RuntimeException e) {
			throw new ToolError("'" + name + "' must be an integer");
		}
	}

	private static String stringArg(JsonObject args, String name, String fallback) {
		if (args == null || !args.has(name) || args.get(name).isJsonNull()) {
			return fallback;
		}
		return args.get(name).getAsString();
	}
}
