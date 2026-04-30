# Daedalus Engine

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
| Tracker adapters | Normalize issue sources such as GitHub, `local-json`, and experimental Linear. |
| Runtime adapters | Dispatch prompts/actions to Codex app-server, ACPX Codex, Claude CLI, Hermes Agent, or custom commands. |
| Workspace lifecycle | Creates isolated workspaces and runs configured lifecycle hooks. |
| SQLite store | Source of truth for engine execution state: work items, running work, retries, runtime sessions, token totals, and workflow-specific tables. |
| Scheduler snapshot | Generated JSON view of worker, retry, Codex thread, token, and rate-limit state for operator tools that still consume files. |
| JSONL audit | Append-only workflow/runtime event history for debugging and external publishing. |
| Retry and recovery | Tracks attempts, due times, errors, restart recovery, and operator-attention thresholds. |
| Observability | Feeds `/daedalus status`, `/daedalus doctor`, `/daedalus watch`, and optional HTTP status. |

## Current Shared Code

The first shared engine package lives in `daedalus/engine/` and is installed as
the plugin-local `engine` package:

| Module | Shared Primitive |
|---|---|
| `engine.storage` | Atomic JSON/text writes, optional JSON reads, JSONL append. |
| `engine.audit` | JSONL audit writer with best-effort subscriber fanout. |
| `engine.driver` | Minimal workflow driver protocol for status, doctor, and tick surfaces. |
| `engine.work_items` | Neutral work-item/result dataclasses plus issue/lane adapters. |
| `engine.lifecycle` | Shared running, retry, clear, and restart-recovery mutation helpers. |
| `engine.sqlite` | Daedalus SQLite connection setup with WAL, foreign keys, and busy timeout. |
| `engine.state` | Shared SQLite tables and read/write projections for scheduler state. |
| `engine.leases` | Shared lease acquire/release/status helpers for ownership and heartbeat checks. |
| `engine.store` | Workflow-scoped `EngineStore` API for transactions, scheduler state, leases, and doctor checks. |
| `engine.scheduler` | Scheduler snapshot/restore helpers for running work, retry queues, and Codex thread mappings. |

`issue-runner` now consumes the shared scheduler, lifecycle, work-item, and
`EngineStore` primitives directly, then writes `memory/workflow-scheduler.json`
as a generated operator snapshot. `change-delivery` keeps its lane/action
tables for workflow-specific policy, but shares the engine runtime-session,
token accounting, and lease primitives used by watch, status, and doctor
surfaces.

## Boundary

Engine code should not know whether a workflow is doing a PR review, a merge, a
simple issue prompt, or a custom automation. It should know how to persist,
supervise, retry, reconcile, and expose state. Workflow packages keep policy,
prompts, gates, and lifecycle semantics.
