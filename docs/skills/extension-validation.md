---
name: extension-validation
version: "1.1"
last_updated: "2026-09-18"
id: extension-validation
one_line_purpose: Validate extensions and extend the discovery-based test suite.
entry_point: docs/skills/extension-validation.md
category: test-authoring
mcp_compliance_level: partial
optimization_status: draft
status: active
dependencies: []
tags: [testing, validation, ci, gsettings]
description: >-
  Documents how bluefin-bling validates extensions: the discovery-based unittest
  suite under tests/ and the invariants it enforces on metadata.json, GSettings
  schemas and GJS sources. Use when adding a test, debugging CI, or shipping a
  new extension.
metadata:
  type: reference
  context7-sources:
    - /git_gitlab_gnome_org/gnome_gnome-shell
    - /websites/gjs-docs_gnome
---

# Extension Validation

## When to Use

- Adding or changing anything under `extensions/`.
- Adding a test, or teaching an existing test a new shape.
- A CI run failed on `validate extensions (tests, node --check, glib-compile-schemas)`
  and you need to know which invariant fired.
- Shipping a brand-new extension and wanting to know what it must satisfy.

## Run it

```bash
python3 -m unittest discover -s tests -t tests -v
```

Standard library only — no `pip install`, no `package.json`. `node` is used via
`node --check`, and the syntax test skips itself if `node` is absent.

Pull requests and pushes to `main` are validated automatically by CI
(`.github/workflows/ci.yml`). Run the suite above locally before opening a PR,
and compile the schemas the same way CI does:

```bash
shopt -s nullglob
for dir in extensions/*/schemas/; do
  glib-compile-schemas --strict --dry-run "$dir"
done
```

Do not name a single extension's `schemas/` directory by hand. CI loops over
every extension, so a hand-written path silently skips any extension but the one
you typed — the exact anti-pattern the next section exists to prevent.

## The rule that matters: discovery, not lists

`tests/extension_manifest.py` walks `extensions/` at call time. Nothing is
hardcoded. **Adding a new extension folder automatically subjects it to every
test — do not add it to a list, there is no list.**

This exists because validation used to be a hand-run, per-file command list, so
any file a new extension added was silently never checked.

## Invariants enforced

`tests/test_extension_manifest.py`
- covers the helpers in `tests/extension_manifest.py` themselves, because every
  other module trusts them. Several helpers are *fail-open*: if discovery or the
  settings-key regex regresses, the dependent tests would pass while checking
  nothing. These tests make that failure loud instead of silent.
- it also covers `referenced_settings_keys()` and the module constants. Derive the
  current class list rather than trusting a copy here:
  `grep -n '^class ' tests/test_extension_manifest.py`

`tests/test_extension_metadata.py`
- `metadata.json` exists, parses, and carries `uuid`, `name`, `description`, `shell-version`
- `uuid` is exactly `<folder-name>@projectbluefin.io` — GNOME Shell refuses to load
  an extension whose uuid does not match its install directory, and reports it as
  simply missing
- `uuid` is unique across the monorepo
- `shell-version` is an ascending, deduplicated list of numeric strings, minimum 45
  (the first ESM-only release)
- `version`, **when present**, is a positive integer, not a string; `url`, **when
  present**, points at this repo. Neither key is required, and
  `power-status-color` currently ships without both — do not read these as
  repo-wide guarantees
- `settings-schema` is declared **exactly when** `schemas/*.gschema.xml` ships

`tests/test_extension_schemas.py`
- schema XML is well-formed, and the schema `id` matches metadata `settings-schema`
  (a mismatch makes `getSettings()` abort the extension at enable time)
- schema `path` is the `id` with dots as slashes, leading and trailing `/`
- every key has a type, a `<default>` and a `<summary>`, and a kebab-case name
- every key read or bound from a GJS source exists in the schema — `Gio.Settings`
  aborts the whole `gnome-shell` process on an unknown key
- every declared key is actually used by some source (no dead settings)
- `TestPortRangeConsistency`: the `prefs.js` port `Gtk.Adjustment` bounds match the
  gschema `<range>`, via the `port_adjustment_bounds()` helper

`tests/test_extension_sources.py`
- every `.js` under `extensions/` passes `node --check`
- sources are ESM and never CommonJS (`require()` / `module.exports` cannot load in GJS)
- `extension.js` default-exports a class extending `Extension` and defines both
  `enable()` and `disable()`; `prefs.js` default-exports an `ExtensionPreferences`
  subclass
- teardown: a `GLib.timeout_add*` implies `GLib.Source.remove`; a `Gio.Cancellable`
  is cancelled in `disable()`; a `Gio.FileMonitor` is both disconnected and cancelled
  in `disable()`; `disable()` is never an empty body

`tests/test_syncthing_toggle.py`
- the only security-regression test in the repo. Verifies `_validatedServiceName()`
  accepts valid systemd unit names and rejects malformed ones and command-injection
  payloads. `SECURITY.md` depends on the invariant it guards — do not weaken it
  without updating that file too

## Extending the suite

Add assertions to the existing test classes rather than new hardcoded checks.
A new invariant should be expressed over `extension_dirs()` so it applies to every
extension, present and future.

Detecting a GSettings key reference is regex-based
(`referenced_settings_keys()` in `tests/extension_manifest.py`). It understands
`settings.get_*('key')`, `settings.set_*('key')` and `settings.bind('key', …)`.
If a source starts reaching settings through some other shape, teach that helper
the new shape — otherwise the unknown-key test goes quietly blind.

## Red Flags

- **A gate that passes when the thing it guards is broken.** Static extraction
  (regex over JS, globbing for CSS) silently yields the empty set on any shape it
  does not understand, and an empty set trivially satisfies a membership
  assertion. Every new gate needs a probe proving it *fails* on real drift.
- **Asserting source text instead of behaviour.** Pinning a literal like
  `30 * 24 * 60 * 60` reds CI on a behaviour-preserving edit to `2592000` and
  detects nothing a behavioural test does not already catch.
- **Naming one extension by hand** in a command, path, or list. If CI loops and
  the doc does not, the doc is wrong.
- **Documenting an invariant as unconditional when the test is guarded by
  `if key not in data: continue`.** Say "when present".

## Verification

Every project-internal fact above is re-derivable. Check the doc against the
repo before trusting it:

```bash
# Which test modules actually exist? (this list must match "Invariants enforced")
ls tests/

# Which invariants are conditional rather than absolute?
grep -n "continue" tests/test_extension_metadata.py

# What does CI actually run, and on which events?
python3 -c "import yaml; d=yaml.safe_load(open('.github/workflows/ci.yml')); print(list(d[True].keys()))"
grep -n "run:" .github/workflows/ci.yml

# Current test count, to notice silent loss of coverage
python3 -m unittest discover -s tests -t tests 2>&1 | tail -3
```
