"""Compact workflow state for hot runtime prompts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from workflows.config import WorkflowConfig

APP_SERVER_INPUT_LIMIT_CHARS = 1_048_576
DEFAULT_ORCHESTRATOR_LIMIT_CHARS = 900_000
DEFAULT_ORCHESTRATOR_WARN_CHARS = 750_000


@dataclass(frozen=True)
class PromptBudget:
    max_chars: int
    warn_chars: int
    max_string_chars: int
    max_list_items: int
    max_terminal_lanes: int
    max_recent_decisions: int
    max_engine_rows: int


@dataclass(frozen=True)
class PromptBuild:
    prompt: str
    report: dict[str, Any]


def orchestrator_prompt_budget(
    config: WorkflowConfig, *, aggressive: bool = False
) -> PromptBudget:
    raw = _orchestrator_context_config(config)
    max_chars = _positive_int(
        raw,
        "max-input-chars",
        "max_input_chars",
        default=DEFAULT_ORCHESTRATOR_LIMIT_CHARS,
    )
    max_chars = min(max_chars, APP_SERVER_INPUT_LIMIT_CHARS)
    warn_chars = _positive_int(
        raw,
        "warn-input-chars",
        "warn_input_chars",
        default=DEFAULT_ORCHESTRATOR_WARN_CHARS,
    )
    warn_chars = min(warn_chars, max_chars)
    if aggressive:
        return PromptBudget(
            max_chars=max_chars,
            warn_chars=warn_chars,
            max_string_chars=600,
            max_list_items=6,
            max_terminal_lanes=0,
            max_recent_decisions=3,
            max_engine_rows=10,
        )
    return PromptBudget(
        max_chars=max_chars,
        warn_chars=warn_chars,
        max_string_chars=_positive_int(
            raw, "max-string-chars", "max_string_chars", default=2_000
        ),
        max_list_items=_positive_int(
            raw, "max-list-items", "max_list_items", default=20
        ),
        max_terminal_lanes=_nonnegative_int(
            raw, "max-terminal-lanes", "max_terminal_lanes", default=5
        ),
        max_recent_decisions=_positive_int(
            raw, "max-recent-decisions", "max_recent_decisions", default=10
        ),
        max_engine_rows=_positive_int(
            raw, "max-engine-rows", "max_engine_rows", default=50
        ),
    )


def build_orchestrator_payload(
    *,
    config: WorkflowConfig,
    state: Any,
    facts: dict[str, Any],
    available_decisions: list[str],
    aggressive: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    budget = orchestrator_prompt_budget(config, aggressive=aggressive)
    ready_lane_ids = _ready_lane_ids_from_facts(facts)
    compact_state = compact_workflow_state(
        state=state,
        ready_lane_ids=ready_lane_ids,
        budget=budget,
    )
    compact_facts = compact_workflow_facts(facts=facts, budget=budget)
    payload = {
        "config": compact_config(config.raw),
        "state": compact_state,
        "facts": compact_facts,
        "available_decisions": available_decisions,
    }
    report = {
        "compacted": True,
        "aggressive": aggressive,
        "state_lane_count": len(state.lanes),
        "active_lane_count": compact_state["lane_counts"]["active"],
        "terminal_lane_count": compact_state["lane_counts"]["terminal"],
        "decision_ready_lane_count": len(ready_lane_ids),
        "terminal_lanes_included": len(compact_state["terminal_lanes"]["recent"]),
        "max_chars": budget.max_chars,
        "warn_chars": budget.warn_chars,
        "max_string_chars": budget.max_string_chars,
        "max_list_items": budget.max_list_items,
    }
    return payload, report


def compact_workflow_state(
    *,
    state: Any,
    ready_lane_ids: set[str],
    budget: PromptBudget | None = None,
) -> dict[str, Any]:
    budget = budget or PromptBudget(
        max_chars=DEFAULT_ORCHESTRATOR_LIMIT_CHARS,
        warn_chars=DEFAULT_ORCHESTRATOR_WARN_CHARS,
        max_string_chars=2_000,
        max_list_items=20,
        max_terminal_lanes=5,
        max_recent_decisions=10,
        max_engine_rows=50,
    )
    active: list[dict[str, Any]] = []
    terminal: list[dict[str, Any]] = []
    terminal_by_status: dict[str, int] = {}
    for lane_id, lane in state.lanes.items():
        if not isinstance(lane, dict):
            continue
        if lane_is_terminal(lane):
            terminal_by_status[str(lane.get("status") or "unknown")] = (
                terminal_by_status.get(str(lane.get("status") or "unknown"), 0) + 1
            )
            terminal.append(
                _compact_terminal_lane(lane=lane, lane_id=lane_id, budget=budget)
            )
            continue
        active.append(
            compact_lane_for_prompt(
                lane=lane,
                lane_id=lane_id,
                budget=budget,
                detailed=(str(lane.get("lane_id") or lane_id) in ready_lane_ids),
            )
        )
    terminal.sort(
        key=lambda item: str(item.get("last_progress_at") or ""), reverse=True
    )
    return {
        "workflow": state.workflow,
        "status": state.status,
        "idle_reason": state.idle_reason,
        "lane_counts": {
            "total": len(state.lanes),
            "active": len(active),
            "terminal": len(terminal),
            "terminal_by_status": terminal_by_status,
        },
        "decision_ready_lane_ids": sorted(ready_lane_ids),
        "active_lanes": active,
        "terminal_lanes": {
            "count": len(terminal),
            "by_status": terminal_by_status,
            "recent": terminal[: budget.max_terminal_lanes],
            "omitted": max(len(terminal) - budget.max_terminal_lanes, 0),
        },
        "orchestrator_decision_count": len(state.orchestrator_decisions),
        "recent_orchestrator_decisions": compact_value(
            state.orchestrator_decisions[-budget.max_recent_decisions :],
            budget=budget,
        ),
    }


def compact_lane_for_prompt(
    *,
    lane: dict[str, Any],
    lane_id: str,
    budget: PromptBudget,
    detailed: bool = True,
) -> dict[str, Any]:
    actor_outputs = (
        lane.get("actor_outputs") if isinstance(lane.get("actor_outputs"), dict) else {}
    )
    compact = {
        "lane_id": lane.get("lane_id") or lane_id,
        "status": lane.get("status"),
        "stage": lane.get("stage"),
        "actor": lane.get("actor"),
        "attempt": lane.get("attempt"),
        "issue": _compact_issue(lane.get("issue"), budget=budget, detailed=detailed),
        "branch": lane.get("branch"),
        "pull_request": compact_value(lane.get("pull_request"), budget=budget),
        "pending_retry": compact_value(lane.get("pending_retry"), budget=budget),
        "retry": compact_value(retry_summary(lane), budget=budget),
        "operator_attention": _compact_operator_attention(
            lane.get("operator_attention"), budget=budget
        ),
        "actor_outputs": {
            str(actor): _compact_actor_output(output, budget=budget)
            for actor, output in actor_outputs.items()
            if isinstance(output, dict)
        },
        "last_actor_output": _compact_actor_output(
            lane.get("last_actor_output"), budget=budget
        ),
        "last_transition": compact_value(lane.get("last_transition"), budget=budget),
        "actor_dispatch": actor_dispatch_summary(lane),
        "side_effect_count": len(lane.get("side_effects") or {}),
        "side_effects": side_effects_summary(lane, limit=min(5, budget.max_list_items)),
        "runtime_session": _compact_runtime_session(
            lane.get("runtime_session"), budget=budget
        ),
        "thread_id": lane.get("thread_id"),
        "turn_id": lane.get("turn_id"),
        "last_progress_at": lane.get("last_progress_at"),
    }
    if not detailed:
        compact.pop("last_actor_output", None)
        compact["actor_outputs"] = {
            key: _compact_actor_output_summary(value)
            for key, value in compact["actor_outputs"].items()
            if isinstance(value, dict)
        }
    return _drop_empty(compact)


def compact_config(raw: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "workflow",
        "schema-version",
        "instance",
        "repository",
        "tracker",
        "code-host",
        "intake",
        "concurrency",
        "recovery",
        "retry",
        "completion",
        "orchestrator",
        "stages",
        "gates",
        "actions",
    )
    return {key: raw.get(key) for key in keep if raw.get(key) not in (None, "", [], {})}


def compact_workflow_facts(
    *, facts: dict[str, Any], budget: PromptBudget
) -> dict[str, Any]:
    tracker = facts.get("tracker") if isinstance(facts.get("tracker"), dict) else {}
    engine = facts.get("engine") if isinstance(facts.get("engine"), dict) else {}
    return _drop_empty(
        {
            "tracker": _compact_tracker_facts(tracker=tracker, budget=budget),
            "engine": {
                "active_lane_count": engine.get("active_lane_count"),
                "decision_ready_lane_count": engine.get("decision_ready_lane_count"),
                "idle_reason": engine.get("idle_reason"),
                "capacity": engine.get("capacity"),
                "decision_ready_lanes": compact_value(
                    engine.get("decision_ready_lanes"), budget=budget
                ),
                "due_retries": compact_value(
                    _take(engine.get("due_retries"), budget.max_engine_rows),
                    budget=budget,
                ),
                "work_item_count": len(engine.get("work_items") or []),
                "runtime_session_count": len(engine.get("runtime_sessions") or []),
                "work_items": _compact_engine_rows(
                    engine.get("work_items"), budget=budget
                ),
                "runtime_sessions": _compact_engine_rows(
                    engine.get("runtime_sessions"), budget=budget
                ),
            },
            "concurrency": compact_value(facts.get("concurrency"), budget=budget),
            "intake": compact_value(facts.get("intake"), budget=budget),
            "recovery": compact_value(facts.get("recovery"), budget=budget),
            "retry": compact_value(facts.get("retry"), budget=budget),
        }
    )


def prompt_size_report(
    *, prompt: str, report: dict[str, Any], budget: PromptBudget
) -> dict[str, Any]:
    size = len(prompt)
    status = "ok"
    if size >= budget.max_chars:
        status = "too_large"
    elif size >= budget.warn_chars:
        status = "warn"
    return {
        **report,
        "status": status,
        "prompt_chars": size,
        "remaining_chars": max(budget.max_chars - size, 0),
    }


def compact_value(value: Any, *, budget: PromptBudget, depth: int = 0) -> Any:
    if depth > 6:
        return _compact_scalar(value, budget=budget)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key in sorted(value):
            item = value[key]
            if item in (None, "", [], {}):
                continue
            out[str(key)] = compact_value(item, budget=budget, depth=depth + 1)
        return out
    if isinstance(value, list):
        items = [
            compact_value(item, budget=budget, depth=depth + 1)
            for item in value[: budget.max_list_items]
        ]
        omitted = len(value) - len(items)
        if omitted > 0:
            items.append({"omitted_items": omitted})
        return items
    return _compact_scalar(value, budget=budget)


def _compact_issue(
    value: Any, *, budget: PromptBudget, detailed: bool
) -> dict[str, Any]:
    issue = value if isinstance(value, dict) else {}
    labels = issue.get("labels") if isinstance(issue.get("labels"), list) else []
    out = {
        "id": issue.get("id"),
        "identifier": issue.get("identifier") or issue.get("number"),
        "title": issue.get("title"),
        "state": issue.get("state"),
        "labels": compact_value(labels, budget=budget),
        "priority": issue.get("priority"),
        "branch_name": issue.get("branch_name"),
        "url": issue.get("url"),
    }
    if detailed:
        out["description"] = _truncate(
            issue.get("description"), max_chars=budget.max_string_chars
        )
    return _drop_empty(out)


def _compact_actor_output(value: Any, *, budget: PromptBudget) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    keep = (
        "status",
        "summary",
        "branch",
        "branch_name",
        "pull_request",
        "pr",
        "files_changed",
        "commits",
        "verification",
        "findings",
        "required_fixes",
        "verification_gaps",
        "blockers",
        "thread_id",
        "turn_id",
    )
    return _drop_empty(
        {key: compact_value(value.get(key), budget=budget) for key in keep}
    )


def _compact_actor_output_summary(value: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty(
        {
            "status": value.get("status"),
            "summary": value.get("summary"),
            "pull_request": value.get("pull_request") or value.get("pr"),
            "branch": value.get("branch") or value.get("branch_name"),
        }
    )


def _compact_operator_attention(value: Any, *, budget: PromptBudget) -> dict[str, Any]:
    attention = value if isinstance(value, dict) else {}
    keep = ("reason", "message", "created_at", "updated_at", "artifacts")
    return _drop_empty(
        {key: compact_value(attention.get(key), budget=budget) for key in keep}
    )


def _compact_runtime_session(value: Any, *, budget: PromptBudget) -> dict[str, Any]:
    session = value if isinstance(value, dict) else {}
    keep = (
        "status",
        "actor",
        "stage",
        "run_id",
        "session_id",
        "thread_id",
        "turn_id",
        "started_at",
        "updated_at",
        "completed_at",
        "error",
    )
    return _drop_empty(
        {key: compact_value(session.get(key), budget=budget) for key in keep}
    )


def lane_is_terminal(lane: dict[str, Any]) -> bool:
    return str(lane.get("status") or "").strip() in {"complete", "released"}


def retry_summary(lane: dict[str, Any]) -> dict[str, Any] | None:
    pending = (
        lane.get("pending_retry") if isinstance(lane.get("pending_retry"), dict) else {}
    )
    history = [
        item for item in lane.get("retry_history") or [] if isinstance(item, dict)
    ]
    latest = history[-1] if history else {}
    if not pending and not latest:
        return None
    reason = str(pending.get("reason") or latest.get("reason") or "").strip()
    return _drop_empty(
        {
            "stage": pending.get("stage") or latest.get("stage"),
            "target": pending.get("target") or latest.get("target"),
            "reason": reason,
            "attempt": pending.get("attempt") or latest.get("next_attempt"),
            "current_attempt": pending.get("current_attempt")
            or latest.get("current_attempt"),
            "max_attempts": pending.get("max_attempts") or latest.get("max_attempts"),
            "delay_seconds": pending.get("delay_seconds")
            or latest.get("delay_seconds"),
            "due_at": pending.get("due_at") or latest.get("due_at"),
            "queued_at": pending.get("queued_at") or latest.get("queued_at"),
            "status": pending.get("status") or latest.get("status"),
            "history_count": len(history),
            "history": history[-3:],
        }
    )


def actor_dispatch_summary(lane: dict[str, Any]) -> dict[str, Any] | None:
    dispatch = (
        lane.get("actor_dispatch")
        if isinstance(lane.get("actor_dispatch"), dict)
        else {}
    )
    if not dispatch:
        return None
    runtime = (
        dispatch.get("runtime") if isinstance(dispatch.get("runtime"), dict) else {}
    )
    return _drop_empty(
        {
            "dispatch_id": dispatch.get("dispatch_id"),
            "status": dispatch.get("status"),
            "actor": dispatch.get("actor"),
            "stage": dispatch.get("stage"),
            "attempt": dispatch.get("attempt"),
            "mode": runtime.get("dispatch_mode"),
            "planned_at": dispatch.get("planned_at"),
            "started_at": dispatch.get("started_at"),
            "last_progress_at": dispatch.get("last_progress_at"),
            "completed_at": dispatch.get("completed_at"),
            "run_id": dispatch.get("run_id"),
            "thread_id": dispatch.get("thread_id"),
            "turn_id": dispatch.get("turn_id"),
        }
    )


def side_effects_summary(
    lane: dict[str, Any], *, limit: int = 5
) -> list[dict[str, Any]]:
    effects = (
        lane.get("side_effects") if isinstance(lane.get("side_effects"), dict) else {}
    )
    entries = [entry for entry in effects.values() if isinstance(entry, dict)]
    entries.sort(
        key=lambda entry: str(entry.get("updated_at") or entry.get("created_at") or "")
    )
    return [
        _drop_empty(
            {
                key: entry.get(key)
                for key in (
                    "key",
                    "operation",
                    "target",
                    "status",
                    "updated_at",
                    "error",
                )
            }
        )
        for entry in entries[-limit:]
    ]


def _compact_terminal_lane(
    *, lane: dict[str, Any], lane_id: str, budget: PromptBudget
) -> dict[str, Any]:
    return _drop_empty(
        {
            "lane_id": lane.get("lane_id") or lane_id,
            "status": lane.get("status"),
            "stage": lane.get("stage"),
            "issue": _compact_issue(lane.get("issue"), budget=budget, detailed=False),
            "branch": lane.get("branch"),
            "pull_request": compact_value(lane.get("pull_request"), budget=budget),
            "last_progress_at": lane.get("last_progress_at"),
        }
    )


def _compact_tracker_facts(
    *, tracker: dict[str, Any], budget: PromptBudget
) -> dict[str, Any]:
    candidates = (
        tracker.get("candidates") if isinstance(tracker.get("candidates"), list) else []
    )
    terminal = (
        tracker.get("terminal") if isinstance(tracker.get("terminal"), list) else []
    )
    return _drop_empty(
        {
            "enabled": tracker.get("enabled"),
            "kind": tracker.get("kind"),
            "active_states": tracker.get("active_states"),
            "terminal_states": tracker.get("terminal_states"),
            "required_labels": tracker.get("required_labels"),
            "exclude_labels": tracker.get("exclude_labels"),
            "error": tracker.get("error"),
            "candidate_count": tracker.get("candidate_count", len(candidates)),
            "candidates": [
                _compact_issue(issue, budget=budget, detailed=False)
                for issue in candidates[: budget.max_list_items]
                if isinstance(issue, dict)
            ],
            "terminal_count": tracker.get("terminal_count", len(terminal)),
            "terminal_recent": [
                _compact_issue(issue, budget=budget, detailed=False)
                for issue in terminal[: min(budget.max_list_items, 5)]
                if isinstance(issue, dict)
            ],
        }
    )


def _compact_engine_rows(value: Any, *, budget: PromptBudget) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    out: list[dict[str, Any]] = []
    for row in rows[: budget.max_engine_rows]:
        if not isinstance(row, dict):
            continue
        out.append(
            _drop_empty(
                {
                    "work_id": row.get("work_id"),
                    "issue_id": row.get("issue_id"),
                    "state": row.get("state") or row.get("status"),
                    "mode": row.get("mode"),
                    "actor": row.get("actor"),
                    "stage": row.get("stage"),
                    "run_id": row.get("run_id"),
                    "thread_id": row.get("thread_id"),
                    "turn_id": row.get("turn_id"),
                    "updated_at": row.get("updated_at"),
                    "due_at": row.get("due_at"),
                }
            )
        )
    if len(rows) > len(out):
        out.append({"omitted_rows": len(rows) - len(out)})
    return out


def _ready_lane_ids_from_facts(facts: dict[str, Any]) -> set[str]:
    engine = facts.get("engine") if isinstance(facts.get("engine"), dict) else {}
    ready = (
        engine.get("decision_ready_lanes")
        if isinstance(engine.get("decision_ready_lanes"), list)
        else []
    )
    ids: set[str] = set()
    for lane in ready:
        if not isinstance(lane, dict):
            continue
        lane_id = str(lane.get("lane_id") or "").strip()
        if lane_id:
            ids.add(lane_id)
    return ids


def _orchestrator_context_config(config: WorkflowConfig) -> dict[str, Any]:
    raw = config.raw.get("orchestrator")
    if not isinstance(raw, dict):
        return {}
    context = raw.get("context") or raw.get("prompt-context") or {}
    return context if isinstance(context, dict) else {}


def _take(value: Any, limit: int) -> list[Any]:
    return value[:limit] if isinstance(value, list) else []


def _compact_scalar(value: Any, *, budget: PromptBudget) -> Any:
    if isinstance(value, str):
        return _truncate(value, max_chars=budget.max_string_chars)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _truncate(str(value), max_chars=budget.max_string_chars)


def _truncate(value: Any, *, max_chars: int) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    if len(text) <= max_chars:
        return text
    return (
        text[: max(max_chars - 40, 0)]
        + f"... [truncated {len(text) - max_chars} chars]"
    )


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}


def _positive_int(config: dict[str, Any], *keys: str, default: int) -> int:
    for key in keys:
        value = config.get(key)
        if value in (None, ""):
            continue
        try:
            return max(int(value), 1)
        except (TypeError, ValueError):
            return default
    return default


def _nonnegative_int(config: dict[str, Any], *keys: str, default: int) -> int:
    for key in keys:
        value = config.get(key)
        if value in (None, ""):
            continue
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            return default
    return default


def json_size(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True))
