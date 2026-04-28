"""Phase B: external-reviewer repair-handoff prompt template."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_repair_handoff_template_file_exists():
    bundled = Path(__file__).resolve().parent.parent / "daedalus" / "workflows" / "code_review" / "prompts" / "external-reviewer-repair-handoff.md"
    assert bundled.is_file()


def test_render_external_reviewer_repair_handoff_prompt_callable():
    from workflows.code_review.prompts import render_external_reviewer_repair_handoff_prompt
    assert callable(render_external_reviewer_repair_handoff_prompt)


def test_repair_handoff_includes_required_fields():
    from workflows.code_review.prompts import render_external_reviewer_repair_handoff_prompt

    out = render_external_reviewer_repair_handoff_prompt(
        issue={"number": 42, "title": "Bug X"},
        external_review={"reviewedHeadSha": "abc123", "summary": "Found issue."},
        repair_brief={"mustFix": [{"summary": "Fix A"}], "shouldFix": []},
        lane_memo_path=Path("/tmp/memo.md"),
        lane_state_path=Path("/tmp/state.json"),
        pr_url="https://x/1",
        external_reviewer_agent_name="My_External_Reviewer",
    )
    assert "issue #42" in out
    assert "abc123" in out
    assert "Fix A" in out
    assert "My_External_Reviewer" in out
    assert "https://x/1" in out
