"""Manifest invariants for every extension under ``extensions/``.

A bad ``metadata.json`` is a silent installation failure: GNOME Shell refuses to
load an extension whose ``uuid`` does not match its install directory, and
``gnome-extensions`` reports nothing useful. These tests are the automated form
of the manual JSON checks listed in AGENTS.md, minus the hardcoded file list.
"""

from __future__ import annotations

import json
import unittest

from extension_manifest import (
    EXTENSIONS_DIR,
    MIN_SHELL_VERSION,
    REPO_URL,
    UUID_DOMAIN,
    extension_dirs,
    load_metadata,
    metadata_path,
    schema_files,
)

REQUIRED_KEYS = ("uuid", "name", "description", "shell-version")


class TestExtensionsDiscovered(unittest.TestCase):
    def test_extensions_directory_exists(self):
        self.assertTrue(
            EXTENSIONS_DIR.is_dir(),
            f"{EXTENSIONS_DIR} is missing; the monorepo layout in AGENTS.md requires it",
        )

    def test_at_least_one_extension_is_present(self):
        self.assertTrue(
            extension_dirs(),
            "no extension folders found under extensions/ — discovery is broken",
        )


class TestMetadataJson(unittest.TestCase):
    def test_every_extension_has_metadata_json(self):
        for ext_dir in extension_dirs():
            with self.subTest(extension=ext_dir.name):
                self.assertTrue(
                    metadata_path(ext_dir).is_file(),
                    f"extensions/{ext_dir.name}/metadata.json is missing",
                )

    def test_metadata_json_is_valid_json_object(self):
        for ext_dir in extension_dirs():
            with self.subTest(extension=ext_dir.name):
                try:
                    data = load_metadata(ext_dir)
                except json.JSONDecodeError as exc:
                    self.fail(f"extensions/{ext_dir.name}/metadata.json is not valid JSON: {exc}")
                self.assertIsInstance(data, dict)

    def test_required_keys_present_and_non_empty(self):
        for ext_dir in extension_dirs():
            data = load_metadata(ext_dir)
            for key in REQUIRED_KEYS:
                with self.subTest(extension=ext_dir.name, key=key):
                    self.assertIn(key, data, f"{ext_dir.name}: metadata.json lacks '{key}'")
                    self.assertTrue(data[key], f"{ext_dir.name}: metadata.json '{key}' is empty")

    def test_uuid_matches_directory_name_and_domain(self):
        for ext_dir in extension_dirs():
            with self.subTest(extension=ext_dir.name):
                uuid = load_metadata(ext_dir)["uuid"]
                self.assertEqual(
                    uuid,
                    f"{ext_dir.name}@{UUID_DOMAIN}",
                    f"{ext_dir.name}: uuid must be '<folder>@{UUID_DOMAIN}' or GNOME Shell "
                    "will refuse to load the installed extension",
                )

    def test_uuids_are_unique(self):
        seen: dict[str, str] = {}
        for ext_dir in extension_dirs():
            uuid = load_metadata(ext_dir)["uuid"]
            with self.subTest(extension=ext_dir.name):
                self.assertNotIn(
                    uuid, seen, f"uuid {uuid} used by both {seen.get(uuid)} and {ext_dir.name}"
                )
            seen[uuid] = ext_dir.name

    def test_shell_version_is_list_of_ascending_numeric_strings(self):
        for ext_dir in extension_dirs():
            with self.subTest(extension=ext_dir.name):
                versions = load_metadata(ext_dir)["shell-version"]
                self.assertIsInstance(
                    versions, list, f"{ext_dir.name}: shell-version must be a JSON array"
                )
                for entry in versions:
                    self.assertIsInstance(
                        entry, str, f"{ext_dir.name}: shell-version entries must be strings"
                    )
                    self.assertRegex(
                        entry,
                        r"^\d+$",
                        f"{ext_dir.name}: shell-version entry {entry!r} is not a major version",
                    )
                numeric = [int(v) for v in versions]
                self.assertEqual(
                    numeric,
                    sorted(numeric),
                    f"{ext_dir.name}: shell-version must be ascending",
                )
                self.assertEqual(
                    len(numeric), len(set(numeric)), f"{ext_dir.name}: duplicate shell-version"
                )
                self.assertGreaterEqual(
                    min(numeric),
                    MIN_SHELL_VERSION,
                    f"{ext_dir.name}: repo targets GNOME {MIN_SHELL_VERSION}+ (ESM only)",
                )

    def test_version_is_positive_integer_when_present(self):
        for ext_dir in extension_dirs():
            data = load_metadata(ext_dir)
            if "version" not in data:
                continue
            with self.subTest(extension=ext_dir.name):
                self.assertIsInstance(
                    data["version"],
                    int,
                    f"{ext_dir.name}: metadata 'version' must be an integer, not a string",
                )
                self.assertGreater(data["version"], 0)

    def test_url_points_at_this_repo_when_present(self):
        for ext_dir in extension_dirs():
            data = load_metadata(ext_dir)
            if "url" not in data:
                continue
            with self.subTest(extension=ext_dir.name):
                self.assertEqual(
                    data["url"],
                    REPO_URL,
                    f"{ext_dir.name}: metadata 'url' must point at {REPO_URL}",
                )

    def test_settings_schema_declared_exactly_when_schemas_ship(self):
        for ext_dir in extension_dirs():
            data = load_metadata(ext_dir)
            has_schema_files = bool(schema_files(ext_dir))
            declares_schema = "settings-schema" in data
            with self.subTest(extension=ext_dir.name):
                if has_schema_files:
                    self.assertTrue(
                        declares_schema,
                        f"{ext_dir.name}: ships schemas/ but metadata lacks 'settings-schema', "
                        "so getSettings() cannot resolve it",
                    )
                else:
                    self.assertFalse(
                        declares_schema,
                        f"{ext_dir.name}: declares 'settings-schema' but ships no "
                        "schemas/*.gschema.xml",
                    )


if __name__ == "__main__":
    unittest.main()
