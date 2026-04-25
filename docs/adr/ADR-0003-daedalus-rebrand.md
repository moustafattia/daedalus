# ADR-0003: Daedalus rebrand

**Status:** Accepted (2026-04-25)
**Supersedes:** Project identity from ADR-0001 / ADR-0002 era ("hermes-relay")

## Context

The plugin originally shipped as `hermes-relay` — a name that described
its initial role (a relay/orchestrator inside the Hermes plugin
ecosystem). The project has since evolved into a generic workflow
engine that orchestrates other agents through structured workflows
(code-review first, with Testing, Security-Review, etc. on the
roadmap). The "relay" framing no longer captures what the project does.

The workflows-contract migration (ADR-0002) decoupled "workflow type"
from "workspace instance" but kept the engine's identity tied to its
historical name. This created confusion: operators saw `relay` in some
surfaces and `workflow` in others, with no clear semantic boundary.

## Decision

Rebrand the engine to **Daedalus**. The master craftsman of Greek
myth — patron of artisans, builder of complex devices. The name
preserves the Hermes mythological lineage while precisely describing
what the project is: an engine that orchestrates other agents through
structured workflows.

The rename is a single-sweep, no-backward-compat migration:

- Repo: `hermes-relay` → `daedalus`
- Plugin: `plugin.yaml` `name: hermes-relay` → `daedalus`; install dir
  `~/.hermes/plugins/hermes-relay` → `~/.hermes/plugins/daedalus`
- Slash commands: `/relay <cmd>` → `/daedalus <cmd>` (engine commands).
  New `/workflow <name> <cmd>` slash command for per-workflow CLI access.
- Filesystem: `state/relay/relay.db` → `state/daedalus/daedalus.db` (with
  WAL/SHM sidecars), `memory/relay-events.jsonl` →
  `memory/daedalus-events.jsonl`, `memory/hermes-relay-alert-state.json`
  → `memory/daedalus-alert-state.json`
- Env vars: `HERMES_RELAY_WORKFLOW_ROOT` / `YOYOPOD_RELAY_WORKFLOW_ROOT`
  → `DAEDALUS_WORKFLOW_ROOT`
- Systemd: hardcoded `yoyopod-relay-{shadow,active}.service` → template
  units `daedalus-{shadow,active}@<workspace>.service` (enables multiple
  workspaces on one host)
- Internal Python identifiers: `RelayCommandError` →
  `DaedalusCommandError`, `init_relay_db` → `init_daedalus_db`,
  `append_relay_event` → `append_daedalus_event`,
  `RELAY_SCHEMA_VERSION` → `DAEDALUS_SCHEMA_VERSION`, etc.
- SQL schema: `relay_runtime` table → `daedalus_runtime`, `runtime_id`
  row identity `'relay'` → `'daedalus'`, event types
  `relay_runtime_*` → `daedalus_runtime_*`

A one-shot filesystem migrator (`migration.py`) runs at runtime
startup, transparently renaming relay-era files (including SQLite
WAL/SHM sidecars). A separate `daedalus migrate-systemd` operator
command handles the systemd cutover.

## Consequences

Positive:

- Engine identity matches what the engine does
- Slash command split (`/daedalus` engine vs `/workflow` per-workflow)
  cleanly separates what's being controlled
- Systemd template units enable multiple workspaces on one host
- Hard cut (no backward compat) avoids the dual-name period that would
  confuse operators

Negative:

- Operators need to learn the new identity (cheat sheet + skill docs
  capture the new vocabulary)
- Live cutover requires ~30s downtime (acceptable since the workspace
  is idle when the cutover runs)
- External cron jobs / scripts referencing old paths need update (the
  migrator handles file renames; environment / unit references must be
  manually updated)

## Out of scope

- Visual identity redesign (current SVGs keep their visuals; only
  filename + embedded text strings change)
- Brand voice / marketing copy beyond mechanical find-replace
- Multi-workspace operation beyond what the systemd template enables
- New workflow types (Testing, Security-Review, ...) — separate effort

## References

- Spec: `docs/superpowers/specs/2026-04-25-daedalus-rename-design.md`
- Plan: `docs/superpowers/plans/2026-04-25-daedalus-rename.md`
- Predecessor: `docs/adr/ADR-0002-workflows-contract.md`
