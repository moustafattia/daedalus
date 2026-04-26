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
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


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
            "code-review",
            "--github-comments",
            "unset",
        ],
    )
    tools.run_cli_command(args)
    out = capsys.readouterr().out
    assert "unknown daedalus command" not in out, out
    assert "code-review" in out


def test_run_cli_command_dispatches_get_observability(tmp_path, capsys):
    tools = _tools()
    args = _parse(
        tools,
        [
            "get-observability",
            "--workflow-root",
            str(tmp_path),
            "--workflow",
            "code-review",
        ],
    )
    tools.run_cli_command(args)
    out = capsys.readouterr().out
    assert "unknown daedalus command" not in out, out
    assert "code-review" in out or "github-comments" in out.lower()


def test_run_cli_command_dispatches_watch(tmp_path, capsys):
    """Drive ``watch --once`` via run_cli_command (the argparse CLI path)."""
    root = tmp_path / "yoyopod_core"
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
