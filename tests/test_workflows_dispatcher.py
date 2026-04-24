# tests/test_workflows_dispatcher.py
import importlib
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


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
