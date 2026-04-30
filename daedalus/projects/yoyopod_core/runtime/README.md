# yoyopod_core runtime data

This directory documents the older project-pack layout and is kept only as
historical playground material.

It is not part of the shipped plugin payload, and it is not the supported home
for public workflow-instance runtime state. Public runtime state lives under
the real workflow root:

- `~/.hermes/workflows/<owner>-<repo>-<workflow-type>/runtime/`

Expected contents over time:

- `memory/` — status projections, audit logs, alert state
- `state/` — sqlite and durable runtime state
- `logs/` — local runtime/service logs if needed

Do not store plugin source code here.
