package com.vertexpvp.vertex.record;

import com.vertexpvp.vertex.core.trace.CombatEvent.HitKind;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.player.Player;

/**
 * This client's prediction of how the server will classify the local player's attack, using the
 * exact rules from vanilla 26.2's {@code Player.attack} / {@code Player.canCriticalAttack}
 * (read from the game's bytecode):
 * <ul>
 *     <li>strong = {@code getAttackStrengthScale(0.5f) > 0.9}</li>
 *     <li>crit = strong, {@code fallDistance > 0}, not on ground, not climbing, not in water, not
 *     blind ({@code isMobilityRestricted}), not riding, target is a living entity, not sprinting</li>
 *     <li>sprint hit = strong and sprinting</li>
 * </ul>
 * Must be called before the attack is processed (from the attack callback), because vanilla resets
 * attack strength during the attack. The server's own verdict arrives later in packets; the two
 * are stored side by side so latency/desync disagreements become visible.
 */
public final class ClientCritRules {

	private ClientCritRules() {
	}

	public static HitKind predict(Player attacker, Entity target) {
		boolean strong = attacker.getAttackStrengthScale(0.5f) > 0.9f;
		if (!strong) {
			return HitKind.WEAK;
		}
		boolean crit = attacker.fallDistance > 0.0
				&& !attacker.onGround()
				&& !attacker.onClimbable()
				&& !attacker.isInWater()
				&& !attacker.isMobilityRestricted()
				&& !attacker.isPassenger()
				&& target instanceof LivingEntity
				&& !attacker.isSprinting();
		if (crit) {
			return HitKind.CRIT;
		}
		return attacker.isSprinting() ? HitKind.SPRINT_HIT : HitKind.STRONG;
	}
}
