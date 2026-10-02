---
name: quick-settings-integration
version: "1.2"
last_updated: "2026-10-02"
id: quick-settings-integration
one_line_purpose: Integrating indicators, toggles, and notifications into GNOME Quick Settings.
entry_point: docs/skills/quick-settings-integration.md
# category: intentionally outside common's ci-ops|test-authoring|meta enum.
# This is a GNOME-extensions repo; none of the three fit. Tracked upstream.
category: ui
mcp_compliance_level: partial
optimization_status: draft
status: active
dependencies: []
tags: [quicksettings, gnome, notifications, hig, copy]
description: >-
  How to build Quick Settings toggles, indicators, and system menu
  enhancements for Bluefin, including GNOME HIG rules for user-facing copy.
  Use when touching a QuickMenuToggle, SystemIndicator, or any user-visible
  notification text.
metadata:
  type: reference
  context7-sources:
    - /git_gitlab_gnome_org/gnome_gnome-shell
    - /websites/developer_gnome
---

# Quick Settings & HIG Messaging Integration

Quick Settings is the primary system control surface in GNOME Shell. Bluefin extensions hook into Quick Settings to expose essential OS features (like Sync Folder peer sharing and power state alerts).

## When to Use

- Adding or changing a `QuickMenuToggle`, `SystemIndicator`, or system-menu entry.
- Writing any user-visible string: notification, toggle title, subtitle, menu label.
- Deciding how a feature should be surfaced in the Shell UI at all.

---

## Architecture Components

GNOME Shell provides standard base classes in `resource:///org/gnome/shell/ui/quickSettings.js`:

1. **`SystemIndicator`:** Manages an icon/label in the top bar panel and binds to quick settings items.
2. **`QuickMenuToggle`:** A toggle button with an expandable submenu section.
3. **`QuickSettingsItem`:** Base button class for items placed inside `quickSettings.menu._grid`.

```javascript
import { QuickMenuToggle, SystemIndicator } from 'resource:///org/gnome/shell/ui/quickSettings.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

export default class IndicatorExtension extends Extension {
    enable() {
        this._indicator = new SystemIndicator();
        this._toggle = new QuickMenuToggle({
            title: _('Sync Folder'),
            toggleMode: true,
        });
        this._indicator.quickSettingsItems.push(this._toggle);
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);
    }

    disable() {
        this._indicator.quickSettingsItems.forEach(item => item.destroy());
        this._indicator.destroy();
        this._indicator = null;
    }
}
```

---

## Desktop Notifications & HIG Voice

Notify users of background state changes using `Main.notify(title, details)`.

### GNOME Human Interface Guidelines (HIG) Principles

Source: Context7 `/websites/developer_gnome`

1. **Talk like a person:** Use everyday words. Never expose internal protocol names or acronyms (e.g. do not say "P2P", "Syncthing daemon", or "Local subnet sync").
2. **Get to the point:** Put the primary state in the title; describe the user benefit in the body.
3. **Use active voice and short sentences:** Keep text easy to scan on transient desktop banners.

### Copywriting Comparison

| Situation | ❌ Avoid (Geeky / Technical) | ✅ Use (GNOME HIG Compliant) |
|---|---|---|
| Feature Enabled | "Peer-to-peer file sync active on your local network." | **Sync Folder Sharing Enabled**<br>*Your files are sharing with your other devices.* |
| Feature Disabled | "Daemon stopped, port 8384 unmapped." | **Sync Folder Sharing Disabled**<br>*File sharing is paused.* |
| Reboot Pending | "Staged composefs deployment detected in sysroot." | **Restart Required**<br>*System updates are ready to install.* |

---

## Notification Implementation

```javascript
const title = isEnabled
    ? _('Sync Folder Sharing Enabled')
    : _('Sync Folder Sharing Disabled');

const body = isEnabled
    ? _('Your files are sharing with your other devices.')
    : _('File sharing is paused.');

Main.notify(title, body);
```

## Live Reboot-Alert QA

Harness results prove decision logic; shipping a power-button change also requires
the actual rendered Quick Settings surface and restoration of real inputs.

### Establish the running build and real state

1. Inspect `gnome-extensions list` and `gnome-extensions info "$UUID"` to identify
   the loaded UUID and installation path. Compare that path's `extension.js` and
   `stylesheet.css` with the source (`cmp`), and read its `metadata.json`. If an
   image uses another UUID, install a user override with **that same UUID**, matching
   directory and metadata; enable only one copy so two pollers cannot compete.
   Preserve version-validation and Shell Eval policies. Normal code updates need
   logout/login: module imports are cached, and disable/enable is not a reload.
2. Read `/proc/uptime` and inspect the four marker paths in
   `extensions/power-status-color/extension.js`; optionally read
   `bootc status --format=json` when permitted. A native staged marker works even
   when Shell cannot run bootc successfully. Use real pending deployments for
   yellow; leave existing system flags untouched. Without one, harness evidence
   does not establish live yellow or red-over-yellow precedence.
3. After enable, wait with a finite deadline until **both** `_checkingStatus` and
   `_statusQueued` are false before inspecting the power actor or asserting state.
   Open Quick Settings and capture the rendered icon, not merely its CSS classes.

### Reach the native evaluator safely

Use Alt+F2 → `lg` → Evaluator. Upstream Looking Glass documentation describes
pre-imported `Main`, dynamic imports (`await import(...)`), and saved history/results.
Inspect the installed Shell/extension APIs before using private actor paths. Verify
the input text exactly before Return, then identify the newly produced result:
an old `r(n)` row or historical error is not evidence that the current expression ran.
Use `Main.extensionManager.lookup(UUID).stateObj` only after confirming that shape
in the installed Shell, and confirm it is the active instance for the chosen UUID.

For automated input, a missing `gnome-shell` AT-SPI application is a diagnostic,
not a reason to alter accessibility preferences. Probe in a fresh subprocess to
avoid cached GLib/AT-SPI environment state. Bound Ponytail's `Connected` wait (its
installed `connectMonitor`/`connectWindow` loops may lack a timeout); release every
pressed modifier in `finally`. If the controller fails, create an **owned** native
Mutter RemoteDesktop session using one persistent D-Bus connection. Introspect
the service and the returned session before invoking methods; use direct keysym
press/release pairs with settle delays, releasing held keys and stopping that
session in `finally`. Keep the shared controller daemon running.

```bash
gdbus introspect --session --dest org.gnome.Mutter.RemoteDesktop \
  --object-path /org/gnome/Mutter/RemoteDesktop
gdbus introspect --session --dest org.gnome.Mutter.ScreenCast \
  --object-path /org/gnome/Mutter/ScreenCast
```

For visual capture, use an owned Mutter ScreenCast session with finite waits for
stream readiness and frames. Decode mapped video using `GstVideo` row stride,
not `width * bytes_per_pixel`; stop the pipeline and only sessions created by
this QA connection. `AccessDenied` on a foreign session means cleanup failed,
not that it stopped or that ownership can be bypassed.

### Prove red precedence, then restore

With a real pending restart and real uptime below 30 days, make a unique temporary
copy of the tested extension module. Redirect **only** its `'/proc/uptime'` literal
to an owned temporary fixture containing `2592000 0\n`; keep its imports, threshold,
and all other logic unchanged. Dynamically import that unique file in the native
Evaluator without enabling another extension or constructing another poller.
Borrow the imported class prototype's `_checkUptimeOverdue`, bound to the running
instance; its real `_checkStatus()` still checks the real pending deployment.

Before replacement, save the original method and its own-property descriptor
(including whether it was inherited). Arm bounded automatic restoration **before**
the replacement, and also restore in `finally`: restore the descriptor if owned,
otherwise delete the temporary own property. Wait for idle before replacement;
call `_checkStatus()`, wait for both status flags to clear, and capture the rendered
red icon. Restore the original method, rerun `_checkStatus()`, wait for idle, and
capture the real-uptime yellow icon while the real deployment remains pending.
Remove the auto-restore source, fixture, module copy, and other owned temporary
files/resources after restoration. A timeout or failed capture still requires
restoration; leave no production edits or reusable status stubs behind.

Completion requires fresh rendered evidence for the claimed states, the original
method/property state restored, a final real-input check, and all owned input and
capture resources released. Record any unavailable live state as unverified.

## Red Flags

- **Reaching into Shell internals without a fallback.** Paths like
  `qs._system._systemItem.menu.sourceActor` are private and move between
  releases. Guard every hop and degrade quietly rather than throwing during
  `enable()`.
- **Leaving a toggle or indicator registered after `disable()`.** Everything
  pushed to `quickSettingsItems` must be destroyed, and any style class added to
  a system widget must be removed.
- **Notifying before the action succeeded.** Telling the user "Sharing Enabled"
  and then failing validation leaves the message contradicting reality. Notify
  after the work, or not at all.
- **Technical voice in user-facing copy.** See the comparison table above; it is
  the house style, not a suggestion.
- **Untranslated user-visible strings.** Wrap them in `_()`.
- **Settings entries not hidden on lock.** `Main.sessionMode.allowSettings`
  gates visibility; registering the entry is what lets the Shell hide it.

## Verification

```bash
# Which Quick Settings surfaces does this repo actually touch?
grep -rn "QuickMenuToggle\|SystemIndicator\|quickSettingsItems" extensions/

# Every user-visible string should be translatable
grep -rn "Main.notify(" extensions/

# Confirm Shell API shapes against upstream rather than memory
#   Context7: /git_gitlab_gnome_org/gnome_gnome-shell, /websites/developer_gnome
```
