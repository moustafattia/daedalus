"""Audit event → bot-comment rendering. Pure-function, no I/O."""
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _module():
    return load_module(
        "daedalus_workflow_change_delivery_comments_test",
        "workflows/change_delivery/comments.py",
    )


def test_render_row_for_dispatch_implementation_turn():
    comments = _module()
    row = comments.render_row({
        "at": "2026-04-26T22:30:34Z",
        "action": "dispatch-implementation-turn",
        "summary": "Dispatched coder",
        "model": "gpt-5.3-codex-spark/high",
        "sessionName": "lane-329",
    })
    assert "🔄" in row
    assert "Codex coder dispatched" in row
    assert "gpt-5.3-codex-spark/high" in row
    assert "22:30:34" in row


def test_render_row_for_merge_and_promote():
    comments = _module()
    row = comments.render_row({
        "at": "2026-04-26T22:31:00Z",
        "action": "merge-and-promote",
        "summary": "Merged",
        "mergedPrNumber": 382,
    })
    assert "✅" in row or "🚀" in row
    assert "382" in row


def test_render_row_falls_back_for_unknown_action():
    comments = _module()
    row = comments.render_row({
        "at": "2026-04-26T22:31:00Z",
        "action": "some-unknown-action",
        "summary": "Did the thing",
    })
    # Unknown actions still render — generic format keeps the comment honest
    # rather than silently dropping events.
    assert "some-unknown-action" in row
    assert "Did the thing" in row


def test_render_full_comment_includes_header_and_table():
    comments = _module()
    body = comments.render_comment(
        issue_number=329,
        workflow_state="under_review",
        rows=[
            "| 22:30:34 | 🔄 Codex coder dispatched | gpt-5.3-codex-spark/high |",
            "| 22:31:00 | 🚀 PR published | #382 |",
        ],
        is_operator_attention=False,
    )
    assert "Daedalus lane status" in body
    assert "lane #329" in body
    assert "under_review" in body
    assert "| Time (UTC) | Event | Detail |" in body
    assert "22:30:34" in body
    assert "Last update" in body


def test_render_full_comment_with_operator_attention_sets_sticky_header():
    comments = _module()
    body = comments.render_comment(
        issue_number=329,
        workflow_state="operator_attention_required",
        rows=["| 22:31:00 | ⚠️ Operator attention required | retry budget exhausted |"],
        is_operator_attention=True,
    )
    assert "⚠️" in body
    assert "operator-attention" in body or "operator_attention" in body


def test_render_truncates_to_max_rows():
    comments = _module()
    rows = [f"| 22:00:0{i} | x | y |" for i in range(60)]
    body = comments.render_comment(
        issue_number=329,
        workflow_state="under_review",
        rows=rows,
        is_operator_attention=False,
    )
    # Older rows truncated when count exceeds MAX_COMMENT_ROWS (50).
    assert body.count("| 22:00:") <= 51  # 50 rows + 1 header row pattern match


def test_append_row_keeps_chronological_order_newest_first():
    comments = _module()
    existing_rows = ["| 22:00:01 | ev1 | d1 |"]
    new_row = "| 22:00:05 | ev2 | d2 |"
    out = comments.append_row(existing_rows, new_row)
    assert out[0] == new_row  # newest at top
    assert out[1] == existing_rows[0]


def test_append_row_caps_at_max_rows():
    comments = _module()
    existing_rows = [f"| 22:00:{i:02d} | x | y |" for i in range(50)]
    new_row = "| 22:01:00 | new | new |"
    out = comments.append_row(existing_rows, new_row)
    assert len(out) == 50  # MAX_COMMENT_ROWS
    assert out[0] == new_row
    # Oldest row dropped.
    assert "22:00:00" not in out[-1]
