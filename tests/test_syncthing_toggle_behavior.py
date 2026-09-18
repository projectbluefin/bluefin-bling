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
    def test_web_gui_url_targets_the_address_syncthing_listens_on(self):
        # dakota ships `<address>127.0.0.1:8384</address>` — IPv4 loopback only.
        # `localhost` can resolve to ::1 first, where nothing listens, so the
        # entry would open a dead page.
        result = run_scenario("web-gui-url", port=8384)
        self.assertEqual(result["launchedUris"], ["http://127.0.0.1:8384"])
        self.assertEqual(result["errors"], [])

    def test_web_gui_url_tracks_a_non_default_port(self):
        # The port is read at click time, so a dconf change must take effect
        # without re-enabling the extension.
        result = run_scenario("web-gui-url", port=12345)
        self.assertEqual(result["launchedUris"], ["http://127.0.0.1:12345"])

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
            ["Failed to run systemctl start"],
            "a start that never spawned was still persisted with `enable`",
        )

    def test_a_start_that_failed_is_not_persisted_and_not_announced(self):
        """systemctl can refuse: missing unit, masked unit, failing ExecStart.

        Announcing "sharing enabled" for a service that never came up lies to
        the user, and `enable` would make the broken state come back at the
        next login.
        """
        result = run_scenario("clicked", checked=True, systemctlFails=True)
        self.assertEqual(
            result["systemctl"],
            [["systemctl", "--user", "start", "syncthing.service"]],
            "a failed start was followed by `enable`",
        )
        self.assertEqual(
            result["notifications"],
            [],
            f"the failure was announced as success: {result['notifications']}",
        )
        self.assertTrue(result["errors"], "the failure was swallowed silently")

    def test_a_stop_that_failed_is_not_persisted_and_not_announced(self):
        result = run_scenario("clicked", checked=False, systemctlFails=True)
        self.assertEqual(
            result["systemctl"],
            [["systemctl", "--user", "stop", "syncthing.service"]],
        )
        self.assertEqual(result["notifications"], [])


@unittest.skipIf(NODE is None, "node is not installed; cannot execute toggle.js")
class TestSyncthingToggleEnable(unittest.TestCase):
    """What the extension does when GNOME Shell calls enable().

    These scenarios drive the real ``extension.js`` entry point, so the
    reconcile it performs, the poll source it installs and the indicator it
    registers are all observed rather than assumed.
    """

    def test_enable_reads_the_unit_once_and_registers_one_indicator(self):
        # Two initial refreshes mean a duplicate `systemctl status` on every
        # enable, including every screen unlock.
        result = run_scenario("enable")
        self.assertEqual(result["statusSubprocesses"], 1)
        self.assertEqual(result["externalIndicators"], 1)

    def test_enable_reflects_a_unit_started_outside_the_toggle(self):
        # `systemctl --user enable` from a previous session, or a terminal:
        # the toggle must show it without anyone clicking.
        result = run_scenario("enable", unitRunning=True)
        self.assertEqual(
            result["toggle"],
            {"checked": True, "subtitle": "Running", "indicatorVisible": True},
        )

    def test_a_session_that_starts_metered_pauses_the_running_unit(self):
        """`notify::network-metered` fires on changes only.

        Log in on mobile data with the unit enabled from the last session and
        no signal ever arrives, so enable() has to read the current metered
        state itself — otherwise the guard protects only sessions that were
        already open when the connection changed.
        """
        result = run_scenario("enable", unitRunning=True, metered=True)
        self.assertIn(
            "stop",
            result["verbs"],
            f"a metered login left the unit running; ran {result['verbs']}",
        )
        self.assertEqual(
            result["verbs"][0], "status", "the unit was stopped before it was read"
        )
        self.assertEqual(
            result["toggle"],
            {"checked": False, "subtitle": "Stopped", "indicatorVisible": False},
        )
        self.assertTrue(result["notifications"], "sharing was paused without saying so")

    def test_an_unmetered_session_is_left_alone(self):
        result = run_scenario("enable", unitRunning=True)
        self.assertEqual(result["verbs"], ["status"])
        self.assertEqual(result["notifications"], [])

    def test_enable_spawns_nothing_but_systemctl(self):
        # dakota provisions the syncthing config via /etc/skel; the extension
        # must not seed folders or run `syncthing generate`.
        result = run_scenario("enable", unitRunning=True, metered=True)
        self.assertEqual(result["programs"], ["systemctl"])

    def test_status_is_polled_on_a_timer(self):
        result = run_scenario("enable")
        self.assertEqual(
            result["installedSources"], 1, "expected exactly one status poll source"
        )
        # The interval itself is a tuning number, not a contract: what has to
        # hold is that the source is armed and that a tick reflects reality,
        # which test_a_poll_tick_picks_up_a_unit_stopped_elsewhere covers.
        self.assertGreater(
            result["pollSeconds"], 0, "a zero-second source is not a poll"
        )

    def test_a_poll_tick_picks_up_a_unit_stopped_elsewhere(self):
        result = run_scenario("poll-tick")
        self.assertEqual(result["before"]["subtitle"], "Running")
        self.assertEqual(result["statusSubprocesses"], 1, "the tick queried no status")
        self.assertTrue(
            result["returnValue"], "the timer returned SOURCE_REMOVE; polling dies"
        )
        self.assertEqual(
            result["after"],
            {"checked": False, "subtitle": "Stopped", "indicatorVisible": False},
        )


@unittest.skipIf(NODE is None, "node is not installed; cannot execute toggle.js")
class TestSyncthingToggleDisable(unittest.TestCase):
    def test_disable_leaves_no_source_or_handler_behind(self):
        result = run_scenario("disable")
        self.assertEqual(result["liveSources"], 0, "timer source outlived disable()")
        self.assertEqual(
            result["networkMonitorHandlers"],
            0,
            "network-metered handler outlived disable()",
        )
        self.assertEqual(
            result["toggleHandlers"], 0, "clicked handler outlived disable()"
        )
        self.assertTrue(result["cancellableCancelled"])

    def test_a_stale_tick_or_late_click_spawns_nothing(self):
        result = run_scenario("disable")
        self.assertFalse(
            result["staleTimerReturn"], "a stale tick asked to keep polling"
        )
        self.assertEqual(result["subprocessesAfterDisable"], [])
        self.assertEqual(result["notificationsAfterDisable"], [])

    def test_teardown_runs_once_even_if_destroy_is_called_again(self):
        # gnome-shell destroys the quick settings items and then the indicator,
        # so destroy() is reachable twice.
        result = run_scenario("disable")
        self.assertIsNone(
            result["secondDestroyError"],
            "destroy() after disable() raised instead of being a no-op",
        )
        self.assertEqual(result["superDestroyCount"], 1, "teardown ran more than once")

    def test_disable_cancels_in_flight_calls_and_kills_their_children(self):
        result = run_scenario("cancellation")
        self.assertGreaterEqual(
            result["inFlightAtDisable"], 1, "the scenario spawned nothing to cancel"
        )
        self.assertTrue(
            result["everyCallCancellable"],
            "a subprocess was started with a null cancellable and cannot be aborted",
        )
        self.assertTrue(
            result["everySubprocessForcedExit"],
            "cancelling a Cancellable only abandons the wait; the systemctl child "
            "keeps running unless cancellation is wired to proc.force_exit()",
        )
        self.assertEqual(
            result["cancellableHandlersLeft"],
            0,
            "each call must release its cancellation handler, or a long-lived "
            "cancellable accumulates one per status poll",
        )

    def test_a_call_that_succeeded_before_disable_is_not_announced_after(self):
        """Cancelling a Cancellable does not drop the pending callback.

        `systemctl start` can succeed and the status re-read still be in flight
        when the extension is disabled — a screen lock, an extension update.
        The continuation after that await resumes against torn-down widgets, so
        it must re-check the destroyed flag rather than post "Sharing Enabled"
        for an extension that is gone, or persist it with `enable`.
        """
        result = run_scenario("disable-mid-click")
        self.assertEqual(
            result["startedBeforeDisable"],
            ["start"],
            "the scenario disabled before the start even ran",
        )
        # The window under test is the one *after* a call succeeded. Had the
        # teardown landed while `start` was still in flight, the cancelled call
        # would return false and the early return would mask a missing guard,
        # so this test would pass while covering nothing.
        self.assertEqual(
            result["statusInFlightAtDisable"],
            1,
            "disable() landed before `start` resolved; this no longer covers "
            "the success-after-destroy window",
        )
        self.assertEqual(
            result["notifications"],
            [],
            f"a disabled extension still notified: {result['notifications']}",
        )
        self.assertEqual(
            [argv[2] for argv in result["systemctl"]],
            ["start"],
            "teardown did not stop the click handler from persisting the choice",
        )

    def test_a_cancelled_callback_never_touches_the_dead_widgets(self):
        # Cancelling a Cancellable does not drop the callback: it still fires,
        # with G_IO_ERROR_CANCELLED, after the widgets are gone.
        result = run_scenario("cancellation")
        self.assertEqual(result["subtitleAfter"], result["subtitleBefore"])
        self.assertFalse(result["indicatorVisible"])
        self.assertEqual(result["notifications"], [])


@unittest.skipIf(NODE is None, "node is not installed; cannot execute toggle.js")
class TestSyncthingToggleMetered(unittest.TestCase):
    def test_a_metered_connection_refuses_the_start_before_spawning(self):
        result = run_scenario("metered-click")
        self.assertEqual(
            result["systemctl"], [], "systemctl ran despite the metered connection"
        )
        self.assertFalse(
            result["checked"], "the toggle stayed on with nothing started"
        )
        self.assertEqual(len(result["notifications"]), 1)
        self.assertIn("Metered", result["notifications"][0]["body"])

    def test_going_metered_while_running_pauses_the_unit(self):
        result = run_scenario("metered-signal")
        self.assertEqual(
            result["whileUnmetered"], [], "an unmetered notify stopped the service"
        )
        self.assertEqual(
            result["whenMetered"][:1],
            ["stop"],
            "going metered while running did not stop the service",
        )
        self.assertTrue(result["notifications"])

    def test_after_a_metered_pause_the_toggle_reads_stopped(self):
        result = run_scenario("metered-signal")
        self.assertEqual(
            result["toggle"],
            {"checked": False, "subtitle": "Stopped", "indicatorVisible": False},
        )

    def test_the_metered_handler_is_gone_after_disable(self):
        result = run_scenario("metered-signal")
        self.assertEqual(
            result["afterDisable"],
            [],
            "the network-metered handler still fired after disable()",
        )

    def test_a_pause_that_cannot_stop_the_unit_stays_quiet(self):
        """Saying "paused" while the unit keeps running is worse than silence.

        The user reads it as "safe to stay on mobile data" — the one thing the
        metered guard exists to prevent — so the notification waits for a stop
        that actually succeeded.
        """
        result = run_scenario("failed-metered-stop")
        self.assertIn("stop", result["verbs"], "no stop was attempted at all")
        self.assertEqual(
            result["notifications"],
            [],
            f"a failed stop was announced as a pause: {result['notifications']}",
        )
        self.assertTrue(result["errors"], "the failed stop was swallowed silently")

    def test_a_failed_start_leaves_the_toggle_showing_the_real_state(self):
        result = run_scenario("failed-start")
        self.assertEqual(
            result["toggle"],
            {"checked": False, "subtitle": "Stopped", "indicatorVisible": False},
        )
        self.assertNotIn(
            "enable",
            result["verbs"],
            f"a failed start was persisted with `enable`; ran {result['verbs']}",
        )
        self.assertEqual(result["notifications"], [])


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

    def test_harness_loads_the_shipped_extension_entry_point(self):
        # The lifecycle scenarios import extension.js, whose relative
        # './toggle.js' import the harness has to rebind by hand. A scenario
        # that runs at all proves that rewrite is still current.
        result = run_scenario("enable")
        self.assertEqual(result["externalIndicators"], 1)


if __name__ == "__main__":
    unittest.main()
