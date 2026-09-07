"""Shared discovery helpers for the bluefin-bling extension test suite.

Every helper here walks ``extensions/`` at call time. Nothing is hardcoded, so a
newly added extension folder is validated by the existing tests without anyone
editing a list.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTENSIONS_DIR = REPO_ROOT / "extensions"

UUID_DOMAIN = "projectbluefin.io"
REPO_URL = "https://github.com/projectbluefin/bluefin-bling"

# GNOME 45 is the first ESM-only release; the repo targets 45+ everywhere.
MIN_SHELL_VERSION = 45

# Matches settings accessors and Gio.Settings.bind() call sites in GJS sources.
_SETTINGS_ACCESS_RE = re.compile(
    r"""(?:_settings|settings)\s*\.\s*
        (?:get|set)_(?:boolean|string|int|uint|double|value|strv|enum|flags)
        \s*\(\s*(['"])(?P<key>[^'"]+)\1""",
    re.VERBOSE,
)
_SETTINGS_BIND_RE = re.compile(
    r"""(?:_settings|settings)\s*\.\s*bind(?:_writable)?\s*\(\s*
        (['"])(?P<key>[^'"]+)\1""",
    re.VERBOSE,
)


def extension_dirs() -> list[Path]:
    """Return every extension folder, sorted by name."""
    if not EXTENSIONS_DIR.is_dir():
        return []
    return sorted(p for p in EXTENSIONS_DIR.iterdir() if p.is_dir())


def metadata_path(ext_dir: Path) -> Path:
    return ext_dir / "metadata.json"


def load_metadata(ext_dir: Path) -> dict:
    return json.loads(metadata_path(ext_dir).read_text(encoding="utf-8"))


def js_sources(ext_dir: Path) -> list[Path]:
    """Return every JavaScript source shipped by an extension."""
    return sorted(ext_dir.rglob("*.js"))


def schema_files(ext_dir: Path) -> list[Path]:
    return sorted((ext_dir / "schemas").glob("*.gschema.xml"))


def parse_schemas(schema_file: Path) -> list[ET.Element]:
    root = ET.parse(schema_file).getroot()
    return list(root.findall("schema"))


def schema_key_names(schema: ET.Element) -> set[str]:
    return {key.get("name") for key in schema.findall("key") if key.get("name")}


def referenced_settings_keys(source: str) -> set[str]:
    """Extract GSettings key names referenced from a GJS source string."""
    keys = {m.group("key") for m in _SETTINGS_ACCESS_RE.finditer(source)}
    keys |= {m.group("key") for m in _SETTINGS_BIND_RE.finditer(source)}
    return keys
