"""Unit tests for compatibility aggregation and issue reporting scripts."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
EVAL_SCRIPT = SCRIPTS_DIR / "evaluate_aggregate_compat.py"
ISSUE_SCRIPT = SCRIPTS_DIR / "file_compat_issue.py"


class TestEvaluateAggregateCompat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.artifacts_dir = Path(self.tmp.name) / "artifacts"
        self.artifacts_dir.mkdir(parents=True)
        self.ext_dir = Path(self.tmp.name) / "extensions"
        self.ext_dir.mkdir(parents=True)

        # Create 2 mock extensions
        for name in ("ext-one", "ext-two"):
            d = self.ext_dir / name
            d.mkdir()
            (d / "metadata.json").write_text(
                json.dumps({"uuid": f"{name}@projectbluefin.io", "shell-version": ["50", "51"]}),
                encoding="utf-8",
            )
        self.uuids = [f"{name}@projectbluefin.io" for name in ("ext-one", "ext-two")]

    def tearDown(self):
        self.tmp.cleanup()

    def _run_eval(self, expected_channels="stable,development"):
        return subprocess.run(
            [
                sys.executable,
                str(EVAL_SCRIPT),
                "--artifacts-dir",
                str(self.artifacts_dir),
                "--extensions-dir",
                str(self.ext_dir),
                "--expected-channels",
                expected_channels,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_passes_when_all_expected_channels_pass(self):
        for ch in ("stable", "development"):
            doc = {
                "schema_version": "1.0",
                "channel": ch,
                "infrastructure_error": None,
                "extensions": [{"uuid": u, "status": "pass", "phase": None} for u in self.uuids],
            }
            (self.artifacts_dir / f"compat-results-{ch}.json").write_text(json.dumps(doc))

        proc = self._run_eval()
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("All expected extensions passed", proc.stdout)

    def test_fails_when_artifact_is_missing(self):
        # Only write stable
        doc = {
            "schema_version": "1.0",
            "channel": "stable",
            "infrastructure_error": None,
            "extensions": [{"uuid": u, "status": "pass", "phase": None} for u in self.uuids],
        }
        (self.artifacts_dir / "compat-results-stable.json").write_text(json.dumps(doc))

        proc = self._run_eval()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Missing required artifact for channel 'development'", proc.stderr)

    def test_fails_closed_on_duplicate_ambiguous_artifacts(self):
        # Write both flat and nested for stable
        doc = {
            "schema_version": "1.0",
            "channel": "stable",
            "infrastructure_error": None,
            "extensions": [{"uuid": u, "status": "pass", "phase": None} for u in self.uuids],
        }
        (self.artifacts_dir / "compat-results-stable.json").write_text(json.dumps(doc))
        nested = self.artifacts_dir / "subfolder"
        nested.mkdir()
        (nested / "compat-results-stable.json").write_text(json.dumps(doc))

        proc = self._run_eval(expected_channels="stable")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Ambiguous multiple artifacts found for channel 'stable'", proc.stderr)

    def test_fails_on_infrastructure_error_in_artifact(self):
        doc = {
            "schema_version": "1.0",
            "channel": "stable",
            "infrastructure_error": "QEMU boot failed",
            "extensions": [],
        }
        (self.artifacts_dir / "compat-results-stable.json").write_text(json.dumps(doc))

        proc = self._run_eval(expected_channels="stable")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Channel reported infrastructure_error: QEMU boot failed", proc.stderr)


class TestFileCompatIssue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.artifacts_dir = Path(self.tmp.name) / "artifacts"
        self.artifacts_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _run_issue_script(self, extra_args=None):
        args = [
            sys.executable,
            str(ISSUE_SCRIPT),
            "--artifacts-dir",
            str(self.artifacts_dir),
        ]
        if extra_args:
            args.extend(extra_args)
        return subprocess.run(args, capture_output=True, text=True, check=False)

    def test_fails_on_missing_artifacts_directory_or_empty_files(self):
        proc = self._run_issue_script()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("No compat-results-*.json files found", proc.stderr)

    def test_fails_when_workflow_concluded_failure(self):
        proc = self._run_issue_script(["--workflow-conclusion", "failure"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("concluded 'failure' with 0 artifacts", proc.stderr)

    def test_reports_infrastructure_errors_as_failure(self):
        doc = {
            "schema_version": "1.0",
            "channel": "stable",
            "infrastructure_error": "VM crash on startup",
            "extensions": [],
        }
        (self.artifacts_dir / "compat-results-stable.json").write_text(json.dumps(doc))

        proc = self._run_issue_script(["--expected-channels", "stable"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Compatibility run reported infrastructure errors", proc.stderr)
    def test_fails_on_invalid_extension_status(self):
        doc = {
            "schema_version": "1.0",
            "channel": "stable",
            "infrastructure_error": None,
            "extensions": [
                {
                    "uuid": "ext-one@projectbluefin.io",
                    "status": "error",
                    "phase": "enable",
                    "diagnostics": "unexpected failure",
                }
            ],
        }
        (self.artifacts_dir / "compat-results-stable.json").write_text(json.dumps(doc))

        proc = self._run_issue_script(["--expected-channels", "stable"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Invalid status 'error'", proc.stderr)

    def test_fails_closed_on_ambiguous_duplicate_artifacts(self):
        doc = {
            "schema_version": "1.0",
            "channel": "stable",
            "infrastructure_error": None,
            "extensions": [],
        }
        (self.artifacts_dir / "compat-results-stable.json").write_text(json.dumps(doc))
        sub = self.artifacts_dir / "nested"
        sub.mkdir()
        (sub / "compat-results-stable.json").write_text(json.dumps(doc))

        proc = self._run_issue_script(["--expected-channels", "stable"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Ambiguous multiple artifacts found for channel 'stable'", proc.stderr)

    def test_fails_when_workflow_concluded_failure_even_with_passing_artifacts(self):
        doc = {
            "schema_version": "1.0",
            "channel": "stable",
            "infrastructure_error": None,
            "extensions": [
                {
                    "uuid": "ext-one@projectbluefin.io",
                    "status": "pass",
                    "phase": None,
                }
            ],
        }
        (self.artifacts_dir / "compat-results-stable.json").write_text(json.dumps(doc))

        proc = self._run_issue_script(["--expected-channels", "stable", "--workflow-conclusion", "failure"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Triggering workflow concluded with 'failure'", proc.stderr)


if __name__ == "__main__":
    unittest.main()
