import importlib.util
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_workflow_yaml(workflow_root: Path, *, instance_name: str = "blueprint-engine") -> None:
    config_dir = workflow_root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "workflow": "change-delivery",
                "schema-version": 1,
                "instance": {"name": instance_name, "engine-owner": "hermes"},
                "repository": {
                    "local-path": str(workflow_root / "repo"),
                    "github-slug": "owner/repo",
                    "active-lane-label": "active-lane",
                },
                "runtimes": {"acpx-codex": {"kind": "acpx-codex"}},
                "agents": {
                    "coder": {"default": {"name": "Internal_Coder_Agent", "model": "gpt-5.3-codex", "runtime": "acpx-codex"}},
                    "internal-reviewer": {"name": "Internal_Reviewer_Agent", "model": "claude-sonnet-4-6", "runtime": "acpx-codex"},
                    "external-reviewer": {"enabled": True, "name": "External_Reviewer_Agent"},
                },
                "gates": {"internal-review": {}, "external-review": {}, "merge": {}},
                "triggers": {"lane-selector": {"type": "github-label", "label": "active-lane"}},
                "storage": {
                    "ledger": "memory/workflow-status.json",
                    "health": "memory/workflow-health.json",
                    "audit-log": "memory/workflow-audit.jsonl",
                },
            }
        ),
        encoding="utf-8",
    )


def _write_workflow_markdown(workflow_root: Path, *, instance_name: str = "blueprint-engine") -> None:
    config = {
        "workflow": "change-delivery",
        "schema-version": 1,
        "instance": {"name": instance_name, "engine-owner": "hermes"},
        "repository": {
            "local-path": str(workflow_root / "repo"),
            "github-slug": "owner/repo",
            "active-lane-label": "active-lane",
        },
        "runtimes": {"acpx-codex": {"kind": "acpx-codex"}},
        "agents": {
            "coder": {"default": {"name": "Internal_Coder_Agent", "model": "gpt-5.3-codex", "runtime": "acpx-codex"}},
            "internal-reviewer": {"name": "Internal_Reviewer_Agent", "model": "claude-sonnet-4-6", "runtime": "acpx-codex"},
            "external-reviewer": {"enabled": True, "name": "External_Reviewer_Agent"},
        },
        "gates": {"internal-review": {}, "external-review": {}, "merge": {}},
        "triggers": {"lane-selector": {"type": "github-label", "label": "active-lane"}},
        "storage": {
            "ledger": "memory/workflow-status.json",
            "health": "memory/workflow-health.json",
            "audit-log": "memory/workflow-audit.jsonl",
        },
    }
    workflow_root.mkdir(parents=True, exist_ok=True)
    (workflow_root / "WORKFLOW.md").write_text(
        "---\n" + yaml.safe_dump(config, sort_keys=False) + "---\n\nPrompt body\n",
        encoding="utf-8",
    )


def test_runtime_paths_use_project_runtime_subdirs_when_present(tmp_path):
    paths_module = load_module("daedalus_workflows_change_delivery_paths_test", "workflows/change_delivery/paths.py")
    workflow_root = tmp_path / "blueprint"
    (workflow_root / "runtime").mkdir(parents=True)
    (workflow_root / "config").mkdir()
    (workflow_root / "workspace").mkdir()

    paths = paths_module.runtime_paths(workflow_root)

    assert paths["db_path"] == workflow_root / "runtime" / "state" / "daedalus" / "daedalus.db"
    assert paths["event_log_path"] == workflow_root / "runtime" / "memory" / "daedalus-events.jsonl"


def test_derive_workflow_instance_name_uses_owner_repo_workflow_convention(tmp_path):
    del tmp_path
    paths_module = load_module("daedalus_workflows_change_delivery_paths_test", "workflows/change_delivery/paths.py")

    name = paths_module.derive_workflow_instance_name(
        repo_slug="Attmous/Daedalus_Core",
        workflow_name="change-delivery",
    )

    assert name == "attmous-daedalus-core-change-delivery"


def test_runtime_paths_fall_back_to_legacy_layout_without_project_runtime(tmp_path):
    paths_module = load_module("daedalus_workflows_change_delivery_paths_test", "workflows/change_delivery/paths.py")
    workflow_root = tmp_path / "workflow"

    paths = paths_module.runtime_paths(workflow_root)

    assert paths["db_path"] == workflow_root / "state" / "daedalus" / "daedalus.db"
    assert paths["event_log_path"] == workflow_root / "memory" / "daedalus-events.jsonl"


def test_resolve_default_workflow_root_prefers_env_var(tmp_path):
    paths_module = load_module("daedalus_workflows_change_delivery_paths_test", "workflows/change_delivery/paths.py")
    workflow_root = tmp_path / "workflow-root"

    resolved = paths_module.resolve_default_workflow_root(
        plugin_dir=tmp_path / "plugin" / "daedalus",
        env={"DAEDALUS_WORKFLOW_ROOT": str(workflow_root)},
    )

    assert resolved == workflow_root.resolve()


def test_resolve_default_workflow_root_detects_cwd_ancestor_with_config(tmp_path):
    paths_module = load_module("daedalus_workflows_change_delivery_paths_test", "workflows/change_delivery/paths.py")
    workflow_root = tmp_path / "workflow-root"
    nested = workflow_root / "workspace" / "repo" / "src"
    _write_workflow_yaml(workflow_root)
    nested.mkdir(parents=True)

    resolved = paths_module.resolve_default_workflow_root(
        plugin_dir=tmp_path / "plugin" / "daedalus",
        env={},
        cwd=nested,
    )

    assert resolved == workflow_root.resolve()


def test_resolve_default_workflow_root_detects_cwd_ancestor_with_workflow_markdown(tmp_path):
    paths_module = load_module("daedalus_workflows_change_delivery_paths_test", "workflows/change_delivery/paths.py")
    workflow_root = tmp_path / "workflow-root"
    nested = workflow_root / "workspace" / "repo" / "src"
    _write_workflow_markdown(workflow_root)
    (workflow_root / "runtime").mkdir(parents=True, exist_ok=True)
    (workflow_root / "memory").mkdir(parents=True, exist_ok=True)
    (workflow_root / "state").mkdir(parents=True, exist_ok=True)
    nested.mkdir(parents=True)

    resolved = paths_module.resolve_default_workflow_root(
        plugin_dir=tmp_path / "plugin" / "daedalus",
        env={},
        cwd=nested,
    )

    assert resolved == workflow_root.resolve()


def test_resolve_default_workflow_root_follows_repo_local_pointer(tmp_path):
    paths_module = load_module("daedalus_workflows_change_delivery_paths_test", "workflows/change_delivery/paths.py")
    workflow_root = tmp_path / ".hermes" / "workflows" / "owner-repo-change-delivery"
    _write_workflow_markdown(workflow_root)
    (workflow_root / "runtime").mkdir(parents=True, exist_ok=True)
    (workflow_root / "memory").mkdir(parents=True, exist_ok=True)
    (workflow_root / "state").mkdir(parents=True, exist_ok=True)

    repo_root = tmp_path / "repo"
    nested = repo_root / "src" / "pkg"
    nested.mkdir(parents=True)
    pointer_path = repo_root / ".hermes" / "daedalus" / "workflow-root"
    pointer_path.parent.mkdir(parents=True)
    pointer_path.write_text(str(workflow_root.resolve()) + "\n", encoding="utf-8")

    resolved = paths_module.resolve_default_workflow_root(
        plugin_dir=tmp_path / "plugin" / "daedalus",
        env={},
        cwd=nested,
    )

    assert resolved == workflow_root.resolve()


def test_resolve_default_workflow_root_falls_back_to_cwd_when_no_config(tmp_path):
    paths_module = load_module("daedalus_workflows_change_delivery_paths_test", "workflows/change_delivery/paths.py")
    cwd = tmp_path / "scratch"
    cwd.mkdir()

    resolved = paths_module.resolve_default_workflow_root(
        plugin_dir=tmp_path / "plugin" / "daedalus",
        env={},
        cwd=cwd,
    )

    assert resolved == cwd.resolve()


def test_lane_state_and_memo_paths_resolve_under_worktree_and_handle_none(tmp_path):
    paths_module = load_module("daedalus_workflows_change_delivery_paths_test", "workflows/change_delivery/paths.py")
    worktree = tmp_path / "issue-224"

    assert paths_module.lane_state_path(worktree) == worktree / ".lane-state.json"
    assert paths_module.lane_memo_path(worktree) == worktree / ".lane-memo.md"
    assert paths_module.lane_state_path(None) is None
    assert paths_module.lane_memo_path(None) is None


def test_plugin_entrypoint_path_points_at_generic_dispatcher(tmp_path):
    paths_module = load_module("daedalus_workflows_change_delivery_paths_test", "workflows/change_delivery/paths.py")
    expected = Path(paths_module.__file__).resolve().parents[2] / "workflows" / "__main__.py"
    assert paths_module.plugin_entrypoint_path(tmp_path / "workflow") == expected


def test_workflow_cli_argv_uses_global_plugin_entrypoint(tmp_path):
    paths_module = load_module("daedalus_workflows_change_delivery_paths_test", "workflows/change_delivery/paths.py")
    workflow_root = tmp_path / "workflow"

    import sys as _sys
    argv = paths_module.workflow_cli_argv(workflow_root, "status", "--json")
    assert argv == [_sys.executable, str(paths_module.plugin_entrypoint_path(workflow_root)), "status", "--json"]


def test_project_key_for_workflow_root_reads_instance_name_from_workflow_yaml(tmp_path):
    paths_module = load_module("daedalus_workflows_change_delivery_paths_test", "workflows/change_delivery/paths.py")
    workflow_root = tmp_path / "workflow"
    _write_workflow_yaml(workflow_root, instance_name="Blueprint Engine")
    assert paths_module.project_key_for_workflow_root(workflow_root) == "blueprint-engine"


def test_project_key_for_workflow_root_reads_instance_name_from_workflow_markdown(tmp_path):
    paths_module = load_module("daedalus_workflows_change_delivery_paths_test", "workflows/change_delivery/paths.py")
    workflow_root = tmp_path / "workflow"
    _write_workflow_markdown(workflow_root, instance_name="Blueprint Engine")
    assert paths_module.project_key_for_workflow_root(workflow_root) == "blueprint-engine"


def test_workflow_cli_argv_always_targets_generic_dispatcher(tmp_path):
    """A local scripts wrapper is no longer a fallback.

    ``workflow_cli_argv`` should always build an argv targeting the plugin's
    generic dispatcher regardless of what happens to exist in the workflow root.
    """
    paths_module = load_module("daedalus_workflows_change_delivery_paths_test", "workflows/change_delivery/paths.py")
    workflow_root = tmp_path / "workflow"

    import sys as _sys
    argv = paths_module.workflow_cli_argv(workflow_root, "status", "--json")
    # argv[0] is sys.executable (absolute path to the calling interpreter,
    # not bare "python3") so subprocess inherits the right pyyaml/jsonschema
    # environment regardless of PATH ordering on the host.
    assert argv[0] == _sys.executable
    assert argv[1].endswith("/workflows/__main__.py")
    assert argv[2:] == ["status", "--json"]

    # Even if a retired-style wrapper script appears under scripts/, it is
    # ignored — we no longer probe for or fall back to it.
    wrapper = workflow_root / "scripts" / "workflow_wrapper.py"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("# retired\n", encoding="utf-8")
    argv2 = paths_module.workflow_cli_argv(workflow_root, "tick")
    assert argv2[1].endswith("/workflows/__main__.py")
