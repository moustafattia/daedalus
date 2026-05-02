# Sprints Engine

The engine is the durable runtime layer shared by workflow packages. A workflow
decides what an issue means and what should happen next; the engine provides the
mechanics that make that decision safe to run unattended.

## Engine-Owned Mechanisms

| Mechanism | Purpose |
|---|---|
| `tick` | One control-loop pass: load contract, inspect state, derive work, dispatch or reconcile, then persist results. |
| Work items | Tracker-neutral `WorkItemRef` objects that let workflows expose issues/lanes through one engine vocabulary. |
| Service loop | Repeats ticks under `systemd --user` supervision for unattended operation. |
| Workflow root | Durable instance directory under `~/.hermes/workflows/<owner>-<repo>-<workflow-type>`. |
| Contract loading | Reads repo-owned `WORKFLOW.md` / `WORKFLOW-<name>.md` and preserves last-known-good config. |
| Preflight | Blocks unsafe dispatch when config, tracker, runtime, or storage wiring is invalid. |
| Tracker adapters | Normalize issue sources such as GitHub and Linear. |
| Runtime adapters | Dispatch prompts/actions to Codex app-server, ACPX Codex, Claude CLI, Hermes Agent, or custom commands. |
| Workspace lifecycle | Creates isolated workspaces and runs configured lifecycle hooks. |
| SQLite store | Source of truth for engine execution state: work items, running work, retries, runtime sessions, token totals, and workflow-specific tables. |
| Scheduler snapshot | Generated JSON view of worker, retry, Codex thread, token, and rate-limit state for operator tools that still consume files. |
| Retry and recovery | Tracks attempts, due times, errors, restart recovery, and operator-attention thresholds. |
| Observability | Feeds `/sprints status`, `/sprints doctor`, `/sprints watch`, and optional HTTP status. |

## Current Shared Code

The first shared engine package lives in `sprints/engine/` and is installed as
the plugin-local `engine` package:

| Module | Shared Primitive |
|---|---|
| `engine.db` | SQLite connection setup, table names, schema creation, and compatibility migrations. |
| `engine.work` | Neutral work/result dataclasses plus tracker adapters. |
| `engine.lifecycle` | Shared running, retry, clear, and restart-recovery mutation helpers. |
| `engine.state` | Low-level SQL read/write projections for scheduler state, runs, and events. |
| `engine.leases` | Shared lease acquire/release/status helpers for ownership and heartbeat checks. |
| `engine.store` | Workflow-scoped `EngineStore` API for transactions, scheduler state, leases, and doctor checks. |
| `engine.scheduler` | Scheduler snapshot/restore helpers for running work, retry queues, and Codex thread mappings. |
| `engine.retention` | Event-retention config normalization for pruning and reporting. |

The agentic workflow consumes the shared scheduler, lifecycle, work-item, and
`EngineStore` primitives directly. The engine persists durable SQLite state and
produces generated scheduler snapshots for operator surfaces such as watch,
status, and doctor.

## Boundary

Engine code should not know whether a workflow is doing a PR review, a merge, a
simple issue prompt, or a custom automation. It should know how to persist,
supervise, retry, reconcile, and expose state. Workflow packages keep policy,
prompts, gates, and lifecycle semantics.
