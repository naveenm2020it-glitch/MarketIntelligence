import unittest

from downloader.nse_historical_backfill import backfill_exit_code


class BackfillExitPolicyTests(unittest.TestCase):
    def test_legacy_mode_keeps_partial_success_behavior(self):
        summary = {"passed_days": ["2025-01-02"], "skipped_or_failed_days": [{"date": "2020-04-13", "reason": "failure"}]}
        self.assertEqual(backfill_exit_code(summary), 0)

    def test_strict_pilot_fails_when_any_requested_date_fails(self):
        summary = {"passed_days": ["2025-01-02"], "skipped_or_failed_days": [{"date": "2020-04-13", "reason": "failure"}]}
        self.assertEqual(backfill_exit_code(summary, require_all_dates=True), 1)

    def test_strict_pilot_passes_when_all_dates_pass(self):
        summary = {"passed_days": ["2020-04-13", "2022-01-05"], "skipped_or_failed_days": []}
        self.assertEqual(backfill_exit_code(summary, require_all_dates=True), 0)

    def test_no_valid_agent_day_fails(self):
        summary = {"passed_days": [], "skipped_or_failed_days": [{"date": "2020-04-13", "reason": "failure"}]}
        self.assertEqual(backfill_exit_code(summary, require_all_dates=True), 1)


if __name__ == "__main__":
    unittest.main()
