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
| Workspace manager | Partial | Generic workspace root, lifecycle hooks, terminal cleanup, sanitized workspace keys, root-containment checks, managed long-running `issue-runner`, supervised `change-delivery` active iterations, worker reconciliation, and persisted scheduler state now exist. |
| Bounded concurrency | Partial | `issue-runner` dispatches bounded async workers in the service loop and persists running-worker recovery. `change-delivery` now supervises one active worker iteration at a time, but the broader engine is still not uniformly scheduler-driven. |
| Retry/backoff policy | Partial | `issue-runner` uses Symphony-style 1s continuation retries and 10s-based exponential failure backoff, including supervised worker completion and terminal-state retry suppression. |
| Coding-agent protocol | Partial | `issue-runner` ships a protocol-valid `codex-app-server` JSON-RPC adapter with managed stdio, external WebSocket mode, a managed app-server user unit, persisted thread resume, and cooperative turn interruption. |
| Observability surface | Partial | Events, status, watch, and HTTP surfaces exist; `issue-runner` records per-run token/rate-limit metrics and exposes `codex_totals` plus Codex thread mappings. |
| Trust/safety posture | Implemented | See [security.md](security.md). |
| Terminal workspace cleanup | Partial | Terminal lane states exist; full Symphony-style cleanup semantics still need explicit policy. |

## Important Differences

Daedalus currently differs from the Symphony draft in four material ways:

1. The supported managed workflow is GitHub-backed `change-delivery`; `issue-runner` is the generic reference workflow and has a Linear adapter, but the broader product story is still not Linear-first.
2. Runtime adapters are still mixed: `issue-runner` has a protocol-level Codex app-server path, while the rest of Daedalus remains CLI/session-oriented.
3. `WORKFLOW.md` still maps into the current Daedalus schema rather than a tracker-agnostic Symphony config model.
4. Long-running service paths have async supervision for `issue-runner` workers and `change-delivery` active iterations, but manual `tick` remains synchronous and command-style runtimes still have limited cancellation semantics.

## Recommended Next Gaps

1. Keep Codex app-server sessions warm across worker turns instead of opening a connection per run.
2. Add stronger cancellation semantics for command-style runtimes, including subprocess group termination where safe.
3. Add real Linear integration smoke tests and publish a stricter conformance checklist.

Until those land, Daedalus should be described as **Symphony-inspired and partially compatible**, not as a strict implementation of the current spec.
