"""The champion's win chance over one fight: a text preview of the replay viewer's timeline strip."""

import mlflow
import pandas as pd

from . import config, features, recordings

BAR = 40


def show(path, task="window", every=20):
    model = mlflow.pyfunc.load_model(f"models:/{config.registered_model(task)}@champion")
    fight = recordings.load(path)
    if fight is None:
        raise SystemExit(f"{path} is not a two-player recording")
    derived = features.per_frame(fight)
    print(f"{fight.names[0]} (left) vs {fight.names[1]}: chance {fight.names[0]} comes out ahead over the next 2 s")
    n = len(fight.ticks)
    for t in range(features.HISTORY, n, every):
        row = features._moment(fight, derived, t, 0)
        hp = f"{fight.f[0]['health'][t]:4.1f} / {fight.f[1]['health'][t]:4.1f} HP"
        if row["dist"] > features.ENGAGED_RANGE:
            print(f"  {t / 20:6.1f}s  {hp}  (apart)")
            continue
        p = float(model.predict(pd.DataFrame([row]).astype("float64"))[0][1])
        bar = "#" * round(p * BAR)
        print(f"  {t / 20:6.1f}s  {hp}  {p:5.0%} |{bar:<{BAR}}|")
