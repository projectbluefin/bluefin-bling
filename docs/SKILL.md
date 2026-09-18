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
| Understand the operating contract, PR rules, or factory model | [`AGENTS.md`](../AGENTS.md) |
| Check onboarding rules or factory alignment with `projectbluefin/common` | [`skills/factory-onboarding.md`](skills/factory-onboarding.md) |
| **Extension development** | |
| Build or modify a GNOME Shell extension (GNOME 45+ ESM, lifecycle, cleanup) | [`skills/gnome-shell-extension-dev.md`](skills/gnome-shell-extension-dev.md) |
| Work with Quick Settings toggles, indicators, or desktop notifications | [`skills/quick-settings-integration.md`](skills/quick-settings-integration.md) |
| Inspect or integrate with pure `bootc` image status or system updates | [`skills/gnome-shell-extension-dev.md`](skills/gnome-shell-extension-dev.md) |
| Work with GSettings schemas or preferences UI | [`skills/gnome-shell-extension-dev.md`](skills/gnome-shell-extension-dev.md) |
| **Quality & Lifecycle Hygiene** | |
| Validate an extension or add a test | [`skills/extension-validation.md`](skills/extension-validation.md) |
| Debug asynchronous I/O, `Gio.Subprocess`, `Gio.FileMonitor`, or cancellables | [`skills/gnome-shell-extension-dev.md`](skills/gnome-shell-extension-dev.md) |
| Ensure zero resource leaks upon `disable()` | [`skills/gnome-shell-extension-dev.md`](skills/gnome-shell-extension-dev.md) |
| **Review and merge** | |
| Review, approve, or merge a PR; work out why a green PR will not merge | [`skills/pr-review-and-merge.md`](skills/pr-review-and-merge.md) |
| Count approvals correctly, or debug a stuck merge queue | [`skills/pr-review-and-merge.md`](skills/pr-review-and-merge.md) |
| **Factory and self-improvement** | |
| Learn how to record discovered patterns, workarounds, or conventions | [`skills/skill-improvement.md`](skills/skill-improvement.md) |
| Complete a task and write back learnings in the same PR | [`skills/skill-improvement.md`](skills/skill-improvement.md) |

---

## Scope Rules

- **Everything needs a PR — docs included.** `main` rejects direct pushes
  (`Changes must be made through a pull request` / `through the merge queue`).
  Branch, open a PR, get two write-permission approvals, let the queue merge it.
  See [`skills/pr-review-and-merge.md`](skills/pr-review-and-merge.md).
- **One PR per feature.** Never batch unrelated changes.

---

## Improving Skill Docs

All files in `docs/skills/` are authoritative operational knowledge. Update them in the **same PR** as the work — never a follow-up. See [`skills/skill-improvement.md`](skills/skill-improvement.md) for what to write and [`skills/pr-review-and-merge.md`](skills/pr-review-and-merge.md) for how it lands.
