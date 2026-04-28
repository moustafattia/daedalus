# Symphony-Conformance Pass — Design

**Status:** Draft v1
**Date:** 2026-04-28
**References:** [openai/symphony SPEC.md](https://github.com/openai/symphony/blob/main/SPEC.md) §6.2, §6.3, §8.5, §10.4, §13.7

## 1. Goal

Adopt five facets of the Symphony service specification into Daedalus to align the operator-facing contract (configuration, observability, runtime liveness) with an emerging public standard, without surrendering Daedalus-specific design choices (durable SQLite ledger, GitHub Issues tracker, pluggable runtimes/reviewers/webhooks).

The five features are independent and ship as separate phased PRs. They share architectural infrastructure (`ConfigSnapshot`, daemon process) but do not depend on each other for correctness.

## 2. Non-Goals

- Continuation-turn semantics (Symphony §7.1) — touches the runtime contract; deferred to a separate spec.
- `github_graphql` first-class tool extension (Symphony §10.5 analog) — deferred.
- Replacing SQLite ledger with Symphony's in-memory-only model — explicit regression, not pursued.
- Adopting Symphony's `WORKFLOW.md` single-file front-matter format — Daedalus's split (`workflow.yaml` + `prompts/`) is intentional.
- Rewriting Linear-specific tracker semantics — Daedalus stays GitHub-native.

## 3. Architecture

The `daedalus@<workspace>.service` systemd unit's main process (today: `watch.py`'s tick loop) gains four in-process subsystems sharing one in-memory `ConfigSnapshot` reference. Each thread that reads `daedalus.db` opens its own SQLite connection (SQLite connections are not thread-safe), but reads are serialized by SQLite's WAL mode without coordination.

```
┌─────────────────────────── daedalus@<workspace>.service ───────────────────────────┐
│                                                                                    │
│  ConfigWatcher (mtime poll) ──▶ ConfigSnapshot (last-known-good) ◀── TickLoop      │
│                                                                          │         │
│                                                                          ▼         │
│                                                          per-tick:                 │
│                                                            - preflight_validate()  │
│                                                            - reconcile_stalls()    │
│                                                            - dispatch (existing)   │
│                                                                                    │
│  LivenessProbe ◀── Runtime.last_activity_ts()  (Protocol extension)                │
│                                                                                    │
│  StatusServer (threading.Thread, daemon=True)                                      │
│    - http.server.ThreadingHTTPServer                                               │
│    - reads daedalus.db + ConfigSnapshot                                            │
│    - GET / · GET /api/v1/state · GET /api/v1/<id> · POST /api/v1/refresh           │
│                                                                                    │
│  events_log_writer (renamed taxonomy, dual-read alias window)                      │
└────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Invariants

- **One process, one PID.** HTTP server, file watcher, stall reconciler, dispatch loop all share the same in-process state.
- **`ConfigSnapshot` is the only mutable shared state.** It holds the last-known-good parsed config and prompt templates. Readers (HTTP, tick loop, dispatch) read it lock-free; the writer (`ConfigWatcher`) swaps the reference atomically.
- **No new external dependencies.** `http.server`, `threading`, `pathlib.Path.stat().st_mtime`, `time.monotonic()` — all stdlib. Maintains Daedalus's dep posture.
- **HTTP server binds `127.0.0.1` by default.** Non-loopback bind requires explicit `server.bind` schema field.

## 4. Feature 1 — Hot-reload of `workflow.yaml`

Aligns with Symphony §6.2 (REQUIRED dynamic reload).

### 4.1 New module: `workflows/code_review/config_watcher.py`

```python
@dataclass(frozen=True)
class ConfigSnapshot:
    """Immutable parsed-config + prompt-template view. Atomic swap via reference."""
    config: dict           # validated workflow.yaml
    prompts: dict          # {tier: rendered_template}
    loaded_at: float       # monotonic clock
    source_mtime: float    # workflow.yaml st_mtime at parse time

class ConfigWatcher:
    def __init__(self, workflow_yaml_path: Path, snapshot_ref: AtomicRef[ConfigSnapshot]):
        self._path = workflow_yaml_path
        self._ref = snapshot_ref
        self._last_mtime = snapshot_ref.get().source_mtime

    def poll(self) -> None:
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return  # file vanished; keep last-known-good
        if mtime == self._last_mtime:
            return
        try:
            new_snapshot = parse_and_validate(self._path)
        except (ValidationError, ParseError) as e:
            emit_event(DAEDALUS_CONFIG_RELOAD_FAILED, {"error": str(e)})
            self._last_mtime = mtime  # don't retry the same broken bytes
            return
        self._ref.set(new_snapshot)
        self._last_mtime = mtime
        emit_event(DAEDALUS_CONFIG_RELOADED, {"loaded_at": new_snapshot.loaded_at})
```

`AtomicRef[T]` is a thin wrapper around `threading.Lock` with `get()` and `set()` methods, since `ConfigSnapshot` may be read by the HTTP thread concurrently with the tick-loop swap.

### 4.2 Semantics

- **Detection:** Poll `(st_mtime, st_size)` tuple on every tick (default 5s). Both fields together — mtime alone misses edits on filesystems with coarse mtime resolution or mtime-preserving copy tools (NFS / rsync `-t` / overlayfs). No `inotify` dependency.
- **Reload scope:** poll interval, concurrency limits (currently single-lane; will respect future caps), active/terminal label sets, runtime/reviewer/webhook configs, prompt templates for *future* runs. Live agent sessions are NOT restarted — Symphony §6.2 explicitly allows this.
- **Bad reload behavior:** keep `ConfigSnapshot` reference unchanged, emit `daedalus.config_reload_failed` event, log to stderr, never crash the daemon.
- **HTTP listener exclusion:** `server.port` and `server.bind` are excluded from hot reload. Listener changes require restart (matches Symphony §6.2 "MAY require restart for listener changes").
- **mtime-tied retry suppression:** a broken file's mtime is recorded so the same bytes are not re-parsed every tick. The next *change* to the file triggers a re-parse attempt.

### 4.3 Tests (`tests/test_config_watcher.py`)

- Edit `workflow.yaml` between two ticks → next tick uses new config; `daedalus.config_reloaded` event present.
- Inject syntactically invalid YAML → snapshot unchanged, `daedalus.config_reload_failed` emitted, daemon survives.
- Inject schema-valid but semantically broken (missing required field) → snapshot unchanged, failure event.
- mtime-tied retry suppression — broken file is not re-parsed every tick.
- File temporarily missing (atomic rename mid-poll) → snapshot unchanged, no failure event.

## 5. Feature 2 — Per-tick dispatch preflight validation

Aligns with Symphony §6.3 (per-tick preflight).

### 5.1 New module: `workflows/code_review/preflight.py`

```python
@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    error_code: str | None
    error_detail: str | None
    can_reconcile: bool  # always True; preflight never blocks reconciliation

def run_preflight(snapshot: ConfigSnapshot) -> PreflightResult:
    """Pure: snapshot → verdict. No side effects. Cheap (<1ms)."""
    # 1. workflow.yaml parsed (snapshot exists at all)
    # 2. Required fields present: tracker.kind, tracker.repo, runtime.kind, etc.
    # 3. Resolved $VAR_NAME tokens are non-empty (e.g. $GITHUB_TOKEN)
    # 4. runtime.command and reviewer config resolve to known plugin kinds
    # 5. workspace.root exists and is writable
```

### 5.2 Tick-loop integration

```python
def tick(snapshot: ConfigSnapshot, db: DaedalusDB) -> None:
    # ALWAYS reconcile first (Symphony §6.3, §8.1)
    reconcile_running_lanes(snapshot, db)
    reconcile_stalls(snapshot, db)  # Feature 5

    pre = run_preflight(snapshot)
    if not pre.ok:
        emit_event(DAEDALUS_DISPATCH_SKIPPED, {
            "code": pre.error_code,
            "detail": pre.error_detail,
        })
        return
    dispatch_eligible_lanes(snapshot, db)
```

### 5.3 Semantics

- Preflight runs **every tick**, not just startup. A broken `workflow.yaml` edit between ticks skips the next dispatch but does not kill the daemon and does not drop reconciliation.
- Reconciliation always runs first — even if preflight fails, a worker that needs to be terminated (e.g., issue closed in tracker, stall detected) still gets terminated.
- Startup validation calls the same `run_preflight()` and exits non-zero if it fails (preserves current "fail loud at startup" behavior). Once running, the same failure becomes a soft skip.

### 5.4 Error codes (fixed enum)

`missing_workflow_file` · `workflow_parse_error` · `workflow_front_matter_not_a_map` · `unsupported_runtime_kind` · `unsupported_reviewer_kind` · `missing_tracker_credentials` · `unsupported_tracker_kind` · `workspace_root_unwritable`

### 5.5 Tests (`tests/test_preflight.py`)

- Happy path → `ok=True`, tick dispatches.
- Missing required field → `daedalus.dispatch_skipped` emitted, no dispatch, reconciler still ran (assert reconcile call order).
- Error codes match the fixed enum.
- Startup-time call to `run_preflight()` exits non-zero on failure (regression on existing behavior).

## 6. Feature 3 — Optional HTTP status surface

Aligns with Symphony §13.7. Decision: JSON API + minimal server-rendered HTML page (option (b)).

### 6.1 New package: `workflows/code_review/server/`

```
workflows/code_review/server/
├── __init__.py        # public: start_server(snapshot_ref, db_path, port) -> ServerHandle
├── routes.py          # request dispatch
├── views.py           # state_view(), issue_view() — pure DB → dict
├── html.py            # render_dashboard(state_dict) -> str
└── refresh.py         # POST /refresh handler
```

### 6.2 Schema addition (`workflows/code_review/schema.yaml`)

```yaml
server:
  type: object
  additionalProperties: false
  properties:
    port:
      type: integer
      minimum: 0
      maximum: 65535
      description: "0 = ephemeral (tests). Omit/null = HTTP server disabled."
    bind:
      type: string
      default: "127.0.0.1"
      description: "Loopback by default. Non-loopback requires explicit override."
```

### 6.3 Endpoints

| Method | Path | Handler | Source |
|---|---|---|---|
| `GET` | `/` | `html.render_dashboard(state_view())` | server-rendered HTML, `<meta http-equiv="refresh" content="10">` |
| `GET` | `/api/v1/state` | `views.state_view()` | reads `daedalus.db` ledger + `ConfigSnapshot` |
| `GET` | `/api/v1/<identifier>` | `views.issue_view(identifier)` | per-lane DB row + recent events |
| `POST` | `/api/v1/refresh` | `refresh.queue_refresh()` | sets `threading.Event`; tick loop coalesces |
| `*` | other | `404` JSON `{"error": {"code": "not_found"}}` | |

### 6.4 `views.state_view()` shape

```json
{
  "generated_at": "2026-04-28T20:15:30Z",
  "counts": {"running": 1, "retrying": 0},
  "running": [
    {
      "issue_id": "...",
      "issue_identifier": "yoyopod#42",
      "state": "active-lane",
      "session_id": "thr-1-turn-3",
      "turn_count": 3,
      "last_event": "turn_completed",
      "started_at": "...",
      "last_event_at": "...",
      "tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    }
  ],
  "retrying": [],
  "totals": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "seconds_running": 0},
  "rate_limits": null
}
```

### 6.5 Lifecycle

- `start_server()` returns a `ServerHandle` with `.shutdown()` for clean teardown on SIGTERM.
- Spawns `ThreadingHTTPServer` in `threading.Thread(daemon=True)`.
- `port = 0` returns OS-assigned port via `handle.port` — used by tests.
- `bind = "127.0.0.1"` enforced unless schema explicitly carries non-loopback bind.
- Disabled by default: `server` key absent → no thread spawned, zero overhead.
- Read-only except for `/api/v1/refresh`, which only sets an in-process flag (no DB writes from the HTTP path).

### 6.6 HTML dashboard scope

- Single static-rendered page, table layout, `<meta http-equiv="refresh" content="10">`.
- Renders running lanes, retry queue, totals, last 20 events.
- No JS, no CSS framework — stdlib `html.escape` only.
- ~150 lines in `html.py`. Upgrade path to client-side polling is changing one number / adding one fetch.

### 6.7 Tests (`tests/test_status_server.py`)

- `port=0` → server binds, returns dict shape; assert JSON schema of `/api/v1/state`.
- `GET /api/v1/<unknown-identifier>` → 404 with JSON error envelope.
- `POST /api/v1/refresh` → 202, sets refresh flag; next tick observes it; coalesces N rapid POSTs into one.
- HTML page renders and contains expected lane identifiers (smoke test).
- `bind=0.0.0.0` requires explicit schema field (rejected without it via schema validation).
- Server thread cleanly shuts down on `handle.shutdown()`.
- `server` key absent → `start_server()` returns disabled handle, no thread spawned.

## 7. Feature 4 — Event vocabulary alignment

Aligns with Symphony §10.4. Decision: full rename + one-release alias window (option (a)).

### 7.1 New module: `workflows/code_review/event_taxonomy.py`

Single source of truth for canonical event names and the legacy alias map.

```python
# Symphony §10.4 session/turn-level events (renamed from current Daedalus names)
SESSION_STARTED       = "session_started"
TURN_COMPLETED        = "turn_completed"
TURN_FAILED           = "turn_failed"
TURN_CANCELLED        = "turn_cancelled"
TURN_INPUT_REQUIRED   = "turn_input_required"
NOTIFICATION          = "notification"
UNSUPPORTED_TOOL_CALL = "unsupported_tool_call"
MALFORMED             = "malformed"
STARTUP_FAILED        = "startup_failed"

# Daedalus-native events (no Symphony equivalent — keep prefixed)
DAEDALUS_LANE_CLAIMED         = "daedalus.lane_claimed"
DAEDALUS_LANE_RELEASED        = "daedalus.lane_released"
DAEDALUS_REPAIR_HANDOFF       = "daedalus.repair_handoff_dispatched"
DAEDALUS_REVIEW_LANDED        = "daedalus.review_landed"
DAEDALUS_VERDICT_PUBLISHED    = "daedalus.verdict_published"
DAEDALUS_CONFIG_RELOADED      = "daedalus.config_reloaded"
DAEDALUS_CONFIG_RELOAD_FAILED = "daedalus.config_reload_failed"
DAEDALUS_DISPATCH_SKIPPED     = "daedalus.dispatch_skipped"
DAEDALUS_STALL_DETECTED       = "daedalus.stall_detected"
DAEDALUS_STALL_TERMINATED     = "daedalus.stall_terminated"
DAEDALUS_REFRESH_REQUESTED    = "daedalus.refresh_requested"

# One-release alias window — readers accept legacy → canonical mapping.
# Concrete entries enumerated during implementation by grepping existing
# event-string call sites in runtime.py.
EVENT_ALIASES: dict[str, str] = {
    "claude_review_started":     SESSION_STARTED,
    "claude_review_completed":   TURN_COMPLETED,
    "claude_review_failed":      TURN_FAILED,
    "codex_handoff_dispatched":  DAEDALUS_REPAIR_HANDOFF,
    "internal_review_started":   SESSION_STARTED,
    "internal_review_completed": TURN_COMPLETED,
    # full enumeration completed during implementation
}

def canonicalize(event_type: str) -> str:
    """Readers call this. Idempotent for already-canonical names."""
    return EVENT_ALIASES.get(event_type, event_type)
```

### 7.2 Mechanics

- **Writers** (~15 call sites in `runtime.py`, all via `append_daedalus_event`): emit only canonical names. Refactor pass replaces literal strings with module constants.
- **Readers** (`status.py`, `watch.py`, `observability.py`, `views.py`, anything that branches on `event["type"]`): wrap reads in `canonicalize(event["type"])` before comparison. Old log files keep working.
- **Alias window:** one release. Removed in the next phase like `claudeModel` / `codexCloud` were.
- **No write-time dual emission** — strategy (a) chosen. Cleaner log, no double-counting in observability.

### 7.3 Namespace rule

Session/turn lifecycle events use Symphony's bare names (`session_started`, `turn_completed`, …). Daedalus-specific orchestration events live under the `daedalus.*` prefix (`daedalus.lane_claimed`, `daedalus.repair_handoff_dispatched`, …). The prefix prevents future Symphony additions from colliding with Daedalus-native events.

### 7.4 Tests (`tests/test_event_taxonomy.py`)

- Round-trip: write canonical event → reader sees canonical via `canonicalize`.
- Legacy: write a fixture with old name → `canonicalize` returns canonical.
- Every legacy name in `EVENT_ALIASES` resolves to a known canonical (table integrity).
- Every `append_daedalus_event` call site in `runtime.py` uses a constant from `event_taxonomy`, not a string literal (regression: AST-based grep test).
- `daedalus.*` prefix invariant — every Daedalus-native canonical starts with `daedalus.`.

### 7.5 Migration

No data migration. Existing `daedalus-events.jsonl` files contain old names; readers handle both. Operators can `cat daedalus-events.jsonl` and see canonical names from cutover forward.

## 8. Feature 5 — Stall detection

Aligns with Symphony §8.5 Part A. Decision: per-runtime liveness signal via Protocol extension (option (a)).

### 8.1 Runtime Protocol extension

```python
# workflows/code_review/runtimes/__init__.py — extend existing Protocol

class Runtime(Protocol):
    kind: ClassVar[str]
    def run_command(self, ctx: RuntimeContext) -> RuntimeResult: ...

    # NEW (optional method — runtimes that don't implement it opt out of stall detection)
    def last_activity_ts(self) -> float | None:
        """Monotonic timestamp of the most recent forward-progress signal
        from the running agent. None = method is defined but no signal has
        been produced yet (still in startup); the stall reconciler then
        uses worker `started_at_monotonic` as the baseline so a worker
        that hangs during startup still has a deadline.

        Runtimes that DO NOT IMPLEMENT this method (the attribute is
        absent on the instance) opt out of stall detection entirely —
        the reconciler skips them rather than falling back to
        `started_at_monotonic`. Returning None is a startup signal,
        not an opt-out signal."""
        ...
```

### 8.2 Per-runtime implementation

| Runtime | Signal source |
|---|---|
| `acpx-codex` | Update on every Codex app-server event received (turn_started, turn_completed, notification, …). |
| `claude-cli` | Update on every line read from subprocess stdout/stderr. |
| `hermes-agent` | Update on every callback fired by the in-process session runner. |

Each runtime stores `_last_activity = time.monotonic()` in instance state. Orchestrator reads via the Protocol method (no shared mutable state — runtime owns it).

### 8.3 New module: `workflows/code_review/stall.py`

```python
@dataclass(frozen=True)
class StallVerdict:
    issue_id: str
    elapsed_seconds: float
    threshold_seconds: float
    action: Literal["terminate", "warn", "noop"]

def reconcile_stalls(
    snapshot: ConfigSnapshot,
    running: Mapping[str, RunningEntry],
    now: float,
) -> list[StallVerdict]:
    """Pure function — caller acts on the verdicts (kills workers, queues retries)."""
    threshold_ms = snapshot.config.get("stall", {}).get("timeout_ms", 300_000)
    if threshold_ms <= 0:
        return []  # explicitly disabled
    threshold_s = threshold_ms / 1000
    out = []
    for issue_id, entry in running.items():
        rt = getattr(entry, "runtime", None)
        # Opt out: runtime doesn't implement last_activity_ts at all.
        if rt is None or not hasattr(rt, "last_activity_ts"):
            continue
        last = rt.last_activity_ts()
        # Defined but None = "still in startup, no signal yet" — fall back
        # to started_at so a hung-startup worker still has a deadline.
        baseline = last if last is not None else entry.started_at_monotonic
        elapsed = now - baseline
        if elapsed > threshold_s:
            out.append(StallVerdict(issue_id, elapsed, threshold_s, "terminate"))
    return out
```

### 8.4 Schema addition (`workflows/code_review/schema.yaml`)

```yaml
stall:
  type: object
  additionalProperties: false
  properties:
    timeout_ms:
      type: integer
      minimum: 0
      default: 300000
      description: "Worker terminated if runtime has shown no activity for this long. 0 = disabled."
```

### 8.5 Tick-loop integration

```python
def reconcile_stalls_and_act(snapshot, db, running, now):
    for verdict in reconcile_stalls(snapshot, running, now):
        emit_event(DAEDALUS_STALL_DETECTED, {
            "issue_id": verdict.issue_id,
            "elapsed_seconds": verdict.elapsed_seconds,
            "threshold_seconds": verdict.threshold_seconds,
        })
        terminate_worker(verdict.issue_id, reason="stall")
        emit_event(DAEDALUS_STALL_TERMINATED, {"issue_id": verdict.issue_id})
        queue_retry(db, verdict.issue_id, error="stall_timeout")
```

### 8.6 Semantics

- Threshold: `stall.timeout_ms`, default 5 min, `0` = disabled (Symphony's contract).
- Baseline: `last_activity_ts()` if any, else worker `started_at_monotonic` (so a worker that never produces a signal still has a deadline).
- Action: terminate worker subprocess + queue retry with exponential backoff (Daedalus's existing retry logic).
- Stall verdict computed **before** tracker state refresh in reconciliation — a stalled worker on a now-terminal issue still gets stall-terminated; cleaner ledger semantics than racing the two paths.

### 8.7 Tests (`tests/test_stall_detection.py`)

- No activity past threshold → `StallVerdict(action="terminate")`.
- Activity within threshold → no verdict.
- `timeout_ms=0` → empty list always (disabled).
- Worker never produced a signal → baseline is `started_at`, deadline still applies.
- Per-runtime test: each runtime's `last_activity_ts()` updates on its respective signal source.
- Stall + tracker-terminal race: stall verdict wins, both events emitted.
- Opt-out: a runtime instance without `last_activity_ts` attribute is skipped (no `StallVerdict` produced) regardless of elapsed time.

## 9. Phasing

Each feature ships as its own phased PR, mirroring the cadence of the recent A → D-6 refactor. Suggested order:

| Phase | Feature | Depends on |
|---|---|---|
| **S-1** | `ConfigSnapshot` + `AtomicRef` infrastructure (foundation) | — |
| **S-2** | Hot-reload (Feature 1) | S-1 |
| **S-3** | Per-tick preflight (Feature 2) | S-1, S-2 |
| **S-4** | Event vocabulary alignment (Feature 4) | — (independent) |
| **S-5** | Stall detection (Feature 5) | S-1 |
| **S-6** | HTTP status surface (Feature 3) | S-1, S-4 |

Phases can be parallelized across worktrees where dependencies allow. S-4 is fully independent.

## 10. Test/validation matrix

Aggregate across phases:

- All 591 currently-passing tests still pass (regression).
- New tests per feature (sections 4.3, 5.5, 6.7, 7.4, 8.7).
- Live YoYoPod workspace at `/home/radxa/.hermes/workflows/yoyopod` continues to operate — these features are additive and don't change existing semantics for an idle workspace.
- HTTP server smoke: `curl http://127.0.0.1:<port>/api/v1/state` returns valid JSON when enabled.
- Hot-reload smoke: edit a `workflow.yaml` field between ticks; observe `daedalus.config_reloaded` in `daedalus-events.jsonl`.

## 11. Operational notes

- HTTP listener changes (`server.port`, `server.bind`) require service restart — explicit operator action.
- Stall threshold `0` disables the feature — escape hatch for runtimes whose liveness signal is unreliable.
- Event alias window is one release; legacy names removed in the phase after S-4 lands (separate PR, follows the D-rename pattern).
- All five features default to OFF or backward-compatible behavior when their schema sections are absent — no operator action required to upgrade.
