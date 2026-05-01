"""Symphony §6.3 dispatch preflight validation.

Pure function: takes the parsed config dict, returns PreflightResult.
No side effects. Cheap (<1ms). Called from the CLI tick path before
dispatch; reconciliation runs regardless of preflight outcome.

Error codes (fixed enum, mirrors Symphony's recommended categories):

- ``missing_workflow_file``        — file not found / unreadable
- ``workflow_parse_error``         — YAML syntax error
- ``workflow_front_matter_not_a_map`` — root not a dict
- ``unsupported_runtime_kind``     — runtime.kind not in registered kinds
- ``unsupported_reviewer_kind``    — reviewer kind not in registered kinds
- ``missing_tracker_credentials``  — required env var unset / empty
- ``unsupported_tracker_kind``     — tracker.kind not supported
- ``unsupported_code_host_kind``   — code-host.kind not supported
- ``workspace_root_unwritable``    — workspace.root missing or not writable
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    error_code: str | None
    error_detail: str | None
    can_reconcile: bool = True  # always True; preflight never blocks reconciliation


_RECOGNIZED_RUNTIME_KINDS = frozenset({"acpx-codex", "claude-cli", "codex-app-server", "hermes-agent"})
_RECOGNIZED_GATE_TYPES = frozenset({"agent-review", "pr-comment-approval", "code-host-checks"})
_RECOGNIZED_TRACKER_KINDS = frozenset({"github"})
_RECOGNIZED_CODE_HOST_KINDS = frozenset({"github"})


def run_preflight(config: Mapping[str, Any]) -> PreflightResult:
    """Validate the workflow config for dispatch readiness.

    Pure: only inspects the dict and reads ``os.environ`` for ``$VAR``
    token resolution. Caller is responsible for ensuring the file was
    parseable; this function only inspects the already-parsed structure.
    """
    if not isinstance(config, dict):
        return PreflightResult(
            False,
            "workflow_front_matter_not_a_map",
            f"expected dict, got {type(config).__name__}",
        )

    # Codex P2 on PR #21: walk the actual schema field paths.
    # Change-delivery workflow contract shape:
    #   runtimes:
    #     <name>: { kind: acpx-codex | claude-cli | hermes-agent, ... }
    #     <name>: { ... }
    #   actors:
    #     <name>: { model: ..., runtime: <runtimes key> }
    #   gates:
    #     <name>: { type: agent-review | pr-comment-approval | code-host-checks, ... }
    runtimes = config.get("runtimes") or {}
    if isinstance(runtimes, dict):
        for name, rt_cfg in runtimes.items():
            if not isinstance(rt_cfg, dict):
                continue
            rk = rt_cfg.get("kind")
            if rk and rk not in _RECOGNIZED_RUNTIME_KINDS:
                return PreflightResult(
                    False,
                    "unsupported_runtime_kind",
                    f"runtimes.{name}.kind={rk!r} not in {sorted(_RECOGNIZED_RUNTIME_KINDS)}",
                )

    gates = config.get("gates") or {}
    if isinstance(gates, dict):
        for name, gate in gates.items():
            if not isinstance(gate, dict):
                continue
            gate_type = gate.get("type")
            if gate_type and gate_type not in _RECOGNIZED_GATE_TYPES:
                return PreflightResult(
                    False,
                    "unsupported_reviewer_kind",
                    f"gates.{name}.type={gate_type!r} not in {sorted(_RECOGNIZED_GATE_TYPES)}",
                )

    tracker = config.get("tracker") or {}
    if isinstance(tracker, dict):
        tk = tracker.get("kind")
        if tk and tk not in _RECOGNIZED_TRACKER_KINDS:
            return PreflightResult(
                False,
                "unsupported_tracker_kind",
                f"tracker.kind={tk!r} not in {sorted(_RECOGNIZED_TRACKER_KINDS)}",
            )

    code_host = config.get("code-host") or {}
    if isinstance(code_host, dict):
        chk = code_host.get("kind")
        if chk and chk not in _RECOGNIZED_CODE_HOST_KINDS:
            return PreflightResult(
                False,
                "unsupported_code_host_kind",
                f"code-host.kind={chk!r} not in {sorted(_RECOGNIZED_CODE_HOST_KINDS)}",
            )

    # Tracker credential resolution — if config references a $VAR_NAME and
    # it's unset / empty, that's missing_tracker_credentials.
    repo_section = config.get("repository") or {}
    if isinstance(repo_section, dict):
        for k in ("github-token", "token"):
            v = repo_section.get(k)
            if isinstance(v, str) and v.startswith("$"):
                env_name = v[1:]
                if not os.environ.get(env_name):
                    return PreflightResult(
                        False,
                        "missing_tracker_credentials",
                        f"{k}={v!r} env var is unset or empty",
                    )

    return PreflightResult(True, None, None)
