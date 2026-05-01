# Public contract

This document defines the stability boundary for the first public Daedalus release.

## Stable surfaces

These are the surfaces we should treat as `v1` public contract:

- repo-owned workflow contracts:
  - `WORKFLOW.md` when a repo carries one workflow
  - `WORKFLOW-<workflow>.md` when a repo carries multiple workflows
  - bootstrap promotion from `WORKFLOW.md` to named contracts must not
    overwrite existing named contracts
- `hermes plugins install attmous/daedalus --enable`
- the `hermes_agent.plugins` entry point name `daedalus`
- `hermes daedalus bootstrap`
- `hermes daedalus scaffold-workflow`
- `hermes daedalus service-up`
- `hermes daedalus init`
- `hermes daedalus service-*`
- `/daedalus ...` operator commands
- `/workflow <name> ...` workflow commands
- the workflow root naming convention: `~/.hermes/workflows/<owner>-<repo>-<workflow-type>`
- the repo-local workflow pointer written by `bootstrap`: `./.hermes/daedalus/workflow-root`
- the workflow-root contract pointer written under runtime state

Changes to those surfaces should be documented, tested, and treated as compatibility-sensitive.

## Internal implementation

These are not public compatibility promises yet:

- SQLite schema details in `runtime/state/daedalus/daedalus.db`
- event payload internals beyond documented operator output
- placeholder-only source tree under `daedalus/projects/**` (not shipped in the
  public plugin payload)
- experimental skills and local migration helpers

We can refactor those freely as long as the stable surfaces above keep working.

## Bundled workflows

- `workflow: change-delivery`
  This is the supported managed workflow behind the public `bootstrap` and `service-up` path.
- `workflow: issue-runner`
  This is bundled as the generic tracker-driven workflow. It supports the same repo-owned `WORKFLOW*.md`, `bootstrap` / `scaffold-workflow`, and `service-up` path, but its managed service mode is `active` only.

## Contract preference

The preferred and scaffolded public path is a repo-owned `WORKFLOW*.md`.

`config/workflow.yaml` is not a supported public workflow contract. Use
repo-owned `WORKFLOW.md` or `WORKFLOW-<workflow>.md`.
