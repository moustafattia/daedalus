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


def test_runtime_paths_use_project_runtime_subdirs_when_present(tmp_path):
    paths_module = load_module("daedalus_workflows_code_review_paths_test", "workflows/code_review/paths.py")
    workflow_root = tmp_path / "yoyopod_core"
    (workflow_root / "runtime").mkdir(parents=True)
    (workflow_root / "config").mkdir()
    (workflow_root / "workspace").mkdir()

    paths = paths_module.runtime_paths(workflow_root)

    assert paths["db_path"] == workflow_root / "runtime" / "state" / "daedalus" / "daedalus.db"
    assert paths["event_log_path"] == workflow_root / "runtime" / "memory" / "daedalus-events.jsonl"


def test_runtime_paths_fall_back_to_legacy_layout_without_project_runtime(tmp_path):
    paths_module = load_module("daedalus_workflows_code_review_paths_test", "workflows/code_review/paths.py")
    workflow_root = tmp_path / "workflow"

    paths = paths_module.runtime_paths(workflow_root)

    assert paths["db_path"] == workflow_root / "state" / "daedalus" / "daedalus.db"
    assert paths["event_log_path"] == workflow_root / "memory" / "daedalus-events.jsonl"


def test_resolve_default_workflow_root_prefers_repo_project_dir_when_no_env_or_legacy_root(tmp_path):
    paths_module = load_module("daedalus_workflows_code_review_paths_test", "workflows/code_review/paths.py")
    plugin_dir = tmp_path / "repo"
    repo_project_root = plugin_dir / "projects" / "yoyopod_core"
    repo_project_root.mkdir(parents=True)
    (repo_project_root / "runtime").mkdir()
    (repo_project_root / "config").mkdir()
    home = tmp_path / "home"
    home.mkdir()

    resolved = paths_module.resolve_default_workflow_root(plugin_dir=plugin_dir, env={}, home=home)

    assert resolved == repo_project_root.resolve()


def test_lane_state_and_memo_paths_resolve_under_worktree_and_handle_none(tmp_path):
    paths_module = load_module("daedalus_workflows_code_review_paths_test", "workflows/code_review/paths.py")
    worktree = tmp_path / "yoyopod-issue-224"

    assert paths_module.lane_state_path(worktree) == worktree / ".lane-state.json"
    assert paths_module.lane_memo_path(worktree) == worktree / ".lane-memo.md"
    assert paths_module.lane_state_path(None) is None
    assert paths_module.lane_memo_path(None) is None


def test_plugin_entrypoint_path_points_at_generic_dispatcher(tmp_path):
    paths_module = load_module("daedalus_workflows_code_review_paths_test", "workflows/code_review/paths.py")
    workflow_root = tmp_path / "workflow"
    expected = (
        workflow_root.resolve()
        / ".hermes"
        / "plugins"
        / "daedalus"
        / "workflows"
        / "__main__.py"
    )
    assert paths_module.plugin_entrypoint_path(workflow_root) == expected


def test_workflow_cli_argv_prefers_plugin_entrypoint_when_installed(tmp_path):
    paths_module = load_module("daedalus_workflows_code_review_paths_test", "workflows/code_review/paths.py")
    workflow_root = tmp_path / "workflow"
    plugin_main = paths_module.plugin_entrypoint_path(workflow_root)
    plugin_main.parent.mkdir(parents=True)
    plugin_main.write_text("# main\n", encoding="utf-8")

    import sys as _sys
    argv = paths_module.workflow_cli_argv(workflow_root, "status", "--json")
    assert argv == [_sys.executable, str(plugin_main), "status", "--json"]


def test_yoyopod_cli_argv_is_backcompat_alias(tmp_path):
    """yoyopod_cli_argv is a deprecated alias for workflow_cli_argv."""
    paths_module = load_module("daedalus_workflows_code_review_paths_test", "workflows/code_review/paths.py")
    assert paths_module.yoyopod_cli_argv is paths_module.workflow_cli_argv


def test_workflow_cli_argv_always_targets_generic_dispatcher(tmp_path):
    """The retired ``scripts/yoyopod_workflow.py`` wrapper is no longer a fallback.

    ``workflow_cli_argv`` should always build an argv targeting the plugin's
    generic dispatcher regardless of what happens to exist in the workflow root.
    """
    paths_module = load_module("daedalus_workflows_code_review_paths_test", "workflows/code_review/paths.py")
    workflow_root = tmp_path / "workflow"

    import sys as _sys
    argv = paths_module.workflow_cli_argv(workflow_root, "status", "--json")
    # argv[0] is sys.executable (absolute path to the calling interpreter,
    # not bare "python3") so subprocess inherits the right pyyaml/jsonschema
    # environment regardless of PATH ordering on the host.
    assert argv[0] == _sys.executable
    assert argv[1].endswith("/.hermes/plugins/daedalus/workflows/__main__.py")
    assert argv[2:] == ["status", "--json"]

    # Even if a retired-style wrapper script appears under scripts/, it is
    # ignored — we no longer probe for or fall back to it.
    wrapper = workflow_root / "scripts" / "yoyopod_workflow.py"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("# retired\n", encoding="utf-8")
    argv2 = paths_module.workflow_cli_argv(workflow_root, "tick")
    assert argv2[1].endswith("/.hermes/plugins/daedalus/workflows/__main__.py")
