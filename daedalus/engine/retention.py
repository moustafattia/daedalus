from __future__ import annotations

from typing import Any


SECONDS_PER_DAY = 86400


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_event_retention(value: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize WORKFLOW.md event-retention config.

    Accepts either the full ``retention:`` block or its nested ``events:`` block.
    """

    raw = value if isinstance(value, dict) else {}
    events = raw.get("events") if isinstance(raw.get("events"), dict) else raw
    max_age_days = _number(events.get("max-age-days") if isinstance(events, dict) else None)
    if max_age_days is None and isinstance(events, dict):
        max_age_days = _number(events.get("max_age_days"))
    max_age_seconds = _number(events.get("max-age-seconds") if isinstance(events, dict) else None)
    if max_age_seconds is None and isinstance(events, dict):
        max_age_seconds = _number(events.get("max_age_seconds"))
    if max_age_seconds is None and max_age_days is not None:
        max_age_seconds = max_age_days * SECONDS_PER_DAY
    max_rows = _integer(events.get("max-rows") if isinstance(events, dict) else None)
    if max_rows is None and isinstance(events, dict):
        max_rows = _integer(events.get("max_rows"))
    configured = max_age_seconds is not None or max_rows is not None
    return {
        "configured": configured,
        "max_age_days": max_age_days,
        "max_age_seconds": max_age_seconds,
        "max_rows": max_rows,
    }
