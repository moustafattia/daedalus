"""The three code blocks that float on the bust side.

Each block is a list of *lines*; each line is a list of (token, color)
tuples so individual tokens can be highlighted.

Editing the content here re-themes the banner without touching the
renderer.
"""

from __future__ import annotations

from PIL import ImageDraw, ImageFont

from . import config

CYAN = config.CYAN
CYAN_BRIGHT = config.CYAN_BRIGHT
INK = config.INK
INK_SOFT = config.INK_SOFT


# Top — multi-agent config: who's on the team.
AGENTS_BLOCK = [
    [("agents:", CYAN)],
    [
        ("  coder    ", INK),
        ("→ ", INK_SOFT),
        ("claude", CYAN_BRIGHT),
        ("/", INK_SOFT),
        ("sonnet-4.5", INK),
    ],
    [
        ("  reviewer ", INK),
        ("→ ", INK_SOFT),
        ("codex", CYAN_BRIGHT),
        ("/", INK_SOFT),
        ("gpt-5", INK),
    ],
    [
        ("  merger   ", INK),
        ("→ ", INK_SOFT),
        ("claude", CYAN_BRIGHT),
        ("/", INK_SOFT),
        ("haiku", INK),
    ],
]

# Middle — GitHub-native lane state. Repo + issue ref say "real GitHub".
GITHUB_BLOCK = [
    [("{", INK), ('"repo"', CYAN), (": ", INK), ('"attmous/sprints"', INK), (",", INK)],
    [
        (' "issue"', CYAN),
        (": ", INK),
        ("#42", CYAN_BRIGHT),
        (",", INK),
        ('  "label"', CYAN),
        (": ", INK),
        ('"active-lane"', INK),
        (",", INK),
    ],
    [(' "state"', CYAN), (": ", INK), ('"awaiting_review"', CYAN_BRIGHT), ("}", INK)],
]

# Bottom — turn log: the agents collaborating in sequence.
TURNLOG_BLOCK = [
    [("[coder]    ", CYAN), ("claude/sonnet  ", INK), ("✓ wrote 3 files", INK_SOFT)],
    [("[reviewer] ", CYAN), ("codex/gpt-5    ", INK), ("⚠ 2 nits, 1 fix", INK_SOFT)],
    [("[coder]    ", CYAN), ("claude/sonnet  ", INK), ("✓ pushed fixes", INK_SOFT)],
    [
        ("[reviewer] ", CYAN),
        ("codex/gpt-5    ", INK),
        ("✓ approved →  merge", INK_SOFT),
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
