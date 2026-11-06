"""End-to-end training run for one task, tracked in MLflow.

  1. dataset    build (or reuse) the versioned dataset and log it as the run's input
  2. candidates cross-validate every baseline and candidate on the TRAIN split (one nested run each)
  3. challenger refit the best candidate on all of TRAIN
  4. gate       score challenger, baselines and the current champion on the locked TEST split
  5. registry   register the challenger; it becomes "champion" only if it passed the gate

The FINAL split is never used here (see `final` in __main__).
"""

import os

# joblib counts physical cores with `wmic`, which Windows 11 no longer has, and prints a
# traceback. Naming a count below the logical total skips that lookup.
os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(max(1, (os.cpu_count() or 2) // 2)))
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

import warnings

# A local Parquet path matches two equivalent MLflow source types; it picks one, which is fine.
warnings.filterwarnings("ignore", message="The specified dataset source can be interpreted in multiple ways")
# The dataset record's id columns (me, recorder) are integers; model inputs are all floats.
warnings.filterwarnings("ignore", message="Hint: Inferred schema contains integer column")

import mlflow
import numpy as np
from mlflow.models import infer_signature
from mlflow.tracking import MlflowClient
from sklearn.inspection import permutation_importance

from . import config, dataset, evaluate, features, models

CHAMPION = "champion"


def setup():
    config.HOME.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(config.TRACKING_URI)
    if mlflow.get_experiment_by_name(config.EXPERIMENT) is None:
        mlflow.create_experiment(config.EXPERIMENT, artifact_location=config.ARTIFACTS_DIR.as_uri())
    mlflow.set_experiment(config.EXPERIMENT)


def champion(task):
    """(sklearn model, model version) of the current champion, or (None, None)."""
    client = MlflowClient()
    try:
        version = client.get_model_version_by_alias(config.registered_model(task), CHAMPION)
    except mlflow.exceptions.MlflowException:
        return None, None
    return mlflow.sklearn.load_model(f"models:/{config.registered_model(task)}@{CHAMPION}"), version


def run(task, data, promote=True, log=print):
    train_df, y_train = evaluate.classification_rows(data.part(task, "train"))
    test_df, y_test = evaluate.classification_rows(data.part(task, "test"))
    cols = features.feature_columns(train_df)
    # All features as floats, so the model's input schema never rejects a value at serving time.
    X_train, X_test = train_df[cols].astype("float64"), test_df[cols].astype("float64")
    groups_train, groups_test = train_df["fight"].to_numpy(), test_df["fight"].to_numpy()

    with mlflow.start_run(run_name=f"{task}-pipeline") as parent:
        mlflow.set_tags({"task": task, "stage": "pipeline", "dataset_version": data.version})
        mlflow.log_params({
            "task": task, "dataset_version": data.version, "feature_version": config.FEATURE_VERSION,
            "features": len(cols), "train_fights": len(set(groups_train)), "test_fights": len(set(groups_test)),
            "train_samples": len(y_train), "test_samples": len(y_test), "ahead_min_hp": evaluate.AHEAD_MIN,
        })
        table = data.tables[task]
        mlflow.log_input(mlflow.data.from_pandas(table, source=str(data.path / f"{task}.parquet"),
                                                 name=f"{task}-{data.version}"), context="training")

        # 2. candidates (cross-validated on train only)
        cv = {}
        for name in list(models.BASELINES) + list(models.CANDIDATES):
            with mlflow.start_run(run_name=f"{task}-{name}", nested=True):
                mlflow.set_tags({"task": task, "stage": "candidate", "model": name})
                mlflow.log_params(models.PARAMS[name])
                p = evaluate.cross_validate(models.make(name), X_train, y_train, groups_train)
                cv[name] = evaluate.scores(y_train, p)
                mlflow.log_metrics({f"cv_{k}": v for k, v in cv[name].items()})
            log(f"  cv  {name:14s} brier {cv[name]['brier']:.4f}  auc {cv[name]['auc']:.3f}  acc {cv[name]['accuracy']:.3f}")

        # 3. challenger
        best = min(models.CANDIDATES, key=lambda n: cv[n]["brier"])
        challenger = models.make(best).fit(X_train, y_train)
        p_new = challenger.predict_proba(X_test)[:, 1]

        # 4. gate on the locked test split
        refs = {name: models.make(name).fit(X_train, y_train).predict_proba(X_test)[:, 1] for name in models.BASELINES}
        champ_model, champ_version = champion(task)
        if champ_model is not None and champ_version.tags.get("feature_version") == config.FEATURE_VERSION:
            refs["champion"] = champ_model.predict_proba(X_test)[:, 1]
        test = {name: evaluate.scores(y_test, p) for name, p in refs.items()}
        test["challenger"] = evaluate.scores(y_test, p_new)
        for name, s in test.items():
            mlflow.log_metrics({f"test_{name}_{k}": v for k, v in s.items()})

        verdicts = {}
        for name, p_ref in refs.items():
            lo, hi = evaluate.brier_difference_ci(y_test, p_new, p_ref, groups_test)
            verdicts[name] = {"better": test["challenger"]["brier"] < test[name]["brier"],
                              "ci_low": lo, "ci_high": hi, "significant": hi < 0}
            mlflow.log_metrics({f"gate_vs_{name}_ci_low": lo, f"gate_vs_{name}_ci_high": hi})
        passed = all(v["better"] for v in verdicts.values())
        mlflow.set_tags({"challenger": best, "gate": "pass" if passed else "fail"})
        mlflow.log_dict({"cv": cv, "test": test, "verdicts": verdicts}, "gate.json")
        mlflow.log_figure(evaluate.calibration_figure(y_test, p_new, f"{task} ({best}), test split"),
                          "calibration_test.png")

        imp = permutation_importance(challenger, X_test, y_test, scoring="neg_brier_score", n_repeats=5, random_state=0)
        order = np.argsort(-imp.importances_mean)
        mlflow.log_dict({cols[i]: float(imp.importances_mean[i]) for i in order}, "feature_importance.json")

        # 5. registry
        info = mlflow.sklearn.log_model(
            challenger, name="model", pyfunc_predict_fn="predict_proba",
            # MLflow saves with skops, which only loads types it can check. Boosted trees store a
            # node table it can't; these models are trained here, so that one type is trusted.
            skops_trusted_types=models.SKOPS_TRUSTED.get(best),
            signature=infer_signature(X_train, challenger.predict_proba(X_train.head(50))),
            input_example=X_train.head(3),
        )
        version = mlflow.register_model(info.model_uri, config.registered_model(task))
        client = MlflowClient()
        for key, value in {"feature_version": config.FEATURE_VERSION, "dataset_version": data.version,
                           "gate": "pass" if passed else "fail", "model": best,
                           "test_brier": f"{test['challenger']['brier']:.4f}", "run_id": parent.info.run_id}.items():
            client.set_model_version_tag(version.name, version.version, key, value)
        promoted = passed and promote
        if promoted:
            client.set_registered_model_alias(version.name, CHAMPION, version.version)

        log(f"  test challenger ({best}) brier {test['challenger']['brier']:.4f}  auc {test['challenger']['auc']:.3f}"
            f"  acc {test['challenger']['accuracy']:.3f}  ece {test['challenger']['ece']:.3f}")
        for name, v in verdicts.items():
            log(f"  gate vs {name:14s} brier {test[name]['brier']:.4f}  "
                f"{'better' if v['better'] else 'NOT better'}  diff 95% CI [{v['ci_low']:+.4f}, {v['ci_high']:+.4f}]"
                f"{'  (significant)' if v['significant'] else ''}")
        log(f"  -> {version.name} v{version.version}: gate {'PASS' if passed else 'FAIL'}"
            f"{', now champion' if promoted else ''}")
        return {"run_id": parent.info.run_id, "passed": passed, "version": version.version, "model": best}


def final(task, data, log=print):
    """Score the champion on the FINAL split. Use rarely: every look makes it less honest."""
    model, version = champion(task)
    if model is None:
        raise SystemExit(f"No champion for {task} yet; run the pipeline first.")
    df, y = evaluate.classification_rows(data.part(task, "final"))
    p = model.predict_proba(df[features.feature_columns(df)].astype("float64"))[:, 1]
    s = evaluate.scores(y, p)
    with mlflow.start_run(run_name=f"{task}-final-check"):
        mlflow.set_tags({"task": task, "stage": "final", "model_version": version.version})
        mlflow.log_metrics({f"final_{k}": v for k, v in s.items()})
    log(f"  final split ({len(set(df['fight']))} fights, {len(y)} samples): "
        + "  ".join(f"{k} {v:.3f}" for k, v in s.items()))
    return s
