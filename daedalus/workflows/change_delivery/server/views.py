"""Pure DB → dict readers for the HTTP status surface.

These functions never write. They open a fresh ``sqlite3`` connection per
call (cheap, and avoids any shared-state hazards across the
``ThreadingHTTPServer`` worker threads). The events tail is read from the
JSONL events log on disk per request.

Shape conforms to Symphony §13.7 (spec §6.4):

- ``state_view`` returns a snapshot of running + retrying work plus a
  ``codex_totals`` block. `change-delivery` keeps the legacy lane-backed model;
  `issue-runner` projects from scheduler/status JSON files.
- ``issue_view`` returns the per-lane shape, or ``None`` if the
  identifier is unknown.

The functions tolerate a missing DB or events log and return a
well-formed empty shape rather than raising.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workflows.contract import WorkflowContractError, load_workflow_contract

# Lane statuses the spec considers "active" (running). Anything else
# (merged / closed / archived) is omitted from the running list. The
# active set mirrors watch_sources.active_lanes for consistency.
_TERMINAL_LANE_STATUSES = {"merged", "closed", "archived"}

# Event tail size for the dashboard view.
_RECENT_EVENTS_LIMIT = 20


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_events_tail(events_log_path: Path, limit: int) -> list[dict[str, Any]]:
    """Return up to ``limit`` most recent JSONL events, newest first.

    Codex P2 on PR #22: a previous implementation called ``readlines()``
    which loads the entire file before truncating. Since this is called
    on every HTTP request, request cost grew with total log size — a
    long-lived ``daedalus-events.jsonl`` caused avoidable latency and
    memory spikes. Now reads from the END via seek + chunked reverse
    scan, so cost is bounded by ``limit`` (plus average line length)
    regardless of total file size.
    """
    if not events_log_path.exists():
        return []
    try:
        size = events_log_path.stat().st_size
    except OSError:
        return []
    if size == 0:
        return []
    # Read 8 KiB chunks from the tail until we've collected ``limit`` newlines
    # or hit BOF. A line is at most one parsed event; non-JSON / empty lines
    # don't count toward limit so they're ignored when assembling the result.
    chunk_size = 8192
    collected: list[bytes] = []
    pending = b""
    pos = size
    found_lines = 0
    try:
        with open(events_log_path, "rb") as fh:
            while pos > 0 and found_lines <= limit:
                read_size = min(chunk_size, pos)
                pos -= read_size
                fh.seek(pos)
                chunk = fh.read(read_size)
                pending = chunk + pending
                # Split on \n; everything except the very first slice (which
                # may be the start of an unfinished line) is a complete line.
                # When pos reaches 0 the very first slice is also a complete
                # line (no preceding bytes can extend it).
                parts = pending.split(b"\n")
                # Keep the first chunk as "potentially incomplete" until we
                # read more from earlier in the file (pos > 0).
                if pos > 0:
                    pending = parts[0]
                    complete = parts[1:]
                else:
                    pending = b""
                    complete = parts
                # complete is in file-order; we want newest first. Iterate in
                # reverse so we collect the latest lines first.
                for line in reversed(complete):
                    if not line:
                        continue
                    collected.append(line)
                    found_lines += 1
                    if found_lines >= limit:
                        break
    except OSError:
        return []

    out: list[dict[str, Any]] = []
    for raw in collected[:limit]:
        try:
            out.append(json.loads(raw.decode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return out  # already newest first


def _query_active_lanes(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return []
    try:
        cur = conn.execute(
            """
            SELECT lane_id, issue_number, issue_url, issue_title,
                   workflow_state, lane_status,
                   active_actor_id, current_action_id,
                   created_at, updated_at, last_meaningful_progress_at,
                   last_meaningful_progress_kind
              FROM lanes
             WHERE lane_status NOT IN (?, ?, ?)
             ORDER BY created_at ASC
            """,
            tuple(sorted(_TERMINAL_LANE_STATUSES)),
        )
        rows = cur.fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "lane_id": row[0],
                "issue_number": row[1],
                "issue_url": row[2],
                "issue_title": row[3],
                "workflow_state": row[4],
                "lane_status": row[5],
                "active_actor_id": row[6],
                "current_action_id": row[7],
                "created_at": row[8],
                "updated_at": row[9],
                "last_meaningful_progress_at": row[10],
                "last_meaningful_progress_kind": row[11],
            }
        )
    return out


def _zero_tokens() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _load_optional_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _resolve_issue_runner_storage_path(workflow_root: Path, key: str, default: str) -> Path | None:
    try:
        contract = load_workflow_contract(workflow_root)
    except (FileNotFoundError, WorkflowContractError, OSError):
        return None
    storage_cfg = contract.config.get("storage") or {}
    raw = str(storage_cfg.get(key) or default).strip()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (workflow_root / path).resolve()
    return path


def _storage_path(workflow_root: Path | None, key: str, default: str) -> Path | None:
    if workflow_root is None:
        return None
    return _resolve_issue_runner_storage_path(Path(workflow_root), key, default)


def _workflow_name(workflow_root: Path | None) -> str | None:
    if workflow_root is None:
        return None
    try:
        return str(load_workflow_contract(workflow_root).config.get("workflow") or "").strip() or None
    except (FileNotFoundError, WorkflowContractError, OSError):
        return None


def _epoch_to_iso(value: Any) -> str | None:
    try:
        epoch = float(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _identifier_for_lane(lane: dict[str, Any]) -> str:
    """Build a stable issue_identifier string for a lane row.

    Daedalus lane rows already encode ``issue_number``; the identifier
    is rendered as ``#<n>`` so it can be substituted directly into URLs
    like ``/api/v1/#42``. The lane_id is also exposed, but the friendlier
    ``#<n>`` form is what humans use.
    """
    issue_number = lane.get("issue_number")
    if issue_number is not None:
        return f"#{issue_number}"
    return str(lane.get("lane_id") or "")


def _lane_to_running_entry(lane: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    last_event = next(
        (
            evt
            for evt in events
            if evt.get("lane_id") == lane.get("lane_id")
            or evt.get("issue_number") == lane.get("issue_number")
        ),
        None,
    )
    return {
        "issue_id": lane.get("lane_id"),
        "issue_identifier": _identifier_for_lane(lane),
        "state": lane.get("workflow_state"),
        "session_id": lane.get("active_actor_id"),
        "turn_count": 0,
        "last_event": (last_event or {}).get("kind") or lane.get("last_meaningful_progress_kind"),
        "started_at": lane.get("created_at"),
        "last_event_at": (last_event or {}).get("at")
        or lane.get("last_meaningful_progress_at")
        or lane.get("updated_at"),
        "tokens": _zero_tokens(),
    }


def _issue_runner_recent_events(
    workflow_root: Path,
    *,
    events_log_path: Path,
    audit_log_path: Path | None,
) -> list[dict[str, Any]]:
    merged = [{**event, "source": "daedalus"} for event in _read_events_tail(events_log_path, _RECENT_EVENTS_LIMIT)]
    if audit_log_path is not None:
        merged.extend({**event, "source": "workflow"} for event in _read_events_tail(audit_log_path, _RECENT_EVENTS_LIMIT))
    merged.sort(key=lambda event: event.get("at") or "", reverse=True)
    return merged[:_RECENT_EVENTS_LIMIT]


def _issue_runner_running_entry(row: dict[str, Any]) -> dict[str, Any]:
    started_at = _epoch_to_iso(row.get("started_at_epoch"))
    return {
        "issue_id": row.get("issue_id"),
        "issue_identifier": row.get("identifier") or row.get("issue_id"),
        "state": row.get("state") or "running",
        "session_id": None,
        "turn_count": 0,
        "last_event": "running",
        "started_at": started_at,
        "last_event_at": started_at,
        "tokens": _zero_tokens(),
    }


def _issue_runner_retry_entry(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "issue_id": row.get("issue_id"),
        "issue_identifier": row.get("identifier") or row.get("issue_id"),
        "state": "retrying",
        "session_id": None,
        "turn_count": 0,
        "last_event": "retry_queued",
        "started_at": None,
        "last_event_at": _epoch_to_iso(row.get("due_at_epoch")),
        "tokens": _zero_tokens(),
        "error": row.get("error"),
        "due_in_ms": row.get("due_in_ms"),
    }


def _issue_runner_state_view(workflow_root: Path, events_log_path: Path) -> dict[str, Any]:
    status_path = _resolve_issue_runner_storage_path(workflow_root, "status", "memory/workflow-status.json")
    scheduler_path = _resolve_issue_runner_storage_path(workflow_root, "scheduler", "memory/workflow-scheduler.json")
    audit_log_path = _resolve_issue_runner_storage_path(workflow_root, "audit-log", "memory/workflow-audit.jsonl")
    status_payload = _load_optional_json(status_path) or {}
    scheduler_payload = _load_optional_json(scheduler_path) or {}
    running_rows = [
        _issue_runner_running_entry(row)
        for row in (scheduler_payload.get("running") or [])
        if isinstance(row, dict)
    ]
    retry_rows = [
        _issue_runner_retry_entry(row)
        for row in (scheduler_payload.get("retry_queue") or scheduler_payload.get("retryQueue") or [])
        if isinstance(row, dict)
    ]
    codex_totals = dict(scheduler_payload.get("codex_totals") or scheduler_payload.get("codexTotals") or {})
    seconds_running = sum(int((row.get("running_for_ms") or 0)) for row in (scheduler_payload.get("running") or []) if isinstance(row, dict)) // 1000
    recent_events = _issue_runner_recent_events(
        workflow_root,
        events_log_path=events_log_path,
        audit_log_path=audit_log_path,
    )
    rate_limits = codex_totals.pop("rate_limits", None)
    totals = {
        "input_tokens": int(codex_totals.get("input_tokens") or 0),
        "output_tokens": int(codex_totals.get("output_tokens") or 0),
        "total_tokens": int(codex_totals.get("total_tokens") or 0),
        "seconds_running": seconds_running,
    }
    return {
        "generated_at": scheduler_payload.get("updatedAt") or ((status_payload.get("lastRun") or {}).get("updatedAt")) or _now_iso(),
        "counts": {"running": len(running_rows), "retrying": len(retry_rows)},
        "running": running_rows,
        "retrying": retry_rows,
        "codex_totals": totals,
        "rate_limits": rate_limits,
        "recent_events": recent_events,
    }


def _issue_runner_issue_view(
    workflow_root: Path,
    events_log_path: Path,
    identifier: str,
) -> dict[str, Any] | None:
    state = _issue_runner_state_view(workflow_root, events_log_path)
    def _matches(entry: dict[str, Any]) -> bool:
        if entry.get("issue_id") == identifier:
            return True
        if entry.get("issue_identifier") == identifier:
            return True
        return str(entry.get("issue_identifier") or "").lstrip("#") == identifier.lstrip("#")

    entry = next((item for item in state.get("running") or [] if _matches(item)), None)
    if entry is None:
        entry = next((item for item in state.get("retrying") or [] if _matches(item)), None)
    if entry is None:
        return None
    issue_events = [
        event
        for event in (state.get("recent_events") or [])
        if event.get("issue_id") == entry.get("issue_id")
        or event.get("identifier") == entry.get("issue_identifier")
        or str(event.get("issue_id") or "").lstrip("#") == str(entry.get("issue_id") or "").lstrip("#")
    ]
    return {**entry, "recent_events": issue_events}


def _codex_turn_entries(scheduler: dict[str, Any]) -> list[dict[str, Any]]:
    entries = []
    for issue_id, raw_entry in (scheduler.get("codex_threads") or scheduler.get("codexThreads") or {}).items():
        if not isinstance(raw_entry, dict):
            continue
        issue_number = raw_entry.get("issue_number") or raw_entry.get("issueNumber")
        entries.append(
            {
                "issue_id": raw_entry.get("issue_id") or issue_id,
                "issue_number": issue_number,
                "issue_identifier": raw_entry.get("identifier") or (f"#{issue_number}" if issue_number else issue_id),
                "session_name": raw_entry.get("session_name") or raw_entry.get("sessionName"),
                "runtime_name": raw_entry.get("runtime_name") or raw_entry.get("runtimeName"),
                "runtime_kind": raw_entry.get("runtime_kind") or raw_entry.get("runtimeKind"),
                "thread_id": raw_entry.get("thread_id") or raw_entry.get("threadId"),
                "turn_id": raw_entry.get("turn_id") or raw_entry.get("turnId"),
                "status": raw_entry.get("status"),
                "cancel_requested": bool(raw_entry.get("cancel_requested") or raw_entry.get("cancelRequested") or False),
                "cancel_reason": raw_entry.get("cancel_reason") or raw_entry.get("cancelReason"),
                "updated_at": raw_entry.get("updated_at") or raw_entry.get("updatedAt"),
            }
        )
    return sorted(entries, key=lambda item: str(item.get("issue_id") or ""))


def state_view(db_path: Path, events_log_path: Path, workflow_root: Path | None = None) -> dict[str, Any]:
    """Snapshot view conforming to Symphony §13.7 / spec §6.4."""
    if _workflow_name(workflow_root) == "issue-runner":
        return _issue_runner_state_view(Path(workflow_root), events_log_path)
    lanes = _query_active_lanes(db_path)
    events = _read_events_tail(events_log_path, _RECENT_EVENTS_LIMIT)
    running = [_lane_to_running_entry(lane, events) for lane in lanes]
    scheduler = _load_optional_json(_storage_path(workflow_root, "scheduler", "memory/workflow-scheduler.json")) or {}
    codex_totals = dict(scheduler.get("codex_totals") or scheduler.get("codexTotals") or {})
    rate_limits = codex_totals.pop("rate_limits", None)
    codex_turns = _codex_turn_entries(scheduler)
    return {
        "generated_at": _now_iso(),
        "counts": {"running": len(running), "retrying": 0},
        "running": running,
        "retrying": [],
        "codex_turns": codex_turns,
        "codex_turn_counts": {
            "running": len([entry for entry in codex_turns if entry.get("status") == "running"]),
            "canceling": len([entry for entry in codex_turns if entry.get("status") == "canceling"]),
        },
        "codex_totals": {
            "input_tokens": int(codex_totals.get("input_tokens") or 0),
            "output_tokens": int(codex_totals.get("output_tokens") or 0),
            "total_tokens": int(codex_totals.get("total_tokens") or 0),
            "seconds_running": 0,
        },
        "rate_limits": rate_limits,
        "recent_events": events,
    }


def _find_lane_by_identifier(
    lanes: list[dict[str, Any]], identifier: str
) -> dict[str, Any] | None:
    if not identifier:
        return None
    # Accept either lane_id or "#<n>" or bare "<n>".
    digits = identifier.lstrip("#")
    issue_number: int | None = None
    if digits.isdigit():
        issue_number = int(digits)
    for lane in lanes:
        if lane.get("lane_id") == identifier:
            return lane
        if issue_number is not None and lane.get("issue_number") == issue_number:
            return lane
    return None


def issue_view(
    db_path: Path,
    events_log_path: Path,
    identifier: str,
    workflow_root: Path | None = None,
) -> dict[str, Any] | None:
    """Per-lane view; ``None`` when the identifier matches no active lane."""
    if _workflow_name(workflow_root) == "issue-runner":
        return _issue_runner_issue_view(Path(workflow_root), events_log_path, identifier)
    lanes = _query_active_lanes(db_path)
    lane = _find_lane_by_identifier(lanes, identifier)
    if lane is None:
        return None
    events = _read_events_tail(events_log_path, _RECENT_EVENTS_LIMIT)
    lane_events = [
        evt
        for evt in events
        if evt.get("lane_id") == lane.get("lane_id")
        or evt.get("issue_number") == lane.get("issue_number")
    ]
    entry = _lane_to_running_entry(lane, events)
    entry["recent_events"] = lane_events
    return entry
