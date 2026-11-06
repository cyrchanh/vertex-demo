package com.vertexpvp.vertex.mcp;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.List;
import java.util.Optional;

/**
 * Vertex MCP server: lets MCP-capable AI apps (Claude Code, Claude Desktop, ...) read your Vertex
 * replays through a few read-only tools (see {@link ReplayTools}).
 *
 * <p>Transport is MCP's standard "stdio": one JSON-RPC 2.0 message per line on stdin, one response
 * per line on stdout. stdout carries protocol messages only; anything else goes to stderr.
 * Implemented directly (it's a small subset of MCP: initialize, ping, tools/list, tools/call)
 * to keep this a single dependency-light jar; the official MCP Java SDK could replace this class
 * later without touching the tools.
 *
 * <p>Usage: {@code java -jar vertex-mcp.jar [--replays <folder>]}. Without {@code --replays} it
 * uses the {@code VERTEX_REPLAYS} environment variable, then {@code <.minecraft>/vertex-replays}.
 */
public final class VertexMcpServer {

	static final String NAME = "vertex-replays";
	static final String VERSION = "0.2.0";
	/** Newest first. We only use tools, which work the same in all of these. */
	static final List<String> SUPPORTED_PROTOCOLS = List.of("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05");

	private static final Gson GSON = new Gson();

	private final ReplayTools tools;
	private final ReplayLibrary library;

	VertexMcpServer(Path replayDir) {
		this.library = new ReplayLibrary(replayDir);
		this.tools = new ReplayTools(library);
	}

	public static void main(String[] args) throws Exception {
		Path dir = replayDir(args);
		System.err.println("[vertex-mcp] serving replays from " + dir);
		VertexMcpServer server = new VertexMcpServer(dir);
		PrintStream out = new PrintStream(System.out, true, StandardCharsets.UTF_8);
		try (BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8))) {
			String line;
			while ((line = in.readLine()) != null) {
				if (line.isBlank()) {
					continue;
				}
				server.handle(line).ifPresent(out::println);
			}
		}
	}

	static Path replayDir(String[] args) {
		for (int i = 0; i < args.length - 1; i++) {
			if (args[i].equals("--replays")) {
				return Path.of(args[i + 1]);
			}
		}
		String env = System.getenv("VERTEX_REPLAYS");
		if (env != null && !env.isBlank()) {
			return Path.of(env);
		}
		String appData = System.getenv("APPDATA");
		Path minecraft = appData != null
				? Path.of(appData, ".minecraft")
				: Path.of(System.getProperty("user.home"), ".minecraft");
		return minecraft.resolve("vertex-replays");
	}

	/** Handle one incoming line; returns the response line, or empty for notifications. */
	Optional<String> handle(String line) {
		JsonObject message;
		try {
			message = JsonParser.parseString(line).getAsJsonObject();
		} catch (RuntimeException e) {
			return Optional.of(error(JsonNull.INSTANCE, -32700, "Parse error"));
		}
		JsonElement id = message.get("id");
		String method = message.has("method") ? message.get("method").getAsString() : null;
		if (id == null) {
			return Optional.empty(); // notification (e.g. notifications/initialized): no response
		}
		if (method == null) {
			return Optional.of(error(id, -32600, "Invalid request"));
		}
		JsonObject params = message.has("params") && message.get("params").isJsonObject()
				? message.getAsJsonObject("params") : new JsonObject();
		return Optional.of(switch (method) {
			case "initialize" -> result(id, initialize(params));
			case "ping" -> result(id, new JsonObject());
			case "tools/list" -> {
				JsonObject r = new JsonObject();
				r.add("tools", tools.definitions());
				yield result(id, r);
			}
			case "tools/call" -> {
				String name = params.has("name") ? params.get("name").getAsString() : "";
				yield tools.has(name) ? result(id, callTool(params)) : error(id, -32602, "Unknown tool: " + name);
			}
			default -> error(id, -32601, "Method not found: " + method);
		});
	}

	private JsonObject initialize(JsonObject params) {
		String requested = params.has("protocolVersion") ? params.get("protocolVersion").getAsString() : null;
		String version = SUPPORTED_PROTOCOLS.contains(requested) ? requested : SUPPORTED_PROTOCOLS.get(0);

		JsonObject capabilities = new JsonObject();
		JsonObject toolsCap = new JsonObject();
		toolsCap.addProperty("listChanged", false);
		capabilities.add("tools", toolsCap);

		JsonObject serverInfo = new JsonObject();
		serverInfo.addProperty("name", NAME);
		serverInfo.addProperty("version", VERSION);

		JsonObject r = new JsonObject();
		r.addProperty("protocolVersion", version);
		r.add("capabilities", capabilities);
		r.add("serverInfo", serverInfo);
		r.addProperty("instructions", "Read-only access to Vertex Minecraft sword-PvP replays recorded by the player. "
				+ "'me' is the player who recorded (their keys are exact); 'opponent' keys are unknown. Hit kinds "
				+ "(CRIT, SPRINT_HIT, STRONG, WEAK) are the server's own verdict. 20 ticks = 1 second. Start with "
				+ "list_replays or get_stats, drill into get_replay / get_exchange, search patterns with find_moments. "
				+ "Coaching text comes from the player's own Sword guides; ground any advice in the tick data.");
		return r;
	}

	private JsonObject callTool(JsonObject params) {
		String name = params.has("name") ? params.get("name").getAsString() : "";
		JsonObject args = params.has("arguments") && params.get("arguments").isJsonObject()
				? params.getAsJsonObject("arguments") : new JsonObject();
		String text;
		boolean isError = false;
		try {
			text = tools.call(name, args);
		} catch (ReplayTools.ToolError e) {
			text = e.getMessage();
			isError = true;
		} catch (RuntimeException e) {
			text = "Internal error: " + e;
			isError = true;
		}
		JsonObject content = new JsonObject();
		content.addProperty("type", "text");
		content.addProperty("text", text);
		JsonArray contents = new JsonArray();
		contents.add(content);
		JsonObject r = new JsonObject();
		r.add("content", contents);
		r.addProperty("isError", isError);
		return r;
	}

	private static String result(JsonElement id, JsonObject result) {
		JsonObject r = new JsonObject();
		r.addProperty("jsonrpc", "2.0");
		r.add("id", id);
		r.add("result", result);
		return GSON.toJson(r);
	}

	private static String error(JsonElement id, int code, String message) {
		JsonObject err = new JsonObject();
		err.addProperty("code", code);
		err.addProperty("message", message);
		JsonObject r = new JsonObject();
		r.addProperty("jsonrpc", "2.0");
		r.add("id", id);
		r.add("error", err);
		return GSON.toJson(r);
	}
}
