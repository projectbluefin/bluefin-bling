"""Behavioral unit tests for extensions/power-status-color/extension.js.

The rest of the suite checks structure — metadata, schemas, ESM-ness, teardown
shape. Nothing executed the power-status-color decision logic, so the parts that
actually decide whether the Quick Settings power button turns red or yellow were
unverified: the /proc/uptime parse and 30-day threshold, the reboot-flag and
`bootc status --format=json` staged-deployment checks, the red-over-yellow
priority, the mirrored style classes on the button child, the fallback power
button search, and the enable/disable lifecycle.

These tests drive the real shipped source through
``tests/power_status_color_harness.mjs``, which rewrites only the gi:// and
resource:/// import lines so node can load the module and stub GNOME Shell.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "power_status_color_harness.mjs"
EXTENSION_JS = REPO_ROOT / "extensions" / "power-status-color" / "extension.js"

NODE = shutil.which("node")

CLASS_OVERDUE = "power-status-overdue"
CLASS_REBOOT = "power-status-reboot"

DAY_SECONDS = 24 * 60 * 60
THRESHOLD_SECONDS = 30 * DAY_SECONDS

REBOOT_FLAG = "/run/reboot-required"
LEGACY_REBOOT_FLAG = "/var/run/reboot-required"

BOOTC_STAGED = json.dumps({"status": {"staged": {"image": {"image": "ghcr.io/x:y"}}}})
BOOTC_CLEAN = json.dumps({"status": {"staged": None, "booted": {"image": {}}}})


def uptime_file(seconds: float) -> str:
    """Return /proc/uptime content: uptime seconds then idle seconds."""
    return f"{seconds} {seconds * 2}\n"


@unittest.skipIf(NODE is None, "node is not installed")
class PowerStatusColorTestCase(unittest.TestCase):
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


class TestHarnessFidelity(PowerStatusColorTestCase):
    """The harness is only meaningful if it still loads the shipped source."""

    def test_extension_still_imports_only_stubbable_modules(self):
        source = EXTENSION_JS.read_text(encoding="utf-8")
        imports = [
            line
            for line in source.splitlines()
            if line.lstrip().startswith("import ")
        ]
        self.assertTrue(imports, "extension.js has no imports; harness rewrite is stale")
        for line in imports:
            with self.subTest(line=line.strip()):
                self.assertRegex(
                    line,
                    r"from\s+['\"](gi://|resource:///)",
                    "harness only rewrites gi:// and resource:/// imports",
                )

    def test_constants_under_test_match_the_source(self):
        source = EXTENSION_JS.read_text(encoding="utf-8")
        self.assertIn("const UPTIME_THRESHOLD_SECONDS = 30 * 24 * 60 * 60;", source)
        self.assertIn("const CHECK_INTERVAL_SECONDS = 300;", source)
        self.assertIn(f"'{CLASS_OVERDUE}'", source)
        self.assertIn(f"'{CLASS_REBOOT}'", source)


class TestUptimeOverdue(PowerStatusColorTestCase):
    def test_uptime_past_threshold_is_overdue(self):
        result = self.run_scenario("uptimeOverdue", uptimeContent=uptime_file(31 * DAY_SECONDS))
        self.assertTrue(result["overdue"])

    def test_uptime_exactly_at_threshold_is_overdue(self):
        result = self.run_scenario("uptimeOverdue", uptimeContent=uptime_file(THRESHOLD_SECONDS))
        self.assertTrue(result["overdue"], "the comparison is >=, so the boundary counts as overdue")

    def test_uptime_one_second_below_threshold_is_not_overdue(self):
        result = self.run_scenario("uptimeOverdue", uptimeContent=uptime_file(THRESHOLD_SECONDS - 1))
        self.assertFalse(result["overdue"])

    def test_fresh_boot_is_not_overdue(self):
        result = self.run_scenario("uptimeOverdue", uptimeContent=uptime_file(12.5))
        self.assertFalse(result["overdue"])

    def test_unreadable_uptime_is_not_overdue(self):
        result = self.run_scenario("uptimeOverdue")
        self.assertFalse(result["overdue"], "load_contents_finish returning ok=false must not colour the button")

    def test_uptime_read_error_is_not_overdue(self):
        result = self.run_scenario("uptimeOverdue", uptimeThrows=True)
        self.assertFalse(result["overdue"])

    def test_unparseable_uptime_is_not_overdue(self):
        for content in ("garbage\n", "\n", "   \n", "NaN 0\n"):
            with self.subTest(content=content):
                result = self.run_scenario("uptimeOverdue", uptimeContent=content)
                self.assertFalse(result["overdue"])

    def test_leading_whitespace_is_tolerated(self):
        result = self.run_scenario("uptimeOverdue", uptimeContent=f"   {31 * DAY_SECONDS} 100\n")
        self.assertTrue(result["overdue"])


class TestRebootPending(PowerStatusColorTestCase):
    def test_run_reboot_required_flag_is_pending(self):
        result = self.run_scenario("rebootPending", existingFlagFiles=[REBOOT_FLAG])
        self.assertTrue(result["pending"])
        self.assertIsNone(
            result["subprocessArgv"],
            "a present flag file short-circuits before spawning bootc",
        )

    def test_legacy_var_run_flag_is_pending(self):
        result = self.run_scenario("rebootPending", existingFlagFiles=[LEGACY_REBOOT_FLAG])
        self.assertTrue(result["pending"])

    def test_bootc_staged_deployment_is_pending(self):
        result = self.run_scenario("rebootPending", bootcStdout=BOOTC_STAGED)
        self.assertTrue(result["pending"])
        self.assertEqual(result["subprocessArgv"], ["bootc", "status", "--format=json"])

    def test_bootc_without_staged_deployment_is_not_pending(self):
        result = self.run_scenario("rebootPending", bootcStdout=BOOTC_CLEAN)
        self.assertFalse(result["pending"])

    def test_bootc_missing_status_object_is_not_pending(self):
        for payload in ("{}", json.dumps({"status": {}}), json.dumps({"other": 1})):
            with self.subTest(payload=payload):
                result = self.run_scenario("rebootPending", bootcStdout=payload)
                self.assertFalse(result["pending"])

    def test_bootc_invalid_json_is_not_pending(self):
        result = self.run_scenario("rebootPending", bootcStdout="not json at all")
        self.assertFalse(result["pending"], "a JSON parse error must be swallowed, not thrown")

    def test_bootc_empty_stdout_is_not_pending(self):
        result = self.run_scenario("rebootPending", bootcStdout="")
        self.assertFalse(result["pending"])

    def test_bootc_spawn_failure_is_not_pending(self):
        result = self.run_scenario("rebootPending", subprocessThrows=True)
        self.assertFalse(result["pending"])

    def test_bootc_communicate_failure_is_not_pending(self):
        result = self.run_scenario("rebootPending", bootcThrows=True)
        self.assertFalse(result["pending"])

    def test_flag_file_query_error_falls_through_to_bootc(self):
        result = self.run_scenario("rebootPending", flagFileThrows=True, bootcStdout=BOOTC_STAGED)
        self.assertTrue(result["pending"])

    def test_clean_system_is_not_pending(self):
        result = self.run_scenario("rebootPending", bootcStdout=BOOTC_CLEAN, existingFlagFiles=[])
        self.assertFalse(result["pending"])


class TestStatusStyling(PowerStatusColorTestCase):
    def test_overdue_uptime_applies_the_red_class(self):
        result = self.run_scenario(
            "checkStatus",
            uptimeContent=uptime_file(31 * DAY_SECONDS),
            bootcStdout=BOOTC_CLEAN,
        )
        self.assertEqual(result["classes"], [CLASS_OVERDUE])

    def test_reboot_pending_applies_the_yellow_class(self):
        result = self.run_scenario(
            "checkStatus",
            uptimeContent=uptime_file(2 * DAY_SECONDS),
            existingFlagFiles=[REBOOT_FLAG],
        )
        self.assertEqual(result["classes"], [CLASS_REBOOT])

    def test_overdue_uptime_wins_over_reboot_pending(self):
        result = self.run_scenario(
            "checkStatus",
            uptimeContent=uptime_file(45 * DAY_SECONDS),
            existingFlagFiles=[REBOOT_FLAG],
        )
        self.assertEqual(
            result["classes"],
            [CLASS_OVERDUE],
            "the two classes are mutually exclusive and red outranks yellow",
        )

    def test_healthy_system_carries_no_class(self):
        result = self.run_scenario(
            "checkStatus",
            uptimeContent=uptime_file(3 * DAY_SECONDS),
            bootcStdout=BOOTC_CLEAN,
        )
        self.assertEqual(result["classes"], [])

    def test_recovered_system_drops_a_previously_applied_class(self):
        result = self.run_scenario(
            "checkStatus",
            uptimeContent=uptime_file(1 * DAY_SECONDS),
            bootcStdout=BOOTC_CLEAN,
            initialClasses=[CLASS_OVERDUE, CLASS_REBOOT],
        )
        self.assertEqual(result["classes"], [])

    def test_downgrade_from_overdue_to_reboot_removes_the_red_class(self):
        result = self.run_scenario(
            "checkStatus",
            uptimeContent=uptime_file(1 * DAY_SECONDS),
            existingFlagFiles=[REBOOT_FLAG],
            initialClasses=[CLASS_OVERDUE],
        )
        self.assertEqual(result["classes"], [CLASS_REBOOT])

    def test_style_is_mirrored_onto_the_button_child(self):
        result = self.run_scenario(
            "checkStatus",
            uptimeContent=uptime_file(40 * DAY_SECONDS),
            withChild=True,
        )
        self.assertEqual(result["classes"], [CLASS_OVERDUE])
        self.assertEqual(result["childClasses"], [CLASS_OVERDUE])

    def test_child_classes_are_cleared_with_the_button(self):
        result = self.run_scenario(
            "checkStatus",
            uptimeContent=uptime_file(1 * DAY_SECONDS),
            bootcStdout=BOOTC_CLEAN,
            withChild=True,
            initialClasses=[CLASS_REBOOT],
        )
        self.assertEqual(result["classes"], [])
        self.assertEqual(result["childClasses"], [])

    def test_reapplying_the_same_class_is_idempotent(self):
        result = self.run_scenario(
            "checkStatus",
            uptimeContent=uptime_file(40 * DAY_SECONDS),
            initialClasses=[CLASS_OVERDUE],
        )
        self.assertEqual(result["classes"], [CLASS_OVERDUE])

    def test_missing_quick_settings_does_not_raise(self):
        result = self.run_scenario(
            "checkStatus",
            uptimeContent=uptime_file(40 * DAY_SECONDS),
            quickSettings=None,
        )
        self.assertEqual(result["classes"], [], "no power button means nothing to style, not a crash")

    def test_disabled_extension_never_styles_the_button(self):
        result = self.run_scenario(
            "checkStatusWhileDisabled",
            uptimeContent=uptime_file(90 * DAY_SECONDS),
            existingFlagFiles=[REBOOT_FLAG],
        )
        self.assertEqual(
            result["classes"],
            [],
            "_checkStatus must bail out when the extension is disabled",
        )

    def test_disabled_extension_does_no_io_at_all(self):
        result = self.run_scenario(
            "checkStatusWhileDisabled",
            uptimeContent=uptime_file(1 * DAY_SECONDS),
            bootcStdout=BOOTC_CLEAN,
        )
        self.assertIsNone(
            result["subprocessArgv"],
            "the pre-await guard must return before probing uptime or spawning bootc",
        )

    def test_disabling_mid_check_discards_the_result(self):
        result = self.run_scenario(
            "disabledMidCheck",
            uptimeContent=uptime_file(90 * DAY_SECONDS),
            existingFlagFiles=[REBOOT_FLAG],
        )
        self.assertEqual(
            result["classes"],
            [],
            "the post-await guard must drop a result that arrives after disable()",
        )


class TestFindPowerButton(PowerStatusColorTestCase):
    def test_direct_system_item_path_is_used(self):
        result = self.run_scenario("findPowerButton")
        self.assertTrue(result["found"])

    def test_missing_quick_settings_returns_null(self):
        result = self.run_scenario("findPowerButton", quickSettings=None)
        self.assertFalse(result["found"])

    def test_fallback_finds_the_shutdown_icon_button(self):
        tree = {
            "children": [
                {"children": [
                    {"styleClasses": ["icon-button"], "iconName": "audio-volume-high-symbolic"},
                    {"styleClasses": ["icon-button"], "iconName": "system-shutdown-symbolic"},
                ]},
            ],
        }
        result = self.run_scenario("findPowerButton", fallbackTree=tree)
        self.assertTrue(result["found"])
        self.assertEqual(result["marker"], "system-shutdown-symbolic")

    def test_fallback_matches_the_accessible_name_case_insensitively(self):
        tree = {
            "children": [
                {"styleClasses": ["icon-button"], "accessibleName": "Power Off…"},
            ],
        }
        result = self.run_scenario("findPowerButton", fallbackTree=tree)
        self.assertTrue(result["found"])
        self.assertEqual(result["marker"], "Power Off…")

    def test_fallback_ignores_matching_actors_without_the_icon_button_class(self):
        tree = {
            "children": [
                {"iconName": "system-shutdown-symbolic"},
                {"accessibleName": "power off"},
            ],
        }
        result = self.run_scenario("findPowerButton", fallbackTree=tree)
        self.assertFalse(
            result["found"],
            "the fallback requires the icon-button style class as well as the marker",
        )

    def test_fallback_returns_null_when_nothing_matches(self):
        tree = {"children": [{"styleClasses": ["icon-button"], "iconName": "network-wireless-symbolic"}]}
        result = self.run_scenario("findPowerButton", fallbackTree=tree)
        self.assertFalse(result["found"])


class TestLifecycle(PowerStatusColorTestCase):
    def test_enable_schedules_the_five_minute_poll_and_runs_an_initial_check(self):
        result = self.run_scenario(
            "lifecycle",
            uptimeContent=uptime_file(60 * DAY_SECONDS),
        )
        self.assertEqual(result["afterEnable"]["intervals"], [300])
        self.assertTrue(result["afterEnable"]["monitorConnected"])
        self.assertEqual(result["afterEnable"]["classes"], [CLASS_OVERDUE])

    def test_disable_releases_every_resource_enable_created(self):
        result = self.run_scenario("lifecycle", uptimeContent=uptime_file(60 * DAY_SECONDS))
        after = result["afterDisable"]
        self.assertFalse(after["enabled"])
        self.assertIsNone(after["timeoutId"])
        self.assertIsNone(after["fileMonitor"])
        self.assertIsNone(after["cancellable"])
        self.assertEqual(after["timeoutsRemoved"], [42])
        self.assertTrue(after["monitorDisconnected"])
        self.assertTrue(after["monitorCancelled"])
        self.assertTrue(after["cancellableCancelled"])
        self.assertFalse(after["monitorStillConnected"])

    def test_disable_clears_the_style_classes(self):
        result = self.run_scenario("lifecycle", uptimeContent=uptime_file(60 * DAY_SECONDS))
        self.assertEqual(result["afterEnable"]["classes"], [CLASS_OVERDUE])
        self.assertEqual(result["afterDisable"]["classes"], [])

    def test_a_post_disable_monitor_event_cannot_fire(self):
        result = self.run_scenario("lifecycle", uptimeContent=uptime_file(60 * DAY_SECONDS))
        self.assertFalse(result["fireAfterDisableThrows"])
        self.assertFalse(result["afterDisable"]["monitorStillConnected"])

    def test_enable_survives_a_failing_run_directory_monitor(self):
        result = self.run_scenario(
            "lifecycle",
            monitorThrows=True,
            uptimeContent=uptime_file(60 * DAY_SECONDS),
        )
        self.assertFalse(result["afterEnable"]["monitorConnected"])
        self.assertEqual(
            result["afterEnable"]["classes"],
            [CLASS_OVERDUE],
            "a monitor failure must not stop the poll or the initial check",
        )
        self.assertEqual(result["afterDisable"]["timeoutsRemoved"], [42])

    def test_reboot_required_file_event_triggers_a_recheck(self):
        result = self.run_scenario(
            "monitorTrigger",
            changedBasename="reboot-required",
            uptimeContent=uptime_file(1 * DAY_SECONDS),
            existingFlagFiles=[REBOOT_FLAG],
        )
        self.assertEqual(result["classes"], [CLASS_REBOOT])

    def test_unrelated_file_event_does_not_trigger_a_recheck(self):
        result = self.run_scenario(
            "monitorTrigger",
            changedBasename="systemd",
            uptimeContent=uptime_file(1 * DAY_SECONDS),
            existingFlagFiles=[REBOOT_FLAG],
        )
        self.assertEqual(result["classes"], [], "only reboot-required is interesting")

    def test_poll_callback_rechecks_and_keeps_the_source_alive(self):
        result = self.run_scenario(
            "timerCallback",
            uptimeContent=uptime_file(31 * DAY_SECONDS),
        )
        self.assertTrue(result["returned"], "the timeout must return GLib.SOURCE_CONTINUE")
        self.assertEqual(result["classes"], [CLASS_OVERDUE])


if __name__ == "__main__":
    unittest.main()
