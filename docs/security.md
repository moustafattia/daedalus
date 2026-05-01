# Security Posture

Daedalus is built for a **trusted local operator** running on a **trusted host**. It is not a hardened multi-tenant control plane.

## Trust Model

- The operator controls the workflow root, runtime binaries, hooks, and host credentials.
- The target repository may be buggy or malicious. Daedalus does **not** assume repo contents are safe to execute.
- Runtime adapters and hooks are allowed to run shell commands. Treat them as code execution surfaces.

## Filesystem and Process Scope

- Plugin code lives under `~/.hermes/plugins/daedalus`.
- Workflow config and mutable state live under `~/.hermes/workflows/<owner>-<repo>-<workflow-type>`.
- Agent turns are intended to operate inside the configured repo/worktree, but Daedalus does not enforce a universal filesystem sandbox.
- Hooks can execute arbitrary shell and can escape the worktree if you configure them to.

## Network and External Side Effects

- GitHub mutations happen through the configured runtime/tooling and inherit that runtime's credentials.
- Daedalus may post comments, push branches, open PRs, or merge when the workflow and runtime are configured to do so.
- There is no global approval gate enforced by Daedalus itself. Approval and sandbox behavior are runtime-specific.

## Secrets and Logging

- Keep secrets in environment variables, host credential stores, or runtime-specific auth surfaces.
- Do not commit secrets into `WORKFLOW.md`, workflow prompts, or hook scripts.
- Daedalus writes structured status/events/audit data for observability. It does not guarantee universal secret redaction across operator-configured prompts, hook output, or third-party runtime stderr/stdout.

## Safe Deployment Guidance

- Run Daedalus only on machines where shell execution by the selected runtimes is acceptable.
- Prefer dedicated credentials scoped to the target repository.
- Review hook scripts as carefully as application code.
- Treat `WORKFLOW.md` and prompt changes as security-sensitive configuration changes.
