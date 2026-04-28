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


def test_compute_health_ignores_core_job_missing_when_engine_owner_is_hermes():
    health_module = load_module("daedalus_workflows_code_review_health_test", "workflows/code_review/health.py")

    health = health_module.compute_health(
        engine_owner="hermes",
        active_lane_error=None,
        missing_core_jobs=["yoyopod-workflow-milestone-telegram"],
        disabled_core_jobs=[],
        stale_core_jobs=[],
        drift=[],
        stale_lane_reasons=[],
        broken_watchers=[],
    )

    assert health == "healthy"


def test_compute_health_marks_missing_core_jobs_for_non_hermes_engine_owner():
    health_module = load_module("daedalus_workflows_code_review_health_test", "workflows/code_review/health.py")

    health = health_module.compute_health(
        engine_owner="openclaw",
        active_lane_error=None,
        missing_core_jobs=["checker"],
        disabled_core_jobs=[],
        stale_core_jobs=[],
        drift=[],
        stale_lane_reasons=[],
        broken_watchers=[],
    )

    assert health == "missing-core-jobs"


def test_compute_health_prefers_stale_ledger_before_stale_lane():
    health_module = load_module("daedalus_workflows_code_review_health_test", "workflows/code_review/health.py")

    health = health_module.compute_health(
        engine_owner="hermes",
        active_lane_error=None,
        missing_core_jobs=[],
        disabled_core_jobs=[],
        stale_core_jobs=[],
        drift=["ledger mismatch"],
        stale_lane_reasons=["lane stale"],
        broken_watchers=[],
    )

    assert health == "stale-ledger"


def test_collect_broken_watchers_filters_by_regex_and_error_text():
    health_module = load_module("daedalus_workflows_code_review_health_cbw", "workflows/code_review/health.py")

    jobs_payload = {
        "jobs": [
            # Match: matches regex, enabled, last_status=error, has target chatId error.
            {
                "name": "issue-224-watch",
                "id": "job-1",
                "enabled": True,
                "state": {"lastStatus": "error", "lastError": "target <chatId> missing"},
            },
            # Skipped: error but no chatId marker.
            {
                "name": "issue-225-watch",
                "id": "job-2",
                "enabled": True,
                "state": {"lastStatus": "error", "lastError": "something else"},
            },
            # Skipped: disabled.
            {
                "name": "issue-226-watch",
                "id": "job-3",
                "enabled": False,
                "state": {"lastStatus": "error", "lastError": "target <chatId> missing"},
            },
            # Skipped: name doesn't match the default regex.
            {
                "name": "unrelated-job",
                "id": "job-4",
                "enabled": True,
                "state": {"lastStatus": "error", "lastError": "target <chatId> missing"},
            },
            # Skipped: last status is not error.
            {
                "name": "issue-227-watch",
                "id": "job-5",
                "enabled": True,
                "state": {"lastStatus": "ok"},
            },
        ],
    }

    import re

    result = health_module.collect_broken_watchers(jobs_payload, issue_watcher_re=re.compile(r"issue-\d+-watch"))
    assert [w["id"] for w in result] == ["job-1"]
    assert result[0]["lastError"] == "target <chatId> missing"


def test_collect_broken_watchers_reads_legacy_flat_fields():
    health_module = load_module("daedalus_workflows_code_review_health_cbw", "workflows/code_review/health.py")
    import re

    jobs_payload = {
        "jobs": [
            {
                "name": "issue-224-watch",
                "id": "job-1",
                "enabled": True,
                "last_status": "error",
                "last_error": "target <chatId> for bot-id is not configured",
            },
        ],
    }
    result = health_module.collect_broken_watchers(jobs_payload, issue_watcher_re=re.compile(r"issue-\d+-watch"))
    assert len(result) == 1
    assert result[0]["name"] == "issue-224-watch"


def test_disable_broken_watchers_disables_matching_jobs_and_returns_names():
    health_module = load_module("daedalus_workflows_code_review_health_dbw", "workflows/code_review/health.py")
    import re

    jobs_payload = {
        "jobs": [
            {
                "name": "issue-224-watch",
                "id": "job-1",
                "enabled": True,
                "state": {"lastStatus": "error", "lastError": "target <chatId> missing"},
            },
            {
                "name": "issue-225-watch",
                "id": "job-2",
                "enabled": True,
                "state": {"lastStatus": "ok"},
            },
            {
                "name": "unrelated-job",
                "id": "job-3",
                "enabled": True,
                "state": {"lastStatus": "error", "lastError": "target <chatId> missing"},
            },
        ],
    }

    disabled = health_module.disable_broken_watchers(
        jobs_payload,
        issue_watcher_re=re.compile(r"issue-\d+-watch"),
        now_ms_fn=lambda: 1700000000000,
    )
    assert disabled == ["issue-224-watch"]
    # Mutates the job in place.
    by_name = {j["name"]: j for j in jobs_payload["jobs"]}
    assert by_name["issue-224-watch"]["enabled"] is False
    assert by_name["issue-224-watch"]["updatedAtMs"] == 1700000000000
    assert by_name["issue-225-watch"]["enabled"] is True
    assert by_name["unrelated-job"]["enabled"] is True


def test_disable_broken_watchers_is_noop_when_nothing_matches():
    health_module = load_module("daedalus_workflows_code_review_health_dbw", "workflows/code_review/health.py")
    import re

    jobs_payload = {"jobs": [{"name": "issue-224-watch", "enabled": False, "state": {"lastStatus": "error", "lastError": "target <chatId>"}}]}
    disabled = health_module.disable_broken_watchers(
        jobs_payload,
        issue_watcher_re=re.compile(r"issue-\d+-watch"),
        now_ms_fn=lambda: 1700000000000,
    )
    assert disabled == []


def test_compute_core_job_status_splits_missing_disabled_and_stale():
    health_module = load_module("daedalus_workflows_code_review_health_cjs", "workflows/code_review/health.py")

    managed = ["job-a", "job-b", "job-c", "job-d"]
    job_map = {
        "job-b": {"name": "job-b", "enabled": True},
        "job-c": {"name": "job-c", "enabled": False},
        "job-d": {"name": "job-d", "enabled": True},
    }

    summaries = {
        "job-a": None,
        "job-b": {"stale": False},
        "job-c": {"stale": False},
        "job-d": {"stale": True},
    }

    result = health_module.compute_core_job_status(
        managed,
        job_map,
        summarize_job_fn=lambda job: summaries.get((job or {}).get("name")) if job else None,
    )
    assert result["missing"] == ["job-a"]
    assert result["disabled"] == ["job-c"]
    assert result["stale"] == ["job-d"]
    assert set(result["detailed"].keys()) == set(managed)
    # Detailed entries reflect what summarize_job_fn returned (None for the missing one).
    assert result["detailed"]["job-a"] is None
    assert result["detailed"]["job-d"]["stale"] is True
