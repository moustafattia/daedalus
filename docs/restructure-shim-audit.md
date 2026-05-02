# Restructure Shim Audit

Status: current after workflows flattening.

The restructure keeps public compatibility shims while internal workflow code
moves to the new namespaces.

## Retained Public Shims

- `engine/`
- `workflows/`
- `runtimes/`
- `trackers/`

These remain public compatibility paths for local plugin execution, direct
workflow CLI entrypoints, and existing imports.

## New Internal Namespaces

- `workflows.config`
- `workflows.workflow`
- `workflows.registry`
- `workflows.config_snapshot`
- `workflows.config_watcher`
- `workflows.paths`
- `workflows.stall`
- `integrations.trackers`
- `integrations.code_hosts`
- `integrations.notifications`
- `runtimes.types`
- `runtimes.registry`
- `runtimes.command`
- `daedalus.operator`

Workflow code should prefer these namespaces for new imports. Compatibility
wrappers may still import old paths internally because that is their purpose.

## Removed In Workflows Flattening

- `daedalus/workflows/core/`
- `daedalus/workflows/shared/`
- `daedalus/workflows/shared/runtimes/`
- `daedalus/workflows/change_delivery/runtimes/`
- `daedalus/workflows/change_delivery/config_snapshot.py`
- `daedalus/workflows/change_delivery/stall.py`

The public root-level `workflows/` compatibility package remains.

## Removed In Turn 12

- Private duplicate change-delivery storage path resolver, removed with the
  legacy workflow package.

No public shim was removed in this branch because root-level compatibility
packages are still listed as stable public contract.
