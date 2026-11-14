#!/usr/bin/env python3
"""Build a leakage-safe, chronological ML dataset from persisted NSE agent snapshots.

Features are taken only from the signal date. Labels use the next archived
session's same-expiry NIFTY FUTIDX close. No NSE/B2 downloads are performed.
"""
from __future__ import annotations
import argparse
import csv
import json
import math
from datetime import date, datetime
from pathlib import Path

FILES = {
    "participant": ("participant_oi_intelligence.json", "participant_oi_intelligence"),
    "delivery": ("delivery_strength.json", "equity_delivery_strength"),
    "futures": ("futures_buildup_intelligence.json", "futures_buildup_intelligence"),
    "options": ("nifty_option_oi_intelligence.json", "nifty_option_oi_intelligence"),
    "confluence": ("eod_market_confluence_intelligence.json", "eod_market_confluence_intelligence"),
}
CLASSES = {"bullish": 1, "bearish": -1, "neutral": 0}


def read_snapshot(folder: Path, key: str, day: str, required: bool = True):
    filename, agent = FILES[key]
    path = folder / filename
    if not path.is_file():
        if required:
            raise ValueError(f"Missing required {key} snapshot for {day}")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid {path}: {exc}") from exc
    if data.get("status") != "passed" or data.get("agent") != agent:
        raise ValueError(f"{path} is not a passed {agent} snapshot")
    if data.get("trading_date") != day:
        raise ValueError(f"{path} trading_date does not match folder {day}")
    return data


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def add(out: dict, key: str, value):
    out[key] = float(value) if finite(value) else None


def features_for(folder: Path, day: str) -> dict:
    p = read_snapshot(folder, "participant", day)
    d = read_snapshot(folder, "delivery", day)
    f = read_snapshot(folder, "futures", day)
    c = read_snapshot(folder, "confluence", day)
    o = read_snapshot(folder, "options", day, required=False)
    x = {}

    # Participant positions are retained separately; counterpart groups are not summed.
    for group in ("FII", "DII", "CLIENT", "PRO"):
        row = p.get("participants", {}).get(group, {})
        for field in ("index_futures_net_long", "index_options_directional_net",
                      "future_index_long", "future_index_short",
                      "option_index_call_long", "option_index_put_long",
                      "option_index_call_short", "option_index_put_short"):
            add(x, f"participant_{group.lower()}_{field}", row.get(field))

    stocks = [r for r in d.get("stocks", []) if isinstance(r, dict)]
    delivery_pct = [r["delivery_percentage"] for r in stocks if finite(r.get("delivery_percentage"))]
    price_pct = [r["price_change_percentage"] for r in stocks if finite(r.get("price_change_percentage"))]
    matched = [r for r in stocks if r.get("equity_bhavcopy_match") is True]
    add(x, "delivery_universe_symbols", d.get("universe_symbols", len(stocks)))
    add(x, "delivery_equity_matches", d.get("equity_bhavcopy_matches", len(matched)))
    add(x, "delivery_median_percentage", d.get("universe_median_delivery_percentage"))
    add(x, "delivery_positive_price_breadth", sum(v > 0 for v in price_pct) / len(price_pct) if price_pct else None)
    add(x, "delivery_negative_price_breadth", sum(v < 0 for v in price_pct) / len(price_pct) if price_pct else None)
    add(x, "delivery_median_stock_price_change_pct", sorted(price_pct)[len(price_pct)//2] if price_pct else None)
    add(x, "delivery_median_stock_delivery_pct", sorted(delivery_pct)[len(delivery_pct)//2] if delivery_pct else None)
    add(x, "delivery_price_delivery_rows", len(price_pct))

    nifty = [r for r in f.get("contracts", []) if r.get("symbol") == "NIFTY"
             and r.get("instrument_type") == "FUTIDX" and finite(r.get("close_price"))
             and r.get("expiry")]
    nifty.sort(key=lambda r: str(r.get("expiry")))
    nearest = nifty[0] if nifty else {}
    add(x, "nifty_fut_nearest_close", nearest.get("close_price"))
    add(x, "nifty_fut_nearest_price_change_pct", nearest.get("price_change_percentage"))
    add(x, "nifty_fut_nearest_oi", nearest.get("open_interest"))
    add(x, "nifty_fut_nearest_oi_change", nearest.get("open_interest_change"))
    add(x, "nifty_fut_contract_count", len(nifty))
    for label in ("long_buildup", "short_buildup", "short_covering", "long_unwinding", "neutral_or_flat"):
        add(x, f"nifty_fut_class_{label}", 1 if nearest.get("classification") == label else 0)

    # Option features use the nearest expiry only; absent options stay null.
    expiries = (o or {}).get("expiries", [])
    expiries = sorted(expiries, key=lambda r: str(r.get("expiry") or "9999"))
    nearest_opt = expiries[0] if expiries else {}
    strikes = nearest_opt.get("strikes", [])
    # New compact snapshots store pre-aggregated OI to avoid retaining every strike.
    # Older full snapshots remain supported as a fallback.
    call_oi = nearest_opt.get("total_call_oi")
    put_oi = nearest_opt.get("total_put_oi")
    if not finite(call_oi):
        call_oi = sum(r.get("call_oi", 0) for r in strikes if finite(r.get("call_oi"))) if strikes else None
    if not finite(put_oi):
        put_oi = sum(r.get("put_oi", 0) for r in strikes if finite(r.get("put_oi"))) if strikes else None
    add(x, "option_nearest_expiry_strike_count", nearest_opt.get("strike_count"))
    add(x, "option_nearest_expiry_total_call_oi", call_oi if expiries else None)
    add(x, "option_nearest_expiry_total_put_oi", put_oi if expiries else None)
    ratio = nearest_opt.get("put_call_oi_ratio")
    if not finite(ratio):
        ratio = put_oi / call_oi if finite(call_oi) and call_oi else None
    add(x, "option_nearest_expiry_put_call_oi_ratio", ratio)
    add(x, "option_nearest_expiry_max_pain", nearest_opt.get("max_pain"))
    support = (nearest_opt.get("support_levels") or [{}])[0]
    resistance = (nearest_opt.get("resistance_levels") or [{}])[0]
    add(x, "option_top_put_support_strike", support.get("strike"))
    add(x, "option_top_call_resistance_strike", resistance.get("strike"))

    # Agent 6 outputs are features, never the target.
    add(x, "agent6_score", c.get("score"))
    add(x, "agent6_confidence_score", c.get("confidence_score"))
    add(x, "agent6_coverage_percent", c.get("coverage_percent"))
    direction = c.get("direction")
    for label in ("bullish", "bearish", "neutral_or_mixed", "insufficient_data"):
        add(x, f"agent6_direction_{label}", 1 if direction == label else 0)
    for grade in ("high", "moderate", "low", "unrated"):
        add(x, f"agent6_confidence_{grade}", 1 if c.get("confidence") == grade else 0)
    return x


def fut_contract(snapshot: dict, expiry: str):
    rows = [r for r in snapshot.get("contracts", [])
            if r.get("symbol") == "NIFTY" and r.get("instrument_type") == "FUTIDX"
            and r.get("expiry") == expiry and finite(r.get("close_price"))]
    if len(rows) > 1:
        raise ValueError(f"Duplicate NIFTY FUTIDX contract for expiry {expiry}")
    return rows[0] if rows else None


def build_rows(root: Path, max_gap_days: int = 5, neutral_band_bps: float = 10.0):
    folders = sorted(p for p in root.iterdir() if p.is_dir())
    dates = []
    for folder in folders:
        try:
            day = date.fromisoformat(folder.name)
        except ValueError:
            continue
        if day.weekday() >= 5:
            continue
        # Required features must be present and validated on signal day.
        try:
            feats = features_for(folder, folder.name)
            futures = read_snapshot(folder, "futures", folder.name)
        except (ValueError, OSError):
            continue
        contracts = [r for r in futures.get("contracts", [])
                     if r.get("symbol") == "NIFTY" and r.get("instrument_type") == "FUTIDX"
                     and r.get("expiry") and finite(r.get("close_price"))]
        if not contracts:
            continue
        dates.append((folder.name, feats, futures))

    rows, skipped = [], []
    for (signal_day, feats, signal_fut), (target_day, _, target_fut) in zip(dates, dates[1:]):
        gap = (date.fromisoformat(target_day) - date.fromisoformat(signal_day)).days
        if gap <= 0 or gap > max_gap_days:
            skipped.append({"signal_date": signal_day, "target_date": target_day, "reason": "archive_gap"})
            continue
        common = sorted({r["expiry"] for r in signal_fut["contracts"]
                         if r.get("symbol") == "NIFTY" and r.get("instrument_type") == "FUTIDX"}
                        & {r["expiry"] for r in target_fut["contracts"]
                           if r.get("symbol") == "NIFTY" and r.get("instrument_type") == "FUTIDX"})
        if not common:
            skipped.append({"signal_date": signal_day, "target_date": target_day, "reason": "no_common_expiry"})
            continue
        expiry = common[0]
        before, after = fut_contract(signal_fut, expiry), fut_contract(target_fut, expiry)
        if not before or not after or before["close_price"] <= 0:
            skipped.append({"signal_date": signal_day, "target_date": target_day, "reason": "missing_same_expiry_close"})
            continue
        move_pct = (after["close_price"] - before["close_price"]) / before["close_price"] * 100
        band = neutral_band_bps / 100
        label = "bullish" if move_pct > band else "bearish" if move_pct < -band else "neutral"
        row = {"signal_date": signal_day, "target_date": target_day, "target_expiry": expiry,
               **feats, "target_return_pct": round(move_pct, 6), "target_class": label}
        rows.append(row)
    return rows, skipped


def write_dataset(rows: list[dict], output: Path, minimum_samples: int = 30):
    if len(rows) < minimum_samples:
        raise ValueError(f"Only {len(rows)} valid feature-label pairs; need at least {minimum_samples}")
    output.mkdir(parents=True, exist_ok=True)
    feature_columns = sorted(k for k in rows[0] if k not in {
        "signal_date", "target_date", "target_expiry", "target_return_pct", "target_class"
    })
    # Chronological 70/15/15 split. Do not shuffle time-series observations.
    n = len(rows)
    train_end = int(n * 0.70)
    validation_end = int(n * 0.85)
    splits = {
        "train": rows[:train_end],
        "validation": rows[train_end:validation_end],
        "test": rows[validation_end:],
    }
    columns = ["signal_date", "target_date", "target_expiry", *feature_columns,
               "target_return_pct", "target_class"]
    for name, subset in splits.items():
        with (output / f"{name}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(subset)
    manifest = {
        "status": "ready_for_model_training",
        "dataset_version": 1,
        "rows": len(rows),
        "feature_count": len(feature_columns),
        "label": "next archived session same-expiry NIFTY FUTIDX return; +/-10 bps neutral band",
        "feature_timing": "signal-date snapshots only; target-date data is used only to calculate labels",
        "no_shuffle": True,
        "split": {name: {"rows": len(subset),
                         "start_signal_date": subset[0]["signal_date"] if subset else None,
                         "end_signal_date": subset[-1]["signal_date"] if subset else None}
                  for name, subset in splits.items()},
        "class_counts": {label: sum(r["target_class"] == label for r in rows)
                         for label in ("bullish", "bearish", "neutral")},
        "missing_feature_values": {key: sum(r.get(key) is None for r in rows)
                                   for key in feature_columns},
        "excluded_pairs": None,
        "limitations": [
            "This is a futures-proxy target, not NIFTY 50 cash-index direction.",
            "Chronological split is a first baseline; tuning must use train/validation only and test remains untouched.",
            "Feature nulls are preserved; the training pipeline must impute within each training fold only.",
            "No model has been trained by this dataset builder.",
        ],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--analysis-root", default="data/analysis")
    ap.add_argument("--output", default="data/ml_dataset")
    ap.add_argument("--minimum-samples", type=int, default=30)
    ap.add_argument("--max-gap-days", type=int, default=5)
    args = ap.parse_args()
    rows, skipped = build_rows(Path(args.analysis_root), args.max_gap_days)
    manifest = write_dataset(rows, Path(args.output), args.minimum_samples)
    manifest["excluded_pairs"] = len(skipped)
    (Path(args.output) / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("status", "rows", "feature_count", "split", "class_counts", "excluded_pairs")}, indent=2))


if __name__ == "__main__":
    main()
