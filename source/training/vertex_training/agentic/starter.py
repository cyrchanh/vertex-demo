import math


class Fighter:
    """Starter bot every strategist begins from: face the opponent, sprint in, click on full charge
    with the crosshair on them, W-tap after landing a hit."""

    def __init__(self, seed):
        self.wtap = 0

    def act(self, view):
        me, rel = view["me"], view["rel"]
        if any(e["type"] == "hit" and e["by_me"] for e in view["events"]):
            self.wtap = 2
        dist = rel["dist"]
        action = {
            "dyaw": rel["yaw_off"] / 2,
            "dpitch": (rel["pitch_off"] + 8.0) / 2,
            "forward": self.wtap == 0 and dist > 1.2,
            "sprint": dist > 1.2,
            "attack": me["aim_on_target"] and me["charge"] > 0.9 and me["miss_cooldown"] == 0,
        }
        self.wtap = max(0, self.wtap - 1)
        return action
