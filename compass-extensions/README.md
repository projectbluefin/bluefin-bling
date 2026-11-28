# Compass extensions

This directory holds the [Compass](https://github.com/tuna-os/compass) launcher
extensions maintained alongside the GNOME Shell extensions in `../extensions/`.

These are **not** GNOME Shell extensions. They are TypeScript packages built with
the [`@vicinae/api`](https://www.npmjs.com/package/@vicinae/api) SDK, so the same
source runs on Compass, Vicinae, and Raycast-compatible launchers. Each folder is
one self-contained extension with its own `package.json` manifest.

## Extensions

| Folder | Command | What it does |
|---|---|---|
| `container-terminal/` | Open Container in Terminal | Search Podman, Docker, and Distrobox containers and open the selected one in Ghostty or a terminal of your choice. |
| `brew-search/` | Search Brew Packages | Search formulae/casks through formulae.brew.sh and install the selection with host Homebrew. |
| `ai-command/` | Ask AI | `/ai`-style prompt command with a selectable model and provider/source. |

## Develop

From an extension folder:

```bash
npm install
npm run dev          # vici develop: builds and hot-reloads into a running Compass
```

To build once:

```bash
npm run build        # vici build -> dist
```

With the Flatpak build of Compass, point `vici` at its data directory:

```bash
XDG_DATA_HOME=~/.var/app/org.tunaos.compass/data npm run dev
```

`console.log` from an extension goes to the Compass engine's stderr, which is
visible with `journalctl --user -u compass -f` when running as a user service.

## Conventions

- Manifest rules are the [`extra/schemas/extension.json`](https://github.com/tuna-os/compass/blob/main/extra/schemas/extension.json)
  schema. `license` must be `MIT`; `name` is the slugged identifier; each
  `commands[].name` maps to `src/<name>.tsx`.
- Prefer Linux mechanisms (XDG paths, `xdg-open`, D-Bus) over macOS-only shims.
- The worker sandbox can read the system and execute system programs, but only
  its own support/assets directories are writable. Keep state in `LocalStorage`
  or `environment.supportPath`.
- Compass does **not** implement `AI.ask()`/`AI.getModels()` yet. Use `fetch` to
  the provider's HTTP API instead.
- Do not hand-maintain a list of extensions anywhere. `tests/test_compass_extensions.py`
  discovers every folder under `compass-extensions/` the same way the GNOME
  suite discovers `extensions/`.

See [`docs/skills/compass-extension-dev.md`](../docs/skills/compass-extension-dev.md)
for the full operational guide.
