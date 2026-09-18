---
name: extension-validation
description: >-
  How bluefin-bling validates extensions automatically. Covers the discovery-based
  unittest suite under tests/, the invariants it enforces on metadata.json,
  GSettings schemas and GJS sources, and how to extend it when adding a new
  extension or a new class of regression.
metadata:
  context7-sources:
    - /git_gitlab_gnome_org/gnome_gnome-shell
    - /websites/gjs-docs_gnome
---

# Extension Validation

## Run it

```bash
python3 -m unittest discover -s tests -t tests -v
```

Standard library only — no `pip install`, no `package.json`. `node` is used via
`node --check` and the syntax test skips itself if `node` is absent.

Pull requests and pushes to `main` are automatically validated by CI
(`.github/workflows/ci.yml`). Before opening a PR, run this locally:
Also compile the schemas:

```bash
glib-compile-schemas --strict --dry-run extensions/syncthing-toggle/schemas/
```

## The rule that matters: discovery, not lists

`tests/extension_manifest.py` walks `extensions/` at call time. Nothing is
hardcoded. **Adding a new extension folder automatically subjects it to every
test — do not add it to a list, there is no list.**

This exists because the manual command block in `AGENTS.md` names files
one-by-one, so any file a new extension adds was silently never checked.

## Invariants enforced

`tests/test_extension_metadata.py`
- `metadata.json` exists, parses, and carries `uuid`, `name`, `description`, `shell-version`
- `uuid` is exactly `<folder-name>@projectbluefin.io` — GNOME Shell refuses to load
  an extension whose uuid does not match its install directory, and reports it as
  simply missing
- `uuid` is unique across the monorepo
- `shell-version` is an ascending, deduplicated list of numeric strings, minimum 45
  (the first ESM-only release)
- `version` is an integer, not a string; `url` points at this repo
- `settings-schema` is declared **exactly when** `schemas/*.gschema.xml` ships

`tests/test_extension_schemas.py`
- schema XML is well-formed, and the schema `id` matches metadata `settings-schema`
  (a mismatch makes `getSettings()` abort the extension at enable time)
- schema `path` is the `id` with dots as slashes, leading and trailing `/`
- every key has a type, a `<default>` and a `<summary>`, and a kebab-case name
- every key read or bound from a GJS source exists in the schema — `Gio.Settings`
  aborts the whole `gnome-shell` process on an unknown key
- every declared key is actually used by some source (no dead settings)

`tests/test_extension_sources.py`
- every `.js` under `extensions/` passes `node --check`
- sources are ESM and never CommonJS (`require()` / `module.exports` cannot load in GJS)
- `extension.js` default-exports a class extending `Extension` and defines both
  `enable()` and `disable()`; `prefs.js` default-exports an `ExtensionPreferences`
  subclass
- teardown: a `GLib.timeout_add*` implies `GLib.Source.remove`; a `Gio.Cancellable`
  is cancelled in `disable()`; a `Gio.FileMonitor` is both disconnected and cancelled
  in `disable()`; `disable()` is never an empty body

`tests/test_extension_styles.py`
- every CSS class an extension applies to an actor (`add_/set_/remove_/toggle_style_class_name`
  with a string literal or a `const NAME = 'class'` identifier) has a rule in **every
  stylesheet GNOME Shell could load for that extension**, checked sheet by sheet
  rather than across their union. Shell loads at most one sheet per extension —
  `${sessionMode}-${variant}.css`, `stylesheet-${variant}.css`, `${sessionMode}.css`,
  `stylesheet.css`, each looked up in the extension root only, first hit wins — so a
  rule that lives only in `stylesheet-dark.css`, or in a `css/` subdirectory, is a rule
  nobody on the other side of that choice ever gets. The JS side and the CSS side are
  joined only by a bare string, so drifting either one makes the styling a silent
  no-op — GNOME Shell reports nothing at all
- an extension that applies style classes ships a stylesheet Shell will actually load
- the gate matches something: if no extension in the repo yields a statically
  resolvable class name, both assertions above pass vacuously, so that state is itself
  a failure. A drift gate that has gone blind must not read as green
- `has_style_class_name()` is deliberately *not* counted: it reads back a class
  somebody else already set, so it obliges the extension to define nothing. Class
  names GNOME Shell itself owns (`popup-menu-item`, `icon-button`, …) are dropped for
  the same reason — applying one is how an extension adopts the platform look, and is
  exactly when it must *not* ship a competing rule. That short frozenset in
  `tests/extension_manifest.py` is a denylist of *upstream's* names keyed to a
  gnome-shell release; it never grows when a folder is added under `extensions/`, so
  the discovery principle stands
- JS comments are stripped before extraction, so retiring a class in the natural order
  — comment out the call, then delete the rule — does not red the suite. The stripper
  is a small scanner that skips string literals, so the `//` in `'http://example.com'`
  does not eat the rest of the line
- an argument that cannot be resolved statically (a parameter, a template literal) is
  skipped rather than guessed at — a class name that only ever reaches the actor
  through a parameter is invisible to this gate, so keep the names in constants.
  `applied_style_classes()` resolves a `const NAME = 'class'` binding textually and
  does not model JS scope: a function-scoped constant resolves exactly like a
  module-level one, and a shadowed name resolves to whichever binding was seen last
- only selector text is scanned on the CSS side; declaration blocks are removed first,
  so `url(icons/foo.svg)` and `content: ".fake-class"` cannot smuggle names into the
  declared set and excuse a rule that is genuinely missing

## Extending the suite

Add assertions to the existing test classes rather than new hardcoded checks.
A new invariant should be expressed over `extension_dirs()` so it applies to every
extension, present and future.

Detecting a GSettings key reference is regex-based
(`referenced_settings_keys()` in `tests/extension_manifest.py`). It understands
`settings.get_*('key')`, `settings.set_*('key')` and `settings.bind('key', …)`.
If a source starts reaching settings through some other shape, teach that helper
the new shape — otherwise the unknown-key test goes quietly blind.

## Verification

Two of the style-gate invariants are claims about someone else's code, so they are
only as good as the release they were read from. They were derived against
gnome-shell **48.0**; re-derive them before trusting them against a newer Shell.

Which stylesheets Shell loads, and that it never recurses:

```bash
curl -fsSL https://gitlab.gnome.org/GNOME/gnome-shell/-/raw/48.0/js/ui/extensionSystem.js \
  | sed -n '/_loadExtensionStylesheet(extension)/,/^    }/p'
```

Expect the four candidate names and a single `extension.dir.get_child(name)` lookup
that `break`s on the first sheet that loads. `get_child()` takes a name, not a path,
so nothing under a subdirectory is ever reachable.

The class names gnome-shell sets on its own widgets, which
`_SHELL_OWNED_STYLE_CLASSES` in `tests/extension_manifest.py` must not outlive:

```bash
for f in popupMenu panelMenu quickSettings; do
  curl -fsSL "https://gitlab.gnome.org/GNOME/gnome-shell/-/raw/48.0/js/ui/$f.js" \
    | grep -n "style_class: '\|add_style_class_name('"
done
```

Drop an entry that upstream no longer sets: while it sits in the frozenset, a real
missing rule for that name goes unreported.

The extraction helpers are pinned by unit tests rather than by prose — run them
directly after touching either regex or the comment stripper:

```bash
python3 -m unittest discover -s tests -t tests -v -p 'test_extension_styles.py'
```
