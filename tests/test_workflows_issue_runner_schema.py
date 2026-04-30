from pathlib import Path

import jsonschema
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


def _config() -> dict:
    return {
        "workflow": "issue-runner",
        "schema-version": 1,
        "instance": {"name": "attmous-daedalus-issue-runner", "engine-owner": "hermes"},
        "repository": {"local-path": "/tmp/repo", "github-slug": "attmous/daedalus"},
        "tracker": {
            "kind": "local-json",
            "path": "config/issues.json",
            "active_states": ["todo"],
            "terminal_states": ["done"],
        },
        "workspace": {"root": "workspace/issues"},
        "agent": {
            "name": "runner",
            "model": "claude-sonnet-4-6",
            "runtime": "default",
            "max_concurrent_agents": 1,
            "max_turns": 20,
            "max_retry_backoff_ms": 300000,
        },
        "codex": {
            "command": "codex app-server",
            "ephemeral": False,
            "approval_policy": "never",
            "thread_sandbox": "workspace-write",
            "turn_sandbox_policy": "workspace-write",
            "turn_timeout_ms": 3600000,
            "read_timeout_ms": 5000,
            "stall_timeout_ms": 300000,
        },
        "daedalus": {
            "runtimes": {
                "default": {
                    "kind": "claude-cli",
                    "max-turns-per-invocation": 8,
                    "timeout-seconds": 60,
                }
            }
        },
        "storage": {
            "status": "memory/workflow-status.json",
            "health": "memory/workflow-health.json",
            "audit-log": "memory/workflow-audit.jsonl",
        },
    }


def test_issue_runner_schema_accepts_minimal_valid_config():
    schema = yaml.safe_load(
        (REPO_ROOT / "daedalus" / "workflows" / "issue_runner" / "schema.yaml").read_text(encoding="utf-8")
    )
    jsonschema.validate(_config(), schema)


def test_issue_runner_schema_rejects_wrong_workflow_name():
    schema = yaml.safe_load(
        (REPO_ROOT / "daedalus" / "workflows" / "issue_runner" / "schema.yaml").read_text(encoding="utf-8")
    )
    cfg = _config()
    cfg["workflow"] = "change-delivery"
    try:
        jsonschema.validate(cfg, schema)
    except jsonschema.ValidationError:
        return
    raise AssertionError("expected schema validation error for wrong workflow name")


def test_issue_runner_schema_accepts_linear_tracker_and_codex_runtime():
    schema = yaml.safe_load(
        (REPO_ROOT / "daedalus" / "workflows" / "issue_runner" / "schema.yaml").read_text(encoding="utf-8")
    )
    cfg = _config()
    cfg["tracker"] = {
        "kind": "linear",
        "endpoint": "https://api.linear.app/graphql",
        "api_key": "$LINEAR_API_KEY",
        "project_slug": "core",
        "active_states": ["Todo"],
        "terminal_states": ["Done"],
    }
    cfg["agent"]["runtime"] = "codex"
    cfg["daedalus"] = {
        "runtimes": {
            "codex": {
                "kind": "codex-app-server",
                "command": "codex app-server",
                "approval_policy": "never",
                "thread_sandbox": "workspace-write",
                "turn_sandbox_policy": "workspace-write",
                "turn_timeout_ms": 3600000,
                "read_timeout_ms": 5000,
                "stall_timeout_ms": 300000,
            }
        }
    }
    jsonschema.validate(cfg, schema)


def test_issue_runner_schema_accepts_external_codex_app_server_runtime():
    schema = yaml.safe_load(
        (REPO_ROOT / "daedalus" / "workflows" / "issue_runner" / "schema.yaml").read_text(encoding="utf-8")
    )
    cfg = _config()
    cfg["agent"]["runtime"] = "codex"
    cfg["daedalus"] = {
        "runtimes": {
            "codex": {
                "kind": "codex-app-server",
                "mode": "external",
                "endpoint": "ws://127.0.0.1:4500",
                "healthcheck_path": "/readyz",
                "ws_token_env": "CODEX_APP_SERVER_TOKEN",
                "ephemeral": False,
                "keep_alive": True,
                "approval_policy": "never",
                "thread_sandbox": "workspace-write",
                "turn_sandbox_policy": "workspace-write",
            }
        }
    }
    jsonschema.validate(cfg, schema)


def test_issue_runner_schema_accepts_github_tracker():
    schema = yaml.safe_load(
        (REPO_ROOT / "daedalus" / "workflows" / "issue_runner" / "schema.yaml").read_text(encoding="utf-8")
    )
    cfg = _config()
    cfg["tracker"] = {
        "kind": "github",
        "active_states": ["open"],
        "terminal_states": ["closed"],
        "required_labels": ["ready"],
        "exclude_labels": ["blocked"],
    }
    jsonschema.validate(cfg, schema)
