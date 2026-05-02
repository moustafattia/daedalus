"""Symphony §10.4-aligned event taxonomy."""
from __future__ import annotations


# ---- Symphony §10.4 session/turn-level events. ----
SESSION_STARTED       = "session_started"
TURN_COMPLETED        = "turn_completed"
TURN_FAILED           = "turn_failed"
TURN_CANCELLED        = "turn_cancelled"
TURN_INPUT_REQUIRED   = "turn_input_required"
NOTIFICATION          = "notification"
UNSUPPORTED_TOOL_CALL = "unsupported_tool_call"
MALFORMED             = "malformed"
STARTUP_FAILED        = "startup_failed"


DAEDALUS_CONFIG_RELOADED              = "daedalus.config_reloaded"
DAEDALUS_CONFIG_RELOAD_FAILED         = "daedalus.config_reload_failed"
DAEDALUS_DISPATCH_SKIPPED             = "daedalus.dispatch_skipped"
DAEDALUS_STALL_DETECTED               = "daedalus.stall_detected"
DAEDALUS_STALL_TERMINATED             = "daedalus.stall_terminated"
DAEDALUS_REFRESH_REQUESTED            = "daedalus.refresh_requested"


EVENT_ALIASES: dict[str, str] = {
}


def canonicalize(event_type: str) -> str:
    """Resolve a possibly-legacy event-type string to its canonical form.

    Idempotent for already-canonical names. Unknown names pass through
    unchanged so readers don't lose information about events emitted by
    code paths added after the alias map was last updated.
    """
    return EVENT_ALIASES.get(event_type, event_type)
