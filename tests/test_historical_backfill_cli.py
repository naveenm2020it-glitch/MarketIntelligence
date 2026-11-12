"""Smoke tests for the historical backfill entry point."""
import subprocess
import sys
import unittest
from pathlib import Path


class HistoricalBackfillCliTests(unittest.TestCase):
    def test_direct_script_entrypoint_imports_repo_packages(self):
        repo_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, str(repo_root / "downloader" / "nse_historical_backfill.py"), "--help"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("--start-date", result.stdout)
        self.assertIn("--end-date", result.stdout)
        self.assertIn("--validation-output", result.stdout)


if __name__ == "__main__":
    unittest.main()
