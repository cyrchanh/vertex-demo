package com.vertexpvp.vertex.command;

import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.context.CommandContext;
import com.vertexpvp.vertex.VertexClient;
import com.vertexpvp.vertex.core.ReplayDocument;
import com.vertexpvp.vertex.core.detect.Interaction;
import com.vertexpvp.vertex.core.explain.Explanation;
import com.vertexpvp.vertex.record.DuelRecorder;
import com.vertexpvp.vertex.record.ReplayStore;
import com.vertexpvp.vertex.replay.ReplayLibraryScreen;
import com.vertexpvp.vertex.replay.ReplayWorld;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback;
import net.fabricmc.fabric.api.client.command.v2.ClientCommands;
import net.fabricmc.fabric.api.client.command.v2.FabricClientCommandSource;
import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.ClickEvent;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.MutableComponent;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.List;
import java.util.Optional;

/**
 * {@code /vertex ...} - client-side commands. They're handled entirely on this computer and never
 * sent to the server.
 * <ul>
 *     <li>{@code /vertex record [on|off]} - toggle / set recording (on by default in singleplayer,
 *     off by default on servers)</li>
 *     <li>{@code /vertex replays} - list recent replays (clickable)</li>
 *     <li>{@code /vertex library} - open the replay library screen</li>
 *     <li>{@code /vertex watch <id>|latest} - open the 3D replay (in the private replay world)</li>
 *     <li>{@code /vertex summary <id>|latest} - print the coaching notes in chat</li>
 * </ul>
 */
public final class VertexCommands {

	private VertexCommands() {
	}

	public static void register(DuelRecorder recorder) {
		ClientCommandRegistrationCallback.EVENT.register((dispatcher, registryAccess) -> dispatcher.register(
				ClientCommands.literal("vertex")
						.executes(ctx -> help(ctx.getSource()))
						.then(ClientCommands.literal("record")
								.executes(ctx -> setRecording(ctx.getSource(), recorder, !recorder.isEnabled()))
								.then(ClientCommands.literal("on").executes(ctx -> setRecording(ctx.getSource(), recorder, true)))
								.then(ClientCommands.literal("off").executes(ctx -> setRecording(ctx.getSource(), recorder, false))))
						.then(ClientCommands.literal("replays").executes(ctx -> list(ctx.getSource(), recorder.store())))
						.then(ClientCommands.literal("library").executes(ctx -> library()))
						.then(ClientCommands.literal("watch")
								.then(ClientCommands.argument("id", StringArgumentType.word())
										.executes(ctx -> watch(ctx, recorder.store()))))
						.then(ClientCommands.literal("summary")
								.then(ClientCommands.argument("id", StringArgumentType.word())
										.executes(ctx -> summary(ctx, recorder.store()))))
		));
	}

	private static int help(FabricClientCommandSource source) {
		source.sendFeedback(prefix().append(Component.literal(
				"/vertex record, /vertex library, /vertex replays, /vertex watch latest, /vertex summary latest").withStyle(ChatFormatting.GRAY)));
		return 1;
	}

	private static int setRecording(FabricClientCommandSource source, DuelRecorder recorder, boolean on) {
		recorder.setEnabled(on);
		source.sendFeedback(prefix().append(Component.literal(on
				? "Recording your fights. Hit something (or get hit) to start; a replay is saved when the fight ends."
				: "Recording off.").withStyle(on ? ChatFormatting.GREEN : ChatFormatting.GRAY)));
		return 1;
	}

	private static int list(FabricClientCommandSource source, ReplayStore store) {
		List<ReplayStore.Entry> entries = store.list();
		if (entries.isEmpty()) {
			source.sendFeedback(prefix().append(Component.literal("No replays yet.").withStyle(ChatFormatting.GRAY)));
			return 1;
		}
		source.sendFeedback(prefix().append(Component.literal("Recent replays (click to watch):").withStyle(ChatFormatting.GRAY)));
		SimpleDateFormat format = new SimpleDateFormat("MMM d HH:mm");
		for (ReplayStore.Entry e : entries.subList(0, Math.min(8, entries.size()))) {
			MutableComponent line = Component.literal(" " + format.format(new Date(e.startedAtEpochMs())) + "  vs "
					+ e.opponent() + "  " + e.hitsLanded() + "-" + e.hitsTaken() + "  " + e.result() + " ")
					.withStyle(ChatFormatting.WHITE);
			line.append(link("[Watch]", "/vertex watch " + e.id()));
			line.append(Component.literal(" "));
			line.append(link("[Summary]", "/vertex summary " + e.id()));
			source.sendFeedback(line);
		}
		return 1;
	}

	private static int watch(CommandContext<FabricClientCommandSource> ctx, ReplayStore store) {
		Optional<ReplayDocument> doc = resolve(ctx, store);
		if (doc.isEmpty()) {
			return 0;
		}
		// Next tick: the chat screen closes itself right after running a command.
		VertexClient.runNextTick(() -> ReplayWorld.requestWatch(doc.get(), null));
		return 1;
	}

	private static int library() {
		VertexClient.openNextTick(() -> new ReplayLibraryScreen(null));
		return 1;
	}

	private static int summary(CommandContext<FabricClientCommandSource> ctx, ReplayStore store) {
		Optional<ReplayDocument> found = resolve(ctx, store);
		if (found.isEmpty()) {
			return 0;
		}
		ReplayDocument doc = found.get();
		int me = Math.max(0, doc.trace().recorderIndex());
		FabricClientCommandSource source = ctx.getSource();
		source.sendFeedback(prefix().append(Component.literal("Fight vs "
				+ doc.trace().participants().get(doc.trace().opponentOf(me)).name() + ":").withStyle(ChatFormatting.WHITE)));
		for (Interaction in : doc.interactions()) {
			Optional<Explanation> e = doc.explanationFor(in.index(), me);
			if (e.isEmpty()) {
				continue;
			}
			ChatFormatting color = switch (e.get().verdict()) {
				case GOOD -> ChatFormatting.GREEN;
				case MISTAKE -> ChatFormatting.RED;
				case NEUTRAL -> ChatFormatting.YELLOW;
			};
			source.sendFeedback(Component.literal(" #" + (in.index() + 1) + " ").withStyle(ChatFormatting.DARK_GRAY)
					.append(Component.literal(e.get().headline()).withStyle(color)));
			if (e.get().verdict() != Explanation.Verdict.GOOD) {
				source.sendFeedback(Component.literal("    Instead: " + e.get().instead()).withStyle(ChatFormatting.GRAY));
			}
		}
		source.sendFeedback(link("[Watch the replay]", "/vertex watch " + doc.trace().id()));
		return 1;
	}

	private static Optional<ReplayDocument> resolve(CommandContext<FabricClientCommandSource> ctx, ReplayStore store) {
		String id = StringArgumentType.getString(ctx, "id");
		if (id.equalsIgnoreCase("latest")) {
			Optional<ReplayStore.Entry> latest = store.latest();
			if (latest.isEmpty()) {
				ctx.getSource().sendError(Component.literal("No replays yet."));
				return Optional.empty();
			}
			id = latest.get().id();
		}
		Optional<ReplayDocument> doc = store.load(id);
		if (doc.isEmpty()) {
			ctx.getSource().sendError(Component.literal("No replay called " + id + ". Try /vertex replays."));
		}
		return doc;
	}

	public static MutableComponent prefix() {
		return Component.literal("Vertex » ").withStyle(ChatFormatting.AQUA);
	}

	public static MutableComponent link(String label, String command) {
		return Component.literal(label)
				.withStyle(ChatFormatting.GREEN, ChatFormatting.UNDERLINE)
				.withStyle(style -> style.withClickEvent(new ClickEvent.RunCommand(command)));
	}
}
