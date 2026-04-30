# Migration & Cutover

Daedalus was previously known as **hermes-relay**. The rename introduced new state paths, new systemd unit names, and a new plugin registration model. This document covers how to migrate from relay-era deployments and how cutover between shadow and active modes works.

---

## What changed in the rename

| Relay-era | Daedalus |
|---|---|
| `hermes-relay` | `daedalus` |
| `relay.db` | `daedalus.db` |
| `relay-events.jsonl` | `daedalus-events.jsonl` |
| `relay-active@.service` | `daedalus-active@.service` |
| legacy workspace wrapper script | `workflows/__main__.py` (plugin-owned) |
| `relay/` directory | `daedalus/` directory |
| `watchdog` terminology | `engine` / `runtime` terminology |

---

## Filesystem migration

### One-shot command

```bash
/daedalus migrate-filesystem
```

This renames relay-era state files to Daedalus paths:
- `relay.db` → `daedalus.db`
- `relay-events.jsonl` → `daedalus-events.jsonl`
- Legacy control schema JSON → new ownership schema

For current workflow roots, state and runtime event files live under
`runtime/`:

```text
runtime/state/daedalus/daedalus.db
runtime/memory/daedalus-events.jsonl
```

Pure legacy roots without `runtime/`, `config/`, `workspace/`, or `docs/`
markers keep the old top-level `state/` and `memory/` layout.

It is **idempotent** — running it twice is safe.

### Manual fallback

If the command is unavailable, the migration is a simple rename:

```bash
mv ~/.hermes/workflows/<owner>-<repo>-<workflow-type>/state/relay/relay.db \
   ~/.hermes/workflows/<owner>-<repo>-<workflow-type>/state/daedalus/daedalus.db

mv ~/.hermes/workflows/<owner>-<repo>-<workflow-type>/memory/relay-events.jsonl \
   ~/.hermes/workflows/<owner>-<repo>-<workflow-type>/memory/daedalus-events.jsonl
```

For a current workflow root, insert `runtime/` after the workflow root in those
paths.

---

## Systemd migration

### One-shot command

```bash
/daedalus migrate-systemd
```

This:
1. Stops `relay-active@<owner>-<repo>-<workflow-type>.service`
2. Disables it
3. Installs `daedalus-active@<owner>-<repo>-<workflow-type>.service`
4. Enables and starts it

### Manual fallback

```bash
# Stop old
systemctl --user stop relay-active@<owner>-<repo>-<workflow-type>.service
systemctl --user disable relay-active@<owner>-<repo>-<workflow-type>.service

# Install new
cd <repo-root>
./scripts/install.sh

# Start new
systemctl --user enable daedalus-active@<owner>-<repo>-<workflow-type>.service
systemctl --user start daedalus-active@<owner>-<repo>-<workflow-type>.service
```

---

## Config migration

The workflow CLI moved from a workflow-local wrapper script to `workflows/__main__.py`. If you have cron jobs or aliases pointing at the old path, update them:

```bash
# Old (retired)
python3 <old-wrapper-script> status --json

# New
python3 -m workflows --workflow-root ~/.hermes/workflows/<owner>-<repo>-<workflow-type> status --json
```

The `scripts/migrate_config.py` helper can rewrite paths in shell scripts and systemd units.

---

## Shadow → active cutover

Daedalus runs in **shadow** mode by default after installation. It observes, derives actions, and writes shadow rows — but never dispatches to real runtimes.

### Promotion sequence

```mermaid
sequenceDiagram
    participant op as Operator
    participant rt as Daedalus runtime
    participant gate as Active gate

    op->>rt: /daedalus status
    rt-->>op: running (shadow mode)

    op->>rt: /daedalus active-gate-status
    rt-->>op: gate-ready report

    op->>gate: open gate (manual)
    op->>rt: /daedalus run-active
    rt-->>op: running (active mode)
```

### Gate checks

Before active mode is allowed, `active-gate-status` verifies:

1. **Ownership posture** — `primary_owner = daedalus`
2. **Active execution** — enabled in config
3. **Runtime mode** — not already running active elsewhere
4. **Legacy watchdog** — retired (no split-brain with relay)

If any check fails, the gate is **BLOCKED** and the operator sees exactly why.

---

## Rollback

If active mode causes problems:

```bash
# Stop active service
/daedalus service-stop

# Switch back to shadow
/daedalus run-shadow

# Or disable active execution entirely
/daedalus set-active-execution --enabled false
```

The shadow rows remain, so you can diff "what shadow would do" vs "what active did" before attempting promotion again.

---

## Where this lives in code

- Filesystem migration: `daedalus/migration.py`
- Systemd templates: `daedalus/tools.py` (service-install helpers)
- Migration scripts: `scripts/migrate_config.py`, `scripts/install.py`
- Active gate: `daedalus/runtime.py::active_gate_status`
- Shadow/active modes: `daedalus/runtime.py` (look for `Mode.SHADOW`, `Mode.ACTIVE`)
- ADR: `docs/adr/ADR-0003-daedalus-rebrand.md`
- Tests: `tests/test_daedalus_migration.py`, `tests/test_migrate_config.py`
