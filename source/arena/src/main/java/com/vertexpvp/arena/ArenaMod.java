package com.vertexpvp.arena;

import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.entity.event.v1.ServerLivingEntityEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.gamerules.GameRules;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Set;

/**
 * Entry point: on server start, builds arena A0, joins two bots and listens for a trainer.
 * Port: system property {@code vertex.arena.port}, default 25570.
 */
public final class ArenaMod implements ModInitializer {

	private static final Logger LOG = LoggerFactory.getLogger("vertex-arena");
	private static ArenaSession session;

	@Override
	public void onInitialize() {
		ServerLifecycleEvents.SERVER_STARTED.register(server -> {
			ServerLevel level = server.overworld();
			level.getGameRules().set(GameRules.ADVANCE_TIME, false, server);
			level.getGameRules().set(GameRules.ADVANCE_WEATHER, false, server);
			level.getGameRules().set(GameRules.SPAWN_MOBS, false, server);
			ArenaLayout.build(level);
			BotPlayer[] bots = {BotPlayer.join(server, level, "VertexBotA"), BotPlayer.join(server, level, "VertexBotB")};
			session = new ArenaSession(server, bots);
			session.listen(Integer.getInteger("vertex.arena.port", 25570));
			// Health under each bot's name, for spectators.
			server.getCommands().performPrefixedCommand(server.createCommandSourceStack().withSuppressedOutput(),
					"scoreboard objectives add hp health \"HP\"");
			server.getCommands().performPrefixedCommand(server.createCommandSourceStack().withSuppressedOutput(),
					"scoreboard objectives setdisplay below_name hp");
			LOG.info("Vertex arena: arena built, bots {} and {} joined", bots[0].getPlainTextName(), bots[1].getPlainTextName());
		});
		// A person who joins watches: spectator mode, above the south wall, looking at the centre.
		ServerPlayConnectionEvents.JOIN.register((handler, sender, server) -> {
			ServerPlayer player = handler.getPlayer();
			if (player instanceof BotPlayer) {
				return;
			}
			player.setGameMode(GameType.SPECTATOR);
			player.teleportTo(server.overworld(), 0.5, ArenaLayout.FLOOR_Y + 8, ArenaLayout.HALF + 6, Set.of(), 180f, 30f, true);
		});
		ServerTickEvents.START_SERVER_TICK.register(server -> {
			if (session != null) {
				session.beforeTick();
			}
		});
		ServerTickEvents.END_SERVER_TICK.register(server -> {
			if (session != null) {
				session.afterTick();
			}
		});
		ServerLivingEntityEvents.AFTER_DAMAGE.register((entity, source, baseDamage, damageTaken, blocked) -> {
			if (session != null && source.getEntity() instanceof LivingEntity attacker) {
				session.onDamage(entity, attacker, damageTaken);
			}
		});
		ServerLivingEntityEvents.ALLOW_DEATH.register((entity, source, amount) -> session == null
				|| session.onDeath(entity, source.getEntity() instanceof LivingEntity attacker ? attacker : null, amount));
	}
}
