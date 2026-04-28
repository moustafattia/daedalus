"""Reusable icon helpers used by the banner.

Two pieces:

  * `paste_caduceus`     — load `assets/source/caduceus.jpg`, recolour to
                            any tint, paste at the requested size. Used
                            for the tall decorative emblem on the far-left
                            margin.
  * `draw_margin_icons`  — small editorial vignettes (magnifying glass,
                            doc, curly braces) painted with PIL primitives
                            in the right margin.

Both render into an existing Image / ImageDraw — no global state. Add new
icons in this module so callers stay agnostic of source format.
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageOps

from . import config, typography


# ── PNG/JPG-embedded icons ──────────────────────────────────────────────

_png_cache: dict[str, Image.Image] = {}


def _load(path) -> Image.Image:
    key = str(path)
    if key not in _png_cache:
        _png_cache[key] = Image.open(path).convert("RGBA")
    return _png_cache[key].copy()


def _recolour(src: Image.Image, color: tuple[int, int, int],
              alpha: int = 255) -> Image.Image:
    """Recolour a single-tone artwork to `color`.

    Picks the right "silhouette mask" for the source format:

    * **Alpha-shaped PNG**: opaque shape on a transparent background —
      use the alpha channel directly.
    * **Line-art on white**: black ink on a white background — use
      inverted luminance so dark ink becomes opaque coloured pixels.

    Detected automatically by checking whether the source has variable
    alpha. Future PNG-with-alpha sources (Octicons-style marks) Just Work.
    """
    if src.mode == "RGBA":
        a_channel = src.split()[3]
        a_min, a_max = a_channel.getextrema()
        has_real_alpha = a_min < 250 and a_max > 5
    else:
        has_real_alpha = False

    if has_real_alpha:
        mask = a_channel
    else:
        grey = ImageOps.grayscale(src)
        mask = ImageOps.invert(grey)

    out = Image.new("RGBA", src.size, (*color, 0))
    fill = Image.new("RGBA", src.size, (*color, alpha))
    out.paste(fill, (0, 0), mask)
    return out


def paste_png(im: Image.Image, src_path, cx: int, cy: int,
              height: int, color: tuple[int, int, int],
              alpha: int = 255) -> None:
    """Paste a single-tone artwork at (cx, cy) scaled to `height`, recoloured."""
    if alpha <= 0:
        return
    src = _load(src_path)
    aspect = src.width / src.height
    target_h = height
    target_w = max(1, int(round(target_h * aspect)))
    src = src.resize((target_w, target_h), Image.LANCZOS)
    tinted = _recolour(src, color, alpha)
    im.paste(tinted, (cx - target_w // 2, cy - target_h // 2), tinted)


def paste_caduceus(im: Image.Image, cx: int, cy: int, height: int,
                   color: tuple[int, int, int] = config.HERMES_GOLD,
                   alpha: int = 255) -> None:
    """Hermes's herald wand — embedded line drawing recoloured to `color`."""
    paste_png(im, config.ASSETS / "source" / "caduceus.jpg",
              cx, cy, height, color, alpha)


# ── right-margin editorial vignettes ────────────────────────────────────

def draw_margin_icons(d: ImageDraw.ImageDraw, alpha: int) -> None:
    """Magnifying glass + doc + curly braces. Ambient editorial decoration."""
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
