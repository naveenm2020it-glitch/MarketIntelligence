import json, tempfile, unittest
from pathlib import Path
from agents.eod_market_confluence_intelligence import combine, participant_signal, futures_signal, options_signal, delivery_signal, run

class Agent6Tests(unittest.TestCase):
    def test_participant_fii_bearish_dii_bullish_is_mixed(self):
        data={"participants":{"FII":{"index_futures_net_long":-10,"index_options_directional_net":-5},
                              "DII":{"index_futures_net_long":10,"index_options_directional_net":5}}}
        self.assertEqual(participant_signal(data)["direction"],"mixed")
    def test_futures_uses_nifty_index_only(self):
        data={"underlyings":[{"symbol":"RELIANCE","instrument_type":"FUTSTK","classification":"long_buildup"}]}
        self.assertFalse(futures_signal(data)["available"])
    def test_futures_bullish_classification(self):
        data={"underlyings":[{"symbol":"NIFTY","instrument_type":"FUTIDX","classification":"long_buildup"}]}
        self.assertEqual(futures_signal(data)["direction"],"bullish")
    def test_options_nearest_expiry_only(self):
        data={"expiries":[{"expiry":"2026-11-17","support_levels":[{"put_oi":500}],
                           "resistance_levels":[{"call_oi":200}],"max_pain":25000},
                          {"expiry":"2026-11-24","support_levels":[{"put_oi":100}],
                           "resistance_levels":[{"call_oi":900}],"max_pain":26000}]}
        x=options_signal(data)
        self.assertEqual(x["direction"],"bullish")
        self.assertEqual(x["evidence"]["expiry"],"2026-11-17")
    def test_delivery_breadth(self):
        data={"stocks":[{"price_change_percentage":1.0,"delivery_percentage":60},
                        {"price_change_percentage":-0.2,"delivery_percentage":50}]}
        self.assertEqual(delivery_signal(data)["direction"],"neutral")
    def test_missing_sources_reduce_coverage(self):
        x=combine({"participant":{"direction":"bullish"},"futures":{"direction":"unavailable"},
                   "options":{"direction":"bearish"},"delivery":{"direction":"unavailable"}})
        self.assertEqual(x["coverage_percent"],55.0)
        self.assertEqual(x["confidence"],"low")
    def test_all_bullish_high_confidence(self):
        sig={k:{"direction":"bullish"} for k in ("participant","futures","options","delivery")}
        x=combine(sig)
        self.assertEqual(x["direction"],"bullish")
        self.assertEqual(x["confidence"],"high")
    def test_all_bearish_high_confidence(self):
        sig={k:{"direction":"bearish"} for k in ("participant","futures","options","delivery")}
        self.assertEqual(combine(sig)["direction"],"bearish")
    def test_conflict_becomes_neutral_or_mixed(self):
        sig={"participant":{"direction":"bullish"},"futures":{"direction":"bearish"},
             "options":{"direction":"bullish"},"delivery":{"direction":"bearish"}}
        self.assertEqual(combine(sig)["direction"],"neutral_or_mixed")
    def test_no_evidence_is_unrated(self):
        sig={k:{"direction":"unavailable"} for k in ("participant","futures","options","delivery")}
        self.assertEqual(combine(sig)["direction"],"insufficient_data")
        self.assertEqual(combine(sig)["confidence"],"unrated")
    def test_run_requires_matching_date_and_writes_output(self):
        with tempfile.TemporaryDirectory() as d:
            day=Path(d)/"2026-11-11"; day.mkdir()
            payloads={
              "participant_oi_intelligence.json":{"agent":"participant_oi_intelligence","status":"passed","trading_date":"2026-11-11","participants":{}},
              "delivery_strength.json":{"agent":"equity_delivery_strength","status":"passed","trading_date":"2026-11-11","stocks":[]},
              "futures_buildup_intelligence.json":{"agent":"futures_buildup_intelligence","status":"passed","trading_date":"2026-11-11","underlyings":[]},
              "nifty_option_oi_intelligence.json":{"agent":"nifty_option_oi_intelligence","status":"passed","trading_date":"2026-11-11","expiries":[]}}
            for name,obj in payloads.items(): (day/name).write_text(json.dumps(obj))
            out=day/"out.json"
            result=run(day,out)
            self.assertEqual(result["status"],"passed")
            self.assertTrue(out.exists())
    def test_empty_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            day=Path(d)/"2026-11-11"; day.mkdir()
            (day/"delivery_strength.json").write_text("")
            with self.assertRaises(ValueError):
                run(day,day/"out.json")

    def test_date_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            day=Path(d)/"2026-11-11"; day.mkdir()
            (day/"participant_oi_intelligence.json").write_text(json.dumps({"agent":"participant_oi_intelligence","status":"passed","trading_date":"2026-11-10"}))
            with self.assertRaises(ValueError): run(day,day/"out.json")

if __name__=="__main__": unittest.main()
