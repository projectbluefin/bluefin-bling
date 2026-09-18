---
name: factory-onboarding
version: "1.1"
last_updated: "2026-09-18"
id: factory-onboarding
one_line_purpose: Onboard bluefin-bling into the Project Bluefin factory model.
entry_point: docs/skills/factory-onboarding.md
category: meta
mcp_compliance_level: partial
optimization_status: draft
status: active
dependencies: []
tags: [factory, onboarding, setup, bluefin-bling]
description: >-
  How bluefin-bling operates inside the Project Bluefin agentic factory: agent
  onboarding, self-repair, the two-output rule, and banned anti-patterns. Use
  when starting a session here or auditing factory compliance against
  projectbluefin/common.
metadata:
  type: reference
  context7-sources:
    - /git_gitlab_gnome_org/gnome_gnome-shell
    - /websites/developer_gnome
    - /bootc-dev/bootc
---

# Factory Onboarding — bluefin-bling

Project Bluefin is an agentic OS factory. Agents implement. Humans set direction
and approve merges. The factory gets smarter only if agents write back what they
learn — otherwise every session starts from zero.

## When to Use

- Entering this repository for the first time in a session.
- Auditing whether this repo still satisfies the factory contract.
- Deciding where a learning belongs when it is not bling-specific.

## Authority model

`AGENTS.md` and this `docs/skills/` tree are **local authority** for
bluefin-bling. [`projectbluefin/common`](https://github.com/projectbluefin/common)
is attached as a **pinned shared-contract sidecar**: it supplies factory-wide
rules, and **it never overrides this repository's local authority.**

The canonical, factory-wide version of this document is
[`common/docs/skills/factory-onboarding.md`](https://github.com/projectbluefin/common/blob/main/docs/skills/factory-onboarding.md).
Read it there rather than expecting a copy here — common's own instruction is
that downstream repos *link* to it rather than copying the policy tree, because
a copy silently rots. This file records only what is specific to bling.

A missing or unreachable catalog is **degraded mode**, not permission to
substitute a sibling checkout or stale instructions.

## Agent Onboarding Sequence

When entering `projectbluefin/bluefin-bling`:

1. Read `AGENTS.md` — authoritative for layout, validation commands, and
   workflow boundaries.
2. Read [`docs/SKILL.md`](../SKILL.md) and load only the skills matching the task.
3. **Resolve the Hive assignment and GitHub issue through their APIs.** Verify
   repository, issue, branch target, and requested scope *before* editing. This
   repo is Hive-fed — several open PRs are authored by `kubestellar-hive[bot]` —
   so this step is load-bearing, not ceremonial.
4. Load `projectbluefin/common` as the pinned shared-contract sidecar.
5. **Check ownership, canonical labels, credential rules, and named human
   gates.** Do not simulate workflow state or bypass approval boundaries. For
   this repo the approval boundary is specified in
   [`pr-review-and-merge.md`](./pr-review-and-merge.md).
6. Use Context7 before touching any GNOME Shell, GJS, or bootc API, and record
   the library ID in `metadata.context7-sources`.
7. Keep one compact task record: task ID, verified repo and issue, skills
   loaded, evidence, confidence, learned facts.
8. Make the smallest scoped change, validate with the repo's own checks
   (`python3 -m unittest discover -s tests -t tests -v`, `node --check`,
   `glib-compile-schemas --strict --dry-run`), and hand off the record.

## Every-Loop Self-Repair

1. **Preflight** — verify repository, issue, branch target, and loaded skills.
2. **Detect** — treat stale, contradictory, or missing GNOME/bootc guidance as a
   repair signal. Do not silently fall back.
3. **Repair** — update the closest authoritative skill in `docs/skills/`.
4. **Validate** — rerun the smallest relevant check; confirm links and ownership
   still agree.
5. **Write back** — record durable learning and evidence in the same PR.
6. **Escalate** — stop for design, security, cross-repo breakage, merge, and
   production decisions. Autonomy repairs known failures; it does not
   manufacture approval.

## The Two-Output Rule

Every agent session produces two outputs:

1. **The work** — the PR, fix, or feature.
2. **The learning** — what a future agent needs to know.

Output 1 without Output 2 means the factory does not improve. The learning goes
into `docs/skills/` in the **same PR**, never a follow-up. See
[`skill-improvement.md`](./skill-improvement.md).

## What Is Banned

Delete these on sight.

- **Changelog files** — `CHANGELOG.md`, `CHANGES.md`, `IMPROVEMENTS.md`,
  `SESSION.md`, and anything similar. Agents append to them instead of updating
  skills; the result is a stale changelog beside skills that never improve.
- **Session logs committed to the repo** — `NOTES.md`, `PLAN.md`, `TODO.md`,
  progress files. Session state lives in the agent's session folder only.
- **"Append here" instructions.** Any doc saying "append when you ship
  something" is a hallucination magnet. Route to `docs/skills/` instead.

## Red Flags

Wrong-repository edits; stale catalog use; silent fallback; repeated failure
without a skill update; undocumented workarounds; and a task that ends without
evidence or durable learning.

## Cross-Repo Escalation

- Factory-wide learning → **open an issue** in `projectbluefin/common`, with the
  learning, the affected component, and the evidence in the body.
- `ublue-os/*` is **read-only**. Never open issues, PRs, or comments there. Tell
  the human to report upstream manually.

## Known deliberate divergence from common

`common`'s skill catalog constrains `category` to `ci-ops | test-authoring |
meta`, enforced by its `docs/skills/index.schema.json`. bluefin-bling is a GNOME
Shell extension repo where none of the three fit two of its skills, which use
`architecture` and `ui`. `common/docs/skills/write-a-skill.md` says to propose
widening the enum rather than mis-file, so this is **intentional, not drift** —
do not "fix" it by forcing those into `meta`. Tracked upstream in `common`.

bluefin-bling also has no `docs/skills/index.json`, `index.schema.json`, or
`scripts/generate_skill_index.py`. Those are common's catalog tooling and are
not part of the factory "Done When" checklist; the hand-written router in
[`docs/SKILL.md`](../SKILL.md) is this repo's catalog.

## Verification

```bash
# Factory "Done When" checklist, mechanically
ls docs/skills/skill-improvement.md docs/SKILL.md          # both must exist
grep -n "Self-Improvement\|Two-Output" AGENTS.md           # mandate present
ls CHANGELOG.md CHANGES.md IMPROVEMENTS.md SESSION.md NOTES.md PLAN.md TODO.md 2>/dev/null \
  || echo "no banned artifacts (correct)"

# Every skill is routed from the router
for f in docs/skills/*.md; do
  grep -q "$(basename "$f")" docs/SKILL.md || echo "UNROUTED: $f"
done
```
