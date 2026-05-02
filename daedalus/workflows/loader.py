"""Workflow loading, contracts, validation, and runtime binding helpers."""
from __future__ import annotations

import copy
import importlib
import inspect
import re
import shutil
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Protocol, runtime_checkable

import jsonschema
import yaml

from runtimes import recognized_runtime_kinds
from workflows.config import AgenticConfig

DEFAULT_WORKFLOW_MARKDOWN_FILENAME = "WORKFLOW.md"
WORKFLOW_MARKDOWN_PREFIX = "WORKFLOW-"
WORKFLOW_CONTRACT_POINTER_RELATIVE_PATH = Path("config") / "workflow-contract-path"
WORKFLOW_POLICY_KEY = "workflow-policy"
NAME = "agentic"
SUPPORTED_SCHEMA_VERSIONS = (1,)
CONFIG_SCHEMA_PATH = Path(__file__).with_name("schema.yaml")
PREFLIGHT_GATED_COMMANDS = frozenset()
_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


class WorkflowContractError(RuntimeError):
    """Raised when a workflow contract cannot be loaded or projected."""


class WorkflowPolicyError(RuntimeError):
    """Raised when Markdown policy chunks are missing or malformed."""


class RuntimePresetError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkflowContract:
    source_path: Path
    config: dict[str, Any]
    prompt_template: str
    front_matter: dict[str, Any]


@dataclass(frozen=True)
class ActorPolicy:
    name: str
    body: str


@dataclass(frozen=True)
class WorkflowPolicy:
    orchestrator: str
    actors: dict[str, ActorPolicy]


@runtime_checkable
class Workflow(Protocol):
    name: str
    schema_versions: tuple[int, ...]
    schema_path: Path
    preflight_gated_commands: frozenset[str]

    def load_config(self, *, workflow_root: Path, raw: dict[str, Any]) -> object: ...

    def make_workspace(self, *, workflow_root: Path, config: object) -> object: ...

    def run_cli(self, *, workspace: object, argv: list[str]) -> int: ...

    def run_preflight(self, *, workflow_root: Path, config: object) -> object: ...


@dataclass(frozen=True)
class ModuleWorkflow:
    module: ModuleType

    @property
    def name(self) -> str:
        return self.module.NAME

    @property
    def schema_versions(self) -> tuple[int, ...]:
        return tuple(self.module.SUPPORTED_SCHEMA_VERSIONS)

    @property
    def schema_path(self) -> Path:
        return Path(self.module.CONFIG_SCHEMA_PATH)

    @property
    def preflight_gated_commands(self) -> frozenset[str]:
        return frozenset(getattr(self.module, "PREFLIGHT_GATED_COMMANDS", frozenset()))

    def load_config(self, *, workflow_root: Path, raw: dict[str, Any]) -> object:
        loader = getattr(self.module, "load_config", None)
        if callable(loader):
            return loader(workflow_root=workflow_root, raw=raw)
        return raw

    def make_workspace(self, *, workflow_root: Path, config: object) -> object:
        raw = config.raw if hasattr(config, "raw") else config
        return self.module.make_workspace(workflow_root=workflow_root, config=raw)

    def run_cli(self, *, workspace: object, argv: list[str]) -> int:
        return self.module.cli_main(workspace, argv)

    def run_preflight(self, *, workflow_root: Path, config: object) -> object:
        preflight = getattr(self.module, "run_preflight", None)
        if not callable(preflight):
            return type("PreflightResult", (), {"ok": True})()
        raw = config.raw if hasattr(config, "raw") else config
        signature = inspect.signature(preflight)
        if "workflow_root" in signature.parameters:
            return preflight(raw, workflow_root=workflow_root)
        return preflight(raw)


@dataclass(frozen=True)
class AgenticWorkflow:
    name: str = NAME
    schema_versions: tuple[int, ...] = SUPPORTED_SCHEMA_VERSIONS
    schema_path: Path = CONFIG_SCHEMA_PATH
    preflight_gated_commands: frozenset[str] = PREFLIGHT_GATED_COMMANDS

    def load_config(self, *, workflow_root: Path, raw: dict[str, Any]) -> object:
        return load_config(workflow_root=workflow_root, raw=raw)

    def make_workspace(self, *, workflow_root: Path, config: object) -> object:
        return make_workspace(workflow_root=workflow_root, config=config)

    def run_cli(self, *, workspace: object, argv: list[str]) -> int:
        from workflows.runner import main

        return main(workspace, argv)

    def run_preflight(self, *, workflow_root: Path, config: object) -> object:
        del workflow_root, config
        return type("PreflightResult", (), {"ok": True})()


WORKFLOW = AgenticWorkflow()


def load_config(*, workflow_root: Path, raw: dict[str, Any]) -> AgenticConfig:
    return AgenticConfig.from_raw(raw=raw, workflow_root=workflow_root)


def make_workspace(*, workflow_root: Path, config: object) -> AgenticConfig:
    if isinstance(config, AgenticConfig):
        return config
    if isinstance(config, dict):
        return AgenticConfig.from_raw(raw=config, workflow_root=workflow_root)
    raise TypeError(f"unsupported agentic config object: {type(config).__name__}")


def workflow_markdown_path(workflow_root: Path) -> Path:
    return workflow_root.resolve() / DEFAULT_WORKFLOW_MARKDOWN_FILENAME


def workflow_named_markdown_filename(workflow_name: str) -> str:
    return f"{WORKFLOW_MARKDOWN_PREFIX}{workflow_name}.md"


def workflow_named_markdown_path(repo_root: Path, workflow_name: str) -> Path:
    return repo_root.resolve() / workflow_named_markdown_filename(workflow_name)


def workflow_contract_pointer_path(workflow_root: Path) -> Path:
    return workflow_root.resolve() / WORKFLOW_CONTRACT_POINTER_RELATIVE_PATH


def read_workflow_contract_pointer(workflow_root: Path) -> Path | None:
    pointer_path = workflow_contract_pointer_path(workflow_root)
    if not pointer_path.exists():
        return None
    try:
        raw = pointer_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    target = Path(raw).expanduser()
    if not target.is_absolute():
        target = (pointer_path.parent / target).resolve()
    else:
        target = target.resolve()
    return target


def write_workflow_contract_pointer(workflow_root: Path, contract_path: Path) -> Path:
    pointer_path = workflow_contract_pointer_path(workflow_root)
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(str(contract_path.resolve()) + "\n", encoding="utf-8")
    return pointer_path


def find_repo_workflow_contract_path(repo_root: Path, *, workflow_name: str | None = None) -> Path | None:
    root = repo_root.resolve()
    if workflow_name:
        named_path = workflow_named_markdown_path(root, workflow_name)
        if named_path.exists():
            return named_path
    default_path = workflow_markdown_path(root)
    candidates = _repo_workflow_candidates(root)
    if workflow_name and default_path.exists() and _workflow_name_for_contract_path(default_path) == workflow_name:
        return default_path
    if default_path.exists() and not workflow_name:
        return default_path
    if len(candidates) == 1:
        return candidates[0]
    return None


def find_workflow_contract_path(workflow_root: Path, *, workflow_name: str | None = None) -> Path | None:
    root = workflow_root.resolve()
    pointer_target = read_workflow_contract_pointer(root)
    if pointer_target is not None and pointer_target.exists():
        return pointer_target
    repo_owned = find_repo_workflow_contract_path(root, workflow_name=workflow_name)
    if repo_owned is not None:
        return repo_owned
    markdown_path = workflow_markdown_path(root)
    return markdown_path if markdown_path.exists() else None


def load_workflow_contract(workflow_root: Path) -> WorkflowContract:
    path = find_workflow_contract_path(workflow_root)
    if path is None:
        raise FileNotFoundError(
            f"workflow contract not found under {Path(workflow_root).resolve()} "
            f"(looked for {DEFAULT_WORKFLOW_MARKDOWN_FILENAME} / WORKFLOW-<name>.md)"
        )
    return load_workflow_contract_file(path)


def load_workflow_contract_file(path: Path) -> WorkflowContract:
    resolved = Path(path).expanduser().resolve()
    if resolved.suffix.lower() != ".md":
        raise WorkflowContractError(f"unsupported workflow contract format for {resolved}; expected Markdown (.md)")
    text = resolved.read_text(encoding="utf-8")
    front_matter, prompt_template = _parse_markdown_contract(resolved, text)
    return WorkflowContract(
        source_path=resolved,
        config=_project_markdown_front_matter(path=resolved, front_matter=front_matter, prompt_template=prompt_template),
        prompt_template=prompt_template,
        front_matter=front_matter,
    )


def render_workflow_markdown(*, config: dict[str, Any], prompt_template: str | None = None) -> str:
    front_matter = deepcopy(config)
    body = prompt_template
    if body is None:
        policy = front_matter.pop(WORKFLOW_POLICY_KEY, "")
        if policy is None:
            body = ""
        elif isinstance(policy, str):
            body = policy
        else:
            raise WorkflowContractError(f"{WORKFLOW_POLICY_KEY} must be a string when rendering WORKFLOW.md")
    else:
        front_matter.pop(WORKFLOW_POLICY_KEY, None)
    if not isinstance(front_matter, dict):
        raise WorkflowContractError("workflow config must be a mapping when rendering WORKFLOW.md")
    front_matter_text = yaml.safe_dump(front_matter, sort_keys=False).strip()
    body_text = str(body or "").strip()
    if body_text:
        return f"---\n{front_matter_text}\n---\n\n{body_text}\n"
    return f"---\n{front_matter_text}\n---\n"


def parse_workflow_policy(markdown_body: str) -> WorkflowPolicy:
    sections: list[tuple[str, str]] = []
    body = markdown_body or ""
    matches = list(_HEADING_RE.finditer(body))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections.append((match.group(1).strip(), body[start:end].strip()))
    orchestrator = ""
    actors: dict[str, ActorPolicy] = {}
    for title, section_body in sections:
        if title == "Orchestrator Policy":
            orchestrator = section_body
        elif title.startswith("Actor:"):
            name = title.split(":", 1)[1].strip()
            if not name:
                raise WorkflowPolicyError("actor policy heading is missing a name")
            actors[name] = ActorPolicy(name=name, body=section_body)
    if not orchestrator:
        raise WorkflowPolicyError("missing # Orchestrator Policy section")
    if not actors:
        raise WorkflowPolicyError("missing # Actor: <name> policy sections")
    return WorkflowPolicy(orchestrator=orchestrator, actors=actors)


def load_workflow(name: str) -> ModuleType:
    workflow = load_workflow_object(name)
    module = _import_workflow_module(name)
    if module.NAME != workflow.name:
        raise WorkflowContractError(f"workflow module for {name!r} declares NAME={module.NAME!r}, expected {workflow.name!r}")
    return module


def load_workflow_object(name: str) -> Workflow:
    if name == NAME:
        return WORKFLOW
    module = _import_workflow_module(name)
    workflow = getattr(module, "WORKFLOW", None) or ModuleWorkflow(module)
    if workflow.name != name:
        raise WorkflowContractError(f"workflow module for {name!r} declares NAME={workflow.name!r}")
    return workflow


def run_cli(workflow_root: Path, argv: list[str], *, require_workflow: str | None = None) -> int:
    contract = load_workflow_contract(workflow_root)
    raw_config = contract.config
    workflow_name = raw_config.get("workflow")
    if not workflow_name:
        raise WorkflowContractError(f"{contract.source_path} is missing top-level `workflow:` field")
    if require_workflow and workflow_name != require_workflow:
        raise WorkflowContractError(
            f"{contract.source_path} declares workflow={workflow_name!r}, "
            f"but invocation pins require_workflow={require_workflow!r}"
        )
    workflow = load_workflow_object(str(workflow_name))
    schema = yaml.safe_load(workflow.schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(raw_config, schema)
    schema_version = int(raw_config.get("schema-version", 1))
    if schema_version not in workflow.schema_versions:
        raise WorkflowContractError(
            f"workflow {workflow_name!r} does not support schema-version={schema_version}; "
            f"supported: {list(workflow.schema_versions)}"
        )
    config = workflow.load_config(workflow_root=workflow_root, raw=raw_config)
    invoked_command = argv[0] if argv else None
    if invoked_command in workflow.preflight_gated_commands:
        result = workflow.run_preflight(workflow_root=workflow_root, config=config)
        if not getattr(result, "ok", True):
            _emit_dispatch_skipped_event(
                workflow_root=workflow_root,
                workflow_name=str(workflow_name),
                error_code=getattr(result, "error_code", None),
                error_detail=getattr(result, "error_detail", None),
            )
            raise WorkflowContractError(
                f"dispatch preflight failed for workflow {workflow_name!r}: "
                f"code={getattr(result, 'error_code', None)} detail={getattr(result, 'error_detail', None)}"
            )
    workspace = workflow.make_workspace(workflow_root=workflow_root, config=config)
    return workflow.run_cli(workspace=workspace, argv=argv)


def list_workflows() -> list[str]:
    return [NAME]


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
    "hermes-chat": {"kind": "hermes-agent", "mode": "chat", "source": "daedalus"},
}


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
            render_workflow_markdown(config=config, prompt_template=contract.prompt_template),
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
        "capability_checks": runtime_capability_checks(config),
        "availability_checks": runtime_availability_checks(config),
        "dry_run": dry_run,
    }


def bind_runtime_role(*, config: dict[str, Any], workflow_name: str, role: str, runtime_name: str) -> list[str]:
    del workflow_name
    actors = config.setdefault("actors", {})
    if not isinstance(actors, dict):
        raise RuntimePresetError("top-level actors must be a mapping")
    normalized = _normalize_role(role)
    names = sorted(str(name) for name in actors) if normalized == "all" else [normalized]
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
        _append_binding(bindings, role=str(role), runtime_name=runtime_name, runtimes=runtimes)
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
            checks.append(_check(f"runtime-binding:{role}", "fail", f"{role} has no runtime profile", role=role))
        elif not binding.get("profile_exists"):
            checks.append(
                _check(
                    f"runtime-binding:{role}",
                    "fail",
                    f"{role} references missing runtime profile {runtime_name!r}",
                    role=role,
                    runtime=runtime_name,
                )
            )
        else:
            checks.append(_check(f"runtime-binding:{role}", "pass", f"{role} -> {runtime_name}", role=role, runtime=runtime_name))
    return checks


def runtime_stage_checks(config: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for binding in runtime_stage_bindings(config):
        name = str(binding.get("name") or "runtime-stage")
        if not binding.get("role_exists"):
            checks.append(_check(name, "fail", f"missing actor {binding.get('role')!r}", role=binding.get("role")))
        elif not binding.get("runtime"):
            checks.append(_check(name, "fail", f"actor {binding.get('role')!r} has no runtime", role=binding.get("role")))
        else:
            checks.append(_check(name, "pass", f"{binding.get('role')} -> {binding.get('runtime')}", role=binding.get("role")))
    return checks


def runtime_capability_checks(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _check(f"runtime-capability:{binding.get('role')}", "pass", "capability policy is runtime-owned", role=binding.get("role"))
        for binding in runtime_role_bindings(config)
        if binding.get("runtime")
    ]


def runtime_availability_checks(config: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for name, runtime_cfg in sorted(_runtime_profiles_from_config(config).items()):
        if not isinstance(runtime_cfg, dict):
            checks.append(_check(f"runtime-availability:{name}", "fail", "runtime profile must be a mapping", runtime=name))
            continue
        kind = str(runtime_cfg.get("kind") or "").strip()
        if kind and kind not in recognized_runtime_kinds():
            checks.append(_check(f"runtime-availability:{name}", "warn", f"unknown runtime kind {kind!r}", runtime=name))
            continue
        executable = runtime_cfg.get("executable")
        if executable and shutil.which(str(executable)) is None:
            checks.append(_check(f"runtime-availability:{name}", "fail", f"executable not found: {executable}", runtime=name))
            continue
        checks.append(_check(f"runtime-availability:{name}", "pass", kind or "runtime", runtime=name))
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
            *runtime_capability_checks(config),
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


def validate_workflow_contract(workflow_root: Path, *, run_preflight: bool = True) -> dict[str, Any]:
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
        return _validation_report(root, source_path, workflow_name, schema_version, checks)

    config = contract.config
    workflow_name = str(config.get("workflow") or "").strip() or None
    if not workflow_name:
        checks.append(_check("workflow-field", "fail", "top-level workflow field is required"))
        return _validation_report(root, source_path, workflow_name, schema_version, checks)
    checks.append(_check("workflow-field", "pass", workflow_name))

    workflow: Workflow | None = None
    try:
        workflow = load_workflow_object(workflow_name)
        checks.append(_check("workflow-package", "pass", f"workflows.{workflow_name.replace('-', '_')}"))
    except Exception as exc:
        checks.append(_check("workflow-package", "fail", str(exc)))

    if workflow is not None:
        try:
            schema = yaml.safe_load(workflow.schema_path.read_text(encoding="utf-8"))
            if not isinstance(schema, dict):
                raise WorkflowContractError(f"{workflow.schema_path} must decode to a mapping")
            errors = _schema_errors(config=config, schema=schema)
            checks.append(
                _check("schema", "fail", f"{len(errors)} schema violation(s)", items=errors)
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
    checks.extend(runtime_capability_checks(config))

    if workflow is not None and run_preflight:
        try:
            loaded_config = workflow.load_config(workflow_root=root, raw=config)
            result = workflow.run_preflight(workflow_root=root, config=loaded_config)
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
            _append_once(recommendations, "Run `hermes daedalus bootstrap` from the target repo, or pass `--workflow-root` for an existing workflow instance.")
        elif name == "contract-format":
            _append_once(recommendations, "Use a repo-owned `WORKFLOW.md` / `WORKFLOW-<name>.md` contract before publishing the workflow.")
        elif name == "workflow-field":
            _append_once(recommendations, f"Add top-level `workflow:` to {source}.")
        elif name == "workflow-package":
            _append_once(recommendations, "Use `workflow: agentic` or reinstall the Daedalus plugin.")
        elif name == "schema":
            _append_once(recommendations, f"Edit the YAML front matter in {source} and fix the listed schema paths.")
        elif name == "schema-version":
            _append_once(recommendations, "Set `schema-version` to a version supported by this plugin, or update the installed Daedalus plugin.")
        elif name == "instance-name":
            _append_once(recommendations, "Make `instance.name` match the workflow root directory name, or rerun `hermes daedalus bootstrap`.")
        elif name == "repository-path":
            _append_once(recommendations, "Set `repository.local-path` to an existing local checkout path.")
        elif name == "workflow-preflight":
            _append_once(recommendations, _preflight_recommendation(check=check, workflow=workflow))
        elif name.startswith("runtime-binding"):
            _append_once(recommendations, _runtime_binding_recommendation(workflow=workflow))
        elif name.startswith("runtime-stage"):
            _append_once(recommendations, "Fix the actor/stage runtime references in `WORKFLOW.md`, then rerun `hermes daedalus validate`.")
        elif name.startswith("runtime-capability"):
            _append_once(recommendations, "Bind the role to a runtime with the required capabilities, or remove the explicit `required-capabilities` entry.")
        elif name.startswith("runtime-availability"):
            _append_once(recommendations, _runtime_availability_recommendation(detail))
        elif name == "github-auth":
            _append_once(recommendations, "Run `gh auth status` and `gh auth login` for the configured GitHub host.")
        elif name == "github-repo":
            _append_once(recommendations, "Check `tracker.github_slug`, `code-host.github_slug`, and local GitHub access with `gh repo view`.")
        elif name == "tracker":
            _append_once(recommendations, "Run `hermes daedalus validate --format json` and fix the tracker configuration before starting the service.")
        elif name == "workspace-root":
            _append_once(recommendations, "Create the configured workspace root or fix filesystem permissions for the workflow user.")
        elif name in {"engine_event_retention", "engine-event-retention"}:
            _append_once(recommendations, "Configure or apply event retention with `hermes daedalus events stats` and `hermes daedalus events prune`.")
        elif status == "fail":
            _append_once(recommendations, f"Fix failing check `{name}`: {detail or 'see JSON output for details'}.")
    return recommendations


def _repo_workflow_candidates(repo_root: Path) -> list[Path]:
    root = repo_root.resolve()
    candidates: list[Path] = []
    default_path = workflow_markdown_path(root)
    if default_path.exists():
        candidates.append(default_path)
    candidates.extend(path.resolve() for path in sorted(root.glob(f"{WORKFLOW_MARKDOWN_PREFIX}*.md")) if path.is_file())
    return candidates


def _workflow_name_for_contract_path(path: Path) -> str | None:
    try:
        contract = load_workflow_contract_file(path)
    except (WorkflowContractError, OSError, UnicodeDecodeError):
        return None
    value = contract.config.get("workflow")
    return str(value).strip() if value else None


def _parse_markdown_contract(path: Path, text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text.strip()
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()
    closing_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_index = index
            break
    if closing_index is None:
        raise WorkflowContractError(f"{path} starts with YAML front matter but is missing the closing --- delimiter")
    front_matter_text = "\n".join(lines[1:closing_index])
    prompt_body = "\n".join(lines[closing_index + 1 :]).strip()
    try:
        parsed = yaml.safe_load(front_matter_text) if front_matter_text.strip() else {}
    except yaml.YAMLError as exc:
        raise WorkflowContractError(f"YAML front-matter parse error in {path}: {exc}") from exc
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise WorkflowContractError(f"{path} front matter must decode to a YAML mapping at the top level")
    return parsed, prompt_body


def _project_markdown_front_matter(*, path: Path, front_matter: dict[str, Any], prompt_template: str) -> dict[str, Any]:
    config = deepcopy(front_matter)
    existing_policy = config.get(WORKFLOW_POLICY_KEY)
    if existing_policy is not None and not isinstance(existing_policy, str):
        raise WorkflowContractError(f"{path} {WORKFLOW_POLICY_KEY} must be a string when present")
    if existing_policy and prompt_template:
        raise WorkflowContractError(
            f"{path} defines both front-matter {WORKFLOW_POLICY_KEY!r} and a Markdown body; use the body as the workflow policy source"
        )
    if prompt_template:
        config[WORKFLOW_POLICY_KEY] = prompt_template
    return config


def _import_workflow_module(name: str) -> ModuleType:
    if name == NAME:
        return importlib.import_module("workflows")
    return importlib.import_module(f"workflows.{name.replace('-', '_')}")


def _emit_dispatch_skipped_event(*, workflow_root: Path, workflow_name: str, error_code: str | None, error_detail: str | None) -> None:
    try:
        from workflows.paths import runtime_paths
        import runtime as _runtime

        paths = runtime_paths(workflow_root)
        _runtime.append_daedalus_event(
            event_log_path=paths["event_log_path"],
            event={"event": "daedalus.dispatch_skipped", "workflow": workflow_name, "code": error_code, "detail": error_detail},
        )
    except Exception:
        pass


def _check(name: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    payload = {"name": name, "status": status, "detail": detail}
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def _normalize_role(role: str) -> str:
    normalized = role.strip()
    if not normalized:
        raise RuntimePresetError("--role cannot be blank")
    if normalized.startswith("agentic."):
        normalized = normalized.removeprefix("agentic.")
    return normalized


def _runtime_profiles_from_config(config: dict[str, Any]) -> dict[str, Any]:
    runtimes = config.get("runtimes")
    return runtimes if isinstance(runtimes, dict) else {}


def _append_binding(bindings: list[dict[str, Any]], *, role: str, runtime_name: Any, runtimes: dict[str, Any]) -> None:
    normalized_runtime = str(runtime_name or "").strip() or None
    runtime_cfg = runtimes.get(normalized_runtime) if normalized_runtime else None
    profile_exists = isinstance(runtime_cfg, dict)
    bindings.append(
        {
            "role": role,
            "runtime": normalized_runtime,
            "profile_exists": profile_exists,
            "kind": str(runtime_cfg.get("kind") or "").strip() if profile_exists else None,
            "capabilities": [],
        }
    )


def _json_path(parts: Any) -> str:
    values = [str(part) for part in parts]
    return ".".join(values) if values else "<root>"


def _schema_errors(*, config: dict[str, Any], schema: dict[str, Any]) -> list[dict[str, Any]]:
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(config), key=lambda item: list(item.absolute_path))
    return [
        {"path": _json_path(error.absolute_path), "message": error.message, "validator": str(error.validator)}
        for error in errors
    ]


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
        return _check("instance-name", "fail", f"instance.name={name!r} must match workflow root directory {workflow_root.name!r}")
    return _check("instance-name", "pass", name)


def _contract_kind_check(contract: WorkflowContract) -> dict[str, Any]:
    if contract.source_path.suffix.lower() == ".md":
        return _check("contract-format", "pass", "repo-owned Markdown workflow contract")
    return _check("contract-format", "fail", f"unsupported workflow contract format: {contract.source_path}")


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
