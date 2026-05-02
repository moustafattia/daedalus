---
workflow: issue-runner
schema-version: 1

instance:
  name: your-org-your-repo-issue-runner
  engine-owner: hermes

repository:
  local-path: /home/you/src/acme-repo
  slug: your-org/your-repo

tracker:
  kind: github
  github_slug: your-org/your-repo
  active_states:
    - open
  terminal_states:
    - closed

polling:
  interval_ms: 30000

workspace:
  root: workspace/issues

hooks:
  timeout_ms: 60000

agent:
  name: Issue_Runner_Agent
  model: gpt-5.4
  runtime: codex-app-server
  max_concurrent_agents: 1
  max_turns: 20
  max_retry_backoff_ms: 300000

runtimes:
  codex-app-server:
    kind: codex-app-server
    stage-command: false
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

storage:
  status: memory/workflow-status.json
  health: memory/workflow-health.json
  audit-log: memory/workflow-audit.jsonl

retention:
  events:
    max-age-days: 30
    max-rows: 100000
---

# Workflow Policy

Run the `issue-runner` workflow only on the selected issue and keep outputs grounded in the live issue state.

Issue: {{ issue.identifier }} - {{ issue.title }}

State: {{ issue.state }}
Labels: {{ issue.labels }}
Priority: {{ issue.priority }}
Branch: {{ issue.branch_name }}
URL: {{ issue.url }}
Attempt: {{ attempt }}

Description:
{{ issue.description }}
