"""Hot-reload of workflow.yaml (Symphony §6.2).

`ConfigWatcher.poll()` is called every tick. It mtime-checks the
workflow file; on change, reparses + validates and swaps the
`AtomicRef[ConfigSnapshot]`. On failure, the last-known-good snapshot
is kept and `daedalus.config_reload_failed` is emitted.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml
from jsonschema import Draft7Validator
from jsonschema.exceptions import ValidationError as _JSValidationError

from workflows.code_review.config_snapshot import AtomicRef, ConfigSnapshot


class ParseError(Exception):
    """Raised when workflow.yaml cannot be parsed as YAML."""


class ValidationError(Exception):
    """Raised when workflow.yaml parses but violates schema.yaml."""


_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.yaml"


def _load_schema() -> dict:
    return yaml.safe_load(_SCHEMA_PATH.read_text(encoding="utf-8"))


def parse_and_validate(workflow_yaml_path: Path) -> ConfigSnapshot:
    """Parse `workflow.yaml`, validate against `schema.yaml`, return snapshot.

    Raises:
        ParseError: yaml.YAMLError or non-dict top-level.
        ValidationError: schema validation failure.
    """
    try:
        text = workflow_yaml_path.read_text(encoding="utf-8")
        config = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ParseError(f"YAML parse error: {exc}") from exc
    if not isinstance(config, dict):
        raise ParseError(f"workflow.yaml top-level must be a mapping, got {type(config).__name__}")

    try:
        Draft7Validator(_load_schema()).validate(config)
    except _JSValidationError as exc:
        raise ValidationError(f"schema validation failed: {exc.message}") from exc

    prompts = config.get("prompts") or {}
    return ConfigSnapshot(
        config=config,
        prompts=prompts,
        loaded_at=time.monotonic(),
        source_mtime=workflow_yaml_path.stat().st_mtime,
    )


@dataclass
class ConfigWatcher:
    """mtime-polled config-reload driver. Call `.poll()` once per tick."""

    workflow_yaml_path: Path
    snapshot_ref: AtomicRef[ConfigSnapshot]
    emit_event: Callable[[str, dict], None]
    _last_key: tuple[float, int] = (0.0, 0)

    def __post_init__(self) -> None:
        snap = self.snapshot_ref.get()
        # Initialize from the loaded snapshot's mtime; size unknown at boot,
        # so a first poll will always detect a change and re-stat. That's
        # fine — re-parse on first tick is cheap and validates the on-disk
        # bytes match the snapshot we booted with.
        self._last_key = (snap.source_mtime, -1)

    def poll(self) -> None:
        """One tick of the watcher loop. Cheap when no change.

        Uses (st_mtime, st_size) as the change-detection key. mtime alone
        is insufficient on filesystems with coarse timestamp resolution
        or mtime-preserving copies (NFS, rsync -t, overlayfs).
        """
        try:
            st = self.workflow_yaml_path.stat()
        except OSError:
            return  # file vanished mid-poll (atomic rename); keep last-known-good
        key = (st.st_mtime, st.st_size)
        if key == self._last_key:
            return

        try:
            new_snapshot = parse_and_validate(self.workflow_yaml_path)
        except (ParseError, ValidationError) as exc:
            self.emit_event(
                "daedalus.config_reload_failed",
                {"error": str(exc), "mtime": st.st_mtime, "size": st.st_size},
            )
            self._last_key = key  # suppress retrying same broken bytes
            return

        self.snapshot_ref.set(new_snapshot)
        self._last_key = key
        self.emit_event(
            "daedalus.config_reloaded",
            {"loaded_at": new_snapshot.loaded_at, "source_mtime": st.st_mtime, "size": st.st_size},
        )
