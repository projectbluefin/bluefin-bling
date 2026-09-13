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


# The syncthing-toggle prefs.js binds the port Gtk.SpinButton straight to the
# port GSettings key, so the Gtk.Adjustment bounds and the gschema <range> must
# agree: anything the UI offers that the schema rejects is a silent write
# failure. These two regexes read the adjustment bounds back out of prefs.js so
# the test suite can compare them against the schema.
_PORT_ADJUSTMENT_RE = re.compile(
    r"new\s+Gtk\.Adjustment\s*\(\s*\{(?P<body>.*?)\}\s*\)",
    re.DOTALL,
)
_ADJUSTMENT_BOUNDS_RE = re.compile(r"\b(lower|upper)\b\s*:\s*(\d+)\s*,?")


def port_adjustment_bounds(prefs_source: str) -> tuple[int, int]:
    """Return the (lower, upper) bounds of the Gtk.Adjustment in a prefs.js.

    Raises ValueError if there is no Gtk.Adjustment or it is missing either
    bound, so callers can assert the two agree with the gschema <range>.
    """
    match = _PORT_ADJUSTMENT_RE.search(prefs_source)
    if match is None:
        raise ValueError("no Gtk.Adjustment found")
    bounds = {
        kind: int(value)
        for kind, value in _ADJUSTMENT_BOUNDS_RE.findall(match.group("body"))
    }
    if "lower" not in bounds or "upper" not in bounds:
        raise ValueError(f"Gtk.Adjustment missing a bound: {bounds}")
    return bounds["lower"], bounds["upper"]


# A GJS source applies CSS by name (`actor.add_style_class_name('foo')`) and the
# rule that gives the name meaning lives in the extension's stylesheet.css. The
# two sides are joined by a bare string: rename or drop one and GNOME Shell
# reports nothing at all, the styling is simply a silent no-op. These helpers
# read both sides so the suite can assert they still agree.
_STRING_CONST_RE = re.compile(
    r"""\bconst\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(['"])(?P<value>[^'"]*)\2""",
)
# add/set/remove/toggle name a class the extension owns; has_style_class_name is
# excluded because it probes for classes GNOME Shell itself owns ('icon-button').
_STYLE_CLASS_CALL_RE = re.compile(
    r"""(?:add|set|remove|toggle)_style_class_name\s*\(\s*(?P<arg>[^),]+?)\s*[),]""",
)
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_CLASS_RE = re.compile(r"\.(?P<name>-?[_a-zA-Z][\w-]*)")


def stylesheet_files(ext_dir: Path) -> list[Path]:
    """Return every CSS stylesheet shipped by an extension."""
    return sorted(ext_dir.rglob("*.css"))


def applied_style_classes(source: str) -> set[str]:
    """Return CSS class names a GJS source owns and applies to actors.

    Handles a string literal argument and a module-level ``const NAME = 'class'``
    passed by identifier. An argument this helper cannot resolve statically (a
    parameter, a template literal, a property) is skipped rather than guessed at.
    """
    constants = {
        m.group("name"): m.group("value") for m in _STRING_CONST_RE.finditer(source)
    }
    classes: set[str] = set()
    for match in _STYLE_CLASS_CALL_RE.finditer(source):
        arg = match.group("arg").strip()
        if len(arg) >= 2 and arg[0] in "'\"" and arg[-1] == arg[0]:
            value = arg[1:-1]
        elif arg in constants:
            value = constants[arg]
        else:
            continue
        # set_style_class_name() takes a space-separated list.
        classes.update(name for name in value.split() if name)
    return classes


def css_class_names(stylesheet_source: str) -> set[str]:
    """Return every class name a stylesheet defines a rule for."""
    without_comments = _CSS_COMMENT_RE.sub(" ", stylesheet_source)
    return {m.group("name") for m in _CSS_CLASS_RE.finditer(without_comments)}
