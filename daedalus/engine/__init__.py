"""Shared Daedalus engine primitives.

Workflow packages own lifecycle policy. This package owns reusable runtime
mechanics: durable file IO, audit writes, scheduler snapshots, and SQLite setup.
"""

from .audit import make_audit_fn
from .driver import WorkflowDriver
from .lifecycle import (
    clear_work_entries,
    mark_running_work,
    recover_running_as_retry,
    retry_delay,
    schedule_retry_entry,
)
from .scheduler import (
    RestoredSchedulerState,
    build_scheduler_payload,
    codex_threads_snapshot,
    restore_scheduler_state,
    retry_due_at,
    retry_queue_snapshot,
    running_snapshot,
)
from .sqlite import connect_daedalus_db
from .storage import append_jsonl, load_optional_json, write_json_atomic, write_text_atomic
from .work_items import (
    RetryEntry,
    RunningWork,
    WorkItemRef,
    WorkResult,
    work_item_from_change_delivery_lane,
    work_item_from_issue,
)

__all__ = [
    "RestoredSchedulerState",
    "WorkflowDriver",
    "RetryEntry",
    "RunningWork",
    "WorkItemRef",
    "WorkResult",
    "append_jsonl",
    "build_scheduler_payload",
    "clear_work_entries",
    "codex_threads_snapshot",
    "connect_daedalus_db",
    "load_optional_json",
    "mark_running_work",
    "make_audit_fn",
    "recover_running_as_retry",
    "restore_scheduler_state",
    "retry_delay",
    "retry_due_at",
    "retry_queue_snapshot",
    "running_snapshot",
    "schedule_retry_entry",
    "work_item_from_change_delivery_lane",
    "work_item_from_issue",
    "write_json_atomic",
    "write_text_atomic",
]
