---
workflow: change-delivery
schema-version: 1

instance:
  name: your-org-your-repo-change-delivery
  engine-owner: hermes

repository:
  local-path: /home/you/src/acme-repo
  slug: your-org/your-repo
  github-slug: your-org/your-repo
  active-lane-label: active-lane

tracker:
  kind: github
  active_states:
    - open
  terminal_states:
    - closed

runtimes:
  coder-runtime:
    kind: acpx-codex
    session-idle-freshness-seconds: 900
    session-idle-grace-seconds: 1800
    session-nudge-cooldown-seconds: 600

  reviewer-runtime:
    kind: claude-cli
    max-turns-per-invocation: 24
    timeout-seconds: 1200

agents:
  coder:
    default:
      name: Internal_Coder_Agent
      model: gpt-5.3-codex-spark/high
      runtime: coder-runtime
    high-effort:
      name: Escalation_Coder_Agent
      model: gpt-5.4
      runtime: coder-runtime

  internal-reviewer:
    name: Internal_Reviewer_Agent
    model: claude-sonnet-4-6
    runtime: reviewer-runtime
    freeze-coder-while-running: true

  external-reviewer:
    enabled: false
    name: External_Reviewer_Agent
    kind: disabled

gates:
  internal-review:
    pass-with-findings-tolerance: 1
    require-pass-clean-before-publish: true
    request-cooldown-seconds: 1200
  external-review:
    required-for-merge: true
  merge:
    require-ci-acceptable: true

triggers:
  lane-selector:
    type: github-label
    label: active-lane

storage:
  ledger: memory/workflow-status.json
  health: memory/workflow-health.json
  audit-log: memory/workflow-audit.jsonl
  scheduler: memory/workflow-scheduler.json

lane-selection:
  exclude-labels:
    - blocked
  tiebreak: oldest

observability:
  github-comments:
    enabled: false
---

# Workflow Policy

Daedalus runs the `change-delivery` workflow for the repository configured above.

Shared rules:

- Keep scope narrow to the active issue and current lane state.
- Prefer small, reviewable diffs over speculative refactors.
- Run focused validation and report it honestly.
- Stop and surface blockers instead of guessing.
- Do not publish generated artifacts or unrelated files.

Role intent:

- `coder`: implement the next correct change and leave a clean handoff.
- `internal-reviewer`: review correctness, regressions, and test honesty before publish.
- `external-reviewer`: provide an optional second-pass review when enabled.
