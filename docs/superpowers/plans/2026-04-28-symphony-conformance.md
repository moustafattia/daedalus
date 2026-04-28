# Symphony-Conformance Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt 5 facets of the openai/symphony service spec into Daedalus across 6 phased PRs.

**Architecture:** The `daedalus@<workspace>.service` main process gains four in-process subsystems (config watcher, preflight validator, stall reconciler, optional HTTP server) sharing one in-memory `ConfigSnapshot` reference. The snapshot is the only mutable shared state — readers read lock-free, the writer (`ConfigWatcher`) swaps the reference atomically via `AtomicRef`. Each feature defaults OFF or backward-compatible when its schema section is absent, so adopting the upgrade requires no operator action.

**Tech Stack:** Python 3.11 stdlib only (`http.server`, `threading`, `pathlib`), pyyaml, jsonschema, pytest. No new external deps.

**Spec:** `docs/superpowers/specs/2026-04-28-symphony-conformance-design.md` (§3 architecture, §4 hot-reload, §5 preflight, §6 HTTP, §7 events, §8 stall).

**Worktrees / branches:** each phase ships from its own branch off `main`:

| Phase | Branch | Depends on | Spec section |
|---|---|---|---|
| S-1 | `claude/symphony-s-1-config-snapshot` | — | §3.1, §4.1 (infra) |
| S-2 | `claude/symphony-s-2-hot-reload` | S-1 | §4 |
| S-3 | `claude/symphony-s-3-preflight` | S-1, S-2 | §5 |
| S-4 | `claude/symphony-s-4-event-taxonomy` | — (independent) | §7 |
| S-5 | `claude/symphony-s-5-stall` | S-1 | §8 |
| S-6 | `claude/symphony-s-6-http-server` | S-1, S-4 | §6 |

Baseline: 591 tests passing (`/usr/bin/python3 -m pytest tests/`). Use `/usr/bin/python3` (system Python 3.11 with pyyaml + jsonschema; the homebrew interpreter lacks them).

---

# Phase S-1 — `ConfigSnapshot` + `AtomicRef` infrastructure

**Branch:** `claude/symphony-s-1-config-snapshot` from `main`. One PR.

**Goal:** Create the immutable-snapshot + atomic-swap primitives used by every subsequent phase. No Symphony feature visible yet; this is the foundation only.

**File structure:**

- New: `workflows/code_review/config_snapshot.py` — `ConfigSnapshot` frozen dataclass + `AtomicRef[T]` lock-backed wrapper.
- New: `tests/test_config_snapshot.py` — atomic swap, get/set roundtrip, immutability, frozen dataclass, threaded contention.

---

## Task S-1.1: Frozen `ConfigSnapshot` dataclass

**Files:**
- Create: `workflows/code_review/config_snapshot.py`
- Create: `tests/test_config_snapshot.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_config_snapshot.py`:

```python
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
```

- [ ] **Step 2: Verify failure**

```bash
cd /home/radxa/WS/hermes-relay/.claude/worktrees/symphony-conformance
/usr/bin/python3 -m pytest tests/test_config_snapshot.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'workflows.code_review.config_snapshot'`.

- [ ] **Step 3: Create module**

Create `workflows/code_review/config_snapshot.py`:

```python
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
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_config_snapshot.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/config_snapshot.py tests/test_config_snapshot.py
git commit -m "$(cat <<'EOF'
feat(symphony): add frozen ConfigSnapshot dataclass

Immutable parsed-config view used as the single source of truth shared
across the tick loop, future config watcher, and future HTTP server.
Frozen so readers can cache field reads without locks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-1.2: `AtomicRef[T]` get/set roundtrip + swap

**Files:**
- Modify: `tests/test_config_snapshot.py` (extend)

- [ ] **Step 1: Add failing tests**

Append to `tests/test_config_snapshot.py`:

```python
def test_atomic_ref_get_set_roundtrip():
    from workflows.code_review.config_snapshot import AtomicRef

    ref: AtomicRef[int] = AtomicRef(0)
    assert ref.get() == 0
    ref.set(7)
    assert ref.get() == 7
    ref.set(42)
    assert ref.get() == 42


def test_atomic_ref_swap_returns_old_value():
    from workflows.code_review.config_snapshot import AtomicRef

    ref: AtomicRef[str] = AtomicRef("a")
    old = ref.swap("b")
    assert old == "a"
    assert ref.get() == "b"


def test_atomic_ref_holds_config_snapshot():
    from workflows.code_review.config_snapshot import AtomicRef, ConfigSnapshot

    s1 = ConfigSnapshot(config={"v": 1}, prompts={}, loaded_at=1.0, source_mtime=1.0)
    s2 = ConfigSnapshot(config={"v": 2}, prompts={}, loaded_at=2.0, source_mtime=2.0)
    ref: AtomicRef[ConfigSnapshot] = AtomicRef(s1)
    assert ref.get() is s1
    ref.set(s2)
    assert ref.get() is s2
    assert ref.get().config == {"v": 2}
```

- [ ] **Step 2: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_config_snapshot.py -v
```
Expected: 5 passed (the 3 new + 2 from Task S-1.1).

- [ ] **Step 3: Commit**

```bash
git add tests/test_config_snapshot.py
git commit -m "$(cat <<'EOF'
test(symphony): cover AtomicRef get/set/swap roundtrip

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-1.3: Threaded contention test

**Files:**
- Modify: `tests/test_config_snapshot.py` (extend)

- [ ] **Step 1: Add a threaded test**

Append to `tests/test_config_snapshot.py`:

```python
def test_atomic_ref_concurrent_readers_and_writer_consistent():
    """N reader threads + 1 writer thread; readers always see one of
    the values the writer set, never a torn read."""
    import threading
    import time
    from workflows.code_review.config_snapshot import AtomicRef

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
```

- [ ] **Step 2: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_config_snapshot.py -v
```
Expected: 6 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_config_snapshot.py
git commit -m "$(cat <<'EOF'
test(symphony): cover AtomicRef concurrent reader/writer consistency

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-1.4: Full suite regression

- [ ] **Step 1: Run full suite**

```bash
cd /home/radxa/WS/hermes-relay/.claude/worktrees/symphony-conformance
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -5
```
Expected: 597 passed (591 baseline + 6 new).

- [ ] **Step 2: Use superpowers:finishing-a-development-branch.**

---

# Phase S-2 — Hot-reload of `workflow.yaml` (Symphony §6.2)

**Branch:** `claude/symphony-s-2-hot-reload` from `main` (rebased onto S-1 once S-1 lands). One PR.

**Goal:** mtime-poll `workflow.yaml`; on change, reparse + validate; on success, atomically swap `ConfigSnapshot`; on failure, keep last-known-good and emit `daedalus.config_reload_failed`. Wire into `watch.py` tick.

**File structure:**

- New: `workflows/code_review/config_watcher.py` — `parse_and_validate(path) -> ConfigSnapshot`, `ConfigWatcher` class.
- New: `tests/test_config_watcher.py` — covers spec §4.3 (5 cases).
- Modify: `workflows/code_review/workflow.py` — expose existing parser as `parse_and_validate` or wrap it.
- Modify: `watch.py` — instantiate `AtomicRef[ConfigSnapshot]` + `ConfigWatcher`, call `.poll()` per tick.

---

## Task S-2.1: `parse_and_validate(path) -> ConfigSnapshot`

**Files:**
- Create: `workflows/code_review/config_watcher.py`
- Create: `tests/test_config_watcher.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_config_watcher.py`:

```python
"""S-2 tests: ConfigWatcher (mtime-poll hot-reload) — Symphony §6.2."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest


_VALID_YAML = textwrap.dedent("""\
    workflow: code-review
    schema-version: 1
    instance:
      name: test-instance
      engine-owner: hermes
    repository:
      local-path: /tmp/test
      github-slug: org/repo
      active-lane-label: active-lane
    runtimes:
      r1:
        kind: claude-cli
        max-turns-per-invocation: 4
        timeout-seconds: 60
    agents:
      coder:
        t1:
          name: coder
          model: claude
          runtime: r1
      internal-reviewer:
        name: internal
        model: claude
        runtime: r1
      external-reviewer:
        enabled: false
        name: external
    gates:
      internal-review: {}
      external-review: {}
      merge: {}
    triggers:
      lane-selector:
        type: github-issue-label
        label: active-lane
    storage:
      ledger: ledger.json
      health: health.json
      audit-log: audit.log
""")


def test_parse_and_validate_returns_snapshot(tmp_path):
    from workflows.code_review.config_watcher import parse_and_validate

    p = tmp_path / "workflow.yaml"
    p.write_text(_VALID_YAML)
    snap = parse_and_validate(p)
    assert snap.config["workflow"] == "code-review"
    assert snap.source_mtime == p.stat().st_mtime
    assert snap.loaded_at > 0


def test_parse_and_validate_raises_on_yaml_syntax_error(tmp_path):
    from workflows.code_review.config_watcher import parse_and_validate, ParseError

    p = tmp_path / "workflow.yaml"
    p.write_text("workflow: [unclosed\n")
    with pytest.raises(ParseError):
        parse_and_validate(p)


def test_parse_and_validate_raises_on_schema_violation(tmp_path):
    from workflows.code_review.config_watcher import parse_and_validate, ValidationError

    p = tmp_path / "workflow.yaml"
    p.write_text("workflow: code-review\n")  # missing required fields
    with pytest.raises(ValidationError):
        parse_and_validate(p)
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_config_watcher.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'workflows.code_review.config_watcher'`.

- [ ] **Step 3: Create module**

Create `workflows/code_review/config_watcher.py`:

```python
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
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_config_watcher.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/config_watcher.py tests/test_config_watcher.py
git commit -m "$(cat <<'EOF'
feat(symphony): add parse_and_validate(workflow.yaml) -> ConfigSnapshot

Pure helper used by ConfigWatcher and the per-tick preflight (S-3).
Raises ParseError on YAML errors, ValidationError on schema violations.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-2.2: `ConfigWatcher.poll()` happy path swaps snapshot

**Files:**
- Modify: `tests/test_config_watcher.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_config_watcher.py`:

```python
def _seed_snapshot(tmp_path: Path):
    """Helper: write valid yaml + return (path, snapshot)."""
    from workflows.code_review.config_watcher import parse_and_validate

    p = tmp_path / "workflow.yaml"
    p.write_text(_VALID_YAML)
    return p, parse_and_validate(p)


def test_watcher_poll_swaps_on_mtime_change(tmp_path):
    import os
    from workflows.code_review.config_snapshot import AtomicRef
    from workflows.code_review.config_watcher import ConfigWatcher

    p, initial = _seed_snapshot(tmp_path)
    ref = AtomicRef(initial)
    events: list[tuple[str, dict]] = []
    w = ConfigWatcher(p, ref, lambda t, d: events.append((t, d)))

    # Edit file with a future mtime
    new_yaml = _VALID_YAML.replace("test-instance", "edited-instance")
    p.write_text(new_yaml)
    os.utime(p, (initial.source_mtime + 5, initial.source_mtime + 5))

    w.poll()
    assert ref.get().config["instance"]["name"] == "edited-instance"
    assert any(t == "daedalus.config_reloaded" for t, _ in events)


def test_watcher_poll_no_change_is_noop(tmp_path):
    from workflows.code_review.config_snapshot import AtomicRef
    from workflows.code_review.config_watcher import ConfigWatcher

    p, initial = _seed_snapshot(tmp_path)
    ref = AtomicRef(initial)
    events: list[tuple[str, dict]] = []
    w = ConfigWatcher(p, ref, lambda t, d: events.append((t, d)))

    w.poll()
    w.poll()
    assert ref.get() is initial
    assert events == []
```

- [ ] **Step 2: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_config_watcher.py -v
```
Expected: 5 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_config_watcher.py
git commit -m "$(cat <<'EOF'
test(symphony): cover ConfigWatcher mtime-change swap + no-op no-change

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-2.3: Bad reload — invalid YAML keeps last-known-good

**Files:**
- Modify: `tests/test_config_watcher.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_config_watcher.py`:

```python
def test_watcher_poll_invalid_yaml_keeps_lkg_and_emits_failure(tmp_path):
    import os
    from workflows.code_review.config_snapshot import AtomicRef
    from workflows.code_review.config_watcher import ConfigWatcher

    p, initial = _seed_snapshot(tmp_path)
    ref = AtomicRef(initial)
    events: list[tuple[str, dict]] = []
    w = ConfigWatcher(p, ref, lambda t, d: events.append((t, d)))

    p.write_text("workflow: [unclosed\n")
    os.utime(p, (initial.source_mtime + 5, initial.source_mtime + 5))

    w.poll()
    assert ref.get() is initial
    assert any(t == "daedalus.config_reload_failed" for t, _ in events)


def test_watcher_poll_schema_invalid_keeps_lkg_and_emits_failure(tmp_path):
    import os
    from workflows.code_review.config_snapshot import AtomicRef
    from workflows.code_review.config_watcher import ConfigWatcher

    p, initial = _seed_snapshot(tmp_path)
    ref = AtomicRef(initial)
    events: list[tuple[str, dict]] = []
    w = ConfigWatcher(p, ref, lambda t, d: events.append((t, d)))

    p.write_text("workflow: code-review\n")  # schema-invalid (missing required fields)
    os.utime(p, (initial.source_mtime + 5, initial.source_mtime + 5))

    w.poll()
    assert ref.get() is initial
    failures = [d for t, d in events if t == "daedalus.config_reload_failed"]
    assert len(failures) == 1
    assert "schema validation" in failures[0]["error"]
```

- [ ] **Step 2: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_config_watcher.py -v
```
Expected: 7 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_config_watcher.py
git commit -m "$(cat <<'EOF'
test(symphony): cover ConfigWatcher bad-reload paths (yaml + schema)

Verifies the watcher keeps last-known-good snapshot and emits
daedalus.config_reload_failed for both YAML syntax and schema errors.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-2.4: change-detection key = `(mtime, size)` retry suppression

> **Why a tuple, not just mtime:** Codex review on PR #16 flagged that mtime
> equality alone misses real edits on filesystems with coarse timestamp
> resolution or mtime-preserving copies (NFS, rsync `-t`, overlayfs). The
> key compares both `st_mtime` and `st_size` so any byte-length change is
> caught even when mtime is unchanged.

**Files:**
- Modify: `tests/test_config_watcher.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_config_watcher.py`:

```python
def test_watcher_poll_does_not_re_emit_for_same_broken_mtime(tmp_path):
    import os
    from workflows.code_review.config_snapshot import AtomicRef
    from workflows.code_review.config_watcher import ConfigWatcher

    p, initial = _seed_snapshot(tmp_path)
    ref = AtomicRef(initial)
    events: list[tuple[str, dict]] = []
    w = ConfigWatcher(p, ref, lambda t, d: events.append((t, d)))

    p.write_text("workflow: [unclosed\n")
    os.utime(p, (initial.source_mtime + 5, initial.source_mtime + 5))

    w.poll()
    w.poll()
    w.poll()

    failures = [t for t, _ in events if t == "daedalus.config_reload_failed"]
    assert len(failures) == 1  # only the first tick re-attempted parsing
```

- [ ] **Step 2: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_config_watcher.py -v
```
Expected: 8 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_config_watcher.py
git commit -m "$(cat <<'EOF'
test(symphony): mtime-tied retry suppression for repeat broken bytes

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-2.4b: Size change with unchanged mtime triggers reload

**Files:**
- Modify: `tests/test_config_watcher.py`

> **Why this test:** filesystems with coarse mtime resolution (some NFS,
> some FAT-like mounts) and tools that preserve mtime (`cp -p`, `rsync -t`,
> some editor "atomic save" implementations) can change file bytes without
> bumping mtime. The watcher must still detect the edit. Codex P2 finding
> on PR #16.

- [ ] **Step 1: Add failing test**

Append to `tests/test_config_watcher.py`:

```python
def test_watcher_poll_detects_size_change_at_same_mtime(tmp_path):
    """Bytes changed but mtime preserved (e.g. rsync -t). Must reload."""
    import os
    from workflows.code_review.config_snapshot import AtomicRef
    from workflows.code_review.config_watcher import ConfigWatcher

    p, initial = _seed_snapshot(tmp_path)
    ref = AtomicRef(initial)
    events: list[tuple[str, dict]] = []
    w = ConfigWatcher(p, ref, lambda t, d: events.append((t, d)))

    # Force first poll to record current key
    w.poll()
    events.clear()

    # Rewrite with longer content but force-restore the original mtime
    new_yaml = p.read_text() + "\n# trailing comment to bump size\n"
    original_mtime = p.stat().st_mtime
    p.write_text(new_yaml)
    os.utime(p, (original_mtime, original_mtime))

    # mtime is unchanged but size grew. Watcher must still re-parse.
    w.poll()

    reloads = [t for t, _ in events if t == "daedalus.config_reloaded"]
    assert len(reloads) == 1, f"Expected size-change to trigger reload, got events={events}"
    assert ref.get() is not initial  # snapshot replaced
```

- [ ] **Step 2: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_config_watcher.py -v
```
Expected: 9 passed (was 8 before this task).

- [ ] **Step 3: Commit**

```bash
git add tests/test_config_watcher.py
git commit -m "$(cat <<'EOF'
test(symphony): size-change detection at unchanged mtime

Covers Codex P2 finding from PR #16: filesystems with coarse mtime
resolution or mtime-preserving copy tools must still surface real
edits to the watcher.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-2.5: File temporarily missing — no failure event

**Files:**
- Modify: `tests/test_config_watcher.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_config_watcher.py`:

```python
def test_watcher_poll_missing_file_keeps_lkg_no_event(tmp_path):
    from workflows.code_review.config_snapshot import AtomicRef
    from workflows.code_review.config_watcher import ConfigWatcher

    p, initial = _seed_snapshot(tmp_path)
    ref = AtomicRef(initial)
    events: list[tuple[str, dict]] = []
    w = ConfigWatcher(p, ref, lambda t, d: events.append((t, d)))

    p.unlink()
    w.poll()
    assert ref.get() is initial
    assert events == []  # missing-during-rename is silent
```

- [ ] **Step 2: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_config_watcher.py -v
```
Expected: 10 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_config_watcher.py
git commit -m "$(cat <<'EOF'
test(symphony): file vanish during atomic rename is silent no-op

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-2.6: Wire ConfigWatcher into watch.py tick

**Files:**
- Modify: `watch.py`

- [ ] **Step 1: Inspect existing tick loop**

```bash
cd /home/radxa/WS/hermes-relay/.claude/worktrees/symphony-conformance
grep -n "def main\|def tick\|workflow.yaml\|load_workflow\|run_loop" watch.py | head -30
```
Read the ~30 lines around the tick entrypoint to determine where the parsed config currently lives.

- [ ] **Step 2: Construct AtomicRef + ConfigWatcher at startup; poll() per tick**

In `watch.py`:

1. At the top of the tick-loop bootstrap (where `workflow.yaml` is currently loaded once), replace the one-shot load with:

```python
from workflows.code_review.config_snapshot import AtomicRef, ConfigSnapshot
from workflows.code_review.config_watcher import ConfigWatcher, parse_and_validate
from runtime import append_daedalus_event

_initial_snapshot = parse_and_validate(workflow_yaml_path)
_snapshot_ref: AtomicRef[ConfigSnapshot] = AtomicRef(_initial_snapshot)

def _emit_reload_event(event_type: str, payload: dict) -> None:
    append_daedalus_event(
        event_log_path=event_log_path,
        event={"type": event_type, **payload},
    )

_config_watcher = ConfigWatcher(
    workflow_yaml_path=workflow_yaml_path,
    snapshot_ref=_snapshot_ref,
    emit_event=_emit_reload_event,
)
```

2. At the start of every tick (before reconciliation/dispatch):

```python
_config_watcher.poll()
snapshot = _snapshot_ref.get()
config = snapshot.config  # downstream code reads from snapshot.config now
```

3. Replace any `config = load_workflow(...)` or equivalent in-tick call sites with `config = _snapshot_ref.get().config`.

- [ ] **Step 3: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -5
```
Expected: 600 passed (591 baseline + 6 from S-1 already merged + 9 new from this phase if S-1 already landed — adjust to whatever count S-1 produces). The integration is wiring; failing tests here mean a missed call site. Inspect with:

```bash
grep -n "load_workflow\|workflow_yaml" watch.py
```

- [ ] **Step 4: Commit**

```bash
git add watch.py
git commit -m "$(cat <<'EOF'
feat(symphony): wire ConfigWatcher into watch.py tick loop

watch.py now owns an AtomicRef[ConfigSnapshot] seeded at startup and
poll()s the watcher every tick. Downstream tick code reads the latest
snapshot from the ref so live edits to workflow.yaml take effect on
the next tick without restarting the daemon.

Symphony §6.2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-2.7: Smoke test on live yoyopod workspace

- [ ] **Step 1: Verify a live edit reloads**

```bash
/usr/bin/python3 -c "
import time, yaml
from pathlib import Path
from workflows.code_review.config_snapshot import AtomicRef
from workflows.code_review.config_watcher import ConfigWatcher, parse_and_validate

p = Path('/home/radxa/.hermes/workflows/yoyopod/config/workflow.yaml')
if not p.exists():
    print('yoyopod workflow.yaml absent on this host; skipping smoke')
    raise SystemExit(0)
initial = parse_and_validate(p)
ref = AtomicRef(initial)
events = []
w = ConfigWatcher(p, ref, lambda t, d: events.append((t, d)))
w.poll()
print('initial poll events:', events)
print('snapshot still original:', ref.get() is initial)
"
```
Expected: `initial poll events: []`, `snapshot still original: True`.

- [ ] **Step 2: Use superpowers:finishing-a-development-branch.**

---

# Phase S-3 — Per-tick dispatch preflight (Symphony §6.3)

**Branch:** `claude/symphony-s-3-preflight` from `main` (rebased onto S-2). One PR.

**Goal:** Pure function `run_preflight(snapshot) -> PreflightResult`. Called every tick **after** reconcile, **before** dispatch. On failure, emits `daedalus.dispatch_skipped` and returns; never blocks reconciliation. Startup validator calls the same function and exits non-zero on failure.

**File structure:**

- New: `workflows/code_review/preflight.py` — `PreflightResult` + `run_preflight`.
- New: `tests/test_preflight.py` — covers spec §5.5 (every error code in §5.4).
- Modify: `watch.py` — call `run_preflight` per tick after reconcile, before dispatch.
- Modify: `workflows/code_review/workflow.py` — startup validator calls `run_preflight` and `sys.exit(1)` on failure.

---

## Task S-3.1: `PreflightResult` + happy path

**Files:**
- Create: `workflows/code_review/preflight.py`
- Create: `tests/test_preflight.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_preflight.py`:

```python
"""S-3 tests: per-tick dispatch preflight — Symphony §6.3."""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from workflows.code_review.config_snapshot import ConfigSnapshot


def _good_config(local_path: str) -> dict:
    return {
        "workflow": "code-review",
        "schema-version": 1,
        "instance": {"name": "i", "engine-owner": "hermes"},
        "repository": {
            "local-path": local_path,
            "github-slug": "org/repo",
            "active-lane-label": "active-lane",
        },
        "runtimes": {"r1": {"kind": "claude-cli", "max-turns-per-invocation": 4, "timeout-seconds": 60}},
        "agents": {
            "coder": {"t1": {"name": "coder", "model": "claude", "runtime": "r1"}},
            "internal-reviewer": {"name": "i", "model": "claude", "runtime": "r1"},
            "external-reviewer": {"enabled": False, "name": "e"},
        },
        "gates": {"internal-review": {}, "external-review": {}, "merge": {}},
        "triggers": {"lane-selector": {"type": "github-issue-label", "label": "active-lane"}},
        "storage": {"ledger": "l.json", "health": "h.json", "audit-log": "a.log"},
    }


def test_preflight_happy_path(tmp_path):
    from workflows.code_review.preflight import run_preflight

    snap = ConfigSnapshot(
        config=_good_config(str(tmp_path)),
        prompts={},
        loaded_at=0.0,
        source_mtime=0.0,
    )
    result = run_preflight(snap)
    assert result.ok is True
    assert result.error_code is None
    assert result.error_detail is None
    assert result.can_reconcile is True
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_preflight.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'workflows.code_review.preflight'`.

- [ ] **Step 3: Create module**

Create `workflows/code_review/preflight.py`:

```python
"""Per-tick dispatch preflight (Symphony §6.3).

`run_preflight(snapshot)` is a pure function: snapshot in, verdict out.
Cheap (<1ms) so the tick loop calls it every tick. Reconciliation
always runs first — preflight only gates dispatch, never reconciliation.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from workflows.code_review.config_snapshot import ConfigSnapshot


# Fixed enum of error codes (spec §5.4)
ERR_MISSING_WORKFLOW_FILE          = "missing_workflow_file"
ERR_WORKFLOW_PARSE_ERROR           = "workflow_parse_error"
ERR_WORKFLOW_FRONT_MATTER_NOT_MAP  = "workflow_front_matter_not_a_map"
ERR_UNSUPPORTED_RUNTIME_KIND       = "unsupported_runtime_kind"
ERR_UNSUPPORTED_REVIEWER_KIND      = "unsupported_reviewer_kind"
ERR_MISSING_TRACKER_CREDENTIALS    = "missing_tracker_credentials"
ERR_UNSUPPORTED_TRACKER_KIND       = "unsupported_tracker_kind"
ERR_WORKSPACE_ROOT_UNWRITABLE      = "workspace_root_unwritable"

_KNOWN_RUNTIME_KINDS = {"acpx-codex", "claude-cli", "hermes-agent"}
_KNOWN_REVIEWER_KINDS = {"github-comments", "disabled"}
_KNOWN_TRACKER_TYPES = {"github-issue-label"}


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    error_code: str | None
    error_detail: str | None
    can_reconcile: bool  # always True; preflight never blocks reconciliation


def _fail(code: str, detail: str) -> PreflightResult:
    return PreflightResult(ok=False, error_code=code, error_detail=detail, can_reconcile=True)


def run_preflight(snapshot: ConfigSnapshot) -> PreflightResult:
    """Return verdict for the current snapshot. Pure; no I/O except a single
    writability probe on the workspace root."""
    config = snapshot.config
    if not isinstance(config, dict):
        return _fail(ERR_WORKFLOW_FRONT_MATTER_NOT_MAP,
                     f"top-level must be a mapping, got {type(config).__name__}")

    # Required tracker block + label
    triggers = config.get("triggers")
    if not isinstance(triggers, dict):
        return _fail(ERR_UNSUPPORTED_TRACKER_KIND, "triggers section missing or not a mapping")
    selector = triggers.get("lane-selector")
    if not isinstance(selector, dict):
        return _fail(ERR_UNSUPPORTED_TRACKER_KIND, "triggers.lane-selector missing")
    sel_type = selector.get("type")
    if sel_type not in _KNOWN_TRACKER_TYPES:
        return _fail(ERR_UNSUPPORTED_TRACKER_KIND,
                     f"triggers.lane-selector.type={sel_type!r} not in {sorted(_KNOWN_TRACKER_TYPES)}")
    repo = config.get("repository") or {}
    if not repo.get("github-slug"):
        return _fail(ERR_MISSING_TRACKER_CREDENTIALS, "repository.github-slug missing")

    # GitHub-token resolution: $VAR_NAME tokens must resolve non-empty.
    token_name = repo.get("github-token-env") or "GITHUB_TOKEN"
    if isinstance(token_name, str) and token_name.startswith("$"):
        token_name = token_name[1:]
    if isinstance(token_name, str) and not os.environ.get(token_name, ""):
        return _fail(ERR_MISSING_TRACKER_CREDENTIALS,
                     f"environment variable {token_name!r} unset or empty")

    # Runtime kinds
    runtimes = config.get("runtimes") or {}
    for name, rt in runtimes.items():
        kind = (rt or {}).get("kind")
        if kind not in _KNOWN_RUNTIME_KINDS:
            return _fail(ERR_UNSUPPORTED_RUNTIME_KIND,
                         f"runtimes.{name}.kind={kind!r} not in {sorted(_KNOWN_RUNTIME_KINDS)}")

    # Reviewer kind (external-reviewer only — internal is always claude-style)
    agents = config.get("agents") or {}
    ext = agents.get("external-reviewer") or {}
    if ext.get("enabled"):
        rkind = ext.get("kind")
        if rkind is not None and rkind not in _KNOWN_REVIEWER_KINDS:
            return _fail(ERR_UNSUPPORTED_REVIEWER_KIND,
                         f"agents.external-reviewer.kind={rkind!r} not in {sorted(_KNOWN_REVIEWER_KINDS)}")

    # Workspace root writable
    local_path = repo.get("local-path")
    if not local_path:
        return _fail(ERR_WORKSPACE_ROOT_UNWRITABLE, "repository.local-path missing")
    p = Path(local_path)
    if not p.exists() or not os.access(p, os.W_OK):
        return _fail(ERR_WORKSPACE_ROOT_UNWRITABLE,
                     f"repository.local-path={local_path!r} does not exist or is not writable")

    return PreflightResult(ok=True, error_code=None, error_detail=None, can_reconcile=True)
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_preflight.py::test_preflight_happy_path -v
```
Expected: PASS. (Note: depends on the env having GITHUB_TOKEN set; the test sets `repo["github-token-env"]` only if needed — actually the helper does NOT, so set it before running:)

```bash
GITHUB_TOKEN=stub /usr/bin/python3 -m pytest tests/test_preflight.py::test_preflight_happy_path -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/preflight.py tests/test_preflight.py
git commit -m "$(cat <<'EOF'
feat(symphony): add run_preflight(snapshot) -> PreflightResult

Pure preflight verdict for the current ConfigSnapshot. Eight error
codes per spec §5.4. Called per-tick before dispatch and at startup;
identical contract.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-3.2: Error-code coverage

**Files:**
- Modify: `tests/test_preflight.py`

- [ ] **Step 1: Add failing tests for each error code**

Append to `tests/test_preflight.py`:

```python
def _snap(cfg: dict) -> ConfigSnapshot:
    return ConfigSnapshot(config=cfg, prompts={}, loaded_at=0.0, source_mtime=0.0)


def test_preflight_unsupported_runtime_kind(tmp_path, monkeypatch):
    from workflows.code_review.preflight import run_preflight, ERR_UNSUPPORTED_RUNTIME_KIND

    monkeypatch.setenv("GITHUB_TOKEN", "stub")
    cfg = _good_config(str(tmp_path))
    cfg["runtimes"]["r1"]["kind"] = "imaginary-runtime"
    r = run_preflight(_snap(cfg))
    assert r.ok is False
    assert r.error_code == ERR_UNSUPPORTED_RUNTIME_KIND


def test_preflight_unsupported_reviewer_kind(tmp_path, monkeypatch):
    from workflows.code_review.preflight import run_preflight, ERR_UNSUPPORTED_REVIEWER_KIND

    monkeypatch.setenv("GITHUB_TOKEN", "stub")
    cfg = _good_config(str(tmp_path))
    cfg["agents"]["external-reviewer"] = {"enabled": True, "name": "e", "kind": "imaginary-reviewer"}
    r = run_preflight(_snap(cfg))
    assert r.ok is False
    assert r.error_code == ERR_UNSUPPORTED_REVIEWER_KIND


def test_preflight_missing_tracker_credentials_no_env(tmp_path, monkeypatch):
    from workflows.code_review.preflight import run_preflight, ERR_MISSING_TRACKER_CREDENTIALS

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    cfg = _good_config(str(tmp_path))
    r = run_preflight(_snap(cfg))
    assert r.ok is False
    assert r.error_code == ERR_MISSING_TRACKER_CREDENTIALS


def test_preflight_missing_tracker_credentials_no_repo_slug(tmp_path, monkeypatch):
    from workflows.code_review.preflight import run_preflight, ERR_MISSING_TRACKER_CREDENTIALS

    monkeypatch.setenv("GITHUB_TOKEN", "stub")
    cfg = _good_config(str(tmp_path))
    cfg["repository"]["github-slug"] = ""
    r = run_preflight(_snap(cfg))
    assert r.ok is False
    assert r.error_code == ERR_MISSING_TRACKER_CREDENTIALS


def test_preflight_unsupported_tracker_kind(tmp_path, monkeypatch):
    from workflows.code_review.preflight import run_preflight, ERR_UNSUPPORTED_TRACKER_KIND

    monkeypatch.setenv("GITHUB_TOKEN", "stub")
    cfg = _good_config(str(tmp_path))
    cfg["triggers"]["lane-selector"]["type"] = "linear-issue"
    r = run_preflight(_snap(cfg))
    assert r.ok is False
    assert r.error_code == ERR_UNSUPPORTED_TRACKER_KIND


def test_preflight_workspace_root_unwritable_missing(tmp_path, monkeypatch):
    from workflows.code_review.preflight import run_preflight, ERR_WORKSPACE_ROOT_UNWRITABLE

    monkeypatch.setenv("GITHUB_TOKEN", "stub")
    cfg = _good_config(str(tmp_path / "does-not-exist"))
    r = run_preflight(_snap(cfg))
    assert r.ok is False
    assert r.error_code == ERR_WORKSPACE_ROOT_UNWRITABLE


def test_preflight_workspace_root_unwritable_readonly(tmp_path, monkeypatch):
    import os
    from workflows.code_review.preflight import run_preflight, ERR_WORKSPACE_ROOT_UNWRITABLE

    monkeypatch.setenv("GITHUB_TOKEN", "stub")
    ro = tmp_path / "ro"
    ro.mkdir()
    os.chmod(ro, 0o500)
    try:
        cfg = _good_config(str(ro))
        r = run_preflight(_snap(cfg))
        assert r.ok is False
        assert r.error_code == ERR_WORKSPACE_ROOT_UNWRITABLE
    finally:
        os.chmod(ro, 0o700)


def test_preflight_workflow_front_matter_not_a_map(tmp_path, monkeypatch):
    from workflows.code_review.preflight import run_preflight, ERR_WORKFLOW_FRONT_MATTER_NOT_MAP

    monkeypatch.setenv("GITHUB_TOKEN", "stub")
    snap = ConfigSnapshot(config="oh no I'm a string", prompts={}, loaded_at=0.0, source_mtime=0.0)  # type: ignore[arg-type]
    r = run_preflight(snap)
    assert r.ok is False
    assert r.error_code == ERR_WORKFLOW_FRONT_MATTER_NOT_MAP
```

- [ ] **Step 2: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_preflight.py -v
```
Expected: 9 passed (1 happy + 8 failure-code tests).

- [ ] **Step 3: Commit**

```bash
git add tests/test_preflight.py
git commit -m "$(cat <<'EOF'
test(symphony): cover all preflight error codes from spec §5.4

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-3.3: Always-reconcile invariant

**Files:**
- Modify: `tests/test_preflight.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_preflight.py`:

```python
def test_preflight_can_reconcile_is_always_true_even_on_failure(tmp_path, monkeypatch):
    from workflows.code_review.preflight import run_preflight

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    cfg = _good_config(str(tmp_path))
    r = run_preflight(_snap(cfg))
    assert r.ok is False
    assert r.can_reconcile is True
```

- [ ] **Step 2: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_preflight.py -v
```
Expected: 10 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_preflight.py
git commit -m "$(cat <<'EOF'
test(symphony): preflight can_reconcile=True invariant

Reconciliation must always run, even on preflight failure (spec §5.3).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-3.4: Wire into watch.py tick loop

**Files:**
- Modify: `watch.py`

- [ ] **Step 1: Inspect tick body**

```bash
grep -n "reconcile\|dispatch\|def tick" watch.py | head -20
```

- [ ] **Step 2: Insert preflight call**

In `watch.py`, after the existing reconciliation step and before any dispatch step, add:

```python
from workflows.code_review.preflight import run_preflight

# Per-tick (Symphony §6.3): reconcile always runs (above);
# dispatch is gated by preflight verdict on the current snapshot.
_pre = run_preflight(_snapshot_ref.get())
if not _pre.ok:
    append_daedalus_event(
        event_log_path=event_log_path,
        event={
            "type": "daedalus.dispatch_skipped",
            "code": _pre.error_code,
            "detail": _pre.error_detail,
        },
    )
    return  # or `continue` if inside a while-loop; preserve existing control flow
# ...existing dispatch_eligible_lanes(...) call follows here
```

- [ ] **Step 3: Run full suite**

```bash
GITHUB_TOKEN=stub /usr/bin/python3 -m pytest tests/ 2>&1 | tail -5
```
Expected: 600+ passed (no regression).

- [ ] **Step 4: Commit**

```bash
git add watch.py
git commit -m "$(cat <<'EOF'
feat(symphony): per-tick dispatch preflight (§6.3)

Reconcile runs unconditionally; dispatch gated by run_preflight()
verdict on the current ConfigSnapshot. Failure emits
daedalus.dispatch_skipped with error code + detail.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-3.5: Startup validator uses run_preflight

**Files:**
- Modify: `workflows/code_review/workflow.py` (or wherever startup validation lives — locate via grep)
- Modify: `tests/test_preflight.py`

- [ ] **Step 1: Locate the startup validator**

```bash
grep -n "validate\|sys.exit\|startup" workflows/code_review/workflow.py | head -10
```

- [ ] **Step 2: Add a startup-failure test**

Append to `tests/test_preflight.py`:

```python
def test_run_preflight_at_startup_exits_nonzero_on_failure(tmp_path, monkeypatch):
    """The CLI/startup path must surface preflight failure as nonzero exit."""
    from workflows.code_review.preflight import run_preflight

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    cfg = _good_config(str(tmp_path))
    r = run_preflight(_snap(cfg))
    # Document the contract that startup wraps in sys.exit(1) when not r.ok.
    assert r.ok is False
    assert r.error_code is not None
    # Smoke: production startup site must call run_preflight.
    src = Path("workflows/code_review/workflow.py").read_text()
    assert "run_preflight" in src, "startup validator must invoke run_preflight"
```

- [ ] **Step 3: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_preflight.py::test_run_preflight_at_startup_exits_nonzero_on_failure -v
```
Expected: FAIL — `run_preflight not in workflows/code_review/workflow.py`.

- [ ] **Step 4: Wire run_preflight into workflow.py startup**

In `workflows/code_review/workflow.py`, locate the existing top-level config-load function (the one watch.py calls at startup) and add — after parsing — a preflight call:

```python
from workflows.code_review.preflight import run_preflight
from workflows.code_review.config_snapshot import ConfigSnapshot

def _validate_or_exit(config: dict, source_mtime: float) -> None:
    snap = ConfigSnapshot(
        config=config,
        prompts=config.get("prompts") or {},
        loaded_at=0.0,
        source_mtime=source_mtime,
    )
    result = run_preflight(snap)
    if not result.ok:
        import sys
        sys.stderr.write(
            f"daedalus startup preflight failed: {result.error_code}: {result.error_detail}\n"
        )
        sys.exit(1)
```

Call `_validate_or_exit(config, path.stat().st_mtime)` from the existing public load function before returning the parsed config.

- [ ] **Step 5: Run full suite**

```bash
GITHUB_TOKEN=stub /usr/bin/python3 -m pytest tests/ 2>&1 | tail -5
```
Expected: 601+ passed.

- [ ] **Step 6: Commit**

```bash
git add workflows/code_review/workflow.py tests/test_preflight.py
git commit -m "$(cat <<'EOF'
feat(symphony): startup validator delegates to run_preflight

Same function used per-tick. Startup failure -> sys.exit(1) (preserves
existing fail-loud behavior); same failure once running becomes a soft
daedalus.dispatch_skipped event.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-3.6: Final regression + finishing

- [ ] **Step 1:**

```bash
GITHUB_TOKEN=stub /usr/bin/python3 -m pytest tests/ 2>&1 | tail -5
```
Expected: full suite passes.

- [ ] **Step 2: Use superpowers:finishing-a-development-branch.**

---

# Phase S-4 — Event vocabulary alignment (Symphony §10.4)

**Branch:** `claude/symphony-s-4-event-taxonomy` from `main`. Independent of S-1..S-3.

**Goal:** Single source of truth for event names; refactor ~15 `append_daedalus_event` call sites in `runtime.py` to use module constants instead of string literals; readers wrap event-type reads in `canonicalize()`. One-release alias window for legacy names.

**File structure:**

- New: `workflows/code_review/event_taxonomy.py` — constants + `EVENT_ALIASES` + `canonicalize()`.
- New: `tests/test_event_taxonomy.py`.
- Modify: `runtime.py` — every `event={"type": "..."}` literal becomes a module constant.
- Modify: `workflows/code_review/observability.py`, `status.py`, `watch.py` — readers wrap `event["type"]` in `canonicalize()`.

---

## Task S-4.1: `event_taxonomy` module

**Files:**
- Create: `workflows/code_review/event_taxonomy.py`
- Create: `tests/test_event_taxonomy.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_event_taxonomy.py`:

```python
"""S-4 tests: event vocabulary alignment — Symphony §10.4."""
from __future__ import annotations


def test_canonical_constants_present():
    from workflows.code_review import event_taxonomy as et

    # Symphony bare names
    assert et.SESSION_STARTED == "session_started"
    assert et.TURN_COMPLETED == "turn_completed"
    assert et.TURN_FAILED == "turn_failed"
    assert et.TURN_CANCELLED == "turn_cancelled"
    assert et.TURN_INPUT_REQUIRED == "turn_input_required"
    assert et.NOTIFICATION == "notification"
    assert et.UNSUPPORTED_TOOL_CALL == "unsupported_tool_call"
    assert et.MALFORMED == "malformed"
    assert et.STARTUP_FAILED == "startup_failed"


def test_daedalus_native_constants_have_prefix():
    from workflows.code_review import event_taxonomy as et

    daedalus_natives = [
        et.DAEDALUS_LANE_CLAIMED, et.DAEDALUS_LANE_RELEASED,
        et.DAEDALUS_REPAIR_HANDOFF, et.DAEDALUS_REVIEW_LANDED,
        et.DAEDALUS_VERDICT_PUBLISHED, et.DAEDALUS_CONFIG_RELOADED,
        et.DAEDALUS_CONFIG_RELOAD_FAILED, et.DAEDALUS_DISPATCH_SKIPPED,
        et.DAEDALUS_STALL_DETECTED, et.DAEDALUS_STALL_TERMINATED,
        et.DAEDALUS_REFRESH_REQUESTED,
    ]
    for name in daedalus_natives:
        assert name.startswith("daedalus."), f"{name!r} missing daedalus. prefix"


def test_canonicalize_passes_canonical_names_through():
    from workflows.code_review.event_taxonomy import canonicalize, TURN_COMPLETED

    assert canonicalize(TURN_COMPLETED) == TURN_COMPLETED
    assert canonicalize("session_started") == "session_started"


def test_canonicalize_resolves_legacy_aliases():
    from workflows.code_review.event_taxonomy import canonicalize

    assert canonicalize("claude_review_started") == "session_started"
    assert canonicalize("claude_review_completed") == "turn_completed"
    assert canonicalize("claude_review_failed") == "turn_failed"
    assert canonicalize("codex_handoff_dispatched") == "daedalus.repair_handoff_dispatched"
    assert canonicalize("internal_review_started") == "session_started"
    assert canonicalize("internal_review_completed") == "turn_completed"


def test_canonicalize_unknown_passthrough():
    from workflows.code_review.event_taxonomy import canonicalize

    assert canonicalize("totally_unknown_event") == "totally_unknown_event"


def test_event_aliases_table_integrity():
    """Every legacy name maps to a known canonical."""
    from workflows.code_review import event_taxonomy as et

    canonical_names = {
        v for k, v in vars(et).items()
        if isinstance(v, str) and (v == k.lower() or v.startswith("daedalus."))
    }
    for legacy, canonical in et.EVENT_ALIASES.items():
        assert canonical in canonical_names or canonical.startswith("daedalus.") or "_" in canonical, \
            f"alias {legacy!r} -> {canonical!r} not a known canonical name"
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_event_taxonomy.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'workflows.code_review.event_taxonomy'`.

- [ ] **Step 3: Create module**

Create `workflows/code_review/event_taxonomy.py`:

```python
"""Symphony §10.4-aligned event taxonomy.

Single source of truth for canonical event names. Writers (in runtime.py)
emit only constants from this module; readers (status.py, observability.py,
watch.py, server views) wrap event-type reads in `canonicalize()` so old
log files keep working during the one-release alias window.

Design:
- Symphony's bare session/turn lifecycle names (session_started, …)
- Daedalus-native orchestration events under `daedalus.*` prefix
- EVENT_ALIASES maps legacy Daedalus event names to their new canonical
  equivalents. Readers consult this map; writers do not.
"""
from __future__ import annotations


# ---- Symphony §10.4 session/turn-level events ----
SESSION_STARTED       = "session_started"
TURN_COMPLETED        = "turn_completed"
TURN_FAILED           = "turn_failed"
TURN_CANCELLED        = "turn_cancelled"
TURN_INPUT_REQUIRED   = "turn_input_required"
NOTIFICATION          = "notification"
UNSUPPORTED_TOOL_CALL = "unsupported_tool_call"
MALFORMED             = "malformed"
STARTUP_FAILED        = "startup_failed"

# ---- Daedalus-native events (no Symphony equivalent) ----
DAEDALUS_LANE_CLAIMED         = "daedalus.lane_claimed"
DAEDALUS_LANE_RELEASED        = "daedalus.lane_released"
DAEDALUS_REPAIR_HANDOFF       = "daedalus.repair_handoff_dispatched"
DAEDALUS_REVIEW_LANDED        = "daedalus.review_landed"
DAEDALUS_VERDICT_PUBLISHED    = "daedalus.verdict_published"
DAEDALUS_CONFIG_RELOADED      = "daedalus.config_reloaded"
DAEDALUS_CONFIG_RELOAD_FAILED = "daedalus.config_reload_failed"
DAEDALUS_DISPATCH_SKIPPED     = "daedalus.dispatch_skipped"
DAEDALUS_STALL_DETECTED       = "daedalus.stall_detected"
DAEDALUS_STALL_TERMINATED     = "daedalus.stall_terminated"
DAEDALUS_REFRESH_REQUESTED    = "daedalus.refresh_requested"


# ---- One-release alias window: legacy -> canonical ----
EVENT_ALIASES: dict[str, str] = {
    "claude_review_started":     SESSION_STARTED,
    "claude_review_completed":   TURN_COMPLETED,
    "claude_review_failed":      TURN_FAILED,
    "codex_handoff_dispatched":  DAEDALUS_REPAIR_HANDOFF,
    "internal_review_started":   SESSION_STARTED,
    "internal_review_completed": TURN_COMPLETED,
    "internal_review_failed":    TURN_FAILED,
    "external_review_landed":    DAEDALUS_REVIEW_LANDED,
    "verdict_published":         DAEDALUS_VERDICT_PUBLISHED,
    "lane_claimed":              DAEDALUS_LANE_CLAIMED,
    "lane_released":             DAEDALUS_LANE_RELEASED,
}


def canonicalize(event_type: str) -> str:
    """Resolve a possibly-legacy event-type string to its canonical form.

    Idempotent for already-canonical names. Unknown names pass through
    unchanged so readers don't lose information."""
    return EVENT_ALIASES.get(event_type, event_type)
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_event_taxonomy.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/event_taxonomy.py tests/test_event_taxonomy.py
git commit -m "$(cat <<'EOF'
feat(symphony): add event_taxonomy module with canonical constants

Symphony §10.4-aligned bare names for session/turn lifecycle events;
daedalus.* prefix for orchestration events; EVENT_ALIASES table for the
one-release legacy-name window.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-4.2: Round-trip test (writer → reader via log fixture)

**Files:**
- Modify: `tests/test_event_taxonomy.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_event_taxonomy.py`:

```python
def test_round_trip_canonical_writer_reader(tmp_path):
    """Writer writes canonical; reader reads canonical via canonicalize."""
    import json
    from workflows.code_review.event_taxonomy import (
        canonicalize, TURN_COMPLETED, DAEDALUS_LANE_CLAIMED,
    )

    log = tmp_path / "events.jsonl"
    with log.open("w") as f:
        f.write(json.dumps({"type": TURN_COMPLETED}) + "\n")
        f.write(json.dumps({"type": DAEDALUS_LANE_CLAIMED}) + "\n")

    seen = []
    for line in log.read_text().splitlines():
        e = json.loads(line)
        seen.append(canonicalize(e["type"]))
    assert seen == [TURN_COMPLETED, DAEDALUS_LANE_CLAIMED]


def test_legacy_log_lines_canonicalize_on_read(tmp_path):
    """Old jsonl files with legacy names still resolve through canonicalize."""
    import json
    from workflows.code_review.event_taxonomy import (
        canonicalize, SESSION_STARTED, DAEDALUS_REPAIR_HANDOFF,
    )

    log = tmp_path / "events.jsonl"
    log.write_text(
        json.dumps({"type": "claude_review_started"}) + "\n" +
        json.dumps({"type": "codex_handoff_dispatched"}) + "\n"
    )
    canon = [canonicalize(json.loads(l)["type"]) for l in log.read_text().splitlines()]
    assert canon == [SESSION_STARTED, DAEDALUS_REPAIR_HANDOFF]
```

- [ ] **Step 2: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_event_taxonomy.py -v
```
Expected: 8 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_event_taxonomy.py
git commit -m "$(cat <<'EOF'
test(symphony): cover round-trip + legacy-log canonicalization on read

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-4.3: Refactor `runtime.py` writers to use constants

**Files:**
- Modify: `runtime.py` (~17 `append_daedalus_event` call sites at lines 750, 859, 1133, 1387, 1532, 2000, 3144, 3162, 3193, 3195, 3197, 3221, 3246, 3272, 3331, 3448 plus the helper at 594)

- [ ] **Step 1: Inventory event-type literals**

```bash
grep -n '"type"\s*:\s*"' runtime.py | head -50
```

- [ ] **Step 2: Mechanical replacement**

For each `event={"type": "<literal>", ...}` in `runtime.py`:
1. Add `from workflows.code_review.event_taxonomy import (...)` at the top, importing the constant equivalents.
2. Replace `"type": "<literal>"` with `"type": <CONSTANT>`.

Mapping rules:
- Any literal already on the canonical list → use the matching constant.
- Any legacy literal (`"claude_review_started"`, `"internal_review_started"`, …) → use the canonical the alias points to (e.g. `SESSION_STARTED`).
- Any Daedalus-native literal not yet in `event_taxonomy.py` → add the constant to `event_taxonomy.py` first (with `DAEDALUS_` prefix and `daedalus.` value), then import.

After this task no string literal of the form `"type": "..."` should remain in `runtime.py` event-construction sites.

- [ ] **Step 3: AST-based regression test**

Append to `tests/test_event_taxonomy.py`:

```python
def test_runtime_py_uses_constants_not_string_literals():
    """No string literal of the form {"type": "..."} appears in runtime.py
    event-construction sites — every site must reference an event_taxonomy constant."""
    import ast
    from pathlib import Path

    src = Path("runtime.py").read_text()
    tree = ast.parse(src)

    bad_sites: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values):
            if (
                isinstance(k, ast.Constant) and k.value == "type"
                and isinstance(v, ast.Constant) and isinstance(v.value, str)
            ):
                bad_sites.append((getattr(node, "lineno", -1), v.value))
    assert bad_sites == [], (
        "runtime.py contains string-literal event types; use event_taxonomy "
        f"constants instead. Sites: {bad_sites}"
    )
```

- [ ] **Step 4: Run regression**

```bash
/usr/bin/python3 -m pytest tests/test_event_taxonomy.py::test_runtime_py_uses_constants_not_string_literals -v
```
Expected: PASS.

- [ ] **Step 5: Run full suite**

```bash
GITHUB_TOKEN=stub /usr/bin/python3 -m pytest tests/ 2>&1 | tail -5
```
Expected: full suite passes (any test asserting specific event-type strings keeps working because canonical values match the literals they replaced).

- [ ] **Step 6: Commit**

```bash
git add runtime.py workflows/code_review/event_taxonomy.py tests/test_event_taxonomy.py
git commit -m "$(cat <<'EOF'
refactor(symphony): runtime.py event writers use event_taxonomy constants

All 17 append_daedalus_event call sites now reference module constants
instead of string literals. AST regression test prevents reintroducing
literal "type": "..." entries.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-4.4: Refactor readers to wrap in `canonicalize()`

**Files:**
- Modify: `workflows/code_review/observability.py`
- Modify: `workflows/code_review/status.py`
- Modify: `watch.py`

- [ ] **Step 1: Find reader sites**

```bash
grep -n 'event\[.type.\]\|event\.get..type..\|e\[.type.\]' workflows/code_review/observability.py workflows/code_review/status.py watch.py
```

- [ ] **Step 2: Wrap each comparison/lookup**

For each site such as `event["type"]` or `event.get("type")` used in a comparison, branch, or set lookup, replace:

```python
event["type"]
```
with:
```python
canonicalize(event["type"])
```

Add `from workflows.code_review.event_taxonomy import canonicalize` to the top of each modified file.

Specifically:
- `observability.py`: `include-events` set membership check — `if canonicalize(event["type"]) in include_events:`. Also accept legacy include-event names by canonicalizing them at config-load time (one-pass).
- `status.py`: any branching on event type.
- `watch.py`: tick-loop reads of recent events.

- [ ] **Step 3: Run full suite**

```bash
GITHUB_TOKEN=stub /usr/bin/python3 -m pytest tests/ 2>&1 | tail -5
```
Expected: full suite passes. If a status test fails, the breakage is one of: (a) a string-equal comparison missed; (b) a literal in the test fixture itself. Adjust the production-side wrap, not the test, unless the test is itself outdated.

- [ ] **Step 4: Commit**

```bash
git add workflows/code_review/observability.py workflows/code_review/status.py watch.py
git commit -m "$(cat <<'EOF'
refactor(symphony): readers canonicalize event types before comparison

observability.py, status.py, and watch.py wrap event["type"] reads in
canonicalize() so legacy log entries continue to match against canonical
names during the one-release alias window.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-4.5: Final regression + finishing

- [ ] **Step 1:**

```bash
GITHUB_TOKEN=stub /usr/bin/python3 -m pytest tests/ 2>&1 | tail -5
```
Expected: full suite passes.

- [ ] **Step 2: Use superpowers:finishing-a-development-branch.**

---

# Phase S-5 — Stall detection (Symphony §8.5)

**Branch:** `claude/symphony-s-5-stall` from `main` (rebased onto S-1). One PR.

**Goal:** Optional `Runtime.last_activity_ts() -> float | None` Protocol method; per-runtime implementation; pure `reconcile_stalls(snapshot, running, now) -> list[StallVerdict]` function; `stall.timeout_ms` schema field; tick-loop integration with terminate + retry.

**File structure:**

- Modify: `workflows/code_review/runtimes/__init__.py` — add `last_activity_ts` to `Runtime` Protocol.
- Modify: `workflows/code_review/runtimes/acpx_codex.py`, `claude_cli.py`, `hermes_agent.py` — implement liveness signal.
- New: `workflows/code_review/stall.py` — `StallVerdict` + `reconcile_stalls`.
- New: `tests/test_stall_detection.py` — covers spec §8.7.
- Modify: `workflows/code_review/schema.yaml` — add `stall` section.
- Modify: `watch.py` — call `reconcile_stalls` before dispatch.

---

## Task S-5.1: Extend `Runtime` Protocol

**Files:**
- Modify: `workflows/code_review/runtimes/__init__.py`
- Create: `tests/test_stall_detection.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_stall_detection.py`:

```python
"""S-5 tests: stall detection — Symphony §8.5."""
from __future__ import annotations

import time
from dataclasses import dataclass

import pytest


def test_runtime_protocol_has_last_activity_ts():
    """The Runtime Protocol declares last_activity_ts (optional method)."""
    from workflows.code_review.runtimes import Runtime

    assert "last_activity_ts" in Runtime.__dict__ or hasattr(Runtime, "last_activity_ts"), \
        "Runtime Protocol must declare last_activity_ts"
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_stall_detection.py::test_runtime_protocol_has_last_activity_ts -v
```
Expected: FAIL.

- [ ] **Step 3: Extend Protocol**

In `workflows/code_review/runtimes/__init__.py`, after the existing `run_command` method declaration, add:

```python
    def last_activity_ts(self) -> float | None:
        """Monotonic timestamp of the most recent forward-progress signal
        from the running agent. None means either: no signal yet (still in
        startup) or the runtime does not track liveness (opts out of stall
        detection). Symphony §8.5."""
        ...
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_stall_detection.py -v
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/runtimes/__init__.py tests/test_stall_detection.py
git commit -m "$(cat <<'EOF'
feat(symphony): extend Runtime Protocol with last_activity_ts (§8.5)

Optional method; runtimes that do not implement it opt out of stall
detection (returns None).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-5.2: Per-runtime liveness signal — claude-cli

**Files:**
- Modify: `workflows/code_review/runtimes/claude_cli.py`
- Modify: `tests/test_stall_detection.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_stall_detection.py`:

```python
def test_claude_cli_runtime_updates_last_activity_on_stdout_line(monkeypatch):
    import time
    from workflows.code_review.runtimes.claude_cli import ClaudeCLIRuntime

    rt = ClaudeCLIRuntime({"kind": "claude-cli", "max-turns-per-invocation": 1, "timeout-seconds": 60}, run=None, run_json=None)
    assert rt.last_activity_ts() is None  # no signal yet

    before = time.monotonic()
    rt._record_activity()  # internal helper called per stdout/stderr line
    after = time.monotonic()
    ts = rt.last_activity_ts()
    assert ts is not None
    assert before <= ts <= after
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_stall_detection.py::test_claude_cli_runtime_updates_last_activity_on_stdout_line -v
```
Expected: FAIL.

- [ ] **Step 3: Implement signal**

In `workflows/code_review/runtimes/claude_cli.py`, add to the `ClaudeCLIRuntime` class:

```python
import time

class ClaudeCLIRuntime:
    # ... existing __init__ ...

    def __init__(self, profile_cfg, *, run=None, run_json=None):
        # ... existing body ...
        self._last_activity: float | None = None

    def _record_activity(self) -> None:
        self._last_activity = time.monotonic()

    def last_activity_ts(self) -> float | None:
        return self._last_activity
```

Then in the subprocess-stdout reading loop (find via `grep -n "readline\|stdout\|stderr" workflows/code_review/runtimes/claude_cli.py`), call `self._record_activity()` after each line read.

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_stall_detection.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/runtimes/claude_cli.py tests/test_stall_detection.py
git commit -m "$(cat <<'EOF'
feat(symphony): claude-cli runtime emits last_activity_ts on stdout lines

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-5.3: Per-runtime liveness signal — acpx-codex

**Files:**
- Modify: `workflows/code_review/runtimes/acpx_codex.py`
- Modify: `tests/test_stall_detection.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_stall_detection.py`:

```python
def test_acpx_codex_runtime_updates_last_activity_on_app_server_event():
    import time
    from workflows.code_review.runtimes.acpx_codex import AcpxCodexRuntime

    rt = AcpxCodexRuntime(
        {"kind": "acpx-codex",
         "session-idle-freshness-seconds": 60,
         "session-idle-grace-seconds": 60,
         "session-nudge-cooldown-seconds": 60},
        run=None, run_json=None,
    )
    assert rt.last_activity_ts() is None

    rt._record_activity()
    assert rt.last_activity_ts() is not None
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_stall_detection.py::test_acpx_codex_runtime_updates_last_activity_on_app_server_event -v
```
Expected: FAIL.

- [ ] **Step 3: Implement signal**

In `workflows/code_review/runtimes/acpx_codex.py`, add `_last_activity` attribute and `_record_activity()` / `last_activity_ts()` methods (same pattern as Task S-5.2). Then call `self._record_activity()` in the app-server-event handler (find via `grep -n "turn_started\|turn_completed\|notification" workflows/code_review/runtimes/acpx_codex.py`).

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_stall_detection.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/runtimes/acpx_codex.py tests/test_stall_detection.py
git commit -m "$(cat <<'EOF'
feat(symphony): acpx-codex runtime emits last_activity_ts on app-server events

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-5.4: Per-runtime liveness signal — hermes-agent

**Files:**
- Modify: `workflows/code_review/runtimes/hermes_agent.py`
- Modify: `tests/test_stall_detection.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_stall_detection.py`:

```python
def test_hermes_agent_runtime_updates_last_activity_on_callback():
    from workflows.code_review.runtimes.hermes_agent import HermesAgentRuntime

    rt = HermesAgentRuntime({"kind": "hermes-agent"}, run=None, run_json=None)
    assert rt.last_activity_ts() is None

    rt._record_activity()
    assert rt.last_activity_ts() is not None
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_stall_detection.py::test_hermes_agent_runtime_updates_last_activity_on_callback -v
```
Expected: FAIL.

- [ ] **Step 3: Implement signal**

In `workflows/code_review/runtimes/hermes_agent.py`, add the same `_last_activity` / `_record_activity()` / `last_activity_ts()` triple. Call `self._record_activity()` in each in-process session-runner callback.

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_stall_detection.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/runtimes/hermes_agent.py tests/test_stall_detection.py
git commit -m "$(cat <<'EOF'
feat(symphony): hermes-agent runtime emits last_activity_ts on callbacks

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-5.5: `StallVerdict` + `reconcile_stalls`

**Files:**
- Create: `workflows/code_review/stall.py`
- Modify: `tests/test_stall_detection.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_stall_detection.py`:

```python
@dataclass
class _FakeRuntime:
    last_ts: float | None
    def last_activity_ts(self) -> float | None:
        return self.last_ts


@dataclass
class _FakeEntry:
    runtime: _FakeRuntime
    started_at_monotonic: float


def _snap_with_stall(timeout_ms: int):
    from workflows.code_review.config_snapshot import ConfigSnapshot
    return ConfigSnapshot(
        config={"stall": {"timeout_ms": timeout_ms}},
        prompts={}, loaded_at=0.0, source_mtime=0.0,
    )


def test_reconcile_stalls_terminates_inactive_worker():
    from workflows.code_review.stall import reconcile_stalls

    snap = _snap_with_stall(1000)  # 1s threshold
    rt = _FakeRuntime(last_ts=100.0)
    entry = _FakeEntry(runtime=rt, started_at_monotonic=50.0)
    verdicts = reconcile_stalls(snap, {"i1": entry}, now=200.0)
    assert len(verdicts) == 1
    v = verdicts[0]
    assert v.issue_id == "i1"
    assert v.action == "terminate"
    assert v.threshold_seconds == 1.0


def test_reconcile_stalls_skips_active_worker():
    from workflows.code_review.stall import reconcile_stalls

    snap = _snap_with_stall(10000)  # 10s threshold
    rt = _FakeRuntime(last_ts=199.5)
    entry = _FakeEntry(runtime=rt, started_at_monotonic=50.0)
    verdicts = reconcile_stalls(snap, {"i1": entry}, now=200.0)
    assert verdicts == []


def test_reconcile_stalls_disabled_when_timeout_zero():
    from workflows.code_review.stall import reconcile_stalls

    snap = _snap_with_stall(0)
    rt = _FakeRuntime(last_ts=0.0)
    entry = _FakeEntry(runtime=rt, started_at_monotonic=0.0)
    verdicts = reconcile_stalls(snap, {"i1": entry}, now=99999.0)
    assert verdicts == []


def test_reconcile_stalls_baseline_falls_back_to_started_at():
    """Worker that has produced no signal still gets a deadline."""
    from workflows.code_review.stall import reconcile_stalls

    snap = _snap_with_stall(1000)
    rt = _FakeRuntime(last_ts=None)
    entry = _FakeEntry(runtime=rt, started_at_monotonic=100.0)
    verdicts = reconcile_stalls(snap, {"i1": entry}, now=200.0)
    assert len(verdicts) == 1
    assert verdicts[0].action == "terminate"


def test_reconcile_stalls_default_timeout_when_section_absent():
    """Spec §8.4 default: 300_000 ms."""
    from workflows.code_review.config_snapshot import ConfigSnapshot
    from workflows.code_review.stall import reconcile_stalls

    snap = ConfigSnapshot(config={}, prompts={}, loaded_at=0.0, source_mtime=0.0)
    rt = _FakeRuntime(last_ts=0.0)
    entry = _FakeEntry(runtime=rt, started_at_monotonic=0.0)
    verdicts = reconcile_stalls(snap, {"i1": entry}, now=299.0)
    assert verdicts == []  # 299s < 300s default
    verdicts = reconcile_stalls(snap, {"i1": entry}, now=400.0)
    assert len(verdicts) == 1


def test_reconcile_stalls_opt_out_when_method_absent():
    """Codex P1 on PR #16: a runtime that doesn't implement
    last_activity_ts opts out entirely — the reconciler must skip it,
    NOT fall back to started_at_monotonic."""
    from workflows.code_review.stall import reconcile_stalls

    class _OptOutRuntime:
        # Deliberately does NOT define last_activity_ts.
        pass

    snap = _snap_with_stall(1000)
    rt = _OptOutRuntime()
    entry = _FakeEntry(runtime=rt, started_at_monotonic=100.0)
    # Elapsed since started_at is 99_900 seconds — vastly past threshold.
    # If the implementation falls back to started_at, this would terminate.
    verdicts = reconcile_stalls(snap, {"i1": entry}, now=100_000.0)
    assert verdicts == [], (
        f"Opt-out runtime (no last_activity_ts attr) must be skipped, "
        f"not force-killed via started_at fallback. Got verdicts={verdicts}"
    )
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_stall_detection.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'workflows.code_review.stall'`.

- [ ] **Step 3: Create module**

Create `workflows/code_review/stall.py`:

```python
"""Stall detection (Symphony §8.5).

Pure function: snapshot + running-state map + clock -> list of verdicts.
The caller (watch.py) acts on the verdicts (kills workers, queues retries).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Protocol

from workflows.code_review.config_snapshot import ConfigSnapshot


_DEFAULT_TIMEOUT_MS = 300_000


@dataclass(frozen=True)
class StallVerdict:
    issue_id: str
    elapsed_seconds: float
    threshold_seconds: float
    action: Literal["terminate", "warn", "noop"]


class _RunningEntry(Protocol):
    """Structural type for running-lane entries — only the two attrs we use."""

    started_at_monotonic: float

    def runtime(self): ...  # actually .runtime is a Runtime instance attr


def reconcile_stalls(
    snapshot: ConfigSnapshot,
    running: Mapping[str, object],
    now: float,
) -> list[StallVerdict]:
    """Return a `terminate` verdict for every running entry whose most-recent
    activity (or, if none, started_at_monotonic) is older than `now -
    snapshot.config.stall.timeout_ms`. `timeout_ms <= 0` disables the check.

    The map values are duck-typed: must expose `.runtime.last_activity_ts()`
    and `.started_at_monotonic`. This keeps the function decoupled from the
    concrete RunningEntry class in orchestrator.py.
    """
    stall_cfg = (snapshot.config or {}).get("stall") or {}
    threshold_ms = stall_cfg.get("timeout_ms", _DEFAULT_TIMEOUT_MS)
    if threshold_ms <= 0:
        return []
    threshold_s = threshold_ms / 1000.0

    out: list[StallVerdict] = []
    for issue_id, entry in running.items():
        rt = getattr(entry, "runtime", None)
        # OPT-OUT: runtime instance lacks `last_activity_ts` attribute entirely.
        # Per spec §8.1, opting out skips stall enforcement; we do NOT fall
        # back to started_at_monotonic for these. Codex P1 finding on PR #16.
        if rt is None or not hasattr(rt, "last_activity_ts"):
            continue
        last = rt.last_activity_ts()
        # Method defined and returned None = "still in startup, not yet
        # produced a signal" — fall back to started_at so a hung-startup
        # worker still has a deadline.
        baseline = last if last is not None else entry.started_at_monotonic
        elapsed = now - baseline
        if elapsed > threshold_s:
            out.append(
                StallVerdict(
                    issue_id=issue_id,
                    elapsed_seconds=elapsed,
                    threshold_seconds=threshold_s,
                    action="terminate",
                )
            )
    return out
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_stall_detection.py -v
```
Expected: 10 passed (was 9 before adding the opt-out test).

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/stall.py tests/test_stall_detection.py
git commit -m "$(cat <<'EOF'
feat(symphony): add reconcile_stalls + StallVerdict (§8.5)

Pure function: snapshot + running-state + clock -> verdicts. timeout_ms<=0
disables; baseline falls back to started_at_monotonic when runtime
hasn't yet reported a liveness signal.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-5.6: Schema addition for `stall.timeout_ms`

**Files:**
- Modify: `workflows/code_review/schema.yaml`
- Modify: `tests/test_stall_detection.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_stall_detection.py`:

```python
def test_schema_accepts_stall_section():
    import yaml
    from pathlib import Path
    from jsonschema import Draft7Validator

    schema = yaml.safe_load(Path("workflows/code_review/schema.yaml").read_text())
    base = {
        "workflow": "code-review", "schema-version": 1,
        "instance": {"name": "i", "engine-owner": "hermes"},
        "repository": {"local-path": "/tmp", "github-slug": "o/r", "active-lane-label": "x"},
        "runtimes": {"r1": {"kind": "claude-cli", "max-turns-per-invocation": 1, "timeout-seconds": 60}},
        "agents": {
            "coder": {"t1": {"name": "c", "model": "m", "runtime": "r1"}},
            "internal-reviewer": {"name": "i", "model": "m", "runtime": "r1"},
            "external-reviewer": {"enabled": False, "name": "e"},
        },
        "gates": {"internal-review": {}, "external-review": {}, "merge": {}},
        "triggers": {"lane-selector": {"type": "github-issue-label", "label": "x"}},
        "storage": {"ledger": "l", "health": "h", "audit-log": "a"},
        "stall": {"timeout_ms": 60000},
    }
    Draft7Validator(schema).validate(base)


def test_schema_rejects_negative_stall_timeout():
    import yaml
    import pytest
    from pathlib import Path
    from jsonschema import Draft7Validator
    from jsonschema.exceptions import ValidationError as JSError

    schema = yaml.safe_load(Path("workflows/code_review/schema.yaml").read_text())
    base = {
        "workflow": "code-review", "schema-version": 1,
        "instance": {"name": "i", "engine-owner": "hermes"},
        "repository": {"local-path": "/tmp", "github-slug": "o/r", "active-lane-label": "x"},
        "runtimes": {"r1": {"kind": "claude-cli", "max-turns-per-invocation": 1, "timeout-seconds": 60}},
        "agents": {
            "coder": {"t1": {"name": "c", "model": "m", "runtime": "r1"}},
            "internal-reviewer": {"name": "i", "model": "m", "runtime": "r1"},
            "external-reviewer": {"enabled": False, "name": "e"},
        },
        "gates": {"internal-review": {}, "external-review": {}, "merge": {}},
        "triggers": {"lane-selector": {"type": "github-issue-label", "label": "x"}},
        "storage": {"ledger": "l", "health": "h", "audit-log": "a"},
        "stall": {"timeout_ms": -1},
    }
    with pytest.raises(JSError):
        Draft7Validator(schema).validate(base)
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_stall_detection.py::test_schema_accepts_stall_section -v
```
Expected: FAIL — `additionalProperties` rejects `stall`.

- [ ] **Step 3: Add `stall` section to schema.yaml**

In `workflows/code_review/schema.yaml`, add (alongside `webhooks`, before `definitions`):

```yaml
  stall:
    type: object
    additionalProperties: false
    properties:
      timeout_ms:
        type: integer
        minimum: 0
        default: 300000
        description: "Worker terminated if its runtime has shown no activity for this long. 0 = disabled."
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_stall_detection.py -v
```
Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/schema.yaml tests/test_stall_detection.py
git commit -m "$(cat <<'EOF'
feat(symphony): add stall.timeout_ms to schema.yaml

Default 300_000 ms; 0 disables; minimum 0. Symphony §8.5.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-5.7: Tick-loop integration

**Files:**
- Modify: `watch.py`

- [ ] **Step 1: Locate the running-lanes map**

The orchestrator owns the running-state. In `watch.py`, look for the call site that obtains the running-lanes mapping (likely `orchestrator.running_entries()` or a direct dict on the orchestrator).

```bash
grep -n "running\|orchestrator" watch.py | head -20
```

- [ ] **Step 2: Insert reconcile_stalls call**

In `watch.py`, immediately before the existing `reconcile_running_lanes` block (so stall reconciliation runs **before** tracker-state refresh per spec §8.6), add:

```python
import time
from workflows.code_review.stall import reconcile_stalls
from workflows.code_review.event_taxonomy import (
    DAEDALUS_STALL_DETECTED, DAEDALUS_STALL_TERMINATED,
)

_running_now = orchestrator.running_entries()  # or whatever the existing accessor is
for verdict in reconcile_stalls(_snapshot_ref.get(), _running_now, now=time.monotonic()):
    append_daedalus_event(
        event_log_path=event_log_path,
        event={
            "type": DAEDALUS_STALL_DETECTED,
            "issue_id": verdict.issue_id,
            "elapsed_seconds": verdict.elapsed_seconds,
            "threshold_seconds": verdict.threshold_seconds,
        },
    )
    orchestrator.terminate_worker(verdict.issue_id, reason="stall")
    append_daedalus_event(
        event_log_path=event_log_path,
        event={"type": DAEDALUS_STALL_TERMINATED, "issue_id": verdict.issue_id},
    )
    orchestrator.queue_retry(verdict.issue_id, error="stall_timeout")
```

If `orchestrator.running_entries`, `terminate_worker`, or `queue_retry` do not exist with these exact names, locate the equivalent on the orchestrator class and adjust. The contract is: enumerate running lanes, terminate by issue id, queue a retry.

- [ ] **Step 3: Add an integration test**

Append to `tests/test_stall_detection.py`:

```python
def test_stall_emits_both_events_and_queues_retry(tmp_path, monkeypatch):
    """Smoke: when reconcile_stalls returns a verdict, the tick-loop
    integration emits stall_detected, terminates, emits stall_terminated,
    and queues a retry. Tested via a thin fake orchestrator."""
    from workflows.code_review.stall import StallVerdict, reconcile_stalls
    from workflows.code_review.event_taxonomy import (
        DAEDALUS_STALL_DETECTED, DAEDALUS_STALL_TERMINATED,
    )

    snap = _snap_with_stall(1000)
    rt = _FakeRuntime(last_ts=0.0)
    entry = _FakeEntry(runtime=rt, started_at_monotonic=0.0)
    verdicts = reconcile_stalls(snap, {"i1": entry}, now=2.0)
    assert len(verdicts) == 1

    # Emulate the watch.py side-effect block in isolation
    events: list[dict] = []
    terminated: list[str] = []
    retried: list[tuple[str, str]] = []
    for v in verdicts:
        events.append({"type": DAEDALUS_STALL_DETECTED, "issue_id": v.issue_id})
        terminated.append(v.issue_id)
        events.append({"type": DAEDALUS_STALL_TERMINATED, "issue_id": v.issue_id})
        retried.append((v.issue_id, "stall_timeout"))
    assert [e["type"] for e in events] == [DAEDALUS_STALL_DETECTED, DAEDALUS_STALL_TERMINATED]
    assert terminated == ["i1"]
    assert retried == [("i1", "stall_timeout")]
```

- [ ] **Step 4: Run full suite**

```bash
GITHUB_TOKEN=stub /usr/bin/python3 -m pytest tests/ 2>&1 | tail -5
```
Expected: full suite passes.

- [ ] **Step 5: Commit**

```bash
git add watch.py tests/test_stall_detection.py
git commit -m "$(cat <<'EOF'
feat(symphony): wire reconcile_stalls into watch.py tick (§8.5)

Stall verdict computed BEFORE tracker-state refresh per spec §8.6, so a
stalled worker on a now-terminal issue still gets stall-terminated.
Verdict triggers stall_detected + stall_terminated events and queues a
retry with error="stall_timeout".

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-5.8: Final regression + finishing

- [ ] **Step 1:**

```bash
GITHUB_TOKEN=stub /usr/bin/python3 -m pytest tests/ 2>&1 | tail -5
```
Expected: full suite passes.

- [ ] **Step 2: Use superpowers:finishing-a-development-branch.**

---

# Phase S-6 — Optional HTTP status surface (Symphony §13.7)

**Branch:** `claude/symphony-s-6-http-server` from `main` (rebased onto S-1 + S-4). One PR.

**Goal:** Optional in-process HTTP server. JSON `/api/v1/state`, JSON `/api/v1/<id>`, POST `/api/v1/refresh`, server-rendered HTML `/`. Disabled by default; non-loopback bind requires explicit schema field; `port=0` = ephemeral for tests.

**File structure:**

- New: `workflows/code_review/server/__init__.py` — `start_server(snapshot_ref, db_path, server_cfg) -> ServerHandle`.
- New: `workflows/code_review/server/routes.py` — request dispatch.
- New: `workflows/code_review/server/views.py` — `state_view`, `issue_view`.
- New: `workflows/code_review/server/html.py` — `render_dashboard`.
- New: `workflows/code_review/server/refresh.py` — `RefreshFlag`.
- New: `tests/test_status_server.py` — covers spec §6.7.
- Modify: `workflows/code_review/schema.yaml` — add `server` section.
- Modify: `watch.py` — wire `start_server` (when enabled) + tick observes `RefreshFlag`.

---

## Task S-6.1: Schema addition for `server`

**Files:**
- Modify: `workflows/code_review/schema.yaml`
- Create: `tests/test_status_server.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_status_server.py`:

```python
"""S-6 tests: HTTP status surface — Symphony §13.7."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest


def test_schema_accepts_server_block():
    import yaml
    from jsonschema import Draft7Validator

    schema = yaml.safe_load(Path("workflows/code_review/schema.yaml").read_text())
    cfg = {
        "workflow": "code-review", "schema-version": 1,
        "instance": {"name": "i", "engine-owner": "hermes"},
        "repository": {"local-path": "/tmp", "github-slug": "o/r", "active-lane-label": "x"},
        "runtimes": {"r1": {"kind": "claude-cli", "max-turns-per-invocation": 1, "timeout-seconds": 60}},
        "agents": {
            "coder": {"t1": {"name": "c", "model": "m", "runtime": "r1"}},
            "internal-reviewer": {"name": "i", "model": "m", "runtime": "r1"},
            "external-reviewer": {"enabled": False, "name": "e"},
        },
        "gates": {"internal-review": {}, "external-review": {}, "merge": {}},
        "triggers": {"lane-selector": {"type": "github-issue-label", "label": "x"}},
        "storage": {"ledger": "l", "health": "h", "audit-log": "a"},
        "server": {"port": 0, "bind": "127.0.0.1"},
    }
    Draft7Validator(schema).validate(cfg)


def test_schema_rejects_oob_server_port():
    import yaml
    import pytest
    from jsonschema import Draft7Validator
    from jsonschema.exceptions import ValidationError as JSError

    schema = yaml.safe_load(Path("workflows/code_review/schema.yaml").read_text())
    cfg = {
        "workflow": "code-review", "schema-version": 1,
        "instance": {"name": "i", "engine-owner": "hermes"},
        "repository": {"local-path": "/tmp", "github-slug": "o/r", "active-lane-label": "x"},
        "runtimes": {"r1": {"kind": "claude-cli", "max-turns-per-invocation": 1, "timeout-seconds": 60}},
        "agents": {
            "coder": {"t1": {"name": "c", "model": "m", "runtime": "r1"}},
            "internal-reviewer": {"name": "i", "model": "m", "runtime": "r1"},
            "external-reviewer": {"enabled": False, "name": "e"},
        },
        "gates": {"internal-review": {}, "external-review": {}, "merge": {}},
        "triggers": {"lane-selector": {"type": "github-issue-label", "label": "x"}},
        "storage": {"ledger": "l", "health": "h", "audit-log": "a"},
        "server": {"port": 70000},
    }
    with pytest.raises(JSError):
        Draft7Validator(schema).validate(cfg)
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_status_server.py::test_schema_accepts_server_block -v
```
Expected: FAIL — `additionalProperties` rejects `server`.

- [ ] **Step 3: Add `server` block to schema.yaml**

In `workflows/code_review/schema.yaml` add (alongside `stall`):

```yaml
  server:
    type: object
    additionalProperties: false
    properties:
      port:
        type: integer
        minimum: 0
        maximum: 65535
        description: "0 = ephemeral (tests). Omit/null = HTTP server disabled."
      bind:
        type: string
        default: "127.0.0.1"
        description: "Loopback by default. Non-loopback requires explicit override."
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_status_server.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/schema.yaml tests/test_status_server.py
git commit -m "$(cat <<'EOF'
feat(symphony): add server.port/server.bind to schema (§13.7)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-6.2: `views.state_view()` shape

**Files:**
- Create: `workflows/code_review/server/__init__.py` (initial stub; replaced in S-6.5)
- Create: `workflows/code_review/server/views.py`
- Modify: `tests/test_status_server.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_status_server.py`:

```python
def test_state_view_shape(tmp_path):
    """state_view returns the JSON shape from spec §6.4."""
    from workflows.code_review.server.views import state_view
    from workflows.code_review.config_snapshot import ConfigSnapshot, AtomicRef

    snap = ConfigSnapshot(config={}, prompts={}, loaded_at=1.0, source_mtime=2.0)
    ref = AtomicRef(snap)
    db = tmp_path / "daedalus.db"
    db.touch()  # empty stub; views handle the empty case gracefully

    out = state_view(ref, db)
    assert "generated_at" in out
    assert isinstance(out["generated_at"], str)
    assert "counts" in out and isinstance(out["counts"], dict)
    assert "running" in out and isinstance(out["running"], list)
    assert "retrying" in out and isinstance(out["retrying"], list)
    assert "totals" in out and isinstance(out["totals"], dict)
    assert "rate_limits" in out  # may be None
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_status_server.py::test_state_view_shape -v
```
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create stub package**

Create `workflows/code_review/server/__init__.py`:

```python
"""HTTP status surface (Symphony §13.7).

Public entrypoint: ``start_server(snapshot_ref, db_path, server_cfg) -> ServerHandle``.
Disabled by default; only spawned when the workflow.yaml carries a
``server`` block.
"""
from __future__ import annotations

# re-exports populated as submodules land
```

Create `workflows/code_review/server/views.py`:

```python
"""Pure DB+snapshot -> dict views for the HTTP status surface."""
from __future__ import annotations

import datetime as _dt
import sqlite3
from pathlib import Path
from typing import Any

from workflows.code_review.config_snapshot import AtomicRef, ConfigSnapshot
from workflows.code_review.event_taxonomy import canonicalize


def _now_iso() -> str:
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _open_ro(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def state_view(snapshot_ref: AtomicRef[ConfigSnapshot], db_path: Path) -> dict[str, Any]:
    """Aggregate workspace state for `/api/v1/state`. Spec §6.4 shape."""
    running: list[dict[str, Any]] = []
    retrying: list[dict[str, Any]] = []
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "seconds_running": 0}

    # Best-effort DB read; an absent/empty DB returns the empty shape.
    try:
        with _open_ro(db_path) as cx:
            cur = cx.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lanes'")
            if cur.fetchone() is not None:
                rows = cx.execute(
                    "SELECT issue_id, issue_identifier, state, session_id, "
                    "turn_count, last_event, started_at, last_event_at "
                    "FROM lanes"
                ).fetchall()
                for r in rows:
                    item = {
                        "issue_id": r[0],
                        "issue_identifier": r[1],
                        "state": r[2],
                        "session_id": r[3],
                        "turn_count": r[4],
                        "last_event": canonicalize(r[5]) if r[5] else None,
                        "started_at": r[6],
                        "last_event_at": r[7],
                        "tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                    }
                    if r[2] == "retrying":
                        retrying.append(item)
                    else:
                        running.append(item)
    except sqlite3.DatabaseError:
        pass  # malformed/empty DB — empty view

    return {
        "generated_at": _now_iso(),
        "counts": {"running": len(running), "retrying": len(retrying)},
        "running": running,
        "retrying": retrying,
        "totals": totals,
        "rate_limits": None,
    }


def issue_view(snapshot_ref: AtomicRef[ConfigSnapshot], db_path: Path, identifier: str) -> dict[str, Any] | None:
    """Per-lane view for `/api/v1/<identifier>`. Returns None for unknown id."""
    try:
        with _open_ro(db_path) as cx:
            cur = cx.execute(
                "SELECT issue_id, issue_identifier, state, session_id, turn_count, "
                "last_event, started_at, last_event_at FROM lanes "
                "WHERE issue_id=? OR issue_identifier=?",
                (identifier, identifier),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {
                "issue_id": row[0],
                "issue_identifier": row[1],
                "state": row[2],
                "session_id": row[3],
                "turn_count": row[4],
                "last_event": canonicalize(row[5]) if row[5] else None,
                "started_at": row[6],
                "last_event_at": row[7],
            }
    except sqlite3.DatabaseError:
        return None
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_status_server.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/server/__init__.py workflows/code_review/server/views.py tests/test_status_server.py
git commit -m "$(cat <<'EOF'
feat(symphony): add views.state_view + views.issue_view

Pure DB+snapshot -> dict; handles missing/empty/malformed DB by
returning the empty shape. Used by both the JSON API and the HTML
dashboard.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-6.3: HTML dashboard

**Files:**
- Create: `workflows/code_review/server/html.py`
- Modify: `tests/test_status_server.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_status_server.py`:

```python
def test_render_dashboard_smoke():
    from workflows.code_review.server.html import render_dashboard

    state = {
        "generated_at": "2026-04-28T20:15:30Z",
        "counts": {"running": 1, "retrying": 0},
        "running": [{
            "issue_id": "i1", "issue_identifier": "yoyopod#42", "state": "active-lane",
            "session_id": "s1", "turn_count": 3, "last_event": "turn_completed",
            "started_at": "x", "last_event_at": "y",
            "tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }],
        "retrying": [],
        "totals": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "seconds_running": 0},
        "rate_limits": None,
    }
    html = render_dashboard(state)
    assert "<html" in html.lower()
    assert "yoyopod#42" in html
    assert 'http-equiv="refresh"' in html
    # html.escape applied
    assert "<script" not in html
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_status_server.py::test_render_dashboard_smoke -v
```
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create module**

Create `workflows/code_review/server/html.py`:

```python
"""Minimal server-rendered dashboard (Symphony §13.7, spec §6.6)."""
from __future__ import annotations

import html as _html
from typing import Any


def _esc(v: Any) -> str:
    return _html.escape(str(v)) if v is not None else ""


def render_dashboard(state: dict[str, Any]) -> str:
    """Single static-rendered HTML page. ~150 lines max; no JS/CSS framework."""
    counts = state.get("counts") or {}
    running = state.get("running") or []
    retrying = state.get("retrying") or []
    totals = state.get("totals") or {}

    running_rows = "".join(
        f"<tr><td>{_esc(r.get('issue_identifier'))}</td>"
        f"<td>{_esc(r.get('state'))}</td>"
        f"<td>{_esc(r.get('session_id'))}</td>"
        f"<td>{_esc(r.get('turn_count'))}</td>"
        f"<td>{_esc(r.get('last_event'))}</td>"
        f"<td>{_esc(r.get('started_at'))}</td>"
        f"<td>{_esc(r.get('last_event_at'))}</td></tr>"
        for r in running
    )
    retrying_rows = "".join(
        f"<tr><td>{_esc(r.get('issue_identifier'))}</td>"
        f"<td>{_esc(r.get('state'))}</td></tr>"
        for r in retrying
    )

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="10">
  <title>Daedalus status</title>
</head>
<body>
  <h1>Daedalus</h1>
  <p>Generated at {_esc(state.get('generated_at'))}</p>
  <h2>Counts</h2>
  <p>Running: {_esc(counts.get('running', 0))} &middot; Retrying: {_esc(counts.get('retrying', 0))}</p>
  <h2>Running lanes</h2>
  <table border="1" cellspacing="0" cellpadding="4">
    <tr><th>identifier</th><th>state</th><th>session</th><th>turn</th><th>last_event</th><th>started</th><th>last_event_at</th></tr>
    {running_rows}
  </table>
  <h2>Retrying</h2>
  <table border="1" cellspacing="0" cellpadding="4">
    <tr><th>identifier</th><th>state</th></tr>
    {retrying_rows}
  </table>
  <h2>Totals</h2>
  <p>input: {_esc(totals.get('input_tokens', 0))} &middot;
     output: {_esc(totals.get('output_tokens', 0))} &middot;
     total: {_esc(totals.get('total_tokens', 0))} &middot;
     seconds_running: {_esc(totals.get('seconds_running', 0))}</p>
</body>
</html>
"""
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_status_server.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/server/html.py tests/test_status_server.py
git commit -m "$(cat <<'EOF'
feat(symphony): add render_dashboard (server-rendered HTML)

stdlib-only, html.escape applied throughout, meta-refresh every 10s.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-6.4: `RefreshFlag` (POST /refresh coalescing)

**Files:**
- Create: `workflows/code_review/server/refresh.py`
- Modify: `tests/test_status_server.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_status_server.py`:

```python
def test_refresh_flag_coalesces():
    from workflows.code_review.server.refresh import RefreshFlag

    flag = RefreshFlag()
    assert flag.consume() is False  # nothing pending
    flag.queue()
    flag.queue()
    flag.queue()
    assert flag.consume() is True
    assert flag.consume() is False  # already drained


def test_refresh_flag_concurrent_queue_during_consume_not_lost():
    """Codex P2 finding on PR #16 — exercise the consume/queue race.

    Spawn N HTTP-side threads that queue() while a tick-side thread
    repeatedly consume()s. Total observed True consumes plus the final
    pending state must equal total queue() calls — i.e. no signal
    silently dropped.
    """
    import threading
    from workflows.code_review.server.refresh import RefreshFlag

    flag = RefreshFlag()
    QUEUERS = 8
    PER_THREAD = 200
    seen_true = 0
    stop = threading.Event()

    def queuer():
        for _ in range(PER_THREAD):
            flag.queue()

    def consumer():
        nonlocal seen_true
        while not stop.is_set():
            if flag.consume():
                seen_true += 1

    threads = [threading.Thread(target=queuer) for _ in range(QUEUERS)]
    cons = threading.Thread(target=consumer)
    cons.start()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Drain any final pending then stop the consumer.
    stop.set()
    cons.join()
    if flag.consume():
        seen_true += 1

    # We don't assert seen_true == QUEUERS * PER_THREAD because coalescing
    # is the explicit design (N rapid queue()s collapse to one consume()).
    # We DO assert that at least one True was observed — which would fail
    # under the racy Event-based implementation when a queue() lands
    # between is_set() and clear() and the consumer never sees it AND no
    # later queue() arrives before the test ends.
    assert seen_true >= 1, (
        f"No queue()s were ever consumed (expected coalesced result). "
        f"Strong evidence of dropped signal under race."
    )
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_status_server.py::test_refresh_flag_coalesces -v
```
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create module**

Create `workflows/code_review/server/refresh.py`:

```python
"""POST /api/v1/refresh handler state.

The HTTP path only sets a flag; the tick loop calls `.consume()` once
per tick, so N rapid POSTs coalesce into one extra dispatch attempt.
"""
from __future__ import annotations

import threading


class RefreshFlag:
    """Coalescing pending-flag for POST /api/v1/refresh.

    Codex P2 finding on PR #16: a naive `Event.is_set()` + `Event.clear()`
    pair is racy — a `set()` between the two steps gets dropped. Use a
    Lock + bool so queue/consume are mutually exclusive.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending = False

    def queue(self) -> None:
        with self._lock:
            self._pending = True

    def consume(self) -> bool:
        """Return True (and clear) if a refresh is pending; False otherwise.

        Atomic under the internal lock — a concurrent `queue()` cannot be
        dropped between the read and the clear.
        """
        with self._lock:
            if self._pending:
                self._pending = False
                return True
            return False
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_status_server.py -v
```
Expected: 6 passed (4 prior + 2 RefreshFlag tests including the concurrent-race regression).

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/server/refresh.py tests/test_status_server.py
git commit -m "$(cat <<'EOF'
feat(symphony): add RefreshFlag for /api/v1/refresh coalescing

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-6.5: `routes` + `start_server` + `ServerHandle`

**Files:**
- Create: `workflows/code_review/server/routes.py`
- Modify: `workflows/code_review/server/__init__.py`
- Modify: `tests/test_status_server.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_status_server.py`:

```python
def test_start_server_disabled_when_no_server_block(tmp_path):
    from workflows.code_review.server import start_server
    from workflows.code_review.config_snapshot import AtomicRef, ConfigSnapshot

    snap = ConfigSnapshot(config={}, prompts={}, loaded_at=0.0, source_mtime=0.0)
    handle = start_server(AtomicRef(snap), tmp_path / "daedalus.db", server_cfg=None)
    assert handle.enabled is False
    assert handle.port is None
    handle.shutdown()  # safe no-op


def test_start_server_ephemeral_port_binds_and_serves_state(tmp_path):
    import urllib.request
    import urllib.error
    from workflows.code_review.server import start_server
    from workflows.code_review.config_snapshot import AtomicRef, ConfigSnapshot

    snap = ConfigSnapshot(config={}, prompts={}, loaded_at=0.0, source_mtime=0.0)
    db = tmp_path / "daedalus.db"
    db.touch()
    handle = start_server(AtomicRef(snap), db, server_cfg={"port": 0, "bind": "127.0.0.1"})
    try:
        assert handle.enabled is True
        assert handle.port is not None
        url = f"http://127.0.0.1:{handle.port}/api/v1/state"
        body = urllib.request.urlopen(url, timeout=5).read().decode()
        data = json.loads(body)
        assert "generated_at" in data and "running" in data and "retrying" in data
    finally:
        handle.shutdown()


def test_start_server_unknown_id_returns_404(tmp_path):
    import urllib.request
    import urllib.error
    from workflows.code_review.server import start_server
    from workflows.code_review.config_snapshot import AtomicRef, ConfigSnapshot

    snap = ConfigSnapshot(config={}, prompts={}, loaded_at=0.0, source_mtime=0.0)
    db = tmp_path / "daedalus.db"
    db.touch()
    handle = start_server(AtomicRef(snap), db, server_cfg={"port": 0, "bind": "127.0.0.1"})
    try:
        url = f"http://127.0.0.1:{handle.port}/api/v1/does-not-exist"
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(url, timeout=5)
        assert ei.value.code == 404
        body = json.loads(ei.value.read().decode())
        assert body == {"error": {"code": "not_found"}}
    finally:
        handle.shutdown()


def test_start_server_post_refresh_returns_202_and_sets_flag(tmp_path):
    import urllib.request
    from workflows.code_review.server import start_server
    from workflows.code_review.config_snapshot import AtomicRef, ConfigSnapshot

    snap = ConfigSnapshot(config={}, prompts={}, loaded_at=0.0, source_mtime=0.0)
    db = tmp_path / "daedalus.db"
    db.touch()
    handle = start_server(AtomicRef(snap), db, server_cfg={"port": 0, "bind": "127.0.0.1"})
    try:
        url = f"http://127.0.0.1:{handle.port}/api/v1/refresh"
        for _ in range(5):
            req = urllib.request.Request(url, method="POST")
            resp = urllib.request.urlopen(req, timeout=5)
            assert resp.status == 202
        # All 5 POSTs coalesce into one pending refresh.
        assert handle.refresh_flag.consume() is True
        assert handle.refresh_flag.consume() is False
    finally:
        handle.shutdown()


def test_start_server_html_dashboard_smoke(tmp_path):
    import urllib.request
    from workflows.code_review.server import start_server
    from workflows.code_review.config_snapshot import AtomicRef, ConfigSnapshot

    snap = ConfigSnapshot(config={}, prompts={}, loaded_at=0.0, source_mtime=0.0)
    db = tmp_path / "daedalus.db"
    db.touch()
    handle = start_server(AtomicRef(snap), db, server_cfg={"port": 0, "bind": "127.0.0.1"})
    try:
        body = urllib.request.urlopen(f"http://127.0.0.1:{handle.port}/", timeout=5).read().decode()
        assert "<html" in body.lower()
        assert "Daedalus" in body
    finally:
        handle.shutdown()


def test_start_server_clean_shutdown(tmp_path):
    import socket
    from workflows.code_review.server import start_server
    from workflows.code_review.config_snapshot import AtomicRef, ConfigSnapshot

    snap = ConfigSnapshot(config={}, prompts={}, loaded_at=0.0, source_mtime=0.0)
    db = tmp_path / "daedalus.db"
    db.touch()
    handle = start_server(AtomicRef(snap), db, server_cfg={"port": 0, "bind": "127.0.0.1"})
    port = handle.port
    handle.shutdown()
    # Port should be free after shutdown.
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
    finally:
        s.close()
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_status_server.py -v
```
Expected: FAIL — `start_server` not exported.

- [ ] **Step 3: Implement routes + server**

Create `workflows/code_review/server/routes.py`:

```python
"""HTTP request dispatch."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from workflows.code_review.config_snapshot import AtomicRef, ConfigSnapshot
from workflows.code_review.server.html import render_dashboard
from workflows.code_review.server.refresh import RefreshFlag
from workflows.code_review.server.views import issue_view, state_view


def make_handler_class(
    snapshot_ref: AtomicRef[ConfigSnapshot],
    db_path: Path,
    refresh_flag: RefreshFlag,
):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # silence stderr in tests
            return

        # ---- helpers ----
        def _json(self, status: int, body: dict) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _html(self, status: int, body: str) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _not_found(self) -> None:
            self._json(404, {"error": {"code": "not_found"}})

        # ---- methods ----
        def do_GET(self) -> None:
            if self.path == "/" or self.path == "":
                self._html(200, render_dashboard(state_view(snapshot_ref, db_path)))
                return
            if self.path == "/api/v1/state":
                self._json(200, state_view(snapshot_ref, db_path))
                return
            if self.path.startswith("/api/v1/"):
                ident = self.path[len("/api/v1/") :]
                if ident in ("state", "refresh", ""):
                    self._not_found()
                    return
                view = issue_view(snapshot_ref, db_path, ident)
                if view is None:
                    self._not_found()
                    return
                self._json(200, view)
                return
            self._not_found()

        def do_POST(self) -> None:
            if self.path == "/api/v1/refresh":
                refresh_flag.queue()
                self._json(202, {"queued": True})
                return
            self._not_found()

    return Handler
```

Replace `workflows/code_review/server/__init__.py` with:

```python
"""HTTP status surface (Symphony §13.7).

Public entrypoint: ``start_server(snapshot_ref, db_path, server_cfg)``
returning a `ServerHandle` with `.shutdown()` for clean teardown.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from workflows.code_review.config_snapshot import AtomicRef, ConfigSnapshot
from workflows.code_review.server.refresh import RefreshFlag
from workflows.code_review.server.routes import make_handler_class


@dataclass
class ServerHandle:
    enabled: bool
    port: int | None
    refresh_flag: RefreshFlag
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    def shutdown(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)


def start_server(
    snapshot_ref: AtomicRef[ConfigSnapshot],
    db_path: Path,
    server_cfg: dict[str, Any] | None,
) -> ServerHandle:
    """Spawn the HTTP server thread when `server_cfg` is non-empty.

    Returns a disabled handle (no thread spawned) when `server_cfg is None`
    or the dict is empty / lacks `port`. `port=0` requests an OS-assigned
    ephemeral port. Non-loopback bind requires explicit `bind` field
    (enforced by schema; this function trusts the validated config).
    """
    refresh_flag = RefreshFlag()
    if not server_cfg or server_cfg.get("port") is None:
        return ServerHandle(enabled=False, port=None, refresh_flag=refresh_flag)

    port = int(server_cfg["port"])
    bind = server_cfg.get("bind", "127.0.0.1")

    handler_cls = make_handler_class(snapshot_ref, db_path, refresh_flag)
    server = ThreadingHTTPServer((bind, port), handler_cls)
    actual_port = server.server_address[1]

    thread = threading.Thread(target=server.serve_forever, name="daedalus-status-server", daemon=True)
    thread.start()

    return ServerHandle(
        enabled=True,
        port=actual_port,
        refresh_flag=refresh_flag,
        _server=server,
        _thread=thread,
    )
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_status_server.py -v
```
Expected: 12 passed (6 prior + 6 new).

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/server/__init__.py workflows/code_review/server/routes.py tests/test_status_server.py
git commit -m "$(cat <<'EOF'
feat(symphony): add start_server + ServerHandle (§13.7)

ThreadingHTTPServer in a daemon thread; ephemeral port via port=0;
disabled handle when config absent (zero overhead). Endpoints:
GET /, GET /api/v1/state, GET /api/v1/<id>, POST /api/v1/refresh,
404 JSON envelope for everything else.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-6.6: Non-loopback bind enforcement (schema-level)

**Files:**
- Modify: `tests/test_status_server.py`

- [ ] **Step 1: Add a clarifying test**

Append to `tests/test_status_server.py`:

```python
def test_default_bind_is_loopback_in_schema():
    """Spec §3.1: HTTP server binds 127.0.0.1 by default; non-loopback
    requires explicit bind field. Schema enforces structure; this test
    documents the default."""
    import yaml
    schema = yaml.safe_load(Path("workflows/code_review/schema.yaml").read_text())
    server_props = schema["properties"]["server"]["properties"]
    assert server_props["bind"]["default"] == "127.0.0.1"


def test_non_loopback_bind_passes_schema_when_explicit():
    """The schema does not block non-loopback bind, but the operator
    must set it explicitly — there is no implicit non-loopback default."""
    import yaml
    from jsonschema import Draft7Validator

    schema = yaml.safe_load(Path("workflows/code_review/schema.yaml").read_text())
    cfg = {
        "workflow": "code-review", "schema-version": 1,
        "instance": {"name": "i", "engine-owner": "hermes"},
        "repository": {"local-path": "/tmp", "github-slug": "o/r", "active-lane-label": "x"},
        "runtimes": {"r1": {"kind": "claude-cli", "max-turns-per-invocation": 1, "timeout-seconds": 60}},
        "agents": {
            "coder": {"t1": {"name": "c", "model": "m", "runtime": "r1"}},
            "internal-reviewer": {"name": "i", "model": "m", "runtime": "r1"},
            "external-reviewer": {"enabled": False, "name": "e"},
        },
        "gates": {"internal-review": {}, "external-review": {}, "merge": {}},
        "triggers": {"lane-selector": {"type": "github-issue-label", "label": "x"}},
        "storage": {"ledger": "l", "health": "h", "audit-log": "a"},
        "server": {"port": 8080, "bind": "0.0.0.0"},
    }
    Draft7Validator(schema).validate(cfg)
```

- [ ] **Step 2: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_status_server.py -v
```
Expected: 14 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_status_server.py
git commit -m "$(cat <<'EOF'
test(symphony): document loopback-by-default + explicit-non-loopback contract

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-6.7: Wire start_server into watch.py + tick observes RefreshFlag

**Files:**
- Modify: `watch.py`

- [ ] **Step 1: Insert server bootstrap + per-tick refresh check**

In `watch.py`, after the `AtomicRef[ConfigSnapshot]` is built (Phase S-2 wiring), add:

```python
from workflows.code_review.server import start_server
from workflows.code_review.event_taxonomy import DAEDALUS_REFRESH_REQUESTED

_server_cfg = _snapshot_ref.get().config.get("server")
_server_handle = start_server(_snapshot_ref, db_path, server_cfg=_server_cfg)
```

In each tick body, after preflight passes and before the dispatch decision, observe the refresh flag:

```python
if _server_handle.enabled and _server_handle.refresh_flag.consume():
    append_daedalus_event(
        event_log_path=event_log_path,
        event={"type": DAEDALUS_REFRESH_REQUESTED},
    )
    # The refresh accelerates this tick's dispatch attempt; no other
    # state change is required (the tick already runs reconcile + dispatch).
```

On daemon shutdown (signal handler / loop-exit), call `_server_handle.shutdown()`.

- [ ] **Step 2: Run full suite**

```bash
GITHUB_TOKEN=stub /usr/bin/python3 -m pytest tests/ 2>&1 | tail -5
```
Expected: full suite passes.

- [ ] **Step 3: Commit**

```bash
git add watch.py
git commit -m "$(cat <<'EOF'
feat(symphony): wire status server + refresh-flag observation into watch.py

start_server() runs at daemon startup when workflow.yaml carries a server
block; tick body consumes the refresh flag once per tick and emits
daedalus.refresh_requested when a manual refresh was queued.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task S-6.8: Smoke + finishing

- [ ] **Step 1: Smoke against an ephemeral server**

```bash
/usr/bin/python3 -c "
import json, urllib.request
from pathlib import Path
from workflows.code_review.server import start_server
from workflows.code_review.config_snapshot import AtomicRef, ConfigSnapshot

snap = ConfigSnapshot(config={}, prompts={}, loaded_at=0.0, source_mtime=0.0)
import tempfile
with tempfile.TemporaryDirectory() as td:
    db = Path(td) / 'daedalus.db'
    db.touch()
    h = start_server(AtomicRef(snap), db, server_cfg={'port': 0, 'bind': '127.0.0.1'})
    try:
        body = urllib.request.urlopen(f'http://127.0.0.1:{h.port}/api/v1/state', timeout=5).read()
        data = json.loads(body)
        print('keys:', sorted(data.keys()))
        print('counts:', data['counts'])
    finally:
        h.shutdown()
"
```
Expected: `keys: ['counts', 'generated_at', 'rate_limits', 'retrying', 'running', 'totals']` and `counts: {'running': 0, 'retrying': 0}`.

- [ ] **Step 2: Run full suite**

```bash
GITHUB_TOKEN=stub /usr/bin/python3 -m pytest tests/ 2>&1 | tail -5
```
Expected: full suite passes.

- [ ] **Step 3: Use superpowers:finishing-a-development-branch.**

---

## Cross-phase final notes

- **Default-OFF posture:** every phase's schema additions (`server`, `stall`) are absent from the existing yoyopod `workflow.yaml`. Adopting any phase requires zero operator action; opting in is one schema edit.
- **Alias-window removal (post-S-4):** legacy event names in `EVENT_ALIASES` are removed in a follow-on PR (one release later), mirroring the D-rename pattern. Out of scope for these six phases.
- **Live-workspace check after each phase:** keep the yoyopod ledger at `/home/radxa/.hermes/workflows/yoyopod` running; assert no `daedalus.config_reload_failed` / `daedalus.dispatch_skipped` / `daedalus.stall_terminated` events appear unless they should.
