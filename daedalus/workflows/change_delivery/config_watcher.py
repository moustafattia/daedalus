"""Hot-reload of the workflow contract (Symphony §6.2).

`ConfigWatcher.poll()` is called every tick. It mtime-checks the
workflow contract file; on change, reparses + validates and swaps the
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

from workflows.contract import WorkflowContractError, load_workflow_contract_file
from workflows.change_delivery.config_snapshot import AtomicRef, ConfigSnapshot


class ParseError(Exception):
    """Raised when the workflow contract cannot be parsed or projected."""


class ValidationError(Exception):
    """Raised when the workflow contract parses but violates schema.yaml."""


_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.yaml"


def _load_schema() -> dict:
    return yaml.safe_load(_SCHEMA_PATH.read_text(encoding="utf-8"))


def parse_and_validate(workflow_contract_path: Path) -> ConfigSnapshot:
    """Parse the workflow contract, validate against `schema.yaml`, return snapshot.

    Raises:
        ParseError: contract parse/project errors.
        ValidationError: schema validation failure.
    """
    try:
        contract = load_workflow_contract_file(workflow_contract_path)
    except WorkflowContractError as exc:
        raise ParseError(str(exc)) from exc
    config = contract.config

    try:
        Draft7Validator(_load_schema()).validate(config)
    except _JSValidationError as exc:
        raise ValidationError(f"schema validation failed: {exc.message}") from exc

    prompts = config.get("prompts") or {}
    st = contract.source_path.stat()
    return ConfigSnapshot(
        config=config,
        prompts=prompts,
        loaded_at=time.monotonic(),
        source_mtime=st.st_mtime,
        source_size=st.st_size,
    )


@dataclass
class ConfigWatcher:
    """mtime-polled config-reload driver. Call `.poll()` once per tick."""

    workflow_contract_path: Path
    snapshot_ref: AtomicRef[ConfigSnapshot]
    emit_event: Callable[[str, dict], None]
    _last_key: tuple[float, int] = (0.0, 0)

    def __post_init__(self) -> None:
        snap = self.snapshot_ref.get()
        # Codex P2 on PR #19: seed _last_key from the snapshot's recorded
        # (mtime, size), NOT the live file. If WORKFLOW.md changed between
        # bootstrap parse and watcher construction, the snapshot still holds
        # the OLD config; seeding from the LIVE file would convince poll()
        # the new bytes are "current" and the watcher would never reload
        # until the next edit. Seeding from the snapshot's recorded values
        # ensures the next poll detects the drift and reloads.
        self._last_key = (snap.source_mtime, snap.source_size)

    def poll(self) -> None:
        """One tick of the watcher loop. Cheap when no change.

        Uses (st_mtime, st_size) as the change-detection key. mtime alone
        is insufficient on filesystems with coarse timestamp resolution
        or mtime-preserving copies (NFS, rsync -t, overlayfs).
        """
        try:
            st = self.workflow_contract_path.stat()
        except OSError:
            return  # file vanished mid-poll (atomic rename); keep last-known-good
        key = (st.st_mtime, st.st_size)
        if key == self._last_key:
            return

        # Codex P1 on PR #19: catch the full set of failures parse_and_validate
        # can raise. OSError covers "file disappeared between stat() and
        # read_text()", UnicodeDecodeError covers binary content / encoding
        # mismatch, ParseError/ValidationError cover front-matter syntax + schema.
        # An uncaught exception here would propagate out of poll() and crash
        # the watcher loop instead of preserving last-known-good config.
        try:
            new_snapshot = parse_and_validate(self.workflow_contract_path)
        except (ParseError, ValidationError, OSError, UnicodeDecodeError) as exc:
            self.emit_event(
                "daedalus.config_reload_failed",
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "mtime": st.st_mtime,
                    "size": st.st_size,
                },
            )
            self._last_key = key  # suppress retrying same broken bytes
            return

        self.snapshot_ref.set(new_snapshot)
        self._last_key = key
        self.emit_event(
            "daedalus.config_reloaded",
            {"loaded_at": new_snapshot.loaded_at, "source_mtime": st.st_mtime, "size": st.st_size},
        )
