"""S-1 tests: ConfigSnapshot + AtomicRef primitives."""
from __future__ import annotations

import dataclasses

import pytest


def test_config_snapshot_is_frozen():
    from workflows.code_review.config_snapshot import ConfigSnapshot

    snap = ConfigSnapshot(
        config={"workflow": "code-review"},
        prompts={"coder": "hi"},
        loaded_at=1.0,
        source_mtime=2.0,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.config = {}  # type: ignore[misc]


def test_config_snapshot_fields():
    from workflows.code_review.config_snapshot import ConfigSnapshot

    snap = ConfigSnapshot(
        config={"k": "v"},
        prompts={"t": "p"},
        loaded_at=1.5,
        source_mtime=2.5,
    )
    assert snap.config == {"k": "v"}
    assert snap.prompts == {"t": "p"}
    assert snap.loaded_at == 1.5
    assert snap.source_mtime == 2.5
