"""Unit tests for tests/gnome_module_loader.mjs — the shared GNOME-ESM shim.

GNOME Shell sources import from ``gi://`` and ``resource:///``, which node
cannot resolve, so every behavior harness here rewrites those import lines into
bindings drawn from a globalThis stub namespace before importing the source as a
``data:`` module.

That rewrite used to be reimplemented per harness, and the copies drifted: the
syncthing harness learned that ``import {gettext as _}`` must become
``{gettext: _}`` once it is a destructuring binding, while the power-status-color
copy would have emitted a ``SyntaxError`` for the same line. Now one module owns
the grammar, and these tests exercise it directly against synthetic sources
rather than only through whichever import forms the shipped extensions use
today.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
HARNESS = TESTS_DIR / "gnome_module_loader_harness.mjs"

# The shim's own harness is the one file allowed to name the grammar: it exists
# to exercise `rewriteGnomeImports` and `asDataModule` directly.
SHIM_OWNER_HARNESS = "gnome_module_loader_harness.mjs"

# Markers of a private reimplementation of the rewrite: a regex literal that
# matches GNOME module specifiers, and the `data:` module encoding. Either one
# appearing outside the owner means a harness has grown its own copy of the
# grammar again.
_OWNED_GRAMMAR_MARKERS = (
    (re.compile(r"/[^/\n]*gi:\\/\\/"), "a regex literal matching `gi://` specifiers"),
    (re.compile(r"data:text/javascript"), "the `data:` module encoding"),
)


def behavior_harnesses() -> list[Path]:
    """Every harness that loads a shipped source, owner's own harness excluded."""
    return sorted(
        path
        for path in TESTS_DIR.glob("*_harness.mjs")
        if path.name != SHIM_OWNER_HARNESS
    )

NODE = shutil.which("node")

PROBE = "export function probe() { return describe(); }"


@unittest.skipIf(NODE is None, "node is not installed")
class GnomeModuleLoaderTestCase(unittest.TestCase):
    def run_scenario(self, scenario: str, **options):
        result = subprocess.run(
            [NODE, str(HARNESS), scenario, json.dumps(options)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"harness scenario {scenario} failed:\n{result.stderr.strip()}",
        )
        return json.loads(result.stdout)

    # --- rewrite grammar ---------------------------------------------------

    def test_default_import_binds_named_property(self):
        source = "import GLib from 'gi://GLib';\n"
        rewritten = self.run_scenario("rewrite", source=source)["rewritten"]
        self.assertEqual(
            rewritten.strip(), "const GLib = globalThis.__loaderStubs.GLib;"
        )

    def test_namespace_import_binds_named_property(self):
        source = "import * as Main from 'resource:///org/gnome/shell/ui/main.js';\n"
        rewritten = self.run_scenario("rewrite", source=source)["rewritten"]
        self.assertEqual(
            rewritten.strip(), "const Main = globalThis.__loaderStubs.Main;"
        )

    def test_named_import_destructures_the_stub_namespace(self):
        source = (
            "import {Extension} from "
            "'resource:///org/gnome/shell/extensions/extension.js';\n"
        )
        rewritten = self.run_scenario("rewrite", source=source)["rewritten"]
        self.assertEqual(
            rewritten.strip(), "const {Extension} = globalThis.__loaderStubs;"
        )

    def test_aliased_named_import_becomes_a_destructuring_rename(self):
        """`{gettext as _}` is import syntax; as a binding it needs `{gettext: _}`.

        This is the exact form that diverged between the two harnesses, and it
        is live in extensions/syncthing-toggle/{extension,toggle,prefs}.js.
        """
        source = (
            "import { Extension, gettext as _ } from "
            "'resource:///org/gnome/shell/extensions/extension.js'\n"
        )
        rewritten = self.run_scenario("rewrite", source=source)["rewritten"]
        self.assertNotIn(" as ", rewritten)
        self.assertEqual(
            rewritten.strip(),
            "const { Extension, gettext: _ } = globalThis.__loaderStubs;",
        )

    def test_multiline_named_import_is_rewritten(self):
        source = (
            "import {\n"
            "\tExtension,\n"
            "\tgettext as _,\n"
            "} from 'resource:///org/gnome/shell/extensions/extension.js'\n"
        )
        rewritten = self.run_scenario("rewrite", source=source)["rewritten"]
        self.assertNotIn("from 'resource:", rewritten)
        self.assertIn("gettext: _", rewritten)

    def test_non_gnome_imports_are_left_alone(self):
        source = "import {thing} from './other.js';\nimport GLib from 'gi://GLib';\n"
        rewritten = self.run_scenario("rewrite", source=source)["rewritten"]
        self.assertIn("import {thing} from './other.js';", rewritten)
        self.assertNotIn("from 'gi://GLib'", rewritten)

    def test_body_below_the_import_block_is_untouched(self):
        source = "import GLib from 'gi://GLib';\n\nconst KEEP = 'gi://not-an-import';\n"
        rewritten = self.run_scenario("rewrite", source=source)["rewritten"]
        self.assertIn("const KEEP = 'gi://not-an-import';", rewritten)

    # --- loading -----------------------------------------------------------

    def test_aliased_import_actually_executes(self):
        """The alias case must load, not merely rewrite to plausible text."""
        source = (
            "import { Extension, gettext as _ } from "
            "'resource:///org/gnome/shell/extensions/extension.js'\n"
            "function describe() { return [_, Extension]; }\n" + PROBE
        )
        result = self.run_scenario(
            "load", source=source, stubs={"gettext": "translated", "Extension": "ext"}
        )
        self.assertEqual(result["probe"], ["translated", "ext"])

    def test_default_and_namespace_imports_actually_execute(self):
        source = (
            "import GLib from 'gi://GLib';\n"
            "import * as Main from 'resource:///org/gnome/shell/ui/main.js';\n"
            "function describe() { return [GLib, Main]; }\n" + PROBE
        )
        result = self.run_scenario(
            "load", source=source, stubs={"GLib": "glib", "Main": "main"}
        )
        self.assertEqual(result["probe"], ["glib", "main"])

    def test_extra_rewrite_pass_can_bind_a_relative_import(self):
        """Relative imports cannot resolve from a data: URL, so callers rewrite them."""
        source = (
            "import GLib from 'gi://GLib';\n"
            "import {fromOther} from './other.js';\n"
            "function describe() { return [GLib, fromOther]; }\n" + PROBE
        )
        result = self.run_scenario(
            "load", source=source, stubs={"GLib": "glib"}, rewriteToggleImport=True
        )
        self.assertEqual(result["probe"], ["glib", "bound"])

    def test_source_without_gnome_imports_is_refused(self):
        result = self.run_scenario(
            "refuses", source="export function probe() { return 1; }\n"
        )
        self.assertTrue(result["threw"])
        self.assertIn("harness rewrite is stale", result["message"])

    def test_loading_twice_in_one_process_is_stable(self):
        """A shared /g regex would carry lastIndex and fail the second load."""
        source = (
            "import GLib from 'gi://GLib';\n"
            "function describe() { return GLib; }\n" + PROBE
        )
        result = self.run_scenario("loadTwice", source=source, stubs={"GLib": "glib"})
        self.assertEqual(result["first"], "glib")
        self.assertEqual(result["second"], "glib")

    def test_data_module_round_trips_the_source(self):
        source = "const x = 'café — ✓';\n"
        result = self.run_scenario("dataModule", source=source)
        self.assertTrue(result["hasPrefix"])
        self.assertEqual(result["decoded"], source)


class ShimHasOneOwnerTestCase(unittest.TestCase):
    """No harness may reimplement the grammar this module owns.

    `gnome_module_loader.mjs` was introduced because the rewrite had been copied
    into each harness and the copies drifted — one handled `import {gettext as _}`
    and the other emitted a `SyntaxError` for it. Nothing stopped a copy from
    reappearing, and two later did. These assertions discover harnesses rather
    than listing them, so a new one is held to the same rule on the day it lands.
    """

    def test_harnesses_are_discovered(self):
        """An empty scan would make the assertions below vacuous."""
        names = [path.name for path in behavior_harnesses()]
        self.assertIn("light_style_harness.mjs", names)
        self.assertGreaterEqual(len(names), 4, names)

    def test_every_harness_loads_through_the_shared_shim(self):
        for path in behavior_harnesses():
            with self.subTest(harness=path.name):
                source = path.read_text(encoding="utf-8")
                if "from './gnome_module_loader.mjs'" not in source:
                    self.fail(
                        f"tests/{path.name} does not import the shared shim; "
                        "call loadGnomeModule() instead of rewriting imports here"
                    )

    def test_no_harness_reimplements_the_rewrite_grammar(self):
        for path in behavior_harnesses():
            source = path.read_text(encoding="utf-8")
            for pattern, description in _OWNED_GRAMMAR_MARKERS:
                with self.subTest(harness=path.name, marker=description):
                    if pattern.search(source):
                        self.fail(
                            f"tests/{path.name} contains {description}, which "
                            "gnome_module_loader.mjs owns; a second copy drifts "
                            "the moment GNOME changes its import surface"
                        )


if __name__ == "__main__":
    unittest.main()
