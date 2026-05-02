---
workflow: change-delivery
schema-version: 1

instance:
  name: your-org-your-repo-change-delivery
  engine-owner: hermes

repository:
  local-path: /home/you/src/acme-repo
  slug: your-org/your-repo
  active-lane-label: active-lane

tracker:
  kind: github
  github_slug: your-org/your-repo
  active_states:
    - open
  terminal_states:
    - closed

code-host:
  kind: github
  github_slug: your-org/your-repo

runtimes:
  codex-app-server:
    kind: codex-app-server
    mode: external
    endpoint: ws://127.0.0.1:4500
    healthcheck_path: /readyz
    ephemeral: false
    keep_alive: true
    approval_policy: never
    thread_sandbox: workspace-write
    turn_sandbox_policy: workspace-write
    turn_timeout_ms: 3600000
    read_timeout_ms: 5000
    stall_timeout_ms: 300000

actors:
  implementer:
    name: Change_Implementer
    model: gpt-5.4
    runtime: codex-app-server

  implementer-high-effort:
    name: Change_Implementer_High_Effort
    model: gpt-5.4
    runtime: codex-app-server

  reviewer:
    name: Change_Reviewer
    model: gpt-5.4
    runtime: codex-app-server

stages:
  implement:
    actor: implementer
    escalation:
      after-attempts: 2
      actor: implementer-high-effort

  publish:
    action: pr.publish

  merge:
    action: pr.merge

gates:
  pre-publish-review:
    type: agent-review
    actor: reviewer
    new-context: true
    pass-with-findings-tolerance: 1
    require-pass-clean-before-publish: true
    request-cooldown-seconds: 1200

  maintainer-approval:
    type: pr-comment-approval
    enabled: false
    required-for-merge: true
    users: []
    approvals:
      - "+1"

  ci-green:
    type: code-host-checks
    required-for-merge: true

triggers:
  lane-selector:
    type: github-label
    label: active-lane

storage:
  ledger: memory/workflow-status.json
  health: memory/workflow-health.json
  audit-log: memory/workflow-audit.jsonl
  scheduler: memory/workflow-scheduler.json

retention:
  events:
    max-age-days: 30
    max-rows: 100000

lane-selection:
  exclude-labels:
    - blocked
  tiebreak: oldest

tracker-feedback:
  enabled: true
  comment-mode: append
  include:
    - dispatch-implementation-turn
    - internal-review-completed
    - publish-ready-pr
    - push-pr-update
    - merge-and-promote
    - operator-attention-transition
    - operator-attention-recovered
  state-updates:
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

Actor and gate intent:

- `implementer`: make the next scoped code change and leave a clean handoff.
- `reviewer`: review correctness, regressions, and test honesty in a fresh context.
- `pre-publish-review`: blocks publish until the configured review policy passes.
- `maintainer-approval`: optionally waits for registered PR commenters to approve.
