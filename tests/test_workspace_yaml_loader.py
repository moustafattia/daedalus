"""Regression test: load_workspace_from_config reads workflow.yaml.

The workflows-contract migration moved the canonical config from
config/yoyopod-workflow.json to config/workflow.yaml. The legacy loader
must detect and read the new location so callers (runtime.py via
_load_legacy_workflow_module, and workflows/code_review/status.py:build_status)
keep working on YAML-migrated workspaces.
"""
import importlib.util
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def _load_workspace_module():
    workspace_path = REPO_ROOT / "workflows" / "code_review" / "workspace.py"
    spec = importlib.util.spec_from_file_location(
        "daedalus_workspace_yaml_loader_test", workspace_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _yaml_config(repo_path: Path) -> dict:
    """Minimal valid YAML config that satisfies the schema and the bridge.

    Mirrors the shape used by tests/test_workflows_code_review_entrypoint.py
    in `_write_workflow_yaml` so the same bridge in `_yaml_to_legacy_view`
    can project it into the legacy view.
    """
    return {
        "workflow": "code-review",
        "schema-version": 1,
        "instance": {"name": "yoyopod", "engine-owner": "hermes"},
        "repository": {
            "local-path": str(repo_path),
            "github-slug": "owner/repo",
            "active-lane-label": "active-lane",
        },
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


def test_load_workspace_from_config_prefers_workflow_yaml(tmp_path):
    """When workflow.yaml exists, it must be read (and bridged) to build the workspace."""
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


def test_load_workspace_from_config_yaml_takes_precedence_over_legacy_json(tmp_path):
    """If both YAML and legacy JSON exist, prefer the YAML (post-migration source of truth)."""
    workspace = _load_workspace_module()
    workspace_root = tmp_path / "workflow"
    config_dir = workspace_root / "config"
    config_dir.mkdir(parents=True)
    cfg = _yaml_config(tmp_path / "yaml-repo")
    (config_dir / "workflow.yaml").write_text(
        yaml.safe_dump(cfg), encoding="utf-8"
    )
    # Legacy JSON points at a different repo path; if it's read, we'd see it.
    legacy = {
        "repoPath": str(tmp_path / "json-repo"),
        "cronJobsPath": str(tmp_path / "cron.json"),
        "ledgerPath": str(tmp_path / "ledger.json"),
        "healthPath": str(tmp_path / "health.json"),
        "auditLogPath": str(tmp_path / "audit.jsonl"),
        "engineOwner": "openclaw",
        "activeLaneLabel": "active-lane",
        "coreJobNames": [],
        "hermesJobNames": [],
    }
    import json as _json
    (config_dir / "yoyopod-workflow.json").write_text(
        _json.dumps(legacy), encoding="utf-8"
    )

    ws = workspace.load_workspace_from_config(workspace_root=workspace_root)

    assert ws.REPO_PATH == Path(tmp_path / "yaml-repo")
    assert ws.ENGINE_OWNER == "hermes"


def test_load_workspace_from_config_falls_back_to_legacy_json(tmp_path):
    """If only the legacy JSON exists (unmigrated workspace), it still works."""
    workspace = _load_workspace_module()
    workspace_root = tmp_path / "workflow"
    config_dir = workspace_root / "config"
    config_dir.mkdir(parents=True)
    legacy = {
        "repoPath": str(tmp_path / "json-repo"),
        "cronJobsPath": str(tmp_path / "cron.json"),
        "ledgerPath": str(tmp_path / "ledger.json"),
        "healthPath": str(tmp_path / "health.json"),
        "auditLogPath": str(tmp_path / "audit.jsonl"),
        "engineOwner": "openclaw",
        "activeLaneLabel": "active-lane",
        "coreJobNames": [],
        "hermesJobNames": [],
    }
    import json as _json
    (config_dir / "yoyopod-workflow.json").write_text(
        _json.dumps(legacy), encoding="utf-8"
    )

    ws = workspace.load_workspace_from_config(workspace_root=workspace_root)

    assert ws.REPO_PATH == Path(tmp_path / "json-repo")


def test_load_workspace_from_config_raises_when_no_config_present(tmp_path):
    """If neither workflow.yaml nor yoyopod-workflow.json exists, raise FileNotFoundError."""
    workspace = _load_workspace_module()
    workspace_root = tmp_path / "workflow"
    (workspace_root / "config").mkdir(parents=True)

    with pytest.raises(FileNotFoundError):
        workspace.load_workspace_from_config(workspace_root=workspace_root)
