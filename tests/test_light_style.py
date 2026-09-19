"""Behavioral unit tests for extensions/light-style/extension.js.

The rest of the suite checks structure — metadata, schemas, ESM-ness, teardown
shape — and it sweeps light-style along with every other extension by reading
its source as text. Nothing ever executed it, so the decisions the extension
actually makes were unverified: which color-scheme value means "light", the
mirrored style class on ``Main.uiGroup``, the ``St.Settings`` notify that makes
the shell re-read the theme, the ``changed::color-scheme`` wiring, and — the
invariant the source comments at length — that ``enable()`` captures the
session's own ``Main.sessionMode.colorScheme`` *before* the first ``_sync()``
overwrites it, and restores that captured value rather than a literal.

That last one matters on Bluefin and Dakota specifically: both ship a custom
session mode, so ``sessionMode.colorScheme`` holds a value the stock default
does not. Restoring a hardcoded literal would destroy it and stomp any other
theming extension driving the same property.

These tests drive the real shipped source through
``tests/light_style_harness.mjs``, which rewrites only the gi:// and
resource:/// import lines so node can load the module and stub GNOME Shell.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "light_style_harness.mjs"
EXTENSION_JS = REPO_ROOT / "extensions" / "light-style" / "extension.js"

NODE = shutil.which("node")

STYLE_CLASS = "light-style-active"
INTERFACE_SCHEMA = "org.gnome.desktop.interface"
COLOR_SCHEME_SIGNAL = "changed::color-scheme"

# The value the extension treats as "the user wants dark"; every other value
# (including the empty string and the GNOME default) means light.
PREFER_DARK = "prefer-dark"
PREFER_LIGHT = "prefer-light"

# A session mode value the stock GNOME default does not have, standing in for
# what a Bluefin or Dakota image ships in /usr/share/gnome-shell/modes/*.json.
CUSTOM_SESSION_SCHEME = "bluefin-custom-scheme"


@unittest.skipIf(NODE is None, "node is not installed")
class LightStyleTestCase(unittest.TestCase):
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


class TestHarnessFidelity(LightStyleTestCase):
    """The harness is only meaningful if it still loads the shipped source."""

    def test_extension_still_imports_only_stubbable_modules(self):
        source = EXTENSION_JS.read_text(encoding="utf-8")
        imports = [
            line.strip()
            for line in source.splitlines()
            if line.strip().startswith("import ")
        ]
        self.assertTrue(imports, "extension.js declares no imports at all")
        for line in imports:
            self.assertRegex(
                line,
                r"from '(?:gi://|resource:///)",
                "extension.js grew an import the harness does not rewrite; "
                "teach light_style_harness.mjs about it",
            )

    def test_harness_actually_executes_the_shipped_source(self):
        """A harness that silently stopped loading the module would pass every
        other test vacuously. Assert on state only the real code can produce."""
        result = self.run_scenario(
            "enable", colorScheme="default", sessionColorScheme=CUSTOM_SESSION_SCHEME
        )
        self.assertEqual(result["schemasConstructed"], [INTERFACE_SCHEMA])
        self.assertEqual(result["keysRead"], ["color-scheme"])


class TestEnable(LightStyleTestCase):
    def test_light_scheme_adds_the_style_class_and_forces_prefer_light(self):
        result = self.run_scenario(
            "enable", colorScheme="default", sessionColorScheme=CUSTOM_SESSION_SCHEME
        )
        self.assertEqual(result["classes"], [STYLE_CLASS])
        self.assertEqual(result["sessionColorScheme"], PREFER_LIGHT)

    def test_dark_scheme_leaves_the_style_class_off(self):
        result = self.run_scenario(
            "enable",
            colorScheme=PREFER_DARK,
            sessionColorScheme=CUSTOM_SESSION_SCHEME,
        )
        self.assertEqual(result["classes"], [])

    def test_dark_scheme_restores_the_session_value_not_a_literal(self):
        """The whole point of _savedColorScheme: on the dark branch the session
        mode gets its own value back, never a hardcoded 'default'."""
        result = self.run_scenario(
            "enable",
            colorScheme=PREFER_DARK,
            sessionColorScheme=CUSTOM_SESSION_SCHEME,
        )
        self.assertEqual(result["sessionColorScheme"], CUSTOM_SESSION_SCHEME)

    def test_saved_scheme_is_captured_before_the_first_sync_overwrites_it(self):
        """_sync() assigns sessionMode.colorScheme. If the capture happened
        after it, the light branch would save 'prefer-light' and disable()
        would leave the session permanently light."""
        result = self.run_scenario(
            "enable", colorScheme="default", sessionColorScheme=CUSTOM_SESSION_SCHEME
        )
        self.assertEqual(result["savedColorScheme"], CUSTOM_SESSION_SCHEME)
        self.assertNotEqual(result["savedColorScheme"], PREFER_LIGHT)

    def test_only_prefer_dark_counts_as_dark(self):
        """Any other value — including GNOME's empty-string default — is light.
        A substring or prefix test here would misread 'prefer-dark-extra'."""
        for scheme in ("default", "", "prefer-light", "Prefer-Dark", "dark"):
            with self.subTest(scheme=scheme):
                result = self.run_scenario(
                    "enable",
                    colorScheme=scheme,
                    sessionColorScheme=CUSTOM_SESSION_SCHEME,
                )
                self.assertEqual(
                    result["classes"],
                    [STYLE_CLASS],
                    f"{scheme!r} was treated as dark",
                )

    def test_enable_connects_exactly_one_color_scheme_handler(self):
        result = self.run_scenario(
            "enable", colorScheme="default", sessionColorScheme=CUSTOM_SESSION_SCHEME
        )
        self.assertEqual(result["connected"], [COLOR_SCHEME_SIGNAL])
        self.assertEqual(result["liveHandlers"], 1)
        self.assertIsNotNone(result["colorSchemeId"])

    def test_enable_notifies_st_settings_so_the_shell_re_reads_the_theme(self):
        result = self.run_scenario(
            "enable", colorScheme="default", sessionColorScheme=CUSTOM_SESSION_SCHEME
        )
        self.assertEqual(result["stNotifications"], ["color-scheme"])


class TestSchemeChange(LightStyleTestCase):
    """The connected handler has to actually re-run _sync()."""

    def test_switching_to_dark_removes_the_class_and_restores_the_session_value(self):
        result = self.run_scenario(
            "schemeChange",
            colorScheme="default",
            sessionColorScheme=CUSTOM_SESSION_SCHEME,
            nextColorScheme=PREFER_DARK,
        )
        self.assertEqual(result["classes"], [])
        self.assertEqual(result["sessionColorScheme"], CUSTOM_SESSION_SCHEME)
        self.assertEqual(
            result["uiGroupCalls"],
            [["add", STYLE_CLASS], ["remove", STYLE_CLASS]],
        )

    def test_switching_to_light_adds_the_class_and_forces_prefer_light(self):
        result = self.run_scenario(
            "schemeChange",
            colorScheme=PREFER_DARK,
            sessionColorScheme=CUSTOM_SESSION_SCHEME,
            nextColorScheme="default",
        )
        self.assertEqual(result["classes"], [STYLE_CLASS])
        self.assertEqual(result["sessionColorScheme"], PREFER_LIGHT)

    def test_a_scheme_change_re_reads_the_setting_and_notifies_again(self):
        """_sync() must read the live value, not a value cached at enable()."""
        result = self.run_scenario(
            "schemeChange",
            colorScheme="default",
            sessionColorScheme=CUSTOM_SESSION_SCHEME,
            nextColorScheme=PREFER_DARK,
        )
        self.assertEqual(result["keysRead"], ["color-scheme", "color-scheme"])
        self.assertEqual(result["stNotifications"], ["color-scheme"] * 2)

    def test_repeated_changes_never_stack_the_style_class(self):
        result = self.run_scenario(
            "schemeChange",
            colorScheme="default",
            sessionColorScheme=CUSTOM_SESSION_SCHEME,
            nextColorScheme="default",
        )
        self.assertEqual(result["classes"], [STYLE_CLASS])

    def test_the_saved_scheme_survives_a_change_and_is_not_re_captured(self):
        """_sync() must not touch _savedColorScheme: on the light branch it has
        already written 'prefer-light' into sessionMode, so re-capturing there
        would overwrite the real value with the extension's own."""
        result = self.run_scenario(
            "schemeChange",
            colorScheme="default",
            sessionColorScheme=CUSTOM_SESSION_SCHEME,
            nextColorScheme=PREFER_DARK,
        )
        self.assertEqual(result["savedColorScheme"], CUSTOM_SESSION_SCHEME)


class TestDisable(LightStyleTestCase):
    def test_disable_leaves_no_style_class_behind(self):
        result = self.run_scenario(
            "lifecycle",
            colorScheme="default",
            sessionColorScheme=CUSTOM_SESSION_SCHEME,
        )
        self.assertEqual(result["afterEnable"]["classes"], [STYLE_CLASS])
        self.assertEqual(result["afterDisable"]["classes"], [])

    def test_disable_restores_the_session_scheme_captured_at_enable(self):
        result = self.run_scenario(
            "lifecycle",
            colorScheme="default",
            sessionColorScheme=CUSTOM_SESSION_SCHEME,
        )
        self.assertEqual(result["afterEnable"]["sessionColorScheme"], PREFER_LIGHT)
        self.assertEqual(
            result["afterDisable"]["sessionColorScheme"], CUSTOM_SESSION_SCHEME
        )

    def test_disable_restores_the_session_scheme_from_the_dark_branch_too(self):
        result = self.run_scenario(
            "lifecycle",
            colorScheme=PREFER_DARK,
            sessionColorScheme=CUSTOM_SESSION_SCHEME,
        )
        self.assertEqual(
            result["afterDisable"]["sessionColorScheme"], CUSTOM_SESSION_SCHEME
        )

    def test_disable_disconnects_the_handler_it_connected(self):
        result = self.run_scenario(
            "lifecycle",
            colorScheme="default",
            sessionColorScheme=CUSTOM_SESSION_SCHEME,
        )
        after = result["afterDisable"]
        self.assertEqual(after["disconnected"], [result["afterEnable"]["colorSchemeId"]])
        self.assertEqual(after["liveHandlers"], 0)
        self.assertIsNone(after["colorSchemeId"])

    def test_disable_drops_every_reference_it_held(self):
        """A retained Gio.Settings or saved scheme is a leak across the
        disable/enable cycle GNOME Shell runs on screen lock."""
        after = self.run_scenario(
            "lifecycle",
            colorScheme="default",
            sessionColorScheme=CUSTOM_SESSION_SCHEME,
        )["afterDisable"]
        self.assertTrue(after["interfaceSettingsIsNull"])
        self.assertIsNone(after["savedColorScheme"])

    def test_disable_notifies_st_settings_so_the_shell_drops_the_light_theme(self):
        after = self.run_scenario(
            "lifecycle",
            colorScheme="default",
            sessionColorScheme=CUSTOM_SESSION_SCHEME,
        )["afterDisable"]
        self.assertEqual(after["stNotifications"], ["color-scheme"] * 2)

    def test_a_scheme_change_after_disable_is_inert(self):
        """If the handler outlived disable() it would re-enter _sync() with a
        nulled _interfaceSettings and throw inside the dconf callback."""
        result = self.run_scenario(
            "changeAfterDisable",
            colorScheme="default",
            sessionColorScheme=CUSTOM_SESSION_SCHEME,
            nextColorScheme=PREFER_DARK,
        )
        self.assertFalse(result["threw"], "a post-disable dconf change threw")
        self.assertEqual(result["classes"], [])
        self.assertEqual(result["sessionColorScheme"], CUSTOM_SESSION_SCHEME)
        self.assertEqual(result["keysRead"], ["color-scheme"])


if __name__ == "__main__":
    unittest.main()
