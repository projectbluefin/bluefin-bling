"""Source-level invariants for every JavaScript file under ``extensions/``.

These are the automated form of the manual ``node --check`` list in AGENTS.md.
That list is hardcoded per-file, so a file added by a new extension is silently
never syntax-checked; this suite discovers sources instead of listing them.

The lifecycle assertions encode the repo rule that ``disable()`` must undo
everything ``enable()`` created — a leaked ``GLib`` source or signal handler
survives extension disable and keeps running inside gnome-shell.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest

from extension_manifest import extension_dirs, js_sources

NODE = shutil.which("node")

_ENABLE_RE = re.compile(r"\benable\s*\(\s*\)\s*\{")
_DISABLE_RE = re.compile(r"\bdisable\s*\(\s*\)\s*\{")
_DEFAULT_EXPORT_RE = re.compile(r"export\s+default\s+class\s+\w+\s+extends\s+Extension\b")
_ESM_RE = re.compile(r"^\s*(import|export)\b", re.M)
_COMMONJS_RE = re.compile(r"\b(require\s*\(|module\.exports\b)")


def _all_sources():
    for ext_dir in extension_dirs():
        for source in js_sources(ext_dir):
            yield ext_dir, source


def _disable_body(text: str) -> str:
    """Return the source text from ``disable() {`` to the end of the class."""
    match = _DISABLE_RE.search(text)
    return text[match.end():] if match else ""


class TestJavaScriptSyntax(unittest.TestCase):
    @unittest.skipIf(NODE is None, "node is not installed")
    def test_every_js_source_parses(self):
        for ext_dir, source in _all_sources():
            with self.subTest(extension=ext_dir.name, source=source.name):
                result = subprocess.run(
                    [NODE, "--check", str(source)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    f"node --check failed for {source.relative_to(source.parents[2])}:\n"
                    f"{result.stderr.strip()}",
                )

    def test_sources_are_esm(self):
        """GNOME 45+ extensions are ESM only; a stray require() will not load."""
        for ext_dir, source in _all_sources():
            text = source.read_text(encoding="utf-8")
            with self.subTest(extension=ext_dir.name, source=source.name):
                self.assertRegex(
                    text,
                    _ESM_RE,
                    f"{ext_dir.name}/{source.name} has no top-level import/export — "
                    "GNOME 45+ requires ESM",
                )

    def test_sources_do_not_use_commonjs(self):
        for ext_dir, source in _all_sources():
            text = source.read_text(encoding="utf-8")
            with self.subTest(extension=ext_dir.name, source=source.name):
                self.assertNotRegex(
                    text,
                    _COMMONJS_RE,
                    f"{ext_dir.name}/{source.name} uses CommonJS, which GJS cannot load",
                )


class TestExtensionEntryPoint(unittest.TestCase):
    def test_every_extension_ships_extension_js(self):
        for ext_dir in extension_dirs():
            with self.subTest(extension=ext_dir.name):
                self.assertTrue(
                    (ext_dir / "extension.js").is_file(),
                    f"{ext_dir.name}: extension.js is the required entry point",
                )

    def test_entry_point_default_exports_an_extension_subclass(self):
        for ext_dir in extension_dirs():
            text = (ext_dir / "extension.js").read_text(encoding="utf-8")
            with self.subTest(extension=ext_dir.name):
                self.assertRegex(
                    text,
                    _DEFAULT_EXPORT_RE,
                    f"{ext_dir.name}/extension.js must 'export default class ... extends "
                    "Extension' for GNOME Shell to instantiate it",
                )

    def test_entry_point_defines_enable_and_disable(self):
        for ext_dir in extension_dirs():
            text = (ext_dir / "extension.js").read_text(encoding="utf-8")
            with self.subTest(extension=ext_dir.name):
                self.assertRegex(
                    text, _ENABLE_RE, f"{ext_dir.name}/extension.js defines no enable()"
                )
                self.assertRegex(
                    text, _DISABLE_RE, f"{ext_dir.name}/extension.js defines no disable()"
                )

    def test_prefs_js_default_exports_extension_preferences(self):
        for ext_dir in extension_dirs():
            prefs = ext_dir / "prefs.js"
            if not prefs.is_file():
                continue
            with self.subTest(extension=ext_dir.name):
                self.assertRegex(
                    prefs.read_text(encoding="utf-8"),
                    r"export\s+default\s+class\s+\w+\s+extends\s+ExtensionPreferences\b",
                    f"{ext_dir.name}/prefs.js must default-export an ExtensionPreferences "
                    "subclass",
                )


class TestLifecycleTeardown(unittest.TestCase):
    """disable() must release everything enable() acquired."""

    def test_glib_timeouts_are_removed_on_disable(self):
        for ext_dir, source in _all_sources():
            text = source.read_text(encoding="utf-8")
            if "GLib.timeout_add" not in text:
                continue
            with self.subTest(extension=ext_dir.name, source=source.name):
                self.assertIn(
                    "GLib.Source.remove",
                    text,
                    f"{ext_dir.name}/{source.name} installs a GLib timeout but never calls "
                    "GLib.Source.remove — the timer survives disable()",
                )

    def test_cancellables_are_cancelled_on_disable(self):
        for ext_dir in extension_dirs():
            text = (ext_dir / "extension.js").read_text(encoding="utf-8")
            if "new Gio.Cancellable" not in text:
                continue
            with self.subTest(extension=ext_dir.name):
                self.assertIn(
                    ".cancel()",
                    _disable_body(text),
                    f"{ext_dir.name}/extension.js creates a Gio.Cancellable but disable() "
                    "never cancels it — in-flight subprocesses outlive the extension",
                )

    def test_file_monitors_are_cancelled_on_disable(self):
        for ext_dir in extension_dirs():
            text = (ext_dir / "extension.js").read_text(encoding="utf-8")
            if "monitor_directory" not in text and "monitor_file" not in text:
                continue
            disable_body = _disable_body(text)
            with self.subTest(extension=ext_dir.name):
                self.assertIn(
                    "disconnect",
                    disable_body,
                    f"{ext_dir.name}/extension.js connects to a Gio.FileMonitor but disable() "
                    "never disconnects the handler",
                )
                self.assertIn(
                    "cancel()",
                    disable_body,
                    f"{ext_dir.name}/extension.js never cancels its Gio.FileMonitor in "
                    "disable()",
                )

    def test_disable_is_not_empty(self):
        for ext_dir in extension_dirs():
            text = (ext_dir / "extension.js").read_text(encoding="utf-8")
            match = _DISABLE_RE.search(text)
            with self.subTest(extension=ext_dir.name):
                self.assertIsNotNone(match)
                remainder = text[match.end():].lstrip()
                self.assertFalse(
                    remainder.startswith("}"),
                    f"{ext_dir.name}/extension.js has an empty disable(); teardown is "
                    "mandatory in this repo",
                )


if __name__ == "__main__":
    unittest.main()
