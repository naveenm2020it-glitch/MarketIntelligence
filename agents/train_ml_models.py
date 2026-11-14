#!/usr/bin/env python3
"""Train and evaluate NSE direction models without generating a market forecast."""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path
import joblib
import numpy as np
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, precision_recall_fscore_support
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

LABELS = ("bearish", "neutral", "bullish")
META = {"signal_date", "target_date", "target_expiry", "target_return_pct", "target_class"}

def load_split(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"Empty dataset split: {path}")
    columns = [key for key in rows[0] if key not in META]
    if not columns or any([key for key in row if key not in META] != columns for row in rows):
        raise ValueError(f"Invalid/inconsistent feature columns in {path}")
    dates = [row["signal_date"] for row in rows]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError(f"Signal dates in {path} must be unique and chronological")
    x = np.asarray([[float(row[c]) if row.get(c, "").strip() else float("nan") for c in columns] for row in rows], dtype=float)
    y = np.asarray([row["target_class"] for row in rows])
    if not set(y).issubset(set(LABELS)):
        raise ValueError(f"Unknown target class in {path}")
    return x, y, dates, columns

def make_pipeline(name):
    if name == "logistic_regression":
        estimator = LogisticRegression(C=0.1, class_weight="balanced", max_iter=5000, solver="lbfgs", random_state=42)
        return Pipeline([("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                         ("scaler", StandardScaler()), ("model", estimator)])
    if name == "random_forest":
        estimator = RandomForestClassifier(n_estimators=400, max_depth=4, min_samples_leaf=8,
            max_features="sqrt", class_weight="balanced_subsample", random_state=42, n_jobs=1)
    elif name == "extra_trees":
        estimator = ExtraTreesClassifier(n_estimators=400, max_depth=5, min_samples_leaf=8,
            max_features="sqrt", class_weight="balanced", random_state=42, n_jobs=1)
    else:
        raise ValueError(f"Unknown model: {name}")
    return Pipeline([("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                     ("model", estimator)])

def metrics(y_true, y_pred):
    p, r, f, support = precision_recall_fscore_support(y_true, y_pred, labels=list(LABELS), zero_division=0)
    return {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "macro_f1": round(float(f1_score(y_true, y_pred, labels=list(LABELS), average="macro", zero_division=0)), 4),
        "class_metrics": {label: {"precision": round(float(a), 4), "recall": round(float(b), 4),
            "f1": round(float(c), 4), "support": int(n)} for label, a, b, c, n in zip(LABELS, p, r, f, support)}
    }

def agent6_labels(x, columns):
    """Recover the same-day rule-based Agent 6 label from its one-hot dataset fields."""
    mapping = {
        "agent6_direction_bearish": "bearish",
        "agent6_direction_neutral_or_mixed": "neutral",
        "agent6_direction_bullish": "bullish",
        "agent6_direction_insufficient_data": "neutral",
    }
    indices = {key: columns.index(key) for key in mapping if key in columns}
    if not indices:
        raise ValueError("Dataset lacks Agent 6 direction features for hybrid evaluation")
    labels = []
    for row in x:
        active = [label for key, label in mapping.items()
                  if key in indices and row[indices[key]] >= 0.5]
        labels.append(active[0] if len(active) == 1 else "neutral")
    return np.asarray(labels)


def consensus_labels(ml_labels, agent6):
    # Fixed, conservative rule decided before viewing holdout results:
    # publish a direction only when the independent ML and rule-based Agent 6 agree.
    return np.asarray([
        ml if ml == rule and ml in ("bullish", "bearish") else "neutral"
        for ml, rule in zip(ml_labels, agent6)
    ])


def ensemble_diagnostics(y_true, ml_labels, agent6):
    consensus = consensus_labels(ml_labels, agent6)
    agreement = np.asarray(ml_labels) == np.asarray(agent6)
    return {
        "ml_only": metrics(y_true, ml_labels),
        "agent6_rule_only": metrics(y_true, agent6),
        "conservative_ml_agent6_consensus": metrics(y_true, consensus),
        "consensus_agreement_coverage": round(float(np.mean(agreement)), 4),
        "directional_output_coverage": round(float(np.mean(np.isin(consensus, ["bullish", "bearish"]))), 4),
        "rule": "Emit bullish/bearish only when ML and Agent 6 agree on that direction; otherwise neutral/abstain.",
    }


def run(dataset_dir, output_dir, model_path):
    dataset_dir, output_dir, model_path = Path(dataset_dir), Path(output_dir), Path(model_path)
    xtr, ytr, dtr, cols = load_split(dataset_dir / "train.csv")
    xv, yv, dv, cv = load_split(dataset_dir / "validation.csv")
    xt, yt, dt, ct = load_split(dataset_dir / "test.csv")
    if cols != cv or cols != ct or dtr[-1] >= dv[0] or dv[-1] >= dt[0]:
        raise ValueError("Feature schema or chronological split boundaries are invalid")
    if min(len(ytr), len(yv), len(yt)) < 20 or len(set(ytr)) < 2:
        raise ValueError("Insufficient rows/classes for time-series model evaluation")

    # Preserve the full feature matrix for the independent Agent 6 comparison.
    full_cols = list(cols)
    xv_full, xt_full = xv.copy(), xt.copy()
    missing = {col: int(np.isnan(xtr[:, i]).sum()) for i, col in enumerate(full_cols)}
    all_missing = [col for col, count in missing.items() if count == len(ytr)]
    constant = []
    keep_indices = []
    for i, col in enumerate(full_cols):
        observed = xtr[:, i][~np.isnan(xtr[:, i])]
        if len(observed) and len(np.unique(observed)) <= 1:
            constant.append(col)
        if len(observed) and len(np.unique(observed)) > 1:
            keep_indices.append(i)
    excluded = sorted(set(all_missing) | set(constant))
    if not keep_indices:
        raise ValueError("All features are missing or constant in the training partition")
    xtr, xv, xt = xtr[:, keep_indices], xv[:, keep_indices], xt[:, keep_indices]
    cols = [full_cols[i] for i in keep_indices]

    splitter = TimeSeriesSplit(n_splits=4)
    cv_report, candidates = {}, {}
    for name in ("logistic_regression", "random_forest", "extra_trees"):
        folds = []
        for fold, (fit_idx, eval_idx) in enumerate(splitter.split(xtr), 1):
            if len(set(ytr[fit_idx])) < 2:
                continue
            model = make_pipeline(name)
            model.fit(xtr[fit_idx], ytr[fit_idx])
            folds.append({"fold": fold, "fit_rows": len(fit_idx), "evaluation_rows": len(eval_idx),
                          **metrics(ytr[eval_idx], model.predict(xtr[eval_idx]))})
        if not folds:
            raise ValueError(f"No usable expanding-window CV folds for {name}")
        cv_scores = [item["macro_f1"] for item in folds]
        cv_report[name] = {"folds": folds, "mean_macro_f1": round(float(np.mean(cv_scores)), 4),
                           "std_macro_f1": round(float(np.std(cv_scores)), 4)}
        candidate = make_pipeline(name)
        candidate.fit(xtr, ytr)
        candidates[name] = {"model": candidate, "validation": metrics(yv, candidate.predict(xv))}

    selected = max(candidates, key=lambda name: (candidates[name]["validation"]["macro_f1"],
        cv_report[name]["mean_macro_f1"], -cv_report[name]["std_macro_f1"]))
    xtrv, ytrv = np.concatenate([xtr, xv]), np.concatenate([ytr, yv])
    holdout = make_pipeline(selected)
    holdout.fit(xtrv, ytrv)
    test_predictions = holdout.predict(xt)
    test_metrics = metrics(yt, test_predictions)
    validation_predictions = candidates[selected]["model"].predict(xv)
    validation_agent6 = agent6_labels(xv_full, full_cols)
    test_agent6 = agent6_labels(xt_full, full_cols)
    hybrid_validation = ensemble_diagnostics(yv, validation_predictions, validation_agent6)
    hybrid_test = ensemble_diagnostics(yt, test_predictions, test_agent6)
    majority = max(set(ytr), key=list(ytr).count)
    val_baseline = metrics(yv, np.asarray([majority] * len(yv)))
    test_baseline = metrics(yt, np.asarray([majority] * len(yt)))

    # Record the untouched holdout score first; then refit the saved research model on all labelled history.
    all_x, all_y = np.concatenate([xtr, xv, xt]), np.concatenate([ytr, yv, yt])
    production = make_pipeline(selected)
    production.fit(all_x, all_y)
    output_dir.mkdir(parents=True, exist_ok=True)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"pipeline": production, "feature_columns": cols, "selected_model": selected,
        "training_rows": int(len(all_y)), "label_classes": list(LABELS),
        "model_status": "research_candidate_not_live"}, model_path)

    report = {
        "status": "training_and_evaluation_completed", "live_prediction_enabled": False,
        "selected_model": selected,
        "selection_method": "4-fold expanding time-series CV on train; validation macro-F1 selects candidate; test used once",
        "cross_validation": cv_report, "validation": candidates[selected]["validation"],
        "validation_majority_baseline": val_baseline, "final_holdout_test": test_metrics,
        "test_majority_baseline": test_baseline,
        "agent6_ml_hybrid_backtest": {"validation": hybrid_validation, "final_holdout_test": hybrid_test},
        "rows": {"train": len(ytr), "validation": len(yv), "test": len(yt), "model_refit_after_test": len(all_y)},
        "date_ranges": {"train": [dtr[0], dtr[-1]], "validation": [dv[0], dv[-1]], "test": [dt[0], dt[-1]]},
        "source_feature_count": len(full_cols),
        "feature_count": len(cols),
        "excluded_feature_count": len(excluded),
        "excluded_features": excluded,
        "class_counts": {label: int(np.sum(all_y == label)) for label in LABELS},
        "data_quality": {"features_with_missing_values_in_train": sum(v > 0 for v in missing.values()),
            "all_missing_features_in_train": all_missing, "constant_features_in_train": constant,
            "missing_counts_by_feature": missing,
            "selection_rule": "Drop features that are all-missing or constant in the training partition only; apply the same retained columns to validation and test."},
        "preprocessing": {"imputation": "Median imputer fitted inside each training fold and final pipeline.",
            "scaling": "StandardScaler fitted inside Logistic Regression pipeline only; tree models are not scaled.",
            "leakage_control": "Chronological partitions, no shuffle; validation for selection; test used once."},
        "limitations": ["Target is next archived session same-expiry NIFTY FUTIDX return, not NIFTY 50 cash-index return.",
            "About two years of daily observations may yield unstable metrics.",
            "Neutral class has low support; inspect per-class recall and macro-F1.",
            "This workflow trains/evaluates only; it does not generate a market forecast."]
    }
    (output_dir / "training_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "live_prediction_enabled": False,
        "selected_model": selected, "rows": report["rows"], "source_feature_count": len(full_cols),
        "feature_count": len(cols), "excluded_features": excluded,
        "cv_mean_macro_f1": {k: v["mean_macro_f1"] for k, v in cv_report.items()},
        "validation": report["validation"], "final_holdout_test": test_metrics,
        "agent6_ml_hybrid_validation": hybrid_validation,
        "agent6_ml_hybrid_final_holdout_test": hybrid_test,
        "data_quality": report["data_quality"]}, indent=2))
    return report

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset-dir", default="data/ml_dataset")
    ap.add_argument("--output-dir", default="data/ml_training")
    ap.add_argument("--model-path", default="artifacts/nse_direction_model.joblib")
    args = ap.parse_args()
    run(args.dataset_dir, args.output_dir, args.model_path)

if __name__ == "__main__":
    main()
