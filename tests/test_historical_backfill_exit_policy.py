import unittest
from unittest.mock import patch
import subprocess

from downloader.nse_historical_backfill import (
    backfill_exit_code,
    run_agent_recorded,
    should_compact_snapshots,
)


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

    def test_exact_date_pilot_preserves_full_per_agent_snapshots(self):
        self.assertFalse(should_compact_snapshots(True))
        self.assertTrue(should_compact_snapshots(False))

    def test_agent_run_status_is_recorded(self):
        result = {"agent_status": {}}
        with patch("downloader.nse_historical_backfill.run_agent") as run:
            run_agent_recorded(result, "agent5", ["python", "agent5.py"])
        self.assertEqual(result["agent_status"]["agent5"], {"status": "passed"})
        run.assert_called_once_with(["python", "agent5.py"])

    def test_agent_failure_reason_is_recorded_and_raised(self):
        result = {"agent_status": {}}
        error = subprocess.CalledProcessError(1, ["python", "agent5.py"])
        with patch("downloader.nse_historical_backfill.run_agent", side_effect=error):
            with self.assertRaises(subprocess.CalledProcessError):
                run_agent_recorded(result, "agent5", ["python", "agent5.py"])
        self.assertEqual(result["agent_status"]["agent5"]["status"], "failed")
        self.assertIn("returned non-zero exit status 1", result["agent_status"]["agent5"]["reason"])


if __name__ == "__main__":
    unittest.main()
