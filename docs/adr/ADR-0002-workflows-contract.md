# ADR-0002: Workflows contract surface

**Status:** Superseded by repo-owned `WORKFLOW.md` contracts (2026-05-01)
**Supersedes:** the early `adapters/<project>/` layout

## Context

The hermes-relay plugin initially hosted one adapter per project. This
conflated two concepts:

- **Workflow type** (Code-Review, Testing, Security-Review, ...) — the engine
- **Workspace instance** (a specific repository/workflow root) — the runtime binding

Operators also could not tune workflow behavior (coder model, reviewer model,
gate policy, ...) without editing Python, and the partial JSON config that
did exist accumulated aliases from implementation history.

## Decision

Re-frame the plugin around a **workflow-plugin contract**:

- Workflows live at `workflows/<name>/` as Python packages.
- Each package exposes a five-attribute contract: `NAME`,
  `SUPPORTED_SCHEMA_VERSIONS`, `CONFIG_SCHEMA_PATH`, `make_workspace`,
  `cli_main`.
- A dispatcher at `workflows/__init__.py` reads the repo-owned `WORKFLOW.md`
  or `WORKFLOW-<name>.md`, validates the front-matter config against the
  workflow's JSON Schema, and hands off to `cli_main`.
- Runtimes (how we talk to models) are pluggable behind a `Runtime`
  `Protocol`; `acpx-codex` and `claude-cli` are the initial
  implementations. Adding a new runtime (Kimi, Gemini, HTTP-API) is a
  new module + schema entry; no dispatcher change.
- The workspace accessor exposes named runtime instances via
  `ws.runtime(name)`.
- The contract front matter cleanly separates **role** (coder, reviewer) from
  **identity** (name, model) from **runtime** (plumbing); no more
  provider-prefixed aliases for the same
  concept.

## Consequences

Positive:

- Adding a new workflow (Testing, Security-Review, ...) is a new
  directory implementing the five-attribute contract; no plugin-level
  changes required.
- Swapping the coder to a different model/runtime is a config-only
  change in most cases.
- One canonical CLI surface per workspace: `python3 -m workflows
  --workflow-root <root> <cmd>`.
- External callers (systemd, cron, runtime.py subprocess spawns) never
  couple to a specific workflow module.

Negative:

- Config file shape changed; operators use the scaffolded repo-owned
  `WORKFLOW*.md` contract. Workflow-root YAML contracts are no longer loaded.
- `plugin_entrypoint_path` now returns the generic dispatcher, not the
  per-workflow module; callers that need to pin a workflow use the
  `-m workflows.<name>` form.

## References

Historical implementation notes were removed from the public docs tree during
the public-standard cleanup. This ADR is the retained decision record.
