# Sprints Packages Layout Migration Spec

Date: 2026-05-05

## Goal

Restructure the repository from a Hermes-plugin-first layout into a product-first
multi-package workspace. The product is Sprints. Hermes becomes one plugin host
under `packages/plugins/hermes/`, not the root organizing principle.

The target repository name is `sprints/`.

## Target Root Layout

```text
sprints/
  README.md
  LICENSE
  AGENTS.md
  plugin.yaml
  __init__.py
  pyproject.toml
  uv.lock
  .python-version
  .gitignore
  .github/
    workflows/
      ci.yml

  docs/
    architecture.md
    public-contract.md
    operator/
    workflows/
    concepts/
    superpowers/
      specs/

  packages/
    core/
    cli/
    tui/
    web/
    mob/
    plugins/
      hermes/
      openclaw/

  packaging/
    systemd/
    docker/
    completions/

  scripts/
    dev/
    release/
```

`packages/` contains installable product packages. `packaging/` contains
distribution artifacts that are not themselves Python product packages.

Root `plugin.yaml` and root `__init__.py` are intentional. They preserve the
Hermes directory-plugin install path:

```bash
hermes plugins install attmous/sprints --enable
```

They are not transitional shims. They are the canonical Hermes directory-plugin
entrypoint for Git-based installs.

## Package Names

| Path | Project name | Import namespace | Purpose |
| --- | --- | --- | --- |
| `packages/core` | `sprints-core` | `sprints` | Engine, workflows, runtime adapters, trackers, services, app API. |
| `packages/cli` | `sprints-cli` | `sprints_cli` | Standalone command-line interface. |
| `packages/tui` | `sprints-tui` | `sprints_tui` | Terminal UI. |
| `packages/web` | `sprints-web` | `sprints_web` | Web server and web UI adapter. |
| `packages/mob` | `sprints-mobile` | `sprints_mobile` | Mobile adapter/app package. |
| `packages/plugins/hermes` | `sprints-hermes-plugin` | `sprints_hermes` | Hermes plugin integration. |
| `packages/plugins/openclaw` | `sprints-openclaw-plugin` | `sprints_openclaw` | OpenClaw plugin integration. |

The package directory remains `mob/` as approved, but the Python import namespace
is `sprints_mobile` for clarity.

## Dependency Rules

Allowed dependency direction:

```text
packages/cli              -> packages/core
packages/tui              -> packages/core
packages/web              -> packages/core
packages/mob              -> packages/core
packages/plugins/hermes   -> packages/core
packages/plugins/openclaw -> packages/core
```

Forbidden dependencies:

```text
packages/core -> packages/cli
packages/core -> packages/tui
packages/core -> packages/web
packages/core -> packages/mob
packages/core -> packages/plugins/*
packages/plugins/* -> packages/cli
```

Plugins and UIs must call shared product behavior through `sprints.app`, not by
shelling out to the CLI or importing presentation modules.

## Core Package Layout

```text
packages/core/
  pyproject.toml
  src/
    sprints/
      __init__.py
      app/
        commands.py
        models.py
        errors.py
      core/
        contracts.py
        config.py
        paths.py
        validation.py
        doctor.py
        bootstrap.py
        init_wizard.py
      engine/
      workflows/
      runtimes/
      trackers/
      services/
      observe/
```

`sprints.app` is the stable application layer used by every interface. It should
return typed or clearly shaped DTOs and avoid printing, terminal formatting, web
framework objects, or Hermes context objects.

Initial application API:

```python
get_status(workflow_root)
run_doctor(workflow_root, fix=False)
init_workflow(options)
validate_workflow(workflow_root)
list_runs(workflow_root, filters)
list_events(workflow_root, filters)
control_daemon(workflow_root, action, options)
control_codex_app_server(workflow_root, action, options)
```

## Interface Package Layouts

```text
packages/cli/
  pyproject.toml
  src/sprints_cli/
    __init__.py
    main.py
    commands.py
    render.py
    formatters.py
```

```text
packages/tui/
  pyproject.toml
  src/sprints_tui/
    __init__.py
    main.py
    data.py
    screens/
    widgets/
```

```text
packages/web/
  pyproject.toml
  src/sprints_web/
    __init__.py
    server.py
    routes.py
    models.py
    frontend/
```

```text
packages/mob/
  pyproject.toml
  src/sprints_mobile/
    __init__.py
```

```text
packages/plugins/hermes/
  pyproject.toml
  plugin.yaml
  after-install.md
  src/sprints_hermes/
    __init__.py
    register.py
    install_checks.py
    commands.py
```

```text
packages/plugins/openclaw/
  pyproject.toml
  src/sprints_openclaw/
    __init__.py
```

## Hermes Agent Recognition Surface

Hermes Agent recognizes general plugins through two relevant mechanisms:

1. Directory plugins under a scanned plugin directory.
2. Pip entry points in the `hermes_agent.plugins` entry-point group.

For directory plugins, Hermes requires the plugin directory itself to contain:

```text
plugin.yaml
__init__.py
```

The `__init__.py` module must expose:

```python
def register(ctx): ...
```

Inside `register(ctx)`, Sprints should register:

```python
ctx.register_command(
    "sprints",
    handler=handle_sprints_slash_command,
    description="Operate the Sprints workflow engine.",
)

ctx.register_command(
    "workflow",
    handler=handle_workflow_slash_command,
    description="Run a Sprints workflow command.",
)

ctx.register_cli_command(
    name="sprints",
    help="Operate the Sprints workflow engine.",
    setup_fn=setup_sprints_argparse,
    handler_fn=handle_sprints_cli_namespace,
)
```

Register bundled actor/operator skills with:

```python
ctx.register_skill(skill_name, skill_md_path)
```

Do not register Sprints workflow operations as model tools unless there is a
separate product decision to expose them to the LLM tool registry. The current
plugin surface is command-oriented: `/sprints`, `/workflow`, and
`hermes sprints ...`.

The Hermes plugin manifest must remain factual:

```yaml
name: sprints
version: 0.3.0
description: Sprints workflow engine and operator control surface.
author: Sprints
provides_tools: []
provides_hooks: []
```

If Sprints needs environment values later, add `requires_env` entries to
`plugin.yaml`; Hermes prompts for missing values during plugin install.

### Distribution Consequence

The approved monorepo layout places the Hermes source package at:

```text
packages/plugins/hermes/
```

Hermes documentation and source do not describe `hermes plugins install
owner/repo` installing a nested subdirectory from a monorepo. Therefore, the
repo root must keep the Hermes directory-plugin recognition files:

```text
plugin.yaml
__init__.py
```

This keeps direct Git installs supported:

```bash
hermes plugins install attmous/sprints --enable
```

Root `__init__.py` should delegate to `sprints_hermes.register(ctx)` from
`packages/plugins/hermes`. That delegation is a first-class install surface, not
a fallback path.

Supported recognition paths after the migration:

1. Git directory-plugin install from the repo root:

   ```text
   plugin.yaml
   __init__.py
   ```

2. Pip/package install: `packages/plugins/hermes` exposes:

   ```toml
   [project.entry-points."hermes_agent.plugins"]
   sprints = "sprints_hermes"
   ```

   `sprints_hermes.__init__` exposes `register(ctx)`.

3. Directory artifact install: release automation produces a Hermes plugin
   artifact whose root contains `plugin.yaml` and `__init__.py`.

Do not keep any additional root-level Python package besides the Hermes
directory-plugin entrypoint.

## Root Workspace Config

The root `pyproject.toml` owns workspace membership and shared development
tooling. Package dependencies live in package-specific `pyproject.toml` files.

```toml
[project]
name = "sprints-workspace"
version = "0.0.0"
requires-python = ">=3.11"

[tool.uv.workspace]
members = [
  "packages/core",
  "packages/cli",
  "packages/tui",
  "packages/web",
  "packages/mob",
  "packages/plugins/hermes",
  "packages/plugins/openclaw",
]

[dependency-groups]
dev = [
  "ruff>=0.14.0",
]
```

The standalone CLI package exposes:

```toml
[project.scripts]
sprints = "sprints_cli.main:main"
```

Hermes packaging continues to expose Hermes commands from
`packages/plugins/hermes`.

## Migration Plan

### Phase 1: Create Workspace Skeleton

1. Create `packages/core`, `packages/cli`, `packages/tui`, `packages/web`,
   `packages/mob`, `packages/plugins/hermes`, and `packages/plugins/openclaw`.
2. Add package-local `pyproject.toml` files.
3. Convert the root `pyproject.toml` into a uv workspace.
4. Keep existing code in place temporarily.
5. Verify `uv lock` and `uv sync --locked --dev`.

### Phase 2: Move Core Code

Move product logic into `packages/core/src/sprints/`:

```text
sprints/engine        -> packages/core/src/sprints/engine
sprints/workflows     -> packages/core/src/sprints/workflows
sprints/runtimes      -> packages/core/src/sprints/runtimes
sprints/trackers      -> packages/core/src/sprints/trackers
sprints/observe       -> packages/core/src/sprints/observe
```

Move setup and contract logic into core-owned modules:

```text
sprints/workflows/contracts.py   -> packages/core/src/sprints/core/contracts.py
sprints/workflows/config.py      -> packages/core/src/sprints/core/config.py
sprints/workflows/paths.py       -> packages/core/src/sprints/core/paths.py
sprints/workflows/validation.py  -> packages/core/src/sprints/core/validation.py
sprints/workflows/bootstrap.py   -> packages/core/src/sprints/core/bootstrap.py
sprints/workflows/init_wizard.py -> packages/core/src/sprints/core/init_wizard.py
sprints/workflows/doctor.py      -> packages/core/src/sprints/core/doctor.py
```

This migration is a hard cutover for product Python module paths. Do not add
compatibility wrappers for old module paths. Every import must be updated to the
final package path in the same change that moves the file.

### Phase 3: Add `sprints.app`

1. Create `packages/core/src/sprints/app/commands.py`.
2. Move current command behavior behind app functions.
3. Keep app return values structured and presentation-neutral.
4. Update core internals to avoid CLI imports.

This phase is complete when `run_doctor`, `get_status`, `init_workflow`, and
validation all work without importing `sprints_cli` or Hermes modules.

### Phase 4: Move CLI

Move CLI-only code:

```text
sprints/cli/*      -> packages/cli/src/sprints_cli/
sprints/sprints_cli.py -> packages/cli/src/sprints_cli/main.py
```

Then update CLI commands so they call `sprints.app.commands` instead of reaching
into workflow internals directly. Keep text and JSON rendering in the CLI
package.

### Phase 5: Move Hermes Plugin

Move Hermes-specific files:

```text
sprints/install_checks.py -> packages/plugins/hermes/src/sprints_hermes/install_checks.py
sprints/__init__.py plugin registration -> packages/plugins/hermes/src/sprints_hermes/register.py
plugin.yaml -> packages/plugins/hermes/plugin.yaml
sprints/plugin.yaml -> packages/plugins/hermes/plugin.yaml or package artifact copy
after-install.md -> packages/plugins/hermes/after-install.md
```

The Hermes plugin should import `sprints.app.commands` and `sprints_hermes`
install checks only. It should not own product behavior.

### Phase 6: Add Empty TUI/Web/Mobile/OpenClaw Packages

Create minimal package shells only:

```text
packages/tui/src/sprints_tui/
packages/web/src/sprints_web/
packages/mob/src/sprints_mobile/
packages/plugins/openclaw/src/sprints_openclaw/
```

Do not move behavior into these packages until their interfaces are defined.
They should depend on `sprints-core` and call `sprints.app`.

### Phase 7: Repository Docs and Commands

1. Update README quick start to show standalone and Hermes paths separately.
2. Update architecture docs to describe `core`, `app`, interfaces, and plugins.
3. Update public contract docs with package and command surfaces.
4. Update CI to run package-aware commands.

Expected commands:

```bash
uv sync --locked --dev
uv run ruff check packages
uv run python -m compileall packages
uv run sprints doctor --fix
```

Hermes-specific verification remains separate and should only run when Hermes is
available in the environment.

### Phase 8: Remove Transitional Shims

This phase should be a verification phase, not a cleanup phase. The migration
must not introduce transitional shims. The only allowed root Python file is the
Hermes directory-plugin entrypoint.

1. Confirm no old root-level `sprints/` package files remain.
2. Confirm no compatibility wrappers or fallback imports exist.
3. Confirm imports reference final package paths.
4. Confirm generated artifacts and plugin manifests point at
   `packages/plugins/hermes`.

## Compatibility Policy

The migration is a breaking internal layout cutover for product modules, not a
staged compatibility layer. Preserve operator-facing command names and the
Hermes Git install path, but do not preserve old Python module import paths.

Preserve these operator-facing commands:

```text
hermes sprints init
hermes sprints doctor
hermes sprints doctor --fix
hermes sprints daemon ...
hermes sprints codex-app-server ...
/sprints ...
```

Preserve this install path:

```text
hermes plugins install attmous/sprints --enable
```

Add standalone commands:

```text
sprints init
sprints doctor
sprints doctor --fix
sprints daemon ...
sprints codex-app-server ...
```

The same app-layer function should power each matching standalone and Hermes
command.

## Risks

- Import churn: moving modules from `workflows.*` to `sprints.core.*` can break
  internal imports. The mitigation is to update all imports in the cutover, not
  to add wrappers.
- Plugin packaging drift: Hermes metadata may need package-specific install
  paths. Keep root `plugin.yaml` and root `__init__.py` aligned with
  `packages/plugins/hermes`, and keep package-only plugin metadata in
  `packages/plugins/hermes`.
- UI duplication: TUI and WebUI can accidentally rebuild command logic. Avoid
  this by making `sprints.app` the only command surface they call.
- Workspace lock churn: all package dependency changes update the shared
  `uv.lock`. Keep dependency changes grouped by migration phase.

## Done Criteria

- Root repo is a uv workspace named `sprints-workspace`.
- Core product imports live under `packages/core/src/sprints`.
- Standalone CLI entrypoint `sprints` works.
- Hermes plugin still exposes existing Hermes commands.
- `hermes plugins install attmous/sprints --enable` remains a supported install
  path.
- `doctor --fix` behavior is available from standalone CLI and Hermes plugin.
- TUI, Web, Mobile, Hermes, and OpenClaw packages depend on core but core does
  not depend on them.
- No old root-level Python package remains.
- Root `__init__.py` exists only as the Hermes directory-plugin entrypoint.
- No compatibility wrappers preserve old module paths.
- No fallback imports route between old and new layouts.
- Repo docs explain the new layout and migration status.
