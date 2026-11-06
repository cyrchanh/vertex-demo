package com.vertexpvp.vertex.core;

import com.vertexpvp.vertex.core.trace.ControlInput;
import com.vertexpvp.vertex.core.trace.DuelTrace;
import com.vertexpvp.vertex.core.trace.ParticipantFrame;
import com.vertexpvp.vertex.core.trace.ParticipantInfo;
import com.vertexpvp.vertex.core.trace.TickFrame;
import com.vertexpvp.vertex.core.view.AttackCharge;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AttackChargeTest {

	/** Me: exact charge 0.4 throughout. Opponent: swings at tick 10, switches item at tick 30. */
	private static DuelTrace trace() {
		List<TickFrame> frames = new ArrayList<>();
		for (int t = 0; t < 50; t++) {
			ParticipantFrame me = new ParticipantFrame(0, 64, 0, 0, 0, 0, 0, 0, 0, 0, true, false, false,
					ControlInput.NONE, 20f, 0.4f, 0, false, 0, "minecraft:diamond_sword", true);
			ParticipantFrame them = new ParticipantFrame(3, 64, 0, 0, 0, 0, 0, 0, 0, 0, true, false, false,
					null, 20f, -1f, -1, t == 10, 0, t < 30 ? "minecraft:iron_sword" : "minecraft:iron_axe", false);
			frames.add(new TickFrame(t, List.of(me, them)));
		}
		return new DuelTrace(DuelTrace.FORMAT_VERSION, "t", "client", 0L, "minecraft:overworld",
				List.of(new ParticipantInfo(0, "Me", true, "minecraft:player", true),
						new ParticipantInfo(1, "Rival", true, "minecraft:player", false)),
				frames, List.of(), DuelTrace.EndReason.IDLE, -1, null);
	}

	@Test
	void recordersChargeIsExact() {
		DuelTrace trace = trace();
		assertTrue(AttackCharge.exact(trace, 0, 20));
		assertEquals(0.4f, AttackCharge.at(trace, 0, 20, 12.5f), 1e-6);
	}

	@Test
	void opponentsChargeRefillsAfterASwingLikeVanilla() {
		DuelTrace trace = trace();
		assertFalse(AttackCharge.exact(trace, 1, 20));
		assertEquals(1f, AttackCharge.at(trace, 1, 5, 12.5f), 1e-6, "no swing yet: fully charged");
		assertEquals(0.5f / 12.5f, AttackCharge.at(trace, 1, 10, 12.5f), 1e-6, "just swung");
		assertEquals(6.5f / 12.5f, AttackCharge.at(trace, 1, 16, 12.5f), 1e-6);
		assertEquals(1f, AttackCharge.at(trace, 1, 25, 12.5f), 1e-6, "refilled");
		assertEquals(2.5f / 20f, AttackCharge.at(trace, 1, 32, 20f), 1e-6, "switching items resets it too");
	}

	@Test
	void olderRecordingsHaveNoHeldItem() {
		ParticipantFrame old = new ParticipantFrame(0, 64, 0, 0, 0, 0, 0, 0, 0, 0, true, false, false,
				null, 20f, -1f, -1, false, 0);
		assertNull(old.mainHand(), "unknown, not empty");
		assertFalse(old.mainHandFoil());
	}

	@Test
	void heldItemSurvivesSaving() throws Exception {
		DuelTrace trace = trace();
		ReplayDocument doc = new ReplayDocument(trace, List.of(), List.of(), "test");
		DuelTrace back = ReplayCodec.decode(ReplayCodec.encode(doc)).trace();
		assertEquals("minecraft:diamond_sword", back.state(3, 0).mainHand());
		assertTrue(back.state(3, 0).mainHandFoil());
		assertEquals("minecraft:iron_axe", back.state(40, 1).mainHand());
	}
}
