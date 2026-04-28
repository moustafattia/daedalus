"""Marble bust loading + chroma-key against the museum backdrop."""
from __future__ import annotations

import math

from PIL import Image, ImageFilter, ImageOps

from . import config


def prepare_bust() -> Image.Image:
    """Load, crop, chroma-key, and tone-blend the bust photo."""
    src = Image.open(config.BUST_SRC).convert("RGBA")
    w0, h0 = src.size
    src = src.crop((int(w0 * 0.05), int(h0 * 0.02),
                    int(w0 * 0.95), int(h0 * 0.78)))
    ratio = config.BUST_TARGET_H / src.height
    src = src.resize((int(src.width * ratio), config.BUST_TARGET_H),
                     Image.LANCZOS)

    # Sample background colour from 4 corners — the museum backdrop.
    px = src.load()
    samples = []
    for sx, sy in [(5, 5), (src.width - 6, 5),
                   (5, src.height - 6), (src.width - 6, src.height - 6)]:
        r, g, b, _ = px[sx, sy]
        samples.append((r, g, b))
    bg_r = sum(s[0] for s in samples) // len(samples)
    bg_g = sum(s[1] for s in samples) // len(samples)
    bg_b = sum(s[2] for s in samples) // len(samples)

    near, far = 55, 95
    for y in range(src.height):
        for x in range(src.width):
            r, g, b, _ = px[x, y]
            d = math.sqrt(
                (r - bg_r) ** 2 + (g - bg_g) ** 2 + (b - bg_b) ** 2
            )
            if d <= near:
                a = 0
            elif d >= far:
                a = 255
            else:
                a = int((d - near) / (far - near) * 255)
            r2 = min(255, int(r * 1.02 + 4))
            g2 = min(255, int(g * 1.01 + 2))
            b2 = min(255, int(b * 0.97))
            px[x, y] = (r2, g2, b2, a)

    rgb = src.convert("RGB")
    grey = ImageOps.grayscale(rgb).convert("RGB")
    blended = Image.blend(rgb, grey, 0.30)
    blended.putalpha(src.split()[3])

    alpha = blended.split()[3].filter(ImageFilter.GaussianBlur(radius=1.2))
    blended.putalpha(alpha)
    return blended


def placement(bust: Image.Image) -> dict:
    """Return placement coordinates derived from the bust size."""
    bust_x = config.W - bust.width - config.BUST_RIGHT_MARGIN
    bust_y = config.H - bust.height + 10
    return {
        "x": bust_x,
        "y": bust_y,
        "eye_y": bust_y + int(bust.height * 0.28),
        "eye_x1": bust_x + int(bust.width * 0.20),
        "eye_x2": bust_x + int(bust.width * 0.78),
    }
