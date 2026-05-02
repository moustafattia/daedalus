from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from workflows.config import AgenticConfig
from workflows.contract import load_workflow_contract


def build_status(workflow_root: Path) -> dict[str, Any]:
    root = Path(workflow_root).expanduser().resolve()
    contract = load_workflow_contract(root)
    config = AgenticConfig.from_raw(raw=contract.config, workflow_root=root)
    state: dict[str, Any] = {}
    if config.storage.state_path.exists():
        try:
            state = json.loads(config.storage.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    return {
        "workflow": "agentic",
        "health": "ok" if state else "unknown",
        "workflow_root": str(root),
        "contract_path": str(contract.source_path),
        "state_path": str(config.storage.state_path),
        "audit_log_path": str(config.storage.audit_log_path),
        "current_stage": state.get("current_stage"),
        "status": state.get("status"),
        "running_count": 1 if state.get("status") == "running" else 0,
        "retry_count": int(state.get("attempt") or 0),
        "canceling_count": 0,
        "total_tokens": 0,
        "latest_runs": [],
        "runtime_sessions": [],
    }
