"""Server-rendered HTML dashboard for the optional HTTP status surface.

Single static page with ``<meta http-equiv="refresh" content="10">``.
Stdlib ``html.escape`` only — no JS, no CSS framework. ~150 LOC budget.
The upgrade path to client-side polling is changing the meta tag and
adding one fetch call.
"""
from __future__ import annotations

from html import escape
from typing import Any


_PAGE_HEAD = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="10">
  <title>Daedalus Status</title>
  <style>
    body {{ font-family: -apple-system, system-ui, sans-serif; margin: 1.5em; color: #222; }}
    h1 {{ font-size: 1.4em; margin-bottom: 0.2em; }}
    h2 {{ font-size: 1.1em; margin-top: 1.4em; }}
    .meta {{ color: #777; font-size: 0.9em; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 0.5em; }}
    th, td {{ border-bottom: 1px solid #eee; padding: 6px 8px; text-align: left; vertical-align: top; }}
    th {{ background: #fafafa; font-weight: 600; }}
    .empty {{ color: #999; font-style: italic; }}
    code {{ font-family: ui-monospace, Menlo, Consolas, monospace; }}
  </style>
</head>
<body>
"""

_PAGE_FOOT = """\
</body>
</html>
"""


def _row(values: list[Any]) -> str:
    cells = "".join(f"<td>{escape(str(v) if v is not None else '—')}</td>" for v in values)
    return f"<tr>{cells}</tr>"


def _running_table(running: list[dict[str, Any]]) -> str:
    if not running:
        return '<p class="empty">No running lanes.</p>'
    headers = [
        "Issue", "State", "Session", "Last event", "Started", "Last event at",
    ]
    head = "<tr>" + "".join(f"<th>{escape(h)}</th>" for h in headers) + "</tr>"
    body = "".join(
        _row(
            [
                entry.get("issue_identifier"),
                entry.get("state"),
                entry.get("session_id"),
                entry.get("last_event"),
                entry.get("started_at"),
                entry.get("last_event_at"),
            ]
        )
        for entry in running
    )
    return f"<table>{head}{body}</table>"


def _retrying_table(retrying: list[dict[str, Any]]) -> str:
    if not retrying:
        return '<p class="empty">No retrying lanes.</p>'
    headers = ["Issue", "State", "Last event"]
    head = "<tr>" + "".join(f"<th>{escape(h)}</th>" for h in headers) + "</tr>"
    body = "".join(
        _row(
            [
                entry.get("issue_identifier"),
                entry.get("state"),
                entry.get("last_event"),
            ]
        )
        for entry in retrying
    )
    return f"<table>{head}{body}</table>"


def _events_table(events: list[dict[str, Any]]) -> str:
    if not events:
        return '<p class="empty">No recent events.</p>'
    headers = ["At", "Kind", "Lane", "Detail"]
    head = "<tr>" + "".join(f"<th>{escape(h)}</th>" for h in headers) + "</tr>"
    body = ""
    for evt in events:
        payload = evt.get("payload") if isinstance(evt.get("payload"), dict) else {}
        detail_source = payload if payload else evt
        detail_keys = {
            k: v
            for k, v in detail_source.items()
            if k not in {"at", "created_at", "kind", "event_type", "event", "action", "lane_id", "issue_number"}
        }
        detail_str = ", ".join(f"{k}={v}" for k, v in detail_keys.items())
        body += _row(
            [
                evt.get("at") or evt.get("created_at"),
                evt.get("kind") or evt.get("event_type") or evt.get("event") or evt.get("action"),
                evt.get("work_id") or evt.get("lane_id") or payload.get("lane_id") or payload.get("issue_number"),
                detail_str,
            ]
        )
    return f"<table>{head}{body}</table>"


def _totals_block(totals: dict[str, Any]) -> str:
    items = ", ".join(
        f"<code>{escape(k)}</code>={escape(str(v))}" for k, v in totals.items()
    )
    return f"<p>{items}</p>"


def render_dashboard(state: dict[str, Any]) -> str:
    """Render a static HTML dashboard from a ``state_view`` dict."""
    counts = state.get("counts") or {}
    running = state.get("running") or []
    retrying = state.get("retrying") or []
    events = state.get("recent_events") or []
    totals = state.get("codex_totals") or {}
    rate_limits = state.get("rate_limits")

    parts: list[str] = [_PAGE_HEAD]
    parts.append("<h1>Daedalus Status</h1>")
    parts.append(
        f'<p class="meta">Generated at {escape(str(state.get("generated_at") or "—"))} · '
        f'running={escape(str(counts.get("running", 0)))}, '
        f'retrying={escape(str(counts.get("retrying", 0)))}</p>'
    )

    parts.append("<h2>Running</h2>")
    parts.append(_running_table(running))

    parts.append("<h2>Retrying</h2>")
    parts.append(_retrying_table(retrying))

    parts.append("<h2>Totals</h2>")
    parts.append(_totals_block(totals))
    if rate_limits:
        parts.append(f"<p>Rate limits: <code>{escape(str(rate_limits))}</code></p>")
    else:
        parts.append('<p class="empty">No rate-limit state.</p>')

    parts.append("<h2>Recent events</h2>")
    parts.append(_events_table(events))

    parts.append(_PAGE_FOOT)
    return "".join(parts)
