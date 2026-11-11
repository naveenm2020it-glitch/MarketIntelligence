#!/usr/bin/env python3
"""Backfill NSE's public historical EOD archives and run the existing agents chronologically."""
from __future__ import annotations
import argparse, csv, json, logging, subprocess, sys, zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

# Ensure direct script execution can import sibling top-level packages (agents, downloader).
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from urllib.parse import urlparse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from agents.data_normalization import normalize_day, read_report
from downloader.nse_downloader import validate_csv

BASE = 'https://nsearchives.nseindia.com'
RAW = Path('data/raw')
EXTRACTED = Path('data/extracted')
ANALYSIS = Path('data/analysis')
REPORTS = ('equity_udiff', 'fo_udiff', 'participant_oi', 'security_delivery')
HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128 Safari/537.36', 'Accept': '*/*', 'Referer': 'https://www.nseindia.com/all-reports'}

def urls(day: date) -> dict[str, str]:
    ymd, ddmmyyyy = day.strftime('%Y%m%d'), day.strftime('%d%m%Y')
    return {
        'equity_udiff': f'{BASE}/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip',
        'fo_udiff': f'{BASE}/content/fo/BhavCopy_NSE_FO_0_0_0_{ymd}_F_0000.csv.zip',
        'participant_oi': f'{BASE}/content/nsccl/fao_participant_oi_{ddmmyyyy}.csv',
        'security_delivery': f'{BASE}/products/content/sec_bhavdata_full_{ddmmyyyy}.csv',
    }

def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, connect=4, read=4, status=4, backoff_factor=1.2,
                  status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset(['GET']),
                  respect_retry_after_header=True)
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    s.mount('https://', adapter)
    s.headers.update(HEADERS)
    return s

def fetch_one(session: requests.Session, day: date, key: str, url: str) -> dict:
    folder = RAW / day.isoformat() / key
    outdir = EXTRACTED / day.isoformat() / key
    folder.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    suffix = '.zip' if url.endswith('.zip') else '.csv'
    target = folder / (key + suffix)
    response = session.get(url, timeout=90)
    if response.status_code == 404:
        raise FileNotFoundError('NSE archive not found (404)')
    response.raise_for_status()
    payload = response.content
    if len(payload) < 100 or payload.lstrip().lower().startswith((b'<html', b'<!doctype')):
        raise ValueError('NSE returned HTML or an undersized response, not a data file')
    target.write_bytes(payload)
    validations = []
    if suffix == '.zip':
        with zipfile.ZipFile(target) as z:
            bad = z.testzip()
            if bad: raise ValueError(f'Corrupt ZIP member: {bad}')
            members = [n for n in z.namelist() if n.lower().endswith('.csv')]
            if not members: raise ValueError('ZIP contains no CSV')
            for member in members:
                dest = outdir / Path(member).name
                dest.write_bytes(z.read(member))
                validations.append(validate_csv(dest, key))
    else:
        dest = outdir / target.name
        dest.write_bytes(payload)
        validations.append(validate_csv(dest, key))
    # Verify report dates inside the data where the report provides them.
    for file in sorted(outdir.glob('*.csv')):
        parsed = read_report(file, key)
        observed = parsed.get('trading_dates', [])
        if key in ('equity_udiff', 'fo_udiff', 'security_delivery') and observed and observed != [day.isoformat()]:
            raise ValueError(f'Embedded trading date mismatch: expected {day.isoformat()}, found {observed}')
    return {'report': key, 'url': url, 'bytes': len(payload), 'saved_as': str(target),
            'sha256': __import__('hashlib').sha256(payload).hexdigest(),
            'validation': 'passed', 'csv_files': len(validations),
            'rows': sum(x['data_rows'] for x in validations)}

def run_agent(command: list[str]) -> None:
    subprocess.run(command, check=True)

def process_day(day: date, session: requests.Session) -> dict:
    daystr = day.isoformat()
    result = {'date': daystr, 'reports': {}, 'status': 'failed'}
    try:
        for key, url in urls(day).items():
            result['reports'][key] = fetch_one(session, day, key, url)
        normalized = Path('data/normalized') / daystr / 'normalized.json'
        normalize_day(EXTRACTED, normalized, daystr)
        run_agent([sys.executable, 'agents/participant_oi_intelligence.py', '--input', str(normalized), '--history-root', 'data/analysis'])
        run_agent([sys.executable, 'agents/equity_delivery_strength.py', '--input', str(normalized), '--history-root', 'data/analysis'])
        run_agent([sys.executable, 'agents/futures_buildup_intelligence.py', '--input', str(normalized)])
        run_agent([sys.executable, 'agents/nifty_option_oi_intelligence.py', '--input', str(normalized)])
        run_agent([sys.executable, 'agents/eod_market_confluence_intelligence.py', '--date', daystr, '--analysis-root', 'data/analysis'])
        result['status'] = 'passed'
    except Exception as exc:
        result['error'] = str(exc)
        logging.exception('Backfill failed for %s', daystr)
    return result

def compact_snapshots(day: str) -> None:
    folder = ANALYSIS / day
    future = folder / 'futures_buildup_intelligence.json'
    if future.exists():
        payload = json.loads(future.read_text(encoding='utf-8'))
        payload['contracts'] = [x for x in payload.get('contracts', [])
                                if x.get('symbol') == 'NIFTY' and x.get('instrument_type') == 'FUTIDX']
        payload['underlyings'] = [x for x in payload.get('underlyings', [])
                                  if x.get('symbol') == 'NIFTY' and x.get('instrument_type') == 'FUTIDX']
        future.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    # Agent 6 contains the combined daily evidence. Keep its snapshot and the
    # compact NIFTY futures contracts required by Agent 7; discard bulky intermediates.
    for name in ('participant_oi_intelligence.json', 'delivery_strength.json', 'nifty_option_oi_intelligence.json'):
        p = folder / name
        if p.exists(): p.unlink()

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--start-date', required=True, help='YYYY-MM-DD inclusive')
    ap.add_argument('--end-date', required=True, help='YYYY-MM-DD inclusive')
    ap.add_argument('--minimum-samples', type=int, default=30)
    args = ap.parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    if end < start: ap.error('end-date must be on or after start-date')
    session = make_session()
    summary = {'source': 'NSE public historical archives', 'start_date': args.start_date,
               'end_date': args.end_date, 'trading_days_attempted': 0, 'passed_days': [],
               'skipped_or_failed_days': [], 'results': []}
    current = start
    while current <= end:
        if current.weekday() < 5:
            summary['trading_days_attempted'] += 1
            item = process_day(current, session)
            summary['results'].append(item)
            if item['status'] == 'passed':
                summary['passed_days'].append(current.isoformat())
            else:
                summary['skipped_or_failed_days'].append({'date': current.isoformat(), 'reason': item.get('error', 'unknown')})
        current += timedelta(days=1)
    for daystr in summary['passed_days']: compact_snapshots(daystr)
    if summary['passed_days']:
        run_agent([sys.executable, 'agents/walk_forward_validation.py', '--analysis-root', 'data/analysis',
                   '--output', 'data/backfill/walk_forward_validation.json',
                   '--minimum-samples', str(args.minimum_samples)])
    out = Path('data/backfill/backfill_summary.json')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'attempted_weekdays': summary['trading_days_attempted'],
                      'valid_agent_days': len(summary['passed_days']),
                      'skipped_or_failed': len(summary['skipped_or_failed_days']),
                      'validation_report': 'data/backfill/walk_forward_validation.json',
                      'summary': str(out)}, indent=2))
    return 0 if summary['passed_days'] else 1

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try: raise SystemExit(main())
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1)