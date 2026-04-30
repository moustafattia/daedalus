# Runtimes

A **runtime** is the thing Daedalus shells out to when a turn happens. Daedalus owns leases, state, and dispatch; the runtime owns "how do I actually run an LLM turn against a worktree." Four are shipped today.

At the code level, these shared execution backends live under
`daedalus/runtimes/`. The operator-facing contract also uses the `runtimes:`
config block because workflows bind named runtime profiles to workflow roles.

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

## Adapter shape comparison

|| | `claude-cli` | `acpx-codex` | `hermes-agent` | `codex-app-server` |
|---|---|---|---|---|---|
| Persistent session | ❌ one-shot | ✅ resumable | ❌ one-shot | ✅ resumable Codex thread |
| `ensure_session` | no-op | `acpx codex sessions ensure` | no-op | no-op |
| `run_prompt` | `claude --print …` | `acpx codex prompt -s <name>` | requires `command:` override | JSON-RPC over stdio to `codex app-server` |
| `assess_health` | always healthy | freshness + grace window | always healthy | always healthy |
| `close_session` | no-op | `acpx codex sessions close` | no-op | no-op |
| Records `last_activity_ts` | yes (before + after `_run`) | yes | yes | yes |

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

agents:
  coder:
    t1: { name: claude-coder, model: opus, runtime: coder-runtime }
  internal-reviewer:
    name: codex-reviewer
    model: gpt-5
    runtime: reviewer-runtime
```

The preflight pass walks `runtimes.<name>.kind` and `agents.external-reviewer.kind` to confirm every referenced runtime resolves to a registered adapter before a tick dispatches.

### `hermes-agent` runtime

The `hermes-agent` runtime delegates turns to a local Hermes agent process. It is **one-shot** (no persistent session) and requires a `command:` override in `WORKFLOW.md` because the exact invocation depends on the agent's entry point.

```yaml
runtimes:
  my-agent-runtime:
    kind: hermes-agent
    command: ["python3", "-m", "my_agent", "--workflow-root", "{{workflow_root}}"]
    timeout-seconds: 1200
```

Because it is one-shot, `assess_health` always returns healthy and `last_activity_ts` records the subprocess start/end timestamps.

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
`account/rateLimits/updated` into the latest rate-limit snapshot. It rejects
non-interactive approval requests so an unattended service does not hang.
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
- Adapters: `daedalus/runtimes/{claude_cli,acpx_codex,hermes_agent,codex_app_server}.py`
- Workflow compatibility shims: `daedalus/workflows/change_delivery/runtimes/`
- Preflight: `daedalus/workflows/change_delivery/preflight.py`
