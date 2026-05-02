from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


CAP_PROMPT_TURN = "prompt-turn"
CAP_COMMAND_STAGE = "command-stage"
CAP_ONE_SHOT = "one-shot"
CAP_PERSISTENT_SESSION = "persistent-session"
CAP_RESUME = "resume"
CAP_CANCEL = "cancel"
CAP_STRUCTURED_EVENTS = "structured-events"
CAP_TOKEN_METRICS = "token-metrics"
CAP_THREAD_VISIBLE = "thread-visible"
CAP_ACTIVITY_HEARTBEAT = "activity-heartbeat"
CAP_SERVICE_REQUIRED = "service-required"


KNOWN_CAPABILITIES = frozenset(
    {
        CAP_PROMPT_TURN,
        CAP_COMMAND_STAGE,
        CAP_ONE_SHOT,
        CAP_PERSISTENT_SESSION,
        CAP_RESUME,
        CAP_CANCEL,
        CAP_STRUCTURED_EVENTS,
        CAP_TOKEN_METRICS,
        CAP_THREAD_VISIBLE,
        CAP_ACTIVITY_HEARTBEAT,
        CAP_SERVICE_REQUIRED,
    }
)


@dataclass(frozen=True)
class RuntimeCapabilityProfile:
    kind: str
    capabilities: frozenset[str]
    summary: str

    def has_all(self, required: set[str] | frozenset[str]) -> bool:
        return set(required).issubset(self.capabilities)

    def has_any(self, required: set[str] | frozenset[str]) -> bool:
        return bool(set(required) & self.capabilities)


_BASE_EXECUTION_CAPABILITIES = frozenset(
    {
        CAP_PROMPT_TURN,
        CAP_COMMAND_STAGE,
        CAP_ACTIVITY_HEARTBEAT,
    }
)

_KIND_CAPABILITIES: dict[str, RuntimeCapabilityProfile] = {
    "acpx-codex": RuntimeCapabilityProfile(
        kind="acpx-codex",
        capabilities=_BASE_EXECUTION_CAPABILITIES
        | {
            CAP_PERSISTENT_SESSION,
            CAP_RESUME,
        },
        summary="ACPX-backed Codex sessions with resume support.",
    ),
    "claude-cli": RuntimeCapabilityProfile(
        kind="claude-cli",
        capabilities=_BASE_EXECUTION_CAPABILITIES | {CAP_ONE_SHOT},
        summary="One-shot Claude CLI prompt execution.",
    ),
    "codex-app-server": RuntimeCapabilityProfile(
        kind="codex-app-server",
        capabilities=_BASE_EXECUTION_CAPABILITIES
        | {
            CAP_PERSISTENT_SESSION,
            CAP_RESUME,
            CAP_CANCEL,
            CAP_STRUCTURED_EVENTS,
            CAP_TOKEN_METRICS,
            CAP_THREAD_VISIBLE,
        },
        summary="Codex app-server thread runtime with structured turn events.",
    ),
    "hermes-agent": RuntimeCapabilityProfile(
        kind="hermes-agent",
        capabilities=_BASE_EXECUTION_CAPABILITIES | {CAP_ONE_SHOT},
        summary="Hermes Agent CLI execution via final or quiet chat mode.",
    ),
}


def recognized_runtime_kinds() -> frozenset[str]:
    return frozenset(_KIND_CAPABILITIES)


def runtime_kind_profile(kind: str) -> RuntimeCapabilityProfile | None:
    normalized = str(kind or "").strip()
    return _KIND_CAPABILITIES.get(normalized)


def runtime_profile_capabilities(runtime_cfg: Mapping[str, Any] | None) -> RuntimeCapabilityProfile | None:
    cfg = runtime_cfg if isinstance(runtime_cfg, Mapping) else {}
    kind = str(cfg.get("kind") or "").strip()
    profile = runtime_kind_profile(kind)
    if profile is None:
        return None

    capabilities = set(profile.capabilities)
    if kind == "codex-app-server":
        mode = str(cfg.get("mode") or ("external" if cfg.get("endpoint") else "managed")).strip()
        if mode == "external":
            capabilities.add(CAP_SERVICE_REQUIRED)
    return RuntimeCapabilityProfile(
        kind=profile.kind,
        capabilities=frozenset(capabilities),
        summary=profile.summary,
    )


def explicit_required_capabilities(config: Mapping[str, Any] | None) -> frozenset[str]:
    if not isinstance(config, Mapping):
        return frozenset()
    raw = config.get("required-capabilities")
    if raw is None:
        return frozenset()
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = raw
    else:
        return frozenset({f"<invalid:{type(raw).__name__}>"})
    return frozenset(str(value).strip() for value in values if str(value).strip())


def unknown_capabilities(values: set[str] | frozenset[str]) -> frozenset[str]:
    return frozenset(set(values) - KNOWN_CAPABILITIES)


def format_capabilities(values: set[str] | frozenset[str]) -> str:
    return ", ".join(sorted(values)) if values else "none"


__all__ = [
    "CAP_ACTIVITY_HEARTBEAT",
    "CAP_CANCEL",
    "CAP_COMMAND_STAGE",
    "CAP_ONE_SHOT",
    "CAP_PERSISTENT_SESSION",
    "CAP_PROMPT_TURN",
    "CAP_RESUME",
    "CAP_SERVICE_REQUIRED",
    "CAP_STRUCTURED_EVENTS",
    "CAP_THREAD_VISIBLE",
    "CAP_TOKEN_METRICS",
    "KNOWN_CAPABILITIES",
    "RuntimeCapabilityProfile",
    "explicit_required_capabilities",
    "format_capabilities",
    "recognized_runtime_kinds",
    "runtime_kind_profile",
    "runtime_profile_capabilities",
    "unknown_capabilities",
]
