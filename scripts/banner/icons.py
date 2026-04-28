"""Reusable icon glyphs.

Three groups:
  * `draw_margin_icons` — small editorial vignettes in the right margin
  * `draw_github_mark`  — a recognisable but trademark-clean GitHub-ish
                          silhouette, drawable inline at any size
  * `draw_caduceus`     — Hermes's herald wand: staff + two snakes + wings
                          (line drawing in PIL, scales to any size)

Adding new icons: drop a `draw_<name>(d, cx, cy, ...)` function below
and import it where you need it. Each icon paints into an existing
ImageDraw — no global state.
"""
from __future__ import annotations

import math

from PIL import ImageDraw

from . import config, typography


# ── right-margin editorial vignettes ────────────────────────────────────

def draw_margin_icons(d: ImageDraw.ImageDraw, alpha: int) -> None:
    """Magnifying glass + doc + curly braces. Used as ambient decoration."""
    col = (*config.INK_SOFT, alpha)
    W = config.W

    # Magnifying glass
    cx, cy, r = W - 50, 40, 10
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=col, width=2)
    d.line((cx + 7, cy + 7, cx + 14, cy + 14), fill=col, width=2)

    # Curly braces
    bx, by = W - 48, 180
    d.text((bx, by), "{ }", font=typography.caption_sans(), fill=col)

    # Doc icon
    dx, dy = W - 56, 110
    d.rectangle((dx, dy, dx + 16, dy + 20), outline=col, width=2)
    d.line((dx + 4, dy + 6, dx + 12, dy + 6), fill=col, width=1)
    d.line((dx + 4, dy + 11, dx + 12, dy + 11), fill=col, width=1)
    d.line((dx + 4, dy + 16, dx + 9, dy + 16), fill=col, width=1)


# ── GitHub mark ─────────────────────────────────────────────────────────

def draw_github_mark(d: ImageDraw.ImageDraw, cx: int, cy: int,
                     size: int, color: tuple[int, int, int],
                     alpha: int = 255) -> None:
    """Filled circular silhouette + ear nubs + tail-tick.

    Reads as "GitHub" because of the cat-face proportions, without
    reproducing the official Octocat. Renders crisp at sizes 12-32 px.
    """
    if alpha <= 0:
        return
    col = (*color, alpha)
    bg = (*config.PAPER, alpha)
    r = size // 2

    # main circle
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col)
    # ear nubs
    nub = max(2, size // 6)
    d.ellipse((cx - r - 1, cy - r - 1, cx - r + nub + 1, cy - r + nub + 1),
              fill=col)
    d.ellipse((cx + r - nub - 1, cy - r - 1, cx + r + 1, cy - r + nub + 1),
              fill=col)
    # tail flick
    d.line((cx + r - nub, cy + r - nub,
            cx + r + nub - 1, cy + r + nub - 1),
           fill=col, width=max(2, size // 8))
    # negative-space "eyes" so it reads as a mark, not a blob
    eye_r = max(1, size // 10)
    d.ellipse((cx - 3 * eye_r, cy - eye_r,
               cx - eye_r, cy + eye_r), fill=bg)
    d.ellipse((cx + eye_r, cy - eye_r,
               cx + 3 * eye_r, cy + eye_r), fill=bg)


# ── Caduceus (Hermes's wand) ────────────────────────────────────────────

def draw_caduceus(d: ImageDraw.ImageDraw, cx: int, cy: int,
                  height: int, color: tuple[int, int, int],
                  alpha: int = 255) -> None:
    """Hermes's herald wand: vertical staff + two snakes + spread wings.

    Drawn in line-art style. Staff height = `height`. Wings span ~height
    horizontally. (cx, cy) is the visual centre.
    """
    if alpha <= 0:
        return
    col = (*color, alpha)
    half = height // 2
    top = cy - half
    bot = cy + half

    line_w = max(1, height // 28)

    # ── staff ────────────────────────────────────────────────────────────
    d.line((cx, top + 4, cx, bot), fill=col, width=line_w + 1)

    # finial orb at the tip
    orb_r = max(2, height // 22)
    d.ellipse((cx - orb_r, top - orb_r,
               cx + orb_r, top + orb_r), fill=col)

    # ── wings (two arcs spreading from below the orb) ───────────────────
    wing_y = top + max(3, height // 14)
    span = max(8, height // 2)
    # left wing — series of feather-curves
    for i in range(3):
        d.arc(
            (cx - span - i * 2, wing_y - 2 - i,
             cx - 2, wing_y + max(4, height // 14) + i * 2),
            start=180, end=350,
            fill=col, width=line_w,
        )
    # right wing — mirror
    for i in range(3):
        d.arc(
            (cx + 2, wing_y - 2 - i,
             cx + span + i * 2, wing_y + max(4, height // 14) + i * 2),
            start=190, end=360,
            fill=col, width=line_w,
        )

    # ── two snakes (sinusoidal coils crossing the staff) ────────────────
    snake_top = top + max(6, height // 8)
    snake_bot = bot - max(2, height // 16)
    n = 24
    amp = max(3, height // 10)
    cycles = 1.6
    for phase in (0.0, math.pi):
        pts = []
        for i in range(n + 1):
            t = i / n
            y = snake_top + (snake_bot - snake_top) * t
            x = cx + amp * math.sin(t * cycles * 2 * math.pi + phase)
            pts.append((x, y))
        d.line(pts, fill=col, width=line_w)
        # head — slightly larger dot at the top end of each snake
        head = pts[0]
        d.ellipse((head[0] - line_w - 1, head[1] - line_w - 1,
                   head[0] + line_w + 1, head[1] + line_w + 1),
                  fill=col)
