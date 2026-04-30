"""S-4 tests: event vocabulary alignment — Symphony §10.4."""
from __future__ import annotations


def test_canonical_constants_present():
    from workflows.change_delivery import event_taxonomy as et

    # Symphony bare names (forward-use; no current writer emits these)
    assert et.SESSION_STARTED == "session_started"
    assert et.TURN_COMPLETED == "turn_completed"
    assert et.TURN_FAILED == "turn_failed"
    assert et.TURN_CANCELLED == "turn_cancelled"
    assert et.TURN_INPUT_REQUIRED == "turn_input_required"
    assert et.NOTIFICATION == "notification"
    assert et.UNSUPPORTED_TOOL_CALL == "unsupported_tool_call"
    assert et.MALFORMED == "malformed"
    assert et.STARTUP_FAILED == "startup_failed"


def test_daedalus_native_constants_have_prefix():
    from workflows.change_delivery import event_taxonomy as et

    daedalus_natives = [
        et.DAEDALUS_RUNTIME_STARTED,
        et.DAEDALUS_RUNTIME_HEARTBEAT,
        et.DAEDALUS_LANE_PROMOTED,
        et.DAEDALUS_ACTIVE_EXECUTION_CONTROL_UPDATED,
        et.DAEDALUS_SHADOW_ACTION_REQUESTED,
        et.DAEDALUS_ACTIVE_ACTION_REQUESTED,
        et.DAEDALUS_ACTIVE_ACTION_COMPLETED,
        et.DAEDALUS_ACTIVE_ACTION_FAILED,
        et.DAEDALUS_RECOVERY_REQUESTED,
        et.DAEDALUS_OPERATOR_ATTENTION_REQUIRED,
        et.DAEDALUS_FAILURE_DETECTED,
        et.DAEDALUS_ERROR_ANALYSIS_REQUESTED,
        et.DAEDALUS_ERROR_ANALYSIS_COMPLETED,
        et.DAEDALUS_CONFIG_RELOADED,
        et.DAEDALUS_CONFIG_RELOAD_FAILED,
        et.DAEDALUS_DISPATCH_SKIPPED,
        et.DAEDALUS_STALL_DETECTED,
        et.DAEDALUS_STALL_TERMINATED,
        et.DAEDALUS_REFRESH_REQUESTED,
    ]
    for name in daedalus_natives:
        assert name.startswith("daedalus."), f"{name!r} missing daedalus. prefix"


def test_canonicalize_passes_canonical_names_through():
    from workflows.change_delivery.event_taxonomy import canonicalize, TURN_COMPLETED, DAEDALUS_LANE_PROMOTED

    assert canonicalize(TURN_COMPLETED) == TURN_COMPLETED
    assert canonicalize(DAEDALUS_LANE_PROMOTED) == DAEDALUS_LANE_PROMOTED
    assert canonicalize("session_started") == "session_started"


def test_canonicalize_resolves_legacy_aliases():
    """Pre-rename Daedalus orchestration names get resolved to daedalus.* canonical."""
    from workflows.change_delivery.event_taxonomy import canonicalize

    assert canonicalize("daedalus_runtime_started") == "daedalus.runtime_started"
    assert canonicalize("daedalus_runtime_heartbeat") == "daedalus.runtime_heartbeat"
    assert canonicalize("lane_promoted") == "daedalus.lane_promoted"
    assert canonicalize("active_execution_control_updated") == "daedalus.active_execution_control_updated"
    assert canonicalize("shadow_action_requested") == "daedalus.shadow_action_requested"
    assert canonicalize("active_action_requested") == "daedalus.active_action_requested"
    assert canonicalize("active_action_completed") == "daedalus.active_action_completed"
    assert canonicalize("active_action_failed") == "daedalus.active_action_failed"
    assert canonicalize("recovery_requested") == "daedalus.recovery_requested"
    assert canonicalize("operator_attention_required") == "daedalus.operator_attention_required"
    assert canonicalize("failure_detected") == "daedalus.failure_detected"
    assert canonicalize("error_analysis_requested") == "daedalus.error_analysis_requested"
    assert canonicalize("error_analysis_completed") == "daedalus.error_analysis_completed"


def test_canonicalize_unknown_passthrough():
    from workflows.change_delivery.event_taxonomy import canonicalize

    assert canonicalize("totally_unknown_event") == "totally_unknown_event"


def test_event_aliases_table_integrity():
    """Every legacy alias resolves to a string starting with 'daedalus.' (the
    canonical namespace for Daedalus-native orchestration events that this
    rename pass formalizes)."""
    from workflows.change_delivery import event_taxonomy as et

    for legacy, canonical in et.EVENT_ALIASES.items():
        assert canonical.startswith("daedalus."), \
            f"alias {legacy!r} -> {canonical!r} must resolve to a daedalus.* canonical"


def test_round_trip_canonical_writer_reader(tmp_path):
    """Writer writes canonical; reader reads canonical via canonicalize.

    Daedalus's append_daedalus_event uses field 'event_type' (not 'type');
    test mirrors that schema.
    """
    import json
    from workflows.change_delivery.event_taxonomy import (
        canonicalize, DAEDALUS_LANE_PROMOTED, DAEDALUS_RUNTIME_STARTED,
    )

    log = tmp_path / "events.jsonl"
    with log.open("w") as f:
        f.write(json.dumps({"event_type": DAEDALUS_LANE_PROMOTED}) + "\n")
        f.write(json.dumps({"event_type": DAEDALUS_RUNTIME_STARTED}) + "\n")

    seen = []
    for line in log.read_text().splitlines():
        e = json.loads(line)
        seen.append(canonicalize(e["event_type"]))
    assert seen == [DAEDALUS_LANE_PROMOTED, DAEDALUS_RUNTIME_STARTED]


def test_legacy_log_lines_canonicalize_on_read(tmp_path):
    """Old jsonl files with bare Daedalus names still resolve through canonicalize."""
    import json
    from workflows.change_delivery.event_taxonomy import (
        canonicalize, DAEDALUS_LANE_PROMOTED, DAEDALUS_RUNTIME_STARTED,
    )

    log = tmp_path / "events.jsonl"
    log.write_text(
        json.dumps({"event_type": "lane_promoted"}) + "\n" +
        json.dumps({"event_type": "daedalus_runtime_started"}) + "\n"
    )
    canon = [canonicalize(json.loads(l)["event_type"]) for l in log.read_text().splitlines()]
    assert canon == [DAEDALUS_LANE_PROMOTED, DAEDALUS_RUNTIME_STARTED]


def test_runtime_py_emits_only_canonical_event_types():
    """AST scan of runtime.py: every dict literal {"event_type": "..."}
    has its value supplied by an event_taxonomy constant, never a bare
    string literal. Regression for the S-4 rename pass.
    """
    import ast
    import pathlib
    from workflows.change_delivery import event_taxonomy as et

    canonical_values = {
        v for v in vars(et).values()
        if isinstance(v, str) and (v.startswith("daedalus.") or "_" not in v or v in {
            et.SESSION_STARTED, et.TURN_COMPLETED, et.TURN_FAILED,
            et.TURN_CANCELLED, et.TURN_INPUT_REQUIRED, et.NOTIFICATION,
            et.UNSUPPORTED_TOOL_CALL, et.MALFORMED, et.STARTUP_FAILED,
        })
    }

    repo_root = pathlib.Path(__file__).resolve().parents[1] / "daedalus"
    runtime_src = (repo_root / "runtime.py").read_text()
    tree = ast.parse(runtime_src)

    bare_literal_sites: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "event_type"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    if value.value not in canonical_values:
                        bare_literal_sites.append((value.lineno, value.value))

    assert bare_literal_sites == [], (
        f"runtime.py contains bare event_type string literals that aren't "
        f"event_taxonomy canonicals: {bare_literal_sites}. Use a DAEDALUS_* "
        f"constant from workflows.change_delivery.event_taxonomy instead."
    )
