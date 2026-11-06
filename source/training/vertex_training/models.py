"""Candidate models and baselines for "do I come out ahead?". All share the scikit-learn API."""

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


class BaseRate(ClassifierMixin, BaseEstimator):
    """Always predicts the training share of "ahead"."""

    def fit(self, X, y):
        self.classes_ = np.array([0, 1])
        self.rate_ = float(np.mean(y))
        return self

    def predict_proba(self, X):
        p = np.full(len(X), self.rate_)
        return np.column_stack([1 - p, p])

    def predict(self, X):
        return (self.predict_proba(X)[:, 1] > 0.5).astype(int)


class MomentumRule(BaseRate):
    """Hand-written rule: whoever was ahead over the last 2 s stays ahead."""

    def predict_proba(self, X):
        r = np.asarray(X["net_recent"])
        p = np.where(r > 0, 0.6, np.where(r < 0, 0.4, 0.5))
        return np.column_stack([1 - p, p])


BASELINES = {
    "base-rate": BaseRate,
    "momentum-rule": MomentumRule,
}

CANDIDATES = {
    "logistic": lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=0.5)),
    "boosted-trees": lambda: HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15,
                                                           min_samples_leaf=40, random_state=0),
}

# Types skops must be told to trust when MLflow saves the model (see pipeline.py).
SKOPS_TRUSTED = {
    "boosted-trees": ["sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor"],
}

# Hyperparameters logged to MLflow for each candidate.
PARAMS = {
    "logistic": {"C": 0.5, "max_iter": 2000, "scaled": True},
    "boosted-trees": {"max_iter": 200, "learning_rate": 0.05, "max_leaf_nodes": 15, "min_samples_leaf": 40},
    "base-rate": {},
    "momentum-rule": {"ahead_p": 0.6},
}


def make(name):
    return (BASELINES.get(name) or CANDIDATES[name])()
