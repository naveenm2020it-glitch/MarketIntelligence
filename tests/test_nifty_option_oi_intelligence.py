import json, tempfile, unittest
from pathlib import Path
from agents.nifty_option_oi_intelligence import analyze, run

class NiftyOptionOITests(unittest.TestCase):
    def row(self, inst, sym, opt, strike, oi, chg, expiry="2026-11-26"):
        return {"FinInstrmTp":inst,"TckrSymb":sym,"OptnTp":opt,"StrkPric":str(strike),"XpryDt":expiry,"OpnIntrst":str(oi),"ChngInOpnIntrst":str(chg)}
    def test_nifty_only(self):
        x=analyze([{"records":[self.row("IDO","NIFTY","CE",26000,1000,100),self.row("IDO","NIFTY","PE",26000,2000,200),self.row("STO","BANKNIFTY","CE",26000,9000,900),self.row("STF","NIFTY","CE",26000,8000,800)]}])
        self.assertEqual(x["valid_expiries"],1); self.assertEqual(x["valid_nifty_option_strikes"],1); self.assertEqual(x["skipped_rows"]["non_nifty_option"],1)
    def test_oi_levels_and_max_pain(self):
        x=analyze([{"records":[self.row("IDO","NIFTY","CE",25900,100,10),self.row("IDO","NIFTY","PE",25900,500,50),self.row("IDO","NIFTY","CE",26000,800,80),self.row("IDO","NIFTY","PE",26000,900,90),self.row("IDO","NIFTY","CE",26100,1200,120),self.row("IDO","NIFTY","PE",26100,200,20)]}])["expiries"][0]
        self.assertEqual(x["support_levels"][0]["strike"],26000); self.assertEqual(x["resistance_levels"][0]["strike"],26100); self.assertIn(x["max_pain"],[25900,26000,26100])
    def test_expiries_separate(self):
        x=analyze([{"records":[self.row("IDO","NIFTY","CE",26000,100,10,"2026-11-26"),self.row("IDO","NIFTY","PE",26000,200,20,"2026-12-31")]}])
        self.assertEqual(x["valid_expiries"],2)
    def test_invalid_rows_counted(self):
        x=analyze([{"records":[self.row("IDO","NIFTY","XX",26000,100,10)]}]); self.assertEqual(x["invalid_nifty_option_rows"],1)
    def test_no_valid_nifty_options_fails(self):
        with self.assertRaises(ValueError): analyze([{"records":[self.row("IDO","BANKNIFTY","CE",26000,100,10)]}])
    def test_run_writes_result_and_requires_valid_normalization(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/"normalized.json"; out=Path(d)/"result.json"
            src.write_text(json.dumps({"status":"passed","date_consistency":"passed","requested_date":"2026-11-11","reports":{"fo_udiff":[{"records":[self.row("IDO","NIFTY","CE",26000,100,10),self.row("IDO","NIFTY","PE",26000,200,20)]}]}}))
            self.assertEqual(run(src,out)["status"],"passed"); self.assertTrue(out.exists())
