#!/usr/bin/env python3
"""Agent 2: participant-wise F&O positioning intelligence from normalized NSE EOD data.

No intraday inference, fabricated values, or predictive accuracy claims are produced.
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path
from typing import Any

PARTICIPANTS = {"FII", "DII", "CLIENT", "PRO"}
ALIASES = {
    "FII": "FII", "DII": "DII", "CLIENT": "CLIENT", "CLIENTS": "CLIENT",
    "PRO": "PRO", "PROPRIETARY": "PRO",
}
NUMERIC = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")

def number(value: Any) -> float | None:
    if value is None: return None
    raw = str(value).strip().replace(",", "").replace("%", "")
    if raw in {"", "-", "--", "NA", "N/A", "NULL"}: return None
    if not NUMERIC.match(raw): return None
    return float(raw)

def keymap(record: dict) -> dict[str, Any]:
    return {re.sub(r"[^A-Z0-9]", "", str(k).upper()): v for k, v in record.items()}

def first_num(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in row:
            parsed = number(row[key])
            if parsed is not None: return parsed
    return None

def participant_name(value: Any) -> str | None:
    raw = re.sub(r"[^A-Z]", "", str(value or "").upper())
    return ALIASES.get(raw)

def analyze_report(report_files: list[dict]) -> dict:
    rows = []
    for file in report_files:
        rows.extend(file.get("records", []))
    if not rows:
        raise ValueError("Participant OI report contains no records")
    aggregated: dict[str, dict[str, float]] = {}
    ignored = []
    for raw in rows:
        row = keymap(raw)
        name = participant_name(row.get("CLIENTTYPE") or row.get("PARTICIPANTTYPE") or row.get("CLIENTCATEGORY"))
        if not name:
            ignored.append(str(row.get("CLIENTTYPE") or "unknown"))
            continue
        values = {
            "future_index_long": first_num(row, "FUTUREINDEXLONG", "FUTIDXLONG"),
            "future_index_short": first_num(row, "FUTUREINDEXSHORT", "FUTIDXSHORT"),
            "future_stock_long": first_num(row, "FUTURESTOCKLONG", "FUTSTKLONG"),
            "future_stock_short": first_num(row, "FUTURESTOCKSHORT", "FUTSTKSHORT"),
            "option_index_call_long": first_num(row, "OPTIONINDEXCALLLONG", "OPTIDXCALLLONG"),
            "option_index_put_long": first_num(row, "OPTIONINDEXPUTLONG", "OPTIDXPUTLONG"),
            "option_index_call_short": first_num(row, "OPTIONINDEXCALLSHORT", "OPTIDXCALLSHORT"),
            "option_index_put_short": first_num(row, "OPTIONINDEXPUTSHORT", "OPTIDXPUTSHORT"),
            "total_long_contracts": first_num(row, "TOTALLONGCONTRACTS"),
            "total_short_contracts": first_num(row, "TOTALSHORTCONTRACTS"),
        }
        # Some NSE report variants use a generic total long/short label.
        if values["total_long_contracts"] is None:
            values["total_long_contracts"] = first_num(row, "TOTALLONG")
        if values["total_short_contracts"] is None:
            values["total_short_contracts"] = first_num(row, "TOTALSHORT")
        aggregated[name] = {k: v for k, v in values.items() if v is not None}
    if not aggregated:
        raise ValueError("No recognized FII/DII/CLIENT/PRO participant rows found")
    output = {}
    for name, v in aggregated.items():
        f_l, f_s = v.get("future_index_long"), v.get("future_index_short")
        call_l, put_l = v.get("option_index_call_long"), v.get("option_index_put_long")
        call_s, put_s = v.get("option_index_call_short"), v.get("option_index_put_short")
        future_net = f_l - f_s if f_l is not None and f_s is not None else None
        # Positive means net bullish option positioning; negative means net bearish.
        option_net = (call_l + put_s - call_s - put_l
                      if all(x is not None for x in (call_l, put_l, call_s, put_s)) else None)
        output[name] = {
            **v,
            "index_futures_net_long": future_net,
            "index_options_directional_net": option_net,
            "futures_bias": bias(future_net),
            "options_bias": bias(option_net),
        }
    return {"participants": output, "unrecognized_rows": ignored}

def bias(value: float | None) -> str:
    if value is None: return "unavailable"
    if value > 0: return "net_long"
    if value < 0: return "net_short"
    return "balanced"

def overall_assessment(participants: dict) -> dict:
    # Participant positions are counterparties and largely offset; summing them
    # would create a misleading market-wide net near zero. Classify each group,
    # then describe whether the participant groups agree or diverge.
    group_bias = {}
    for name, values in participants.items():
        futures = values.get("index_futures_net_long")
        options = values.get("index_options_directional_net")
        signs = [1 if x > 0 else -1 if x < 0 else 0 for x in (futures, options) if x is not None]
        if not signs:
            group_bias[name] = "unavailable"
        elif all(x > 0 for x in signs):
            group_bias[name] = "bullish"
        elif all(x < 0 for x in signs):
            group_bias[name] = "bearish"
        else:
            group_bias[name] = "mixed"
    available = [v for v in group_bias.values() if v != "unavailable"]
    if not available:
        label = "insufficient_data"
    elif all(v == "bullish" for v in available):
        label = "bullish_consensus"
    elif all(v == "bearish" for v in available):
        label = "bearish_consensus"
    else:
        label = "mixed_participant_positioning"
    return {
        "classification": label,
        "participant_bias": group_bias,
        "method": "Classifies each participant group from its own index-futures and directional index-options net positions; reports consensus only when all available groups agree. Participant totals are not added because counterpart positions offset."
    }

def load_normalized(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != "passed" or payload.get("date_consistency") != "passed":
        raise ValueError(f"Input normalization status/date check not passed: {path}")
    files = payload.get("reports", {}).get("participant_oi", [])
    if not files:
        raise ValueError(f"participant_oi missing from normalized input: {path}")
    return payload

def find_previous(root: Path, current_date: str) -> Path | None:
    # Prefer persisted Agent 2 snapshots so comparison survives ephemeral GitHub runners.
    candidates = []
    for path in root.glob("*/participant_oi_intelligence.json"):
        day = path.parent.name
        if day >= current_date: continue
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            if snapshot.get("agent") == "participant_oi_intelligence" and snapshot.get("status") == "passed":
                candidates.append((day, path))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(candidates, key=lambda x: x[0])[-1][1] if candidates else None

def run(input_path: Path, output_path: Path, history_root: Path | None = None) -> dict:
    payload = load_normalized(input_path)
    current = analyze_report(payload["reports"]["participant_oi"])
    previous_path = find_previous(history_root, payload["requested_date"]) if history_root else None
    previous_snapshot = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path else None
    previous = previous_snapshot.get("participants", {}) if previous_snapshot else {}
    changes = {}
    if previous:
        for participant, current_values in current["participants"].items():
            old = previous.get(participant, {})
            changes[participant] = {}
            for key in ("index_futures_net_long", "index_options_directional_net"):
                now, before = current_values.get(key), old.get(key)
                changes[participant][key] = now - before if now is not None and before is not None else None
    result = {
        "schema_version": 1, "agent": "participant_oi_intelligence",
        "status": "passed", "data_scope": "EOD participant positioning only",
        "trading_date": payload["requested_date"],
        "comparison_date": previous_path.parent.name if previous_path else None,
        "participants": current["participants"],
        "participant_changes_vs_previous_day": changes if previous else None,
        "overall_assessment": overall_assessment(current["participants"]),
        "unrecognized_rows": current["unrecognized_rows"],
        "limitations": [
            "Participant OI is an end-of-day snapshot, not intraday order flow.",
            "Net positioning is descriptive and is not a standalone trading signal.",
            "Missing source columns remain unavailable; they are not estimated.",
            "No prediction accuracy is claimed without separate historical walk-forward validation."
        ]
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", default=None)
    ap.add_argument("--history-root", default="data/normalized")
    args = ap.parse_args()
    source = Path(args.input)
    out = Path(args.output or f"data/analysis/{source.parent.name}/participant_oi_intelligence.json")
    result = run(source, out, Path(args.history_root))
    print(json.dumps({"status": result["status"], "trading_date": result["trading_date"],
                      "comparison_date": result["comparison_date"],
                      "participants": sorted(result["participants"]),
                      "overall_assessment": result["overall_assessment"]["classification"],
                      "output": str(out)}, indent=2))
    return 0

if __name__ == "__main__":
    try: raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr); raise SystemExit(1)
