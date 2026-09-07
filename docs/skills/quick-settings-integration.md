---
name: quick-settings-integration
version: "1.0"
last_updated: "2026-09-06"
id: quick-settings-integration
one_line_purpose: Integrating indicators, toggles, and notifications into GNOME Quick Settings.
entry_point: docs/skills/quick-settings-integration.md
category: ui
status: active
tags: [quicksettings, gnome, notifications, hig, copy]
description: >-
  How to build Quick Settings toggles, indicators, and system menu enhancements
  for Bluefin. Includes GNOME HIG messaging guidelines for user-facing copy.
metadata:
  type: reference
  context7-sources:
    - /git_gitlab_gnome_org/gnome_gnome-shell
    - /websites/developer_gnome
---

# Quick Settings & HIG Messaging Integration

Quick Settings is the primary system control surface in GNOME Shell. Bluefin extensions hook into Quick Settings to expose essential OS features (like Sync Folder peer sharing and power state alerts).

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
