"""Animated code snippets for the right side of the banner."""

from __future__ import annotations

from PIL import ImageDraw, ImageFont

from . import config

CYAN = config.CYAN
CYAN_BRIGHT = config.CYAN_BRIGHT
INK = config.INK
INK_SOFT = config.INK_SOFT


AGENTS_BLOCK = [
    [("agents:", CYAN)],
    [
        ("  coder    ", INK),
        ("-> ", INK_SOFT),
        ("claude", CYAN_BRIGHT),
        ("/", INK_SOFT),
        ("sonnet-4.5", INK),
    ],
    [
        ("  reviewer ", INK),
        ("-> ", INK_SOFT),
        ("codex", CYAN_BRIGHT),
        ("/", INK_SOFT),
        ("gpt-5", INK),
    ],
    [
        ("  merger   ", INK),
        ("-> ", INK_SOFT),
        ("claude", CYAN_BRIGHT),
        ("/", INK_SOFT),
        ("haiku", INK),
    ],
]

GITHUB_BLOCK = [
    [("{", INK), ('"repo"', CYAN), (": ", INK), ('"attmous/sprints"', INK), (",", INK)],
    [
        (' "issue"', CYAN),
        (": ", INK),
        ("#42", CYAN_BRIGHT),
        (",", INK),
        ('  "label"', CYAN),
        (": ", INK),
        ('"active"', INK),
        (",", INK),
    ],
    [(' "state"', CYAN), (": ", INK), ('"awaiting_review"', CYAN_BRIGHT), ("}", INK)],
]

TURNLOG_BLOCK = [
    [("[coder]    ", CYAN), ("claude/sonnet  ", INK), ("ok wrote 3 files", INK_SOFT)],
    [("[reviewer] ", CYAN), ("codex/gpt-5    ", INK), ("fix 2 nits, 1 gap", INK_SOFT)],
    [("[coder]    ", CYAN), ("claude/sonnet  ", INK), ("ok pushed fixes", INK_SOFT)],
    [
        ("[reviewer] ", CYAN),
        ("codex/gpt-5    ", INK),
        ("ok approved -> merge", INK_SOFT),
    ],
]


def draw_block(
    d: ImageDraw.ImageDraw,
    lines: list[list[tuple[str, tuple[int, int, int]]]],
    x: int,
    y: int,
    font: ImageFont.ImageFont,
    alpha: int,
) -> None:
    """Render a list-of-lines code block at (x, y) with global alpha."""
    if alpha <= 0:
        return
    line_h = font.size + 4
    for i, line in enumerate(lines):
        tx = x
        for tok, color in line:
            d.text((tx, y + i * line_h), tok, font=font, fill=(*color, alpha))
            bbox = font.getbbox(tok)
            tx += bbox[2] - bbox[0]
