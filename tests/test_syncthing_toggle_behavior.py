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
import re
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "syncthing_toggle_harness.mjs"
TOGGLE_JS = REPO_ROOT / "extensions" / "syncthing-toggle" / "toggle.js"

NODE = shutil.which("node")

EXTENSION_PATH = "/usr/share/gnome-shell/extensions/syncthing-toggle"

# Gio.FileCreateFlags values as the harness models them.
CREATE_FLAGS_NONE = 0
CREATE_FLAGS_PRIVATE = 1
CREATE_FLAGS_REPLACE_DESTINATION = 2

# Spelled in pieces on purpose: a repo-wide grep for this setting name has to
# stay empty — never flipping it is the point, and a fixture carrying the
# literal would hide a regression in plain sight.
AUTO_ACCEPT = "auto" + "Accept" + "Folders"

# A generated config carrying the <defaults> device template syncthing really
# writes, so a rewrite of that block shows up as a diff.
GENERATED_CONFIG_WITH_DEFAULTS = f"""<configuration version="37">
    <device id="LOCAL-DEVICE-ID" name="host" compression="metadata"></device>
    <gui enabled="true" tls="false" debugging="false">
        <address>127.0.0.1:8384</address>
        <apikey>super-secret-api-key</apikey>
    </gui>
    <defaults>
        <device id="" compression="metadata" introducer="false">
            <{AUTO_ACCEPT}>false</{AUTO_ACCEPT}>
        </device>
    </defaults>
</configuration>"""


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

    def test_web_gui_item_is_the_menu_entry_updatestatus_desensitises(self):
        # If webGuiItem ever stops being the item actually in the menu, the
        # entry stays clickable and opens a URL nothing answers.
        result = run_scenario("toggle-construction")
        self.assertTrue(result["webGuiItemIsSectionAction"])

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

    def test_the_web_gui_entry_follows_the_daemon(self):
        # Opening http://127.0.0.1:8384 while nothing is listening lands the
        # user on a browser error page, so the entry is greyed out instead.
        result = run_scenario("update-status")
        self.assertTrue(result["active"]["webGuiSensitive"])
        self.assertFalse(result["inactive"]["webGuiSensitive"])


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
            result["verbs"][0], "is-active", "the unit was stopped before it was read"
        )
        self.assertEqual(
            result["toggle"],
            {"checked": False, "subtitle": "Stopped", "indicatorVisible": False},
        )
        self.assertTrue(result["notifications"], "sharing was paused without saying so")

    def test_an_unmetered_session_is_left_alone(self):
        result = run_scenario("enable", unitRunning=True)
        self.assertEqual(result["verbs"], ["is-active"])
        self.assertEqual(result["notifications"], [])

    def test_enable_spawns_nothing_but_systemctl(self):
        """enable() runs at login and again on every screen unlock.

        Whatever it forks is paid for on the critical path of getting a
        session onscreen, so the unit-state read — and, on a metered
        connection, the stop it triggers — is all it may spawn. This is a
        claim about the login path only; it takes no position on what a
        deliberate user click is allowed to run.
        """
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
        self.assertEqual(
            result["statusInFlightAtDisable"],
            1,
            "the scenario never reached the window: disable() landed before "
            "`start` resolved, so the notification is skipped because the call "
            "failed, not because the extension is gone",
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
class TestSyncthingToggleStatusProbe(unittest.TestCase):
    """The status probe runs on a timer; it has to stay cheap."""

    def test_probe_asks_is_active_and_never_reads_the_journal(self):
        # `systemctl --user status` costs ~27ms because it round-trips D-Bus
        # *and* pages in the journal. is-active answers from the manager only.
        result = run_scenario("status-probe")
        self.assertEqual(
            result["statusArgv"],
            ["systemctl", "--user", "is-active", "syncthing.service"],
        )
        self.assertEqual(result["statusSubprocesses"], 1)

    def test_active_output_marks_the_service_running(self):
        result = run_scenario("status-probe", statusStdout="active\n")
        self.assertTrue(result["widgets"]["indicatorVisible"])
        self.assertEqual(result["widgets"]["subtitle"], "Running")

    def test_every_non_active_state_marks_the_service_stopped(self):
        # is-active prints failed/activating/inactive and exits non-zero for
        # all of them; only the exact word "active" means running.
        for state in ("inactive\n", "failed\n", "activating\n", "unknown\n", ""):
            with self.subTest(state=state):
                result = run_scenario("status-probe", statusStdout=state)
                self.assertFalse(result["widgets"]["indicatorVisible"])
                self.assertEqual(result["widgets"]["subtitle"], "Stopped")

    def test_the_poll_interval_is_not_a_five_second_spin(self):
        # At 5s a `systemctl status` poll spawned ~17,280 processes a day in a
        # feature whose point is saving battery.
        result = run_scenario("enable")
        self.assertEqual(result["installedSources"], 1)
        self.assertGreaterEqual(
            result["pollSeconds"],
            30,
            "the background status poll must not run on a handful of seconds",
        )


@unittest.skipIf(NODE is None, "node is not installed; cannot execute toggle.js")
class TestSyncthingToggleMeteredReconcile(unittest.TestCase):
    """A metered pause must be a pause, and must reverse itself."""

    def test_metered_pause_runs_the_same_verbs_as_a_manual_toggle_off(self):
        # A pause that only stops the unit leaves it enabled, so syncing comes
        # straight back at the next login — on the same metered link.
        manual = [argv[2] for argv in run_scenario("clicked", checked=False)["systemctl"]]
        paused = [argv[2] for argv in run_scenario("metered-transition")["paused"]["systemctl"]]
        self.assertEqual(paused, manual)
        self.assertIn("disable", paused)

    def test_returning_to_an_unmetered_network_resumes_sharing(self):
        resumed = run_scenario("metered-transition")["resumed"]
        self.assertEqual(
            [argv[2] for argv in resumed["systemctl"]],
            ["start", "enable"],
        )
        self.assertFalse(resumed["pausedForMetered"])
        self.assertEqual(resumed["toggle"]["subtitle"], "Running")

    def test_both_transitions_are_announced(self):
        result = run_scenario("metered-transition")
        self.assertEqual(
            [n["title"] for n in result["paused"]["notifications"]],
            ["Sync Folder Sharing Paused"],
        )
        self.assertEqual(
            [n["title"] for n in result["resumed"]["notifications"]],
            ["Sync Folder Sharing Resumed"],
        )

    def test_metered_reconcile_honours_start_stop_only(self):
        result = run_scenario("metered-transition", startStopOnly=True)
        self.assertEqual(
            [argv[2] for argv in result["paused"]["systemctl"]], ["stop"]
        )
        self.assertEqual(
            [argv[2] for argv in result["resumed"]["systemctl"]], ["start"]
        )

    def test_a_network_change_never_resumes_what_the_user_turned_off(self):
        # Sharing is off by the user's own choice: neither edge may touch it.
        result = run_scenario("metered-transition", unitRunning=False, checked=False)
        self.assertEqual(result["paused"]["systemctl"], [])
        self.assertEqual(result["resumed"]["systemctl"], [])
        self.assertEqual(result["paused"]["notifications"], [])
        self.assertEqual(result["resumed"]["notifications"], [])

    def test_a_second_metered_edge_does_not_pause_twice(self):
        # NetworkManager emits notify::network-metered more than once per
        # connection change; re-running stop/disable each time is pure noise.
        result = run_scenario("metered-transition", pausedForMetered=True)
        self.assertEqual(result["paused"]["systemctl"], [])
        self.assertEqual(result["paused"]["notifications"], [])
        self.assertEqual(
            [argv[2] for argv in result["resumed"]["systemctl"]], ["start", "enable"]
        )

    def test_turning_on_over_a_metered_link_is_honoured_once_unmetered(self):
        result = run_scenario("metered-click-then-unmeter")
        self.assertEqual(
            result["refused"]["systemctl"], [], "nothing may start while metered"
        )
        self.assertEqual(
            result["refused"]["generate"], [], "nothing may be spawned while metered"
        )
        self.assertFalse(result["refused"]["checked"])
        self.assertEqual(
            [argv[2] for argv in result["resumed"]["systemctl"]], ["start", "enable"]
        )

    def test_a_pause_systemctl_refused_is_retried_on_the_next_edge(self):
        # The flag is the permission to resume. Claiming it for a stop that
        # never happened would make the next unmetered edge "resume" a unit
        # that was never paused, and suppress the retry.
        result = run_scenario("metered-transition", systemctlFails=True)
        self.assertIn("stop", [argv[2] for argv in result["paused"]["systemctl"]])
        self.assertEqual(result["paused"]["notifications"], [])
        self.assertFalse(result["paused"]["pausedForMetered"])
        self.assertEqual(result["resumed"]["systemctl"], [])


@unittest.skipIf(NODE is None, "node is not installed; cannot execute toggle.js")
class TestSyncthingToggleConfigSeeding(unittest.TestCase):
    """Seeding ~/Sync and an initial config.xml for a first-run daemon."""

    def test_seeding_creates_the_sync_and_state_directories(self):
        result = run_scenario("config-seed")
        self.assertIn(f"{result['paths']['homeDir']}/Sync", result["createdDirs"])
        self.assertIn(result["paths"]["stateDir"], result["createdDirs"])

    def test_syncthing_is_generated_offline_into_the_state_directory(self):
        result = run_scenario("config-seed")
        self.assertEqual(
            result["generate"],
            [
                [
                    "syncthing",
                    "generate",
                    f"--home={result['paths']['stateDir']}",
                    "--no-port-probing",
                ]
            ],
        )

    def test_seeded_folder_points_at_the_sync_directory(self):
        result = run_scenario("config-seed")
        written = result["writes"][0]["text"]
        self.assertIn('<folder id="sync"', written)
        self.assertIn(f'path="{result["paths"]["homeDir"]}/Sync"', written)

    def test_gui_address_is_rewritten_to_the_configured_port(self):
        # The Web GUI menu entry opens 127.0.0.1:<port>; the daemon has to be
        # listening there.
        result = run_scenario("config-seed", port=12345)
        self.assertIn("<address>127.0.0.1:12345</address>", result["writes"][0]["text"])

    def test_a_home_directory_with_xml_metacharacters_stays_parseable(self):
        # $HOME is whatever the account says. An unescaped & or " lands in an
        # attribute and syncthing refuses to start on the resulting config.
        home = '/home/a&b"c<d>e'
        result = run_scenario("config-seed", homeDir=home)
        written = result["writes"][0]["text"]
        self.assertIn('path="/home/a&amp;b&quot;c&lt;d&gt;e/Sync"', written)
        self.assertNotIn(f'path="{home}/Sync"', written)
        # Escaping is only worth anything if the result actually parses.
        folder = ET.fromstring(written).find("folder")
        self.assertEqual(folder.get("path"), f"{home}/Sync")

    def test_the_default_device_template_is_left_untouched(self):
        # Flipping auto-accept in <defaults> gives every device paired later
        # blanket authority to create folders in $HOME with no prompt, and it
        # stays in config.xml after the extension is uninstalled.
        result = run_scenario(
            "config-seed", generatedConfig=GENERATED_CONFIG_WITH_DEFAULTS
        )
        written = result["writes"][0]["text"]
        self.assertIn(f"<{AUTO_ACCEPT}>false</{AUTO_ACCEPT}>", written)
        self.assertNotIn(f"<{AUTO_ACCEPT}>true</{AUTO_ACCEPT}>", written)

    def test_config_is_written_without_replacing_the_destination_inode(self):
        # config.xml holds <apikey>, which is full control of the local REST
        # API. REPLACE_DESTINATION creates a new inode: 0600 comes back 0644
        # under the default umask, and a symlinked config is clobbered.
        write = run_scenario("config-seed")["writes"][0]
        self.assertIn(write["flags"], (CREATE_FLAGS_NONE, CREATE_FLAGS_PRIVATE))
        self.assertNotEqual(write["flags"], CREATE_FLAGS_REPLACE_DESTINATION)
        self.assertFalse(write["makeBackup"])

    def test_a_config_already_provisioned_is_never_rewritten(self):
        # dakota ships one through /etc/skel; the extension must not touch it.
        result = run_scenario("config-seed", configExists=True)
        self.assertEqual(result["writes"], [])
        self.assertEqual(result["generate"], [])

    def test_a_failed_generate_writes_nothing_and_is_logged(self):
        result = run_scenario("config-seed", generateFails=True)
        self.assertEqual(result["writes"], [])
        self.assertTrue(
            any("generate" in part for error in result["errors"] for part in error),
            f"expected a logged generate failure, got {result['errors']}",
        )

    def test_a_generate_that_produced_no_config_writes_nothing(self):
        result = run_scenario("config-seed", generateProducesNothing=True)
        self.assertEqual(result["writes"], [])
        self.assertEqual(result["errors"], [])

    def test_turning_the_toggle_on_seeds_before_starting_the_unit(self):
        result = run_scenario(
            "clicked", checked=True, configExists=False, syncDirExists=False
        )
        self.assertEqual(len(result["generate"]), 1)
        self.assertEqual(result["systemctl"][0][2], "start")

    def test_turning_the_toggle_off_never_seeds(self):
        result = run_scenario(
            "clicked", checked=False, configExists=False, syncDirExists=False
        )
        self.assertEqual(result["generate"], [])
        self.assertEqual(result["createdDirs"], [])


@unittest.skipIf(NODE is None, "node is not installed; cannot execute toggle.js")
class TestSyncthingToggleSeedingDestroyGuards(unittest.TestCase):
    """disable() can land inside any of the seeding awaits."""

    def test_a_cancelled_status_callback_never_touches_the_widgets(self):
        result = run_scenario("destroy-during-status", release="cancelled")
        self.assertEqual(result["before"]["subtitle"], "Loading")
        self.assertEqual(
            result["after"],
            result["before"],
            "a callback delivered after disable() must leave every widget alone",
        )

    def test_a_status_callback_that_still_succeeds_after_disable_is_ignored(self):
        # force_exit races the child: systemctl can have written its answer
        # before the kill lands, so the callback arrives *successfully* once
        # the widgets are gone. The catch path does not cover this one.
        result = run_scenario(
            "destroy-during-status", release="ok", statusStdout="active\n"
        )
        self.assertEqual(result["after"], result["before"])
        self.assertEqual(result["after"]["subtitle"], "Loading")
        self.assertFalse(result["after"]["indicatorVisible"])

    def test_disable_mid_seed_abandons_the_rest_of_the_click(self):
        # `syncthing generate` is the longest await on the click path. If
        # disable() lands while it is outstanding, neither the config rewrite
        # nor the systemctl sequence behind it may resume.
        result = run_scenario("destroy-during-seed")
        self.assertEqual(result["generateStarted"], 1)
        self.assertEqual(
            result["writtenPaths"],
            [],
            "config.xml must not be rewritten after the extension was disabled",
        )
        self.assertEqual(result["systemctl"], [])
        self.assertEqual(result["notifications"], [])

    def test_disable_kills_an_in_flight_syncthing_generate(self):
        # A Cancellable aborts our end of the pipe; the child keeps running
        # unless force_exit() is wired to it. The seeding spawn needs that too,
        # not only the systemctl calls.
        result = run_scenario("destroy-during-seed")
        self.assertIn("syncthing", result["forceExits"])

    def test_disable_mid_start_never_spawns_the_status_refresh(self):
        # disable() lands while `systemctl start` is still outstanding. The
        # refresh chained behind it would spawn a process for an extension
        # that no longer exists.
        result = run_scenario("destroy-during-systemctl")
        self.assertEqual(result["started"], ["start"])
        self.assertEqual(
            result["statusSubprocesses"],
            0,
            "no status probe may be spawned after the extension was disabled",
        )
        self.assertEqual(result["verbs"], ["start"])
        self.assertEqual(result["notifications"], [])


class TestSyncthingToggleStaysOffTheCompositorThread(unittest.TestCase):
    """Static guards for calls that freeze the whole desktop, not just a menu."""

    BANNED = {
        r"\bquery_exists\s*\(": "query_exists() blocks; use query_info_async()",
        r"\bload_contents\s*\(": "load_contents() blocks; use load_contents_async()",
        r"\breplace_contents\s*\(": (
            "replace_contents() blocks; use replace_contents_bytes_async()"
        ),
        r"\bmake_directory_with_parents\s*\(": (
            "make_directory_with_parents() blocks; walk parents with "
            "make_directory_async()"
        ),
        r"\bfind_program_in_path\s*\(": (
            "find_program_in_path() stats every PATH entry on the main loop; "
            "let Gio.Subprocess resolve the binary in the child"
        ),
        r"REPLACE_DESTINATION": (
            "REPLACE_DESTINATION drops the 0600 mode on a file holding <apikey>"
        ),
    }

    def test_no_blocking_gio_call_reaches_the_shell_main_loop(self):
        # Whole-line comments are dropped first: the source explains why each
        # of these is banned, and naming one there is not calling it.
        source = "\n".join(
            line
            for line in TOGGLE_JS.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("//")
        )
        for pattern, why in self.BANNED.items():
            with self.subTest(pattern=pattern):
                self.assertIsNone(
                    re.search(pattern, source),
                    f"extensions/syncthing-toggle/toggle.js: {why}",
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

    def test_harness_loads_the_shipped_extension_entry_point(self):
        # The lifecycle scenarios import extension.js, whose relative
        # './toggle.js' import the harness has to rebind by hand. A scenario
        # that runs at all proves that rewrite is still current.
        result = run_scenario("enable")
        self.assertEqual(result["externalIndicators"], 1)


if __name__ == "__main__":
    unittest.main()
