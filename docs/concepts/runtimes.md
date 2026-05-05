# Runtimes

A runtime is the backend that executes one actor turn.

Supported runtime kinds:

- `codex-app-server`
- `hermes-agent`
- `claude-cli`
- `acpx-codex`

## Config

Actors bind to named runtime profiles:

```yaml
runtimes:
  codex:
    kind: codex-app-server
    mode: external
    endpoint: ws://127.0.0.1:4500
    ephemeral: false
    keep_alive: true

actors:
  orchestrator:
    runtime: codex
  implementer:
    runtime: codex
```

Every actor and orchestrator turn goes through the configured runtime.

## Turn Path

```text
workflows.runner
  -> workflows.actors
  -> runtimes.turns.run_runtime_stage
  -> runtime adapter
```

`runtimes/turns.py` owns the shared boundary:

- session setup
- prompt materialization for command-backed turns
- command placeholder substitution
- runtime prompt dispatch
- result normalization
- cancellation/progress callback wiring

## Command-Backed Turns

`command:` is an explicit runtime execution mode. It does not bypass the
runtime contract.

Command-backed turns receive:

- `{prompt_path}`
- `{result_path}`
- `{worktree}`
- `{session_name}`
- `{model}`
- `SPRINTS_PROMPT_PATH`
- `SPRINTS_RESULT_PATH`
- `SPRINTS_WORKTREE`
- `SPRINTS_SESSION_NAME`
- `SPRINTS_MODEL`

If the command writes JSON to `{result_path}`, Sprints reads `output`,
`session_id`, `thread_id`, `turn_id`, `tokens`, and `rate_limits` from it.

## Adapter Files

| Runtime kind | File |
| --- | --- |
| `codex-app-server` | `packages/core/src/sprints/runtimes/codex_app_server.py` |
| `hermes-agent` | `packages/core/src/sprints/runtimes/hermes_agent_cli.py` |
| `claude-cli` | `packages/core/src/sprints/runtimes/claude_cli.py` |
| `acpx-codex` | `packages/core/src/sprints/runtimes/codex_acpx.py` |
