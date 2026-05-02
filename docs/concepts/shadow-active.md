# Shadow → active

Daedalus runs in one of two modes per instance. **Shadow** observes — it ticks, picks lanes, evaluates "what would happen next" — and writes to a separate shadow action queue. **Active** does the same evaluation but actually executes the next action against the real runtime.

The promotion from shadow to active is gated by `active-gate-status` — an explicit operator step, not a config edit.

## Mode comparison

| | Shadow | Active |
|---|---|---|
| Reads workflow state | ✅ | ✅ |
| Picks next action | ✅ | ✅ |
| Writes shadow rows | ✅ | ❌ |
| Writes active rows | ❌ | ✅ |
| Calls runtimes | ❌ | ✅ |
| Affects GitHub | ❌ | ✅ (comments, merges) |
| Holds leases | ✅ (shadow leases) | ✅ (active leases) |

## Why two modes

The shadow path exists so you can:

- Stand up a new instance against a live workspace and watch it for a day before promoting it.
- Diff "what would shadow do" vs "what active actually did" to catch policy regressions.
- Keep a passive observer running for alerting (`daedalus/alerts.py`) without having two writers fight.

## Promotion sequence

```mermaid
sequenceDiagram
    participant op as Operator
    participant rt as Daedalus runtime
    participant gate as Active gate
    participant rwf as Workflow (real runtimes)

    op->>rt: start --mode shadow
    loop ticks
        rt->>rt: pick next action
        rt->>rt: write SHADOW row only
    end
    op->>rt: active-gate-status
    rt-->>op: report (which lanes are gate-ready)
    op->>gate: open gate (manual)
    op->>rt: start --mode active --instance-id A
    loop ticks
        rt->>rt: pick next action
        rt->>rwf: dispatch turn
        rwf-->>rt: result
        rt->>rt: write ACTIVE row + events
    end
```

## Operator commands that touch this

- `daedalus/runtime.py active-gate-status` — what's blocking promotion
- `daedalus/runtime.py iterate-shadow` / `iterate-active` — single tick in either mode
- `daedalus/runtime.py run-shadow` / `run-active` — long-running loop; active mode supervises its worker iteration and keeps the lease fresh while actions run
- `/daedalus shadow-report` — diff between shadow plan and active reality

## Where this lives in code

- Mode selection: `daedalus/runtime.py` (look for `Mode`, `iterate_shadow`, `iterate_active`)
- Active gate: `daedalus/runtime.py::active_gate_status`
- Service supervision: `daedalus/daedalus_cli.py` (systemd helpers)
- Shadow reporting: `daedalus/cli/formatters.py`
