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

They also pin the parts of the sync-folder feature that are easy to regress
back into a desktop freeze or a data leak: the status probe must stay off the
journal, the background poll must stay cheap and die with the extension, a
metered pause and a manual toggle-off must leave the unit in the same state, a
cancelled callback must never reach a finalized St widget, and the seeded
config.xml must be XML-safe, private, and free of any blanket folder-accept
default.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
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
# stay empty — never flipping it is the fix, and a fixture carrying the literal
# would hide a regression in plain sight.
AUTO_ACCEPT = "auto" + "Accept" + "Folders"

# A generated config that carries the <defaults> device template syncthing
# really writes, so a rewrite of that block would show up as a diff.
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

    def test_web_gui_item_is_the_menu_action_updatestatus_desensitises(self):
        # updateStatus() greys the Web GUI entry out while the daemon is down.
        # If webGuiItem ever stops being the item actually in the menu, the
        # entry stays clickable and opens a dead URL.
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
    def test_web_gui_url_targets_the_loopback_address_the_config_binds(self):
        # The seeded config.xml binds the GUI to 127.0.0.1. 'localhost' can
        # resolve to ::1 first, where syncthing is not listening.
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
        self.assertTrue(result["webGuiSensitive"])

    def test_stopped_service_hides_the_indicator_and_unchecks_the_toggle(self):
        result = run_scenario("update-status")["inactive"]
        self.assertFalse(result["indicatorVisible"])
        self.assertFalse(result["checked"])
        self.assertEqual(result["subtitle"], "Stopped")
        self.assertFalse(
            result["webGuiSensitive"],
            "the Web GUI entry must not stay clickable while the daemon is down",
        )


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
        for state in ("inactive\n", "failed\n", "activating\n", "unknown\n", ""):
            with self.subTest(state=state):
                result = run_scenario("status-probe", statusStdout=state)
                self.assertFalse(result["widgets"]["indicatorVisible"])
                self.assertEqual(result["widgets"]["subtitle"], "Stopped")

    def test_rejected_service_name_reports_stopped_without_spawning_anything(self):
        result = run_scenario("status-probe", serviceName="syncthing.service; reboot")
        self.assertEqual(result["statusSubprocesses"], 0)
        self.assertEqual(result["widgets"]["subtitle"], "Stopped")


@unittest.skipIf(NODE is None, "node is not installed; cannot execute toggle.js")
class TestSyncthingTogglePolling(unittest.TestCase):
    def test_background_poll_is_not_a_five_second_spin(self):
        # At 5s this feature spawned ~17,280 processes a day to save battery.
        result = run_scenario("poll-lifecycle")
        self.assertEqual(len(result["installed"]), 1)
        self.assertGreaterEqual(
            result["installed"][0]["intervalSeconds"],
            30,
            "the background status poll must not run on a handful of seconds",
        )

    def test_destroy_removes_the_poll_source_and_the_metered_handler(self):
        result = run_scenario("poll-lifecycle")
        self.assertEqual(result["liveBefore"], result["removedSources"])
        self.assertEqual(
            result["liveAfter"],
            [],
            "a surviving timeout keeps calling into finalized widgets",
        )
        self.assertEqual(result["monitorDisconnects"], 1)


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

    def test_turning_off_never_seeds_configuration(self):
        # Seeding is for the enable direction only; a stop must not create
        # directories or spawn syncthing.
        result = run_scenario("clicked", checked=False, configExists=False, syncDirExists=False)
        self.assertEqual(result["generate"], [])
        self.assertEqual(result["createdDirs"], [])

    def test_turning_on_seeds_configuration_before_starting_the_unit(self):
        result = run_scenario("clicked", checked=True, configExists=False, syncDirExists=False)
        self.assertEqual(len(result["generate"]), 1)
        self.assertEqual(
            result["systemctl"][0],
            ["systemctl", "--user", "start", "syncthing.service"],
        )

    def test_an_existing_config_means_syncthing_is_never_respawned(self):
        result = run_scenario("clicked", checked=True)
        self.assertEqual(result["generate"], [])

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
        self.assertEqual(
            result["generate"],
            [],
            "a rejected service-name must not reach the config seeding either",
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
class TestSyncthingToggleMeteredReconcile(unittest.TestCase):
    """A metered pause must be a pause, and must reverse itself."""

    def test_metered_pause_runs_the_same_verbs_as_a_manual_toggle_off(self):
        # A pause that only stops the unit leaves it enabled, so syncing comes
        # straight back at the next login — on the same metered link.
        manual = run_scenario("clicked", checked=False)["systemctl"]
        paused = run_scenario("metered-transition")["paused"]["systemctl"]
        self.assertEqual(paused, manual)

    def test_returning_to_an_unmetered_network_resumes_sharing(self):
        resumed = run_scenario("metered-transition")["resumed"]
        self.assertEqual(
            resumed["systemctl"],
            [
                ["systemctl", "--user", "start", "syncthing.service"],
                ["systemctl", "--user", "enable", "syncthing.service"],
            ],
        )
        self.assertFalse(resumed["pausedForMetered"])

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
            result["paused"]["systemctl"],
            [["systemctl", "--user", "stop", "syncthing.service"]],
        )
        self.assertEqual(
            result["resumed"]["systemctl"],
            [["systemctl", "--user", "start", "syncthing.service"]],
        )

    def test_a_network_change_never_resumes_what_the_user_turned_off(self):
        # Sharing is off by the user's own choice: neither edge may touch it.
        result = run_scenario("metered-transition", checked=False)
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
            result["resumed"]["systemctl"],
            [
                ["systemctl", "--user", "start", "syncthing.service"],
                ["systemctl", "--user", "enable", "syncthing.service"],
            ],
        )

    def test_turning_on_over_a_metered_link_is_honoured_once_unmetered(self):
        result = run_scenario("metered-click-then-unmeter")
        self.assertEqual(
            result["refused"]["systemctl"],
            [],
            "nothing may start while the link is metered",
        )
        self.assertFalse(result["refused"]["checked"])
        self.assertEqual(
            [n["title"] for n in result["refused"]["notifications"]],
            ["Sync Folder Sharing Paused"],
        )
        self.assertEqual(
            result["resumed"]["systemctl"],
            [
                ["systemctl", "--user", "start", "syncthing.service"],
                ["systemctl", "--user", "enable", "syncthing.service"],
            ],
        )

    def test_a_rejected_service_name_stops_the_metered_path_too(self):
        result = run_scenario(
            "metered-transition", serviceName="--system.service"
        )
        self.assertEqual(result["paused"]["systemctl"], [])
        self.assertEqual(result["resumed"]["systemctl"], [])


@unittest.skipIf(NODE is None, "node is not installed; cannot execute toggle.js")
class TestSyncthingToggleConfigSeeding(unittest.TestCase):
    def test_seeding_creates_the_sync_and_state_directories(self):
        result = run_scenario("config-seed")
        home = result["paths"]["homeDir"]
        self.assertIn(f"{home}/Sync", result["createdDirs"])
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
        result = run_scenario("config-seed", port=12345)
        written = result["writes"][0]["text"]
        self.assertIn("<address>127.0.0.1:12345</address>", written)

    def test_a_home_directory_with_xml_metacharacters_stays_parseable(self):
        # $HOME is whatever the account says. An unescaped & or " lands in an
        # attribute and syncthing refuses to start on the resulting config.
        home = '/home/a&b"c<d>e'
        result = run_scenario("config-seed", homeDir=home)
        written = result["writes"][0]["text"]
        self.assertIn('path="/home/a&amp;b&quot;c&lt;d&gt;e/Sync"', written)
        self.assertNotIn(f'path="{home}/Sync"', written)
        # Escaping is only worth anything if the result actually parses.
        import xml.etree.ElementTree as ET

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
        result = run_scenario("config-seed")
        write = result["writes"][0]
        self.assertIn(write["flags"], (CREATE_FLAGS_NONE, CREATE_FLAGS_PRIVATE))
        self.assertNotEqual(write["flags"], CREATE_FLAGS_REPLACE_DESTINATION)
        self.assertFalse(write["makeBackup"])

    def test_an_existing_config_is_never_rewritten(self):
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


@unittest.skipIf(NODE is None, "node is not installed; cannot execute toggle.js")
class TestSyncthingToggleDestroyGuards(unittest.TestCase):
    """disable() finalizes the St widgets before the indicator is destroyed."""

    def test_a_cancelled_status_callback_never_touches_the_widgets(self):
        # Cancelling a Gio.Cancellable does not drop the callback: it fires
        # with G_IO_ERROR_CANCELLED. Without a re-check after the await,
        # updateStatus() writes to already-finalized St widgets.
        result = run_scenario("destroy-during-status", release="cancelled")
        self.assertEqual(result["before"]["subtitle"], "Loading")
        self.assertEqual(
            result["after"],
            result["before"],
            "a callback delivered after destroy() must leave every widget alone",
        )

    def test_a_status_callback_that_still_succeeds_after_destroy_is_ignored(self):
        # The cancel races the child: force_exit can land after systemctl has
        # already written its answer, so the callback arrives *successfully*
        # once disable() has finalized the widgets. The catch-path guard does
        # not cover this; the guard after the await does.
        result = run_scenario("destroy-during-status", release="ok", statusStdout="active\n")
        self.assertEqual(
            result["after"],
            result["before"],
            "a successful callback delivered after destroy() must change nothing",
        )
        self.assertEqual(result["after"]["subtitle"], "Loading")
        self.assertFalse(result["after"]["indicatorVisible"])

    def test_cancelling_kills_the_child_process(self):
        # A cancellable aborts the local stream read only; without force_exit
        # the systemctl child outlives the extension.
        result = run_scenario("destroy-during-status")
        self.assertEqual(result["forceExitsAfterCancel"], ["systemctl"])

    def test_the_cancel_handler_is_disconnected_once_the_call_completes(self):
        result = run_scenario("destroy-during-status")
        self.assertEqual(
            result["leakedCancellableHandlers"],
            0,
            "every force_exit handler must be disconnected in the callback",
        )

    def test_destroy_mid_seed_abandons_the_rest_of_the_click(self):
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
        self.assertEqual(
            result["systemctl"],
            [],
            "no systemctl verb may run after the extension was disabled",
        )

    def test_destroy_kills_an_in_flight_syncthing_generate(self):
        # Cancelling aborts our end of the pipe; the child keeps running unless
        # force_exit() is wired to the cancellable.
        result = run_scenario("destroy-during-seed")
        self.assertIn("syncthing", result["forceExits"])

    def test_destroy_mid_start_never_spawns_the_status_refresh(self):
        # disable() lands while `systemctl start` is still outstanding. The
        # refresh chained behind it would spawn a process for an extension
        # that no longer exists, then write to finalized widgets.
        result = run_scenario("destroy-during-systemctl")
        self.assertEqual(
            result["started"],
            [["systemctl", "--user", "start", "syncthing.service"]],
        )
        self.assertEqual(
            result["statusSubprocesses"],
            0,
            "no status probe may be spawned after the extension was disabled",
        )
        self.assertEqual(result["systemctl"], result["started"])

    def test_destroy_mid_click_abandons_the_remaining_systemctl_verbs(self):
        result = run_scenario("destroy-during-click")
        self.assertEqual(
            result["systemctl"],
            [["systemctl", "--user", "start", "syncthing.service"]],
            "the enable verb must not run after the extension was disabled",
        )
        self.assertEqual(result["widgets"]["subtitle"], "Loading")


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


if __name__ == "__main__":
    unittest.main()
