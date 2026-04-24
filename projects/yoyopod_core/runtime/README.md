# yoyopod_core runtime data

This directory is the live mutable home for `yoyopod-core` runtime state.

Expected contents over time:

- `memory/` — status projections, audit logs, alert state
- `state/` — sqlite and durable runtime state
- `logs/` — local runtime/service logs if needed

Do not store plugin source code here.
