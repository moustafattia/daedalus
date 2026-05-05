from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from sprints.core.config import WorkflowConfig
from sprints.core.contracts import (
    DEFAULT_WORKFLOW_MARKDOWN_FILENAME,
    WorkflowContractError,
    load_workflow_contract,
    load_workflow_contract_file,
    snapshot_workflow_contract,
    workflow_named_markdown_filename,
)


class WorkflowContractApplyError(RuntimeError):
    pass


def apply_workflow_contract(
    *,
    workflow_root: Path,
    source_ref: str = "origin/main",
    force: bool = False,
) -> dict[str, Any]:
    root = Path(workflow_root).expanduser().resolve()
    current = load_workflow_contract(root)
    config = WorkflowConfig.from_raw(raw=current.config, workflow_root=root)
    active_lanes = _active_lanes(config.storage.state_path)
    if active_lanes and not force:
        raise WorkflowContractApplyError(
            "cannot apply workflow contract while lanes are active: "
            + ", ".join(active_lanes)
            + " (pass --force to override)"
        )
    repo_path = _repo_path(config)
    _git("fetch", "origin", cwd=repo_path)
    source_commit = _git("rev-parse", source_ref, cwd=repo_path).strip()
    contract_filename, text = _repo_contract_text(
        repo_path=repo_path,
        source_ref=source_ref,
        workflow_name=config.workflow_name,
    )
    incoming_path = root / "config" / f"incoming-{contract_filename}"
    incoming_path.parent.mkdir(parents=True, exist_ok=True)
    incoming_path.write_text(text, encoding="utf-8")
    try:
        incoming = load_workflow_contract_file(incoming_path)
        incoming_workflow = str(incoming.config.get("workflow") or "").strip()
        if incoming_workflow != config.workflow_name:
            raise WorkflowContractApplyError(
                f"incoming contract {contract_filename} declares workflow "
                f"{incoming_workflow!r}, expected {config.workflow_name!r}"
            )
        WorkflowConfig.from_raw(raw=incoming.config, workflow_root=root)
    except (WorkflowContractError, OSError, ValueError) as exc:
        raise WorkflowContractApplyError(
            f"incoming workflow contract is invalid: {exc}"
        ) from exc
    meta = snapshot_workflow_contract(
        workflow_root=root,
        source_path=incoming_path,
        source_ref=source_ref,
        source_commit=source_commit,
    )
    return {
        "ok": True,
        "workflow_root": str(root),
        "source_ref": source_ref,
        "source_commit": source_commit,
        "source_contract_path": contract_filename,
        "active_lanes": active_lanes,
        **meta,
    }


def _repo_path(config: WorkflowConfig) -> Path:
    repository = config.raw.get("repository")
    if not isinstance(repository, dict):
        raise WorkflowContractApplyError("repository config must be a mapping")
    raw_path = str(repository.get("local-path") or repository.get("local_path") or "")
    if not raw_path.strip():
        raise WorkflowContractApplyError("repository.local-path is required")
    path = Path(raw_path).expanduser()
    resolved = path if path.is_absolute() else (config.workflow_root / path).resolve()
    if not resolved.is_dir():
        raise WorkflowContractApplyError(
            f"repository.local-path is not a directory: {resolved}"
        )
    return resolved


def _active_lanes(state_path: Path) -> list[str]:
    if not state_path.exists():
        return []
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    lanes = state.get("lanes") if isinstance(state, dict) else {}
    if not isinstance(lanes, dict):
        return []
    active: list[str] = []
    for lane_id, lane in lanes.items():
        if not isinstance(lane, dict):
            continue
        status = str(lane.get("status") or "").strip()
        if status not in {"complete", "released"}:
            active.append(str(lane_id))
    return active


def _repo_contract_text(
    *, repo_path: Path, source_ref: str, workflow_name: str
) -> tuple[str, str]:
    candidates = [
        workflow_named_markdown_filename(workflow_name),
        DEFAULT_WORKFLOW_MARKDOWN_FILENAME,
    ]
    failures: list[str] = []
    for filename in dict.fromkeys(candidates):
        completed = subprocess.run(
            ["git", "show", f"{source_ref}:{filename}"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            return filename, completed.stdout
        failures.append(completed.stderr.strip() or completed.stdout.strip())
    raise WorkflowContractApplyError(
        "could not find workflow contract at "
        + " or ".join(candidates)
        + f" in {source_ref}: "
        + "; ".join(item for item in failures if item)
    )


def _git(*args: str, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git failed"
        raise WorkflowContractApplyError(
            f"`git {' '.join(args)}` failed in {cwd}: {detail}"
        )
    return completed.stdout
