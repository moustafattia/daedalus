"""Font loading. Single source of truth for sizes/styles."""

from __future__ import annotations

from pathlib import Path

from PIL import ImageFont

from . import config


def _load(path, size: int) -> ImageFont.ImageFont:
    p = str(path)
    if Path(p).exists():
        return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# Lazy singletons so import-time work is cheap.
_cache: dict[tuple, ImageFont.ImageFont] = {}


def font(path, size: int) -> ImageFont.ImageFont:
    key = (str(path), size)
    if key not in _cache:
        _cache[key] = _load(path, size)
    return _cache[key]


# ── named families used across the banner ───────────────────────────────
def title() -> ImageFont.ImageFont:
    return font(config.FONT_DISPLAY, 100)


def subtitle() -> ImageFont.ImageFont:
    return font(config.FONT_DISPLAY, 38)


def subtitle_italic() -> ImageFont.ImageFont:
    return font(config.FONT_DISPLAY_ITALIC, 38)


def caption_serif_italic() -> ImageFont.ImageFont:
    return font(config.FONT_DISPLAY_ITALIC, 17)


def caption_sans() -> ImageFont.ImageFont:
    return font(config.FONT_SANS, 15)


def caption_sans_small() -> ImageFont.ImageFont:
    return font(config.FONT_SANS, 13)


def code() -> ImageFont.ImageFont:
    return font(config.FONT_MONO, 14)


def code_small() -> ImageFont.ImageFont:
    return font(config.FONT_MONO, 12)
