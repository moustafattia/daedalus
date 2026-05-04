from pathlib import Path

from workflows.config import WorkflowConfig
from workflows.lanes import (
    advance_lane,
    apply_actor_output_status,
    record_actor_output,
    validate_decision_for_lane,
)
from workflows.orchestrator import OrchestratorDecision


def _config(tmp_path: Path) -> WorkflowConfig:
    return WorkflowConfig.from_raw(
        raw={
            "workflow": "change-delivery",
            "orchestrator": {"actor": "orchestrator"},
            "runtimes": {"local": {"kind": "local"}},
            "actors": {
                "orchestrator": {"runtime": "local"},
                "implementer": {"runtime": "local"},
                "reviewer": {"runtime": "local"},
            },
            "stages": {
                "deliver": {"actors": ["implementer"], "next": "review"},
                "review": {"actors": ["reviewer"], "next": "done"},
                "done": {},
            },
            "storage": {},
        },
        workflow_root=tmp_path,
    )


def test_successful_retry_delivery_clears_stale_reviewer_changes(tmp_path):
    config = _config(tmp_path)
    lane = {
        "lane_id": "github#31",
        "stage": "deliver",
        "status": "running",
        "attempt": 2,
        "pending_retry": {
            "stage": "deliver",
            "target": "implementer",
            "attempt": 2,
        },
        "actor_outputs": {
            "reviewer": {
                "status": "changes_requested",
                "summary": "Fix CLI dispatch.",
                "required_fixes": [{"file": "src/pkg/cli.py"}],
            }
        },
    }
    output = {
        "status": "done",
        "branch": "codex/issue-31",
        "pull_request": {
            "number": 36,
            "state": "open",
            "url": "https://github.example/pr/36",
        },
        "verification": [{"command": "pytest", "status": "passed"}],
    }

    record_actor_output(
        config=config,
        lane=lane,
        actor_name="implementer",
        output=output,
    )
    apply_actor_output_status(
        config=config,
        lane=lane,
        actor_name="implementer",
        output=output,
    )
    advance_lane(config=config, lane=lane, target="review")

    validate_decision_for_lane(
        config=config,
        lane=lane,
        decision=OrchestratorDecision(
            decision="run_actor",
            stage="review",
            lane_id="github#31",
            target="reviewer",
        ),
    )
    assert lane["actor_outputs"]["implementer"] == output
    assert "reviewer" not in lane["actor_outputs"]
    assert lane["superseded_actor_outputs"][0]["actor"] == "reviewer"


def test_existing_retry_state_with_stale_reviewer_changes_can_resume(tmp_path):
    config = _config(tmp_path)
    implementation = {
        "status": "done",
        "pull_request": {
            "number": 36,
            "state": "open",
            "url": "https://github.example/pr/36",
        },
        "verification": [{"command": "pytest", "status": "passed"}],
    }
    lane = {
        "lane_id": "github#31",
        "stage": "review",
        "status": "waiting",
        "attempt": 2,
        "pending_retry": None,
        "last_actor_output": implementation,
        "actor_outputs": {
            "implementer": implementation,
            "reviewer": {
                "status": "changes_requested",
                "summary": "Fix CLI dispatch.",
                "required_fixes": [{"file": "src/pkg/cli.py"}],
            },
        },
    }

    validate_decision_for_lane(
        config=config,
        lane=lane,
        decision=OrchestratorDecision(
            decision="run_actor",
            stage="review",
            lane_id="github#31",
            target="reviewer",
        ),
    )
