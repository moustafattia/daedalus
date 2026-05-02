"""Synthesizing the parsed lane-selection config from workflow contracts."""
import importlib.util
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
    return load_module(
        "daedalus_workflow_lane_selection_config_test",
        "workflows/change_delivery/lane_selection.py",
    )


def test_synthesize_defaults_when_block_absent():
    ls = _module()
    cfg = ls.parse_config(workflow_yaml={}, active_lane_label="active-lane")
    assert cfg["require-labels"] == []
    assert cfg["allow-any-of"] == []
    # The active-lane label is auto-excluded so the picker can never pick
    # an already-promoted issue, even if the operator forgets to list it.
    assert "active-lane" in cfg["exclude-labels"]
    assert cfg["priority"] == []
    assert cfg["tiebreak"] == "oldest"


def test_synthesize_uses_active_lane_label_in_excludes():
    ls = _module()
    cfg = ls.parse_config(workflow_yaml={}, active_lane_label="custom-active")
    assert "custom-active" in cfg["exclude-labels"]


def test_user_excludes_are_merged_with_active_lane_label():
    ls = _module()
    cfg = ls.parse_config(
        workflow_yaml={"lane-selection": {"exclude-labels": ["blocked", "do-not-touch"]}},
        active_lane_label="active-lane",
    )
    # Both user-provided and auto-injected
    assert set(cfg["exclude-labels"]) == {"blocked", "do-not-touch", "active-lane"}


def test_user_explicit_active_lane_label_in_excludes_does_not_duplicate():
    ls = _module()
    cfg = ls.parse_config(
        workflow_yaml={"lane-selection": {"exclude-labels": ["active-lane", "blocked"]}},
        active_lane_label="active-lane",
    )
    # Set semantics — no duplicate
    assert cfg["exclude-labels"].count("active-lane") == 1
    assert "blocked" in cfg["exclude-labels"]


def test_full_block_passes_through():
    ls = _module()
    cfg = ls.parse_config(
        workflow_yaml={
            "lane-selection": {
                "require-labels": ["needs-review"],
                "allow-any-of": ["urgent", "p0"],
                "exclude-labels": ["blocked"],
                "priority": ["severity:critical", "severity:high"],
                "tiebreak": "newest",
            }
        },
        active_lane_label="active-lane",
    )
    assert cfg["require-labels"] == ["needs-review"]
    assert cfg["allow-any-of"] == ["urgent", "p0"]
    assert cfg["priority"] == ["severity:critical", "severity:high"]
    assert cfg["tiebreak"] == "newest"
    assert "active-lane" in cfg["exclude-labels"]


def test_label_strings_are_lowercased():
    """GitHub label matching is case-insensitive in our existing label_set helper.
    The parsed config should normalize so set comparisons later are clean."""
    ls = _module()
    cfg = ls.parse_config(
        workflow_yaml={"lane-selection": {"require-labels": ["Needs-Review"], "exclude-labels": ["BLOCKED"]}},
        active_lane_label="Active-Lane",
    )
    assert cfg["require-labels"] == ["needs-review"]
    assert "blocked" in cfg["exclude-labels"]
    assert "active-lane" in cfg["exclude-labels"]
