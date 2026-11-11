#!/usr/bin/env python3
"""Normalize validated NSE daily CSV reports without inventing or inferring market data."""
from __future__ import annotations
import argparse, csv, json, re, sys
from datetime import datetime
from pathlib import Path

REPORTS = {
    "equity_udiff": {"required": {"TRADDT", "TCKRSYMB", "CLSPRIC"}},
    "fo_udiff": {"required": {"TRADDT", "TCKRSYMB", "CLSPRIC"}},
    "participant_oi": {"required": {"CLIENTTYPE", "FUTUREINDEXLONG", "TOTALSHORTCONTRACTS"}},
    "security_delivery": {"required": {"SYMBOL", "DELIVQTY", "DELIVPER"}},
}
ALIASES = {
    "CLIENT TYPE": "CLIENTTYPE", "CLIENT_TYPE": "CLIENTTYPE",
    "FUTURE INDEX LONG": "FUTUREINDEXLONG", "FUTURE_INDEX_LONG": "FUTUREINDEXLONG",
    "TOTAL SHORT CONTRACTS": "TOTALSHORTCONTRACTS", "TOTAL_SHORT_CONTRACTS": "TOTALSHORTCONTRACTS",
    "DELIV QTY": "DELIVQTY", "DELIV PER": "DELIVPER",
}
def norm_col(value: str) -> str:
    raw = value.strip().upper()
    return ALIASES.get(raw, re.sub(r"[^A-Z0-9]", "", raw))
def iso_date(value: str) -> str:
    value = value.strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try: return datetime.strptime(value[:10], fmt).date().isoformat()
        except ValueError: pass
    return value

def read_report(path: Path, report: str) -> dict:
    if report not in REPORTS:
        raise ValueError(f"Unknown report type: {report}")
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        sample = f.read(8192); f.seek(0)
        try: dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error: dialect = csv.excel
        rows = list(csv.reader(f, dialect))
    header_i = None
    required = REPORTS[report]["required"]
    for i, row in enumerate(rows[:15]):
        normalized = {norm_col(c) for c in row if c.strip()}
        if required.issubset(normalized):
            header_i = i; break
    if header_i is None:
        raise ValueError(f"{report}: required header not found in first 15 rows")
    original_header = [c.strip() for c in rows[header_i]]
    keys = [norm_col(c) for c in original_header]
    records = []
    for row_num, row in enumerate(rows[header_i+1:], start=header_i+2):
        if not any(c.strip() for c in row): continue
        if len(row) > len(keys):
            raise ValueError(f"{report}: row {row_num} has {len(row)} cells; header has {len(keys)}")
        row = row + [""] * (len(keys)-len(row))
        records.append({k: v.strip() for k, v in zip(keys, row) if k})
    if not records:
        raise ValueError(f"{report}: no data rows")
    dates = sorted({iso_date(v) for r in records if (v := (r.get("TRADDT") or r.get("DATE1") or ""))})
    return {"report": report, "source_file": path.name, "header_row": header_i+1,
            "columns": keys, "row_count": len(records), "trading_dates": dates,
            "records": records}
def normalize_day(root: Path, output: Path, date: str) -> dict:
    results, failures = {}, {}
    for report in REPORTS:
        folder = root / date / report
        candidates = sorted(folder.glob("*.csv")) if folder.exists() else []
        if not candidates:
            raw_folder = root.parent / "raw" / date / report
            candidates = sorted(raw_folder.glob("*.csv")) if raw_folder.exists() else []
        if not candidates:
            failures[report] = f"No extracted CSV found under {folder}"
            continue
        try:
            parsed = [read_report(p, report) for p in candidates]
            results[report] = parsed
        except Exception as exc:
            failures[report] = str(exc)
    all_dates = sorted({d for group in results.values() for item in group for d in item["trading_dates"]})
    status = "passed" if not failures and len(results) == len(REPORTS) else "failed"
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "agent": "data_normalization", "status": status,
               "requested_date": date, "observed_trading_dates": all_dates,
               "date_consistency": "passed" if len(all_dates) <= 1 else "failed",
               "reports": results, "failures": failures,
               "disclaimer": "This is normalized reported EOD data, not live or intraday data."}
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if status != "passed" or payload["date_consistency"] != "passed":
        raise ValueError(f"Normalization failed: {json.dumps(failures)}; dates={all_dates}")
    return payload
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="NSE trading date YYYY-MM-DD")
    ap.add_argument("--input", default="data/extracted")
    ap.add_argument("--output", default=None)
    args = ap.parse_args()
    datetime.strptime(args.date, "%Y-%m-%d")
    output = Path(args.output or f"data/normalized/{args.date}/normalized.json")
    result = normalize_day(Path(args.input), output, args.date)
    print(json.dumps({"status": result["status"], "reports": len(result["reports"]),
                      "rows": {k: sum(x["row_count"] for x in v) for k,v in result["reports"].items()},
                      "date_consistency": result["date_consistency"], "output": str(output)}, indent=2))
    return 0
if __name__ == "__main__":
    try: raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr); raise SystemExit(1)
