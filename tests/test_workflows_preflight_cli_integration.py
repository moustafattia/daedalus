"""Smoke integration: run_cli + preflight failure path.

Builds a minimal workflow_root with an unsupported runtime.kind and
verifies:

1. ``workflows.run_cli`` raises ``WorkflowContractError`` with the
   preflight error code/detail in the message.
2. A ``daedalus.dispatch_skipped`` event with ``code=unsupported_runtime_kind``
   was appended to the workflow event log.
"""
from __future__ import annotations

import importlib
import json
import sys

import pytest


def _reset_workflows_module_cache():
    for mod in list(sys.modules):
        if mod == "workflows" or mod.startswith("workflows."):
            del sys.modules[mod]


def _write_workflow_with_preflight(tmp_path, *, name="preflight-wf"):
    """Drop a minimal workflow package whose run_preflight delegates to the
    real change_delivery.preflight.run_preflight (so we exercise the real enum)."""
    slug = name.replace("-", "_")
    (tmp_path / "workflows" / slug).mkdir(parents=True, exist_ok=True)
    schema = (
        "$schema: http://json-schema.org/draft-07/schema#\n"
        "type: object\n"
        "required: [workflow, schema-version]\n"
        "properties:\n"
        "  workflow: {type: string}\n"
        "  schema-version: {type: integer}\n"
        "  runtimes:\n"
        "    type: object\n"
        "    additionalProperties:\n"
        "      type: object\n"
        "      properties:\n"
        "        kind: {type: string}\n"
    )
    (tmp_path / "workflows" / slug / "schema.yaml").write_text(schema, encoding="utf-8")
    (tmp_path / "workflows" / slug / "__init__.py").write_text(
        f"from pathlib import Path\n"
        f"from workflows.change_delivery.preflight import run_preflight\n"
        f"NAME = {name!r}\n"
        f"SUPPORTED_SCHEMA_VERSIONS = (1,)\n"
        f"CONFIG_SCHEMA_PATH = Path(__file__).parent / 'schema.yaml'\n"
        # Codex P1 on PR #21: preflight only fires for gated commands.
        # Test invokes 'tick' so include it in the gated set.
        f"PREFLIGHT_GATED_COMMANDS = frozenset({{'tick'}})\n"
        f"def make_workspace(*, workflow_root, config):\n"
        f"    return {{}}\n"
        f"def cli_main(ws, argv):\n"
        f"    return 0\n",
        encoding="utf-8",
    )


def test_run_cli_emits_dispatch_skipped_on_preflight_failure(tmp_path, monkeypatch):
    _write_workflow_with_preflight(tmp_path, name="preflight-wf")
    workflow_root = tmp_path / "workflow_root"
    workflow_root.mkdir(parents=True)
    (workflow_root / "WORKFLOW.md").write_text(
        "---\n"
        "workflow: preflight-wf\n"
        "schema-version: 1\n"
        "runtimes:\n"
        "  r1:\n"
        "    kind: totally-bogus\n"
        "---\n\nPrompt body\n",
        encoding="utf-8",
    )

    _reset_workflows_module_cache()
    workflows = importlib.import_module("workflows")
    monkeypatch.setattr(
        workflows, "__path__", list(workflows.__path__) + [str(tmp_path / "workflows")]
    )

    with pytest.raises(workflows.WorkflowContractError) as exc:
        workflows.run_cli(workflow_root, ["tick"])

    msg = str(exc.value)
    assert "preflight" in msg.lower()
    assert "unsupported_runtime_kind" in msg

    # Verify the event log was written.
    from workflows.change_delivery.paths import runtime_paths

    event_log_path = runtime_paths(workflow_root)["event_log_path"]
    assert event_log_path.exists(), f"event log not created at {event_log_path}"
    lines = [
        json.loads(line)
        for line in event_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    skipped = [e for e in lines if e.get("event") == "daedalus.dispatch_skipped"]
    assert len(skipped) == 1, f"expected 1 dispatch_skipped event, got {lines}"
    ev = skipped[0]
    assert ev["code"] == "unsupported_runtime_kind"
    assert ev["workflow"] == "preflight-wf"
    assert "totally-bogus" in (ev.get("detail") or "")


def test_run_cli_skips_preflight_for_non_dispatch_commands(tmp_path, monkeypatch):
    """Codex P1 on PR #21: diagnostic commands (status, doctor, ...) MUST
    bypass preflight gating so operators can debug a broken-config workspace.

    Builds the same broken-config setup as the preflight-failure test, but
    invokes a non-gated command. cli_main should be reached without raising.
    """
    _write_workflow_with_preflight(tmp_path, name="preflight-skip-wf")
    workflow_root = tmp_path / "workflow_root_skip"
    workflow_root.mkdir(parents=True)
    (workflow_root / "WORKFLOW.md").write_text(
        "---\n"
        "workflow: preflight-skip-wf\n"
        "schema-version: 1\n"
        "runtimes:\n"
        "  r1:\n"
        "    kind: totally-bogus\n"
        "---\n\nPrompt body\n",
        encoding="utf-8",
    )

    _reset_workflows_module_cache()
    workflows = importlib.import_module("workflows")
    monkeypatch.setattr(
        workflows, "__path__", list(workflows.__path__) + [str(tmp_path / "workflows")]
    )

    # PREFLIGHT_GATED_COMMANDS in the fixture is frozenset({'tick'}), so
    # 'status' is NOT gated. Despite the unsupported runtime kind that
    # would fail preflight, run_cli must reach cli_main and return 0.
    rc = workflows.run_cli(workflow_root, ["status"])
    assert rc == 0, "non-gated command must skip preflight even with a broken config"
