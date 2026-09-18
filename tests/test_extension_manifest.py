"""Unit coverage for the discovery helpers in ``extension_manifest``.

Every other suite in ``tests/`` is data-driven: it discovers the real contents of
``extensions/`` through these helpers and asserts invariants over whatever it
finds. That makes the helpers themselves the trust root of the suite, and it
makes several of them *fail-open* — if ``referenced_settings_keys`` stops
matching a GJS settings call, ``test_every_key_referenced_from_js_exists_in_the
_schema`` sees an empty key set and passes with nothing checked.

These tests exercise the helpers directly against synthetic fixtures so a
regression in discovery or in the settings-key regex fails loudly instead of
silently disarming the rest of the suite.
"""

from __future__ import annotations

import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import extension_manifest
from extension_manifest import (
    extension_dirs,
    js_sources,
    load_metadata,
    metadata_path,
    parse_schemas,
    referenced_settings_keys,
    schema_files,
    schema_key_names,
)


class _TempExtensionsTree(unittest.TestCase):
    """Base class that redirects ``EXTENSIONS_DIR`` at a scratch tree."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.extensions = self.root / "extensions"
        self.extensions.mkdir()

        original = extension_manifest.EXTENSIONS_DIR
        extension_manifest.EXTENSIONS_DIR = self.extensions
        self.addCleanup(setattr, extension_manifest, "EXTENSIONS_DIR", original)

    def make_extension(self, name: str) -> Path:
        ext_dir = self.extensions / name
        ext_dir.mkdir()
        return ext_dir


class TestExtensionDirs(_TempExtensionsTree):
    def test_returns_every_directory_sorted_by_name(self):
        for name in ("zulu", "alpha", "mike"):
            self.make_extension(name)
        self.assertEqual(
            [p.name for p in extension_dirs()],
            ["alpha", "mike", "zulu"],
            "extension_dirs() must be deterministic so subTest output is stable",
        )

    def test_ignores_loose_files_next_to_extension_folders(self):
        self.make_extension("real-extension")
        (self.extensions / "README.md").write_text("not an extension", encoding="utf-8")
        (self.extensions / "bundle.zip").write_text("", encoding="utf-8")
        self.assertEqual([p.name for p in extension_dirs()], ["real-extension"])

    def test_missing_extensions_dir_returns_empty_list(self):
        extension_manifest.EXTENSIONS_DIR = self.root / "does-not-exist"
        self.assertEqual(extension_dirs(), [])

    def test_empty_extensions_dir_returns_empty_list(self):
        self.assertEqual(extension_dirs(), [])


class TestMetadataHelpers(_TempExtensionsTree):
    def test_metadata_path_points_at_metadata_json(self):
        ext_dir = self.make_extension("demo")
        self.assertEqual(metadata_path(ext_dir), ext_dir / "metadata.json")

    def test_load_metadata_round_trips_json(self):
        ext_dir = self.make_extension("demo")
        payload = {
            "uuid": "demo@projectbluefin.io",
            "name": "Demo",
            "shell-version": ["45", "46"],
        }
        metadata_path(ext_dir).write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(load_metadata(ext_dir), payload)

    def test_load_metadata_reads_non_ascii_as_utf8(self):
        ext_dir = self.make_extension("demo")
        metadata_path(ext_dir).write_text(
            json.dumps({"name": "Powér Stätus — Färbung"}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.assertEqual(load_metadata(ext_dir)["name"], "Powér Stätus — Färbung")

    def test_load_metadata_raises_on_malformed_json(self):
        ext_dir = self.make_extension("demo")
        metadata_path(ext_dir).write_text("{ not json", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            load_metadata(ext_dir)


class TestJsSources(_TempExtensionsTree):
    def test_finds_nested_sources_and_sorts_them(self):
        ext_dir = self.make_extension("demo")
        (ext_dir / "extension.js").write_text("", encoding="utf-8")
        (ext_dir / "prefs.js").write_text("", encoding="utf-8")
        nested = ext_dir / "lib" / "deep"
        nested.mkdir(parents=True)
        (nested / "helper.js").write_text("", encoding="utf-8")

        found = js_sources(ext_dir)
        self.assertEqual(
            [p.name for p in found],
            ["extension.js", "helper.js", "prefs.js"],
            "js_sources() must recurse so a new lib/ file is still syntax-checked",
        )
        self.assertEqual(found, sorted(found))

    def test_ignores_non_js_files(self):
        ext_dir = self.make_extension("demo")
        (ext_dir / "extension.js").write_text("", encoding="utf-8")
        (ext_dir / "stylesheet.css").write_text("", encoding="utf-8")
        (ext_dir / "metadata.json").write_text("{}", encoding="utf-8")
        (ext_dir / "notes.js.bak").write_text("", encoding="utf-8")
        self.assertEqual([p.name for p in js_sources(ext_dir)], ["extension.js"])

    def test_extension_without_sources_returns_empty_list(self):
        self.assertEqual(js_sources(self.make_extension("empty")), [])


class TestSchemaFiles(_TempExtensionsTree):
    def test_only_gschema_xml_files_under_schemas_are_returned(self):
        ext_dir = self.make_extension("demo")
        schemas = ext_dir / "schemas"
        schemas.mkdir()
        (schemas / "org.b.gschema.xml").write_text("", encoding="utf-8")
        (schemas / "org.a.gschema.xml").write_text("", encoding="utf-8")
        (schemas / "gschemas.compiled").write_text("", encoding="utf-8")
        (schemas / "notes.xml").write_text("", encoding="utf-8")
        self.assertEqual(
            [p.name for p in schema_files(ext_dir)],
            ["org.a.gschema.xml", "org.b.gschema.xml"],
        )

    def test_extension_without_schemas_dir_returns_empty_list(self):
        self.assertEqual(schema_files(self.make_extension("demo")), [])


SCHEMA_XML = """<?xml version="1.0" encoding="UTF-8"?>
<schemalist>
  <schema id="org.gnome.shell.extensions.demo"
          path="/org/gnome/shell/extensions/demo/">
    <key name="port" type="i"><default>8384</default></key>
    <key name="service-name" type="s"><default>'syncthing'</default></key>
  </schema>
  <schema id="org.gnome.shell.extensions.demo.extra"
          path="/org/gnome/shell/extensions/demo/extra/">
    <key name="start-stop-only" type="b"><default>false</default></key>
  </schema>
</schemalist>
"""


class TestSchemaParsing(_TempExtensionsTree):
    def _schema_file(self, text: str = SCHEMA_XML) -> Path:
        ext_dir = self.make_extension("demo")
        schemas = ext_dir / "schemas"
        schemas.mkdir()
        path = schemas / "org.gnome.shell.extensions.demo.gschema.xml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_parse_schemas_returns_every_schema_element(self):
        schemas = parse_schemas(self._schema_file())
        self.assertEqual(
            [s.get("id") for s in schemas],
            [
                "org.gnome.shell.extensions.demo",
                "org.gnome.shell.extensions.demo.extra",
            ],
            "a file with two <schema> blocks must not collapse to one",
        )

    def test_parse_schemas_raises_on_malformed_xml(self):
        with self.assertRaises(ET.ParseError):
            parse_schemas(self._schema_file("<schemalist><schema></schemalist>"))

    def test_schema_key_names_are_scoped_to_their_own_schema(self):
        first, second = parse_schemas(self._schema_file())
        self.assertEqual(schema_key_names(first), {"port", "service-name"})
        self.assertEqual(schema_key_names(second), {"start-stop-only"})

    def test_schema_key_names_skips_keys_without_a_name(self):
        schema = ET.fromstring(
            "<schema id='x'><key name='kept' type='b'/><key type='b'/></schema>"
        )
        self.assertEqual(schema_key_names(schema), {"kept"})

    def test_schema_without_keys_returns_empty_set(self):
        schema = ET.fromstring("<schema id='x'/>")
        self.assertEqual(schema_key_names(schema), set())


class TestReferencedSettingsKeys(unittest.TestCase):
    """The fail-open surface: an unmatched call site means an unchecked key.

    ``test_every_key_referenced_from_js_exists_in_the_schema`` only asserts over
    keys this regex returns, so a silent match failure turns that gate into a
    no-op. Each accessor and bind spelling used by GJS is pinned here.
    """

    def test_every_typed_getter_is_matched(self):
        for suffix in (
            "boolean",
            "string",
            "int",
            "uint",
            "double",
            "value",
            "strv",
            "enum",
            "flags",
        ):
            with self.subTest(accessor=f"get_{suffix}"):
                source = f"const v = this._settings.get_{suffix}('port')"
                self.assertEqual(referenced_settings_keys(source), {"port"})

    def test_every_typed_setter_is_matched(self):
        for suffix in (
            "boolean",
            "string",
            "int",
            "uint",
            "double",
            "value",
            "strv",
            "enum",
            "flags",
        ):
            with self.subTest(accessor=f"set_{suffix}"):
                source = f"this._settings.set_{suffix}('icon-name', v)"
                self.assertEqual(referenced_settings_keys(source), {"icon-name"})

    def test_unprefixed_settings_receiver_is_matched(self):
        self.assertEqual(
            referenced_settings_keys("settings.get_boolean('start-stop-only')"),
            {"start-stop-only"},
        )

    def test_deep_receiver_chain_is_matched(self):
        # prefs.js reaches settings through the window: this._window._settings.
        self.assertEqual(
            referenced_settings_keys("let port = this._window._settings.get_int('port')"),
            {"port"},
        )

    def test_bind_and_bind_writable_are_matched(self):
        source = """
        this._window._settings.bind(
            'service-name',
            row,
            'text',
            Gio.SettingsBindFlags.DEFAULT
        )
        settings.bind_writable('port', widget, 'sensitive', false)
        """
        self.assertEqual(referenced_settings_keys(source), {"service-name", "port"})

    def test_double_quoted_keys_are_matched(self):
        self.assertEqual(
            referenced_settings_keys('this._settings.get_string("icon-name")'),
            {"icon-name"},
        )

    def test_whitespace_and_newlines_around_the_call_are_tolerated(self):
        source = "this . _settings\n  . get_int (\n  'port'\n)"
        self.assertEqual(referenced_settings_keys(source), {"port"})

    def test_all_keys_in_a_multi_call_source_are_collected(self):
        source = """
        const name = this._settings.get_string('service-name')
        if (!this._settings.get_boolean('start-stop-only')) {
            log(this._settings.get_int('port'))
        }
        this._settings.bind('icon-name', item, 'icon', flags)
        """
        self.assertEqual(
            referenced_settings_keys(source),
            {"service-name", "start-stop-only", "port", "icon-name"},
        )

    def test_repeated_key_is_reported_once(self):
        source = (
            "this._settings.get_string('service-name');"
            "this._settings.get_string('service-name');"
        )
        self.assertEqual(referenced_settings_keys(source), {"service-name"})

    def test_get_settings_constructor_is_not_a_key_reference(self):
        source = "this._settings = extensionObject.getSettings('org.gnome.shell.extensions.demo')"
        self.assertEqual(referenced_settings_keys(source), set())

    def test_unrelated_getters_are_not_treated_as_settings_keys(self):
        # Guards the schema-key-is-dead test from false "used" hits: a GObject
        # accessor on some other receiver must not count as a settings read.
        source = "const w = actor.get_value('width'); const p = box.get_int('pad')"
        self.assertEqual(referenced_settings_keys(source), set())

    def test_source_without_settings_access_returns_empty_set(self):
        source = "export default class Demo extends Extension { enable() {} disable() {} }"
        self.assertEqual(referenced_settings_keys(source), set())

    def test_empty_source_returns_empty_set(self):
        self.assertEqual(referenced_settings_keys(""), set())


class TestModuleConstants(unittest.TestCase):
    """The invariants other suites assert against are asserted about here."""

    def test_repo_root_contains_the_extensions_dir(self):
        self.assertTrue(
            (extension_manifest.REPO_ROOT / "extensions").is_dir(),
            "REPO_ROOT must resolve to the checkout, not the tests/ folder",
        )

    def test_min_shell_version_is_the_esm_cutover(self):
        self.assertEqual(extension_manifest.MIN_SHELL_VERSION, 45)

    def test_uuid_domain_and_repo_url_agree(self):
        self.assertEqual(extension_manifest.UUID_DOMAIN, "projectbluefin.io")
        self.assertEqual(
            extension_manifest.REPO_URL,
            "https://github.com/projectbluefin/bluefin-bling",
        )


if __name__ == "__main__":
    unittest.main()
