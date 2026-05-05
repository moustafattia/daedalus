# Sprints Workflows

`sprints/workflows/` is the flat implementation shared by bundled workflow
templates.

Workflow intent lives in `WORKFLOW.md`. Python owns the mechanics: loading the
contract, typing front matter, dispatching actors/actions, applying
orchestrator decisions, and writing state.

## Layout

```text
workflows/
|-- __init__.py              # public workflow exports
|-- __main__.py              # `python -m workflows --workflow-root <path> ...`
|-- loader.py                # WORKFLOW.md contract loader facade
|-- contracts.py             # WORKFLOW.md loading, rendering, and policy sections
|-- registry.py              # workflow object registry and CLI dispatch
|-- config.py                # typed front matter config
|-- bindings.py              # actor/runtime binding and runtime checks
|-- validation.py            # contract validation and readiness recommendations
|-- bootstrap.py             # repo bootstrap and scaffold mechanics
|-- daemon.py                # workflow tick loop and service controls
|-- orchestrator.py          # orchestrator prompt + decision schema
|-- runner.py                # CLI tick loop and actor/action dispatch
|-- lanes.py                 # lane ledger, reconciliation, runtime/session state
|-- teardown.py              # merge, tracker cleanup, and cleanup retry mechanics
|-- actors.py                # actor runtime dispatch
|-- actions.py               # deterministic action execution
|-- paths.py                 # workflow root and runtime path helpers
|-- schema.yaml              # workflow config schema
`-- templates/               # bundled WORKFLOW.md policy templates
    |-- issue-runner.md
    |-- change-delivery.md
    |-- release.md
    `-- triage.md
```

## Contract Shape

`WORKFLOW.md` has:

- YAML front matter for runtimes, actors, stages, gates, actions, and storage.
- `# Orchestrator Policy` for transition authority.
- `# Actor: <name>` sections for actor-specific policy and output shape.

The orchestrator decides whether to run an actor, run an action, advance,
retry, complete, or raise operator attention. The runner validates and applies
that decision.
