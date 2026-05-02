"""Canonical Daedalus event names used by the generic workflow runtime."""
from __future__ import annotations

DAEDALUS_ACTIVE_ACTION_CANCELED = "daedalus.active.action.canceled"
DAEDALUS_ACTIVE_ACTION_COMPLETED = "daedalus.active.action.completed"
DAEDALUS_ACTIVE_ACTION_FAILED = "daedalus.active.action.failed"
DAEDALUS_ACTIVE_ACTION_REQUESTED = "daedalus.active.action.requested"
DAEDALUS_ACTIVE_EXECUTION_CONTROL_UPDATED = "daedalus.active.execution_control.updated"
DAEDALUS_ERROR_ANALYSIS_COMPLETED = "daedalus.error_analysis.completed"
DAEDALUS_ERROR_ANALYSIS_REQUESTED = "daedalus.error_analysis.requested"
DAEDALUS_FAILURE_DETECTED = "daedalus.failure.detected"
DAEDALUS_LANE_PROMOTED = "daedalus.lane.promoted"
DAEDALUS_OPERATOR_ATTENTION_REQUIRED = "daedalus.operator_attention.required"
DAEDALUS_RECOVERY_REQUESTED = "daedalus.recovery.requested"
DAEDALUS_RUNTIME_HEARTBEAT = "daedalus.runtime.heartbeat"
DAEDALUS_RUNTIME_STARTED = "daedalus.runtime.started"
DAEDALUS_SHADOW_ACTION_REQUESTED = "daedalus.shadow.action.requested"
DAEDALUS_STALL_DETECTED = "daedalus.stall.detected"
DAEDALUS_STALL_TERMINATED = "daedalus.stall.terminated"


def canonicalize(event_type: str) -> str:
    return str(event_type or "").strip()
