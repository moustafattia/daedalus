from __future__ import annotations

from pathlib import Path
from typing import Any

from workflows.config import AgenticConfig
from workflows.contract import load_workflow_contract
from workflows.state import WorkflowState, save_state


def ensure_workflow_state_files(workflow_root: Path, config: dict[str, Any] | None = None) -> dict[str, str]:
    root = Path(workflow_root).expanduser().resolve()
    raw = config if config is not None else load_workflow_contract(root).config
    typed = AgenticConfig.from_raw(raw=raw, workflow_root=root)
    if not typed.storage.state_path.exists():
        save_state(typed.storage.state_path, WorkflowState.initial(typed.first_stage))
    typed.storage.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
    typed.storage.audit_log_path.touch(exist_ok=True)
    return {
        "state": str(typed.storage.state_path),
        "audit_log": str(typed.storage.audit_log_path),
    }


ensure_change_delivery_state_files = ensure_workflow_state_files
