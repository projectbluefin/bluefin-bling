---
name: factory-onboarding
version: "1.0"
last_updated: "2026-09-06"
id: factory-onboarding
one_line_purpose: Onboard bluefin-bling into the Project Bluefin factory model.
entry_point: docs/skills/factory-onboarding.md
category: meta
mcp_compliance_level: full
optimization_status: active
status: active
dependencies: []
tags: [factory, onboarding, setup, bluefin-bling]
description: >-
  How bluefin-bling operates within the Project Bluefin agentic factory model.
  Defines the self-improvement mandate, agent onboarding sequence, every-loop
  self-repair, and banned anti-patterns.
metadata:
  type: reference
  context7-sources:
    - /git_gitlab_gnome_org/gnome_gnome-shell
    - /websites/developer_gnome
    - /bootc-dev/bootc
---

# Factory Onboarding — bluefin-bling

Project Bluefin is an agentic OS factory. Agents implement. Humans set direction and approve merges.

The factory gets smarter only if agents write back what they learn. Without that, every session starts from zero.

---

## Agent Onboarding Sequence

When entering `projectbluefin/bluefin-bling`:

1. Read `AGENTS.md`. It is authoritative for repository layout, validation commands, and workflow boundaries.
2. Read `docs/SKILL.md` to load the exact skill matching the task.
3. Use Context7 before touching any GNOME Shell, GJS, or bootc APIs.
4. Reference `projectbluefin/common` as the pinned shared-contract sidecar.
5. Create two outputs: **The Work** and **The Learning** (`docs/skills/`).
6. Validate all changes with `node --check` and `glib-compile-schemas --strict`.

---

## Every-Loop Self-Repair

1. **Preflight:** Verify repository, issue, branch target, and loaded skills.
2. **Detect:** Treat stale, contradictory, or missing GNOME/bootc guidance as a repair signal.
3. **Repair:** Update the closest authoritative skill file in `docs/skills/`.
4. **Validate:** Confirm syntax and build cleanliness with zero warnings.
5. **Write back:** Record durable learnings in the same PR.
6. **Escalate:** Stop and prompt for human approval on security or design gates.

---

## The Two-Output Rule

Every agent session produces two outputs:

1. **The work** — the PR, fix, or feature
2. **The learning** — what a future agent needs to know

The learning goes into `docs/skills/` in the same commit/PR.

---

## What Is Banned

- **Changelog files** (`CHANGELOG.md`, `IMPROVEMENTS.md`, `SESSION.md`). Delete on sight.
- **Session notes committed to git** (`NOTES.md`, `TODO.md`, `PLAN.md`).
- **"Append here" instructions.** Route all learnings to `docs/skills/`.

---

## Cross-Repo Escalation

If a pattern or fix applies factory-wide across Bluefin:
- Open an issue or PR in `projectbluefin/common`.
- Never touch upstream `ublue-os/*` checkouts directly.
