import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_compute_stale_lane_reasons_flags_no_pr_lane_with_old_progress():
    health_module = load_module("daedalus_workflows_change_delivery_stale_lane_test", "workflows/change_delivery/health.py")

    reasons = health_module.compute_stale_lane_reasons(
        active_lane={"number": 224},
        open_pr=None,
        implementation={"updatedAt": "2026-04-22T00:00:00Z", "activeSessionHealth": {"lastUsedAt": "2026-04-22T00:00:00Z"}},
        lane_state={"implementation": {"lastMeaningfulProgressAt": "2026-04-22T00:00:00Z"}},
        publish_ready=False,
        review_loop_state="implementing_local",
        ledger_state="implementing_local",
        ledger_pr_head_sha=None,
        codex_reviewed_head_sha=None,
        now_epoch=9999999999,
        lane_no_pr_minutes=45,
    )

    assert "active lane has no PR and implementation state is stale" in reasons


def test_compute_stale_lane_reasons_includes_operator_attention_reasons():
    health_module = load_module("daedalus_workflows_change_delivery_stale_lane_test", "workflows/change_delivery/health.py")

    reasons = health_module.compute_stale_lane_reasons(
        active_lane={"number": 224},
        open_pr=None,
        implementation={},
        lane_state={"failure": {"retryCount": 5}, "budget": {"noProgressTicks": 5}},
        publish_ready=False,
        review_loop_state="implementing_local",
        ledger_state="implementing_local",
        ledger_pr_head_sha=None,
        codex_reviewed_head_sha=None,
        now_epoch=0,
        lane_no_pr_minutes=45,
    )

    assert "operator-attention-required:failure-retry-count=5" in reasons
    assert "operator-attention-required:no-progress-ticks=5" in reasons
