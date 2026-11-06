"""Two-player sword self-play environment over the vertex-arena server (PettingZoo parallel API).

Each policy sees only what a real client could: its own exact state, and the opponent's position,
movement, health and flags, but the opponent's attack charge only as an estimate from their swings
(as Vertex's replay viewer does). The reward is net damage, zero-sum: Δ(my health − theirs) / 20 per
step, plus +1 / −1 when the fight ends in a death. Per-step net damage is a potential-based shaping
term (Φ = my health − theirs), so it speeds learning without changing what's optimal; see the
paper's Proposition 1.

When time runs out, whoever is ahead on health wins by that margin (`outcome_score`), and the
terminal reward is ±TIMEOUT_BONUS × that margin: still zero-sum, but running away no longer earns
an even result unless you're ahead, as in real PvP.
"""

import collections
import math

import numpy as np
from gymnasium import spaces
from pettingzoo import ParallelEnv

from .client import ArenaClient

SWORD_DELAY_TICKS = 12.5
EYE_HEIGHT = 1.62
YAW_BINS = np.array([-40.0, -20.0, -10.0, -4.0, 0.0, 4.0, 10.0, 20.0, 40.0], dtype=np.float32)
PITCH_BINS = np.array([-10.0, -3.0, 0.0, 3.0, 10.0], dtype=np.float32)
# forward/back, left/right, jump, sprint, sneak, attack, turn, look up/down
ACTION_NVEC = [3, 3, 2, 2, 2, 2, len(YAW_BINS), len(PITCH_BINS)]
# Observation layouts. 1: everything `reaction_steps` decisions late (v1, v2 and their snapshots).
# 2: what a player perceives: their own body live (they feel their jump, sprint and fall), their
# crosshair on the opponent and reach distance one decision late (~100 ms, how fast a player clicks
# while tracking; instant would be a trigger-bot), the opponent `reaction_steps` late (~200 ms).
OBS_SIZES = {1: 37, 2: 40}
SELF_FEATURES = 13          # layout 1's first 13 numbers describe the observer's own state
OBS_LAYOUT = 2              # for new policies
OBS_SIZE = OBS_SIZES[OBS_LAYOUT]
TIMEOUT_BONUS = 0.5
LEAD_FOR_FULL_SCORE = 10.0   # HP ahead at the time limit that counts as a full win


def outcome_score(state, side, timeout_loss=False):
    """1 win, 0 loss; at the time limit, 0.5 ± half the health lead / LEAD_FOR_FULL_SCORE, or 0 for
    both with timeout_loss (only winning counts: running out the clock earns nothing)."""
    if state["winner"] >= 0:
        return float(state["winner"] == side)
    if timeout_loss:
        return 0.0
    me, op = state["bots"][side], state["bots"][1 - side]
    lead = (me["health"] + me["absorption"]) - (op["health"] + op["absorption"])
    return 0.5 + 0.5 * max(-1.0, min(1.0, lead / LEAD_FOR_FULL_SCORE))


def wrap_degrees(d):
    return (d + 180.0) % 360.0 - 180.0


def decode_action(a):
    """MultiDiscrete action → the dict the arena expects."""
    fb, lr, jump, sprint, sneak, attack, turn, look = (int(v) for v in a)
    return {"forward": fb == 1, "back": fb == 2, "left": lr == 1, "right": lr == 2, "jump": bool(jump),
            "sprint": bool(sprint), "sneak": bool(sneak), "attack": bool(attack),
            "dyaw": float(YAW_BINS[turn]), "dpitch": float(PITCH_BINS[look])}


def relative_view(me, op):
    """Geometry of `op` as seen by `me`: bearing offset, pitch offset to their eyes, distances."""
    dx, dy, dz = op["x"] - me["x"], op["y"] - me["y"], op["z"] - me["z"]
    horizontal = math.hypot(dx, dz)
    yaw_to = math.degrees(math.atan2(-dx, dz))
    pitch_to = -math.degrees(math.atan2(dy, max(horizontal, 1e-6)))
    return {"dx": dx, "dy": dy, "dz": dz, "dist": horizontal,
            "yaw_off": wrap_degrees(yaw_to - me["yaw"]), "pitch_off": pitch_to - me["pitch"]}


def aim_geometry(me, op, reach=3.0):
    """(crosshair on the opponent, eye-to-hitbox distance) from positions and rotation alone: the
    game's pick in an arena with nothing in the way (a ray from the eyes against the 0.6 × 1.8
    hitbox, entity reach 3)."""
    eye = np.array([me["x"], me["y"] + (1.27 if me.get("sneaking") else EYE_HEIGHT), me["z"]])
    yaw, pitch = math.radians(me["yaw"]), math.radians(me["pitch"])
    view = np.array([-math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch)])
    lo = np.array([op["x"] - 0.3, op["y"], op["z"] - 0.3])
    hi = np.array([op["x"] + 0.3, op["y"] + 1.8, op["z"] + 0.3])
    nearest = np.clip(eye, lo, hi)
    distance = float(np.linalg.norm(eye - nearest))
    with np.errstate(divide="ignore", invalid="ignore"):
        t1, t2 = (lo - eye) / view, (hi - eye) / view
    t_near = np.nanmax(np.minimum(t1, t2))
    t_far = np.nanmin(np.maximum(t1, t2))
    on_target = bool(t_far >= max(t_near, 0.0) and t_near <= reach)
    return on_target, distance


class SwordArenaEnv(ParallelEnv):
    metadata = {"name": "vertex_sword_v0"}

    def __init__(self, port=25570, max_ticks=1200, repeat=2, reaction_steps=2, seed=None, aim_noise=0.0,
                 obs_layout=OBS_LAYOUT):
        """repeat: ticks per decision (2 = 10 decisions a second; attack is one click per decision, so
        at most 10 clicks a second). reaction_steps: decisions of delay before a policy sees what
        happened (2 × 2 ticks = 200 ms, a human-like reaction time). aim_noise: standard deviation, in
        degrees, added to each turn a policy makes (hands aren't exact)."""
        self.port = port
        self.obs_layout = obs_layout
        self.aim_noise = aim_noise
        self._rng = np.random.default_rng(seed)
        self.max_ticks = max_ticks
        self.timeout_loss = False   # set by the trainer: a time-out is a loss (−1) for both
        self.repeat = repeat
        self.reaction_steps = reaction_steps
        self._seed = seed
        self.possible_agents = ["bot_0", "bot_1"]
        self.agents = []
        self.client = None
        self._obs_space = spaces.Box(-np.inf, np.inf, shape=(OBS_SIZES[obs_layout],), dtype=np.float32)
        self._act_space = spaces.MultiDiscrete(ACTION_NVEC)

    def observation_space(self, agent):
        return self._obs_space

    def action_space(self, agent):
        return self._act_space

    # ---- PettingZoo API ----

    def reset(self, seed=None, options=None):
        if self.client is None:
            self.client = ArenaClient(port=self.port)
        if seed is not None:
            self._seed = seed
        options = options or {}
        self.fight_ticks = options.get("max_ticks", self.max_ticks)
        reply = self.client.reset(seed=self._seed, max_ticks=self.fight_ticks,
                                  start_distance=options.get("start_distance"), countdown=options.get("countdown", 0))
        if self._seed is not None:
            self._seed += 1
        return self.begin(reply)

    def begin(self, reply, fight_ticks=None):
        """Start a fight from the server's first observation (also used to replay a recorded fight)."""
        if fight_ticks is not None:
            self.fight_ticks = fight_ticks
        self.agents = list(self.possible_agents)
        self.last_swing = [-1000, -1000]
        self.hits_recent = [collections.deque(), collections.deque()]
        self.state = reply
        self.replies = collections.deque([reply] * (self.reaction_steps + 1), maxlen=self.reaction_steps + 1)
        self.potential = self._potential(reply)
        self.history = [collections.deque(maxlen=self.reaction_steps + 1) for _ in range(2)]
        for i in range(2):
            vec = self._observe(reply, i)
            for _ in range(self.reaction_steps + 1):
                self.history[i].append(vec)
        obs = {agent: self.observation(i) for i, agent in enumerate(self.possible_agents)}
        return obs, {agent: {} for agent in self.agents}

    def step(self, actions):
        raw = [actions[a] if isinstance(actions[a], dict) else self._noisy(decode_action(actions[a]))
               for a in self.possible_agents]
        reply = self.client.step(raw, repeat=self.repeat)
        return self.advance(reply)

    def advance(self, reply):
        """Process the server's observation after a step (also used to replay a recorded fight)."""
        self._track_events(reply)
        self.state = reply
        self.replies.append(reply)
        new_potential = self._potential(reply)
        shaped = (new_potential - self.potential) / 20.0
        self.potential = new_potential
        done = reply["done"]
        winner = reply["winner"]
        rewards, terms, truncs, obs, infos = {}, {}, {}, {}, {}
        for i, agent in enumerate(self.possible_agents):
            r = shaped if i == 0 else -shaped
            if done and winner >= 0:
                r += 1.0 if winner == i else -1.0
            elif done:
                r += -1.0 if self.timeout_loss else TIMEOUT_BONUS * 2.0 * (outcome_score(reply, i) - 0.5)
            rewards[agent] = r
            terms[agent] = done and winner >= 0
            truncs[agent] = done and winner < 0
            self.history[i].append(self._observe(reply, i))
            infos[agent] = {"tick": reply["tick"], "winner": winner, "events": reply["events"]}
        obs = {agent: self.observation(i) for i, agent in enumerate(self.possible_agents)}
        if done:
            self.agents = []
        return obs, rewards, terms, truncs, infos

    def observation(self, i, layout=None):
        """Fighter i's observation now, in `layout` (default: this env's). Any layout can be asked for
        at any time, so policies trained on different layouts can share a fight."""
        layout = layout or self.obs_layout
        delayed = self.history[i][0]          # what happened `reaction_steps` decisions ago
        if layout == 1:
            return delayed
        live = self.replies[-1]
        aim = self.replies[-2] if len(self.replies) > 1 else live
        me_live, me_aim = live["bots"][i], aim["bots"][i]
        crit_ready = me_live["fall_distance"] > 0 and not me_live["on_ground"] and not me_live["sprinting"]
        on_target, reach = (me_aim["aim_on_target"], me_aim["reach_distance"]) if "aim_on_target" in me_aim \
            else aim_geometry(me_aim, aim["bots"][1 - i])   # recorded before the server reported them
        extra = [float(on_target), reach / 3.0, float(crit_ready)]
        return np.concatenate([self._self_features(me_live), delayed[SELF_FEATURES:],
                               np.asarray(extra, dtype=np.float32)])

    @property
    def delayed_state(self):
        """The arena state `reaction_steps` decisions ago: what scripted bots should react to, so they
        see the world as late as a policy does."""
        return self.replies[0]

    def _noisy(self, action):
        if self.aim_noise > 0:
            action["dyaw"] += float(self._rng.normal(0.0, self.aim_noise))
            action["dpitch"] += float(self._rng.normal(0.0, self.aim_noise))
        return action

    def close(self):
        if self.client is not None:
            self.client.close()
            self.client = None

    # ---- observation ----

    @staticmethod
    def _potential(reply):
        a, b = reply["bots"]
        return (a["health"] + a["absorption"]) - (b["health"] + b["absorption"])

    def _track_events(self, reply):
        for e in reply["events"]:
            if e["type"] == "swing":
                self.last_swing[e["bot"]] = e["tick"]
            elif e["type"] == "hit":
                self.hits_recent[e["attacker"]].append(e["tick"])
        for q in self.hits_recent:
            while q and q[0] < reply["tick"] - 40:
                q.popleft()

    @staticmethod
    def _self_features(me):
        """The observer's own state (layout 1 sees it delayed, layout 2 live)."""
        yaw = math.radians(me["yaw"])
        fwd = np.array([-math.sin(yaw), math.cos(yaw)])
        right = np.array([-math.cos(yaw), -math.sin(yaw)])
        my_v = np.array([me["vx"], me["vz"]])
        return np.asarray([
            me["health"] / 20, me["absorption"] / 20, float(me["on_ground"]), float(me["sprinting"]),
            float(me["sneaking"]), me["hurt_time"] / 10, me["charge"], me["miss_cooldown"] / 10,
            me["vy"], my_v @ fwd, my_v @ right, me["pitch"] / 90, me["food"] / 20,
        ], dtype=np.float32)

    def _observe(self, reply, i):
        """Layout 1: everything from `reply` (which the env then delays)."""
        me, op = reply["bots"][i], reply["bots"][1 - i]
        tick = reply["tick"]
        view = relative_view(me, op)
        yaw = math.radians(me["yaw"])
        fwd = np.array([-math.sin(yaw), math.cos(yaw)])
        right = np.array([-math.cos(yaw), -math.sin(yaw)])
        rel = np.array([view["dx"], view["dz"]])
        rel_v = np.array([op["vx"] - me["vx"], op["vz"] - me["vz"]])
        my_v = np.array([me["vx"], me["vz"]])
        op_view = relative_view(op, me)
        op_charge = min(1.0, (tick - self.last_swing[1 - i] + 0.5) / SWORD_DELAY_TICKS)
        f = [
            *self._self_features(me),
            # aim
            math.sin(math.radians(view["yaw_off"])), math.cos(math.radians(view["yaw_off"])), view["pitch_off"] / 90,
            # opponent (what a client sees)
            (rel @ fwd) / 8, (rel @ right) / 8, view["dy"] / 4, view["dist"] / 8,
            (rel_v @ fwd), (rel_v @ right), op["vy"],
            float(op["on_ground"]), float(op["sprinting"]), float(op["sneaking"]),
            op["health"] / 20, op["absorption"] / 20, op["hurt_time"] / 10, op_charge,
            math.sin(math.radians(op_view["yaw_off"])), math.cos(math.radians(op_view["yaw_off"])),
            # recent exchange and time
            len(self.hits_recent[i]) / 5, len(self.hits_recent[1 - i]) / 5,
            (me["health"] - op["health"]) / 20, tick / self.fight_ticks, 1.0,
        ]
        return np.asarray(f, dtype=np.float32)
