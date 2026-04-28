"""Title + subtitle + the bottom captions on the left side."""
from __future__ import annotations

from PIL import ImageDraw

from . import config, icons, typography


def draw(d: ImageDraw.ImageDraw, *, underline_progress: float) -> None:
    """Paint the entire left-side title block onto an RGBA-aware draw."""
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

    # Workflow flow caption
    d.text((x, y + config.OFFSET_FLOW),
           "Issue   →   Code   →   Review   →   Merge",
           font=typography.caption_sans(), fill=(*config.INK, 255))

    # ── one inline caption with both icons as visual punctuation ────────
    # Layout (left → right):
    #   [caduceus] A Hermes Agent plugin, [GH] fluent in GitHub.
    #
    # Each icon anchors its clause. Spacing constants live here, not in
    # config, because they're tightly coupled to glyph widths.
    cap_y = y + config.OFFSET_INLINE_CAPTION
    cap_font = typography.caption_serif_italic()
    cursor = x

    # Caduceus icon
    icons.draw_caduceus(d, cursor + 9, cap_y + 9,
                        height=20, color=config.HERMES_GOLD)
    cursor += 22

    # First clause — italic display serif, ink colour
    clause1 = "A Hermes Agent plugin,"
    d.text((cursor, cap_y), clause1, font=cap_font,
           fill=(*config.INK, 255))
    cursor += cap_font.getbbox(clause1)[2] + 12

    # GitHub mark
    icons.draw_github_mark(d, cursor + 8, cap_y + 11,
                           size=15, color=config.INK)
    cursor += 21

    # Second clause — same italic display serif, slightly softer ink
    d.text((cursor, cap_y), "fluent in GitHub.",
           font=cap_font, fill=(*config.INK, 255))
