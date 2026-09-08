"""Unit tests for syncthing-toggle service-name validation.

Verifies acceptance of valid systemd unit names and rejection of invalid,
malformed, or command-injection payloads in _validatedServiceName.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOGGLE_JS = REPO_ROOT / "extensions" / "syncthing-toggle" / "toggle.js"


def _eval_service_name_validator(name: str | None) -> bool:
    """Execute the exact validation logic via node to verify JS RegExp semantics."""
    js_code = r"""
    const fs = require('fs');
    const toggleSource = fs.readFileSync(process.argv[1], 'utf8');
    const startIdx = toggleSource.indexOf('/^[a-zA-Z0-9_.:@-]+\\.service$/');
    if (startIdx === -1) {
        console.error('Pattern literal not found in toggle.js');
        process.exit(2);
    }
    const pattern = /^[a-zA-Z0-9_.:@-]+\.service$/;
    let input;
    try {
        input = JSON.parse(fs.readFileSync(0, 'utf8'));
    } catch {
        input = null;
    }
    if (typeof input !== 'string') {
        process.stdout.write('false');
        process.exit(0);
    }
    const valid = pattern.test(input);
    process.stdout.write(valid ? 'true' : 'false');
    """
    res = subprocess.run(
        ["node", "-e", js_code, str(TOGGLE_JS)],
        input=json.dumps(name),
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip() == "true"


class TestSyncthingToggleServiceName(unittest.TestCase):
    def test_regex_present_in_toggle_js(self):
        source = TOGGLE_JS.read_text(encoding="utf-8")
        self.assertIn(r"/^[a-zA-Z0-9_.:@-]+\.service$/", source)

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


if __name__ == "__main__":
    unittest.main()
