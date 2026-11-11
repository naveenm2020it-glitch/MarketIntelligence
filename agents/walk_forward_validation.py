#!/usr/bin/env python3
"""Agent 7: leakage-safe chronological validation of Agent 6 EOD direction.

Target is the next archived session's NIFTY FUTIDX close-to-close move using
the same expiry contract on both dates. It is explicitly a futures proxy,
not the NIFTY 50 cash-index return.
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

AGENT6_FILE = "eod_market_confluence_intelligence.json"
FUTURES_FILE = "futures_buildup_intelligence.json"
CLASSES = ("bullish", "bearish", "neutral")


def parse_day(value: str) -> datetime.date | None:
    token = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(token[:10] if fmt == "%Y-%m-%d" else token, fmt).date()
        except ValueError:
            pass
    return None


def load_json(path: Path) -> dict | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSON snapshot {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Snapshot must be a JSON object: {path}")
    return value


def valid_snapshot(path: Path, agent: str, day: str) -> dict | None:
    data = load_json(path)
    if data is None:
        return None
    if data.get("status") != "passed" or data.get("agent") != agent:
        return None
    if data.get("trading_date") != day:
        raise ValueError(f"{path} trading_date does not match its folder {day}")
    return data


def prediction_label(value: Any) -> str | None:
    if value == "bullish":
        return "bullish"
    if value == "bearish":
        return "bearish"
    if value in ("neutral_or_mixed", "neutral", "mixed"):
        return "neutral"
    return None


def contract_map(snapshot: dict) -> dict[str, dict]:
    result = {}
    for row in snapshot.get("contracts", []):
        if row.get("symbol") != "NIFTY" or row.get("instrument_type") != "FUTIDX":
            continue
        expiry = parse_day(row.get("expiry"))
        close = row.get("close_price")
        if expiry is None or not isinstance(close, (int, float)) or close <= 0:
            continue
        key = expiry.isoformat()
        # Duplicate rows for the same expiry are ambiguous; do not silently choose.
        if key in result:
            raise ValueError(f"Duplicate NIFTY FUTIDX contract for expiry {key}")
        result[key] = {"close_price": float(close), "expiry": key}
    return result


def build_pairs(root: Path, neutral_band_bps: float, max_gap_days: int) -> tuple[list[dict], list[dict]]:
    dates = sorted(p.name for p in root.iterdir()
                   if p.is_dir() and parse_day(p.name) is not None)
    snapshots = {}
    for day in dates:
        a6 = valid_snapshot(root / day / AGENT6_FILE, "eod_market_confluence_intelligence", day)
        fut = valid_snapshot(root / day / FUTURES_FILE, "futures_buildup_intelligence", day)
        if a6 and fut:
            snapshots[day] = (a6, fut)

    pairs, skipped = [], []
    usable_dates = sorted(snapshots)
    for index in range(len(usable_dates) - 1):
        signal_day, target_day = usable_dates[index], usable_dates[index + 1]
        signal_a6, signal_fut = snapshots[signal_day]
        _, target_fut = snapshots[target_day]
        pred = prediction_label(signal_a6.get("direction"))
        if pred is None:
            skipped.append({"signal_date": signal_day, "target_date": target_day,
                            "reason": "Agent 6 direction is insufficient or unsupported"})
            continue
        d0, d1 = parse_day(signal_day), parse_day(target_day)
        gap = (d1 - d0).days
        if gap <= 0 or gap > max_gap_days:
            skipped.append({"signal_date": signal_day, "target_date": target_day,
                            "reason": f"Archived-session gap {gap} days exceeds allowed range"})
            continue
        before = contract_map(signal_fut)
        after = contract_map(target_fut)
        common = sorted(set(before) & set(after))
        if not common:
            skipped.append({"signal_date": signal_day, "target_date": target_day,
                            "reason": "No same-expiry NIFTY FUTIDX contract on both dates"})
            continue
        expiry = common[0]  # nearest expiry common to both dates; no rollover splice
        start_close, end_close = before[expiry]["close_price"], after[expiry]["close_price"]
        move_pct = (end_close - start_close) / start_close * 100
        threshold_pct = neutral_band_bps / 100
        actual = "bullish" if move_pct > threshold_pct else "bearish" if move_pct < -threshold_pct else "neutral"
        pairs.append({
            "signal_date": signal_day, "target_date": target_day,
            "calendar_gap_days": gap, "prediction": pred, "actual": actual,
            "agent6_confidence": signal_a6.get("confidence"),
            "agent6_confidence_score": signal_a6.get("confidence_score"),
            "nifty_futidx_expiry": expiry,
            "signal_day_close": start_close, "target_day_close": end_close,
            "target_return_percent": round(move_pct, 6),
        })
    return pairs, skipped


def classification_metrics(pairs: list[dict]) -> dict:
    matrix = {actual: {pred: 0 for pred in CLASSES} for actual in CLASSES}
    for row in pairs:
        matrix[row["actual"]][row["prediction"]] += 1
    total = len(pairs)
    correct = sum(matrix[c][c] for c in CLASSES)
    per_class = {}
    recalls = []
    f1s = []
    for cls in CLASSES:
        tp = matrix[cls][cls]
        actual_n = sum(matrix[cls].values())
        predicted_n = sum(matrix[a][cls] for a in CLASSES)
        precision = tp / predicted_n if predicted_n else 0.0
        recall = tp / actual_n if actual_n else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[cls] = {"support": actual_n, "precision": round(precision, 4),
                          "recall": round(recall, 4), "f1": round(f1, 4)}
        if actual_n:
            recalls.append(recall)
            f1s.append(f1)
    directional = [x for x in pairs if x["actual"] in ("bullish", "bearish")]
    directional_correct = sum(x["prediction"] == x["actual"] for x in directional)
    confidence_groups = {}
    for grade in ("high", "moderate", "low", "unrated"):
        rows = [x for x in pairs if x.get("agent6_confidence") == grade]
        if rows:
            confidence_groups[grade] = {
                "samples": len(rows),
                "accuracy": round(sum(x["prediction"] == x["actual"] for x in rows) / len(rows), 4),
            }
    return {
        "samples": total,
        "accuracy": round(correct / total, 4) if total else None,
        "balanced_accuracy": round(sum(recalls) / len(recalls), 4) if recalls else None,
        "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else None,
        "directional_target_samples": len(directional),
        "directional_accuracy": round(directional_correct / len(directional), 4) if directional else None,
        "confusion_matrix_actual_rows_predicted_columns": matrix,
        "per_class": per_class,
        "by_agent6_confidence_grade": confidence_groups,
    }


def run(analysis_root: Path, output: Path, minimum_samples: int = 30,
        neutral_band_bps: float = 10.0, max_gap_days: int = 5) -> dict:
    if minimum_samples < 1 or neutral_band_bps < 0 or max_gap_days < 1:
        raise ValueError("minimum_samples and max_gap_days must be positive; neutral_band_bps cannot be negative")
    if not analysis_root.is_dir():
        raise ValueError(f"Analysis history directory does not exist: {analysis_root}")
    pairs, skipped = build_pairs(analysis_root, neutral_band_bps, max_gap_days)
    metrics = classification_metrics(pairs)
    enough = len(pairs) >= minimum_samples
    result = {
        "schema_version": 1,
        "agent": "walk_forward_validation",
        "status": "passed" if enough else "insufficient_data",
        "validation_type": "chronological_next_archived_session_out_of_sample",
        "target_definition": {
            "instrument": "NIFTY FUTIDX",
            "measure": "same-expiry futures close-to-close return from signal date to next available archived session",
            "neutral_band_bps": neutral_band_bps,
            "neutral_band_percent": round(neutral_band_bps / 100, 4),
            "contract_rollover": "Only the nearest expiry common to both dates is compared; no cross-expiry splice.",
            "not_cash_index": True,
        },
        "minimum_samples_required": minimum_samples,
        "metrics": metrics,
        "reliability_assessment": (
            "Descriptive backtest threshold met; still not proof of future profitability or statistical stability."
            if enough else
            "Insufficient chronological samples. Metrics, if present, are exploratory only; no accuracy grade is assigned."
        ),
        "sample_pairs": pairs,
        "skipped_pairs": skipped,
        "limitations": [
            "This is an EOD next-archived-session validation, not intraday validation.",
            "The target is NIFTY futures, not the NIFTY 50 cash index; same-expiry matching avoids contract-rollover price splicing.",
            "Only persisted Agent 6 and Agent 4 snapshots are used; no missing market values are fabricated.",
            "A missing archive date can make the next archived session differ from the immediately following exchange session; calendar gaps above the configured limit are skipped.",
            "The 10 bps neutral band is a fixed evaluation convention, not an optimized threshold.",
            "Agent 6 confidence is a heuristic grade, not a probability; confidence-group accuracy is descriptive, not calibration.",
            "At least 30 completed pairs are required before reporting the backtest as passed; larger samples and multiple market regimes are needed for stronger conclusions.",
            "No trading costs, slippage, execution assumptions or profitability are modelled."
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis-root", default="data/analysis")
    parser.add_argument("--output", required=True)
    parser.add_argument("--minimum-samples", type=int, default=30)
    parser.add_argument("--neutral-band-bps", type=float, default=10.0)
    parser.add_argument("--max-gap-days", type=int, default=5)
    args = parser.parse_args()
    result = run(Path(args.analysis_root), Path(args.output), args.minimum_samples,
                 args.neutral_band_bps, args.max_gap_days)
    print(json.dumps({
        "status": result["status"], "samples": result["metrics"]["samples"],
        "minimum_samples_required": result["minimum_samples_required"],
        "accuracy": result["metrics"]["accuracy"],
        "balanced_accuracy": result["metrics"]["balanced_accuracy"],
        "output": args.output,
    }, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
