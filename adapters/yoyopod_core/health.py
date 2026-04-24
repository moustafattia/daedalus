from __future__ import annotations

import re
from typing import Any, Callable

from adapters.yoyopod_core.sessions import latest_lane_progress_epoch

DEFAULT_ISSUE_WATCHER_RE = re.compile(r"issue-\d+-watch")


def _job_state_mapping(job: dict[str, Any]) -> dict[str, Any]:
    state = job.get("state") or {}
    if isinstance(state, dict):
        return state
    return {}


def _job_last_status(job: dict[str, Any]) -> Any:
    state = _job_state_mapping(job)
    return state.get("lastStatus", job.get("last_status"))


def _job_last_error(job: dict[str, Any]) -> Any:
    state = _job_state_mapping(job)
    return state.get("lastError", job.get("last_error"))


def compute_core_job_status(
    managed_job_names: list[str],
    job_map: dict[str, dict[str, Any]],
    *,
    summarize_job_fn: Callable[[dict[str, Any] | None], dict[str, Any] | None],
) -> dict[str, Any]:
    """Split managed core jobs into missing/disabled/stale buckets + summary map.

    Pure helper ported from ``build_status_raw``. The caller injects a
    ``summarize_job_fn`` (the wrapper's ``_summarize_job``) so health logic can
    stay free of the wrapper's stale-staleness heuristics.
    """
    missing = [name for name in managed_job_names if name not in job_map]
    disabled = [
        name
        for name in managed_job_names
        if job_map.get(name) and not job_map[name].get("enabled")
    ]
    detailed: dict[str, dict[str, Any] | None] = {}
    stale: list[str] = []
    for name in managed_job_names:
        job = job_map.get(name)
        summary = summarize_job_fn(job) if job else summarize_job_fn(None)
        detailed[name] = summary
        if summary and summary.get("stale"):
            stale.append(name)
    return {
        "missing": missing,
        "disabled": disabled,
        "stale": stale,
        "detailed": detailed,
    }


def disable_broken_watchers(
    jobs_payload: dict[str, Any],
    *,
    issue_watcher_re: re.Pattern[str] = DEFAULT_ISSUE_WATCHER_RE,
    now_ms_fn: Callable[[], int],
) -> list[str]:
    """Disable issue-watcher jobs whose last run failed with "target <chatId>".

    Mutates the jobs payload in place, stamps ``updatedAtMs`` with ``now_ms_fn``,
    and returns the names of the jobs that were disabled. Pure port of the
    ``fix_watchers`` loop in the wrapper's ``reconcile``.
    """
    disabled: list[str] = []
    for job in jobs_payload.get("jobs", []) or []:
        name = str(job.get("name", ""))
        if not issue_watcher_re.fullmatch(name):
            continue
        state = job.get("state") or {}
        if (
            state.get("lastStatus") == "error"
            and "target <chatId>" in str(state.get("lastError", ""))
            and job.get("enabled")
        ):
            job["enabled"] = False
            job["updatedAtMs"] = now_ms_fn()
            disabled.append(name)
    return disabled


def collect_broken_watchers(
    jobs_payload: dict[str, Any],
    *,
    issue_watcher_re: re.Pattern[str] = DEFAULT_ISSUE_WATCHER_RE,
) -> list[dict[str, Any]]:
    """Return the enabled issue-watcher jobs that failed with a missing target chat.

    Ported from the wrapper's ``_collect_broken_watchers``. The ``issue_watcher_re``
    kwarg matches what the wrapper derives from ``CONFIG`` so callers can opt
    into a project-specific name pattern.
    """
    broken: list[dict[str, Any]] = []
    for job in jobs_payload.get("jobs", []) or []:
        name = str(job.get("name", ""))
        if not issue_watcher_re.fullmatch(name):
            continue
        if not job.get("enabled"):
            continue
        if _job_last_status(job) != "error":
            continue
        last_error = str(_job_last_error(job) or "")
        if "target <chatId>" not in last_error:
            continue
        broken.append({"name": name, "id": job.get("id"), "lastError": _job_last_error(job)})
    return broken


"""YoYoPod Core health and drift helpers.

This module is the first real extraction from the legacy wrapper read-model. It
owns the top-level health classification rules so Relay-side consumers can stop
depending on the wrapper's direct health calculation.
"""


def lane_operator_attention_reasons(
    lane_state: dict[str, Any] | None,
    *,
    retry_threshold: int = 5,
    no_progress_threshold: int = 5,
) -> list[str]:
    lane_state = lane_state or {}
    failure_state = lane_state.get("failure") or {}
    budget_state = lane_state.get("budget") or {}
    reasons: list[str] = []
    if int(failure_state.get("retryCount") or 0) >= retry_threshold:
        reasons.append(f"operator-attention-required:failure-retry-count={int(failure_state.get('retryCount') or 0)}")
    if int(budget_state.get("noProgressTicks") or 0) >= no_progress_threshold:
        reasons.append(f"operator-attention-required:no-progress-ticks={int(budget_state.get('noProgressTicks') or 0)}")
    return reasons


def compute_stale_lane_reasons(
    *,
    active_lane: dict[str, Any] | None,
    open_pr: dict[str, Any] | None,
    implementation: dict[str, Any] | None,
    lane_state: dict[str, Any] | None,
    publish_ready: bool,
    review_loop_state: str | None,
    ledger_state: str | None,
    ledger_pr_head_sha: str | None,
    codex_reviewed_head_sha: str | None,
    now_epoch: int | None,
    lane_no_pr_minutes: int = 45,
    retry_threshold: int = 5,
    no_progress_threshold: int = 5,
) -> list[str]:
    reasons: list[str] = []
    impl = implementation or {}
    state = lane_state or {}
    if active_lane and not open_pr:
        latest_progress = latest_lane_progress_epoch(impl, state)
        if latest_progress and now_epoch is not None and now_epoch - latest_progress > (lane_no_pr_minutes * 60):
            reasons.append("active lane has no PR and implementation state is stale")
    if publish_ready and review_loop_state == "awaiting_reviews" and open_pr and codex_reviewed_head_sha in {None, ""}:
        reasons.append("published PR is waiting for review artifacts")
    if publish_ready and ledger_state in {"under_review", "revalidating", "findings_open", "rework_required"} and open_pr and ledger_pr_head_sha in {None, ""}:
        reasons.append("review state lacks current PR head SHA")
    reasons.extend(
        lane_operator_attention_reasons(
            state,
            retry_threshold=retry_threshold,
            no_progress_threshold=no_progress_threshold,
        )
    )
    return reasons


def compute_health(
    *,
    engine_owner: str | None,
    active_lane_error: str | None,
    missing_core_jobs: list[str],
    disabled_core_jobs: list[str],
    stale_core_jobs: list[str],
    drift: list[str],
    stale_lane_reasons: list[str],
    broken_watchers: list[dict],
) -> str:
    archived_job_health_is_advisory = engine_owner == "hermes"
    if active_lane_error:
        return "multi-active-lane"
    if not archived_job_health_is_advisory and missing_core_jobs:
        return "missing-core-jobs"
    if not archived_job_health_is_advisory and disabled_core_jobs:
        return "disabled-core-jobs"
    if not archived_job_health_is_advisory and stale_core_jobs:
        return "stale-core-jobs"
    if drift:
        return "stale-ledger"
    if stale_lane_reasons:
        return "stale-lane"
    if broken_watchers:
        return "degraded"
    return "healthy"
