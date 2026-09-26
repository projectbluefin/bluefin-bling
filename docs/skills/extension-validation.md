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

Standard library only — no `pip install`, no `package.json`. `node` is required
for the parts of the suite that execute JavaScript: `node --check` on every
source, and the behavioural harnesses below. All of them **skip silently when
`node` is absent**, so a green local run on a machine without node has proven
far less than it looks — CI installs node for this reason.

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
- `metadata.json` exists, parses, and carries `uuid`, `name`, `description`,
  `shell-version`, `url` and `version` — the full `REQUIRED_KEYS` tuple, checked for
  presence in one place
- `uuid` is exactly `<folder-name>@projectbluefin.io` — GNOME Shell refuses to load
  an extension whose uuid does not match its install directory, and reports it as
  simply missing
- `uuid` is unique across the monorepo
- `shell-version` is an ascending, deduplicated list of numeric strings, minimum 45
  (the first ESM-only release)
- `version` is a positive integer, not a string, and `url` is exactly `REPO_URL`.
  Both are mandatory for every extension: `version` is the identity GNOME Shell and
  extensions.gnome.org compare to decide an update exists, so a manifest without one
  cannot express that it changed. These two were once checked only *when present*,
  which meant the gate could only judge manifests that already complied — if you add
  a value check here, put the key in `REQUIRED_KEYS` rather than guarding the test
  with `if key not in data: continue`
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

`tests/test_syncthing_toggle.py`
- the only security-regression test in the repo. Verifies `_validatedServiceName()`
  accepts valid systemd unit names and rejects malformed ones and command-injection
  payloads. `SECURITY.md` depends on the invariant it guards — do not weaken it
  without updating that file too

`tests/test_extension_coverage.py`
- the discovery side of the behavioural harnesses below: every gate above is
  shape-only (a `disable()` with the right teardown keywords in its body is not
  proof it runs correctly), so this asserts that every `.js` under `extensions/`
  is actually *executed* by something. For each `js_sources(ext_dir)` entry, some
  `tests/*_harness.mjs` must resolve that exact path
  (`extension_manifest.harness_covers_source()`), and some `tests/test_*.py` must
  spawn that harness through node (`extension_manifest.harness_is_exercised()`) —
  a harness nothing runs proves nothing
- `tests/gnome_module_loader_harness.mjs` is the one exempt harness: it exercises
  the shared import-rewrite shim directly against synthetic sources, not a
  shipped `extensions/` path, so it can never "cover" one
- deliberate gaps belong in `UNCOVERED_SOURCES`, a shrinking ratchet keyed to a
  repo-relative path. It is empty today. `TestRatchetIsAccurate` fails an entry
  that is stale in either direction — already covered, or no longer a real file —
  so remove the entry in the same PR that adds the harness rather than leaving it
  behind

## Behavioural harnesses

The checks above read source text. A harness instead *runs* the shipped source:
`tests/syncthing_toggle_harness.mjs`, `tests/syncthing_prefs_harness.mjs`,
`tests/syncthing_extension_harness.mjs`, `tests/light_style_harness.mjs` and
`tests/power_status_color_harness.mjs` read the real `.js` file, rewrite **only**
its `gi://` and
`resource:///org/gnome/shell/…` import block into bindings taken from
`globalThis`, and import the result as a base64 `data:` module. Everything below
the import block — the logic under test — executes byte-for-byte as shipped.
`tests/gnome_module_loader.mjs` owns that rewrite so the grammar lives in one
place; a harness whose module also imports a sibling of its own — as
`syncthing-toggle/extension.js` imports `./toggle.js`, which no `data:` URL can
resolve — passes a second `rewrite` pass to the loader.
Each harness takes a scenario name and a JSON options blob on argv and prints a
single JSON object describing what the extension did; the matching
`tests/test_*_behavior.py` asserts on that JSON.

The stubs record observable effects — spawned argv, `Main.notify()` calls,
launched URIs, indicator visibility, subtitle text — so the tests assert what a
user or the system would see, never source text. Adding a scenario means adding
one entry to the `scenarios` object plus the assertions for it.

Two rules keep these honest:

- **One harness per module.** A second harness over the same source splits the
  stub surface in two and the halves drift: a behaviour proved in one is not
  proved in the other, and a reviewer cannot tell which is authoritative. Append
  a scenario to the existing harness instead of starting a rival.
- **Mutate the source to prove a scenario bites.** Every scenario here was
  checked by breaking the extension on purpose — deleting the reconcile call at
  `enable()`, hoisting the notification above the `systemctl` result — and
  confirming the suite went red.

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

Two of the style-gate invariants are claims about someone else's code, so they are
only as good as the release they were read from. They were derived against
gnome-shell **48.0**; re-derive them before trusting them against a newer Shell.

Which stylesheets Shell loads, and that it never recurses:

```bash
curl -fsSL https://gitlab.gnome.org/GNOME/gnome-shell/-/raw/48.0/js/ui/extensionSystem.js \
  | sed -n '/^    _loadExtensionStylesheet(extension) {/,/^    }$/p'
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
