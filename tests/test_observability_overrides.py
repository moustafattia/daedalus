"""Read/write the observability-overrides.json file."""
import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _module():
    return load_module("daedalus_observability_overrides_test", "observability_overrides.py")


def test_set_creates_file_when_absent(tmp_path):
    over = _module()
    state_dir = tmp_path / "state"
    over.set_override(state_dir, workflow_name="change-delivery", github_comments_enabled=True, set_by="operator-cli")
    file = state_dir / "observability-overrides.json"
    assert file.exists()
    data = json.loads(file.read_text())
    assert data["change-delivery"]["github-comments"]["enabled"] is True
    assert data["change-delivery"]["github-comments"]["set-by"] == "operator-cli"
    assert "set-at" in data["change-delivery"]["github-comments"]


def test_set_updates_existing_file_preserving_other_workflows(tmp_path):
    over = _module()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "observability-overrides.json").write_text(json.dumps({
        "other-workflow": {"github-comments": {"enabled": True}}
    }))
    over.set_override(state_dir, workflow_name="change-delivery", github_comments_enabled=False)
    data = json.loads((state_dir / "observability-overrides.json").read_text())
    assert data["other-workflow"]["github-comments"]["enabled"] is True  # preserved
    assert data["change-delivery"]["github-comments"]["enabled"] is False


def test_unset_removes_only_the_targeted_workflow_block(tmp_path):
    over = _module()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "observability-overrides.json").write_text(json.dumps({
        "change-delivery": {"github-comments": {"enabled": True}},
        "other-workflow": {"github-comments": {"enabled": True}},
    }))
    over.unset_override(state_dir, workflow_name="change-delivery")
    data = json.loads((state_dir / "observability-overrides.json").read_text())
    assert "change-delivery" not in data
    assert data["other-workflow"]["github-comments"]["enabled"] is True


def test_get_returns_empty_dict_when_file_absent(tmp_path):
    over = _module()
    state_dir = tmp_path / "state"
    out = over.get_override(state_dir, workflow_name="change-delivery")
    assert out == {}


def test_get_returns_workflow_block_when_present(tmp_path):
    over = _module()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "observability-overrides.json").write_text(json.dumps({
        "change-delivery": {"github-comments": {"enabled": True, "set-at": "2026-04-26T00:00:00Z"}}
    }))
    out = over.get_override(state_dir, workflow_name="change-delivery")
    assert out["github-comments"]["enabled"] is True
