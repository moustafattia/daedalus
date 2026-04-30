"""S-1 tests: ConfigSnapshot + AtomicRef primitives."""
from __future__ import annotations

import dataclasses

import pytest


def test_config_snapshot_is_frozen():
    from workflows.change_delivery.config_snapshot import ConfigSnapshot

    snap = ConfigSnapshot(
        config={"workflow": "change-delivery"},
        prompts={"coder": "hi"},
        loaded_at=1.0,
        source_mtime=2.0,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.config = {}  # type: ignore[misc]


def test_config_snapshot_fields():
    from workflows.change_delivery.config_snapshot import ConfigSnapshot

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


def test_atomic_ref_get_set_roundtrip():
    from workflows.change_delivery.config_snapshot import AtomicRef

    ref: AtomicRef[int] = AtomicRef(0)
    assert ref.get() == 0
    ref.set(7)
    assert ref.get() == 7
    ref.set(42)
    assert ref.get() == 42


def test_atomic_ref_swap_returns_old_value():
    from workflows.change_delivery.config_snapshot import AtomicRef

    ref: AtomicRef[str] = AtomicRef("a")
    old = ref.swap("b")
    assert old == "a"
    assert ref.get() == "b"


def test_atomic_ref_holds_config_snapshot():
    from workflows.change_delivery.config_snapshot import AtomicRef, ConfigSnapshot

    s1 = ConfigSnapshot(config={"v": 1}, prompts={}, loaded_at=1.0, source_mtime=1.0)
    s2 = ConfigSnapshot(config={"v": 2}, prompts={}, loaded_at=2.0, source_mtime=2.0)
    ref: AtomicRef[ConfigSnapshot] = AtomicRef(s1)
    assert ref.get() is s1
    ref.set(s2)
    assert ref.get() is s2
    assert ref.get().config == {"v": 2}


def test_config_snapshot_inner_dicts_are_read_only():
    """Code-quality P2 fix: dict contents wrapped in MappingProxyType so
    accidental mutation surfaces as TypeError at the boundary."""
    from workflows.change_delivery.config_snapshot import ConfigSnapshot

    snap = ConfigSnapshot(
        config={"k": "v"},
        prompts={"t": "p"},
        loaded_at=1.0,
        source_mtime=1.0,
    )
    with pytest.raises(TypeError):
        snap.config["k"] = "mutated"  # type: ignore[index]
    with pytest.raises(TypeError):
        snap.prompts["t"] = "mutated"  # type: ignore[index]
    # Reads still work
    assert snap.config["k"] == "v"
    assert snap.prompts["t"] == "p"


def test_atomic_ref_concurrent_readers_and_writer_consistent():
    """N reader threads + 1 writer thread; readers always see one of
    the values the writer set, never a torn read.

    Note: under CPython the GIL makes a single-attribute load atomic,
    so this test cannot directly exercise a torn-read window. It is a
    smoke / liveness check that the lock scope on the writer side
    doesn't deadlock and that readers observe published values rather
    than a stale stuck reference. A true torn-read regression would
    only surface on free-threaded 3.13+ or PyPy without GIL — out of
    scope for Daedalus today.
    """
    import threading
    import time
    from workflows.change_delivery.config_snapshot import AtomicRef

    valid_values = {0, 1, 2, 3, 4}
    ref: AtomicRef[int] = AtomicRef(0)
    stop = threading.Event()
    seen_bad: list[int] = []

    def reader() -> None:
        while not stop.is_set():
            v = ref.get()
            if v not in valid_values:
                seen_bad.append(v)

    def writer() -> None:
        for v in (1, 2, 3, 4, 1, 2, 3, 4):
            ref.set(v)
            time.sleep(0.001)

    readers = [threading.Thread(target=reader) for _ in range(4)]
    for t in readers:
        t.start()
    w = threading.Thread(target=writer)
    w.start()
    w.join()
    stop.set()
    for t in readers:
        t.join()

    assert seen_bad == []
    assert ref.get() in valid_values
