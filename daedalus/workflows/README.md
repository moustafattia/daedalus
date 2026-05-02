# Daedalus Workflows

`daedalus/workflows/` is now the flat implementation of the policy-driven
`workflow: agentic` runtime.

There are no workflow subpackages. `issue_runner/` and `change_delivery/` were
removed because their production policy lived in Python. Workflow intent now
belongs in `WORKFLOW.md`; Python owns mechanics only.

## Layout

```text
workflows/
|-- __init__.py              # exposes the agentic workflow object
|-- __main__.py              # `python -m workflows --workflow-root <path> ...`
|-- actions.py               # deterministic action execution
|-- actors.py                # actor runtime dispatch
|-- cli.py                   # validate, show, tick
|-- config.py                # typed front matter config
|-- contract.py              # WORKFLOW.md loader and policy chunk parser
|-- gates.py                 # gate validation
|-- orchestrator.py          # orchestrator decision schema
|-- prompts.py               # prompt rendering
|-- registry.py              # workflow dispatch
|-- schema.yaml              # agentic config schema
|-- stages.py                # stage mechanics
|-- state.py                 # durable generic state
|-- workflow.py              # workflow protocol
|-- workflow.template.md     # minimal agentic template
`-- workflow_object.py       # concrete agentic workflow object
```

## Contract Shape

`WORKFLOW.md` has:

- YAML front matter for runtimes, actors, stages, gates, actions, and storage.
- `# Orchestrator Policy` for transition authority.
- `# Actor: <name>` sections for actor-specific policy and output shape.

The orchestrator decides whether to run an actor, run an action, advance,
retry, complete, or raise operator attention. The Python code validates and
executes that decision.
