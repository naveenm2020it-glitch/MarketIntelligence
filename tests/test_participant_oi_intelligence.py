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
    def test_mixed_assessment(self):
        assessment = overall_assessment({
            "FII":{"index_futures_net_long":10,"index_options_directional_net":-5}
        })
        self.assertEqual(assessment["classification"], "mixed")
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
