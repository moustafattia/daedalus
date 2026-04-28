"""Regression: the new /daedalus subcommands actually run via execute_raw_args.

Codex Cloud caught (P1) that ``watch`` / ``set-observability`` / ``get-observability``
were registered with ``handler=...`` but the central dispatcher
(``execute_raw_args`` → ``execute_namespace``) only knew about the legacy
dict-returning commands. Without an explicit branch in ``execute_raw_args``
the new commands fall through to ``unknown daedalus command``.

These tests pin the dispatch routing so a future refactor can't silently
re-break it.
"""
import importlib.util
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
    return load_module("daedalus_tools_new_command_dispatch_test", "tools.py")


def test_set_observability_dispatched_not_falling_through_to_unknown(tmp_path):
    """``/daedalus set-observability ...`` should reach cmd_set_observability,
    not error out as ``unknown daedalus command``."""
    tools = _tools()
    # Use a tmp workflow root so we don't write to the live workspace.
    raw = (
        f"set-observability "
        f"--workflow-root {tmp_path} "
        f"--workflow code-review "
        f"--github-comments unset"
    )
    out = tools.execute_raw_args(raw)
    assert "unknown daedalus command" not in out, out
    # Either a normal output ("removed for code-review") or — if the override
    # file doesn't exist — still a clean message, never the "unknown" string.
    assert "code-review" in out or "removed" in out.lower() or "set" in out.lower()


def test_get_observability_dispatched_not_falling_through_to_unknown(tmp_path):
    tools = _tools()
    raw = (
        f"get-observability "
        f"--workflow-root {tmp_path} "
        f"--workflow code-review"
    )
    out = tools.execute_raw_args(raw)
    assert "unknown daedalus command" not in out, out
    # A real config-resolution result mentions the workflow + an enabled state.
    assert "workflow" in out.lower() or "github-comments" in out.lower()


def test_watch_dispatched_not_falling_through_to_unknown(tmp_path):
    """``/daedalus watch --once`` should reach cmd_watch (one-shot render)."""
    # Build a workflow root the watch sources can read.
    root = tmp_path / "yoyopod_core"
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
