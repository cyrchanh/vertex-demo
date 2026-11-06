"""Paths and names shared by every stage. Override any of them with environment variables."""

import os
from pathlib import Path

from . import recordings

# Bump when features.py changes what a sample contains: models trained on another version can't be
# scored on the new data, so the gate then only compares against the baselines.
FEATURE_VERSION = "v1"

RECORDINGS_DIR = Path(os.environ.get("VERTEX_RECORDINGS", recordings.DEFAULT_DIR))

# Outside OneDrive: synced folders lock MLflow's database and the venv (same reason as the Gradle
# build dir).
HOME = Path(os.environ.get("VERTEX_TRAINING_HOME",
                           Path(os.environ.get("LOCALAPPDATA", Path.home())) / "vertex-training"))
DATASETS_DIR = HOME / "datasets"
ARTIFACTS_DIR = HOME / "mlartifacts"
TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "sqlite:///" + (HOME / "mlflow.db").as_posix())

EXPERIMENT = "vertex-game-sense"
TASKS = ("window", "exchange")


def registered_model(task):
    """Registry name of the model that predicts who comes out ahead, per task."""
    return f"vertex-{task}-ahead"
