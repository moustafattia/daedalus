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
from .leases import (
    acquire_engine_lease,
    init_engine_leases,
    read_engine_lease,
    release_engine_lease,
)
from .retention import normalize_event_retention
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
from .state import (
    append_engine_event_to_connection,
    engine_event_stats_from_connection,
    engine_events_from_connection,
    engine_events_for_run_from_connection,
    engine_run_from_connection,
    engine_state_tables_exist,
    finish_engine_run_to_connection,
    init_engine_state,
    latest_engine_runs_from_connection,
    load_engine_scheduler_state,
    prune_engine_events_to_connection,
    read_engine_event_stats,
    read_engine_events,
    read_engine_events_for_run,
    read_engine_run,
    read_engine_runs,
    read_engine_scheduler_state,
    save_engine_scheduler_state,
    save_engine_scheduler_state_to_connection,
    start_engine_run_to_connection,
)
from .store import EngineStore
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
    "EngineStore",
    "RetryEntry",
    "RunningWork",
    "WorkItemRef",
    "WorkResult",
    "acquire_engine_lease",
    "append_engine_event_to_connection",
    "append_jsonl",
    "build_scheduler_payload",
    "clear_work_entries",
    "codex_threads_snapshot",
    "connect_daedalus_db",
    "engine_event_stats_from_connection",
    "engine_events_from_connection",
    "engine_events_for_run_from_connection",
    "engine_run_from_connection",
    "engine_state_tables_exist",
    "finish_engine_run_to_connection",
    "init_engine_state",
    "init_engine_leases",
    "latest_engine_runs_from_connection",
    "load_engine_scheduler_state",
    "load_optional_json",
    "mark_running_work",
    "make_audit_fn",
    "normalize_event_retention",
    "prune_engine_events_to_connection",
    "read_engine_event_stats",
    "read_engine_lease",
    "read_engine_events",
    "read_engine_events_for_run",
    "read_engine_run",
    "read_engine_runs",
    "read_engine_scheduler_state",
    "recover_running_as_retry",
    "restore_scheduler_state",
    "retry_delay",
    "retry_due_at",
    "retry_queue_snapshot",
    "running_snapshot",
    "schedule_retry_entry",
    "save_engine_scheduler_state",
    "save_engine_scheduler_state_to_connection",
    "release_engine_lease",
    "start_engine_run_to_connection",
    "work_item_from_change_delivery_lane",
    "work_item_from_issue",
    "write_json_atomic",
    "write_text_atomic",
]
