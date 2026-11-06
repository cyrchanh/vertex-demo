"""What a strategist's bot sees: what a player's client could know, as the RL policy's layout 2.

  me        own state, live (you feel your own jump, sprint and fall); crosshair one decision late
  op        the opponent, `reaction_steps` decisions late (200 ms), without what no client sees:
            their keys, attack charge, whiff lockout, food, fall distance, crosshair
  events    hits and swings, 200 ms late; the opponent's swing charge is hidden (you see the arm
            move, not how charged it was)
  rel       geometry of op as seen from me: dist (horizontal), dy, yaw_off / pitch_off (degrees to
            turn to face their eyes)
"""

from ..arena.env import relative_view

HIDDEN_OP = ("keys", "charge", "miss_cooldown", "food", "fall_distance", "aim_on_target",
             "reach_distance", "invulnerable_time", "human", "horizontal_collision")


def client_view(env, i):
    live, late = env.replies[-1], env.replies[0]
    aim = env.replies[-2] if len(env.replies) > 1 else live
    me = {k: v for k, v in live["bots"][i].items() if k not in ("human", "keys")}
    me["aim_on_target"] = aim["bots"][i].get("aim_on_target", False)
    me["reach_distance"] = aim["bots"][i].get("reach_distance", 99.0)
    op = {k: v for k, v in late["bots"][1 - i].items() if k not in HIDDEN_OP}
    events = []
    for e in late["events"]:
        if e["type"] == "hit":
            events.append({"type": "hit", "by_me": e["attacker"] == i, "damage": e["damage"],
                           "crit": e["crit"], "sprint_hit": e["sprint_hit"], "tick": e["tick"]})
        elif e["type"] == "swing":
            mine = e["bot"] == i
            ev = {"type": "swing", "by_me": mine, "hit": e["hit"], "tick": e["tick"]}
            if mine:
                ev["charge"] = e["charge"]
            events.append(ev)
    return {"tick": live["tick"], "seen_tick": late["tick"], "fight_ticks": env.fight_ticks,
            "me": me, "op": op, "events": events, "rel": relative_view(live["bots"][i], late["bots"][1 - i])}
