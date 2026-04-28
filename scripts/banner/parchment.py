"""Cream parchment background — baked once, reused on every frame."""
from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw, ImageFilter

from . import config


def make_parchment(w: int = config.W, h: int = config.H) -> Image.Image:
    """Cream paper with subtle warm vignette + grain + smudges."""
    base = Image.new("RGB", (w, h), config.PAPER)
    px = base.load()
    rng = random.Random(11)
    cx, cy = w / 2, h / 2
    maxd = math.hypot(cx, cy)
    for y in range(h):
        for x in range(0, w, 2):
            d = math.hypot(x - cx, y - cy) / maxd
            warm = int(8 * d)
            r = max(0, config.PAPER[0] - warm - rng.randint(0, 5))
            g = max(0, config.PAPER[1] - warm - rng.randint(0, 5))
            b = max(0, config.PAPER[2] - warm - rng.randint(0, 6))
            px[x, y] = (r, g, b)
            if x + 1 < w:
                px[x + 1, y] = (r, g, b)

    smudge = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(smudge)
    for _ in range(40):
        cx2 = rng.randint(0, w)
        cy2 = rng.randint(0, h)
        r2 = rng.randint(40, 140)
        a = rng.randint(4, 12)
        sd.ellipse((cx2 - r2, cy2 - r2, cx2 + r2, cy2 + r2),
                   fill=(110, 90, 60, a))
    smudge = smudge.filter(ImageFilter.GaussianBlur(radius=18))
    base.paste(smudge, (0, 0), smudge)
    return base
