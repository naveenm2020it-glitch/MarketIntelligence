import csv
import json
import tempfile
import unittest
from pathlib import Path

from agents.daily_prediction_audit import run


class DailyPredictionAuditTests(unittest.TestCase):
    def write_json(self, path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_settled_pairs_baseline_and_pending_signal_are_persisted_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            analysis = root / "analysis"
            report_path = root / "validation.json"
            output = root / "audit.json"
            csv_output = root / "audit.csv"
            report = {
                "status": "passed",
                "validation_type": "chronological_next_archived_session_out_of_sample",
                "target_definition": {"instrument": "NIFTY FUTIDX"},
                "sample_pairs": [
                    {"signal_date": "2026-11-05", "target_date": "2026-11-06",
                     "prediction": "bullish", "actual": "bullish", "target_return_percent": 0.2,
                     "nifty_futidx_expiry": "2026-11-26", "signal_day_close": 25000,
                     "target_day_close": 25050, "calendar_gap_days": 1,
                     "agent6_confidence": "moderate", "agent6_confidence_score": 45},
                    {"signal_date": "2026-11-06", "target_date": "2026-11-09",
                     "prediction": "bearish", "actual": "bullish", "target_return_percent": 0.3,
                     "nifty_futidx_expiry": "2026-11-26", "signal_day_close": 25050,
                     "target_day_close": 25125, "calendar_gap_days": 3,
                     "agent6_confidence": "low", "agent6_confidence_score": 20},
                ],
            }
            self.write_json(report_path, report)
            self.write_json(analysis / "2026-11-10" / "eod_market_confluence_intelligence.json", {
                "status": "passed", "agent": "eod_market_confluence_intelligence",
                "trading_date": "2026-11-10", "direction": "bullish",
                "confidence": "high", "confidence_score": 70,
            })
            self.write_json(analysis / "2026-11-10" / "futures_buildup_intelligence.json", {
                "status": "passed", "agent": "futures_buildup_intelligence",
                "trading_date": "2026-11-10",
                "contracts": [{"symbol": "NIFTY", "instrument_type": "FUTIDX",
                               "expiry": "2026-11-26", "close_price": 25100}],
            })

            first = run(analysis, report_path, output, csv_output)
            self.assertEqual(first["records_count"], 3)
            self.assertEqual(first["settled_count"], 2)
            self.assertEqual(first["pending_count"], 1)
            self.assertEqual(first["records"][1]["baseline_prediction"], "bullish")
            self.assertEqual(first["records"][1]["baseline_result"], "correct")
            self.assertEqual(first["matched_comparison"]["samples"], 1)
            self.assertEqual(first["matched_comparison"]["persistence_baseline"]["accuracy"], 1.0)
            self.assertEqual(first["records"][-1]["audit_status"], "pending")

            second = run(analysis, report_path, output, csv_output)
            self.assertEqual(second["records_count"], 3)
            with csv_output.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[-1]["audit_status"], "pending")

    def test_existing_pending_row_is_upgraded_to_settled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            analysis = root / "analysis"
            report_path = root / "validation.json"
            output = root / "audit.json"
            csv_output = root / "audit.csv"
            self.write_json(output, {
                "schema_version": 1,
                "records": [{"signal_date": "2026-11-05", "target_date": None,
                             "audit_status": "pending", "prediction": "bullish",
                             "actual": None, "result": "pending"}],
            })
            self.write_json(report_path, {
                "status": "passed",
                "sample_pairs": [{
                    "signal_date": "2026-11-05", "target_date": "2026-11-06",
                    "prediction": "bullish", "actual": "bearish",
                    "target_return_percent": -0.2, "nifty_futidx_expiry": "2026-11-26",
                    "signal_day_close": 25000, "target_day_close": 24950,
                    "calendar_gap_days": 1, "agent6_confidence": "low",
                    "agent6_confidence_score": 20,
                }],
            })
            result = run(analysis, report_path, output, csv_output)
            self.assertEqual(result["records_count"], 1)
            self.assertEqual(result["settled_count"], 1)
            self.assertEqual(result["records"][0]["result"], "incorrect")


if __name__ == "__main__":
    unittest.main()
