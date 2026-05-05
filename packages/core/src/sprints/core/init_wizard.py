"""First-run setup wizard for repo-owned Sprints workflows."""

from __future__ import annotations

import copy
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from sprints.core.bindings import (
    available_runtime_presets,
    bind_runtime_role,
    runtime_preset_config,
)
from sprints.core.bootstrap import (
    _discover_git_repo_root,
    _git_stdout,
    _repo_slug_from_remote_url,
    scaffold_workflow_root,
)
from sprints.core.contracts import (
    load_workflow_contract_file,
    render_workflow_markdown,
    snapshot_workflow_contract,
)
from sprints.core.paths import derive_workflow_instance_name, repo_local_workflow_pointer_path
from sprints.workflows.registry import DEFAULT_WORKFLOW_NAME, SUPPORTED_WORKFLOW_NAMES
from sprints.core.validation import validate_workflow_contract


class WorkflowInitError(RuntimeError):
    pass


@dataclass(frozen=True)
class InitAnswers:
    repo_path: Path
    repo_slug: str
    workflow_root: Path
    workflow_name: str
    tracker: str
    runtime_preset: str
    runtime_name: str
    model: str | None
    active_label: str
    done_label: str
    exclude_labels: tuple[str, ...]
    max_lanes: int


InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]


def run_init_wizard(
    *,
    repo_path: Path | None,
    workflow_name: str,
    workflow_root: Path | None,
    repo_slug: str | None,
    tracker: str | None,
    runtime_preset: str | None,
    runtime_name: str | None,
    model: str | None,
    active_label: str | None,
    done_label: str | None,
    exclude_labels: list[str] | None,
    max_lanes: int | None,
    force: bool,
    yes: bool,
    input_fn: InputFn = input,
    output_fn: OutputFn = print,
    interactive: bool | None = None,
) -> dict[str, Any]:
    ask = _should_prompt(interactive=interactive, yes=yes)
    answers = _collect_answers(
        repo_path=repo_path,
        workflow_name=workflow_name,
        workflow_root=workflow_root,
        repo_slug=repo_slug,
        tracker=tracker,
        runtime_preset=runtime_preset,
        runtime_name=runtime_name,
        model=model,
        active_label=active_label,
        done_label=done_label,
        exclude_labels=exclude_labels,
        max_lanes=max_lanes,
        ask=ask,
        input_fn=input_fn,
        output_fn=output_fn,
    )

    if ask:
        output_fn("")
        output_fn("Sprints will create:")
        output_fn(f"- repo contract: {answers.repo_path / 'WORKFLOW.md'}")
        output_fn(f"- workflow root: {answers.workflow_root}")
        output_fn(f"- tracker: {answers.tracker}")
        output_fn(f"- runtime: {answers.runtime_name} ({answers.runtime_preset})")
        output_fn(f"- labels: {answers.active_label} -> {answers.done_label}")
        output_fn(f"- concurrency: {answers.max_lanes} lane(s)")
        if not _confirm("Create workflow now?", default=True, input_fn=input_fn):
            raise WorkflowInitError("init cancelled")

    scaffold = scaffold_workflow_root(
        workflow_root=answers.workflow_root,
        workflow_name=answers.workflow_name,
        repo_path=answers.repo_path,
        repo_slug=answers.repo_slug,
        engine_owner="hermes",
        force=force,
    )

    contract_path = Path(scaffold["contract_path"])
    contract = load_workflow_contract_file(contract_path)
    config = _apply_answers(config=contract.config, answers=answers)
    contract_path.write_text(
        render_workflow_markdown(config=config, prompt_template=contract.prompt_template),
        encoding="utf-8",
    )
    snapshot = snapshot_workflow_contract(
        workflow_root=answers.workflow_root,
        source_path=contract_path,
        source_ref="sprints-init",
    )
    pointer_path = repo_local_workflow_pointer_path(answers.repo_path)
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(str(answers.workflow_root) + "\n", encoding="utf-8")

    validation = validate_workflow_contract(answers.workflow_root)
    return {
        **scaffold,
        "action": "init",
        "contract_path": str(contract_path),
        "active_contract_path": snapshot["active_contract_path"],
        "contract_sha256": snapshot["contract_sha256"],
        "repo_pointer_path": str(pointer_path),
        "answers": _answers_payload(answers),
        "validation": validation,
        "next_steps": _next_steps(answers),
    }


def _collect_answers(
    *,
    repo_path: Path | None,
    workflow_name: str,
    workflow_root: Path | None,
    repo_slug: str | None,
    tracker: str | None,
    runtime_preset: str | None,
    runtime_name: str | None,
    model: str | None,
    active_label: str | None,
    done_label: str | None,
    exclude_labels: list[str] | None,
    max_lanes: int | None,
    ask: bool,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> InitAnswers:
    default_repo = _discover_git_repo_root(repo_path)
    if ask:
        output_fn("Sprints first-run setup")
    resolved_repo = Path(
        _prompt(
            "Target repository",
            str(default_repo),
            ask=ask,
            input_fn=input_fn,
        )
    ).expanduser()
    resolved_repo = _discover_git_repo_root(resolved_repo)

    detected_slug = _detect_repo_slug(resolved_repo)
    resolved_slug = _prompt(
        "Repository slug",
        repo_slug or detected_slug,
        ask=ask,
        input_fn=input_fn,
    )
    if not resolved_slug:
        raise WorkflowInitError(
            "repository slug is required in owner/repo form; pass --repo-slug"
        )

    resolved_workflow = _prompt_choice(
        "Workflow",
        workflow_name or DEFAULT_WORKFLOW_NAME,
        SUPPORTED_WORKFLOW_NAMES,
        ask=ask,
        input_fn=input_fn,
    )
    instance_name = derive_workflow_instance_name(
        repo_slug=resolved_slug,
        workflow_name=resolved_workflow,
    )
    default_root = Path.home() / ".hermes" / "workflows" / instance_name
    resolved_root = Path(
        _prompt(
            "Workflow root",
            str(workflow_root or default_root),
            ask=ask,
            input_fn=input_fn,
        )
    ).expanduser().resolve()

    resolved_tracker = _prompt_choice(
        "Tracker",
        tracker or "github",
        ("github",),
        ask=ask,
        input_fn=input_fn,
    )
    resolved_runtime_preset = _prompt_choice(
        "Runtime",
        runtime_preset or "codex-app-server",
        available_runtime_presets(),
        ask=ask,
        input_fn=input_fn,
    )
    default_runtime_name = (
        "codex" if resolved_runtime_preset == "codex-app-server" else resolved_runtime_preset
    )
    resolved_runtime_name = _prompt(
        "Runtime profile name",
        runtime_name or default_runtime_name,
        ask=ask,
        input_fn=input_fn,
    )
    resolved_model = _blank_to_none(
        _prompt("Model override", model or "", ask=ask, input_fn=input_fn)
    )
    resolved_active = _prompt(
        "Active label",
        active_label or "active",
        ask=ask,
        input_fn=input_fn,
    )
    resolved_done = _prompt(
        "Done label",
        done_label or "done",
        ask=ask,
        input_fn=input_fn,
    )
    resolved_excludes = tuple(
        _split_csv(
            _prompt(
                "Exclude labels",
                _join_csv(exclude_labels or ["blocked", "needs-human", "done"]),
                ask=ask,
                input_fn=input_fn,
            )
        )
    )
    resolved_max_lanes = _parse_positive_int(
        _prompt(
            "Max concurrent lanes",
            str(max_lanes or 1),
            ask=ask,
            input_fn=input_fn,
        ),
        field="max concurrent lanes",
    )

    return InitAnswers(
        repo_path=resolved_repo,
        repo_slug=resolved_slug,
        workflow_root=resolved_root,
        workflow_name=resolved_workflow,
        tracker=resolved_tracker,
        runtime_preset=resolved_runtime_preset,
        runtime_name=resolved_runtime_name,
        model=resolved_model,
        active_label=resolved_active,
        done_label=resolved_done,
        exclude_labels=resolved_excludes,
        max_lanes=resolved_max_lanes,
    )


def _apply_answers(*, config: dict[str, Any], answers: InitAnswers) -> dict[str, Any]:
    updated = copy.deepcopy(config)
    updated["workflow"] = answers.workflow_name
    updated.setdefault("repository", {})["local-path"] = str(answers.repo_path)
    updated.setdefault("repository", {})["slug"] = answers.repo_slug

    tracker_cfg = updated.setdefault("tracker", {})
    tracker_cfg["kind"] = answers.tracker
    if answers.tracker == "github":
        tracker_cfg["github_slug"] = answers.repo_slug
    tracker_cfg["required_labels"] = [answers.active_label]
    tracker_cfg["exclude_labels"] = list(answers.exclude_labels)

    code_host_cfg = updated.setdefault("code-host", {})
    if answers.tracker == "github":
        code_host_cfg["kind"] = "github"
        code_host_cfg["github_slug"] = answers.repo_slug

    intake_cfg = updated.setdefault("intake", {}).setdefault("auto-activate", {})
    intake_cfg["enabled"] = True
    intake_cfg["add_label"] = answers.active_label
    intake_cfg["exclude_labels"] = list(answers.exclude_labels)

    completion_cfg = updated.setdefault("completion", {})
    completion_cfg["remove_labels"] = [answers.active_label]
    completion_cfg["add_labels"] = [answers.done_label]

    concurrency_cfg = updated.setdefault("concurrency", {})
    concurrency_cfg["max-lanes"] = answers.max_lanes
    actor_limits = concurrency_cfg.setdefault("actors", {})
    actor_limits["implementer"] = answers.max_lanes
    actor_limits["reviewer"] = answers.max_lanes

    runtimes = updated.setdefault("runtimes", {})
    runtimes.clear()
    runtimes[answers.runtime_name] = runtime_preset_config(answers.runtime_preset)
    bind_runtime_role(
        config=updated,
        workflow_name=answers.workflow_name,
        role="all",
        runtime_name=answers.runtime_name,
    )
    if answers.model:
        for actor in updated.get("actors", {}).values():
            if isinstance(actor, dict):
                actor["model"] = answers.model
    return updated


def _detect_repo_slug(repo_root: Path) -> str:
    try:
        remote_url = _git_stdout("remote", "get-url", "origin", cwd=repo_root)
        return _repo_slug_from_remote_url(remote_url)
    except Exception:
        return "owner/repo"


def _should_prompt(*, interactive: bool | None, yes: bool) -> bool:
    if yes:
        return False
    if interactive is not None:
        return interactive
    return sys.stdin.isatty() and sys.stdout.isatty()


def _prompt(label: str, default: str, *, ask: bool, input_fn: InputFn) -> str:
    if not ask:
        return str(default)
    suffix = f" [{default}]" if default else ""
    raw = input_fn(f"{label}{suffix}: ").strip()
    return raw or str(default)


def _prompt_choice(
    label: str,
    default: str,
    choices: tuple[str, ...],
    *,
    ask: bool,
    input_fn: InputFn,
) -> str:
    if default not in choices:
        raise WorkflowInitError(
            f"{label.lower()} must be one of {', '.join(choices)}; got {default!r}"
        )
    if not ask:
        return default
    choice_text = "/".join(choices)
    while True:
        raw = input_fn(f"{label} ({choice_text}) [{default}]: ").strip() or default
        if raw in choices:
            return raw
        print(f"Choose one of: {', '.join(choices)}")


def _confirm(label: str, *, default: bool, input_fn: InputFn) -> bool:
    suffix = "Y/n" if default else "y/N"
    raw = input_fn(f"{label} [{suffix}]: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _join_csv(values: list[str]) -> str:
    return ", ".join(values)


def _blank_to_none(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None


def _parse_positive_int(value: str, *, field: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise WorkflowInitError(f"{field} must be an integer") from exc
    if parsed < 1:
        raise WorkflowInitError(f"{field} must be at least 1")
    return parsed


def _answers_payload(answers: InitAnswers) -> dict[str, Any]:
    return {
        "repo_path": str(answers.repo_path),
        "repo_slug": answers.repo_slug,
        "workflow_root": str(answers.workflow_root),
        "workflow": answers.workflow_name,
        "tracker": answers.tracker,
        "runtime_preset": answers.runtime_preset,
        "runtime_name": answers.runtime_name,
        "model": answers.model,
        "active_label": answers.active_label,
        "done_label": answers.done_label,
        "exclude_labels": list(answers.exclude_labels),
        "max_lanes": answers.max_lanes,
    }


def _next_steps(answers: InitAnswers) -> list[str]:
    steps = [
        f"cd {answers.repo_path}",
        "hermes sprints validate",
        "hermes sprints doctor",
    ]
    if answers.runtime_preset == "codex-app-server":
        steps.insert(1, "hermes sprints codex-app-server up")
    steps.append("hermes sprints daemon up")
    return steps
