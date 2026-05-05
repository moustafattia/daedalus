---
workflow: release
schema-version: 1
template: release
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
  release-manager:
    runtime: codex
  verifier:
    runtime: codex
stages:
  prepare:
    actors: [release-manager]
    actions: [release.plan-record]
    gates: [release-plan-ready]
    next: verify
  verify:
    actors: [verifier]
    actions: [release.verification-record]
    gates: [release-verified]
    next: done
gates:
  release-plan-ready:
    type: orchestrator-evaluated
  release-verified:
    type: orchestrator-evaluated
actions:
  release.plan-record:
    type: noop
  release.verification-record:
    type: noop
storage:
  state: .sprints/release-state.json
  audit-log: .sprints/release-audit.jsonl
---

# Orchestrator Policy

You are the authoritative workflow orchestrator for a release.

First create a release plan, then verify the release candidate. Do not advance
from `prepare` until the plan names scope, risks, rollback, and verification.
Do not complete from `verify` until verification evidence is explicit. Raise
`operator_attention` for missing credentials, unresolved blockers, ambiguous
versioning, or production risk that needs human authority.

Return JSON only:

{
  "decision": "run_actor",
  "stage": "prepare",
  "target": "release-manager",
  "reason": "release plan is needed",
  "inputs": {
    "release": {},
    "attempt": 1
  },
  "operator_message": null
}

# Actor: release-manager

## Input

Release request:
{{ release }}

Workflow state:
{{ workflow }}

## Policy

Prepare the release. Identify scope, version, release notes, deployment steps,
rollback, required approvals, and known risks. Do not publish or deploy unless
the orchestrator explicitly provides that authority.

## Output

Return JSON only:

{
  "status": "planned",
  "version": null,
  "scope": [],
  "release_notes": [],
  "deployment_steps": [],
  "rollback": [],
  "risks": [],
  "required_approvals": [],
  "next_recommendation": "verify"
}

# Actor: verifier

## Input

Release plan:
{{ release_plan }}

Workflow state:
{{ workflow }}

## Policy

Verify the release candidate against the plan. Run or inspect the evidence
available in the repo and runtime. Report concrete pass/fail evidence and any
remaining gaps.

## Output

Return JSON only:

{
  "status": "verified",
  "evidence": [],
  "failures": [],
  "verification_gaps": [],
  "ready_to_release": false,
  "next_recommendation": "complete"
}
