"""Filesystem migration for the Daedalus rebrand.

Renames relay-era files to daedalus paths in a workflow root. Idempotent
and conservative: if a new-named file already exists, the matching old
file is left untouched (operator must inspect manually).
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable


# Mirrors workflows.code_review.paths._has_project_runtime_layout. Inlined
# here so migration.py has no project-package dependency (it's loaded via
# spec_from_file_location at runtime startup before sys.path includes the
# workflows package).
_PROJECT_RUNTIME_LAYOUT_MARKERS = ("runtime", "config", "workspace", "docs")


def _runtime_base_dir(workflow_root: Path) -> Path:
    """Resolve the directory under which state/ and memory/ live.

    Mirrors workflows.code_review.paths.runtime_base_dir: when the workflow
    root has any of the project-runtime layout markers (runtime/, config/,
    workspace/, docs/), state and memory are stored under
    ``<workflow_root>/runtime/``; otherwise they're at the top level.
    """
    root = workflow_root.resolve()
    if any((root / name).exists() for name in _PROJECT_RUNTIME_LAYOUT_MARKERS):
        return root / "runtime"
    return root


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


def _rename_db_triplet(
    *,
    old_dir: Path,
    new_dir: Path,
    old_stem: str,
    new_stem: str,
) -> list[str]:
    """Atomic rename of a SQLite DB + its WAL/SHM sidecars.

    SQLite WAL mode requires the sidecar filenames to track the main
    DB filename. Moving them independently can produce a corrupt
    triplet (e.g. main DB unchanged, WAL moved to a different name)
    so we treat them as a unit: skip the entire group if the new
    main DB already exists or the old main DB is missing.
    """
    main_old = old_dir / f"{old_stem}.db"
    main_new = new_dir / f"{new_stem}.db"
    # Conflict: new main DB already exists. Leave the entire triplet
    # untouched so an operator can inspect manually.
    if main_new.exists():
        return []
    # No old DB: nothing to migrate (orphan WAL/SHM ignored — they're
    # meaningless without a main DB).
    if not main_old.exists():
        return []
    descriptions: list[str] = []
    for suffix in (".db", ".db-wal", ".db-shm"):
        desc = _rename_if_only_old_exists(
            old_dir / f"{old_stem}{suffix}",
            new_dir / f"{new_stem}{suffix}",
        )
        if desc:
            descriptions.append(desc)
    return descriptions


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
    # Both old (relay) and new (daedalus) paths live under the same base.
    # For project-runtime layouts (runtime/ subdir present), that's
    # <workflow_root>/runtime/; for legacy top-level layouts, it's
    # <workflow_root> itself. Matches paths.runtime_paths() resolution
    # so the migrator never strands data at a layout the runtime won't
    # subsequently look at.
    base_dir = _runtime_base_dir(base)
    descriptions: list[str] = []

    # SQLite DB triplet: main file + WAL + SHM. SQLite WAL mode requires
    # the sidecar filenames to match the main DB filename, so we move all
    # three together as a unit.
    old_state_dir = base_dir / "state" / "relay"
    new_state_dir = base_dir / "state" / "daedalus"
    descriptions.extend(
        _rename_db_triplet(
            old_dir=old_state_dir,
            new_dir=new_state_dir,
            old_stem="relay",
            new_stem="daedalus",
        )
    )

    # Event log and alert state files (single-file moves)
    memory_pairs: Iterable[tuple[Path, Path]] = (
        (base_dir / "memory" / "relay-events.jsonl", base_dir / "memory" / "daedalus-events.jsonl"),
        (
            base_dir / "memory" / "hermes-relay-alert-state.json",
            base_dir / "memory" / "daedalus-alert-state.json",
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
