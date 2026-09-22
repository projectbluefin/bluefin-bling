#!/usr/bin/env python3
"""Resolve the latest GNOME stable and nightly OCI image digests dynamically.

Queries https://release.gnome.org/atom.xml for the authoritative stable major release,
then queries quay.io/gnome_infrastructure/gnome-build-meta to pin immutable digests.
Outputs a matrix JSON suitable for GitHub Actions strategy matrix.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

ATOM_FEED_URL = "https://release.gnome.org/atom.xml"
QUAY_API_BASE = "https://quay.io/api/v1/repository/gnome_infrastructure/gnome-build-meta/tag"
REGISTRY_IMAGE_PREFIX = "quay.io/gnome_infrastructure/gnome-build-meta"


def fetch_url(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "bluefin-bling-compat/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8")


def discover_latest_stable_major() -> str:
    xml_data = fetch_url(ATOM_FEED_URL)
    root = ET.fromstring(xml_data)
    # Namespaces in Atom: {http://www.w3.org/2005/Atom}
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    
    for entry in root.findall("atom:entry", ns):
        title_elem = entry.find("atom:title", ns)
        if title_elem is not None and title_elem.text:
            match = re.search(r"Introducing GNOME\s+(\d+)", title_elem.text, re.IGNORECASE)
            if match:
                return match.group(1)

    raise RuntimeError(f"Could not extract latest stable major GNOME release from {ATOM_FEED_URL}")


def resolve_tag_digest(tag: str) -> str:
    url = f"{QUAY_API_BASE}/?onlyActiveTags=true&specificTag={tag}"
    data = json.loads(fetch_url(url))
    tags = data.get("tags", [])
    if not tags:
        raise RuntimeError(f"No active tag found on Quay for {tag}")
    digest = tags[0].get("manifest_digest")
    if not digest:
        raise RuntimeError(f"No manifest_digest found for tag {tag}")
    return digest


def main() -> None:
    try:
        stable_major = discover_latest_stable_major()
    except Exception as exc:
        print(f"Error discovering stable GNOME major: {exc}", file=sys.stderr)
        sys.exit(1)

    stable_tag = f"gnomeos-{stable_major}"
    nightly_tag = "gnomeos-nightly"

    try:
        stable_digest = resolve_tag_digest(stable_tag)
        nightly_digest = resolve_tag_digest(nightly_tag)
    except Exception as exc:
        print(f"Error resolving image digests: {exc}", file=sys.stderr)
        sys.exit(1)

    matrix = {
        "include": [
            {
                "channel": "stable",
                "target_major": stable_major,
                "image_tag": stable_tag,
                "image_digest": f"{REGISTRY_IMAGE_PREFIX}:{stable_tag}@{stable_digest}",
            },
            {
                "channel": "development",
                "target_major": "",
                "image_tag": nightly_tag,
                "image_digest": f"{REGISTRY_IMAGE_PREFIX}:{nightly_tag}@{nightly_digest}",
            },
        ]
    }

    matrix_json = json.dumps(matrix)
    print("Resolved Matrix:")
    print(json.dumps(matrix, indent=2))

    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"matrix={matrix_json}\n")
            f.write(f"stable_major={stable_major}\n")


if __name__ == "__main__":
    main()
