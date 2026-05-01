"""Regression: the new /daedalus subcommands must also dispatch correctly via
the argparse ``func=run_cli_command`` path.

Codex Cloud follow-up to a3ea328: the previous fix only routed
``watch`` / ``set-observability`` / ``get-observability`` through
``execute_raw_args`` (the slash-command path). The argparse CLI path
(``python3 daedalus_cli.py <cmd> ...`` and any ``setup_cli``-registered command)
still calls ``run_cli_command`` which previously hard-coded
``execute_namespace`` -- raising ``unknown daedalus command`` for the new
string-returning subcommands.

These tests pin ``run_cli_command`` so it dispatches the string-returning
handlers directly.
"""
import json
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


def _tools():
    return load_module("daedalus_tools_run_cli_command_dispatch_test", "daedalus_cli.py")


def _parse(tools, argv):
    """Use the real parser so ``args`` carries the right ``handler`` default."""
    parser = tools.build_parser()
    return parser.parse_args(argv)


def test_run_cli_command_dispatches_set_observability(tmp_path, capsys):
    tools = _tools()
    args = _parse(
        tools,
        [
            "set-observability",
            "--workflow-root",
            str(tmp_path),
            "--workflow",
            "change-delivery",
            "--github-comments",
            "unset",
        ],
    )
    tools.run_cli_command(args)
    out = capsys.readouterr().out
    assert "unknown daedalus command" not in out, out
    assert "change-delivery" in out


def test_run_cli_command_dispatches_get_observability(tmp_path, capsys):
    tools = _tools()
    args = _parse(
        tools,
        [
            "get-observability",
            "--workflow-root",
            str(tmp_path),
            "--workflow",
            "change-delivery",
        ],
    )
    tools.run_cli_command(args)
    out = capsys.readouterr().out
    assert "unknown daedalus command" not in out, out
    assert "change-delivery" in out or "github-comments" in out.lower()


def test_run_cli_command_dispatches_watch(tmp_path, capsys):
    """Drive ``watch --once`` via run_cli_command (the argparse CLI path)."""
    root = tmp_path / "workflow_example"
    (root / "runtime" / "memory").mkdir(parents=True)
    (root / "runtime" / "state" / "daedalus").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "workspace").mkdir()

    tools = _tools()
    args = _parse(
        tools,
        ["watch", "--once", "--workflow-root", str(root)],
    )
    tools.run_cli_command(args)
    out = capsys.readouterr().out
    assert "unknown daedalus command" not in out, out
    assert "Daedalus active lanes" in out or "active lanes" in out.lower()


def test_execute_raw_args_runs_command_lists_engine_runs(tmp_path):
    from engine.store import EngineStore
    from workflows.contract import render_workflow_markdown
    from workflows.shared.paths import runtime_paths

    tools = _tools()
    root = tmp_path / "attmous-daedalus-issue-runner"
    root.mkdir()
    (root / "WORKFLOW.md").write_text(
        render_workflow_markdown(
            config={
                "workflow": "issue-runner",
                "schema-version": 1,
                "instance": {"name": "attmous-daedalus-issue-runner", "engine-owner": "hermes"},
                "repository": {"local-path": str(tmp_path / "repo"), "github-slug": "attmous/daedalus"},
                "tracker": {"kind": "local-json", "path": "config/issues.json"},
                "workspace": {"root": "workspace/issues"},
                "agent": {"name": "runner", "model": "gpt-5.4", "runtime": "default"},
            },
            prompt_template="Issue: {{ issue.identifier }}",
        ),
        encoding="utf-8",
    )
    store = EngineStore(
        db_path=runtime_paths(root)["db_path"],
        workflow="issue-runner",
        now_iso=lambda: "2026-04-30T12:00:21Z",
        now_epoch=lambda: 1714478421.0,
    )
    run = store.start_run(mode="tick")
    store.complete_run(run["run_id"], selected_count=1, completed_count=1)
    store.append_event(
        event_type="issue_runner.tick.completed",
        payload={"event": "issue_runner.tick.completed", "run_id": run["run_id"], "issue_id": "ISSUE-1"},
        run_id=run["run_id"],
        work_id="ISSUE-1",
    )

    output = tools.execute_raw_args(f"runs --workflow-root {root} --json")
    payload = json.loads(output)
    show_output = tools.execute_raw_args(f"runs --workflow-root {root} show {run['run_id']} --json")
    show_payload = json.loads(show_output)

    assert payload["workflow"] == "issue-runner"
    assert payload["runs"][0]["run_id"] == run["run_id"]
    assert show_payload["timeline"][0]["event_type"] == "issue_runner.tick.completed"


def test_execute_raw_args_events_lists_and_prunes_engine_events(tmp_path):
    from engine.store import EngineStore
    from workflows.contract import render_workflow_markdown
    from workflows.shared.paths import runtime_paths

    tools = _tools()
    root = tmp_path / "attmous-daedalus-issue-runner"
    root.mkdir()
    (root / "WORKFLOW.md").write_text(
        render_workflow_markdown(
            config={
                "workflow": "issue-runner",
                "schema-version": 1,
                "instance": {"name": "attmous-daedalus-issue-runner", "engine-owner": "hermes"},
                "repository": {"local-path": str(tmp_path / "repo"), "github-slug": "attmous/daedalus"},
                "tracker": {"kind": "local-json", "path": "config/issues.json"},
                "workspace": {"root": "workspace/issues"},
                "agent": {"name": "runner", "model": "gpt-5.4", "runtime": "default"},
                "retention": {"events": {"max-rows": 1}},
            },
            prompt_template="Issue: {{ issue.identifier }}",
        ),
        encoding="utf-8",
    )
    clock = {"iso": "2026-04-30T12:00:21Z", "epoch": 1714478421.0}
    store = EngineStore(
        db_path=runtime_paths(root)["db_path"],
        workflow="issue-runner",
        now_iso=lambda: clock["iso"],
        now_epoch=lambda: clock["epoch"],
    )
    run = store.start_run(mode="tick")
    store.append_event(event_type="a", payload={"run_id": run["run_id"], "issue_id": "ISSUE-1"})
    clock.update({"iso": "2026-04-30T12:00:22Z", "epoch": 1714478422.0})
    store.append_event(event_type="b", payload={"run_id": run["run_id"], "issue_id": "ISSUE-2"})

    output = tools.execute_raw_args(f"events --workflow-root {root} --work-id ISSUE-2 --json")
    payload = json.loads(output)
    stats_output = tools.execute_raw_args(f"events --workflow-root {root} stats --json")
    stats_payload = json.loads(stats_output)
    prune_output = tools.execute_raw_args(f"events --workflow-root {root} prune --json")
    prune_payload = json.loads(prune_output)

    assert payload["workflow"] == "issue-runner"
    assert payload["events"][0]["event_type"] == "b"
    assert payload["events"][0]["work_id"] == "ISSUE-2"
    assert stats_payload["mode"] == "stats"
    assert stats_payload["stats"]["total_events"] == 2
    assert stats_payload["stats"]["retention"]["max_rows"] == 1
    assert stats_payload["stats"]["retention"]["excess_rows"] == 1
    assert prune_payload["deleted"] == 1
    assert prune_payload["remaining"] == 1


def test_run_cli_command_dispatches_scaffold_workflow(tmp_path, capsys):
    tools = _tools()
    root = tmp_path / "attmous-daedalus-issue-runner"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:attmous/daedalus.git"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    args = _parse(
        tools,
        [
            "scaffold-workflow",
            "--workflow-root",
            str(root),
            "--repo-path",
            str(repo),
            "--repo-slug",
            "attmous/daedalus",
        ],
    )
    tools.run_cli_command(args)
    out = capsys.readouterr().out
    assert "unknown daedalus command" not in out, out
    assert "scaffolded workflow root" in out
    assert (repo / "WORKFLOW.md").exists()


def test_run_cli_command_dispatches_bootstrap(tmp_path, capsys, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:attmous/daedalus.git"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    tools = _tools()
    args = _parse(
        tools,
        [
            "bootstrap",
            "--repo-path",
            str(repo),
        ],
    )
    tools.run_cli_command(args)
    out = capsys.readouterr().out
    assert "unknown daedalus command" not in out, out
    assert "bootstrapped workflow root" in out
    assert (repo / "WORKFLOW.md").exists()


def test_run_cli_command_dispatches_service_loop_for_issue_runner(tmp_path, capsys, monkeypatch):
    tools = _tools()

    class FakeWorkspace:
        def run_loop(self, *, interval_seconds, max_iterations):
            return {
                "loop_status": "completed",
                "iterations": max_iterations,
                "last_result": {"ok": True},
            }

    monkeypatch.setattr(tools, "_assert_service_mode_supported", lambda **kwargs: "issue-runner")
    monkeypatch.setattr(tools, "_load_issue_runner_workspace", lambda workflow_root: FakeWorkspace())
    monkeypatch.setattr(tools, "_record_operator_command_event", lambda **kwargs: None)

    args = _parse(
        tools,
        [
            "service-loop",
            "--workflow-root",
            str(tmp_path),
            "--project-key",
            "attmous-daedalus-issue-runner",
            "--max-iterations",
            "1",
            "--json",
        ],
    )
    tools.run_cli_command(args)
    payload = json.loads(capsys.readouterr().out)
    assert payload["workflow"] == "issue-runner"
    assert payload["loop_status"] == "completed"
    assert payload["iterations"] == 1
