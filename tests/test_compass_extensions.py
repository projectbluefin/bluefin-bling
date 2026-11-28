"""Discovery-based validation for Compass extensions.

The GNOME suite in this repo walks ``extensions/``. Compass extensions are a
second, independent type and live under ``compass-extensions/``; this module
walks that tree so a new folder is checked without editing any list.

It enforces the parts of the Vicinae/Compass extension manifest that are easy to
get wrong and silent at runtime: a command entrypoint that does not exist, a
dropdown default that is not one of its own values, a preference nobody reads,
and use of APIs Compass explicitly does not implement.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from extension_manifest import REPO_ROOT

COMPASS_ROOT = REPO_ROOT / "compass-extensions"
README = REPO_ROOT / "README.md"

REQUIRED_MANIFEST_KEYS = {
    "name",
    "title",
    "description",
    "icon",
    "author",
    "license",
    "commands",
    "dependencies",
}
NAME_RE = re.compile(r"^(@workaround/)?[a-z0-9-~][a-z0-9-_~]*$")
COMMAND_NAME_RE = re.compile(r"^[a-z0-9-~][a-zA-Z0-9-._~]*$")
PREFERENCE_NAME_RE = re.compile(r"^[a-zA-Z0-9-._~]*$")

# Compass ships these stubs; calling them throws "not implemented" at runtime.
UNIMPLEMENTED_APIS = ("AI.ask(", "AI.getModels(")


def compass_extension_dirs() -> list[Path]:
    """Return every Compass extension folder, discovered at call time."""
    if not COMPASS_ROOT.is_dir():
        return []
    return sorted(path for path in COMPASS_ROOT.iterdir() if path.is_dir())


def load_manifest(extension_dir: Path) -> dict:
    with (extension_dir / "package.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def source_files(extension_dir: Path) -> list[Path]:
    src = extension_dir / "src"
    if not src.is_dir():
        return []
    return sorted(
        path
        for path in src.rglob("*")
        if path.is_file() and path.suffix in {".js", ".jsx", ".ts", ".tsx"}
    )


class TestCompassManifest(unittest.TestCase):
    def test_directory_exists(self):
        self.assertTrue(COMPASS_ROOT.is_dir(), f"{COMPASS_ROOT} is missing")

    def test_at_least_one_extension_is_discovered(self):
        self.assertTrue(
            compass_extension_dirs(),
            "no Compass extensions found; the gate would pass vacuously",
        )

    def test_every_extension_has_a_valid_manifest(self):
        for ext in compass_extension_dirs():
            with self.subTest(extension=ext.name):
                manifest_path = ext / "package.json"
                self.assertTrue(manifest_path.is_file(), f"{ext.name} has no package.json")
                manifest = load_manifest(ext)
                missing = REQUIRED_MANIFEST_KEYS - set(manifest)
                self.assertFalse(missing, f"{ext.name} is missing {sorted(missing)}")
                self.assertRegex(manifest["name"], NAME_RE)
                self.assertEqual(manifest["license"], "MIT")
                self.assertIn("@vicinae/api", manifest["dependencies"])
                self.assertGreaterEqual(len(manifest["description"]), 16)
                self.assertEqual(manifest.get("name"), ext.name)

    def test_every_command_has_an_entrypoint(self):
        for ext in compass_extension_dirs():
            manifest = load_manifest(ext)
            commands = manifest.get("commands", [])
            self.assertTrue(commands, f"{ext.name} declares no commands")
            names = set()
            for command in commands:
                with self.subTest(extension=ext.name, command=command.get("name")):
                    name = command.get("name", "")
                    self.assertRegex(name, COMMAND_NAME_RE)
                    self.assertNotIn(name, names, f"duplicate command {name}")
                    names.add(name)
                    self.assertIn(command.get("mode"), {"view", "no-view", "menu-bar"})
                    # vici build only finds .tsx for view commands; a .ts view
                    # command passes a loose gate and then fails to build.
                    suffixes = (
                        (".tsx",) if command.get("mode") == "view"
                        else (".ts", ".tsx", ".js", ".jsx")
                    )
                    entrypoints = [ext / "src" / f"{name}{suffix}" for suffix in suffixes]
                    self.assertTrue(
                        any(path.is_file() for path in entrypoints),
                        f"{ext.name}: no src/{name}{suffixes[0]} entrypoint",
                    )

    def test_every_declared_icon_exists(self):
        for ext in compass_extension_dirs():
            manifest = load_manifest(ext)
            icon = ext / "assets" / manifest["icon"]
            self.assertTrue(icon.is_file(), f"{ext.name}: missing assets/{manifest['icon']}")


class TestCompassPreferences(unittest.TestCase):
    @staticmethod
    def all_preferences(manifest: dict) -> list[dict]:
        prefs = list(manifest.get("preferences", []))
        for command in manifest.get("commands", []):
            prefs.extend(command.get("preferences", []))
        return prefs

    def test_preferences_are_well_formed(self):
        for ext in compass_extension_dirs():
            manifest = load_manifest(ext)
            names = set()
            for pref in self.all_preferences(manifest):
                with self.subTest(extension=ext.name, preference=pref.get("name")):
                    self.assertRegex(pref.get("name", ""), PREFERENCE_NAME_RE)
                    self.assertNotIn(pref["name"], names, f"duplicate preference {pref['name']}")
                    names.add(pref["name"])
                    self.assertIn(
                        pref.get("type"),
                        {"textfield", "password", "checkbox", "dropdown", "appPicker", "file", "directory"},
                    )
                    self.assertIsInstance(pref.get("required"), bool)
                    self.assertGreaterEqual(len(pref.get("description", "")), 8)
                    if pref["type"] != "checkbox":
                        self.assertTrue(pref.get("title"))
                    else:
                        self.assertTrue(pref.get("label"))
                    if pref["type"] == "dropdown":
                        values = [item["value"] for item in pref.get("data", [])]
                        self.assertTrue(values, f"{pref['name']} has no dropdown values")
                        if "default" in pref:
                            self.assertIn(
                                pref["default"],
                                values,
                                f"{pref['name']} default is not one of its values",
                            )

    def test_every_preference_is_read_by_source(self):
        for ext in compass_extension_dirs():
            text = "\n".join(
                path.read_text(encoding="utf-8") for path in source_files(ext)
            )
            for pref in self.all_preferences(load_manifest(ext)):
                with self.subTest(extension=ext.name, preference=pref["name"]):
                    self.assertIn(
                        pref["name"],
                        text,
                        f"{ext.name}: preference {pref['name']} is never read",
                    )


class TestCompassSources(unittest.TestCase):
    def test_compass_unimplemented_apis_are_not_called(self):
        for ext in compass_extension_dirs():
            for path in source_files(ext):
                text = path.read_text(encoding="utf-8")
                for api in UNIMPLEMENTED_APIS:
                    with self.subTest(extension=ext.name, file=path.name, api=api):
                        self.assertNotIn(
                            api,
                            text,
                            f"{path} calls {api}, which throws in Compass",
                        )

    def test_sources_are_esm_and_do_not_eval(self):
        for ext in compass_extension_dirs():
            for path in source_files(ext):
                text = path.read_text(encoding="utf-8")
                with self.subTest(extension=ext.name, file=path.name):
                    self.assertNotIn("module.exports", text)
                    self.assertNotRegex(text, r"\beval\s*\(")
                    self.assertNotRegex(text, r"\bnew Function\s*\(")

    def test_sources_exist(self):
        for ext in compass_extension_dirs():
            self.assertTrue(
                source_files(ext),
                f"{ext.name} ships no TypeScript/JavaScript sources",
            )


class TestCompassDocs(unittest.TestCase):
    def test_readme_names_every_compass_extension(self):
        text = README.read_text(encoding="utf-8")
        for ext in compass_extension_dirs():
            with self.subTest(extension=ext.name):
                self.assertIn(ext.name, text)


if __name__ == "__main__":
    unittest.main()
