"""Filesystem migration for the Daedalus rebrand.

Renames relay-era files to daedalus paths in a workflow root. Idempotent
and conservative: if a new-named file already exists, the matching old
file is left untouched (operator must inspect manually).
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable


def _rename_if_only_old_exists(old: Path, new: Path) -> str | None:
    """Rename old → new only if old exists and new does not.

    Returns a human-readable description of the rename, or None if no
    action was taken.
    """
    if not old.exists():
        return None
    if new.exists():
        return None
    new.parent.mkdir(parents=True, exist_ok=True)
    old.rename(new)
    return f"renamed {old} -> {new}"


def migrate_filesystem_state(workflow_root: Path) -> list[str]:
    """Idempotent rename of relay-era paths to daedalus paths.

    Handles:
    - state/relay/relay.db (and SQLite WAL/SHM sidecars) -> state/daedalus/daedalus.db
    - memory/relay-events.jsonl -> memory/daedalus-events.jsonl
    - memory/hermes-relay-alert-state.json -> memory/daedalus-alert-state.json

    Removes the old state/relay/ directory if it ends up empty after
    the move.

    Returns a list of human-readable descriptions of renames performed.
    Empty list means no migration was needed (already in new shape, or
    workflow root has no relay-era data to migrate).
    """
    base = Path(workflow_root)
    descriptions: list[str] = []

    # SQLite DB triplet: main file + WAL + SHM. SQLite WAL mode requires
    # the sidecar filenames to match the main DB filename, so we move all
    # three together.
    old_state_dir = base / "state" / "relay"
    new_state_dir = base / "state" / "daedalus"
    sqlite_pairs: Iterable[tuple[Path, Path]] = (
        (old_state_dir / "relay.db", new_state_dir / "daedalus.db"),
        (old_state_dir / "relay.db-wal", new_state_dir / "daedalus.db-wal"),
        (old_state_dir / "relay.db-shm", new_state_dir / "daedalus.db-shm"),
    )
    for old, new in sqlite_pairs:
        desc = _rename_if_only_old_exists(old, new)
        if desc:
            descriptions.append(desc)

    # Event log and alert state files (single-file moves)
    memory_pairs: Iterable[tuple[Path, Path]] = (
        (base / "memory" / "relay-events.jsonl", base / "memory" / "daedalus-events.jsonl"),
        (
            base / "memory" / "hermes-relay-alert-state.json",
            base / "memory" / "daedalus-alert-state.json",
        ),
    )
    for old, new in memory_pairs:
        desc = _rename_if_only_old_exists(old, new)
        if desc:
            descriptions.append(desc)

    # If state/relay/ ended up empty, remove it
    if old_state_dir.exists() and old_state_dir.is_dir():
        try:
            next(old_state_dir.iterdir())
        except StopIteration:
            old_state_dir.rmdir()

    return descriptions
