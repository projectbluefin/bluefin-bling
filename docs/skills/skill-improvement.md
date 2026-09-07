---
name: skill-improvement
version: "1.0"
last_updated: "2026-09-06"
id: skill-improvement
one_line_purpose: How to maintain and improve the skill catalog in bluefin-bling.
entry_point: docs/skills/skill-improvement.md
category: meta
mcp_compliance_level: full
status: active
tags: [skills, factory, improvement]
description: >-
  Operational guidance for updating skill files as part of the two-output rule.
  Explains what belongs in docs/skills/, how to capture GNOME Shell and bootc
  learnings, and what to avoid.
metadata:
  type: guide
---

# Skill Improvement in bluefin-bling

Every agent session must leave the repository smarter than it found it.

---

## The Rule

When working on a feature, bugfix, or refactor:
1. Complete the implementation.
2. Update or create the corresponding skill document in `docs/skills/`.
3. Commit both in the **same PR**.

---

## What Belongs in a Skill File

| Category | Example from bluefin-bling |
|---|---|
| **Subprocess Cancellation** | "Cancelling `Gio.Cancellable` passed to `communicate_utf8_async` only cancels the local stream; child process continues running unless killed with `proc.force_exit()`." |
| **bootc Permission Model** | "`bootc status` calls `prepare_for_write()` requiring root privileges; unprivileged processes should handle exit codes gracefully without permanently disabling retries." |
| **GNOME Shell Quick Settings** | "`ShutdownItem` inside `SystemItem` can be accessed via `qs._system._systemItem.menu.sourceActor`." |
| **HIG Copy Conventions** | "Use plain conversational language for notifications: 'Sync Folder Sharing Enabled — Your files are sharing with your other devices.' Avoid technical jargon like 'peer-to-peer'." |

---

## What Does NOT Belong

- Task progress notes or session logs
- Generic JavaScript tutorials found in standard MDN docs
- One-off temporary debugging print statements
- Opinions without technical evidence or citations

---

## Checklist Before Finishing Any Task

- [ ] Discovered a non-obvious GNOME Shell API behavior or workaround?
- [ ] Documented it in the appropriate `docs/skills/*.md` file?
- [ ] Checked frontmatter for Context7 citations?
- [ ] Committed in the same PR as the implementation code?
