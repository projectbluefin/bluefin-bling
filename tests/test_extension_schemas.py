"""GSettings schema invariants.

``glib-compile-schemas --strict`` catches malformed XML, but it cannot catch the
failures that actually bite this repo: a schema ``id`` that does not match
``settings-schema`` in metadata.json (``getSettings()`` aborts the extension), a
non-conventional ``path``, or a key referenced from GJS that does not exist in
the schema (``Gio.Settings`` aborts the whole shell process on an unknown key).
"""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from extension_manifest import (
    extension_dirs,
    js_sources,
    load_metadata,
    parse_schemas,
    referenced_settings_keys,
    schema_files,
    schema_key_names,
)


def _extensions_with_schemas():
    for ext_dir in extension_dirs():
        files = schema_files(ext_dir)
        if files:
            yield ext_dir, files


class TestSchemaXml(unittest.TestCase):
    def test_schema_files_are_well_formed_xml(self):
        for ext_dir, files in _extensions_with_schemas():
            for schema_file in files:
                with self.subTest(extension=ext_dir.name, file=schema_file.name):
                    try:
                        ET.parse(schema_file)
                    except ET.ParseError as exc:
                        self.fail(f"{schema_file} is not well-formed XML: {exc}")

    def test_schema_id_matches_metadata_settings_schema(self):
        for ext_dir, files in _extensions_with_schemas():
            declared = load_metadata(ext_dir).get("settings-schema")
            ids = {
                schema.get("id")
                for schema_file in files
                for schema in parse_schemas(schema_file)
            }
            with self.subTest(extension=ext_dir.name):
                self.assertIn(
                    declared,
                    ids,
                    f"{ext_dir.name}: metadata settings-schema {declared!r} matches no "
                    f"schema id in schemas/ (found {sorted(i for i in ids if i)})",
                )

    def test_schema_path_follows_gnome_convention(self):
        for ext_dir, files in _extensions_with_schemas():
            for schema_file in files:
                for schema in parse_schemas(schema_file):
                    schema_id = schema.get("id")
                    with self.subTest(extension=ext_dir.name, schema=schema_id):
                        path = schema.get("path")
                        self.assertIsNotNone(
                            path, f"{ext_dir.name}: schema {schema_id} has no path attribute"
                        )
                        self.assertTrue(
                            path.startswith("/") and path.endswith("/"),
                            f"{ext_dir.name}: schema path {path!r} must start and end with '/'",
                        )
                        expected = "/" + schema_id.replace(".", "/") + "/"
                        self.assertEqual(
                            path,
                            expected,
                            f"{ext_dir.name}: schema path {path!r} does not match id "
                            f"{schema_id!r} (expected {expected!r})",
                        )

    def test_every_key_has_type_default_and_documentation(self):
        for ext_dir, files in _extensions_with_schemas():
            for schema_file in files:
                for schema in parse_schemas(schema_file):
                    for key in schema.findall("key"):
                        name = key.get("name")
                        with self.subTest(extension=ext_dir.name, key=name):
                            self.assertIsNotNone(name, "schema key without a name attribute")
                            self.assertTrue(
                                key.get("type") or key.get("enum") or key.get("flags"),
                                f"{ext_dir.name}: key {name} declares no type/enum/flags",
                            )
                            self.assertIsNotNone(
                                key.find("default"),
                                f"{ext_dir.name}: key {name} has no <default>",
                            )
                            summary = key.find("summary")
                            self.assertTrue(
                                summary is not None and (summary.text or "").strip(),
                                f"{ext_dir.name}: key {name} has no <summary>",
                            )

    def test_key_names_are_kebab_case(self):
        for ext_dir, files in _extensions_with_schemas():
            for schema_file in files:
                for schema in parse_schemas(schema_file):
                    for name in sorted(schema_key_names(schema)):
                        with self.subTest(extension=ext_dir.name, key=name):
                            self.assertRegex(
                                name,
                                r"^[a-z0-9]+(-[a-z0-9]+)*$",
                                f"{ext_dir.name}: GSettings key {name!r} must be kebab-case",
                            )


class TestSchemaUsageMatchesSources(unittest.TestCase):
    def test_every_key_referenced_from_js_exists_in_the_schema(self):
        for ext_dir, files in _extensions_with_schemas():
            declared_keys = {
                name
                for schema_file in files
                for schema in parse_schemas(schema_file)
                for name in schema_key_names(schema)
            }
            for source in js_sources(ext_dir):
                used = referenced_settings_keys(source.read_text(encoding="utf-8"))
                for key in sorted(used):
                    with self.subTest(extension=ext_dir.name, source=source.name, key=key):
                        self.assertIn(
                            key,
                            declared_keys,
                            f"{ext_dir.name}/{source.name} reads GSettings key {key!r}, which "
                            "is not declared in schemas/ — Gio.Settings aborts on unknown keys",
                        )

    def test_schema_does_not_carry_keys_no_source_uses(self):
        for ext_dir, files in _extensions_with_schemas():
            declared_keys = {
                name
                for schema_file in files
                for schema in parse_schemas(schema_file)
                for name in schema_key_names(schema)
            }
            used: set[str] = set()
            for source in js_sources(ext_dir):
                used |= referenced_settings_keys(source.read_text(encoding="utf-8"))
            for key in sorted(declared_keys - used):
                with self.subTest(extension=ext_dir.name, key=key):
                    self.fail(
                        f"{ext_dir.name}: schema key {key!r} is never read or bound by any "
                        "JS source — dead setting, remove it or wire it up"
                    )


if __name__ == "__main__":
    unittest.main()
