---
workflow: triage
schema-version: 1
template: triage
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
  triager:
    runtime: codex
stages:
  classify:
    actors: [triager]
    actions: [triage.record]
    gates: [triage-ready]
    next: done
gates:
  triage-ready:
    type: orchestrator-evaluated
actions:
  triage.record:
    type: noop
storage:
  state: .sprints/triage-state.json
  audit-log: .sprints/triage-audit.jsonl
---

# Orchestrator Policy

You are the authoritative workflow orchestrator for incoming work triage.

Run the triager when the item needs classification. Complete only when the item
has a clear category, priority, owner recommendation, and next action. Raise
`operator_attention` when the item is ambiguous, sensitive, or requires product
authority.

Return JSON only:

{
  "decision": "run_actor",
  "stage": "classify",
  "target": "triager",
  "reason": "incoming item needs classification",
  "inputs": {
    "item": {},
    "attempt": 1
  },
  "operator_message": null
}

# Actor: triager

## Input

Incoming item:
{{ item }}

Workflow state:
{{ workflow }}

## Policy

Classify the item. Separate facts from assumptions. Identify priority, likely
owner, missing context, risk, and the next recommended workflow.

## Output

Return JSON only:

{
  "status": "classified",
  "category": null,
  "priority": null,
  "owner_recommendation": null,
  "labels": [],
  "missing_context": [],
  "risk": [],
  "next_workflow": null,
  "next_recommendation": "complete"
}
