"""Immutable config snapshot + atomic reference wrapper.

Symphony §6.2 (hot-reload) and §13.7 (HTTP server) require multiple
threads to read the parsed workflow config concurrently while a single
writer thread (the config watcher) swaps in a freshly-parsed snapshot.

`ConfigSnapshot` is a frozen dataclass — readers can safely cache its
fields. `AtomicRef[T]` is a `threading.Lock`-backed reference wrapper
with `get()` / `set()` / `swap()` semantics.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Generic, TypeVar


@dataclass(frozen=True)
class ConfigSnapshot:
    """Immutable parsed-config + prompt-template view.

    Atomic swap via `AtomicRef[ConfigSnapshot].set(new_snapshot)`.
    """

    config: dict
    prompts: dict
    loaded_at: float
    source_mtime: float


T = TypeVar("T")


class AtomicRef(Generic[T]):
    """Lock-protected single-value reference cell.

    Used to pass `ConfigSnapshot` between the watcher thread (writer)
    and the tick / HTTP threads (readers). The lock is held only for
    the pointer swap; readers receive an immutable snapshot and never
    contend on the data inside.
    """

    def __init__(self, initial: T) -> None:
        self._lock = threading.Lock()
        self._value: T = initial

    def get(self) -> T:
        with self._lock:
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
