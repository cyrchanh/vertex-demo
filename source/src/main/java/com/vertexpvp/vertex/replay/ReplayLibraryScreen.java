package com.vertexpvp.vertex.replay;

import com.vertexpvp.vertex.VertexClient;
import com.vertexpvp.vertex.core.ReplayDocument;
import com.vertexpvp.vertex.record.ReplayStore;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.options.controls.KeyBindsScreen;
import net.minecraft.network.chat.Component;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.List;
import java.util.Optional;

/**
 * The replay library: every fight you've recorded, newest first, with Watch and Delete. Reachable
 * from the title screen, the pause menu, the "open library" key (J by default) and
 * {@code /vertex library}. Works without being in a world, so recordings can be watched after
 * you've logged off. Also links to the key binds screen, where the replay keys can be changed.
 */
public final class ReplayLibraryScreen extends Screen {

	private static final int ROW_HEIGHT = 24;
	private static final int TOP = 36;
	private static final int COLOR_TEXT = 0xFFE8E8E8;
	private static final int COLOR_DIM = 0xFFA0A0A0;

	private final Screen parent;
	private final SimpleDateFormat dateFormat = new SimpleDateFormat("MMM d, HH:mm");
	private List<ReplayStore.Entry> entries = List.of();
	private int page;
	private String confirmingDelete;
	private String message;

	public ReplayLibraryScreen(Screen parent) {
		super(Component.literal("Vertex Replays"));
		this.parent = parent;
	}

	private ReplayStore store() {
		return VertexClient.recorder().store();
	}

	private int rowsPerPage() {
		return Math.max(1, (height - TOP - 64) / ROW_HEIGHT);
	}

	public int entryCount() {
		return entries.size();
	}

	@Override
	protected void init() {
		entries = store().list();
		int perPage = rowsPerPage();
		int pages = Math.max(1, (entries.size() + perPage - 1) / perPage);
		page = Math.min(page, pages - 1);

		int listWidth = Math.min(420, width - 20);
		int left = (width - listWidth) / 2;
		int right = left + listWidth;
		int from = page * perPage;
		for (int i = from; i < Math.min(entries.size(), from + perPage); i++) {
			ReplayStore.Entry entry = entries.get(i);
			int y = TOP + (i - from) * ROW_HEIGHT;
			boolean confirming = entry.id().equals(confirmingDelete);
			addRenderableWidget(Button.builder(Component.literal(confirming ? "Sure?" : "Delete"), b -> delete(entry))
					.bounds(right - 50, y, 50, 20).build());
			addRenderableWidget(Button.builder(Component.literal("Watch"), b -> watch(entry))
					.bounds(right - 106, y, 54, 20).build());
		}

		int bottom = height - 28;
		Button prev = addRenderableWidget(Button.builder(Component.literal("<"), b -> {
			page--;
			rebuildWidgets();
		}).bounds(left, bottom - 24, 20, 20).build());
		prev.active = page > 0;
		Button next = addRenderableWidget(Button.builder(Component.literal(">"), b -> {
			page++;
			rebuildWidgets();
		}).bounds(right - 20, bottom - 24, 20, 20).build());
		next.active = page < pages - 1;

		addRenderableWidget(Button.builder(Component.literal("Controls..."),
						b -> minecraft.setScreenAndShow(new KeyBindsScreen(this, minecraft.options)))
				.bounds(width / 2 - 154, bottom, 150, 20).build());
		addRenderableWidget(Button.builder(Component.translatable("gui.done"), b -> onClose())
				.bounds(width / 2 + 4, bottom, 150, 20).build());
	}

	private void watch(ReplayStore.Entry entry) {
		Optional<ReplayDocument> doc = store().load(entry.id());
		if (doc.isEmpty()) {
			message = "Couldn't read that replay file.";
			return;
		}
		ReplayWorld.requestWatch(doc.get(), this);
	}

	private void delete(ReplayStore.Entry entry) {
		if (!entry.id().equals(confirmingDelete)) {
			confirmingDelete = entry.id();
		} else {
			confirmingDelete = null;
			store().delete(entry.id());
		}
		rebuildWidgets();
	}

	@Override
	public void onClose() {
		minecraft.setScreenAndShow(parent);
	}

	@Override
	public void extractRenderState(GuiGraphicsExtractor graphics, int mouseX, int mouseY, float partialTick) {
		super.extractRenderState(graphics, mouseX, mouseY, partialTick);
		graphics.centeredText(font, title, width / 2, 12, COLOR_TEXT);

		int listWidth = Math.min(420, width - 20);
		int left = (width - listWidth) / 2;
		if (entries.isEmpty()) {
			graphics.centeredText(font, "No recordings yet.", width / 2, TOP + 10, COLOR_TEXT);
			graphics.centeredText(font, "Fights in singleplayer are recorded automatically; on a server, use /vertex record on.",
					width / 2, TOP + 24, COLOR_DIM);
		}
		int perPage = rowsPerPage();
		int from = page * perPage;
		for (int i = from; i < Math.min(entries.size(), from + perPage); i++) {
			ReplayStore.Entry e = entries.get(i);
			int y = TOP + (i - from) * ROW_HEIGHT;
			graphics.text(font, "vs " + e.opponent() + "  " + e.hitsLanded() + "-" + e.hitsTaken() + "  " + e.result(),
					left, y + 1, COLOR_TEXT);
			graphics.text(font, dateFormat.format(new Date(e.startedAtEpochMs()))
					+ (e.hasArena() ? "" : "  (older recording: no blocks, plain floor)"), left, y + 11, COLOR_DIM);
		}
		int pages = Math.max(1, (entries.size() + perPage - 1) / perPage);
		int bottom = height - 28;
		if (pages > 1) {
			graphics.centeredText(font, "Page " + (page + 1) + "/" + pages, width / 2, bottom - 18, COLOR_DIM);
		}
		if (message != null) {
			graphics.centeredText(font, message, width / 2, bottom - 36, 0xFFE0574F);
		}
	}
}
