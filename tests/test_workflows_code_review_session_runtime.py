import importlib.util
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_normalize_acpx_session_meta_maps_session_fields():
    sessions_module = load_module("daedalus_workflows_change_delivery_session_runtime_test", "workflows/change_delivery/sessions.py")

    result = sessions_module.normalize_acpx_session_meta(
        {
            "name": "lane-224",
            "closed": False,
            "cwd": "/tmp/issue-224",
            "lastUsedAt": "2026-04-23T01:00:00Z",
            "acpSessionId": "session-123",
            "acpxRecordId": "record-123",
        }
    )

    assert result == {
        "name": "lane-224",
        "closed": False,
        "cwd": "/tmp/issue-224",
        "last_used_at": "2026-04-23T01:00:00Z",
        "session_id": "session-123",
        "record_id": "record-123",
    }



def test_show_acpx_session_returns_normalized_payload():
    sessions_module = load_module("daedalus_workflows_change_delivery_session_runtime_test", "workflows/change_delivery/sessions.py")

    seen = {}

    def fake_run_json(command):
        seen["command"] = command
        return {
            "name": "lane-224",
            "closed": False,
            "cwd": "/tmp/issue-224",
            "last_used_at": "2026-04-23T01:00:00Z",
            "acpxSessionId": "session-123",
            "acpx_record_id": "record-123",
        }

    result = sessions_module.show_acpx_session(
        worktree=Path('/tmp/issue-224'),
        session_name='lane-224',
        run_json=fake_run_json,
    )

    assert result["session_id"] == "session-123"
    assert seen["command"][:6] == ["acpx", "--format", "json", "--json-strict", "--cwd", "/tmp/issue-224"]



def test_close_acpx_session_returns_true_when_runner_succeeds():
    sessions_module = load_module("daedalus_workflows_change_delivery_session_runtime_test", "workflows/change_delivery/sessions.py")

    seen = {}

    def fake_run(command):
        seen["command"] = command

    result = sessions_module.close_acpx_session(
        worktree=Path('/tmp/issue-224'),
        session_name='lane-224',
        run=fake_run,
    )

    assert result is True
    assert seen["command"] == ["acpx", "--cwd", "/tmp/issue-224", "codex", "sessions", "close", "lane-224"]



def test_ensure_acpx_session_retries_without_resume_when_resource_not_found():
    sessions_module = load_module("daedalus_workflows_change_delivery_session_runtime_test", "workflows/change_delivery/sessions.py")

    calls = []

    def fake_run_json(command):
        calls.append(command)
        if len(calls) == 1:
            exc = subprocess.CalledProcessError(1, command, output="Resource not found", stderr="")
            exc.stdout = "Resource not found"
            raise exc
        return {"acpxRecordId": "record-123"}

    result = sessions_module.ensure_acpx_session(
        worktree=Path('/tmp/issue-224'),
        session_name='lane-224',
        codex_model='gpt-5.3-codex',
        resume_session_id='missing-session',
        run_json=fake_run_json,
    )

    assert result == {"acpxRecordId": "record-123"}
    assert calls[0][-2:] == ["--resume-session", "missing-session"]
    assert calls[1][-2:] != ["--resume-session", "missing-session"]



def test_run_acpx_prompt_returns_stripped_stdout():
    sessions_module = load_module("daedalus_workflows_change_delivery_session_runtime_test", "workflows/change_delivery/sessions.py")

    class Completed:
        stdout = "ok\n"

    seen = {}

    def fake_run(command):
        seen["command"] = command
        return Completed()

    result = sessions_module.run_acpx_prompt(
        worktree=Path('/tmp/issue-224'),
        session_name='lane-224',
        prompt='hello',
        codex_model='gpt-5.3-codex',
        run=fake_run,
    )

    assert result == "ok"
    assert seen["command"][-2:] == ["lane-224", "hello"]



def test_prepare_lane_worktree_reuses_existing_git_repo_and_restores_artifacts(tmp_path):
    sessions_module = load_module("daedalus_workflows_change_delivery_session_runtime_test", "workflows/change_delivery/sessions.py")

    worktree = tmp_path / 'lane'
    worktree.mkdir()
    calls = []
    restored = {}

    def fake_run(command, cwd=None):
        calls.append((command, cwd))
        return None

    result = sessions_module.prepare_lane_worktree(
        worktree=worktree,
        branch='codex/issue-224-test',
        open_pr=None,
        repo_path=Path('/tmp/repo'),
        run=fake_run,
        is_git_repo=lambda path: True,
        snapshot_lane_artifacts_fn=lambda path: {'.lane-memo.md': 'memo'},
        restore_lane_artifacts_fn=lambda path, artifacts: restored.update({"path": path, "artifacts": artifacts}),
        rmtree=lambda path: (_ for _ in ()).throw(AssertionError('should not remove existing git repo')),
    )

    assert result["created"] is False
    assert calls[0][0] == ["git", "fetch", "origin", "main"]
    assert calls[1][0] == ["git", "checkout", 'codex/issue-224-test']
    assert restored["artifacts"] == {'.lane-memo.md': 'memo'}


def test_is_git_repo_helper_detects_repo_and_handles_missing_path(tmp_path):
    sessions_module = load_module("daedalus_workflows_change_delivery_session_runtime_test", "workflows/change_delivery/sessions.py")
    worktree = tmp_path / 'lane'
    worktree.mkdir()
    seen = []

    def fake_run(command, cwd=None):
        seen.append((command, cwd))
        return None

    assert sessions_module.is_git_repo(worktree, run=fake_run) is True
    assert seen == [(["git", "rev-parse", "--git-dir"], worktree)]
    assert sessions_module.is_git_repo(tmp_path / 'missing', run=fake_run) is False



def test_prepare_lane_worktree_recreates_non_git_directory_before_worktree_add(tmp_path):
    sessions_module = load_module("daedalus_workflows_change_delivery_session_runtime_test", "workflows/change_delivery/sessions.py")

    worktree = tmp_path / 'lane'
    worktree.mkdir()
    calls = []
    removed = []

    def fake_run(command, cwd=None):
        calls.append((command, cwd))
        return None

    result = sessions_module.prepare_lane_worktree(
        worktree=worktree,
        branch='codex/issue-224-test',
        open_pr={"number": 301},
        repo_path=Path('/tmp/repo'),
        run=fake_run,
        is_git_repo=lambda path: False,
        snapshot_lane_artifacts_fn=lambda path: {'.lane-state.json': '{}'},
        restore_lane_artifacts_fn=lambda path, artifacts: None,
        rmtree=lambda path: removed.append(path),
    )

    assert result["created"] is True
    assert removed == [worktree]
    assert calls[0][0] == ["git", "fetch", "origin", "main", 'codex/issue-224-test']
    assert calls[1][0] == ["git", "worktree", "add", "-B", 'codex/issue-224-test', str(worktree), 'origin/codex/issue-224-test']
