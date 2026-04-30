# WORKFLOW.md Guide

Daedalus uses a repo-owned workflow contract to keep workflow policy close to
the code being automated. Bootstrap writes this file into the target repository,
not into the Daedalus plugin repository.

## Where It Lives

When a repository has one workflow:

```text
/path/to/target-repo/WORKFLOW.md
```

When a repository has more than one workflow:

```text
/path/to/target-repo/WORKFLOW-issue-runner.md
/path/to/target-repo/WORKFLOW-change-delivery.md
```

The workflow root stores runtime data separately under:

```text
~/.hermes/workflows/<owner>-<repo>-<workflow-type>/
```

Bootstrap writes a pointer at `./.hermes/daedalus/workflow-root` in the target
repo so Hermes commands can find the workflow root from that checkout.

## File Shape

`WORKFLOW.md` has YAML front matter followed by Markdown policy text:

```markdown
---
workflow: issue-runner
schema-version: 1

instance:
  name: your-org-your-repo-issue-runner

tracker:
  kind: github

agent:
  name: Issue_Runner_Agent
  model: gpt-5.5
  runtime: codex
---

# Workflow Policy

Only work on the selected issue. Keep changes narrow and report validation.
```

## Front Matter

The YAML front matter is structured operator configuration:

- `workflow` selects the workflow package.
- `instance` names the workflow instance.
- `tracker` and `repository` configure where issues and code live.
- `runtimes` and `agents` bind workflow roles to execution backends.
- `hooks`, `gates`, `observability`, and `server` configure workflow-specific behavior.

Each workflow validates this section against its own schema before dispatch.

## Markdown Body

The Markdown body is policy text. Workflows decide how to use it:

- `issue-runner` renders it as the issue prompt template.
- `change-delivery` composes it into workflow-specific role prompts.

Treat edits to the body like prompt changes: review them carefully and rely on
hot reload to keep the last known good config if a bad edit lands.

## Examples

- [`docs/examples/issue-runner.workflow.md`](../examples/issue-runner.workflow.md)
- [`docs/examples/change-delivery.workflow.md`](../examples/change-delivery.workflow.md)
