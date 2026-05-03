"""Animated workflow flow:  Issue → Code → Review → Merge.

Each token fades in sequentially with a brief cyan "ignition" that
settles to ink, then a glowing pulse sweeps through left-to-right after
all tokens are lit. The whole thing reads as a pipeline lighting up
stage-by-stage.

Public surface::

    flow.draw(im, anchor=(x, y), frame=f)

`anchor` is the top-left corner of the line. `frame` is the current
frame number. Everything else (timing, colours, fonts) is read from
`config` + `timeline` + `typography` so this module has no policy of
its own — only rendering.
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter

from . import config, timeline, typography


# Tokens rendered in sequence. The arrows are tokens too — that way a
# fading-in arrow looks like the pipeline is *connecting* the stage that
# just appeared to the next one.
TOKENS = ["ISSUE", "->", "CODE", "->", "REVIEW", "->", "MERGE"]
SPACING = 14  # px between tokens — gives the line breathing room


def _blend(
    a: tuple[int, int, int], b: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def _token_x_positions(font, anchor_x: int) -> list[int]:
    """Return the left-edge x-coordinate for each token."""
    xs = [anchor_x]
    for tok in TOKENS[:-1]:
        bbox = font.getbbox(tok)
        xs.append(xs[-1] + (bbox[2] - bbox[0]) + SPACING)
    return xs


def _line_extent(font, anchor_x: int) -> tuple[int, int]:
    """(x_start, x_end) in pixels — used for the pulse path."""
    xs = _token_x_positions(font, anchor_x)
    last_bbox = font.getbbox(TOKENS[-1])
    return xs[0], xs[-1] + (last_bbox[2] - last_bbox[0])


def draw(im: Image.Image, *, anchor: tuple[int, int], frame: int) -> None:
    """Paint the animated flow line into `im` at ``anchor``."""
    font = typography.flow_stage()
    anchor_x, anchor_y = anchor
    xs = _token_x_positions(font, anchor_x)

    # Render tokens onto an RGBA layer so blended alpha works cleanly.
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i, (tok, x) in enumerate(zip(TOKENS, xs)):
        alpha, blend = timeline.flow_token_state(frame, i)
        if alpha <= 0:
            continue
        # Cyan when freshly ignited, settling toward ink as `blend` rises.
        rgb = _blend(config.CYAN_BRIGHT, config.INK, blend)
        d.text((x, anchor_y), tok, font=font, fill=(*rgb, alpha))

    im.paste(layer, (0, 0), layer)

    # Travelling pulse — small cyan glow that sweeps along the line once
    # all tokens have ignited. Reads as "data flowing through the pipe."
    pulse_p = timeline.flow_pulse_progress(frame)
    if pulse_p is not None:
        x_start, x_end = _line_extent(font, anchor_x)
        glow_x = int(x_start + (x_end - x_start) * pulse_p)
        glow_y = anchor_y + font.size // 2 + 2
        glow_layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow_layer)
        gd.ellipse(
            (glow_x - 9, glow_y - 9, glow_x + 9, glow_y + 9),
            fill=(*config.CYAN_BRIGHT, 130),
        )
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=4))
        im.paste(glow_layer, (0, 0), glow_layer)
        # Bright core
        gd2 = ImageDraw.Draw(im, "RGBA")
        gd2.ellipse(
            (glow_x - 3, glow_y - 3, glow_x + 3, glow_y + 3),
            fill=(*config.CYAN_BRIGHT, 230),
        )
