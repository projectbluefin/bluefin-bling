# bluefin-bling

Desktop enhancements, GNOME Shell extensions, and UI bling for [Project Bluefin](https://projectbluefin.io).

This repository maintains extensions and visual integrations tailored for Bluefin systems, built around pure `bootc` conventions and modern GNOME Shell releases.

---

## Extensions

### `power-status-color` (Quick Settings Power Status Alert)

**UUID:** `power-status-color@local`  
**Compatibility:** GNOME Shell 45, 46, 47, 48, 49, 50+

Visually alters the Quick Settings power button color to indicate system reboot and maintenance state:

- 🟡 **Yellow Alert (`#f6d32d`):** Reboot required. Triggered when a system update is staged via pure `bootc` (`bootc status --format=json` with `status.staged != null`) or when a standard `/run/reboot-required` flag is present.
- 🔴 **Red Alert (`#e01b24`):** High uptime / reboot overdue. Triggered when host system uptime reaches or exceeds 30 days (`>= 2,592,000` seconds). Takes precedence over yellow reboot alerts.
- ⚪ **Normal State:** Standard system theme styling when neither condition is met or when the extension is disabled.

#### Architecture & Conventions

- **Pure bootc:** Integrates directly with `bootc status --format=json` to check for staged container image updates, skipping legacy distribution package managers.
- **Modern GNOME 45+ ESM:** Implements the modern GNOME Shell Extension class with native ESM imports.
- **Event-Driven & Polling:** Watches `/run` via `Gio.FileMonitor` for instant reaction to reboot flags, paired with a low-overhead 5-minute background timer for uptime and staged update checks.
- **Lifecycle Hygiene:** Gracefully cancels in-flight subprocesses (`Gio.Subprocess.force_exit`), disconnects file monitors, clears `GLib.Source` timeouts, and removes custom CSS classes upon disable.

---

## Installation & Development

### Local Installation

Install the extension directly into your user's GNOME Shell extension directory:

```bash
mkdir -p ~/.local/share/gnome-shell/extensions/power-status-color@local
cp metadata.json extension.js stylesheet.css ~/.local/share/gnome-shell/extensions/power-status-color@local/
```

### Enable Extension

On Wayland (default in Bluefin), log out and back in or reload extensions:

```bash
gnome-extensions enable power-status-color@local
```

Check status:

```bash
gnome-extensions info power-status-color@local
```

### Testing Alert States

#### 1. Reboot Required (Yellow)
Create a temporary flag file:
```bash
sudo touch /run/reboot-required
```
The power icon turns yellow immediately via the `/run` file monitor.

Clear the alert:
```bash
sudo rm -f /run/reboot-required
```

#### 2. Uptime Overdue (Red)
To simulate 30+ days uptime without waiting, temporarily set `UPTIME_THRESHOLD_SECONDS = 60` in `extension.js`, restart or re-enable the extension, and observe the red icon.

---

## Contributing & Maintaining Extensions

This repository follows standard Project Bluefin practices:
- Target modern GNOME Shell (45+ ESM).
- Prefer native platform features and standard library (`Gio`, `GLib`, `St`, `Clutter`).
- Clean teardown on `disable()` is mandatory (no leaked timers, monitors, or lingering DOM styles).
- Follow pure `bootc` image-based lifecycle conventions.
