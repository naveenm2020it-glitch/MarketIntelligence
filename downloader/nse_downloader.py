#!/usr/bin/env python3
"""Download and validate selected NSE daily reports through NSE's daily-reports API."""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from datetime import timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from zoneinfo import ZoneInfo

# Direct execution sets sys.path[0] to downloader/, not the repository root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

RAW = Path("data/raw")
EXTRACTED = Path("data/extracted")
LOGS = Path("logs")
HISTORY = Path("data/history/manifest.json")
IST = ZoneInfo("Asia/Kolkata")
TODAY = datetime.now(IST).date().isoformat()
NSE = "https://www.nseindia.com"
ARCHIVE_HOST = "https://nsearchives.nseindia.com"

REPORTS = {
    "equity_udiff": {
        "segment": "CM",
        "match": lambda key, name: "udiff" in key.lower() and "bhavcopy" in key.lower() and "nse_cm" in name.lower(),
    },
    "fo_udiff": {
        "segment": "FO",
        "match": lambda key, name: "udiff" in key.lower() and "bhavcopy" in key.lower() and "nse_fo" in name.lower(),
    },
    "participant_oi": {
        "segment": "FO",
        "match": lambda key, name: "participant" in key.lower() and "open interest" in key.lower() or "participant_oi" in name.lower(),
    },
    "security_delivery": {
        "segment": "CM",
        "match": lambda key, name: "full bhavcopy and security deliverable" in key.lower() or "sec_bhavdata_full" in name.lower(),
    },
}

def setup_logging() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOGS / f"nse_download_{TODAY}.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)

def parse_date(value: str) -> str:
    if not value:
        return ""
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value[:10], fmt).date().isoformat()
        except ValueError:
            pass
    return value

def report_storage_date(item: dict, fallback: str = TODAY) -> str:
    """Use the NSE catalogue trading date for storage, never the runner calendar date."""
    return parse_date(str(item.get("tradingDate", ""))) or fallback

def collect_entries(payload):
    entries = []
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list):
                entries.extend(x for x in value if isinstance(x, dict))
            elif isinstance(value, dict):
                entries.extend(collect_entries(value))
    elif isinstance(payload, list):
        entries.extend(x for x in payload if isinstance(x, dict))
    return [x for x in entries if x.get("fileActlName") and x.get("filePath")]

def get_catalogue(session: requests.Session, segment: str) -> list[dict]:
    url = f"{NSE}/api/daily-reports"
    response = session.get(url, params={"key": segment}, timeout=45)
    response.raise_for_status()
    payload = response.json()
    entries = collect_entries(payload)
    if not entries:
        raise ValueError(f"NSE daily-reports API returned no file entries for segment {segment}: {str(payload)[:500]}")
    return entries

def find_report(entries: list[dict], report_key: str) -> dict:
    rule = REPORTS[report_key]["match"]
    matches = []
    for item in entries:
        key = str(item.get("fileKey", ""))
        name = str(item.get("fileActlName", ""))
        if rule(key, name):
            item_date = parse_date(str(item.get("tradingDate", "")))
            matches.append((item_date, item))
    if not matches:
        raise ValueError(f"No matching report entry found in NSE daily-reports API for {report_key}")
    matches.sort(key=lambda pair: pair[0], reverse=True)
    return matches[0][1]

def validate_csv(path: Path, report_key: str) -> dict:
    # Use the same report-aware parser as normalization so legacy and UDiFF
    # files are validated against one canonical header contract.
    from agents.data_normalization import read_report
    parsed = read_report(path, report_key)
    logging.info("CSV validation passed: %s; rows=%d; header_row=%d",
                 path, parsed["row_count"], parsed["header_row"])
    return {
        "csv": str(path),
        "columns": len(parsed["columns"]),
        "data_rows": parsed["row_count"],
        "header_row": parsed["header_row"],
        "header": parsed["columns"],
        "trading_dates": parsed["trading_dates"],
    }

def record_history(report_key: str, item: dict, result: dict) -> bool:
    """Persist a checksum-based observation so duplicates are detected across ephemeral runners."""
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    if HISTORY.exists():
        history = json.loads(HISTORY.read_text(encoding="utf-8"))
    else:
        history = {"schema_version": 1, "description": "NSE report observation history; raw files remain in Actions artifacts", "records": []}

    trading_date = parse_date(str(item.get("tradingDate", ""))) or TODAY
    identity = (report_key, trading_date, result["sha256"])
    existing = next((row for row in history["records"]
                     if (row.get("report"), row.get("trading_date"), row.get("sha256")) == identity), None)
    now = datetime.now(timezone.utc).isoformat()
    if existing:
        existing["seen_count"] = int(existing.get("seen_count", 1)) + 1
        existing["last_seen_utc"] = now
        duplicate = True
    else:
        history["records"].append({
            "report": report_key,
            "trading_date": trading_date,
            "file_key": item.get("fileKey"),
            "filename": result["filename"],
            "bytes": result["bytes"],
            "sha256": result["sha256"],
            "first_seen_utc": now,
            "last_seen_utc": now,
            "seen_count": 1,
        })
        duplicate = False

    history["updated_at_utc"] = now
    HISTORY.write_text(json.dumps(history, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    logging.info("%s across workflow runs: %s / %s / %s",
                 "DUPLICATE CONFIRMED" if duplicate else "NEW HISTORICAL REPORT",
                 report_key, trading_date, result["sha256"])
    return duplicate


def download_report(session: requests.Session, report_key: str, item: dict) -> dict:
    filename = safe_name(str(item["fileActlName"]))
    file_path = str(item["filePath"])
    url = urljoin(file_path.rstrip("/") + "/", filename)
    # Preserve the actual NSE filename, not the sanitized local name, in the request.
    url = urljoin(file_path.rstrip("/") + "/", str(item["fileActlName"]))
    report_date = report_storage_date(item)
    folder = RAW / report_date / report_key
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    local_reuse = target.exists() and target.stat().st_size > 0
    if local_reuse:
        payload = target.read_bytes()
        logging.info("Same-run local file already exists; validating it: %s", target)
    else:
        logging.info("Downloading %s (%s) from %s", report_key, item.get("fileKey"), url)
        response = session.get(url, timeout=120)
        response.raise_for_status()
        payload = response.content
        if len(payload) < 100 or payload.lstrip().lower().startswith((b"<html", b"<!doctype")):
            raise ValueError(f"{report_key}: NSE response is too small or HTML, not a report file")
        target.write_bytes(payload)

    result = {
        "report": report_key,
        "file_key": item.get("fileKey"),
        "trading_date": item.get("tradingDate"),
        "trading_date_iso": report_date,
        "filename": str(item["fileActlName"]),
        "saved_as": str(target),
        "download_url": url,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "duplicate": False,
        "local_reuse": local_reuse,
    }

    if payload[:4] == b"PK\x03\x04" or target.suffix.lower() == ".zip":
        with zipfile.ZipFile(target) as archive:
            bad_member = archive.testzip()
            if bad_member:
                raise ValueError(f"{report_key}: corrupt ZIP member {bad_member}")
            csv_members = [member for member in archive.namelist() if member.lower().endswith(".csv")]
            if not csv_members:
                raise ValueError(f"{report_key}: ZIP has no CSV member")
            out_dir = EXTRACTED / report_date / report_key
            out_dir.mkdir(parents=True, exist_ok=True)
            validations = []
            for member in csv_members:
                # Prevent ZIP path traversal; write only the basename into the report folder.
                extracted_path = out_dir / safe_name(Path(member).name)
                extracted_path.write_bytes(archive.read(member))
                validations.append(validate_csv(extracted_path, report_key))
            result["zip_integrity"] = "passed"
            result["extracted_csvs"] = validations
    else:
        result["csv_validation"] = validate_csv(target, report_key)
    result["duplicate"] = record_history(report_key, item, result)
    logging.info("REPORT VERIFIED: %s (%d bytes)", report_key, len(payload))
    return result

def main() -> int:
    setup_logging()
    summary = {
        "date_ist": TODAY,
        "source": "NSE official daily-reports API",
        "reports": {},
        "failures": {},
        "history_manifest": str(HISTORY),
        "retry_policy": {"max_retries": 5, "backoff_factor_seconds": 1.5, "retry_statuses": [429, 500, 502, 503, 504]},
        "report_trading_dates": {},
        "missing_reports": [],
        "trading_date_consistency": "not_checked",
    }
    session = requests.Session()
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    logging.info("HTTP retry policy enabled: up to 5 retries for transient GET failures")
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": NSE + "/all-reports",
    })
    catalogues = {}
    for segment in ("CM", "FO"):
        try:
            catalogues[segment] = get_catalogue(session, segment)
            logging.info("NSE API catalogue %s: %d report entries", segment, len(catalogues[segment]))
        except Exception as exc:
            logging.exception("Failed to read NSE API catalogue %s", segment)
            summary["failures"][f"catalogue_{segment}"] = str(exc)

    for report_key, config in REPORTS.items():
        segment = config["segment"]
        try:
            if segment not in catalogues:
                raise ValueError(f"NSE {segment} catalogue unavailable")
            item = find_report(catalogues[segment], report_key)
            report_date = parse_date(str(item.get("tradingDate", "")))
            if not report_date:
                raise ValueError(f"{report_key}: NSE catalogue entry has no usable tradingDate")
            summary["report_trading_dates"][report_key] = report_date
            summary["reports"][report_key] = download_report(session, report_key, item)
        except Exception as exc:
            summary["failures"][report_key] = str(exc)
            logging.exception("Failed report %s", report_key)

    expected = set(REPORTS)
    missing = sorted(expected - set(summary["reports"]))
    summary["missing_reports"] = missing
    observed_dates = sorted(set(summary["report_trading_dates"].values()))
    if missing:
        summary["failures"]["missing_reports"] = ", ".join(missing)
    if len(observed_dates) == 1 and not missing:
        summary["trading_date_consistency"] = "passed"
    elif len(observed_dates) > 1:
        summary["trading_date_consistency"] = "failed"
        summary["failures"]["trading_date_consistency"] = "Report trading dates differ: " + ", ".join(observed_dates)
    else:
        summary["trading_date_consistency"] = "incomplete"

    LOGS.mkdir(parents=True, exist_ok=True)
    summary_path = LOGS / f"nse_summary_{TODAY}.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 1 if summary["failures"] else 0

if __name__ == "__main__":
    raise SystemExit(main())
