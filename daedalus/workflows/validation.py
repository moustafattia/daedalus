"""Workflow contract validation used by operator commands and service setup."""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from . import load_workflow
from .contract import WorkflowContract, WorkflowContractError, load_workflow_contract
from .readiness import build_readiness_recommendations
from .runtime_presets import runtime_binding_checks


SERVICE_MODES = frozenset({"active", "shadow"})


def _check(name: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    payload = {"name": name, "status": status, "detail": detail}
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def _json_path(parts: Any) -> str:
    values = [str(part) for part in parts]
    return ".".join(values) if values else "<root>"


def _schema_error_item(error: jsonschema.ValidationError) -> dict[str, Any]:
    return {
        "path": _json_path(error.absolute_path),
        "message": error.message,
        "validator": str(error.validator),
    }


def _schema_errors(*, config: dict[str, Any], schema: dict[str, Any]) -> list[dict[str, Any]]:
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(config), key=lambda item: list(item.absolute_path))
    return [_schema_error_item(error) for error in errors]


def _call_preflight(preflight_fn: Any, *, config: dict[str, Any], workflow_root: Path) -> Any:
    signature = inspect.signature(preflight_fn)
    if "workflow_root" in signature.parameters:
        return preflight_fn(config, workflow_root=workflow_root)
    return preflight_fn(config)


def _repository_path_check(*, workflow_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    repository = config.get("repository") or {}
    if not isinstance(repository, dict):
        return _check("repository-path", "fail", "repository must be a mapping")
    raw = str(repository.get("local-path") or repository.get("local_path") or "").strip()
    if not raw:
        return _check("repository-path", "fail", "repository.local-path is required")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (workflow_root / path).resolve()
    if not path.exists():
        return _check("repository-path", "fail", f"repository.local-path does not exist: {path}")
    if not path.is_dir():
        return _check("repository-path", "fail", f"repository.local-path is not a directory: {path}")
    return _check("repository-path", "pass", str(path))


def _instance_name_check(*, workflow_root: Path, config: dict[str, Any]) -> dict[str, Any]:
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


def _service_mode_check(*, workflow_name: str | None, service_mode: str | None) -> dict[str, Any] | None:
    if not service_mode:
        return None
    if service_mode not in SERVICE_MODES:
        return _check(
            "service-mode",
            "fail",
            f"unknown service mode {service_mode!r}; expected one of {sorted(SERVICE_MODES)}",
        )
    if workflow_name == "issue-runner" and service_mode != "active":
        return _check(
            "service-mode",
            "fail",
            "issue-runner supports only active supervised mode; use --service-mode active",
        )
    return _check("service-mode", "pass", f"{workflow_name}:{service_mode}")


def _contract_kind_check(contract: WorkflowContract) -> dict[str, Any]:
    if contract.source_path.suffix.lower() == ".md":
        return _check("contract-format", "pass", "repo-owned Markdown workflow contract")
    return _check(
        "contract-format",
        "fail",
        f"unsupported workflow contract format: {contract.source_path}",
    )


def validate_workflow_contract(
    workflow_root: Path,
    *,
    service_mode: str | None = None,
    run_preflight: bool = True,
) -> dict[str, Any]:
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
            workflow_root=root,
            source_path=source_path,
            workflow_name=workflow_name,
            schema_version=schema_version,
            checks=checks,
        )

    config = contract.config
    workflow_name = str(config.get("workflow") or "").strip() or None
    if not workflow_name:
        checks.append(_check("workflow-field", "fail", "top-level workflow field is required"))
        return _validation_report(
            workflow_root=root,
            source_path=source_path,
            workflow_name=workflow_name,
            schema_version=schema_version,
            checks=checks,
        )
    checks.append(_check("workflow-field", "pass", workflow_name))

    module = None
    try:
        module = load_workflow(workflow_name)
        checks.append(_check("workflow-package", "pass", f"workflows.{workflow_name.replace('-', '_')}"))
    except Exception as exc:
        checks.append(_check("workflow-package", "fail", str(exc)))

    if module is not None:
        try:
            schema = yaml.safe_load(module.CONFIG_SCHEMA_PATH.read_text(encoding="utf-8"))
            if not isinstance(schema, dict):
                raise WorkflowContractError(f"{module.CONFIG_SCHEMA_PATH} must decode to a mapping")
            errors = _schema_errors(config=config, schema=schema)
            if errors:
                checks.append(
                    _check(
                        "schema",
                        "fail",
                        f"{len(errors)} schema violation(s)",
                        items=errors,
                    )
                )
            else:
                checks.append(_check("schema", "pass", str(module.CONFIG_SCHEMA_PATH)))
        except Exception as exc:
            checks.append(_check("schema", "fail", str(exc)))

        try:
            schema_version = int(config.get("schema-version", 1))
            supported = tuple(int(item) for item in module.SUPPORTED_SCHEMA_VERSIONS)
            if schema_version not in supported:
                checks.append(
                    _check(
                        "schema-version",
                        "fail",
                        f"schema-version={schema_version} not supported; supported={list(supported)}",
                    )
                )
            else:
                checks.append(_check("schema-version", "pass", str(schema_version)))
        except Exception as exc:
            checks.append(_check("schema-version", "fail", str(exc)))

    service_check = _service_mode_check(workflow_name=workflow_name, service_mode=service_mode)
    if service_check is not None:
        checks.append(service_check)

    checks.append(_instance_name_check(workflow_root=root, config=config))
    checks.append(_repository_path_check(workflow_root=root, config=config))
    checks.extend(runtime_binding_checks(config))

    if module is not None and run_preflight:
        preflight_fn = getattr(module, "run_preflight", None)
        if callable(preflight_fn):
            try:
                result = _call_preflight(preflight_fn, config=config, workflow_root=root)
                ok = bool(getattr(result, "ok", True))
                code = getattr(result, "error_code", None)
                detail = getattr(result, "error_detail", None)
                checks.append(
                    _check(
                        "workflow-preflight",
                        "pass" if ok else "fail",
                        "ok" if ok else f"code={code} detail={detail}",
                        error_code=code,
                        error_detail=detail,
                    )
                )
            except Exception as exc:
                checks.append(_check("workflow-preflight", "fail", f"{type(exc).__name__}: {exc}"))
        else:
            checks.append(_check("workflow-preflight", "skip", "workflow has no preflight hook"))

    return _validation_report(
        workflow_root=root,
        source_path=source_path,
        workflow_name=workflow_name,
        schema_version=schema_version,
        checks=checks,
    )


def _validation_report(
    *,
    workflow_root: Path,
    source_path: str | None,
    workflow_name: str | None,
    schema_version: int | None,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    failures = [check for check in checks if check.get("status") == "fail"]
    warnings = [check for check in checks if check.get("status") == "warn"]
    recommendations = build_readiness_recommendations(
        checks,
        workflow=workflow_name,
        workflow_root=workflow_root,
        source_path=source_path,
    )
    return {
        "ok": not failures,
        "workflow_root": str(workflow_root),
        "source_path": source_path,
        "workflow": workflow_name,
        "schema_version": schema_version,
        "checks": checks,
        "failures": failures,
        "warnings": warnings,
        "recommendations": recommendations,
    }
