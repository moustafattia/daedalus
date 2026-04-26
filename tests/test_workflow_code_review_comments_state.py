"""State file: per-issue {comment_id, last_rendered_text, last_action}."""
import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _module():
    return load_module(
        "daedalus_workflow_code_review_comments_state_test",
        "workflows/code_review/comments.py",
    )


def test_state_path_for_issue(tmp_path):
    comments = _module()
    p = comments.state_path_for_issue(state_dir=tmp_path, issue_number=329)
    assert p == tmp_path / "329.json"


def test_load_returns_empty_state_when_file_absent(tmp_path):
    comments = _module()
    state = comments.load_state(tmp_path, 329)
    assert state == {"comment_id": None, "last_rendered_text": None, "rows": [], "last_action": None}


def test_save_then_load_roundtrip(tmp_path):
    comments = _module()
    state = {
        "comment_id": "12345",
        "last_rendered_text": "hello",
        "rows": ["| 22:00:01 | ev | d |"],
        "last_action": "dispatch-implementation-turn",
    }
    comments.save_state(tmp_path, 329, state)
    loaded = comments.load_state(tmp_path, 329)
    assert loaded == state


def test_save_writes_atomically(tmp_path):
    """Save should never leave a half-written file."""
    comments = _module()
    state = {"comment_id": "1", "last_rendered_text": "x", "rows": [], "last_action": None}
    comments.save_state(tmp_path, 329, state)
    # No leftover .tmp
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_save_creates_directory_if_missing(tmp_path):
    comments = _module()
    nested = tmp_path / "deeply" / "nested"
    state = {"comment_id": None, "last_rendered_text": None, "rows": [], "last_action": None}
    comments.save_state(nested, 329, state)
    assert (nested / "329.json").exists()


def test_load_corrupt_state_returns_empty(tmp_path):
    comments = _module()
    p = comments.state_path_for_issue(tmp_path, 329)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json{")
    state = comments.load_state(tmp_path, 329)
    assert state["comment_id"] is None
    assert state["rows"] == []
