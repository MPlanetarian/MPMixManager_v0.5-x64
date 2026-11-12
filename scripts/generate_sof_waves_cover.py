#!/usr/bin/env python3
"""Cover art for the two Stream of Frequency — Dynamic Waves presets.

Both presets are pure 2D pixel rivers, so their "cover" is not a photograph:
it is a still of the effect itself, rendered from the same rules the shader
uses. That keeps the one-cover-per-preset rule intact while making the preset
list read correctly at a glance.

    assets/stream_of_frequency_dynamic_waves_cover.png        horizontal arcs
    assets/stream_of_frequency_dynamic_waves_p2_cover.png     vertical P2 rain
    COVERS/Stream_of_Frequency_Dynamic_Waves_Preset.png
    COVERS/Stream_of_Frequency_Dynamic_Waves_P2_Preset.png

The stills are rendered offline with Pillow, not captured from the GPU, so
they can be regenerated on any machine without a display.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
COVERS = ROOT / "COVERS"

SIZE = 1200
COVERAGE = 0.82


def hsv(h: float, s: float, v: float) -> tuple:
    """The same HSV->RGB mapping the shader uses."""
    h = h % 1.0
    i = int(h * 6.0)
    f = h * 6.0 - i
    p, q, t = v * (1.0 - s), v * (1.0 - s * f), v * (1.0 - s * (1.0 - f))
    return [
        (v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q),
    ][i % 6]


def to255(c: tuple) -> tuple:
    return tuple(max(0, min(255, int(round(x * 255)))) for x in c)


def glyph(draw: ImageDraw.ImageDraw, x: float, y: float, size: float, kind: float, colour) -> None:
    """One glyph, matching the shader's square / hollow square / diamond / cross."""
    h = size * 0.5
    if kind < 0.30:
        draw.rectangle([x - h * 0.72, y - h * 0.72, x + h * 0.72, y + h * 0.72], fill=colour)
    elif kind < 0.55:
        draw.rectangle([x - h, y - h, x + h, y + h], outline=colour, width=max(1, int(size * 0.18)))
    elif kind < 0.78:
        draw.polygon([(x, y - h), (x + h, y), (x, y + h), (x - h, y)], fill=colour)
    else:
        arm = h * 0.85
        thick = max(1, int(size * 0.20))
        draw.rectangle([x - arm, y - thick * 0.5, x + arm, y + thick * 0.5], fill=colour)
        draw.rectangle([x - thick * 0.5, y - arm, x + thick * 0.5, y + arm], fill=colour)


def arc_offset(u: float, t: float, band: float) -> float:
    a = 0.052 + 0.018 * math.sin(t * 0.037)
    arc = a * math.sin(u * math.pi + t * 0.085 + band * 0.13) * (
        0.55 + 0.45 * math.sin(band * 0.9 + t * 0.05)
    )
    arc += 0.016 * math.sin(u * math.tau * 1.5 - t * 0.11 + band * 0.21)
    return arc


def _band_energy(fpos: float, t: float) -> float:
    return 0.42 + 0.44 * math.sin(fpos * 6.1 + t * 0.7) ** 2


def render_horizontal(t: float = 12.0) -> Image.Image:
    """A still of the horizontal river: glyph cells along arcs, plus fireworks."""
    img = Image.new("RGB", (SIZE, SIZE), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    span = (1.0 - COVERAGE) * 0.5
    bands = 20
    cols = 148
    cell_x = SIZE / cols
    # Rows are sampled at the same cell pitch as columns so the glyphs are square.
    rows = cols
    cell_y = SIZE / rows

    for r in range(rows):
        b = (r + 0.5) / rows
        for c in range(cols):
            u = (c + 0.5) / cols
            a = (b - span) / COVERAGE + arc_offset(u, t, 0.0)
            if a < 0.0 or a > 1.0:
                continue
            # Per-row arc offset is band dependent, so resolve the band from the
            # unshifted position and then re-test the arc for that band.
            fpos = min(0.999, max(0.0, a))
            band = int(fpos * bands)
            fpos_band = (band + 0.5) / bands
            energy = _band_energy(fpos_band, t)
            a_band = (b - span) / COVERAGE + arc_offset(u, t, float(band))
            fb = a_band * bands
            local = (fb % 1.0) - 0.5
            half_h = 0.30 + 0.32 * energy
            if abs(local) > half_h:
                continue
            travel = u * cols - t * 38.0
            cid = math.floor(travel)
            h_cell = (math.sin(cid * 12.9898 + band * 78.233) * 43758.5453) % 1.0
            if h_cell < 1.0 - (0.18 + 0.52 * energy):
                continue
            h_kind = (math.sin(cid * 1.7 * 12.9898 + band * 21.1) * 43758.5453) % 1.0
            hue = (u * 0.52 + fpos_band * 0.16 - t * 0.011) % 1.0
            colour = to255(hsv(hue, 0.84, 0.99))
            glyph(draw, c * cell_x + cell_x * 0.5, r * cell_y + cell_y * 0.5,
                  min(cell_x, cell_y) * 0.88, h_kind, colour)

    # A brighter core line keeps the river reading as one long stream.
    for c in range(0, cols, 1):
        u = (c + 0.5) / cols
        for band in range(bands):
            fpos_band = (band + 0.5) / bands
            a_band = fpos_band + arc_offset(u, t, float(band))
            y = (span + a_band * COVERAGE) * SIZE
            hue = (u * 0.52 + fpos_band * 0.16 - t * 0.011) % 1.0
            colour = to255(hsv(hue, 0.62, 1.0))
            glyph(draw, c * cell_x + cell_x * 0.5, y, min(cell_x, cell_y) * 0.34, 0.0, colour)

    # Fireworks from one PID event.
    rng = random.Random(11)
    head = 0.62
    for k in range(7):
        cx = (head - 0.03 - 0.05 * k) * SIZE
        cy = rng.uniform(0.15, 0.85) * SIZE
        radius = (0.09 + 0.13 * k / 6.0) * SIZE
        for _ in range(120):
            ang = rng.uniform(0.0, math.tau)
            r = radius * rng.uniform(0.40, 1.0)
            colour = to255(hsv(rng.random(), 0.90, 1.0))
            glyph(draw, cx + math.cos(ang) * r, cy + math.sin(ang) * r,
                  cell_x * 0.85, rng.random(), colour)
    draw.line([(head * SIZE, 0), (head * SIZE, SIZE)], fill=(248, 248, 248), width=4)
    return img


def render_vertical(t: float = 9.0) -> Image.Image:
    """A still of the P2 river: column heads falling top -> bottom."""
    img = Image.new("RGB", (SIZE, SIZE), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    span = (1.0 - COVERAGE) * 0.5
    bands = 20
    cols = 132
    rows = 132
    cell_x, cell_y = SIZE / cols, SIZE / rows
    for r in range(rows):
        b = (r + 0.5) / rows
        for c in range(cols):
            u = (c + 0.5) / cols
            fpos = min(0.999, max(0.0, (u - span) / COVERAGE))
            band = int(fpos * bands)
            fpos_band = (band + 0.5) / bands
            energy = _band_energy(fpos_band, t)
            a_band = (u - span) / COVERAGE + arc_offset(b, t, float(band))
            fb = a_band * bands
            local = (fb % 1.0) - 0.5
            if abs(local) > 0.52:
                continue
            h_col = (math.sin((c % cols) * 3.7 + band) * 43758.5) % 1.0
            speed = 0.055 + 0.085 * h_col + 0.05 * energy
            head = (t * speed + h_col * 7.0) % 1.0
            tail = 0.10 + 0.30 * ((math.sin((c % cols) * 1.9 + 1.0) * 43758.5) % 1.0)
            drop = head - b
            if drop < 0.0 or drop > tail:
                continue
            h_cell = (math.sin((c % cols) * 5.1 + r * 9.7 + band * 5.0) * 43758.5) % 1.0
            if h_cell < 1.0 - (0.16 + 0.50 * energy):
                continue
            h_kind = (math.sin((c % cols) * 7.3 + r * 2.1 + band * 6.0) * 43758.5) % 1.0
            fade = (1.0 - drop / max(tail, 1e-3)) ** 1.6
            hue = (u * 0.42 + fpos_band * 0.22 - t * 0.010) % 1.0
            base = to255(hsv(hue, 0.82, 0.98))
            colour = tuple(int(v * (0.30 + 0.70 * fade)) for v in base)
            glyph(draw, c * cell_x + cell_x * 0.5, r * cell_y + cell_y * 0.5,
                  min(cell_x, cell_y) * 0.86, h_kind, colour)
    return img


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    COVERS.mkdir(parents=True, exist_ok=True)
    outputs = {
        ASSETS / "stream_of_frequency_dynamic_waves_cover.png": render_horizontal(),
        ASSETS / "stream_of_frequency_dynamic_waves_p2_cover.png": render_vertical(),
    }
    for path, image in outputs.items():
        image.save(path, "PNG", optimize=True)
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")
    for path, image in (
        (COVERS / "Stream_of_Frequency_Dynamic_Waves_Preset.png", render_horizontal(14.0)),
        (COVERS / "Stream_of_Frequency_Dynamic_Waves_P2_Preset.png", render_vertical(11.0)),
    ):
        image.save(path, "PNG", optimize=True)
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
