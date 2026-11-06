"""Hand-written opponents: the first rung of the self-play ladder, and a sanity check on the arena.

They read the full server state (they're baselines, not learners) and act through the same
controls and turn-rate cap as any policy.
"""

import collections
import random

from .env import relative_view

MAX_TURN = 40.0


def idle(state, i):
    return {}


class RandomFighter:
    """Mashes buttons: the floor every learner must beat."""

    def __init__(self, seed=None):
        self.rng = random.Random(seed)

    def __call__(self, state, i):
        r = self.rng
        return {"forward": r.random() < 0.6, "back": r.random() < 0.1, "left": r.random() < 0.2,
                "right": r.random() < 0.2, "jump": r.random() < 0.1, "sprint": r.random() < 0.5,
                "sneak": r.random() < 0.05, "attack": r.random() < 0.3,
                "dyaw": r.uniform(-30, 30), "dpitch": r.uniform(-5, 5)}


class ChaseFighter:
    """Faces the opponent, sprints in, attacks on full charge in reach, W-taps after landing a hit,
    and strafes a little. Roughly a beginner who knows the cooldown."""

    def __init__(self, seed=None, strafe=True, crits=False, repeat=1, delay=0, skill=1.0, punish=False, eager=False):
        """repeat: ticks per decision (a turn command applies every tick). delay: decisions between
        what happens and what the bot sees (the env's reaction_steps). Like a player, it remembers
        the turns and clicks it sent since the moment it is seeing, so its aim doesn't overshoot.
        skill: 0 stands still, 1 is the full bot; see `_handicap` (the curriculum's dial).
        punish: play like the owner beat the champion (see `_punish`).
        eager: swing a little too early, at the edge of reach and with looser aim, as people often do
        (the owner whiffs ~30% of swings, a third of them out of reach): it whiffs and gets locked out,
        so baiting and punishing pay off against it."""
        self.eager = eager
        self.rng = random.Random(seed)
        self.punish = punish
        self.delay = delay
        self.since_op_swing = 100   # decisions since the opponent's last swing (as seen)
        self.op_whiffed = False
        self.since_my_hit = 100
        self.skill = max(0.0, min(1.0, skill))
        self.repeat = repeat
        self.sent = collections.deque([(0.0, 0.0, False)] * delay, maxlen=delay) if delay else None
        self.strafe = strafe
        self.crits = crits
        self.wtap = 0
        self.since_jump = 100      # decisions since this bot pressed jump
        self.strafe_dir = 1
        self.last_hits = 0

    def __call__(self, state, i):
        me, op = state["bots"][i], state["bots"][1 - i]
        view = relative_view(me, op)
        if self.punish and self.delay:
            # Lead the target: aim where both will be now, not where they were `delay` decisions ago.
            lag = self.delay * self.repeat
            ahead = lambda b: {**b, "x": b["x"] + b["vx"] * lag, "y": b["y"], "z": b["z"] + b["vz"] * lag}
            led = relative_view(ahead(me), ahead(op))
            view = {**view, "yaw_off": led["yaw_off"], "pitch_off": led["pitch_off"]}
        hits = sum(1 for e in state["events"] if e["type"] == "hit" and e["attacker"] == i)
        if hits:
            self.wtap = 2          # release W briefly after a hit, to re-sprint for the next one
        op_swings = [e for e in state["events"] if e["type"] == "swing" and e["bot"] == 1 - i]
        self.since_op_swing = 0 if op_swings else self.since_op_swing + 1
        if op_swings:
            self.op_whiffed = not op_swings[-1]["hit"]
        self.since_my_hit = 0 if hits else self.since_my_hit + 1
        if self.rng.random() < 0.05:
            self.strafe_dir = -self.strafe_dir
        yaw_off, pitch_off = view["yaw_off"], view["pitch_off"] + 8.0   # aim at the body, not the eyes
        clicked_unseen = False
        if self.sent is not None:
            yaw_off -= sum(t[0] for t in self.sent)
            pitch_off -= sum(t[1] for t in self.sent)
            clicked_unseen = any(t[2] for t in self.sent)
        aimed = abs(yaw_off) < (20 if self.eager else 12)
        in_reach = view["dist"] < (3.6 if self.eager else 2.9)
        charged = me["charge"] > 0.9 and not clicked_unseen
        action = {
            "dyaw": max(-MAX_TURN, min(MAX_TURN, yaw_off / self.repeat)),
            "dpitch": max(-10.0, min(10.0, pitch_off / self.repeat)),
            "forward": self.wtap == 0 and view["dist"] > 1.2,
            "sprint": view["dist"] > 1.2,
            "left": self.strafe and in_reach and self.strafe_dir > 0,
            "right": self.strafe and in_reach and self.strafe_dir < 0,
            "attack": aimed and in_reach and charged,
        }
        if self.crits:
            # Jump crits: walk in without sprinting (a sprinting hit can't crit), jump when close,
            # and swing only on the way down.
            action["sprint"] = False
            if self.sent is None:
                falling = not me["on_ground"] and me["vy"] < 0
                action["jump"] = me["on_ground"] and view["dist"] < 3.2 and charged
            else:
                # With a delayed view it would see itself falling only as it lands, so it times the
                # swing from its own jump, as players do: a jump peaks after ~6 ticks and lands after ~12.
                airborne_ticks = self.since_jump * self.repeat
                falling = 7 <= airborne_ticks <= 10
                action["jump"] = me["on_ground"] and airborne_ticks > 12 and view["dist"] < 3.2 and charged
            action["attack"] = falling and aimed and in_reach and charged
            self.since_jump = 0 if action["jump"] else self.since_jump + 1
        if self.punish:
            self._punish(action, me, op, view, aimed, charged)
        self.wtap = max(0, self.wtap - 1)
        if self.skill < 1.0:
            self._handicap(action)
        if self.sent is not None:
            self.sent.append((action.get("dyaw", 0.0) * self.repeat, action.get("dpitch", 0.0) * self.repeat,
                              action.get("attack", False)))
        return action

    def _punish(self, action, me, op, view, aimed, charged):
        """How the owner beats the champion (duel analysis): stay just outside reach while the
        opponent can swing, so their swings whiff; a whiff locks them out for 10 ticks, so when one
        is seen (`delay` decisions late, as a player would see it) and they're close enough to reach
        in time, rush in and hit; after landing a hit, stay on them (combo) and jump-crit when close.
        Distances are predicted forward over the delay from how fast the gap is closing."""
        lag = self.delay * self.repeat
        dx, dz = op["x"] - me["x"], op["z"] - me["z"]
        d = max(view["dist"], 1e-6)
        closing = ((op["vx"] - me["vx"]) * dx + (op["vz"] - me["vz"]) * dz) / d   # + = moving apart
        d_now = max(0.0, d + closing * lag)
        op_ticks = (self.since_op_swing + self.delay) * self.repeat   # ticks since they swung, now
        locked_out = self.op_whiffed and op_ticks < 10
        combo = self.since_my_hit * self.repeat < 20
        if (locked_out and d_now < 4.5) or combo:
            close = d_now < 3.0
            want_crit = combo and close and me["on_ground"] and charged and self.since_jump * self.repeat > 12
            action["forward"] = d_now > 0.8
            action["back"] = False
            action["sprint"] = not want_crit and d_now > 0.8
            action["jump"] = want_crit
            airborne = self.since_jump * self.repeat
            in_air_wrong_time = airborne <= 12 and not 7 <= airborne <= 10   # rising: wait for the fall
            action["attack"] = aimed and d_now < 2.8 and charged and not in_air_wrong_time
        else:
            # Spacing: hover at 3.2–3.9 blocks, strafing, and only trade if they walk into reach.
            action["forward"] = d_now > 3.9
            action["back"] = d_now < 3.2
            action["sprint"] = d_now > 4.5
            action["left"] = self.strafe_dir > 0
            action["right"] = self.strafe_dir < 0
            action["jump"] = False
            action["attack"] = aimed and d_now < 2.7 and charged
        self.since_jump = 0 if action.get("jump") else self.since_jump + 1

    def _handicap(self, action):
        """Weaker play for skill < 1. Below 0.1 it stands still; movement, then sprinting, then
        clicking and strafing switch on as skill rises, and its aim gets steadier."""
        k, r = self.skill, self.rng
        if k < 0.1:
            action.clear()
            return
        if r.random() > min(1.0, 2 * k):                            # moves on 20% → 100% of decisions
            action["forward"] = action["sprint"] = action["jump"] = False
        if r.random() > max(0.0, min(1.0, (k - 0.3) / 0.5)):        # sprints from skill 0.3, fully at 0.8
            action["sprint"] = False
        if r.random() > max(0.0, min(1.0, (k - 0.2) / 0.6)):        # clicks from 0.2, every chance at 0.8
            action["attack"] = False
        if k < 0.6:
            action["left"] = action["right"] = False
        action["dyaw"] = action.get("dyaw", 0.0) + r.gauss(0.0, 10.0 * (1 - k))   # shakier aim
