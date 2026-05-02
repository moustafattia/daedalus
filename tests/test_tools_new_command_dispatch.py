"""Regression: string-returning /daedalus subcommands run via execute_raw_args."""
import importlib.util
import subprocess
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _tools():
    return load_module("daedalus_tools_new_command_dispatch_test", "daedalus_cli.py")


def test_watch_dispatched_not_falling_through_to_unknown(tmp_path):
    """``/daedalus watch --once`` should reach cmd_watch (one-shot render)."""
    # Build a workflow root the watch sources can read.
    root = tmp_path / "workflow_example"
    (root / "runtime" / "memory").mkdir(parents=True)
    (root / "runtime" / "state" / "daedalus").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "workspace").mkdir()

    tools = _tools()
    raw = f"watch --once --workflow-root {root}"
    out = tools.execute_raw_args(raw)
    assert "unknown daedalus command" not in out, out
    # The watch panel renders a recognizable header even with no data.
    assert "Daedalus active lanes" in out or "active lanes" in out.lower()


def test_scaffold_workflow_dispatched_not_falling_through_to_unknown(tmp_path):
    tools = _tools()
    root = tmp_path / "attmous-daedalus-issue-runner"
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
        f"scaffold-workflow --workflow-root {root} --repo-path {repo} --repo-slug attmous/daedalus"
    )
    assert "unknown daedalus command" not in out, out
    assert "scaffolded workflow root" in out
    assert (repo / "WORKFLOW.md").exists()


def test_bootstrap_dispatched_not_falling_through_to_unknown(tmp_path, monkeypatch):
    tools = _tools()
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
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    out = tools.execute_raw_args(f"bootstrap --repo-path {repo}")
    assert "unknown daedalus command" not in out, out
    assert "bootstrapped workflow root" in out
    assert (repo / "WORKFLOW.md").exists()
