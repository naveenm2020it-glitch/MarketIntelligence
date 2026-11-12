import csv
import tempfile
import unittest
from pathlib import Path
from agents.data_normalization import read_report, normalize_day
from agents.futures_buildup_intelligence import analyze as analyze_futures

class NormalizationTests(unittest.TestCase):
    def write(self, path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
    def test_equity_header_and_rows(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"eq.csv"
            self.write(p,[["NSE EQUITY BHAVCOPY"],["TradDt","TckrSymb","ClsPric"],["11-Nov-2026","ABC","123.4"]])
            result=read_report(p,"equity_udiff")
            self.assertEqual(result["row_count"],1)
            self.assertEqual(result["records"][0]["TCKRSYMB"],"ABC")
    def test_participant_title_row(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"oi.csv"
            self.write(p,[["Participant OI report"],["Client Type","Future Index Long","Total Short Contracts"],["FII","10","4"]])
            result=read_report(p,"participant_oi")
            self.assertEqual(result["header_row"],2)
    def test_legacy_equity_headers_normalize_to_canonical_names(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"cm13APR2020bhav.csv"
            self.write(p,[["SYMBOL","SERIES","OPEN","HIGH","LOW","CLOSE","PREVCLOSE","TIMESTAMP"],
                          ["RELIANCE","EQ","100","105","99","104","98","13-APR-2020"]])
            result=read_report(p,"equity_udiff")
            row=result["records"][0]
            self.assertEqual(row["TCKRSYMB"],"RELIANCE")
            self.assertEqual(row["CLSPRIC"],"104")
            self.assertEqual(row["PRVSCLSGPRIC"],"98")
            self.assertEqual(result["trading_dates"],["2020-04-13"])

    def test_legacy_fo_headers_normalize_without_inventing_previous_close(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"fo13APR2020bhav.csv"
            self.write(p,[["INSTRUMENT","SYMBOL","EXPIRY_DT","CLOSE","OPEN_INT","CHG_IN_OI","TIMESTAMP"],
                          ["FUTIDX","NIFTY","30-APR-2020","9200","12000","500","13-APR-2020"]])
            result=read_report(p,"fo_udiff")
            row=result["records"][0]
            self.assertEqual(row["FININSTRMTP"],"FUTIDX")
            self.assertEqual(row["TCKRSYMB"],"NIFTY")
            self.assertEqual(row["CLSPRIC"],"9200")
            self.assertNotIn("PRVSCLSGPRIC",row)
            self.assertEqual(result["trading_dates"],["2020-04-13"])

    def test_legacy_fo_row_is_accepted_by_agent4_without_formula_change(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"fo13APR2020bhav.csv"
            self.write(p,[["INSTRUMENT","SYMBOL","EXPIRY_DT","CLOSE","PREV_CLOSE","OPEN_INT","CHG_IN_OI","TIMESTAMP"],
                          ["FUTIDX","NIFTY","30-APR-2020","9200","9100","12000","500","13-APR-2020"]])
            parsed=read_report(p,"fo_udiff")
            result=analyze_futures([parsed])
            self.assertEqual(result["valid_future_contracts"],1)
            self.assertEqual(result["contracts"][0]["symbol"],"NIFTY")
            self.assertEqual(result["contracts"][0]["previous_close"],9100.0)
            self.assertEqual(result["contracts"][0]["price_change"],100.0)
            self.assertEqual(result["contracts"][0]["classification"],"long_buildup")

    def test_participant_trade_date_is_normalized_and_checked(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"participant.csv"
            self.write(p,[["trade_date","client_type","future_index_long","total_short_contracts"],
                          ["2026-11-09","FII","100","50"]])
            result=read_report(p,"participant_oi")
            self.assertEqual(result["trading_dates"],["2026-11-09"])
            self.assertEqual(result["records"][0]["TRADDT"],"2026-11-09")

    def test_security_delivery_real_archive_headers_and_date(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"sec_bhavdata_full_05012024.csv"
            self.write(p,[["SYMBOL"," SERIES"," DATE1"," PREV_CLOSE"," OPEN_PRICE"," HIGH_PRICE"," LOW_PRICE"," LAST_PRICE"," CLOSE_PRICE"," AVG_PRICE"," TTL_TRD_QNTY"," TURNOVER_LACS"," NO_OF_TRADES"," DELIV_QTY"," DELIV_PER"],
                          ["20MICRONS","EQ","05-Jan-2024","175.70","175.00","178.65","173.50","175.00","175.25","175.81","92831","163.20","4962","54777","59.01"]])
            result=read_report(p,"security_delivery")
            self.assertEqual(result["trading_dates"],["2024-01-05"])
            self.assertEqual(result["records"][0]["DELIVQTY"],"54777")

    def test_missing_required_header_fails(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"bad.csv"
            self.write(p,[["SYMBOL","PRICE"],["ABC","10"]])
            with self.assertRaises(ValueError): read_report(p,"equity_udiff")
    def test_missing_report_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"input"; out=Path(d)/"out.json"
            for report, rows in {
                "equity_udiff":[["TradDt","TckrSymb","ClsPric"],["11-Nov-2026","ABC","123"]],
                "fo_udiff":[["TradDt","TckrSymb","ClsPric"],["11-Nov-2026","NIFTY","100"]],
                "participant_oi":[["Client Type","Future Index Long","Total Short Contracts"],["FII","10","4"]],
                "security_delivery":[["SYMBOL","DELIVQTY","DELIVPER"],["ABC","50","25"]]
            }.items():
                self.write(root/"2026-11-11"/report/"file.csv",rows)
            result=normalize_day(root,out,"2026-11-11")
            self.assertEqual(result["status"],"passed")
            (root/"2026-11-11"/"fo_udiff"/"file.csv").unlink()
            with self.assertRaises(ValueError): normalize_day(root,out,"2026-11-11")

    def test_report_trading_date_is_independent_of_ingestion_folder_date(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "extracted"
            output = Path(d) / "normalized" / "2026-11-11" / "normalized.json"
            ingest_date = "2026-11-12"
            reports = {
                "equity_udiff": [
                    ["TradDt", "TckrSymb", "ClsPric"],
                    ["2026-11-11", "ABC", "123"],
                ],
                "fo_udiff": [
                    ["TradDt", "TckrSymb", "ClsPric"],
                    ["2026-11-11", "NIFTY", "25000"],
                ],
                "participant_oi": [
                    ["Client Type", "Future Index Long", "Total Short Contracts"],
                    ["FII", "10", "4"],
                ],
                "security_delivery": [
                    ["SYMBOL", "DATE1", "DELIVQTY", "DELIVPER"],
                    ["ABC", "11-Nov-2026", "50", "25"],
                ],
            }
            for report, rows in reports.items():
                self.write(root / ingest_date / report / "file.csv", rows)

            result = normalize_day(root, output, "2026-11-11", source_date=ingest_date)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["requested_date"], "2026-11-11")
            self.assertEqual(result["source_directory_date"], ingest_date)
            self.assertEqual(result["observed_trading_dates"], ["2026-11-11"])

            with self.assertRaisesRegex(ValueError, "expected_trading_date=2026-11-12"):
                normalize_day(root, output, "2026-11-12", source_date=ingest_date)

if __name__ == "__main__": unittest.main()
