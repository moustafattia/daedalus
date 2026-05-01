# Symphony Conformance

This note tracks Daedalus against the public `openai/symphony` draft spec as reviewed on **April 30, 2026**.

The short version: Daedalus is already **Symphony-aligned** in architecture, but
only **partially Symphony-compatible** at the contract and integration
boundaries. The target is to make `issue-runner` the strict reference surface
and keep `change-delivery` as the opinionated GitHub-first SDLC workflow.

## Positioning

- Daedalus is a long-running workflow orchestrator with durable state, hot reload, isolated lane worktrees, recovery, and operator observability.
- Daedalus is intentionally **tracker-neutral in contract shape** and **GitHub-first in production coverage** for the public release. The current Symphony draft is **Linear-first**, so Daedalus tracks that shape without making Linear the launch path.
- Daedalus now uses a Symphony-style `WORKFLOW.md` as the native public contract for bundled workflows. `issue-runner` is the closer generic reference surface; `change-delivery` remains the richer GitHub-first SDLC workflow with separate `tracker` and `code-host` boundaries.

## Status Matrix

| Symphony concept | Daedalus status | Notes |
|---|---|---|
| `WORKFLOW.md` loader | Partial | Supported as a repo-owned public contract. Front matter maps to the selected workflow schema; `issue-runner` is the closer generic reference surface, while `change-delivery` still carries richer PR/review/merge semantics. |
| Typed config + hot reload | Implemented | Bundled workflows load repo-owned `WORKFLOW.md`; `issue-runner` now keeps last-known-good config on invalid reloads. |
| Issue tracker client boundary | Partial | `issue-runner` has shared `github`, `local-json`, and Linear clients. GitHub is the first-class public tracker path and now has a skipped-by-default live smoke; `local-json` is for fixtures/dev; Linear is experimental and deferred. |
| Workspace manager | Partial | Generic workspace root, lifecycle hooks, terminal cleanup, sanitized workspace keys, root-containment checks, managed long-running `issue-runner`, supervised `change-delivery` active iterations, worker reconciliation, and persisted scheduler state now exist. |
| Bounded concurrency | Partial | `issue-runner` dispatches bounded async workers in the service loop and persists running-worker recovery. `change-delivery` now supervises one active worker iteration at a time, but the broader engine is still not uniformly scheduler-driven. |
| Retry/backoff policy | Partial | `issue-runner` uses Symphony-style 1s continuation retries and 10s-based exponential failure backoff, including supervised worker completion and terminal-state retry suppression. |
| Coding-agent protocol | Partial | `issue-runner` and `change-delivery` can use the shared protocol-valid `codex-app-server` JSON-RPC adapter with managed stdio, external WebSocket mode, a managed app-server user unit, persisted thread resume, cooperative turn interruption, and warm external transports. |
| Observability surface | Partial | Events, status, watch, and HTTP surfaces exist; bundled workflows record Codex token/rate-limit totals and expose Codex thread mappings when using `codex-app-server`. |
| Trust/safety posture | Implemented | See [security.md](security.md). |
| Terminal workspace cleanup | Partial | Terminal lane states exist; full Symphony-style cleanup semantics still need explicit policy. |

## Compatibility Target

`issue-runner` is the workflow that should converge toward strict Symphony
compatibility. Its public contract should keep the Symphony-shaped keys
(`tracker`, `polling`, `workspace`, `hooks`, `agent`, `codex`) as the operator
surface and move Daedalus-specific implementation details under `daedalus:`.

`change-delivery` should not be forced into that shape. It is the richer
workflow with GitHub lane policy, review gates, PR publication, merge promotion,
and workflow-specific prompts.

## Important Differences

Daedalus currently differs from the Symphony draft in four material ways:

1. The default managed workflow is `issue-runner`, while `change-delivery` remains the opinionated GitHub-first SDLC workflow.
2. Runtime adapters are still mixed: both bundled workflows can use Codex app-server, but non-Codex command runtimes remain CLI/session-oriented.
3. `WORKFLOW.md` still maps into the current Daedalus schema rather than a tracker-agnostic Symphony config model.
4. Long-running service paths have async supervision for `issue-runner` workers and `change-delivery` active iterations, but manual `tick` remains synchronous and command-style runtimes still have limited cancellation semantics.

## Recommended Next Gaps

1. Make `issue-runner` accept a stricter Symphony-style contract with Daedalus
   extensions isolated under `daedalus:`.
2. Add stronger cancellation semantics for command-style runtimes, including
   subprocess group termination where safe.
3. Expand the opt-in `change-delivery` Codex app-server fixture smoke into a
   full issue-to-PR-to-review-to-merge live E2E.
4. Expand harness checks for public docs, generic examples, workflow-template
   drift, and operator CLI/docs drift.

See [release-readiness.md](release-readiness.md) for the launch scorecard and
hardening gates.

Until those land, Daedalus should be described as **Symphony-inspired and partially compatible**, not as a strict implementation of the current spec.
