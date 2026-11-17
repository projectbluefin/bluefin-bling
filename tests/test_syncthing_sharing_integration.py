"""Opt-in loopback-only peer test against the Syncthing binary under test.

SYNCTHING_TEST_BINARY=/path/to/syncthing python3 -m unittest discover \
    -s tests -t tests -p test_syncthing_sharing_integration.py -v

Never uses the user's service, credentials or folders. Both child processes are
reaped before their temporary homes are removed, including on failure.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

NODE = shutil.which("node")
BINARY = os.environ.get("SYNCTHING_TEST_BINARY")
HARNESS = Path(__file__).with_name("syncthing_toggle_harness.mjs")


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def eventually(check, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise AssertionError("Disposable peers did not reach the expected state")


@unittest.skipUnless(NODE and BINARY, "set SYNCTHING_TEST_BINARY for real peer tests")
class SyncthingSharingIntegration(unittest.TestCase):
    def test_explicit_share_accept_stop_reconnect_and_reshare(self):
        children = []
        with tempfile.TemporaryDirectory(prefix="bluefin-sharing-") as temporary:
            try:
                nodes = []
                for name in ("a", "b"):
                    home = Path(temporary) / name
                    home.mkdir()
                    config = home / "config"
                    folder = home / "Documents"
                    folder.mkdir()
                    env = {**os.environ, "HOME": str(home), "STNOUPGRADE": "1",
                           "STNOBROWSER": "1", "GOMAXPROCS": "2"}
                    subprocess.run([BINARY, "generate", "--home", str(config), "--no-port-probing"],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   check=True, timeout=15)
                    tree = ET.parse(config / "config.xml")
                    root = tree.getroot()
                    for existing in root.findall("folder"):
                        root.remove(existing)
                    options = root.find("options")
                    for key in ("globalAnnounceEnabled", "localAnnounceEnabled", "relaysEnabled",
                                "natEnabled", "crashReportingEnabled"):
                        options.find(key).text = "false"
                    for address in options.findall("listenAddress"):
                        options.remove(address)
                    listen = port()
                    ET.SubElement(options, "listenAddress").text = f"tcp://127.0.0.1:{listen}"
                    tree.write(config / "config.xml")
                    gui = port()
                    key = secrets.token_hex(24)
                    env.update(STGUIADDRESS=f"http://127.0.0.1:{gui}", STGUIAPIKEY=key)
                    child = subprocess.Popen([BINARY, "serve", "--home", str(config), "--no-restart",
                                              "--no-browser", "--no-upgrade"], env=env,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    children.append(child)
                    nodes.append({"port": gui, "listen": listen, "key": key, "folder": folder,
                                  "child": child, "env": env, "config": config})

                def api(node, method, path, body=None):
                    request = urllib.request.Request(f"http://127.0.0.1:{node['port']}/rest/{path}",
                        method=method, data=None if body is None else json.dumps(body).encode(),
                        headers={"X-API-Key": node["key"], "Content-Type": "application/json"})
                    # No ambient proxy can see even these throwaway credentials.
                    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=2) as response:
                        text = response.read()
                        return json.loads(text) if text.strip() else None

                def policy(node, **choice):
                    result = subprocess.run([NODE, str(HARNESS), "realSharing", json.dumps({"port": node["port"], **choice})],
                        env={**os.environ, "SYNCTHING_TEST_API_KEY": node["key"]},
                        capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    return json.loads(result.stdout)

                a, b = nodes
                for node in nodes:
                    eventually(lambda: api(node, "GET", "system/ping"))
                    node["id"] = api(node, "GET", "system/status")["myID"]
                    folder = api(node, "GET", "config/defaults/folder")
                    folder.update(id="documents", label="Documents", path=str(node["folder"]),
                                  paused=False, devices=[{"deviceID": node["id"]}])
                    api(node, "POST", "config/folders", folder)
                for node, other in ((a, b), (b, a)):
                    api(node, "POST", "config/devices", {"deviceID": other["id"], "name": "Other computer",
                        "addresses": [f"tcp://127.0.0.1:{other['listen']}"], "autoAcceptFolders": False})
                eventually(lambda: api(a, "GET", "system/connections")["connections"].get(b["id"], {}).get("connected"))
                (a["folder"] / "from-a.txt").write_text("a disposable file from a\n")
                (b["folder"] / "from-b.txt").write_text("a disposable file from b\n")
                for node in nodes:
                    api(node, "POST", "db/scan?folder=documents")
                time.sleep(1)
                self.assertFalse((b["folder"] / "from-a.txt").exists())
                self.assertEqual(policy(a)["peers"][0]["shares"][0]["state"], "notShared")
                policy(a, device=b["id"], share=True)
                eventually(lambda: "documents" in api(b, "GET", "cluster/pending/folders"))
                self.assertFalse((b["folder"] / "from-a.txt").exists())
                self.assertEqual(policy(b)["peers"][0]["shares"][0]["state"], "offered")
                policy(b, device=a["id"], share=True)
                eventually(lambda: (b["folder"] / "from-a.txt").exists() and (a["folder"] / "from-b.txt").exists())
                for filename in ("from-a.txt", "from-b.txt"):
                    self.assertEqual(hashlib.sha256((a["folder"] / filename).read_bytes()).hexdigest(),
                                     hashlib.sha256((b["folder"] / filename).read_bytes()).hexdigest())
                eventually(lambda: policy(a)["peers"][0]["shares"][0]["state"] == "sharing")
                policy(a, device=b["id"], share=False)
                (a["folder"] / "after-stop.txt").write_text("must not move until shared again\n")
                api(a, "POST", "db/scan?folder=documents")
                # Reconnect with the other computer still offering Documents.
                api(a, "POST", f"system/pause?device={b['id']}")
                api(a, "POST", f"system/resume?device={b['id']}")
                eventually(lambda: api(a, "GET", "system/connections")["connections"].get(b["id"], {}).get("connected"))
                time.sleep(3)
                stopped = policy(a)
                self.assertEqual(stopped["peers"][0]["shares"][0]["state"], "notShared")
                self.assertEqual(stopped["invitations"], [])
                self.assertFalse((b["folder"] / "after-stop.txt").exists())
                self.assertTrue((b["folder"] / "from-a.txt").exists())
                self.assertIn(b["id"], [device["deviceID"] for device in api(a, "GET", "config/devices")])
                self.assertFalse(api(a, "GET", f"config/devices/{b['id']}")["autoAcceptFolders"])
                # A fresh daemon and a fresh policy reader must retain the stop.
                a["child"].terminate()
                a["child"].wait(timeout=10)
                restarted = subprocess.Popen([BINARY, "serve", "--home", str(a["config"]),
                    "--no-restart", "--no-browser", "--no-upgrade"], env=a["env"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                children.append(restarted)
                eventually(lambda: api(a, "GET", "system/ping"))
                eventually(lambda: api(a, "GET", "system/connections")["connections"].get(b["id"], {}).get("connected"))
                self.assertEqual(policy(a)["peers"][0]["shares"][0]["state"], "notShared")
                self.assertEqual(policy(a)["invitations"], [])
                policy(a, device=b["id"], share=True)
                eventually(lambda: (b["folder"] / "after-stop.txt").exists())
            finally:
                for child in children:
                    child.terminate()
                for child in children:
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)
