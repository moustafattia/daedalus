"""Runtime preset binding and runtime readiness checks."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any, Callable

from runtimes import recognized_runtime_kinds
from workflows.contracts import load_workflow_contract, render_workflow_markdown

NAME = "change-delivery"


RUNTIME_PRESETS: dict[str, dict[str, Any]] = {
    "codex-app-server": {
        "kind": "codex-app-server",
        "stage-command": False,
        "mode": "external",
        "endpoint": "ws://127.0.0.1:4500",
        "ephemeral": False,
        "keep_alive": True,
    },
    "hermes-final": {"kind": "hermes-agent", "mode": "final"},
    "hermes-chat": {"kind": "hermes-agent", "mode": "chat", "source": "sprints"},
}


class RuntimePresetError(RuntimeError):
    pass


def available_runtime_presets() -> tuple[str, ...]:
    return tuple(sorted(RUNTIME_PRESETS))


def runtime_preset_config(preset_name: str) -> dict[str, Any]:
    try:
        return copy.deepcopy(RUNTIME_PRESETS[preset_name])
    except KeyError as exc:
        raise RuntimePresetError(
            f"unknown runtime preset {preset_name!r}; expected one of {list(available_runtime_presets())}"
        ) from exc


def configure_runtime_contract(
    *,
    workflow_root: Path,
    preset_name: str,
    role: str,
    runtime_name: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    root = Path(workflow_root).expanduser().resolve()
    contract = load_workflow_contract(root)
    config = copy.deepcopy(contract.config)
    resolved_runtime_name = (runtime_name or preset_name).strip()
    if not resolved_runtime_name:
        raise RuntimePresetError("--runtime-name cannot be blank")
    runtimes = config.setdefault("runtimes", {})
    if not isinstance(runtimes, dict):
        raise RuntimePresetError("top-level runtimes must be a mapping")
    runtimes[resolved_runtime_name] = runtime_preset_config(preset_name)
    changed_roles = bind_runtime_role(
        config=config,
        workflow_name=str(config.get("workflow") or NAME),
        role=role,
        runtime_name=resolved_runtime_name,
    )
    if not dry_run:
        contract.source_path.write_text(
            render_workflow_markdown(
                config=config, prompt_template=contract.prompt_template
            ),
            encoding="utf-8",
        )
    return {
        "ok": True,
        "action": "configure-runtime",
        "workflow_root": str(root),
        "contract_path": str(contract.source_path),
        "workflow": str(config.get("workflow") or NAME),
        "runtime_preset": preset_name,
        "runtime_name": resolved_runtime_name,
        "runtime_config": runtimes[resolved_runtime_name],
        "role": role,
        "changed_roles": changed_roles,
        "bindings": runtime_role_bindings(config),
        "checks": runtime_binding_checks(config),
        "availability_checks": runtime_availability_checks(config),
        "dry_run": dry_run,
    }


def bind_runtime_role(
    *, config: dict[str, Any], workflow_name: str, role: str, runtime_name: str
) -> list[str]:
    del workflow_name
    actors = config.setdefault("actors", {})
    if not isinstance(actors, dict):
        raise RuntimePresetError("top-level actors must be a mapping")
    normalized = _normalize_role(role)
    names = (
        sorted(str(name) for name in actors) if normalized == "all" else [normalized]
    )
    for name in names:
        actor = actors.setdefault(name, {})
        if not isinstance(actor, dict):
            raise RuntimePresetError(f"actor {name!r} must be a mapping")
        actor["runtime"] = runtime_name
    return names


def runtime_role_bindings(config: dict[str, Any]) -> list[dict[str, Any]]:
    runtimes = _runtime_profiles_from_config(config)
    actors = config.get("actors") if isinstance(config.get("actors"), dict) else {}
    bindings: list[dict[str, Any]] = []
    for role, actor in sorted(actors.items()):
        runtime_name = actor.get("runtime") if isinstance(actor, dict) else None
        _append_binding(
            bindings, role=str(role), runtime_name=runtime_name, runtimes=runtimes
        )
    return bindings


def runtime_stage_bindings(config: dict[str, Any]) -> list[dict[str, Any]]:
    actors = config.get("actors") if isinstance(config.get("actors"), dict) else {}
    stages = config.get("stages") if isinstance(config.get("stages"), dict) else {}
    bindings: list[dict[str, Any]] = []
    for stage_name, stage_cfg in stages.items():
        if not isinstance(stage_cfg, dict):
            continue
        for actor_name in stage_cfg.get("actors") or ():
            actor = actors.get(actor_name) if isinstance(actors, dict) else None
            runtime_name = actor.get("runtime") if isinstance(actor, dict) else None
            bindings.append(
                {
                    "name": f"runtime-stage:stages.{stage_name}.actors.{actor_name}",
                    "workflow": str(config.get("workflow") or NAME),
                    "stage": str(stage_name),
                    "path": f"stages.{stage_name}.actors",
                    "role": str(actor_name),
                    "role_exists": isinstance(actor, dict),
                    "runtime": runtime_name,
                }
            )
    return bindings


def runtime_binding_checks(config: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for binding in runtime_role_bindings(config):
        role = str(binding.get("role") or "actor")
        runtime_name = binding.get("runtime")
        if not runtime_name:
            checks.append(
                _runtime_check(
                    f"runtime-binding:{role}",
                    "fail",
                    f"{role} has no runtime profile",
                    role=role,
                )
            )
        elif not binding.get("profile_exists"):
            checks.append(
                _runtime_check(
                    f"runtime-binding:{role}",
                    "fail",
                    f"{role} references missing runtime profile {runtime_name!r}",
                    role=role,
                    runtime=runtime_name,
                )
            )
        else:
            checks.append(
                _runtime_check(
                    f"runtime-binding:{role}",
                    "pass",
                    f"{role} -> {runtime_name}",
                    role=role,
                    runtime=runtime_name,
                )
            )
    return checks


def runtime_stage_checks(config: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for binding in runtime_stage_bindings(config):
        name = str(binding.get("name") or "runtime-stage")
        if not binding.get("role_exists"):
            checks.append(
                _runtime_check(
                    name,
                    "fail",
                    f"missing actor {binding.get('role')!r}",
                    role=binding.get("role"),
                )
            )
        elif not binding.get("runtime"):
            checks.append(
                _runtime_check(
                    name,
                    "fail",
                    f"actor {binding.get('role')!r} has no runtime",
                    role=binding.get("role"),
                )
            )
        else:
            checks.append(
                _runtime_check(
                    name,
                    "pass",
                    f"{binding.get('role')} -> {binding.get('runtime')}",
                    role=binding.get("role"),
                )
            )
    return checks


def runtime_availability_checks(config: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for name, runtime_cfg in sorted(_runtime_profiles_from_config(config).items()):
        if not isinstance(runtime_cfg, dict):
            checks.append(
                _runtime_check(
                    f"runtime-availability:{name}",
                    "fail",
                    "runtime profile must be a mapping",
                    runtime=name,
                )
            )
            continue
        kind = str(runtime_cfg.get("kind") or "").strip()
        if kind and kind not in recognized_runtime_kinds():
            checks.append(
                _runtime_check(
                    f"runtime-availability:{name}",
                    "warn",
                    f"unknown runtime kind {kind!r}",
                    runtime=name,
                )
            )
            continue
        executable = runtime_cfg.get("executable")
        if executable and shutil.which(str(executable)) is None:
            checks.append(
                _runtime_check(
                    f"runtime-availability:{name}",
                    "fail",
                    f"executable not found: {executable}",
                    runtime=name,
                )
            )
            continue
        checks.append(
            _runtime_check(
                f"runtime-availability:{name}", "pass", kind or "runtime", runtime=name
            )
        )
    return checks


def build_runtime_matrix_report(
    *,
    workflow_root: Path,
    execute: bool = False,
    roles: list[str] | None = None,
    runtimes: list[str] | None = None,
    run: Callable[..., Any] | None = None,
    run_json: Callable[..., Any] | None = None,
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
            *runtime_availability_checks(config),
        ]
        if check.get("status") == "fail"
    ]
    return {
        "ok": not failures,
        "workflow": str(config.get("workflow") or NAME),
        "workflow_root": str(root),
        "contract_path": str(contract.source_path),
        "execute": execute,
        "filters": {"roles": sorted(role_filter), "runtimes": sorted(runtime_filter)},
        "runtime_profiles": config.get("runtimes")
        if isinstance(config.get("runtimes"), dict)
        else {},
        "bindings": bindings,
        "stage_bindings": runtime_stage_bindings(config),
        "stage_checks": runtime_stage_checks(config),
        "binding_checks": runtime_binding_checks(config),
        "availability_checks": runtime_availability_checks(config),
        "matrix": selected,
        "failures": failures,
    }


def _normalize_role(role: str) -> str:
    normalized = role.strip()
    if not normalized:
        raise RuntimePresetError("--role cannot be blank")
    if normalized.startswith("change-delivery."):
        normalized = normalized.removeprefix("change-delivery.")
    return normalized


def _runtime_profiles_from_config(config: dict[str, Any]) -> dict[str, Any]:
    runtimes = config.get("runtimes")
    return runtimes if isinstance(runtimes, dict) else {}


def _append_binding(
    bindings: list[dict[str, Any]],
    *,
    role: str,
    runtime_name: Any,
    runtimes: dict[str, Any],
) -> None:
    normalized_runtime = str(runtime_name or "").strip() or None
    runtime_cfg = runtimes.get(normalized_runtime) if normalized_runtime else None
    profile_exists = isinstance(runtime_cfg, dict)
    bindings.append(
        {
            "role": role,
            "runtime": normalized_runtime,
            "profile_exists": profile_exists,
            "kind": str(runtime_cfg.get("kind") or "").strip()
            if profile_exists
            else None,
        }
    )


def _runtime_check(name: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    payload = {"name": name, "status": status, "detail": detail}
    payload.update(extra)
    return payload
