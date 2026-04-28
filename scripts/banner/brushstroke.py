"""Painterly cyan brushstroke across the bust's eyes."""
from __future__ import annotations

import random

from PIL import Image, ImageDraw, ImageFilter

from . import config


def draw(im: Image.Image, x1: int, y1: int, x2: int, y2: int,
         progress: float) -> None:
    """Paint a hand-painted-looking horizontal stroke from x1 → end."""
    if progress <= 0:
        return
    rng = random.Random(99)
    end_x = int(x1 + (x2 - x1) * progress)
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    height = abs(y2 - y1)
    cy = (y1 + y2) // 2

    for _ in range(7):
        y_jit = rng.randint(-3, 3)
        thick = height + rng.randint(-4, 4)
        a = rng.randint(180, 230)
        col = (config.CYAN[0] + rng.randint(-10, 10),
               config.CYAN[1] + rng.randint(-15, 15),
               config.CYAN[2] + rng.randint(-10, 10),
               a)
        ld.line(
            [(x1 - 4, cy + y_jit), (end_x, cy + y_jit)],
            fill=col, width=thick,
        )

    if progress < 1.0:
        for _ in range(40):
            tx = end_x + rng.randint(-12, 6)
            ty = cy + rng.randint(-height // 2, height // 2)
            r = rng.randint(1, 3)
            ld.ellipse((tx - r, ty - r, tx + r, ty + r),
                       fill=(*config.CYAN, rng.randint(80, 200)))

    layer = layer.filter(ImageFilter.GaussianBlur(radius=0.8))
    im.paste(layer, (0, 0), layer)
