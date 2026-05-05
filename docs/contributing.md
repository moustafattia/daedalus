# Contributing

Keep the codebase small and current.

## Rules

- Prefer existing module boundaries.
- Do not add compatibility shims for deleted layouts.
- Do not add docs for behavior that no longer exists.
- Keep policy in `WORKFLOW.md` templates, not Python.
- Keep runtime execution in `runtimes/`, not `workflows/`.
- Keep durable state mechanics in `engine/`.

## Checks

Install the locked development environment first:

```bash
uv sync --locked --dev
```

Run focused checks for touched files:

```bash
uv run ruff format <files>
uv run ruff check <files>
uv run python -m compileall packages __init__.py
uv run ruff check packages __init__.py
```

For docs-only changes, check links and references to deleted files.
