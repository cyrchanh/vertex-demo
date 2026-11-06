package com.vertexpvp.arena;

import com.google.gson.JsonObject;

/**
 * What a bot does on one tick: the same controls a player has for sword PvP.
 *
 * @param dYaw   degrees to turn right this tick (capped by {@link BotPlayer#MAX_TURN_PER_TICK})
 * @param dPitch degrees to look down this tick (capped likewise)
 * @param attack left click this tick
 */
public record BotAction(boolean forward, boolean back, boolean left, boolean right, boolean jump, boolean sprint,
		boolean sneak, boolean attack, float dYaw, float dPitch) {

	public static final BotAction IDLE = new BotAction(false, false, false, false, false, false, false, false, 0f, 0f);

	static BotAction fromJson(JsonObject o) {
		return new BotAction(flag(o, "forward"), flag(o, "back"), flag(o, "left"), flag(o, "right"), flag(o, "jump"),
				flag(o, "sprint"), flag(o, "sneak"), flag(o, "attack"),
				o.has("dyaw") ? o.get("dyaw").getAsFloat() : 0f, o.has("dpitch") ? o.get("dpitch").getAsFloat() : 0f);
	}

	private static boolean flag(JsonObject o, String key) {
		return o.has(key) && o.get(key).getAsBoolean();
	}
}
