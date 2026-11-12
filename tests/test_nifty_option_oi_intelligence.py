import csv, json, tempfile, unittest
from pathlib import Path
from agents.nifty_option_oi_intelligence import analyze, run
from agents.data_normalization import read_report

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
        x=analyze([{"records":[self.row("IDO","NIFTY","CE",26000,100,10),self.row("IDO","NIFTY","XX",26000,100,10)]}]); self.assertEqual(x["invalid_nifty_option_rows"],1)
    def test_no_valid_nifty_options_fails(self):
        with self.assertRaises(ValueError): analyze([{"records":[self.row("IDO","BANKNIFTY","CE",26000,100,10)]}])
    def test_run_writes_result_and_requires_valid_normalization(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/"normalized.json"; out=Path(d)/"result.json"
            src.write_text(json.dumps({"status":"passed","date_consistency":"passed","requested_date":"2026-11-11","reports":{"fo_udiff":[{"records":[self.row("IDO","NIFTY","CE",26000,100,10),self.row("IDO","NIFTY","PE",26000,200,20)]}]}}))
            self.assertEqual(run(src,out)["status"],"passed"); self.assertTrue(out.exists())

    def test_genuine_legacy_nse_archives_2020_2022_2024(self):
        # Values below are copied from genuine NSE F&O Bhavcopy rows in the 2026-11-11 pilot artifact.
        header = ["INSTRUMENT","SYMBOL","EXPIRY_DT","STRIKE_PR","OPTION_TYP","OPEN","HIGH","LOW","CLOSE","SETTLE_PR","CONTRACTS","VAL_INLAKH","OPEN_INT","CHG_IN_OI","TIMESTAMP",""]
        samples = [
            ("2020-04-13","13-APR-2020","16-Apr-2020","6100","75","0","198975","30150"),
            ("2022-01-05","05-JAN-2022","06-Jan-2022","15100","350","300","1829450","62000"),
            ("2024-01-05","05-JAN-2024","11-Jan-2024","19400","3950","50","2416100","2056100"),
        ]
        parsed_files = []
        with tempfile.TemporaryDirectory() as directory:
            for day, stamp, expiry, strike, ce_oi, ce_change, pe_oi, pe_change in samples:
                path = Path(directory) / f"fo-{day}.csv"
                with path.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(header)
                    for option, oi, change in (("CE", ce_oi, ce_change), ("PE", pe_oi, pe_change)):
                        writer.writerow(["OPTIDX","NIFTY",expiry,strike,option,"0","0","0","100","100","1","1",oi,change,stamp,""])
                parsed_files.append(read_report(path, "fo_udiff"))
        result = analyze(parsed_files)
        self.assertEqual(result["valid_expiries"], 3)
        self.assertEqual(result["valid_nifty_option_strikes"], 3)
        by_expiry = {item["expiry"]: item for item in result["expiries"]}
        self.assertEqual(by_expiry["16-Apr-2020"]["strikes"][0]["call_oi"], 75.0)
        self.assertEqual(by_expiry["16-Apr-2020"]["strikes"][0]["put_oi"], 198975.0)
        self.assertEqual(by_expiry["06-Jan-2022"]["strikes"][0]["call_change_oi"], 300.0)
        self.assertEqual(by_expiry["11-Jan-2024"]["strikes"][0]["put_change_oi"], 2056100.0)
