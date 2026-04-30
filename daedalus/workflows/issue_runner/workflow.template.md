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

daedalus:
  runtimes:
    default:
      kind: hermes-agent
      command:
        - fake-agent
        - --prompt
        - "{prompt_path}"
        - --issue
        - "{issue_identifier}"

storage:
  status: memory/workflow-status.json
  health: memory/workflow-health.json
  audit-log: memory/workflow-audit.jsonl
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
