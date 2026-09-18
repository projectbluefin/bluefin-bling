---
name: skill-improvement
version: "1.2"
last_updated: "2026-09-18"
id: skill-improvement
one_line_purpose: How to maintain and improve the skill catalog in bluefin-bling.
entry_point: docs/skills/skill-improvement.md
category: meta
mcp_compliance_level: partial
optimization_status: draft
status: active
dependencies: []
tags: [skills, factory, improvement, verification]
description: >-
  Explains the two-output rule for bluefin-bling: what belongs in docs/skills/,
  how to capture GNOME Shell and bootc learnings, and the verification rule for
  project-internal facts. Use when finishing any task or deciding whether to
  update a skill.
metadata:
  type: reference
---

# Skill Improvement in bluefin-bling

Every agent session must leave the repository smarter than it found it.

This is the local adaptation of
[`projectbluefin/common/docs/skills/skill-improvement.md`](https://github.com/projectbluefin/common/blob/main/docs/skills/skill-improvement.md).
Common supplies the factory-wide rule; this file adds what is specific to a
GNOME Shell extension monorepo. Where the two differ on a factory-wide matter,
common wins — but common never overrides this repo's local authority.

## When to Use

- You are about to mark any task complete.
- You discovered a non-obvious API behaviour, workaround, or repo convention.
- You are writing documentation that states a project-internal fact.
- You are deciding whether to create a new skill or extend an existing one.

## The Rule

When working on a feature, bugfix, or refactor:

1. Complete the implementation.
2. Update or create the corresponding skill document in `docs/skills/`.
3. Commit both in the **same PR**. Never a follow-up.

## Every-Loop Repair Contract

Self-repair runs on every task, not only when something breaks. A *successful*
task still checks for reusable learning and documentation drift before it ends.

1. **Preflight** — verify repo, issue, branch target, and which skills you loaded.
2. **Detect** — treat stale, contradictory, or missing guidance as a repair
   signal. Do not silently work around it.
3. **Repair** — update the closest authoritative skill when the fix is in scope
   and source-backed.
4. **Validate** — rerun the smallest relevant check and confirm links and
   ownership still agree.
5. **Write back** — record the durable learning and the evidence behind it.
6. **Escalate** — stop at design, security, cross-repo breakage, and merge
   decisions. Autonomy repairs known failures; it does not manufacture approval.

## Project-internal fact drift is a first-class failure mode

When an agent documents a project-internal fact — a test module name, a CI
trigger, a schema key, a workflow output — and gets it wrong because it used
training data or memory instead of reading the source, that is a **skill
failure**, not a typo.

**The rule:** any skill file containing project-internal facts **must** carry a
`## Verification` section with the exact commands to re-derive those facts from
source. See [`extension-validation.md`](./extension-validation.md) for the
reference implementation in this repo.

This is not theoretical here. An audit on 2026-09-18 found that
`extension-validation.md` documented three of five test modules, described two
conditional invariants as unconditional, and named a hand-written
`glib-compile-schemas` path that CI does not use. Every one of those would have
been caught by a verification command.

## What Belongs in a Skill File

| Category | Example from bluefin-bling |
|---|---|
| **Subprocess Cancellation** | "Cancelling `Gio.Cancellable` passed to `communicate_utf8_async` only cancels the local stream; the child process continues running unless killed with `proc.force_exit()`." |
| **bootc Permission Model** | "`bootc status` calls `prepare_for_write()` requiring root privileges; unprivileged processes should handle exit codes gracefully without permanently disabling retries." |
| **GNOME Shell Quick Settings** | "`ShutdownItem` inside `SystemItem` can be accessed via `qs._system._systemItem.menu.sourceActor`." |
| **HIG Copy Conventions** | "Use plain conversational language for notifications: 'Sync Folder Sharing Enabled — Your files are sharing with your other devices.' Avoid technical jargon like 'peer-to-peer'." |
| **Repo mechanics** | "Approvals count only from reviewers with write permission; a merge queue needs a `merge_group:` trigger or nothing can ever merge." → [`pr-review-and-merge.md`](./pr-review-and-merge.md) |

## What Does NOT Belong

- Task progress notes or session logs.
- Generic JavaScript tutorials already covered by MDN or the GJS docs.
- One-off debugging output.
- Opinions without technical evidence or citations.
- **Contradictions.** If a skill says X and you want to say not-X, update that
  skill to say not-X. Do not add a second doc that disagrees with the first.

## Which Skill File to Update

Use the closest matching existing skill. Create a new one **only** when the
change introduces a reusable domain with no existing home.

| Learning is about… | Write it to |
|---|---|
| Extension code, lifecycle, GJS/Gio APIs | [`gnome-shell-extension-dev.md`](./gnome-shell-extension-dev.md) |
| Quick Settings surfaces, notifications, HIG copy | [`quick-settings-integration.md`](./quick-settings-integration.md) |
| The test suite, CI, or an invariant | [`extension-validation.md`](./extension-validation.md) |
| Review, approval, or merge mechanics | [`pr-review-and-merge.md`](./pr-review-and-merge.md) |
| Factory alignment with common | [`factory-onboarding.md`](./factory-onboarding.md) |
| Something factory-wide, not bling-specific | Open an issue in `projectbluefin/common` with the learning, affected component, and evidence |
| `ublue-os/*` | **NEVER.** It is read-only. Tell the human to report upstream manually |

## Red Flags

- A task that ends with no durable learning and no evidence.
- Repeating a failure a previous session already hit, because nobody wrote it down.
- Documenting a project-internal fact with no command to re-derive it.
- A changelog, session log, or "append here" file. Delete on sight — see
  [`factory-onboarding.md`](./factory-onboarding.md).
- Adding a skill because one "should exist", rather than because a real session
  learned something.

## Checklist Before Finishing Any Task

- [ ] Discovered a non-obvious API behaviour, workaround, or convention?
- [ ] Documented it in the closest existing `docs/skills/*.md`, or created one?
- [ ] Every project-internal fact backed by a `## Verification` command?
- [ ] Front-matter complete, `description` ≤256 chars and carrying a "Use when" clause?
- [ ] Body has `## When to Use` and `## Red Flags`?
- [ ] Context7 sources recorded in `metadata.context7-sources` for any external API?
- [ ] File under 200 lines?
- [ ] Routed from [`docs/SKILL.md`](../SKILL.md) and listed in `README.md`?
- [ ] Committed in the same PR as the implementation?

## Verification

```bash
# Every skill routed from the router, and every routed file existing
ls docs/skills/*.md
grep -o 'docs/skills/[a-z-]*\.md' docs/SKILL.md | sort -u

# Front-matter present on every skill
head -1 docs/skills/*.md

# Size budget (200 soft, 500 hard)
wc -l docs/skills/*.md

# No banned artifacts
ls CHANGELOG.md CHANGES.md IMPROVEMENTS.md SESSION.md NOTES.md PLAN.md TODO.md 2>/dev/null || echo "none present (correct)"
```
