"""Talks to the vertex-arena server mod: JSON lines over a local socket, one reply per command."""

import json
import socket

DEFAULT_PORT = 25570


class ArenaError(RuntimeError):
    pass


class ArenaClient:
    def __init__(self, host="127.0.0.1", port=DEFAULT_PORT, timeout=60.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.reader = self.sock.makefile("r", encoding="utf-8", newline="\n")
        self.info = self._call({"cmd": "hello"})

    def _call(self, message):
        self.sock.sendall((json.dumps(message) + "\n").encode("utf-8"))
        line = self.reader.readline()
        if not line:
            raise ArenaError("arena closed the connection")
        reply = json.loads(line)
        if reply.get("type") == "error":
            raise ArenaError(reply.get("message"))
        return reply

    def reset(self, seed=None, max_ticks=1200, start_distance=None, countdown=0):
        """start_distance: (min, max) blocks apart; default 6–10. countdown: ticks of "3, 2, 1" (a
        person is held on their spot, nobody can be hurt) before the fight starts and this returns."""
        message = {"cmd": "reset", "max_ticks": max_ticks, "countdown": int(countdown)}
        if seed is not None:
            message["seed"] = int(seed)
        if start_distance is not None:
            message["min_dist"], message["max_dist"] = float(start_distance[0]), float(start_distance[1])
        return self._call(message)

    def step(self, actions, repeat=1):
        """actions: two dicts (forward, back, left, right, jump, sprint, sneak, attack, dyaw, dpitch)."""
        return self._call({"cmd": "step", "actions": actions, "repeat": repeat})

    def realtime(self, on=True):
        """20 ticks per second, so you can join the server (localhost:25566) and watch."""
        return self._call({"cmd": "realtime", "on": bool(on)})

    def duel(self, on=True):
        """Fighter 1 becomes the first person on the server (bot B steps aside). → their name."""
        return self._call({"cmd": "duel", "on": bool(on)}).get("human")

    def close(self):
        # The reader holds its own reference to the socket, so both must go, or the connection
        # stays open and the arena keeps serving this client instead of the next one.
        try:
            self.sock.sendall(b'{"cmd":"close"}\n')
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.reader.close()
        self.sock.close()
