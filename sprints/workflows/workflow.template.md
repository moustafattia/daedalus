---
workflow: agentic
schema-version: 1
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
  state: .sprints/agentic-state.json
  audit-log: .sprints/agentic-audit.jsonl
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
