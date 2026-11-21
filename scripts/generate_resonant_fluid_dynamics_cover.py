#!/usr/bin/env python3
"""Generate the missing cover for the Resonant Fluid Dynamics preset.

The preset's artwork was never committed, and the Vulkan backend treats a
missing cover as fatal. The palette comes from the preset's shader
(abyssal indigo, resonant indigo, violet plum, neon orchid and solar bronze).
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "resonant_fluid_dynamics_cover.png"
SIZE = 1400


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def main() -> Path:
    random.seed(9)
    abyss = (8, 7, 17)
    indigo = (22, 14, 158)
    plum = (116, 11, 110)
    orchid = (224, 86, 253)
    bronze = (212, 194, 120)

    image = Image.new("RGB", (SIZE, SIZE), abyss)
    px = image.load()
    for y in range(SIZE):
        for x in range(SIZE):
            dx = (x - SIZE * 0.53) / SIZE
            dy = (y - SIZE * 0.45) / SIZE
            r = math.hypot(dx, dy)
            t = max(0.0, min(1.0, 1.0 - r * 1.55))
            colour = _mix(abyss, indigo, t)
            if t > 0.25:
                colour = _mix(colour, plum, (t - 0.25) * 0.85)
            px[x, y] = colour

    flow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(flow)
    for band in range(9):
        points = []
        phase = band * 0.7 + 0.4
        amp = 90 + band * 18
        for x in range(-100, SIZE + 100, 12):
            y = (
                SIZE * 0.52
                + math.sin(x / 210.0 + phase) * amp
                + math.sin(x / 73.0 - phase * 1.7) * 34
                + band * 26
            )
            points.append((x, y))
        colour = (orchid if band % 3 == 0 else (bronze if band % 3 == 1 else (100, 20, 220)))
        draw.line(points, fill=colour + (110,), width=10 + band)
    flow = flow.filter(ImageFilter.GaussianBlur(7))
    image = Image.alpha_composite(image.convert("RGBA"), flow)

    # A resonant dodecahedron core: bronze wireframe, brighter toward the front.
    core = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    cdraw = ImageDraw.Draw(core)
    cx = cy = SIZE * 0.53
    radius = SIZE * 0.19
    verts = []
    for i in range(12):
        a = math.pi * 2 * i / 12
        rr = radius * (0.82 if i % 2 else 1.0)
        verts.append((cx + math.cos(a) * rr, cy + math.sin(a) * rr * 0.96))
    for i, p in enumerate(verts):
        for j in range(i + 1, 12):
            if (j - i) in (1, 3, 5, 11):
                cdraw.line((p, verts[j]), fill=bronze + (150,), width=3)
    cdraw.ellipse(
        (cx - radius * 0.35, cy - radius * 0.35, cx + radius * 0.35, cy + radius * 0.35),
        outline=orchid + (220,), width=5,
    )
    cdraw.ellipse(
        (cx - radius * 0.12, cy - radius * 0.12, cx + radius * 0.12, cy + radius * 0.12),
        fill=(255, 245, 220, 235),
    )
    core = core.filter(ImageFilter.GaussianBlur(0.6))
    image = Image.alpha_composite(image, core)

    # Sparse stars keep the centre from looking flat.
    stars = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(stars)
    for _ in range(420):
        x = random.randrange(SIZE)
        y = random.randrange(SIZE)
        r = random.choice((1, 1, 1, 2))
        alpha = random.randrange(60, 190)
        sdraw.ellipse((x, y, x + r, y + r), fill=(235, 240, 255, alpha))
    image = Image.alpha_composite(image, stars)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(OUT, "PNG", optimize=True)
    return OUT


if __name__ == "__main__":
    print(main())
