from __future__ import annotations

import copy
import shlex
import shutil
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from workflows.contract import (
    load_workflow_contract,
    render_workflow_markdown,
)
from workflows.change_delivery.contract_model import (
    actor_config as change_delivery_actor_config,
    bind_actor_runtime,
    change_delivery_actor_names,
)
from runtimes.capabilities import (
    CAP_COMMAND_STAGE,
    CAP_PROMPT_TURN,
    explicit_required_capabilities,
    format_capabilities,
    recognized_runtime_kinds,
    runtime_profile_capabilities,
    unknown_capabilities,
)


RUNTIME_PRESETS: dict[str, dict[str, Any]] = {
    "hermes-final": {
        "kind": "hermes-agent",
        "mode": "final",
    },
    "hermes-chat": {
        "kind": "hermes-agent",
        "mode": "chat",
        "source": "daedalus",
    },
    "codex-service": {
        "kind": "codex-app-server",
        "mode": "external",
        "endpoint": "ws://127.0.0.1:4500",
        "ephemeral": False,
        "keep_alive": True,
    },
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
    if contract.source_path.suffix.lower() != ".md":
        raise RuntimePresetError(
            f"configure-runtime edits repo-owned WORKFLOW.md contracts only; got {contract.source_path}"
        )

    config = copy.deepcopy(contract.config)
    workflow_name = str(config.get("workflow") or "").strip()
    if not workflow_name:
        raise RuntimePresetError("workflow contract is missing top-level workflow")

    resolved_runtime_name = (runtime_name or preset_name).strip()
    if not resolved_runtime_name:
        raise RuntimePresetError("--runtime-name cannot be blank")

    runtime_config = runtime_preset_config(preset_name)
    runtimes = config.setdefault("runtimes", {})
    if not isinstance(runtimes, dict):
        raise RuntimePresetError("top-level runtimes must be a mapping")
    runtimes[resolved_runtime_name] = runtime_config

    changed_roles = bind_runtime_role(
        config=config,
        workflow_name=workflow_name,
        role=role,
        runtime_name=resolved_runtime_name,
    )
    capability_checks = runtime_capability_checks(config)
    capability_failures = _runtime_capability_failures_for_roles(capability_checks, changed_roles)
    if capability_failures:
        details = "; ".join(str(check.get("detail") or check.get("name")) for check in capability_failures)
        raise RuntimePresetError(details)

    if not dry_run:
        contract.source_path.write_text(
            render_workflow_markdown(config=config, prompt_template=contract.prompt_template),
            encoding="utf-8",
        )

    return {
        "ok": True,
        "action": "configure-runtime",
        "workflow_root": str(root),
        "contract_path": str(contract.source_path),
        "workflow": workflow_name,
        "runtime_preset": preset_name,
        "runtime_name": resolved_runtime_name,
        "runtime_config": runtime_config,
        "role": role,
        "changed_roles": changed_roles,
        "bindings": runtime_role_bindings(config),
        "checks": runtime_binding_checks(config),
        "capability_checks": capability_checks,
        "availability_checks": runtime_availability_checks(config),
        "dry_run": dry_run,
    }


def bind_runtime_role(
    *,
    config: dict[str, Any],
    workflow_name: str,
    role: str,
    runtime_name: str,
) -> list[str]:
    normalized_role = _normalize_role(role)
    if workflow_name == "issue-runner":
        return _bind_issue_runner_role(config=config, role=normalized_role, runtime_name=runtime_name)
    if workflow_name == "change-delivery":
        return _bind_change_delivery_role(config=config, role=normalized_role, runtime_name=runtime_name)
    raise RuntimePresetError(f"configure-runtime does not know workflow {workflow_name!r}")


def runtime_role_bindings(config: dict[str, Any]) -> list[dict[str, Any]]:
    workflow_name = str(config.get("workflow") or "").strip()
    runtimes = _runtime_profiles_from_config(config)
    bindings: list[dict[str, Any]] = []
    if workflow_name == "issue-runner":
        agent = config.get("agent") if isinstance(config.get("agent"), dict) else {}
        _append_binding(bindings, role="agent", runtime_name=agent.get("runtime"), runtimes=runtimes)
        return bindings
    if workflow_name == "change-delivery":
        actors = config.get("actors") if isinstance(config.get("actors"), dict) else {}
        if actors:
            for actor_name in change_delivery_actor_names(config):
                actor = change_delivery_actor_config(config, actor_name)
                if isinstance(actor, dict):
                    _append_binding(
                        bindings,
                        role=actor_name,
                        runtime_name=actor.get("runtime"),
                        runtimes=runtimes,
                    )
            return bindings
    return bindings


def runtime_binding_checks(config: dict[str, Any]) -> list[dict[str, Any]]:
    checks = []
    for binding in runtime_role_bindings(config):
        role = binding.get("role") or "runtime-role"
        runtime_name = binding.get("runtime")
        if not runtime_name:
            checks.append(
                {
                    "name": f"runtime-binding:{role}",
                    "status": "fail",
                    "detail": f"{role} has no runtime profile",
                    "role": role,
                }
            )
            continue
        if not binding.get("profile_exists"):
            checks.append(
                {
                    "name": f"runtime-binding:{role}",
                    "status": "fail",
                    "detail": f"{role} references missing runtime profile {runtime_name!r}",
                    "role": role,
                    "runtime": runtime_name,
                }
            )
            continue
        kind = str(binding.get("kind") or "").strip()
        if not kind:
            checks.append(
                {
                    "name": f"runtime-binding:{role}",
                    "status": "fail",
                    "detail": f"{role} references runtime profile {runtime_name!r} without runtime.kind",
                    "role": role,
                    "runtime": runtime_name,
                }
            )
            continue
        if kind not in recognized_runtime_kinds():
            checks.append(
                {
                    "name": f"runtime-binding:{role}",
                    "status": "fail",
                    "detail": f"{role} references runtime profile {runtime_name!r} with unsupported kind {kind!r}",
                    "role": role,
                    "runtime": runtime_name,
                    "kind": kind,
                    "expected": sorted(recognized_runtime_kinds()),
                }
            )
            continue
        checks.append(
            {
                "name": f"runtime-binding:{role}",
                "status": "pass",
                "detail": f"{role} -> {runtime_name} ({binding.get('kind')})",
                "role": role,
                "runtime": runtime_name,
                "kind": binding.get("kind"),
                "capabilities": binding.get("capabilities"),
            }
        )
    return checks


def runtime_stage_checks(config: dict[str, Any]) -> list[dict[str, Any]]:
    workflow_name = str(config.get("workflow") or "").strip()
    if workflow_name == "issue-runner":
        return _issue_runner_stage_checks(config)
    if workflow_name == "change-delivery":
        return _change_delivery_stage_checks(config)
    return []


def runtime_capability_checks(config: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    runtimes = _runtime_profiles_from_config(config)
    for binding in runtime_role_bindings(config):
        role = str(binding.get("role") or "runtime-role")
        runtime_name = str(binding.get("runtime") or "").strip()
        if not runtime_name or not binding.get("profile_exists"):
            continue
        runtime_cfg = runtimes.get(runtime_name) if isinstance(runtimes.get(runtime_name), dict) else {}
        profile = runtime_profile_capabilities(runtime_cfg)
        if profile is None:
            kind = str((runtime_cfg or {}).get("kind") or "").strip()
            checks.append(
                {
                    "name": f"runtime-capability:{role}",
                    "status": "fail",
                    "detail": f"{role} uses unsupported runtime kind {kind!r}",
                    "role": role,
                    "runtime": runtime_name,
                    "kind": kind,
                }
            )
            continue

        role_cfg = _runtime_role_config(config, role) or {}
        explicit = explicit_required_capabilities(role_cfg)
        unknown = unknown_capabilities(explicit)
        if unknown:
            checks.append(
                {
                    "name": f"runtime-capability:{role}",
                    "status": "fail",
                    "detail": f"{role} declares unknown required capabilities: {format_capabilities(unknown)}",
                    "role": role,
                    "runtime": runtime_name,
                    "kind": profile.kind,
                    "unknown_capabilities": sorted(unknown),
                }
            )
            continue

        required_any = _runtime_required_any_capabilities(role_cfg=role_cfg, runtime_cfg=runtime_cfg)
        missing_all = explicit - profile.capabilities
        if required_any and not profile.has_any(required_any):
            checks.append(
                {
                    "name": f"runtime-capability:{role}",
                    "status": "fail",
                    "detail": (
                        f"{role} requires one of {format_capabilities(required_any)}, "
                        f"but {runtime_name} ({profile.kind}) has {format_capabilities(profile.capabilities)}"
                    ),
                    "role": role,
                    "runtime": runtime_name,
                    "kind": profile.kind,
                    "required_any": sorted(required_any),
                    "capabilities": sorted(profile.capabilities),
                }
            )
            continue
        if missing_all:
            checks.append(
                {
                    "name": f"runtime-capability:{role}",
                    "status": "fail",
                    "detail": (
                        f"{role} requires {format_capabilities(explicit)}, "
                        f"but {runtime_name} ({profile.kind}) is missing {format_capabilities(missing_all)}"
                    ),
                    "role": role,
                    "runtime": runtime_name,
                    "kind": profile.kind,
                    "required": sorted(explicit),
                    "missing": sorted(missing_all),
                    "capabilities": sorted(profile.capabilities),
                }
            )
            continue
        required_detail = format_capabilities(explicit or required_any)
        checks.append(
            {
                "name": f"runtime-capability:{role}",
                "status": "pass",
                "detail": f"{role} runtime capabilities ok; required={required_detail}",
                "role": role,
                "runtime": runtime_name,
                "kind": profile.kind,
                "required": sorted(explicit),
                "required_any": sorted(required_any),
                "capabilities": sorted(profile.capabilities),
            }
        )
    return checks


def runtime_availability_checks(config: dict[str, Any]) -> list[dict[str, Any]]:
    runtimes = _runtime_profiles_from_config(config)
    referenced = {
        str(binding.get("runtime"))
        for binding in runtime_role_bindings(config)
        if binding.get("runtime") and binding.get("profile_exists")
    }
    checks = []
    for name in sorted(referenced):
        runtime_cfg = runtimes.get(name) if isinstance(runtimes.get(name), dict) else {}
        kind = str(runtime_cfg.get("kind") or "").strip()
        checks.append(_runtime_availability_check(name=name, kind=kind, runtime_cfg=runtime_cfg))
    return checks


def _normalize_role(role: str) -> str:
    normalized = role.strip()
    if not normalized:
        raise RuntimePresetError("--role cannot be blank")
    if normalized.startswith("issue-runner."):
        normalized = normalized.removeprefix("issue-runner.")
    if normalized.startswith("change-delivery."):
        normalized = normalized.removeprefix("change-delivery.")
    return normalized


def _runtime_profiles_from_config(config: dict[str, Any]) -> dict[str, Any]:
    top_level = config.get("runtimes")
    if isinstance(top_level, dict) and top_level:
        return top_level
    daedalus_cfg = config.get("daedalus")
    if isinstance(daedalus_cfg, dict) and isinstance(daedalus_cfg.get("runtimes"), dict):
        return daedalus_cfg["runtimes"]
    return {}


def _bind_issue_runner_role(*, config: dict[str, Any], role: str, runtime_name: str) -> list[str]:
    if role not in {"agent", "all"}:
        raise RuntimePresetError("issue-runner supports --role agent or --role all")
    agent = config.setdefault("agent", {})
    if not isinstance(agent, dict):
        raise RuntimePresetError("issue-runner agent must be a mapping")
    agent["runtime"] = runtime_name
    return ["agent"]


def _bind_change_delivery_role(*, config: dict[str, Any], role: str, runtime_name: str) -> list[str]:
    if isinstance(config.get("actors"), dict):
        try:
            return bind_actor_runtime(config, role=role, runtime_name=runtime_name)
        except ValueError as exc:
            raise RuntimePresetError(str(exc)) from exc

    raise RuntimePresetError(
        "change-delivery configure-runtime requires an actors: block; use --role <actor-name> or --role all"
    )


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
    capability_profile = runtime_profile_capabilities(runtime_cfg) if profile_exists else None
    bindings.append(
        {
            "role": role,
            "runtime": normalized_runtime,
            "profile_exists": profile_exists,
            "kind": str(runtime_cfg.get("kind") or "").strip() if profile_exists else None,
            "capabilities": sorted(capability_profile.capabilities) if capability_profile else [],
        }
    )


def _runtime_role_config(config: dict[str, Any], role: str) -> dict[str, Any] | None:
    workflow_name = str(config.get("workflow") or "").strip()
    if workflow_name == "issue-runner" and role == "agent":
        agent = config.get("agent")
        return agent if isinstance(agent, dict) else None
    if workflow_name == "change-delivery":
        return change_delivery_actor_config(config, role)
    return None


def _runtime_required_any_capabilities(*, role_cfg: dict[str, Any], runtime_cfg: dict[str, Any]) -> frozenset[str]:
    if role_cfg.get("command") or runtime_cfg.get("command"):
        return frozenset({CAP_COMMAND_STAGE})
    return frozenset({CAP_PROMPT_TURN})


def _runtime_capability_failures_for_roles(
    checks: list[dict[str, Any]],
    roles: list[str],
) -> list[dict[str, Any]]:
    role_set = set(roles)
    return [
        check
        for check in checks
        if check.get("status") == "fail" and str(check.get("role") or "") in role_set
    ]


def _issue_runner_stage_checks(config: dict[str, Any]) -> list[dict[str, Any]]:
    agent = config.get("agent")
    if not isinstance(agent, dict):
        return [
            {
                "name": "runtime-stage:agent",
                "status": "fail",
                "detail": "issue-runner agent must be a mapping",
                "role": "agent",
            }
        ]
    runtime_name = str(agent.get("runtime") or "").strip()
    if runtime_name:
        return [
            {
                "name": "runtime-stage:agent",
                "status": "pass",
                "detail": f"agent dispatches through runtime profile {runtime_name!r}",
                "role": "agent",
                "runtime": runtime_name,
            }
        ]
    return [
        {
            "name": "runtime-stage:agent",
            "status": "fail",
            "detail": "issue-runner agent requires runtime",
            "role": "agent",
        }
    ]


def _change_delivery_stage_checks(config: dict[str, Any]) -> list[dict[str, Any]]:
    actors = config.get("actors") if isinstance(config.get("actors"), dict) else {}
    checks: list[dict[str, Any]] = []

    def append_actor_ref(path: str, actor_name: Any) -> None:
        actor = str(actor_name or "").strip()
        if not actor:
            return
        actor_cfg = actors.get(actor) if isinstance(actors, dict) else None
        if not isinstance(actor_cfg, dict):
            checks.append(
                {
                    "name": f"runtime-stage:{path}",
                    "status": "fail",
                    "detail": f"{path} references missing actor {actor!r}",
                    "path": path,
                    "role": actor,
                }
            )
            return
        runtime_name = str(actor_cfg.get("runtime") or "").strip()
        if not runtime_name:
            checks.append(
                {
                    "name": f"runtime-stage:{path}",
                    "status": "fail",
                    "detail": f"{path} actor {actor!r} has no runtime profile",
                    "path": path,
                    "role": actor,
                }
            )
            return
        checks.append(
            {
                "name": f"runtime-stage:{path}",
                "status": "pass",
                "detail": f"{path} -> actor {actor} -> runtime {runtime_name}",
                "path": path,
                "role": actor,
                "runtime": runtime_name,
            }
        )

    stages = config.get("stages") if isinstance(config.get("stages"), dict) else {}
    for stage_name, stage_cfg in stages.items():
        if not isinstance(stage_cfg, dict):
            continue
        append_actor_ref(f"stages.{stage_name}.actor", stage_cfg.get("actor"))
        escalation = stage_cfg.get("escalation")
        if isinstance(escalation, dict):
            append_actor_ref(f"stages.{stage_name}.escalation.actor", escalation.get("actor"))

    gates = config.get("gates") if isinstance(config.get("gates"), dict) else {}
    for gate_name, gate_cfg in gates.items():
        if not isinstance(gate_cfg, dict):
            continue
        if str(gate_cfg.get("type") or "").strip() == "agent-review":
            append_actor_ref(f"gates.{gate_name}.actor", gate_cfg.get("actor"))

    return checks


def _runtime_availability_check(*, name: str, kind: str, runtime_cfg: dict[str, Any]) -> dict[str, Any]:
    if kind == "codex-app-server":
        mode = str(runtime_cfg.get("mode") or ("external" if runtime_cfg.get("endpoint") else "managed")).strip()
        if mode == "external":
            endpoint = str(runtime_cfg.get("endpoint") or "").strip()
            ok, detail = _probe_ws_endpoint(endpoint)
            return {
                "name": f"runtime-availability:{name}",
                "status": "pass" if ok else "warn",
                "detail": detail,
                "runtime": name,
                "kind": kind,
                "mode": mode,
            }
        executable = _runtime_command_executable(runtime_cfg, default="codex")
        return _executable_check(name=name, kind=kind, executable=executable, mode=mode)

    if kind == "hermes-agent":
        executable = _runtime_command_executable(runtime_cfg, default=str(runtime_cfg.get("executable") or "hermes"))
        return _executable_check(name=name, kind=kind, executable=executable)
    if kind == "claude-cli":
        executable = _runtime_command_executable(runtime_cfg, default="claude")
        return _executable_check(name=name, kind=kind, executable=executable)
    if kind == "acpx-codex":
        executable = _runtime_command_executable(runtime_cfg, default="acpx")
        return _executable_check(name=name, kind=kind, executable=executable)
    return {
        "name": f"runtime-availability:{name}",
        "status": "warn",
        "detail": f"unknown runtime kind {kind!r}",
        "runtime": name,
        "kind": kind,
    }


def _runtime_command_executable(runtime_cfg: dict[str, Any], *, default: str) -> str:
    command = runtime_cfg.get("command")
    if isinstance(command, list) and command:
        return str(command[0])
    if isinstance(command, str) and command.strip():
        parts = shlex.split(command)
        if parts:
            return parts[0]
    return default


def _executable_check(*, name: str, kind: str, executable: str, mode: str | None = None) -> dict[str, Any]:
    path = shutil.which(executable)
    detail = f"{executable} -> {path}" if path else f"{executable} not found on PATH"
    return {
        "name": f"runtime-availability:{name}",
        "status": "pass" if path else "warn",
        "detail": detail,
        "runtime": name,
        "kind": kind,
        "mode": mode,
        "executable": executable,
    }


def _probe_ws_endpoint(endpoint: str) -> tuple[bool, str]:
    if not endpoint:
        return False, "external codex-app-server endpoint is not configured"
    parsed = urlparse(endpoint)
    if parsed.scheme != "ws":
        return False, f"external codex-app-server endpoint must use ws://, got {endpoint!r}"
    if not parsed.hostname or not parsed.port:
        return False, f"external codex-app-server endpoint requires host and port: {endpoint!r}"
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=0.2):
            return True, f"{endpoint} is reachable"
    except OSError as exc:
        return False, f"{endpoint} is not reachable yet: {exc}"


__all__ = [
    "RUNTIME_PRESETS",
    "RuntimePresetError",
    "available_runtime_presets",
    "configure_runtime_contract",
    "runtime_availability_checks",
    "runtime_binding_checks",
    "runtime_capability_checks",
    "runtime_preset_config",
    "runtime_role_bindings",
    "runtime_stage_checks",
]
