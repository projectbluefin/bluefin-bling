"""Consumer regressions exercising the real API-backed Quick Settings control.

The harness substitutes only GNOME imports and the asynchronous OS/HTTP boundary.
It models persisted daemon folders, consent and service state, so these assertions
cover user-visible transitions rather than copied argv, labels or source text.
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


def posted_folders(result: dict) -> list[dict]:
    return [request["body"] for request in result["apiRequests"]
            if request["method"] == "POST" and request["path"] == "config/folders"]


def autostart_changes(result: dict) -> list[bool]:
    return [command["enabled"] for command in result["commands"]
            if command.get("action") == "autostart"]


@unittest.skipIf(NODE is None, "node is not installed")
class SyncthingApiControlTests(unittest.TestCase):
    def test_first_start_configures_local_only_folders_despite_defaults_peers(self):
        result = run_scenario("initial")
        folders = {folder["id"]: folder for folder in result["configured"]}
        self.assertEqual(set(folders), {"documents", "downloads", "pictures"})
        self.assertFalse(folders["documents"]["paused"])
        self.assertTrue(folders["downloads"]["paused"])
        self.assertTrue(folders["pictures"]["paused"])
        self.assertEqual(folders["documents"]["path"], "/home/test/Documentos")
        self.assertEqual(folders["pictures"]["path"], "/home/test/Images & Photos")
        for folder in folders.values():
            self.assertEqual(folder["devices"], [{"deviceID": "LOCAL-DEVICE-ID"}])
        self.assertEqual(result["devices"], result["originalDevices"])
        self.assertTrue(result["provisioned"])
        self.assertTrue(result["checked"])
        self.assertTrue(result["running"])
        self.assertTrue(result["webGuiSensitive"])
        self.assertTrue(result["autostart"])
        self.assertTrue(result["authenticated"])
        self.assertFalse(result["secretLeaked"])
        self.assertEqual(len(result["notifications"]), 1)
        self.assertTrue(result["notifications"][0]["active"])

    def test_legacy_first_use_replaces_the_process_without_rewriting_folders_or_peer_approvals(self):
        existing = [
            {"id": "documents", "path": "/home/test/Documentos", "label": "Private chosen label",
             "paused": True, "type": "receiveonly", "devices": [{"deviceID": "APPROVED-PEER"}],
             "versioning": {"type": "staggered", "params": {"maxAge": "999"}}},
            {"id": "custom-photos", "path": "/home/test/Images & Photos", "label": "My photos",
             "paused": False, "devices": [{"deviceID": "OTHER-APPROVED-PEER"}]},
            {"id": "sync", "path": "/srv/Shared projects", "label": "Existing shared folder",
             "paused": False, "devices": [{"deviceID": "PAIRED-SPUTNIK"}]},
        ]
        migration = run_scenario("firstUse", legacy=True, existingFolders=existing)
        before, result = migration["before"], migration["after"]
        self.assertTrue(before["running"])
        self.assertFalse(before["checked"])
        self.assertFalse(before["webGuiSensitive"])
        self.assertFalse(before["processCurrent"])
        self.assertTrue(result["running"])
        self.assertTrue(result["checked"])
        self.assertTrue(result["webGuiSensitive"])
        self.assertTrue(result["processCurrent"])
        self.assertTrue(result["provisioned"])
        self.assertTrue(result["autostart"])
        actual = {folder["id"]: folder for folder in result["configured"]}
        for folder in existing:
            self.assertEqual(actual[folder["id"]], folder)
        self.assertEqual([folder["id"] for folder in posted_folders(result)], ["downloads"])
        self.assertEqual(result["devices"], result["originalDevices"])
        self.assertFalse(any(command.get("verb") == "stop" for command in result["commands"]))
        self.assertEqual(len(result["notifications"]), 1)
        self.assertTrue(result["notifications"][0]["active"])
        self.assertFalse(result["secretLeaked"])

    def test_stopped_native_upgrade_preserves_custom_folders_identity_and_sharing_consent(self):
        existing = [
            {"id": "team-projects", "path": "/srv/Shared projects", "label": "Approved work",
             "paused": True, "type": "receiveonly", "devices": [{"deviceID": "PAIRED-SPUTNIK"}],
             "versioning": {"type": "staggered", "params": {"maxAge": "999"}}},
            {"id": "my-documents", "path": "/home/test/Documentos", "label": "Custom documents",
             "paused": True, "devices": [{"deviceID": "APPROVED-PEER"}]},
        ]
        migration = run_scenario("stoppedFirstUse", existingFolders=existing,
                                 identity="PRESERVED-NATIVE-IDENTITY", nativeActivationChecks=2,
                                 nativeReadinessChecks=1, nativeApiNotReadyChecks=2)
        before, result = migration["before"], migration["after"]
        self.assertFalse(before["running"])
        self.assertFalse(before["managedPrepared"])
        self.assertTrue(result["running"])
        self.assertTrue(result["checked"])
        self.assertTrue(result["webGuiSensitive"])
        self.assertEqual(result["processKind"], "container")
        self.assertTrue(result["provisioned"])
        self.assertTrue(result["autostart"])
        self.assertEqual(result["identity"], "PRESERVED-NATIVE-IDENTITY")
        actual = {folder["id"]: folder for folder in result["configured"]}
        for folder in existing:
            self.assertEqual(actual[folder["id"]], folder)
        self.assertEqual(result["capturedLegacyPaths"], [folder["path"] for folder in existing])
        for folder in posted_folders(result):
            self.assertEqual(folder["devices"], [{"deviceID": "PRESERVED-NATIVE-IDENTITY"}])
        self.assertEqual(result["devices"], result["originalDevices"])
        self.assertEqual(len(result["notifications"]), 1)
        self.assertTrue(result["notifications"][0]["active"])
        self.assertFalse(result["secretLeaked"])

    def test_originally_running_native_api_failure_is_not_given_temporary_start_retries(self):
        existing = [{"id": "custom", "path": "/srv/Shared projects", "paused": True,
                     "devices": [{"deviceID": "APPROVED-PEER"}]}]
        result = run_scenario("firstUse", legacy=True, nativeApiNotReadyChecks=2,
                              existingFolders=existing)["after"]
        self.assertTrue(result["running"])
        self.assertEqual(result["processKind"], "native")
        self.assertFalse(result["managedPrepared"])
        self.assertFalse(result["legacyCaptured"])
        self.assertFalse(result["provisioned"])
        self.assertEqual(result["configured"], existing)
        self.assertEqual(result["devices"], result["originalDevices"])
        self.assertFalse(any(command.get("verb") == "stop" for command in result["commands"]))
        self.assertEqual(sum(command.get("action") == "prepare" for command in result["commands"]), 1)
        self.assertEqual(len(result["notifications"]), 1)
        self.assertFalse(result["secretLeaked"])

    def test_native_first_use_failure_restores_the_initially_stopped_service(self):
        failures = [
            {"fail": "systemctl:start"},
            {"failNativeInspection": True},
            {"fail": "systemctl:daemon-reload"},
            {"envUnreadable": True},
            {"fail": "systemctl:restart"},
            {"apiErrors": {"system/status": {"status": 401}}},
            {"apiErrors": {"config/folders": {"status": 403}}},
            {"fail": "helper:provisioned"},
        ]
        existing = [{"id": "custom", "path": "/srv/Shared projects", "paused": True,
                     "devices": [{"deviceID": "APPROVED-PEER"}]}]
        for options in failures:
            with self.subTest(options=options):
                result = run_scenario("stoppedFirstUse", existingFolders=existing, **options)["after"]
                self.assertFalse(result["running"])
                self.assertFalse(result["checked"])
                self.assertFalse(result["provisioned"])
                self.assertFalse(result["autostart"])
                self.assertEqual(result["configured"][0], existing[0])
                self.assertEqual(result["devices"], result["originalDevices"])
                self.assertEqual(len(result["notifications"]), 1)
                self.assertFalse(result["notifications"][0]["active"])
                self.assertFalse(result["secretLeaked"])

    def test_native_readiness_is_bounded_and_failure_does_not_retry_unsafe_inspection(self):
        for options in [{"nativeActivationChecks": 1000}, {"nativeReadinessChecks": 1000},
                        {"nativeApiNotReadyChecks": 1000}, {"failNativeInspection": True}]:
            with self.subTest(options=options):
                result = run_scenario("stoppedFirstUse", **options)["after"]
                self.assertFalse(result["running"])
                self.assertFalse(result["managedPrepared"])
                self.assertFalse(result["legacyCaptured"])
                self.assertFalse(result["provisioned"])
                self.assertFalse(result["autostart"])
                self.assertEqual(posted_folders(result), [])
                self.assertEqual(len(result["notifications"]), 1)
                self.assertLessEqual(sum(command.get("action") == "prepare"
                                         for command in result["commands"]), 61)
                if options.get("failNativeInspection"):
                    self.assertEqual(sum(command.get("action") == "prepare"
                                         for command in result["commands"]), 2)

    def test_off_cancellation_and_teardown_never_leave_temporary_native_sharing_running(self):
        gates = [(gate, {}) for gate in ["helper:prepare", "systemctl:start", "native:activation",
                                       "helper:prepare-native", "systemctl:restart", "api:GET:system/status"]]
        gates.append(("delay", {"nativeApiNotReadyChecks": 2}))
        for interrupt in ["off", "cancel", "destroy"]:
            for gate, options in gates:
                with self.subTest(interrupt=interrupt, gate=gate):
                    result = run_scenario("nativeInterrupt", interrupt=interrupt, gate=gate, **options)["after"]
                    self.assertFalse(result["running"])
                    self.assertFalse(result["autostart"])
                    self.assertEqual(result["activeRequests"], 0)
                    self.assertEqual(result["cancellationHandlers"], 0)
                    self.assertFalse(result["secretLeaked"])
                    if interrupt == "off":
                        self.assertFalse(result["desiredOn"])
                        self.assertFalse(result["checked"])
                        self.assertEqual(len(result["notifications"]), 1)
                        self.assertFalse(result["notifications"][0]["active"])
                    elif interrupt == "destroy":
                        self.assertEqual(result["notifications"], [])
                        self.assertEqual(result["timerCount"], 0)
                        self.assertEqual(result["networkHandlers"], 0)
                        self.assertEqual(result["clickHandlers"], 0)
                    else:
                        self.assertEqual(result["notifications"], [])

    def test_unobserved_active_unit_is_not_stopped_when_api_authentication_fails(self):
        result = run_scenario("initial", running=True,
                              apiErrors={"system/status": {"status": 401}})
        self.assertTrue(result["running"])
        self.assertFalse(result["checked"])
        self.assertFalse(result["provisioned"])
        self.assertEqual(autostart_changes(result), [])
        self.assertFalse(any(command.get("verb") == "stop" for command in result["commands"]))
        self.assertEqual(len(result["notifications"]), 1)
        self.assertFalse(result["secretLeaked"])

    def test_failed_replacement_or_provisioning_preserves_the_preexisting_active_service(self):
        failures = [
            ({"fail": "systemctl:restart"}, False, False),
            ({"apiErrors": {"system/status": {"status": 401}}}, True, False),
            ({"apiErrors": {"config/folders": {"status": 403}}}, True, True),
            ({"apiErrors": {"system/status": [None, {"status": 401}]}}, True, False),
        ]
        for options, replaced, healthy in failures:
            with self.subTest(options=options):
                result = run_scenario("firstUse", legacy=True, **options)["after"]
                self.assertTrue(result["running"])
                self.assertEqual(result["processCurrent"], replaced)
                self.assertEqual(result["checked"], healthy)
                self.assertFalse(result["provisioned"])
                self.assertTrue(result["restartRequired"])
                self.assertEqual(autostart_changes(result), [])
                self.assertFalse(any(command.get("verb") == "stop" for command in result["commands"]))
                self.assertEqual(len(result["notifications"]), 1)
                self.assertFalse(result["secretLeaked"])

    def test_subsequent_starts_respect_user_edits_and_deleted_presets(self):
        result = run_scenario("laterStart")
        actual = {folder["id"]: folder for folder in result["configured"]}
        self.assertNotIn("pictures", actual)
        self.assertEqual(actual["documents"]["label"], "My chosen name")
        self.assertTrue(actual["documents"]["paused"])
        self.assertEqual(actual["documents"]["devices"], [{"deviceID": "USER-APPROVED-PEER"}])
        self.assertEqual(len(posted_folders(result)), 3)
        self.assertEqual(sum(request["path"] == "config/defaults/folder"
                             for request in result["apiRequests"]), 1)

    def test_changed_listener_is_acknowledged_without_recreating_deleted_presets(self):
        migration = run_scenario("firstUse", restartRequired=True, provisioned=True, existingFolders=[])
        before, result = migration["before"], migration["after"]
        self.assertTrue(before["running"])
        self.assertFalse(before["checked"])
        self.assertTrue(result["running"])
        self.assertTrue(result["processCurrent"])
        self.assertFalse(result["restartRequired"])
        self.assertEqual(result["configured"], [])
        self.assertEqual(posted_folders(result), [])
        self.assertTrue(result["checked"])

    def test_missing_documents_does_not_create_a_fake_directory_or_definition(self):
        folders = [{"id": "downloads", "label": "Downloads", "path": "/mnt/My Downloads", "paused": True}]
        result = run_scenario("initial", folders=folders)
        self.assertEqual([folder["id"] for folder in result["configured"]], ["downloads"])
        self.assertTrue(result["configured"][0]["paused"])
        self.assertTrue(result["provisioned"])

    def test_readiness_waits_for_the_api_instead_of_announcing_a_spawn(self):
        result = run_scenario("initial", healthFailures=2)
        self.assertTrue(result["checked"])
        self.assertEqual(len(result["notifications"]), 1)
        self.assertTrue(result["notifications"][0]["active"])
        self.assertEqual(sum(request["path"] == "system/status"
                             for request in result["apiRequests"]), 4)

    def test_readiness_timeout_rolls_back_without_persisting_or_announcing_success(self):
        result = run_scenario("initial", healthFailures=1000)
        self.assertFalse(result["checked"])
        self.assertFalse(result["running"])
        self.assertFalse(result["autostart"])
        self.assertFalse(result["provisioned"])
        self.assertEqual(posted_folders(result), [])
        self.assertEqual(len(result["notifications"]), 1)
        self.assertFalse(result["notifications"][0]["active"])
        self.assertLessEqual(sum(request["path"] == "system/status"
                                 for request in result["apiRequests"]), 61)

    def test_api_rejections_and_malformed_data_fail_without_disclosing_response_secrets(self):
        errors = [
            {"system/status": {"status": 401}},
            {"system/status": {"status": 200, "raw": "{not-json"}},
            {"system/status": {"status": 200, "raw": "{}"}},
            {"config/folders": {"status": 200, "raw": "{}"}},
            {"POST:config/folders": {"status": 400}},
        ]
        for error in errors:
            with self.subTest(error=error):
                result = run_scenario("initial", apiErrors=error)
                self.assertFalse(result["checked"])
                self.assertFalse(result["running"])
                self.assertFalse(result["provisioned"])
                self.assertEqual(autostart_changes(result), [])
                self.assertEqual(len(result["notifications"]), 1)
                self.assertFalse(result["notifications"][0]["active"])
                self.assertFalse(result["secretLeaked"])

    def test_helper_failures_and_invalid_credentials_are_user_visible(self):
        for options in [{"fail": "helper:prepare"}, {"malformedHelper": True},
                        {"envUnreadable": True}, {"invalidCredentials": True},
                        {"fail": "systemctl:start"}]:
            with self.subTest(options=options):
                result = run_scenario("initial", **options)
                self.assertFalse(result["checked"])
                self.assertFalse(result["running"])
                self.assertFalse(result["provisioned"])
                self.assertEqual(len(result["notifications"]), 1)
                self.assertFalse(result["secretLeaked"])

    def test_setup_failure_does_not_stop_a_preexisting_users_service(self):
        result = run_scenario("initial", running=True, fail="helper:prepare")
        self.assertTrue(result["running"])
        self.assertTrue(result["checked"])
        self.assertFalse(any(command.get("verb") == "stop" for command in result["commands"]))
        self.assertEqual(len(result["notifications"]), 1)

    def test_persistence_failure_reports_the_observed_running_state_not_a_false_success(self):
        result = run_scenario("initial", fail="helper:autostart")
        self.assertTrue(result["running"])
        self.assertTrue(result["checked"])
        self.assertFalse(result["autostart"])
        self.assertTrue(result["provisioned"])
        self.assertEqual(len(result["notifications"]), 1)

    def test_newest_on_supersedes_an_in_flight_stop(self):
        result = run_scenario("rapidRestart")
        self.assertTrue(result["running"])
        self.assertTrue(result["checked"])
        self.assertTrue(result["autostart"])
        self.assertEqual(len(result["notifications"]), 1)
        self.assertTrue(result["notifications"][0]["active"])

    def test_multiple_immediate_clicks_apply_only_the_newest_request(self):
        result = run_scenario("rapidSequence")
        self.assertFalse(result["running"])
        self.assertFalse(result["checked"])
        self.assertFalse(result["autostart"])
        self.assertEqual(len(result["notifications"]), 1)
        self.assertFalse(result["notifications"][0]["active"])

    def test_failed_stop_keeps_the_toggle_and_saved_choice_on(self):
        for options in [{}, {"stopIneffective": True, "fail": "none"}]:
            with self.subTest(options=options):
                result = run_scenario("failedStop", **options)
                self.assertTrue(result["running"])
                self.assertTrue(result["checked"])
                self.assertTrue(result["autostart"])
                self.assertEqual(autostart_changes(result), [])
                self.assertEqual(len(result["notifications"]), 1)

    def test_polling_requires_daemon_health_not_only_an_active_container(self):
        result = run_scenario("health", apiErrors={"system/status": {"transport": True}})
        self.assertTrue(result["running"])
        self.assertFalse(result["checked"])
        self.assertFalse(result["webGuiSensitive"])
        self.assertEqual(result["notifications"], [])

    def test_latest_off_wins_at_each_asynchronous_start_boundary(self):
        boundaries = [
            (gate, {}) for gate in ["systemctl:is-active", "helper:prepare", "systemctl:daemon-reload",
                                   "file:credentials", "systemctl:start", "api:GET:system/status",
                                   "api:POST:config/folders", "helper:provisioned", "helper:autostart"]
        ] + [("systemctl:restart", {"running": True, "legacy": True})]
        for gate, options in boundaries:
            with self.subTest(gate=gate):
                result = run_scenario("rapid", gate=gate, **options)
                self.assertFalse(result["running"])
                self.assertFalse(result["checked"])
                self.assertFalse(result["desiredOn"])
                self.assertFalse(result["autostart"])
                self.assertEqual(len(result["notifications"]), 1)
                self.assertFalse(result["notifications"][0]["active"])
                self.assertEqual(result["cancellationHandlers"], 0)
                self.assertEqual(result["activeRequests"], 0)

    def test_stale_status_response_cannot_turn_a_stopped_toggle_back_on(self):
        result = run_scenario("stalePoll")
        self.assertFalse(result["running"])
        self.assertFalse(result["checked"])
        self.assertFalse(result["webGuiSensitive"])
        self.assertEqual(len(result["notifications"]), 1)
        self.assertFalse(result["notifications"][0]["active"])

    def test_manual_on_while_metered_uses_the_full_first_start_after_unmetering(self):
        result = run_scenario("meteredFirst")
        before, after = result["before"], result["after"]
        self.assertFalse(before["running"])
        self.assertFalse(before["checked"])
        self.assertTrue(before["desiredOn"])
        self.assertTrue(before["paused"])
        self.assertEqual(before["configured"], [])
        self.assertEqual(autostart_changes(before), [])
        self.assertTrue(after["running"])
        self.assertTrue(after["checked"])
        self.assertFalse(after["paused"])
        self.assertTrue(after["provisioned"])
        self.assertTrue(after["autostart"])
        self.assertEqual(len(posted_folders(after)), 3)
        self.assertEqual(autostart_changes(after), [True])

    def test_automatic_metered_pause_does_not_erase_persistent_on_intent(self):
        result = run_scenario("meteredPause")
        paused, after = result["paused"], result["after"]
        self.assertFalse(paused["running"])
        self.assertTrue(paused["autostart"])
        self.assertTrue(paused["desiredOn"])
        self.assertEqual(autostart_changes(paused), [True])
        self.assertTrue(after["running"])
        self.assertEqual(autostart_changes(after), [True])
        self.assertEqual(len(posted_folders(after)), 3)

    def test_manual_off_cancels_metered_resume_permission_and_persists_off(self):
        result = run_scenario("meteredPause", manualOff=True)["after"]
        self.assertFalse(result["running"])
        self.assertFalse(result["checked"])
        self.assertFalse(result["desiredOn"])
        self.assertFalse(result["autostart"])
        self.assertEqual(autostart_changes(result), [True, False])

    def test_metered_edge_during_first_start_retains_initialization_and_persistence_intent(self):
        result = run_scenario("meteredDuringStart")
        self.assertFalse(result["paused"]["running"])
        self.assertTrue(result["paused"]["desiredOn"])
        self.assertFalse(result["paused"]["provisioned"])
        self.assertTrue(result["after"]["running"])
        self.assertTrue(result["after"]["provisioned"])
        self.assertTrue(result["after"]["autostart"])
        self.assertEqual(autostart_changes(result["after"]), [True])

    def test_start_stop_only_leaves_the_login_choice_untouched(self):
        for autostart in [False, True]:
            with self.subTest(autostart=autostart):
                result = run_scenario("startStopOnly", autostart=autostart)
                self.assertFalse(result["running"])
                self.assertEqual(result["autostart"], autostart)
                self.assertEqual(autostart_changes(result), [])
                self.assertTrue(result["provisioned"])

    def test_initial_reconcile_pauses_an_already_running_service_on_metered_network(self):
        result = run_scenario("reconcile")
        self.assertFalse(result["running"])
        self.assertFalse(result["checked"])
        self.assertTrue(result["desiredOn"])
        self.assertTrue(result["paused"])
        self.assertTrue(result["autostart"])
        self.assertEqual(autostart_changes(result), [])

    def test_metered_startup_of_unmanaged_native_service_resumes_through_one_click_migration(self):
        existing = [{"id": "custom", "path": "/srv/Shared projects", "label": "My shared folder",
                     "paused": False, "devices": [{"deviceID": "PAIRED-SPUTNIK"}]}]
        result = run_scenario("meteredNativeFirstUse", existingFolders=existing,
                              identity="PRESERVED-NATIVE-IDENTITY")
        before, after = result["before"], result["after"]
        self.assertFalse(before["running"])
        self.assertTrue(before["desiredOn"])
        self.assertTrue(before["paused"])
        self.assertTrue(before["autostart"])
        self.assertFalse(before["managedPrepared"])
        self.assertFalse(before["legacyCaptured"])
        self.assertEqual(before["configured"], existing)
        self.assertTrue(after["running"])
        self.assertTrue(after["checked"])
        self.assertEqual(after["processKind"], "container")
        self.assertFalse(after["paused"])
        self.assertTrue(after["provisioned"])
        self.assertTrue(after["autostart"])
        self.assertEqual(after["identity"], "PRESERVED-NATIVE-IDENTITY")
        self.assertEqual(after["configured"][0], existing[0])
        self.assertEqual(after["capturedLegacyPaths"], [existing[0]["path"]])
        self.assertEqual(after["devices"], after["originalDevices"])
        self.assertEqual(autostart_changes(after), [])
        self.assertFalse(after["secretLeaked"])

    def test_metered_manual_first_use_defers_stopped_native_inspection_until_unmetering(self):
        result = run_scenario("meteredFirst", legacy=True)
        before, after = result["before"], result["after"]
        self.assertFalse(before["running"])
        self.assertFalse(before["managedPrepared"])
        self.assertFalse(before["legacyCaptured"])
        self.assertTrue(before["desiredOn"])
        self.assertTrue(before["paused"])
        self.assertEqual(autostart_changes(before), [])
        self.assertTrue(after["running"])
        self.assertTrue(after["checked"])
        self.assertEqual(after["processKind"], "container")
        self.assertTrue(after["provisioned"])
        self.assertTrue(after["autostart"])
        self.assertFalse(after["paused"])
        self.assertEqual(autostart_changes(after), [True])

    def test_pending_failure_and_recovery_do_not_repeat_invitations(self):
        steps = run_scenario("pending")["steps"]
        counts = [len(step["notifications"]) for step in steps]
        self.assertEqual(counts, [1, 1, 1, 1, 1, 2, 2, 3])
        for index in [2, 3, 4]:
            self.assertEqual(steps[index]["pending"], steps[0]["pending"])
            self.assertTrue(steps[index]["checked"])
        self.assertEqual(steps[6]["pending"], [])
        self.assertEqual(len(steps[5]["pending"]), 3)
        self.assertTrue(steps[-1]["authenticated"])
        self.assertFalse(steps[-1]["secretLeaked"])
        self.assertEqual(posted_folders(steps[-1]), [])
        self.assertTrue(all(request["method"] == "GET" for request in steps[-1]["apiRequests"]))

    def test_pending_identity_includes_the_offering_device(self):
        steps = run_scenario("pending")["steps"]
        original = [json.loads(value) for value in steps[0]["pending"]]
        changed = [json.loads(value) for value in steps[5]["pending"]]
        self.assertIn(["folder", "shared", "DEVICE-A"], original)
        self.assertIn(["folder", "shared", "DEVICE-A"], changed)
        self.assertIn(["folder", "shared", "DEVICE-B"], changed)
        self.assertEqual(len(steps[5]["notifications"]), len(steps[4]["notifications"]) + 1)

    def test_overlapping_polls_do_not_duplicate_requests_or_announcements(self):
        result = run_scenario("overlappingPoll")
        paths = [request["path"] for request in result["apiRequests"]]
        self.assertEqual(paths.count("cluster/pending/devices"), 1)
        self.assertEqual(paths.count("cluster/pending/folders"), 1)
        self.assertEqual(result["activeRequests"], 0)
        self.assertEqual(result["cancellationHandlers"], 0)

    def test_disable_cancels_in_flight_resources_without_stopping_users_service(self):
        boundaries = [
            (gate, {}) for gate in ["systemctl:is-active", "helper:prepare", "systemctl:daemon-reload",
                                   "file:credentials", "systemctl:start", "api:GET:system/status",
                                   "helper:provisioned"]
        ] + [("systemctl:restart", {"running": True, "legacy": True})]
        for gate, options in boundaries:
            with self.subTest(gate=gate):
                result = run_scenario("teardown", gate=gate, **options)
                before, after = result["before"], result["after"]
                self.assertEqual(after["running"], before["running"])
                self.assertEqual(after["notifications"], [])
                self.assertEqual(after["timerCount"], 0)
                self.assertEqual(after["cancellationHandlers"], 0)
                self.assertEqual(after["networkHandlers"], 0)
                self.assertEqual(after["clickHandlers"], 0)
                self.assertEqual(after["activeRequests"], 0)
                self.assertEqual(after["destroyCount"], 1)
                self.assertEqual(after["abortCount"], 1)
                self.assertFalse(any(command.get("verb") == "stop" for command in after["commands"]))

    def test_disable_during_readiness_delay_removes_retry_timers_and_handlers(self):
        result = run_scenario("teardownDelay")
        self.assertTrue(result["running"])
        self.assertEqual(result["timerCount"], 0)
        self.assertEqual(result["cancellationHandlers"], 0)
        self.assertEqual(result["notifications"], [])

    def test_invalid_service_names_cannot_reach_a_subprocess(self):
        for name in ["--system.service", "syncthing.service; reboot", "../syncthing.service", "syncthing.service --now"]:
            with self.subTest(name=name):
                result = run_scenario("initial", serviceName=name)
                self.assertEqual(result["commands"], [])
                self.assertFalse(result["checked"])
                self.assertEqual(len(result["notifications"]), 1)

    def test_quick_settings_is_the_only_surface_and_settings_respect_lock_screen(self):
        unlocked = run_scenario("menu", port=12345)
        locked = run_scenario("menu", allowSettings=False)
        self.assertEqual(unlocked["panelIconCount"], 0)
        self.assertEqual(unlocked["quickSettingsCount"], 1)
        self.assertTrue(unlocked["settingsVisible"])
        self.assertFalse(locked["settingsVisible"])
        self.assertTrue(unlocked["preferencesOpened"])
        self.assertEqual(unlocked["launchedUrl"], "http://127.0.0.1:12345")


if __name__ == "__main__":
    unittest.main()
