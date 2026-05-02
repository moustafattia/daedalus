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
  kind: local-json
  path: config/issues.json
  active_states:
    - todo
    - in-progress
  terminal_states:
    - done
    - canceled

tracker-feedback:
  enabled: true
  comment-mode: append
  include:
    - issue.selected
    - issue.dispatched
    - issue.running
    - issue.completed
    - issue.failed
    - issue.canceled
    - issue.retry_scheduled
  state-updates:
    enabled: true
    on-selected: in-progress
    on-dispatched: in-progress
    on-running: in-progress
    on-completed: done
    on-failed: todo
    on-canceled: canceled

polling:
  interval_ms: 30000

workspace:
  root: workspace/issues

hooks:
  timeout_ms: 60000

agent:
  name: Issue_Runner_Agent
  model: claude-sonnet-4-6
  runtime: default
  max_concurrent_agents: 1
  max_turns: 20
  max_retry_backoff_ms: 300000

codex:
  command: codex app-server
  ephemeral: false
  approval_policy: never
  thread_sandbox: workspace-write
  turn_sandbox_policy: workspace-write
  turn_timeout_ms: 3600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000

runtimes:
  default:
    kind: hermes-agent
    command:
      - python3
      - -c
      - "from pathlib import Path; import sys; prompt = Path(sys.argv[1]).read_text(encoding='utf-8'); print('Daedalus demo signoff: runtime received the issue prompt.'); print(prompt)"
      - "{prompt_path}"

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
