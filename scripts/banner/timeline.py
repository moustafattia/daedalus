"""Animation easing + per-element progress functions.

Every animated element has a `<element>_progress(frame) -> float` function
in this module that returns 0.0 (not started) → 1.0 (complete).

Tweak timings here to retune the animation without touching renderers.
"""
from __future__ import annotations

import math

from . import config

F = config.FRAMES


def ease(t: float) -> float:
    """Smooth in-out easing on [0, 1]."""
    t = max(0.0, min(1.0, t))
    return 0.5 - 0.5 * math.cos(math.pi * t)


def _ramp(frame: int, start: float, end: float) -> float:
    """Eased ramp from frame=start*F to frame=end*F."""
    if frame <= start * F:
        return 0.0
    if frame >= end * F:
        return 1.0
    return ease((frame - start * F) / ((end - start) * F))


# ── element progress functions ──────────────────────────────────────────

def constellation_progress(f: int) -> float:
    return _ramp(f, 0.00, 0.30)


def code_alpha(f: int, slot: int) -> int:
    """Three code blocks fade in staggered."""
    starts = [0.18, 0.30, 0.42]
    ramp = _ramp(f, starts[slot], starts[slot] + 0.18)
    return int(255 * ramp)


def brush_progress(f: int) -> float:
    return _ramp(f, 0.45, 0.62)


def underline_progress(f: int) -> float:
    return _ramp(f, 0.62, 0.74)


def hold_to_loop(f: int) -> float:
    """Constellation/icons dim slightly at end-of-loop for smooth wrap."""
    start = 0.90 * F
    if f < start:
        return 1.0
    return 1.0 - ease((f - start) / (F - start)) * 0.55
