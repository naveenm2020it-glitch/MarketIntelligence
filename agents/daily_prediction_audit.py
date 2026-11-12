#!/usr/bin/env python3
"""Persist a leakage-safe daily prediction-vs-actual ledger from Agent 7 evidence."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from agents.walk_forward_validation import classification_metrics, parse_day, prediction_label

AGENT6_FILE = "eod_market_confluence_intelligence.json"
FUTURES_FILE = "futures_buildup_intelligence.json"
CSV_FIELDS = (
    "signal_date", "target_date", "audit_status", "prediction", "actual",
    "result", "baseline_prediction", "baseline_result", "target_return_percent",
    "nifty_futidx_expiry", "signal_day_close", "target_day_close",
    "calendar_gap_days", "agent6_confidence", "agent6_confidence_score",
)


def read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def settled_rows(report: dict) -> list[dict]:
    pairs = report.get("sample_pairs", [])
    if not isinstance(pairs, list):
        raise ValueError("Agent 7 sample_pairs must be a list")
    rows: list[dict] = []
    previous: dict | None = None
    for pair in sorted(pairs, key=lambda x: (x.get("signal_date", ""), x.get("target_date", ""))):
        signal_date = str(pair.get("signal_date", ""))
        target_date = str(pair.get("target_date", ""))
        if not parse_day(signal_date) or not parse_day(target_date):
            raise ValueError(f"Invalid signal/target date in Agent 7 pair: {pair}")
        prediction, actual = pair.get("prediction"), pair.get("actual")
        if prediction not in ("bullish", "bearish", "neutral") or actual not in ("bullish", "bearish", "neutral"):
            raise ValueError(f"Unsupported prediction/actual class in Agent 7 pair: {pair}")
        baseline_prediction = None
        if previous and previous["target_date"] <= signal_date:
            baseline_prediction = previous["actual"]
        result = "correct" if prediction == actual else "incorrect"
        baseline_result = (
            "correct" if baseline_prediction == actual else "incorrect"
        ) if baseline_prediction else None
        rows.append({
            "signal_date": signal_date,
            "target_date": target_date,
            "audit_status": "settled",
            "prediction": prediction,
            "actual": actual,
            "result": result,
            "baseline_prediction": baseline_prediction,
            "baseline_result": baseline_result,
            "target_return_percent": pair.get("target_return_percent"),
            "nifty_futidx_expiry": pair.get("nifty_futidx_expiry"),
            "signal_day_close": pair.get("signal_day_close"),
            "target_day_close": pair.get("target_day_close"),
            "calendar_gap_days": pair.get("calendar_gap_days"),
            "agent6_confidence": pair.get("agent6_confidence"),
            "agent6_confidence_score": pair.get("agent6_confidence_score"),
        })
        previous = pair
    return rows


def latest_pending_signal(analysis_root: Path, settled_signal_dates: set[str]) -> dict | None:
    if not analysis_root.is_dir():
        return None
    folders = sorted(
        (p for p in analysis_root.iterdir()
         if p.is_dir() and parse_day(p.name) and parse_day(p.name).weekday() < 5),
        key=lambda p: p.name,
    )
    for folder in reversed(folders):
        day = folder.name
        if day in settled_signal_dates:
            return None
        a6 = read_json(folder / AGENT6_FILE)
        futures = read_json(folder / FUTURES_FILE)
        if not a6 or not futures:
            continue
        if (a6.get("status") != "passed"
                or a6.get("agent") != "eod_market_confluence_intelligence"
                or a6.get("trading_date") != day
                or futures.get("status") != "passed"
                or futures.get("agent") != "futures_buildup_intelligence"
                or futures.get("trading_date") != day):
            continue
        prediction = prediction_label(a6.get("direction"))
        if prediction is None:
            continue
        contracts = []
        for row in futures.get("contracts", []):
            if row.get("symbol") != "NIFTY" or row.get("instrument_type") != "FUTIDX":
                continue
            expiry = parse_day(row.get("expiry"))
            close = row.get("close_price")
            if expiry and isinstance(close, (int, float)) and close > 0:
                contracts.append((expiry.isoformat(), float(close)))
        if not contracts:
            continue
        expiry, close = sorted(contracts)[0]
        return {
            "signal_date": day,
            "target_date": None,
            "audit_status": "pending",
            "prediction": prediction,
            "actual": None,
            "result": "pending",
            "baseline_prediction": None,
            "baseline_result": None,
            "target_return_percent": None,
            "nifty_futidx_expiry": expiry,
            "signal_day_close": close,
            "target_day_close": None,
            "calendar_gap_days": None,
            "agent6_confidence": a6.get("confidence"),
            "agent6_confidence_score": a6.get("confidence_score"),
        }
    return None


def build_audit(report: dict, analysis_root: Path, existing: dict | None = None) -> dict:
    current_rows = settled_rows(report)
    old_rows = existing.get("records", []) if existing else []
    # Key by signal date: a pending row is upgraded in place when the next
    # archived session arrives. Re-running a workflow does not duplicate rows.
    merged = {str(row.get("signal_date")): row for row in old_rows if row.get("signal_date")}
    for row in current_rows:
        merged[row["signal_date"]] = row
    pending = latest_pending_signal(analysis_root, {r["signal_date"] for r in current_rows})
    if pending and pending["signal_date"] not in merged:
        merged[pending["signal_date"]] = pending
    records = sorted(merged.values(), key=lambda x: x.get("signal_date", ""))

    settled = [r for r in records if r.get("audit_status") == "settled"]
    matched = [r for r in settled if r.get("baseline_prediction") in ("bullish", "bearish", "neutral")]
    baseline_rows = [
        {"prediction": r["baseline_prediction"], "actual": r["actual"]} for r in matched
    ]
    matched_agent6_rows = [
        {"prediction": r["prediction"], "actual": r["actual"]} for r in matched
    ]
    return {
        "schema_version": 1,
        "agent": "daily_prediction_audit",
        "source": "Agent 7 persisted chronological NSE EOD evidence",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "validation_status": report.get("status"),
        "validation_type": report.get("validation_type"),
        "target_definition": report.get("target_definition"),
        "records_count": len(records),
        "settled_count": len(settled),
        "pending_count": sum(r.get("audit_status") == "pending" for r in records),
        "agent6_metrics_all_settled": classification_metrics([
            {"prediction": r["prediction"], "actual": r["actual"]} for r in settled
        ]),
        "matched_comparison": {
            "samples": len(matched),
            "same_rows_for_both_methods": True,
            "agent6": classification_metrics(matched_agent6_rows),
            "persistence_baseline": classification_metrics(baseline_rows),
        },
        "records": records,
        "limitations": [
            "Settled outcomes use same-expiry NIFTY FUTIDX close-to-close returns, not NIFTY 50 cash-index returns.",
            "The latest EOD signal remains pending until a later valid archived session provides its actual outcome.",
            "The persistence baseline uses only a previously completed pair and is omitted when no prior target was available.",
            "This ledger evaluates existing Agent 6 rules; it does not train an ML model or establish intraday profitability.",
            "Only validated NSE archive snapshots are included; unavailable dates are not fabricated.",
        ],
    }


def write_csv(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def run(analysis_root: Path, validation_report: Path, output: Path, csv_output: Path) -> dict:
    report = read_json(validation_report)
    if report is None:
        raise FileNotFoundError(f"Agent 7 validation report not found: {validation_report}")
    existing = read_json(output)
    audit = build_audit(report, analysis_root, existing)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_csv(csv_output, audit["records"])
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-root", default="data/analysis")
    parser.add_argument("--validation-report", default="data/backfill/walk_forward_validation.json")
    parser.add_argument("--output", default="data/audit/daily_prediction_audit.json")
    parser.add_argument("--csv-output", default="data/audit/daily_prediction_audit.csv")
    args = parser.parse_args()
    result = run(Path(args.analysis_root), Path(args.validation_report),
                 Path(args.output), Path(args.csv_output))
    print(json.dumps({
        "status": "passed",
        "records": result["records_count"],
        "settled": result["settled_count"],
        "pending": result["pending_count"],
        "agent6_accuracy": result["agent6_metrics_all_settled"].get("accuracy"),
        "matched_samples": result["matched_comparison"]["samples"],
        "baseline_accuracy": result["matched_comparison"]["persistence_baseline"].get("accuracy"),
        "output": args.output,
        "csv_output": args.csv_output,
    }, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
