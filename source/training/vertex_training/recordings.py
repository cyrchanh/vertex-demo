"""Loads Vertex recordings (the mod's .json.gz replay files) into per-fighter numpy arrays."""

import glob
import gzip
import json
import os
from dataclasses import dataclass, field

import numpy as np

DEFAULT_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), ".minecraft", "vertex-replays")

# Per-tick fields taken from ParticipantFrame. Everything here is observable for both fighters;
# keys (input) and the exact attack charge exist only for the recorder, so they are left out.
FIELDS = ["x", "y", "z", "yaw", "vx", "vy", "vz", "health", "hurtTime"]
FLAGS = ["onGround", "sprinting", "sneaking", "swingStart"]


@dataclass
class Fight:
    id: str
    names: list
    ticks: np.ndarray                      # tick number per frame
    f: list                                # per fighter: dict of field -> array over frames
    damage: np.ndarray                     # [2, frames] health lost on that frame (0 if none)
    hits: list                             # (tick, attacker, victim) for landed hits
    exchanges: list = field(default_factory=list)  # (startTick, anchorTick, endTick) from the mod
    context: dict = None                   # game version, server, ... (format 3+), else None


def load(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        doc = json.load(fh)
    trace = doc["trace"]
    if len(trace["participants"]) != 2 or len(trace["frames"]) < 60:
        return None
    frames = trace["frames"]
    fighters = []
    for i in range(2):
        rows = [fr["participants"][i] for fr in frames]
        d = {k: np.array([r[k] for r in rows], dtype=float) for k in FIELDS}
        d.update({k: np.array([bool(r[k]) for r in rows]) for k in FLAGS})
        fighters.append(d)
    health = np.stack([fighters[0]["health"], fighters[1]["health"]])
    damage = np.zeros_like(health)
    damage[:, 1:] = np.clip(health[:, :-1] - health[:, 1:], 0, None)
    hits = [(e["tick"], e["attacker"], e["victim"]) for e in trace["events"] if e["type"] == "HIT"]
    exchanges = [(x["startTick"], x["anchorTick"], x["endTick"]) for x in doc.get("interactions", [])]
    return Fight(trace["id"], [p["name"] for p in trace["participants"]],
                 np.array([fr["tick"] for fr in frames]), fighters, damage, hits, exchanges,
                 trace.get("context"))


def load_all(directory=DEFAULT_DIR):
    fights = []
    for path in sorted(glob.glob(os.path.join(directory, "*.json.gz"))):
        fight = load(path)
        if fight is not None:
            fights.append(fight)
    return fights
