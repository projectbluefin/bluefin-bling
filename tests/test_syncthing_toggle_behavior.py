"""Executed behavior coverage for extensions/syncthing-toggle/toggle.js.

The rest of the suite checks toggle.js statically (it parses, it is ESM, its
GSettings keys exist in the gschema, its service-name regex has the right
shape). Nothing executes it, so the wiring that decides what the Quick Settings
toggle actually does was unverified: the Web GUI URL, the icon fallback, the
notification text, the systemctl verb sequence, the start-stop-only gate and
the indicator/subtitle update.

These tests drive the real, unmodified toggle.js through
``syncthing_toggle_harness.mjs``, which rewrites only the gnome-shell import
block and leaves every statement under test byte-for-byte as shipped.

Deliberately out of scope: the argv and logging inside ``checkStatus()``. The
harness only counts that a status refresh happened, distinguishing it from
``_runSystemctl`` by ``Gio.SubprocessFlags``, so hardening the status command
does not have to touch this file.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "syncthing_toggle_harness.mjs"
TOGGLE_JS = REPO_ROOT / "extensions" / "syncthing-toggle" / "toggle.js"

NODE = shutil.which("node")

EXTENSION_PATH = "/usr/share/gnome-shell/extensions/syncthing-toggle"


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


@unittest.skipIf(NODE is None, "node is not installed; cannot execute toggle.js")
class TestSyncthingToggleConstruction(unittest.TestCase):
    def test_toggle_is_a_titled_two_state_quick_settings_item(self):
        result = run_scenario("toggle-construction")
        self.assertEqual(result["title"], "Sync Folder")
        self.assertTrue(result["toggleMode"])
        # The real state is unknown until checkStatus() reports it.
        self.assertEqual(result["subtitle"], "Loading")

    def test_menu_header_reuses_the_toggle_icon_and_title(self):
        result = run_scenario("toggle-construction")
        self.assertEqual(result["headerTitle"], "Sync Folder")
        self.assertTrue(
            result["headerIconIsToggleIcon"],
            "menu header must use the same gicon instance as the toggle",
        )

    def test_menu_offers_web_gui_then_a_separated_settings_entry(self):
        result = run_scenario("toggle-construction")
        self.assertEqual(result["sectionActionLabels"], ["Open Web GUI"])
        self.assertEqual(result["menuItemKinds"], ["section", "separator"])
        self.assertEqual(result["menuActionLabels"], ["Extension Settings"])

    def test_settings_entry_is_registered_under_the_extension_uuid(self):
        # gnome-shell hides entries in menu._settingsActions on the lock screen
        # by uuid; registering under the wrong key leaks the entry when locked.
        result = run_scenario("toggle-construction")
        self.assertEqual(
            result["settingsActionUuids"], ["syncthing-toggle@projectbluefin.io"]
        )

    def test_settings_entry_visibility_follows_session_mode(self):
        unlocked = run_scenario("toggle-construction", allowSettings=True)
        locked = run_scenario("toggle-construction", allowSettings=False)
        self.assertTrue(unlocked["settingsActionVisible"])
        self.assertFalse(
            locked["settingsActionVisible"],
            "Extension Settings must be hidden when sessionMode forbids settings",
        )

    def test_indicator_exposes_exactly_the_toggle_as_a_quick_settings_item(self):
        result = run_scenario("toggle-construction")
        self.assertEqual(result["quickSettingsItemCount"], 1)
        self.assertTrue(result["quickSettingsItemIsToggle"])

    def test_settings_entry_opens_extension_preferences(self):
        result = run_scenario("settings-action")
        self.assertEqual(result["openPreferencesCalls"], 1)


@unittest.skipIf(NODE is None, "node is not installed; cannot execute toggle.js")
class TestSyncthingToggleIconResolution(unittest.TestCase):
    def test_blank_icon_name_falls_back_to_the_shipped_symbolic(self):
        result = run_scenario("icon-resolution", iconName="", extensionPath=EXTENSION_PATH)
        self.assertEqual(
            result["iconStrings"],
            [f"{EXTENSION_PATH}/icons/syncthing-symbolic.svg"],
        )

    def test_whitespace_only_icon_name_also_falls_back(self):
        # get_string('icon-name').trim() || fallback — a space-only setting must
        # not resolve to an empty gicon and blank the indicator.
        result = run_scenario(
            "icon-resolution", iconName="   ", extensionPath=EXTENSION_PATH
        )
        self.assertEqual(
            result["iconStrings"],
            [f"{EXTENSION_PATH}/icons/syncthing-symbolic.svg"],
        )

    def test_custom_icon_name_is_trimmed_and_used(self):
        result = run_scenario("icon-resolution", iconName="  my-icon-symbolic  ")
        self.assertEqual(result["iconStrings"], ["my-icon-symbolic"])

    def test_indicator_and_toggle_share_one_icon(self):
        result = run_scenario("icon-resolution")
        self.assertEqual(result["indicatorGicon"], result["toggleGicon"])


@unittest.skipIf(NODE is None, "node is not installed; cannot execute toggle.js")
class TestSyncthingToggleWebGui(unittest.TestCase):
    def test_web_gui_url_is_built_from_the_port_setting(self):
        result = run_scenario("web-gui-url", port=8384)
        self.assertEqual(result["launchedUris"], ["http://localhost:8384"])
        self.assertEqual(result["errors"], [])

    def test_web_gui_url_tracks_a_non_default_port(self):
        # The port is read at click time, so a dconf change must take effect
        # without re-enabling the extension.
        result = run_scenario("web-gui-url", port=12345)
        self.assertEqual(result["launchedUris"], ["http://localhost:12345"])

    def test_launch_failure_is_logged_and_not_thrown(self):
        result = run_scenario("web-gui-url", port=8384, launchThrows=True)
        self.assertEqual(result["launchedUris"], [])
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("Failed to open URL", result["errors"][0])


@unittest.skipIf(NODE is None, "node is not installed; cannot execute toggle.js")
class TestSyncthingToggleUpdateStatus(unittest.TestCase):
    def test_running_service_shows_the_indicator_and_checks_the_toggle(self):
        result = run_scenario("update-status")["active"]
        self.assertTrue(result["indicatorVisible"])
        self.assertTrue(result["checked"])
        self.assertEqual(result["subtitle"], "Running")

    def test_stopped_service_hides_the_indicator_and_unchecks_the_toggle(self):
        result = run_scenario("update-status")["inactive"]
        self.assertFalse(result["indicatorVisible"])
        self.assertFalse(result["checked"])
        self.assertEqual(result["subtitle"], "Stopped")


@unittest.skipIf(NODE is None, "node is not installed; cannot execute toggle.js")
class TestSyncthingToggleClicked(unittest.TestCase):
    def test_turning_on_starts_then_enables_the_service(self):
        result = run_scenario("clicked", checked=True)
        self.assertEqual(
            result["systemctl"],
            [
                ["systemctl", "--user", "start", "syncthing.service"],
                ["systemctl", "--user", "enable", "syncthing.service"],
            ],
        )

    def test_turning_off_stops_then_disables_the_service(self):
        result = run_scenario("clicked", checked=False)
        self.assertEqual(
            result["systemctl"],
            [
                ["systemctl", "--user", "stop", "syncthing.service"],
                ["systemctl", "--user", "disable", "syncthing.service"],
            ],
        )

    def test_service_name_is_a_discrete_argv_entry_never_interpolated(self):
        result = run_scenario("clicked", checked=True, serviceName="syncthing@user.service")
        for argv in result["systemctl"]:
            self.assertEqual(len(argv), 4, f"argv must stay 4 entries: {argv}")
            self.assertEqual(argv[3], "syncthing@user.service")
            self.assertNotIn("sh", argv)
            self.assertNotIn("-c", argv)

    def test_start_stop_only_suppresses_the_enable_disable_call(self):
        result = run_scenario("clicked", checked=True, startStopOnly=True)
        self.assertEqual(
            result["systemctl"],
            [["systemctl", "--user", "start", "syncthing.service"]],
        )

    def test_start_stop_only_suppresses_disable_when_turning_off(self):
        result = run_scenario("clicked", checked=False, startStopOnly=True)
        self.assertEqual(
            result["systemctl"],
            [["systemctl", "--user", "stop", "syncthing.service"]],
        )

    def test_status_is_refreshed_after_the_start_stop_call(self):
        result = run_scenario("clicked", checked=True)
        self.assertEqual(result["statusSubprocesses"], 1)

    def test_notification_text_matches_the_requested_direction(self):
        on = run_scenario("clicked", checked=True)["notifications"]
        off = run_scenario("clicked", checked=False)["notifications"]
        self.assertEqual(
            on,
            [
                {
                    "title": "Sync Folder Sharing Enabled",
                    "body": "Your files are sharing with your other devices.",
                }
            ],
        )
        self.assertEqual(
            off,
            [
                {
                    "title": "Sync Folder Sharing Disabled",
                    "body": "File sharing is paused.",
                }
            ],
        )

    def test_invalid_service_name_runs_no_systemctl_command_at_all(self):
        result = run_scenario(
            "clicked", checked=True, serviceName="syncthing.service; reboot"
        )
        self.assertEqual(result["systemctl"], [])
        self.assertEqual(
            result["statusSubprocesses"],
            0,
            "a rejected service-name must not reach any systemctl invocation",
        )

    def test_spawn_failure_is_logged_and_never_escapes_the_click_handler(self):
        # The handler is an async signal callback; an unhandled rejection here
        # surfaces in gnome-shell's journal and aborts the remaining steps.
        result = run_scenario("clicked", checked=True, subprocessThrows=True)
        self.assertEqual(result["systemctl"], [])
        run_errors = [e for e in result["errors"] if any("systemctl" in p for p in e)]
        self.assertEqual(
            [e[-1] for e in run_errors],
            ["Failed to run systemctl start", "Failed to run systemctl enable"],
        )

    def test_systemctl_nonzero_exit_is_logged_and_the_sequence_continues(self):
        result = run_scenario("clicked", checked=True, systemctlFails=True)
        self.assertEqual(
            result["systemctl"],
            [
                ["systemctl", "--user", "start", "syncthing.service"],
                ["systemctl", "--user", "enable", "syncthing.service"],
            ],
        )


@unittest.skipIf(NODE is None, "node is not installed; cannot execute toggle.js")
class TestHarnessFidelity(unittest.TestCase):
    """Guard the harness against silently drifting away from the real source."""

    def test_harness_targets_the_shipped_toggle_js(self):
        self.assertTrue(TOGGLE_JS.is_file())
        self.assertIn("syncthing-toggle", str(TOGGLE_JS))

    def test_toggle_js_still_exports_serviceindicator(self):
        # The harness constructs module.ServiceIndicator; a rename would make
        # every scenario above fail with an opaque TypeError instead.
        source = TOGGLE_JS.read_text(encoding="utf-8")
        self.assertRegex(source, r"export\s+var\s+ServiceIndicator\b")

    def test_harness_rewrite_covers_every_gnome_import(self):
        # If toggle.js gains an import style the harness rewrite does not
        # handle, the data: module fails to compile. Loading any scenario
        # proves the rewrite still covers the whole import block.
        result = run_scenario("toggle-construction")
        self.assertEqual(result["title"], "Sync Folder")


if __name__ == "__main__":
    unittest.main()
