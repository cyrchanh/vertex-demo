package com.vertexpvp.vertex.core.view;

import com.vertexpvp.vertex.core.trace.ParticipantFrame;

/**
 * The replay's default "fight camera": a side-on view of the line between the two fighters, like a
 * fighting game. It sits on one side of that line, far enough back to keep both fighters in frame,
 * and looks at the point between them.
 *
 * <ul>
 *     <li>Fighter 0 starts on the left of the screen.</li>
 *     <li>The camera stays on the same side even when the fighters circle or swap places, instead
 *     of flipping, which would be disorienting.</li>
 *     <li>It eases toward its target each tick instead of jumping, so knockback doesn't shake it.</li>
 * </ul>
 *
 * Coordinates are Minecraft's: +Y up, yaw 0 = facing +Z, yaw 90 = facing -X, pitch positive = down.
 * Pure math, no Minecraft imports.
 */
public final class SideCamera {

	/** Where the camera's eye is and which way it looks. */
	public record Pose(double x, double y, double z, float yaw, float pitch) {
	}

	public static final double MIN_DISTANCE = 5.0;
	public static final double MAX_DISTANCE = 16.0;
	private static final double EYE_OFFSET = 1.3;
	private static final double HEIGHT = 1.2;
	private static final double SMOOTHING = 0.3;

	private double sideX;
	private double sideZ;
	private boolean hasSide;
	private double camX, camY, camZ;
	private boolean hasPosition;
	private double shiftLeft;
	private double tanHalfFovX = 1.0;

	/**
	 * Frame the fight off-centre, e.g. to keep it clear of an overlay panel on the right.
	 * {@code shiftLeft} is where the midpoint between the fighters should appear, as a fraction of
	 * half the screen width to the left of centre (0 = centred, 0.4 = 40% of the way to the left edge).
	 * {@code tanHalfFovX} is tan(horizontal field of view / 2) of the view this camera feeds.
	 */
	public void setFraming(double shiftLeft, double tanHalfFovX) {
		this.shiftLeft = Math.max(0.0, Math.min(0.9, shiftLeft));
		this.tanHalfFovX = Math.max(0.1, tanHalfFovX);
	}

	/**
	 * Advance one tick. {@code snap} jumps straight to the target (used when seeking).
	 * Returns the camera pose to use for this tick.
	 */
	public Pose update(ParticipantFrame a, ParticipantFrame b, boolean snap) {
		double midX = (a.x() + b.x()) / 2.0;
		double midY = (a.y() + b.y()) / 2.0 + EYE_OFFSET;
		double midZ = (a.z() + b.z()) / 2.0;

		double dx = b.x() - a.x();
		double dz = b.z() - a.z();
		double len = Math.sqrt(dx * dx + dz * dz);

		// Perpendicular to the fighters' line. With (-dz, dx), fighter a appears on the left.
		double px;
		double pz;
		if (len > 0.3) {
			px = -dz / len;
			pz = dx / len;
			if (hasSide && px * sideX + pz * sideZ < 0) {
				px = -px;
				pz = -pz;
			}
			sideX = px;
			sideZ = pz;
			hasSide = true;
		} else if (hasSide) {
			px = sideX;
			pz = sideZ;
		} else {
			px = 1.0;
			pz = 0.0;
		}

		// Off-centre framing leaves less room on screen, so pull back to keep both fighters in view.
		double distance = Math.max(MIN_DISTANCE, Math.min(MAX_DISTANCE, len * 1.1 + 4.0)) * (1.0 + shiftLeft * 0.5);
		double targetX = midX + px * distance;
		double targetY = midY + HEIGHT;
		double targetZ = midZ + pz * distance;

		if (snap || !hasPosition) {
			camX = targetX;
			camY = targetY;
			camZ = targetZ;
			hasPosition = true;
		} else {
			camX += (targetX - camX) * SMOOTHING;
			camY += (targetY - camY) * SMOOTHING;
			camZ += (targetZ - camZ) * SMOOTHING;
		}

		double lookX = midX - camX;
		double lookY = midY - camY;
		double lookZ = midZ - camZ;
		double horizontal = Math.sqrt(lookX * lookX + lookZ * lookZ);
		// Turning right (yaw increases clockwise) moves the scene left on screen.
		double turn = Math.toDegrees(Math.atan(shiftLeft * tanHalfFovX));
		float yaw = (float) (Math.toDegrees(Math.atan2(-lookX, lookZ)) + turn);
		float pitch = (float) -Math.toDegrees(Math.atan2(lookY, horizontal));
		return new Pose(camX, camY, camZ, yaw, pitch);
	}

	public void reset() {
		hasSide = false;
		hasPosition = false;
	}
}
