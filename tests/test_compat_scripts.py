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
RESOLVE_SCRIPT = SCRIPTS_DIR / "resolve_gnome_channels.py"


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


class TestResolveGnomeChannels(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(SCRIPTS_DIR))
        import resolve_gnome_channels
        self.mod = resolve_gnome_channels

    def tearDown(self):
        sys.path.remove(str(SCRIPTS_DIR))

    def test_resolve_tag_digest_validates_sha256_format(self):
        valid_hash = "a" * 64
        mock_data = json.dumps({"tags": [{"manifest_digest": f"sha256:{valid_hash}"}]})
        original_fetch = self.mod.fetch_url
        try:
            self.mod.fetch_url = lambda url, timeout=15: mock_data
            digest = self.mod.resolve_tag_digest("gnomeos-48")
            self.assertEqual(digest, f"sha256:{valid_hash}")
        finally:
            self.mod.fetch_url = original_fetch

    def test_resolve_tag_digest_rejects_invalid_digest_format(self):
        mock_data = json.dumps({"tags": [{"manifest_digest": "not-a-valid-sha256"}]})
        original_fetch = self.mod.fetch_url
        try:
            self.mod.fetch_url = lambda url, timeout=15: mock_data
            with self.assertRaises(ValueError) as ctx:
                self.mod.resolve_tag_digest("gnomeos-48")
            self.assertIn("Invalid manifest_digest format", str(ctx.exception))
        finally:
            self.mod.fetch_url = original_fetch

    def test_resolve_tag_digest_rejects_trailing_newline(self):
        valid_hash = "a" * 64
        mock_data = json.dumps({"tags": [{"manifest_digest": f"sha256:{valid_hash}\n"}]})
        original_fetch = self.mod.fetch_url
        try:
            self.mod.fetch_url = lambda url, timeout=15: mock_data
            with self.assertRaises(ValueError) as ctx:
                self.mod.resolve_tag_digest("gnomeos-48")
            self.assertIn("Invalid manifest_digest format", str(ctx.exception))
        finally:
            self.mod.fetch_url = original_fetch

    def test_discover_latest_stable_major_extracts_version(self):
        atom_xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>Introducing GNOME 50</title>
          </entry>
        </feed>"""
        original_fetch = self.mod.fetch_url
        try:
            self.mod.fetch_url = lambda url, timeout=15: atom_xml
            major = self.mod.discover_latest_stable_major()
            self.assertEqual(major, "50")
        finally:
            self.mod.fetch_url = original_fetch


class TestCheckExistingOpenIssue(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(SCRIPTS_DIR))
        import file_compat_issue
        self.mod = file_compat_issue

    def tearDown(self):
        sys.path.remove(str(SCRIPTS_DIR))

    def test_check_existing_open_issue_matches_exact_title(self):
        exact_title = "bug(compat): ext@projectbluefin.io failing on GNOME stable (enable)"
        mock_issues = [{"number": 12, "title": exact_title}]
        original_run_gh = self.mod.run_gh_cmd
        captured_args = []
        try:
            def mock_run_gh(argv):
                captured_args.append(argv)
                return 0, json.dumps(mock_issues), ""
            self.mod.run_gh_cmd = mock_run_gh
            exists, err = self.mod.check_existing_open_issue(
                "ext@projectbluefin.io", "stable", exact_title
            )
            self.assertIsNone(err)
            self.assertTrue(exists)
            self.assertIn("ext@projectbluefin.io stable in:title", captured_args[0])
        finally:
            self.mod.run_gh_cmd = original_run_gh

    def test_check_existing_open_issue_rejects_different_title(self):
        exact_title = "bug(compat): ext@projectbluefin.io failing on GNOME stable (enable)"
        different_title = "bug(compat): ext@projectbluefin.io failing on GNOME stable (install/schema)"
        mock_issues = [{"number": 12, "title": different_title}]
        original_run_gh = self.mod.run_gh_cmd
        try:
            self.mod.run_gh_cmd = lambda argv: (0, json.dumps(mock_issues), "")
            exists, err = self.mod.check_existing_open_issue(
                "ext@projectbluefin.io", "stable", exact_title
            )
            self.assertIsNone(err)
            self.assertFalse(exists)
        finally:
            self.mod.run_gh_cmd = original_run_gh

    def test_evaluate_aggregate_compat_fails_on_empty_expected_channels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            res = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "evaluate_aggregate_compat.py"),
                    "--artifacts-dir",
                    tmpdir,
                    "--expected-channels",
                    "",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res.returncode, 1)
            self.assertIn("Expected channels list cannot be empty", res.stderr)

    def test_file_compat_issue_fails_on_empty_expected_channels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            res = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "file_compat_issue.py"),
                    "--artifacts-dir",
                    tmpdir,
                    "--expected-channels",
                    "",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res.returncode, 1)
            self.assertIn("Expected channels list cannot be empty", res.stderr)

if __name__ == "__main__":
    unittest.main()
