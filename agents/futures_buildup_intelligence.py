#!/usr/bin/env python3
"""Agent 4: classify EOD futures buildup from NSE F&O UDiFF data."""
from __future__ import annotations
import argparse, json, re, sys
from collections import defaultdict
from pathlib import Path
from typing import Any

NUMERIC = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
FUTURE_TYPES = {"FUTIDX", "FUTSTK", "IDF", "STF"}
INSTRUMENT_CANONICAL = {"IDF": "FUTIDX", "STF": "FUTSTK", "FUTIDX": "FUTIDX", "FUTSTK": "FUTSTK"}

def norm_key(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())

def number(value: Any) -> float | None:
    if value is None:
        return None
    raw = str(value).strip().replace(",", "")
    if raw in {"", "-", "--", "NA", "N/A", "NULL"} or not NUMERIC.match(raw):
        return None
    return float(raw)

def classify(price_change: float | None, oi_change: float | None) -> str:
    if price_change is None or oi_change is None:
        return "unavailable"
    if price_change == 0 or oi_change == 0:
        return "neutral_or_flat"
    if price_change > 0 and oi_change > 0:
        return "long_buildup"
    if price_change < 0 and oi_change > 0:
        return "short_buildup"
    if price_change > 0 and oi_change < 0:
        return "short_covering"
    return "long_unwinding"

def first_num(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in row:
            value = number(row[key])
            if value is not None:
                return value
    return None

def flatten(files: list[dict]) -> list[dict]:
    return [record for file in files for record in file.get("records", [])]

def contract_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("FININSTRMTP") or row.get("FININSTRMTYPE") or row.get("INSTRUMENT") or "").strip().upper(),
        str(row.get("TCKRSYMB") or row.get("SYMBOL") or "").strip().upper(),
        str(row.get("XPRYDT") or row.get("EXPIRYDT") or "").strip().upper(),
        str(row.get("STRIKEPR") or row.get("STRIKEPRICE") or "").strip(),
        str(row.get("OPTIONTYP") or row.get("OPTIONTYPE") or "").strip().upper(),
    )

def analyze(files: list[dict], previous_files: list[dict] | None = None) -> dict:
    previous_closes = {}
    for previous in flatten(previous_files or []):
        prev = {norm_key(k): v for k, v in previous.items()}
        key = contract_key(prev)
        close_value = first_num(prev, "CLSPRIC", "CLOSEPRICE")
        if close_value is not None:
            previous_closes[key] = close_value
    raw_rows = flatten(files)
    if not raw_rows:
        raise ValueError("F&O Bhavcopy contains no records")
    contracts = []
    ignored_types = defaultdict(int)
    invalid_rows = 0
    for raw in raw_rows:
        row = {norm_key(k): v for k, v in raw.items()}
        instrument = str(row.get("FININSTRMTP") or row.get("FININSTRMTYPE") or row.get("INSTRUMENT") or "").strip().upper()
        if instrument not in FUTURE_TYPES:
            ignored_types[instrument or "missing"] += 1
            continue
        canonical_instrument = INSTRUMENT_CANONICAL[instrument]
        symbol = str(row.get("TCKRSYMB") or row.get("SYMBOL") or "").strip().upper()
        expiry = str(row.get("XPRYDT") or row.get("EXPIRYDT") or "").strip()
        close = first_num(row, "CLSPRIC", "CLOSEPRICE")
        previous_close = first_num(row, "PRVSCLSGPRIC", "PREVCLOSE", "PREVIOUSCLOSE")
        previous_close_source = "current_report" if previous_close is not None else None
        if previous_close is None:
            previous_close = previous_closes.get(contract_key(row))\n            if previous_close is not None:\n                previous_close_source = "previous_session_same_contract_close"
        oi = first_num(row, "OPNINTRST", "OPENINTEREST")
        oi_change = first_num(row, "CHNGINOPNINTRST", "CHANGEINOPENINTEREST", "CHNGINOPNINT")
        if not symbol or close is None or previous_close is None or oi_change is None:
            invalid_rows += 1
            continue
        price_change = close - previous_close
        price_change_pct = (price_change / previous_close * 100) if previous_close else None
        contracts.append({
            "symbol": symbol, "instrument_type": canonical_instrument, "source_instrument_code": instrument, "expiry": expiry or None,
            "close_price": close, "previous_close": previous_close, "previous_close_source": previous_close_source,
            "price_change": price_change,
            "price_change_percentage": price_change_pct,
            "open_interest": oi, "open_interest_change": oi_change,
            "classification": classify(price_change, oi_change),
        })
    if not contracts:
        raise ValueError("No valid FUTIDX/FUTSTK rows with close, previous close and OI change")
    by_symbol: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for contract in contracts:
        by_symbol[(contract["symbol"], contract["instrument_type"])].append(contract)
    underlying = []
    for (symbol, instrument), rows in sorted(by_symbol.items()):
        # Sum OI changes across listed expiries; price direction is volume-independent
        # and uses the median contract price percentage to avoid weighting by unlike lots.
        changes = [r["price_change_percentage"] for r in rows if r["price_change_percentage"] is not None]
        oi_changes = [r["open_interest_change"] for r in rows]
        median_price_pct = sorted(changes)[len(changes) // 2] if changes else None
        summed_oi_change = sum(oi_changes)
        underlying.append({
            "symbol": symbol, "instrument_type": instrument,
            "contracts_included": len(rows),
            "median_contract_price_change_percentage": median_price_pct,
            "aggregate_open_interest_change": summed_oi_change,
            "classification": classify(median_price_pct, summed_oi_change),
        })
    counts = defaultdict(int)
    for row in contracts:
        counts[row["classification"]] += 1
    return {
        "contracts": contracts,
        "underlyings": underlying,
        "classification_counts": dict(sorted(counts.items())),
        "source_rows": len(raw_rows),
        "valid_future_contracts": len(contracts),
        "invalid_future_rows": invalid_rows,
        "ignored_instrument_types": dict(sorted(ignored_types.items())),
        "source_instrument_mapping": {"IDF": "FUTIDX", "STF": "FUTSTK", "FUTIDX": "FUTIDX", "FUTSTK": "FUTSTK"},
    }

def run(input_path: Path, output_path: Path, previous_input_path: Path | None = None) -> dict:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if payload.get("status") != "passed" or payload.get("date_consistency") != "passed":
        raise ValueError("Normalization status/date consistency must both be passed")
    files = payload.get("reports", {}).get("fo_udiff", [])
    if not files:
        raise ValueError("Required F&O UDiFF report is missing")
    previous_files = []
    if previous_input_path is not None:
        previous_payload = json.loads(previous_input_path.read_text(encoding="utf-8"))
        previous_files = previous_payload.get("reports", {}).get("fo_udiff", [])
    analysis = analyze(files, previous_files)
    result = {
        "schema_version": 1, "agent": "futures_buildup_intelligence",
        "status": "passed", "data_scope": "EOD FUTIDX/FUTSTK futures buildup only",
        "trading_date": payload.get("requested_date"),
        **analysis,
        "method": "Contract classification uses close minus previous close and reported change in open interest. Underlying aggregation sums OI changes across included expiries and uses the median contract price-change percentage.",
        "limitations": [
            "This is an end-of-day futures snapshot, not intraday order flow or a live signal.",
            "FUTIDX and FUTSTK are included; options and other instrument types are excluded.",
            "Underlying aggregation can combine multiple expiries; inspect contract-level rows for expiry detail.",
            "A buildup label is descriptive and is not a standalone trade recommendation.",
            "No prediction accuracy is claimed without historical walk-forward validation."
        ]
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    parser.add_argument("--previous-input", help="Normalized prior NSE trading-session F&O report for legacy rows without previous close")
    args = parser.parse_args()
    source = Path(args.input)
    output = Path(args.output or f"data/analysis/{source.parent.name}/futures_buildup_intelligence.json")
    result = run(source, output, Path(args.previous_input) if args.previous_input else None)
    print(json.dumps({
        "status": result["status"], "trading_date": result["trading_date"],
        "source_rows": result["source_rows"],
        "valid_future_contracts": result["valid_future_contracts"],
        "classification_counts": result["classification_counts"],
        "underlyings": len(result["underlyings"]), "output": str(output)
    }, indent=2))
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
