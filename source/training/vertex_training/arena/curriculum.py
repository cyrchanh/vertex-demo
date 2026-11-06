"""Autocurriculum: the training decides what to practise next from how the learner is doing.

A fight setup is a bucket: opponent style (chase or crit) × start distance (2–4, 4–6, 6–8, 8–10
blocks). Each bucket has its own opponent skill dial (scripted.ChaseFighter `skill`: 0 stands
still, 1 is the full bot). Two layers adapt it:

  difficulty that follows the score   after every fight, skill += STEP × (score − TARGET), so the
                                      learner keeps facing opponents it beats ~60% of the time
  learning progress (the teacher)     buckets are sampled in proportion to how fast their score
                                      is changing (|fast average − slow average|), plus a floor
                                      and some uniform sampling, so mastered and hopeless buckets
                                      get less time but none is forgotten

When every bucket's skill reaches HANDOVER, the league takes over: self-play and snapshots get
most fights, and curriculum fights (by then at full skill) keep the scripted styles in the mix.
"""

import random

from . import scripted

STYLES = ("chase", "crit", "punish", "eager")   # punish, eager: added after the duel analyses
DISTANCES = ((2.0, 4.0), (4.0, 6.0), (6.0, 8.0), (8.0, 10.0))
TARGET = 0.6
STEP = 0.05
FAST, SLOW = 0.2, 0.02
FLOOR = 0.02
UNIFORM = 0.2
HANDOVER = 0.95
FOCUS_WEIGHT = 4.0


def _key(style, dist):
    return f"{style}@{dist[0]:g}-{dist[1]:g}"


class Curriculum:
    def __init__(self, state=None, seed=0, start_skill=0.0):
        """start_skill: for a new run whose learner starts strong (e.g. copied from another policy),
        begin every bucket there; at HANDOVER or above the curriculum starts complete."""
        self.rng = random.Random(seed)
        if state:
            self.buckets = state["buckets"]
            self.handed_over = state["handed_over"]
            # A style added since this run started joins at the learner's current level (its skill
            # dial then follows the learner's score as usual).
            start = self.mean_skill()
            for s_ in STYLES:
                for d in DISTANCES:
                    self.buckets.setdefault(_key(s_, d), {"style": s_, "dist": list(d), "skill": start, "fast": 0.5,
                                                          "slow": 0.5, "fights": 0})
        else:
            self.buckets = {_key(s, d): {"style": s, "dist": list(d), "skill": start_skill, "fast": 0.5, "slow": 0.5,
                                         "fights": 0} for s in STYLES for d in DISTANCES}
            self.handed_over = start_skill >= HANDOVER

    def state(self):
        return {"buckets": self.buckets, "handed_over": self.handed_over}

    def sample(self, focus=None):
        """focus: a style, or several separated by commas, whose buckets are picked FOCUS_WEIGHT
        times as often (e.g. to practise against opponents that expose a known weakness)."""
        keys = list(self.buckets)
        focused = set(focus.split(",")) if focus else set()
        boost = [FOCUS_WEIGHT if b["style"] in focused else 1.0 for b in self.buckets.values()]
        if self.rng.random() < UNIFORM:
            return self.rng.choices(keys, boost)[0]
        weights = [(abs(b["fast"] - b["slow"]) + FLOOR) * w for b, w in zip(self.buckets.values(), boost)]
        return self.rng.choices(keys, weights)[0]

    def fighter(self, key, seed):
        b = self.buckets[key]
        return scripted.ChaseFighter(seed=seed, crits=b["style"] == "crit", punish=b["style"] == "punish",
                                     eager=b["style"] == "eager", repeat=2, delay=2, skill=b["skill"])

    def start_distance(self, key):
        return tuple(self.buckets[key]["dist"])

    def record(self, key, score):
        b = self.buckets[key]
        b["skill"] = max(0.0, min(1.0, b["skill"] + STEP * (score - TARGET)))
        b["fast"] += FAST * (score - b["fast"])
        b["slow"] += SLOW * (score - b["slow"])
        b["fights"] += 1
        if not self.handed_over and all(x["skill"] >= HANDOVER for x in self.buckets.values()):
            self.handed_over = True
            return True
        return False

    def mean_skill(self):
        return sum(b["skill"] for b in self.buckets.values()) / len(self.buckets)
