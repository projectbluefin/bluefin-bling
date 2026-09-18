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
# add/set/remove/toggle name a class the source puts on an actor;
# has_style_class_name is excluded because it only probes for a class someone
# else already set, which obliges the extension to define nothing.
_STYLE_CLASS_CALL_RE = re.compile(
    r"""(?:add|set|remove|toggle)_style_class_name\s*\(\s*(?P<arg>[^),]+?)\s*[),]""",
)
# Class names GNOME Shell sets on its own widgets and styles from its own theme.
# Applying one is routine — it is how an extension adopts the platform look —
# and it is exactly the case where the extension must *not* ship a competing
# rule, so demanding a local definition for these would invert the intent. Same
# principle that already excludes has_style_class_name, applied to the other
# call shape. Verified against gnome-shell 48 (re-derive with the commands in
# docs/skills/extension-validation.md):
#   icon-button                 js/ui/quickSettings.js:183,282,336
#   panel-menu                  js/ui/panelMenu.js:126
#   popup-inactive-menu-item    js/ui/popupMenu.js:120
#   popup-menu                  js/ui/popupMenu.js:1021
#   popup-menu-icon             js/ui/popupMenu.js:648,1359
#   popup-menu-item             js/ui/popupMenu.js:89
#   popup-menu-section          js/ui/popupMenu.js:1337
#   popup-ornamented-menu-item  js/ui/popupMenu.js:277
#   popup-submenu-menu-item     js/ui/popupMenu.js:1356
#   quick-settings              js/ui/quickSettings.js:734
#   quick-toggle-has-menu       js/ui/quickSettings.js:169
#   quick-toggle-menu-button    js/ui/quickSettings.js:183
#
# This is a denylist of *GNOME Shell's* names, not a list of this repo's
# extensions. It is keyed to an upstream release, and adding a folder under
# extensions/ never adds an entry to it, so the discovery principle the rest of
# the suite is built on is untouched.
_SHELL_OWNED_STYLE_CLASSES = frozenset(
    {
        "icon-button",
        "panel-menu",
        "popup-inactive-menu-item",
        "popup-menu",
        "popup-menu-icon",
        "popup-menu-item",
        "popup-menu-section",
        "popup-ornamented-menu-item",
        "popup-submenu-menu-item",
        "quick-settings",
        "quick-toggle-has-menu",
        "quick-toggle-menu-button",
    }
)
_JS_STRING_DELIMITERS = "'\"`"
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
# The innermost `{ … }` of a rule is its declaration block: no selector text
# lives there, only property values (`url(icons/foo.svg)`, `content: ".x"`) that
# would otherwise read as class names and silently widen the declared set.
_CSS_DECLARATION_BLOCK_RE = re.compile(r"\{[^{}]*\}")
_CSS_CLASS_RE = re.compile(r"\.(?P<name>-?[_a-zA-Z][\w-]*)")

# GNOME Shell loads at most *one* stylesheet per extension. Verified against
# gnome-shell 48 js/ui/extensionSystem.js `_loadExtensionStylesheet()`: it tries
# `${global.sessionMode}-${variant}.css`, `stylesheet-${variant}.css`,
# `${global.sessionMode}.css` and `stylesheet.css`, each through
# `extension.dir.get_child(name)` — the extension root, never recursively — and
# breaks on the first one that loads. Both `sessionMode` and `variant` are open
# sets, so rather than hardcode a list of variants the four shapes are matched
# structurally: a single undotted stem sitting directly in the extension root. A
# .css nested in a subdirectory matches no shape at all and Shell never reads it.
_STYLESHEET_NAME_RE = re.compile(r"^[^.]+\.css$")


def stylesheet_files(ext_dir: Path) -> list[Path]:
    """Return every stylesheet GNOME Shell could load for an extension.

    Shell picks at most one of these at runtime and which one depends on the
    session mode and the style variant, so a caller asserting a CSS invariant
    has to hold it for each sheet separately rather than for their union.
    """
    return sorted(
        path
        for path in ext_dir.glob("*.css")
        if path.is_file() and _STYLESHEET_NAME_RE.match(path.name)
    )


def strip_js_comments(source: str) -> str:
    """Return *source* with ``//`` and ``/* */`` comments blanked out.

    Commenting out a call site is the first half of retiring a class, so a
    commented-out call must stop demanding a CSS rule. A plain regex would also
    eat the ``//`` inside ``'http://example.com'`` and swallow the rest of the
    line, so this walks the source and skips over ``'``, ``"`` and backtick
    string literals (honouring backslash escapes) before treating a ``/`` as a
    comment. Regular-expression literals are not tracked: GJS sources name CSS
    classes with string literals, never with a regex.
    """
    out: list[str] = []
    index = 0
    end = len(source)
    while index < end:
        char = source[index]
        if char in _JS_STRING_DELIMITERS:
            out.append(char)
            index += 1
            while index < end:
                current = source[index]
                out.append(current)
                index += 1
                if current == "\\" and index < end:
                    out.append(source[index])
                    index += 1
                elif current == char:
                    break
            continue
        if char == "/" and index + 1 < end and source[index + 1] in "/*":
            if source[index + 1] == "/":
                stop = source.find("\n", index)
                # Leave the newline itself in place so lines never run together.
                index = end if stop == -1 else stop
            else:
                stop = source.find("*/", index + 2)
                index = end if stop == -1 else stop + 2
            out.append(" ")
            continue
        out.append(char)
        index += 1
    return "".join(out)


def applied_style_classes(source: str) -> set[str]:
    """Return CSS class names a GJS source applies and must therefore define.

    Resolves a string literal argument and an identifier bound by any
    ``const NAME = 'class'`` in the file. The binding is matched textually, so a
    function-scoped constant resolves exactly like a module-level one — this
    helper does not model JS scope, and a shadowed name resolves to whichever
    binding the regex saw last. An argument it cannot resolve statically (a
    parameter, a template literal, a property) is skipped rather than guessed
    at. Comments are stripped first, and names GNOME Shell owns are dropped:
    neither obliges the extension to ship a rule.
    """
    code = strip_js_comments(source)
    constants = {
        m.group("name"): m.group("value") for m in _STRING_CONST_RE.finditer(code)
    }
    classes: set[str] = set()
    for match in _STYLE_CLASS_CALL_RE.finditer(code):
        arg = match.group("arg").strip()
        if len(arg) >= 2 and arg[0] in "'\"" and arg[-1] == arg[0]:
            value = arg[1:-1]
        elif arg in constants:
            value = constants[arg]
        else:
            continue
        # set_style_class_name() takes a space-separated list.
        classes.update(name for name in value.split() if name)
    return classes - _SHELL_OWNED_STYLE_CLASSES


def css_class_names(stylesheet_source: str) -> set[str]:
    """Return every class name a stylesheet defines a rule for."""
    selectors = _CSS_COMMENT_RE.sub(" ", stylesheet_source)
    # Only selector text can name a class; a declaration block holds values.
    selectors = _CSS_DECLARATION_BLOCK_RE.sub(" ", selectors)
    return {m.group("name") for m in _CSS_CLASS_RE.finditer(selectors)}
