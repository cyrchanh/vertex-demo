package com.vertexpvp.vertex.core;

import com.vertexpvp.vertex.core.trace.Arena;
import com.vertexpvp.vertex.core.trace.ArenaColumn;
import com.vertexpvp.vertex.core.trace.ControlInput;
import com.vertexpvp.vertex.core.trace.ParticipantFrame;
import com.vertexpvp.vertex.core.view.SideCamera;
import org.junit.jupiter.api.Test;

import java.util.Arrays;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ArenaAndCameraTest {

	private static final int MIN_Y = 60;
	private static final int HEIGHT = 8;

	/** A stone floor at y=60, one oak stair at (3, 61, 5) in chunk (2, -1), air above. */
	private static ArenaColumn sampleColumn() {
		String[] states = new String[16 * 16 * HEIGHT];
		Arrays.fill(states, ArenaColumn.AIR);
		for (int lz = 0; lz < 16; lz++) {
			for (int lx = 0; lx < 16; lx++) {
				states[ArenaColumn.indexOf(lx, 60, lz, MIN_Y)] = "minecraft:stone";
			}
		}
		states[ArenaColumn.indexOf(3, 61, 5, MIN_Y)] = "minecraft:oak_stairs[facing=east,half=bottom,shape=straight,waterlogged=false]";
		return ArenaColumn.encode(2, -1, MIN_Y, HEIGHT, states);
	}

	@Test
	void arenaColumnRoundTripsAndCompresses() {
		ArenaColumn column = sampleColumn();
		assertEquals(3, column.palette().size(), "air, stone and the stair");
		assertTrue(column.runs().length <= 10, "long runs of the same block collapse: " + column.runs().length);

		String[] decoded = column.decode();
		assertEquals(16 * 16 * HEIGHT, decoded.length);
		assertEquals("minecraft:stone", decoded[ArenaColumn.indexOf(0, 60, 0, MIN_Y)]);
		assertEquals(ArenaColumn.AIR, decoded[ArenaColumn.indexOf(0, 62, 0, MIN_Y)]);
		assertArrayEquals(decoded, ArenaColumn.encode(2, -1, MIN_Y, HEIGHT, decoded).decode());
	}

	@Test
	void arenaLooksUpWorldCoordinates() {
		Arena arena = new Arena(List.of(sampleColumn()));
		// Chunk (2, -1) covers x 32..47, z -16..-1.
		assertEquals("minecraft:stone", arena.blockAt(40, 60, -8));
		assertTrue(arena.blockAt(35, 61, -11).startsWith("minecraft:oak_stairs"));
		assertEquals(ArenaColumn.AIR, arena.blockAt(40, 63, -8));
		// Not captured is "unknown" (null), not air.
		assertNull(arena.blockAt(0, 60, 0), "outside every captured column");
		assertNull(arena.blockAt(40, 200, -8), "above the captured range");
		assertEquals(257, arena.nonAirBlocks());
		assertFalse(sampleColumn().allAir());
	}

	private static ParticipantFrame at(double x, double z) {
		return new ParticipantFrame(x, 64, z, 0f, 0f, 0f, 0f, 0, 0, 0, true, false, false, ControlInput.NONE,
				20f, 1f, 0.0, false, 0);
	}

	/** Screen-right direction for a camera with this yaw (Minecraft: yaw 0 looks toward +Z, right is -X). */
	private static double screenX(SideCamera.Pose pose, double x, double z) {
		double yaw = Math.toRadians(pose.yaw());
		double rightX = -Math.cos(yaw);
		double rightZ = -Math.sin(yaw);
		return (x - pose.x()) * rightX + (z - pose.z()) * rightZ;
	}

	@Test
	void sideCameraLooksAtBothFightersFromTheSide() {
		SideCamera camera = new SideCamera();
		ParticipantFrame a = at(0, 0);
		ParticipantFrame b = at(4, 0);
		SideCamera.Pose pose = camera.update(a, b, true);

		// Perpendicular to the fighters' line (which runs along X), level with the midpoint in X.
		assertEquals(2.0, pose.x(), 1e-6);
		double distance = Math.abs(pose.z());
		assertTrue(distance >= SideCamera.MIN_DISTANCE && distance <= SideCamera.MAX_DISTANCE, "distance " + distance);
		assertTrue(pose.y() > 64, "camera is above the fighters");
		assertTrue(pose.pitch() > 0, "and looks slightly down");

		// Looking at the midpoint: the midpoint is dead centre, fighter 0 on the left, fighter 1 on the right.
		assertEquals(0.0, screenX(pose, 2, 0), 1e-6);
		assertTrue(screenX(pose, 0, 0) < 0, "fighter 0 on the left");
		assertTrue(screenX(pose, 4, 0) > 0, "fighter 1 on the right");
	}

	@Test
	void sideCameraCanFrameTheFightLeftOfAPanel() {
		SideCamera centred = new SideCamera();
		SideCamera shifted = new SideCamera();
		double tanHalfFovX = Math.tan(Math.toRadians(35)) * 16 / 9.0;
		shifted.setFraming(0.4, tanHalfFovX);
		SideCamera.Pose a = centred.update(at(0, 0), at(4, 0), true);
		SideCamera.Pose b = shifted.update(at(0, 0), at(4, 0), true);

		// Project the midpoint: screen x / half-width = (right · d) / (forward · d) / tanHalfFovX.
		double yaw = Math.toRadians(b.yaw());
		double dx = 2 - b.x();
		double dz = 0 - b.z();
		double forward = dx * -Math.sin(yaw) + dz * Math.cos(yaw);
		double right = screenX(b, 2, 0);
		assertEquals(-0.4, right / forward / tanHalfFovX, 1e-6, "midpoint 40% of the way to the left edge");
		assertTrue(Math.abs(b.z()) > Math.abs(a.z()), "pulls back to make room");
	}

	@Test
	void sideCameraDoesNotFlipWhenFightersSwapSides() {
		SideCamera camera = new SideCamera();
		SideCamera.Pose before = camera.update(at(0, 0), at(4, 0), true);
		// Fighter 0 jumps over to the other side: the line reverses, but the camera stays put.
		SideCamera.Pose after = camera.update(at(4, 0), at(0, 0), true);
		assertEquals(Math.signum(before.z()), Math.signum(after.z()), "same side of the fight");
	}

	@Test
	void sideCameraEasesInsteadOfJumping() {
		SideCamera camera = new SideCamera();
		SideCamera.Pose start = camera.update(at(0, 0), at(4, 0), true);
		SideCamera.Pose moved = camera.update(at(10, 0), at(14, 0), false);
		assertTrue(moved.x() > start.x() && moved.x() < 12.0, "moves part of the way: " + moved.x());
		assertTrue(camera.update(at(0, 0), at(0.1, 0), false).z() != 0, "fighters on top of each other: keeps its side");
	}
}
