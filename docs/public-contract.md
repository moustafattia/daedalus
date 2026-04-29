# Public contract

This document defines the stability boundary for the first public Daedalus release.

## Stable surfaces

These are the surfaces we should treat as `v1` public contract:

- `config/workflow.yaml` for workflow instance configuration
- `hermes plugins install attmous/daedalus --enable`
- the `hermes_agent.plugins` entry point name `daedalus`
- `hermes daedalus scaffold-workflow`
- `hermes daedalus init`
- `hermes daedalus service-*`
- `/daedalus ...` operator commands
- `/workflow <name> ...` workflow commands
- the workflow root naming convention: `~/.hermes/workflows/<owner>-<repo>-<workflow-type>`

Changes to those surfaces should be documented, tested, and treated as compatibility-sensitive.

## Internal implementation

These are not public compatibility promises yet:

- SQLite schema details in `runtime/state/daedalus/daedalus.db`
- event payload internals beyond documented operator output
- archived design/spec material under `docs/superpowers/`
- playground project packs under `daedalus/projects/**`
- experimental skills and local migration helpers

We can refactor those freely as long as the stable surfaces above keep working.

## Supported workflow

The first bundled public workflow is:

- `workflow: code-review`

Additional workflow types should not be advertised as public contract until they have the same scaffold, schema, docs, and smoke-test coverage.
