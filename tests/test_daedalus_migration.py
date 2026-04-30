import importlib.util
from pathlib import Path


MIGRATION_MODULE_PATH = Path(__file__).resolve().parents[1] / "daedalus" / "migration.py"


def load_migration_module():
    spec = importlib.util.spec_from_file_location("daedalus_migration_test", MIGRATION_MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migrate_filesystem_state_no_op_on_clean_workflow_root(tmp_path):
    migration = load_migration_module()
    result = migration.migrate_filesystem_state(tmp_path)
    assert result == []


def test_migrate_filesystem_state_renames_db_and_sidecars(tmp_path):
    migration = load_migration_module()
    old_dir = tmp_path / "state" / "relay"
    old_dir.mkdir(parents=True)
    (old_dir / "relay.db").write_bytes(b"sqlite-data")
    (old_dir / "relay.db-wal").write_bytes(b"wal-data")
    (old_dir / "relay.db-shm").write_bytes(b"shm-data")

    result = migration.migrate_filesystem_state(tmp_path)

    new_dir = tmp_path / "state" / "daedalus"
    assert (new_dir / "daedalus.db").read_bytes() == b"sqlite-data"
    assert (new_dir / "daedalus.db-wal").read_bytes() == b"wal-data"
    assert (new_dir / "daedalus.db-shm").read_bytes() == b"shm-data"
    assert not (old_dir / "relay.db").exists()
    assert not (old_dir / "relay.db-wal").exists()
    assert not (old_dir / "relay.db-shm").exists()
    # Old empty dir gets removed
    assert not old_dir.exists()
    # Returns descriptions of what happened
    assert any("relay.db" in line and "daedalus.db" in line for line in result)


def test_migrate_filesystem_state_renames_event_log(tmp_path):
    migration = load_migration_module()
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "relay-events.jsonl").write_text("event-1\nevent-2\n", encoding="utf-8")

    result = migration.migrate_filesystem_state(tmp_path)

    assert (memory_dir / "daedalus-events.jsonl").read_text(encoding="utf-8") == "event-1\nevent-2\n"
    assert not (memory_dir / "relay-events.jsonl").exists()
    assert any("relay-events.jsonl" in line for line in result)


def test_migrate_filesystem_state_renames_alert_state(tmp_path):
    migration = load_migration_module()
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "hermes-relay-alert-state.json").write_text('{"k":"v"}', encoding="utf-8")

    result = migration.migrate_filesystem_state(tmp_path)

    assert (memory_dir / "daedalus-alert-state.json").read_text(encoding="utf-8") == '{"k":"v"}'
    assert not (memory_dir / "hermes-relay-alert-state.json").exists()
    assert any("hermes-relay-alert-state.json" in line for line in result)


def test_migrate_filesystem_state_idempotent_when_already_migrated(tmp_path):
    migration = load_migration_module()
    new_db_dir = tmp_path / "state" / "daedalus"
    new_db_dir.mkdir(parents=True)
    (new_db_dir / "daedalus.db").write_bytes(b"already-here")
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "daedalus-events.jsonl").write_text("kept", encoding="utf-8")

    result = migration.migrate_filesystem_state(tmp_path)

    # No move attempted because new files already exist; old files don't either
    assert result == []
    assert (new_db_dir / "daedalus.db").read_bytes() == b"already-here"


def test_migrate_filesystem_state_skips_old_when_new_present(tmp_path):
    """If both old and new exist, leave both untouched (manual operator
    inspection required). Conservative: never overwrite existing new files."""
    migration = load_migration_module()
    old_dir = tmp_path / "state" / "relay"
    old_dir.mkdir(parents=True)
    (old_dir / "relay.db").write_bytes(b"old-data")
    new_dir = tmp_path / "state" / "daedalus"
    new_dir.mkdir(parents=True)
    (new_dir / "daedalus.db").write_bytes(b"new-data")

    result = migration.migrate_filesystem_state(tmp_path)

    # No move; both files preserved as-is
    assert (old_dir / "relay.db").read_bytes() == b"old-data"
    assert (new_dir / "daedalus.db").read_bytes() == b"new-data"
    assert result == []


def test_migrate_filesystem_state_keeps_orphan_wal_when_new_db_already_exists(tmp_path):
    """Triplet atomicity: if new main DB already exists, do NOT move
    the old WAL/SHM sidecars (they belong to a different DB)."""
    migration = load_migration_module()
    old_dir = tmp_path / "state" / "relay"
    old_dir.mkdir(parents=True)
    (old_dir / "relay.db").write_bytes(b"old-db")
    (old_dir / "relay.db-wal").write_bytes(b"old-wal")
    (old_dir / "relay.db-shm").write_bytes(b"old-shm")
    new_dir = tmp_path / "state" / "daedalus"
    new_dir.mkdir(parents=True)
    (new_dir / "daedalus.db").write_bytes(b"new-db")

    result = migration.migrate_filesystem_state(tmp_path)

    # Old triplet preserved entirely
    assert (old_dir / "relay.db").read_bytes() == b"old-db"
    assert (old_dir / "relay.db-wal").read_bytes() == b"old-wal"
    assert (old_dir / "relay.db-shm").read_bytes() == b"old-shm"
    # New main DB unchanged; no orphan WAL/SHM appeared in the new dir
    assert (new_dir / "daedalus.db").read_bytes() == b"new-db"
    assert not (new_dir / "daedalus.db-wal").exists()
    assert not (new_dir / "daedalus.db-shm").exists()
    # No work reported
    assert result == []


def test_migrate_filesystem_state_preserves_old_dir_when_unknown_files_present(tmp_path):
    """Cleanup is conservative: never rmdir a non-empty old state/relay/."""
    migration = load_migration_module()
    old_dir = tmp_path / "state" / "relay"
    old_dir.mkdir(parents=True)
    (old_dir / "relay.db").write_bytes(b"db")
    (old_dir / "operator-notes.txt").write_text("manual artifact", encoding="utf-8")

    migration.migrate_filesystem_state(tmp_path)

    assert old_dir.exists()
    assert (old_dir / "operator-notes.txt").read_text(encoding="utf-8") == "manual artifact"


def test_cli_migrate_filesystem_invokes_migrator(tmp_path, monkeypatch):
    """Smoke test: `daedalus migrate-filesystem --workflow-root <path>`
    invokes migrate_filesystem_state and prints the result."""
    import importlib.util
    tools_path = Path(__file__).resolve().parents[1] / "daedalus" / "daedalus_cli.py"
    spec = importlib.util.spec_from_file_location("daedalus_tools_for_migrate_test", tools_path)
    tools = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tools)

    # Seed an old-shape workflow
    old_dir = tmp_path / "state" / "relay"
    old_dir.mkdir(parents=True)
    (old_dir / "relay.db").write_bytes(b"data")

    result = tools.execute_raw_args(f"migrate-filesystem --workflow-root {tmp_path}")

    # Should not be an error, and should report the migration
    assert "daedalus error" not in result.lower()
    assert "renamed" in result.lower() or "migrated" in result.lower()
    assert (tmp_path / "state" / "daedalus" / "daedalus.db").exists()


def test_migrate_filesystem_state_uses_project_runtime_layout_when_present(tmp_path):
    """FU-2: when the workflow root has runtime/ (or any other project-
    runtime layout marker), the migrator should look for legacy data under
    runtime/ and put new data there too — matching paths.runtime_paths()
    resolution. Without this, the migrator would strand data at top-level
    state/daedalus/ while the runtime would look for it under runtime/."""
    migration = load_migration_module()

    # Create a project-runtime layout: presence of any of (runtime, config,
    # workspace, docs) flips the layout. Use runtime/ here since it's the
    # most common marker in real workspaces.
    (tmp_path / "runtime").mkdir()

    # Seed legacy data under the project-runtime layout
    old_dir = tmp_path / "runtime" / "state" / "relay"
    old_dir.mkdir(parents=True)
    (old_dir / "relay.db").write_bytes(b"sqlite-data")
    memory_dir = tmp_path / "runtime" / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "relay-events.jsonl").write_text("event-1\n", encoding="utf-8")
    (memory_dir / "hermes-relay-alert-state.json").write_text('{"k":"v"}', encoding="utf-8")

    result = migration.migrate_filesystem_state(tmp_path)

    # New data lives under runtime/, NOT at top-level
    assert (tmp_path / "runtime" / "state" / "daedalus" / "daedalus.db").read_bytes() == b"sqlite-data"
    assert (tmp_path / "runtime" / "memory" / "daedalus-events.jsonl").read_text(encoding="utf-8") == "event-1\n"
    assert (tmp_path / "runtime" / "memory" / "daedalus-alert-state.json").read_text(encoding="utf-8") == '{"k":"v"}'
    # Top-level paths should NOT have been created
    assert not (tmp_path / "state" / "daedalus" / "daedalus.db").exists()
    # Old paths under runtime/ are gone
    assert not (old_dir / "relay.db").exists()
    # Result reports the renames
    assert any("relay.db" in line for line in result)
    assert any("relay-events.jsonl" in line for line in result)


def test_migrate_filesystem_state_top_level_layout_unchanged_without_markers(tmp_path):
    """When no project-runtime layout markers exist, migrator stays at
    top-level (back-compat with legacy workspaces). Pin the existing
    behavior so a future refactor doesn't accidentally always use runtime/."""
    migration = load_migration_module()

    # No runtime/, config/, workspace/, docs/ — pure legacy layout
    old_dir = tmp_path / "state" / "relay"
    old_dir.mkdir(parents=True)
    (old_dir / "relay.db").write_bytes(b"sqlite-data")

    migration.migrate_filesystem_state(tmp_path)

    # New data at top-level
    assert (tmp_path / "state" / "daedalus" / "daedalus.db").read_bytes() == b"sqlite-data"
    # NOT under runtime/
    assert not (tmp_path / "runtime").exists()
