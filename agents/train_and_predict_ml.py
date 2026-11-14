#!/usr/bin/env python3
"""Leakage-safe baseline training, evaluation and next-session NSE forecast."""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date, timedelta
from pathlib import Path

import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, precision_recall_fscore_support
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

try:
    from agents.build_ml_training_dataset import features_for
except ModuleNotFoundError:  # Direct script execution from the agents/ directory.
    from build_ml_training_dataset import features_for

LABELS = ("bearish", "neutral", "bullish")
META = {"signal_date", "target_date", "target_expiry", "target_return_pct", "target_class"}


def load_split(path: Path):
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"Empty dataset split: {path}")
    columns = [key for key in rows[0] if key not in META]
    x, y, dates = [], [], []
    for row in rows:
        x.append([float(row[c]) if row.get(c, "").strip() else float("nan") for c in columns])
        y.append(row["target_class"])
        dates.append(row["signal_date"])
    return x, y, dates, columns


def make_model(name: str):
    if name == "logistic_regression":
        estimator = LogisticRegression(
            C=0.1, class_weight="balanced", max_iter=5000, solver="lbfgs",
            random_state=42,
        )
        return Pipeline([
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
            ("model", estimator),
        ])
    if name == "random_forest":
        estimator = RandomForestClassifier(
            n_estimators=300, max_depth=4, min_samples_leaf=8,
            max_features="sqrt", class_weight="balanced_subsample",
            random_state=42, n_jobs=1,
        )
        return Pipeline([
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("model", estimator),
        ])
    raise ValueError(f"Unknown model: {name}")


def score(model, x, y):
    predicted = model.predict(x)
    return {
        "accuracy": round(float(accuracy_score(y, predicted)), 4),
        "macro_f1": round(float(f1_score(y, predicted, labels=list(LABELS), average="macro", zero_division=0)), 4),
        "class_metrics": {
            label: {
                "precision": round(float(p), 4),
                "recall": round(float(r), 4),
                "f1": round(float(f), 4),
                "support": int(n),
            }
            for label, p, r, f, n in zip(
                LABELS,
                *precision_recall_fscore_support(y, predicted, labels=list(LABELS), zero_division=0),
            )
        },
    }


def latest_valid_signal(analysis_root: Path):
    folders = sorted(
        (p for p in analysis_root.iterdir() if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    for folder in folders:
        try:
            signal_day = date.fromisoformat(folder.name)
            features = features_for(folder, folder.name)
            return signal_day, features
        except (ValueError, OSError):
            continue
    raise ValueError("No latest validated signal-day snapshots found in data/analysis")


def expected_next_weekday(signal_day: date) -> date:
    candidate = signal_day + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def run(dataset_dir: Path, analysis_root: Path, output_dir: Path, model_path: Path):
    x_train, y_train, train_dates, columns = load_split(dataset_dir / "train.csv")
    x_val, y_val, _, val_columns = load_split(dataset_dir / "validation.csv")
    x_test, y_test, _, test_columns = load_split(dataset_dir / "test.csv")
    if columns != val_columns or columns != test_columns:
        raise ValueError("Feature columns differ between chronological dataset splits")
    if len(x_train) < 100 or len(x_val) < 20 or len(x_test) < 20:
        raise ValueError("Insufficient rows for a responsible train/validation/test evaluation")
    if len(set(y_train)) < 2:
        raise ValueError("Training partition contains fewer than two target classes")

    # Model choice uses validation only. Test remains untouched until this choice is fixed.
    candidates = {}
    for name in ("logistic_regression", "random_forest"):
        candidate = make_model(name)
        candidate.fit(x_train, y_train)
        candidates[name] = {"model": candidate, "validation": score(candidate, x_val, y_val)}
    selected = max(candidates, key=lambda name: (candidates[name]["validation"]["macro_f1"], name == "logistic_regression"))
    chosen = candidates[selected]["model"]

    # One final holdout evaluation; no tuning or model choice uses this result.
    x_train_val = x_train + x_val
    y_train_val = y_train + y_val
    holdout_model = make_model(selected)
    holdout_model.fit(x_train_val, y_train_val)
    test_metrics = score(holdout_model, x_test, y_test)

    # After recording holdout metrics, refit the production model on all known labels.
    all_x = x_train + x_val + x_test
    all_y = y_train + y_val + y_test
    production_model = make_model(selected)
    production_model.fit(all_x, all_y)

    signal_day, latest_features = latest_valid_signal(analysis_root)
    latest_x = [[float(latest_features.get(column)) if latest_features.get(column) is not None else float("nan") for column in columns]]
    probabilities = production_model.predict_proba(latest_x)[0]
    probability_map = {label: 0.0 for label in LABELS}
    for label, probability in zip(production_model.classes_, probabilities):
        probability_map[str(label)] = round(float(probability), 4)
    raw_prediction = max(probability_map, key=probability_map.get)
    ranked_scores = sorted(probability_map.values(), reverse=True)
    score_margin = ranked_scores[0] - ranked_scores[1]
    next_weekday = expected_next_weekday(signal_day)

    validation_best = candidates[selected]["validation"]
    majority = max(set(y_train), key=y_train.count)
    majority_validation = [majority] * len(y_val)
    majority_f1 = float(f1_score(y_val, majority_validation, labels=list(LABELS), average="macro", zero_division=0))
    has_validation_edge = validation_best["macro_f1"] > majority_f1
    # Do not publish a directional call when holdout skill is weak or class scores are tied.
    model_readiness = "research_only" if test_metrics["macro_f1"] < 0.40 or not has_validation_edge else "experimental"
    has_directional_edge = (
        model_readiness == "experimental"
        and probability_map[raw_prediction] >= 0.45
        and score_margin >= 0.08
    )
    prediction = raw_prediction if has_directional_edge else "no_clear_edge"

    output_dir.mkdir(parents=True, exist_ok=True)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "pipeline": production_model,
        "feature_columns": columns,
        "selected_model": selected,
        "training_rows": len(all_y),
        "label_classes": list(LABELS),
    }, model_path)

    evaluation = {
        "status": "evaluated",
        "selected_model": selected,
        "selection_metric": "validation_macro_f1",
        "validation": validation_best,
        "majority_baseline_validation_macro_f1": round(majority_f1, 4),
        "validation_edge_over_majority_baseline": has_validation_edge,
        "final_holdout_test": test_metrics,
        "rows": {"train": len(x_train), "validation": len(x_val), "test": len(x_test), "production_refit": len(all_y)},
        "features": len(columns),
        "date_ranges": {
            "train": [train_dates[0], train_dates[-1]],
            "validation": [load_split(dataset_dir / "validation.csv")[2][0], load_split(dataset_dir / "validation.csv")[2][-1]],
            "test": [load_split(dataset_dir / "test.csv")[2][0], load_split(dataset_dir / "test.csv")[2][-1]],
        },
        "preprocessing": "Median imputation fit inside each training pipeline; StandardScaler used only for logistic regression; no preprocessing fit on validation/test during model selection.",
        "production_refit": "After one-time holdout evaluation, selected model refit on all currently labelled historical rows for next-session inference.",
        "limitations": [
            "Small historical sample; this is an experimental baseline, not a proven trading system.",
            "Target is next archived session same-expiry NIFTY FUTIDX return, not NIFTY 50 cash-index return.",
            "Expected next date skips weekends only; NSE holidays must be checked before treating it as a confirmed session.",
            "Model probabilities are uncalibrated scores, not guaranteed confidence.",
        ],
    }
    (output_dir / "model_evaluation.json").write_text(json.dumps(evaluation, indent=2) + "\n", encoding="utf-8")
    prediction_doc = {
        "status": "experimental_forecast",
        "signal_date": signal_day.isoformat(),
        "expected_next_weekday": next_weekday.isoformat(),
        "target_session_note": "Expected weekday only; confirm NSE holiday calendar.",
        "prediction": prediction,
        "leading_class": raw_prediction,
        "class_scores": probability_map,
        "top_class_score": probability_map[raw_prediction],
        "top_two_score_margin": round(score_margin, 4),
        "model_readiness": model_readiness,
        "decision_reason": (
            "No directional edge: holdout performance is below the 0.40 macro-F1 readiness threshold."
            if model_readiness == "research_only"
            else "No directional edge: leading class score or margin is below the decision threshold."
            if not has_directional_edge
            else "Experimental direction passed the baseline and score-margin gates."
        ),
        "selected_model": selected,
        "validation_edge_over_majority_baseline": has_validation_edge,
        "validation_macro_f1": validation_best["macro_f1"],
        "final_holdout_test_macro_f1": test_metrics["macro_f1"],
        "training_rows": len(all_y),
        "feature_count": len(columns),
        "interpretation": "Research signal only. Do not use as a standalone trade instruction.",
    }
    (output_dir / "latest.json").write_text(json.dumps(prediction_doc, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"prediction": prediction_doc, "evaluation": evaluation}, indent=2))
    return prediction_doc, evaluation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", default="data/ml_dataset")
    parser.add_argument("--analysis-root", default="data/analysis")
    parser.add_argument("--output-dir", default="data/ml_predictions")
    parser.add_argument("--model-path", default="artifacts/nse_direction_model.joblib")
    args = parser.parse_args()
    run(Path(args.dataset_dir), Path(args.analysis_root), Path(args.output_dir), Path(args.model_path))


if __name__ == "__main__":
    main()
