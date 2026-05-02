import importlib.util
import json
from pathlib import Path

import pytest

from workflows.contract import (
    load_workflow_contract_file,
    render_workflow_markdown,
    write_workflow_contract_pointer,
)
from workflows.runtime_presets import RuntimePresetError, configure_runtime_contract, runtime_stage_bindings


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_contract(path: Path, config: dict, body: str = "# Policy\n\nDo the work.") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_workflow_markdown(config=config, prompt_template=body), encoding="utf-8")


def test_configure_runtime_binds_issue_runner_agent_and_preserves_body(tmp_path):
    root = tmp_path / "attmous-daedalus-issue-runner"
    root.mkdir()
    contract_path = root / "WORKFLOW.md"
    _write_contract(
        contract_path,
        {
            "workflow": "issue-runner",
            "schema-version": 1,
            "instance": {"name": root.name, "engine-owner": "hermes"},
            "repository": {"local-path": str(tmp_path), "slug": "attmous/daedalus"},
            "tracker": {"kind": "local-json", "path": "config/issues.json"},
            "workspace": {"root": "workspace/issues"},
            "agent": {"name": "runner", "model": "gpt-5.4", "runtime": "default"},
            "runtimes": {"default": {"kind": "hermes-agent", "command": ["echo", "{prompt_path}"]}},
            "storage": {"status": "memory/status.json", "health": "memory/health.json", "audit-log": "memory/audit.jsonl"},
        },
    )

    result = configure_runtime_contract(
        workflow_root=root,
        preset_name="hermes-chat",
        role="agent",
        runtime_name=None,
    )
    contract = load_workflow_contract_file(contract_path)

    assert result["changed_roles"] == ["agent"]
    assert contract.config["agent"]["runtime"] == "hermes-chat"
    assert contract.config["runtimes"]["hermes-chat"] == {
        "kind": "hermes-agent",
        "mode": "chat",
        "source": "daedalus",
    }
    assert contract.prompt_template == "# Policy\n\nDo the work."


def test_issue_runner_stage_contract_maps_run_stage_to_agent_runtime(tmp_path):
    config = {
        "workflow": "issue-runner",
        "agent": {"name": "runner", "model": "gpt-5.4", "runtime": "codex-app-server"},
        "runtimes": {"codex-app-server": {"kind": "codex-app-server"}},
    }

    assert runtime_stage_bindings(config) == [
        {
            "name": "runtime-stage:agent",
            "workflow": "issue-runner",
            "stage": "run",
            "path": "agent",
            "role": "agent",
            "role_exists": True,
            "runtime": "codex-app-server",
        }
    ]


def test_configure_runtime_uses_workflow_root_pointer_for_change_delivery(tmp_path):
    workflow_root = tmp_path / "attmous-daedalus-change-delivery"
    repo_root = tmp_path / "repo"
    contract_path = repo_root / "WORKFLOW-change-delivery.md"
    workflow_root.mkdir()
    _write_contract(
        contract_path,
        {
            "workflow": "change-delivery",
            "schema-version": 1,
            "instance": {"name": workflow_root.name, "engine-owner": "hermes"},
            "repository": {"local-path": str(repo_root), "slug": "attmous/daedalus", "active-lane-label": "active-lane"},
            "tracker": {"kind": "github", "github_slug": "attmous/daedalus"},
            "code-host": {"kind": "github", "github_slug": "attmous/daedalus"},
            "runtimes": {
                "coder-runtime": {"kind": "acpx-codex"},
                "reviewer-runtime": {"kind": "claude-cli"},
            },
            "actors": {
                "implementer": {"name": "coder", "model": "gpt-5", "runtime": "coder-runtime"},
                "implementer-high-effort": {"name": "coder-hi", "model": "gpt-5.5", "runtime": "coder-runtime"},
                "reviewer": {"name": "reviewer", "model": "gpt-5", "runtime": "reviewer-runtime"},
            },
            "stages": {
                "implement": {
                    "actor": "implementer",
                    "escalation": {"after-attempts": 2, "actor": "implementer-high-effort"},
                },
                "publish": {"action": "pr.publish"},
                "merge": {"action": "pr.merge"},
            },
            "gates": {
                "pre-publish-review": {"type": "agent-review", "actor": "reviewer"},
                "maintainer-approval": {"type": "pr-comment-approval", "enabled": False},
                "ci-green": {"type": "code-host-checks"},
            },
            "triggers": {},
            "storage": {"ledger": "memory/status.json", "health": "memory/health.json", "audit-log": "memory/audit.jsonl"},
        },
    )
    write_workflow_contract_pointer(workflow_root, contract_path)

    result = configure_runtime_contract(
        workflow_root=workflow_root,
        preset_name="codex-app-server",
        role="implementer",
        runtime_name="codex",
    )
    cfg = load_workflow_contract_file(contract_path).config

    assert result["changed_roles"] == ["implementer"]
    assert cfg["actors"]["implementer"]["runtime"] == "codex"
    assert cfg["actors"]["implementer-high-effort"]["runtime"] == "coder-runtime"
    assert cfg["runtimes"]["codex"] == {
        "kind": "codex-app-server",
        "mode": "external",
        "endpoint": "ws://127.0.0.1:4500",
        "ephemeral": False,
        "keep_alive": True,
    }


def test_change_delivery_stage_contract_maps_stages_and_gates_to_actor_runtimes():
    config = {
        "workflow": "change-delivery",
        "runtimes": {"codex-app-server": {"kind": "codex-app-server"}},
        "actors": {
            "implementer": {"name": "impl", "model": "gpt-5.4", "runtime": "codex-app-server"},
            "implementer-high-effort": {"name": "impl-hi", "model": "gpt-5.4", "runtime": "codex-app-server"},
            "reviewer": {"name": "review", "model": "gpt-5.4", "runtime": "codex-app-server"},
        },
        "stages": {
            "implement": {
                "actor": "implementer",
                "escalation": {"after-attempts": 2, "actor": "implementer-high-effort"},
            },
            "publish": {"action": "pr.publish"},
            "merge": {"action": "pr.merge"},
        },
        "gates": {
            "pre-publish-review": {"type": "agent-review", "actor": "reviewer"},
            "maintainer-approval": {"type": "pr-comment-approval"},
            "ci-green": {"type": "code-host-checks"},
        },
    }

    assert [
        (item["stage"], item["role"], item["runtime"])
        for item in runtime_stage_bindings(config)
    ] == [
        ("implement", "implementer", "codex-app-server"),
        ("implement.escalation", "implementer-high-effort", "codex-app-server"),
        ("gate:pre-publish-review", "reviewer", "codex-app-server"),
    ]


def test_configure_runtime_rejects_unknown_role(tmp_path):
    root = tmp_path / "attmous-daedalus-issue-runner"
    root.mkdir()
    _write_contract(
        root / "WORKFLOW.md",
        {
            "workflow": "issue-runner",
            "schema-version": 1,
            "instance": {"name": root.name, "engine-owner": "hermes"},
            "repository": {"local-path": str(tmp_path)},
            "tracker": {"kind": "local-json"},
            "workspace": {"root": "workspace/issues"},
            "agent": {"name": "runner", "model": "gpt-5.4"},
            "storage": {"status": "memory/status.json", "health": "memory/health.json", "audit-log": "memory/audit.jsonl"},
        },
    )

    with pytest.raises(RuntimePresetError, match="issue-runner supports"):
        configure_runtime_contract(
            workflow_root=root,
            preset_name="hermes-final",
            role="coder.default",
            runtime_name=None,
        )


def test_configure_runtime_rejects_capability_mismatch(tmp_path):
    root = tmp_path / "attmous-daedalus-issue-runner"
    root.mkdir()
    _write_contract(
        root / "WORKFLOW.md",
        {
            "workflow": "issue-runner",
            "schema-version": 1,
            "instance": {"name": root.name, "engine-owner": "hermes"},
            "repository": {"local-path": str(tmp_path)},
            "tracker": {"kind": "local-json"},
            "workspace": {"root": "workspace/issues"},
            "agent": {
                "name": "runner",
                "model": "gpt-5.4",
                "required-capabilities": ["token-metrics"],
            },
            "storage": {"status": "memory/status.json", "health": "memory/health.json", "audit-log": "memory/audit.jsonl"},
        },
    )

    with pytest.raises(RuntimePresetError, match="token-metrics"):
        configure_runtime_contract(
            workflow_root=root,
            preset_name="hermes-final",
            role="agent",
            runtime_name=None,
        )


def test_configure_runtime_cli_outputs_json(tmp_path):
    tools = load_module("daedalus_tools_runtime_presets_test", "daedalus_cli.py")
    root = tmp_path / "attmous-daedalus-issue-runner"
    root.mkdir()
    _write_contract(
        root / "WORKFLOW.md",
        {
            "workflow": "issue-runner",
            "schema-version": 1,
            "instance": {"name": root.name, "engine-owner": "hermes"},
            "repository": {"local-path": str(tmp_path), "slug": "attmous/daedalus"},
            "tracker": {"kind": "local-json", "path": "config/issues.json"},
            "workspace": {"root": "workspace/issues"},
            "agent": {"name": "runner", "model": "gpt-5.4", "runtime": "default"},
            "runtimes": {"default": {"kind": "hermes-agent", "command": ["echo", "{prompt_path}"]}},
            "storage": {"status": "memory/status.json", "health": "memory/health.json", "audit-log": "memory/audit.jsonl"},
        },
    )

    output = tools.execute_raw_args(
        f"configure-runtime --workflow-root {root} --runtime hermes-final --role agent --json"
    )
    payload = json.loads(output)

    assert payload["workflow"] == "issue-runner"
    assert payload["runtime_name"] == "hermes-final"
    assert payload["bindings"][0]["runtime"] == "hermes-final"


def test_issue_runner_preflight_accepts_external_codex_service_without_command(tmp_path):
    from workflows.issue_runner.preflight import run_preflight

    root = tmp_path / "attmous-daedalus-issue-runner"
    repo = tmp_path / "repo"
    root.mkdir()
    repo.mkdir()
    (root / "config").mkdir()
    (root / "config" / "issues.json").write_text(
        json.dumps({"issues": [{"id": "ISSUE-1", "title": "One", "state": "todo"}]}),
        encoding="utf-8",
    )

    result = run_preflight(
        {
            "workflow": "issue-runner",
            "schema-version": 1,
            "instance": {"name": root.name, "engine-owner": "hermes"},
            "repository": {"local-path": str(repo), "slug": "attmous/daedalus"},
            "tracker": {"kind": "local-json", "path": "config/issues.json"},
            "workspace": {"root": "workspace/issues"},
            "agent": {"name": "runner", "model": "gpt-5.4", "runtime": "codex-app-server"},
            "runtimes": {
                "codex-app-server": {
                    "kind": "codex-app-server",
                    "mode": "external",
                    "endpoint": "ws://127.0.0.1:4500",
                    "ephemeral": False,
                    "keep_alive": True,
                }
            },
            "storage": {"status": "memory/status.json", "health": "memory/health.json", "audit-log": "memory/audit.jsonl"},
        },
        workflow_root=root,
    )

    assert result.ok is True
