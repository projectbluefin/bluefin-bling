---
name: skill-router
description: >-
  Routes agent tasks to the correct skill file for the bluefin-bling project.
  Use at session start to find the right skill before acting. Covers GNOME Shell
  extension development, Quick Settings integration, bootc integration,
  lifecycle hygiene, and factory compliance.
---

# bluefin-bling Skill Router

Agent entry point for `projectbluefin/bluefin-bling`. Load only the skill(s) that match your task.

## Task → Skill

| I need to… | Load |
|---|---|
| **Session start / orientation** | |
| Understand the operating contract, PR rules, or factory model | `AGENTS.md` |
| Check onboarding rules or factory alignment with `projectbluefin/common` | `docs/skills/factory-onboarding.md` |
| **Extension development** | |
| Build or modify a GNOME Shell extension (GNOME 45+ ESM, lifecycle, cleanup) | `docs/skills/gnome-shell-extension-dev.md` |
| Work with Quick Settings toggles, indicators, or desktop notifications | `docs/skills/quick-settings-integration.md` |
| Inspect or integrate with pure `bootc` image status or system updates | `docs/skills/gnome-shell-extension-dev.md` |
| Work with GSettings schemas or preferences UI | `docs/skills/gnome-shell-extension-dev.md` |
| **Quality & Lifecycle Hygiene** | |
| Debug asynchronous I/O, `Gio.Subprocess`, `Gio.FileMonitor`, or cancellables | `docs/skills/gnome-shell-extension-dev.md` |
| Ensure zero resource leaks upon `disable()` | `docs/skills/gnome-shell-extension-dev.md` |
| **Factory and self-improvement** | |
| Learn how to record discovered patterns, workarounds, or conventions | `docs/skills/skill-improvement.md` |
| Complete a task and write back learnings in the same PR | `docs/skills/skill-improvement.md` |

---

## Scope Rules

- **Doc tasks** (`docs/`, `AGENTS.md`, `README.md`) → push directly to `main`, no PR needed.  
  Before pushing, verify: `git diff --cached --name-only` must show only `docs/*`, `AGENTS.md`, or `README.md`.
- **Implementation tasks** → branch + PR targeting `main`.
- **One PR per feature.** Never batch unrelated changes.

---

## Improving Skill Docs

All files in `docs/skills/` are authoritative operational knowledge. Update them in the **same PR** as the work — never a follow-up. See `docs/skills/skill-improvement.md`.
