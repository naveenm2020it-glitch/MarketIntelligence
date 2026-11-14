import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents import equity_delivery_strength, participant_oi_intelligence
from downloader import nse_historical_backfill


class CompactAgentSnapshotTests(unittest.TestCase):
    def test_compaction_preserves_minimal_agent_2_and_3_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day = "2026-11-11"
            folder = root / day
            folder.mkdir()
            participant = {
                "schema_version": 1,
                "agent": "participant_oi_intelligence",
                "status": "passed",
                "data_scope": "EOD participant positioning only",
                "trading_date": day,
                "comparison_date": "2026-11-09",
                "participants": {"FII": {"index_futures_net_long": -120.0}},
                "unrecognized_rows": ["unused"],
                "limitations": ["unused"],
            }
            delivery = {
                "schema_version": 1,
                "agent": "equity_delivery_strength",
                "status": "passed",
                "data_scope": "EOD equity delivery only",
                "trading_date": day,
                "comparison_date": "2026-11-09",
                "universe_symbols": 1,
                "equity_bhavcopy_matches": 1,
                "universe_median_delivery_percentage": 52.5,
                "stocks": [
                    {"symbol": "ABC", "delivery_quantity": 1000, "delivery_percentage": 52.5,
                     "price_change_percentage": 1.25, "equity_bhavcopy_match": True,
                     "close_price": 123.4, "extra_large_field": "discard"},
                ],
                "top_delivery_percentage": [{"large": "discard"}],
            }
            (folder / "participant_oi_intelligence.json").write_text(json.dumps(participant))
            (folder / "delivery_strength.json").write_text(json.dumps(delivery))
            option = {
                "schema_version": 1, "agent": "nifty_option_oi_intelligence",
                "status": "passed", "data_scope": "NIFTY options EOD OI",
                "trading_date": day,
                "expiries": [{
                    "expiry": "2026-11-26", "strike_count": 2,
                    "strikes": [
                        {"strike": 25000, "call_oi": 100, "put_oi": 200},
                        {"strike": 25100, "call_oi": 300, "put_oi": 400},
                    ],
                    "max_pain": 25000,
                    "support_levels": [{"strike": 25000}, {"strike": 24900}],
                    "resistance_levels": [{"strike": 25100}, {"strike": 25200}],
                }],
            }
            (folder / "nifty_option_oi_intelligence.json").write_text(json.dumps(option))

            with patch.object(nse_historical_backfill, "ANALYSIS", root):
                nse_historical_backfill.compact_snapshots(day)

            participant_path = folder / "participant_oi_intelligence.json"
            delivery_path = folder / "delivery_strength.json"
            self.assertTrue(participant_path.is_file())
            self.assertTrue(delivery_path.is_file())
            option_path = folder / "nifty_option_oi_intelligence.json"
            self.assertTrue(option_path.is_file())

            compact_participant = json.loads(participant_path.read_text())
            self.assertEqual(compact_participant["participants"]["FII"]["index_futures_net_long"], -120.0)
            self.assertEqual(compact_participant["trading_date"], day)
            self.assertNotIn("limitations", compact_participant)

            compact_delivery = json.loads(delivery_path.read_text())
            self.assertEqual(compact_delivery["stocks"], [
                {"symbol": "ABC", "delivery_quantity": 1000, "delivery_percentage": 52.5,
                 "price_change_percentage": 1.25, "equity_bhavcopy_match": True}
            ])
            self.assertEqual(compact_delivery["universe_median_delivery_percentage"], 52.5)
            self.assertNotIn("top_delivery_percentage", compact_delivery)

            compact_option = json.loads(option_path.read_text())
            nearest = compact_option["expiries"][0]
            self.assertEqual(nearest["total_call_oi"], 400)
            self.assertEqual(nearest["total_put_oi"], 600)
            self.assertEqual(nearest["put_call_oi_ratio"], 1.5)
            self.assertEqual(nearest["support_levels"], [{"strike": 25000}])
            self.assertEqual(nearest["resistance_levels"], [{"strike": 25100}])
            self.assertNotIn("strikes", nearest)

            # Both agents must be able to discover these as previous-session snapshots.
            self.assertEqual(
                participant_oi_intelligence.find_previous(root, "2026-11-12"),
                participant_path,
            )
            self.assertEqual(
                equity_delivery_strength.previous_snapshot(root, "2026-11-12"),
                delivery_path,
            )


if __name__ == "__main__":
    unittest.main()
