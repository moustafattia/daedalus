import importlib
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def test_change_delivery_package_exposes_all_five_contract_attributes():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    for mod in list(sys.modules):
        if mod == "workflows" or mod.startswith("workflows."):
            del sys.modules[mod]

    module = importlib.import_module("workflows.change_delivery")

    assert module.NAME == "change-delivery"
    assert isinstance(module.SUPPORTED_SCHEMA_VERSIONS, tuple)
    assert 1 in module.SUPPORTED_SCHEMA_VERSIONS
    assert isinstance(module.CONFIG_SCHEMA_PATH, Path)
    assert module.CONFIG_SCHEMA_PATH.exists(), f"schema.yaml missing at {module.CONFIG_SCHEMA_PATH}"
    assert callable(module.make_workspace)
    assert callable(module.cli_main)


def test_change_delivery_load_workflow_succeeds():
    """The dispatcher must be able to load this workflow without error."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    for mod in list(sys.modules):
        if mod == "workflows" or mod.startswith("workflows."):
            del sys.modules[mod]

    workflows = importlib.import_module("workflows")
    module = workflows.load_workflow("change-delivery")
    assert module.NAME == "change-delivery"


def test_change_delivery_main_pins_workflow_via_require_workflow(tmp_path, monkeypatch):
    """workflows.change_delivery.__main__ must pass require_workflow='change-delivery' to run_cli."""
    import pytest
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    for mod in list(sys.modules):
        if mod == "workflows" or mod.startswith("workflows."):
            del sys.modules[mod]

    import workflows
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    (workspace_root / "WORKFLOW.md").write_text(
        "---\nworkflow: some-other-workflow\nschema-version: 1\n---\n\nPrompt body\n",
        encoding="utf-8",
    )

    main_mod = importlib.import_module("workflows.change_delivery.__main__")
    with pytest.raises(workflows.WorkflowContractError) as exc:
        main_mod.main(["--workflow-root", str(workspace_root), "status"])
    assert "require_workflow='change-delivery'" in str(exc.value)
