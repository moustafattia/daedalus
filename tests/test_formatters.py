"""Panel renderer + color helpers."""
import importlib.util
import os
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


def _module():
    return load_module("daedalus_formatters_test", "formatters.py")


# ─── Color gating ────────────────────────────────────────────────

def test_use_color_false_when_not_a_tty():
    fmt = _module()
    with mock.patch("sys.stdout") as fake:
        fake.isatty.return_value = False
        assert fmt._use_color() is False


def test_use_color_false_when_NO_COLOR_env_set():
    fmt = _module()
    with mock.patch.dict(os.environ, {"NO_COLOR": "1"}, clear=False):
        with mock.patch("sys.stdout") as fake:
            fake.isatty.return_value = True
            assert fmt._use_color() is False


def test_use_color_true_when_tty_and_no_NO_COLOR():
    fmt = _module()
    env = {k: v for k, v in os.environ.items() if k != "NO_COLOR"}
    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch("sys.stdout") as fake:
            fake.isatty.return_value = True
            assert fmt._use_color() is True


def test_color_wrapper_returns_input_when_disabled():
    fmt = _module()
    assert fmt._color("hello", "green", use_color=False) == "hello"


def test_color_wrapper_wraps_with_ansi_when_enabled():
    fmt = _module()
    out = fmt._color("hello", "green", use_color=True)
    assert "\033[" in out
    assert out.endswith("\033[0m")
    assert "hello" in out


# ─── Panel rendering ────────────────────────────────────────────────

def test_panel_with_single_section_no_section_header():
    fmt = _module()
    out = fmt.format_panel(
        title="Daedalus runtime — yoyopod",
        sections=[
            fmt.Section(name=None, rows=[
                fmt.Row(label="state", value="running"),
                fmt.Row(label="owner", value="daedalus-active"),
            ]),
        ],
        use_color=False,
    )
    assert "Daedalus runtime — yoyopod" in out
    assert "state" in out
    assert "running" in out
    assert "owner" in out
    # No section header line when name is None
    lines = out.split("\n")
    assert all("None" not in line for line in lines)


def test_panel_with_named_sections():
    fmt = _module()
    out = fmt.format_panel(
        title="Active execution gate",
        sections=[
            fmt.Section(name="checks", rows=[
                fmt.Row(label="ownership", value="desired_owner = daedalus", status="pass"),
                fmt.Row(label="active execution", value="DISABLED", status="fail"),
            ]),
        ],
        use_color=False,
    )
    assert "checks" in out
    assert "ownership" in out
    assert "DISABLED" in out
    # Pass/fail glyphs present (ASCII forms acceptable here too)
    assert "✓" in out or "+" in out
    assert "✗" in out or "x" in out


def test_panel_aligns_labels_within_section():
    fmt = _module()
    out = fmt.format_panel(
        title="t",
        sections=[
            fmt.Section(name=None, rows=[
                fmt.Row(label="a", value="1"),
                fmt.Row(label="longer-label", value="2"),
            ]),
        ],
        use_color=False,
    )
    # Each row's value position should align — easy check: locate '1' and '2' column.
    lines = [l for l in out.split("\n") if "1" in l or "2" in l]
    # Both rows should have the same number of leading characters before the value
    pos1 = lines[0].index("1")
    pos2 = lines[1].index("2")
    assert pos1 == pos2


def test_panel_empty_value_renders_as_em_dash():
    fmt = _module()
    out = fmt.format_panel(
        title="t",
        sections=[fmt.Section(name=None, rows=[fmt.Row(label="reasons", value="")])],
        use_color=False,
    )
    assert "—" in out


def test_panel_footer_appears_when_provided():
    fmt = _module()
    out = fmt.format_panel(
        title="t",
        sections=[fmt.Section(name=None, rows=[fmt.Row(label="x", value="y")])],
        footer="→ gate is open",
        use_color=False,
    )
    assert "→ gate is open" in out


def test_panel_footer_omitted_when_none():
    fmt = _module()
    out = fmt.format_panel(
        title="t",
        sections=[fmt.Section(name=None, rows=[fmt.Row(label="x", value="y")])],
        footer=None,
        use_color=False,
    )
    assert "→" not in out  # no footer arrow


def test_panel_row_with_detail_appends_after_value():
    fmt = _module()
    out = fmt.format_panel(
        title="t",
        sections=[fmt.Section(name=None, rows=[fmt.Row(label="x", value="y", detail="(extra info)")])],
        use_color=False,
    )
    assert "y" in out
    assert "(extra info)" in out
    # detail should appear AFTER value on the same line
    line = next(l for l in out.split("\n") if "y" in l and "(extra" in l)
    assert line.index("y") < line.index("(extra")


# ─── No raw True/False leakage ────────────────────────────────────────

def test_render_bool_helper_returns_human_strings():
    fmt = _module()
    assert fmt.render_bool(True) in {"yes", "enabled", "✓"}
    # Helper used by per-command formatters; test it directly.
    assert "True" not in fmt.render_bool(True)
    assert "False" not in fmt.render_bool(False)


# ─── Path + timestamp helpers ────────────────────────────────────────

def test_format_path_collapses_home_to_tilde():
    fmt = _module()
    home = os.environ.get("HOME") or str(Path.home())
    p = f"{home}/some/sub/dir"
    assert fmt.format_path(p).startswith("~/")


def test_format_path_returns_unmodified_when_outside_home():
    fmt = _module()
    assert fmt.format_path("/var/log/daedalus.log") == "/var/log/daedalus.log"


def test_format_timestamp_with_relative_age():
    fmt = _module()
    out = fmt.format_timestamp("2026-04-26T22:43:01Z", now_iso="2026-04-26T22:43:18Z")
    assert "22:43:01" in out
    assert "17s ago" in out or "17 sec" in out


def test_format_timestamp_handles_empty():
    fmt = _module()
    assert fmt.format_timestamp("", now_iso="2026-04-26T22:43:01Z") == "—"
