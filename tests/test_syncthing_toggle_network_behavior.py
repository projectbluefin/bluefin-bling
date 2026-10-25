"""Fail-safe regressions for the Sync Folder toggle's network and service-name guards.

Each scenario drives the shipped indicator through the shared harness: a session
with no usable network monitor, a metered probe that raises, repeated metered
edges, and a service name the user changes to a rejected value mid-session.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

NODE = shutil.which("node")
HARNESS = Path(__file__).with_name("syncthing_toggle_harness.mjs")


def run_scenario(name: str, **options) -> dict:
    result = subprocess.run(
        [NODE, str(HARNESS), name, json.dumps(options)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode:
        raise AssertionError(f"{name} failed:\n{result.stderr}")
    return json.loads(result.stdout)


def verbs(snapshot: dict) -> list[str]:
    return [command.get("verb") or command.get("action") for command in snapshot["commands"]]


def titles(snapshot: dict) -> list[str]:
    return [notification["title"] for notification in snapshot["notifications"]]


@unittest.skipIf(NODE is None, "node is not installed")
class NetworkMonitorUnavailableTests(unittest.TestCase):
    def test_toggle_works_end_to_end_without_a_network_monitor(self):
        for mode in ["throws", "absent"]:
            with self.subTest(networkMonitor=mode):
                steps = run_scenario("monitorUnavailable", networkMonitor=mode)
                self.assertEqual(steps["initial"]["subtitle"], "Stopped")
                self.assertEqual(steps["initial"]["networkHandlers"], 0)

                on = steps["on"]
                self.assertTrue(on["checked"])
                self.assertTrue(on["running"])
                self.assertFalse(on["paused"])
                self.assertIn("start", verbs(on))
                self.assertEqual(titles(on), ["Sync Folder Sharing Enabled"])

                # Without a monitor, login reconcile treats the link as unmetered.
                reconciled = steps["reconciled"]
                self.assertTrue(reconciled["running"])
                self.assertFalse(reconciled["paused"])
                self.assertNotIn("stop", verbs(reconciled))

                off = steps["off"]
                self.assertFalse(off["checked"])
                self.assertFalse(off["running"])
                self.assertFalse(off["autostart"])
                self.assertEqual(titles(off)[-1], "Sync Folder Sharing Disabled")

                destroyed = steps["destroyed"]
                self.assertEqual(destroyed["destroyCount"], 1)
                self.assertEqual(destroyed["networkHandlers"], 0)
                self.assertEqual(destroyed["clickHandlers"], 0)


@unittest.skipIf(NODE is None, "node is not installed")
class MeteredProbeFailureTests(unittest.TestCase):
    def setUp(self):
        self.result = run_scenario("meteredProbeThrows")

    def test_click_treats_a_failing_probe_as_unmetered(self):
        on = self.result["on"]
        self.assertTrue(on["checked"])
        self.assertTrue(on["running"])
        self.assertFalse(on["paused"])
        self.assertEqual(titles(on), ["Sync Folder Sharing Enabled"])

    def test_metered_signal_with_a_failing_probe_does_not_pause(self):
        before, after = self.result["on"], self.result["signalled"]
        self.assertTrue(after["running"])
        self.assertFalse(after["paused"])
        self.assertEqual(after["commands"], before["commands"])
        self.assertEqual(after["notifications"], before["notifications"])

    def test_reconcile_with_a_failing_probe_keeps_sharing_running(self):
        reconciled = self.result["reconciled"]
        self.assertTrue(reconciled["running"])
        self.assertFalse(reconciled["paused"])
        self.assertNotIn("stop", verbs(reconciled))


@unittest.skipIf(NODE is None, "node is not installed")
class MeteredEdgeGuardTests(unittest.TestCase):
    def setUp(self):
        self.result = run_scenario("meteredRepeat")

    def test_metered_edge_before_sharing_was_requested_does_nothing(self):
        idle = self.result["idle"]
        self.assertEqual(idle["commands"], [])
        self.assertEqual(idle["notifications"], [])
        self.assertFalse(idle["paused"])

    def test_second_metered_edge_while_paused_is_ignored(self):
        paused, repeated = self.result["paused"], self.result["repeated"]
        self.assertTrue(paused["paused"])
        self.assertEqual(titles(paused), ["Sync Folder Sharing Paused"])
        self.assertEqual(repeated["commands"], paused["commands"])
        self.assertEqual(repeated["notifications"], paused["notifications"])
        self.assertTrue(repeated["paused"])

    def test_unmetered_edge_resumes_once_after_repeated_metered_edges(self):
        resumed = self.result["resumed"]
        self.assertTrue(resumed["running"])
        self.assertFalse(resumed["paused"])
        self.assertEqual(verbs(resumed).count("start"), 1)
        self.assertEqual(titles(resumed), ["Sync Folder Sharing Paused", "Sync Folder Sharing Enabled"])


@unittest.skipIf(NODE is None, "node is not installed")
class ServiceRenamedInvalidTests(unittest.TestCase):
    NAMES = ["syncthing.service; reboot", "--system.service", "../syncthing.service"]

    def test_status_reads_never_spawn_for_a_rejected_name(self):
        for name in self.NAMES:
            with self.subTest(name=name):
                result = run_scenario("serviceRenamedInvalid", invalidName=name)
                baseline = result["on"]["commands"]
                for step in ["checked", "polled", "reconciled"]:
                    self.assertEqual(result[step]["commands"], baseline, step)
                    self.assertFalse(result[step]["checked"], step)
                    self.assertEqual(result[step]["subtitle"], "Invalid service name", step)
                    self.assertFalse(result[step]["webGuiSensitive"], step)

    def test_rejected_name_leaves_the_running_service_alone(self):
        result = run_scenario("serviceRenamedInvalid")
        for step in ["checked", "polled", "reconciled"]:
            self.assertTrue(result[step]["running"], step)
            self.assertNotIn("stop", verbs(result[step]), step)
            self.assertEqual(result[step]["notifications"], result["on"]["notifications"], step)

    def test_restoring_a_valid_name_resumes_status_reads(self):
        restored = run_scenario("serviceRenamedInvalid")["restored"]
        self.assertTrue(restored["checked"])
        self.assertEqual(restored["subtitle"], "Running")
        self.assertEqual(verbs(restored)[-1], "is-active")


if __name__ == "__main__":
    unittest.main()
