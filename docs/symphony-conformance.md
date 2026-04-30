# Symphony Conformance

This note tracks Daedalus against the public `openai/symphony` draft spec as reviewed on **April 29, 2026**.

The short version: Daedalus is already **Symphony-aligned** in architecture, but only **partially Symphony-compatible** at the contract and integration boundaries.

## Positioning

- Daedalus is a long-running workflow orchestrator with durable state, hot reload, isolated lane worktrees, recovery, and operator observability.
- Daedalus is still **GitHub-first**. The current Symphony draft is still **Linear-first**.
- Daedalus now uses a Symphony-style `WORKFLOW.md` as the native public contract for the bundled workflow, but it remains GitHub-first and not fully spec-conformant.

## Status Matrix

| Symphony concept | Daedalus status | Notes |
|---|---|---|
| `WORKFLOW.md` loader | Partial | Supported at the workflow root as the public contract. Front matter maps directly to the current `change-delivery` schema, and the Markdown body becomes shared workflow policy. |
| Typed config + hot reload | Implemented | Current `change-delivery` schema is validated and hot-reloaded with last-known-good behavior. |
| Issue tracker client boundary | Partial | GitHub issue selection exists, but there is no generic tracker protocol or Linear adapter yet. |
| Workspace manager | Partial | Per-lane worktrees and lane-local files exist; generic lifecycle hooks are not first-class yet. |
| Bounded concurrency | Partial | Ownership and recovery exist; Symphony-style global/per-state scheduler limits are not yet config-first. |
| Retry/backoff policy | Partial | Durable retry and recovery bookkeeping exist, but backoff policy is not exposed as a clean public contract yet. |
| Coding-agent protocol | Partial | CLI/session runtimes ship today; a real Codex app-server adapter is still missing. |
| Observability surface | Partial | Events, status, watch, and HTTP surfaces exist; token/rate-limit accounting is still incomplete. |
| Trust/safety posture | Implemented | See [security.md](security.md). |
| Terminal workspace cleanup | Partial | Terminal lane states exist; full Symphony-style cleanup semantics still need explicit policy. |

## Important Differences

Daedalus currently differs from the Symphony draft in three material ways:

1. The first workflow is GitHub-backed `change-delivery`, not a Linear-backed generic scheduler.
2. Runtime adapters are CLI-oriented today, not Codex app-server-native.
3. `WORKFLOW.md` still maps into the current Daedalus schema rather than a tracker-agnostic Symphony config model.

## Recommended Next Gaps

1. Extract a real tracker interface and add a Linear adapter.
2. Add configurable workspace root + lifecycle hooks.
3. Promote concurrency and retry policy into the public schema.
4. Add a Codex app-server runtime with token and rate-limit accounting.

Until those land, Daedalus should be described as **Symphony-inspired and partially compatible**, not as a strict implementation of the current spec.
