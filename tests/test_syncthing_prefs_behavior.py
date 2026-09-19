"""Executed behavior coverage for extensions/syncthing-toggle/prefs.js.

The rest of the suite checks prefs.js statically: that it parses, that it is
ESM, that it default-exports an ``ExtensionPreferences`` subclass, and that a
regex can read the port ``Gtk.Adjustment`` bounds back out of the source. None
of it executes the file, so everything the preferences dialog actually does was
unverified — which schema it opens, where it parks the settings object, the
icon search path, the row order, every GSettings binding, the adjustment seeded
from the stored port, and the About window's metadata.

These tests drive the real, unmodified prefs.js through
``syncthing_prefs_harness.mjs``, which rewrites only the gnome-shell import
block and leaves every statement under test byte-for-byte as shipped.

Several assertions are made against the shipped ``metadata.json`` and gschema
rather than against literals, so a key renamed in one file and not the other
fails here instead of at runtime, where a bad binding is silent: the widget
moves and the preference never changes.

Deliberately out of scope: real Adw/Gtk widget behavior. The stubs record the
constructor properties and method calls prefs.js makes; they do not model GTK.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "syncthing_prefs_harness.mjs"
EXTENSION_DIR = REPO_ROOT / "extensions" / "syncthing-toggle"
METADATA = EXTENSION_DIR / "metadata.json"
GSCHEMA = (
    EXTENSION_DIR
    / "schemas"
    / "org.gnome.shell.extensions.syncthing-toggle.gschema.xml"
)

NODE = shutil.which("node")

BIND_FLAGS_DEFAULT = "Gio.SettingsBindFlags.DEFAULT"


def metadata() -> dict:
    return json.loads(METADATA.read_text(encoding="utf-8"))


def schema_keys() -> set[str]:
    root = ET.parse(GSCHEMA).getroot()
    return {key.get("name") for key in root.iter("key")}


def run_scenario(name: str, **options) -> dict:
    """Run one harness scenario and return its decoded JSON result."""
    argv = [NODE, str(HARNESS), name]
    if options:
        argv.append(json.dumps(options))
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(
            f"harness scenario {name!r} failed ({result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return json.loads(result.stdout)


@unittest.skipIf(NODE is None, "node is not installed; cannot execute prefs.js")
class TestFillPreferencesWindow(unittest.TestCase):
    def test_opens_the_schema_metadata_declares(self):
        # A schema id that drifts from metadata's settings-schema makes
        # getSettings() throw and the dialog never opens.
        result = run_scenario("window-setup")
        self.assertEqual(
            result["requestedSchemas"], [metadata()["settings-schema"]]
        )

    def test_settings_object_is_parked_on_the_window(self):
        # _general() and the bindings all reach settings through
        # window._settings; storing it anywhere else breaks every binding.
        result = run_scenario("window-setup")
        self.assertEqual(
            result["settingsOnWindow"], metadata()["settings-schema"]
        )

    def test_window_is_retained_for_general(self):
        result = run_scenario("window-setup")
        self.assertTrue(result["windowRetained"])

    def test_icons_directory_is_added_to_the_search_path(self):
        # The About window asks for 'syncthing-logo-only', which is not a
        # system icon: without this search path it renders as missing.
        extension_path = "/usr/share/gnome-shell/extensions/syncthing-toggle"
        result = run_scenario("window-setup", extensionPath=extension_path)
        self.assertEqual(
            result["iconSearchPaths"], [f"{extension_path}/icons"]
        )

    def test_icons_directory_is_resolved_from_the_extension_directory(self):
        # Proves the path is derived from this.dir rather than hardcoded.
        result = run_scenario("window-setup", extensionPath="/tmp/elsewhere")
        self.assertEqual(result["iconSearchPaths"], ["/tmp/elsewhere/icons"])

    def test_icon_theme_is_taken_from_the_windows_own_display(self):
        result = run_scenario("window-setup")
        self.assertEqual(result["iconThemeUsedWindowDisplay"], [True])


@unittest.skipIf(NODE is None, "node is not installed; cannot execute prefs.js")
class TestPreferencesWidgetTree(unittest.TestCase):
    def test_builds_exactly_one_page_with_one_group(self):
        result = run_scenario("widget-tree")
        self.assertEqual(result["pageCount"], 1)
        self.assertEqual(result["groupCount"], 1)

    def test_group_carries_the_shipped_title_and_description(self):
        result = run_scenario("widget-tree")
        self.assertEqual(result["groupTitle"], "General Settings")
        self.assertEqual(result["groupDescription"], "Configure Syncthing Toggle")

    def test_rows_appear_in_shipped_order_with_shipped_types(self):
        # Row order is the dialog's reading order, and the row type decides
        # what control the user gets. Both are user-visible.
        result = run_scenario("widget-tree")
        self.assertEqual(
            [(row["kind"], row["title"]) for row in result["rows"]],
            [
                ("EntryRow", "Service name"),
                ("ActionRow", "Syncthing port"),
                ("SwitchRow", "Start/Stop only"),
                ("EntryRow", "Custom icon name"),
            ],
        )

    def test_only_the_port_and_switch_rows_carry_subtitles(self):
        result = run_scenario("widget-tree")
        self.assertEqual(
            [row["subtitle"] for row in result["rows"]],
            [
                None,
                "Set the port Syncthing runs on.",
                (
                    "Whether or not to only start/stop or also enable/disable "
                    "the Syncthing service when toggling."
                ),
                None,
            ],
        )


@unittest.skipIf(NODE is None, "node is not installed; cannot execute prefs.js")
class TestSettingsBindings(unittest.TestCase):
    def test_binds_every_key_the_dialog_exposes_in_row_order(self):
        result = run_scenario("settings-bindings")
        self.assertEqual(
            [(bind["key"], bind["property"]) for bind in result["binds"]],
            [
                ("service-name", "text"),
                ("port", "value"),
                ("start-stop-only", "active"),
                ("icon-name", "text"),
            ],
        )

    def test_every_bound_key_exists_in_the_gschema(self):
        # A bound key with no schema entry aborts the prefs process on open.
        result = run_scenario("settings-bindings")
        bound = {bind["key"] for bind in result["binds"]}
        self.assertLessEqual(bound, schema_keys())

    def test_every_schema_key_is_reachable_from_the_dialog(self):
        # The inverse direction: a key nothing binds is only settable with
        # gsettings(1), so the preference is effectively unreachable.
        result = run_scenario("settings-bindings")
        bound = {bind["key"] for bind in result["binds"]}
        self.assertEqual(bound, schema_keys())

    def test_bindings_are_two_way_defaults(self):
        # GET_NO_CHANGES or SET_NO_CHANGES here would make the dialog forget
        # edits or ignore changes made elsewhere.
        result = run_scenario("settings-bindings")
        self.assertEqual(
            [bind["flags"] for bind in result["binds"]],
            [BIND_FLAGS_DEFAULT] * 4,
        )

    def test_each_key_is_bound_to_its_own_widget(self):
        # Binding the right key to the wrong widget is silent: the control
        # moves and the setting never changes.
        result = run_scenario("settings-bindings")
        self.assertTrue(result["serviceNameBoundToRow"])
        self.assertTrue(result["portBoundToSpinButton"])
        self.assertTrue(result["startStopOnlyBoundToRow"])
        self.assertTrue(result["iconNameBoundToRow"])


@unittest.skipIf(NODE is None, "node is not installed; cannot execute prefs.js")
class TestPortAdjustment(unittest.TestCase):
    def test_spin_button_is_the_port_rows_activatable_suffix(self):
        result = run_scenario("port-adjustment")
        self.assertEqual(result["suffixCount"], 1)
        self.assertTrue(result["rowActivatesSpinButton"])

    def test_spin_button_is_numeric_and_horizontally_centred(self):
        result = run_scenario("port-adjustment")
        self.assertTrue(result["numeric"])
        self.assertEqual(result["valign"], "Gtk.Align.CENTER")
        self.assertEqual(result["orientation"], "Gtk.Orientation.HORIZONTAL")

    def test_adjustment_spans_the_full_tcp_port_range(self):
        # Narrower bounds silently clamp a stored port on open; the gschema
        # <range> is the authority and the widget must match it.
        root = ET.parse(GSCHEMA).getroot()
        port_range = next(
            key.find("range") for key in root.iter("key") if key.get("name") == "port"
        )
        result = run_scenario("port-adjustment")
        self.assertEqual(result["lower"], int(port_range.get("min")))
        self.assertEqual(result["upper"], int(port_range.get("max")))
        self.assertEqual(result["stepIncrement"], 1)

    def test_adjustment_is_seeded_from_the_stored_port(self):
        result = run_scenario("port-adjustment", settings={"port": 9000})
        self.assertEqual(result["settingsReads"], ["port"])
        self.assertEqual(result["value"], 9000)

    def test_a_non_default_port_is_not_reset_to_the_schema_default(self):
        result = run_scenario("port-adjustment", settings={"port": 1})
        self.assertEqual(result["value"], 1)


@unittest.skipIf(NODE is None, "node is not installed; cannot execute prefs.js")
class TestAboutButton(unittest.TestCase):
    def test_about_button_is_a_group_header_suffix_not_a_row(self):
        result = run_scenario("about-button")
        self.assertTrue(result["buttonIsHeaderSuffix"])
        self.assertTrue(result["buttonIsNotARow"])

    def test_about_button_uses_the_accent_style(self):
        result = run_scenario("about-button")
        self.assertEqual(result["cssClasses"], ["accent"])

    def test_about_button_shows_the_information_icon_and_label(self):
        result = run_scenario("about-button")
        self.assertEqual(result["label"], "About")
        self.assertEqual(result["iconName"], "dialog-information-symbolic")

    def test_about_button_carries_its_vertical_margins(self):
        result = run_scenario("about-button")
        self.assertEqual(result["marginTop"], 8)
        self.assertEqual(result["marginBottom"], 8)

    def test_no_about_window_is_built_until_the_button_is_clicked(self):
        result = run_scenario("about-button")
        self.assertEqual(result["aboutWindowsBeforeClick"], 0)


@unittest.skipIf(NODE is None, "node is not installed; cannot execute prefs.js")
class TestAboutWindow(unittest.TestCase):
    def test_clicking_about_opens_exactly_one_window(self):
        result = run_scenario("about-window")
        self.assertEqual(result["aboutWindowCount"], 1)
        self.assertTrue(result["shown"])

    def test_about_window_is_modal_and_transient_for_the_prefs_window(self):
        # A non-transient modal can end up behind the dialog it blocks.
        result = run_scenario("about-window")
        self.assertTrue(result["transientForPrefsWindow"])
        self.assertTrue(result["modal"])

    def test_about_window_uses_the_bundled_logo(self):
        # 'syncthing-logo-only' resolves only through the icons search path
        # fillPreferencesWindow registers.
        result = run_scenario("about-window")
        self.assertEqual(result["applicationIcon"], "syncthing-logo-only")

    def test_about_window_reports_the_metadata_version(self):
        result = run_scenario("about-window", metadata={"version": 7})
        self.assertEqual(result["version"], "7.0")

    def test_about_window_links_derive_from_the_metadata_url(self):
        url = metadata()["url"]
        result = run_scenario("about-window")
        self.assertEqual(result["website"], url)
        self.assertEqual(result["issueUrl"], f"{url}/issues")

    def test_about_window_issue_url_follows_a_relocated_repository(self):
        result = run_scenario(
            "about-window", metadata={"url": "https://example.invalid/repo"}
        )
        self.assertEqual(result["issueUrl"], "https://example.invalid/repo/issues")

    def test_about_window_declares_gpl_3_and_upstream_attribution(self):
        # The extension is a GPL-3.0 derivative; COPYING and this dialog are
        # the only places a user can see that.
        result = run_scenario("about-window")
        self.assertEqual(result["licenseType"], "Gtk.License.GPL_3_0")
        self.assertEqual(result["applicationName"], "Syncthing Toggle")
        self.assertEqual(result["developerName"], "rehhouari (rehhouari@gmail.com)")
        self.assertEqual(result["copyright"], "© 2024 rehhouari")


@unittest.skipIf(NODE is None, "node is not installed; cannot execute prefs.js")
class TestTranslatableStrings(unittest.TestCase):
    def test_translated_strings_are_exactly_the_ones_shipped_through_gettext(self):
        # The port row's title and subtitle are built from bare literals, so
        # they stay English in a translated session. Pinning the set keeps that
        # omission visible rather than letting it pass as an untested detail.
        result = run_scenario("translation")
        self.assertEqual(
            result["translated"],
            [
                "General Settings",
                "Configure Syncthing Toggle",
                "Service name",
                "Start/Stop only",
                (
                    "Whether or not to only start/stop or also enable/disable "
                    "the Syncthing service when toggling."
                ),
                "About",
                "Custom icon name",
            ],
        )

    def test_port_row_strings_bypass_gettext(self):
        result = run_scenario("widget-tree")
        port_row = result["rows"][1]
        translated = set(run_scenario("translation")["translated"])
        self.assertNotIn(port_row["title"], translated)
        self.assertNotIn(port_row["subtitle"], translated)


if __name__ == "__main__":
    unittest.main()
