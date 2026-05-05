# Workflows

Sprints defaults to `change-delivery`.

The bundled files under `packages/core/src/sprints/workflows/templates/` are policy templates.
Bootstrap writes the `change-delivery` template by default, and `--workflow`
selects a different bundled template.

## Files

| File | Purpose |
| --- | --- |
| `packages/core/src/sprints/workflows/templates/issue-runner.md` | Issue-focused policy template. |
| `packages/core/src/sprints/workflows/templates/change-delivery.md` | Implementation/review policy template. |
| `packages/core/src/sprints/workflows/templates/release.md` | Release planning and verification template. |
| `packages/core/src/sprints/workflows/templates/triage.md` | Incoming work triage template. |

## Implementation Specs

| Spec | Purpose |
| --- | --- |
| [Runner Split](runner-split-spec.md) | Implemented split of `sprints/workflows/runner.py` into clear execution modules. |

## Contract

Use `WORKFLOW.md` in the target repo.

The file has YAML front matter for mechanics and Markdown sections for policy.
Read [workflow-contract.md](workflow-contract.md) for the exact shape.
