"""Turns fights into training samples: features at one moment, target = net damage afterwards.

Every sample is built from one fighter's point of view ("me") and is symmetric: the same moment
appears twice, once per fighter, with the target's sign flipped. Net damage is in health points
(2 = one heart) and is zero-sum: what I deal minus what I take.
"""

import math

import numpy as np
import pandas as pd

SWORD_DELAY_TICKS = 12.5  # matches core/view/AttackCharge
HISTORY = 40              # ticks of look-back for momentum features
ENGAGED_RANGE = 8.0       # only judge moments where the fighters are this close (blocks)


def _since(events, n, cap):
    """For each frame, ticks since the last True in events (capped)."""
    out = np.full(n, cap, dtype=float)
    last = None
    for i in range(n):
        if events[i]:
            last = i
        if last is not None:
            out[i] = min(cap, i - last)
    return out


def _wrap(deg):
    return (deg + 180.0) % 360.0 - 180.0


def per_frame(fight):
    """Per-fighter derived series that features read from."""
    n = len(fight.ticks)
    index = {int(t): i for i, t in enumerate(fight.ticks)}
    landed = np.zeros((2, n), dtype=bool)
    for tick, attacker, _ in fight.hits:
        if tick in index and attacker in (0, 1):
            landed[attacker, index[tick]] = True
    took = fight.damage > 0
    out = []
    for p in range(2):
        f = fight.f[p]
        since_swing = _since(f["swingStart"], n, 60)
        out.append({
            "since_swing": since_swing,
            "charge": np.minimum(1.0, (since_swing + 0.5) / SWORD_DELAY_TICKS),
            "since_landed": _since(landed[p], n, 100),
            "since_took": _since(took[p], n, 100),
            "landed": landed[p],
        })
    return out


def _moment(fight, derived, t, me):
    op = 1 - me
    a, b = fight.f[me], fight.f[op]
    da, db = derived[me], derived[op]
    dx, dz = b["x"][t] - a["x"][t], b["z"][t] - a["z"][t]
    dist = math.hypot(dx, dz)
    ux, uz = (dx / dist, dz / dist) if dist > 1e-6 else (0.0, 0.0)
    bearing_ab = math.degrees(math.atan2(-dx, dz))      # Minecraft yaw that would face the opponent
    bearing_ba = math.degrees(math.atan2(dx, -dz))
    h0 = t - HISTORY
    dealt_recent = fight.damage[op, h0 + 1:t + 1].sum()
    taken_recent = fight.damage[me, h0 + 1:t + 1].sum()
    return {
        "dist": dist,
        "dy": b["y"][t] - a["y"][t],
        "dist_change_10": dist - math.hypot(b["x"][t - 10] - a["x"][t - 10], b["z"][t - 10] - a["z"][t - 10]),
        "my_closing": a["vx"][t] * ux + a["vz"][t] * uz,
        "op_closing": -(b["vx"][t] * ux + b["vz"][t] * uz),
        "my_speed": math.hypot(a["vx"][t], a["vz"][t]),
        "op_speed": math.hypot(b["vx"][t], b["vz"][t]),
        "my_vy": a["vy"][t], "op_vy": b["vy"][t],
        "my_ground": float(a["onGround"][t]), "op_ground": float(b["onGround"][t]),
        "my_sprint": float(a["sprinting"][t]), "op_sprint": float(b["sprinting"][t]),
        "my_sneak": float(a["sneaking"][t]), "op_sneak": float(b["sneaking"][t]),
        "my_health": a["health"][t], "op_health": b["health"][t],
        "health_diff": a["health"][t] - b["health"][t],
        "my_hurt": a["hurtTime"][t], "op_hurt": b["hurtTime"][t],
        "my_charge": da["charge"][t], "op_charge": db["charge"][t],
        "my_since_swing": da["since_swing"][t], "op_since_swing": db["since_swing"][t],
        "my_aim_off": abs(_wrap(a["yaw"][t] - bearing_ab)), "op_aim_off": abs(_wrap(b["yaw"][t] - bearing_ba)),
        "my_since_landed": da["since_landed"][t], "op_since_landed": db["since_landed"][t],
        "net_recent": dealt_recent - taken_recent,
        "my_hits_recent": float(da["landed"][h0 + 1:t + 1].sum()),
        "op_hits_recent": float(db["landed"][h0 + 1:t + 1].sum()),
    }


def window_samples(fights, horizon=40, stride=2):
    """Engaged moments every `stride` ticks; target = my net damage over the next `horizon` ticks."""
    rows = []
    for fight in fights:
        derived = per_frame(fight)
        n = len(fight.ticks)
        for t in range(HISTORY, n - horizon, stride):
            for me in (0, 1):
                feats = _moment(fight, derived, t, me)
                if feats["dist"] > ENGAGED_RANGE:
                    break
                op = 1 - me
                net = fight.damage[op, t + 1:t + 1 + horizon].sum() - fight.damage[me, t + 1:t + 1 + horizon].sum()
                rows.append({**feats, "fight": fight.id, "me": me, "recorder": int(me == 0), "net": net})
    return pd.DataFrame(rows)


def exchange_samples(fights, lead=3):
    """One moment `lead` ticks before each exchange's first hit; target = my net damage in that exchange."""
    rows = []
    for fight in fights:
        derived = per_frame(fight)
        index = {int(t): i for i, t in enumerate(fight.ticks)}
        for start, anchor, end in fight.exchanges:
            t = index.get(anchor - lead)
            e = index.get(min(end, int(fight.ticks[-1])))
            if t is None or e is None or t < HISTORY:
                continue
            for me in (0, 1):
                op = 1 - me
                net = fight.damage[op, t + 1:e + 1].sum() - fight.damage[me, t + 1:e + 1].sum()
                rows.append({**_moment(fight, derived, t, me), "fight": fight.id, "me": me,
                             "recorder": int(me == 0), "net": net})
    return pd.DataFrame(rows)


def feature_columns(df):
    # "recorder" stays out on purpose: the model must judge both sides the same way, not learn
    # that the person who recorded usually wins.
    return [c for c in df.columns if c not in ("fight", "me", "recorder", "net", "split")]
