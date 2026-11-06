package com.vertexpvp.arena;

import com.mojang.authlib.GameProfile;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.UUIDUtil;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ClientInformation;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.network.CommonListenerCookie;
import net.minecraft.util.Mth;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.entity.projectile.ProjectileUtil;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec2;
import net.minecraft.world.phys.Vec3;

/**
 * A player with no client: the arena feeds it one {@link BotAction} per tick.
 *
 * <p>A normal server player's movement is simulated by its client; the server's network handler
 * runs {@code doTick()} and then snaps the player back to where the client said it was. A bot has
 * no network handler ticking it, so it runs {@code doTick()} itself and the server's own physics,
 * knockback and combat apply. Everything a client would decide from key presses (movement input,
 * sprint start/stop, what the crosshair is on, the whiff lockout) is copied from the vanilla
 * client ({@code KeyboardInput}, {@code LocalPlayer}, {@code GameRenderer.pick},
 * {@code Minecraft.startAttack}).
 */
public final class BotPlayer extends ServerPlayer {

	/** Fastest a bot can turn, in degrees per tick (1000 degrees per second): a human-like cap. */
	public static final float MAX_TURN_PER_TICK = 50f;
	/** Ticks a survival or adventure client refuses to attack after clicking at nothing. */
	static final int MISS_LOCKOUT_TICKS = 10;

	private final BotConnection link;
	private BotAction action = BotAction.IDLE;
	/** One click per action, however many ticks it lasts: at 2 ticks per decision, at most 10 clicks a second. */
	private boolean clickPending;
	private int missCooldown;

	/** What happened on the last tick, for the observation. */
	boolean swungLastTick;
	boolean attackedLastTick;
	boolean critAttackLastTick;
	boolean sprintAttackLastTick;
	float chargeAtLastSwing;

	private BotPlayer(MinecraftServer server, ServerLevel level, GameProfile profile, BotConnection link) {
		super(server, level, profile, ClientInformation.createDefault());
		this.link = link;
		link.bind(getId());
	}

	static BotPlayer join(MinecraftServer server, ServerLevel level, String name) {
		GameProfile profile = new GameProfile(UUIDUtil.createOfflinePlayerUUID(name), name);
		BotConnection link = new BotConnection();
		BotPlayer bot = new BotPlayer(server, level, profile, link);
		server.getPlayerList().placeNewPlayer(link, bot, CommonListenerCookie.createInitial(profile, false));
		return bot;
	}

	/**
	 * Players are client-authoritative, so on the server {@code Entity.move} skips fall tracking
	 * (no fall distance, so no crits and no fall damage) and only updates {@code onGround} on vertical
	 * movement. A bot has no client: the server is its authority.
	 */
	@Override
	public boolean isClientAuthoritative() {
		return false;
	}

	void setAction(BotAction action) {
		this.action = action == null ? BotAction.IDLE : action;
		this.clickPending = this.action.attack();
	}

	/** The keys this bot is holding (its current action). */
	BotAction action() {
		return action;
	}

	int missCooldown() {
		return missCooldown;
	}

	/** Full attack charge, as at the start of a fight. */
	void resetCombatState() {
		attackStrengthTicker = 100;
		missCooldown = 0;
		action = BotAction.IDLE;
		clickPending = false;
		link.takeMotion();   // drop knockback still in flight from the last fight
		swungLastTick = attackedLastTick = critAttackLastTick = sprintAttackLastTick = false;
	}

	@Override
	public void tick() {
		// The same order as a client tick: packets (knockback), look, the attack key, then move.
		Vec3 knockback = link.takeMotion();
		if (knockback != null) {
			setDeltaMovement(knockback);
		}
		look();
		attackKey();
		movementKeys();
		super.tick();   // server-side player bookkeeping
		doTick();       // physics; for a real player the network handler would call this
	}

	private void look() {
		float yaw = getYRot() + Mth.clamp(action.dYaw(), -MAX_TURN_PER_TICK, MAX_TURN_PER_TICK);
		float pitch = Mth.clamp(getXRot() + Mth.clamp(action.dPitch(), -MAX_TURN_PER_TICK, MAX_TURN_PER_TICK), -90f, 90f);
		setYRot(yaw);
		setXRot(pitch);
		setYHeadRot(yaw);
	}

	private void attackKey() {
		swungLastTick = attackedLastTick = critAttackLastTick = sprintAttackLastTick = false;
		if (missCooldown > 0) {
			missCooldown--;
		}
		boolean click = clickPending;
		clickPending = false;   // a click during the whiff lockout is lost, as on a client
		if (!click || missCooldown > 0 || isSpectator()) {
			return;
		}
		HitResult hit = crosshair(this);
		float charge = getAttackStrengthScale(0.5f);
		if (hit instanceof EntityHitResult entityHit && !cannotAttackWithItem(getMainHandItem(), 5)) {
			Entity target = entityHit.getEntity();
			boolean full = charge > 0.9f;
			// The same conditions Player.attack uses; computed before attacking changes them.
			critAttackLastTick = full && fallDistance > 0.0 && !onGround() && !onClimbable() && !isInWater()
					&& !isMobilityRestricted() && !isPassenger() && target instanceof LivingEntity && !isSprinting();
			sprintAttackLastTick = full && isSprinting();
			attack(target);
			attackedLastTick = true;
		} else if (hit.getType() == HitResult.Type.MISS) {
			missCooldown = MISS_LOCKOUT_TICKS;
		}
		chargeAtLastSwing = charge;
		swing(InteractionHand.MAIN_HAND);   // resets the attack charge, as the swing packet does
		swungLastTick = true;
	}

	/** What the crosshair is on, as {@code GameRenderer.pick} works it out. */
	/** What a client's crosshair is on: {@code GameRenderer.pick} (entity reach 3, block reach 4.5), for any player. */
	static HitResult crosshair(ServerPlayer p) {
		double entityRange = p.entityInteractionRange();
		double blockRange = p.blockInteractionRange();
		double pickRange = Math.max(entityRange, blockRange);
		Vec3 eye = p.getEyePosition();
		Vec3 view = p.getViewVector(1f);
		Vec3 end = eye.add(view.scale(pickRange));
		BlockHitResult block = p.level().clip(new ClipContext(eye, end, ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE, p));
		double maxDistSq = block.getType() == HitResult.Type.MISS ? pickRange * pickRange : block.getLocation().distanceToSqr(eye);
		AABB box = p.getBoundingBox().expandTowards(view.scale(pickRange)).inflate(1.0);
		EntityHitResult entity = ProjectileUtil.getEntityHitResult(p, eye, end, box,
				e -> !e.isSpectator() && e.isPickable(), maxDistSq);
		if (entity != null && entity.getLocation().distanceToSqr(eye) < entityRange * entityRange) {
			return entity;
		}
		if (block.getType() != HitResult.Type.MISS && block.getLocation().distanceToSqr(eye) < blockRange * blockRange) {
			return block;
		}
		return BlockHitResult.miss(end, Direction.getApproximateNearest(view), BlockPos.containing(end));
	}

	/** Distance from {@code p}'s eyes to the nearest point of {@code target}'s hitbox: what the 3-block reach is measured on. */
	static double reachDistance(ServerPlayer p, Entity target) {
		Vec3 eye = p.getEyePosition();
		AABB b = target.getBoundingBox();
		return eye.distanceTo(new Vec3(Mth.clamp(eye.x, b.minX, b.maxX), Mth.clamp(eye.y, b.minY, b.maxY),
				Mth.clamp(eye.z, b.minZ, b.maxZ)));
	}

	/** Keys to movement input and sprinting, as {@code KeyboardInput} and {@code LocalPlayer} do it. */
	private void movementKeys() {
		setShiftKeyDown(action.sneak());
		Vec2 move = new Vec2(impulse(action.left(), action.right()), impulse(action.forward(), action.back())).normalized();
		boolean forwardImpulse = move.y > 1.0E-5f;
		boolean sprintPossible = !isMobilityRestricted() && getFoodData().hasEnoughFood();
		boolean movingSlowly = isCrouching() || isVisuallyCrawling();
		if (!isSprinting() && action.sprint() && forwardImpulse && sprintPossible && !isUsingItem() && !movingSlowly) {
			setSprinting(true);
		}
		if (isSprinting() && (!sprintPossible || !forwardImpulse || horizontalCollision && !minorHorizontalCollision)) {
			setSprinting(false);
		}
		if (move.lengthSquared() > 0f) {
			move = move.scale(0.98f);
			if (movingSlowly) {
				move = move.scale((float) getAttributeValue(Attributes.SNEAKING_SPEED));
			}
			move = squareMovement(move);
		}
		xxa = move.x;
		zza = move.y;
		setJumping(action.jump());
	}

	private static float impulse(boolean positive, boolean negative) {
		return positive == negative ? 0f : positive ? 1f : -1f;
	}

	private static Vec2 squareMovement(Vec2 input) {
		float length = input.length();
		if (length <= 0f) {
			return input;
		}
		Vec2 direction = input.scale(1f / length);
		float x = Math.abs(direction.x);
		float y = Math.abs(direction.y);
		float tan = y > x ? x / y : y / x;
		float toSquare = Mth.sqrt(1f + Mth.square(tan));
		return direction.scale(Math.min(length * toSquare, 1f));
	}
}
