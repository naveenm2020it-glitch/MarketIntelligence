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
    # NSE replaced legacy CM/FO bhavcopies with UDiFF from 08-Jul-2024.
    # 05-Jul-2024 is the last legacy trading session; 08-Jul-2024 is UDiFF.
    udiff_start = date(2024, 7, 8)
    ymd = day.strftime("%Y%m%d")
    ddmonyyyy = day.strftime("%d%b%Y").upper()
    ddmmyyyy = day.strftime("%d%m%Y")
    if day >= udiff_start:
        equity = f"{BASE}/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip"
        fo = f"{BASE}/content/fo/BhavCopy_NSE_FO_0_0_0_{ymd}_F_0000.csv.zip"
    else:
        month = day.strftime("%b").upper()
        equity = f"https://archives.nseindia.com/content/historical/EQUITIES/{day.year}/{month}/cm{ddmonyyyy}bhav.csv.zip"
        fo = f"https://archives.nseindia.com/content/historical/DERIVATIVES/{day.year}/{month}/fo{ddmonyyyy}bhav.csv.zip"
    return {
        "equity_udiff": equity,
        "fo_udiff": fo,
        "participant_oi": f"{BASE}/content/nsccl/fao_participant_oi_{ddmmyyyy}.csv",
        "security_delivery": f"{BASE}/products/content/sec_bhavdata_full_{ddmmyyyy}.csv",
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

def fetch_previous_fo_payload(day: date, session: requests.Session) -> dict:
    """Fetch the nearest available earlier NSE F&O session; never substitute a settlement field."""
    last_error = None
    for offset in range(1, 11):
        candidate = day - timedelta(days=offset)
        try:
            item = fetch_one(session, candidate, 'fo_udiff', urls(candidate)['fo_udiff'])
            files = [read_report(p, 'fo_udiff') for p in sorted((EXTRACTED / candidate.isoformat() / 'fo_udiff').glob('*.csv'))]
            if files:
                return {'requested_date': candidate.isoformat(), 'reports': {'fo_udiff': files},
                        'source': 'NSE prior-session F&O Bhavcopy', 'download': item}
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f'Could not retrieve a prior NSE F&O session within 10 calendar days: {last_error}')

def needs_prior_session_close(normalized_path: Path) -> bool:
    payload = json.loads(normalized_path.read_text(encoding='utf-8'))
    files = payload.get('reports', {}).get('fo_udiff', [])
    for file in files:
        for raw in file.get('records', []):
            row = {str(k).upper().replace('_', '').replace('-', '').replace(' ', ''): v for k, v in raw.items()}
            instrument = str(row.get('FININSTRMTP') or row.get('INSTRUMENT') or '').strip().upper()
            if instrument in ('FUTIDX', 'FUTSTK', 'IDF', 'STF'):
                previous = row.get('PRVSCLSGPRIC') or row.get('PREVCLOSE') or row.get('PREVIOUSCLOSE')
                if previous is None or str(previous).strip() in ('', '-', '--'):
                    return True
    return False

def run_agent(command: list[str]) -> None:
    subprocess.run(command, check=True)


def run_agent_recorded(result: dict, agent_name: str, command: list[str]) -> None:
    """Run one agent and record its exact completion state in the per-date pilot summary."""
    try:
        run_agent(command)
    except Exception as exc:
        result.setdefault('agent_status', {})[agent_name] = {
            'status': 'failed', 'reason': str(exc)
        }
        raise
    result.setdefault('agent_status', {})[agent_name] = {'status': 'passed'}


def should_compact_snapshots(exact_date_mode: bool) -> bool:
    """Keep full per-agent outputs for bounded exact-date pilots; compact range backfills."""
    return not exact_date_mode


def process_day(day: date, session: requests.Session) -> dict:
    daystr = day.isoformat()
    result = {'date': daystr, 'reports': {}, 'agent_status': {}, 'status': 'failed'}
    try:
        for key, url in urls(day).items():
            result['reports'][key] = fetch_one(session, day, key, url)
        normalized = Path('data/normalized') / daystr / 'normalized.json'
        try:
            normalized_payload = normalize_day(EXTRACTED, normalized, daystr)
        except Exception as exc:
            result['agent_status']['agent1_normalization'] = {
                'status': 'failed', 'reason': str(exc)
            }
            raise
        result['agent_status']['agent1_normalization'] = {
            'status': 'passed',
            'observed_trading_dates': normalized_payload.get('observed_trading_dates', []),
            'date_consistency': normalized_payload.get('date_consistency'),
            'report_rows': {
                key: sum(item.get('row_count', 0) for item in files)
                for key, files in normalized_payload.get('reports', {}).items()
            },
        }
        run_agent_recorded(result, 'agent2_participant_oi', [
            sys.executable, 'agents/participant_oi_intelligence.py',
            '--input', str(normalized), '--history-root', 'data/analysis'
        ])
        run_agent_recorded(result, 'agent3_equity_delivery', [
            sys.executable, 'agents/equity_delivery_strength.py',
            '--input', str(normalized), '--history-root', 'data/analysis'
        ])
        futures_command = [
            sys.executable, 'agents/futures_buildup_intelligence.py',
            '--input', str(normalized)
        ]
        if needs_prior_session_close(normalized):
            try:
                prior_payload = fetch_previous_fo_payload(day, session)
            except Exception as exc:
                result['agent_status']['agent4_futures_buildup'] = {
                    'status': 'failed', 'reason': f'Prior-session same-contract close retrieval failed: {exc}'
                }
                raise
            prior_input = Path('data/normalized') / daystr / 'previous_session_fo.json'
            prior_input.parent.mkdir(parents=True, exist_ok=True)
            prior_input.write_text(
                json.dumps(prior_payload, indent=2, ensure_ascii=False) + chr(10),
                encoding='utf-8'
            )
            futures_command.extend(['--previous-input', str(prior_input)])
        run_agent_recorded(result, 'agent4_futures_buildup', futures_command)
        run_agent_recorded(result, 'agent5_nifty_option_oi', [
            sys.executable, 'agents/nifty_option_oi_intelligence.py',
            '--input', str(normalized)
        ])
        run_agent_recorded(result, 'agent6_eod_market_confluence', [
            sys.executable, 'agents/eod_market_confluence_intelligence.py',
            '--date', daystr, '--analysis-root', 'data/analysis'
        ])
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

def backfill_exit_code(summary: dict, require_all_dates: bool = False) -> int:
    if not summary.get('passed_days'):
        return 1
    if require_all_dates and summary.get('skipped_or_failed_days'):
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    date_mode = ap.add_mutually_exclusive_group(required=True)
    date_mode.add_argument('--dates', help='Comma-separated exact trading dates (YYYY-MM-DD); use for bounded pilot runs')
    date_mode.add_argument('--start-date', help='YYYY-MM-DD inclusive; use with --end-date for continuous backfills')
    ap.add_argument('--end-date', help='YYYY-MM-DD inclusive; required with --start-date')
    ap.add_argument('--minimum-samples', type=int, default=30)
    ap.add_argument('--require-all-dates', action='store_true', help='Fail the run if any requested date fails')
    args = ap.parse_args()
    if args.dates:
        try:
            selected_days = sorted({date.fromisoformat(x.strip()) for x in args.dates.split(',') if x.strip()})
        except ValueError as exc:
            ap.error(f'invalid --dates value: {exc}')
        if not selected_days:
            ap.error('--dates must contain at least one date')
        if len(selected_days) > 10:
            ap.error('--dates accepts at most 10 dates per bounded pilot run')
        if any(x.weekday() >= 5 for x in selected_days):
            ap.error('--dates contains a weekend date; supply NSE trading dates only')
        start_label, end_label = selected_days[0].isoformat(), selected_days[-1].isoformat()
    else:
        if not args.end_date:
            ap.error('--end-date is required with --start-date')
        start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
        if end < start: ap.error('end-date must be on or after start-date')
        selected_days = []
        current = start
        while current <= end:
            if current.weekday() < 5:
                selected_days.append(current)
            current += timedelta(days=1)
        start_label, end_label = args.start_date, args.end_date
    session = make_session()
    summary = {'source': 'NSE public historical archives', 'mode': 'exact_dates' if args.dates else 'date_range',
               'requested_dates': [x.isoformat() for x in selected_days],
               'start_date': start_label, 'end_date': end_label,
               'trading_days_attempted': 0, 'passed_days': [],
               'skipped_or_failed_days': [], 'results': []}
    for current in selected_days:
        summary['trading_days_attempted'] += 1
        item = process_day(current, session)
        summary['results'].append(item)
        if item['status'] == 'passed':
            summary['passed_days'].append(current.isoformat())
        else:
            summary['skipped_or_failed_days'].append({'date': current.isoformat(), 'reason': item.get('error', 'unknown')})
    preserve_full_agent_snapshots = bool(args.dates)
    summary['agent_evidence_retention'] = (
        'full_per_agent_snapshots' if preserve_full_agent_snapshots else 'compact_range_backfill'
    )
    if should_compact_snapshots(preserve_full_agent_snapshots):
        for daystr in summary['passed_days']:
            compact_snapshots(daystr)
    if summary['passed_days']:
        run_agent([sys.executable, 'agents/walk_forward_validation.py', '--analysis-root', 'data/analysis',
                   '--output', 'data/backfill/walk_forward_validation.json',
                   '--minimum-samples', str(args.minimum_samples)])
        validation_path = Path('data/backfill/walk_forward_validation.json')
        validation = json.loads(validation_path.read_text(encoding='utf-8'))
        metrics = validation.get('metrics', {})
        summary['agent7_validation'] = {
            'status': validation.get('status'),
            'samples': metrics.get('samples'),
            'minimum_samples_required': validation.get('minimum_samples_required'),
            'accuracy': metrics.get('accuracy'),
            'balanced_accuracy': metrics.get('balanced_accuracy'),
            'macro_f1': metrics.get('macro_f1'),
            'directional_accuracy': metrics.get('directional_accuracy'),
            'confusion_matrix': metrics.get('confusion_matrix_actual_rows_predicted_columns'),
            'by_agent6_confidence_grade': metrics.get('by_agent6_confidence_grade'),
            'target_definition': validation.get('target_definition'),
        }
    else:
        summary['agent7_validation'] = {
            'status': 'not_run', 'samples': 0,
            'minimum_samples_required': args.minimum_samples,
            'reason': 'No requested trading date completed Agents 1–6 successfully.'
        }
    out = Path('data/backfill/backfill_summary.json')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'attempted_weekdays': summary['trading_days_attempted'],
                      'valid_agent_days': len(summary['passed_days']),
                      'skipped_or_failed': len(summary['skipped_or_failed_days']),
                      'validation_report': 'data/backfill/walk_forward_validation.json',
                      'summary': str(out)}, indent=2))
    return backfill_exit_code(summary, args.require_all_dates)

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try: raise SystemExit(main())
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1)