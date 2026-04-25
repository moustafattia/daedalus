import importlib.util
from pathlib import Path

import pytest


MIGRATION_MODULE_PATH = Path(__file__).resolve().parents[1] / "migration.py"


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
