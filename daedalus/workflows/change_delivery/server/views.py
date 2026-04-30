"""Pure DB → dict readers for the HTTP status surface.

These functions never write. They open a fresh ``sqlite3`` connection per
call (cheap, and avoids any shared-state hazards across the
``ThreadingHTTPServer`` worker threads). The events tail is read from the
JSONL events log on disk per request.

Shape conforms to Symphony §13.7 (spec §6.4):

- ``state_view`` returns a snapshot of running + retrying lanes plus a
  ``totals`` block. Daedalus does not currently track per-lane token
  counts, so token fields are populated as 0; rate_limits is ``None``.
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


def state_view(db_path: Path, events_log_path: Path) -> dict[str, Any]:
    """Snapshot view conforming to Symphony §13.7 / spec §6.4."""
    lanes = _query_active_lanes(db_path)
    events = _read_events_tail(events_log_path, _RECENT_EVENTS_LIMIT)
    running = [_lane_to_running_entry(lane, events) for lane in lanes]
    return {
        "generated_at": _now_iso(),
        "counts": {"running": len(running), "retrying": 0},
        "running": running,
        "retrying": [],
        "totals": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "seconds_running": 0,
        },
        "rate_limits": None,
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
) -> dict[str, Any] | None:
    """Per-lane view; ``None`` when the identifier matches no active lane."""
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
