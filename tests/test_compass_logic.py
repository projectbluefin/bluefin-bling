"""Behaviour of the pure modules behind the Compass extensions.

The engine-specific code cannot run in this repo, but the parsers, command
builders and provider resolution are plain modules. These tests execute the
shipped source through `tests/compass_logic.mjs` so the review fixes are
proved by behaviour rather than by reading source text.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest

from extension_manifest import REPO_ROOT

NODE = shutil.which("node")
HARNESS = REPO_ROOT / "tests" / "compass_logic.mjs"


def run_scenario(name: str) -> object:
    env = dict(os.environ, NODE_NO_WARNINGS="1")
    result = subprocess.run(
        [NODE, str(HARNESS), name],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(f"harness {name} failed: {result.stderr}")
    return json.loads(result.stdout)


@unittest.skipIf(NODE is None, "node is not installed")
class TestCompassLogic(unittest.TestCase):
    def test_distrobox_rows_are_parsed_with_hex_ids_and_status_before_image(self):
        containers = run_scenario("distrobox")
        self.assertEqual(len(containers), 2)
        self.assertEqual(containers[0]["id"], "3f2a1b9c7d8e")
        self.assertEqual(containers[0]["name"], "fedora-box")
        self.assertEqual(containers[0]["status"], "Up 2 hours")
        self.assertEqual(
            containers[0]["image"], "quay.io/fedora/fedora-toolbox:41"
        )
        self.assertEqual(containers[0]["state"], "running")
        self.assertEqual(containers[1]["state"], "exited")
        for container in containers:
            self.assertEqual(container["runtime"], "distrobox")

    def test_engine_discovery_parses_tagged_sections_and_builds_a_host_script(self):
        result = run_scenario("discovery-output")
        names = [(c["runtime"], c["name"]) for c in result["containers"]]
        self.assertEqual(
            names, [("distrobox", "fedora-box"), ("podman", "web")]
        )
        script = result["script"]
        self.assertIn("command -v podman", script)
        self.assertIn("podman ps --filter status=running", script)
        self.assertIn("command -v distrobox", script)
        self.assertIn("distrobox list --no-color", script)

    def test_stopped_container_command_is_one_argument_to_the_terminal(self):
        result = run_scenario("stopped-container")
        command = result["command"]
        self.assertTrue(command.startswith("/bin/sh -c '"))
        self.assertIn("podman", command)
        self.assertIn("exec", command)
        self.assertIn("abc123", command)
        # The && must be inside the single-quoted argument, not an outer split.
        self.assertEqual(command.count("&&"), 1)
        self.assertTrue(command.index("'") < command.index("&&") < command.rindex("'"))
        self.assertIn("ghostty -e " + command, result["rendered"])

    def test_cask_only_search_still_returns_the_cask(self):
        result = run_scenario("brew-cask-only")
        self.assertEqual(len(result["index"]["formulae"]), 1)
        self.assertEqual(len(result["index"]["casks"]), 1)
        self.assertEqual(result["results"], [
            {
                "name": "firefox",
                "kind": "cask",
                "description": "Web browser",
            }
        ])

    def test_default_model_applies_only_to_the_default_provider(self):
        result = run_scenario("model-resolution")
        self.assertEqual(result, {
            "openai": "gpt-4o-mini",
            "anthropic": "claude-3-5-haiku-latest",
            "ollama": "llama3.2",
        })


if __name__ == "__main__":
    unittest.main()
