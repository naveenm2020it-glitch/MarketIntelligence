#!/usr/bin/env python3
"""Discover and validate daily NSE reports from NSE's official report pages."""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = Path("data")
RAW = BASE / "raw"
EXTRACTED = BASE / "extracted"
LOGS = Path("logs")
IST = __import__("zoneinfo").ZoneInfo("Asia/Kolkata")
TODAY = datetime.now(IST).date().isoformat()

REPORT_PAGES = [
    "https://www.nseindia.com/all-reports",
    "https://www.nseindia.com/all-reports-derivatives",
]
PREFIXES = {
    "equity_udiff": ("BhavCopy_NSE_CM_", ".csv.zip"),
    "fo_udiff": ("BhavCopy_NSE_FO_", ".csv.zip"),
    "participant_oi": ("fao_participant_oi_", ".csv"),
    "security_delivery": ("sec_bhavdata_full_", ".csv"),
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

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/",
        "Connection": "keep-alive",
    })
    # Establish NSE session cookies before requesting report pages/files.
    s.get("https://www.nseindia.com/", timeout=30)
    return s

def discover(session: requests.Session) -> dict[str, list[dict[str, str]]]:
    found = {k: [] for k in PREFIXES}
    for page in REPORT_PAGES:
        logging.info("Reading official report catalogue: %s", page)
        response = session.get(page, timeout=45)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(page, a["href"])
            filename = Path(urlparse(href).path).name
            for key, (prefix, suffix) in PREFIXES.items():
                if filename.startswith(prefix) and filename.lower().endswith(suffix):
                    if not any(x["url"] == href for x in found[key]):
                        found[key].append({"url": href, "filename": filename, "label": a.get_text(" ", strip=True)})
    for key in found:
        found[key].sort(key=lambda x: x["filename"], reverse=True)
    return found

def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)

def download_one(session: requests.Session, key: str, item: dict[str, str]) -> dict:
    dest_dir = RAW / TODAY
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_name(item["filename"])
    target = dest_dir / filename
    if target.exists() and target.stat().st_size > 0:
        logging.info("Duplicate detected; existing file retained: %s", target)
        payload = target.read_bytes()
    else:
        logging.info("Downloading %s from %s", key, item["url"])
        r = session.get(item["url"], timeout=90, stream=True)
        r.raise_for_status()
        chunks = []
        size = 0
        for chunk in r.iter_content(1024 * 128):
            if chunk:
                chunks.append(chunk)
                size += len(chunk)
        payload = b"".join(chunks)
        if size < 100:
            raise ValueError(f"{key}: response too small ({size} bytes); likely an NSE error page")
        if b"<html" in payload[:500].lower() or b"access denied" in payload[:2000].lower():
            raise ValueError(f"{key}: received HTML/access-denied page instead of report")
        target.write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()
    result = {"key": key, "file": str(target), "bytes": len(payload), "sha256": sha, "url": item["url"], "duplicate": target.exists() and len(payload) > 0}
    if filename.lower().endswith(".zip"):
        with zipfile.ZipFile(target) as zf:
            bad = zf.testzip()
            if bad:
                raise ValueError(f"{key}: corrupt ZIP member {bad}")
            members = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not members:
                raise ValueError(f"{key}: ZIP contains no CSV member")
            out = EXTRACTED / TODAY / key
            out.mkdir(parents=True, exist_ok=True)
            zf.extractall(out)
            for member in members:
                validate_csv(out / member, key)
            result["extracted"] = [str(out / m) for m in members]
            result["zip_test"] = "passed"
    else:
        validate_csv(target, key)
    logging.info("VALIDATED %s (%s bytes)", key, len(payload))
    return result

def validate_csv(path: Path, key: str) -> None:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header or len([x for x in header if x.strip()]) < 2:
            raise ValueError(f"{key}: CSV has missing/invalid header: {path}")
        rows = 0
        for row in reader:
            if any(cell.strip() for cell in row):
                rows += 1
                if rows >= 2:
                    break
        if rows == 0:
            raise ValueError(f"{key}: CSV has header but no data rows: {path}")
    logging.info("CSV validation passed: %s (header columns=%d)", path, len(header))

def main() -> int:
    setup_logging()
    summary = {"date_ist": TODAY, "source": "NSE official reports catalogue", "reports": {}, "failures": {}}
    try:
        session = make_session()
        candidates = discover(session)
        for key, items in candidates.items():
            if not items:
                summary["failures"][key] = "No matching downloadable link found in official NSE report catalogue"
                logging.error(summary["failures"][key] + ": " + key)
                continue
            # Catalogue is ordered newest filename first; attempt newest available.
            try:
                summary["reports"][key] = download_one(session, key, items[0])
            except Exception as exc:
                summary["failures"][key] = str(exc)
                logging.exception("Failed report %s", key)
    except Exception as exc:
        summary["fatal_error"] = str(exc)
        logging.exception("Downloader failed before report processing")
    LOGS.mkdir(parents=True, exist_ok=True)
    out = LOGS / f"nse_summary_{TODAY}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 1 if summary.get("fatal_error") or summary["failures"] else 0

if __name__ == "__main__":
    raise SystemExit(main())
