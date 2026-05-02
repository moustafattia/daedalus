"""GIF encoding with shared-palette interframe optimisation.

The trick: quantise frame 0 with an adaptive palette, then quantise every
later frame against that *same* palette. Subsequent frames reuse
identical indices for unchanged regions, which is what lets the GIF
encoder's interframe optimisation actually skip them.

Without shared palette + disposal=1, the same frames take ~50× more
bytes on disk.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from . import config


def encode(
    frames: list[Image.Image], out_path: Path | None = None, colors: int = 48
) -> Path:
    target = out_path or config.OUT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    print("quantizing …")
    base_palette = frames[0].convert(
        "P",
        palette=Image.Palette.ADAPTIVE,
        colors=colors,
        dither=Image.Dither.NONE,
    )
    quantized = [base_palette]
    for f in frames[1:]:
        quantized.append(f.quantize(palette=base_palette, dither=Image.Dither.NONE))

    print("encoding GIF …")
    quantized[0].save(
        target,
        save_all=True,
        append_images=quantized[1:],
        duration=config.DURATION_MS,
        loop=0,
        optimize=True,
        disposal=1,  # leave previous frame intact for interframe
        # optimisation — encoder skips unchanged pixels.
    )
    size_kb = target.stat().st_size / 1024
    print(f"wrote {target} ({size_kb:.1f} KiB, {len(frames)} frames)")
    return target
