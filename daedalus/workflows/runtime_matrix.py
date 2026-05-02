from __future__ import annotations

from pathlib import Path
from typing import Any

from workflows.contract import load_workflow_contract
from workflows.runtime_presets import (
    runtime_availability_checks,
    runtime_binding_checks,
    runtime_capability_checks,
    runtime_role_bindings,
    runtime_stage_bindings,
    runtime_stage_checks,
)


def build_runtime_matrix_report(
    *,
    workflow_root: Path,
    execute: bool = False,
    roles: list[str] | None = None,
    runtimes: list[str] | None = None,
    run=None,
    run_json=None,
) -> dict[str, Any]:
    del run, run_json
    root = Path(workflow_root).expanduser().resolve()
    contract = load_workflow_contract(root)
    config = dict(contract.config)
    role_filter = {str(item) for item in roles or []}
    runtime_filter = {str(item) for item in runtimes or []}
    bindings = runtime_role_bindings(config)
    selected = [
        binding
        for binding in bindings
        if (not role_filter or str(binding.get("role")) in role_filter)
        and (not runtime_filter or str(binding.get("runtime")) in runtime_filter)
    ]
    failures = [
        check
        for check in [
            *runtime_stage_checks(config),
            *runtime_binding_checks(config),
            *runtime_capability_checks(config),
            *runtime_availability_checks(config),
        ]
        if check.get("status") == "fail"
    ]
    return {
        "ok": not failures,
        "workflow": str(config.get("workflow") or "agentic"),
        "workflow_root": str(root),
        "contract_path": str(contract.source_path),
        "execute": execute,
        "filters": {"roles": sorted(role_filter), "runtimes": sorted(runtime_filter)},
        "missing": {"roles": [], "runtimes": []},
        "runtime_profiles": config.get("runtimes") if isinstance(config.get("runtimes"), dict) else {},
        "bindings": bindings,
        "stage_bindings": runtime_stage_bindings(config),
        "stage_checks": runtime_stage_checks(config),
        "binding_checks": runtime_binding_checks(config),
        "capability_checks": runtime_capability_checks(config),
        "availability_checks": runtime_availability_checks(config),
        "matrix": selected,
        "failures": failures,
    }
