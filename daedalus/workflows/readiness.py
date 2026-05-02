from __future__ import annotations

from pathlib import Path
from typing import Any


def build_readiness_recommendations(
    checks: list[dict[str, Any]],
    *,
    workflow: str | None = None,
    workflow_root: str | Path | None = None,
    source_path: str | Path | None = None,
) -> list[str]:
    """Return concise operator next steps for failing/warning checks."""

    del workflow_root
    recommendations: list[str] = []
    source = str(source_path) if source_path else "WORKFLOW.md"
    for check in checks:
        status = str(check.get("status") or "").lower()
        if status not in {"fail", "warn"}:
            continue
        name = _check_name(check)
        detail = str(check.get("detail") or check.get("summary") or "")

        if name == "contract-file":
            _append_once(
                recommendations,
                "Run `hermes daedalus bootstrap` from the target repo, or pass `--workflow-root` for an existing workflow instance.",
            )
        elif name == "contract-format":
            _append_once(
                recommendations,
                "Use a repo-owned `WORKFLOW.md` / `WORKFLOW-<name>.md` contract before publishing the workflow.",
            )
        elif name == "workflow-field":
            _append_once(recommendations, f"Add top-level `workflow:` to {source}.")
        elif name == "workflow-package":
            _append_once(
                recommendations,
                "Use a bundled workflow (`issue-runner` or `change-delivery`) or reinstall the Daedalus plugin.",
            )
        elif name == "schema":
            _append_once(
                recommendations,
                f"Edit the YAML front matter in {source} and fix the listed schema paths.",
            )
        elif name == "schema-version":
            _append_once(
                recommendations,
                "Set `schema-version` to a version supported by this plugin, or update the installed Daedalus plugin.",
            )
        elif name == "instance-name":
            _append_once(
                recommendations,
                "Make `instance.name` match the workflow root directory name, or rerun `hermes daedalus bootstrap`.",
            )
        elif name == "repository-path":
            _append_once(
                recommendations,
                "Set `repository.local-path` to an existing local checkout path.",
            )
        elif name == "workflow-preflight":
            _append_once(recommendations, _preflight_recommendation(check=check, workflow=workflow))
        elif name.startswith("runtime-binding"):
            _append_once(recommendations, _runtime_binding_recommendation(workflow=workflow))
        elif name.startswith("runtime-stage"):
            _append_once(
                recommendations,
                "Fix the actor/stage runtime references in `WORKFLOW.md`, then rerun `hermes daedalus validate`.",
            )
        elif name.startswith("runtime-capability"):
            _append_once(
                recommendations,
                "Bind the role to a runtime with the required capabilities, or remove the explicit `required-capabilities` entry.",
            )
        elif name.startswith("runtime-availability"):
            _append_once(recommendations, _runtime_availability_recommendation(detail))
        elif name == "github-auth":
            _append_once(
                recommendations,
                "Run `gh auth status` and `gh auth login` for the configured GitHub host.",
            )
        elif name == "github-repo":
            _append_once(
                recommendations,
                "Check `tracker.github_slug`, `code-host.github_slug`, and local GitHub access with `gh repo view`.",
            )
        elif name == "tracker":
            _append_once(
                recommendations,
                "Run `hermes daedalus validate --format json` and fix the tracker configuration before starting the service.",
            )
        elif name == "workspace-root":
            _append_once(
                recommendations,
                "Create the configured workspace root or fix filesystem permissions for the workflow user.",
            )
        elif name in {"engine_event_retention", "engine-event-retention"}:
            _append_once(
                recommendations,
                "Configure or apply event retention with `hermes daedalus events stats` and `hermes daedalus events prune`.",
            )
        elif status == "fail":
            _append_once(
                recommendations,
                f"Fix failing check `{name}`: {detail or 'see JSON output for details'}.",
            )
    return recommendations


def _check_name(check: dict[str, Any]) -> str:
    raw = str(check.get("name") or check.get("code") or "check").strip()
    return raw.replace("_", "-") if raw.startswith("runtime_") else raw


def _append_once(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _preflight_recommendation(*, check: dict[str, Any], workflow: str | None) -> str:
    detail = str(check.get("error_detail") or check.get("detail") or "")
    lowered = detail.lower()
    if "agent.runtime" in lowered or "runtime" in lowered:
        return _runtime_binding_recommendation(workflow=workflow)
    if "tracker.path" in lowered or "issues.json" in lowered:
        return "Remove the legacy tracker path config and use a supported tracker kind."
    if "github" in lowered or "gh " in lowered:
        return "Run `gh auth status`, verify `tracker.github_slug`, then rerun `hermes daedalus validate`."
    return "Fix the workflow preflight detail shown above, then rerun `hermes daedalus validate`."


def _runtime_binding_recommendation(*, workflow: str | None) -> str:
    if workflow == "issue-runner":
        return "Run `hermes daedalus configure-runtime --runtime codex-app-server --role agent`, or define the referenced runtime profile manually."
    if workflow == "change-delivery":
        return "Run `hermes daedalus configure-runtime --runtime codex-app-server --role implementer`, or define the referenced runtime profile manually."
    return "Run `hermes daedalus configure-runtime` for the affected role, or define the referenced runtime profile manually."


def _runtime_availability_recommendation(detail: str) -> str:
    lowered = detail.lower()
    if "127.0.0.1:4500" in lowered or "codex-app-server" in lowered or "ws://" in lowered:
        return "Start or diagnose the shared Codex listener with `hermes daedalus codex-app-server up` and `hermes daedalus codex-app-server doctor`."
    if "hermes" in lowered:
        return "Install Hermes Agent on PATH, or set `runtimes.<name>.executable` / `command` to the correct Hermes binary."
    if "gh" in lowered:
        return "Install GitHub CLI and authenticate with `gh auth login`."
    return "Install the required runtime CLI on PATH or edit the runtime profile command."


__all__ = ["build_readiness_recommendations"]
