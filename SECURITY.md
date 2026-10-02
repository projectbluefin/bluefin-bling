# Security policy

## Reporting

Report security vulnerabilities through [GitHub Private Vulnerability Reporting](https://github.com/projectbluefin/bluefin-bling/security/advisories/new). Do not disclose an unpatched vulnerability in a public issue.

Include:

- vulnerability description and impact
- reproduction steps or proof of concept
- affected extension (`syncthing-toggle`, `power-status-color`) and GNOME Shell version
- suggested mitigation, if available

## Response

The maintainers acknowledge reports within 48 hours and aim to assess them
within 7 days. Fix and disclosure timing depends on severity and coordination
with affected upstreams.

## Scope

This policy covers the GNOME Shell extensions in this repository, including
their settings schemas, preference handling, subprocess invocations, standalone
deployment helpers, and packaged runtime assets. Syncthing integration scope
includes `service.js`, `syncthing.container.in`, generated user Quadlets and
rootless Podman invocations, private state and credentials, and authenticated
loopback HTTP requests, as well as workflow automation.

Report vulnerabilities in GNOME Shell, Syncthing, bootc, or other third-party
components to their respective upstream projects unless the issue is
introduced by this repository's integration.

## Syncthing boundaries

The deployment helper and rootless container operate as the desktop user's UID;
this is not an elevated system service or an isolation boundary against other
processes running as that user. The container mounts its private state and
selected XDG, migrated or explicitly supplied folders, not the whole home.
Mounting a folder does not grant a peer permission to sync it: device and folder
sharing approval remains in Syncthing's web UI.

The managed GUI/API endpoint is HTTP on `127.0.0.1`, with REST requests
authenticated by `X-API-Key`. Loopback is reachable by other local users; API-key
authentication does not configure or replace GUI username/password protection.
State is private (`0700`), and helper-managed credential, metadata and Quadlet
files are `0600`. These permissions do not protect credentials from the same UID
or root. Preserve credentials and engine-owned identity/configuration during
migration; keep keys and full daemon configuration out of argv, logs and reports.

The human-approved `SecurityLabelDisable=true` tradeoff, ownership checks,
migration rules and verification procedures are authoritative in
[`Syncthing container integration`](docs/skills/syncthing-container-integration.md).
Changes to this boundary require human security review; do not silently replace
it with recursive relabeling of desktop directories.

## Safe handling

Do not commit credentials, private keys, tokens, or exploit payloads. Preserve
input validation on dconf-writable settings (e.g. `service-name`) and discrete
argv subprocess invocation — never interpolate settings into shell command
lines.
