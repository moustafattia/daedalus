#!/usr/bin/env python3
"""Thin entrypoint for the README banner generator.

Most of the logic lives in ``scripts/banner/`` as a modular package so
each visual element (parchment, hero emblem, constellation, code overlays,
icons, text, animated flow) can be modified or replaced independently.

Run::

    /usr/bin/python3 scripts/build_banner_gif.py

Or::

    /usr/bin/python3 -m scripts.banner

Both write to ``assets/daedalus-banner.gif``.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow the script to be run directly (without -m). Add repo root so
# `from scripts.banner import build` resolves.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.banner import build  # noqa: E402


if __name__ == "__main__":
    build()
