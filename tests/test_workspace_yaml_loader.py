"""Regression tests for workflow contract workspace loading."""
import importlib.util
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def _load_workspace_module():
    workspace_path = REPO_ROOT / "workflows" / "change_delivery" / "workspace.py"
    spec = importlib.util.spec_from_file_location(
        "daedalus_workspace_yaml_loader_test", workspace_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _yaml_config(repo_path: Path) -> dict:
    """Minimal valid YAML config that satisfies the schema and the bridge.

    Mirrors the shape used by tests/test_workflows_change_delivery_entrypoint.py
    in `_write_workflow_yaml` so the same bridge in `_yaml_to_legacy_view`
    can project it into the legacy view.
    """
    return {
        "workflow": "change-delivery",
        "schema-version": 1,
        "instance": {"name": "workflow-engine", "engine-owner": "hermes"},
        "repository": {
            "local-path": str(repo_path),
            "slug": "owner/repo",
            "active-lane-label": "active-lane",
        },
        "tracker": {
            "kind": "github",
            "github_slug": "owner/repo",
            "active_states": ["open"],
            "terminal_states": ["closed"],
        },
        "code-host": {"kind": "github", "github_slug": "owner/repo"},
        "runtimes": {
            "acpx-codex": {
                "kind": "acpx-codex",
                "session-idle-freshness-seconds": 900,
                "session-idle-grace-seconds": 1800,
                "session-nudge-cooldown-seconds": 600,
            },
            "claude-cli": {
                "kind": "claude-cli",
                "max-turns-per-invocation": 24,
                "timeout-seconds": 1200,
            },
        },
        "agents": {
            "coder": {
                "default": {
                    "name": "Internal_Coder_Agent",
                    "model": "gpt-5.3-codex-spark/high",
                    "runtime": "acpx-codex",
                },
            },
            "internal-reviewer": {
                "name": "Internal_Reviewer_Agent",
                "model": "claude-sonnet-4-6",
                "runtime": "claude-cli",
            },
            "external-reviewer": {
                "enabled": True,
                "name": "External_Reviewer_Agent",
            },
        },
        "gates": {
            "internal-review": {},
            "external-review": {},
            "merge": {},
        },
        "triggers": {
            "lane-selector": {"type": "github-label", "label": "active-lane"},
        },
        "storage": {
            "ledger": "memory/workflow-status.json",
            "health": "memory/workflow-health.json",
            "audit-log": "memory/workflow-audit.jsonl",
        },
    }


def _workflow_markdown(config: dict, *, prompt_role: str = "coder", body: str = "Contract prompt") -> str:
    del prompt_role
    return "---\n" + yaml.safe_dump(config, sort_keys=False) + "---\n\n" + body + "\n"


def test_load_workspace_from_config_prefers_workflow_yaml(tmp_path):
    """When workflow.yaml exists, it must be read to build the workspace."""
    workspace = _load_workspace_module()
    workspace_root = tmp_path / "workflow"
    config_dir = workspace_root / "config"
    config_dir.mkdir(parents=True)
    cfg = _yaml_config(tmp_path / "repo")
    (config_dir / "workflow.yaml").write_text(
        yaml.safe_dump(cfg), encoding="utf-8"
    )

    ws = workspace.load_workspace_from_config(workspace_root=workspace_root)

    assert ws is not None
    assert ws.WORKSPACE == workspace_root.resolve()
    # The YAML repository.local-path drove repoPath in the bridge.
    assert ws.REPO_PATH == Path(tmp_path / "repo")
    assert ws.ENGINE_OWNER == "hermes"


def test_load_workspace_from_config_accepts_explicit_yaml_path(tmp_path):
    workspace = _load_workspace_module()
    workspace_root = tmp_path / "workflow"
    config_dir = workspace_root / "config"
    config_dir.mkdir(parents=True)
    cfg = _yaml_config(tmp_path / "yaml-repo")
    path = config_dir / "workflow.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    ws = workspace.load_workspace_from_config(workspace_root=workspace_root, config_path=path)

    assert ws.REPO_PATH == Path(tmp_path / "yaml-repo")
    assert ws.ENGINE_OWNER == "hermes"


def test_load_workspace_from_config_accepts_explicit_markdown_path(tmp_path):
    workspace = _load_workspace_module()
    workspace_root = tmp_path / "workflow"
    workspace_root.mkdir()
    cfg = _yaml_config(tmp_path / "markdown-repo")
    path = workspace_root / "WORKFLOW.md"
    path.write_text(_workflow_markdown(cfg, prompt_role="internal-reviewer"), encoding="utf-8")

    ws = workspace.load_workspace_from_config(workspace_root=workspace_root, config_path=path)

    assert ws.REPO_PATH == Path(tmp_path / "markdown-repo")
    assert ws.ENGINE_OWNER == "hermes"
    assert ws.WORKFLOW_POLICY == "Contract prompt"


def test_load_workspace_from_config_rejects_json_config_path(tmp_path):
    workspace = _load_workspace_module()
    workspace_root = tmp_path / "workflow"
    config_dir = workspace_root / "config"
    config_dir.mkdir(parents=True)
    json_path = config_dir / "workflow.json"
    json_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError):
        workspace.load_workspace_from_config(workspace_root=workspace_root, config_path=json_path)

def test_load_workspace_from_config_falls_back_to_workflow_markdown(tmp_path):
    workspace = _load_workspace_module()
    workspace_root = tmp_path / "workflow"
    workspace_root.mkdir()
    cfg = _yaml_config(tmp_path / "markdown-repo")
    (workspace_root / "WORKFLOW.md").write_text(_workflow_markdown(cfg), encoding="utf-8")

    ws = workspace.load_workspace_from_config(workspace_root=workspace_root)

    assert ws.REPO_PATH == Path(tmp_path / "markdown-repo")
    assert ws.ENGINE_OWNER == "hermes"
    assert ws.WORKFLOW_POLICY == "Contract prompt"


def test_load_workspace_from_config_raises_when_no_config_present(tmp_path):
    """If no workflow contract is present, raise FileNotFoundError."""
    workspace = _load_workspace_module()
    workspace_root = tmp_path / "workflow"
    (workspace_root / "config").mkdir(parents=True)

    with pytest.raises(FileNotFoundError):
        workspace.load_workspace_from_config(workspace_root=workspace_root)
