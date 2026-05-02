# Daedalus workflows

Each subdirectory under `workflows/` is one **workflow** — a Python
package implementing the stages, gates, and agent dispatch for a
specific lifecycle. Today we bundle two workflow surfaces:

- `change_delivery/` — the opinionated managed SDLC workflow (`Issue → Code → Review → Merge`)
- `issue_runner/` — the generic tracker-driven reference workflow

Shared workflow mechanics live under `shared/`. Shared execution backends live
under top-level `runtimes/`, shared tracker integrations live under
top-level `trackers/`, including PR/merge clients.

Workflows are loaded by name through `workflows.<slug>`. The dispatcher
in `__init__.py` enforces a small contract: every workflow package must
expose `NAME`, `SUPPORTED_SCHEMA_VERSIONS`, `CONFIG_SCHEMA_PATH`,
`make_workspace(...)`, and `cli_main(workspace, argv)`.

## Naming

- Workflow type: external contract in `WORKFLOW.md` front matter, always `lower-kebab-case` such as `change-delivery`.
- Workflow package: Python slug under `workflows/`, always `lower_snake_case` such as `change_delivery/`.
- Workflow instance root: directory under `~/.hermes/workflows/`, always `<owner>-<repo>-<workflow-type>`.
- `instance.name` in `WORKFLOW.md` should match the workflow root directory name.

## Layout

```
workflows/
├── __init__.py              # workflow loader + dispatcher contract
├── __main__.py              # `python -m workflows <name> ...` entrypoint
├── README.md                # this file
├── shared/                  # reusable paths, snapshots, stall helpers
└── change_delivery/         # the bundled Issue → Code → Review → Merge workflow
    ├── __init__.py          # workflow contract attrs (NAME, schema, etc.)
    ├── __main__.py          # `python -m workflows.change_delivery ...`
    ├── cli.py               # operator subcommands (status, doctor, tick)
    ├── workflow.py          # top-level workflow wiring
    ├── orchestrator.py      # stage transitions + lane lifecycle
    ├── dispatch.py          # per-tick dispatch preflight (Symphony §6.3)
    ├── lane_selection.py    # picks which issues become active lanes
    ├── stall.py             # stall detection (Symphony §8.5)
    ├── config_snapshot.py   # AtomicRef-backed hot-reload (Symphony §6.2)
    ├── config_watcher.py    # file watcher for the workflow contract
    ├── event_taxonomy.py    # Symphony-aligned event names (§10.4)
    ├── github.py            # GitHub API surface (issues, PRs, labels)
    ├── reviews.py           # review aggregation across reviewer agents
    ├── sessions.py          # per-turn agent invocation bookkeeping
    ├── prompts.py           # prompt loading + parameter binding
    ├── prompts/             # prompt templates (coder, reviewer, repair)
    ├── runtimes/            # workflow-local compatibility re-exports over shared runtimes/
    ├── reviewers/           # external reviewer plug points
    ├── webhooks/            # incoming webhooks (slack, http_json)
    ├── server/              # optional HTTP status surface (Symphony §13.7)
    ├── schema.yaml          # JSON Schema for the workflow's config
    ├── status.py            # status projections used by /workflow status
    ├── health.py            # health checks used by /workflow doctor
    ├── migrations.py        # config migrations
    ├── workspace.py         # workspace bootstrap (config + paths + db)
    ├── actions.py           # the action enum the runtime dispatches on
    ├── preflight.py         # config validity check (callable per-tick)
    └── paths.py             # compatibility re-export for shared path helpers
└── issue_runner/            # generic tracker-driven issue execution workflow
    ├── __init__.py          # workflow contract attrs (NAME, schema, etc.)
    ├── __main__.py          # `python -m workflows.issue_runner ...`
    ├── cli.py               # status, doctor, tick, run
    ├── preflight.py         # config validity checks for dispatch-gated commands
    ├── schema.yaml          # JSON Schema for the workflow's config
    ├── tracker.py           # issue selection rules over shared trackers/
    ├── workspace.py         # runtime wiring, hooks, prompt rendering, storage
    └── workflow.template.md # scaffoldable WORKFLOW.md baseline
```

## How a workflow runs

1. Daedalus loads the repo-owned `WORKFLOW.md` / `WORKFLOW-<workflow>.md`
   contract referenced by the workflow root pointer.
2. The dispatcher imports the workflow package referenced by
   `workflow:` in the config (e.g. `change-delivery`).
3. `make_workspace(workflow_root, config)` returns the workspace
   object the CLI subcommands operate on.
4. Per-tick: preflight validates the config; if it passes, the
   workflow-specific workspace runs its next action.

## Adding a new workflow

1. Create `workflows/<your-name>/__init__.py` implementing the five
   required attributes from the contract.
2. Add a `schema.yaml` defining the workflow's config shape.
3. Implement `cli_main(workspace, argv)` so operators can run
   `/workflow <your-name> status` and friends.
4. Reference it from `WORKFLOW.md` front matter in the workflow root:
   `workflow: <your-name>`.

`change_delivery/` is the managed SDLC reference implementation.
`issue_runner/` is the smaller generic reference surface. Start by
copying the one whose contract is closer to the lifecycle you want.
