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

REPO_ROOT = Path(__file__).resolve().parent.parent
TOGGLE_JS = REPO_ROOT / "extensions" / "syncthing-toggle" / "toggle.js"
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

    def test_regex_present_in_toggle_js(self):
        # SECURITY.md quotes this pattern; weakening it silently would let an
        # option-like service-name through. Pinned deliberately.
        source = TOGGLE_JS.read_text(encoding="utf-8")
        self.assertIn(r"/^[a-zA-Z0-9_][a-zA-Z0-9_.:@-]*\.service$/", source)

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
            # Leading dash: would be parsed by systemctl as an option, not a
            # unit name (option injection via dconf-writable free-text key).
            "-syncthing.service",
            "--system.service",
            "--global.service",
        ]
        for name in invalid_cases:
            with self.subTest(name=name):
                self.assertFalse(
                    _eval_service_name_validator(name),
                    f"Expected invalid service name {name!r} to be rejected",
                )


if __name__ == "__main__":
    unittest.main()
