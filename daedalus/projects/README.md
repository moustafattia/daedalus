# Daedalus projects

Each subdirectory under `projects/` is one **project** — a single source
repository that Daedalus operates on. Project state, config, and a clone
of the source repo all live here under a project slug.

The published Daedalus repo ships with one example project,
`yoyopod_core/`, that exists as the structural placeholder. It is
referenced by the bundled workflow's defaults so new operators have
something to point at; replace or rename it for your own use.

## Layout

```
projects/
├── README.md                # this file
└── <project-slug>/          # one directory per project
    ├── config/              # project metadata + workflow config
    │   └── project.json     # {projectSlug, displayName, workspaceRepoName}
    ├── docs/                # project-scoped runbooks (versioned)
    ├── runtime/             # mutable runtime state (gitignored)
    │   ├── memory/          # event log, alert state, status projections
    │   ├── state/           # sqlite + durable runtime state
    │   └── logs/            # local runtime/service logs
    └── workspace/           # cloned source repo (gitignored)
        └── <repo-name>/     # the actual git checkout the agents work in
```

`runtime/` and `workspace/` are excluded from git — only the README
stubs inside them are tracked, so the directory shape is preserved on a
fresh clone.

## Adding a new project

1. Create `projects/<your-slug>/config/project.json` with the three
   keys: `projectSlug`, `displayName`, `workspaceRepoName`.
2. Add `projects/<your-slug>/runtime/README.md` and
   `projects/<your-slug>/workspace/README.md` placeholders.
3. Update the bundled workflow's `workflow.yaml` to point at the new
   slug, or pass `--project <your-slug>` on the CLI.

For the time being the slug is referenced from
`daedalus/workflows/code_review/paths.py`. Generalising that to read
the slug from config is on the roadmap.
