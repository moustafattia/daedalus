import re
import subprocess
from pathlib import Path
from typing import Any

from workflows.contracts import (
    WorkflowContractError,
    find_repo_workflow_contract_path,
    load_workflow_contract_file,
    render_workflow_markdown,
    workflow_contract_pointer_path,
    workflow_named_markdown_path,
    write_workflow_contract_pointer,
)
from workflows.paths import (
    derive_workflow_instance_name,
    repo_local_workflow_pointer_path,
)
from workflows.registry import SUPPORTED_WORKFLOW_NAMES

PLUGIN_DIR = Path(__file__).resolve().parents[1]


class WorkflowBootstrapError(Exception):
    pass


_REMOTE_OWNER_REPO_RE = re.compile(r"(?P<owner>[^/:]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")
_REMOTE_SCP_RE = re.compile(
    r"^[^@]+@[^:]+:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)


def _workflow_template_path(workflow_name: str) -> Path:
    if workflow_name not in SUPPORTED_WORKFLOW_NAMES:
        raise WorkflowBootstrapError(
            f"no bundled workflow template for {workflow_name!r}; "
            f"expected one of {list(SUPPORTED_WORKFLOW_NAMES)}"
        )
    return PLUGIN_DIR / "workflows" / "templates" / f"{workflow_name}.md"


def _git_stdout(*args: str, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (
            completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        )
        raise WorkflowBootstrapError(
            f"`git {' '.join(args)}` failed in {cwd}: {detail}"
        )
    return completed.stdout.strip()


def _discover_git_repo_root(start_path: Path | None) -> Path:
    start = (start_path or Path.cwd()).expanduser().resolve()
    if not start.exists():
        raise WorkflowBootstrapError(f"repo path does not exist: {start}")
    cwd = start.parent if start.is_file() else start
    try:
        repo_root = _git_stdout("rev-parse", "--show-toplevel", cwd=cwd)
    except WorkflowBootstrapError as exc:
        raise WorkflowBootstrapError(
            "bootstrap must run inside a git repository or use --repo-path pointing at one"
        ) from exc
    return Path(repo_root).expanduser().resolve()


def _repo_slug_from_remote_url(remote_url: str) -> str:
    raw = remote_url.strip()
    match = _REMOTE_SCP_RE.match(raw) or _REMOTE_OWNER_REPO_RE.search(raw)
    if not match:
        raise WorkflowBootstrapError(
            "unable to derive --repo-slug from git origin; pass --repo-slug owner/repo explicitly"
        )
    owner = match.group("owner").strip()
    repo = match.group("repo").strip()
    if not owner or not repo:
        raise WorkflowBootstrapError(
            "unable to derive --repo-slug from git origin; pass --repo-slug owner/repo explicitly"
        )
    return f"{owner}/{repo}"


def _repo_workflow_contract_candidates(repo_root: Path) -> list[Path]:
    return sorted(
        path.resolve() for path in repo_root.glob("WORKFLOW*.md") if path.is_file()
    )


def _prepare_repo_contract_paths(
    *,
    repo_root: Path,
    workflow_name: str,
    force: bool,
) -> tuple[Path, list[tuple[Path, Path]]]:
    repo_root = repo_root.resolve()
    default_path = repo_root / "WORKFLOW.md"
    named_path = workflow_named_markdown_path(repo_root, workflow_name)

    if named_path.exists():
        return named_path, []

    if default_path.exists():
        try:
            existing_contract = load_workflow_contract_file(default_path)
        except (WorkflowContractError, OSError, UnicodeDecodeError) as exc:
            raise WorkflowBootstrapError(
                f"{default_path} exists but is not a Sprints workflow contract; "
                "expected YAML front matter with a top-level `workflow:` field"
            ) from exc
        existing_workflow = str(existing_contract.config.get("workflow") or "").strip()
        if existing_workflow == workflow_name:
            return default_path, []
        if not existing_workflow:
            raise WorkflowBootstrapError(
                f"{default_path} exists but is not a Sprints workflow contract; "
                "expected YAML front matter with a top-level `workflow:` field"
            )
        migrated_path = workflow_named_markdown_path(repo_root, existing_workflow)
        if migrated_path.exists():
            raise WorkflowBootstrapError(
                f"cannot promote {default_path.name} into multi-workflow form because "
                f"{migrated_path.name} already exists; Sprints will not overwrite "
                "repo-owned workflow contracts"
            )
        return named_path, [(default_path, migrated_path)]

    existing = find_repo_workflow_contract_path(repo_root, workflow_name=workflow_name)
    if existing is not None:
        return existing, []

    if _repo_workflow_contract_candidates(repo_root):
        return named_path, []
    return default_path, []


def _git_branch_exists(branch_name: str, *, cwd: Path) -> bool:
    completed = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def _current_git_branch(cwd: Path) -> str | None:
    completed = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    branch = completed.stdout.strip()
    return branch or None


def _ensure_bootstrap_branch(*, repo_root: Path, workflow_name: str) -> str:
    branch_name = f"sprints/bootstrap-{workflow_name}"
    current_branch = _current_git_branch(repo_root)
    if current_branch == branch_name:
        return branch_name
    if _git_branch_exists(branch_name, cwd=repo_root):
        _git_stdout("checkout", branch_name, cwd=repo_root)
        return branch_name
    _git_stdout("checkout", "-b", branch_name, cwd=repo_root)
    return branch_name


def _git_path_is_tracked(*, repo_root: Path, path: Path) -> bool:
    try:
        relpath = str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return False
    completed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", relpath],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def _commit_bootstrap_contract(
    *,
    repo_root: Path,
    workflow_name: str,
    paths: list[Path],
) -> dict[str, Any]:
    branch_name = _ensure_bootstrap_branch(
        repo_root=repo_root, workflow_name=workflow_name
    )
    relpaths = []
    for path in paths:
        resolved = path.resolve()
        try:
            relpath = str(resolved.relative_to(repo_root.resolve()))
        except ValueError as exc:
            raise WorkflowBootstrapError(
                f"cannot commit path outside repo root: {resolved}"
            ) from exc
        if resolved.exists() or _git_path_is_tracked(
            repo_root=repo_root, path=resolved
        ):
            relpaths.append(relpath)
    relpaths = sorted(set(relpaths))
    subprocess.run(
        ["git", "add", "--", *relpaths],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
        text=True,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", *relpaths],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    committed = False
    commit_sha = None
    commit_message = f"Add {workflow_name} workflow contract"
    if status.stdout.strip():
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Sprints",
                "-c",
                "user.email=sprints@local",
                "commit",
                "-m",
                commit_message,
            ],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
        committed = True
        commit_sha = _git_stdout("rev-parse", "HEAD", cwd=repo_root)
    return {
        "branch": branch_name,
        "committed": committed,
        "commit_sha": commit_sha,
        "commit_message": commit_message if committed else None,
        "paths": [str(path) for path in paths],
    }


def bootstrap_workflow_root(
    *,
    repo_path: Path | None,
    workflow_name: str,
    workflow_root: Path | None,
    repo_slug: str | None,
    engine_owner: str,
    force: bool,
) -> dict[str, Any]:
    repo_root = _discover_git_repo_root(repo_path)
    remote_url = None
    resolved_repo_slug = (repo_slug or "").strip()
    if not resolved_repo_slug:
        remote_url = _git_stdout("remote", "get-url", "origin", cwd=repo_root)
        resolved_repo_slug = _repo_slug_from_remote_url(remote_url)

    try:
        instance_name = derive_workflow_instance_name(
            repo_slug=resolved_repo_slug,
            workflow_name=workflow_name,
        )
    except ValueError as exc:
        raise WorkflowBootstrapError(
            f"--repo-slug {resolved_repo_slug!r} is invalid: {exc}"
        ) from exc

    resolved_workflow_root = (
        workflow_root.expanduser().resolve()
        if workflow_root is not None
        else (Path.home() / ".hermes" / "workflows" / instance_name).resolve()
    )

    result = scaffold_workflow_root(
        workflow_root=resolved_workflow_root,
        workflow_name=workflow_name,
        repo_path=repo_root,
        repo_slug=resolved_repo_slug,
        engine_owner=engine_owner,
        force=force,
    )
    pointer_path = repo_local_workflow_pointer_path(repo_root)
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(str(resolved_workflow_root) + "\n", encoding="utf-8")
    next_command = "hermes sprints status"
    commit_result = _commit_bootstrap_contract(
        repo_root=repo_root,
        workflow_name=workflow_name,
        paths=[
            Path(result["contract_path"]),
            *[Path(path) for path in result.get("renamed_contract_paths") or []],
            *[Path(path) for path in result.get("renamed_contract_source_paths") or []],
        ],
    )

    result.update(
        {
            "bootstrap": True,
            "detected_repo_root": str(repo_root),
            "remote_url": remote_url,
            "repo_pointer_path": str(pointer_path),
            "next_edit_path": result["contract_path"],
            "next_command": next_command,
            "git_branch": commit_result["branch"],
            "git_committed": commit_result["committed"],
            "git_commit_sha": commit_result["commit_sha"],
            "git_commit_message": commit_result["commit_message"],
        }
    )
    return result


def scaffold_workflow_root(
    *,
    workflow_root: Path,
    workflow_name: str,
    repo_path: Path | None,
    repo_slug: str,
    engine_owner: str,
    force: bool,
) -> dict[str, Any]:
    root = workflow_root.expanduser().resolve()
    repo_root = _discover_git_repo_root(repo_path)
    contract_path, rename_pairs = _prepare_repo_contract_paths(
        repo_root=repo_root,
        workflow_name=workflow_name,
        force=force,
    )
    if contract_path.exists() and not force:
        raise WorkflowBootstrapError(
            f"refusing to overwrite existing workflow contract: {contract_path} "
            "(pass --force to replace it)"
        )

    template_path = _workflow_template_path(workflow_name)
    try:
        template_contract = load_workflow_contract_file(template_path)
    except (WorkflowContractError, OSError, UnicodeDecodeError) as exc:
        raise WorkflowBootstrapError(
            f"unable to load workflow template {template_path}: {exc}"
        ) from exc
    config = dict(template_contract.config)
    workflow_policy = template_contract.prompt_template

    resolved_repo_slug = repo_slug.strip()
    if not resolved_repo_slug:
        raise WorkflowBootstrapError("--repo-slug cannot be blank")
    try:
        resolved_instance_name = derive_workflow_instance_name(
            repo_slug=resolved_repo_slug,
            workflow_name=workflow_name,
        )
    except ValueError as exc:
        raise WorkflowBootstrapError(
            f"--repo-slug {resolved_repo_slug!r} is invalid: {exc}"
        ) from exc
    if root.name != resolved_instance_name:
        expected_root = root.parent / resolved_instance_name
        raise WorkflowBootstrapError(
            "workflow root directory name must follow <owner>-<repo>-<workflow-type>: "
            f"expected {expected_root} for repo-slug={resolved_repo_slug!r} "
            f"and workflow={workflow_name!r}"
        )

    resolved_repo_path = repo_root

    config["workflow"] = workflow_name
    instance_cfg = config.setdefault("instance", {})
    repository_cfg = config.setdefault("repository", {})

    instance_cfg["name"] = resolved_instance_name
    instance_cfg["engine-owner"] = engine_owner
    repository_cfg["local-path"] = str(resolved_repo_path)
    repository_cfg["slug"] = resolved_repo_slug
    _fill_repo_slug(config.get("tracker"), resolved_repo_slug)
    _fill_repo_slug(config.get("code-host"), resolved_repo_slug)

    created_dirs = [
        root / "config",
        root / "memory",
        root / "state" / "sessions",
        root / "runtime" / "logs",
        root / "runtime" / "memory",
        root / "runtime" / "state" / "sprints",
        root / "workspace",
    ]
    for path in created_dirs:
        path.mkdir(parents=True, exist_ok=True)

    renamed_contract_paths: list[str] = []
    renamed_contract_source_paths: list[str] = []
    for source_path, target_path in rename_pairs:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.replace(target_path)
        renamed_contract_paths.append(str(target_path))
        renamed_contract_source_paths.append(str(source_path))

    contract_path.write_text(
        render_workflow_markdown(config=config, prompt_template=workflow_policy),
        encoding="utf-8",
    )
    write_workflow_contract_pointer(root, contract_path)
    return {
        "ok": True,
        "workflow_root": str(root),
        "contract_path": str(contract_path),
        "config_path": str(contract_path),
        "workflow": workflow_name,
        "instance_name": resolved_instance_name,
        "engine_owner": engine_owner,
        "repo_path": str(resolved_repo_path),
        "repo_slug": resolved_repo_slug,
        "force": force,
        "workflow_contract_pointer_path": str(workflow_contract_pointer_path(root)),
        "renamed_contract_paths": renamed_contract_paths,
        "renamed_contract_source_paths": renamed_contract_source_paths,
    }


def _fill_repo_slug(section: Any, repo_slug: str) -> None:
    if not isinstance(section, dict):
        return
    if str(section.get("kind") or "").strip() != "github":
        return
    current = str(section.get("github_slug") or "").strip()
    if current in {"", "owner/repo"}:
        section["github_slug"] = repo_slug
