"""--format text|json flag resolution and --json alias back-compat."""
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
    return load_module("daedalus_tools_format_flag_test", "daedalus_cli.py")


def test_resolve_format_default_is_text():
    tools = _tools()
    assert tools._resolve_format(None, None) == "text"


def test_resolve_format_explicit_text():
    tools = _tools()
    assert tools._resolve_format("text", False) == "text"


def test_resolve_format_explicit_json():
    tools = _tools()
    assert tools._resolve_format("json", False) == "json"


def test_resolve_format_legacy_json_flag():
    tools = _tools()
    assert tools._resolve_format(None, True) == "json"


def test_json_flag_wins_over_format_text():
    """Pre-existing scripts using --json shouldn't be silently downgraded."""
    tools = _tools()
    assert tools._resolve_format("text", True) == "json"


def test_format_json_wins_over_default_text():
    tools = _tools()
    assert tools._resolve_format("json", False) == "json"


def test_status_subparser_accepts_format_flag():
    tools = _tools()
    parser = tools.build_parser()
    args = parser.parse_args(["status", "--format", "json"])
    assert args.format == "json"


def test_status_subparser_accepts_legacy_json_flag():
    tools = _tools()
    parser = tools.build_parser()
    args = parser.parse_args(["status", "--json"])
    assert args.json is True


def test_status_subparser_accepts_no_format_flag_defaults_text():
    tools = _tools()
    parser = tools.build_parser()
    args = parser.parse_args(["status"])
    assert getattr(args, "format", "text") == "text"
    assert getattr(args, "json", False) is False


def test_doctor_subparser_accepts_format_flag():
    tools = _tools()
    parser = tools.build_parser()
    args = parser.parse_args(["doctor", "--format", "json"])
    assert args.format == "json"


def test_active_gate_status_subparser_accepts_format_flag():
    tools = _tools()
    parser = tools.build_parser()
    args = parser.parse_args(["active-gate-status", "--format", "text"])
    assert args.format == "text"


def test_invalid_format_value_rejected():
    tools = _tools()
    parser = tools.build_parser()
    try:
        parser.parse_args(["status", "--format", "yaml"])
    except SystemExit:
        return
    except tools.DaedalusCommandError:
        # Project's DaedalusArgumentParser raises DaedalusCommandError instead
        # of letting argparse call sys.exit. Either form means rejection succeeded.
        return
    raise AssertionError("expected rejection on invalid --format value")
