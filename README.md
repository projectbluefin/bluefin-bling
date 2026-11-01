# bluefin-bling

Desktop enhancements, GNOME Shell extensions, and UI bling for [Project Bluefin](https://projectbluefin.io).

This monorepo maintains extensions and visual integrations tailored for Bluefin systems, built around pure `bootc` conventions and modern GNOME Shell releases.

---

## Repository Structure

```
bluefin-bling/
├── .github/
│   └── workflows/
│       ├── ci.yml                          # Runs the validation suite on every PR and on main
│       └── gnomeos-boot-feasibility.yml     # Manual feasibility probe for GNOME OS channels
├── extensions/
│   ├── light-style/           # Follows the desktop color-scheme into GNOME Shell's light theme
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
│       ├── service.js         # Standalone GJS deployment helper
│       ├── syncthing.container.in  # Runtime Quadlet template
│       ├── prefs.js
│       ├── icons/
│       └── schemas/
├── docs/
│   ├── SKILL.md               # Task → skill router
│   └── skills/                # Authoritative operational knowledge
├── scripts/                  # CI and compatibility matrix orchestration
├── tests/                     # Discovery-based validation suite (stdlib unittest)
├── AGENTS.md
├── README.md
├── SECURITY.md
├── renovate.json
└── .gitignore
```

`tests/test_docs_inventory.py` checks this tree against the actual top level, so a
new top-level entry fails CI until it is named here.

---

## Extensions

### 1. `power-status-color` (Quick Settings Power Status Alert)

**UUID:** `power-status-color@projectbluefin.io`  
**Compatibility:** GNOME Shell 45, 46, 47, 48, 49, 50, 51+

Visually alters the Quick Settings power button color to indicate system reboot and maintenance state:

- 🟡 **Yellow Alert (`#f6d32d`):** Reboot required. Triggered by bootc's `/run/composefs/staged-deployment` marker, OSTree's `/run/ostree/staged-deployment` marker (also present on rpm-ostree hosts without bootc), a standard `/run/reboot-required` flag (including `/var/run/reboot-required`), or `bootc status --format=json` with `status.staged != null` when that command is permitted.
- 🔴 **Red Alert (`#e01b24`):** High uptime / reboot overdue. Triggered when host system uptime reaches or exceeds 30 days (`>= 2,592,000` seconds). Takes precedence over yellow reboot alerts.
- ⚪ **Normal State:** Standard system theme styling when neither condition is met or when the extension is disabled.

#### Architecture & Conventions

- **Pure bootc:** Detects staged container image updates through user-readable runtime markers without elevated execution. `bootc status --format=json` remains a fallback; on releases that require root, the markers provide detection.
- **Modern GNOME 45+ ESM:** Implements the modern GNOME Shell Extension class with native ESM imports.
- **Event-Driven & Polling:** Monitors each reboot/staged-deployment file via `Gio.FileMonitor`, including creation and removal, paired with a low-overhead 5-minute timer. Polling remains available if a monitor cannot be installed.
- **Lifecycle Hygiene:** Gracefully cancels in-flight subprocesses (`Gio.Subprocess.force_exit`), disconnects file monitors, clears `GLib.Source` timeouts, and removes custom CSS classes upon disable.

### 2. `syncthing-toggle` (Sync Folder Peer Sharing Toggle)

**UUID:** `syncthing-toggle@projectbluefin.io`  
**Compatibility:** GNOME Shell 45, 46, 47, 48, 49, 50, 51+

A single Quick Settings toggle controls a rootless Quadlet running the official
Syncthing container. No separate panel icon or companion desktop app is needed.

- **Documents by default:** The actual XDG Documents directory is configured locally. Other available XDG folders appear paused in Sharing Settings for opt-in.
- **XDG-only fresh setup:** Only existing XDG presets are offered; no custom `~/Sync` folder is created. Existing folders are retained during migration.
- **Explicit sharing:** Pair devices and approve folders in Syncthing's existing web UI. Enabling the service or unpausing a folder does not grant a peer access to Documents.
- **Supported configuration:** The extension uses authenticated asynchronous REST requests, not daemon XML rewrites or a host Syncthing binary.
- **Preserved settings:** Native-service migration keeps identity, database, existing folders and peer approvals. Later toggles preserve user edits and deleted presets.
- **Metered networks:** Sharing pauses without erasing the user's login startup choice; turning sharing on while metered is remembered across logouts and starts on the next unmetered connection; a newer off request prevents automatic resume.
- **Protected web UI:** The built-in web page is locked with the login name and a private random password on every start, so other local accounts and sandboxed apps cannot reach it. Sharing Settings copies that password to the clipboard before opening the page; a login the user set themselves is kept.
- **Choose who gets each folder:** The Sync Folder menu lists paired devices and lets you share, accept, or stop sharing each enabled standard folder. Pairing and incoming offers never share files on their own.
- **Simple controls:** Sharing Settings opens the built-in web UI for pairing and advanced choices; Start/Stop only leaves login startup unchanged.

#### Share a folder between two computers

1. Turn on Sync Folder on both computers. On each one, open its menu and select
   **Sharing Settings**. Sign in if asked; the menu copies the password for you.
2. Pair the computers in Sharing Settings, approving the other computer on each
   side. Pairing alone does not share any folders.
3. On the first computer, open the **Sync Folder** menu, choose the other
   computer, and select **Share** for Documents (or another standard folder
   that is turned on).
4. On the second computer, open the **Sync Folder** menu, choose the first
   computer, and select **Accept** for that folder. Both computers must agree
   before files can sync. The menu says **Waiting for the other device** until
   the other computer agrees, then **Sharing** when both are connected.

Only standard folders that are turned on appear here. Turn on other standard
folders in Sharing Settings on both computers first. Pairing, custom folders,
paused folders, and offers from unknown devices stay in Sharing Settings.

Choose **Stop sharing** under a device to stop sending and receiving that
folder with it. Files already copied stay on both computers, and the devices
stay paired. The other computer's continuing offer will not prompt you to
share again; you can choose **Share** again whenever you want. Sharing and
stopping also work while the other computer is away; the menu shows only this
computer's choice until it reconnects. Leave automatic folder acceptance off
in Sharing Settings so that it cannot undo a stop without your consent.

If a change is refused or cannot be confirmed, reopen the menu to check the
actual state before trying again. Device names and sharing controls are hidden
while the screen is locked.

Existing files in the two folders are brought together. If both computers have
changed the same file differently, Syncthing keeps a conflict copy; see its
[explanation of conflicts](https://docs.syncthing.net/users/syncing.html#conflicting-changes).
Review the folder contents before sharing: later changes and deletions can sync
to the other computer too.

Requires `podman`, `gjs` with Soup 3, and the desktop's `xdg-user-dirs` configuration. The first on request prepares
the private configuration and user Quadlet. Install the whole extension directory,
including its helper and container template. After replacing an already-loaded
extension version on Wayland, save work and log out/in to load the new code.

The rootless container mounts only selected folders. It deliberately disables
container SELinux labeling rather than relabeling user files; user namespaces,
same-UID execution, dropped capabilities and no-new-privileges remain in place.
See [container integration](docs/skills/syncthing-container-integration.md) for the
security tradeoff, migration details, and verification workflow.

### 3. `light-style` (Light Style Follower)

**UUID:** `light-style@projectbluefin.io`  
**Display name:** Bluefin Light Style — deliberately distinct from the upstream `light-style@gnome-shell-extensions.gcampax.github.com` extension, which presents as plain "Light Style".  
**Compatibility:** GNOME Shell 45, 46, 47, 48, 49, 50, 51+

Makes GNOME Shell itself follow the desktop light/dark preference, rather than
repainting the Shell from the extension:

- **Dynamic Theme Tracking:** Watches `org.gnome.desktop.interface color-scheme` and re-syncs live, with no Shell restart.
- **Session Mode Handover:** Sets `Main.sessionMode.colorScheme` to `prefer-light`, which is what makes the Shell load its own `gnome-shell-light.css`. Panel, dash, overview, search entry and app folders are then themed by Adwaita, not by hex in this repo.
- **Accent-Aware Dock Dot:** The one supplementary rule — Dash to Dock's running dot uses `-st-accent-color` (GNOME 47+) instead of the foreground colour. On 45/46 the declaration is dropped and the dot keeps the Shell default.
- **Non-Destructive Teardown:** `enable()` records the session's existing `colorScheme` and `disable()` restores exactly that value, so a custom `/usr/share/gnome-shell/modes/*.json` — which Bluefin and Dakota ship — survives an enable/disable cycle. The `.light-style-active` class is removed from `Main.uiGroup` synchronously.

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

GNOME Shell caches imported extension modules. When updating an already loaded extension, log out and back in to load the new JavaScript; toggling it alone is not a reload. If an image ships the same extension under a different UUID (such as `power-status-color@local`), use that UUID for a user-level override and in its `metadata.json` instead of enabling two copies. Keep version-validation policy unchanged.

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
The power icon turns yellow immediately via the file monitor.

Clear the alert:
```bash
sudo rm -f /run/reboot-required
```

The same yellow state is produced by a real staged deployment. Inspect the
markers the extension watches without changing anything:
```bash
for marker in /run/composefs/staged-deployment /run/ostree/staged-deployment /run/reboot-required /var/run/reboot-required; do
  test ! -e "$marker" || printf '%s\n' "$marker"
done
bootc status --format=json # optional; may require permissions unavailable to Shell
```

#### 2. Uptime Overdue (Red)
To simulate 30+ days uptime without waiting, temporarily set `UPTIME_THRESHOLD_SECONDS = 60` in `extensions/power-status-color/extension.js`, restart or re-enable the extension, and observe the red icon. Restore the production value afterwards.

The decision logic (including the exact boundary — **2,591,999 seconds is not overdue; 2,592,000 seconds is overdue**) is covered without touching the system:
```bash
python3 -m unittest discover -s tests -t tests -v -p 'test_power_status_color.py'
```

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
  - [`extension-validation.md`](docs/skills/extension-validation.md)
  - [`factory-onboarding.md`](docs/skills/factory-onboarding.md)
  - [`gnome-shell-extension-dev.md`](docs/skills/gnome-shell-extension-dev.md)
  - [`pr-review-and-merge.md`](docs/skills/pr-review-and-merge.md)
  - [`quick-settings-integration.md`](docs/skills/quick-settings-integration.md)
  - [`skill-improvement.md`](docs/skills/skill-improvement.md)
  - [`syncthing-container-integration.md`](docs/skills/syncthing-container-integration.md)

This catalog and [`docs/SKILL.md`](docs/SKILL.md) are both checked against
`docs/skills/` by `tests/test_docs_inventory.py`: every skill doc must appear in
**both** this catalog **and** `docs/SKILL.md`, and missing from either one fails
CI.

