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


def test_public_onboarding_path_install_scaffold_init_and_supervise(tmp_path, monkeypatch):
    install = _load_module("daedalus_install_smoke", REPO_ROOT / "scripts" / "install.py")
    hermes_home = tmp_path / ".hermes"
    plugin_dir = install.install_plugin(repo_root=REPO_ROOT, hermes_home=hermes_home)

    monkeypatch.syspath_prepend(str(plugin_dir))
    tools = _load_module("daedalus_tools_smoke", plugin_dir / "tools.py")

    systemd_user_dir = tmp_path / "systemd-user"
    monkeypatch.setenv("DAEDALUS_SYSTEMD_USER_DIR", str(systemd_user_dir))

    captured_commands = []

    def fake_run(cmd, **kwargs):
        captured_commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(tools.subprocess, "run", fake_run)

    workflow_root = hermes_home / "workflows" / "attmous-daedalus-code-review"

    scaffold_out = tools.execute_raw_args(
        f"scaffold-workflow --workflow-root {workflow_root} --github-slug attmous/daedalus"
    )
    assert "scaffolded workflow root" in scaffold_out

    init_out = tools.execute_raw_args(f"init --workflow-root {workflow_root} --json")
    init_payload = json.loads(init_out)
    assert init_payload["ok"] is True

    status_out = tools.execute_raw_args(f"status --workflow-root {workflow_root} --format json")
    status_payload = json.loads(status_out)
    assert status_payload["runtime_status"] == "initialized"
    assert status_payload["project_key"] == "attmous-daedalus-code-review"

    install_out = tools.execute_raw_args(
        f"service-install --workflow-root {workflow_root} --service-mode active --json"
    )
    install_payload = json.loads(install_out)
    assert install_payload["installed"] is True
    assert Path(install_payload["unit_path"]).exists()

    enable_out = tools.execute_raw_args(
        f"service-enable --workflow-root {workflow_root} --service-mode active --json"
    )
    enable_payload = json.loads(enable_out)
    assert enable_payload["ok"] is True

    start_out = tools.execute_raw_args(
        f"service-start --workflow-root {workflow_root} --service-mode active --json"
    )
    start_payload = json.loads(start_out)
    assert start_payload["ok"] is True

    assert ["systemctl", "--user", "daemon-reload"] in captured_commands
    assert ["systemctl", "--user", "enable", "daedalus-active@attmous-daedalus-code-review.service"] in captured_commands
    assert ["systemctl", "--user", "start", "daedalus-active@attmous-daedalus-code-review.service"] in captured_commands


def test_readme_quickstart_mentions_supported_public_path():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "hermes plugins install attmous/daedalus --enable" in readme
    assert "hermes daedalus scaffold-workflow" in readme
    assert "scaffold-workflow" in readme
    assert "service-install" in readme
    assert "docs/operator/installation.md" in readme
    assert "docs/public-contract.md" in readme
    assert "python3 -m pip install ." in readme
    assert "hermes plugins enable daedalus" in readme
    assert "HERMES_ENABLE_PROJECT_PLUGINS=true" in readme
    assert "project-local plugins" in readme
