"""Unit start/stop jobs may legitimately outlast the generic 30 s command bound.

The first start of the Quadlet unit pulls the Syncthing image inside systemd's
start job (``TimeoutStartSec=900``), and a graceful stop may take up to
``TimeoutStopSec=60``. The toggle must wait for systemd's own job bounds instead
of killing ``systemctl`` and rolling the unit back mid-pull.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from test_syncthing_toggle_behavior import NODE, run_scenario

TEMPLATE = (Path(__file__).resolve().parents[1] / "extensions" / "syncthing-toggle" /
            "syncthing.container.in").read_text(encoding="utf-8")
TOGGLE = (Path(__file__).resolve().parents[1] / "extensions" / "syncthing-toggle" /
          "toggle.js").read_text(encoding="utf-8")


def unit_seconds(key: str) -> int:
    return int(re.search(rf"^{key}=(\d+)$", TEMPLATE, re.MULTILINE).group(1))


@unittest.skipIf(NODE is None, "node is not installed")
class SlowUnitJobTests(unittest.TestCase):
    def test_first_start_survives_an_image_pull_longer_than_thirty_seconds(self):
        result = run_scenario("slowJob", gate="systemctl:start", holdMs=600)
        self.assertEqual(result["killedProcesses"], 0)
        self.assertTrue(result["running"])
        self.assertTrue(result["checked"])
        self.assertTrue(result["provisioned"])
        self.assertFalse(any(command.get("verb") == "stop" for command in result["commands"]))
        self.assertTrue(result["notifications"][-1]["active"])
        self.assertEqual(result["timerCount"], 1)

    def test_graceful_stop_longer_than_thirty_seconds_is_not_reported_as_failure(self):
        result = run_scenario("slowJob", gate="systemctl:stop", holdMs=450, enable=False,
                              running=True, provisioned=True, autostart=True)
        self.assertEqual(result["killedProcesses"], 0)
        self.assertFalse(result["running"])
        self.assertFalse(result["checked"])
        self.assertFalse(result["autostart"])
        self.assertEqual(result["subtitle"], "Stopped")
        self.assertEqual(result["notifications"][-1]["title"], "Sync Folder Sharing Disabled")

    def test_toggle_job_bounds_cover_the_quadlet_unit_timeouts(self):
        start = int(re.search(r"^const unitStartSeconds = (\d+)", TOGGLE, re.MULTILINE).group(1))
        stop = int(re.search(r"^const unitStopSeconds = (\d+)", TOGGLE, re.MULTILINE).group(1))
        self.assertGreater(start, unit_seconds("TimeoutStartSec"))
        self.assertGreater(stop, unit_seconds("TimeoutStopSec"))


if __name__ == "__main__":
    unittest.main()
