# Workflows

Sprints defaults to `change-delivery`.

The bundled files under `sprints/workflows/templates/` are policy templates.
Bootstrap writes the `change-delivery` template by default, and `--workflow`
selects a different bundled template.

## Files

| File | Purpose |
| --- | --- |
| `sprints/workflows/templates/issue-runner.md` | Issue-focused policy template. |
| `sprints/workflows/templates/change-delivery.md` | Implementation/review policy template. |
| `sprints/workflows/templates/release.md` | Release planning and verification template. |
| `sprints/workflows/templates/triage.md` | Incoming work triage template. |

## Contract

Use `WORKFLOW.md` in the target repo.

The file has YAML front matter for mechanics and Markdown sections for policy.
Read [workflow-contract.md](workflow-contract.md) for the exact shape.
