package com.vertexpvp.vertex.core.trace;

/**
 * Where and with what a fight was recorded, so training can tell versions and servers apart.
 *
 * @param gameVersion  Minecraft version, e.g. {@code 26.2}
 * @param modVersion   Vertex version that recorded it
 * @param server       server address as typed in the server list, or {@code null} in singleplayer
 * @param serverBrand  what the server reports itself as (e.g. {@code vanilla}, {@code Paper}), or
 *                     {@code null} if it sent nothing
 * @param singleplayer true for the integrated server (singleplayer or a LAN world you host)
 */
public record RecordingContext(String gameVersion, String modVersion, String server, String serverBrand,
		boolean singleplayer) {
}
