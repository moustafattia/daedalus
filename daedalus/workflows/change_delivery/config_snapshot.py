"""Immutable config snapshot + atomic reference wrapper.

Symphony §6.2 (hot-reload) and §13.7 (HTTP server) require multiple
threads to read the parsed workflow config concurrently while a single
writer thread (the config watcher) swaps in a freshly-parsed snapshot.

`ConfigSnapshot` is a frozen dataclass — readers can safely cache its
fields. The frozen wrapper does not deep-freeze the `config` / `prompts`
dicts; the immutability contract is that **callers must treat their
contents as read-only and writers must build a fresh `ConfigSnapshot`
with new dict values rather than mutating the existing ones**. The
constructor wraps both dicts in `types.MappingProxyType` to make
accidental mutation surface as a `TypeError` at the boundary.

`AtomicRef[T]` is a `threading.Lock`-backed reference cell with
`get()` / `set()` / `swap()` semantics. Reads are lock-free under
CPython (a single attribute load is atomic via the GIL); the lock
only guards the pointer-swap on the writer side. This keeps HTTP /
tick-loop reader hotpaths uncontended.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Generic, Mapping, TypeVar


def _freeze_mapping(value: Any) -> Mapping[str, Any]:
    """Wrap a dict in a read-only view; pass through if already a Mapping view."""
    if isinstance(value, MappingProxyType):
        return value
    if isinstance(value, dict):
        return MappingProxyType(value)
    # Allow any Mapping subclass — caller's responsibility to keep it stable.
    return value


@dataclass(frozen=True)
class ConfigSnapshot:
    """Immutable parsed-config + prompt-template view.

    Atomic swap via `AtomicRef[ConfigSnapshot].set(new_snapshot)`.

    Equality compares by dict contents (default frozen-dataclass `__eq__`),
    which means two snapshots with structurally-equal configs but distinct
    `MappingProxyType` wrappers compare equal — usually what you want.
    Snapshots are NOT hashable because the underlying dicts are mutable.
    """

    config: Mapping[str, Any]
    prompts: Mapping[str, Any]
    loaded_at: float
    source_mtime: float
    # Codex P2 on PR #19: track on-disk size at snapshot-construction time so
    # ConfigWatcher can seed its (mtime, size) change-detection key from the
    # snapshot without re-stat()ing the live file. Defaults to -1 (sentinel)
    # for back-compat with existing call sites that don't supply it.
    source_size: int = -1

    def __post_init__(self) -> None:
        # Wrap incoming dicts in read-only views so callers cannot mutate
        # them after a snapshot is sealed. object.__setattr__ is the
        # documented pattern for frozen-dataclass post-init mutation.
        object.__setattr__(self, "config", _freeze_mapping(self.config))
        object.__setattr__(self, "prompts", _freeze_mapping(self.prompts))


T = TypeVar("T")


class AtomicRef(Generic[T]):
    """Lock-protected single-value reference cell.

    Used to pass `ConfigSnapshot` between the watcher thread (writer)
    and the tick / HTTP threads (readers). Readers do NOT take the
    lock — under CPython a single attribute load is atomic, so reading
    `self._value` returns either the pre- or post-swap reference but
    never a torn intermediate. Writers (`set` / `swap`) take the lock
    so concurrent writers serialize cleanly.

    This intentionally keeps HTTP and tick-loop reader hotpaths
    uncontended; the writer side is rare (config-reload tick).
    """

    def __init__(self, initial: T) -> None:
        self._lock = threading.Lock()
        self._value: T = initial

    def get(self) -> T:
        # No lock — see class docstring. CPython's GIL makes the attribute
        # load atomic; the only torn-read risk is on free-threaded CPython
        # 3.13+ or PyPy without GIL, neither of which Daedalus targets.
        return self._value

    def set(self, new_value: T) -> None:
        with self._lock:
            self._value = new_value

    def swap(self, new_value: T) -> T:
        """Set new value and return the previous value atomically."""
        with self._lock:
            old = self._value
            self._value = new_value
            return old
