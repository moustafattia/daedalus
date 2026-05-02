"""Workspace bootstrap parses lane-selection block and threads it to the picker."""
import importlib.util
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name, relative_path):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lane_selection_cfg_synthesized_when_block_absent():
    """A workspace built from a yaml without lane-selection still gets a parsed config attached."""
    workspace = load_module("daedalus_workspace_lane_selection_test_a", "workflows/change_delivery/workspace.py")
    yaml_cfg = {
        "workflow": "change-delivery",
        "schema-version": 1,
        "instance": {"name": "x", "engine-owner": "hermes"},
        "repository": {"local-path": "/tmp", "slug": "o/r", "active-lane-label": "active-lane"},
        "tracker": {"kind": "github", "github_slug": "o/r", "active_states": ["open"], "terminal_states": ["closed"]},
        "code-host": {"kind": "github", "github_slug": "o/r"},
        "runtimes": {"acpx-codex": {"kind": "acpx-codex", "session-idle-freshness-seconds": 1, "session-idle-grace-seconds": 1, "session-nudge-cooldown-seconds": 1}},
        "actors": {"implementer": {"name": "x", "model": "y", "runtime": "acpx-codex"}, "reviewer": {"name": "x", "model": "y", "runtime": "acpx-codex"}},
        "stages": {"implement": {"actor": "implementer"}},
        "gates": {"pre-publish-review": {"type": "agent-review", "actor": "reviewer"}},
        "triggers": {"lane-selector": {"type": "github-label", "label": "active-lane"}},
        "storage": {"ledger": "l", "health": "h", "audit-log": "a"},
    }
    cfg = workspace._derive_lane_selection_cfg(yaml_cfg, active_lane_label="active-lane")
    assert cfg["require-labels"] == []
    assert "active-lane" in cfg["exclude-labels"]
    assert cfg["tiebreak"] == "oldest"


def test_lane_selection_cfg_picked_up_when_block_present():
    workspace = load_module("daedalus_workspace_lane_selection_test_b", "workflows/change_delivery/workspace.py")
    yaml_cfg = {
        "lane-selection": {
            "require-labels": ["needs-review"],
            "exclude-labels": ["blocked"],
            "priority": ["severity:critical"],
            "tiebreak": "newest",
        }
    }
    cfg = workspace._derive_lane_selection_cfg(yaml_cfg, active_lane_label="active-lane")
    assert cfg["require-labels"] == ["needs-review"]
    assert cfg["priority"] == ["severity:critical"]
    assert cfg["tiebreak"] == "newest"
    assert "active-lane" in cfg["exclude-labels"]
    assert "blocked" in cfg["exclude-labels"]
