import json
import tempfile
import unittest
from pathlib import Path

from agents.walk_forward_validation import (
    build_pairs, chronological_diagnostics, classification_metrics, contract_map, prediction_label, run,
)


class WalkForwardValidationTests(unittest.TestCase):
    def write_day(self, root, day, direction, close, expiry="2026-11-26",
                  confidence="low", confidence_score=20):
        folder = root / day
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "eod_market_confluence_intelligence.json").write_text(json.dumps({
            "agent": "eod_market_confluence_intelligence", "status": "passed",
            "trading_date": day, "direction": direction,
            "confidence": confidence, "confidence_score": confidence_score,
        }))
        (folder / "futures_buildup_intelligence.json").write_text(json.dumps({
            "agent": "futures_buildup_intelligence", "status": "passed",
            "trading_date": day, "contracts": [{
                "symbol": "NIFTY", "instrument_type": "FUTIDX",
                "expiry": expiry, "close_price": close,
            }],
        }))
        return folder

    def test_prediction_mapping(self):
        self.assertEqual(prediction_label("bullish"), "bullish")
        self.assertEqual(prediction_label("bearish"), "bearish")
        self.assertEqual(prediction_label("neutral_or_mixed"), "neutral")
        self.assertIsNone(prediction_label("insufficient_data"))

    def test_same_expiry_next_archived_session_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_day(root, "2026-11-11", "bullish", 25000)
            self.write_day(root, "2026-11-12", "bearish", 25200)
            pairs, skipped = build_pairs(root, 10, 5)
            self.assertEqual(len(pairs), 1)
            self.assertEqual(pairs[0]["prediction"], "bullish")
            self.assertEqual(pairs[0]["actual"], "bullish")
            self.assertEqual(pairs[0]["target_return_percent"], 0.8)
            self.assertEqual(pairs[0]["nifty_futidx_expiry"], "2026-11-26")
            self.assertEqual(skipped, [])

    def test_rollover_does_not_splice_different_expiries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_day(root, "2026-11-11", "bullish", 25000, "2026-11-26")
            self.write_day(root, "2026-11-12", "bullish", 25300, "2026-12-31")
            pairs, skipped = build_pairs(root, 10, 5)
            self.assertEqual(pairs, [])
            self.assertIn("No same-expiry", skipped[0]["reason"])

    def test_nearest_common_expiry_is_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for day, close in (("2026-11-11", 25000), ("2026-11-12", 25100)):
                folder = self.write_day(root, day, "bullish", close, "2026-11-26")
                fut_path = folder / "futures_buildup_intelligence.json"
                fut = json.loads(fut_path.read_text())
                fut["contracts"].append({"symbol":"NIFTY","instrument_type":"FUTIDX",
                                         "expiry":"2026-12-31","close_price":close+500})
                fut_path.write_text(json.dumps(fut))
            pairs, _ = build_pairs(root, 10, 5)
            self.assertEqual(pairs[0]["nifty_futidx_expiry"], "2026-11-26")

    def test_neutral_band_applied_in_basis_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_day(root, "2026-11-11", "neutral_or_mixed", 25000)
            self.write_day(root, "2026-11-12", "bullish", 25010)
            pairs, _ = build_pairs(root, 10, 5)
            self.assertEqual(pairs[0]["actual"], "neutral")

    def test_gap_above_limit_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_day(root, "2026-11-11", "bullish", 25000)
            self.write_day(root, "2026-11-20", "bullish", 25100)
            pairs, skipped = build_pairs(root, 10, 5)
            self.assertEqual(pairs, [])
            self.assertIn("gap", skipped[0]["reason"])

    def test_bad_date_or_wrong_agent_status_is_not_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = self.write_day(root, "2026-11-11", "bullish", 25000)
            a6 = json.loads((folder / "eod_market_confluence_intelligence.json").read_text())
            a6["status"] = "failed"
            (folder / "eod_market_confluence_intelligence.json").write_text(json.dumps(a6))
            self.write_day(root, "2026-11-12", "bullish", 25100)
            pairs, _ = build_pairs(root, 10, 5)
            self.assertEqual(pairs, [])

    def test_duplicate_contract_expiry_rejected(self):
        with self.assertRaises(ValueError):
            contract_map({"contracts":[
                {"symbol":"NIFTY","instrument_type":"FUTIDX","expiry":"2026-11-26","close_price":25000},
                {"symbol":"NIFTY","instrument_type":"FUTIDX","expiry":"2026-11-26","close_price":25010},
            ]})

    def test_confusion_metrics(self):
        pairs = [
            {"prediction":"bullish","actual":"bullish","agent6_confidence":"low"},
            {"prediction":"bearish","actual":"bullish","agent6_confidence":"low"},
            {"prediction":"neutral","actual":"neutral","agent6_confidence":"moderate"},
        ]
        m = classification_metrics(pairs)
        self.assertEqual(m["samples"], 3)
        self.assertAlmostEqual(m["accuracy"], 2/3, places=3)
        self.assertEqual(m["confusion_matrix_actual_rows_predicted_columns"]["bullish"]["bearish"], 1)
        self.assertEqual(m["directional_target_samples"], 2)

    def test_run_reports_insufficient_data_without_fake_accuracy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "analysis"
            root.mkdir()
            self.write_day(root, "2026-11-11", "bullish", 25000)
            output = Path(tmp) / "result.json"
            result = run(root, output, minimum_samples=30)
            self.assertEqual(result["status"], "insufficient_data")
            self.assertEqual(result["metrics"]["samples"], 0)
            self.assertIsNone(result["metrics"]["accuracy"])
            self.assertTrue(output.exists())

    def test_run_passes_only_after_minimum_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "analysis"
            root.mkdir()
            dates = ["2026-11-02", "2026-11-03", "2026-11-04"]
            for i, day in enumerate(dates):
                self.write_day(root, day, "bullish", 25000 + i * 100)
            result = run(root, Path(tmp) / "result.json", minimum_samples=2)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["metrics"]["samples"], 2)

    def test_snapshot_folder_date_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = self.write_day(root, "2026-11-11", "bullish", 25000)
            a6_path = folder / "eod_market_confluence_intelligence.json"
            a6 = json.loads(a6_path.read_text())
            a6["trading_date"] = "2026-11-10"
            a6_path.write_text(json.dumps(a6))
            with self.assertRaises(ValueError):
                build_pairs(root, 10, 5)

    def test_chronological_diagnostics_and_persistence_baseline(self):
        pairs = [
            {"signal_date":"2026-11-02","target_date":"2026-11-03","actual":"bullish",
             "prediction":"bullish","calendar_gap_days":1,"nifty_futidx_expiry":"2026-11-26"},
            {"signal_date":"2026-11-03","target_date":"2026-11-04","actual":"bearish",
             "prediction":"bullish","calendar_gap_days":1,"nifty_futidx_expiry":"2026-11-26"},
            {"signal_date":"2026-11-04","target_date":"2026-11-05","actual":"bearish",
             "prediction":"bearish","calendar_gap_days":2,"nifty_futidx_expiry":"2026-12-31"},
        ]
        result = chronological_diagnostics(pairs)
        baseline = result["persistence_baseline"]
        self.assertEqual(baseline["samples"], 2)
        # Predictions are prior observed actual labels: bullish, then bearish.
        self.assertAlmostEqual(baseline["metrics"]["accuracy"], 0.5)
        self.assertFalse(baseline["uses_current_target_for_prediction"])
        self.assertEqual([x["period"] for x in result["chronological_periods"]],
                         ["early", "middle", "late"])
        audit = result["archive_and_contract_audit"]
        self.assertEqual(audit["calendar_gap_days_max"], 2)
        self.assertEqual(audit["common_expiry_sample_counts"],
                         {"2026-11-26": 2, "2026-12-31": 1})

    def test_persistence_baseline_excludes_first_pair(self):
        pairs = [{
            "signal_date":"2026-11-02","target_date":"2026-11-03","actual":"bullish",
            "prediction":"bearish","calendar_gap_days":1,"nifty_futidx_expiry":"2026-11-26",
        }]
        result = chronological_diagnostics(pairs)
        self.assertEqual(result["persistence_baseline"]["samples"], 0)
        self.assertIsNone(result["persistence_baseline"]["metrics"]["accuracy"])


if __name__ == "__main__":
    unittest.main()
