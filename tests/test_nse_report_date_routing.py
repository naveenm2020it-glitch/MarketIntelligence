import unittest

from downloader.nse_downloader import report_storage_date


class NSEReportDateRoutingTests(unittest.TestCase):
    def test_catalogue_trading_date_controls_storage_folder(self):
        self.assertEqual(report_storage_date({"tradingDate": "11-Nov-2026"}), "2026-11-11")

    def test_runner_date_is_only_a_fallback_when_source_date_is_missing(self):
        self.assertEqual(report_storage_date({}, fallback="2026-11-12"), "2026-11-12")


if __name__ == "__main__":
    unittest.main()
