from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from runtimes import build_runtimes
from runtimes.stages import prompt_result_from_stage, run_runtime_stage
from workflows.contract import load_workflow_contract
from workflows.change_delivery.contract_model import actor_config as change_delivery_actor_config
from workflows.runtime_presets import (
    runtime_availability_checks,
    runtime_binding_checks,
    runtime_capability_checks,
    runtime_role_bindings,
    runtime_stage_bindings,
    runtime_stage_checks,
)


_SAFE_PATH_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def build_runtime_matrix_report(
    *,
    workflow_root: Path,
    execute: bool = False,
    roles: list[str] | None = None,
    runtimes: list[str] | None = None,
    run=None,
    run_json=None,
) -> dict[str, Any]:
    """Build the runtime-role matrix for a workflow contract.

    When ``execute`` is true, each selected role gets a tiny runtime-stage smoke.
    This exercises the same shared stage boundary used by workflow dispatch; it
    does not call workflow-specific code paths or mutate tracker state.
    """
    root = Path(workflow_root).expanduser().resolve()
    contract = load_workflow_contract(root)
    config = dict(contract.config)
    workflow_name = str(config.get("workflow") or "").strip()
    runtime_profiles = _runtime_profiles_from_config(config)
    role_filter = _normalized_filter(roles)
    runtime_filter = _normalized_filter(runtimes)

    binding_checks = runtime_binding_checks(config)
    stage_bindings = runtime_stage_bindings(config)
    stage_checks = runtime_stage_checks(config)
    capability_checks = runtime_capability_checks(config)
    availability_checks = runtime_availability_checks(config)
    bindings = runtime_role_bindings(config)
    selected_bindings = [
        binding
        for binding in bindings
        if (not role_filter or str(binding.get("role") or "") in role_filter)
        and (not runtime_filter or str(binding.get("runtime") or "") in runtime_filter)
    ]

    matrix = [
        _matrix_item(
            binding=binding,
            binding_checks=binding_checks,
            capability_checks=capability_checks,
            availability_checks=availability_checks,
        )
        for binding in selected_bindings
    ]

    missing_roles = sorted(role_filter - {str(binding.get("role") or "") for binding in bindings})
    missing_runtimes = sorted(runtime_filter - {str(binding.get("runtime") or "") for binding in bindings})

    if execute:
        _execute_matrix_smokes(
            matrix=matrix,
            config=config,
            runtime_profiles=runtime_profiles,
            workflow_root=root,
            run=run,
            run_json=run_json,
        )

    selected_check_names = {
        f"{prefix}:{item.get('role')}"
        for item in matrix
        if item.get("role")
        for prefix in ("runtime-binding", "runtime-capability")
    }
    failures = [
        check
        for check in stage_checks
        if str(check.get("status") or "") == "fail"
    ]
    failures.extend(
        [
            check
            for check in [*binding_checks, *capability_checks]
            if check.get("name") in selected_check_names
            and str(check.get("status") or "") == "fail"
        ]
    )
    if execute:
        failures.extend(
            {
                "name": f"runtime-smoke:{item.get('role')}",
                "status": "fail",
                "detail": (item.get("smoke") or {}).get("error"),
            }
            for item in matrix
            if not (item.get("smoke") or {}).get("ok")
        )
    return {
        "ok": not failures and not missing_roles and not missing_runtimes,
        "workflow": workflow_name,
        "workflow_root": str(root),
        "contract_path": str(contract.source_path),
        "execute": execute,
        "filters": {
            "roles": sorted(role_filter),
            "runtimes": sorted(runtime_filter),
        },
        "missing": {
            "roles": missing_roles,
            "runtimes": missing_runtimes,
        },
        "runtime_profiles": {
            name: {"kind": str((cfg or {}).get("kind") or "")}
            for name, cfg in sorted(runtime_profiles.items())
            if isinstance(cfg, dict)
        },
        "bindings": bindings,
        "stage_bindings": stage_bindings,
        "stage_checks": stage_checks,
        "binding_checks": binding_checks,
        "capability_checks": capability_checks,
        "availability_checks": availability_checks,
        "matrix": matrix,
        "failures": failures,
    }


def _execute_matrix_smokes(
    *,
    matrix: list[dict[str, Any]],
    config: dict[str, Any],
    runtime_profiles: dict[str, Any],
    workflow_root: Path,
    run,
    run_json,
) -> None:
    selected_runtime_names = {str(item.get("runtime") or "") for item in matrix if item.get("runtime")}
    selected_runtime_profiles = {
        name: cfg
        for name, cfg in runtime_profiles.items()
        if name in selected_runtime_names
    }
    runtimes = build_runtimes(
        selected_runtime_profiles,
        run=run or _subprocess_run,
        run_json=run_json or _subprocess_run_json,
    )
    for item in matrix:
        role = str(item.get("role") or "")
        runtime_name = str(item.get("runtime") or "")
        if not item.get("profile_exists"):
            item["smoke"] = {"ok": False, "error": f"missing runtime profile {runtime_name!r}"}
            continue
        runtime = runtimes.get(runtime_name)
        runtime_cfg = runtime_profiles.get(runtime_name) if isinstance(runtime_profiles.get(runtime_name), dict) else {}
        if runtime is None or not isinstance(runtime_cfg, dict):
            item["smoke"] = {"ok": False, "error": f"unable to build runtime profile {runtime_name!r}"}
            continue
        agent_cfg = _agent_cfg_for_role(config, role)
        if agent_cfg is None:
            item["smoke"] = {"ok": False, "error": f"unable to resolve agent config for role {role!r}"}
            continue
        worktree = workflow_root / "runtime" / "matrix-smoke" / _safe_name(role)
        worktree.mkdir(parents=True, exist_ok=True)
        prompt = (
            f"Daedalus runtime matrix smoke.\n"
            f"Workflow: {config.get('workflow')}\n"
            f"Role: {role}\n"
            "Return a short signoff.\n"
        )
        try:
            result = run_runtime_stage(
                runtime=runtime,
                runtime_cfg=runtime_cfg,
                agent_cfg=agent_cfg,
                stage_name=f"runtime-matrix-{_safe_name(role)}",
                worktree=worktree,
                session_name=f"runtime-matrix-{_safe_name(role)}",
                prompt=prompt,
                placeholders={
                    "role": role,
                    "workflow": str(config.get("workflow") or ""),
                    "runtime": runtime_name,
                    "workflow_root": str(workflow_root),
                },
            )
            metrics = prompt_result_from_stage(result)
            item["smoke"] = {
                "ok": True,
                "used_command": result.used_command,
                "output_preview": (result.output or "").strip()[:500],
                "prompt_path": str(result.prompt_path) if result.prompt_path else None,
                "result_path": str(result.result_path) if result.result_path else None,
                "session_id": metrics.session_id,
                "thread_id": metrics.thread_id,
                "turn_id": metrics.turn_id,
                "last_event": metrics.last_event,
                "tokens": metrics.tokens or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "rate_limits": metrics.rate_limits,
            }
        except Exception as exc:
            item["smoke"] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }


def _matrix_item(
    *,
    binding: dict[str, Any],
    binding_checks: list[dict[str, Any]],
    capability_checks: list[dict[str, Any]],
    availability_checks: list[dict[str, Any]],
) -> dict[str, Any]:
    role = str(binding.get("role") or "")
    runtime_name = str(binding.get("runtime") or "")
    binding_check = _find_check(binding_checks, f"runtime-binding:{role}")
    capability_check = _find_check(capability_checks, f"runtime-capability:{role}")
    availability_check = _find_check(availability_checks, f"runtime-availability:{runtime_name}")
    return {
        "role": role,
        "runtime": runtime_name or None,
        "kind": binding.get("kind"),
        "profile_exists": bool(binding.get("profile_exists")),
        "capabilities": binding.get("capabilities") or [],
        "binding": binding_check,
        "capability": capability_check,
        "availability": availability_check,
    }


def _find_check(checks: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((check for check in checks if check.get("name") == name), None)


def _runtime_profiles_from_config(config: dict[str, Any]) -> dict[str, Any]:
    top_level = config.get("runtimes")
    if isinstance(top_level, dict):
        return top_level
    daedalus_cfg = config.get("daedalus")
    if isinstance(daedalus_cfg, dict) and isinstance(daedalus_cfg.get("runtimes"), dict):
        return daedalus_cfg["runtimes"]
    return {}


def _agent_cfg_for_role(config: dict[str, Any], role: str) -> dict[str, Any] | None:
    if str(config.get("workflow") or "") == "issue-runner":
        agent = config.get("agent")
        return agent if isinstance(agent, dict) and role == "agent" else None

    if str(config.get("workflow") or "") != "change-delivery":
        return None
    actors = config.get("actors")
    if isinstance(actors, dict):
        return change_delivery_actor_config(config, role)
    return None


def _normalized_filter(values: list[str] | None) -> set[str]:
    return {str(value).strip() for value in (values or []) if str(value).strip()}


def _safe_name(value: str) -> str:
    return _SAFE_PATH_RE.sub("-", value).strip("-") or "runtime"


def _subprocess_run(command, *, cwd=None, timeout=None, env=None, **kwargs):
    return subprocess.run(
        command,
        cwd=cwd,
        timeout=timeout,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _subprocess_run_json(command, *, cwd=None, timeout=None, env=None, **kwargs):
    completed = _subprocess_run(command, cwd=cwd, timeout=timeout, env=env)
    return json.loads(completed.stdout or "null")
