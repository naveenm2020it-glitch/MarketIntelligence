import csv
import tempfile
import unittest
from pathlib import Path
from agents.data_normalization import read_report, normalize_day

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

if __name__ == "__main__": unittest.main()
