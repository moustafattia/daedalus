import importlib
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_code_review_package_exposes_all_five_contract_attributes():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    for mod in list(sys.modules):
        if mod == "workflows" or mod.startswith("workflows."):
            del sys.modules[mod]

    module = importlib.import_module("workflows.code_review")

    assert module.NAME == "code-review"
    assert isinstance(module.SUPPORTED_SCHEMA_VERSIONS, tuple)
    assert 1 in module.SUPPORTED_SCHEMA_VERSIONS
    assert isinstance(module.CONFIG_SCHEMA_PATH, Path)
    assert module.CONFIG_SCHEMA_PATH.exists(), f"schema.yaml missing at {module.CONFIG_SCHEMA_PATH}"
    assert callable(module.make_workspace)
    assert callable(module.cli_main)


def test_code_review_load_workflow_succeeds():
    """The dispatcher must be able to load this workflow without error."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    for mod in list(sys.modules):
        if mod == "workflows" or mod.startswith("workflows."):
            del sys.modules[mod]

    workflows = importlib.import_module("workflows")
    module = workflows.load_workflow("code-review")
    assert module.NAME == "code-review"
