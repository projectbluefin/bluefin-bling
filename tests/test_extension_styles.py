"""Drift gate for the JS-to-stylesheet CSS class contract.

``extension.js`` names a CSS class as a bare string and the stylesheet gives
that name meaning. Nothing joins the two: rename one side, or ship the rule in a
file GNOME Shell never loads, and Shell logs nothing — the styling silently
stops happening. Shell loads exactly one sheet per extension and picks it from a
fixed set of root-level names, so the contract is checked against each sheet it
could pick, not against the union of every .css in the folder. Discovery-based,
like the rest of the suite: every folder under ``extensions/`` is covered with
no list to maintain.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from extension_manifest import (
    applied_style_classes,
    css_class_names,
    extension_dirs,
    js_sources,
    stylesheet_files,
)


def _applied_classes(ext_dir):
    """Return {class name: [source paths]} for one extension."""
    applied: dict[str, list[str]] = {}
    for source in js_sources(ext_dir):
        text = source.read_text(encoding="utf-8")
        for name in applied_style_classes(text):
            applied.setdefault(name, []).append(source.name)
    return applied


def _declared_classes(sheet):
    """Return the class names one stylesheet defines a rule for."""
    return css_class_names(sheet.read_text(encoding="utf-8"))


class TestStyleClassContract(unittest.TestCase):
    def test_every_applied_class_has_a_rule_in_every_loadable_stylesheet(self):
        # GNOME Shell loads exactly one of an extension's candidate sheets, and
        # which one depends on the session mode and the style variant, so the
        # invariant has to hold sheet by sheet rather than across their union:
        # a class defined only in stylesheet-dark.css is missing for everybody
        # running a light theme.
        for ext_dir in extension_dirs():
            applied = _applied_classes(ext_dir)
            if not applied:
                continue
            for sheet in stylesheet_files(ext_dir):
                declared = _declared_classes(sheet)
                for name, sources in sorted(applied.items()):
                    with self.subTest(
                        extension=ext_dir.name,
                        stylesheet=sheet.name,
                        css_class=name,
                    ):
                        self.assertIn(
                            name,
                            declared,
                            f"{ext_dir.name}/{', '.join(sorted(set(sources)))} "
                            f"applies CSS class '{name}' but "
                            f"{ext_dir.name}/{sheet.name} defines no rule for it "
                            "— GNOME Shell may load exactly that sheet, and then "
                            "the styling is a silent no-op",
                        )

    def test_extensions_that_style_actors_ship_a_loadable_stylesheet(self):
        for ext_dir in extension_dirs():
            with self.subTest(extension=ext_dir.name):
                if not _applied_classes(ext_dir):
                    continue
                self.assertTrue(
                    stylesheet_files(ext_dir),
                    f"{ext_dir.name} applies CSS classes but ships no stylesheet "
                    "GNOME Shell will load — it reads stylesheet.css (or a "
                    "stylesheet-<variant>.css / <sessionMode>.css sibling) from "
                    "the extension root only, never from a subdirectory",
                )

    def test_the_gate_matches_something(self):
        # Both assertions above are filters, and a filter with nothing to
        # filter passes by doing nothing. If every call site in the repo moves
        # behind a parameter or a template literal the gate goes blind while
        # staying green, so make that condition itself a failure.
        coverage = {
            ext_dir.name: sorted(_applied_classes(ext_dir))
            for ext_dir in extension_dirs()
        }
        self.assertTrue(
            any(coverage.values()),
            "no extension under extensions/ yields a statically resolvable "
            f"applied CSS class, so the drift gate matched nothing: {coverage}. "
            "Either a call site stopped naming its class with a string literal "
            "or a `const NAME = 'class'` identifier (keep them there so the "
            "gate can see them), or applied_style_classes() no longer "
            "recognises the call shape",
        )


class TestAppliedStyleClassExtraction(unittest.TestCase):
    def test_string_literal_argument(self):
        self.assertEqual(
            applied_style_classes("actor.add_style_class_name('alert-red');"),
            {"alert-red"},
        )

    def test_constant_argument_is_resolved(self):
        source = "const CLASS_REBOOT = 'power-status-reboot';\n" \
                 "btn.add_style_class_name(CLASS_REBOOT);"
        self.assertEqual(applied_style_classes(source), {"power-status-reboot"})

    def test_unresolvable_argument_is_skipped(self):
        source = "applyStyle(className) { btn.add_style_class_name(className); }"
        self.assertEqual(applied_style_classes(source), set())

    def test_set_style_class_name_splits_a_class_list(self):
        self.assertEqual(
            applied_style_classes('a.set_style_class_name("one two");'),
            {"one", "two"},
        )

    def test_remove_counts_but_has_probe_does_not(self):
        # remove_style_class_name puts a class on an actor (well, takes it off
        # again), so the extension owes it a rule. has_style_class_name only
        # reads back a class somebody else set, so it owes nothing. The probed
        # name here is deliberately not one gnome-shell owns, so this pins the
        # call-shape exclusion rather than the upstream-owned denylist.
        self.assertEqual(
            applied_style_classes("btn.remove_style_class_name('alert-red');"),
            {"alert-red"},
        )
        self.assertEqual(
            applied_style_classes("if (a.has_style_class_name('alert-red')) {}"),
            set(),
        )

    def test_shell_owned_class_is_not_demanded(self):
        # Applying a class gnome-shell sets on its own widgets is how an
        # extension adopts the platform look, and it is exactly when shipping a
        # competing rule would be the bug — so the gate must not ask for one.
        self.assertEqual(
            applied_style_classes("a.add_style_class_name('popup-menu-item');"),
            set(),
        )
        self.assertEqual(
            applied_style_classes(
                "a.set_style_class_name('icon-button power-status-overdue');"
            ),
            {"power-status-overdue"},
        )

    def test_commented_out_calls_are_ignored(self):
        # Retiring a class goes comment-out-the-call, then delete-the-rule. If
        # a commented-out call still demanded a rule, that order would red the
        # suite for a change that is entirely correct.
        self.assertEqual(
            applied_style_classes("// a.add_style_class_name('retired-class');"),
            set(),
        )
        self.assertEqual(
            applied_style_classes("/* a.add_style_class_name('dead-class'); */"),
            set(),
        )

    def test_a_url_literal_does_not_start_a_comment(self):
        # The `//` lives inside a string, so nothing after it is a comment.
        source = "const HELP = 'http://example.com'; a.add_style_class_name('live');"
        self.assertEqual(applied_style_classes(source), {"live"})


class TestCssClassExtraction(unittest.TestCase):
    def test_reads_class_selectors(self):
        css = ".alpha StIcon,\n.beta .icon-button { color: #fff; }"
        self.assertEqual(css_class_names(css), {"alpha", "beta", "icon-button"})

    def test_ignores_commented_out_rules(self):
        css = "/* .retired { color: red; } */\n.live { color: blue; }"
        self.assertEqual(css_class_names(css), {"live"})

    def test_declaration_values_are_not_class_names(self):
        # A url() path and a content string are values, not selector text.
        # Counting them widens the declared set and silently excuses a rule
        # that is genuinely missing.
        self.assertEqual(css_class_names(".a{background:url(icons/foo.svg)}"), {"a"})
        self.assertEqual(css_class_names('.a{content:".fake-class"}'), {"a"})

    def test_lengths_are_not_class_names(self):
        self.assertEqual(css_class_names(".pad { margin: 0.5em; }"), {"pad"})

    def test_grouped_and_descendant_selectors_survive(self):
        css = ".one .two,\n.three > .four { color: #fff; }"
        self.assertEqual(css_class_names(css), {"one", "two", "three", "four"})


class TestStylesheetDiscovery(unittest.TestCase):
    def test_only_sheets_gnome_shell_can_load_are_returned(self):
        # extensionSystem.js resolves candidate names with
        # `extension.dir.get_child(name)`, so a .css in a subdirectory is dead
        # weight Shell will never read — reporting it would let an extension
        # pass this gate while shipping no styling at all.
        with tempfile.TemporaryDirectory() as tmp:
            ext_dir = Path(tmp)
            for name in ("stylesheet.css", "stylesheet-dark.css", "user.css"):
                (ext_dir / name).write_text("", encoding="utf-8")
            (ext_dir / "css").mkdir()
            (ext_dir / "css" / "theme.css").write_text("", encoding="utf-8")
            self.assertEqual(
                [sheet.name for sheet in stylesheet_files(ext_dir)],
                ["stylesheet-dark.css", "stylesheet.css", "user.css"],
            )


if __name__ == "__main__":
    unittest.main()
