"""Behavioral unit tests for extensions/syncthing-toggle/extension.js.

``toggle.js`` and ``prefs.js`` are both executed by their own harnesses, and
``light-style`` and ``power-status-color`` each have one for their entry point.
``syncthing-toggle/extension.js`` was the one entry point nothing ran: the rest
of the suite reaches it only as text, asserting that it exports a default class
extending ``Extension`` and that ``disable()`` is not empty
(``tests/test_extension_sources.py``). A ``disable()`` that destroyed the
indicator but not its quick-settings items, or an ``enable()`` that called
``checkStatus()`` instead of ``reconcile()``, satisfied every one of those
regex checks.

Each of those is a silent failure in the panel rather than a crash:

* ``ServiceIndicator`` is handed ``this``; it reads ``getSettings()`` and
  ``path`` off it to find the service name and the bundled icon. Anything else
  and the toggle comes up unconfigured.
* ``reconcile()``, not ``checkStatus()`` — the source comments exactly this.
  A session may start with the unit already running on a metered link, which
  emits no signal, so the cheap probe leaves the toggle showing the wrong
  state until something else happens to fire.
* the indicator is reconciled *before* it is handed to the panel, so the
  toggle is never briefly visible in a state the daemon is not in.
* ``disable()`` destroys every entry of ``quickSettingsItems`` before the
  indicator itself and then drops the reference. Destroying the indicator
  first leaves its items parented to a destroyed widget, and keeping the
  reference pins the whole tree for the life of the session — a lock/unlock
  cycle runs this path every time.
* a second ``enable()`` must build a *fresh* indicator; re-adding the destroyed
  one puts a dead widget in quick settings.

These tests drive the real shipped source through
``tests/syncthing_extension_harness.mjs``, which rewrites only the import lines
— the gi://resource:/// ones via ``tests/gnome_module_loader.mjs``, and the
relative ``./toggle.js`` so a stub indicator can record what was asked of it.
``ServiceIndicator``'s own behavior is covered by
``tests/test_syncthing_toggle_behavior.py``; what is under test here is the
lifecycle contract around it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "syncthing_extension_harness.mjs"
EXTENSION_JS = REPO_ROOT / "extensions" / "syncthing-toggle" / "extension.js"

NODE = shutil.which("node")

# Every assertion below runs by spawning node. Without a bound, a harness that
# deadlocks -- a promise that never settles, a stubbed timeout that never fires
# -- blocks the suite forever: locally it hangs the terminal, and in CI it holds
# the required `validate extensions` status context until the job ceiling. A
# whole suite run takes seconds, so this only ever fires on a real hang.
NODE_TIMEOUT_SECONDS = 60

# The quick-settings entry point for an indicator the shell does not own.
ADD_PATH = "Main.panel.statusArea.quickSettings.addExternalIndicator"


class HarnessMixin:
    """Spawns the harness and returns its JSON result."""

    def run_scenario(self, scenario: str, **options):
        result = subprocess.run(
            [NODE, str(HARNESS), scenario, json.dumps(options)],
            capture_output=True,
            text=True,
            check=False,
            timeout=NODE_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"harness scenario {scenario} failed:\n{result.stderr.strip()}",
        )
        return json.loads(result.stdout)

    @staticmethod
    def names(events):
        """Return just the call names, dropping the indicator ids."""
        return [event[0] for event in events]


@unittest.skipIf(NODE is None, "node is not installed")
class SyncthingExtensionLifecycleTestCase(HarnessMixin, unittest.TestCase):
    """The enable/disable contract extension.js keeps around ServiceIndicator."""

    # --- enable() ----------------------------------------------------------

    def test_indicator_is_constructed_with_the_extension_itself(self):
        # ServiceIndicator calls extensionObject.getSettings() and reads
        # extensionObject.path for the bundled icon. Handing it anything but
        # `this` -- a settings object, a metadata dict -- leaves the toggle
        # without a service name and without its icon.
        snapshot = self.run_scenario("enable")
        self.assertEqual(snapshot["indicatorsConstructed"], 1)
        self.assertEqual(snapshot["constructedWithExtension"], [True])

    def test_enable_reconciles_rather_than_checking_status(self):
        # The source comments this choice: a session can start with the unit
        # already running, which emits no signal for checkStatus() to have
        # observed.
        events = self.run_scenario("enable")["events"]
        self.assertIn("reconcile", self.names(events))
        self.assertNotIn("checkStatus", self.names(events))

    def test_enable_reconciles_exactly_once(self):
        events = self.run_scenario("enable")["events"]
        self.assertEqual(self.names(events).count("reconcile"), 1)

    def test_indicator_is_reconciled_before_the_panel_sees_it(self):
        # Added first, reconciled second, and the toggle is briefly visible
        # holding whatever state its constructor defaulted to.
        events = self.names(self.run_scenario("enable")["events"])
        self.assertEqual(events, ["construct", "reconcile", "addExternalIndicator"])

    def test_indicator_is_added_through_add_external_indicator(self):
        # addIndicator() and Main.panel.addToStatusArea() are both stubbed and
        # recorded, so routing the indicator through one of them fails here
        # instead of passing identically.
        snapshot = self.run_scenario("enable")
        self.assertEqual([call["path"] for call in snapshot["addCalls"]], [ADD_PATH])

    def test_the_indicator_added_is_the_one_constructed(self):
        snapshot = self.run_scenario("enable")
        self.assertEqual([call["indicatorId"] for call in snapshot["addCalls"]], [1])
        self.assertEqual(snapshot["currentIndicatorId"], 1)

    def test_enable_leaves_nothing_destroyed(self):
        snapshot = self.run_scenario("enable", itemCount=2)
        self.assertEqual(snapshot["indicatorDestroyCounts"], [0])
        self.assertEqual(snapshot["itemDestroyCounts"], [[0, 0]])

    # --- disable() ---------------------------------------------------------

    def test_disable_destroys_every_quick_settings_item(self):
        # forEach, not quickSettingsItems[0]: the indicator may carry more than
        # one item, and a survivor stays in quick settings with its signal
        # handlers still attached to a destroyed indicator.
        after = self.run_scenario("lifecycle", itemCount=3)["afterDisable"]
        self.assertEqual(after["itemDestroyCounts"], [[1, 1, 1]])

    def test_disable_destroys_items_before_the_indicator(self):
        events = self.names(
            self.run_scenario("lifecycle", itemCount=2)["afterDisable"]["events"]
        )
        self.assertEqual(
            events,
            [
                "construct",
                "reconcile",
                "addExternalIndicator",
                "item-destroy",
                "item-destroy",
                "indicator-destroy",
            ],
        )

    def test_disable_destroys_the_indicator_exactly_once(self):
        after = self.run_scenario("lifecycle", itemCount=1)["afterDisable"]
        self.assertEqual(after["indicatorDestroyCounts"], [1])

    def test_disable_drops_the_indicator_reference(self):
        # Keeping it pins the destroyed widget tree for the life of the
        # session, and gnome-shell's extension checker reports the leak.
        after = self.run_scenario("lifecycle", itemCount=1)["afterDisable"]
        self.assertTrue(after["indicatorIsNull"])

    def test_disable_survives_an_indicator_with_no_quick_settings_items(self):
        after = self.run_scenario("lifecycle", itemCount=0)["afterDisable"]
        self.assertEqual(after["itemDestroyCounts"], [[]])
        self.assertEqual(after["indicatorDestroyCounts"], [1])
        self.assertTrue(after["indicatorIsNull"])

    def test_enable_state_is_unchanged_by_the_later_disable(self):
        # Guards the harness itself: afterEnable must be a snapshot, not a live
        # view of the log that disable() then writes into.
        result = self.run_scenario("lifecycle", itemCount=1)
        self.assertEqual(result["afterEnable"]["indicatorDestroyCounts"], [0])
        self.assertFalse(result["afterEnable"]["indicatorIsNull"])

    # --- re-enable ---------------------------------------------------------

    def test_re_enable_builds_a_fresh_indicator(self):
        # An ordinary lock/unlock cycle. Reusing the destroyed indicator puts a
        # dead widget in quick settings.
        snapshot = self.run_scenario("reEnable", itemCount=1)
        self.assertEqual(snapshot["indicatorsConstructed"], 2)
        self.assertEqual(snapshot["constructedWithExtension"], [True, True])
        self.assertEqual(snapshot["currentIndicatorId"], 2)

    def test_re_enable_adds_the_new_indicator_and_not_the_destroyed_one(self):
        snapshot = self.run_scenario("reEnable", itemCount=1)
        self.assertEqual([call["path"] for call in snapshot["addCalls"]], [ADD_PATH] * 2)
        self.assertEqual([call["indicatorId"] for call in snapshot["addCalls"]], [1, 2])

    def test_re_enable_reconciles_the_new_indicator(self):
        # The second session starts from whatever state the daemon is in now,
        # not from whatever the first session last saw.
        events = self.run_scenario("reEnable", itemCount=1)["events"]
        self.assertEqual([e for e in events if e[0] == "reconcile"], [["reconcile", 1], ["reconcile", 2]])

    def test_re_enable_does_not_destroy_the_first_indicator_twice(self):
        snapshot = self.run_scenario("reEnable", itemCount=1)
        self.assertEqual(snapshot["indicatorDestroyCounts"], [1, 0])
        self.assertEqual(snapshot["itemDestroyCounts"], [[1], [0]])


@unittest.skipIf(NODE is None, "node is not installed")
class SyncthingExtensionHarnessContractTestCase(HarnessMixin, unittest.TestCase):
    """The rewrite the harness performs has to keep matching the shipped file."""

    def test_extension_js_imports_service_indicator_from_toggle_js(self):
        # If the entry point stops taking ServiceIndicator from its own module,
        # the harness is stubbing something the source no longer imports and
        # every assertion above would be exercising a different shape.
        source = EXTENSION_JS.read_text(encoding="utf-8")
        self.assertRegex(
            source,
            r"import\s*\{[^}]*\bServiceIndicator\b[^}]*\}\s*from\s*['\"]\./toggle\.js['\"]",
            "extension.js no longer imports ServiceIndicator from './toggle.js'",
        )

    def test_harness_refuses_a_source_with_no_relative_import(self):
        # The staleness guard must actually fire, or a future rewrite that
        # silently matches nothing would leave the real ServiceIndicator
        # unstubbed and the tests asserting against gnome-shell internals.
        result = self.run_scenario(
            "rewriteRelative",
            source="import * as Main from 'resource:///org/gnome/shell/ui/main.js'\n",
        )
        self.assertTrue(result["threw"])
        self.assertIn("harness rewrite is stale", result["message"])

    def test_harness_binds_a_relative_import_from_the_stub_namespace(self):
        result = self.run_scenario(
            "rewriteRelative", source="import { ServiceIndicator } from './toggle.js'\n"
        )
        self.assertFalse(result["threw"])
        self.assertEqual(
            result["rewritten"].strip(),
            "const { ServiceIndicator } = globalThis.__syncthingExtensionStubs;",
        )

    def test_harness_rewrites_a_renamed_relative_import_as_destructuring(self):
        # `{ a as b }` is import syntax; as a destructuring binding it has to
        # become `{ a: b }` or node raises a SyntaxError -- the same drift
        # gnome_module_loader.mjs exists to stop repeating per harness.
        result = self.run_scenario(
            "rewriteRelative",
            source="import { ServiceIndicator as Indicator } from './toggle.js'\n",
        )
        self.assertFalse(result["threw"])
        self.assertEqual(
            result["rewritten"].strip(),
            "const { ServiceIndicator: Indicator } = globalThis.__syncthingExtensionStubs;",
        )


if __name__ == "__main__":
    unittest.main()
