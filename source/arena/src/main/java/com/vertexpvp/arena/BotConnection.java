package com.vertexpvp.arena;

import io.netty.buffer.ByteBuf;
import io.netty.buffer.Unpooled;
import io.netty.channel.ChannelFutureListener;
import net.minecraft.network.Connection;
import net.minecraft.network.DisconnectionDetails;
import net.minecraft.network.PacketListener;
import net.minecraft.network.ProtocolInfo;
import net.minecraft.network.protocol.Packet;
import net.minecraft.network.protocol.PacketFlow;
import net.minecraft.network.protocol.game.ClientboundBundlePacket;
import net.minecraft.network.protocol.game.ClientboundSetEntityMotionPacket;
import net.minecraft.world.phys.Vec3;
import org.jspecify.annotations.Nullable;

/**
 * A network connection with no client behind it. Everything the server would send to a bot is
 * dropped (a connection without a channel otherwise queues every packet forever, and the server
 * sends players packets every tick), except one: the bot's own motion packet.
 *
 * <p>Knockback reaches a player only through that packet. {@code Player.causeExtraKnockback}
 * sends the victim's new velocity to the victim's client and then puts the server-side velocity
 * back, because a real player's client owns its movement; the client sets its velocity to the
 * packet's value ({@code Entity.lerpMotion}). The bot keeps the value, as its client would have
 * received it, and applies it at the start of its next tick.
 */
final class BotConnection extends Connection {

	private int ownId = -1;
	private @Nullable Vec3 pendingMotion;

	BotConnection() {
		super(PacketFlow.SERVERBOUND);
	}

	void bind(int entityId) {
		ownId = entityId;
	}

	/** The velocity the server last sent this bot, once; null if none arrived since the last call. */
	@Nullable Vec3 takeMotion() {
		Vec3 motion = pendingMotion;
		pendingMotion = null;
		return motion;
	}

	private void receive(Packet<?> packet) {
		if (packet instanceof ClientboundSetEntityMotionPacket motion && motion.id() == ownId) {
			pendingMotion = overTheWire(motion.movement());
		} else if (packet instanceof ClientboundBundlePacket bundle) {
			bundle.subPackets().forEach(this::receive);
		}
	}

	/** The packet's velocity encoding is lossy; pass through it so the bot gets what a client would. */
	private static Vec3 overTheWire(Vec3 movement) {
		ByteBuf buf = Unpooled.buffer();
		try {
			Vec3.LP_STREAM_CODEC.encode(buf, movement);
			return Vec3.LP_STREAM_CODEC.decode(buf);
		} finally {
			buf.release();
		}
	}

	@Override
	public <T extends PacketListener> void setupInboundProtocol(ProtocolInfo<T> protocol, T packetListener) {
	}

	@Override
	public void setupOutboundProtocol(ProtocolInfo<?> protocol) {
	}

	@Override
	public void setListenerForServerboundHandshake(PacketListener packetListener) {
	}

	@Override
	public void send(Packet<?> packet, ChannelFutureListener listener, boolean flush) {
		receive(packet);
	}

	@Override
	public void flushChannel() {
	}

	@Override
	public void setReadOnly() {
	}

	@Override
	public void handleDisconnection() {
	}

	@Override
	public void disconnect(DisconnectionDetails details) {
	}
}
