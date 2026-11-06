package com.vertexpvp.vertex.core;

import com.vertexpvp.vertex.core.detect.InteractionDetector;
import com.vertexpvp.vertex.core.explain.RuleBasedExplainer;
import com.vertexpvp.vertex.core.explain.SummaryBuilder;
import com.vertexpvp.vertex.core.trace.CombatEvent;
import com.vertexpvp.vertex.core.trace.CombatEvent.HitKind;
import com.vertexpvp.vertex.core.trace.DuelTrace;
import com.vertexpvp.vertex.core.trace.EffectState;
import com.vertexpvp.vertex.core.trace.ParticipantFrame;
import com.vertexpvp.vertex.core.trace.RecordingContext;
import com.vertexpvp.vertex.core.trace.TickFrame;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.zip.GZIPInputStream;
import java.util.zip.GZIPOutputStream;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ReplayCodecTest {

	private static ReplayDocument sample() {
		DuelTrace trace = new TraceBuilder(100)
				.jump(1, 10)
				.hit(14, 0, HitKind.CRIT, HitKind.CRIT, 2.4)
				.hit(30, 1, HitKind.SPRINT_HIT)
				.hit(40, 0, HitKind.WEAK)
				.winner(0)
				.build();
		var interactions = new InteractionDetector().detect(trace);
		var explainer = new RuleBasedExplainer();
		return new ReplayDocument(trace, interactions, explainer.explain(trace, interactions), explainer.id());
	}

	@Test
	void roundTripKeepsEverything() throws IOException {
		ReplayDocument original = sample();

		ReplayDocument decoded = ReplayCodec.decode(ReplayCodec.encode(original));

		assertEquals(original.trace().frames().size(), decoded.trace().frames().size());
		assertEquals(original.trace().events(), decoded.trace().events());
		assertEquals(original.trace().frames().get(15), decoded.trace().frames().get(15));
		assertEquals(original.explanations(), decoded.explanations());
		assertEquals(original.interactions().size(), decoded.interactions().size());
		assertArrayEquals(original.interactions().get(0).hitsBy(), decoded.interactions().get(0).hitsBy());
		assertEquals(RuleBasedExplainer.ID, decoded.explainerId());
	}

	@Test
	void unknownValuesSurviveRoundTrip() throws IOException {
		ReplayDocument decoded = ReplayCodec.decode(ReplayCodec.encode(sample()));
		CombatEvent e = decoded.trace().events().get(0);

		assertTrue(Double.isNaN(e.knockbackX()), "unseen knockback stays NaN, not 0");
		assertNull(decoded.trace().state(5, 1).input(), "opponent keys stay unknown, not 'nothing pressed'");
	}

	@Test
	void formatThreeFieldsRoundTrip() throws IOException {
		ReplayDocument doc = sample();
		DuelTrace t = doc.trace();
		ParticipantFrame old = t.state(15, 0);
		ParticipantFrame geared = new ParticipantFrame(old.x(), old.y(), old.z(), old.yaw(), old.pitch(), old.headYaw(),
				old.bodyYaw(), old.vx(), old.vy(), old.vz(), old.onGround(), old.sprinting(), old.sneaking(), old.input(),
				old.health(), old.attackStrength(), old.fallDistance(), old.swingStart(), old.hurtTime(),
				"minecraft:diamond_sword", true, "minecraft:golden_apple",
				List.of("minecraft:diamond_helmet", "minecraft:diamond_chestplate", "minecraft:air", "minecraft:diamond_boots"),
				4f, true, 3, 42, List.of(new EffectState("minecraft:speed", 1, 600), new EffectState("minecraft:regeneration", 0, -1)));
		List<TickFrame> frames = new ArrayList<>(t.frames());
		frames.set(15, new TickFrame(15, List.of(geared, t.state(15, 1))));
		RecordingContext context = new RecordingContext("26.2", "0.2.0", "play.example.net", "Paper", false);
		DuelTrace withGear = new DuelTrace(t.formatVersion(), t.id(), t.source(), t.startedAtEpochMs(), t.dimension(),
				t.participants(), frames, t.events(), t.endReason(), t.winner(), t.arena(), context);

		ReplayDocument decoded = ReplayCodec.decode(ReplayCodec.encode(
				new ReplayDocument(withGear, doc.interactions(), doc.explanations(), doc.explainerId())));

		assertEquals(geared, decoded.trace().state(15, 0));
		assertEquals(context, decoded.trace().context());
	}

	@Test
	void olderFilesLoadWithNewFieldsUnknown() throws IOException {
		// Write a replay, then strip everything format 3 added, as a format 2 file would look.
		JsonObject json = JsonParser.parseString(gunzip(ReplayCodec.encode(sample()))).getAsJsonObject();
		JsonObject trace = json.getAsJsonObject("trace");
		trace.remove("context");
		for (JsonElement frame : trace.getAsJsonArray("frames")) {
			for (JsonElement p : frame.getAsJsonObject().getAsJsonArray("participants")) {
				for (String key : List.of("offHand", "armor", "absorption", "usingItem", "selectedSlot", "pingMs", "effects")) {
					p.getAsJsonObject().remove(key);
				}
			}
		}

		ReplayDocument decoded = ReplayCodec.decode(gzip(json.toString()));
		ParticipantFrame f = decoded.trace().state(15, 0);

		assertNull(decoded.trace().context());
		assertNull(f.armor());
		assertNull(f.offHand());
		assertNull(f.pingMs(), "unknown ping stays unknown, not 0 ms");
		assertNull(f.selectedSlot());
		assertNull(f.effects(), "unknown effects stay unknown, not 'no effects'");
		assertEquals(0f, f.absorption());
	}

	private static String gunzip(byte[] data) throws IOException {
		try (GZIPInputStream in = new GZIPInputStream(new ByteArrayInputStream(data))) {
			return new String(in.readAllBytes(), StandardCharsets.UTF_8);
		}
	}

	private static byte[] gzip(String text) throws IOException {
		ByteArrayOutputStream bytes = new ByteArrayOutputStream();
		try (GZIPOutputStream out = new GZIPOutputStream(bytes)) {
			out.write(text.getBytes(StandardCharsets.UTF_8));
		}
		return bytes.toByteArray();
	}

	@Test
	void rejectsGarbage() {
		assertThrows(IOException.class, () -> ReplayCodec.decode(new byte[] {1, 2, 3}));
	}

	@Test
	void summaryCountsFromTheRightPerspective() {
		ReplayDocument doc = sample();

		SummaryBuilder.Summary me = SummaryBuilder.forParticipant(doc, 0);
		SummaryBuilder.Summary them = SummaryBuilder.forParticipant(doc, 1);

		assertEquals(2, me.hitsLanded());
		assertEquals(1, me.critsLanded());
		assertEquals(1, me.hitsTaken());
		assertEquals(1, me.sprintHitsTaken());
		assertEquals("You won", me.result());
		assertEquals("You lost", them.result());
		assertEquals(5, me.seconds());
	}
}
