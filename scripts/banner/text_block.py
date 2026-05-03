"""Title + subtitle + the bottom captions on the left side.

Now takes the Image object too, because the inline icons are PNG-embedded
(real artwork, recoloured) rather than drawn primitives.

Layout:

    ┌────────────────────────────────────────────────────────────┐
    │ ☤    Sprints              [code] [bust]                  │
    │      ─                                                     │
    │      Agents that fly.                                      │
    │      Workflows that don't melt.                            │
    │      Issue → Code → Review → Merge                         │
    │      A Hermes Agent plugin · Reads issues, writes PRs.    │
    │      [GH] GitHub now — Linear next.                        │
    └────────────────────────────────────────────────────────────┘

The caduceus is a tall decorative emblem on the far-left margin.
The GitHub mark is inline next to its tagline clause.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from . import config, flow, icons, typography


def _title_emblem() -> Image.Image:
    src = Image.open(config.BUST_SRC).convert("RGBA")
    if src.getbbox():
        src = src.crop(src.getbbox())
    ratio = min(config.TITLE_EMBLEM_W / src.width, config.TITLE_EMBLEM_H / src.height)
    return src.resize((int(src.width * ratio), int(src.height * ratio)), Image.LANCZOS)


def draw(im: Image.Image, *, frame: int) -> None:
    """Paint the entire left-side title block onto `im`."""

    # ── Caduceus emblem on the far-left margin ──────────────────────────
    icons.paste_caduceus(
        im,
        cx=config.CADUCEUS_X + 30,
        cy=config.CADUCEUS_Y + config.CADUCEUS_HEIGHT // 2,
        height=config.CADUCEUS_HEIGHT,
        color=config.HERMES_GOLD,
    )

    # ── Text on its own RGBA layer ──────────────────────────────────────
    text_layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(text_layer)

    x = config.TITLE_X
    y = config.TITLE_Y

    emblem = _title_emblem()
    im.paste(emblem, (x + 18, y - 18), emblem)

    # Subtitle — two lines, second in cyan
    d.text(
        (x, y + config.OFFSET_SUBTITLE_1),
        "A Hermes-Agent plugin",
        font=typography.subtitle(),
        fill=(*config.INK, 255),
    )
    d.text(
        (x, y + config.OFFSET_SUBTITLE_2),
        config.TAGLINE_TEXT,
        font=typography.tagline(),
        fill=(*config.CYAN, 255),
    )

    # Caption line 1 — plugin + behaviour
    cap_font = typography.caption_serif_italic()
    d.text(
        (x, y + config.OFFSET_CAPTION_1),
        "",
        font=cap_font,
        fill=(*config.INK, 255),
    )

    im.paste(text_layer, (0, 0), text_layer)

    # ── Animated workflow flow — its own module so timing + look stay
    #    isolated. Renders directly onto `im` so the pulse glow can blur
    #    cleanly across the parchment.
    flow.draw(im, anchor=(x, y + config.OFFSET_FLOW), frame=frame)
