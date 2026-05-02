"""Shared Sprints engine database mechanics."""

from __future__ import annotations

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
    restore_scheduler_state,
    retry_due_at,
    retry_queue_snapshot,
    running_snapshot,
    runtime_sessions_snapshot,
)
from .db import (
    connect_sprints_db,
    engine_state_tables_exist,
    init_engine_state,
)
from .state import (
    append_engine_event_to_connection,
    engine_event_stats_from_connection,
    engine_events_for_run_from_connection,
    engine_events_from_connection,
    engine_run_from_connection,
    finish_engine_run_to_connection,
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
from .work import (
    RetryEntry,
    RunningWork,
    WorkItemRef,
    WorkResult,
    work_item_from_issue,
)

__all__ = [
    "EngineStore",
    "RestoredSchedulerState",
    "RetryEntry",
    "RunningWork",
    "WorkItemRef",
    "WorkResult",
    "acquire_engine_lease",
    "append_engine_event_to_connection",
    "build_scheduler_payload",
    "clear_work_entries",
    "connect_sprints_db",
    "engine_event_stats_from_connection",
    "engine_events_for_run_from_connection",
    "engine_events_from_connection",
    "engine_run_from_connection",
    "engine_state_tables_exist",
    "finish_engine_run_to_connection",
    "init_engine_leases",
    "init_engine_state",
    "latest_engine_runs_from_connection",
    "load_engine_scheduler_state",
    "mark_running_work",
    "normalize_event_retention",
    "prune_engine_events_to_connection",
    "read_engine_event_stats",
    "read_engine_events",
    "read_engine_events_for_run",
    "read_engine_lease",
    "read_engine_run",
    "read_engine_runs",
    "read_engine_scheduler_state",
    "recover_running_as_retry",
    "release_engine_lease",
    "restore_scheduler_state",
    "retry_delay",
    "retry_due_at",
    "retry_queue_snapshot",
    "running_snapshot",
    "runtime_sessions_snapshot",
    "save_engine_scheduler_state",
    "save_engine_scheduler_state_to_connection",
    "schedule_retry_entry",
    "start_engine_run_to_connection",
    "work_item_from_issue",
]
