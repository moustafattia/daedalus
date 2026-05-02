import importlib.util
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_live_smoke_runner_lists_skipped_smokes_without_env(monkeypatch, capsys):
    smoke_live = _load_script("smoke_live_test", REPO_ROOT / "scripts" / "smoke_live.py")
    for key in [
        "DAEDALUS_GITHUB_SMOKE_REPO",
        "DAEDALUS_REAL_CODEX_APP_SERVER",
        "DAEDALUS_CHANGE_DELIVERY_CODEX_E2E",
        "DAEDALUS_CHANGE_DELIVERY_E2E_REPO",
    ]:
        monkeypatch.delenv(key, raising=False)

    assert smoke_live.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "skip github-issue-runner" in out
    assert "skip codex-app-server-runtime" in out
    assert "skip change-delivery-codex" in out


def test_live_smoke_runner_executes_only_configured_smoke(monkeypatch):
    smoke_live = _load_script("smoke_live_test", REPO_ROOT / "scripts" / "smoke_live.py")
    monkeypatch.setenv("DAEDALUS_CHANGE_DELIVERY_CODEX_E2E", "1")
    monkeypatch.setenv("DAEDALUS_CHANGE_DELIVERY_E2E_REPO", "your-org/your-repo")
    seen: list[tuple[str, ...]] = []

    def fake_run(command, *, cwd=None, env=None, check=False):
        seen.append(tuple(command))
        assert cwd == REPO_ROOT
        assert env["DAEDALUS_CHANGE_DELIVERY_E2E_REPO"] == "your-org/your-repo"
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(smoke_live.subprocess, "run", fake_run)

    assert smoke_live.main(["--only", "change-delivery-codex"]) == 0
    assert len(seen) == 1
    assert "tests/test_change_delivery_codex_app_server_smoke.py" in seen[0]


def test_live_smoke_runner_can_fail_when_nothing_is_configured(monkeypatch):
    smoke_live = _load_script("smoke_live_test", REPO_ROOT / "scripts" / "smoke_live.py")
    for key in [
        "DAEDALUS_GITHUB_SMOKE_REPO",
        "DAEDALUS_REAL_CODEX_APP_SERVER",
        "DAEDALUS_CHANGE_DELIVERY_CODEX_E2E",
        "DAEDALUS_CHANGE_DELIVERY_E2E_REPO",
    ]:
        monkeypatch.delenv(key, raising=False)

    assert smoke_live.main(["--only", "github-issue-runner", "--fail-if-none"]) == 2


def test_release_scorecard_script_tracks_current_evidence():
    scorecard = _load_script("release_scorecard_test", REPO_ROOT / "scripts" / "release_scorecard.py")
    report = scorecard.collect()

    assert report["ok"] is True
    names = {item["name"] for item in report["checks"]}
    assert "change-delivery Codex smoke" in names
    assert "smoke runner script" in names
    assert "scorecard automation" in names


def test_release_scorecard_workflow_is_scheduled():
    workflow = (REPO_ROOT / ".github" / "workflows" / "release-scorecard.yml").read_text(encoding="utf-8")

    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "python scripts/release_scorecard.py --check" in workflow
