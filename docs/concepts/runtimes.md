# Runtimes

A **runtime** is the thing Daedalus shells out to when a turn happens. Daedalus owns leases, state, and dispatch; the runtime owns "how do I actually run an LLM turn against a worktree." Four are shipped today.

At the code level, these shared execution backends live under
`daedalus/runtimes/`. The operator-facing contract also uses the `runtimes:`
config block because workflows bind named runtime profiles to workflow roles.
Every runtime-backed role must name a runtime profile. Command overrides are
execution details on that role/profile; they are not fallback paths around the
runtime contract.

## The Protocol

```python
class Runtime(Protocol):
    def ensure_session(*, worktree, session_name, model, resume_session_id) -> SessionHandle
    def run_prompt(*, worktree, session_name, prompt, model) -> str
    def run_command(*, worktree, command_argv, env) -> str   # for `command:` overrides
    def assess_health(session_meta, *, worktree, now_epoch) -> SessionHealth
    def close_session(*, worktree, session_name) -> None

    # Optional — runtime opts out by simply not defining it.
    def last_activity_ts() -> float | None
```

`last_activity_ts()` is the Symphony §8.5 hook that lets [stall detection](stalls.md) work. Runtimes without it are skipped by the reconciler — they opt out silently.

## Runtime Stages

Workflow code does not invoke runtime adapters directly. It runs named stages
through `daedalus/runtimes/stages.py`. The shared stage dispatcher owns the
execution boundary:

- receives the workflow-selected runtime profile and agent config
- ensures/resumes the runtime session before dispatch
- chooses `agent.command` or runtime `command` for command-backed runtimes
- renders `{prompt_path}`, `{worktree}`, `{session_name}`, `{model}`, and workflow-specific placeholders
- calls `run_prompt_result()` / `run_prompt()` for prompt-native runtimes
- wires cancellation/progress callbacks when the runtime supports them
- normalizes command output into a `PromptRunResult`-shaped metrics source

The workflow still owns state, prompts, gates, and tracker/code-host effects.
This is what lets `issue-runner`, `change-delivery`, and future workflows bind
Codex app-server or Hermes Agent to any runtime-backed stage without hard-coded
runtime names in the workflow logic.

Important: for `codex-app-server`, `runtime.command` starts or connects the
app-server transport. It is not treated as a per-stage command. Use
`agent.command` only when a role should run a command-backed adapter.

## Adapter shape comparison

| Runtime kind | Execution model | Session behavior | Strongest capabilities |
|---|---|---|---|
| `hermes-agent` | `hermes -z` or `hermes chat --quiet -q` | one-shot from Daedalus' point of view | `prompt-turn`, `command-stage`, `one-shot`, `activity-heartbeat` |
| `codex-app-server` | JSON-RPC over stdio or WebSocket | resumable Codex threads | `persistent-session`, `resume`, `cancel`, `structured-events`, `token-metrics`, `thread-visible` |
| `claude-cli` | `claude --print ...` | one-shot | `prompt-turn`, `command-stage`, `one-shot`, `activity-heartbeat` |
| `acpx-codex` | `acpx codex prompt -s <name>` | resumable ACPX sessions | `persistent-session`, `resume`, `activity-heartbeat` |

External `codex-app-server` profiles also expose `service-required` because the
listener must already be running.

## Capability Validation

Daedalus validates runtime bindings before dispatch. The checks cover:

- runtime profile exists
- `runtime.kind` is one of the registered adapters
- workflow stages and gates reference declared actors
- each bound actor/agent has the execution capability needed for its stage
- explicit `required-capabilities` are supported by the selected runtime

Use `required-capabilities` only when the workflow role truly depends on a
runtime feature:

```yaml
actors:
  implementer:
    name: Change_Implementer
    model: gpt-5.4
    runtime: codex-service
    required-capabilities:
      - persistent-session
      - resume
      - token-metrics
```

If the selected runtime lacks one of those capabilities, `hermes daedalus
validate`, `doctor`, `runtime-matrix`, and `configure-runtime` fail instead of
silently falling back.

## Selection in `WORKFLOW.md`

```yaml
runtimes:
  coder-runtime:
    kind: claude-cli
    max-turns-per-invocation: 24
    timeout-seconds: 1200
  reviewer-runtime:
    kind: acpx-codex
    session-idle-freshness-seconds: 900
    session-idle-grace-seconds: 1800
    session-nudge-cooldown-seconds: 600

actors:
  implementer:
    name: claude-implementer
    model: opus
    runtime: coder-runtime
  reviewer:
    name: codex-reviewer
    model: gpt-5
    runtime: reviewer-runtime
```

The preflight pass walks `runtimes.<name>.kind` and workflow-specific gate
types to confirm the contract can dispatch safely before a tick runs.

## Configure with Presets

For common choices, let Daedalus edit the repo-owned workflow contract:

```bash
hermes daedalus configure-runtime --runtime hermes-final --role agent
hermes daedalus configure-runtime --runtime hermes-chat --role reviewer
hermes daedalus configure-runtime --runtime codex-service --role implementer
```

The command writes a named profile under `runtimes:` and updates the selected
role under `agent:` or actor under `actors:`. Use `--runtime-name` if you want the profile
key to be different from the preset name. Use `--dry-run --json` to inspect the
change without writing the file.

## Runtime Matrix Check

After editing runtime bindings, inspect the workflow's role matrix:

```bash
hermes daedalus runtime-matrix
hermes daedalus runtime-matrix --format json
```

This reports every runtime-backed role, the selected profile, adapter kind,
binding health, and host availability. To exercise the shared stage boundary
with a tiny prompt, run:

```bash
hermes daedalus runtime-matrix --execute
hermes daedalus runtime-matrix --role agent --execute
hermes daedalus runtime-matrix --runtime codex-service --execute --format json
```

`--execute` does not mutate trackers or code hosts. It only creates a temporary
worktree under the workflow root and dispatches one minimal prompt through the
configured runtime profile. For `codex-service`, start the shared Codex listener
first with `hermes daedalus codex-app-server up`.

### `hermes-agent` runtime

The `hermes-agent` runtime delegates turns to a local Hermes Agent CLI. By
default it uses the documented scripted path, `hermes -z`, which returns only
the final answer. Set `mode: chat` to use `hermes chat --quiet -q` when you need
Hermes chat features such as `--source`, `--max-turns`, skills, toolsets, or
session resume.

```yaml
runtimes:
  hermes-final:
    kind: hermes-agent
    mode: final
    provider: openrouter
    timeout-seconds: 1200

  hermes-chat:
    kind: hermes-agent
    mode: chat
    source: daedalus
    max-turns: 90
    toolsets: terminal,skills
```

Custom `command:` overrides still work. Command-backed stages receive
`{prompt_path}`, `{result_path}`, `{worktree}`, `{session_name}`, and `{model}`
placeholders plus `DAEDALUS_*` environment variables. If the command writes a
JSON object to `{result_path}`, Daedalus records its `output`, `session_id`,
`thread_id`, `turn_id`, `tokens`, `rate_limits`, and related fields as the
runtime result.

### `codex-app-server` runtime

The `codex-app-server` runtime speaks JSON-RPC to Codex app-server. In managed
mode it starts `codex app-server` over stdio for one run. In external mode it
connects to a long-running WebSocket listener. It sends `initialize`, starts or
resumes a thread with `thread/start` or `thread/resume`, sends `turn/start`, and
consumes notifications until `turn/completed`.

```yaml
runtimes:
  codex:
    kind: codex-app-server
    command: codex app-server
    ephemeral: false
    approval_policy: never
    thread_sandbox: workspace-write
    turn_sandbox_policy: workspace-write
```

For a supervised long-running listener, install and start the Daedalus-managed
user service:

```bash
hermes daedalus codex-app-server install
hermes daedalus codex-app-server up
hermes daedalus codex-app-server status
hermes daedalus codex-app-server doctor
hermes daedalus codex-app-server logs
```

The default listener is `ws://127.0.0.1:4500`. The generated unit runs:

```bash
codex app-server --listen ws://127.0.0.1:4500
```

If you expose the WebSocket listener beyond loopback, configure auth when
installing the service. Supported auth modes mirror Codex app-server:

```bash
hermes daedalus codex-app-server up \
  --ws-token-file /absolute/path/to/codex-app-server.token

hermes daedalus codex-app-server up \
  --ws-token-sha256 <sha256-hex>

hermes daedalus codex-app-server up \
  --ws-shared-secret-file /absolute/path/to/shared-secret \
  --ws-issuer daedalus \
  --ws-audience codex-app-server
```

Client-side runtime config can then use `ws_token_file` or `ws_token_env` so
Daedalus presents `Authorization: Bearer <token>` during the WebSocket
handshake. `status` includes both systemd state and a `GET /readyz` probe.
`doctor` adds managed/external diagnostics, auth validation, and persisted
Codex thread mappings from workflow scheduler state.

Then configure Daedalus for external mode:

```yaml
runtimes:
  codex:
    kind: codex-app-server
    mode: external
    endpoint: ws://127.0.0.1:4500
    healthcheck_path: /readyz
    ephemeral: false
    keep_alive: true
    ws_token_env: CODEX_APP_SERVER_TOKEN  # only if the listener requires auth
```

External mode checks `GET /readyz` before connecting. By default it keeps the
WebSocket transport warm for the lifetime of the runtime object
(`keep_alive: true`), so supervised services can reuse one initialized JSON-RPC
connection across turns. If the socket is closed or the app-server restarts,
the next turn reconnects and initializes a fresh connection. `ephemeral: false`
keeps Codex threads visible through app-server thread APIs. The default stdio
transport cannot be shared: Daedalus can only attach to an already-started
app-server when it exposes a socket transport. Setting `keep_alive: true` with
managed stdio mode is invalid and fails config loading.

It maps `thread/tokenUsage/updated` into Daedalus token totals and
`account/rateLimits/updated` into the latest rate-limit snapshot. When Codex
emits both `tokenUsage.last` and cumulative `tokenUsage.total`, Daedalus records
`last` as the per-turn delta so resumed threads do not double-count cumulative
totals. It rejects non-interactive approval requests so an unattended service
does not hang.
Bundled workflows persist work-item thread mappings in scheduler state
(`issue-runner`: `issue_id -> thread_id`; `change-delivery`:
`lane:<issue-number> -> thread_id`) and resume the existing Codex thread on
later ticks instead of starting a fresh thread.
In supervised service loops, cancellation is cooperative. `issue-runner`
requests cancellation when a running issue reaches a terminal tracker state.
`change-delivery` requests cancellation when the active lane disappears,
changes, the runtime lease is lost, or the service is interrupted. The Codex
adapter sends `turn/interrupt` for the active turn and records the cancellation
state in scheduler metadata.

Runtimes may expose lightweight diagnostics. The Codex app-server adapter
reports its mode, transport, `keep_alive` setting, endpoint, and whether a warm
client is currently open; workflows can include that payload in their status
surfaces without changing the shared runtime protocol.

## Adding a new runtime

1. Subclass nothing — just implement the Protocol shape.
2. Decorate with `@register("<your-kind>")` from `runtimes`.
3. Add the kind to `schema.yaml` so config validation accepts it.
4. Optionally implement `set_cancel_event()`, `set_progress_callback()`, and
   `interrupt_turn()` if the runtime supports cooperative turn cancellation.
5. Optionally implement `last_activity_ts()` for stall participation.

## Where this lives in code

- Protocol: `daedalus/runtimes/__init__.py`
- Stage dispatcher: `daedalus/runtimes/stages.py`
- Adapters: `daedalus/runtimes/{claude_cli,acpx_codex,hermes_agent,codex_app_server}.py`
- Workflow compatibility shims: `daedalus/workflows/change_delivery/runtimes/`
- Preflight: `daedalus/workflows/change_delivery/preflight.py`
