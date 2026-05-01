from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from . import (
    DEFAULT_TERMINAL_STATES,
    TrackerConfigError,
    cfg_list,
    normalize_issue,
    register,
    resolve_tracker_path,
)


_WRITE_LOCK = threading.Lock()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_issue_payload(path: Path) -> tuple[Any, list[Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        raw_issues = payload.get("issues")
    else:
        raw_issues = payload
    if not isinstance(raw_issues, list):
        raise TrackerConfigError(f"{path} must contain a top-level list or an object with an 'issues' list")
    return payload, raw_issues


def _write_issue_payload(path: Path, payload: Any) -> None:
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp_path.replace(path)


@register("local-json")
class LocalJsonTrackerClient:
    kind = "local-json"

    def __init__(self, *, workflow_root: Path, tracker_cfg: dict[str, object]):
        self._workflow_root = workflow_root
        self._tracker_cfg = tracker_cfg

    def list_all(self) -> list[dict[str, object]]:
        path = resolve_tracker_path(workflow_root=self._workflow_root, tracker_cfg=self._tracker_cfg)
        _payload, raw_issues = _read_issue_payload(path)
        return [normalize_issue(item) for item in raw_issues]

    def list_candidates(self) -> list[dict[str, object]]:
        from workflows.issue_runner.tracker import eligible_issues

        return eligible_issues(tracker_cfg=self._tracker_cfg, issues=self.list_all())

    def refresh(self, issue_ids: list[str]) -> dict[str, dict[str, object]]:
        ids = {str(issue_id).strip() for issue_id in issue_ids if str(issue_id).strip()}
        if not ids:
            return {}
        return {
            str(issue["id"]): issue
            for issue in self.list_all()
            if issue.get("id") in ids
        }

    def list_terminal(self) -> list[dict[str, object]]:
        terminal_states = {
            str(value).strip().lower()
            for value in (cfg_list(self._tracker_cfg, "terminal_states", "terminal-states") or DEFAULT_TERMINAL_STATES)
            if str(value).strip()
        }
        return [
            issue
            for issue in self.list_all()
            if str(issue.get("state") or "").strip().lower() in terminal_states
        ]

    def post_feedback(
        self,
        *,
        issue_id: str,
        event: str,
        body: str,
        summary: str,
        state: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = resolve_tracker_path(workflow_root=self._workflow_root, tracker_cfg=self._tracker_cfg)
        target_id = str(issue_id or "").strip()
        if not target_id:
            raise TrackerConfigError("issue_id is required when posting local-json feedback")

        with _WRITE_LOCK:
            payload, raw_issues = _read_issue_payload(path)
            for raw_issue in raw_issues:
                if not isinstance(raw_issue, dict):
                    continue
                if str(raw_issue.get("id") or "").strip() != target_id:
                    continue
                now_iso = _now_iso()
                comments = raw_issue.get("comments")
                if not isinstance(comments, list):
                    comments = []
                comment = {
                    "at": now_iso,
                    "event": event,
                    "summary": summary,
                    "body": body.rstrip(),
                    "metadata": metadata or {},
                }
                if state:
                    raw_issue["state"] = state
                    comment["state"] = state
                comments.append(comment)
                raw_issue["comments"] = comments
                raw_issue["updated_at"] = now_iso
                _write_issue_payload(path, payload)
                return {
                    "ok": True,
                    "kind": self.kind,
                    "issue_id": target_id,
                    "event": event,
                    "state": state,
                    "comment_count": len(comments),
                }

        raise TrackerConfigError(f"local-json issue {target_id!r} not found in {path}")
