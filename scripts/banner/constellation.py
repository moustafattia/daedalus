"""Scattered network of nodes + edges that draws in around the bust."""
from __future__ import annotations

import math
import random

from PIL import ImageDraw

from . import config


def build(seed_origin: tuple[int, int], seed: int = 3):
    """Generate (nodes, edges).

    nodes: list of (x, y, radius, color)
    edges: list of (i, j) sorted index pairs
    """
    rng = random.Random(seed)
    cx, cy = seed_origin
    nodes = []
    for _ in range(34):
        angle = rng.uniform(0, 2 * math.pi)
        dist = rng.choice([
            rng.uniform(40, 130),
            rng.uniform(140, 240),
            rng.uniform(260, 380),
        ])
        x = int(cx + math.cos(angle) * dist)
        y = int(cy + math.sin(angle) * dist * 0.7)
        r = rng.choice([3, 4, 5, 6, 8])
        c = rng.choice(config.NETWORK_COLORS)
        nodes.append((x, y, r, c))

    edges = set()
    for i, (x1, y1, _, _) in enumerate(nodes):
        dists = sorted(
            [(j, math.hypot(x2 - x1, y2 - y1))
             for j, (x2, y2, _, _) in enumerate(nodes) if j != i],
            key=lambda p: p[1],
        )
        for j, _ in dists[: rng.randint(1, 3)]:
            a, b = sorted((i, j))
            edges.add((a, b))
    return nodes, sorted(edges)


def draw(d: ImageDraw.ImageDraw, nodes, edges, progress: float, dim: float):
    """Render the constellation into an RGBA-aware ImageDraw."""
    n_visible_edges = int(len(edges) * progress)
    for a, b in edges[:n_visible_edges]:
        x1, y1, _, _ = nodes[a]
        x2, y2, _, _ = nodes[b]
        col = nodes[b][3]
        alpha = int(95 * dim)
        d.line([(x1, y1), (x2, y2)], fill=(*col, alpha), width=1)

    n_visible_nodes = int(len(nodes) * progress)
    for x, y, r, c in nodes[:n_visible_nodes]:
        a = int(255 * dim)
        d.ellipse((x - r - 2, y - r - 2, x + r + 2, y + r + 2),
                  fill=(*c, max(0, a // 4)))
        d.ellipse((x - r, y - r, x + r, y + r), fill=(*c, a))
