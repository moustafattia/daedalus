# Symphony Conformance

This note tracks Daedalus against the public `openai/symphony` draft spec as reviewed on **April 30, 2026**.

The short version: Daedalus is already **Symphony-aligned** in architecture, but only **partially Symphony-compatible** at the contract and integration boundaries.

## Positioning

- Daedalus is a long-running workflow orchestrator with durable state, hot reload, isolated lane worktrees, recovery, and operator observability.
- Daedalus is still **GitHub-first** in its managed/default workflow. The current Symphony draft is still **Linear-first**.
- Daedalus now uses a Symphony-style `WORKFLOW.md` as the native public contract for bundled workflows. `issue-runner` is the closer generic reference surface; `change-delivery` remains the richer GitHub SDLC workflow.

## Status Matrix

| Symphony concept | Daedalus status | Notes |
|---|---|---|
| `WORKFLOW.md` loader | Partial | Supported as a repo-owned public contract. Front matter maps to the selected workflow schema; `issue-runner` is the closer generic reference surface, while `change-delivery` still carries richer GitHub-specific semantics. |
| Typed config + hot reload | Implemented | Bundled workflows load repo-owned `WORKFLOW.md`; `issue-runner` now keeps last-known-good config on invalid reloads. |
| Issue tracker client boundary | Partial | `issue-runner` has shared `local-json`, `github`, and Linear clients. The Linear query shape follows the Symphony baseline, but still needs real-service smoke validation. |
| Workspace manager | Partial | Generic workspace root, lifecycle hooks, terminal cleanup, sanitized workspace keys, root-containment checks, managed long-running `issue-runner`, and persisted scheduler state now exist. |
| Bounded concurrency | Partial | `issue-runner` now dispatches bounded batches and persists running-worker recovery, but the broader engine is still not uniformly scheduler-driven. |
| Retry/backoff policy | Partial | `issue-runner` now uses Symphony-style 1s continuation retries and 10s-based exponential failure backoff. The remaining gap is true async worker cancellation/reconciliation. |
| Coding-agent protocol | Partial | `issue-runner` now ships a protocol-valid `codex-app-server` JSON-RPC adapter with managed stdio, external WebSocket mode, a managed app-server user unit, and persisted thread resume. The remaining gap is in-flight worker cancellation/reconciliation. |
| Observability surface | Partial | Events, status, watch, and HTTP surfaces exist; `issue-runner` records per-run token/rate-limit metrics and exposes `codex_totals` plus Codex thread mappings. |
| Trust/safety posture | Implemented | See [security.md](security.md). |
| Terminal workspace cleanup | Partial | Terminal lane states exist; full Symphony-style cleanup semantics still need explicit policy. |

## Important Differences

Daedalus currently differs from the Symphony draft in three material ways:

1. The supported managed workflow is GitHub-backed `change-delivery`; `issue-runner` is the generic reference workflow and has a Linear adapter, but the broader product story is still not Linear-first.
2. Runtime adapters are still mixed: `issue-runner` has a protocol-level Codex app-server path, while the rest of Daedalus remains CLI/session-oriented.
3. `WORKFLOW.md` still maps into the current Daedalus schema rather than a tracker-agnostic Symphony config model.
4. `issue-runner` still executes worker turns synchronously inside a tick, so in-flight cancellation on tracker state changes is not fully Symphony-shaped yet.

## Recommended Next Gaps

1. Move `issue-runner` from synchronous tick execution to true async worker supervision so reconciliation can stop active runs.
2. Keep Codex app-server threads and loaded sessions alive across ticks instead of starting/resuming one connection per run.
3. Add real Linear integration smoke tests and publish a stricter conformance checklist.

Until those land, Daedalus should be described as **Symphony-inspired and partially compatible**, not as a strict implementation of the current spec.
