"""Per-frame composition. Glues all the modules together.

Frame layers (bottom → top):
    1. Parchment
    2. Constellation (animated draw-in)
    3. Engraved Daedalus emblem (right-side hero)
    4. Floating code overlays (staggered fade-in)
    5. Right-margin editorial vignettes
    6. Left-side title block (caduceus + wordmark + animated flow line)
"""
from __future__ import annotations

import random

from PIL import Image, ImageDraw

from . import (
    bust as bust_mod,
    code_overlays,
    config,
    constellation,
    icons,
    parchment,
    text_block,
    timeline,
    typography,
)

# Determinism for the constellation seed.
random.seed(7)


class Scene:
    """Pre-baked, frame-invariant pieces. Built once per run."""

    def __init__(self) -> None:
        print("baking parchment …")
        self.parchment = parchment.make_parchment()
        print("preparing bust …")
        self.bust = bust_mod.prepare_bust()
        self.bust_pos = bust_mod.placement(self.bust)
        # Constellation seed near the bust head's upper-right.
        seed_origin = (self.bust_pos["x"] + self.bust.width - 60,
                       self.bust_pos["y"] + 110)
        self.nodes, self.edges = constellation.build(seed_origin)


def render_frame(scene: Scene, f: int) -> Image.Image:
    im = scene.parchment.copy()
    overlay = Image.new("RGBA", (config.W, config.H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    dim = timeline.hold_to_loop(f)
    cp = timeline.constellation_progress(f) * dim

    # 1+2. constellation (under hero)
    constellation.draw(d, scene.nodes, scene.edges, cp, dim)
    im.paste(overlay, (0, 0), overlay)

    # 3. engraved hero emblem
    im.paste(scene.bust, (scene.bust_pos["x"], scene.bust_pos["y"]),
             scene.bust)

    # 4. code overlays (each on its own RGBA layer for clean alpha).
    # Anchored relative to the hero image's left edge so they sit next
    # to it without overlap regardless of the hero's width. The 270px
    # offset keeps the GITHUB block's left edge clear of the wordmark
    # tagline ("Workflows that don't melt.") that runs to ~x=577.
    code_layer = Image.new("RGBA", (config.W, config.H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(code_layer)
    bx = scene.bust_pos["x"]
    code_x = bx - 270
    code_overlays.draw_block(cd, code_overlays.AGENTS_BLOCK,
                             code_x, 50,
                             typography.code(), timeline.code_alpha(f, 0))
    code_overlays.draw_block(cd, code_overlays.GITHUB_BLOCK,
                             code_x, 165,
                             typography.code_small(),
                             timeline.code_alpha(f, 1))
    code_overlays.draw_block(cd, code_overlays.TURNLOG_BLOCK,
                             code_x, 250,
                             typography.code_small(),
                             timeline.code_alpha(f, 2))
    im.paste(code_layer, (0, 0), code_layer)

    # 5. right-margin editorial vignettes
    margin = Image.new("RGBA", (config.W, config.H), (0, 0, 0, 0))
    md = ImageDraw.Draw(margin)
    margin_alpha = int(180 * cp)
    if margin_alpha > 0:
        icons.draw_margin_icons(md, margin_alpha)
    im.paste(margin, (0, 0), margin)

    # 6. left-side title block (always opaque; workflow flow line
    # animates stage-by-stage with a pulse). text_block paints onto
    # `im` directly so it can paste PNG icons.
    text_block.draw(im, frame=f)

    return im
