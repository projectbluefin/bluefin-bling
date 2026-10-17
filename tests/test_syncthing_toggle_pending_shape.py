"""Pending-invitation payloads that parse as JSON but have the wrong shape.

``_readPending()`` in ``extensions/syncthing-toggle/toggle.js`` rejects a
``cluster/pending/devices`` or ``cluster/pending/folders`` response that is not
an object, and a folder entry whose ``offeredBy`` is not an object. The existing
``pending`` scenario only feeds HTTP errors and unparsable bodies, so neither
shape check ran. These tests drive the same scenario through the harness's
``apiErrors`` option, which returns a raw 200 body for a REST path, and assert
what the user sees: the toggle stays on, the subtitle says invitations are
unavailable, nothing is announced, and recovery announces exactly once.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

NODE = shutil.which("node")
HARNESS = Path(__file__).with_name("syncthing_toggle_harness.mjs")
UNAVAILABLE = "Invitations unavailable"


def run_pending(**api_errors: dict) -> list[dict]:
    options = {"apiErrors": {path: error for path, error in api_errors.items()}}
    result = subprocess.run(
        [NODE, str(HARNESS), "pending", json.dumps(options)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode:
        raise AssertionError(f"pending failed:\n{result.stderr}")
    return json.loads(result.stdout)["steps"]


def body(value) -> dict:
    return {"status": 200, "raw": json.dumps(value)}


@unittest.skipIf(NODE is None, "node is not installed")
class PendingInvitationShapeTests(unittest.TestCase):
    def assert_rejected(self, step: dict) -> None:
        self.assertTrue(step["checked"], "a bad invitation payload must not turn sharing off")
        self.assertEqual(step["subtitle"], UNAVAILABLE)
        self.assertTrue(step["webGuiSensitive"])

    def test_non_object_devices_payload_is_rejected_on_every_poll(self):
        for payload in ([], ["NEW-DEVICE"], "NEW-DEVICE", 7, None):
            with self.subTest(payload=payload):
                steps = run_pending(**{"cluster/pending/devices": body(payload)})
                for step in steps:
                    self.assert_rejected(step)
                    self.assertEqual(step["notifications"], [])
                    self.assertEqual(step["pending"], [])
                self.assertFalse(steps[-1]["secretLeaked"])

    def test_non_object_folders_payload_is_rejected_before_recovery(self):
        for payload in ([], [{"offeredBy": {"DEVICE-A": {}}}], "shared", None):
            with self.subTest(payload=payload):
                steps = run_pending(**{"cluster/pending/folders": body(payload)})
                for step in steps[:2]:
                    self.assert_rejected(step)
                    self.assertEqual(step["notifications"], [])
                    self.assertEqual(step["pending"], [])

    def test_folder_entry_without_object_offered_by_is_rejected(self):
        for folder in (None, [], "DEVICE-A", {}, {"offeredBy": None},
                       {"offeredBy": ["DEVICE-A"]}, {"offeredBy": "DEVICE-A"}):
            with self.subTest(folder=folder):
                steps = run_pending(**{"cluster/pending/folders": body({"shared": folder})})
                for step in steps[:2]:
                    self.assert_rejected(step)
                    self.assertEqual(step["notifications"], [],
                                     "a valid device invitation must not be announced "
                                     "alongside a malformed folder entry")
                    self.assertEqual(step["pending"], [])

    def test_recovery_after_bad_shape_announces_once(self):
        steps = run_pending(**{"cluster/pending/folders": body({"shared": {"offeredBy": []}})})
        # Steps 2-3 swap in a 503 and an unparsable body; step 4 clears the
        # override so the real invitations are read for the first time.
        recovered = steps[4]
        self.assertEqual(recovered["subtitle"], "Running")
        self.assertEqual(len(recovered["notifications"]), 1)
        self.assertEqual(recovered["notifications"][0]["title"], "Sync Folder Sharing Invitation")
        self.assertEqual(sorted(json.loads(key) for key in recovered["pending"]),
                         [["device", "NEW-DEVICE"], ["folder", "shared", "DEVICE-A"]])
        self.assertTrue(all(request["method"] == "GET" for request in steps[-1]["apiRequests"]))


if __name__ == "__main__":
    unittest.main()
