"""Title + subtitle + the bottom captions on the left side.

Now takes the Image object too, because the inline icons are PNG-embedded
(real artwork, recoloured) rather than drawn primitives.

Layout:

    ┌────────────────────────────────────────────────────────────┐
    │ ☤    Daedalus              [code] [bust]                  │
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


def draw(im: Image.Image, *, underline_progress: float, frame: int) -> None:
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

    # Wordmark
    d.text((x, y), "Daedalus",
           font=typography.title(), fill=(*config.INK, 255))

    # Gold underline accent (animated draw-in)
    if underline_progress > 0:
        ux2 = x + int(140 * underline_progress)
        d.line((x, y + config.OFFSET_GOLD_LINE,
                ux2, y + config.OFFSET_GOLD_LINE),
               fill=(*config.GOLD, 255), width=3)

    # Subtitle — two lines, second in cyan
    d.text((x, y + config.OFFSET_SUBTITLE_1), "Agents that fly.",
           font=typography.subtitle(), fill=(*config.INK, 255))
    d.text((x, y + config.OFFSET_SUBTITLE_2), "Workflows that don't melt.",
           font=typography.subtitle(), fill=(*config.CYAN, 255))

    # Caption line 1 — plugin + behaviour
    cap_font = typography.caption_serif_italic()
    d.text((x, y + config.OFFSET_CAPTION_1),
           "A Hermes Agent plugin  ·  Reads issues, writes PRs.",
           font=cap_font, fill=(*config.INK, 255))

    im.paste(text_layer, (0, 0), text_layer)

    # ── Animated workflow flow — its own module so timing + look stay
    #    isolated. Renders directly onto `im` so the pulse glow can blur
    #    cleanly across the parchment.
    flow.draw(im, anchor=(x, y + config.OFFSET_FLOW), frame=frame)

    # ── Caption line 2 — GitHub mark + roadmap (PNG icon needs Image) ──
    cap_y_2 = y + config.OFFSET_CAPTION_2
    icons.paste_github_mark(im,
                            cx=x + 9, cy=cap_y_2 + 10,
                            height=18, color=config.INK)
    cap_layer_2 = Image.new("RGBA", im.size, (0, 0, 0, 0))
    cd2 = ImageDraw.Draw(cap_layer_2)
    cd2.text((x + 26, cap_y_2),
             "GitHub now — Linear next.",
             font=cap_font, fill=(*config.INK_SOFT, 255))
    im.paste(cap_layer_2, (0, 0), cap_layer_2)
