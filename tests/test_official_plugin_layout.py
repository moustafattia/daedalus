import importlib.util
import subprocess
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_repo_root_exposes_official_hermes_plugin_layout():
    expected = [
        REPO_ROOT / "plugin.yaml",
        REPO_ROOT / "__init__.py",
        REPO_ROOT / "runtimes" / "__init__.py",
        REPO_ROOT / "schemas.py",
        REPO_ROOT / "daedalus_cli.py",
        REPO_ROOT / "trackers" / "__init__.py",
        REPO_ROOT / "runtime.py",
        REPO_ROOT / "workflows" / "__init__.py",
        REPO_ROOT / "workflows" / "__main__.py",
        REPO_ROOT / "workflows" / "change_delivery" / "__init__.py",
        REPO_ROOT / "workflows" / "change_delivery" / "__main__.py",
        REPO_ROOT / "workflows" / "issue_runner" / "__init__.py",
        REPO_ROOT / "workflows" / "issue_runner" / "__main__.py",
    ]
    missing = [str(path.relative_to(REPO_ROOT)) for path in expected if not path.exists()]
    assert not missing, f"missing repo-root plugin files: {missing}"
    assert not (REPO_ROOT / "tools.py").exists()


def test_repo_root_manifest_matches_installed_payload_manifest():
    repo_manifest = yaml.safe_load((REPO_ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    payload_manifest = yaml.safe_load((REPO_ROOT / "daedalus" / "plugin.yaml").read_text(encoding="utf-8"))
    assert repo_manifest == payload_manifest


def test_repo_root_plugin_entrypoint_registers_same_commands_and_skill():
    plugin = _load_module("daedalus_repo_root_plugin_test", REPO_ROOT / "__init__.py")

    calls = {
        "commands": [],
        "cli_commands": [],
        "skills": [],
    }

    class FakeCtx:
        def register_command(self, name, handler, description=""):
            calls["commands"].append((name, description, handler))

        def register_cli_command(self, **kwargs):
            calls["cli_commands"].append(kwargs)

        def register_skill(self, name, path, description=""):
            calls["skills"].append((name, Path(path), description))

    plugin.register(FakeCtx())

    command_names = {name for name, _desc, _handler in calls["commands"]}
    assert {"daedalus", "workflow"} <= command_names
    assert any(item["name"] == "daedalus" for item in calls["cli_commands"])
    assert any(name == "operator" for name, _path, _desc in calls["skills"])


def test_repo_root_tools_wrapper_dispatches_scaffold(tmp_path):
    tools = _load_module("daedalus_repo_root_tools_test", REPO_ROOT / "daedalus_cli.py")
    workflow_root = tmp_path / "attmous-daedalus-issue-runner"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:attmous/daedalus.git"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    out = tools.execute_raw_args(
        f"scaffold-workflow --workflow-root {workflow_root} --repo-path {repo} --repo-slug attmous/daedalus"
    )

    assert "daedalus error:" not in out, out
    assert (repo / "WORKFLOW.md").exists()


def test_repo_root_workflows_wrapper_exposes_change_delivery_submodules():
    for module_name in list(sys.modules):
        if module_name == "workflows" or module_name.startswith("workflows."):
            del sys.modules[module_name]

    import importlib

    runtimes = importlib.import_module("workflows.change_delivery.runtimes")

    assert runtimes.__file__ is not None
    assert "daedalus/workflows/change_delivery/runtimes" in runtimes.__file__


def test_repo_root_workflows_wrapper_exposes_issue_runner_submodules():
    for module_name in list(sys.modules):
        if module_name == "workflows" or module_name.startswith("workflows."):
            del sys.modules[module_name]

    import importlib

    tracker = importlib.import_module("workflows.issue_runner.tracker")

    assert tracker.__file__ is not None
    assert "daedalus/workflows/issue_runner/tracker" in tracker.__file__


def test_repo_root_runtimes_wrapper_exposes_shared_runtime_modules():
    for module_name in list(sys.modules):
        if module_name == "runtimes" or module_name.startswith("runtimes."):
            del sys.modules[module_name]

    import importlib

    runtimes_pkg = importlib.import_module("runtimes")
    codex = importlib.import_module("runtimes.codex_app_server")

    assert runtimes_pkg.__file__ is not None
    assert codex.__file__ is not None
    assert "daedalus/runtimes/codex_app_server" in codex.__file__


def test_repo_root_trackers_wrapper_exposes_shared_tracker_modules():
    for module_name in list(sys.modules):
        if module_name == "trackers" or module_name.startswith("trackers."):
            del sys.modules[module_name]

    import importlib

    trackers_pkg = importlib.import_module("trackers")
    linear = importlib.import_module("trackers.linear")

    assert trackers_pkg.__file__ is not None
    assert linear.__file__ is not None
    assert "daedalus/trackers/linear" in linear.__file__
