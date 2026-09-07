---
name: gnome-shell-extension-dev
version: "1.0"
last_updated: "2026-09-06"
id: gnome-shell-extension-dev
one_line_purpose: Development, architecture, and lifecycle rules for modern GNOME Shell extensions.
entry_point: docs/skills/gnome-shell-extension-dev.md
category: architecture
status: active
tags: [gnome, extensions, esm, bootc, gjs]
description: >-
  Standard practices for writing GNOME Shell extensions (45+ ESM) in Bluefin.
  Covers the Extension class lifecycle, Gio.Subprocess execution, cancellation
  hygiene, pure bootc integration, and CSS styling without leaks.
metadata:
  type: reference
  context7-sources:
    - /git_gitlab_gnome_org/gnome_gnome-shell
    - /websites/gjs-docs_gnome
    - /bootc-dev/bootc
---

# GNOME Shell Extension Development (GNOME 45+)

Bluefin extensions target modern GNOME Shell releases (GNOME 45 through 50+) using native ECMAScript Modules (ESM). Deprecated `imports.*` and legacy extension interfaces are prohibited.

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
