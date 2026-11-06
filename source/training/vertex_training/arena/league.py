"""The league: who the learner fights, and ratings for everyone it has fought.

Fighting only its latest self, a learner can go in circles (it learns to beat today's trick and
forgets yesterday's), and two beginners who can't hit teach each other nothing (the first run
learned to avoid fighting). So each fight's opponent is drawn from:

                                                          before handover   after
  curriculum  scripted bot from the autocurriculum              85%          20%
              (curriculum.py: skill and distance adapt)
  self        its current self; both sides learn                15%          50%
  snapshot    a frozen past copy, chosen more often if it        —           30%  (prioritised
              still wins against the learner                                       fictitious self-play)

Handover happens when every curriculum bucket reaches full skill. After it, if a "you-clone"
(a behaviour-cloned copy of the owner, duel.py) exists, it takes CLONE_SHARE of fights first. Elo ratings use the chase bot
as a fixed 1000 anchor, so ratings are comparable across runs; curriculum fights count for Elo
only at full skill. Stored in league/league.json next to the snapshot files.
"""

import json
import random
from pathlib import Path

from . import scripted
from .curriculum import Curriculum

# Scripted bots play with the same 2 ticks per decision and 200 ms delay as the learner.
SCRIPTED = {
    "chase": (lambda seed: scripted.ChaseFighter(seed=seed, repeat=2, delay=2), 0.45),
    "crit": (lambda seed: scripted.ChaseFighter(seed=seed, crits=True, repeat=2, delay=2), 0.45),
    "random": (lambda seed: scripted.RandomFighter(seed=seed), 0.10),
    "punish": (lambda seed: scripted.ChaseFighter(seed=seed, punish=True, repeat=2, delay=2), 0.0),
    "eager": (lambda seed: scripted.ChaseFighter(seed=seed, eager=True, repeat=2, delay=2), 0.0),
}
ANCHOR = "chase"
SHARES = {"self": 0.5, "snapshot": 0.3}           # the rest: curriculum (20%, or curriculum_share)
BEFORE_HANDOVER_SELF = 0.15
CLONE = "you-clone"
CLONE_SHARE = 0.25
ELO_K = 16.0


class League:
    def __init__(self, directory, seed=0, curriculum_share=None, focus=None, start_skill=0.0, seeded_share=0.0):
        """curriculum_share: after the handover, the share of fights from the curriculum (default
        1 − self − snapshot = 20%); self-play and snapshots split the rest 5:3. focus: a curriculum
        style to practise more (curriculum.FOCUS_WEIGHT). seeded_share: the share of fights against
        the opponents added with add_opponent (earlier champions), so the learner's own snapshots
        can't crowd them out."""
        self.curriculum_share = curriculum_share
        self.seeded_share = seeded_share
        self.clone_share = CLONE_SHARE
        self.focus = focus
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "league.json"
        self.rng = random.Random(seed)
        if self.file.exists():
            data = json.loads(self.file.read_text(encoding="utf-8"))
            self.learner = data["learner"]
            self.opponents = data["opponents"]
            for name in SCRIPTED:              # a scripted bot added since this run started
                self.opponents.setdefault(name, {"kind": "scripted", "elo": 1000.0, "games": 0, "learner_score": 0.5})
            self.curriculum = Curriculum(data.get("curriculum"), seed=seed)
        else:
            self.learner = {"elo": 1000.0, "games": 0}
            self.opponents = {name: {"kind": "scripted", "elo": 1000.0, "games": 0, "learner_score": 0.5}
                              for name in SCRIPTED}
            self.curriculum = Curriculum(seed=seed, start_skill=start_skill)

    # ---- choosing opponents ----

    def snapshots(self):
        return [n for n, o in self.opponents.items() if o["kind"] == "snapshot"]

    def sample(self):
        """→ ("self", None) | ("snapshot", name) | ("curriculum", bucket)."""
        r = self.rng.random()
        if not self.curriculum.handed_over:
            return ("self", None) if r < BEFORE_HANDOVER_SELF else ("curriculum", self.curriculum.sample(self.focus))
        if CLONE in self.opponents and self.rng.random() < self.clone_share:
            return "snapshot", CLONE
        seeded = [n for n, o in self.opponents.items() if o.get("seeded")]
        if seeded and self.rng.random() < self.seeded_share:
            return "snapshot", self.rng.choice(seeded)
        if self.curriculum_share is not None:
            if r < self.curriculum_share:
                return "curriculum", self.curriculum.sample(self.focus)
            r = (r - self.curriculum_share) / (1.0 - self.curriculum_share) * (SHARES["self"] + SHARES["snapshot"])
        snaps = self.snapshots()
        if r < SHARES["self"]:
            return "self", None
        if snaps and r < SHARES["self"] + SHARES["snapshot"]:
            # Prioritised fictitious self-play: the more a snapshot still beats the learner, the more
            # often it's picked, with a floor so none is forgotten.
            weights = [(1.0 - self.opponents[n]["learner_score"]) ** 2 + 0.05 for n in snaps]
            return "snapshot", self.rng.choices(snaps, weights)[0]
        return "curriculum", self.curriculum.sample(self.focus)

    def curriculum_fighter(self, bucket):
        return self.curriculum.fighter(bucket, self.rng.randrange(1 << 30))

    def record_curriculum(self, bucket, score):
        """→ True if this result completed the curriculum (handover to the league)."""
        b = self.curriculum.buckets[bucket]
        if b["skill"] >= 0.99:
            self.record(b["style"], score)
        return self.curriculum.record(bucket, score)

    def snapshot_path(self, name):
        return self.dir / self.opponents[name]["file"]   # an absolute path (the clone) stays as it is

    def add_opponent(self, name, path):
        """An existing policy (e.g. an earlier champion) joins as a snapshot opponent, kept by path."""
        if name not in self.opponents:
            self.opponents[name] = {"kind": "snapshot", "file": str(Path(path).resolve()), "elo": self.learner["elo"],
                                    "games": 0, "learner_score": 0.5}
        self.opponents[name]["seeded"] = True

    def use_clone(self, path):
        """The owner's clone joins as an opponent (kept rating if it played before)."""
        old = self.opponents.get(CLONE, {})
        self.opponents[CLONE] = {"kind": "clone", "file": str(Path(path).resolve()),
                                 "elo": old.get("elo", self.learner["elo"]), "games": old.get("games", 0),
                                 "learner_score": old.get("learner_score", 0.5)}

    # ---- results ----

    def record(self, opponent, score):
        """The learner scored `score` (1 win, 0.5 draw, 0 loss) against a snapshot or scripted bot."""
        o = self.opponents[opponent]
        expected = 1.0 / (1.0 + 10 ** ((o["elo"] - self.learner["elo"]) / 400.0))
        self.learner["elo"] += ELO_K * (score - expected)
        if opponent != ANCHOR:
            o["elo"] -= ELO_K * (score - expected)
        self.learner["games"] += 1
        o["games"] += 1
        o["learner_score"] += 0.05 * (score - o["learner_score"])   # recent results count most

    def add_snapshot(self, name, save_fn):
        """Freeze the learner as a new opponent, starting at the learner's current rating."""
        file = f"{name}.pt"
        save_fn(self.dir / file)
        self.opponents[name] = {"kind": "snapshot", "file": file, "elo": self.learner["elo"], "games": 0,
                                "learner_score": 0.5}
        self.save()

    def save(self):
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"learner": self.learner, "opponents": self.opponents,
                                   "curriculum": self.curriculum.state()}, indent=1), encoding="utf-8")
        tmp.replace(self.file)

    def table(self):
        rows = [("learner", self.learner["elo"], self.learner["games"], None)]
        rows += [(n, o["elo"], o["games"], o["learner_score"]) for n, o in self.opponents.items()]
        return sorted(rows, key=lambda r: -r[1])
