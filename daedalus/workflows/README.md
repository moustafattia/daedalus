# Daedalus workflows

Each subdirectory under `workflows/` is one **workflow** — a Python
package implementing the stages, gates, and agent dispatch for a
specific lifecycle. The first workflow we ship and dogfood is
`code_review/` (`Issue → Code → Review → Merge`).

Workflows are loaded by name through `workflows.<slug>`. The dispatcher
in `__init__.py` enforces a small contract: every workflow package must
expose `NAME`, `SUPPORTED_SCHEMA_VERSIONS`, `CONFIG_SCHEMA_PATH`,
`make_workspace(...)`, and `cli_main(workspace, argv)`.

## Layout

```
workflows/
├── __init__.py              # workflow loader + dispatcher contract
├── __main__.py              # `python -m workflows <name> ...` entrypoint
├── README.md                # this file
└── code_review/             # the bundled Issue → Code → Review → Merge workflow
    ├── __init__.py          # workflow contract attrs (NAME, schema, etc.)
    ├── __main__.py          # `python -m workflows.code_review ...`
    ├── cli.py               # operator subcommands (status, doctor, tick)
    ├── workflow.py          # top-level workflow wiring
    ├── orchestrator.py      # stage transitions + lane lifecycle
    ├── dispatch.py          # per-tick dispatch preflight (Symphony §6.3)
    ├── lane_selection.py    # picks which issues become active lanes
    ├── stall.py             # stall detection (Symphony §8.5)
    ├── config_snapshot.py   # AtomicRef-backed hot-reload (Symphony §6.2)
    ├── config_watcher.py    # file watcher for workflow.yaml
    ├── event_taxonomy.py    # Symphony-aligned event names (§10.4)
    ├── github.py            # GitHub API surface (issues, PRs, labels)
    ├── reviews.py           # review aggregation across reviewer agents
    ├── comments.py          # reviewer comment serialization
    ├── comments_publisher.py
    ├── sessions.py          # per-turn agent invocation bookkeeping
    ├── prompts.py           # prompt loading + parameter binding
    ├── prompts/             # prompt templates (coder, reviewer, repair)
    ├── runtimes/            # adapters: claude_cli, acpx_codex, hermes_agent
    ├── reviewers/           # external reviewer plug points
    ├── webhooks/            # incoming webhooks (slack, http_json)
    ├── server/              # optional HTTP status surface (Symphony §13.7)
    ├── schema.yaml          # JSON Schema for the workflow's config
    ├── status.py            # status projections used by /workflow status
    ├── health.py            # health checks used by /workflow doctor
    ├── observability.py     # event log + metrics
    ├── migrations.py        # config migrations
    ├── workspace.py         # workspace bootstrap (config + paths + db)
    ├── actions.py           # the action enum the runtime dispatches on
    ├── preflight.py         # config validity check (callable per-tick)
    └── paths.py             # canonical paths inside a project workspace
```

## How a workflow runs

1. Daedalus's tick loop loads `workflow.yaml` from the project's
   workspace directory.
2. The dispatcher imports the workflow package referenced by
   `workflow:` in the config (e.g. `code-review`).
3. `make_workspace(workflow_root, config)` returns the workspace
   object the CLI subcommands operate on.
4. Per-tick: preflight validates the config; if it passes, the
   orchestrator picks a lane and dispatches the next agent role.

## Adding a new workflow

1. Create `workflows/<your-name>/__init__.py` implementing the five
   required attributes from the contract.
2. Add a `schema.yaml` defining the workflow's config shape.
3. Implement `cli_main(workspace, argv)` so operators can run
   `/workflow <your-name> status` and friends.
4. Reference it from `workflow.yaml` in the project workspace:
   `workflow: <your-name>`.

The `code_review/` package is the reference implementation — start by
copying its `__init__.py`, `cli.py`, and `schema.yaml` and pruning what
you don't need.
