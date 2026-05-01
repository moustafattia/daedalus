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
- `repository` identifies the target checkout, `tracker` selects the issue source,
  and workflows that publish PRs use `code-host` for branch/PR/merge operations.
- `tracker-feedback` controls tracker-facing comments and optional state updates.
- `runtimes` and `agents` bind workflow roles to execution backends.
- `hooks`, `gates`, `webhooks`, and `server` configure workflow-specific behavior.

Each workflow validates this section against its own schema before dispatch.

Validate it explicitly after every config edit:

```bash
hermes daedalus validate
hermes daedalus validate --service-mode active --format json
```

The validator checks:

| Check | What it catches |
|---|---|
| Contract file | Missing file, parse errors, unsupported format |
| Workflow package | Unknown workflow names or broken workflow packages |
| Schema | Missing fields, wrong types, unsupported enum values |
| Schema version | Contract versions not supported by the installed plugin |
| Service mode | Invalid modes, such as `shadow` for `issue-runner` |
| Instance name | `instance.name` not matching the workflow root directory |
| Repository path | Missing or non-directory `repository.local-path` |
| Workflow preflight | Tracker/runtime references that cannot dispatch safely |

## Runtime Presets

Use `configure-runtime` when you want the plugin to update the YAML front matter
for a known runtime shape instead of editing role bindings by hand:

```bash
hermes daedalus configure-runtime --runtime hermes-final --role agent
hermes daedalus configure-runtime --runtime hermes-chat --role internal-reviewer
hermes daedalus configure-runtime --runtime codex-service --role coder.default
```

Built-in presets are `hermes-final`, `hermes-chat`, and `codex-service`.
`issue-runner` supports `agent`; `change-delivery` supports `coder.default`,
`coder.high-effort`, `internal-reviewer`, `coder`, `reviewer`, and `all`.
Run `hermes daedalus validate` and `hermes daedalus doctor` after changing a
binding. Doctor reports each role-to-runtime binding and whether the required
CLI or external Codex service appears reachable.

## Markdown Body

The Markdown body is policy text. Workflows decide how to use it:

- `issue-runner` renders it as the issue prompt template.
- `change-delivery` composes it into workflow-specific role prompts.

Treat edits to the body like prompt changes: review them carefully and rely on
hot reload to keep the last known good config if a bad edit lands.

## Examples

| Example | Use it when |
|---|---|
| [`docs/examples/issue-runner.workflow.md`](../examples/issue-runner.workflow.md) | You want the default generic issue-runner contract. |
| [`docs/examples/change-delivery.workflow.md`](../examples/change-delivery.workflow.md) | You want the opinionated issue-to-PR-to-merge contract. |

For production, start from the same examples and fill in tracker credentials,
real runtime profiles, retention limits, hooks, gates, and tracker feedback
settings before running `hermes daedalus service-up`.
