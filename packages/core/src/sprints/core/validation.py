"""Workflow contract validation and readiness recommendations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import jsonschema
import yaml

from sprints.core.contracts import (
    WorkflowContract,
    WorkflowContractError,
    load_workflow_contract,
)
from sprints.workflows.registry import Workflow, load_workflow_object
from sprints.core.bindings import (
    runtime_availability_checks,
    runtime_binding_checks,
    runtime_stage_checks,
)


def validate_workflow_contract(workflow_root: Path) -> dict[str, Any]:
    root = Path(workflow_root).expanduser().resolve()
    checks: list[dict[str, Any]] = []
    contract: WorkflowContract | None = None
    workflow_name: str | None = None
    schema_version: int | None = None
    source_path: str | None = None
    try:
        contract = load_workflow_contract(root)
        source_path = str(contract.source_path)
        checks.append(_check("contract-file", "pass", str(contract.source_path)))
        checks.append(_contract_kind_check(contract))
    except FileNotFoundError as exc:
        checks.append(_check("contract-file", "fail", str(exc)))
    except (WorkflowContractError, OSError, UnicodeDecodeError) as exc:
        checks.append(_check("contract-file", "fail", str(exc)))
    if contract is None:
        return _validation_report(
            root, source_path, workflow_name, schema_version, checks
        )

    config = contract.config
    workflow_name = str(config.get("workflow") or "").strip() or None
    if not workflow_name:
        checks.append(
            _check("workflow-field", "fail", "top-level workflow field is required")
        )
        return _validation_report(
            root, source_path, workflow_name, schema_version, checks
        )
    checks.append(_check("workflow-field", "pass", workflow_name))

    workflow: Workflow | None = None
    try:
        workflow = load_workflow_object(workflow_name)
        checks.append(
            _check(
                "workflow-package",
                "pass",
                f"workflows.{workflow_name.replace('-', '_')}",
            )
        )
    except Exception as exc:
        checks.append(_check("workflow-package", "fail", str(exc)))

    if workflow is not None:
        try:
            schema = yaml.safe_load(workflow.schema_path.read_text(encoding="utf-8"))
            if not isinstance(schema, dict):
                raise WorkflowContractError(
                    f"{workflow.schema_path} must decode to a mapping"
                )
            errors = _schema_errors(config=config, schema=schema)
            checks.append(
                _check(
                    "schema", "fail", f"{len(errors)} schema violation(s)", items=errors
                )
                if errors
                else _check("schema", "pass", str(workflow.schema_path))
            )
        except Exception as exc:
            checks.append(_check("schema", "fail", str(exc)))
        try:
            schema_version = int(config.get("schema-version", 1))
            if schema_version not in workflow.schema_versions:
                checks.append(
                    _check(
                        "schema-version",
                        "fail",
                        f"schema-version={schema_version} not supported; supported={list(workflow.schema_versions)}",
                    )
                )
            else:
                checks.append(_check("schema-version", "pass", str(schema_version)))
        except Exception as exc:
            checks.append(_check("schema-version", "fail", str(exc)))

    checks.append(_instance_name_check(workflow_root=root, config=config))
    checks.append(_repository_path_check(workflow_root=root, config=config))
    checks.extend(runtime_stage_checks(config))
    checks.extend(runtime_binding_checks(config))
    checks.extend(runtime_availability_checks(config))

    return _validation_report(root, source_path, workflow_name, schema_version, checks)


def build_readiness_recommendations(
    checks: list[dict[str, Any]],
    *,
    workflow: str | None = None,
    workflow_root: str | Path | None = None,
    source_path: str | Path | None = None,
) -> list[str]:
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
                "Run `hermes sprints bootstrap` from the target repo, or pass `--workflow-root` for an existing workflow instance.",
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
                "Use a bundled workflow name or reinstall the Sprints plugin.",
            )
        elif name == "schema":
            _append_once(
                recommendations,
                f"Edit the YAML front matter in {source} and fix the listed schema paths.",
            )
        elif name == "schema-version":
            _append_once(
                recommendations,
                "Set `schema-version` to a version supported by this plugin, or update the installed Sprints plugin.",
            )
        elif name == "instance-name":
            _append_once(
                recommendations,
                "Make `instance.name` match the workflow root directory name, or rerun `hermes sprints bootstrap`.",
            )
        elif name == "repository-path":
            _append_once(
                recommendations,
                "Set `repository.local-path` to an existing local checkout path.",
            )
        elif name.startswith("runtime-binding"):
            _append_once(
                recommendations, _runtime_binding_recommendation(workflow=workflow)
            )
        elif name.startswith("runtime-stage"):
            _append_once(
                recommendations,
                "Fix the actor/stage runtime references in `WORKFLOW.md`, then rerun `hermes sprints validate`.",
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
                "Run `hermes sprints validate --format json` and fix the tracker configuration before starting the service.",
            )
        elif name == "workspace-root":
            _append_once(
                recommendations,
                "Create the configured workspace root or fix filesystem permissions for the workflow user.",
            )
        elif name in {"engine_event_retention", "engine-event-retention"}:
            _append_once(
                recommendations,
                "Configure or apply event retention with `hermes sprints events stats` and `hermes sprints events prune`.",
            )
        elif status == "fail":
            _append_once(
                recommendations,
                f"Fix failing check `{name}`: {detail or 'see JSON output for details'}.",
            )
    return recommendations


def _check(name: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    payload = {"name": name, "status": status, "detail": detail}
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def _json_path(parts: Any) -> str:
    values = [str(part) for part in parts]
    return ".".join(values) if values else "<root>"


def _schema_errors(
    *, config: dict[str, Any], schema: dict[str, Any]
) -> list[dict[str, Any]]:
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(
        validator.iter_errors(config), key=lambda item: list(item.absolute_path)
    )
    return [
        {
            "path": _json_path(error.absolute_path),
            "message": error.message,
            "validator": str(error.validator),
        }
        for error in errors
    ]


def _repository_path_check(
    *, workflow_root: Path, config: dict[str, Any]
) -> dict[str, Any]:
    repository = config.get("repository") or {}
    if not isinstance(repository, dict):
        return _check("repository-path", "fail", "repository must be a mapping")
    raw = str(
        repository.get("local-path") or repository.get("local_path") or ""
    ).strip()
    if not raw:
        return _check("repository-path", "fail", "repository.local-path is required")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (workflow_root / path).resolve()
    if not path.exists():
        return _check(
            "repository-path", "fail", f"repository.local-path does not exist: {path}"
        )
    if not path.is_dir():
        return _check(
            "repository-path",
            "fail",
            f"repository.local-path is not a directory: {path}",
        )
    return _check("repository-path", "pass", str(path))


def _instance_name_check(
    *, workflow_root: Path, config: dict[str, Any]
) -> dict[str, Any]:
    instance = config.get("instance") or {}
    if not isinstance(instance, dict):
        return _check("instance-name", "fail", "instance must be a mapping")
    name = str(instance.get("name") or "").strip()
    if not name:
        return _check("instance-name", "fail", "instance.name is required")
    if name != workflow_root.name:
        return _check(
            "instance-name",
            "fail",
            f"instance.name={name!r} must match workflow root directory {workflow_root.name!r}",
        )
    return _check("instance-name", "pass", name)


def _contract_kind_check(contract: WorkflowContract) -> dict[str, Any]:
    if contract.source_path.suffix.lower() == ".md":
        return _check(
            "contract-format", "pass", "repo-owned Markdown workflow contract"
        )
    return _check(
        "contract-format",
        "fail",
        f"unsupported workflow contract format: {contract.source_path}",
    )


def _validation_report(
    workflow_root: Path,
    source_path: str | None,
    workflow_name: str | None,
    schema_version: int | None,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    failures = [check for check in checks if check.get("status") == "fail"]
    warnings = [check for check in checks if check.get("status") == "warn"]
    return {
        "ok": not failures,
        "workflow_root": str(workflow_root),
        "source_path": source_path,
        "workflow": workflow_name,
        "schema_version": schema_version,
        "checks": checks,
        "failures": failures,
        "warnings": warnings,
        "recommendations": build_readiness_recommendations(
            checks,
            workflow=workflow_name,
            workflow_root=workflow_root,
            source_path=source_path,
        ),
    }


def _check_name(check: dict[str, Any]) -> str:
    raw = str(check.get("name") or check.get("code") or "check").strip()
    return raw.replace("_", "-") if raw.startswith("runtime_") else raw


def _append_once(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _runtime_binding_recommendation(*, workflow: str | None) -> str:
    del workflow
    return "Run `hermes sprints configure-runtime` for the affected role, or define the referenced runtime profile manually."


def _runtime_availability_recommendation(detail: str) -> str:
    lowered = detail.lower()
    if (
        "127.0.0.1:4500" in lowered
        or "codex-app-server" in lowered
        or "ws://" in lowered
    ):
        return "Start or diagnose the shared Codex listener with `hermes sprints codex-app-server up` and `hermes sprints codex-app-server doctor`."
    if "hermes" in lowered:
        return "Install Hermes Agent on PATH, or set `runtimes.<name>.executable` / `command` to the correct Hermes binary."
    if "gh" in lowered:
        return "Install GitHub CLI and authenticate with `gh auth login`."
    return (
        "Install the required runtime CLI on PATH or edit the runtime profile command."
    )
