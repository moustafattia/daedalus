"""Tests for workflows.change_delivery.preflight.run_preflight().

Symphony §6.3: pure dispatch preflight. No I/O beyond inspecting the
config dict (and env for $VAR resolution). Fixed error-code enum.
"""
from __future__ import annotations

import os
from unittest import mock

import pytest

from workflows.change_delivery.preflight import PreflightResult, run_preflight


def _minimal_ok_config() -> dict:
    """Minimal config matching the actual change-delivery schema field paths.

    Codex P2 on PR #21 fix: the preflight reads ``runtimes.<name>.kind``
    and ``agents.external-reviewer.kind`` (the real schema layout), not
    legacy top-level ``runtime`` / ``external-reviewer`` keys.
    """
    return {
        "workflow": "change-delivery",
        "schema-version": 1,
        "runtimes": {"r1": {"kind": "claude-cli"}},
        "agents": {"external-reviewer": {"kind": "github-comments"}},
        "tracker": {"kind": "github"},
        "code-host": {"kind": "github"},
        "repository": {"github-token": "literal-token"},
    }


def test_happy_path_returns_ok():
    result = run_preflight(_minimal_ok_config())
    assert result.ok is True
    assert result.error_code is None
    assert result.error_detail is None
    assert result.can_reconcile is True


def test_codex_app_server_runtime_kind_returns_ok():
    cfg = _minimal_ok_config()
    cfg["runtimes"]["r1"]["kind"] = "codex-app-server"

    result = run_preflight(cfg)

    assert result.ok is True


def test_non_dict_config_yields_front_matter_error():
    result = run_preflight("not-a-dict")  # type: ignore[arg-type]
    assert result.ok is False
    assert result.error_code == "workflow_front_matter_not_a_map"
    assert "str" in (result.error_detail or "")
    assert result.can_reconcile is True


def test_unknown_runtime_kind():
    cfg = _minimal_ok_config()
    cfg["runtimes"]["r1"]["kind"] = "totally-bogus"
    result = run_preflight(cfg)
    assert result.ok is False
    assert result.error_code == "unsupported_runtime_kind"
    assert "totally-bogus" in (result.error_detail or "")
    assert result.can_reconcile is True


def test_unknown_reviewer_kind():
    cfg = _minimal_ok_config()
    cfg["agents"]["external-reviewer"]["kind"] = "carrier-pigeon"
    result = run_preflight(cfg)
    assert result.ok is False
    assert result.error_code == "unsupported_reviewer_kind"


def test_unknown_tracker_kind():
    cfg = _minimal_ok_config()
    cfg["tracker"]["kind"] = "jira"
    result = run_preflight(cfg)
    assert result.ok is False
    assert result.error_code == "unsupported_tracker_kind"


def test_unknown_code_host_kind():
    cfg = _minimal_ok_config()
    cfg["code-host"]["kind"] = "gitlab"
    result = run_preflight(cfg)
    assert result.ok is False
    assert result.error_code == "unsupported_code_host_kind"


def test_var_token_unset_env_yields_missing_credentials():
    cfg = _minimal_ok_config()
    cfg["repository"]["github-token"] = "$DAEDALUS_TEST_UNSET_TOKEN"
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("DAEDALUS_TEST_UNSET_TOKEN", None)
        result = run_preflight(cfg)
    assert result.ok is False
    assert result.error_code == "missing_tracker_credentials"
    assert "DAEDALUS_TEST_UNSET_TOKEN" in (result.error_detail or "")


def test_var_token_set_env_resolves_ok():
    cfg = _minimal_ok_config()
    cfg["repository"]["github-token"] = "$DAEDALUS_TEST_SET_TOKEN"
    with mock.patch.dict(os.environ, {"DAEDALUS_TEST_SET_TOKEN": "ghp_xxx"}):
        result = run_preflight(cfg)
    assert result.ok is True


def test_absent_optional_sections_ok():
    cfg = {
        "workflow": "change-delivery",
        "schema-version": 1,
    }
    result = run_preflight(cfg)
    assert result.ok is True


def test_can_reconcile_true_on_failure():
    cfg = _minimal_ok_config()
    cfg["runtimes"]["r1"]["kind"] = "broken"
    result = run_preflight(cfg)
    assert result.ok is False
    assert result.can_reconcile is True


def test_preflight_result_is_frozen_dataclass():
    r = PreflightResult(True, None, None)
    with pytest.raises(Exception):
        r.ok = False  # type: ignore[misc]
