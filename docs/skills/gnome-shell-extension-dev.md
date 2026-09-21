---
name: gnome-shell-extension-dev
version: "1.2"
last_updated: "2026-09-18"
id: gnome-shell-extension-dev
one_line_purpose: Development, architecture, and lifecycle rules for modern GNOME Shell extensions.
entry_point: docs/skills/gnome-shell-extension-dev.md
# category: intentionally outside common's ci-ops|test-authoring|meta enum.
# This is a GNOME-extensions repo; none of the three fit. Tracked upstream.
category: architecture
mcp_compliance_level: partial
optimization_status: draft
status: active
dependencies: []
tags: [gnome, extensions, esm, bootc, gjs]
description: >-
  Standard practices for writing GNOME Shell extensions (45+ ESM) in Bluefin:
  the Extension lifecycle, Gio.Subprocess execution, cancellation hygiene,
  bootc integration, and CSS without leaks. Use when creating or modifying
  anything under extensions/.
metadata:
  type: reference
  context7-sources:
    - /git_gitlab_gnome_org/gnome_gnome-shell
    - /websites/gjs-docs_gnome
    - /bootc-dev/bootc
---

# GNOME Shell Extension Development (GNOME 45+)

Bluefin extensions target modern GNOME Shell releases (GNOME 45 through 50+) using native ECMAScript Modules (ESM). Deprecated `imports.*` and legacy extension interfaces are prohibited.

## When to Use

- Creating or modifying anything under `extensions/`.
- Writing `enable()` / `disable()`, or chasing a leak on disable.
- Running a subprocess, doing file I/O, or using a `Gio.Cancellable` from Shell.
- Reading bootc system state.
- Adding or changing CSS applied to Shell widgets.

---

## Architecture & Module Imports

All extensions use the `Extension` base class imported from Shell resources:

```javascript
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension, gettext as _} from 'resource:///org/gnome/shell/extensions/extension.js';

export default class MyExtension extends Extension {
    enable() { ... }
    disable() { ... }
}
```

---

## Lifecycle Hygiene Rules

1. **`disable()` is synchronous:** GNOME Shell does not wait for promises in `disable()`. Teardown must be immediate and synchronous.
2. **Subprocess Cancellation:** When running background commands via `Gio.Subprocess`, passing a `Gio.Cancellable` to `communicate_utf8_async` only aborts the local stream read. To prevent orphaned processes, connect to the cancellable and invoke `proc.force_exit()`:

```javascript
function runCommandAsync(argv, cancellable) {
    return new Promise((resolve) => {
        try {
            const proc = new Gio.Subprocess({
                argv,
                flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE,
            });
            proc.init(cancellable);

            let cancelId = 0;
            if (cancellable) {
                cancelId = cancellable.connect(() => {
                    try {
                        proc.force_exit();
                    } catch {}
                });
            }

            proc.communicate_utf8_async(null, cancellable, (p, res) => {
                if (cancelId && cancellable) {
                    cancellable.disconnect(cancelId);
                }
                try {
                    const [ok, stdout] = p.communicate_utf8_finish(res);
                    resolve(ok ? stdout : null);
                } catch {
                    resolve(null);
                }
            });
        } catch {
            resolve(null);
        }
    });
}
```

3. **Timeouts & File Monitors:**
   - Every `GLib.timeout_add_seconds` must be tracked and removed with `GLib.Source.remove(this._timeoutId)` in `disable()`.
   - Every `Gio.FileMonitor` signal must be disconnected with `monitor.disconnect(id)` and cancelled with `monitor.cancel()`.
4. **Style Cleanup & Foreign Actor Ownership:**
   - Never leave custom CSS style classes on system widgets after deactivation. Clean up all added classes in `disable()`.
   - **Retain explicit handles on styled foreign actors:** If an extension styles an actor it does not own (such as GNOME Shell's Quick Settings power button), do not rely on live tree lookups (e.g. `_findPowerButton()`) during teardown. Quick Settings widgets are rebuilt across session-mode transitions, and lookups can return `null` or resolve to a different actor when the session locks. Keep an explicit record of styled actors (e.g. a `Map` of actor to handler id populated in `_applyStyle()`, including child actors when styled), remove classes directly from that record in `_removeStyleClasses()`, and clear/null it in `disable()`. If the lookup resolves a new actor while active, remove styles from the orphaned previous actor before styling the new one.
   - **Never touch a retained foreign actor unguarded:** Shell can destroy an actor you retained. Connect to each retained actor's `destroy` signal to evict it from the record (and `disconnect()` the handler when you drop a still-live actor), and wrap every style mutation on a retained reference in `try/catch`. Calling a method on a disposed GObject logs a GJS critical and throws, which would otherwise abort `_applyStyle()` before the new actor is styled and abort `disable()` before teardown finishes.
   - **In-flight guards for asynchronous checks:** When an async status probe (such as polling system uptime or spawning `bootc status`) can be triggered by multiple sources (a file monitor event, a periodic timer, and `enable()`), guard the method with an in-flight flag and a re-check queue (`_checkingStatus` and `_statusQueued`). Concurrent invocations should not interleave or race `Promise.all` resolution; instead, queue a subsequent check to run after the active one completes.
5. **Dynamic Theme & Style Class Management:**
   - Scope custom theme overrides under a top-level style class on `Main.uiGroup` (e.g. `Main.uiGroup.add_style_class_name('light-style-active')`).
   - Listen to `changed::color-scheme` on `Gio.Settings({ schema_id: 'org.gnome.desktop.interface' })` and notify `St.Settings.get().notify('color-scheme')` after toggling `Main.sessionMode.colorScheme`.
   - **Save and restore `Main.sessionMode.colorScheme`; never reset it to a literal.** Capture `this._savedColorScheme = Main.sessionMode.colorScheme` in `enable()` *before* the first sync, write that value back in `disable()`, then null it. `js/ui/sessionMode.js` `_loadMode()` copies `colorScheme` out of a custom `/usr/share/gnome-shell/modes/*.json` because it is a key of the restrictive `DEFAULT_MODE`, so an image shipping its own session mode — Bluefin and Dakota both do — has a value here that is not `'prefer-dark'`. Hardcoding the reset silently destroys it, and stomps any concurrently enabled theming extension driving the same property.
   - Do the same in `_sync()`: the "back to dark" branch restores the saved value, it does not assert `'prefer-dark'`.
   - Remove the style class from `Main.uiGroup` and disconnect the settings signal synchronously in `disable()` to guarantee zero style leakage.

---

## Pure bootc Integration

Bluefin is an image-based OS powered by `bootc`. Extensions interact with system state following bootc conventions:

1. **Detecting Staged Updates:**
   - Run `bootc status --format=json` (or read `/run/reboot-required`).
   - Parse `status.staged`: if non-null, an update is queued for next boot.
2. **Permissions:**
   - On current bootc releases, `bootc status` calls `prepare_for_write()`, which checks for root privilege. Handle permission denials gracefully and fall back to file flags (`/run/reboot-required`).
   - Do not invoke interactive `pkexec` dialogs on automated background polling loops.

---

## Validation & Linting

Before committing:
```bash
node --check extensions/<extension>/extension.js
python3 -m json.tool extensions/<extension>/metadata.json > /dev/null
glib-compile-schemas --strict extensions/<extension>/schemas/
```

The per-file form above is for spot-checking one extension while iterating. The
authoritative gate is the discovery-based suite — see
[`extension-validation.md`](./extension-validation.md), which runs over every
extension with no hardcoded list.

## Red Flags

- **Synchronous I/O anywhere in the Shell process.** `query_exists(null)`,
  `load_contents(null)`, `replace_contents(..., null)`, and
  `GLib.find_program_in_path()` all block. On NFS, autofs, sshfs, or a
  spun-down disk they freeze the entire compositor, not just your menu. Use the
  `_async` variants.
- **An unguarded continuation after `await`.** Cancelling a `Gio.Cancellable`
  does not drop the callback — it invokes it with `G_IO_ERROR_CANCELLED`. If
  `disable()` already ran, the continuation touches finalized St widgets and
  raises "already deallocated". Re-check your destroyed flag *after* every
  `await`, not only before.
- **Assuming a cancellable kills the child process.** It aborts the local stream
  read only. Connect to the cancellable and call `proc.force_exit()`.
- **`logError('message', err)`.** The GJS signature is
  `logError(error, prefix)` — error first. Swapped arguments lose the stack trace.
- **Interpolating a settings value into a command line.** Settings are
  dconf-writable by any same-uid process. Validate, then pass as a discrete argv
  entry — and reject a leading `-`, or `systemctl` parses it as an option.
- **Hardcoded accent colours.** Since GNOME 47 the accent is user-selectable;
  use the `-st-accent-color` keyword instead of a literal like `#3584e4`.
- **Resetting shared Shell state to a literal instead of the value you found.**
  `Main.sessionMode.colorScheme = 'prefer-dark'` in `disable()` is the common
  case: the session mode may legitimately carry another value, and another
  enabled extension may own it right now. Save in `enable()`, restore in
  `disable()`.
- **Re-theming in CSS what the variant you just selected already themes.**
  Flipping `sessionMode.colorScheme` makes the Shell load
  `gnome-shell-light.css`, which already styles `#panel`, `.dash-background`,
  `.overview-tile`, `.search-entry` and friends. Piling fixed hex and
  `!important` on top fights that sheet and freezes a palette Adwaita has since
  moved on from. Ship only what the stock sheet does not express.

## Verification

```bash
# Which GNOME versions do we actually target?
python3 -c "import json,glob; [print(f, json.load(open(f))['shell-version']) for f in glob.glob('extensions/*/metadata.json')]"

# Teardown hygiene is machine-checked; see which rules exist
grep -n "def test_" tests/test_extension_sources.py

# Confirm an API against upstream rather than memory
#   Context7: /git_gitlab_gnome_org/gnome_gnome-shell, /websites/gjs-docs_gnome
```
