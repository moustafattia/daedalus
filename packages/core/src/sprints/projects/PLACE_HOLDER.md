# Project Placeholder

This directory intentionally contains no committed project implementation.

Sprints should stay project-agnostic in the public repository. Project-specific
checkouts, skills, prompts, operational notes, and runtime artifacts belong in a
private repository, a private plugin/package, or the workflow instance data
directory for that deployment.

Use these locations instead:

- Repo-owned workflow policy: `WORKFLOW.md` or `WORKFLOW-<workflow>.md`.
- Operator runtime state: `~/.hermes/workflows/<owner>-<repo>-<workflow-type>/`.
- Agent working checkout: the path configured as `repository.local-path`.
- Public reusable engine code: `packages/core/src/sprints/workflows/`,
  `packages/core/src/sprints/runtimes/`, and
  `packages/core/src/sprints/trackers/`.

Before upstreaming changes, verify this tree still contains only generic
placeholder documentation.
