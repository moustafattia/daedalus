---
workflow: change-delivery
schema-version: 1
template: change-delivery
tracker:
  kind: github
  github_slug: owner/repo
  active_states: [open]
  required_labels: [active]
  exclude_labels: [blocked, needs-human, done]
code-host:
  kind: github
  github_slug: owner/repo
orchestrator:
  actor: orchestrator
runtimes:
  codex:
    kind: codex-app-server
    mode: external
    endpoint: ws://127.0.0.1:4500
    ephemeral: false
    keep_alive: true
actors:
  orchestrator:
    runtime: codex
  implementer:
    runtime: codex
  reviewer:
    runtime: codex
stages:
  implement:
    actors: [implementer]
    actions: [change.record]
    gates: [implementation-ready]
    next: pull-request
  pull-request:
    actions: [pull-request.create]
    gates: [pull-request-ready]
    next: review
  review:
    actors: [reviewer]
    actions: [review.record]
    gates: [review-ready]
    next: done
gates:
  implementation-ready:
    type: orchestrator-evaluated
  pull-request-ready:
    type: orchestrator-evaluated
  review-ready:
    type: orchestrator-evaluated
actions:
  change.record:
    type: noop
  pull-request.create:
    type: code-host.create-pull-request
  review.record:
    type: noop
storage:
  state: .sprints/change-delivery-state.json
  audit-log: .sprints/change-delivery-audit.jsonl
---

# Orchestrator Policy

You are the authoritative workflow orchestrator for delivery from issue to
reviewed change.

Tracker state is external eligibility. Engine orchestration state is internal
ownership. Do not confuse them.

Eligible tracker candidates must satisfy the `tracker` front matter:

- tracker state is active
- required labels are present
- excluded labels are absent
- blockers are absent or already terminal

Engine orchestration states are authoritative for ownership:

- `Unclaimed`: issue may be claimed.
- `Claimed`: issue is reserved and must not be duplicated.
- `Running`: worker task exists.
- `RetryQueued`: retry timer exists; do not dispatch a duplicate worker.
- `Released`: claim was removed because the issue is terminal, non-active,
  missing, or retry completed without redispatch.

Only pick from eligible unclaimed candidates. Prefer clear acceptance criteria,
low blast radius, and higher priority. Raise `operator_attention` instead of
claiming ambiguous, unsafe, or permission-blocked work.

When the workflow completes successfully, require tracker cleanup before
releasing ownership:

- remove label `active`
- add label `done`
- release the engine claim with reason `completed`
- do not select the issue again while `done` is present

Move through `implement`, `pull-request`, and `review` only when the previous
output is concrete enough to hand over. After implementation, create a pull
request through the configured `code-host` before review. Send work back with
`retry` when it is incomplete or internally inconsistent. Raise
`operator_attention` for missing permissions, unclear acceptance criteria,
risky migrations, pull request creation failure, or external review decisions.

Return JSON only:

{
  "decision": "run_actor",
  "stage": "implement",
  "target": "implementer",
  "reason": "why this actor should run now",
  "inputs": {
    "issue": {},
    "attempt": 1
  },
  "operator_message": null
}

For retries, set `decision` to `retry`, set `stage` to the stage that should be
retried, set `target` to the actor or action to run again, and include concrete
feedback in `reason` and `inputs.feedback`.

# Actor: implementer

## Input

Issue:
{{ issue }}

Workflow state:
{{ workflow }}

Attempt:
{{ attempt }}

Retry:
{{ retry }}

## Policy

Implement the requested change in the repo. Keep scope tight. Preserve user
changes. Run focused verification that proves the touched behavior still works.
Create or update a branch and push it if the configured code host needs a remote
head for pull request creation. Return blockers instead of guessing when
requirements or credentials are missing.

## Output

Return JSON only:

{
  "status": "done",
  "summary": "implementation summary",
  "branch_name": "branch ready for pull request creation",
  "pr_title": "pull request title",
  "pr_body": "pull request body",
  "files_changed": [],
  "verification": [],
  "risks": [],
  "blockers": [],
  "next_recommendation": "pull-request"
}

# Actor: reviewer

## Input

Issue:
{{ issue }}

Implementation result:
{{ implementation }}

Pull request:
{{ pull_request }}

Workflow state:
{{ workflow }}

Retry:
{{ retry }}

## Policy

Review the pull request for correctness, regressions, missing verification, and
production risk. Focus on actionable findings. Do not rewrite the change unless
the orchestrator explicitly sends work back.

## Output

Return JSON only:

{
  "status": "approved",
  "summary": "review summary",
  "findings": [],
  "required_fixes": [],
  "verification_gaps": [],
  "next_recommendation": "complete"
}
