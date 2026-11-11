import unittest
from datetime import date
from downloader.nse_historical_backfill import urls


class HistoricalUrlCompatibilityTests(unittest.TestCase):
    def test_legacy_urls_before_udiff_switch(self):
        result = urls(date(2020, 4, 13))
        self.assertEqual(
            result["equity_udiff"],
            "https://archives.nseindia.com/content/historical/EQUITIES/2020/APR/cm13APR2020bhav.csv.zip",
        )
        self.assertEqual(
            result["fo_udiff"],
            "https://archives.nseindia.com/content/historical/DERIVATIVES/2020/APR/fo13APR2020bhav.csv.zip",
        )

    def test_last_legacy_session_uses_legacy_urls(self):
        result = urls(date(2024, 7, 5))
        self.assertIn("/content/historical/EQUITIES/2024/JUL/", result["equity_udiff"])
        self.assertIn("/content/historical/DERIVATIVES/2024/JUL/", result["fo_udiff"])

    def test_udiff_switch_date_uses_udiff_urls(self):
        result = urls(date(2024, 7, 8))
        self.assertIn("BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip", result["equity_udiff"])
        self.assertIn("BhavCopy_NSE_FO_0_0_0_20240708_F_0000.csv.zip", result["fo_udiff"])

    def test_participant_and_delivery_names_remain_stable(self):
        result = urls(date(2020, 4, 13))
        self.assertTrue(result["participant_oi"].endswith("fao_participant_oi_13042020.csv"))
        self.assertTrue(result["security_delivery"].endswith("sec_bhavdata_full_13042020.csv"))


if __name__ == "__main__":
    unittest.main()
