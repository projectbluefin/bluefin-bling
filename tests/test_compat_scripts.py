"""Unit tests for compatibility aggregation and issue reporting scripts."""

from __future__ import annotations

import contextlib
import io
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


@contextlib.contextmanager
def silenced():
    """Swallow a script's own stdout/stderr while driving `main()` in-process.

    These scripts report through `print`, so without this every in-process
    `main()` call would dump a matrix or an ``::error::`` line into the test log
    and bury a real failure.
    """
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield


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


class TestSanitizeText(unittest.TestCase):
    """`sanitize_text` is the markdown/NUL containment boundary for issue bodies."""

    def setUp(self):
        sys.path.insert(0, str(SCRIPTS_DIR))
        import file_compat_issue
        self.mod = file_compat_issue

    def tearDown(self):
        sys.path.remove(str(SCRIPTS_DIR))

    def test_strips_nul_bytes(self):
        self.assertEqual(self.mod.sanitize_text("a\0b"), "ab")

    def test_neutralizes_code_fence_breakout(self):
        cleaned = self.mod.sanitize_text("```\nnot a fence\n```")
        self.assertNotIn("```", cleaned)
        self.assertIn("\u200b", cleaned)

    def test_truncates_beyond_max_len_and_says_so(self):
        cleaned = self.mod.sanitize_text("x" * 50, max_len=10)
        self.assertTrue(cleaned.startswith("x" * 10))
        self.assertIn("truncated to 10 characters", cleaned)

    def test_keeps_text_at_exactly_max_len_untruncated(self):
        cleaned = self.mod.sanitize_text("x" * 10, max_len=10)
        self.assertEqual(cleaned, "x" * 10)

    def test_default_max_len_is_the_module_bound(self):
        cleaned = self.mod.sanitize_text("x" * (self.mod.MAX_DIAGNOSTICS_LEN + 1))
        self.assertIn(f"truncated to {self.mod.MAX_DIAGNOSTICS_LEN} characters", cleaned)


class TestRunGhCmd(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(SCRIPTS_DIR))
        import file_compat_issue
        self.mod = file_compat_issue

    def tearDown(self):
        sys.path.remove(str(SCRIPTS_DIR))

    def test_prefixes_gh_and_strips_streams(self):
        captured = {}

        class FakeCompleted:
            returncode = 0
            stdout = "  out  \n"
            stderr = "  err  \n"

        original = self.mod.subprocess.run
        try:
            def fake_run(argv, **kwargs):
                captured["argv"] = argv
                captured["kwargs"] = kwargs
                return FakeCompleted()

            self.mod.subprocess.run = fake_run
            code, out, err = self.mod.run_gh_cmd(["issue", "list"])
        finally:
            self.mod.subprocess.run = original

        self.assertEqual(captured["argv"], ["gh", "issue", "list"])
        self.assertFalse(captured["kwargs"]["check"])
        self.assertEqual((code, out, err), (0, "out", "err"))


class TestCreateIssue(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(SCRIPTS_DIR))
        import file_compat_issue
        self.mod = file_compat_issue

    def tearDown(self):
        sys.path.remove(str(SCRIPTS_DIR))

    def test_passes_title_body_and_triage_labels(self):
        captured = []
        original = self.mod.run_gh_cmd
        try:
            self.mod.run_gh_cmd = lambda argv: (captured.append(argv), (0, "https://x/1", ""))[1]
            with silenced():
                self.assertTrue(self.mod.create_issue("t", "b"))
        finally:
            self.mod.run_gh_cmd = original

        argv = captured[0]
        self.assertEqual(argv[:2], ["issue", "create"])
        self.assertEqual(argv[argv.index("--title") + 1], "t")
        self.assertEqual(argv[argv.index("--body") + 1], "b")
        self.assertEqual(argv[argv.index("--label") + 1], "1-triage,bug")

    def test_returns_false_when_gh_fails(self):
        original = self.mod.run_gh_cmd
        try:
            self.mod.run_gh_cmd = lambda argv: (1, "", "boom")
            with silenced():
                self.assertFalse(self.mod.create_issue("t", "b"))
        finally:
            self.mod.run_gh_cmd = original


class TestCheckExistingOpenIssueErrorPaths(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(SCRIPTS_DIR))
        import file_compat_issue
        self.mod = file_compat_issue

    def tearDown(self):
        sys.path.remove(str(SCRIPTS_DIR))

    def _check(self):
        return self.mod.check_existing_open_issue("ext@projectbluefin.io", "stable", "title")

    def test_reports_gh_failure_rather_than_claiming_absence(self):
        original = self.mod.run_gh_cmd
        try:
            self.mod.run_gh_cmd = lambda argv: (2, "", "gh exploded")
            exists, err = self._check()
        finally:
            self.mod.run_gh_cmd = original
        self.assertFalse(exists)
        self.assertIn("gh issue list failed (2)", err)
        self.assertIn("gh exploded", err)

    def test_rejects_non_list_json(self):
        original = self.mod.run_gh_cmd
        try:
            self.mod.run_gh_cmd = lambda argv: (0, json.dumps({"number": 1}), "")
            exists, err = self._check()
        finally:
            self.mod.run_gh_cmd = original
        self.assertFalse(exists)
        self.assertEqual(err, "Unexpected output structure from gh issue list")

    def test_reports_unparseable_json(self):
        original = self.mod.run_gh_cmd
        try:
            self.mod.run_gh_cmd = lambda argv: (0, "{not json", "")
            exists, err = self._check()
        finally:
            self.mod.run_gh_cmd = original
        self.assertFalse(exists)
        self.assertIn("Failed parsing gh issue list JSON", err)

    def test_ignores_non_dict_entries(self):
        original = self.mod.run_gh_cmd
        try:
            self.mod.run_gh_cmd = lambda argv: (0, json.dumps(["title", None]), "")
            exists, err = self._check()
        finally:
            self.mod.run_gh_cmd = original
        self.assertFalse(exists)
        self.assertIsNone(err)


class TestFileCompatIssueReportingPath(unittest.TestCase):
    """Drives `main()` in-process so the failure -> issue body path is executed.

    The subprocess tests elsewhere in this module can only observe exit codes;
    they cannot see the issue that would be filed, because `gh` is never
    reachable under test. These stub `run_gh_cmd` instead.
    """

    def setUp(self):
        sys.path.insert(0, str(SCRIPTS_DIR))
        import file_compat_issue
        self.mod = file_compat_issue
        self.tmp = tempfile.TemporaryDirectory()
        self.artifacts_dir = Path(self.tmp.name) / "artifacts"
        self.artifacts_dir.mkdir(parents=True)
        self._original_run_gh = self.mod.run_gh_cmd
        self._original_argv = sys.argv

    def tearDown(self):
        self.mod.run_gh_cmd = self._original_run_gh
        sys.argv = self._original_argv
        self.tmp.cleanup()
        sys.path.remove(str(SCRIPTS_DIR))

    def _write_artifact(self, channel="stable", **ext_overrides):
        ext = {
            "uuid": "ext-one@projectbluefin.io",
            "status": "fail",
            "phase": "enable",
            "diagnostics": "stack trace here",
        }
        ext.update(ext_overrides)
        doc = {
            "schema_version": "1.0",
            "channel": channel,
            "shell_version": "50.1",
            "extensions": [ext],
        }
        (self.artifacts_dir / f"compat-results-{channel}.json").write_text(
            json.dumps(doc), encoding="utf-8"
        )

    def _run_main(self, run_url="https://run/1"):
        sys.argv = [
            "file_compat_issue.py",
            "--artifacts-dir",
            str(self.artifacts_dir),
            "--expected-channels",
            "stable",
            "--run-url",
            run_url,
        ]
        with silenced():
            self.mod.main()

    def _stub_gh(self, existing_titles=(), create_code=0):
        calls = []

        def fake_run_gh(argv):
            calls.append(argv)
            if argv[:2] == ["issue", "list"]:
                return 0, json.dumps([{"number": 1, "title": t} for t in existing_titles]), ""
            return create_code, "https://x/1", "denied"

        self.mod.run_gh_cmd = fake_run_gh
        return calls

    def test_files_an_issue_for_a_failing_extension(self):
        self._write_artifact()
        calls = self._stub_gh()
        self._run_main()

        creates = [c for c in calls if c[:2] == ["issue", "create"]]
        self.assertEqual(len(creates), 1)
        argv = creates[0]
        title = argv[argv.index("--title") + 1]
        body = argv[argv.index("--body") + 1]
        self.assertEqual(
            title,
            "bug(compat): ext-one@projectbluefin.io failing on GNOME stable (enable)",
        )
        self.assertIn("`ext-one@projectbluefin.io`", body)
        self.assertIn("`50.1`", body)
        self.assertIn("`enable`", body)
        self.assertIn("https://run/1", body)
        self.assertIn("stack trace here", body)

    def test_sanitizes_diagnostics_into_the_issue_body(self):
        self._write_artifact(diagnostics="```\nbreakout\n```")
        calls = self._stub_gh()
        self._run_main()

        create = [c for c in calls if c[:2] == ["issue", "create"]][0]
        body = create[create.index("--body") + 1]
        diagnostics_block = body.split("## Diagnostics", 1)[1]
        self.assertIn("\u200b", diagnostics_block)
        # The only surviving bare fences are the ones the template itself opens
        # and closes around the diagnostics block.
        self.assertEqual(diagnostics_block.count("```"), 2)

    def test_missing_run_url_renders_as_not_available(self):
        self._write_artifact()
        calls = self._stub_gh()
        self._run_main(run_url="")

        create = [c for c in calls if c[:2] == ["issue", "create"]][0]
        body = create[create.index("--body") + 1]
        self.assertIn("| **Run URL** | N/A |", body)

    def test_skips_creation_when_an_identical_issue_is_open(self):
        self._write_artifact()
        calls = self._stub_gh(
            existing_titles=[
                "bug(compat): ext-one@projectbluefin.io failing on GNOME stable (enable)"
            ]
        )
        self._run_main()
        self.assertEqual([c for c in calls if c[:2] == ["issue", "create"]], [])

    def test_exits_nonzero_when_issue_creation_fails(self):
        self._write_artifact()
        self._stub_gh(create_code=1)
        with self.assertRaises(SystemExit) as ctx:
            self._run_main()
        self.assertEqual(ctx.exception.code, 1)

    def test_exits_nonzero_when_the_duplicate_check_fails(self):
        self._write_artifact()

        def fake_run_gh(argv):
            if argv[:2] == ["issue", "list"]:
                return 1, "", "rate limited"
            raise AssertionError("must not create an issue after a failed lookup")

        self.mod.run_gh_cmd = fake_run_gh
        with self.assertRaises(SystemExit) as ctx:
            self._run_main()
        self.assertEqual(ctx.exception.code, 1)

    def test_passing_extension_files_nothing(self):
        self._write_artifact(status="pass")
        calls = self._stub_gh()
        self._run_main()
        self.assertEqual(calls, [])

    def test_rejects_an_out_of_contract_uuid(self):
        self._write_artifact(uuid="evil@example.com")
        self._stub_gh()
        with self.assertRaises(SystemExit) as ctx:
            self._run_main()
        self.assertEqual(ctx.exception.code, 1)

    def test_rejects_an_unknown_phase(self):
        self._write_artifact(phase="teleport")
        self._stub_gh()
        with self.assertRaises(SystemExit) as ctx:
            self._run_main()
        self.assertEqual(ctx.exception.code, 1)

    def test_rejects_an_unknown_status(self):
        self._write_artifact(status="maybe")
        self._stub_gh()
        with self.assertRaises(SystemExit) as ctx:
            self._run_main()
        self.assertEqual(ctx.exception.code, 1)

    def test_missing_diagnostics_fall_back_to_a_placeholder(self):
        self._write_artifact(diagnostics=None)
        calls = self._stub_gh()
        self._run_main()
        create = [c for c in calls if c[:2] == ["issue", "create"]][0]
        self.assertIn("No details provided", create[create.index("--body") + 1])


class TestFileCompatIssueArtifactValidation(unittest.TestCase):
    """The artifact contract is fail-closed: anything unrecognised exits 1.

    Driven in-process so each malformed shape is asserted individually rather
    than through one opaque subprocess exit code.
    """

    def setUp(self):
        sys.path.insert(0, str(SCRIPTS_DIR))
        import file_compat_issue
        self.mod = file_compat_issue
        self.tmp = tempfile.TemporaryDirectory()
        self.artifacts_dir = Path(self.tmp.name) / "artifacts"
        self.artifacts_dir.mkdir(parents=True)
        self._original_run_gh = self.mod.run_gh_cmd
        self._original_argv = sys.argv

        def refuse(argv):
            raise AssertionError(f"gh must not be invoked for invalid input: {argv}")

        self.mod.run_gh_cmd = refuse

    def tearDown(self):
        self.mod.run_gh_cmd = self._original_run_gh
        sys.argv = self._original_argv
        self.tmp.cleanup()
        sys.path.remove(str(SCRIPTS_DIR))

    def _run_main(self, artifacts_dir=None, channels="stable"):
        sys.argv = [
            "file_compat_issue.py",
            "--artifacts-dir",
            str(artifacts_dir if artifacts_dir is not None else self.artifacts_dir),
            "--expected-channels",
            channels,
        ]
        with silenced():
            self.mod.main()

    def _assert_exits_1(self, **kwargs):
        with self.assertRaises(SystemExit) as ctx:
            self._run_main(**kwargs)
        self.assertEqual(ctx.exception.code, 1)

    def _write(self, name, payload):
        path = self.artifacts_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            payload if isinstance(payload, str) else json.dumps(payload),
            encoding="utf-8",
        )

    def _doc(self, **overrides):
        doc = {
            "schema_version": "1.0",
            "channel": "stable",
            "shell_version": "50.1",
            "extensions": [],
        }
        doc.update(overrides)
        return doc

    def test_missing_artifacts_directory(self):
        self._assert_exits_1(artifacts_dir=Path(self.tmp.name) / "absent")

    def test_unknown_expected_channel(self):
        self._write("compat-results-stable.json", self._doc())
        self._assert_exits_1(channels="stable,banana")

    def test_missing_artifact_for_an_expected_channel(self):
        self._write("compat-results-stable.json", self._doc())
        self._assert_exits_1(channels="stable,development")

    def test_ambiguous_duplicate_artifacts_for_one_channel(self):
        self._write("a/compat-results-stable.json", self._doc())
        self._write("b/compat-results-stable.json", self._doc())
        self._assert_exits_1()

    def test_unparseable_artifact_json(self):
        self._write("compat-results-stable.json", "{not json")
        self._assert_exits_1()

    def test_artifact_that_is_not_a_json_object(self):
        self._write("compat-results-stable.json", [1, 2, 3])
        self._assert_exits_1()

    def test_unsupported_schema_version(self):
        self._write("compat-results-stable.json", self._doc(schema_version="2.0"))
        self._assert_exits_1()

    def test_invalid_channel_in_document(self):
        self._write("compat-results-stable.json", self._doc(channel="banana"))
        self._assert_exits_1()

    def test_channel_in_document_disagrees_with_filename(self):
        self._write("compat-results-stable.json", self._doc(channel="development"))
        self._assert_exits_1()

    def test_extensions_field_is_not_a_list(self):
        self._write("compat-results-stable.json", self._doc(extensions={"uuid": "x"}))
        self._assert_exits_1()

    def test_extensions_entry_is_not_an_object(self):
        self._write("compat-results-stable.json", self._doc(extensions=["oops"]))
        self._assert_exits_1()

    def test_infrastructure_error_fails_the_run_without_filing(self):
        self._write(
            "compat-results-stable.json",
            self._doc(infrastructure_error="VM never booted"),
        )
        self._assert_exits_1()


class TestResolveGnomeChannelsMain(unittest.TestCase):
    """`main()` builds the matrix the feasibility workflow consumes."""

    def setUp(self):
        sys.path.insert(0, str(SCRIPTS_DIR))
        import resolve_gnome_channels
        self.mod = resolve_gnome_channels
        self.tmp = tempfile.TemporaryDirectory()
        self._originals = (
            self.mod.discover_latest_stable_major,
            self.mod.resolve_tag_digest,
            self.mod.os.environ.get("GITHUB_OUTPUT"),
        )

    def tearDown(self):
        self.mod.discover_latest_stable_major = self._originals[0]
        self.mod.resolve_tag_digest = self._originals[1]
        if self._originals[2] is None:
            self.mod.os.environ.pop("GITHUB_OUTPUT", None)
        else:
            self.mod.os.environ["GITHUB_OUTPUT"] = self._originals[2]
        self.tmp.cleanup()
        sys.path.remove(str(SCRIPTS_DIR))

    def _stub(self, major="50", digest="sha256:" + "b" * 64):
        self.mod.discover_latest_stable_major = lambda: major
        self.mod.resolve_tag_digest = lambda tag: digest

    def test_writes_matrix_and_stable_major_to_github_output(self):
        digest = "sha256:" + "b" * 64
        self._stub(digest=digest)
        out_path = Path(self.tmp.name) / "github_output"
        self.mod.os.environ["GITHUB_OUTPUT"] = str(out_path)

        with silenced():
            self.mod.main()

        lines = out_path.read_text(encoding="utf-8").splitlines()
        outputs = dict(line.split("=", 1) for line in lines)
        self.assertEqual(outputs["stable_major"], "50")
        include = json.loads(outputs["matrix"])["include"]
        self.assertEqual([e["channel"] for e in include], ["stable", "development"])
        stable, development = include
        self.assertEqual(stable["target_major"], "50")
        self.assertEqual(stable["image_tag"], "gnomeos-50")
        self.assertEqual(
            stable["image_digest"],
            f"{self.mod.REGISTRY_IMAGE_PREFIX}:gnomeos-50@{digest}",
        )
        # The nightly channel floats: pinning a target major would make the
        # matrix claim a release the nightly image does not correspond to.
        self.assertEqual(development["target_major"], "")
        self.assertEqual(development["image_tag"], "gnomeos-nightly")
        self.assertEqual(
            development["image_digest"],
            f"{self.mod.REGISTRY_IMAGE_PREFIX}:gnomeos-nightly@{digest}",
        )

    def test_every_matrix_entry_is_pinned_by_digest(self):
        self._stub()
        out_path = Path(self.tmp.name) / "github_output"
        self.mod.os.environ["GITHUB_OUTPUT"] = str(out_path)
        with silenced():
            self.mod.main()
        outputs = dict(
            line.split("=", 1)
            for line in out_path.read_text(encoding="utf-8").splitlines()
        )
        for entry in json.loads(outputs["matrix"])["include"]:
            self.assertIn("@sha256:", entry["image_digest"])

    def test_runs_without_github_output_set(self):
        self._stub()
        self.mod.os.environ.pop("GITHUB_OUTPUT", None)
        with silenced():
            self.mod.main()  # must not raise

    def test_exits_nonzero_when_stable_major_cannot_be_discovered(self):
        def boom():
            raise RuntimeError("atom feed down")

        self.mod.discover_latest_stable_major = boom
        with self.assertRaises(SystemExit) as ctx, silenced():
            self.mod.main()
        self.assertEqual(ctx.exception.code, 1)

    def test_exits_nonzero_when_a_digest_cannot_be_resolved(self):
        self._stub()

        def boom(tag):
            raise RuntimeError("quay down")

        self.mod.resolve_tag_digest = boom
        with self.assertRaises(SystemExit) as ctx, silenced():
            self.mod.main()
        self.assertEqual(ctx.exception.code, 1)


class TestResolveGnomeChannelsErrors(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(SCRIPTS_DIR))
        import resolve_gnome_channels
        self.mod = resolve_gnome_channels
        self._original_fetch = self.mod.fetch_url

    def tearDown(self):
        self.mod.fetch_url = self._original_fetch
        sys.path.remove(str(SCRIPTS_DIR))

    def test_discover_raises_when_no_entry_announces_a_release(self):
        self.mod.fetch_url = lambda url, timeout=15: (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            "<entry><title>Something else entirely</title></entry>"
            "</feed>"
        )
        with self.assertRaises(RuntimeError) as ctx:
            self.mod.discover_latest_stable_major()
        self.assertIn("Could not extract latest stable major", str(ctx.exception))

    def test_discover_skips_entries_without_a_title(self):
        self.mod.fetch_url = lambda url, timeout=15: (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            "<entry><summary>no title here</summary></entry>"
            "<entry><title>Introducing GNOME 51</title></entry>"
            "</feed>"
        )
        self.assertEqual(self.mod.discover_latest_stable_major(), "51")

    def test_resolve_tag_digest_raises_when_no_active_tag(self):
        self.mod.fetch_url = lambda url, timeout=15: json.dumps({"tags": []})
        with self.assertRaises(RuntimeError) as ctx:
            self.mod.resolve_tag_digest("gnomeos-50")
        self.assertIn("No active tag found", str(ctx.exception))

    def test_resolve_tag_digest_raises_when_digest_missing(self):
        self.mod.fetch_url = lambda url, timeout=15: json.dumps({"tags": [{}]})
        with self.assertRaises(RuntimeError) as ctx:
            self.mod.resolve_tag_digest("gnomeos-50")
        self.assertIn("No manifest_digest found", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
