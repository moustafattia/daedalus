"""Right-side hero image: load + chroma-key the engraved Daedalus emblem.

The vendored source (``assets/source/daedalus-emblem.jpg``) is a 17th-c.
line engraving — black ink on near-white paper. We drop the paper to
transparency via inverted luminance and tint the ink toward our editorial
INK colour so the emblem sits naturally on the parchment.
"""
from __future__ import annotations

from PIL import Image, ImageFilter

from . import config


def _resize_to_target(src: Image.Image) -> Image.Image:
    """Resize to height = BUST_TARGET_H, preserving aspect ratio."""
    ratio = config.BUST_TARGET_H / src.height
    return src.resize(
        (int(src.width * ratio), config.BUST_TARGET_H),
        Image.LANCZOS,
    )


def _key_engraving(src: Image.Image) -> Image.Image:
    """Drop the near-white background; tint ink toward INK colour.

    Uses inverted luminance as alpha (paper bright → transparent, ink
    dark → opaque) and remaps colour so dark pixels read as editorial
    ink rather than pure black.
    """
    grey = src.convert("L")
    g_px = grey.load()
    px = src.load()

    near = 215   # paper threshold — anything brighter is fully transparent
    far = 100    # ink threshold  — anything darker is fully opaque

    ink_r, ink_g, ink_b = config.INK
    for y in range(src.height):
        for x in range(src.width):
            v = g_px[x, y]
            if v >= near:
                a = 0
            elif v <= far:
                a = 255
            else:
                a = int((near - v) / (near - far) * 255)
            t = 1 - (v / 255)            # 0..1 darkness factor
            r2 = int(ink_r + (255 - ink_r) * (1 - t) * 0.8)
            g2 = int(ink_g + (255 - ink_g) * (1 - t) * 0.8)
            b2 = int(ink_b + (255 - ink_b) * (1 - t) * 0.8)
            px[x, y] = (r2, g2, b2, a)
    return src


def prepare_bust() -> Image.Image:
    """Load, crop, key, and soften the right-side hero image."""
    src = Image.open(config.BUST_SRC).convert("RGBA")
    w0, h0 = src.size

    # Trim plate borders / edge content.
    src = src.crop((int(w0 * 0.03), int(h0 * 0.02),
                    int(w0 * 0.97), int(h0 * 0.92)))

    src = _resize_to_target(src)
    keyed = _key_engraving(src)

    # Soften the alpha edge so chroma-key cuts don't show.
    alpha = keyed.split()[3].filter(ImageFilter.GaussianBlur(radius=1.2))
    keyed.putalpha(alpha)
    return keyed


def placement(bust: Image.Image) -> dict:
    """Return placement coordinates derived from the hero image size."""
    bust_x = config.W - bust.width - config.BUST_RIGHT_MARGIN
    bust_y = config.H - bust.height + 10
    return {
        "x": bust_x,
        "y": bust_y,
    }
