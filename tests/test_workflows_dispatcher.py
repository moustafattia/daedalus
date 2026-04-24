# tests/test_workflows_dispatcher.py
import importlib
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _import_workflows():
    """Import workflows/__init__.py without poisoning sys.modules for later tests."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    if "workflows" in sys.modules:
        del sys.modules["workflows"]
    return importlib.import_module("workflows")


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
