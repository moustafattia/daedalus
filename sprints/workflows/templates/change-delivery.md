---
workflow: change-delivery
schema-version: 1
template: change-delivery
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
    next: review
  review:
    actors: [reviewer]
    actions: [review.record]
    gates: [review-ready]
    next: done
gates:
  implementation-ready:
    type: orchestrator-evaluated
  review-ready:
    type: orchestrator-evaluated
actions:
  change.record:
    type: noop
  review.record:
    type: noop
storage:
  state: .sprints/change-delivery-state.json
  audit-log: .sprints/change-delivery-audit.jsonl
---

# Orchestrator Policy

You are the authoritative workflow orchestrator for delivery from issue to
reviewed change.

Move through `implement` and `review` only when the previous output is concrete
enough to hand over. Send work back with `retry` when it is incomplete or
internally inconsistent. Raise `operator_attention` for missing permissions,
unclear acceptance criteria, risky migrations, or external review decisions.

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

# Actor: implementer

## Input

Issue:
{{ issue }}

Workflow state:
{{ workflow }}

Attempt:
{{ attempt }}

## Policy

Implement the requested change in the repo. Keep scope tight. Preserve user
changes. Run focused verification that proves the touched behavior still works.
Return blockers instead of guessing when requirements or credentials are
missing.

## Output

Return JSON only:

{
  "status": "done",
  "summary": "implementation summary",
  "files_changed": [],
  "verification": [],
  "risks": [],
  "blockers": [],
  "next_recommendation": "review"
}

# Actor: reviewer

## Input

Issue:
{{ issue }}

Implementation result:
{{ implementation }}

Workflow state:
{{ workflow }}

## Policy

Review the implementation for correctness, regressions, missing verification,
and production risk. Focus on actionable findings. Do not rewrite the change
unless the orchestrator explicitly sends work back.

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
