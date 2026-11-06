package com.vertexpvp.vertex.mcp;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.vertexpvp.vertex.core.ReplayCodec;
import com.vertexpvp.vertex.core.ReplayDocument;
import com.vertexpvp.vertex.core.detect.InteractionDetector;
import com.vertexpvp.vertex.core.explain.RuleBasedExplainer;
import com.vertexpvp.vertex.core.trace.CombatEvent;
import com.vertexpvp.vertex.core.trace.ControlInput;
import com.vertexpvp.vertex.core.trace.DuelTrace;
import com.vertexpvp.vertex.core.trace.ParticipantFrame;
import com.vertexpvp.vertex.core.trace.ParticipantInfo;
import com.vertexpvp.vertex.core.trace.TickFrame;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class VertexMcpServerTest {

	@TempDir
	Path dir;

	private VertexMcpServer server;

	@BeforeEach
	void setUp() throws Exception {
		Files.write(dir.resolve("20261028-120000-abc123.json.gz"), ReplayCodec.encode(sampleReplay()));
		server = new VertexMcpServer(dir);
	}

	/** I jump in and get hit while still airborne (tick 14), then land a crit at tick 40. */
	private static ReplayDocument sampleReplay() {
		int ticks = 80;
		List<TickFrame> frames = new ArrayList<>();
		double vy = 0.42;
		double y = 0;
		for (int t = 0; t < ticks; t++) {
			boolean airborne = t >= 10 && t < 21;
			double frameVy = 0;
			if (airborne) {
				y += vy;
				frameVy = vy;
				vy = (vy - 0.08) * 0.98;
			}
			ParticipantFrame me = new ParticipantFrame(0, airborne ? Math.max(0, y) : 0, 0, 0, 0, 0, 0, 0, frameVy, 0,
					!airborne, false, false, new ControlInput(true, false, false, false, t == 10, false, false),
					20f, 1f, 0, t == 40, 0);
			ParticipantFrame them = new ParticipantFrame(3, 0, 0, 0, 0, 0, 0, 0, 0, 0,
					true, false, false, null, 18f, -1f, -1, t == 14, 0);
			frames.add(new TickFrame(t, List.of(me, them)));
		}
		List<CombatEvent> events = List.of(
				new CombatEvent(14, CombatEvent.Type.HIT, 1, 0, CombatEvent.HitKind.STRONG, null, true, 2.9, 0.3, 0.36, 0.1),
				new CombatEvent(40, CombatEvent.Type.HIT, 0, 1, CombatEvent.HitKind.CRIT, CombatEvent.HitKind.CRIT, true,
						2.5, Double.NaN, Double.NaN, Double.NaN));
		DuelTrace trace = new DuelTrace(DuelTrace.FORMAT_VERSION, "20261028-120000-abc123", "client", 1_000L,
				"minecraft:overworld",
				List.of(new ParticipantInfo(0, "Me", true, "minecraft:player", true),
						new ParticipantInfo(1, "Rival", true, "minecraft:player", false)),
				frames, events, DuelTrace.EndReason.IDLE, -1, null);
		var interactions = new InteractionDetector().detect(trace);
		var explainer = new RuleBasedExplainer();
		return new ReplayDocument(trace, interactions, explainer.explain(trace, interactions), explainer.id());
	}

	private JsonObject request(String method, String paramsJson) {
		String line = "{\"jsonrpc\":\"2.0\",\"id\":7,\"method\":\"" + method + "\""
				+ (paramsJson == null ? "" : ",\"params\":" + paramsJson) + "}";
		return JsonParser.parseString(server.handle(line).orElseThrow()).getAsJsonObject();
	}

	/** Calls a tool and returns its text output parsed as JSON (plus the isError flag). */
	private JsonObject callTool(String name, String argsJson) {
		JsonObject result = request("tools/call", "{\"name\":\"" + name + "\",\"arguments\":" + argsJson + "}")
				.getAsJsonObject("result");
		String text = result.getAsJsonArray("content").get(0).getAsJsonObject().get("text").getAsString();
		JsonObject wrapped = new JsonObject();
		wrapped.addProperty("isError", result.get("isError").getAsBoolean());
		wrapped.addProperty("text", text);
		if (!result.get("isError").getAsBoolean()) {
			wrapped.add("json", JsonParser.parseString(text));
		}
		return wrapped;
	}

	@Test
	void initializeNegotiatesTheProtocolVersion() {
		JsonObject supported = request("initialize", "{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},"
				+ "\"clientInfo\":{\"name\":\"test\",\"version\":\"1\"}}").getAsJsonObject("result");
		assertEquals("2025-06-18", supported.get("protocolVersion").getAsString());
		assertEquals("vertex-replays", supported.getAsJsonObject("serverInfo").get("name").getAsString());
		assertTrue(supported.getAsJsonObject("capabilities").has("tools"));

		JsonObject unknown = request("initialize", "{\"protocolVersion\":\"1999-01-01\"}").getAsJsonObject("result");
		assertEquals(VertexMcpServer.SUPPORTED_PROTOCOLS.get(0), unknown.get("protocolVersion").getAsString());
	}

	@Test
	void notificationsGetNoResponse() {
		assertTrue(server.handle("{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\"}").isEmpty());
	}

	@Test
	void protocolErrors() {
		assertEquals(-32700, JsonParser.parseString(server.handle("not json").orElseThrow())
				.getAsJsonObject().getAsJsonObject("error").get("code").getAsInt());
		assertEquals(-32601, request("does/not/exist", null).getAsJsonObject("error").get("code").getAsInt());
		assertEquals(-32602, request("tools/call", "{\"name\":\"nope\",\"arguments\":{}}")
				.getAsJsonObject("error").get("code").getAsInt());
		assertTrue(request("ping", null).has("result"));
	}

	@Test
	void listsAllToolsWithSchemas() {
		JsonArray tools = request("tools/list", null).getAsJsonObject("result").getAsJsonArray("tools");
		assertEquals(7, tools.size());
		for (var t : tools) {
			JsonObject tool = t.getAsJsonObject();
			assertFalse(tool.get("description").getAsString().isBlank());
			assertEquals("object", tool.getAsJsonObject("inputSchema").get("type").getAsString());
		}
	}

	@Test
	void listReplays() {
		JsonArray list = callTool("list_replays", "{}").getAsJsonArray("json");
		assertEquals(1, list.size());
		JsonObject r = list.get(0).getAsJsonObject();
		assertEquals("20261028-120000-abc123", r.get("id").getAsString());
		assertEquals("Rival", r.get("opponent").getAsString());
	}

	@Test
	void getReplayLatestShowsServerVerdictsAndCoaching() {
		JsonObject r = callTool("get_replay", "{\"replay_id\":\"latest\"}").getAsJsonObject("json");
		JsonArray events = r.getAsJsonArray("events");
		assertEquals(2, events.size());
		assertEquals("CRIT", events.get(1).getAsJsonObject().get("kind").getAsString());
		assertEquals("CRIT", events.get(1).getAsJsonObject().get("my_client_predicted").getAsString());
		assertTrue(r.getAsJsonArray("exchanges").get(0).getAsJsonObject().has("coaching_for_me"));
	}

	@Test
	void getExchangeHasTechniquesCoachingAndTimeline() {
		JsonObject x = callTool("get_exchange", "{\"replay_id\":\"20261028-120000-abc123\",\"exchange\":1}")
				.getAsJsonObject("json");
		String techniques = x.getAsJsonArray("techniques").toString();
		assertTrue(techniques.contains("FAILED_JUMP_IN"), techniques);
		JsonObject coaching = x.getAsJsonArray("coaching").get(0).getAsJsonObject();
		assertEquals("me", coaching.get("for").getAsString());
		assertEquals("MISTAKE", coaching.get("verdict").getAsString());
		assertFalse(x.getAsJsonArray("timeline").isEmpty());
		assertTrue(x.getAsJsonArray("timeline").toString().contains("keys=?"), "opponent keys shown as unknown");
	}

	@Test
	void badArgumentsAreToolErrorsTheAiCanSee() {
		assertTrue(callTool("get_exchange", "{\"replay_id\":\"latest\",\"exchange\":99}").get("isError").getAsBoolean());
		assertTrue(callTool("get_replay", "{\"replay_id\":\"missing\"}").get("isError").getAsBoolean());
		assertTrue(callTool("get_replay", "{\"replay_id\":\"../../etc\"}").get("isError").getAsBoolean());
		assertTrue(callTool("find_moments", "{\"technique\":\"FLYING\"}").get("isError").getAsBoolean());
	}

	@Test
	void findMomentsSearchesAcrossReplays() {
		JsonObject mine = callTool("find_moments", "{\"technique\":\"failed_jump_in\"}").getAsJsonObject("json");
		assertEquals(1, mine.get("total_found").getAsInt());
		assertEquals("me", mine.getAsJsonArray("moments").get(0).getAsJsonObject().get("who").getAsString());

		JsonObject theirs = callTool("find_moments", "{\"technique\":\"FAILED_JUMP_IN\",\"who\":\"opponent\"}")
				.getAsJsonObject("json");
		assertEquals(0, theirs.get("total_found").getAsInt());
	}

	@Test
	void getFramesIsCapped() {
		JsonObject f = callTool("get_frames", "{\"replay_id\":\"latest\",\"from_tick\":0,\"to_tick\":5000}")
				.getAsJsonObject("json");
		assertEquals(79, f.get("to_tick").getAsInt(), "clamped to the last tick");
		assertEquals(80, f.getAsJsonArray("timeline").size());
	}

	@Test
	void statsAcrossFights() {
		JsonObject s = callTool("get_stats", "{}").getAsJsonObject("json");
		assertEquals(1, s.get("fights").getAsInt());
		assertEquals(1, s.get("crits_landed").getAsInt());
		assertEquals("1/1", s.get("my_crit_attempts_the_server_agreed_with").getAsString());
		assertTrue(s.getAsJsonObject("my_mistakes").size() > 0);
	}

	@Test
	void listTechniquesDefinesEveryTechnique() {
		JsonArray all = callTool("list_techniques", "{}").getAsJsonArray("json");
		for (var t : all) {
			assertFalse(t.getAsJsonObject().get("rule").isJsonNull(), t.toString());
		}
	}
}
