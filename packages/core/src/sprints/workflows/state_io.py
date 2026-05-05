"""Workflow state file IO, audit writes, validation, and state locking."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Callable

from sprints.engine import EngineStore
from sprints.core.config import WorkflowConfig, WorkflowConfigError
from sprints.core.contracts import load_workflow_contract
from sprints.core.paths import runtime_paths
from sprints.workflows.lanes import (
    active_lanes,
    build_dispatch_audit,
    build_retry_audit,
    build_side_effect_audit,
    lane_stage,
    save_scheduler_snapshot,
)

_STATE_LOCK_SCOPE = "workflow-state"
_STATE_LOCK_TTL_SECONDS = 120
_STATE_LOCK_RENEW_INTERVAL_SECONDS = 30.0


def _safe_lock_segment(value: str) -> str:
    return "".join(
        char if char.isalnum() or char in {"-", "_"} else "-" for char in value
    )[:80]


@dataclass
class WorkflowState:
    workflow: str = ""
    status: str = "idle"
    lanes: dict[str, dict[str, Any]] = field(default_factory=dict)
    orchestrator_decisions: list[dict[str, Any]] = field(default_factory=list)
    idle_reason: str | None = None

    @classmethod
    def initial(cls, *, workflow: str, first_stage: str) -> "WorkflowState":
        del first_stage
        return cls(workflow=workflow)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WorkflowState":
        names = {item.name for item in fields(cls)}
        return cls(**{name: raw[name] for name in names if name in raw})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_state(path: Path, *, workflow: str, first_stage: str) -> WorkflowState:
    if not path.exists():
        return WorkflowState.initial(workflow=workflow, first_stage=first_stage)
    state = WorkflowState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    if not state.workflow:
        state.workflow = workflow
    return state


def save_state(path: Path, state: WorkflowState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def append_audit(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def persist_runtime_state(*, config: WorkflowConfig, state: WorkflowState) -> None:
    save_state(config.storage.state_path, state)
    save_scheduler_snapshot(config=config, state=state)


def save_state_event(
    *,
    config: WorkflowConfig,
    state: WorkflowState,
    event: str,
    extra: dict[str, Any] | None = None,
) -> None:
    save_state(config.storage.state_path, state)
    save_scheduler_snapshot(config=config, state=state)
    state_payload = state.to_dict()
    append_audit(
        config.storage.audit_log_path,
        {
            "event": f"{config.workflow_name}.{event}",
            "state": state_payload,
            "retry_audit": build_retry_audit(state_payload),
            "dispatch_audit": build_dispatch_audit(state_payload),
            "side_effect_audit": build_side_effect_audit(state_payload),
            **dict(extra or {}),
        },
    )


def with_state_lock(
    *,
    config: WorkflowConfig,
    owner_role: str,
    callback: Callable[[], int | None],
    timeout_seconds: float = 60.0,
) -> int:
    store = runner_engine_store(config)
    lease_key = str(config.workflow_root)
    owner_instance_id = f"{owner_role}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    deadline = time.monotonic() + timeout_seconds
    acquired = False
    stop_renewing: Callable[[], None] | None = None
    try:
        while True:
            lease = store.acquire_lease(
                lease_scope=_STATE_LOCK_SCOPE,
                lease_key=lease_key,
                owner_instance_id=owner_instance_id,
                owner_role=owner_role,
                ttl_seconds=_STATE_LOCK_TTL_SECONDS,
                metadata={"workflow_root": str(config.workflow_root)},
            )
            acquired = bool(lease.get("acquired"))
            if acquired:
                break
            if time.monotonic() >= deadline:
                current_owner = lease.get("owner_instance_id") or "unknown"
                raise TimeoutError(
                    f"timed out waiting for workflow state lock held by {current_owner}"
                )
            time.sleep(0.2)
        stop_renewing = _start_state_lock_renewer(
            store=store,
            config=config,
            lease_key=lease_key,
            owner_instance_id=owner_instance_id,
            owner_role=owner_role,
        )
        result = callback()
        return int(result or 0)
    finally:
        if stop_renewing is not None:
            stop_renewing()
        if acquired:
            store.release_lease(
                lease_scope=_STATE_LOCK_SCOPE,
                lease_key=lease_key,
                owner_instance_id=owner_instance_id,
                release_reason="complete",
            )


def _start_state_lock_renewer(
    *,
    store: EngineStore,
    config: WorkflowConfig,
    lease_key: str,
    owner_instance_id: str,
    owner_role: str,
) -> Callable[[], None]:
    stop = threading.Event()

    def renew() -> None:
        while not stop.wait(_STATE_LOCK_RENEW_INTERVAL_SECONDS):
            try:
                store.acquire_lease(
                    lease_scope=_STATE_LOCK_SCOPE,
                    lease_key=lease_key,
                    owner_instance_id=owner_instance_id,
                    owner_role=owner_role,
                    ttl_seconds=_STATE_LOCK_TTL_SECONDS,
                    metadata={"workflow_root": str(config.workflow_root)},
                )
            except Exception:
                return

    thread = threading.Thread(
        target=renew,
        name=f"sprints-state-lock-{_safe_lock_segment(owner_role)}",
        daemon=True,
    )
    thread.start()

    def stop_thread() -> None:
        stop.set()
        thread.join(timeout=0.2)

    return stop_thread


def update_state_locked(
    *,
    config: WorkflowConfig,
    event: str,
    extra: dict[str, Any],
    update: Callable[[WorkflowState], None],
) -> None:
    def callback() -> int:
        state = load_state(
            config.storage.state_path,
            workflow=config.workflow_name,
            first_stage=config.first_stage,
        )
        validate_state(config, state)
        update(state)
        refresh_state_status(state, idle_reason="no active lanes")
        save_state_event(config=config, state=state, event=event, extra=extra)
        return 0

    with_state_lock(
        config=config,
        owner_role="actor-worker",
        callback=callback,
    )


def runner_engine_store(config: WorkflowConfig) -> EngineStore:
    return EngineStore(
        db_path=runtime_paths(config.workflow_root)["db_path"],
        workflow=config.workflow_name,
    )


def validate_state(config: WorkflowConfig, state: WorkflowState) -> None:
    for lane in state.lanes.values():
        stage_name = lane_stage(lane)
        if stage_name not in config.stages:
            raise RuntimeError(
                f"lane {lane.get('lane_id')} references unknown stage: {stage_name}"
            )
        validate_stage_gates(config, stage_name)


def validate_stage_gates(config: WorkflowConfig, stage_name: str) -> None:
    stage = config.stages[stage_name]
    for gate_name in stage.gates:
        gate = config.gates[gate_name]
        if gate.type != "orchestrator-evaluated":
            raise WorkflowConfigError(
                f"unsupported gate type for {gate_name}: {gate.type}"
            )


def ensure_workflow_state_files(
    workflow_root: Path, config: dict[str, Any] | None = None
) -> dict[str, str]:
    root = Path(workflow_root).expanduser().resolve()
    raw = config if config is not None else load_workflow_contract(root).config
    typed = WorkflowConfig.from_raw(raw=raw, workflow_root=root)
    if not typed.storage.state_path.exists():
        save_state(
            typed.storage.state_path,
            WorkflowState.initial(
                workflow=typed.workflow_name, first_stage=typed.first_stage
            ),
        )
    typed.storage.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
    typed.storage.audit_log_path.touch(exist_ok=True)
    return {
        "state": str(typed.storage.state_path),
        "audit_log": str(typed.storage.audit_log_path),
    }


def refresh_state_status(state: WorkflowState, *, idle_reason: str) -> None:
    if active_lanes(state):
        state.status = "running"
        state.idle_reason = None
        return
    state.status = "idle"
    state.idle_reason = idle_reason
