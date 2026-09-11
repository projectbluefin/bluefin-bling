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
their settings schemas, subprocess invocations (systemctl, bootc), preference
handling, and any workflow automation added later.

Report vulnerabilities in GNOME Shell, Syncthing, bootc, or other third-party
components to their respective upstream projects unless the issue is
introduced by this repository's integration.

## Safe handling

Do not commit credentials, private keys, tokens, or exploit payloads. Preserve
input validation on dconf-writable settings (e.g. `service-name`) and discrete
argv subprocess invocation — never interpolate settings into shell command
lines.
