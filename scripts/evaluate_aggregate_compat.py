#!/usr/bin/env python3
"""Fail-closed aggregate evaluator for GNOME OS compatibility matrix results.

Usage:
  python3 scripts/evaluate_aggregate_compat.py \
    --artifacts-dir artifacts \
    --expected-channels "stable,development" \
    --extensions-dir extensions

Exits 0 only if:
1. Artifacts exist for all expected channels without ambiguous duplicate matches.
2. schema_version == "1.0" and doc.channel matches expected channel name.
3. No infrastructure errors occurred.
4. Extension results match exactly the set of discovered production extensions:
   - every extension in extensions/*/metadata.json is present
   - no missing extensions, no extra extensions, no duplicate UUIDs
5. Every extension status == "pass" with valid phase constraints.
Otherwise exits 1 with actionable diagnostic output.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

VALID_STATUSES = {"pass", "fail"}
VALID_PHASES = {"metadata", "install/schema", "load", "enable", "behavior", "teardown"}


def discover_expected_uuids(extensions_dir: Path) -> set[str]:
    """Discover all production extension UUIDs declared in metadata.json."""
    uuids: set[str] = set()
    if not extensions_dir.is_dir():
        raise RuntimeError(f"Extensions directory not found: {extensions_dir}")

    for child in sorted(extensions_dir.iterdir()):
        meta_file = child / "metadata.json"
        if child.is_dir() and meta_file.is_file():
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                uuid = data.get("uuid")
                if uuid:
                    uuids.add(uuid)
                else:
                    raise ValueError(f"{meta_file} missing 'uuid'")
            except Exception as exc:
                raise RuntimeError(f"Failed to read metadata from {meta_file}: {exc}") from exc

    if not uuids:
        raise RuntimeError(f"No extensions found under {extensions_dir}")
    return uuids


def find_channel_artifact(artifacts_dir: Path, channel: str) -> tuple[Path | None, str | None]:
    """Find channel artifact by exact expected file names only.

    Returns (artifact_path, error_message).
    Fails closed if multiple matching candidate files exist for the channel.
    """
    matches = sorted(set(artifacts_dir.glob(f"**/compat-results-{channel}.json")))
    if not matches:
        return None, f"Missing required artifact for channel '{channel}' (expected compat-results-{channel}.json)"
    if len(matches) > 1:
        return None, f"Ambiguous multiple artifacts found for channel '{channel}': {[str(m) for m in matches]}"
    return matches[0], None


def evaluate_channel_artifact(
    artifact_path: Path, channel: str, expected_uuids: set[str]
) -> tuple[bool, list[str]]:
    """Validate schema, exact UUID coverage, and pass status for a single channel."""
    errors: list[str] = []

    try:
        doc = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, [f"Failed to parse JSON in {artifact_path}: {exc}"]

    if not isinstance(doc, dict):
        return False, [f"Artifact {artifact_path} is not a valid JSON object"]

    # 1. Schema version and channel identity check
    schema_version = doc.get("schema_version")
    if schema_version != "1.0":
        errors.append(f"Invalid schema_version '{schema_version}' (expected '1.0')")

    doc_channel = doc.get("channel")
    if doc_channel != channel:
        errors.append(f"doc.channel '{doc_channel}' does not match expected '{channel}'")

    # 2. Infrastructure error check
    if doc.get("infrastructure_error"):
        errors.append(f"Channel reported infrastructure_error: {doc['infrastructure_error']}")
        return False, errors

    # 3. Extension results validation
    raw_extensions = doc.get("extensions")
    if not isinstance(raw_extensions, list):
        errors.append("doc.extensions must be a list")
        return False, errors

    seen_uuids: set[str] = set()
    result_uuids: set[str] = set()

    for idx, ext in enumerate(raw_extensions):
        if not isinstance(ext, dict):
            errors.append(f"extensions[{idx}] is not an object")
            continue

        uuid = ext.get("uuid")
        if not isinstance(uuid, str) or not uuid:
            errors.append(f"extensions[{idx}] missing valid string 'uuid'")
            continue

        if uuid in seen_uuids:
            errors.append(f"Duplicate result for extension UUID '{uuid}'")
        seen_uuids.add(uuid)
        result_uuids.add(uuid)

        status = ext.get("status")
        if status not in VALID_STATUSES:
            errors.append(f"Extension '{uuid}' has invalid status '{status}' (expected {VALID_STATUSES})")
            continue

        phase = ext.get("phase")
        if status == "fail":
            if phase not in VALID_PHASES:
                errors.append(f"Failed extension '{uuid}' has invalid phase '{phase}' (expected {VALID_PHASES})")
            errors.append(f"Extension '{uuid}' FAILED in phase '{phase}': {ext.get('diagnostics')}")
        elif status == "pass":
            if phase is not None:
                errors.append(f"Passing extension '{uuid}' must have phase=null, got '{phase}'")

    # 4. Strict exact-set comparison against production extensions
    missing_uuids = expected_uuids - result_uuids
    extra_uuids = result_uuids - expected_uuids

    if missing_uuids:
        errors.append(f"Missing results for expected extensions: {sorted(missing_uuids)}")
    if extra_uuids:
        errors.append(f"Unexpected extra extension results: {sorted(extra_uuids)}")

    return len(errors) == 0, errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate aggregate compatibility results")
    parser.add_argument("--artifacts-dir", required=True, help="Directory containing downloaded artifacts")
    parser.add_argument(
        "--expected-channels",
        default="stable,development",
        help="Comma-separated list of expected channel names",
    )
    parser.add_argument(
        "--extensions-dir",
        default="extensions",
        help="Path to repository extensions directory",
    )
    args = parser.parse_args()

    artifacts_dir = Path(args.artifacts_dir)
    extensions_dir = Path(args.extensions_dir)
    expected_channels = [c.strip() for c in args.expected_channels.split(",") if c.strip()]

    try:
        expected_uuids = discover_expected_uuids(extensions_dir)
    except Exception as exc:
        print(f"::error::Failed discovering extensions: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Evaluating aggregate compatibility results in {artifacts_dir}...")
    print(f"Expected channels: {expected_channels}")
    print(f"Expected extension UUIDs ({len(expected_uuids)}): {sorted(expected_uuids)}")

    overall_failed = False

    for channel in expected_channels:
        artifact_path, err = find_channel_artifact(artifacts_dir, channel)
        if err or not artifact_path:
            print(f"::error::{err}", file=sys.stderr)
            overall_failed = True
            continue

        print(f"\nValidating {channel} results from {artifact_path}...")
        ok, errors = evaluate_channel_artifact(artifact_path, channel, expected_uuids)
        if not ok:
            overall_failed = True
            for err_msg in errors:
                print(f"::error::[{channel}] {err_msg}", file=sys.stderr)
        else:
            print(f"  ✓ {channel}: all {len(expected_uuids)} extensions validated and PASS")

    if overall_failed:
        print("\n::error::Aggregate compatibility evaluation failed.", file=sys.stderr)
        sys.exit(1)

    print("\n✓ All expected extensions passed compatibility matrix across all channels.")


if __name__ == "__main__":
    main()
