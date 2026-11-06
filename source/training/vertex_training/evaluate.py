"""Vertex Bench layer A: prediction scores, calibration and the gate's confidence interval."""

import numpy as np
from sklearn.base import clone
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import GroupKFold

AHEAD_MIN = 1.0  # a sample counts when someone is ahead by at least half a heart
FOLDS = 5


def classification_rows(df):
    """Rows where someone came out ahead, and the label "I came out ahead"."""
    rows = df[df["net"].abs() >= AHEAD_MIN].reset_index(drop=True)
    return rows, (rows["net"] > 0).astype(int).to_numpy()


def scores(y, p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return {
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "auc": float(roc_auc_score(y, p)) if len(set(y)) > 1 else float("nan"),
        "accuracy": float(((p > 0.5) == y).mean()),
        "ece": expected_calibration_error(y, p),
    }


def expected_calibration_error(y, p, bins=10):
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    total = 0.0
    for b in range(bins):
        mask = idx == b
        if mask.any():
            total += mask.mean() * abs(y[mask].mean() - p[mask].mean())
    return float(total)


def cross_validate(model, X, y, groups):
    """Out-of-fold predictions with whole fights held out."""
    p = np.zeros(len(y))
    for train, test in GroupKFold(n_splits=min(FOLDS, len(set(groups)))).split(X, y, groups):
        p[test] = clone(model).fit(X.iloc[train], y[train]).predict_proba(X.iloc[test])[:, 1]
    return p


def brier_difference_ci(y, p_new, p_ref, groups, n=2000, seed=0):
    """95% interval for Brier(new) - Brier(ref), resampling whole fights (paired bootstrap)."""
    rng = np.random.default_rng(seed)
    fights = np.unique(groups)
    per_fight = {f: np.flatnonzero(groups == f) for f in fights}
    diff = (p_new - y) ** 2 - (p_ref - y) ** 2
    stats = []
    for _ in range(n):
        idx = np.concatenate([per_fight[f] for f in rng.choice(fights, size=len(fights))])
        stats.append(diff[idx].mean())
    return float(np.percentile(stats, 2.5)), float(np.percentile(stats, 97.5))


def calibration_figure(y, p, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    edges = np.linspace(0, 1, 11)
    xs, ys = [], []
    for lo, hi in zip(edges, edges[1:]):
        mask = (p >= lo) & (p <= hi if hi == 1 else p < hi)
        if mask.sum() >= 10:
            xs.append(p[mask].mean())
            ys.append(y[mask].mean())
    fig, ax = plt.subplots(figsize=(4, 4))
    ax.plot([0, 1], [0, 1], "--", color="grey", label="perfect")
    ax.plot(xs, ys, "o-", label="model")
    ax.set_xlabel("predicted P(I come out ahead)")
    ax.set_ylabel("actual fraction ahead")
    ax.set_title(title)
    ax.legend()
    fig.tight_layout()
    return fig
