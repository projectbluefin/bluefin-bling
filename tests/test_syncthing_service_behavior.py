"""Exercise the shipped GJS deployment helper in isolated XDG homes."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "extensions" / "syncthing-toggle" / "service.js"
GJS = shutil.which("gjs")
GENERATOR = next((path for path in (
    shutil.which("podman-system-generator"),
    "/usr/lib/systemd/system-generators/podman-system-generator",
    "/usr/libexec/podman/quadlet",
) if path and Path(path).is_file()), None)


def systemd_arguments(command):
    """Decode systemd quoting/C escapes, keeping literal Unicode as UTF-8."""
    arguments, word = [], bytearray()
    quote = None
    index = 0
    escapes = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "s": 32}
    while index < len(command):
        char = command[index]
        if char == "\\":
            index += 1
            escaped = command[index]
            if escaped in ("x", "u", "U"):
                count = {"x": 2, "u": 4, "U": 8}[escaped]
                digits = command[index + 1:index + 1 + count]
                if len(digits) != count or not re.fullmatch(r"[0-9a-fA-F]+", digits):
                    raise ValueError("Invalid systemd hexadecimal escape")
                value = int(digits, 16)
                word.extend(bytes([value]) if escaped == "x" else chr(value).encode("utf-8"))
                index += count
            elif escaped in escapes:
                word.append(escapes[escaped])
            elif escaped in ("\\", '"', "'"):
                word.extend(escaped.encode("utf-8"))
            else:
                raise ValueError("Unexpected systemd argument escape")
        elif char in ('"', "'") and quote in (None, char):
            quote = char if quote is None else None
        elif char.isspace() and quote is None:
            if word:
                arguments.append(word.decode("utf-8").replace("%%", "%"))
                word.clear()
        else:
            word.extend(char.encode("utf-8"))
        index += 1
    if quote is not None:
        raise ValueError("Unclosed systemd argument quote")
    if word:
        arguments.append(word.decode("utf-8").replace("%%", "%"))
    return arguments


@unittest.skipUnless(GJS, "real GJS is required")
class SyncthingServiceBehavior(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "home"
        self.home.mkdir(mode=0o700)
        self.config = self.home / "config % café"
        self.state = self.home / "state % café"
        self.config.mkdir(mode=0o700)
        self.state.mkdir(mode=0o700)
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.config),
            "XDG_STATE_HOME": str(self.state),
            "XDG_DATA_HOME": str(self.home / "data"),
            "XDG_CACHE_HOME": str(self.home / "cache"),
        }
        self.state_dir = self.state / "syncthing"
        self.quadlet_dir = self.config / "containers" / "systemd"
        self.quadlet = self.quadlet_dir / "syncthing.container"
        self.set_directories({})

    def set_directories(self, paths):
        kinds = ("DOCUMENTS", "DESKTOP", "DOWNLOAD", "MUSIC", "PICTURES", "VIDEOS", "TEMPLATES", "PUBLICSHARE")
        lines = []
        for kind in kinds:
            value = str(paths.get(kind, self.home)).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'XDG_{kind}_DIR="{value}"')
        (self.config / "user-dirs.dirs").write_text("\n".join(lines) + "\n")

    def invoke(self, action="prepare", expected_success=True, **kwargs):
        request = {"action": action, "port": 18384, "serviceName": "syncthing.service", **kwargs}
        result = subprocess.run(
            [GJS, "-m", str(HELPER), json.dumps(request)],
            env=self.env, capture_output=True, text=True, timeout=30,
        )
        if expected_success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        return result

    def metadata(self):
        return json.loads((self.state_dir / "desktop.json").read_text())

    def test_localized_paths_permissions_and_persistent_identity(self):
        documents = self.home / 'Mes documents % & <été> "photos"'
        downloads = self.home / "Téléchargements"
        documents.mkdir()
        downloads.mkdir()
        self.set_directories({"DOCUMENTS": documents, "DOWNLOAD": downloads, "MUSIC": downloads})
        # Seed managed state before preserving its engine-owned identity files.
        self.invoke()
        identity = {"config.xml": b"<configuration>native identity</configuration>\n",
                    "cert.pem": b"existing certificate\n", "key.pem": b"existing private identity\n"}
        for name, data in identity.items():
            (self.state_dir / name).write_bytes(data)
        response = self.invoke()
        self.assertEqual(response["stateDir"], str(self.state_dir))
        self.assertEqual(response["envFile"], str(self.state_dir / "container.env"))
        self.assertTrue(response["documentsAvailable"])
        self.assertFalse(response["provisioned"])
        self.assertEqual(response["folders"], [
            {"id": "documents", "label": "Documents", "path": str(documents), "paused": False},
            {"id": "downloads", "label": "Downloads", "path": str(downloads), "paused": True},
        ])
        credentials = Path(response["envFile"]).read_text()
        key = credentials.splitlines()[0].split("=", 1)[1]
        self.assertRegex(key, r"^[0-9a-f]{64}$")
        self.assertNotIn(key, json.dumps(response))
        self.invoke()
        self.assertEqual(Path(response["envFile"]).read_text(), credentials)
        for name, data in identity.items():
            self.assertEqual((self.state_dir / name).read_bytes(), data)
        self.assertEqual(stat.S_IMODE(self.state_dir.stat().st_mode), 0o700)
        for path in (Path(response["envFile"]), self.state_dir / "desktop.json", self.quadlet):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_missing_disabled_and_home_aliases_do_not_create_defaults(self):
        alias = self.home / "home-alias"
        alias.symlink_to(self.home, target_is_directory=True)
        self.set_directories({"DOCUMENTS": self.home / "missing docs", "DOWNLOAD": alias})
        response = self.invoke()
        self.assertFalse(response["documentsAvailable"])
        self.assertEqual(response["folders"], [])
        self.assertFalse((self.home / "Documents").exists())
        self.assertFalse((self.home / "missing docs").exists())

    def test_metadata_autostart_and_migration_survive_prepare(self):
        custom = self.home / "legacy sync"
        custom.mkdir()
        self.invoke(extraPaths=[str(custom), str(custom)])
        self.invoke("autostart", enabled=True)
        self.invoke("provisioned")
        metadata = self.metadata()
        metadata["userChoices"] = {"documentsDeleted": True, "label": "My folder", "paused": True}
        (self.state_dir / "desktop.json").write_text(json.dumps(metadata))
        response = self.invoke()
        self.assertTrue(response["provisioned"])
        self.assertEqual(self.metadata(), metadata)
        self.invoke("autostart", enabled=False)
        self.invoke()
        self.assertFalse(self.metadata()["autostart"])
        self.assertEqual(self.metadata()["extraPaths"], [str(custom)])
        self.assertEqual(self.metadata()["userChoices"], metadata["userChoices"])

    def test_restart_changes_remain_pending_until_successful_provisioning(self):
        first = self.invoke()
        self.assertTrue(first["restartRequired"])
        self.assertFalse(first["nativeStartRequired"])
        credentials = Path(first["envFile"]).read_text()
        changed = self.invoke(port=19384)
        self.assertTrue(changed["restartRequired"])
        self.assertTrue(self.invoke(port=19384)["restartRequired"])
        self.assertEqual(Path(first["envFile"]).read_text().splitlines()[0], credentials.splitlines()[0])
        provisioned = self.invoke("provisioned", port=19384)
        self.assertFalse(provisioned["restartRequired"])
        self.assertFalse(provisioned["nativeStartRequired"])
        self.assertFalse(self.invoke(port=19384)["restartRequired"])

    def test_captured_legacy_paths_survive_explicit_replacement_and_missing_folder(self):
        legacy = self.home / 'Legacy %% "été" sync'
        explicit = self.home / "manual mount"
        legacy.mkdir()
        explicit.mkdir()
        self.invoke(extraPaths=[str(legacy), str(explicit)])
        metadata = self.metadata()
        metadata.update(legacyCaptured=True, legacyPaths=[str(legacy)], restartRequired=True)
        (self.state_dir / "desktop.json").write_text(json.dumps(metadata))
        response = self.invoke(extraPaths=[])
        self.assertTrue(response["restartRequired"])
        self.assertEqual(self.metadata()["extraPaths"], [str(legacy)])
        self.assertIn(f'Volume={str(legacy).replace("%", "%%")}:{str(legacy).replace("%", "%%")}:rw', self.quadlet.read_text())
        self.assertNotIn(str(explicit), self.quadlet.read_text())
        self.invoke("provisioned")
        self.assertFalse(self.invoke()["restartRequired"])
        before = {path: path.read_bytes() for path in (
            self.quadlet, self.state_dir / "container.env", self.state_dir / "desktop.json")}
        legacy.rmdir()
        self.invoke(expected_success=False)
        for path, contents in before.items():
            self.assertEqual(path.read_bytes(), contents)

    def test_uninspectable_first_use_preserves_state_without_managed_files(self):
        self.state_dir.mkdir(mode=0o700)
        config = self.state_dir / "config.xml"
        original = b"engine-owned configuration; never parsed by the helper\n"
        config.write_bytes(original)
        result = self.invoke(expected_success=False, serviceName="uninspectable-syncthing-fixture.service",
                             waitForNative=True)
        self.assertIn("restore the packaged syncthing.service", result.stderr)
        self.assertNotIn(original.decode().strip(), result.stderr)
        self.assertEqual(config.read_bytes(), original)
        self.assertFalse((self.state_dir / "container.env").exists())
        self.assertFalse((self.state_dir / "desktop.json").exists())
        self.assertFalse(self.quadlet.exists())

    def test_invalid_native_wait_request_cannot_modify_existing_state(self):
        self.state_dir.mkdir(mode=0o700)
        config = self.state_dir / "config.xml"
        original = b"engine-owned state\n"
        config.write_bytes(original)
        result = self.invoke(expected_success=False, waitForNative="true")
        self.assertIn("waitForNative requires a boolean value", result.stderr)
        self.assertEqual(config.read_bytes(), original)
        self.assertFalse((self.state_dir / "container.env").exists())
        self.assertFalse((self.state_dir / "desktop.json").exists())
        self.assertFalse(self.quadlet.exists())

    def test_stopped_packaged_native_requests_start_without_managed_files(self):
        # Read-only observation: never install/start a native service from this suite.
        status = subprocess.run(
            ["/usr/bin/systemctl", "--user", "show", "syncthing.service",
             "--property=LoadState", "--property=FragmentPath", "--property=DropInPaths",
             "--property=ActiveState"],
            env=self.env, capture_output=True, text=True, timeout=10,
        )
        properties = dict(line.split("=", 1) for line in status.stdout.splitlines() if "=" in line)
        fragment = properties.get("FragmentPath", "")
        if (status.returncode or properties.get("LoadState") != "loaded" or
                properties.get("DropInPaths") != "" or
                properties.get("ActiveState") not in ("inactive", "failed") or
                fragment not in ("/usr/lib/systemd/user/syncthing.service", "/lib/systemd/user/syncthing.service")):
            self.skipTest("an inactive, unmodified packaged native unit is required")
        self.state_dir.mkdir(mode=0o700)
        identity = {"config.xml": b"opaque native state; no XML parsing\n",
                    "cert.pem": b"native certificate\n", "key.pem": b"native private identity\n"}
        for name, contents in identity.items():
            (self.state_dir / name).write_bytes(contents)
        response = self.invoke()
        self.assertTrue(response["nativeStartRequired"])
        self.assertTrue(response["restartRequired"])
        self.assertFalse(response["provisioned"])
        self.assertEqual(response["envFile"], str(self.state_dir / "container.env"))
        self.assertFalse((self.state_dir / "container.env").exists())
        self.assertFalse((self.state_dir / "desktop.json").exists())
        self.assertFalse(self.quadlet.exists())
        for name, contents in identity.items():
            self.assertEqual((self.state_dir / name).read_bytes(), contents)

    def test_stopped_native_off_removes_only_legacy_link_without_managed_state(self):
        self.state_dir.mkdir(mode=0o700)
        original = b"engine-owned configuration; preserve exactly on explicit off\n"
        config = self.state_dir / "config.xml"
        config.write_bytes(original)
        wants = self.config / "systemd" / "user" / "default.target.wants"
        wants.mkdir(parents=True, mode=0o700)
        link = wants / "syncthing.service"
        link.symlink_to("/usr/lib/systemd/user/syncthing.service")
        unrelated = wants / "other.service"
        unrelated.symlink_to("/usr/lib/systemd/user/other.service")
        for _ in range(2):
            response = self.invoke("autostart", enabled=False)
            self.assertFalse(response["nativeStartRequired"])
            self.assertFalse(response["restartRequired"])
            self.assertFalse(link.is_symlink())
            self.assertEqual(str(unrelated.readlink()), "/usr/lib/systemd/user/other.service")
            self.assertEqual(config.read_bytes(), original)
            self.assertFalse((self.state_dir / "container.env").exists())
            self.assertFalse((self.state_dir / "desktop.json").exists())
            self.assertFalse(self.quadlet.exists())

    def test_off_does_not_remove_custom_service_link_to_packaged_native_unit(self):
        self.state_dir.mkdir(mode=0o700)
        config = self.state_dir / "config.xml"
        original = b"opaque uninspectable configuration\n"
        config.write_bytes(original)
        wants = self.config / "systemd" / "user" / "default.target.wants"
        wants.mkdir(parents=True, mode=0o700)
        link = wants / "uninspectable-syncthing-fixture.service"
        link.symlink_to("/usr/lib/systemd/user/syncthing.service")
        self.invoke("autostart", enabled=False, serviceName=link.name)
        self.assertEqual(str(link.readlink()), "/usr/lib/systemd/user/syncthing.service")
        self.invoke(expected_success=False, serviceName=link.name)
        self.assertEqual(config.read_bytes(), original)
        self.assertTrue(link.is_symlink())
        self.assertFalse((self.state_dir / "container.env").exists())
        self.assertFalse((self.state_dir / "desktop.json").exists())
        self.assertEqual(list(self.quadlet_dir.iterdir()), [])

    def test_off_refuses_unsafe_legacy_link_ancestors_without_mutation(self):
        self.state_dir.mkdir(mode=0o700)
        config = self.state_dir / "config.xml"
        original = b"engine-owned state\n"
        config.write_bytes(original)
        outside = self.home / "outside-wants"
        outside.mkdir(mode=0o700)
        link = outside / "syncthing.service"
        link.symlink_to("/usr/lib/systemd/user/syncthing.service")
        user_units = self.config / "systemd" / "user"
        user_units.mkdir(parents=True, mode=0o700)
        (user_units / "default.target.wants").symlink_to(outside, target_is_directory=True)
        self.invoke("autostart", enabled=False, expected_success=False)
        self.assertEqual(str(link.readlink()), "/usr/lib/systemd/user/syncthing.service")
        self.assertEqual(config.read_bytes(), original)
        self.assertFalse((self.state_dir / "container.env").exists())
        self.assertFalse((self.state_dir / "desktop.json").exists())
        self.assertFalse(self.quadlet.exists())

    def test_native_autostart_link_imports_then_can_be_disabled(self):
        wants = self.config / "systemd" / "user" / "default.target.wants"
        wants.mkdir(parents=True, mode=0o700)
        link = wants / "syncthing.service"
        link.symlink_to("/usr/lib/systemd/user/syncthing.service")
        self.invoke()
        self.assertFalse(link.is_symlink())
        self.assertTrue(self.metadata()["autostart"])
        self.assertIn("WantedBy=default.target", self.quadlet.read_text())
        self.invoke()
        self.assertTrue(self.metadata()["autostart"])
        self.invoke("autostart", enabled=False)
        self.invoke()
        self.assertFalse(self.metadata()["autostart"])
        self.assertNotIn("WantedBy=default.target", self.quadlet.read_text())
        self.assertFalse(link.is_symlink())

    def test_explicit_autostart_off_overrides_import_without_touching_other_links(self):
        wants = self.config / "systemd" / "user" / "default.target.wants"
        wants.mkdir(parents=True, mode=0o700)
        link = wants / "syncthing.service"
        link.symlink_to("/usr/lib/systemd/user/syncthing.service")
        self.invoke("autostart", enabled=False)
        self.assertFalse(link.is_symlink())
        self.assertFalse(self.metadata()["autostart"])
        unrelated = self.config / "systemd" / "user" / "custom-syncthing.service"
        link.symlink_to(unrelated)
        self.invoke()
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.readlink(), unrelated)
        self.assertFalse(self.metadata()["autostart"])

    def test_failed_migration_keeps_native_autostart_link(self):
        wants = self.config / "systemd" / "user" / "default.target.wants"
        wants.mkdir(parents=True, mode=0o700)
        link = wants / "syncthing.service"
        link.symlink_to("/usr/lib/systemd/user/syncthing.service")
        self.state_dir.mkdir(mode=0o700)
        (self.state_dir / "config.xml").write_bytes(b"engine-owned configuration\n")
        self.quadlet_dir.mkdir(parents=True, mode=0o700)
        unrelated = b"[Container]\nImage=unrelated.example/image:latest\n"
        self.quadlet.write_bytes(unrelated)
        self.invoke(expected_success=False)
        self.assertTrue(link.is_symlink())
        self.assertEqual(str(link.readlink()), "/usr/lib/systemd/user/syncthing.service")
        self.assertEqual(self.quadlet.read_bytes(), unrelated)
        self.assertFalse((self.state_dir / "desktop.json").exists())
        self.assertFalse((self.state_dir / "container.env").exists())

    def test_unrelated_quadlet_is_not_overwritten(self):
        self.quadlet_dir.mkdir(parents=True, mode=0o700)
        original = b"[Container]\nImage=unrelated.example/image:latest\n"
        self.quadlet.write_bytes(original)
        self.invoke(expected_success=False)
        self.assertEqual(self.quadlet.read_bytes(), original)
        self.assertFalse((self.state_dir / "container.env").exists())

    def test_private_paths_refuse_symlinks_and_hardlinks(self):
        outside = self.home / "outside"
        outside.mkdir(mode=0o755)
        self.state_dir.symlink_to(outside, target_is_directory=True)
        self.invoke(expected_success=False)
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o755)
        self.assertEqual(list(outside.iterdir()), [])
        self.state_dir.unlink()
        self.state_dir.mkdir(mode=0o700)
        victim = outside / "credential"
        victim.write_text("do not touch\n")
        env_file = self.state_dir / "container.env"
        env_file.symlink_to(victim)
        self.invoke(expected_success=False)
        self.assertEqual(victim.read_text(), "do not touch\n")
        env_file.unlink()
        os.link(victim, env_file)
        self.invoke(expected_success=False)
        self.assertEqual(victim.read_text(), "do not touch\n")

    @unittest.skipUnless(GENERATOR, "real Quadlet generator is required")
    def test_generator_produces_rootless_mounts_and_autostart(self):
        documents = self.home / 'Documents %% <é> "space"'
        documents.mkdir()
        custom = self.home / "Legacy & sync"
        custom.mkdir()
        self.set_directories({"DOCUMENTS": documents})
        response = self.invoke(extraPaths=[str(custom)])
        for enabled in (True, False):
            self.invoke("autostart", enabled=enabled)
            self.invoke()
            result = subprocess.run(
                [GENERATOR, "--user", "--dryrun"],
                env={**self.env, "QUADLET_UNIT_DIRS": str(self.quadlet_dir)},
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("syncthing.service", result.stdout)
            start = next(line for line in result.stdout.splitlines() if line.startswith("ExecStart="))
            args = systemd_arguments(start.removeprefix("ExecStart="))
            def option(flag):
                return args[args.index(flag) + 1]
            self.assertEqual(option("--network"), "host")
            self.assertEqual(option("--userns"), "keep-id")
            self.assertEqual(option("--user"), f"{os.getuid()}:{os.getgid()}")
            self.assertEqual(option("--env-file"), response["envFile"])
            volumes = [args[index + 1] for index, value in enumerate(args) if value in ("-v", "--volume")]
            self.assertEqual(set(volumes), {
                f"{self.state_dir}:/var/syncthing/config:rw",
                f"{documents}:{documents}:rw", f"{custom}:{custom}:rw",
            })
            self.assertIn("ghcr.io/syncthing/syncthing:2.1.5", args)
            self.assertIn("label=disable", args)
            self.assertNotIn(f"{self.home}:{self.home}:rw", volumes)
            key = Path(response["envFile"]).read_text().splitlines()[0].split("=", 1)[1]
            self.assertNotIn(key, result.stdout)
            self.assertEqual("WantedBy=default.target" in result.stdout, enabled)


if __name__ == "__main__":
    unittest.main()
