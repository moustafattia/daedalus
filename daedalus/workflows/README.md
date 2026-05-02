# Daedalus Workflows

`daedalus/workflows/` has one flat support layer plus bundled workflow packages.
Runtime adapters live under top-level `runtimes/`; workflow code imports them
directly.

Bundled workflow packages:

- `agentic/` - policy-driven workflow mechanics where `WORKFLOW.md` defines
  stages, gates, actors, actions, and orchestrator policy.
- `change_delivery/` - managed SDLC workflow: issue, implementation, review, PR, merge.
- `issue_runner/` - generic tracker-driven reference workflow.

`agentic/` is the clean replacement path for hardcoded workflow policy. It
reads `WORKFLOW.md` front matter plus Markdown policy chunks, runs
actors/actions mechanically, and stores generic state. `issue_runner/` and
`change_delivery/` remain legacy packages until their policies are ported into
agentic workflow templates.

Workflows are loaded by name through `workflows.<slug>`. The registry adapts
each package's `WORKFLOW` object while keeping the legacy package constants
available during migration.

## Naming

- Workflow type: external contract in `WORKFLOW.md` front matter, always `lower-kebab-case` such as `change-delivery`.
- Workflow package: Python slug under `workflows/`, always `lower_snake_case` such as `change_delivery/`.
- Workflow instance root: directory under `~/.hermes/workflows/`, always `<owner>-<repo>-<workflow-type>`.
- `instance.name` in `WORKFLOW.md` should match the workflow root directory name.

## Layout

```text
workflows/
|-- __init__.py              # public loader exports
|-- __main__.py              # `python -m workflows <name> ...`
|-- workflow.py              # standard workflow object contract
|-- registry.py              # workflow discovery + dispatch
|-- config.py                # typed config helpers
|-- config_snapshot.py       # hot-reload snapshot primitives
|-- config_watcher.py        # workflow contract file watcher
|-- paths.py                 # workflow root/path helpers
|-- stall.py                 # shared stall detection
|-- contract.py              # WORKFLOW.md parser/projector
|-- validation.py            # schema validation helpers
|-- readiness.py             # readiness recommendations
|-- runtime_matrix.py        # runtime matrix command support
|-- runtime_presets.py       # runtime config normalization
|-- agentic/                 # policy-driven workflow mechanics
|-- change_delivery/         # managed SDLC workflow internals
`-- issue_runner/            # generic tracker-driven workflow internals
```

## How a Workflow Runs

1. Daedalus loads the repo-owned `WORKFLOW.md` or `WORKFLOW-<workflow>.md`
   contract referenced by the workflow root pointer.
2. `workflows.registry` imports the workflow package referenced by `workflow:`
   in the config.
3. The workflow's `WORKFLOW` object loads typed config and builds the workspace.
4. The workflow CLI handles the requested operator command.
5. Per-tick, preflight validates dispatch-gated commands before runtime work starts.

## Adding a Workflow

New workflow packages should expose `WORKFLOW` and keep package-level constants
only as public compatibility adapters:

- `NAME`
- `SUPPORTED_SCHEMA_VERSIONS`
- `CONFIG_SCHEMA_PATH`
- `PREFLIGHT_GATED_COMMANDS`
- `make_workspace(...)`
- `cli_main(workspace, argv)`

Start from `agentic/` when the workflow can be expressed as orchestrator policy,
stages, gates, actors, and actions in `WORKFLOW.md`.
