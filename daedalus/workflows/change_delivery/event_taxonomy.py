"""Symphony §10.4-aligned event taxonomy.

Single source of truth for canonical event names. Writers (in runtime.py)
emit only constants from this module; readers (status.py, tracker feedback,
watch.py, server views) wrap event-type reads in `canonicalize()` so old
log files keep working during the one-release alias window.

Design notes:
- Daedalus's existing orchestration events (`daedalus_runtime_started`,
  `lane_promoted`, `active_action_requested`, etc.) are renamed under the
  `daedalus.*` namespace so they don't collide with Symphony's bare
  session/turn names that may be added later.
- Symphony's bare session/turn lifecycle constants (`session_started`,
  `turn_completed`, …) are defined here for FORWARD use by future
  agent-runner integration. Today's `runtime.py` writers don't emit
  them — they emit Daedalus-orchestration events. Code that wraps
  Codex / Claude session lifecycles will adopt these names later.
- `EVENT_ALIASES` maps legacy bare names (the strings actually present in
  log files written before this rename) to canonical `daedalus.*` forms.
"""
from __future__ import annotations


# ---- Symphony §10.4 session/turn-level events (forward use; not emitted
#      by runtime.py today — reserved for future agent-runner integration) ----
SESSION_STARTED       = "session_started"
TURN_COMPLETED        = "turn_completed"
TURN_FAILED           = "turn_failed"
TURN_CANCELLED        = "turn_cancelled"
TURN_INPUT_REQUIRED   = "turn_input_required"
NOTIFICATION          = "notification"
UNSUPPORTED_TOOL_CALL = "unsupported_tool_call"
MALFORMED             = "malformed"
STARTUP_FAILED        = "startup_failed"


# ---- Daedalus-native orchestration events (canonical, prefixed). ----
# These cover the distinct event_type literals currently emitted by
# runtime.py via append_daedalus_event.
DAEDALUS_RUNTIME_STARTED              = "daedalus.runtime_started"
DAEDALUS_RUNTIME_HEARTBEAT            = "daedalus.runtime_heartbeat"
DAEDALUS_LANE_PROMOTED                = "daedalus.lane_promoted"
DAEDALUS_ACTIVE_EXECUTION_CONTROL_UPDATED = "daedalus.active_execution_control_updated"
DAEDALUS_SHADOW_ACTION_REQUESTED      = "daedalus.shadow_action_requested"
DAEDALUS_ACTIVE_ACTION_REQUESTED      = "daedalus.active_action_requested"
DAEDALUS_ACTIVE_ACTION_COMPLETED      = "daedalus.active_action_completed"
DAEDALUS_ACTIVE_ACTION_CANCELED       = "daedalus.active_action_canceled"
DAEDALUS_ACTIVE_ACTION_FAILED         = "daedalus.active_action_failed"
DAEDALUS_RECOVERY_REQUESTED           = "daedalus.recovery_requested"
DAEDALUS_OPERATOR_ATTENTION_REQUIRED  = "daedalus.operator_attention_required"
DAEDALUS_FAILURE_DETECTED             = "daedalus.failure_detected"
DAEDALUS_ERROR_ANALYSIS_REQUESTED     = "daedalus.error_analysis_requested"
DAEDALUS_ERROR_ANALYSIS_COMPLETED     = "daedalus.error_analysis_completed"

# Daedalus-native events introduced by other Symphony-conformance phases
# (declared here for the single-source-of-truth invariant; emitted by
# code added in S-2 / S-3 / S-5 / S-6).
DAEDALUS_CONFIG_RELOADED              = "daedalus.config_reloaded"
DAEDALUS_CONFIG_RELOAD_FAILED         = "daedalus.config_reload_failed"
DAEDALUS_DISPATCH_SKIPPED             = "daedalus.dispatch_skipped"
DAEDALUS_STALL_DETECTED               = "daedalus.stall_detected"
DAEDALUS_STALL_TERMINATED             = "daedalus.stall_terminated"
DAEDALUS_REFRESH_REQUESTED            = "daedalus.refresh_requested"


# ---- One-release alias window: legacy bare names -> canonical prefixed. ----
# Pre-rename log files contain the bare Daedalus names; readers normalize
# via canonicalize() so dashboards, tracker-feedback filters, and tests
# that consume daedalus-events.jsonl keep working across the rollout.
EVENT_ALIASES: dict[str, str] = {
    "daedalus_runtime_started":            DAEDALUS_RUNTIME_STARTED,
    "daedalus_runtime_heartbeat":          DAEDALUS_RUNTIME_HEARTBEAT,
    "lane_promoted":                       DAEDALUS_LANE_PROMOTED,
    "active_execution_control_updated":    DAEDALUS_ACTIVE_EXECUTION_CONTROL_UPDATED,
    "shadow_action_requested":             DAEDALUS_SHADOW_ACTION_REQUESTED,
    "active_action_requested":             DAEDALUS_ACTIVE_ACTION_REQUESTED,
    "active_action_completed":             DAEDALUS_ACTIVE_ACTION_COMPLETED,
    "active_action_canceled":              DAEDALUS_ACTIVE_ACTION_CANCELED,
    "active_action_failed":                DAEDALUS_ACTIVE_ACTION_FAILED,
    "recovery_requested":                  DAEDALUS_RECOVERY_REQUESTED,
    "operator_attention_required":         DAEDALUS_OPERATOR_ATTENTION_REQUIRED,
    "failure_detected":                    DAEDALUS_FAILURE_DETECTED,
    "error_analysis_requested":            DAEDALUS_ERROR_ANALYSIS_REQUESTED,
    "error_analysis_completed":            DAEDALUS_ERROR_ANALYSIS_COMPLETED,
}


def canonicalize(event_type: str) -> str:
    """Resolve a possibly-legacy event-type string to its canonical form.

    Idempotent for already-canonical names. Unknown names pass through
    unchanged so readers don't lose information about events emitted by
    code paths added after the alias map was last updated.
    """
    return EVENT_ALIASES.get(event_type, event_type)
