"""Exercise per-device sharing through the shipped Quick Settings controller."""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

NODE = shutil.which("node")
HARNESS = Path(__file__).with_name("syncthing_toggle_harness.mjs")


def fixtures():
    return {
        "existingFolders": [
            {"id": "documents", "label": "My documents", "path": "/home/test/Documents",
             "paused": False, "devices": [{"deviceID": "LOCAL-DEVICE-ID"},
                                             {"deviceID": "C", "introducedBy": "someone"}],
             "rescanIntervalS": 123, "versioning": {"type": "simple"}},
            {"id": "pictures", "paused": True, "devices": [{"deviceID": "B"}]},
            {"id": "custom", "paused": False, "devices": [{"deviceID": "B"}]},
        ],
        "devices": [
            {"deviceID": "LOCAL-DEVICE-ID", "name": "This computer"},
            {"deviceID": "B", "name": "Laptop", "autoAcceptFolders": False,
             "addresses": ["dynamic"], "ignoredFolders": [{"id": "other", "label": "Keep"}]},
            {"deviceID": "C", "name": "Desktop", "autoAcceptFolders": False},
        ],
        "connections": {"B": {"connected": True}, "C": {"connected": False}},
    }


def run(scenario, options):
    result = subprocess.run([NODE, str(HARNESS), scenario, json.dumps(options)],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def writes(state):
    return [request for request in state["apiRequests"] if request["method"] != "GET"]


@unittest.skipUnless(NODE, "node is not installed")
class SyncthingSharingTests(unittest.TestCase):
    def test_menu_lists_only_peers_and_enabled_presets_without_writes(self):
        result = run("sharingView", fixtures())
        self.assertEqual([item["text"] for item in result["sharingMenu"]],
                         ["Laptop — Connected", "Desktop — Not connected"])
        self.assertEqual(result["sharingMenu"][0]["actions"], ["Documents — Not shared — Share"])
        self.assertEqual(result["sharingMenu"][1]["actions"], ["Documents — Shared with this device — Stop sharing"])
        self.assertEqual(writes(result), [])
        self.assertTrue(result["authenticated"])
        self.assertFalse(result["secretLeaked"])

    def test_reopening_menu_cancels_stale_poll_and_reads_current_configuration(self):
        result = run("sharingReopen", fixtures())
        self.assertEqual(result["sharingMenu"][0]["actions"], ["Documents — Sharing — Stop sharing"])
        self.assertEqual(writes(result), [])

    def test_opening_menu_during_poll_does_not_report_invitations_unavailable(self):
        result = run("sharingPollDuringOpen", fixtures())
        self.assertNotEqual(result["subtitle"], "Invitations unavailable")
        self.assertEqual(result["sharingMenu"][0]["text"], "Laptop — Connected")

    def test_current_poll_failure_reports_unavailable_and_recovers(self):
        result = run("sharingPollFailureRecovery", fixtures())
        self.assertEqual(result["failed"]["subtitle"], "Invitations unavailable")
        self.assertNotEqual(result["recovered"]["subtitle"], "Invitations unavailable")
        self.assertEqual(result["recovered"]["sharingMenu"][0]["text"], "Laptop — Connected")
        self.assertEqual(writes(result["recovered"]), [])

    def test_background_poll_does_not_rebuild_open_menu(self):
        result = run("sharingPollWithMenu", fixtures())
        self.assertTrue(result["keptWhileOpen"])
        self.assertTrue(result["renderedWhileClosed"])
        self.assertEqual(result["reopened"]["sharingMenu"][0]["actions"],
                         ["Documents — Sharing — Stop sharing"])
        self.assertEqual(writes(result), [])

    def test_empty_peer_list_points_to_pairing(self):
        options = fixtures()
        options["devices"] = options["devices"][:1]
        result = run("sharingView", options)
        self.assertIn("No paired devices", result["sharingMenu"][0]["text"])
        self.assertIn("Sharing Settings", result["sharingMenu"][0]["text"])

    def test_share_preserves_other_devices_and_all_other_settings(self):
        options = fixtures()
        result = run("sharingAction", options)
        folder = result["after"]["configured"][0]
        self.assertEqual(folder["devices"], options["existingFolders"][0]["devices"] + [{"deviceID": "B"}])
        original = options["existingFolders"][0].copy()
        original["devices"] = folder["devices"]
        self.assertEqual(folder, original)
        self.assertEqual(result["after"]["configured"][1:], options["existingFolders"][1:])
        self.assertTrue(all(set(request["body"]) in ({"devices"}, {"ignoredFolders"})
                            for request in writes(result["after"])))
        self.assertFalse(result["after"]["devices"][1]["autoAcceptFolders"])
        self.assertEqual(result["after"]["sharingMenu"][0]["actions"], ["Documents — Sharing — Stop sharing"])

    def test_accept_joins_existing_folder_and_notifies_about_menu(self):
        options = fixtures()
        options["pendingFolders"] = {"documents": {"offeredBy": {"B": {}}}}
        result = run("sharingAction", options)
        self.assertIn("Accept", result["before"]["sharingMenu"][0]["actions"][0])
        self.assertIn("Offered by the other device", result["before"]["sharingMenu"][0]["actions"][0])
        self.assertIn("Sync Folder menu", result["before"]["notifications"][0]["body"])
        self.assertEqual(len(result["after"]["configured"]), 3)
        self.assertIn({"deviceID": "B"}, result["after"]["configured"][0]["devices"])

    def test_stop_preserves_files_pairing_other_shares_and_suppresses_standing_offer(self):
        options = fixtures()
        options["existingFolders"][0]["devices"].append({"deviceID": "B"})
        options["pendingFolders"] = {"documents": {"offeredBy": {"B": {}}}}
        result = run("sharingAction", options)
        after = result["after"]
        self.assertEqual(after["configured"][0]["devices"], options["existingFolders"][0]["devices"][:-1])
        self.assertEqual(len(after["devices"]), 3)
        self.assertEqual(after["sharingMenu"][0]["actions"], ["Documents — Not shared — Share"])
        self.assertEqual(after["notifications"], [])
        self.assertEqual(after["pending"], [])
        self.assertEqual({item["id"] for item in after["devices"][1]["ignoredFolders"]}, {"other", "documents"})
        self.assertEqual([request["path"] for request in writes(after)],
                         ["config/folders/documents", "config/devices/B"])

    def test_share_again_clears_only_matching_durable_ignore(self):
        options = fixtures()
        options["devices"][1]["ignoredFolders"].append({"id": "documents"})
        options["pendingFolders"] = {"documents": {"offeredBy": {"B": {}}}}
        result = run("sharingAction", options)
        self.assertIn("Not shared — Share", result["before"]["sharingMenu"][0]["actions"][0])
        self.assertEqual(result["after"]["devices"][1]["ignoredFolders"], [{"id": "other", "label": "Keep"}])

    def test_connected_states_and_offline_local_only_state(self):
        for remote, connected, expected in [("valid", True, "Sharing"),
                                             ("unknown", True, "Waiting for the other device"),
                                             ("notSharing", True, "Waiting for the other device"),
                                             ("valid", False, "Shared with this device")]:
            with self.subTest(remote=remote, connected=connected):
                options = fixtures()
                options["existingFolders"][0]["devices"].append({"deviceID": "B"})
                options["remoteState"] = remote
                options["connections"]["B"]["connected"] = connected
                result = run("sharingView", options)
                self.assertEqual(result["sharingMenu"][0]["actions"], [f"Documents — {expected} — Stop sharing"])

    def test_offline_share_is_still_available(self):
        options = fixtures()
        options["connections"]["B"]["connected"] = False
        result = run("sharingAction", options)
        self.assertEqual(result["after"]["sharingMenu"][0]["actions"], ["Documents — Shared with this device — Stop sharing"])

    def test_offline_stop_removes_local_share_and_persists_offer_suppression(self):
        options = fixtures()
        options["connections"]["B"]["connected"] = False
        options["existingFolders"][0]["devices"].append({"deviceID": "B"})
        result = run("sharingAction", options)
        self.assertEqual(result["after"]["sharingMenu"][0]["actions"], ["Documents — Not shared — Share"])
        self.assertIn("documents", [item["id"] for item in result["after"]["devices"][1]["ignoredFolders"]])

    def test_unmatched_offers_only_point_to_settings_and_never_write(self):
        options = fixtures()
        options["pendingDevices"] = {"STRANGER": {}}
        options["pendingFolders"] = {folder: {"offeredBy": {peer: {}}}
                                     for folder, peer in [("pictures", "B"), ("custom", "B"),
                                                          ("documents", "STRANGER"), ("unknown", "B")]}
        result = run("sharingView", options)
        self.assertNotIn("Accept", json.dumps(result["sharingMenu"]))
        self.assertEqual(len(result["notifications"]), 1)
        self.assertIn("Sharing Settings", result["notifications"][0]["body"])
        self.assertEqual(writes(result), [])

    def test_device_names_are_literal_bounded_and_absent_from_notifications(self):
        options = fixtures()
        options["devices"][1]["name"] = "<b>Device</b>" + "x" * 500 + "\n"
        options["pendingFolders"] = {"documents": {"offeredBy": {"B": {}}}}
        result = run("sharingView", options)
        name = result["sharingMenu"][0]["text"]
        self.assertTrue(name.startswith("<b>Device</b>"))
        self.assertLess(len(name), 60)
        self.assertIn("…", name)
        self.assertNotIn("<b>", json.dumps(result["notifications"]))

    def test_lock_removes_names_and_controls_and_rejects_retained_callback(self):
        result = run("sharingLocked", fixtures())
        self.assertEqual(result["locked"]["sharingMenu"], [])
        self.assertFalse(result["locked"]["sharingVisible"])
        self.assertEqual(writes(result["locked"]), [])
        self.assertTrue(result["unlocked"]["sharingVisible"])
        self.assertEqual(len(result["unlocked"]["sharingMenu"]), 2)

    def test_refusal_shows_unchanged_state_and_does_not_write_device(self):
        options = fixtures()
        options["refuse"] = True
        result = run("sharingAction", options)
        self.assertEqual(result["after"]["configured"], options["existingFolders"])
        self.assertIn("refused", result["after"]["notifications"][0]["body"])
        self.assertEqual(len(writes(result["after"])), 1)
        self.assertIn("Not shared", result["after"]["sharingMenu"][0]["actions"][0])

    def test_timeout_reports_uncertainty_and_refreshes_actual_applied_state(self):
        options = fixtures()
        options["interrupt"] = "timeout"
        result = run("sharingAction", options)
        self.assertIn("could not be confirmed", result["after"]["notifications"][0]["body"])
        self.assertIn("Sharing — Stop sharing", result["after"]["sharingMenu"][0]["actions"][0])

    def test_lock_or_disable_during_write_prevents_followup_mutations(self):
        for interrupt in ("lock", "destroy"):
            with self.subTest(interrupt=interrupt):
                options = fixtures()
                options["interrupt"] = interrupt
                result = run("sharingAction", options)
                self.assertEqual(len(writes(result["after"])), 1)
                self.assertEqual(result["after"]["notifications"], [])
                self.assertEqual(result["destroyed"]["timerCount"], 0)
                self.assertEqual(result["destroyed"]["cancellationHandlers"], 0)
                self.assertEqual(result["destroyed"]["sessionHandlers"], 0)
                self.assertEqual(result["destroyed"]["menuHandlers"], 0)

    def test_revalidates_folder_before_applying_stale_menu_choice(self):
        options = fixtures()
        options["changeBeforeClick"] = {"paused": True}
        result = run("sharingAction", options)
        self.assertEqual(writes(result["after"]), [])
        self.assertEqual(result["after"]["sharingMenu"][0]["actions"], [])

    def test_invalid_snapshot_has_no_controls_and_recovers_no_assumed_success(self):
        options = fixtures()
        options["apiErrors"] = {"system/connections": {"status": 200, "raw": "[]"}}
        result = run("sharingView", options)
        self.assertIn("Sharing unavailable", result["sharingMenu"][0]["text"])
        self.assertEqual(writes(result), [])
