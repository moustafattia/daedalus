import importlib
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def test_issue_runner_package_exposes_contract_attributes():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    for mod in list(sys.modules):
        if mod == "workflows" or mod.startswith("workflows."):
            del sys.modules[mod]

    module = importlib.import_module("workflows.issue_runner")

    assert module.NAME == "issue-runner"
    assert isinstance(module.SUPPORTED_SCHEMA_VERSIONS, tuple)
    assert 1 in module.SUPPORTED_SCHEMA_VERSIONS
    assert isinstance(module.CONFIG_SCHEMA_PATH, Path)
    assert module.CONFIG_SCHEMA_PATH.exists()
    assert callable(module.make_workspace)
    assert callable(module.cli_main)


def test_issue_runner_load_workflow_succeeds():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    for mod in list(sys.modules):
        if mod == "workflows" or mod.startswith("workflows."):
            del sys.modules[mod]

    workflows = importlib.import_module("workflows")
    module = workflows.load_workflow("issue-runner")
    assert module.NAME == "issue-runner"


def test_issue_runner_main_pins_workflow_via_require_workflow(tmp_path):
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

    main_mod = importlib.import_module("workflows.issue_runner.__main__")
    with pytest.raises(workflows.WorkflowContractError) as exc:
        main_mod.main(["--workflow-root", str(workspace_root), "status"])
    assert "require_workflow='issue-runner'" in str(exc.value)
