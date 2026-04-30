"""Regression: the new /daedalus subcommands must also dispatch correctly via
the argparse ``func=run_cli_command`` path.

Codex Cloud follow-up to a3ea328: the previous fix only routed
``watch`` / ``set-observability`` / ``get-observability`` through
``execute_raw_args`` (the slash-command path). The argparse CLI path
(``python3 tools.py <cmd> ...`` and any ``setup_cli``-registered command)
still calls ``run_cli_command`` which previously hard-coded
``execute_namespace`` -- raising ``unknown daedalus command`` for the new
string-returning subcommands.

These tests pin ``run_cli_command`` so it dispatches the string-returning
handlers directly.
"""
import importlib.util
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _tools():
    return load_module("daedalus_tools_run_cli_command_dispatch_test", "tools.py")


def _parse(tools, argv):
    """Use the real parser so ``args`` carries the right ``handler`` default."""
    parser = tools.build_parser()
    return parser.parse_args(argv)


def test_run_cli_command_dispatches_set_observability(tmp_path, capsys):
    tools = _tools()
    args = _parse(
        tools,
        [
            "set-observability",
            "--workflow-root",
            str(tmp_path),
            "--workflow",
            "change-delivery",
            "--github-comments",
            "unset",
        ],
    )
    tools.run_cli_command(args)
    out = capsys.readouterr().out
    assert "unknown daedalus command" not in out, out
    assert "change-delivery" in out


def test_run_cli_command_dispatches_get_observability(tmp_path, capsys):
    tools = _tools()
    args = _parse(
        tools,
        [
            "get-observability",
            "--workflow-root",
            str(tmp_path),
            "--workflow",
            "change-delivery",
        ],
    )
    tools.run_cli_command(args)
    out = capsys.readouterr().out
    assert "unknown daedalus command" not in out, out
    assert "change-delivery" in out or "github-comments" in out.lower()


def test_run_cli_command_dispatches_watch(tmp_path, capsys):
    """Drive ``watch --once`` via run_cli_command (the argparse CLI path)."""
    root = tmp_path / "workflow_example"
    (root / "runtime" / "memory").mkdir(parents=True)
    (root / "runtime" / "state" / "daedalus").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "workspace").mkdir()

    tools = _tools()
    args = _parse(
        tools,
        ["watch", "--once", "--workflow-root", str(root)],
    )
    tools.run_cli_command(args)
    out = capsys.readouterr().out
    assert "unknown daedalus command" not in out, out
    assert "Daedalus active lanes" in out or "active lanes" in out.lower()


def test_run_cli_command_dispatches_scaffold_workflow(tmp_path, capsys):
    tools = _tools()
    root = tmp_path / "attmous-daedalus-change-delivery"
    args = _parse(
        tools,
        [
            "scaffold-workflow",
            "--workflow-root",
            str(root),
            "--github-slug",
            "attmous/daedalus",
        ],
    )
    tools.run_cli_command(args)
    out = capsys.readouterr().out
    assert "unknown daedalus command" not in out, out
    assert "scaffolded workflow root" in out
    assert (root / "WORKFLOW.md").exists()


def test_run_cli_command_dispatches_bootstrap(tmp_path, capsys, monkeypatch):
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

    tools = _tools()
    args = _parse(
        tools,
        [
            "bootstrap",
            "--repo-path",
            str(repo),
        ],
    )
    tools.run_cli_command(args)
    out = capsys.readouterr().out
    assert "unknown daedalus command" not in out, out
    assert "bootstrapped workflow root" in out
    assert (home / ".hermes" / "workflows" / "attmous-daedalus-change-delivery" / "WORKFLOW.md").exists()
