"""Drift gate for the JS-to-stylesheet CSS class contract.

``extension.js`` names a CSS class as a bare string and ``stylesheet.css`` gives
that name meaning. Nothing joins the two: rename one side, or ship a source that
styles actors without shipping a stylesheet, and GNOME Shell logs nothing — the
styling silently stops happening. Discovery-based, like the rest of the suite:
every folder under ``extensions/`` is covered with no list to maintain.
"""

from __future__ import annotations

import unittest

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


def _declared_classes(ext_dir):
    declared: set[str] = set()
    for sheet in stylesheet_files(ext_dir):
        declared |= css_class_names(sheet.read_text(encoding="utf-8"))
    return declared


class TestStyleClassContract(unittest.TestCase):
    def test_every_applied_class_has_a_stylesheet_rule(self):
        for ext_dir in extension_dirs():
            applied = _applied_classes(ext_dir)
            if not applied:
                continue
            declared = _declared_classes(ext_dir)
            for name, sources in sorted(applied.items()):
                with self.subTest(extension=ext_dir.name, css_class=name):
                    self.assertIn(
                        name,
                        declared,
                        f"{ext_dir.name}/{', '.join(sorted(set(sources)))} applies CSS "
                        f"class '{name}' but no stylesheet under {ext_dir.name}/ "
                        f"defines a rule for it — the styling is a silent no-op",
                    )

    def test_extensions_that_style_actors_ship_a_stylesheet(self):
        for ext_dir in extension_dirs():
            with self.subTest(extension=ext_dir.name):
                if not _applied_classes(ext_dir):
                    continue
                self.assertTrue(
                    stylesheet_files(ext_dir),
                    f"{ext_dir.name} applies CSS classes but ships no stylesheet.css",
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
        # remove_style_class_name names a class the extension owns; the
        # has_style_class_name probe reads a class GNOME Shell owns.
        self.assertEqual(
            applied_style_classes("btn.remove_style_class_name('alert-red');"),
            {"alert-red"},
        )
        self.assertEqual(
            applied_style_classes("if (a.has_style_class_name('icon-button')) {}"),
            set(),
        )


class TestCssClassExtraction(unittest.TestCase):
    def test_reads_class_selectors(self):
        css = ".alpha StIcon,\n.beta .icon-button { color: #fff; }"
        self.assertEqual(css_class_names(css), {"alpha", "beta", "icon-button"})

    def test_ignores_commented_out_rules(self):
        css = "/* .retired { color: red; } */\n.live { color: blue; }"
        self.assertEqual(css_class_names(css), {"live"})


if __name__ == "__main__":
    unittest.main()
