"""Comment publisher: orchestrates state load → render → gh CLI → state save.

We mock subprocess.run to capture every gh invocation; no live GitHub calls.
"""
import importlib.util
import json
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


def _publisher():
    return load_module(
        "daedalus_workflow_code_review_comments_publisher_test",
        "workflows/code_review/comments_publisher.py",
    )


class _FakeRun:
    """Capturable subprocess.run replacement."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": argv, "kwargs": kwargs})
        if not self.responses:
            raise AssertionError(f"FakeRun ran out of responses; argv={argv}")
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        result = mock.Mock()
        result.returncode = resp.get("returncode", 0)
        result.stdout = resp.get("stdout", "")
        result.stderr = resp.get("stderr", "")
        if result.returncode != 0:
            import subprocess
            raise subprocess.CalledProcessError(result.returncode, argv, output=result.stdout, stderr=result.stderr)
        return result


def test_publisher_disabled_when_event_not_included(tmp_path):
    pub = _publisher()
    fake_run = _FakeRun([])  # no calls expected
    result = pub.publish_event(
        repo_slug="owner/repo",
        issue_number=329,
        workflow_state="under_review",
        is_operator_attention=False,
        audit_event={"at": "2026-04-26T22:00:00Z", "action": "reconcile", "summary": "x"},
        effective_config={"github-comments": {"enabled": True, "include-events": ["merge-and-promote"]}},
        state_dir=tmp_path,
        run_fn=fake_run,
    )
    assert result == {"published": False, "reason": "event-not-in-include-events"}
    assert fake_run.calls == []


def test_publisher_disabled_when_globally_off(tmp_path):
    pub = _publisher()
    fake_run = _FakeRun([])
    result = pub.publish_event(
        repo_slug="owner/repo",
        issue_number=329,
        workflow_state="under_review",
        is_operator_attention=False,
        audit_event={"at": "2026-04-26T22:00:00Z", "action": "merge-and-promote", "summary": "x"},
        effective_config={"github-comments": {"enabled": False, "include-events": []}},
        state_dir=tmp_path,
        run_fn=fake_run,
    )
    assert result["published"] is False
    assert result["reason"] == "github-comments-disabled"
    assert fake_run.calls == []


def test_first_event_creates_comment_via_gh(tmp_path):
    pub = _publisher()
    # gh issue comment returns the URL of the new comment
    fake_run = _FakeRun([
        {"returncode": 0, "stdout": "https://github.com/owner/repo/issues/329#issuecomment-12345\n"},
    ])
    result = pub.publish_event(
        repo_slug="owner/repo",
        issue_number=329,
        workflow_state="under_review",
        is_operator_attention=False,
        audit_event={
            "at": "2026-04-26T22:30:00Z",
            "action": "dispatch-implementation-turn",
            "summary": "Dispatched coder",
            "model": "gpt-5.3-codex-spark",
            "sessionName": "lane-329",
        },
        effective_config={"github-comments": {"enabled": True, "include-events": []}},
        state_dir=tmp_path,
        run_fn=fake_run,
    )
    assert result["published"] is True
    assert result["comment_id"] == "12345"
    assert len(fake_run.calls) == 1
    argv = fake_run.calls[0]["argv"]
    assert argv[0] == "gh"
    assert "issue" in argv and "comment" in argv
    assert "329" in argv
    assert "--repo" in argv and "owner/repo" in argv
    # State persisted
    state_file = tmp_path / "329.json"
    assert state_file.exists()
    state = json.loads(state_file.read_text())
    assert state["comment_id"] == "12345"
    assert "Daedalus lane status" in state["last_rendered_text"]


def test_subsequent_event_edits_existing_comment(tmp_path):
    pub = _publisher()
    # Pre-seed state as if a prior event created comment 12345
    (tmp_path / "329.json").write_text(json.dumps({
        "comment_id": "12345",
        "last_rendered_text": "old body",
        "rows": ["| 22:00:00 | 🔄 Codex coder dispatched | x |"],
        "last_action": "dispatch-implementation-turn",
    }))
    fake_run = _FakeRun([
        {"returncode": 0, "stdout": ""},  # gh api PATCH returns empty
    ])
    result = pub.publish_event(
        repo_slug="owner/repo",
        issue_number=329,
        workflow_state="under_review",
        is_operator_attention=False,
        audit_event={
            "at": "2026-04-26T22:31:00Z",
            "action": "merge-and-promote",
            "summary": "Merged",
            "mergedPrNumber": 382,
        },
        effective_config={"github-comments": {"enabled": True, "include-events": []}},
        state_dir=tmp_path,
        run_fn=fake_run,
    )
    assert result["published"] is True
    assert result["comment_id"] == "12345"
    assert len(fake_run.calls) == 1
    argv = fake_run.calls[0]["argv"]
    # PATCH path uses gh api
    assert argv[0] == "gh"
    assert argv[1] == "api"
    assert any("12345" in part for part in argv)


def test_skip_publish_when_rendered_body_unchanged(tmp_path):
    pub = _publisher()
    # The comment publisher dedupes when rendered body matches last_rendered_text.
    # Pre-seed a state with a known body, then re-fire the same event.
    pre_event = {
        "at": "2026-04-26T22:30:00Z",
        "action": "dispatch-implementation-turn",
        "summary": "x",
        "model": "gpt-5.3-codex-spark",
        "sessionName": "lane-329",
    }
    fake_run_first = _FakeRun([
        {"returncode": 0, "stdout": "https://github.com/owner/repo/issues/329#issuecomment-12345\n"},
    ])
    pub.publish_event(
        repo_slug="owner/repo",
        issue_number=329,
        workflow_state="under_review",
        is_operator_attention=False,
        audit_event=pre_event,
        effective_config={"github-comments": {"enabled": True, "include-events": []}},
        state_dir=tmp_path,
        run_fn=fake_run_first,
    )
    # Now re-fire the same event. With the same rendered output, no second gh call.
    fake_run_second = _FakeRun([])
    result = pub.publish_event(
        repo_slug="owner/repo",
        issue_number=329,
        workflow_state="under_review",
        is_operator_attention=False,
        audit_event=pre_event,
        effective_config={"github-comments": {"enabled": True, "include-events": []}},
        state_dir=tmp_path,
        run_fn=fake_run_second,
    )
    assert result["published"] is False
    assert result["reason"] == "rendered-unchanged"
    assert fake_run_second.calls == []


def test_gh_failure_does_not_raise_returns_failure_result(tmp_path):
    pub = _publisher()
    import subprocess
    fake_run = _FakeRun([
        subprocess.CalledProcessError(1, ["gh"], output="", stderr="rate limited"),
    ])
    result = pub.publish_event(
        repo_slug="owner/repo",
        issue_number=329,
        workflow_state="under_review",
        is_operator_attention=False,
        audit_event={"at": "2026-04-26T22:30:00Z", "action": "merge-and-promote", "summary": "x", "mergedPrNumber": 382},
        effective_config={"github-comments": {"enabled": True, "include-events": []}},
        state_dir=tmp_path,
        run_fn=fake_run,
    )
    # Publisher swallows the error — observability never blocks workflow execution.
    assert result["published"] is False
    assert "error" in result
    assert "rate limited" in result["error"]
