# Hot-reload & per-tick preflight

Symphony §6.2 + §6.3. The two halves of "edits to the workflow contract should never crash the loop."

## §6.2 Hot-reload

The workflow contract (`WORKFLOW.md` or `WORKFLOW-<name>.md`) is re-parsed and re-validated on every tick. The new config replaces the old one **only if** parse + validate both succeed. A bad edit keeps the previous good config alive — the next valid save picks up automatically.

```mermaid
flowchart TD
  A[tick begins] --> B[read workflow contract<br/>stat: mtime+size]
  B --> C{key changed?}
  C -- no --> D[reuse last ConfigSnapshot]
  C -- yes --> E[parse + validate]
  E -- ok --> F[swap AtomicRef → new snapshot]
  E -- ParseError or<br/>ValidationError or<br/>OSError or<br/>UnicodeDecodeError --> G[keep last known good<br/>emit daedalus.config_reload_failed]
  D --> H[continue tick]
  F --> H
  G --> H
```

### Why `(mtime, size)` instead of just mtime

Some filesystems (NFS, coarse-mtime FS) can produce a same-mtime write. The size guards against that. If you build something on top of `ConfigWatcher`, use the same tuple key.

### Snapshot safety

`ConfigSnapshot` is a frozen dataclass and inner dicts are wrapped in `MappingProxyType` — the snapshot can be passed across threads without copy. Readers go through `AtomicRef.get()` which is lock-free; writers (the watcher) hold a lock to swap.

## §6.3 Per-tick preflight

Even with hot-reload, *some* configurations are bad enough that dispatch can't proceed — e.g., a referenced runtime that doesn't exist. Preflight is the single check that runs at the top of every gated command:

```mermaid
flowchart LR
  A[run_cli enters] --> B{command in<br/>PREFLIGHT_GATED_COMMANDS?}
  B -- no --> C[execute as normal]
  B -- yes --> D[run_preflight]
  D -- ok --> C
  D -- failed --> E{can_reconcile?}
  E -- yes --> F[skip dispatch<br/>reconciliation still ticks]
  E -- no --> G[abort tick]
```

`PREFLIGHT_GATED_COMMANDS` is declared on each workflow module. `change-delivery` gates dispatching commands such as `tick`, `dispatch-implementation-turn`, `dispatch-internal-review`, `publish-ready-pr`, `merge-and-promote`, and `resume`. `issue-runner` gates `tick` and `run`. Read-only commands such as `status`, `doctor`, and inspection helpers bypass the gate.

### What preflight actually checks

- The workflow contract parses
- The projected workflow config matches the selected workflow's `schema.yaml`
- Every referenced runtime kind is registered
- Workflow-specific references are internally consistent, such as `change-delivery` lane selection or `issue-runner` tracker/runtime storage paths

`PreflightResult.can_reconcile=True` (the default) means: dispatch is unsafe but reconciliation (lease recovery, stall detection, status reads) is still safe. That's the rule that keeps a bad edit from killing the loop.

## Where this lives in code

- `ConfigSnapshot` + `AtomicRef`: `daedalus/workflows/shared/config_snapshot.py`
- Watcher: `daedalus/workflows/shared/config_watcher.py`
- `change-delivery` compatibility re-exports: `daedalus/workflows/change_delivery/config_snapshot.py`, `daedalus/workflows/change_delivery/config_watcher.py`
- Preflight: `daedalus/workflows/change_delivery/preflight.py`, `daedalus/workflows/issue_runner/preflight.py`
- Gating: `daedalus/workflows/__init__.py::run_cli` reads `PREFLIGHT_GATED_COMMANDS`
- Tests: `tests/test_config_snapshot.py`, `tests/test_config_watcher.py`, `tests/test_workflows_preflight_cli_integration.py`
