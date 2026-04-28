"""watch CLI handler in non-TTY mode renders one frame and exits."""
import importlib.util
import io
import sys
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


def test_cmd_watch_one_shot_when_not_tty(tmp_path, capsys):
    watch = load_module("daedalus_watch_cli_test", "watch.py")
    root = tmp_path / "yoyopod_core"
    (root / "runtime" / "memory").mkdir(parents=True)
    (root / "runtime" / "state" / "daedalus").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "workspace").mkdir()

    args = mock.Mock()
    args.workflow_root = root
    args.once = False  # don't force one-shot via flag

    # Force is_tty to False
    with mock.patch.object(watch, "_stdout_is_tty", return_value=False):
        result = watch.cmd_watch(args, parser=None)

    assert "Daedalus active lanes" in result
    # No live loop entered (would block test)


def test_cmd_watch_with_once_flag_renders_one_frame(tmp_path):
    watch = load_module("daedalus_watch_cli_test", "watch.py")
    root = tmp_path / "yoyopod_core"
    (root / "runtime" / "memory").mkdir(parents=True)
    (root / "runtime" / "state" / "daedalus").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "workspace").mkdir()

    args = mock.Mock()
    args.workflow_root = root
    args.once = True

    # Even with TTY, --once should bypass live loop
    with mock.patch.object(watch, "_stdout_is_tty", return_value=True):
        result = watch.cmd_watch(args, parser=None)

    assert "Daedalus active lanes" in result
