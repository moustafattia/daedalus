"""Engine-backed journal for one workflow tick."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from sprints.engine import EngineStore
from sprints.core.config import WorkflowConfig
from sprints.workflows.orchestrator import OrchestratorDecision
from sprints.core.paths import runtime_paths
from sprints.workflows.state_io import WorkflowState
from sprints.workflows.lanes import active_lanes, decision_ready_lanes


def _engine_store(config: WorkflowConfig) -> EngineStore:
    return EngineStore(
        db_path=runtime_paths(config.workflow_root)["db_path"],
        workflow=config.workflow_name,
    )


@dataclass(frozen=True)
class TickJournal:
    run_id: str
    tick_id: str
    started_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "tick_id": self.tick_id,
            "started_at": self.started_at,
        }


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, sort_keys=True))


def _tick_journal_counts(state: WorkflowState | None) -> dict[str, Any]:
    if state is None:
        return {
            "lane_count": 0,
            "active_lane_count": 0,
            "decision_ready_count": 0,
            "running_count": 0,
            "retry_count": 0,
            "operator_attention_count": 0,
            "workflow_status": None,
        }
    current_active_lanes = active_lanes(state)
    return {
        "lane_count": len(state.lanes),
        "active_lane_count": len(current_active_lanes),
        "decision_ready_count": len(decision_ready_lanes(state)),
        "running_count": len(
            [
                lane
                for lane in current_active_lanes
                if str(lane.get("status") or "") == "running"
            ]
        ),
        "retry_count": len(
            [
                lane
                for lane in current_active_lanes
                if str(lane.get("status") or "") == "retry_queued"
            ]
        ),
        "operator_attention_count": len(
            [
                lane
                for lane in current_active_lanes
                if str(lane.get("status") or "") == "operator_attention"
            ]
        ),
        "workflow_status": state.status,
    }


def start_tick_journal(
    *,
    config: WorkflowConfig,
    state: WorkflowState | None = None,
    orchestrator_output: str = "",
) -> TickJournal:
    counts = _tick_journal_counts(state)
    run = _engine_store(config).start_run(
        mode="tick",
        selected_count=int(counts["active_lane_count"] or 0),
        metadata={
            "workflow_root": str(config.workflow_root),
            "lane_counts": counts,
            "orchestrator_output_supplied": bool(
                str(orchestrator_output or "").strip()
            ),
        },
    )
    journal = TickJournal(
        run_id=str(run["run_id"]),
        tick_id=str(run["run_id"]),
        started_at=str(run["started_at"]),
    )
    record_tick_journal(
        config=config,
        journal=journal,
        state=state,
        event="started",
        details={"orchestrator_output_supplied": bool(orchestrator_output)},
    )
    return journal


def record_tick_journal(
    *,
    config: WorkflowConfig,
    journal: TickJournal,
    event: str,
    state: WorkflowState | None = None,
    details: dict[str, Any] | None = None,
    severity: str = "info",
) -> None:
    payload: dict[str, Any] = {
        "tick_id": journal.tick_id,
        "run_id": journal.run_id,
        "workflow": config.workflow_name,
        "workflow_root": str(config.workflow_root),
        "lane_counts": _tick_journal_counts(state),
    }
    if details:
        payload["details"] = _json_safe(details)
    _engine_store(config).append_event(
        event_type=f"workflow.tick.{event}",
        run_id=journal.run_id,
        payload=payload,
        severity=severity,
    )


def finish_tick_journal(
    *,
    config: WorkflowConfig,
    journal: TickJournal,
    state: WorkflowState | None,
    status: Literal["completed", "failed"],
    terminal_event: str,
    selected_count: int | None = None,
    completed_count: int = 0,
    error: Exception | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    severity = "error" if status == "failed" else "info"
    terminal_details = dict(details or {})
    if error is not None:
        terminal_details["error"] = str(error)
        terminal_details["error_type"] = type(error).__name__
    record_tick_journal(
        config=config,
        journal=journal,
        state=state,
        event=terminal_event,
        details=terminal_details,
        severity=severity,
    )
    counts = _tick_journal_counts(state)
    _engine_store(config).finish_run(
        journal.run_id,
        status=status,
        selected_count=(
            int(selected_count)
            if selected_count is not None
            else int(counts["active_lane_count"] or 0)
        ),
        completed_count=completed_count,
        error=str(error) if error is not None else None,
        metadata={
            **journal.to_dict(),
            "workflow_root": str(config.workflow_root),
            "lane_counts": counts,
            "terminal_event": terminal_event,
            **_json_safe(details or {}),
        },
    )


def decision_summaries(
    decisions: list[OrchestratorDecision],
) -> list[dict[str, Any]]:
    return [
        {
            "lane_id": decision.lane_id,
            "decision": decision.decision,
            "stage": decision.stage,
            "target": decision.target,
            "reason": decision.reason,
        }
        for decision in decisions
    ]


def result_summaries(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for result in results:
        nested = result.get("result") if isinstance(result.get("result"), dict) else {}
        summaries.append(
            {
                "lane_id": result.get("lane_id"),
                "decision": result.get("decision"),
                "target": result.get("target"),
                "status": nested.get("status") or result.get("status"),
                "mode": nested.get("mode") or result.get("mode"),
            }
        )
    return summaries
