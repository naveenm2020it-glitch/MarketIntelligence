import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class DownloaderImportPathTests(unittest.TestCase):
    def test_direct_script_bootstrap_makes_agents_importable(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "downloader" / "nse_downloader.py"
        code = """
import runpy
import sys
script_dir, repo_root, script = sys.argv[1:]
sys.path[:] = [entry for entry in sys.path if entry not in ("", repo_root)]
sys.path.insert(0, script_dir)
runpy.run_path(script, run_name="nse_downloader_test")
import agents.data_normalization
"""
        with tempfile.TemporaryDirectory() as working_directory:
            result = subprocess.run(
                [sys.executable, "-c", code, str(script.parent), str(repo_root), str(script)],
                cwd=working_directory,
                capture_output=True,
                text=True,
                timeout=20,
            )
        self.assertEqual(result.returncode, 0, msg=result.stderr)


if __name__ == "__main__":
    unittest.main()
