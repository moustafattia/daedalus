"""Tracker intake, auto-activation, and lane claiming."""

from __future__ import annotations

from typing import Any

from sprints.trackers import build_tracker_client, issue_priority_sort_key
from sprints.core.config import WorkflowConfig
from sprints.workflows.effects import (
    record_side_effect_failed,
    record_side_effect_started,
    record_side_effect_succeeded,
    side_effect_key,
)
from sprints.workflows.lane_state import (
    acquire_lane_lease,
    append_engine_event,
    configured_texts,
    concurrency_config,
    has_open_blockers,
    intake_auto_activate_config,
    issue_labels,
    lane_id as build_lane_id,
    new_lane,
    record_engine_lane,
    repository_path,
    tracker_config,
    active_lanes,
    lane_is_terminal,
)


def claim_new_lanes(*, config: WorkflowConfig, state: Any) -> dict[str, Any]:
    tracker_cfg = tracker_config(config)
    if not tracker_cfg:
        return _claim_manual_lane(config=config, state=state)
    concurrency = concurrency_config(config)
    available = max(concurrency["max_active_lanes"] - len(active_lanes(state)), 0)
    if available <= 0:
        return {"status": "full", "reason": "lane capacity reached"}

    facts = tracker_facts(config=config, state=state)
    auto_activate = {"status": "skipped", "reason": "eligible candidates found"}
    candidates = facts.get("candidates") if isinstance(facts, dict) else []
    if isinstance(candidates, list) and not candidates and not facts.get("error"):
        auto_activate = _auto_activate_tracker_candidates(
            config=config,
            state=state,
            available=available,
        )
        if auto_activate.get("activated"):
            facts = tracker_facts(config=config, state=state)
            candidates = facts.get("candidates") if isinstance(facts, dict) else []
            if not isinstance(candidates, list) or not candidates:
                candidates = _eligible_candidates(
                    config=config,
                    tracker_cfg=tracker_cfg,
                    issues=[
                        issue
                        for issue in auto_activate.get("activated_issues", [])
                        if isinstance(issue, dict)
                    ],
                    state=state,
                )
                facts = {
                    **facts,
                    "candidates": candidates,
                    "candidate_count": len(candidates),
                }
    if not isinstance(candidates, list) or not candidates:
        return {
            "status": "idle",
            "reason": str(facts.get("error") or "no eligible tracker candidates"),
            "facts": facts,
            "auto_activate": auto_activate,
        }

    claimed: list[dict[str, Any]] = []
    for issue in candidates:
        if len(claimed) >= available:
            break
        lane_id = build_lane_id(config=config, issue=issue)
        if lane_id in state.lanes and not lane_is_terminal(state.lanes[lane_id]):
            continue
        lease = acquire_lane_lease(config=config, lane_id=lane_id, issue=issue)
        if not lease.get("acquired"):
            continue
        lane = new_lane(
            config=config,
            lane_id=lane_id,
            issue=issue,
            lease=lease,
        )
        state.lanes[lane_id] = lane
        record_engine_lane(config=config, lane=lane)
        append_engine_event(
            config=config,
            lane=lane,
            event_type=f"{config.workflow_name}.lane.claimed",
            payload={"lane": lane},
        )
        claimed.append(lane)

    if claimed:
        return {
            "status": "claimed",
            "claimed": [lane["lane_id"] for lane in claimed],
            "facts": facts,
            "auto_activate": auto_activate,
        }
    return {
        "status": "idle",
        "reason": "all eligible tracker candidates are already claimed",
        "facts": facts,
        "auto_activate": auto_activate,
    }


def _claim_manual_lane(*, config: WorkflowConfig, state: Any) -> dict[str, Any]:
    lane_id = f"{config.workflow_name}#manual"
    if lane_id in state.lanes:
        return {"status": "skipped", "reason": "manual lane already exists"}
    if active_lanes(state):
        return {"status": "full", "reason": "lane capacity reached"}
    issue = {"id": "manual", "identifier": lane_id, "title": config.workflow_name}
    lease = acquire_lane_lease(config=config, lane_id=lane_id, issue=issue)
    if not lease.get("acquired"):
        return {"status": "idle", "reason": "manual lane is already claimed"}
    lane = new_lane(config=config, lane_id=lane_id, issue=issue, lease=lease)
    state.lanes[lane_id] = lane
    record_engine_lane(config=config, lane=lane)
    append_engine_event(
        config=config,
        lane=lane,
        event_type=f"{config.workflow_name}.lane.claimed",
        payload={"lane": lane},
    )
    return {"status": "claimed", "claimed": [lane_id]}


def tracker_facts(*, config: WorkflowConfig, state: Any) -> dict[str, Any]:
    tracker_cfg = tracker_config(config)
    if not tracker_cfg:
        return {"enabled": False, "candidates": [], "candidate_count": 0}
    base = {
        "enabled": True,
        "kind": str(tracker_cfg.get("kind") or ""),
        "active_states": configured_texts(
            tracker_cfg, "active_states", "active-states"
        ),
        "terminal_states": configured_texts(
            tracker_cfg, "terminal_states", "terminal-states"
        ),
        "required_labels": configured_texts(
            tracker_cfg, "required_labels", "required-labels"
        ),
        "exclude_labels": configured_texts(
            tracker_cfg, "exclude_labels", "exclude-labels"
        ),
    }
    try:
        client = build_tracker_client(
            workflow_root=config.workflow_root,
            tracker_cfg=tracker_cfg,
            repo_path=repository_path(config),
        )
        raw_candidates = client.list_candidates()
        terminal = client.list_terminal()
    except Exception as exc:
        return {
            **base,
            "error": str(exc),
            "candidates": [],
            "candidate_count": 0,
            "terminal": [],
            "terminal_count": 0,
        }
    candidates = _eligible_candidates(
        config=config,
        tracker_cfg=tracker_cfg,
        issues=raw_candidates,
        state=state,
    )
    return {
        **base,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "terminal": terminal,
        "terminal_count": len(terminal),
    }


def _auto_activate_tracker_candidates(
    *, config: WorkflowConfig, state: Any, available: int
) -> dict[str, Any]:
    auto_cfg = intake_auto_activate_config(config)
    if not auto_cfg["enabled"]:
        return {"status": "skipped", "reason": "intake auto-activate disabled"}
    tracker_cfg = tracker_config(config)
    if not tracker_cfg:
        return {"status": "skipped", "reason": "no tracker config"}
    limit = min(auto_cfg["max_per_tick"], max(int(available or 0), 0))
    if limit <= 0:
        return {"status": "skipped", "reason": "lane capacity reached"}
    try:
        client = build_tracker_client(
            workflow_root=config.workflow_root,
            tracker_cfg=tracker_cfg,
            repo_path=repository_path(config),
        )
        issues = client.list_candidates()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    candidates = _auto_activation_candidates(
        config=config,
        tracker_cfg=tracker_cfg,
        issues=issues,
        state=state,
        add_label=auto_cfg["add_label"],
        exclude_labels=auto_cfg["exclude_labels"],
    )
    activated: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for issue in candidates[:limit]:
        issue_id = issue.get("id")
        lane_id = build_lane_id(config=config, issue=issue)
        effect_lane = {"lane_id": lane_id, "issue": issue}
        effect_payload = {"label": auto_cfg["add_label"]}
        effect_key = side_effect_key(
            config=config,
            lane=effect_lane,
            operation="tracker.auto_activate",
            target=f"issue:{issue_id}",
            payload=effect_payload,
        )
        record_side_effect_started(
            config=config,
            lane=effect_lane,
            key=effect_key,
            operation="tracker.auto_activate",
            target=f"issue:{issue_id}",
            payload=effect_payload,
        )
        try:
            added = client.add_labels(issue_id, [auto_cfg["add_label"]])
        except Exception as exc:
            record_side_effect_failed(
                config=config,
                lane=effect_lane,
                key=effect_key,
                operation="tracker.auto_activate",
                target=f"issue:{issue_id}",
                payload=effect_payload,
                error=str(exc),
            )
            failed.append(
                {
                    "lane_id": lane_id,
                    "issue_id": issue_id,
                    "error": str(exc),
                    "idempotency_key": effect_key,
                }
            )
            append_engine_event(
                config=config,
                lane={"lane_id": lane_id, "issue": issue},
                event_type=f"{config.workflow_name}.lane.auto_activate_failed",
                payload={
                    "issue": issue,
                    "error": str(exc),
                    "idempotency_key": effect_key,
                    "config": auto_cfg,
                },
                severity="warning",
            )
            continue
        if not added:
            record_side_effect_failed(
                config=config,
                lane=effect_lane,
                key=effect_key,
                operation="tracker.auto_activate",
                target=f"issue:{issue_id}",
                payload=effect_payload,
                error="tracker did not add label",
            )
            failed.append(
                {
                    "lane_id": lane_id,
                    "issue_id": issue_id,
                    "error": "tracker did not add label",
                    "idempotency_key": effect_key,
                }
            )
            continue
        record_side_effect_succeeded(
            config=config,
            lane=effect_lane,
            key=effect_key,
            operation="tracker.auto_activate",
            target=f"issue:{issue_id}",
            payload=effect_payload,
            result={"label": auto_cfg["add_label"]},
        )
        activated_issue = {
            **issue,
            "labels": sorted({*issue_labels(issue), auto_cfg["add_label"].lower()}),
        }
        activated.append(activated_issue)
        append_engine_event(
            config=config,
            lane={"lane_id": lane_id, "issue": activated_issue},
            event_type=f"{config.workflow_name}.lane.auto_activated",
            payload={
                "issue": activated_issue,
                "add_label": auto_cfg["add_label"],
                "idempotency_key": effect_key,
                "config": auto_cfg,
            },
        )

    status = "activated" if activated else "idle"
    return {
        "status": status,
        "activated": [build_lane_id(config=config, issue=issue) for issue in activated],
        "activated_issues": activated,
        "failed": failed,
        "candidate_count": len(candidates),
    }


def _auto_activation_candidates(
    *,
    config: WorkflowConfig,
    tracker_cfg: dict[str, Any],
    issues: list[dict[str, Any]],
    state: Any,
    add_label: str,
    exclude_labels: list[str],
) -> list[dict[str, Any]]:
    active_states = set(configured_texts(tracker_cfg, "active_states", "active-states"))
    terminal_states = set(
        configured_texts(tracker_cfg, "terminal_states", "terminal-states")
    )
    excluded = {label.lower() for label in exclude_labels}
    activation_label = add_label.lower()
    known_lane_ids = set(state.lanes)
    candidates: list[dict[str, Any]] = []
    for issue in issues:
        lane_id = build_lane_id(config=config, issue=issue)
        if lane_id in known_lane_ids:
            continue
        issue_state = str(issue.get("state") or "").strip().lower()
        if active_states and issue_state not in active_states:
            continue
        labels = issue_labels(issue)
        if activation_label in labels:
            continue
        if excluded.intersection(labels):
            continue
        if has_open_blockers(issue, terminal_states=terminal_states):
            continue
        candidates.append(issue)
    return sorted(candidates, key=issue_priority_sort_key)


def _eligible_candidates(
    *,
    config: WorkflowConfig,
    tracker_cfg: dict[str, Any],
    issues: list[dict[str, Any]],
    state: Any,
) -> list[dict[str, Any]]:
    active_states = set(configured_texts(tracker_cfg, "active_states", "active-states"))
    terminal_states = set(
        configured_texts(tracker_cfg, "terminal_states", "terminal-states")
    )
    required_labels = set(
        configured_texts(tracker_cfg, "required_labels", "required-labels")
    )
    exclude_labels = set(
        configured_texts(tracker_cfg, "exclude_labels", "exclude-labels")
    )
    known_lane_ids = set(state.lanes)
    candidates: list[dict[str, Any]] = []
    for issue in issues:
        lane_id = build_lane_id(config=config, issue=issue)
        if lane_id in known_lane_ids:
            continue
        issue_state = str(issue.get("state") or "").strip().lower()
        if active_states and issue_state not in active_states:
            continue
        labels = issue_labels(issue)
        if required_labels and not required_labels.issubset(labels):
            continue
        if exclude_labels.intersection(labels):
            continue
        if has_open_blockers(issue, terminal_states=terminal_states):
            continue
        candidates.append(issue)
    return sorted(candidates, key=issue_priority_sort_key)
