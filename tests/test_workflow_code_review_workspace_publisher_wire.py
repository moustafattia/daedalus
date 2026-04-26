"""build_workspace should wire the comment publisher when observability is on."""
import importlib.util
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _make_minimal_config(workspace_root: Path) -> dict:
    return {
        "workflow": "code-review",
        "schemaVersion": 1,
        "instance": {"name": "test", "engineOwner": "hermes"},
        "repository": {
            "localPath": str(workspace_root),
            "githubSlug": "owner/repo",
            "activeLaneLabel": "active-lane",
        },
        "auditLogPath": str(workspace_root / "memory" / "audit.jsonl"),
        "ledgerPath": str(workspace_root / "memory" / "ledger.json"),
        "healthPath": str(workspace_root / "memory" / "health.json"),
        "cronJobsPath": str(workspace_root / "cron-jobs.json"),
        "hermesCronJobsPath": str(workspace_root / "hermes-cron-jobs.json"),
        "sessionsStatePath": str(workspace_root / "state" / "sessions"),
        # … minimal stubs for the rest. Do NOT fully replicate workflow.yaml here;
        # only fields that build_workspace dereferences before the audit wiring.
    }


def test_make_publisher_returns_callable_even_when_initially_disabled(tmp_path):
    """When github-comments.enabled=false at startup, a publisher is STILL
    returned so a later /daedalus set-observability --github-comments on
    override takes effect at the next audit event without a service restart.
    The publisher's per-call re-resolution is the gate — not this factory.
    Calling the publisher when disabled is a no-op (no gh CLI calls)."""
    import subprocess
    workspace_module = load_module(
        "daedalus_workflow_code_review_workspace_publisher_wire_test",
        "workflows/code_review/workspace.py",
    )
    fake_run_calls = []

    def fake_run(*args, **kwargs):
        fake_run_calls.append(args)
        # If we ever reach here, the gate failed to short-circuit.
        raise AssertionError("publisher must not call gh when disabled")

    publisher = workspace_module._make_comment_publisher(
        workflow_root=tmp_path,
        repo_slug="owner/repo",
        workflow_yaml={"observability": {"github-comments": {"enabled": False}}},
        get_active_issue_number=lambda: 329,
        get_workflow_state=lambda: "under_review",
        get_is_operator_attention=lambda: False,
        run_fn=fake_run,
    )
    assert callable(publisher)
    publisher(action="merge-and-promote", summary="ok", extra={"mergedPrNumber": 1})
    assert fake_run_calls == []  # short-circuited inside publisher()


def test_disabled_then_override_enabled_publisher_takes_effect(tmp_path):
    """After flipping enabled=true via the runtime override file, the same
    publisher instance starts emitting — no restart needed."""
    import json
    workspace_module = load_module(
        "daedalus_workflow_code_review_workspace_publisher_wire_test",
        "workflows/code_review/workspace.py",
    )
    fake_run_calls = []

    def fake_run(argv, **kwargs):
        fake_run_calls.append(argv)
        from unittest import mock
        result = mock.Mock()
        result.returncode = 0
        result.stdout = "https://github.com/o/r/issues/329#issuecomment-99\n"
        result.stderr = ""
        return result

    publisher = workspace_module._make_comment_publisher(
        workflow_root=tmp_path,
        repo_slug="owner/repo",
        workflow_yaml={"observability": {"github-comments": {"enabled": False}}},
        get_active_issue_number=lambda: 329,
        get_workflow_state=lambda: "under_review",
        get_is_operator_attention=lambda: False,
        run_fn=fake_run,
    )
    # First call: still disabled, no gh.
    publisher(action="merge-and-promote", summary="ok", extra={"mergedPrNumber": 1})
    assert fake_run_calls == []

    # Operator flips on via override file (mirrors /daedalus set-observability).
    override_dir = tmp_path / "runtime" / "state" / "daedalus"
    override_dir.mkdir(parents=True, exist_ok=True)
    (override_dir / "observability-overrides.json").write_text(json.dumps({
        "code-review": {"github-comments": {"enabled": True, "set-at": "2026-04-26T00:00:00Z"}}
    }))

    # Same publisher instance now emits.
    publisher(action="merge-and-promote", summary="ok", extra={"mergedPrNumber": 1})
    assert len(fake_run_calls) == 1
    assert fake_run_calls[0][0] == "gh"


def test_make_publisher_returns_callable_when_enabled(tmp_path):
    workspace_module = load_module(
        "daedalus_workflow_code_review_workspace_publisher_wire_test",
        "workflows/code_review/workspace.py",
    )
    publisher = workspace_module._make_comment_publisher(
        workflow_root=tmp_path,
        repo_slug="owner/repo",
        workflow_yaml={"observability": {"github-comments": {"enabled": True}}},
        get_active_issue_number=lambda: 329,
        get_workflow_state=lambda: "under_review",
        get_is_operator_attention=lambda: False,
    )
    assert callable(publisher)


def test_publisher_skips_when_no_active_issue(tmp_path):
    """When no active lane exists, the publisher silently skips."""
    workspace_module = load_module(
        "daedalus_workflow_code_review_workspace_publisher_wire_test",
        "workflows/code_review/workspace.py",
    )
    fake_run_calls = []

    def fake_run(*args, **kwargs):
        fake_run_calls.append(args)
        raise AssertionError("publisher should not have called gh when issue=None")

    publisher = workspace_module._make_comment_publisher(
        workflow_root=tmp_path,
        repo_slug="owner/repo",
        workflow_yaml={"observability": {"github-comments": {"enabled": True}}},
        get_active_issue_number=lambda: None,
        get_workflow_state=lambda: "no_active_lane",
        get_is_operator_attention=lambda: False,
        run_fn=fake_run,
    )
    publisher(action="merge-and-promote", summary="x", extra={"mergedPrNumber": 1})
    assert fake_run_calls == []
