# `issue-runner`

`issue-runner` is the generic bundled workflow. It is intentionally smaller
than `change-delivery`: it selects an eligible issue, creates or reuses an
issue workspace, runs hooks, renders a prompt, and invokes one agent runtime.

## What it does

For each eligible tracker issue:

1. load the tracker feed
2. select the next eligible issue
3. create/reuse an isolated issue workspace
4. run lifecycle hooks
5. render the Markdown workflow body as the issue prompt template
6. invoke the configured runtime/agent
7. persist output and audit state
8. persist scheduler state for running workers, continuation retries, failure backoff, recovery, and token totals

## Use it when

- you want a generic tracker-driven automation loop
- you do not want built-in PR review/merge policy
- you want a starting point for a more Symphony-shaped workflow

## Default template

- Public example: [`docs/examples/issue-runner.workflow.md`](../examples/issue-runner.workflow.md)
- Bundled payload template: [`daedalus/workflows/issue_runner/workflow.template.md`](/home/radxa/WS/daedalus/daedalus/workflows/issue_runner/workflow.template.md)
- Sample tracker file: [`daedalus/workflows/issue_runner/issues.template.json`](/home/radxa/WS/daedalus/daedalus/workflows/issue_runner/issues.template.json)

## Key config blocks

- `tracker`: shared tracker client kind, source path or endpoint, active/terminal states, label filters
- `workspace`: per-issue workspace root
- `hooks`: `after_create`, `before_run`, `after_run`, `before_remove`
- `agent`: model/runtime plus scheduler-facing limits
- `codex`: spec-shaped Codex runner settings; set `mode: external` and `endpoint: ws://127.0.0.1:<port>` to connect to an already-running app-server, and keep `ephemeral: false` if you want Codex threads to remain inspectable
- `daedalus.runtimes`: shared runtime backend profiles used by the current implementation when you are not using the top-level `codex` block

External Codex app-server example:

```yaml
agent:
  model: gpt-5.5
  runtime: codex

runtimes:
  codex:
    kind: codex-app-server
    mode: external
    endpoint: ws://127.0.0.1:4500
    ephemeral: false
    keep_alive: true
    approval_policy: never
    thread_sandbox: workspace-write
    turn_sandbox_policy: workspace-write
```

Supported tracker kinds today:

- `github`
- `local-json`
- `linear`

`issue-runner` composes the shared `trackers/` clients with workflow-specific
eligibility, ordering, retry, and workspace policy.

`tick` is the manual/debug path: it selects a batch and runs it synchronously
before returning. `run` is the service path: it dispatches eligible workers,
returns to the polling loop, reconciles completed workers on later iterations,
and requests cancellation when a running issue enters a terminal tracker state.

Scheduler state is persisted under `storage.scheduler` (default:
`memory/workflow-scheduler.json`) so continuation retries, failure backoff,
running-worker recovery, aggregate Codex token totals, and Codex
`issue_id -> thread_id` mappings survive loop restarts. When a mapped thread
exists, the Codex app-server adapter resumes it with `thread/resume` before
starting the next turn. `status` also includes runtime diagnostics when the
selected runtime exposes them, including Codex app-server transport mode and
warm-client state.

## Operator path

`issue-runner` now supports the same repo-owned contract and managed service
path as `change-delivery`.

Use either:

```bash
cd /path/to/repo
hermes daedalus bootstrap --workflow issue-runner
```

or the explicit scaffold path:

```bash
hermes daedalus scaffold-workflow \
  --workflow issue-runner \
  --workflow-root ~/.hermes/workflows/<owner>-<repo>-issue-runner \
  --github-slug <owner>/<repo>
```

Then edit:

- `WORKFLOW.md` or `WORKFLOW-issue-runner.md` in the repo checkout
- nothing extra if you are using `tracker.kind: github` and the repo checkout already has `gh` auth
- `config/issues.json` if you are using `tracker.kind: local-json`
- `tracker.endpoint`, `tracker.api_key`, and `tracker.project_slug` if you are using `tracker.kind: linear`

Then bring it up:

```bash
hermes daedalus service-up
```

For direct workflow operations:

```bash
/workflow issue-runner status
/workflow issue-runner doctor
/workflow issue-runner tick
/workflow issue-runner run --max-iterations 1 --json
/workflow issue-runner serve
```

If `server.port` is set in the repo-owned contract, `serve` exposes the same
localhost JSON + HTML status surface used by `change-delivery`, but backed by
the `issue-runner` scheduler/status/audit files instead of the lane SQLite
tables.

## Current limitation

- The Linear adapter follows the Symphony baseline query shape, but still needs real Linear integration smoke coverage before claiming production-grade Linear support.
- Managed service mode is `active` only. `shadow` remains specific to `change-delivery`.
- The bundled Codex app-server adapter supports managed stdio, warm external WebSocket transports, durable thread resume across ticks, and cooperative in-flight cancellation in the supervised `run` loop.
- Cancellation is cooperative. Codex app-server turns are interrupted when Daedalus requests cancellation; command-style runtimes may only observe cancellation before they start or after they exit.

## Related docs

- [Architecture](../architecture.md)
- [Runtimes](../concepts/runtimes.md)
- [Hot-reload](../concepts/hot-reload.md)
- [Symphony conformance](../symphony-conformance.md)
