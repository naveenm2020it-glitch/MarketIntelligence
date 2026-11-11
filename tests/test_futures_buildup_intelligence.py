import json
import tempfile
import unittest
from pathlib import Path

from agents.futures_buildup_intelligence import analyze, classify, run

class FuturesBuildupTests(unittest.TestCase):
    def row(self, instrument, symbol, close, previous, oi_change, expiry="2026-11-26"):
        return {
            "FinInstrmTp": instrument, "TckrSymb": symbol, "XpryDt": expiry,
            "ClsPric": str(close), "PrvsClsgPric": str(previous),
            "ChngInOpnIntrst": str(oi_change), "OpnIntrst": "1000"
        }

    def test_four_classifications(self):
        self.assertEqual(classify(2, 100), "long_buildup")
        self.assertEqual(classify(-2, 100), "short_buildup")
        self.assertEqual(classify(2, -100), "short_covering")
        self.assertEqual(classify(-2, -100), "long_unwinding")

    def test_zero_and_missing_are_not_forced_into_four_classes(self):
        self.assertEqual(classify(0, 100), "neutral_or_flat")
        self.assertEqual(classify(2, None), "unavailable")

    def test_options_are_excluded(self):
        data = analyze([{"records": [
            self.row("FUTIDX", "NIFTY", 220, 210, 500),
            self.row("OPTIDX", "NIFTY", 10, 8, 1000)
        ]}])
        self.assertEqual(data["valid_future_contracts"], 1)
        self.assertEqual(data["ignored_instrument_types"], {"OPTIDX": 1})

    def test_aggregate_underlying_across_expiries(self):
        data = analyze([{"records": [
            self.row("FUTIDX", "NIFTY", 220, 210, 500, "2026-11-26"),
            self.row("FUTIDX", "NIFTY", 218, 210, 300, "2026-12-31"),
            self.row("FUTSTK", "RELIANCE", 100, 102, 200)
        ]}])
        nifty = next(x for x in data["underlyings"] if x["symbol"] == "NIFTY")
        self.assertEqual(nifty["aggregate_open_interest_change"], 800)
        self.assertEqual(nifty["classification"], "long_buildup")
        self.assertEqual(nifty["contracts_included"], 2)

    def test_invalid_required_values_are_counted_and_fail_if_all_invalid(self):
        with self.assertRaises(ValueError):
            analyze([{"records": [self.row("FUTIDX", "NIFTY", "-", 210, 500)]}])

    def test_run_requires_passed_normalization_and_writes_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "normalized.json"
            output = root / "result.json"
            source.write_text(json.dumps({
                "status": "passed", "date_consistency": "passed",
                "requested_date": "2026-11-11",
                "reports": {"fo_udiff": [{"records": [self.row("FUTIDX", "NIFTY", 220, 210, 500)]}]}
            }))
            result = run(source, output)
            self.assertTrue(output.exists())
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["contracts"][0]["classification"], "long_buildup")

    def test_failed_normalization_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "normalized.json"
            source.write_text(json.dumps({"status": "failed", "date_consistency": "passed", "reports": {}}))
            with self.assertRaises(ValueError):
                run(source, Path(tmp) / "out.json")

if __name__ == "__main__":
    unittest.main()
