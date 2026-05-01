"""Workflow contract loading for Daedalus.

Daedalus uses repo-owned ``WORKFLOW.md`` / ``WORKFLOW-<name>.md`` contracts.
The Markdown file uses YAML front matter for structured config and its body as
the shared workflow policy text.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_WORKFLOW_MARKDOWN_FILENAME = "WORKFLOW.md"
WORKFLOW_MARKDOWN_PREFIX = "WORKFLOW-"
WORKFLOW_CONTRACT_POINTER_RELATIVE_PATH = Path("config") / "workflow-contract-path"
WORKFLOW_POLICY_KEY = "workflow-policy"


class WorkflowContractError(RuntimeError):
    """Raised when the workflow contract file cannot be loaded or projected."""


@dataclass(frozen=True)
class WorkflowContract:
    """Loaded workflow contract plus prompt body metadata."""

    source_path: Path
    config: dict[str, Any]
    prompt_template: str
    front_matter: dict[str, Any]


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


def _repo_workflow_candidates(repo_root: Path) -> list[Path]:
    root = repo_root.resolve()
    candidates: list[Path] = []
    default_path = workflow_markdown_path(root)
    if default_path.exists():
        candidates.append(default_path)
    named_paths = sorted(
        path
        for path in root.glob(f"{WORKFLOW_MARKDOWN_PREFIX}*.md")
        if path.is_file()
    )
    candidates.extend(path.resolve() for path in named_paths)
    return candidates


def _workflow_name_for_contract_path(path: Path) -> str | None:
    try:
        contract = load_workflow_contract_file(path)
    except (WorkflowContractError, OSError, UnicodeDecodeError):
        return None
    value = contract.config.get("workflow")
    return str(value).strip() if value else None


def find_repo_workflow_contract_path(
    repo_root: Path,
    *,
    workflow_name: str | None = None,
) -> Path | None:
    """Return a repo-owned workflow contract path when one can be resolved.

    Resolution rules:
    - prefer the explicitly named ``WORKFLOW-<workflow>.md`` file
    - otherwise allow a single ``WORKFLOW.md`` or single named workflow file
    - if ``WORKFLOW.md`` exists, allow it only when it declares the requested
      workflow name (or when no specific workflow name is required)
    """
    root = repo_root.resolve()
    if workflow_name:
        named_path = workflow_named_markdown_path(root, workflow_name)
        if named_path.exists():
            return named_path

    default_path = workflow_markdown_path(root)
    candidates = _repo_workflow_candidates(root)

    if workflow_name and default_path.exists():
        if _workflow_name_for_contract_path(default_path) == workflow_name:
            return default_path

    if default_path.exists() and not workflow_name:
        return default_path

    if len(candidates) == 1:
        return candidates[0]
    return None


def find_workflow_contract_path(
    workflow_root: Path,
    *,
    workflow_name: str | None = None,
) -> Path | None:
    """Return the preferred contract path for a workflow root, if any.

    Resolution order:
    1. explicit workflow-root pointer to a repo-owned contract
    2. direct repo-owned contract files under the given path
    3. in-root ``WORKFLOW.md``
    """
    root = workflow_root.resolve()
    pointer_target = read_workflow_contract_pointer(root)
    if pointer_target is not None and pointer_target.exists():
        return pointer_target

    repo_owned = find_repo_workflow_contract_path(root, workflow_name=workflow_name)
    if repo_owned is not None:
        return repo_owned

    markdown_path = workflow_markdown_path(root)
    if markdown_path.exists():
        return markdown_path

    return None


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
    suffix = resolved.suffix.lower()
    if suffix == ".md":
        return _load_markdown_contract(resolved)
    raise WorkflowContractError(
        f"unsupported workflow contract format for {resolved}; "
        "expected Markdown (.md)"
    )


def _load_markdown_contract(path: Path) -> WorkflowContract:
    text = path.read_text(encoding="utf-8")
    front_matter, prompt_template = _parse_markdown_contract(path, text)
    config = _project_markdown_front_matter(
        path=path,
        front_matter=front_matter,
        prompt_template=prompt_template,
    )
    return WorkflowContract(
        source_path=path,
        config=config,
        prompt_template=prompt_template,
        front_matter=front_matter,
    )


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
        raise WorkflowContractError(
            f"{path} starts with YAML front matter but is missing the closing --- delimiter"
        )

    front_matter_text = "\n".join(lines[1:closing_index])
    prompt_body = "\n".join(lines[closing_index + 1 :]).strip()
    try:
        parsed = yaml.safe_load(front_matter_text) if front_matter_text.strip() else {}
    except yaml.YAMLError as exc:
        raise WorkflowContractError(f"YAML front-matter parse error in {path}: {exc}") from exc
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise WorkflowContractError(
            f"{path} front matter must decode to a YAML mapping at the top level"
        )
    return parsed, prompt_body


def _project_markdown_front_matter(
    *,
    path: Path,
    front_matter: dict[str, Any],
    prompt_template: str,
) -> dict[str, Any]:
    config = deepcopy(front_matter)
    existing_policy = config.get(WORKFLOW_POLICY_KEY)
    if existing_policy is not None and not isinstance(existing_policy, str):
        raise WorkflowContractError(f"{path} {WORKFLOW_POLICY_KEY} must be a string when present")
    if existing_policy and prompt_template:
        raise WorkflowContractError(
            f"{path} defines both front-matter {WORKFLOW_POLICY_KEY!r} and a Markdown body; "
            "use the body as the workflow policy source"
        )
    if prompt_template:
        config[WORKFLOW_POLICY_KEY] = prompt_template
    return config


def render_workflow_markdown(*, config: dict[str, Any], prompt_template: str | None = None) -> str:
    """Render a native ``WORKFLOW.md`` file from config + policy text."""
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
