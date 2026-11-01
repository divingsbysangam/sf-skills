"""Deterministic Versions-response selection without Salesforce access."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from _test_support import load_module

SCRIPT = Path(__file__).resolve().parent.parent / "h360_api_version.py"
helper = load_module(SCRIPT, "h360_version_selection")


def entry(version):
    return {"version": version, "url": "/services/data/v" + version}


class VersionSelectionTests(unittest.TestCase):
    def test_numeric_maximum_uses_returned_root_and_not_array_order(self):
        self.assertEqual(entry("100.0"), helper.select_version([entry("99.0"), entry("100.0"), entry("9.0")]))

    def test_dispatch_response_body_is_supported(self):
        self.assertEqual(entry("68.0"), helper.select_version({"status_code": 200, "body": [entry("68.0")]}))

    def test_malformed_arrays_versions_and_urls_fail_closed(self):
        for response in (None, {}, [], [None], [entry("NaN")], [entry("1e9")],
                         [{"version": 68, "url": "/services/data/v68.0"}],
                         [{"version": "68.0", "url": "https://example.org"}],
                         [{"version": "68.0", "url": "/services/data/v67.0"}],
                         {"status_code": 403, "body": [entry("68.0")]}):
            with self.subTest(response=response), self.assertRaises(ValueError):
                helper.select_version(response)

    def test_cli_accepts_existing_response_file(self):
        with tempfile.TemporaryDirectory() as raw:
            response = Path(raw) / "versions.json"
            response.write_text(json.dumps([entry("68.0"), entry("67.0")]))
            result = subprocess.run([sys.executable, str(SCRIPT), str(response)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(entry("68.0"), json.loads(result.stdout))

    def test_cli_invalid_input_returns_error_without_echoing_content(self):
        result = subprocess.run([sys.executable, str(SCRIPT)], input="private-invalid-content",
                                capture_output=True, text=True)
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertNotIn("private-invalid-content", result.stderr)


if __name__ == "__main__":
    unittest.main()
