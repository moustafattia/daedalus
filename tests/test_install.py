import importlib.util
from pathlib import Path


INSTALL_MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "install.py"


def load_install_module():
    spec = importlib.util.spec_from_file_location("daedalus_install", INSTALL_MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_install_into_default_hermes_home_copies_plugin_tree(tmp_path):
    install = load_install_module()
    repo_root = Path(__file__).resolve().parents[1]
    hermes_home = tmp_path / ".hermes"

    result = install.install_plugin(repo_root=repo_root, hermes_home=hermes_home)

    plugin_dir = hermes_home / "plugins" / "daedalus"
    assert result == plugin_dir
    assert (plugin_dir / "plugin.yaml").exists()
    assert (plugin_dir / "runtime.py").exists()
    assert (plugin_dir / "alerts.py").exists()
    assert (plugin_dir / "workflows" / "change_delivery" / "status.py").exists()
    assert (plugin_dir / "workflows" / "change_delivery" / "workflow.template.md").exists()
    assert not (plugin_dir / "projects").exists()
    assert (plugin_dir / "skills" / "operator" / "SKILL.md").exists()


def test_install_into_explicit_destination_uses_given_path(tmp_path):
    install = load_install_module()
    repo_root = Path(__file__).resolve().parents[1]
    target = tmp_path / "custom-plugins" / "daedalus"

    result = install.install_plugin(repo_root=repo_root, destination=target)

    assert result == target
    assert (target / "plugin.yaml").exists()
    assert (target / "tools.py").exists()
    assert (target / "workflows" / "change_delivery" / "workflow.py").exists()
    assert not (target / "projects").exists()


def test_install_replaces_legacy_symlink_destination_with_real_directory(tmp_path):
    """A legacy symlinked install is retired in place.

    The canonical install target is now a real directory at
    ``~/.hermes/plugins/daedalus``. If that path is still a symlink to an old
    workflow-local plugin tree, reinstall removes the symlink and recreates the
    global directory without mutating the old external target.
    """
    install = load_install_module()
    repo_root = Path(__file__).resolve().parents[1]
    real_plugin_dir = tmp_path / "workflow" / ".hermes" / "plugins" / "daedalus"
    real_plugin_dir.mkdir(parents=True)
    # Seed the old workflow-local target; reinstall should not follow into it.
    (real_plugin_dir / "stale.txt").write_text("stale", encoding="utf-8")

    symlink_target = tmp_path / ".hermes" / "plugins" / "daedalus"
    symlink_target.parent.mkdir(parents=True)
    symlink_target.symlink_to(real_plugin_dir)

    result = install.install_plugin(repo_root=repo_root, destination=symlink_target)

    assert result == symlink_target
    assert symlink_target.is_dir()
    assert not symlink_target.is_symlink()
    assert (symlink_target / "plugin.yaml").exists()
    # Old workflow-local target is untouched.
    assert (real_plugin_dir / "stale.txt").exists()


def test_install_replaces_existing_regular_directory(tmp_path):
    """Reinstall over an existing (non-symlink) directory wipes and rebuilds it."""
    install = load_install_module()
    repo_root = Path(__file__).resolve().parents[1]
    target = tmp_path / "plugins" / "daedalus"
    target.mkdir(parents=True)
    (target / "stale.txt").write_text("stale", encoding="utf-8")

    install.install_plugin(repo_root=repo_root, destination=target)

    assert (target / "plugin.yaml").exists()
    assert not (target / "stale.txt").exists()
