from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from workflows.contract import render_workflow_markdown
from workflows.runtime_matrix import build_runtime_matrix_report


def _runtime_smoke_command() -> list[str]:
    code = (
        "import json, os, pathlib; "
        "prompt = pathlib.Path(os.environ['DAEDALUS_PROMPT_PATH']).read_text(encoding='utf-8'); "
        "pathlib.Path(os.environ['DAEDALUS_RESULT_PATH']).write_text(json.dumps({"
        "'output': 'matrix-ok:' + os.environ['DAEDALUS_SESSION_NAME'], "
        "'last_event': 'turn/completed', "
        "'last_message': prompt.splitlines()[0], "
        "'turn_count': 1, "
        "'tokens': {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2}"
        "}), encoding='utf-8'); "
        "print('stdout fallback')"
    )
    return [sys.executable, "-c", code]


def _write_contract(root: Path, config: dict, prompt: str = "Policy body.") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "WORKFLOW.md").write_text(
        render_workflow_markdown(config=config, prompt_template=prompt),
        encoding="utf-8",
    )


def test_runtime_matrix_executes_issue_runner_command_runtime(tmp_path):
    root = tmp_path / "attmous-daedalus-issue-runner"
    _write_contract(
        root,
        {
            "workflow": "issue-runner",
            "schema-version": 1,
            "instance": {"name": root.name, "engine-owner": "hermes"},
            "repository": {"local-path": str(tmp_path), "slug": "attmous/daedalus"},
            "tracker": {"kind": "local-json", "path": "config/issues.json"},
            "workspace": {"root": "workspace/issues"},
            "agent": {"name": "runner", "model": "local-smoke", "runtime": "hermes-command"},
            "runtimes": {
                "hermes-command": {
                    "kind": "hermes-agent",
                    "command": _runtime_smoke_command(),
                }
            },
        },
    )

    report = build_runtime_matrix_report(workflow_root=root, execute=True)

    assert report["ok"] is True
    assert report["workflow"] == "issue-runner"
    assert report["matrix"][0]["role"] == "agent"
    assert report["matrix"][0]["runtime"] == "hermes-command"
    assert report["matrix"][0]["kind"] == "hermes-agent"
    assert report["matrix"][0]["smoke"]["ok"] is True
    assert report["matrix"][0]["smoke"]["used_command"] is True
    assert report["matrix"][0]["smoke"]["output_preview"] == "matrix-ok:runtime-matrix-agent"
    assert report["matrix"][0]["smoke"]["tokens"]["total_tokens"] == 2


def test_runtime_matrix_reports_change_delivery_role_bindings(tmp_path):
    root = tmp_path / "attmous-daedalus-change-delivery"
    _write_contract(
        root,
        {
            "workflow": "change-delivery",
            "schema-version": 1,
            "instance": {"name": root.name, "engine-owner": "hermes"},
            "repository": {"local-path": str(tmp_path), "slug": "attmous/daedalus", "active-lane-label": "active-lane"},
            "tracker": {"kind": "github", "github_slug": "attmous/daedalus"},
            "code-host": {"kind": "github", "github_slug": "attmous/daedalus"},
            "runtimes": {
                "hermes-final": {"kind": "hermes-agent", "mode": "final"},
                "codex-service": {
                    "kind": "codex-app-server",
                    "mode": "external",
                    "endpoint": "ws://127.0.0.1:4500",
                    "ephemeral": False,
                    "keep_alive": True,
                },
            },
            "actors": {
                "implementer": {"name": "coder", "model": "gpt-5", "runtime": "codex-service"},
                "implementer-high-effort": {"name": "coder-hi", "model": "gpt-5.5", "runtime": "hermes-final"},
                "reviewer": {"name": "reviewer", "model": "gpt-5", "runtime": "codex-service"},
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
        },
    )

    report = build_runtime_matrix_report(workflow_root=root)
    roles = {item["role"]: item for item in report["matrix"]}

    assert report["ok"] is True
    assert set(roles) == {"implementer", "implementer-high-effort", "reviewer"}
    assert roles["implementer"]["kind"] == "codex-app-server"
    assert roles["implementer-high-effort"]["kind"] == "hermes-agent"
    assert roles["reviewer"]["runtime"] == "codex-service"
    assert all(check["status"] == "pass" for check in report["binding_checks"])


def test_runtime_matrix_cli_executes_selected_role(tmp_path):
    import importlib.util

    repo_root = Path(__file__).resolve().parents[1] / "daedalus"
    spec = importlib.util.spec_from_file_location("daedalus_cli_runtime_matrix_test", repo_root / "daedalus_cli.py")
    tools = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(tools)

    root = tmp_path / "attmous-daedalus-issue-runner"
    _write_contract(
        root,
        {
            "workflow": "issue-runner",
            "schema-version": 1,
            "instance": {"name": root.name, "engine-owner": "hermes"},
            "repository": {"local-path": str(tmp_path), "slug": "attmous/daedalus"},
            "tracker": {"kind": "local-json", "path": "config/issues.json"},
            "workspace": {"root": "workspace/issues"},
            "agent": {"name": "runner", "model": "local-smoke", "runtime": "hermes-command"},
            "runtimes": {"hermes-command": {"kind": "hermes-agent", "command": _runtime_smoke_command()}},
        },
    )

    output = tools.execute_raw_args(f"runtime-matrix --workflow-root {root} --role agent --execute --json")
    payload = json.loads(output)

    assert payload["ok"] is True
    assert payload["matrix"][0]["smoke"]["output_preview"] == "matrix-ok:runtime-matrix-agent"


def test_runtime_matrix_role_filter_ignores_unselected_broken_role(tmp_path):
    root = tmp_path / "attmous-daedalus-change-delivery"
    _write_contract(
        root,
        {
            "workflow": "change-delivery",
            "schema-version": 1,
            "instance": {"name": root.name, "engine-owner": "hermes"},
            "repository": {"local-path": str(tmp_path), "slug": "attmous/daedalus", "active-lane-label": "active-lane"},
            "tracker": {"kind": "github", "github_slug": "attmous/daedalus"},
            "code-host": {"kind": "github", "github_slug": "attmous/daedalus"},
            "runtimes": {
                "hermes-command": {"kind": "hermes-agent", "command": _runtime_smoke_command()},
            },
            "actors": {
                "implementer": {"name": "coder", "model": "gpt-5", "runtime": "hermes-command"},
                "reviewer": {"name": "reviewer", "model": "gpt-5", "runtime": "missing-runtime"},
            },
            "stages": {
                "implement": {"actor": "implementer"},
                "publish": {"action": "pr.publish"},
                "merge": {"action": "pr.merge"},
            },
            "gates": {
                "pre-publish-review": {"type": "agent-review", "actor": "reviewer"},
            },
        },
    )

    report = build_runtime_matrix_report(
        workflow_root=root,
        execute=True,
        roles=["implementer"],
    )

    assert report["ok"] is True
    assert [item["role"] for item in report["matrix"]] == ["implementer"]
    assert report["matrix"][0]["smoke"]["ok"] is True


@pytest.mark.skipif(
    os.environ.get("DAEDALUS_REAL_CODEX_APP_SERVER") != "1",
    reason="set DAEDALUS_REAL_CODEX_APP_SERVER=1 to run the real Codex runtime-matrix smoke",
)
def test_runtime_matrix_real_codex_service_issue_runner_smoke(tmp_path):
    root = tmp_path / "attmous-daedalus-issue-runner"
    _write_contract(
        root,
        {
            "workflow": "issue-runner",
            "schema-version": 1,
            "instance": {"name": root.name, "engine-owner": "hermes"},
            "repository": {"local-path": str(tmp_path), "slug": "attmous/daedalus"},
            "tracker": {"kind": "local-json", "path": "config/issues.json"},
            "workspace": {"root": "workspace/issues"},
            "agent": {
                "name": "runner",
                "model": os.environ.get("DAEDALUS_REAL_CODEX_MODEL", ""),
                "runtime": "codex-service",
            },
            "runtimes": {
                "codex-service": {
                    "kind": "codex-app-server",
                    "mode": "external",
                    "endpoint": os.environ.get("DAEDALUS_REAL_CODEX_ENDPOINT", "ws://127.0.0.1:4500"),
                    "approval_policy": "never",
                    "thread_sandbox": "workspace-write",
                    "turn_sandbox_policy": "workspace-write",
                    "turn_timeout_ms": int(os.environ.get("DAEDALUS_REAL_CODEX_TURN_TIMEOUT_MS", "180000")),
                    "read_timeout_ms": int(os.environ.get("DAEDALUS_REAL_CODEX_READ_TIMEOUT_MS", "5000")),
                    "stall_timeout_ms": int(os.environ.get("DAEDALUS_REAL_CODEX_STALL_TIMEOUT_MS", "60000")),
                    "ephemeral": False,
                    "keep_alive": True,
                }
            },
        },
    )

    report = build_runtime_matrix_report(workflow_root=root, execute=True, roles=["agent"])

    assert report["ok"] is True
    smoke = report["matrix"][0]["smoke"]
    assert smoke["ok"] is True
    assert smoke["thread_id"]
    assert smoke["last_event"] == "turn/completed"
