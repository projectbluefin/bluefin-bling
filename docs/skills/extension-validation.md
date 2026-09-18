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

## Extending the suite

Add assertions to the existing test classes rather than new hardcoded checks.
A new invariant should be expressed over `extension_dirs()` so it applies to every
extension, present and future.

Detecting a GSettings key reference is regex-based
(`referenced_settings_keys()` in `tests/extension_manifest.py`). It understands
`settings.get_*('key')`, `settings.set_*('key')` and `settings.bind('key', …)`.
If a source starts reaching settings through some other shape, teach that helper
the new shape — otherwise the unknown-key test goes quietly blind.
