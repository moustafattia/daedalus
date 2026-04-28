"""S-2 tests: ConfigWatcher (mtime-poll hot-reload) — Symphony §6.2."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest


_VALID_YAML = textwrap.dedent("""\
    workflow: code-review
    schema-version: 1
    instance:
      name: test-instance
      engine-owner: hermes
    repository:
      local-path: /tmp/test
      github-slug: org/repo
      active-lane-label: active-lane
    runtimes:
      r1:
        kind: claude-cli
        max-turns-per-invocation: 4
        timeout-seconds: 60
    agents:
      coder:
        t1:
          name: coder
          model: claude
          runtime: r1
      internal-reviewer:
        name: internal
        model: claude
        runtime: r1
      external-reviewer:
        enabled: false
        name: external
    gates:
      internal-review: {}
      external-review: {}
      merge: {}
    triggers:
      lane-selector:
        type: github-issue-label
        label: active-lane
    storage:
      ledger: ledger.json
      health: health.json
      audit-log: audit.log
""")


def test_parse_and_validate_returns_snapshot(tmp_path):
    from workflows.code_review.config_watcher import parse_and_validate

    p = tmp_path / "workflow.yaml"
    p.write_text(_VALID_YAML)
    snap = parse_and_validate(p)
    assert snap.config["workflow"] == "code-review"
    assert snap.source_mtime == p.stat().st_mtime
    assert snap.loaded_at > 0


def test_parse_and_validate_raises_on_yaml_syntax_error(tmp_path):
    from workflows.code_review.config_watcher import parse_and_validate, ParseError

    p = tmp_path / "workflow.yaml"
    p.write_text("workflow: [unclosed\n")
    with pytest.raises(ParseError):
        parse_and_validate(p)


def test_parse_and_validate_raises_on_schema_violation(tmp_path):
    from workflows.code_review.config_watcher import parse_and_validate, ValidationError

    p = tmp_path / "workflow.yaml"
    p.write_text("workflow: code-review\n")  # missing required fields
    with pytest.raises(ValidationError):
        parse_and_validate(p)
