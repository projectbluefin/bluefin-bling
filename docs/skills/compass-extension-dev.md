---
name: compass-extension-dev
version: "1.0"
last_updated: "2026-11-22"
id: compass-extension-dev
one_line_purpose: Build and validate Compass launcher extensions in compass-extensions/.
entry_point: docs/skills/compass-extension-dev.md
category: extension-development
mcp_compliance_level: partial
optimization_status: draft
status: active
dependencies: []
tags: [compass, vicinae, raycast, typescript, launcher]
description: >-
  Authoritative guidance for the TypeScript launcher extensions under
  compass-extensions/. Covers the Vicinae/Compass API, the worker sandbox,
  host-program access, preferences, and the discovery-based validation gate.
metadata:
  type: reference
  sources:
    - https://github.com/tuna-os/compass/blob/main/docs/extensions.md
    - https://docs.vicinae.com/extensions/introduction
    - https://github.com/tuna-os/compass/blob/main/extra/schemas/extension.json
---

# Compass Extension Development

## When to use

- Adding or changing anything under `compass-extensions/`.
- Deciding whether a feature belongs in a Compass extension, a Rhai script, or
  a shell script command.
- Debugging why an extension cannot run `podman`, `brew`, or a terminal, or why
  a preference has no effect.

These are **launcher extensions**, not GNOME Shell extensions. They are not
loaded by GNOME Shell, do not have `metadata.json`, and are deliberately kept
outside `extensions/` so the GNOME validation suite does not mistake them for
Shell extensions.

## Pick the smallest tier

Compass offers three tiers (see upstream `docs/extensions.md`):

| Need | Tier |
|---|---|
| One-shot command whose output is text | Script command (shell/Python) |
| Searchable list with actions, no dependencies or build step | Rhai script |
| React views, npm packages, forms, credentials | TypeScript extension |

This directory uses the TypeScript tier because the extensions need React
views, preferences, and structured results.

## Manifest invariants

`package.json` follows the
[`extra/schemas/extension.json`](https://github.com/tuna-os/compass/blob/main/extra/schemas/extension.json)
schema. The load-bearing rules:

- `license` must be the literal `MIT`.
- `name` is the slugged identifier and must equal the folder name.
- Each `commands[].name` maps to `src/<name>.tsx` for `mode: "view"`
  (`vici build` rejects a `.ts` view command with "Unable to find view
  command"); `no-view` commands may use `.ts`. The validation gate enforces the
  same distinction.
- `icon` is a path under `assets/`; ship a real 512×512 PNG.
- `preferences` and `commands[].preferences` share one namespace: a command
  preference with the same `name` overrides the extension-level one.
- Dropdown `default` must be one of its own `data[].value` entries. A default
  outside that list is silently ignored.

`tests/test_compass_extensions.py` discovers every folder under
`compass-extensions/` at call time. There is no list to edit when adding one.

## API facts that differ from Raycast

Compass implements the `@vicinae/api` surface, but a few things are missing or
behave differently. Verify against the shipped API source before relying on a
Raycast example:

- **`AI.ask()` and `AI.getModels()` throw "not implemented".** Compass does not
  have an AI subsystem yet. The `ai-command` extension therefore calls provider
  HTTP APIs with `fetch`. Do not import `AI` expecting it to work.
- **`Detail` has no `isLoading` prop.** Put a "Thinking…" message in its
  `markdown` while work is pending.
- **No `@raycast/utils`** such as `usePromise`. Use React `useState`,
  `useEffect`, and plain promises with cleanup.
- `showToast({ style, title, message })` and
  `showToast(Toast.Style.Success, title, message)` both work;
  `Toast.Style.Failure` exists.
- `runInTerminal(argv, { hold, title, workingDirectory })` exists and goes
  through the engine's `xdg-terminal-exec` support. Use it for every GUI or
  host-side command. It does not select an emulator, so to honour a terminal
  preference, write a small wrapper script into `environment.supportPath` whose
  body is `exec <chosen-terminal> …`, then pass that script to `runInTerminal`.
  The engine has the session environment; the worker does not.
- Never `spawn()` a GUI program (terminal, browser, file manager) from the
  worker even though `/bin/sh` is executable. The worker's environment has no
  `DISPLAY`, `WAYLAND_DISPLAY`, or `XDG_RUNTIME_DIR`, so the program cannot find
  the session.
- `List.Item` accepts `keywords`, `icon` (including emoji strings), and
  `actions`. Avoid props you have not verified in `components/list.tsx`.

## Sandbox and host programs

Each TypeScript worker runs behind Landlock:

- Read: the system (`/usr`, `/etc`, …) and its own extension directory. In
  `$HOME`, only a small allowlist (`~/.ssh/config`, password store, a few WM
  configs). Everything else in home is denied.
- Write: only its support and assets directories. Use `LocalStorage` or
  `environment.supportPath` for state. `/tmp` is private per extension via
  `TMPDIR`.
- Execute: Node and system programs under `/usr`, `/bin`, `/lib`, `/app`.
  **Programs under `$HOME` are not executable here.** Linuxbrew's
  `/home/linuxbrew/.linuxbrew/bin/brew` is therefore unavailable to the worker.
- Network is allowed. `fetch` works with the worker's proxy environment, so a
  public JSON API is a better source for package search than a host CLI.
- No session sockets or session environment are handed to the worker: D-Bus,
  Wayland, `DISPLAY`, `XDG_RUNTIME_DIR` and `COMPASS_SOCKET` are withheld.
  Rootless `podman`/`distrobox` need the runtime dir and container storage under
  `$HOME`, which the worker cannot read. **Do not call their CLIs with
  `spawnSync` from a worker.** `container-terminal` runs discovery on the host
  instead: it writes a self-deleting script into `environment.supportPath`,
  hands it to `runInTerminal`, and the detached process writes tagged output to
  a file that the extension polls for. Bound the detached work with `timeout`
  below the polling deadline, and always write the completion marker, so a
  slow command cannot recreate temporary files after cleanup. Keep the script
  builder and output parser pure so they can be tested without an engine.
- GUI work must go through the engine APIs (`open`, `runInTerminal`,
  window-management), never a directly spawned process.
- Syscalls are filtered and memory is capped; avoid native addons and large
  buffers.

### Raycast shim overrides are a different mechanism

Compass has a host-command broker (`HostCommand/run`) that runs a program
outside the extension sandbox after asking the person. It is reached through
the Raycast compatibility shim's `child_process` patch and gated by
`extensions/raycast-linux-overrides.json` in the Compass repo (currently only
`brew`). `@vicinae/api` does not export a public wrapper for it, so native
extensions in this repo cannot call it. Do not add entries to that manifest
expecting a local extension to gain permissions; use `runInTerminal` instead.

## Conventions in this repo

- **Terminal choice is a preference.** Keep the terminal launch template in a
  single pure module. `{command}` is replaced by a fully shell-quoted command,
  written into a wrapper script and handed to `runInTerminal`. The script must
  delete itself (`rm -f "$0"`) and detach the chosen terminal with `setsid -f`;
  plain `exec` only replaces the shell inside the helper terminal window and
  leaves an extra blank window behind. A compound command (for example
  `start && exec`) must itself be quoted as one argument, otherwise the outer
  shell splits on `&&`. Never build a command string from unquoted container or
  package names.
- **Install commands go through `runInTerminal`,** so the user sees progress
  and can interact with prompts. The install argv is an array; no shell string
  interpolation.
- **Search does not depend on host CLIs.** `brew-search` queries
  formulae.brew.sh and caches a compact index with `Cache`; that works inside
  the sandbox and fixes cask-only searches. Container runtimes are discovered
  on the host with `command -v` inside the helper script, and a missing or
  failing runtime is skipped.
- **Keep pure logic in separate modules.** Parsers and command builders should
  not import `@vicinae/api`, so they can be exercised by the Node harnesses in
  `tests/` without a running engine.
- **No API keys in source.** Keys live in `password` preferences.
- **No hardcoded extension lists.** README and tests discover folders.

## Validate

Run from the repo root:

```bash
python3 -m unittest discover -s tests -t tests -v
python3 -m unittest discover -s tests -t tests -v -p 'test_compass_extensions.py'
```

`tests/test_compass_extensions.py` checks manifests, entrypoints (view commands
must be `.tsx`), icons, preference wiring (including dead preferences),
ESM/no-`eval` sources, and that no extension calls the unimplemented `AI` APIs.
It fails when zero extensions are discovered so the gate cannot pass vacuously.

`tests/test_compass_logic.py` runs `tests/compass_logic.mjs` to exercise
the pure parsers and command builders with real inputs. Add a scenario there
whenever a parsing or quoting rule changes.

A type check with the real SDK is a separate, optional step inside an extension
folder (`npm install && npx tsc --noEmit`). CI does not install npm packages for
these extensions; keep the Python gate authoritative for repo CI.

## Red flags

- A dropdown default that is not in its `data` list — the preference silently
  falls back or is ignored.
- A preference declared in `package.json` but read under a different name in
  source — the gate catches this; do not "fix" it by removing the check.
- Calling `AI.ask()` because a Raycast example does — it throws here.
- Assuming `~/.config` or `~/.local/share` is readable from the worker — use
  `LocalStorage` or the support directory.
- Quoting a container or package name into a shell command without
  `shellQuote`-style escaping.
