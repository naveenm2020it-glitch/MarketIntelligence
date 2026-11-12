import json
import tempfile
import unittest
from pathlib import Path
from agents.participant_oi_intelligence import analyze_report, overall_assessment, run

class ParticipantOIIntelligenceTests(unittest.TestCase):
    def report(self, rows):
        return [{"records": rows}]
    def test_fii_net_long_and_option_direction(self):
        result = analyze_report(self.report([{
            "CLIENTTYPE":"FII","FUTUREINDEXLONG":"120","FUTUREINDEXSHORT":"80",
            "OPTIONINDEXCALLLONG":"50","OPTIONINDEXPUTLONG":"20",
            "OPTIONINDEXCALLSHORT":"10","OPTIONINDEXPUTSHORT":"30"
        }]))
        fii = result["participants"]["FII"]
        self.assertEqual(fii["index_futures_net_long"], 40)
        self.assertEqual(fii["index_options_directional_net"], 50)
        self.assertEqual(fii["futures_bias"], "net_long")
    def test_unknown_and_missing_columns_are_not_invented(self):
        result = analyze_report(self.report([{"CLIENTTYPE":"FII","FUTUREINDEXLONG":"10"}]))
        self.assertIsNone(result["participants"]["FII"]["index_futures_net_long"])
        self.assertEqual(result["unrecognized_rows"], [])
    def test_participant_offsets_do_not_cancel_market_assessment(self):
        assessment = overall_assessment({
            "FII":{"index_futures_net_long":-70,"index_options_directional_net":-680},
            "CLIENT":{"index_futures_net_long":67,"index_options_directional_net":758},
            "DII":{"index_futures_net_long":27,"index_options_directional_net":-104},
            "PRO":{"index_futures_net_long":-23,"index_options_directional_net":30}
        })
        self.assertEqual(assessment["classification"], "mixed_participant_positioning")
        self.assertEqual(assessment["participant_bias"]["FII"], "bearish")
        self.assertEqual(assessment["participant_bias"]["CLIENT"], "bullish")
        self.assertNotIn("index_futures_net_total", assessment)

    def test_comparison_uses_persisted_previous_agent_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); history=root/"normalized"
            previous_dir=history/"2026-11-10"; previous_dir.mkdir(parents=True)
            (previous_dir/"participant_oi_intelligence.json").write_text(json.dumps({
                "agent":"participant_oi_intelligence","status":"passed",
                "participants":{"FII":{"index_futures_net_long":20,"index_options_directional_net":5}}
            }))
            today=history/"2026-11-11"; today.mkdir(parents=True)
            source=today/"normalized.json"
            source.write_text(json.dumps({
                "status":"passed","date_consistency":"passed","requested_date":"2026-11-11",
                "observed_trading_dates":["2026-11-11"],
                "reports":{"participant_oi":self.report([{
                    "CLIENTTYPE":"FII","FUTUREINDEXLONG":"100","FUTUREINDEXSHORT":"70"
                }])}
            }))
            result=run(source,root/"out.json",history)
            self.assertEqual(result["comparison_date"],"2026-11-10")
            self.assertEqual(result["participant_changes_vs_previous_day"]["FII"]["index_futures_net_long"],10)

    def test_sparse_historical_pilot_does_not_use_years_old_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); history=root/"analysis"
            old=history/"2020-04-13"; old.mkdir(parents=True)
            (old/"participant_oi_intelligence.json").write_text(json.dumps({
                "agent":"participant_oi_intelligence","status":"passed",
                "participants":{"FII":{"index_futures_net_long":20,"index_options_directional_net":5}}
            }))
            today=history/"2022-01-05"; today.mkdir(parents=True)
            source=today/"normalized.json"
            source.write_text(json.dumps({
                "status":"passed","date_consistency":"passed","requested_date":"2022-01-05",
                "reports":{"participant_oi":self.report([{
                    "CLIENTTYPE":"FII","FUTUREINDEXLONG":"100","FUTUREINDEXSHORT":"70"
                }])}
            }))
            result=run(source,root/"out.json",history)
            self.assertIsNone(result["comparison_date"])
            self.assertIsNone(result["participant_changes_vs_previous_day"])

    def test_end_to_end_without_previous_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); day=root/"normalized"/"2026-11-11"
            day.mkdir(parents=True)
            source=day/"normalized.json"
            source.write_text(json.dumps({
                "status":"passed","date_consistency":"passed","requested_date":"2026-11-11",
                "observed_trading_dates":["2026-11-11"],
                "reports":{"participant_oi":self.report([{
                    "CLIENTTYPE":"FII","FUTUREINDEXLONG":"100","FUTUREINDEXSHORT":"70"
                }])}
            }))
            output=root/"analysis.json"
            result=run(source,output,root/"normalized")
            self.assertEqual(result["status"],"passed")
            self.assertIsNone(result["comparison_date"])
            self.assertTrue(output.exists())

if __name__ == "__main__": unittest.main()
