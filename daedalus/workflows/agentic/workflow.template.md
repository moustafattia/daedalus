---
workflow: agentic
schema-version: 1
orchestrator:
  actor: orchestrator
runtimes:
  local:
    kind: local
actors:
  orchestrator:
    runtime: local
  implementer:
    runtime: local
stages:
  entry:
    actors: [implementer]
    actions: [noop.record]
    gates: [entry-complete]
    next: done
gates:
  entry-complete:
    type: orchestrator-evaluated
actions:
  noop.record:
    type: noop
storage:
  state: .daedalus/agentic-state.json
  audit-log: .daedalus/agentic-audit.jsonl
---

# Orchestrator Policy

Decide the next valid workflow transition from the current state and stage.

Return JSON only:

{
  "decision": "complete",
  "stage": "entry",
  "target": null,
  "reason": "minimal template completed",
  "inputs": {},
  "operator_message": null
}

# Actor: implementer

## Input

Current stage: {{ workflow.current_stage }}

## Policy

Return a small structured result for the stage.

## Output

Return JSON only:

{
  "status": "done",
  "summary": "minimal actor completed",
  "artifacts": [],
  "validation": [],
  "next_recommendation": "complete"
}
