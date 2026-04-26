"""Resolution of effective observability config (override > yaml > default)."""
import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _module():
    return load_module(
        "daedalus_workflow_code_review_observability_test",
        "workflows/code_review/observability.py",
    )


def test_default_when_yaml_block_absent_and_no_override(tmp_path):
    obs = _module()
    cfg = obs.resolve_effective_config(workflow_yaml={}, override_dir=tmp_path, workflow_name="code-review")
    assert cfg["github-comments"]["enabled"] is False
    assert cfg["github-comments"]["mode"] == "edit-in-place"
    assert cfg["github-comments"]["include-events"] == []
    assert cfg["github-comments"]["suppress-transient-failures"] is True
    assert cfg["source"]["github-comments"] == "default"


def test_yaml_block_picked_up_when_no_override(tmp_path):
    obs = _module()
    yaml_block = {
        "observability": {
            "github-comments": {
                "enabled": True,
                "mode": "edit-in-place",
                "include-events": ["merge-and-promote"],
                "suppress-transient-failures": False,
            }
        }
    }
    cfg = obs.resolve_effective_config(
        workflow_yaml=yaml_block, override_dir=tmp_path, workflow_name="code-review"
    )
    assert cfg["github-comments"]["enabled"] is True
    assert cfg["github-comments"]["include-events"] == ["merge-and-promote"]
    assert cfg["github-comments"]["suppress-transient-failures"] is False
    assert cfg["source"]["github-comments"] == "yaml"


def test_override_file_wins_over_yaml(tmp_path):
    obs = _module()
    yaml_block = {"observability": {"github-comments": {"enabled": True}}}
    override_file = tmp_path / "observability-overrides.json"
    override_file.write_text(json.dumps({
        "code-review": {"github-comments": {"enabled": False, "set-at": "2026-04-26T00:00:00Z"}}
    }))
    cfg = obs.resolve_effective_config(
        workflow_yaml=yaml_block, override_dir=tmp_path, workflow_name="code-review"
    )
    assert cfg["github-comments"]["enabled"] is False
    assert cfg["source"]["github-comments"] == "override"


def test_override_for_other_workflow_is_ignored(tmp_path):
    obs = _module()
    yaml_block = {"observability": {"github-comments": {"enabled": True}}}
    override_file = tmp_path / "observability-overrides.json"
    override_file.write_text(json.dumps({
        "other-workflow": {"github-comments": {"enabled": False}}
    }))
    cfg = obs.resolve_effective_config(
        workflow_yaml=yaml_block, override_dir=tmp_path, workflow_name="code-review"
    )
    assert cfg["github-comments"]["enabled"] is True
    assert cfg["source"]["github-comments"] == "yaml"


def test_override_file_corrupt_falls_through_to_yaml(tmp_path):
    obs = _module()
    yaml_block = {"observability": {"github-comments": {"enabled": True}}}
    (tmp_path / "observability-overrides.json").write_text("not json{")
    cfg = obs.resolve_effective_config(
        workflow_yaml=yaml_block, override_dir=tmp_path, workflow_name="code-review"
    )
    # Corrupt override is ignored, yaml wins, source reflects fallback.
    assert cfg["github-comments"]["enabled"] is True
    assert cfg["source"]["github-comments"] == "yaml"


def test_event_is_included_respects_include_events_list(tmp_path):
    obs = _module()
    cfg = {"github-comments": {"enabled": True, "include-events": ["merge-and-promote"]}}
    assert obs.event_is_included(cfg, "merge-and-promote") is True
    assert obs.event_is_included(cfg, "dispatch-implementation-turn") is False


def test_event_is_included_empty_list_means_all_events(tmp_path):
    """An empty include-events list = include every audit action (operator can opt out per-event later)."""
    obs = _module()
    cfg = {"github-comments": {"enabled": True, "include-events": []}}
    assert obs.event_is_included(cfg, "anything") is True
