"""Coalescing tick-trigger for ``POST /api/v1/refresh``.

Daedalus is a CLI-tick architecture — there is no in-process tick loop
to share state with. So instead of poking a ``threading.Event`` that the
tick observes, the refresh endpoint shells out a tick subprocess best
effort. Concurrent refresh requests collapse into one subprocess per
debounce window, so a flurry of clicks on the dashboard refresh button
spawns at most one tick (per second) rather than one per click.
"""
from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from workflows.change_delivery.paths import workflow_cli_argv


class RefreshController:
    """Best-effort, fire-and-forget tick trigger with debounce coalescing.

    Concurrency model:
      - ``trigger()`` is safe to call from multiple HTTP worker threads.
      - A monotonic clock + ``threading.Lock`` ensure exactly one
        ``Popen`` invocation per ``DEBOUNCE_SECONDS`` window.
      - The spawned subprocess is intentionally not waited on; its
        stdout/stderr are discarded. The HTTP layer does not surface
        tick exit codes — the source of truth remains the events log.
    """

    DEBOUNCE_SECONDS: float = 1.0

    def __init__(self, workflow_root: Path) -> None:
        self._lock = threading.Lock()
        self._workflow_root = Path(workflow_root)
        self._last_trigger_at: float = 0.0

    def trigger(self) -> bool:
        """Fire a tick subprocess unless one was fired within the debounce.

        Returns:
            True if a tick was spawned by this call, False if it was
            coalesced into a recent prior trigger.
        """
        now = time.monotonic()
        with self._lock:
            if now - self._last_trigger_at < self.DEBOUNCE_SECONDS:
                return False
            self._last_trigger_at = now
        # Codex P1 on PR #22: invoke via the plugin entrypoint, not
        # ``-m workflows.change_delivery``. The ``-m`` form requires the
        # child to import ``workflows`` from its sys.path, which only
        # works in the editable-source dev layout. In installed
        # deployments the package lives under the global plugin root and
        # the import path adjustment is done by ``__main__.py`` in-process.
        # ``workflow_cli_argv`` returns that plugin entrypoint path so the
        # subprocess works in both source and installed layouts.
        argv = workflow_cli_argv(
            self._workflow_root,
            "--workflow-root",
            str(self._workflow_root),
            "tick",
        )
        subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
