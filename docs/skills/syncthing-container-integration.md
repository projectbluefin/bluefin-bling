---
name: syncthing-container-integration
version: "1.0"
last_updated: "2026-10-02"
id: syncthing-container-integration
one_line_purpose: Operate the API-backed Sync Folder toggle and its rootless Syncthing Quadlet safely.
entry_point: docs/skills/syncthing-container-integration.md
category: architecture
mcp_compliance_level: partial
optimization_status: draft
status: active
dependencies: [gnome-shell-extension-dev, quick-settings-integration]
tags: [syncthing, containers, quadlet, xdg, gjs]
description: >-
  Use when changing Sync Folder container setup, authenticated REST requests,
  XDG folder defaults, native-service migration, sharing persistence, or pending
  invitation notifications.
metadata:
  type: reference
  context7-sources:
    - /websites/syncthing_net
    - /websites/podman_io_en
    - /websites/gjs-docs_gnome
    - /gnome/libsoup
---

# Syncthing container integration

## When to Use

- Changing `extensions/syncthing-toggle/toggle.js`, `service.js`, or the container template.
- Diagnosing a failed first start, native-service upgrade, or login startup choice.
- Verifying peer sync without exposing real Documents.

## Ownership and desktop UX

The Quick Settings toggle is the primary surface. Keep its `SystemIndicator` container and `quickSettingsItems`, but do not call `_addIndicator()`; no separate panel icon is needed. Sharing Settings opens Syncthing's existing web UI for folder selection and device approval.

`toggle.js` uses asynchronous Soup 3 requests and cancellable subprocesses. `service.js` is a separate, short-lived GJS process: synchronous filesystem checks and XDG lookup belong there, never in the compositor. Copy the **whole extension directory**, including `service.js` and `syncthing.container.in`, when packaging it; a hardcoded JS install list loses runtime assets.

## Container and credentials

The template runs the official pinned image as the user's UID/GID with `UserNS=keep-id`, dropped capabilities, no new privileges, and host networking. Bind the GUI explicitly to `http://127.0.0.1:<port>`: the image's default `0.0.0.0:8384` is unsafe with host networking. The explicit HTTP scheme also prevents an inherited daemon TLS setting from changing the local client's endpoint. See [official container usage](https://github.com/syncthing/syncthing/blob/main/README-Docker.md) and [runtime overrides](https://docs.syncthing.net/users/syncthing.html).

Use `PodmanArgs=--stop-timeout=30` rather than the newer `StopTimeout` Quadlet key so the same graceful-stop behavior also generates successfully with Podman 4.9. See [the supported 4.9 Quadlet options](https://docs.podman.io/en/v4.9.3/markdown/podman-systemd.unit.5.html).

State lives under `$XDG_STATE_HOME/syncthing` (normally `~/.local/state/syncthing`). Preserve its certificate, key, config and database; the daemon creates identity itself for a fresh installation. The helper writes private `container.env` and `desktop.json`; state is `0700`, managed files `0600`. `STGUIAPIKEY` is an OS-random key retained across setup calls. Read it asynchronously and send `X-API-Key`; never put it in command argv, logs, or screenshots. Use `Soup.Session({proxy_resolver: null})` so loopback credentials do not follow a system proxy.

The helper runs with the same UID as GNOME Shell, without elevation. It checks configuration/state ancestors for symlinks and unsafe writable modes, requires current-user ownership for private state and the Quadlet directory, and accepts managed files only as current-user-owned, singly linked regular files. Existing Quadlets must carry the management marker; existing desktop metadata and credential files must satisfy their managed formats. Refuse unrelated or unsafe targets rather than overwriting them. These checks and private permissions do not isolate credentials from other processes running as the same user or from root.

API authentication does not replace GUI username/password authentication on a multi-user computer. The web UI exposes that choice separately. See [REST authentication](https://docs.syncthing.net/dev/rest.html) and [Soup proxy behavior](https://github.com/GNOME/libsoup/blob/master/libsoup/soup-session.c).

`SecurityLabelDisable=true` is a deliberate, human-approved tradeoff for this rootless container: existing desktop SELinux labels remain unchanged, but container SELinux confinement is disabled. DAC, user namespace and restricted mounts remain. Do not silently replace this with recursive `:Z` relabeling of user directories.

## XDG presets and consent

Read the [standard shell-format XDG configuration](https://www.freedesktop.org/wiki/Software/xdg-user-dirs/) in the standalone helper with a fixed POSIX shell lookup: quote the configuration filename and print the predefined directory variable without re-evaluating its value. GLib can retain literal backslashes, while older `xdg-user-dir` scripts split configuration paths containing spaces and re-evaluate quoted directory values. Directory types come only from the helper's fixed preset list, never request/settings input; this reads the same user-owned shell configuration as the XDG utility, without elevation. Check that each path exists and is a directory. Skip disabled/unset/missing directories, the home directory, and root/ancestors; do not create a guessed `~/Documents` or mount the whole home.

Use stable ASCII IDs independently of localized or relocated paths. Documents is initially unpaused; Desktop, Downloads, Music, Pictures, Videos, Templates and Public are paused opt-ins when available. Mount selected paths at their same absolute container paths so existing folder configuration remains valid.

Obtain local identity from `GET /rest/system/status` (`myID`). Folder defaults can contain **peers**, so override `devices` with the local device only. Add initial missing folders through the granular [configuration endpoints](https://docs.syncthing.net/rest/config.html). Preserve existing IDs/paths, sharing permissions, labels and paused choices. The successful provisioning marker prevents later clicks from recreating deleted presets. Unpausing a preset does not authorize sharing it with a peer; approval remains explicit.

## Native migration and persistence

First-use migration must preserve existing folders, not merely XDG presets. A running native daemon is inspected through the official container's supported `cli config dump-json`, an authenticated REST client with a read-only state mount. Parse only its `folders` array; the root response contains private GUI values and must never be logged. `cli config folders dump-json` is **not** supported by the pinned image.

For a stopped native installation, the helper authorizes temporary startup only for the unmodified, root-owned packaged `syncthing.service`. The user's on request starts it, waits for the explicit bounded readiness handshake, captures paths, and restarts into the generated container. Unknown units or unsafe/unavailable folders fail without overwriting state. Roll back only a service this request started, never an already-running user's service.

Persist captured migration mounts separately from explicit extra mounts. Preserve restart intent until successful health/provisioning acknowledgement. Remove only the owned legacy `default.target.wants/syncthing.service` link to the known packaged native unit; leaving it behind defeats an off choice at the next login.

New persistence comes from Quadlet's `[Install] WantedBy=default.target`, followed by `daemon-reload`; generated services cannot be enabled with `systemctl enable`. Automatic metered pause does not erase the user's persistent on intent. Start/Stop only leaves login intent unchanged. See [Quadlet enable semantics](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html).

Retiring a custom share is a configuration operation, not a data deletion. Remove its peer offers on every sharing host before deleting its folder definition through `/rest/config/folders/<id>`; otherwise a peer with `autoAcceptFolders` can offer it again. Remove its mount from both `extraPaths` and captured `legacyPaths` in `desktop.json`, then use `service.js` to regenerate the Quadlet and acknowledge provisioning only after restart/API health. Clearing explicit extras alone preserves captured migration mounts. Compare user-file inventories separately from `.stfolder` metadata, which Syncthing removes when a folder is unconfigured.

## Pending invitations

Read `cluster/pending/devices` and `cluster/pending/folders` through the authenticated API. Device offers are keyed by device ID; folder offers by **folder ID and offering device ID**, so a second peer offering the same folder is new. Replace announcement memory only after both responses are valid and the request is still current. Transport, HTTP and JSON failures preserve it; a successful empty response removes resolved offers, allowing a later re-offer to notify again. Overlapping polls share one in-flight read. This memory is per indicator instance, not durable sharing approval: notifications only direct the user to Sharing Settings and never accept a device or folder.

## Red Flags

- Editing daemon XML while it runs: its next save can overwrite external edits. Use its live API.
- Guessing local identity from XML order or defaults; inheriting peers into a new folder.
- Announcing success from container spawn alone, or letting a delayed on override a newer off.
- Clearing invitation memory on network/HTTP/JSON failure. Folder offers are keyed by folder **and offering device**.
- Treating module reload as fresh code. GNOME may reject a new extension version in the current session; save work and log out/in rather than weakening production guards.
- Testing with real Documents before explicit sharing approval. Use disposable folders and compare file hashes on every peer.

## Verification

```bash
python3 -m unittest discover -s tests -t tests -v
# Real helper dependencies and guarded coverage: see extension-validation.md
python3 -m unittest discover -s tests -t tests -v -p 'test_syncthing_service_behavior.py'
# Re-derive private-file ownership and request/credential handling from shipped sources
grep -nE 'function (directory|ownedFile|writePrivate|apiKey|prepare)|STGUIADDRESS=|Refusing to overwrite' extensions/syncthing-toggle/service.js
grep -nE 'proxy_resolver|X-API-Key|cluster/pending|offeredBy|_announcedPending' extensions/syncthing-toggle/toggle.js
systemctl --user show syncthing.service -p FragmentPath -p SourcePath -p ActiveState
podman ps --filter name=systemd-syncthing --format '{{.Names}} {{.Image}} {{.Status}}'
gnome-extensions info syncthing-toggle@projectbluefin.io
```

Read [`extension validation`](extension-validation.md) for GJS, XDG utility,
generator and packaged-native-unit prerequisites and skip behavior. Node harness
or generator success is not proof of the desktop surface or real peer sync.

For runtime proof, use a unique test subdirectory inside an empty, already-mounted XDG preset such as Public; capture its full configuration, temporarily share it only with approved peers, and restore it afterwards. A new path outside existing mounts requires container regeneration/restart, and sharing an existing populated folder transfers its real data. Write distinct fixtures on every node and compare explicit fixture SHA-256 hashes, excluding node-local `.stfolder` markers. Pause the test folder on one node, prove an active peer receives a new fixture while the paused node does not, then resume and prove convergence. Check `/rest/db/status` and `/rest/db/completion` for each tested folder: `/rest/system/error` alone does not report all folder failures. Recreate a container and verify identity and user-edited folder choices survive. Remove only test-owned files; restore folder sharing/pause choices and any test mounts. Retain real peer approvals only when the user requested them.
