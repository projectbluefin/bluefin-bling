---
name: gnome-shell-extension-dev
version: "1.1"
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
4. **Style Cleanup:**
   - Never leave custom CSS style classes on system widgets after deactivation. Clean up all added classes in `disable()`.

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

## Verification

```bash
# Which GNOME versions do we actually target?
python3 -c "import json,glob; [print(f, json.load(open(f))['shell-version']) for f in glob.glob('extensions/*/metadata.json')]"

# Teardown hygiene is machine-checked; see which rules exist
grep -n "def test_" tests/test_extension_sources.py

# Confirm an API against upstream rather than memory
#   Context7: /git_gitlab_gnome_org/gnome_gnome-shell, /websites/gjs-docs_gnome
```
