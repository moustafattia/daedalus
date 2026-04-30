import importlib.util
import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_public_onboarding_path_install_bootstrap_defaults_to_issue_runner_and_service_up(tmp_path, monkeypatch):
    install = _load_module("daedalus_install_smoke", REPO_ROOT / "scripts" / "install.py")
    hermes_home = tmp_path / ".hermes"
    plugin_dir = install.install_plugin(repo_root=REPO_ROOT, hermes_home=hermes_home)
    monkeypatch.setenv("HOME", str(tmp_path))

    monkeypatch.syspath_prepend(str(plugin_dir))
    tools = _load_module("daedalus_tools_smoke", plugin_dir / "daedalus_cli.py")

    systemd_user_dir = tmp_path / "systemd-user"
    monkeypatch.setenv("DAEDALUS_SYSTEMD_USER_DIR", str(systemd_user_dir))

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
    monkeypatch.chdir(repo)

    captured_commands = []
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        captured_commands.append(cmd)
        if cmd[:2] == ["systemctl", "--user"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(tools.subprocess, "run", fake_run)

    workflow_root = hermes_home / "workflows" / "attmous-daedalus-issue-runner"

    bootstrap_out = tools.execute_raw_args("bootstrap")
    assert "bootstrapped workflow root" in bootstrap_out
    assert (repo / ".hermes" / "daedalus" / "workflow-root").read_text(encoding="utf-8").strip() == str(workflow_root)
    assert (repo / "WORKFLOW.md").exists()
    assert ["git", "checkout", "-b", "daedalus/bootstrap-issue-runner"] in captured_commands

    service_up_out = tools.execute_raw_args("service-up --json")
    service_up_payload = json.loads(service_up_out)
    assert service_up_payload["ok"] is True
    assert service_up_payload["preflight"]["ok"] is True
    assert service_up_payload["preflight"]["workflow"] == "issue-runner"
    assert service_up_payload["init"]["skipped"] is True
    assert Path(service_up_payload["service_install"]["unit_path"]).exists()
    assert service_up_payload["service_enable"]["ok"] is True
    assert service_up_payload["service_start"]["ok"] is True
    assert service_up_payload["service_status"]["service_name"] == "daedalus-active@attmous-daedalus-issue-runner.service"

    status_out = tools.execute_raw_args("status --format json")
    status_payload = json.loads(status_out)
    assert status_payload["workflow"] == "issue-runner"
    assert status_payload["contractPath"] == str(repo / "WORKFLOW.md")
    assert status_payload["tracker"]["kind"] == "local-json"
    assert status_payload["tracker"]["issueCount"] >= 1

    assert ["systemctl", "--user", "daemon-reload"] in captured_commands
    assert ["systemctl", "--user", "enable", "daedalus-active@attmous-daedalus-issue-runner.service"] in captured_commands
    assert ["systemctl", "--user", "start", "daedalus-active@attmous-daedalus-issue-runner.service"] in captured_commands


def test_change_delivery_onboarding_path_bootstrap_service_up_and_status(tmp_path, monkeypatch):
    install = _load_module("daedalus_install_issue_runner_smoke", REPO_ROOT / "scripts" / "install.py")
    hermes_home = tmp_path / ".hermes"
    plugin_dir = install.install_plugin(repo_root=REPO_ROOT, hermes_home=hermes_home)
    monkeypatch.setenv("HOME", str(tmp_path))

    monkeypatch.syspath_prepend(str(plugin_dir))
    tools = _load_module("daedalus_tools_issue_runner_smoke", plugin_dir / "daedalus_cli.py")

    systemd_user_dir = tmp_path / "systemd-user"
    monkeypatch.setenv("DAEDALUS_SYSTEMD_USER_DIR", str(systemd_user_dir))

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
    monkeypatch.chdir(repo)

    captured_commands = []
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        captured_commands.append(cmd)
        if cmd[:2] == ["systemctl", "--user"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(tools.subprocess, "run", fake_run)

    workflow_root = hermes_home / "workflows" / "attmous-daedalus-change-delivery"

    bootstrap_out = tools.execute_raw_args("bootstrap --workflow change-delivery")
    assert "bootstrapped workflow root" in bootstrap_out
    assert (repo / ".hermes" / "daedalus" / "workflow-root").read_text(encoding="utf-8").strip() == str(workflow_root)
    assert (repo / "WORKFLOW.md").exists()
    assert ["git", "checkout", "-b", "daedalus/bootstrap-change-delivery"] in captured_commands

    service_up_out = tools.execute_raw_args("service-up --json")
    service_up_payload = json.loads(service_up_out)
    assert service_up_payload["ok"] is True
    assert service_up_payload["preflight"]["ok"] is True
    assert service_up_payload["preflight"]["workflow"] == "change-delivery"
    assert Path(service_up_payload["service_install"]["unit_path"]).exists()
    assert service_up_payload["service_enable"]["ok"] is True
    assert service_up_payload["service_start"]["ok"] is True
    assert service_up_payload["service_status"]["service_name"] == "daedalus-active@attmous-daedalus-change-delivery.service"

    status_out = tools.execute_raw_args("status --format json")
    status_payload = json.loads(status_out)
    assert status_payload["runtime_status"] == "initialized"
    assert status_payload["project_key"] == "attmous-daedalus-change-delivery"


def test_readme_quickstart_mentions_supported_public_path():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "hermes plugins install attmous/daedalus --enable" in readme
    assert "hermes daedalus bootstrap" in readme
    assert "WORKFLOW.md" in readme
    assert "service-up" in readme
    assert "docs/operator/installation.md" in readme
    assert "docs/public-contract.md" in readme
    assert "manual scaffold paths" in readme.lower()
    assert "lower-level command" in readme.lower()
