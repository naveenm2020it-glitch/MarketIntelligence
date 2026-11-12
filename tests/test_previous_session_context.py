import unittest
from datetime import date
from unittest.mock import patch

from downloader import nse_historical_backfill as backfill


class PreviousSessionContextTests(unittest.TestCase):
    def test_skips_non_session_dates_and_prepares_nearest_valid_prior_session(self):
        calls = []

        def fake_fetch(session, candidate, key, url):
            calls.append((candidate, key))
            if candidate > date(2026, 11, 9):
                raise FileNotFoundError("NSE archive not found")
            return {"validation": "passed"}

        with patch.object(backfill, "fetch_one", side_effect=fake_fetch), \
             patch.object(backfill, "normalize_day") as normalize, \
             patch.object(backfill, "run_agent") as run_agent:
            found = backfill.prepare_previous_session_context(date(2026, 11, 11), object())

        self.assertEqual(found, date(2026, 11, 9))
        self.assertEqual([key for _, key in calls[-4:]], list(backfill.REPORTS))
        normalize.assert_called_once()
        self.assertEqual(run_agent.call_count, 2)

    def test_returns_none_when_no_valid_prior_archive_exists(self):
        with patch.object(backfill, "fetch_one", side_effect=FileNotFoundError("missing")), \
             patch.object(backfill, "normalize_day") as normalize, \
             patch.object(backfill, "run_agent") as run_agent:
            found = backfill.prepare_previous_session_context(date(2026, 11, 11), object())

        self.assertIsNone(found)
        normalize.assert_not_called()
        run_agent.assert_not_called()


if __name__ == "__main__":
    unittest.main()
