from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .storage import append_jsonl


AuditPublisher = Callable[..., Any]
EventSink = Callable[[dict[str, Any]], Any]
Clock = Callable[[], str]


def make_audit_fn(
    *,
    audit_log_path: Path,
    now_iso: Clock,
    publisher: AuditPublisher | None = None,
    event_sink: EventSink | None = None,
) -> Callable[..., None]:
    """Build a JSONL audit writer with best-effort subscriber fanout."""

    def audit(action: str, summary: str, **extra: Any) -> None:
        event = {
            "at": now_iso(),
            "action": action,
            "summary": summary,
            **extra,
        }
        append_jsonl(audit_log_path, event)
        if event_sink is not None:
            try:
                event_sink(event)
            except Exception:
                # The durable audit file is already written; secondary sinks
                # must not break workflow execution.
                pass
        if publisher is None:
            return
        try:
            publisher(action=action, summary=summary, extra=dict(extra))
        except Exception:
            # Observability subscribers must never break workflow execution.
            pass

    return audit
