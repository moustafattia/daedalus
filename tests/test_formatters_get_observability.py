import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name, relative_path):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fmt():
    return load_module("daedalus_formatters_get_obs_test", "formatters.py")


def _example():
    return {
        "workflow": "code-review",
        "github_comments": {
            "enabled": True,
            "mode": "edit-in-place",
            "include_events": ["dispatch-implementation-turn", "merge-and-promote"],
        },
        "source": "yaml",
    }


def test_panel_includes_workflow_and_enabled():
    fmt = _fmt()
    out = fmt.format_get_observability(_example(), use_color=False)
    assert "code-review" in out
    assert "yaml" in out
    # enabled rendered as 'yes' not True
    assert " True" not in out


def test_firehose_warning_when_include_events_empty():
    fmt = _fmt()
    rec = _example()
    rec["github_comments"]["include_events"] = []
    out = fmt.format_get_observability(rec, use_color=False)
    assert "FIREHOSE" in out


def test_disabled_state_renders_with_fail_glyph():
    fmt = _fmt()
    rec = _example()
    rec["github_comments"]["enabled"] = False
    out = fmt.format_get_observability(rec, use_color=False)
    assert "no" in out.lower()  # rendered as 'no'
