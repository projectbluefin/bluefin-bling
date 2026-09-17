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

Standard library only — no `pip install`, no `package.json`. `node` is required
for the parts of the suite that execute JavaScript: `node --check` on every
source, and the behavioural harness below. Both **skip silently when `node` is
absent**, so a green local run on a machine without node has proven far less
than it looks — CI installs node 22 for this reason.

Pull requests and pushes to `main` are automatically validated by CI
(`.github/workflows/ci.yml`). Before opening a PR, run the suite locally and
compile the schemas:

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

## Behavioural harness (`tests/js/`)

The checks above read source text. `tests/js/driver.mjs` instead *runs* the
extension: it loads the real `extension.js` and `toggle.js` under node with
`gi://` and `resource:///org/gnome/shell/…` resolved to the stubs in
`tests/js/stubs/` (see `loader.mjs`), drives the actual `enable()`/`disable()`
lifecycle through a set of scenarios, and prints what the extension did as JSON
on stdout. `tests/test_syncthing_toggle.py` asserts on that JSON.

The stubs record observable effects — spawned argv and the cancellable each
call carried, installed main-loop sources, connected signal handlers,
`Main.notify()` calls, launched URIs, and any file or directory the extension
touched — so tests assert what a user or the system would see, never source
text. Adding a scenario means adding a block to `driver.mjs` that writes one
key into `results`, plus the assertions for it.

Two rules keep these honest:

- **Identify UI by behaviour, not by label.** The Web GUI scenario finds the
  menu entry by invoking every action and seeing which one launches a URI.
  Matching on `'Open Web GUI'` would turn a copy change into a test failure.
- **Mutate the extension to prove a test bites.** Every scenario here was
  checked by breaking `toggle.js` on purpose (hardcoding the GUI port, moving
  it off loopback, deleting the launch call) and confirming the suite failed.

## Extending the suite

Add assertions to the existing test classes rather than new hardcoded checks.
A new invariant should be expressed over `extension_dirs()` so it applies to every
extension, present and future.

Detecting a GSettings key reference is regex-based
(`referenced_settings_keys()` in `tests/extension_manifest.py`). It understands
`settings.get_*('key')`, `settings.set_*('key')` and `settings.bind('key', …)`.
If a source starts reaching settings through some other shape, teach that helper
the new shape — otherwise the unknown-key test goes quietly blind.
