import json
import tempfile
import unittest
from pathlib import Path

from agents.build_ml_training_dataset import build_rows, write_dataset


class TrainingDatasetBuilderTests(unittest.TestCase):
    def write_day(self, root, day, close, direction="bullish", expiry="2026-11-26"):
        folder = root / day
        folder.mkdir(parents=True, exist_ok=True)
        docs = {
            "participant_oi_intelligence.json": {
                "agent": "participant_oi_intelligence", "status": "passed",
                "trading_date": day, "participants": {"FII": {
                    "index_futures_net_long": 100, "index_options_directional_net": -20,
                    "future_index_long": 200, "future_index_short": 100}},
            },
            "delivery_strength.json": {
                "agent": "equity_delivery_strength", "status": "passed",
                "trading_date": day, "universe_symbols": 2, "equity_bhavcopy_matches": 2,
                "universe_median_delivery_percentage": 40,
                "stocks": [
                    {"symbol": "AAA", "delivery_percentage": 50, "price_change_percentage": 1.2,
                     "equity_bhavcopy_match": True},
                    {"symbol": "BBB", "delivery_percentage": 30, "price_change_percentage": -0.5,
                     "equity_bhavcopy_match": True},
                ],
            },
            "futures_buildup_intelligence.json": {
                "agent": "futures_buildup_intelligence", "status": "passed",
                "trading_date": day, "contracts": [{
                    "symbol": "NIFTY", "instrument_type": "FUTIDX", "expiry": expiry,
                    "close_price": close, "price_change_percentage": 0.2,
                    "open_interest": 1000, "open_interest_change": 50,
                    "classification": "long_buildup"}],
            },
            "eod_market_confluence_intelligence.json": {
                "agent": "eod_market_confluence_intelligence", "status": "passed",
                "trading_date": day, "direction": direction, "confidence": "moderate",
                "confidence_score": 45, "score": 0.3, "coverage_percent": 70,
            },
        }
        for name, value in docs.items():
            (folder / name).write_text(json.dumps(value), encoding="utf-8")

    def test_signal_day_features_and_next_session_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_day(root, "2026-11-11", 25000, "bearish")
            self.write_day(root, "2026-11-12", 25200, "bullish")
            rows, skipped = build_rows(root)
            self.assertEqual(len(rows), 1)
            self.assertEqual(skipped, [])
            row = rows[0]
            self.assertEqual(row["target_class"], "bullish")
            self.assertAlmostEqual(row["target_return_pct"], 0.8)
            self.assertEqual(row["agent6_direction_bearish"], 1.0)
            self.assertEqual(row["participant_fii_index_futures_net_long"], 100.0)
            self.assertAlmostEqual(row["delivery_positive_price_breadth"], 0.5)

    def test_different_expiry_is_not_spliced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_day(root, "2026-11-11", 25000, expiry="2026-11-26")
            self.write_day(root, "2026-11-12", 25200, expiry="2026-12-31")
            rows, skipped = build_rows(root)
            self.assertEqual(rows, [])
            self.assertEqual(skipped[0]["reason"], "no_common_expiry")

    def test_chronological_split_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = []
            for i in range(40):
                rows.append({"signal_date": f"2026-01-{i+1:02d}", "target_date": f"2026-02-{i+1:02d}",
                             "target_expiry": "2026-03-01", "feature_x": float(i),
                             "target_return_pct": 0.2, "target_class": "bullish"})
            out = Path(tmp) / "dataset"
            manifest = write_dataset(rows, out)
            self.assertEqual(manifest["rows"], 40)
            self.assertEqual(manifest["split"]["train"]["rows"], 28)
            self.assertEqual(manifest["split"]["validation"]["rows"], 6)
            self.assertEqual(manifest["split"]["test"]["rows"], 6)
            self.assertTrue((out / "train.csv").is_file())
            self.assertTrue((out / "manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
