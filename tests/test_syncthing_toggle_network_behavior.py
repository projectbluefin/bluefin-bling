"""Executed coverage for toggle.js's network-monitor and service-name fail-safes.

``test_syncthing_toggle_behavior.py`` drives the metered pause/resume logic with
a working ``Gio.NetworkMonitor`` and a valid ``service-name``. The branches that
keep the toggle usable when either of those is missing never ran:

- ``Gio.NetworkMonitor.get_default()`` raising, or returning null, in the
  ``ServiceIndicator`` constructor;
- ``get_network_metered()`` raising inside ``_isNetworkMetered()``;
- a metered edge reaching ``_onNetworkMeteredChanged()`` while ``service-name``
  fails ``_validatedServiceName()``;
- ``checkStatus()`` with an invalid ``service-name`` (``enable()`` and the poll);
- ``disable()`` landing inside a metered pause after ``stop`` succeeded.

Every test runs the shipped toggle.js and extension.js through
``syncthing_toggle_harness.mjs``.
"""

from __future__ import annotations

import unittest

from test_syncthing_toggle_behavior import NODE, run_scenario

RUNNING = {"checked": True, "subtitle": "Running", "indicatorVisible": True}
STOPPED = {"checked": False, "subtitle": "Stopped", "indicatorVisible": False}


@unittest.skipIf(NODE is None, "node is not installed; cannot execute toggle.js")
class TestNetworkMonitorUnavailable(unittest.TestCase):
    """No GNetworkMonitor: the toggle still works and treats the link as unmetered."""

    def _run(self, failure: str) -> dict:
        return run_scenario("network-monitor-unavailable", networkMonitor=failure)

    def test_enable_reads_the_unit_without_pausing_it(self):
        for failure in ("throws", "absent"):
            with self.subTest(networkMonitor=failure):
                at_enable = self._run(failure)["atEnable"]
                self.assertEqual(at_enable["statusSubprocesses"], 1)
                self.assertEqual(
                    at_enable["verbs"],
                    [],
                    "with no monitor the reconcile must not treat the link as metered",
                )
                self.assertEqual(at_enable["toggle"], RUNNING)
                self.assertEqual(at_enable["notifications"], [])

    def test_no_metered_handler_is_connected(self):
        for failure in ("throws", "absent"):
            with self.subTest(networkMonitor=failure):
                self.assertEqual(self._run(failure)["atEnable"]["meteredHandlers"], 0)

    def test_clicks_still_run_the_full_verb_sequence(self):
        for failure in ("throws", "absent"):
            with self.subTest(networkMonitor=failure):
                result = self._run(failure)
                self.assertEqual(result["turnedOff"], ["stop", "disable"])
                self.assertEqual(
                    result["turnedOn"],
                    ["start", "enable"],
                    "turning sharing on was refused without a network monitor",
                )
                self.assertEqual(result["toggleAfterOn"], RUNNING)

    def test_a_raising_monitor_is_reported_once_and_not_thrown(self):
        result = self._run("throws")
        self.assertEqual(
            result["consoleErrors"],
            [
                "[SyncthingToggle] Error connecting network monitor: "
                "no GNetworkMonitor implementation"
            ],
        )
        self.assertEqual(result["errors"], [])

    def test_a_null_monitor_is_not_an_error(self):
        self.assertEqual(self._run("absent")["consoleErrors"], [])

    def test_disable_tears_down_without_a_monitor_to_disconnect(self):
        for failure in ("throws", "absent"):
            with self.subTest(networkMonitor=failure):
                result = self._run(failure)
                self.assertIsNone(result["disableError"])
                self.assertEqual(result["sourcesLeft"], 0)

    def test_the_working_monitor_baseline_connects_one_handler(self):
        # Guards the two tests above: without this, a harness that never
        # connected anything would make "no handler" pass vacuously.
        self.assertEqual(
            run_scenario("network-monitor-unavailable")["atEnable"]["meteredHandlers"], 1
        )


@unittest.skipIf(NODE is None, "node is not installed; cannot execute toggle.js")
class TestMeteredProbeThrows(unittest.TestCase):
    """get_network_metered() raising is read as "unmetered" everywhere."""

    @classmethod
    def setUpClass(cls):
        cls.result = run_scenario("metered-probe-throws")

    def test_enable_reconcile_leaves_the_running_unit_alone(self):
        self.assertEqual(self.result["atEnable"]["verbs"], [])
        self.assertEqual(self.result["atEnable"]["toggle"], RUNNING)

    def test_the_metered_signal_does_not_pause(self):
        self.assertEqual(self.result["onSignal"]["verbs"], [])
        self.assertFalse(self.result["onSignal"]["pausedForMetered"])

    def test_the_click_guard_does_not_refuse_the_start(self):
        click_on = self.result["clickOn"]
        self.assertEqual(click_on["verbs"], ["start", "enable"])
        self.assertEqual(
            [n["title"] for n in click_on["notifications"]],
            ["Sync Folder Sharing Enabled"],
        )
        self.assertEqual(click_on["toggle"], RUNNING)
        self.assertEqual(self.result["errors"], [])


@unittest.skipIf(NODE is None, "node is not installed; cannot execute toggle.js")
class TestMeteredEdgeWithInvalidServiceName(unittest.TestCase):
    """A rejected service-name stops the metered handler before systemctl runs."""

    @classmethod
    def setUpClass(cls):
        cls.result = run_scenario("metered-edge-invalid-service")

    def test_going_metered_with_an_invalid_name_spawns_nothing(self):
        edge = self.result["meteredWhileInvalid"]
        self.assertEqual(edge["verbs"], [])
        self.assertEqual(edge["notifications"], [])
        self.assertEqual(edge["toggle"], RUNNING)

    def test_going_metered_with_an_invalid_name_records_no_pause(self):
        # A pause that never happened must not be recorded, or the next
        # unmetered edge would "resume" a unit the network never stopped.
        self.assertFalse(self.result["meteredWhileInvalid"]["pausedForMetered"])

    def test_the_valid_name_baseline_really_pauses(self):
        edge = self.result["pausedWithValid"]
        self.assertEqual(edge["verbs"], ["stop", "disable"])
        self.assertTrue(edge["pausedForMetered"])

    def test_returning_unmetered_with_an_invalid_name_spawns_nothing(self):
        edge = self.result["unmeteredWhileInvalid"]
        self.assertEqual(edge["verbs"], [])
        self.assertEqual(edge["notifications"], [])
        self.assertEqual(edge["toggle"], STOPPED)

    def test_the_pause_survives_an_invalid_name_and_resumes_once_fixed(self):
        self.assertTrue(
            self.result["unmeteredWhileInvalid"]["pausedForMetered"],
            "the pause flag was cleared by an edge that could not resume",
        )
        resumed = self.result["resumedAfterFix"]
        self.assertEqual(resumed["verbs"], ["start", "enable"])
        self.assertEqual(resumed["notifications"], ["Sync Folder Sharing Resumed"])
        self.assertFalse(resumed["pausedForMetered"])
        self.assertEqual(resumed["toggle"], RUNNING)

    def test_each_rejected_edge_is_reported(self):
        self.assertEqual(self.result["rejections"], 2)
        self.assertEqual(self.result["errors"], [])


@unittest.skipIf(NODE is None, "node is not installed; cannot execute toggle.js")
class TestStatusWithInvalidServiceName(unittest.TestCase):
    """checkStatus() never hands a rejected name to systemctl is-active."""

    @classmethod
    def setUpClass(cls):
        cls.result = run_scenario("enable-invalid-service")

    def test_enable_spawns_nothing_and_shows_stopped(self):
        at_enable = self.result["atEnable"]
        self.assertEqual(at_enable["subprocesses"], 0)
        self.assertEqual(at_enable["widgets"], {**STOPPED, "webGuiSensitive": False})

    def test_a_poll_tick_spawns_nothing_and_stays_stopped(self):
        after_poll = self.result["afterPoll"]
        self.assertEqual(after_poll["subprocesses"], 0)
        self.assertEqual(after_poll["widgets"], {**STOPPED, "webGuiSensitive": False})

    def test_the_rejection_is_reported_without_a_logged_error(self):
        self.assertTrue(self.result["consoleErrors"])
        for line in self.result["consoleErrors"]:
            self.assertEqual(
                line,
                "[SyncthingToggle] Rejecting invalid service-name: syncthing.service; reboot",
            )
        self.assertEqual(self.result["errors"], [])

    def test_option_shaped_names_are_rejected_too(self):
        result = run_scenario("enable-invalid-service", serviceName="--system.service")
        self.assertEqual(result["atEnable"]["subprocesses"], 0)
        self.assertEqual(result["afterPoll"]["subprocesses"], 0)


@unittest.skipIf(NODE is None, "node is not installed; cannot execute toggle.js")
class TestDisableMidMeteredPause(unittest.TestCase):
    def test_a_pause_interrupted_by_disable_is_neither_announced_nor_persisted(self):
        result = run_scenario("disable-mid-metered-pause")
        self.assertEqual(
            result["stoppedBeforeDisable"],
            ["stop"],
            "the scenario disabled before the metered stop ran",
        )
        self.assertTrue(result["destroyed"])
        self.assertEqual(result["notifications"], [])
        self.assertEqual(
            result["verbs"],
            ["stop"],
            "teardown did not stop the metered pause from persisting with `disable`",
        )


if __name__ == "__main__":
    unittest.main()
