"""Constants for the README banner generator.

Everything tunable lives here. Other modules import from this one.
"""

from __future__ import annotations

from pathlib import Path

# ── paths ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "assets"
OUT_PATH = ASSETS / "sprints-banner.gif"

# Right-side hero source art.
BUST_SRC = ASSETS / "source" / "sprints-emblem.png"

FONT_DISPLAY = ASSETS / "fonts" / "PlayfairDisplay.ttf"
FONT_DISPLAY_ITALIC = ASSETS / "fonts" / "PlayfairDisplay-Italic.ttf"


def _first_existing(*paths: str) -> str:
    for path in paths:
        if Path(path).exists():
            return path
    return paths[0]


FONT_MONO = _first_existing(
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/CascadiaMono.ttf",
)
FONT_SANS = _first_existing(
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
)

# ── canvas ───────────────────────────────────────────────────────────────
W, H = 1200, 400

# ── palette ──────────────────────────────────────────────────────────────
PAPER = (232, 226, 213)  # cream parchment
PAPER_SHADOW = (210, 202, 186)
INK = (28, 32, 36)  # near-black for body text
INK_SOFT = (76, 84, 92)
CYAN = (16, 130, 142)  # darker, painterly variant of brand cyan
CYAN_BRIGHT = (34, 180, 195)
HERMES_GOLD = (165, 132, 60)  # warmer gold for the caduceus

NETWORK_COLORS = [
    (110, 70, 60),  # burgundy
    (170, 130, 70),  # ochre
    (60, 110, 110),  # teal-grey
    (120, 130, 90),  # olive
    (90, 80, 110),  # ink-purple
    (160, 100, 80),  # terracotta
]

# ── animation timing ─────────────────────────────────────────────────────
FRAMES = 50
DURATION_MS = 80  # 12.5 fps

# ── layout anchors ───────────────────────────────────────────────────────

# Decorative caduceus: tall narrow element on the far-left margin,
# anchored from the top of the canvas. The 1:3 aspect ratio of the
# Wikimedia line drawing fits naturally as a margin emblem.
CADUCEUS_X = 30
CADUCEUS_Y = 60
CADUCEUS_HEIGHT = 280

# Title block — shifted right to leave room for the caduceus.
TITLE_X = 130
TITLE_Y = 70
TITLE_EMBLEM_W = 500
TITLE_EMBLEM_H = 170

# Title block vertical offsets relative to TITLE_Y.
OFFSET_SUBTITLE_1 = 170
OFFSET_SUBTITLE_2 = 218
OFFSET_FLOW = 270
TAGLINE_TEXT = "supervised autonomous coding workflows"

# Animated code column on the right side.
CODE_X = 720

# Bottom workflow stage line.
FLOW_FONT_SIZE = 21
# Caption underneath the title block:
#   "A Hermes Agent plugin  ·  Reads issues, writes PRs."
# (The GitHub-now/Linear-next roadmap line moved into the README so it
# can carry more weight there. Keeping OFFSET_CAPTION_1 only.)
OFFSET_CAPTION_1 = 270

# Hero image target height — leaves margin so the engraved emblem
# reads as a framed editorial plate, not a dominating photo.
BUST_TARGET_H = 155
BUST_TARGET_W = 510
BUST_RIGHT_MARGIN = 40
