import json, tempfile, unittest
from pathlib import Path
from agents.equity_delivery_strength import build_rows, run

class DeliveryTests(unittest.TestCase):
    def payload(self):
        return {"status":"passed","date_consistency":"passed","requested_date":"2026-11-11","reports":{
          "equity_udiff":[{"records":[{"TckrSymb":"AAA","ClsPric":"101","PrvsClsgPric":"100","TtlTradgVol":"1000"},{"TckrSymb":"BBB","ClsPric":"49","PrvsClsgPric":"50","TtlTradgVol":"2000"}]}],
          "security_delivery":[{"records":[{"SYMBOL":"AAA","DELIVQTY":"600","DELIVPER":"60","TTL_TRD_QNTY":"1000"},{"SYMBOL":"BBB","DELIVQTY":"500","DELIVPER":"25","TTL_TRD_QNTY":"2000"},{"SYMBOL":"CCC","DELIVQTY":"80","DELIVPER":"40","TTL_TRD_QNTY":"200"}]}]}}
    def test_join_and_price_change(self):
        x=next(v for v in build_rows(self.payload()) if v["symbol"]=="AAA")
        self.assertEqual(x["price_change_percentage"],1); self.assertTrue(x["equity_bhavcopy_match"])
    def test_invalid_delivery_percentage_excluded(self):
        p=self.payload(); p["reports"]["security_delivery"][0]["records"].append({"SYMBOL":"BAD","DELIVQTY":"10","DELIVPER":"120"})
        self.assertNotIn("BAD",[x["symbol"] for x in build_rows(p)])
    def test_previous_day_comparison(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); old=root/"2026-11-10"; old.mkdir()
            (old/"delivery_strength.json").write_text(json.dumps({"agent":"equity_delivery_strength","status":"passed","stocks":[{"symbol":"AAA","delivery_percentage":55,"delivery_quantity":500}]}))
            src=root/"normalized.json"; src.write_text(json.dumps(self.payload()))
            result=run(src,root/"out.json",root)
            self.assertEqual(result["comparison_date"],"2026-11-10")
            self.assertEqual(result["changes_vs_previous_day"]["AAA"]["delivery_percentage_change_pp"],5)
    def test_missing_report_fails_closed(self):
        p=self.payload(); del p["reports"]["security_delivery"]
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/"in.json"; src.write_text(json.dumps(p))
            with self.assertRaises(ValueError): run(src,Path(d)/"out.json",Path(d))
if __name__=="__main__": unittest.main()
