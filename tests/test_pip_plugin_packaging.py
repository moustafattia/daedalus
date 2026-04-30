from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import zipfile
from email.parser import Parser
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _build_wheel(tmp_path: Path) -> Path:
    shutil.rmtree(REPO_ROOT / "build", ignore_errors=True)
    for egg_info in REPO_ROOT.glob("*.egg-info"):
        shutil.rmtree(egg_info, ignore_errors=True)
    dist_dir = tmp_path / "dist"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(dist_dir),
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    wheels = sorted(dist_dir.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, found {wheels}"
    return wheels[0]


def _read_wheel_text(wheel_path: Path, suffix: str) -> str:
    with zipfile.ZipFile(wheel_path) as zf:
        match = next(name for name in zf.namelist() if name.endswith(suffix))
        return zf.read(match).decode("utf-8")


def test_wheel_metadata_matches_plugin_manifest(tmp_path):
    wheel_path = _build_wheel(tmp_path)
    metadata = Parser().parsestr(_read_wheel_text(wheel_path, ".dist-info/METADATA"))
    entry_points = _read_wheel_text(wheel_path, ".dist-info/entry_points.txt")
    manifest = yaml.safe_load((REPO_ROOT / "daedalus" / "plugin.yaml").read_text(encoding="utf-8"))

    assert metadata["Name"] == "hermes-plugin-daedalus"
    assert metadata["Version"] == manifest["version"]
    assert metadata["Summary"] == manifest["description"]
    assert metadata["Requires-Python"] == ">=3.10"
    requires_dist = metadata.get_all("Requires-Dist") or []
    assert any(req.startswith("PyYAML") for req in requires_dist)
    assert any(req.startswith("jsonschema") for req in requires_dist)
    assert any(req.startswith("rich") for req in requires_dist)
    assert "[hermes_agent.plugins]" in entry_points
    assert "daedalus = daedalus" in entry_points


def test_wheel_contains_runtime_loaded_plugin_payload(tmp_path):
    wheel_path = _build_wheel(tmp_path)
    with zipfile.ZipFile(wheel_path) as zf:
        names = set(zf.namelist())

    expected = {
        "daedalus/runtimes/__init__.py",
        "daedalus/runtimes/codex_app_server.py",
        "daedalus/plugin.yaml",
        "daedalus/skills/operator/SKILL.md",
        "daedalus/trackers/__init__.py",
        "daedalus/trackers/linear.py",
        "daedalus/workflows/change_delivery/schema.yaml",
        "daedalus/workflows/change_delivery/workflow.template.md",
        "daedalus/workflows/change_delivery/prompts/coder.md",
        "daedalus/workflows/issue_runner/schema.yaml",
        "daedalus/workflows/issue_runner/workflow.template.md",
    }
    missing = sorted(path for path in expected if path not in names)
    assert not missing, f"wheel missing runtime payload files: {missing}"
    assert not any(name.startswith("daedalus/projects/") for name in names), (
        "wheel should not ship source-only project packs"
    )


def test_wheel_extracts_to_working_plugin_package(tmp_path):
    wheel_path = _build_wheel(tmp_path)
    site_packages = tmp_path / "site-packages"
    with zipfile.ZipFile(wheel_path) as zf:
        zf.extractall(site_packages)

    plugin_dir = site_packages / "daedalus"
    plugin = _load_module("daedalus_packaged_plugin_test", plugin_dir / "__init__.py")
    tools = _load_module("daedalus_packaged_tools_test", plugin_dir / "daedalus_cli.py")
    assert (plugin_dir / "runtimes" / "__init__.py").exists()
    assert (plugin_dir / "trackers" / "__init__.py").exists()

    calls = {
        "commands": [],
        "cli_commands": [],
        "skills": [],
    }

    class FakeCtx:
        def register_command(self, name, handler, description=""):
            calls["commands"].append((name, description, handler))

        def register_cli_command(self, **kwargs):
            calls["cli_commands"].append(kwargs)

        def register_skill(self, name, path, description=""):
            calls["skills"].append((name, Path(path), description))

    plugin.register(FakeCtx())

    command_names = {name for name, _desc, _handler in calls["commands"]}
    assert {"daedalus", "workflow"} <= command_names
    assert any(item["name"] == "daedalus" for item in calls["cli_commands"])
    assert any(name == "operator" and path.exists() for name, path, _desc in calls["skills"])

    workflow_root = tmp_path / "attmous-daedalus-issue-runner"
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
    out = tools.execute_raw_args(
        f"scaffold-workflow --workflow-root {workflow_root} --repo-path {repo} --repo-slug attmous/daedalus"
    )
    assert "daedalus error:" not in out, out
    assert (repo / "WORKFLOW.md").exists()
