package com.vertexpvp.vertex.replay;

import com.vertexpvp.vertex.VertexKeys;
import com.vertexpvp.vertex.core.ReplayDocument;
import com.vertexpvp.vertex.core.detect.Interaction;
import com.vertexpvp.vertex.core.explain.Explanation;
import com.vertexpvp.vertex.core.trace.CombatEvent;
import com.vertexpvp.vertex.core.trace.ControlInput;
import com.vertexpvp.vertex.core.trace.DuelTrace;
import com.vertexpvp.vertex.core.trace.ParticipantFrame;
import com.vertexpvp.vertex.core.trace.ParticipantInfo;
import net.minecraft.client.CameraType;
import net.minecraft.client.KeyMapping;
import com.vertexpvp.vertex.core.view.AttackCharge;
import com.vertexpvp.vertex.mixin.LivingEntityAccessor;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.entity.ai.attributes.AttributeInstance;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.ItemStack;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.network.chat.Component;
import net.minecraft.world.entity.Entity;
import org.lwjgl.glfw.GLFW;

import java.util.List;

/**
 * The 3D replay viewer, opened inside the {@link ReplayWorld} once the recorded arena has been
 * rebuilt there. Both fighters are re-enacted by client-only ghost entities, and the camera is
 * either the side-on fight camera (the default) or rides one fighter in first or third person.
 * The screen draws no background, so the world stays fully visible behind the overlay.
 *
 * <p>Default controls (all rebindable under Options > Controls > Key Binds > Vertex Replay):
 * Space play/pause, Left/Right +-1 s (with Shift: +-1 tick), Up/Down speed, 1/2 choose fighter,
 * C camera mode, N/P next/previous exchange, Esc leave.
 */
public final class ReplayScreen extends Screen {

	private static final double[] SPEEDS = {0.25, 0.5, 1.0, 2.0};
	private static final int COLOR_GOOD = 0xFF55E07A;
	private static final int COLOR_BAD = 0xFFE0574F;
	private static final int COLOR_NEUTRAL = 0xFFE0C04F;
	private static final int COLOR_TEXT = 0xFFE8E8E8;
	private static final int COLOR_DIM = 0xFFA0A0A0;
	// Mostly opaque so chat/HUD drawn underneath doesn't bleed through the text.
	private static final int COLOR_PANEL = 0xE8101216;
	private static final int COLOR_ACCENT = 0xFF39E0C4;
	private static final int INPUTS_PANEL_RIGHT = 140;

	public enum CameraMode {
		SIDE("side view"),
		FIRST_PERSON("first person"),
		THIRD_PERSON("third person");

		final String label;

		CameraMode(String label) {
			this.label = label;
		}
	}

	private final ReplayDocument doc;
	private final DuelTrace trace;
	private final GhostCast cast;
	private final CameraRig rig = new CameraRig();

	private double cursor;
	private int appliedTick = -1;
	private boolean playing = true;
	private int speedIndex = 2;
	private int perspective;
	private CameraMode cameraMode = CameraMode.SIDE;

	private boolean started;
	private boolean closed;
	private Entity previousCamera;
	private CameraType previousCameraType;

	private static ReplayScreen active;

	/** The replay screen currently open, if any. */
	public static ReplayScreen active() {
		return active;
	}

	/** A replay is playing: vanilla's own status bars (your health, food, XP...) are hidden. */
	public static boolean isWatching() {
		return active != null && active.started && !active.closed;
	}

	/**
	 * Not watching from a fighter's eyes: your own first-person hand and the crosshair are hidden.
	 * In first person they stay, showing the fighter's held item and attack cooldown.
	 */
	public static boolean hidesHand() {
		return isWatching() && active.cameraMode != CameraMode.FIRST_PERSON;
	}

	public ReplayScreen(ReplayDocument doc) {
		super(Component.literal("Vertex Replay"));
		this.doc = doc;
		this.trace = doc.trace();
		this.cast = new GhostCast(trace);
		int recorder = trace.recorderIndex();
		this.perspective = recorder >= 0 ? recorder : 0;
	}

	public DuelTrace trace() {
		return trace;
	}

	public int ghostCount() {
		return cast.ghosts().size();
	}

	public double cursor() {
		return cursor;
	}

	public CameraMode cameraMode() {
		return cameraMode;
	}

	@Override
	protected void init() {
		if (started || minecraft.level == null) {
			return;
		}
		started = true;
		active = this;
		previousCamera = minecraft.getCameraEntity();
		previousCameraType = minecraft.options.getCameraType();
		cast.spawn(minecraft.level);
		seek(0);
		applyCamera();
	}

	@Override
	public boolean isPauseScreen() {
		return false;
	}

	@Override
	public void extractBackground(GuiGraphicsExtractor graphics, int mouseX, int mouseY, float partialTick) {
		// Intentionally empty: no dimming or blur, so the replay in the world stays fully visible.
	}

	@Override
	public void tick() {
		if (!started || closed) {
			return;
		}
		if (playing) {
			double next = cursor + SPEEDS[speedIndex];
			if (next >= trace.lastTick()) {
				next = trace.lastTick();
				playing = false;
			}
			advanceTo(next);
		}
	}

	private void advanceTo(double target) {
		int t = (int) Math.floor(target);
		while (appliedTick < t) {
			appliedTick++;
			applyFrame(appliedTick, false);
		}
		cursor = target;
	}

	/** Pause on a tick (used by the in-game test). */
	public void pauseAt(int tick) {
		playing = false;
		seek(tick);
	}

	private void seek(double target) {
		cursor = Math.max(0, Math.min(trace.lastTick(), target));
		appliedTick = (int) Math.floor(cursor);
		applyFrame(appliedTick, true);
	}

	private void applyFrame(int t, boolean snap) {
		for (ParticipantInfo info : trace.participants()) {
			cast.apply(info.index(), trace.state(t, info.index()), snap);
		}
		updateFraming();
		rig.update(trace.state(t, 0), trace.state(t, trace.opponentOf(0)), snap);
		if (cameraMode == CameraMode.FIRST_PERSON) {
			drivePointOfView(t, snap);
		}
	}

	/**
	 * First person: vanilla draws the held item, the swing and the attack-cooldown indicator from
	 * your own (local) player, so copy the watched fighter's onto it. In the replay world you're an
	 * invisible adventure-mode player parked out of shot, so nothing else about you shows.
	 */
	private void drivePointOfView(int t, boolean snap) {
		LocalPlayer player = minecraft.player;
		if (player == null) {
			return;
		}
		ParticipantFrame f = trace.state(t, perspective);
		ItemStack held = HeldItems.stack(f);
		Inventory inventory = player.getInventory();
		if (inventory.getSelectedSlot() != 0) {
			inventory.setSelectedSlot(0);
		}
		if (!ItemStack.matches(inventory.getItem(0), held)) {
			inventory.setItem(0, held);
		}
		if (!snap && f.swingStart()) {
			player.swing(InteractionHand.MAIN_HAND);
		}

		// Make your attack speed match the fighter's item, then set the charge directly.
		float itemDelay = HeldItems.attackDelayTicks(f);
		AttributeInstance speed = player.getAttribute(Attributes.ATTACK_SPEED);
		double wanted = 20.0 / itemDelay;
		if (speed != null && Math.abs(speed.getValue() - wanted) > 1.0e-4) {
			speed.setBaseValue(speed.getBaseValue() + wanted - speed.getValue());
		}
		float charge = AttackCharge.at(trace, perspective, t, itemDelay);
		float delay = player.getCurrentItemAttackStrengthDelay();
		// The player's own tick adds 1 before the next frame is drawn.
		int ticks = charge >= 1f ? Math.round(delay) + 1 : Math.max(0, Math.round(charge * delay) - 1);
		((LivingEntityAccessor) player).vertex$setAttackStrengthTicker(ticks);
	}

	/** Keep the side view's fighters in the clear gap between the keys panel (left) and the coaching panel (right). */
	private void updateFraming() {
		if (width <= 0 || height <= 0) {
			return;
		}
		double clearCentre = (INPUTS_PANEL_RIGHT + width - panelWidth() - 6) / 2.0;
		double shiftLeft = (width / 2.0 - clearCentre) / (width / 2.0);
		double tanHalfFovX = Math.tan(Math.toRadians(minecraft.options.fov().get()) / 2.0) * width / height;
		rig.setFraming(shiftLeft, tanHalfFovX);
	}

	private int panelWidth() {
		return Math.min(300, (int) (width * 0.42));
	}

	private void applyCamera() {
		if (minecraft.player == null) {
			return;
		}
		if (cameraMode == CameraMode.SIDE) {
			int t = Math.max(0, appliedTick);
			updateFraming();
			rig.spawn(minecraft.level, trace.state(t, 0), trace.state(t, trace.opponentOf(0)));
			minecraft.setCameraEntity(rig.entity());
			minecraft.options.setCameraType(CameraType.FIRST_PERSON);
			return;
		}
		minecraft.setCameraEntity(cast.ghost(perspective));
		minecraft.options.setCameraType(cameraMode == CameraMode.FIRST_PERSON ? CameraType.FIRST_PERSON : CameraType.THIRD_PERSON_BACK);
		rig.despawn(minecraft.level);
		if (cameraMode == CameraMode.FIRST_PERSON) {
			drivePointOfView(Math.max(0, appliedTick), true);
		}
	}

	@Override
	public void removed() {
		if (!started || closed) {
			return;
		}
		closed = true;
		if (active == this) {
			active = null;
		}
		Entity restore = previousCamera != null && !previousCamera.isRemoved() && previousCamera.level() == minecraft.level
				? previousCamera : minecraft.player;
		if (restore != null) {
			minecraft.setCameraEntity(restore);
		}
		if (previousCameraType != null) {
			minecraft.options.setCameraType(previousCameraType);
		}
		if (minecraft.level != null) {
			cast.despawn(minecraft.level);
			rig.despawn(minecraft.level);
		}
	}

	/** Esc: leave the replay world (or just close, if it was opened outside one). */
	@Override
	public void onClose() {
		if (ReplayWorld.isActive()) {
			ReplayWorld.closeViewer();
		} else {
			super.onClose();
		}
	}

	@Override
	public boolean keyPressed(KeyEvent event) {
		if (!started) {
			return super.keyPressed(event);
		}
		boolean shift = (event.modifiers() & GLFW.GLFW_MOD_SHIFT) != 0;
		if (VertexKeys.playPause.matches(event)) {
			if (!playing && cursor >= trace.lastTick()) {
				seek(0);
			}
			playing = !playing;
		} else if (VertexKeys.back.matches(event)) {
			seek(cursor - (shift ? 1 : 20));
		} else if (VertexKeys.forward.matches(event)) {
			seek(cursor + (shift ? 1 : 20));
		} else if (VertexKeys.faster.matches(event)) {
			speedIndex = Math.min(SPEEDS.length - 1, speedIndex + 1);
		} else if (VertexKeys.slower.matches(event)) {
			speedIndex = Math.max(0, speedIndex - 1);
		} else if (VertexKeys.view1.matches(event)) {
			switchPerspective(0);
		} else if (VertexKeys.view2.matches(event)) {
			switchPerspective(1);
		} else if (VertexKeys.camera.matches(event)) {
			cameraMode = CameraMode.values()[(cameraMode.ordinal() + 1) % CameraMode.values().length];
			applyCamera();
		} else if (VertexKeys.nextExchange.matches(event)) {
			jumpToInteraction(true);
		} else if (VertexKeys.previousExchange.matches(event)) {
			jumpToInteraction(false);
		} else {
			return super.keyPressed(event);
		}
		return true;
	}

	private void switchPerspective(int p) {
		if (p < trace.participants().size()) {
			perspective = p;
			applyCamera();
		}
	}

	private void jumpToInteraction(boolean forward) {
		List<Interaction> interactions = doc.interactions();
		if (interactions.isEmpty()) {
			return;
		}
		Interaction target = null;
		for (Interaction in : interactions) {
			if (forward && in.startTick() > cursor + 1) {
				target = in;
				break;
			}
			if (!forward && in.startTick() < cursor - 1) {
				target = in;
			}
		}
		if (target == null) {
			target = forward ? interactions.get(0) : interactions.get(interactions.size() - 1);
		}
		seek(target.startTick());
		playing = false;
	}

	/** The exchange the cursor is in, or the one that just finished (kept on screen a couple of seconds). */
	private Interaction currentInteraction() {
		Interaction current = null;
		for (Interaction in : doc.interactions()) {
			if (in.startTick() <= cursor && cursor <= in.endTick() + 40) {
				current = in;
			}
		}
		return current;
	}

	// ---------------------------------------------------------------- drawing

	@Override
	public void extractRenderState(GuiGraphicsExtractor graphics, int mouseX, int mouseY, float partialTick) {
		super.extractRenderState(graphics, mouseX, mouseY, partialTick);
		if (!started) {
			graphics.centeredText(font, "Loading replay...", width / 2, height / 2, COLOR_TEXT);
			return;
		}
		drawHeader(graphics);
		drawInputs(graphics);
		drawExplanation(graphics);
		drawTimeline(graphics);
	}

	private void drawHeader(GuiGraphicsExtractor g) {
		ParticipantInfo me = trace.participants().get(perspective);
		ParticipantInfo other = trace.participants().get(trace.opponentOf(perspective));
		int x = 8;
		int y = 8;
		g.fill(4, 4, 250, 52, COLOR_PANEL);
		g.text(font, "VERTEX REPLAY", x, y, COLOR_ACCENT);
		g.text(font, me.name() + " vs " + other.name(), x + 88, y, COLOR_TEXT);
		y += 12;
		String status = playing ? "" : (cursor >= trace.lastTick() ? "  [END]" : "  [PAUSED]");
		g.text(font, time(cursor) + " / " + time(trace.lastTick()) + "   x" + speedLabel() + status, x, y, COLOR_TEXT);
		y += 12;
		String view = cameraMode == CameraMode.SIDE
				? "Side view - coaching for " + me.name()
				: "Watching " + me.name() + " - " + cameraMode.label;
		g.text(font, view, x, y, COLOR_DIM);
		y += 12;
		if (!trace.hasArena()) {
			g.text(font, "Older recording: blocks weren't saved", x, y, COLOR_NEUTRAL);
		}
	}

	private void drawInputs(GuiGraphicsExtractor g) {
		int x = 8;
		int y = 60;
		g.fill(4, 56, INPUTS_PANEL_RIGHT, 142, COLOR_PANEL);
		ParticipantFrame f = trace.state((int) cursor, perspective);
		ControlInput in = f.input();
		if (in == null) {
			g.text(font, "Keys: not visible", x, y, COLOR_DIM);
			g.text(font, "(opponent's client)", x, y + 10, COLOR_DIM);
		} else {
			key(g, "W", x + 22, y, in.forward());
			key(g, "A", x, y + 18, in.left());
			key(g, "S", x + 22, y + 18, in.back());
			key(g, "D", x + 44, y + 18, in.right());
			key(g, "Jump", x + 68, y, in.jump());
			key(g, "Crouch", x + 68, y + 18, in.shift());
			key(g, "Sprint", x + 68, y + 36, in.sprint());
		}
		int hy = y + 56;
		int t = (int) cursor;
		for (ParticipantInfo info : trace.participants()) {
			ParticipantFrame pf = trace.state(t, info.index());
			int color = info.index() == perspective ? COLOR_TEXT : COLOR_DIM;
			g.text(font, shorten(info.name(), 9) + String.format(" %.1f hp", pf.health()), x, hy, color);
			// Attack charge, like vanilla's cooldown indicator. "~" = estimated from their swings.
			float charge = AttackCharge.at(trace, info.index(), t, HeldItems.attackDelayTicks(pf));
			int barX = INPUTS_PANEL_RIGHT - 34;
			g.fill(barX, hy + 2, barX + 22, hy + 6, 0xFF303030);
			g.fill(barX, hy + 2, barX + Math.round(22 * charge), hy + 6, charge >= 1f ? COLOR_GOOD : COLOR_NEUTRAL);
			if (!AttackCharge.exact(trace, info.index(), t)) {
				g.text(font, "~", barX + 24, hy, COLOR_DIM);
			}
			hy += 10;
		}
	}

	private void key(GuiGraphicsExtractor g, String label, int x, int y, boolean down) {
		int w = font.width(label) + 8;
		g.fill(x, y, x + w, y + 14, down ? 0xFFE8E8E8 : 0x80404040);
		g.text(font, label, x + 4, y + 3, down ? 0xFF101010 : COLOR_DIM, false);
	}

	private void drawExplanation(GuiGraphicsExtractor g) {
		int panelWidth = panelWidth();
		int x = width - panelWidth - 6;
		int y = 6;
		int inner = panelWidth - 12;

		Interaction in = currentInteraction();
		if (in == null) {
			int h = 34;
			g.fill(x, y, x + panelWidth, y + h, COLOR_PANEL);
			g.text(font, "Neutral - no exchange right now", x + 6, y + 6, COLOR_DIM);
			g.text(font, k(VertexKeys.nextExchange) + " = jump to the next exchange", x + 6, y + 18, COLOR_DIM);
			return;
		}
		Explanation e = doc.explanationFor(in.index(), perspective)
				.or(() -> doc.explanationFor(in.index(), trace.opponentOf(perspective)))
				.orElse(null);
		if (e == null) {
			return;
		}

		int color = switch (e.verdict()) {
			case GOOD -> COLOR_GOOD;
			case MISTAKE -> COLOR_BAD;
			case NEUTRAL -> COLOR_NEUTRAL;
		};
		String perspectiveName = trace.participants().get(e.perspective()).name();
		String[][] sections = {
				{"What happened", e.what()},
				{"Why", e.why()},
				{"Do instead", e.instead()},
				{"Why that works", e.insteadWhy()}
		};

		int height = 12 + wrapHeight(e.headline(), inner) + 12;
		for (String[] s : sections) {
			height += 11 + wrapHeight(s[1], inner) + 4;
		}
		List<String> notes = e.notes().stream().limit(4).toList();
		for (String note : notes) {
			height += wrapHeight("- " + note, inner);
		}
		height += 22;
		// In first person the held item is drawn bottom-right, so the panel stops halfway down to keep it
		// visible (sections that don't fit are dropped cleanly below).
		height = Math.min(height, cameraMode == CameraMode.FIRST_PERSON ? this.height / 2 : this.height - 50);

		g.fill(x, y, x + panelWidth, y + height, COLOR_PANEL);
		g.fill(x, y, x + 3, y + height, color);
		int ty = y + 6;
		g.text(font, "Exchange " + (in.index() + 1) + "/" + doc.interactions().size() + " - for " + perspectiveName,
				x + 8, ty, COLOR_DIM);
		ty += 12;
		g.textWithWordWrap(font, Component.literal(e.headline()), x + 8, ty, inner, color);
		ty += wrapHeight(e.headline(), inner) + 6;
		// Only draw a block if it fits above the source line; small windows drop later blocks cleanly
		// instead of overlapping.
		int limit = y + height - 14;
		boolean truncated = false;
		for (String[] s : sections) {
			int blockHeight = 11 + wrapHeight(s[1], inner);
			if (ty + blockHeight > limit) {
				truncated = true;
				break;
			}
			g.text(font, s[0], x + 8, ty, COLOR_ACCENT);
			g.textWithWordWrap(font, Component.literal(s[1]), x + 8, ty + 11, inner, COLOR_TEXT);
			ty += blockHeight + 4;
		}
		for (String note : truncated ? List.<String>of() : notes) {
			int noteHeight = wrapHeight("- " + note, inner);
			if (ty + noteHeight > limit) {
				break;
			}
			g.textWithWordWrap(font, Component.literal("- " + note), x + 8, ty, inner, COLOR_DIM);
			ty += noteHeight;
		}
		String source = (e.inferred() ? "Partly estimated. " : "") + "Source: " + e.source();
		g.text(font, shorten(source, inner / 5), x + 8, y + height - 12, COLOR_DIM);
	}

	private void drawTimeline(GuiGraphicsExtractor g) {
		int x0 = 10;
		int x1 = width - 10;
		int y = height - 30;
		int last = Math.max(1, trace.lastTick());
		g.fill(4, y - 14, width - 4, height - 2, COLOR_PANEL);
		g.fill(x0, y, x1, y + 5, 0xFF303030);
		int progress = x0 + (int) ((x1 - x0) * (cursor / last));
		g.fill(x0, y, progress, y + 5, 0xFF707070);

		for (Interaction in : doc.interactions()) {
			int ix = x0 + (int) ((x1 - x0) * (in.anchorTick() / (double) last));
			int color = doc.explanationFor(in.index(), perspective).map(e -> switch (e.verdict()) {
				case GOOD -> COLOR_GOOD;
				case MISTAKE -> COLOR_BAD;
				case NEUTRAL -> COLOR_NEUTRAL;
			}).orElse(COLOR_NEUTRAL);
			g.fill(ix - 1, y - 6, ix + 2, y, color);
		}
		for (CombatEvent e : trace.events()) {
			if (e.type() != CombatEvent.Type.HIT) {
				continue;
			}
			int ex = x0 + (int) ((x1 - x0) * (e.tick() / (double) last));
			int color = e.attacker() == perspective ? COLOR_GOOD : COLOR_BAD;
			int top = e.kind() == CombatEvent.HitKind.CRIT ? y - 3 : y + 1;
			g.fill(ex, top, ex + 1, y + 5, color);
		}
		g.fill(progress - 1, y - 8, progress + 1, y + 8, 0xFFFFFFFF);

		g.text(font, shorten(helpLine(), (width - 20) / 5), x0, height - 12, COLOR_DIM);
	}

	/** The controls hint, showing whatever keys are currently bound. */
	private static String helpLine() {
		return k(VertexKeys.playPause) + " play/pause  " + k(VertexKeys.back) + "/" + k(VertexKeys.forward)
				+ " 1s (Shift: 1 tick)  " + k(VertexKeys.faster) + "/" + k(VertexKeys.slower) + " speed  "
				+ k(VertexKeys.view1) + "/" + k(VertexKeys.view2) + " fighter  " + k(VertexKeys.camera) + " camera  "
				+ k(VertexKeys.nextExchange) + "/" + k(VertexKeys.previousExchange) + " exchange  Esc leave";
	}

	private static String k(KeyMapping mapping) {
		return VertexKeys.label(mapping);
	}

	private int wrapHeight(String text, int width) {
		return font.split(Component.literal(text), width).size() * (font.lineHeight + 1);
	}

	private String speedLabel() {
		double s = SPEEDS[speedIndex];
		return s == Math.floor(s) ? String.valueOf((int) s) : String.valueOf(s);
	}

	private static String time(double tick) {
		double seconds = tick / 20.0;
		int m = (int) (seconds / 60);
		return String.format("%d:%04.1f", m, seconds - m * 60);
	}

	private static String shorten(String s, int max) {
		return s.length() <= max ? s : s.substring(0, Math.max(0, max - 1)) + "...";
	}
}
