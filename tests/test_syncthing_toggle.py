"""Unit tests for syncthing-toggle service-name validation.

The `service-name` GSettings key is free text writable by any process running
as the same user, and it is passed to `systemctl`. These tests verify that the
validator accepts genuine unit names, rejects malformed or injected values, and
that every `systemctl` call site actually routes through the validator.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
TOGGLE_JS = REPO_ROOT / "extensions" / "syncthing-toggle" / "toggle.js"
DRIVER_JS = REPO_ROOT / "tests" / "js" / "driver.mjs"
NODE = shutil.which("node")


def _validator_pattern() -> str:
    """Return the regex literal `_validatedServiceName` actually tests against.

    The pattern is read out of the source rather than restated here, so that
    changing the validator changes what these tests exercise. A copy kept in
    the test would keep passing after the real validator regressed.
    """
    source = TOGGLE_JS.read_text(encoding="utf-8")
    match = re.search(
        r"_validatedServiceName\s*\(\s*\)\s*\{.*?(/\^.*?\$/)\s*\.test\(",
        source,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError(
            "no regex literal found inside _validatedServiceName() in toggle.js"
        )
    return match.group(1)


def _function_body(name: str) -> str:
    """Return the source of a method declaration, not one of its call sites."""
    source = TOGGLE_JS.read_text(encoding="utf-8")
    match = re.search(rf"^\s*(?:async\s+)?{re.escape(name)}\s*\(", source, re.MULTILINE)
    if match is None:
        raise AssertionError(f"no declaration of {name}() found in toggle.js")
    tail = source[match.start() :]
    end = tail.index("\n\t\t}")
    return tail[:end]


def _eval_service_name_validator(name: str | None) -> bool:
    """Apply the extracted pattern under a real JS engine.

    JavaScript and Python regex semantics differ, so the check runs in node to
    match how the extension evaluates it at runtime.
    """
    js_code = r"""
    const pattern = (0, eval)(process.argv[1]);
    let input;
    try {
        input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
    } catch {
        input = null;
    }
    if (typeof input !== 'string') {
        process.stdout.write('false');
        process.exit(0);
    }
    process.stdout.write(pattern.test(input) ? 'true' : 'false');
    """
    res = subprocess.run(
        [NODE, "-e", js_code, _validator_pattern()],
        input=json.dumps(name),
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip() == "true"


class TestSyncthingToggleCallSites(unittest.TestCase):
    """Source-level guard; needs no JS engine, so it runs everywhere."""

    def test_every_systemctl_call_uses_the_validated_name(self):
        """No `systemctl` argv may be built from the raw GSettings string.

        `checkStatus` polls every few seconds; reading the setting directly
        there would hand unvalidated text to `systemctl` on a timer.
        """
        source = TOGGLE_JS.read_text(encoding="utf-8")
        reads = [
            line.strip()
            for line in source.splitlines()
            if "get_string('service-name')" in line
        ]
        self.assertEqual(
            len(reads),
            1,
            f"service-name must be read only by the validator, found: {reads}",
        )
        self.assertIn("_validatedServiceName", _function_body("checkStatus"))


# The pattern is evaluated by node so JS regex semantics decide the verdict,
# not Python's; without node these cases cannot be judged at all.
@unittest.skipIf(NODE is None, "node is not installed")
class TestSyncthingToggleServiceName(unittest.TestCase):
    def test_valid_service_names_accepted(self):
        valid_cases = [
            "syncthing.service",
            "syncthing@user.service",
            "syncthing-user.service",
            "sync.thing_instance:1@sub-domain.service",
            "my_syncthing.service",
            "app-123.service",
            "user@1000.service",
        ]
        for name in valid_cases:
            with self.subTest(name=name):
                self.assertTrue(
                    _eval_service_name_validator(name),
                    f"Expected valid service name {name!r} to be accepted",
                )

    def test_leading_dash_rejected(self):
        """A leading dash is parsed by systemctl as an option, not a unit."""
        for name in [
            "--global.service",
            "-H.service",
            "--user.service",
            "-.service",
            "--machine=other.service",
        ]:
            with self.subTest(name=name):
                self.assertFalse(
                    _eval_service_name_validator(name),
                    f"Expected option-like value {name!r} to be rejected",
                )

    def test_invalid_service_names_rejected(self):
        invalid_cases = [
            "",
            None,
            "syncthing",
            "syncthing.target",
            "syncthing.socket",
            "syncthing service",
            "syncthing.service; reboot",
            "syncthing.service && rm -rf /",
            "syncthing.service|cat",
            "syncthing.service\n",
            "syncthing.service`whoami`",
            "syncthing$(id).service",
            "../syncthing.service",
            "/syncthing.service",
            "syncthing.service --now",
            "syncthing.service\x00",
            "syncthing/foo.service",
        ]
        for name in invalid_cases:
            with self.subTest(name=name):
                self.assertFalse(
                    _eval_service_name_validator(name),
                    f"Expected invalid service name {name!r} to be rejected",
                )


class TestSyncthingToggleLifecycle(unittest.TestCase):
    """Runtime behaviour of the extension, exercised under node.

    ``tests/js/driver.mjs`` imports the real extension.js and toggle.js with
    stubbed GJS and gnome-shell modules, drives the actual enable()/disable()
    lifecycle and reports what the extension did: which argv it spawned, which
    main-loop sources and signal handlers it left behind, and what it showed.
    """

    results: dict

    @classmethod
    def setUpClass(cls):
        if NODE is None:
            raise unittest.SkipTest("node is not installed")
        proc = subprocess.run(
            [NODE, str(DRIVER_JS), str(TOGGLE_JS)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise AssertionError(
                "syncthing-toggle harness failed:\n"
                f"{proc.stdout.strip()}\n{proc.stderr.strip()}"
            )
        cls.results = json.loads(proc.stdout)

    def test_enable_runs_exactly_one_initial_status_check(self):
        """enable() refreshes once.

        The entry point already refreshes the status; a second refresh inside
        the indicator constructor would spawn a duplicate `systemctl status`
        process on every enable, including every screen unlock.
        """
        enable = self.results["enable"]
        self.assertEqual(
            enable["initialStatusCalls"],
            1,
            f"enable() spawned {enable['spawnedAtEnable']}; expected exactly one "
            "`systemctl status`",
        )

    def test_enable_reflects_a_service_started_outside_the_toggle(self):
        """A unit running before login must show up without anyone clicking."""
        enable = self.results["enable"]
        self.assertEqual(
            enable["toggleState"],
            {"checked": True, "subtitle": "Running", "indicatorVisible": True},
        )

    def test_status_is_polled_on_a_timer(self):
        enable = self.results["enable"]
        self.assertEqual(
            enable["installedSources"], 1, "expected exactly one status poll source"
        )
        self.assertGreater(enable["pollSeconds"], 0)
        self.assertLessEqual(
            enable["pollSeconds"],
            15,
            "poll interval is too long to reflect external changes promptly",
        )

    def test_poll_tick_picks_up_an_external_stop(self):
        tick = self.results["tick"]
        self.assertGreaterEqual(tick["statusCalls"], 1, "tick queried no status")
        self.assertTrue(tick["returnValue"], "timer returned SOURCE_REMOVE; polling dies")
        self.assertEqual(tick["checked"], False)
        self.assertEqual(tick["subtitle"], "Stopped")
        self.assertEqual(tick["indicatorVisible"], False)

    def test_disable_leaves_nothing_behind(self):
        disable = self.results["disable"]
        self.assertEqual(disable["liveSources"], 0, "timer source outlived disable()")
        self.assertEqual(
            disable["networkMonitorHandlers"],
            0,
            "network-metered handler outlived disable()",
        )
        self.assertEqual(
            disable["toggleHandlers"], 0, "clicked handler outlived disable()"
        )
        self.assertTrue(disable["cancellableCancelled"])
        self.assertEqual(
            disable["subprocessesAfterDisable"],
            [],
            "a stale tick or click spawned systemctl after disable()",
        )

    def test_teardown_runs_once_even_if_destroy_is_called_again(self):
        disable = self.results["disable"]
        self.assertIsNone(
            disable["secondDestroyError"],
            "destroy() after disable() raised instead of being a no-op",
        )
        self.assertEqual(
            disable["superDestroyCount"],
            1,
            "teardown ran more than once",
        )

    def test_disable_cancels_in_flight_calls_and_stops_touching_widgets(self):
        cancellation = self.results["cancellation"]
        self.assertGreaterEqual(
            cancellation["inFlightAtDisable"], 1, "scenario spawned nothing to cancel"
        )
        self.assertTrue(
            cancellation["cancellablePassedToSubprocess"],
            "a subprocess was started with a null cancellable and cannot be aborted",
        )
        self.assertTrue(
            cancellation["everySubprocessForcedExit"],
            "cancelling a Cancellable only abandons the wait; the systemctl child "
            "keeps running unless cancellation is wired to proc.force_exit()",
        )
        self.assertEqual(
            cancellation["cancellableHandlersLeft"],
            0,
            "each call must release its cancellation handler, or a long-lived "
            "cancellable accumulates one handler per status poll",
        )
        self.assertEqual(
            cancellation["subtitleAfter"],
            cancellation["subtitleBefore"],
            "a cancelled status callback still wrote to the destroyed toggle",
        )
        self.assertEqual(cancellation["indicatorVisible"], False)

    def test_metered_connection_blocks_the_start(self):
        metered = self.results["meteredClick"]
        self.assertEqual(
            metered["argv"],
            [],
            "systemctl ran despite the metered connection",
        )
        self.assertEqual(
            metered["checked"], False, "the toggle stayed on with nothing started"
        )
        self.assertEqual(len(metered["notifications"]), 1)
        self.assertIn("Metered", metered["notifications"][0]["body"])

    def test_unmetered_click_starts_and_stops_the_service(self):
        click = self.results["unmeteredClick"]
        self.assertEqual(
            click["argv"][0], ["systemctl", "--user", "start", "syncthing.service"]
        )
        self.assertTrue(
            click["allCallsCancellable"],
            "subprocesses were not tied to the indicator's cancellable",
        )
        self.assertEqual(click["checked"], True)
        self.assertEqual(
            click["offArgv"][0], ["systemctl", "--user", "stop", "syncthing.service"]
        )

    def test_click_persists_the_choice_across_reboots_by_default(self):
        """`start-stop-only` ships false, so a click also enables the unit.

        Only `start` means the toggle forgets itself at the next login; the
        `enable` has to follow the `start` (and `disable` the `stop`), or a
        failed start still leaves the unit enabled.
        """
        click = self.results["unmeteredClick"]
        self.assertEqual(
            click["verbs"],
            ["start", "status", "enable"],
            "a default click did not start, re-read and then persist the unit",
        )
        self.assertEqual(
            click["offVerbs"],
            ["stop", "status", "disable"],
            "turning the toggle off left the unit enabled for the next login",
        )

    def test_start_stop_only_leaves_unit_enablement_alone(self):
        verbs = self.results["startStopOnly"]["verbs"]
        self.assertEqual(verbs, ["start", "status"], verbs)

    def test_a_failed_start_does_not_report_success(self):
        """systemctl can refuse; the toggle must show what the unit really does.

        Announcing "sharing enabled" before the call, or running `enable`
        after a start that failed, leaves the user believing their files sync
        and makes the broken state come back at the next login.
        """
        failed = self.results["failedStart"]
        self.assertEqual(failed["checked"], False, "the toggle stayed on")
        self.assertEqual(failed["subtitle"], "Stopped")
        self.assertEqual(failed["indicatorVisible"], False)
        self.assertNotIn(
            "enable",
            failed["verbs"],
            f"a failed start was persisted with `enable`; ran {failed['verbs']}",
        )
        self.assertEqual(
            failed["notifications"],
            [],
            f"the failure was announced as success: {failed['notifications']}",
        )
        self.assertTrue(failed["errors"], "the failure was swallowed silently")

    def test_a_metered_pause_that_cannot_stop_the_unit_stays_quiet(self):
        """Saying "paused" while the unit keeps running is worse than silence.

        The user reads that as "safe to stay on mobile data" — the one thing
        the metered guard exists to prevent — so the notification waits for a
        stop that actually succeeded.
        """
        failed = self.results["failedMeteredStop"]
        self.assertIn("stop", failed["verbs"], "no stop was attempted at all")
        self.assertEqual(
            failed["notifications"],
            [],
            f"a failed stop was announced as a pause: {failed['notifications']}",
        )
        self.assertTrue(failed["errors"], "the failed stop was swallowed silently")

    def test_metered_pause_leaves_the_toggle_showing_a_stopped_unit(self):
        """After the pause the quick toggle must not still read as syncing."""
        signal = self.results["meteredSignal"]
        self.assertEqual(signal["checked"], False)
        self.assertEqual(signal["subtitle"], "Stopped")
        self.assertEqual(signal["indicatorVisible"], False)

    def test_extension_provisions_no_folders(self):
        """dakota ships the syncthing config via /etc/skel; the extension must
        not create ~/Sync, seed folders or run `syncthing generate`."""
        click = self.results["unmeteredClick"]
        self.assertEqual(click["directoriesCreated"], [])
        self.assertEqual(click["filesWritten"], [])
        self.assertEqual(
            click["filesTouched"], [], "the extension opened files of its own"
        )
        spawned = [argv[0] for argv in click["argv"] + click["offArgv"]]
        self.assertEqual(
            set(spawned), {"systemctl"}, f"unexpected program spawned: {spawned}"
        )

    def test_going_metered_while_running_stops_the_service(self):
        signal = self.results["meteredSignal"]
        self.assertEqual(
            signal["whileUnmetered"], [], "an unmetered notify stopped the service"
        )
        self.assertEqual(
            signal["whenMetered"][:1],
            ["stop"],
            "going metered while running did not stop the service",
        )
        self.assertTrue(signal["notifications"])
        self.assertEqual(
            signal["afterDisable"],
            [],
            "the network-metered handler still fired after disable()",
        )

    def test_a_session_that_starts_metered_stops_the_running_unit(self):
        """`notify::network-metered` fires on changes only.

        Log in on mobile data with the unit enabled from the last session and
        no signal ever arrives, so enable() has to check the current state
        itself — otherwise the metered guard protects only sessions that were
        already open when the connection changed.
        """
        login = self.results["meteredAtLogin"]
        self.assertIn(
            "stop",
            login["verbs"],
            f"a metered login left the unit running; ran {login['verbs']}",
        )
        self.assertEqual(
            login["verbs"][0], "status", "the unit was stopped before it was read"
        )
        self.assertTrue(login["notifications"], "sharing was paused without saying so")

    def test_invalid_service_name_never_reaches_systemctl(self):
        invalid = self.results["invalidServiceName"]
        self.assertEqual(
            invalid["argv"],
            [],
            "an option-like service-name was handed to systemctl",
        )
        self.assertEqual(invalid["subtitle"], "Stopped")
        self.assertEqual(invalid["indicatorVisible"], False)

    def test_web_gui_entry_opens_the_local_gui_on_the_configured_port(self):
        """The menu must reach the GUI the daemon actually serves.

        dakota ships `<address>127.0.0.1:8384</address>` — IPv4 loopback only.
        `localhost` is not good enough: it can resolve to ::1 first, where
        nothing listens. The URI must use the `port` setting and 127.0.0.1, or
        the entry opens a dead page, and a non-loopback host would point the
        user at another machine's instance — Syncthing's GUI administers every
        shared folder.
        """
        web = self.results["webGui"]
        self.assertEqual(
            len(web["launched"]),
            1,
            f"expected exactly one menu entry to open a URI; menu is {web['labels']}",
        )
        entry = web["launched"][0]
        for uri, port in ((entry["uri"], 8384), (entry["relocatedUri"], 9999)):
            parts = urlsplit(uri or "")
            self.assertEqual(parts.scheme, "http", uri)
            self.assertEqual(parts.hostname, "127.0.0.1", uri)
            self.assertEqual(parts.port, port, uri)
        self.assertEqual(web["errors"], [], "launching the Web GUI logged an error")

    def test_web_gui_entry_only_opens_a_browser(self):
        """Opening the GUI is a read-only action: it must not touch the unit."""
        web = self.results["webGui"]
        self.assertEqual(web["argv"], [], "the Web GUI entry spawned systemctl")
        self.assertEqual(web["filesTouched"], [])
        self.assertEqual(web["directoriesCreated"], [])


if __name__ == "__main__":
    unittest.main()
