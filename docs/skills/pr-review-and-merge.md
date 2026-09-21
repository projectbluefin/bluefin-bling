---
name: pr-review-and-merge
version: "1.1"
last_updated: "2026-09-21"
id: pr-review-and-merge
one_line_purpose: Review, approve, and merge PRs under the main ruleset and merge queue.
entry_point: docs/skills/pr-review-and-merge.md
category: ci-ops
mcp_compliance_level: partial
optimization_status: draft
status: active
dependencies: []
tags: [review, merge-queue, rulesets, governance, ci]
description: >-
  Documents how review approval and merging actually work in bluefin-bling: the
  main ruleset, which approvals count, and the merge queue's merge_group
  requirement. Use when reviewing, approving, or merging a PR, or when a green
  PR will not merge.
metadata:
  type: runbook
---

# PR Review and Merge

## When to Use

- Reviewing, approving, or merging any PR in this repo.
- A PR is green and approved but the merge button is unavailable.
- A PR is sitting in the merge queue and nothing is happening.
- Counting approvals and getting a different answer than GitHub does.

## What `main` actually requires

Two active rulesets govern `main`: `main — review policy` (22705148) and
`main — merge queue` (23208287). Derive them rather than trusting this list —
see [Verification](#verification).

| Requirement | Value |
|---|---|
| Approving reviews | **1** |
| Required status check | `validate extensions (tests, node --check, glib-compile-schemas)` |
| Dismiss stale reviews on push | yes |
| Require approval of most recent push | no |
| Require extra approval for unattributed changes | yes |
| Merge method | **squash** |
| Direct push to `main` | **rejected, no exceptions** |
| Merge queue | **active** (ruleset 23208287, squash merge) |

There is no doc-only fast path. `AGENTS.md` and `docs/SKILL.md` both claimed one
until 2026-09-18; the ruleset rejects every direct push:

```
remote: error: GH013: Repository rule violations found for refs/heads/main.
remote: - Required status check "validate extensions (...)" is expected.
remote: - Changes must be made through a pull request.
```

### Merge queue configuration

Ruleset `main — merge queue` (23208287) manages landing changes on `main` using
squash merges. Because both rulesets enforce branch protection with
`bypass_actors: []`, all PRs land via the merge queue once required status
checks pass and approvals are met.

When `gh pr merge <PR>` is run, GitHub adds the PR to the merge queue, which
spawns a `merge_group` workflow run against a temporary merge group ref. Once
`validate extensions` completes successfully, GitHub merges the PR automatically.

## Only write-access approvals count

This is the single most common source of confusion here.

GitHub counts an approving review toward `required_approving_review_count`
**only if the reviewer has write permission.** An approval from a read-only
collaborator renders identically in the UI, shows as `APPROVED` in
`gh pr view --json reviews`, and contributes **nothing**.

> "If you enable required reviews, collaborators can only push changes to a
> branch via a pull request that is approved by the required number of
> reviewers **with write permissions**."
> — [GitHub docs, available rules for rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)

Symptom: a PR shows two green approvals but `reviewDecision` is
`REVIEW_REQUIRED`. Do not go looking for a ruleset bug. Check permissions.

Permissions also change. On 2026-09-18 six collaborators went from `read` to
`write` inside a single session, which silently turned several 1-of-2 PRs into
2-of-2. **Re-derive the qualifying reviewer set at review time; never cache it.**

Corollaries:
- You cannot approve your own PR. An author needs a write-access reviewer other than themselves.
- Multiple approvals from the same person count once.

## The merge queue needs a `merge_group` trigger

`main` merges through a merge queue. The queue builds a **temporary
`merge_group` ref** and waits for the required status check to report against
*that ref* — not against the PR head.

**A workflow without a `merge_group:` trigger can never satisfy it.** No run is
started, the context never reports, and the entry sits in `AWAITING_CHECKS`
until `check_response_timeout_minutes` (90) expires and is dropped. Re-queueing
repeats this forever.

This is not hypothetical. The merge-queue ruleset was created
2026-09-13T20:07Z; `ci.yml` had no `merge_group:` trigger; the last successful
merge was #25 on 2026-09-12. **Nothing merged for six days** and eleven green,
reviewed PRs piled up behind a CI plumbing gap that no PR was responsible for.

So `.github/workflows/ci.yml` must keep:

```yaml
on:
  merge_group:
    types: [checks_requested]
```

The job name must match the required context **exactly**, and the job must be
event-agnostic — no `if:`, no path filters, no `github.event.pull_request.*`
references, or it will not instantiate for `merge_group`.

## A required job must also be bounded

A required context that *hangs* stalls the queue the same way a context that
never starts does — the entry stays in `AWAITING_CHECKS` until
`check_response_timeout_minutes` (90) drops it, and the runner keeps burning.
GitHub's default job timeout is **360 minutes**, so an unbounded job can hold a
queue slot four times longer than the queue is willing to wait, once per
attempt.

Every assertion in the suite spawns `node` (harness scenarios, `node --check`,
`node -e`); a child that never writes to stdout and never exits produces a run
with no output and no end. So the required job carries an explicit bound:

```yaml
jobs:
  extension-validation:
    name: validate extensions (tests, node --check, glib-compile-schemas)
    runs-on: ubuntu-latest
    timeout-minutes: 15
```

Healthy runs finish in 20s–2m, so 15 minutes is generous; it exists to convert
a hang into a fast, visible failure. Put it on the **job**, not a step — a step
timeout leaves the rest of the job free to hang. Changing the bound never
changes the job `name:`, which is the required status context.

## Triage order for a PR that will not merge

Work down this list; stop at the first that explains it.

1. `mergeStateStatus` — `DIRTY` means conflicts, `BEHIND` means stale branch.
2. Required check on the **head SHA** — is it `success`? A PR whose branch
   predates `ci.yml` has *no* checks at all, which is not the same as passing.
   Merge `main` into it to make CI run.
3. `reviewDecision` — `REVIEW_REQUIRED` means not enough *counting* approvals;
   `CHANGES_REQUESTED` means a reviewer is blocking.
4. Reviewer permissions — see above.
5. Merge queue — if `isInMergeQueue` is true and state is `AWAITING_CHECKS`
   with `estimatedTimeToMerge: null`, suspect the `merge_group` trigger.

Dequeuing is safe: it does not change the head, dismiss approvals, mark the PR
dirty, or add a failure to the head's check suite.

## Red Flags

- **Counting approvals from the UI.** Check permissions via the API instead.
- **Reading a `lgtm` label as an approval.** Labels are not reviews.
- **Assuming absent CI means passing CI.** An empty `statusCheckRollup` means
  the workflow never ran.
- **Admin-merging to get past a review requirement.** Never. The only defensible
  bypass is a mechanically impossible check — and even then the review
  requirement must be genuinely satisfied first.
- **Disabling a ruleset to land something.** Non-enforcing windows are repo-wide
  and easy to forget to close. A single pinned admin merge is narrower.
- **Two open PRs making the same change.** Close the duplicates with a reason
  and a link to the survivor; do not merge obsolete code for attribution.

## Verification

Every fact above is re-derivable. Do this rather than trusting the table:

```bash
R=projectbluefin/bluefin-bling

# What does main actually require right now?
gh api repos/$R/rules/branches/main --jq '.[] | {type, parameters}'

# Who can cast a counting approval? (push=true only)
gh api repos/$R/collaborators --jq '.[] | select(.permissions.push) | .login'

# Real state of one PR: CI on the head SHA, deduped reviews, merge state
gh pr view <N> --repo $R --json mergeStateStatus,reviewDecision,statusCheckRollup,reviews

# Has the merge queue ever actually run? (0 means the merge_group trigger is missing)
gh api "repos/$R/actions/runs?event=merge_group" --jq '.total_count'

# Is the trigger present?
python3 -c "import yaml; print(yaml.safe_load(open('.github/workflows/ci.yml'))[True])"
```
