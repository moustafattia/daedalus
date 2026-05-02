# Sessions

A **session** is the runtime's handle to a persistent or one-shot actor process. Daedalus tracks sessions per work item so it knows which actor owns which worktree, whether the session is still alive, and when it last did something useful.

---

## Session model

Sessions are owned by the **runtime adapter**, not by Daedalus directly. Daedalus asks the runtime: "do you have a session for lane X?" and the runtime answers yes/no/healthy/stale.

### Session lifecycle

```mermaid
stateDiagram-v2
    [*] --> created: ensure_session
    created --> running: first prompt dispatched
    running --> idle: prompt completed
    idle --> running: next prompt dispatched
    running --> terminated: stall timeout
    idle --> terminated: idle timeout (acpx-codex only)
    terminated --> [*]
```

### Session properties

| Property | Meaning |
|---|---|
| `session_name` | Human-readable name, e.g. `coder-claude-1`. |
| `session_id` | Runtime-specific handle. For `acpx-codex`: the Codex session UUID. For `codex-app-server`: the Codex `thread_id`. For one-shot runtimes: usually `None`. |
| `worktree` | Path to the lane's workspace clone. |
| `model` | Model string used for this session. |
| `resume_session_id` | If restarting, the old session ID to resume from. |

---

## Runtime-specific session behavior

### `claude-cli` (one-shot)

- No persistent session.
- `ensure_session` is a no-op.
- `run_prompt` spawns `claude --print …` as a subprocess.
- `assess_health` always returns healthy.
- `close_session` is a no-op.

### `acpx-codex` (resumable)

- Persistent session across multiple turns.
- `ensure_session` calls `acpx codex sessions ensure`.
- `run_prompt` calls `acpx codex prompt -s <name>`.
- `assess_health` checks session freshness against `session-idle-freshness-seconds` and `session-idle-grace-seconds`.
- `close_session` calls `acpx codex sessions close`.
- `last_activity_ts` returns the most recent prompt start or completion time.

### `hermes-agent` (one-shot)

- Built-in final mode uses `hermes -z`.
- Built-in chat mode uses `hermes chat --quiet -q` and can pass `--resume` when the workflow has a session id.
- Custom `command:` overrides can write structured metadata to `{result_path}`.
- `assess_health` always returns healthy.
- `last_activity_ts` records subprocess start/end timestamps.

### `codex-app-server` (resumable thread)

- Persistent Codex thread across turns when `ephemeral: false`.
- Managed stdio mode starts `codex app-server` for the run; external mode connects to a long-running WebSocket listener.
- Workflows persist mappings so later ticks call `thread/resume` instead of `thread/start`.
- `issue-runner` stores `issue_id -> thread_id`; `change-delivery` stores `lane:<issue-number> -> thread_id`.
- Token totals and latest rate-limit payloads are persisted in scheduler state.
- Cooperative cancellation sends `turn/interrupt` for the active turn when the service stops or the work item becomes terminal.

---

## Session health

The `assess_health` protocol returns:

```python
class SessionHealth:
    status: "healthy" | "stale" | "unknown"
    reason: str | None
    last_activity_ts: float | None
```

| Status | Meaning | Action |
|---|---|---|
| `healthy` | Session is responsive and recent. | Continue dispatching. |
| `stale` | Session hasn't been active longer than the freshness threshold. | Emit `daedalus.stall_detected`, terminate, retry. |
| `unknown` | Runtime doesn't implement health checks. | Skip stall detection for this session. |

---

## Session naming convention

Sessions are named per lane and role:

```
<role>-<backend>-<lane_short_id>
```

Examples:
- `coder-claude-220` — Coder session for lane 220
- `reviewer-codex-220` — External reviewer session for lane 220
- `issue-runner-42` — Generic issue runner session for issue 42

The short id is usually the issue number (or a hash if there is no issue number). This makes session names human-readable and unique.

---

## SQL debugging

### Show actor sessions for a lane

```sql
select actor_id, backend_identity, runtime_status, session_action_recommendation, last_used_at, can_continue, can_nudge
from lane_actors
where lane_id='lane:220';
```

### Find stale sessions

```sql
select lane_id, actor_id, last_used_at
from lane_actors
where can_continue = false
   or (can_nudge = true and datetime(last_used_at, '+15 minutes') < datetime('now'));
```

---

## Where this lives in code

- Session protocol: `daedalus/workflows/change_delivery/sessions.py`
- Shared runtime adapters: `daedalus/runtimes/{claude_cli,acpx_codex,hermes_agent,codex_app_server}.py`
- Workflow compatibility shims: `daedalus/workflows/change_delivery/runtimes/`
- Health checks: `daedalus/workflows/change_delivery/health.py`
- Stall detection: `daedalus/workflows/shared/stall.py`, `daedalus/workflows/change_delivery/stall.py`
- Tests: `tests/test_workflows_change_delivery_sessions.py`, `tests/test_workflows_change_delivery_session_runtime.py`
