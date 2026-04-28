"""Per-frame composition. Glues all the modules together.

Frame layers (bottom → top):
    1. Parchment
    2. Constellation (animated draw-in)
    3. Bust photograph
    4. Brushstroke across the bust's eyes (animated)
    5. Floating code overlays (staggered fade-in)
    6. Right-margin editorial vignettes
    7. Left-side title block (always visible — never animated alpha)
"""
from __future__ import annotations

import random

from PIL import Image, ImageDraw

from . import (
    brushstroke,
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

    # 1+2. constellation (under bust)
    constellation.draw(d, scene.nodes, scene.edges, cp, dim)
    im.paste(overlay, (0, 0), overlay)

    # 3. bust photo
    im.paste(scene.bust, (scene.bust_pos["x"], scene.bust_pos["y"]),
             scene.bust)

    # 4. brushstroke across the eyes
    bp = timeline.brush_progress(f)
    if bp > 0:
        brushstroke.draw(
            im,
            scene.bust_pos["eye_x1"], scene.bust_pos["eye_y"] - 22,
            scene.bust_pos["eye_x2"], scene.bust_pos["eye_y"] + 22,
            bp,
        )

    # 5. code overlays (each on its own RGBA layer for clean alpha)
    code_layer = Image.new("RGBA", (config.W, config.H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(code_layer)
    bx = scene.bust_pos["x"]
    code_overlays.draw_block(cd, code_overlays.AGENTS_BLOCK,
                             bx - 290, 30,
                             typography.code(), timeline.code_alpha(f, 0))
    code_overlays.draw_block(cd, code_overlays.GITHUB_BLOCK,
                             bx - 320, 145,
                             typography.code_small(),
                             timeline.code_alpha(f, 1))
    code_overlays.draw_block(cd, code_overlays.TURNLOG_BLOCK,
                             bx - 280, 230,
                             typography.code_small(),
                             timeline.code_alpha(f, 2))
    im.paste(code_layer, (0, 0), code_layer)

    # 6. right-margin editorial vignettes
    margin = Image.new("RGBA", (config.W, config.H), (0, 0, 0, 0))
    md = ImageDraw.Draw(margin)
    margin_alpha = int(180 * cp)
    if margin_alpha > 0:
        icons.draw_margin_icons(md, margin_alpha)
    im.paste(margin, (0, 0), margin)

    # 7. left-side title block (always opaque; underline animates draw-in)
    text_layer = Image.new("RGBA", (config.W, config.H), (0, 0, 0, 0))
    td = ImageDraw.Draw(text_layer)
    text_block.draw(td, underline_progress=timeline.underline_progress(f))
    im.paste(text_layer, (0, 0), text_layer)

    return im
