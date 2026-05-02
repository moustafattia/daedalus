# Change-Delivery Contract Spec

This document defines the public `change-delivery` workflow contract. The goal
is to keep operator configuration small while letting the engine supervise the
same durable SDLC mechanics for any runtime profile.

## Design Goals

- Treat `change-delivery` as issue-to-merge delivery, not as a fixed set of
  named bots.
- Let operators bind Codex app-server, Hermes Agent, or future runtimes to any
  workflow actor.
- Keep review, CI, and human signoff as gates rather than hardcoded roles.
- Keep existing engine guarantees: tick loop, leases, retries, SQLite state,
  tracker feedback, reconciliation, and service supervision.

## Public Model

`actors` are named executors. They own `model`, `runtime`, optional command
overrides, and prompt overrides.

```yaml
actors:
  implementer:
    name: Change_Implementer
    model: gpt-5.4
    runtime: codex-app-server
```

`stages` are lifecycle steps. A stage either calls an actor or invokes an engine
action such as PR publish or merge.

```yaml
stages:
  implement:
    actor: implementer
    escalation:
      after-attempts: 2
      actor: implementer-high-effort
  publish:
    action: pr.publish
```

`gates` decide whether the workflow can advance. Gate types are standardized:
`agent-review`, `pr-comment-approval`, and `code-host-checks`.

```yaml
gates:
  pre-publish-review:
    type: agent-review
    actor: reviewer
  maintainer-approval:
    type: pr-comment-approval
    users: ["maintainer"]
    approvals: ["+1"]
```

## Engine Mapping

The public contract is compiled inside `change-delivery` into the private
engine view consumed by the current implementation. Operators should not edit
or depend on that view.

| Public contract | Engine use |
|---|---|
| `stages.implement.actor` | implementation dispatch actor |
| `stages.implement.escalation.actor` | high-effort implementation actor |
| `gates.*.type: agent-review` | pre-publish review runner |
| `gates.*.type: pr-comment-approval` | PR comment/reaction approval gate |
| `gates.*.type: code-host-checks` | merge-time code-host checks gate |

## Runtime Binding

`hermes daedalus configure-runtime` binds runtime presets to actor names:

```bash
hermes daedalus configure-runtime --runtime codex-app-server --role implementer
hermes daedalus configure-runtime --runtime hermes-chat --role reviewer
hermes daedalus configure-runtime --runtime codex-app-server --role all
```

The runtime matrix reports the same actor names, so every configured actor can
be smoked independently before the service is promoted to active mode.
