"""Sprints README banner generator.

Public surface::

    from scripts.banner import build
    build()

is equivalent to running ``scripts/build_banner_gif.py``.

The package is split so each visual element lives in one module and
can be modified or replaced without touching the others. See module
docstrings for the responsibilities of each piece.
"""

from __future__ import annotations

from . import config
from .encode import encode
from .render import Scene, render_frame


def build() -> None:
    print(f"rendering {config.FRAMES} frames @ {config.W}x{config.H} …")
    scene = Scene()
    frames = []
    for i in range(config.FRAMES):
        frames.append(render_frame(scene, i))
        if i % 10 == 0:
            print(f"  frame {i}/{config.FRAMES}")
    encode(frames)


__all__ = ["build", "Scene", "render_frame", "encode", "config"]
