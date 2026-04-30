from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml
from jsonschema import Draft7Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from runtimes import PromptRunResult, Runtime, build_runtimes
from workflows.contract import WORKFLOW_POLICY_KEY, WorkflowContractError, load_workflow_contract
from workflows.shared.config_snapshot import AtomicRef, ConfigSnapshot
from workflows.issue_runner.tracker import (
    TrackerClient,
    TrackerConfigError,
    build_tracker_client,
    describe_tracker_source,
    eligible_issues,
    issue_session_name,
    issue_workspace_slug,
    select_issue,
)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _now_epoch() -> float:
    return time.time()


def _cfg_value(config: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in config:
            return config[key]
    return default


def _repository_path_from_config(workflow_root: Path, config: dict[str, Any]) -> Path | None:
    repository_cfg = config.get("repository") or {}
    raw = str(
        repository_cfg.get("local-path")
        or repository_cfg.get("local_path")
        or ""
    ).strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (workflow_root / path).resolve()
    return path


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_optional_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _schema_path() -> Path:
    return Path(__file__).with_name("schema.yaml")


def _validate_issue_runner_config(config: dict[str, Any]) -> None:
    schema = yaml.safe_load(_schema_path().read_text(encoding="utf-8"))
    Draft7Validator(schema).validate(config)


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _safe_issue_workspace_path(workspace_root: Path, issue: dict[str, Any]) -> Path:
    root = workspace_root.expanduser().resolve()
    workspace_key = issue_workspace_slug(issue)
    path = root / workspace_key
    lexical_path = path.resolve(strict=False)
    if lexical_path == root or not _is_relative_to(lexical_path, root):
        raise RuntimeError(
            f"invalid workspace path for issue {issue.get('identifier') or issue.get('id')!r}: "
            f"{lexical_path} is not a child of {root}"
        )
    return path


def _assert_workspace_inside_root(workspace_root: Path, issue_workspace: Path) -> None:
    root = workspace_root.expanduser().resolve()
    resolved = issue_workspace.expanduser().resolve()
    if resolved == root or not _is_relative_to(resolved, root):
        raise RuntimeError(f"invalid workspace path: {resolved} is not a child of {root}")


def _retry_due_at(entry: dict[str, Any] | None, *, default: float | None = None) -> float:
    payload = entry or {}
    if payload.get("due_at_monotonic") is not None:
        return float(payload.get("due_at_monotonic") or 0.0)
    if payload.get("due_at_epoch") is not None:
        return float(payload.get("due_at_epoch") or 0.0)
    if payload.get("dueAtEpoch") is not None:
        return float(payload.get("dueAtEpoch") or 0.0)
    return float(default or _now_epoch())


def _render_prompt(*, prompt_template: str, issue: dict[str, Any], attempt: int | None) -> str:
    template = str(prompt_template or "").strip()
    if not template:
        template = "You are working on an issue.\n\nIssue: {{ issue.identifier }} - {{ issue.title }}"
    if "{%" in template or "%}" in template:
        raise RuntimeError("template_parse_error: control blocks are not supported")

    import re

    def replace(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        if "|" in expr:
            raise RuntimeError(f"template_render_error: unsupported filter in {expr!r}")
        if expr == "attempt":
            return "" if attempt is None else str(attempt)
        if not expr.startswith("issue."):
            raise RuntimeError(f"template_render_error: unknown variable {expr!r}")
        value: Any = issue
        for part in expr.split(".")[1:]:
            if not isinstance(value, dict) or part not in value:
                raise RuntimeError(f"template_render_error: unknown variable {expr!r}")
            value = value[part]
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True)
        return str(value)

    rendered = re.sub(r"{{\s*([^{}]+?)\s*}}", replace, template)
    if "{{" in rendered or "}}" in rendered:
        raise RuntimeError("template_parse_error: unbalanced template delimiters")
    return rendered.strip() + "\n"


def _subprocess_run(command: list[str], *, cwd: Path | None = None, timeout: int | None = None, env: dict[str, str] | None = None):
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=merged_env,
    )


def _subprocess_run_json(command: list[str], *, cwd: Path | None = None, timeout: int | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
    completed = _subprocess_run(command, cwd=cwd, timeout=timeout, env=env)
    payload = json.loads(completed.stdout or "{}")
    if not isinstance(payload, dict):
        raise RuntimeError("expected JSON object payload")
    return payload


def _runtime_profiles_from_config(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    daedalus_cfg = config.get("daedalus") or {}
    raw_profiles = config.get("runtimes") or (
        daedalus_cfg.get("runtimes") if isinstance(daedalus_cfg, dict) else {}
    ) or {}
    profiles = {
        str(name): dict(profile_cfg or {})
        for name, profile_cfg in raw_profiles.items()
    }
    codex_cfg = dict(config.get("codex") or {})
    agent_cfg = config.get("agent") or {}
    runtime_name = str(agent_cfg.get("runtime") or "").strip()

    for profile in profiles.values():
        if str(profile.get("kind") or "").strip() != "codex-app-server":
            continue
        for key in (
            "command",
            "mode",
            "endpoint",
            "healthcheck_path",
            "ws_token_env",
            "ws_token_file",
            "ephemeral",
            "approval_policy",
            "thread_sandbox",
            "turn_sandbox_policy",
            "turn_timeout_ms",
            "read_timeout_ms",
            "stall_timeout_ms",
        ):
            if profile.get(key) in (None, "", []):
                value = codex_cfg.get(key)
                if value not in (None, "", []):
                    profile[key] = value

    if not runtime_name and codex_cfg.get("command"):
        profiles.setdefault(
            "codex",
            {
                "kind": "codex-app-server",
                **{
                    key: value
                    for key, value in codex_cfg.items()
                    if value not in (None, "", [])
                },
            },
        )
    return profiles


def _build_runtimes_from_config(
    config: dict[str, Any],
    *,
    run: Callable[..., Any],
    run_json: Callable[..., dict[str, Any]],
) -> dict[str, Runtime]:
    return build_runtimes(_runtime_profiles_from_config(config), run=run, run_json=run_json)


def _scheduler_state_from_config(config: dict[str, Any]) -> dict[str, Any]:
    polling_cfg = config.get("polling") or {}
    agent_cfg = config.get("agent") or {}
    interval_ms = _cfg_value(polling_cfg, "interval_ms")
    if interval_ms in (None, ""):
        interval_ms = int(_cfg_value(polling_cfg, "interval_seconds", "interval-seconds", default=30) or 30) * 1000
    return {
        "poll_interval_ms": max(int(interval_ms or 30000), 1),
        "max_concurrent_agents": max(int(agent_cfg.get("max_concurrent_agents") or 10), 1),
        "max_concurrent_agents_by_state": {
            str(state).strip().lower(): int(limit)
            for state, limit in ((agent_cfg.get("max_concurrent_agents_by_state") or {}).items())
            if str(state).strip() and int(limit) > 0
        },
    }


@dataclass
class IssueRunnerWorkspace:
    path: Path
    config: dict[str, Any]
    snapshot_ref: AtomicRef[ConfigSnapshot]
    contract_path: Path
    tracker_source: str
    tracker_client: TrackerClient
    issue_workspace_root: Path
    status_path: Path
    health_path: Path
    audit_log_path: Path
    scheduler_path: Path
    prompt_template: str
    runtimes: dict[str, Runtime]
    _run: Callable[..., Any]
    _run_json: Callable[..., dict[str, Any]]
    retry_entries: dict[str, dict[str, Any]]
    running_entries: dict[str, dict[str, Any]]
    codex_threads: dict[str, dict[str, Any]]
    running_issue_id: str | None = None
    codex_totals: dict[str, Any] | None = None

    def runtime(self, name: str) -> Runtime:
        return self.runtimes[name]

    def _agent_runtime_name(self) -> str:
        agent_cfg = self.config.get("agent") or {}
        runtime_name = str(agent_cfg.get("runtime") or "").strip()
        if runtime_name:
            return runtime_name
        codex_cfg = self.config.get("codex") or {}
        if codex_cfg.get("command"):
            return "codex"
        raise RuntimeError("issue-runner requires agent.runtime, agent.command, or codex.command")

    def _agent_runtime(self) -> tuple[str, Runtime, dict[str, Any]]:
        runtime_name = self._agent_runtime_name()
        runtime_profiles = _runtime_profiles_from_config(self.config)
        runtime_cfg = runtime_profiles.get(runtime_name) or {}
        try:
            runtime = self.runtime(runtime_name)
        except KeyError as exc:
            raise RuntimeError(f"unknown runtime profile {runtime_name!r}") from exc
        return runtime_name, runtime, runtime_cfg

    def _poll_interval_seconds(self, override: int | None) -> int:
        if override is not None:
            return max(int(override), 1)
        polling_cfg = self.config.get("polling") or {}
        interval_ms = _cfg_value(polling_cfg, "interval_ms")
        if interval_ms not in (None, ""):
            return max(int(interval_ms) // 1000, 1)
        interval_seconds = _cfg_value(polling_cfg, "interval_seconds", "interval-seconds", default=30)
        return max(int(interval_seconds or 30), 1)

    def build_status(self) -> dict[str, Any]:
        snapshot = self.snapshot_ref.get()
        tracker_cfg = snapshot.config.get("tracker") or {}
        try:
            issues = self.tracker_client.list_all()
            eligible = eligible_issues(tracker_cfg=tracker_cfg, issues=issues)
            selected = eligible[0] if eligible else None
            eligible_count = len(eligible)
            health = "healthy"
            error = None
        except Exception as exc:
            issues = []
            selected = None
            eligible_count = 0
            health = "error"
            error = f"{type(exc).__name__}: {exc}"
        last_run = _load_optional_json(self.status_path)
        return {
            "workflow": "issue-runner",
            "source": "issue-runner",
            "workflowRoot": str(self.path),
            "contractPath": str(self.contract_path),
            "health": health,
            "error": error,
            "tracker": {
                "kind": tracker_cfg.get("kind"),
                "path": self.tracker_source,
                "issueCount": len(issues),
                "eligibleCount": eligible_count,
            },
            "scheduler": {
                **_scheduler_state_from_config(self.config),
                "running": self._running_snapshot(),
                "retry_queue": self._retry_queue_snapshot(),
                "codex_totals": dict(self.codex_totals or {}),
                "codex_threads": self._codex_threads_snapshot(),
            },
            "selectedIssue": selected,
            "workspaceRoot": str(self.issue_workspace_root),
            "lastRun": (last_run or {}).get("lastRun"),
            "metrics": (last_run or {}).get("metrics") or {},
            "updatedAt": _now_iso(),
        }

    def doctor(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        try:
            issues = self.tracker_client.list_all()
            checks.append({"name": "tracker", "status": "pass", "detail": f"{len(issues)} issue(s) loaded"})
        except Exception as exc:
            checks.append({"name": "tracker", "status": "fail", "detail": str(exc)})

        try:
            self.issue_workspace_root.mkdir(parents=True, exist_ok=True)
            checks.append({"name": "workspace-root", "status": "pass", "detail": str(self.issue_workspace_root)})
        except OSError as exc:
            checks.append({"name": "workspace-root", "status": "fail", "detail": str(exc)})

        try:
            runtime_name, _, _ = self._agent_runtime()
            checks.append({"name": "agent-runtime", "status": "pass", "detail": runtime_name})
        except Exception as exc:
            checks.append({"name": "agent-runtime", "status": "fail", "detail": str(exc)})

        ok = all(check["status"] == "pass" for check in checks)
        return {
            "ok": ok,
            "workflow": "issue-runner",
            "checks": checks,
            "updatedAt": _now_iso(),
        }

    def _load_scheduler_state(self) -> dict[str, Any]:
        return _load_optional_json(self.scheduler_path) or {}

    def _persist_scheduler_state(self) -> None:
        _write_json(
            self.scheduler_path,
            {
                "workflow": "issue-runner",
                "updatedAt": _now_iso(),
                "retry_queue": self._retry_queue_snapshot(),
                "running": self._running_snapshot(),
                "codex_totals": dict(self.codex_totals or {}),
                "codex_threads": self._codex_threads_snapshot(),
            },
        )

    def _restore_scheduler_state(self) -> None:
        payload = self._load_scheduler_state()
        retry_entries: dict[str, dict[str, Any]] = {}
        for item in payload.get("retry_queue") or payload.get("retryQueue") or []:
            if not isinstance(item, dict):
                continue
            issue_id = str(item.get("issue_id") or item.get("issueId") or "").strip()
            if not issue_id:
                continue
            retry_entries[issue_id] = {
                "issue_id": issue_id,
                "identifier": item.get("identifier"),
                "attempt": int(item.get("attempt") or 0),
                "error": item.get("error"),
                "due_at_epoch": float(item.get("due_at_epoch") or item.get("dueAtEpoch") or _now_epoch()),
                "current_attempt": item.get("current_attempt") or item.get("currentAttempt"),
            }

        running_entries: dict[str, dict[str, Any]] = {}
        recovered_running: list[dict[str, Any]] = []
        for item in payload.get("running") or []:
            if not isinstance(item, dict):
                continue
            issue_id = str(item.get("issue_id") or item.get("issueId") or "").strip()
            if not issue_id:
                continue
            entry = {
                "issue_id": issue_id,
                "identifier": item.get("identifier"),
                "attempt": int(item.get("attempt") or 0),
                "state": item.get("state"),
                "started_at_epoch": float(item.get("started_at_epoch") or item.get("startedAtEpoch") or _now_epoch()),
            }
            running_entries[issue_id] = entry
            recovered_running.append(entry)

        self.retry_entries = retry_entries
        self.running_entries = {}
        self.codex_totals = dict(payload.get("codex_totals") or payload.get("codexTotals") or {})
        self.codex_threads = self._restore_codex_threads(payload.get("codex_threads") or {})
        self.running_issue_id = None

        if recovered_running:
            now_epoch = _now_epoch()
            for entry in recovered_running:
                issue_id = str(entry.get("issue_id") or "")
                existing = self.retry_entries.get(issue_id) or {}
                self.retry_entries[issue_id] = {
                    "issue_id": issue_id,
                    "identifier": entry.get("identifier"),
                    "attempt": max(int(existing.get("attempt") or 0), int(entry.get("attempt") or 0), 1),
                    "error": "scheduler restarted while issue was running",
                    "due_at_epoch": now_epoch,
                    "current_attempt": entry.get("attempt"),
                }
            self._persist_scheduler_state()

    def _running_snapshot(self) -> list[dict[str, Any]]:
        now_epoch = _now_epoch()
        running = []
        for issue_id, entry in self.running_entries.items():
            started_at_epoch = float(entry.get("started_at_epoch") or now_epoch)
            running.append(
                {
                    "issue_id": issue_id,
                    "identifier": entry.get("identifier"),
                    "attempt": int(entry.get("attempt") or 0),
                    "state": entry.get("state"),
                    "started_at_epoch": started_at_epoch,
                    "running_for_ms": max(int((now_epoch - started_at_epoch) * 1000), 0),
                }
            )
        running.sort(key=lambda item: (item["state"] or "", item["identifier"] or item["issue_id"]))
        return running

    def _retry_queue_snapshot(self) -> list[dict[str, Any]]:
        now_epoch = _now_epoch()
        entries = []
        for issue_id, entry in self.retry_entries.items():
            due_at = _retry_due_at(entry, default=now_epoch)
            entries.append(
                {
                    "issue_id": issue_id,
                    "identifier": entry.get("identifier"),
                    "attempt": int(entry.get("attempt") or 0),
                    "error": entry.get("error"),
                    "due_at_epoch": due_at,
                    "due_in_ms": max(int((due_at - now_epoch) * 1000), 0),
                }
            )
        entries.sort(key=lambda item: (item["due_in_ms"], item["attempt"], item["identifier"] or item["issue_id"]))
        return entries

    def _restore_codex_threads(self, raw: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(raw, dict):
            return {}
        restored: dict[str, dict[str, Any]] = {}
        for issue_id, item in raw.items():
            if not isinstance(item, dict):
                continue
            normalized_issue_id = str(item.get("issue_id") or issue_id or "").strip()
            thread_id = str(item.get("thread_id") or "").strip()
            if not normalized_issue_id or not thread_id:
                continue
            restored[normalized_issue_id] = {
                "issue_id": normalized_issue_id,
                "identifier": item.get("identifier"),
                "session_name": item.get("session_name"),
                "thread_id": thread_id,
                "turn_id": item.get("turn_id"),
                "updated_at": item.get("updated_at"),
            }
        return restored

    def _codex_threads_snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            issue_id: dict(entry)
            for issue_id, entry in sorted(self.codex_threads.items(), key=lambda item: item[0])
        }

    def _codex_thread_for_issue(self, issue: dict[str, Any]) -> str | None:
        issue_id = str(issue.get("id") or "").strip()
        if not issue_id:
            return None
        entry = self.codex_threads.get(issue_id) or {}
        thread_id = str(entry.get("thread_id") or "").strip()
        return thread_id or None

    def _record_codex_thread(
        self,
        *,
        issue: dict[str, Any],
        session_name: str,
        metrics: dict[str, Any],
    ) -> None:
        issue_id = str(issue.get("id") or "").strip()
        thread_id = str(metrics.get("thread_id") or "").strip()
        if not issue_id or not thread_id:
            return
        self.codex_threads[issue_id] = {
            "issue_id": issue_id,
            "identifier": issue.get("identifier"),
            "session_name": session_name,
            "thread_id": thread_id,
            "turn_id": metrics.get("turn_id"),
            "updated_at": _now_iso(),
        }

    def _clear_codex_thread(self, issue_id: str | None) -> None:
        if issue_id:
            self.codex_threads.pop(issue_id, None)

    def _due_retry_issue(self, *, issues_by_id: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        now_epoch = _now_epoch()
        due_entries = sorted(
            self.retry_entries.items(),
            key=lambda item: (
                _retry_due_at(item[1], default=0.0),
                int((item[1] or {}).get("attempt") or 0),
                str((item[1] or {}).get("identifier") or item[0]),
            ),
        )
        for issue_id, entry in due_entries:
            if _retry_due_at(entry, default=0.0) > now_epoch:
                continue
            issue = issues_by_id.get(issue_id)
            if issue is None:
                self.retry_entries.pop(issue_id, None)
                continue
            return issue, entry
        return None, None

    def _schedule_retry(
        self,
        *,
        issue: dict[str, Any],
        error: str,
        current_attempt: int | None,
        delay_type: str = "failure",
    ) -> dict[str, Any]:
        max_backoff_ms = int((self.config.get("agent") or {}).get("max_retry_backoff_ms") or 300000)
        if delay_type == "continuation":
            retry_attempt = 1
            delay_ms = 1000
        else:
            retry_attempt = int((self.retry_entries.get(issue["id"]) or {}).get("attempt") or 0) + 1
            delay_ms = min(max_backoff_ms, 10000 * (2 ** max(retry_attempt - 1, 0)))
        due_at = _now_epoch() + (delay_ms / 1000.0)
        entry = {
            "issue_id": issue.get("id"),
            "identifier": issue.get("identifier"),
            "attempt": retry_attempt,
            "due_at_epoch": due_at,
            "error": error,
            "current_attempt": current_attempt,
            "delay_type": delay_type,
        }
        self.retry_entries[str(issue.get("id"))] = entry
        self._emit_event(
            "issue_runner.retry.scheduled",
            {
                "issue_id": issue.get("id"),
                "identifier": issue.get("identifier"),
                "retry_attempt": retry_attempt,
                "delay_ms": delay_ms,
                "delay_type": delay_type,
                "error": error,
            },
        )
        return {
            "issue_id": issue.get("id"),
            "identifier": issue.get("identifier"),
            "retry_attempt": retry_attempt,
            "delay_ms": delay_ms,
            "delay_type": delay_type,
        }

    def _clear_retry(self, issue_id: str | None) -> None:
        if issue_id:
            self.retry_entries.pop(issue_id, None)

    def _record_metrics(self, result: PromptRunResult) -> dict[str, Any]:
        metrics = self._metrics_payload(result)
        totals = dict(self.codex_totals or {})
        tokens = metrics.get("tokens") or {}
        totals["input_tokens"] = int(totals.get("input_tokens") or 0) + int(tokens.get("input_tokens") or 0)
        totals["output_tokens"] = int(totals.get("output_tokens") or 0) + int(tokens.get("output_tokens") or 0)
        totals["total_tokens"] = int(totals.get("total_tokens") or 0) + int(tokens.get("total_tokens") or 0)
        totals["turn_count"] = int(totals.get("turn_count") or 0) + int(metrics.get("turn_count") or 0)
        if metrics.get("rate_limits") is not None:
            totals["rate_limits"] = metrics.get("rate_limits")
        self.codex_totals = totals
        return metrics

    def _dispatch_slots(self) -> int:
        scheduler = _scheduler_state_from_config(self.config)
        running_count = len(self.running_entries)
        return max(int(scheduler["max_concurrent_agents"]) - running_count, 0)

    def _select_issue_batch(
        self,
        *,
        issues: list[dict[str, Any]],
        issues_by_id: dict[str, dict[str, Any]],
    ) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
        slots = self._dispatch_slots()
        if slots <= 0:
            return []

        tracker_cfg = self.config.get("tracker") or {}
        scheduler = _scheduler_state_from_config(self.config)
        state_limits = dict(scheduler["max_concurrent_agents_by_state"])
        pending_retry_ids = {
            issue_id
            for issue_id, entry in self.retry_entries.items()
            if _retry_due_at(entry, default=0.0) > _now_epoch()
        }
        running_ids = set(self.running_entries)
        selected: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        selected_ids: set[str] = set()
        state_counts: dict[str, int] = {}

        def _can_take(issue: dict[str, Any]) -> bool:
            issue_id = str(issue.get("id") or "").strip()
            if not issue_id or issue_id in selected_ids or issue_id in running_ids:
                return False
            if issue_id in pending_retry_ids:
                return False
            state = str(issue.get("state") or "").strip().lower()
            limit = state_limits.get(state)
            if limit is not None and state_counts.get(state, 0) >= limit:
                return False
            return True

        due_retry_entries = sorted(
            [
                (issue_id, entry)
                for issue_id, entry in self.retry_entries.items()
                if _retry_due_at(entry, default=0.0) <= _now_epoch()
            ],
            key=lambda item: (
                _retry_due_at(item[1], default=0.0),
                int((item[1] or {}).get("attempt") or 0),
                str((item[1] or {}).get("identifier") or item[0]),
            ),
        )
        for issue_id, retry_entry in due_retry_entries:
            if len(selected) >= slots:
                break
            issue = issues_by_id.get(issue_id)
            if issue is None:
                self.retry_entries.pop(issue_id, None)
                continue
            if not _can_take(issue):
                continue
            selected.append((issue, retry_entry))
            selected_ids.add(issue_id)
            state = str(issue.get("state") or "").strip().lower()
            state_counts[state] = state_counts.get(state, 0) + 1

        if len(selected) >= slots:
            return selected

        for issue in eligible_issues(tracker_cfg=tracker_cfg, issues=issues):
            if len(selected) >= slots:
                break
            if not _can_take(issue):
                continue
            issue_id = str(issue.get("id") or "").strip()
            selected.append((issue, self.retry_entries.get(issue_id)))
            selected_ids.add(issue_id)
            state = str(issue.get("state") or "").strip().lower()
            state_counts[state] = state_counts.get(state, 0) + 1
        return selected

    def _issue_attempt(self, *, issue: dict[str, Any], retry_entry: dict[str, Any] | None) -> int:
        if retry_entry is not None:
            return int(retry_entry.get("attempt") or 0) + 1
        last_run = (_load_optional_json(self.status_path) or {}).get("lastRun") or {}
        if (last_run.get("issue") or {}).get("id") == issue.get("id"):
            return int(last_run.get("attempt") or 0) + 1
        return 1

    def _mark_running(self, selections: list[tuple[dict[str, Any], dict[str, Any] | None]]) -> None:
        now_epoch = _now_epoch()
        self.running_entries = {
            str(issue.get("id") or ""): {
                "issue_id": str(issue.get("id") or ""),
                "identifier": issue.get("identifier"),
                "attempt": self._issue_attempt(issue=issue, retry_entry=retry_entry),
                "state": issue.get("state"),
                "started_at_epoch": now_epoch,
            }
            for issue, retry_entry in selections
            if str(issue.get("id") or "").strip()
        }
        self.running_issue_id = next(iter(self.running_entries), None)
        self._persist_scheduler_state()

    def _clear_running(self, issue_ids: list[str]) -> None:
        for issue_id in issue_ids:
            self.running_entries.pop(issue_id, None)
        self.running_issue_id = next(iter(self.running_entries), None)

    def _metrics_from_exception(self, exc: Exception, runtime: Runtime | None = None) -> dict[str, Any]:
        result = getattr(exc, "result", None)
        if result is None and runtime is not None:
            last_result = getattr(runtime, "last_result", None)
            if callable(last_result):
                result = last_result()
        if isinstance(result, PromptRunResult):
            return self._metrics_payload(result)
        return {}

    def _execute_issue(
        self,
        *,
        issue: dict[str, Any],
        retry_entry: dict[str, Any] | None,
    ) -> dict[str, Any]:
        hook_results: list[dict[str, Any]] = []
        runtime: Runtime | None = None
        runtime_name: str | None = None
        runtime_cfg: dict[str, Any] = {}
        issue_workspace: Path | None = None
        output_path: Path | None = None
        env: dict[str, str] | None = None
        created_workspace = False
        attempt = self._issue_attempt(issue=issue, retry_entry=retry_entry)

        try:
            issue_workspace = _safe_issue_workspace_path(self.issue_workspace_root, issue)
            issue_workspace.mkdir(parents=True, exist_ok=True)
            _assert_workspace_inside_root(self.issue_workspace_root, issue_workspace)
            daemon_dir = issue_workspace / ".daedalus"
            daemon_dir.mkdir(parents=True, exist_ok=True)
            created_marker = daemon_dir / "created.marker"
            created_workspace = not created_marker.exists()

            prompt = _render_prompt(
                prompt_template=self.prompt_template or str(self.config.get(WORKFLOW_POLICY_KEY) or ""),
                issue=issue,
                attempt=None if attempt <= 1 else attempt,
            )
            prompt_path = daemon_dir / "prompt.txt"
            prompt_path.write_text(prompt, encoding="utf-8")
            output_path = daemon_dir / "last-output.txt"
            env = self._hook_env(issue=issue, issue_workspace=issue_workspace, prompt_path=prompt_path, output_path=output_path)
            if created_workspace:
                hook_results.append(self._run_hook("after_create", issue_workspace, env))
                created_marker.write_text(_now_iso() + "\n", encoding="utf-8")
            hook_results.append(self._run_hook("before_run", issue_workspace, env))

            agent_cfg = self.config.get("agent") or {}
            runtime_name = self._agent_runtime_name()
            runtime_profiles = _runtime_profiles_from_config(self.config)
            runtime_cfg = runtime_profiles.get(runtime_name) or {}
            runtime = _build_runtimes_from_config(self.config, run=self._run, run_json=self._run_json)[runtime_name]
            session_name = issue_session_name(issue)
            model = str(agent_cfg.get("model") or "")
            resume_thread_id = None
            if str(runtime_cfg.get("kind") or "").strip() == "codex-app-server":
                resume_thread_id = self._codex_thread_for_issue(issue)
            runtime.ensure_session(
                worktree=issue_workspace,
                session_name=session_name,
                model=model,
                resume_session_id=resume_thread_id,
            )

            command = agent_cfg.get("command")
            if command is None:
                runtime_command = runtime_cfg.get("command")
                if isinstance(runtime_command, list):
                    command = runtime_command
            if command:
                argv = self._render_command(
                    command=command,
                    worktree=issue_workspace,
                    model=model,
                    session_name=session_name,
                    prompt_path=prompt_path,
                    issue=issue,
                )
                output = runtime.run_command(worktree=issue_workspace, command_argv=argv, env=env)
                run_result = self._runtime_result_from_command_output(output)
            else:
                run_result = self._run_runtime_prompt(
                    runtime=runtime,
                    worktree=issue_workspace,
                    session_name=session_name,
                    prompt=prompt,
                    model=model,
                )
                output = run_result.output
            output_path.write_text(output, encoding="utf-8")
            hook_results.append(self._run_hook("after_run", issue_workspace, env))
            return {
                "ok": True,
                "issue": issue,
                "attempt": attempt,
                "workspace": str(issue_workspace),
                "createdWorkspace": created_workspace,
                "hookResults": hook_results,
                "outputPath": str(output_path),
                "metrics": self._metrics_payload(run_result),
                "runtime": runtime_name,
                "runtimeKind": runtime_cfg.get("kind"),
            }
        except Exception as exc:
            if issue_workspace is not None and env is not None:
                hook_results.append(self._run_hook("after_run", issue_workspace, env, ignore_failure=True))
            return {
                "ok": False,
                "issue": issue,
                "attempt": attempt,
                "workspace": str(issue_workspace) if issue_workspace is not None else None,
                "createdWorkspace": created_workspace,
                "hookResults": hook_results,
                "outputPath": str(output_path) if output_path is not None else None,
                "error": f"{type(exc).__name__}: {exc}",
                "metrics": self._metrics_from_exception(exc, runtime=runtime),
                "runtime": runtime_name if runtime is not None else None,
                "runtimeKind": runtime_cfg.get("kind") if runtime is not None else None,
            }

    def tick(self) -> dict[str, Any]:
        status = {
            "ok": False,
            "workflow": "issue-runner",
            "updatedAt": _now_iso(),
            "selectedIssue": None,
            "selectedIssues": [],
            "attempt": None,
            "outputPath": None,
            "metrics": {},
        }
        tracker_cfg = self.config.get("tracker") or {}
        try:
            issues = self.tracker_client.list_all()
            cleanup = self._cleanup_terminal_workspaces(self.tracker_client.list_terminal())
        except Exception as exc:
            status["error"] = f"{type(exc).__name__}: {exc}"
            self._write_status(status, health="error")
            self._emit_event(
                "issue_runner.tick.failed",
                {
                    "error": status["error"],
                    "reason": "tracker-load-failed",
                },
            )
            return status
        issues_by_id = {str(issue.get("id")): issue for issue in issues if str(issue.get("id") or "").strip()}
        selections = self._select_issue_batch(issues=issues, issues_by_id=issues_by_id)
        refreshed_selections: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        for selected, retry_entry in selections:
            issue_id = str(selected.get("id") or "").strip()
            if issue_id:
                selected = self.tracker_client.refresh([issue_id]).get(issue_id, selected)
            refreshed_selections.append((selected, retry_entry))
        selections = refreshed_selections
        status["selectedIssues"] = [issue for issue, _retry_entry in selections]
        status["selectedIssue"] = selections[0][0] if selections else None
        status["cleanup"] = cleanup
        if not selections:
            status["ok"] = True
            status["message"] = "no dispatchable issues"
            self._write_status(status, health="healthy")
            self._persist_scheduler_state()
            self._emit_event("issue_runner.tick.noop", {"reason": "no-dispatchable-issues"})
            return status

        self._mark_running(selections)
        results: list[dict[str, Any]] = []
        tick_metrics: list[dict[str, Any]] = []
        try:
            if len(selections) == 1:
                issue, retry_entry = selections[0]
                results = [self._execute_issue(issue=issue, retry_entry=retry_entry)]
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(selections)) as executor:
                    future_map = {
                        executor.submit(self._execute_issue, issue=issue, retry_entry=retry_entry): (issue, retry_entry)
                        for issue, retry_entry in selections
                    }
                    for future in concurrent.futures.as_completed(future_map):
                        results.append(future.result())

            results.sort(key=lambda item: str(((item.get("issue") or {}).get("identifier")) or ((item.get("issue") or {}).get("id")) or ""))
            for result in results:
                issue = result.get("issue") or {}
                issue_id = str(issue.get("id") or "")
                metrics = result.get("metrics") or {}
                if metrics:
                    recorded_metrics = self._record_metrics(
                        PromptRunResult(
                            output="",
                            session_id=metrics.get("session_id"),
                            thread_id=metrics.get("thread_id"),
                            turn_id=metrics.get("turn_id"),
                            last_event=metrics.get("last_event"),
                            last_message=metrics.get("last_message"),
                            turn_count=int(metrics.get("turn_count") or 0),
                            tokens=metrics.get("tokens"),
                            rate_limits=metrics.get("rate_limits"),
                        )
                    )
                    if result.get("runtimeKind") == "codex-app-server":
                        self._record_codex_thread(
                            issue=issue,
                            session_name=issue_session_name(issue),
                            metrics=recorded_metrics,
                        )
                    tick_metrics.append(recorded_metrics)
                if result.get("ok"):
                    self._clear_retry(issue_id)
                    retry = self._schedule_retry(
                        issue=issue,
                        error="continuation",
                        current_attempt=result.get("attempt"),
                        delay_type="continuation",
                    )
                    result["retry"] = retry
                    self._emit_event(
                        "issue_runner.tick.completed",
                        {
                            "issue_id": issue.get("id"),
                            "attempt": result.get("attempt"),
                            "workspace": result.get("workspace"),
                            "output_path": result.get("outputPath"),
                            "continuation_retry_attempt": retry.get("retry_attempt"),
                            "continuation_retry_delay_ms": retry.get("delay_ms"),
                        },
                    )
                else:
                    retry = self._schedule_retry(
                        issue=issue,
                        error=str(result.get("error") or "issue execution failed"),
                        current_attempt=result.get("attempt"),
                    )
                    result["retry"] = retry
                    self._emit_event(
                        "issue_runner.tick.failed",
                        {
                            "issue_id": issue.get("id"),
                            "attempt": result.get("attempt"),
                            "workspace": result.get("workspace"),
                            "error": result.get("error"),
                            "retry_attempt": retry.get("retry_attempt"),
                            "retry_delay_ms": retry.get("delay_ms"),
                        },
                    )

            first = results[0]
            status.update(
                {
                    "ok": all(result.get("ok") for result in results),
                    "attempt": first.get("attempt"),
                    "outputPath": first.get("outputPath"),
                    "workspace": first.get("workspace"),
                    "createdWorkspace": first.get("createdWorkspace"),
                    "hookResults": first.get("hookResults"),
                    "results": results,
                    "metrics": self._aggregate_metrics(tick_metrics),
                }
            )
            failed = next((result for result in results if not result.get("ok")), None)
            if failed:
                status["error"] = failed.get("error")
                status["retry"] = failed.get("retry")
            self._write_status(status, health="healthy" if status["ok"] else "error")
            return status
        finally:
            self._clear_running([str(issue.get("id") or "") for issue, _retry_entry in selections])
            self._persist_scheduler_state()

    def _cleanup_terminal_workspaces(self, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tracker_cfg = self.config.get("tracker") or {}
        terminal_states = {
            str(value).strip().lower()
            for value in (
                _cfg_value(tracker_cfg, "terminal_states", "terminal-states")
                or ["done", "closed", "canceled", "cancelled", "resolved"]
            )
            if str(value).strip()
        }
        cleaned: list[dict[str, Any]] = []
        for issue in issues:
            state = str(issue.get("state") or "").strip().lower()
            if state not in terminal_states:
                continue
            issue_id = str(issue.get("id") or "")
            self._clear_retry(issue_id)
            self._clear_codex_thread(issue_id)
            issue_workspace = _safe_issue_workspace_path(self.issue_workspace_root, issue)
            if not issue_workspace.exists():
                continue
            _assert_workspace_inside_root(self.issue_workspace_root, issue_workspace)
            daemon_dir = issue_workspace / ".daedalus"
            env = self._hook_env(
                issue=issue,
                issue_workspace=issue_workspace,
                prompt_path=daemon_dir / "prompt.txt",
                output_path=daemon_dir / "last-output.txt",
            )
            hook_result = self._run_hook("before_remove", issue_workspace, env, ignore_failure=True)
            shutil.rmtree(issue_workspace, ignore_errors=True)
            cleaned.append(
                {
                    "issue_id": issue.get("id"),
                    "identifier": issue.get("identifier"),
                    "workspace": str(issue_workspace),
                    "hook": hook_result,
                }
            )
            self._emit_event(
                "issue_runner.workspace.cleaned",
                {
                    "issue_id": issue.get("id"),
                    "identifier": issue.get("identifier"),
                    "workspace": str(issue_workspace),
                },
            )
        return cleaned

    def _write_status(self, tick_result: dict[str, Any], *, health: str) -> None:
        payload = {
            "workflow": "issue-runner",
            "health": health,
            "lastRun": {
                "ok": tick_result.get("ok"),
                "issue": tick_result.get("selectedIssue"),
                "issues": tick_result.get("selectedIssues") or ([tick_result.get("selectedIssue")] if tick_result.get("selectedIssue") else []),
                "attempt": tick_result.get("attempt"),
                "outputPath": tick_result.get("outputPath"),
                "results": tick_result.get("results") or [],
                "updatedAt": tick_result.get("updatedAt") or _now_iso(),
            },
            "metrics": tick_result.get("metrics") or {},
        }
        _write_json(self.status_path, payload)
        _write_json(
            self.health_path,
            {
                "workflow": "issue-runner",
                "health": health,
                "updatedAt": payload["lastRun"]["updatedAt"],
            },
        )

    def _emit_event(self, event: str, payload: dict[str, Any]) -> None:
        _append_jsonl(
            self.audit_log_path,
            {"event": event, "at": _now_iso(), **payload},
        )

    def _hook_env(
        self,
        *,
        issue: dict[str, Any],
        issue_workspace: Path,
        prompt_path: Path,
        output_path: Path,
    ) -> dict[str, str]:
        repository_cfg = self.config.get("repository") or {}
        return {
            "WORKFLOW_ROOT": str(self.path),
            "ISSUE_ID": str(issue.get("id") or ""),
            "ISSUE_IDENTIFIER": str(issue.get("identifier") or ""),
            "ISSUE_TITLE": str(issue.get("title") or ""),
            "ISSUE_STATE": str(issue.get("state") or ""),
            "ISSUE_LABELS": ",".join(issue.get("labels") or []),
            "ISSUE_WORKSPACE": str(issue_workspace),
            "PROMPT_PATH": str(prompt_path),
            "OUTPUT_PATH": str(output_path),
            "REPOSITORY_PATH": str(repository_cfg.get("local-path") or ""),
        }

    def _run_hook(
        self,
        hook_name: str,
        worktree: Path,
        env: dict[str, str],
        *,
        ignore_failure: bool = False,
    ) -> dict[str, Any]:
        hooks_cfg = self.config.get("hooks") or {}
        script = str(_cfg_value(hooks_cfg, hook_name, hook_name.replace("_", "-")) or "").strip()
        if not script:
            return {"hook": hook_name, "ran": False}
        timeout_ms = int(_cfg_value(hooks_cfg, "timeout_ms", "timeout-seconds", default=60000) or 60000)
        timeout = max(timeout_ms // 1000, 1)
        try:
            completed = self._run(["bash", "-lc", script], cwd=worktree, timeout=timeout, env=env)
        except Exception as exc:
            if not ignore_failure:
                raise
            return {
                "hook": hook_name,
                "ran": True,
                "returncode": None,
                "ignored_failure": True,
                "error": str(exc),
            }
        return {
            "hook": hook_name,
            "ran": True,
            "returncode": getattr(completed, "returncode", 0),
        }

    def _render_command(
        self,
        *,
        command: Any,
        worktree: Path,
        model: str,
        session_name: str,
        prompt_path: Path,
        issue: dict[str, Any],
    ) -> list[str]:
        if not isinstance(command, list) or not command:
            raise RuntimeError("agent.command and runtime command must be a non-empty argv list")
        fmt = {
            "worktree": str(worktree),
            "model": model,
            "session_name": session_name,
            "prompt_path": str(prompt_path),
            "issue_id": str(issue.get("id") or ""),
            "issue_identifier": str(issue.get("identifier") or ""),
            "issue_title": str(issue.get("title") or ""),
            "workflow_root": str(self.path),
        }
        return [str(part).format(**fmt) for part in command]

    def _run_runtime_prompt(
        self,
        *,
        runtime: Runtime,
        worktree: Path,
        session_name: str,
        prompt: str,
        model: str,
    ) -> PromptRunResult:
        runner = getattr(runtime, "run_prompt_result", None)
        if callable(runner):
            return runner(
                worktree=worktree,
                session_name=session_name,
                prompt=prompt,
                model=model,
            )
        output = runtime.run_prompt(
            worktree=worktree,
            session_name=session_name,
            prompt=prompt,
            model=model,
        )
        return self._runtime_result_from_command_output(output)

    def _runtime_result_from_command_output(self, output: str) -> PromptRunResult:
        return PromptRunResult(
            output=output,
            tokens={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            rate_limits=None,
        )

    def _metrics_payload(self, result: PromptRunResult) -> dict[str, Any]:
        return {
            "session_id": result.session_id,
            "thread_id": result.thread_id,
            "turn_id": result.turn_id,
            "last_event": result.last_event,
            "last_message": result.last_message,
            "turn_count": result.turn_count,
            "tokens": result.tokens or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "rate_limits": result.rate_limits,
        }

    def _aggregate_metrics(self, metrics_list: list[dict[str, Any]]) -> dict[str, Any]:
        if not metrics_list:
            return {}
        aggregate = {
            "session_id": metrics_list[0].get("session_id") if len(metrics_list) == 1 else None,
            "thread_id": metrics_list[0].get("thread_id") if len(metrics_list) == 1 else None,
            "turn_id": metrics_list[-1].get("turn_id"),
            "last_event": metrics_list[-1].get("last_event"),
            "last_message": metrics_list[-1].get("last_message"),
            "turn_count": 0,
            "tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "rate_limits": None,
        }
        for metrics in metrics_list:
            tokens = metrics.get("tokens") or {}
            aggregate["turn_count"] += int(metrics.get("turn_count") or 0)
            aggregate["tokens"]["input_tokens"] += int(tokens.get("input_tokens") or 0)
            aggregate["tokens"]["output_tokens"] += int(tokens.get("output_tokens") or 0)
            aggregate["tokens"]["total_tokens"] += int(tokens.get("total_tokens") or 0)
            if metrics.get("rate_limits") is not None:
                aggregate["rate_limits"] = metrics.get("rate_limits")
        return aggregate

    def run_loop(
        self,
        *,
        interval_seconds: int | None,
        max_iterations: int | None = None,
        sleep_fn=time.sleep,
    ) -> dict[str, Any]:
        iterations = 0
        last_result = None
        try:
            while True:
                self.reload_contract()
                last_result = self.tick()
                iterations += 1
                if max_iterations is not None and iterations >= max_iterations:
                    break
                sleep_fn(self._poll_interval_seconds(interval_seconds))
        except KeyboardInterrupt:
            return {
                "loop_status": "interrupted",
                "iterations": iterations,
                "last_result": last_result,
            }
        return {
            "loop_status": "completed",
            "iterations": iterations,
            "last_result": last_result,
        }

    def reload_contract(self) -> None:
        try:
            contract = load_workflow_contract(self.path)
            _validate_issue_runner_config(dict(contract.config))
        except (
            FileNotFoundError,
            WorkflowContractError,
            JsonSchemaValidationError,
            OSError,
            UnicodeDecodeError,
            yaml.YAMLError,
        ) as exc:
            self._emit_event(
                "daedalus.config_reload_failed",
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "contract_path": str(self.contract_path),
                },
            )
            return
        st = contract.source_path.stat()
        current = self.snapshot_ref.get()
        key = (st.st_mtime, st.st_size, str(contract.source_path))
        current_key = (current.source_mtime, current.source_size, str(self.contract_path))
        if key == current_key:
            return
        cfg = dict(contract.config)
        prompts = cfg.get("prompts") or {}
        snapshot = ConfigSnapshot(
            config=cfg,
            prompts=prompts,
            loaded_at=time.monotonic(),
            source_mtime=st.st_mtime,
            source_size=st.st_size,
        )
        self.config = cfg
        self.contract_path = contract.source_path
        self.prompt_template = contract.prompt_template
        self.snapshot_ref.set(snapshot)
        tracker_cfg = cfg.get("tracker") or {}
        workspace_cfg = cfg.get("workspace") or {}
        storage_cfg = cfg.get("storage") or {}
        repo_path = _repository_path_from_config(self.path, cfg)
        tracker_source_cfg = dict(tracker_cfg)
        if repo_path is not None and str(tracker_cfg.get("kind") or "").strip() == "github":
            tracker_source_cfg.setdefault("repo_path", str(repo_path))
        self.tracker_source = describe_tracker_source(workflow_root=self.path, tracker_cfg=tracker_source_cfg)
        self.tracker_client = build_tracker_client(
            workflow_root=self.path,
            tracker_cfg=tracker_cfg,
            repo_path=repo_path,
            run_json=self._run_json,
        )
        previous_scheduler_path = self.scheduler_path

        def _resolve_path(value: str, default: str) -> Path:
            raw = str(value or default).strip()
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = (self.path / path).resolve()
            return path

        self.issue_workspace_root = _resolve_path(_cfg_value(workspace_cfg, "root", default="workspace/issues"), "workspace/issues")
        self.status_path = _resolve_path(storage_cfg.get("status") or "memory/workflow-status.json", "memory/workflow-status.json")
        self.health_path = _resolve_path(storage_cfg.get("health") or "memory/workflow-health.json", "memory/workflow-health.json")
        self.audit_log_path = _resolve_path(storage_cfg.get("audit-log") or "memory/workflow-audit.jsonl", "memory/workflow-audit.jsonl")
        self.scheduler_path = _resolve_path(storage_cfg.get("scheduler") or "memory/workflow-scheduler.json", "memory/workflow-scheduler.json")
        self.runtimes = _build_runtimes_from_config(cfg, run=self._run, run_json=self._run_json)
        if self.scheduler_path != previous_scheduler_path:
            self._restore_scheduler_state()


def load_workspace_from_config(
    *,
    workspace_root: Path,
    config: dict[str, Any] | None = None,
    run: Callable[..., Any] | None = None,
    run_json: Callable[..., dict[str, Any]] | None = None,
) -> IssueRunnerWorkspace:
    root = workspace_root.expanduser().resolve()
    contract = load_workflow_contract(root)
    cfg = dict(config or contract.config)
    prompts = cfg.get("prompts") or {}
    st = contract.source_path.stat()
    snapshot = ConfigSnapshot(
        config=cfg,
        prompts=prompts,
        loaded_at=time.monotonic(),
        source_mtime=st.st_mtime,
        source_size=st.st_size,
    )
    tracker_cfg = cfg.get("tracker") or {}
    workspace_cfg = cfg.get("workspace") or {}
    storage_cfg = cfg.get("storage") or {}
    repo_path = _repository_path_from_config(root, cfg)
    tracker_source_cfg = dict(tracker_cfg)
    if repo_path is not None and str(tracker_cfg.get("kind") or "").strip() == "github":
        tracker_source_cfg.setdefault("repo_path", str(repo_path))

    def _resolve_path(value: str, default: str) -> Path:
        raw = str(value or default).strip()
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (root / path).resolve()
        return path

    tracker_source = describe_tracker_source(workflow_root=root, tracker_cfg=tracker_source_cfg)
    tracker_client = build_tracker_client(
        workflow_root=root,
        tracker_cfg=tracker_cfg,
        repo_path=repo_path,
        run_json=run_json or _subprocess_run_json,
    )
    issue_workspace_root = _resolve_path(_cfg_value(workspace_cfg, "root", default="workspace/issues"), "workspace/issues")
    status_path = _resolve_path(storage_cfg.get("status") or "memory/workflow-status.json", "memory/workflow-status.json")
    health_path = _resolve_path(storage_cfg.get("health") or "memory/workflow-health.json", "memory/workflow-health.json")
    audit_log_path = _resolve_path(storage_cfg.get("audit-log") or "memory/workflow-audit.jsonl", "memory/workflow-audit.jsonl")
    scheduler_path = _resolve_path(storage_cfg.get("scheduler") or "memory/workflow-scheduler.json", "memory/workflow-scheduler.json")

    runner = run or _subprocess_run
    runner_json = run_json or _subprocess_run_json
    runtimes = _build_runtimes_from_config(cfg, run=runner, run_json=runner_json)

    workspace = IssueRunnerWorkspace(
        path=root,
        config=cfg,
        snapshot_ref=AtomicRef(snapshot),
        contract_path=contract.source_path,
        tracker_source=tracker_source,
        tracker_client=tracker_client,
        issue_workspace_root=issue_workspace_root,
        status_path=status_path,
        health_path=health_path,
        audit_log_path=audit_log_path,
        scheduler_path=scheduler_path,
        prompt_template=contract.prompt_template,
        runtimes=runtimes,
        _run=runner,
        _run_json=runner_json,
        retry_entries={},
        running_entries={},
        codex_threads={},
        codex_totals={},
    )
    workspace._restore_scheduler_state()
    return workspace


def make_workspace(*, workflow_root: Path, config: dict) -> IssueRunnerWorkspace:
    return load_workspace_from_config(workspace_root=workflow_root, config=config)
