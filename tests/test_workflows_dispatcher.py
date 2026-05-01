# tests/test_workflows_dispatcher.py
import importlib
import sys
from pathlib import Path

import pytest
import yaml


def test_load_workflow_returns_module_when_contract_is_complete(tmp_path, monkeypatch):
    """A package exposing all five required attributes loads and is returned as-is."""
    # Build a fake workflow sub-package in tmp_path/workflows/fake_wf/
    wf_root = tmp_path / "workflows"
    (wf_root / "fake_wf").mkdir(parents=True)
    (wf_root / "fake_wf" / "__init__.py").write_text(
        "from pathlib import Path\n"
        "NAME = 'fake-wf'\n"
        "SUPPORTED_SCHEMA_VERSIONS = (1,)\n"
        "CONFIG_SCHEMA_PATH = Path(__file__).parent / 'schema.yaml'\n"
        "def make_workspace(*, workflow_root, config): return {}\n"
        "def cli_main(ws, argv): return 0\n",
        encoding="utf-8",
    )
    # Ensure any stale workflows modules are cleared before we start
    for mod in list(sys.modules):
        if mod == "workflows" or mod.startswith("workflows."):
            del sys.modules[mod]

    # Load the real workflows package from the repo and extend its __path__ so
    # that importlib can find the fake_wf sub-package in tmp_path.
    workflows = importlib.import_module("workflows")
    monkeypatch.setattr(workflows, "__path__", list(workflows.__path__) + [str(wf_root)])

    module = workflows.load_workflow("fake-wf")

    assert module.NAME == "fake-wf"
    assert module.SUPPORTED_SCHEMA_VERSIONS == (1,)
    assert callable(module.make_workspace)
    assert callable(module.cli_main)


def test_load_workflow_raises_on_missing_attributes(tmp_path, monkeypatch):
    """Workflow packages missing any required contract attribute raise WorkflowContractError
    listing every missing name."""
    wf_root = tmp_path / "workflows"
    (wf_root / "incomplete").mkdir(parents=True)
    (wf_root / "incomplete" / "__init__.py").write_text(
        "NAME = 'incomplete'\n",  # missing the other four attrs
        encoding="utf-8",
    )
    for mod in list(sys.modules):
        if mod == "workflows" or mod.startswith("workflows."):
            del sys.modules[mod]

    workflows = importlib.import_module("workflows")
    monkeypatch.setattr(workflows, "__path__", list(workflows.__path__) + [str(wf_root)])

    with pytest.raises(workflows.WorkflowContractError) as exc:
        workflows.load_workflow("incomplete")
    msg = str(exc.value)
    assert "missing required attributes" in msg
    assert "SUPPORTED_SCHEMA_VERSIONS" in msg
    assert "CONFIG_SCHEMA_PATH" in msg
    assert "make_workspace" in msg
    assert "cli_main" in msg


def test_load_workflow_raises_when_name_does_not_match_directory(tmp_path, monkeypatch):
    """A workflow module declaring NAME that does not match its directory name
    raises WorkflowContractError citing both names."""
    wf_root = tmp_path / "workflows"
    (wf_root / "mismatched").mkdir(parents=True)
    (wf_root / "mismatched" / "__init__.py").write_text(
        "from pathlib import Path\n"
        "NAME = 'some-other-name'\n"
        "SUPPORTED_SCHEMA_VERSIONS = (1,)\n"
        "CONFIG_SCHEMA_PATH = Path(__file__).parent / 's.yaml'\n"
        "def make_workspace(*, workflow_root, config): return None\n"
        "def cli_main(ws, argv): return 0\n",
        encoding="utf-8",
    )
    for mod in list(sys.modules):
        if mod == "workflows" or mod.startswith("workflows."):
            del sys.modules[mod]

    workflows = importlib.import_module("workflows")
    monkeypatch.setattr(workflows, "__path__", list(workflows.__path__) + [str(wf_root)])

    with pytest.raises(workflows.WorkflowContractError) as exc:
        workflows.load_workflow("mismatched")
    assert "declares NAME='some-other-name'" in str(exc.value)
    assert "'mismatched'" in str(exc.value)


def _write_stub_workflow(tmp_path, *, name="stub-wf", supported=(1,), raises=None):
    """Drop a minimal workflow package under tmp_path/workflows/<slug>/ + schema.yaml."""
    slug = name.replace("-", "_")
    (tmp_path / "workflows" / slug).mkdir(parents=True, exist_ok=True)
    # Minimal schema: require top-level 'workflow' + 'schema-version' keys
    schema = (
        "$schema: http://json-schema.org/draft-07/schema#\n"
        "type: object\n"
        "required: [workflow, schema-version]\n"
        "properties:\n"
        "  workflow: {type: string}\n"
        "  schema-version: {type: integer}\n"
    )
    (tmp_path / "workflows" / slug / "schema.yaml").write_text(schema, encoding="utf-8")
    supported_tuple = f"({', '.join(str(v) for v in supported)},)"
    raise_line = f"    raise {raises}('boom')\n" if raises else ""
    (tmp_path / "workflows" / slug / "__init__.py").write_text(
        f"from pathlib import Path\n"
        f"NAME = {name!r}\n"
        f"SUPPORTED_SCHEMA_VERSIONS = {supported_tuple}\n"
        f"CONFIG_SCHEMA_PATH = Path(__file__).parent / 'schema.yaml'\n"
        f"def make_workspace(*, workflow_root, config):\n"
        f"    return {{'cfg': config, 'root': str(workflow_root)}}\n"
        f"def cli_main(ws, argv):\n"
        f"{raise_line}    print(f'ran {name} with argv={{argv}}')\n"
        f"    return 0\n",
        encoding="utf-8",
    )


def _reset_workflows_module_cache():
    for mod in list(sys.modules):
        if mod == "workflows" or mod.startswith("workflows."):
            del sys.modules[mod]


def _write_workflow_markdown(workspace_root: Path, *, workflow_name: str = "demo-wf", body: str = "Prompt body") -> None:
    front_matter = {
        "workflow": workflow_name,
        "schema-version": 1,
    }
    (workspace_root / "WORKFLOW.md").write_text(
        "---\n" + yaml.safe_dump(front_matter, sort_keys=False) + "---\n\n" + body + "\n",
        encoding="utf-8",
    )


def test_run_cli_dispatches_to_named_workflow_and_returns_its_exit_code(tmp_path, monkeypatch, capsys):
    _write_stub_workflow(tmp_path, name="demo-wf")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_workflow_markdown(workspace_root, workflow_name="demo-wf")
    _reset_workflows_module_cache()
    workflows = importlib.import_module("workflows")
    monkeypatch.setattr(workflows, "__path__", list(workflows.__path__) + [str(tmp_path / "workflows")])

    code = workflows.run_cli(workspace_root, ["status", "--json"])

    assert code == 0
    assert "ran demo-wf with argv=['status', '--json']" in capsys.readouterr().out


def test_run_cli_dispatches_when_workflow_contract_is_markdown(tmp_path, monkeypatch, capsys):
    _write_stub_workflow(tmp_path, name="demo-wf")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_workflow_markdown(workspace_root, workflow_name="demo-wf", body="Markdown prompt body")
    _reset_workflows_module_cache()
    workflows = importlib.import_module("workflows")
    monkeypatch.setattr(workflows, "__path__", list(workflows.__path__) + [str(tmp_path / "workflows")])

    code = workflows.run_cli(workspace_root, ["status"])

    assert code == 0
    assert "ran demo-wf with argv=['status']" in capsys.readouterr().out


def test_run_cli_raises_when_workflow_key_missing(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "WORKFLOW.md").write_text(
        "---\nschema-version: 1\n---\n\nPrompt body\n",  # missing 'workflow:'
        encoding="utf-8",
    )
    _reset_workflows_module_cache()
    workflows = importlib.import_module("workflows")

    with pytest.raises(workflows.WorkflowContractError) as exc:
        workflows.run_cli(workspace_root, [])
    assert "missing top-level `workflow:` field" in str(exc.value)


def test_run_cli_raises_on_unsupported_schema_version(tmp_path, monkeypatch):
    _write_stub_workflow(tmp_path, name="demo-wf", supported=(1, 2))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_workflow_markdown(workspace_root, workflow_name="demo-wf")
    (workspace_root / "WORKFLOW.md").write_text(
        "---\nworkflow: demo-wf\nschema-version: 99\n---\n\nPrompt body\n",
        encoding="utf-8",
    )
    _reset_workflows_module_cache()
    workflows = importlib.import_module("workflows")
    monkeypatch.setattr(workflows, "__path__", list(workflows.__path__) + [str(tmp_path / "workflows")])

    with pytest.raises(workflows.WorkflowContractError) as exc:
        workflows.run_cli(workspace_root, [])
    assert "does not support schema-version=99" in str(exc.value)
    assert "[1, 2]" in str(exc.value)


def test_run_cli_raises_when_require_workflow_does_not_match_contract(tmp_path, monkeypatch):
    _write_stub_workflow(tmp_path, name="demo-wf")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_workflow_markdown(workspace_root, workflow_name="demo-wf")
    _reset_workflows_module_cache()
    workflows = importlib.import_module("workflows")
    monkeypatch.setattr(workflows, "__path__", list(workflows.__path__) + [str(tmp_path / "workflows")])

    with pytest.raises(workflows.WorkflowContractError) as exc:
        workflows.run_cli(workspace_root, [], require_workflow="other-wf")
    assert "declares workflow='demo-wf'" in str(exc.value)
    assert "require_workflow='other-wf'" in str(exc.value)


def test_run_cli_raises_on_schema_validation_error(tmp_path, monkeypatch):
    _write_stub_workflow(tmp_path, name="demo-wf")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    # schema-version has the wrong type — must be integer per the stub schema.
    (workspace_root / "WORKFLOW.md").write_text(
        "---\nworkflow: demo-wf\nschema-version: not-an-integer\n---\n\nPrompt body\n",
        encoding="utf-8",
    )
    _reset_workflows_module_cache()
    workflows = importlib.import_module("workflows")
    monkeypatch.setattr(workflows, "__path__", list(workflows.__path__) + [str(tmp_path / "workflows")])

    # run_cli must raise a jsonschema.ValidationError (NOT a WorkflowContractError)
    # because the schema's own type check is what fails. It must happen before
    # the workflow's cli_main is called.
    import jsonschema
    with pytest.raises(jsonschema.ValidationError):
        workflows.run_cli(workspace_root, [])


def test_main_parses_workflow_root_flag_and_invokes_run_cli(tmp_path, monkeypatch, capsys):
    _write_stub_workflow(tmp_path, name="demo-wf")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_workflow_markdown(workspace_root, workflow_name="demo-wf")
    _reset_workflows_module_cache()
    workflows = importlib.import_module("workflows")
    monkeypatch.setattr(workflows, "__path__", list(workflows.__path__) + [str(tmp_path / "workflows")])

    workflows_main = importlib.import_module("workflows.__main__")
    code = workflows_main.main([
        "--workflow-root", str(workspace_root),
        "status", "--json",
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "ran demo-wf with argv=['status', '--json']" in out


def test_main_uses_env_fallback_when_no_workflow_root_flag(tmp_path, monkeypatch, capsys):
    _write_stub_workflow(tmp_path, name="demo-wf")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_workflow_markdown(workspace_root, workflow_name="demo-wf")
    _reset_workflows_module_cache()
    monkeypatch.setenv("DAEDALUS_WORKFLOW_ROOT", str(workspace_root))
    workflows = importlib.import_module("workflows")
    monkeypatch.setattr(workflows, "__path__", list(workflows.__path__) + [str(tmp_path / "workflows")])

    workflows_main = importlib.import_module("workflows.__main__")
    code = workflows_main.main(["tick"])
    assert code == 0
