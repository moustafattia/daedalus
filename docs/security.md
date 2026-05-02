# Security

Sprints is for a trusted local operator on a trusted host.

It is not a multi-tenant sandbox.

## Execution Risk

Actors run through configured runtimes. Those runtimes may execute shell
commands, edit files, call network services, and use local credentials.

Treat these as code execution surfaces:

- `WORKFLOW.md`
- runtime `command:` overrides
- hook/action commands
- target repository scripts
- runtime CLIs

## Files

- Plugin code: `~/.hermes/plugins/sprints`
- Workflow roots: `~/.hermes/workflows/<owner>-<repo>-change-delivery`
- Target checkout: `repository.local-path`
- Engine DB: `<workflow-root>/runtime/state/sprints/sprints.db`

Sprints does not enforce a universal filesystem sandbox. Use runtime-specific
sandbox/approval settings when needed.

## Secrets

Do not commit secrets into:

- `WORKFLOW.md`
- actor policy text
- command arguments
- generated state files

Prefer environment variables, host credential stores, or runtime-specific auth
files.

## Network

Sprints may talk to trackers, code hosts, runtime services, and configured
webhooks. Scope credentials to the target repo where possible.
