#!/usr/bin/env python3
"""Automatically files GitHub issues when GNOME OS extension compatibility tests fail.

Security & Trust Boundary:
- Runs in a trusted context (default branch workflow_run) with issues:write.
- Validates all input data against a strict schema (types, bounds, allowed values).
- Rejects malformed artifacts, unknown channels, or invalid phases.
- Bounds diagnostics string length to prevent unbounded issue sizes.
- Sanitizes code fences to prevent markdown breakout.
- Fails closed if expected channels are missing or if any failure could not be filed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

VALID_CHANNELS = {"stable", "development"}
VALID_PHASES = {"metadata", "install/schema", "load", "enable", "behavior", "teardown"}
UUID_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+@projectbluefin\.io$")
MAX_DIAGNOSTICS_LEN = 8192


def run_gh_cmd(argv: list[str]) -> tuple[int, str, str]:
    res = subprocess.run(["gh"] + argv, capture_output=True, text=True, check=False)
    return res.returncode, res.stdout.strip(), res.stderr.strip()


def check_existing_open_issue(title: str) -> tuple[bool, str | None]:
    """Check if an open issue with the exact title already exists.

    Returns (exists, error_message).
    """
    code, stdout, stderr = run_gh_cmd([
        "issue", "list",
        "--state", "open",
        "--search", f'"{title}" in:title',
        "--json", "number,title"
    ])
    if code != 0:
        return False, f"gh issue list failed ({code}): {stderr}"

    try:
        issues = json.loads(stdout)
        if not isinstance(issues, list):
            return False, "Unexpected output structure from gh issue list"
        for issue in issues:
            if isinstance(issue, dict) and issue.get("title", "").strip() == title.strip():
                return True, None
    except Exception as exc:
        return False, f"Failed parsing gh issue list JSON: {exc}"

    return False, None


def create_issue(title: str, body: str) -> bool:
    code, stdout, stderr = run_gh_cmd([
        "issue", "create",
        "--title", title,
        "--label", "1-triage,bug",
        "--body", body
    ])
    if code != 0:
        print(f"::error::Failed to create issue '{title}': {stderr}", file=sys.stderr)
        return False

    print(f"Created issue: {stdout}")
    return True


def sanitize_text(text: str, max_len: int = MAX_DIAGNOSTICS_LEN) -> str:
    """Trim string to safe maximum length, strip NUL, and neutralize backtick fences."""
    clean = text.replace("\0", "").strip()
    clean = clean.replace("```", "`\u200b``")
    if len(clean) > max_len:
        clean = clean[:max_len] + f"\n... [truncated to {max_len} characters]"
    return clean


def main() -> None:
    parser = argparse.ArgumentParser(description="File GitHub issues for broken extensions")
    parser.add_argument("--artifacts-dir", required=True, help="Directory containing compat artifacts")
    parser.add_argument("--run-url", default="", help="GitHub Actions run URL")
    parser.add_argument(
        "--expected-channels",
        default="stable,development",
        help="Comma-separated expected channel names",
    )
    parser.add_argument(
        "--workflow-conclusion",
        default="",
        help="Conclusion of triggering workflow run (e.g. success, failure)",
    )
    args = parser.parse_args()

    artifacts_dir = Path(args.artifacts_dir)
    if not artifacts_dir.is_dir():
        print(f"::error::Artifacts directory {artifacts_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    expected_channels = {c.strip() for c in args.expected_channels.split(",") if c.strip()}
    artifact_files = sorted(artifacts_dir.glob("**/compat-results-*.json"))
    if not artifact_files:
        if args.workflow_conclusion and args.workflow_conclusion != "success":
            print(f"::error::Compatibility workflow concluded '{args.workflow_conclusion}' with 0 artifacts", file=sys.stderr)
        else:
            print("::error::No compat-results-*.json files found; missing required artifacts", file=sys.stderr)
        sys.exit(1)

    # Enforce exactly one artifact per expected channel
    channel_files: dict[str, Path] = {}
    has_errors = False
    found_infra_failures: list[str] = []

    for ch in expected_channels:
        matches = sorted(set(artifacts_dir.glob(f"**/compat-results-{ch}.json")))
        if not matches:
            print(f"::error::Missing required artifact for channel '{ch}'", file=sys.stderr)
            has_errors = True
        elif len(matches) > 1:
            print(f"::error::Ambiguous multiple artifacts found for channel '{ch}': {[str(m) for m in matches]}", file=sys.stderr)
            has_errors = True
        else:
            channel_files[ch] = matches[0]

    for channel, file_path in sorted(channel_files.items()):
        try:
            doc = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"::error::Failed to parse JSON in {file_path}: {exc}", file=sys.stderr)
            has_errors = True
            continue

        if not isinstance(doc, dict):
            print(f"::error::Artifact {file_path} is not a valid JSON object", file=sys.stderr)
            has_errors = True
            continue

        # Schema version check
        if doc.get("schema_version") != "1.0":
            print(f"::error::Unsupported schema_version in {file_path}", file=sys.stderr)
            has_errors = True
            continue

        doc_channel = doc.get("channel")
        if doc_channel != channel:
            print(f"::error::Channel in file '{doc_channel}' does not match expected '{channel}' in {file_path}", file=sys.stderr)
            has_errors = True
            continue

        if doc.get("infrastructure_error"):
            found_infra_failures.append(f"{channel}: {doc['infrastructure_error']}")
            continue

        shell_version = str(doc.get("shell_version") or "unknown")
        raw_extensions = doc.get("extensions")
        if not isinstance(raw_extensions, list):
            print(f"::error::extensions field must be a list in {file_path}", file=sys.stderr)
            has_errors = True
            continue

        for idx, ext in enumerate(raw_extensions):
            if not isinstance(ext, dict):
                print(f"::error::extensions[{idx}] is not an object in {file_path}", file=sys.stderr)
                has_errors = True
                continue

            status = ext.get("status")
            if status not in ("pass", "fail"):
                print(f"::error::Invalid status '{status}' for extension in {file_path}", file=sys.stderr)
                has_errors = True
                continue
            if status != "fail":
                continue

            uuid = ext.get("uuid", "")
            if not isinstance(uuid, str) or not UUID_PATTERN.match(uuid):
                print(f"::error::Invalid extension UUID '{uuid}' in {file_path}", file=sys.stderr)
                has_errors = True
                continue

            phase = ext.get("phase")
            if phase not in VALID_PHASES:
                print(f"::error::Invalid phase '{phase}' for {uuid} in {file_path}", file=sys.stderr)
                has_errors = True
                continue

            raw_diagnostics = str(ext.get("diagnostics") or "No details provided")
            diagnostics = sanitize_text(raw_diagnostics)

            title = f"bug(compat): {uuid} failing on GNOME {channel} ({phase})"

            exists, err = check_existing_open_issue(title)
            if err:
                print(f"::error::Failed checking existing issue for '{title}': {err}", file=sys.stderr)
                has_errors = True
                continue

            if exists:
                print(f"Existing open issue found for '{title}'; skipping duplicate creation.")
                continue

            safe_run_url = sanitize_text(args.run_url, max_len=512) or "N/A"
            body = f"""# Extension Compatibility Failure

| Field | Details |
|---|---|
| **Extension** | `{uuid}` |
| **Channel** | `{channel}` |
| **GNOME Shell Version** | `{sanitize_text(shell_version, 64)}` |
| **Failure Phase** | `{phase}` |
| **Run URL** | {safe_run_url} |

## Diagnostics
```text
{diagnostics}
```

*Automated compatibility test report from Project Bluefin CI.*
"""
            print(f"Reporting broken extension: {title}")
            ok = create_issue(title, body)
            if not ok:
                has_errors = True

    if found_infra_failures:
        print(f"::error::Compatibility run reported infrastructure errors: {found_infra_failures}", file=sys.stderr)
        has_errors = True
    if args.workflow_conclusion and args.workflow_conclusion != "success":
        print(f"::error::Triggering workflow concluded with '{args.workflow_conclusion}'", file=sys.stderr)
        has_errors = True

    if has_errors:
        print("\n::error::One or more errors occurred while processing compat failures.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
