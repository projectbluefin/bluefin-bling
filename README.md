# bluefin-bling

Desktop enhancements, GNOME Shell extensions, and UI bling for [Project Bluefin](https://projectbluefin.io).

This monorepo maintains extensions and visual integrations tailored for Bluefin systems, built around pure `bootc` conventions and modern GNOME Shell releases.

---

## Repository Structure

```
bluefin-bling/
├── extensions/
│   ├── light-style/           # Adaptive light/dark style theming for panel, dock, app grid
│   │   ├── metadata.json
│   │   ├── extension.js
│   │   ├── stylesheet.css
│   │   └── COPYING
│   ├── power-status-color/    # Quick Settings power button status styling
│   │   ├── metadata.json
│   │   ├── extension.js
│   │   └── stylesheet.css
│   └── syncthing-toggle/      # Sync Folder peer sharing quick settings toggle
│       ├── metadata.json
│       ├── extension.js
│       ├── toggle.js
│       ├── prefs.js
│       ├── icons/
│       └── schemas/
├── README.md
└── .gitignore
```

---

## Extensions

### 1. `power-status-color` (Quick Settings Power Status Alert)

**UUID:** `power-status-color@projectbluefin.io`  
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

### 2. `syncthing-toggle` (Sync Folder Peer Sharing Toggle)

**UUID:** `syncthing-toggle@projectbluefin.io`  
**Compatibility:** GNOME Shell 45, 46, 47, 48, 49, 50+

A Quick Settings toggle for Bluefin's built-in Sync Folder peer sharing service. This is just a quadlet that runs the headless official syncthing container:

> EZ
>
> -- John Bazzite

- **Quick Toggle:** Turn peer file sharing on or off with a single click.
- **Desktop Notifications:** GNOME HIG-aligned notifications keep you informed without jargon:
  - **Enabled:** *"Sync Folder Sharing Enabled — Your files are sharing with your other devices."*
  - **Disabled:** *"Sync Folder Sharing Disabled — File sharing is paused."*
- **Submenu Actions:** Quick link to open the Syncthing Web GUI directly in the default browser.

### 3. `light-style` (Adaptive Light Style Theming)

**UUID:** `light-style@projectbluefin.io`  
**Compatibility:** GNOME Shell 45, 46, 47, 48, 49, 50+

Adaptive runtime theming for top bar, dock, and app grid that coordinates with GNOME's Dark/Light style:

- **Dynamic Theme Tracking:** Listens to `org.gnome.desktop.interface color-scheme` signal and applies styles instantly without restarting GNOME Shell.
- **Top Bar (`#panel`):** Polished light palette (`rgba(255, 255, 255, 0.88)`) with dark text/icons (`#2e3436`) in light mode.
- **Dock & Show Apps:** Styled dock container and dark symbolic styling for the show apps grid button.
- **Overview & App Grid:** Light background with crisp typography and search entry styling.
- **Synchronous Cleanup:** Removes `.light-style-active` from `Main.uiGroup` and restores session mode palette cleanly upon disable.

---

## Installation & Development

### Local Installation

Install an extension directly into your user's GNOME Shell extension directory:

#### Install Light Style
```bash
mkdir -p ~/.local/share/gnome-shell/extensions/light-style@projectbluefin.io
cp -r extensions/light-style/* ~/.local/share/gnome-shell/extensions/light-style@projectbluefin.io/
```

#### Install Power Status Color
```bash
mkdir -p ~/.local/share/gnome-shell/extensions/power-status-color@projectbluefin.io
cp -r extensions/power-status-color/* ~/.local/share/gnome-shell/extensions/power-status-color@projectbluefin.io/
```

#### Install Sync Folder Toggle
```bash
mkdir -p ~/.local/share/gnome-shell/extensions/syncthing-toggle@projectbluefin.io
cp -r extensions/syncthing-toggle/* ~/.local/share/gnome-shell/extensions/syncthing-toggle@projectbluefin.io/
glib-compile-schemas ~/.local/share/gnome-shell/extensions/syncthing-toggle@projectbluefin.io/schemas/
```

### Enable Extensions

```bash
gnome-extensions enable light-style@projectbluefin.io
gnome-extensions enable power-status-color@projectbluefin.io
gnome-extensions enable syncthing-toggle@projectbluefin.io
```

Check status:

```bash
gnome-extensions info light-style@projectbluefin.io
gnome-extensions info power-status-color@projectbluefin.io
gnome-extensions info syncthing-toggle@projectbluefin.io
```

### Testing Alert States (`power-status-color`)

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
To simulate 30+ days uptime without waiting, temporarily set `UPTIME_THRESHOLD_SECONDS = 60` in `extensions/power-status-color/extension.js`, restart or re-enable the extension, and observe the red icon.

---

## Contributing & Maintaining Extensions

This repository follows standard Project Bluefin practices:
- Target modern GNOME Shell (45+ ESM).
- Prefer native platform features and standard library (`Gio`, `GLib`, `St`, `Clutter`).
- Follow GNOME Human Interface Guidelines (HIG) for tone and messaging: clear, direct, friendly, and jargon-free.
- Clean teardown on `disable()` is mandatory (no leaked timers, monitors, or lingering DOM styles).
- Follow pure `bootc` image-based lifecycle conventions.

---

## Agentic Factory Onboarding

`bluefin-bling` operates under the Project Bluefin agentic factory model:
- **Authoritative Agent Instructions:** [`AGENTS.md`](AGENTS.md)
- **Skill Router:** [`docs/SKILL.md`](docs/SKILL.md)
- **Skills Catalog:** [`docs/skills/`](docs/skills/)
  - [`factory-onboarding.md`](docs/skills/factory-onboarding.md)
  - [`skill-improvement.md`](docs/skills/skill-improvement.md)
  - [`gnome-shell-extension-dev.md`](docs/skills/gnome-shell-extension-dev.md)
  - [`quick-settings-integration.md`](docs/skills/quick-settings-integration.md)

