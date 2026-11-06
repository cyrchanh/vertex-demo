"""Builds a versioned Parquet dataset from recordings, with locked train / test / final splits.

The split of a fight is decided by a hash of its id, so it never changes when recordings are added:
a fight that was in the test set stays there. 70% train, 20% test (the gate), 10% final (rarely
looked at). The version is a hash of the fight ids and the feature version, so the same recordings
always give the same dataset, and it's built only once.
"""

import datetime
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import config, features, recordings


def split_of(fight_id):
    bucket = int(hashlib.sha256(fight_id.encode()).hexdigest(), 16) % 10
    return "test" if bucket < 2 else "final" if bucket == 2 else "train"


@dataclass
class Dataset:
    version: str
    path: Path
    manifest: dict
    tables: dict  # task -> DataFrame with a "split" column

    def part(self, task, split):
        df = self.tables[task]
        return df[df["split"] == split].reset_index(drop=True)


def build(recordings_dir=config.RECORDINGS_DIR, rebuild=False):
    fights = recordings.load_all(recordings_dir)
    if not fights:
        raise SystemExit(f"No two-player recordings found in {recordings_dir}")
    ids = sorted(f.id for f in fights)
    version = hashlib.sha256((config.FEATURE_VERSION + "|" + ",".join(ids)).encode()).hexdigest()[:12]
    path = config.DATASETS_DIR / version
    if path.exists() and not rebuild:
        return load(path)

    path.mkdir(parents=True, exist_ok=True)
    splits = {fid: split_of(fid) for fid in ids}
    tables = {"window": features.window_samples(fights), "exchange": features.exchange_samples(fights)}
    for task, df in tables.items():
        df["split"] = df["fight"].map(splits)
        df.to_parquet(path / f"{task}.parquet", index=False)
    manifest = {
        "version": version,
        "feature_version": config.FEATURE_VERSION,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "recordings_dir": str(recordings_dir),
        # Fight ids only: player names never leave the recordings.
        "fights": {fid: splits[fid] for fid in ids},
        "counts": {task: df.groupby("split").size().to_dict() for task, df in tables.items()},
        # Game version and server per fight (format 3+ recordings), for per-version evaluation.
        "contexts": {f.id: {k: f.context.get(k) for k in ("gameVersion", "server", "serverBrand", "singleplayer")}
                     for f in fights if f.context},
    }
    (path / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return Dataset(version, path, manifest, tables)


def load(path):
    path = Path(path)
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    tables = {task: pd.read_parquet(path / f"{task}.parquet") for task in config.TASKS}
    return Dataset(manifest["version"], path, manifest, tables)
