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
        agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
        coder = agents.get("coder") if isinstance(agents.get("coder"), dict) else {}
        for tier_name in sorted(coder):
            tier = coder.get(tier_name)
            if isinstance(tier, dict):
                _append_binding(bindings, role=f"coder.{tier_name}", runtime_name=tier.get("runtime"), runtimes=runtimes)
        reviewer = agents.get("internal-reviewer") if isinstance(agents.get("internal-reviewer"), dict) else {}
        _append_binding(
            bindings,
            role="internal-reviewer",
            runtime_name=reviewer.get("runtime"),
            runtimes=runtimes,
        )
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
        checks.append(
            {
                "name": f"runtime-binding:{role}",
                "status": "pass",
                "detail": f"{role} -> {runtime_name} ({binding.get('kind')})",
                "role": role,
                "runtime": runtime_name,
                "kind": binding.get("kind"),
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
    agents = config.setdefault("agents", {})
    if not isinstance(agents, dict):
        raise RuntimePresetError("change-delivery agents must be a mapping")

    changed: list[str] = []
    if role in {"coder.default", "coder", "all"}:
        changed.extend(_bind_coder_tiers(agents=agents, tiers=("default",), runtime_name=runtime_name))
    if role in {"coder.high-effort", "coder", "all"}:
        changed.extend(_bind_coder_tiers(agents=agents, tiers=("high-effort",), runtime_name=runtime_name))
    if role in {"internal-reviewer", "reviewer", "all"}:
        reviewer = agents.setdefault("internal-reviewer", {})
        if not isinstance(reviewer, dict):
            raise RuntimePresetError("agents.internal-reviewer must be a mapping")
        reviewer["runtime"] = runtime_name
        changed.append("internal-reviewer")
    if changed:
        return changed
    raise RuntimePresetError(
        "change-delivery supports --role coder.default, coder.high-effort, coder, "
        "internal-reviewer, reviewer, or all"
    )


def _bind_coder_tiers(*, agents: dict[str, Any], tiers: tuple[str, ...], runtime_name: str) -> list[str]:
    coder = agents.setdefault("coder", {})
    if not isinstance(coder, dict):
        raise RuntimePresetError("agents.coder must be a mapping")
    changed = []
    for tier_name in tiers:
        tier = coder.setdefault(tier_name, {})
        if not isinstance(tier, dict):
            raise RuntimePresetError(f"agents.coder.{tier_name} must be a mapping")
        tier["runtime"] = runtime_name
        changed.append(f"coder.{tier_name}")
    return changed


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
            "kind": str(runtime_cfg.get("kind") or "").strip() if profile_exists else None,
        }
    )


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
    "runtime_preset_config",
    "runtime_role_bindings",
]
