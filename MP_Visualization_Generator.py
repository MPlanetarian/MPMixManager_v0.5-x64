#!/usr/bin/env python3
"""MP Visualisation Generator.

Builds brand-new audio-reactive 3D presets for the MP Visualizer from a
background image, previews the generated shader live, and installs the preset
into MP Mix Manager for the OpenGL, Vulkan and Metal backends.

The window is a subclass of the same ``GlassHost`` the MP Liquid Player uses:
the theme resolver, font handling and shared config directory are the player's
own.  Press:

    C        cycle the liquid-glass theme
    F / F11  fullscreen / windowed
    Esc      close
    L        launch the MP Liquid Player
    G        launch MP Gui
    M        launch MP Mix Manager (Mix_Archive_Manager.sh) in a console

The visual language is learned from the thirteen shipped presets: orbiting
cameras that pull back on bass, raymarched SDF scenes, liquid metaballs,
accretion rings, mycelial tendrils, kinetic shards, cover-art palette sampling,
volumetric glow, Fresnel glass and ACES-style tone mapping.

Usage:
    python3 MP_Visualization_Generator.py [--image FILE] [--fullscreen]
"""
from __future__ import annotations

import colorsys
import json
import math
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
for _path in (ROOT, ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

# The Liquid Player's GUI class. MP Liquid Mixer subclasses the same host for
# the same reason: theme resolution, fonts and child environments stay in one
# place instead of being reimplemented here.
from MP_Audio_Player_Liquid_Glass_OpenGL import (  # noqa: E402
    GlassHost,
    _import_gtk,
    get_liquid_glass_font,
)
from liquid_glass_opengl import (  # noqa: E402
    get_dynamic_theme_state,
    glass_foreground_rgb,
    read_player_theme,
    theme_rgb,
    theme_uses_dark_glass,
)
from mp_theme_palette import DEFAULT_THEME, THEME_LABELS  # noqa: E402
import mp_visualizer_user_presets as user_presets  # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
except Exception:  # pragma: no cover - the import error is reported in the UI
    Image = None  # type: ignore[assignment]
    ImageDraw = ImageEnhance = ImageFilter = None  # type: ignore[assignment]

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]

CONFIG_DIR = Path.home() / ".config" / "mix-manager"
GENERATOR_TITLE = "MP Visualisation Generator"
WINDOW_W, WINDOW_H = 1280, 720
PREVIEW_W, PREVIEW_H = 640, 360
MARCH_STEPS = 72
PREVIEW_STEPS = 48
STYLES: Tuple[Tuple[int, str, str], ...] = (
    (0, "Hybrid — Orbs + Rings + Tendrils + Shards", "hybrid"),
    (1, "Liquid Glass Orbs (Metaballs)", "orbs"),
    (2, "Accretion Rings (Orbital Halos)", "rings"),
    (3, "Mycelial Tendrils (Filaments)", "tendrils"),
    (4, "Kinetic Shards (Architectural Fins)", "shards"),
)
STYLE_NAMES = {style[0]: style[1] for style in STYLES}


# ---------------------------------------------------------------------------
# palette extraction
# ---------------------------------------------------------------------------
def _clamp_channel(value: float) -> int:
    return max(0, min(255, int(round(value))))


def _hsv_shift(rgb: Tuple[int, int, int], dh: float, ds: float, dv: float) -> Tuple[int, int, int]:
    h, s, v = colorsys.rgb_to_hsv(*(channel / 255.0 for channel in rgb))
    h = (h + dh) % 1.0
    s = max(0.0, min(1.0, s + ds))
    v = max(0.0, min(1.0, v + dv))
    return tuple(_clamp_channel(channel * 255.0) for channel in colorsys.hsv_to_rgb(h, s, v))  # type: ignore[return-value]


def extract_palette(image_path: Path) -> Dict[str, Any]:
    """Dominant colours, sorted into the roles the shader wants.

    The shipped presets all build a six-stop signature palette from their cover
    art: two or three vivid accents, a bright pearl, a mid tone and a deep
    void.  Median-cut gives us the source colours; a little HSV nudging makes
    sure the set is actually usable as a neon palette even for a monochrome
    image.
    """
    if Image is None:
        raise RuntimeError("Pillow is required to read the background image")
    source = Image.open(image_path).convert("RGB")
    source.thumbnail((256, 256))
    quantized = source.quantize(colors=14, method=Image.Quantize.MEDIANCUT)
    raw_palette = quantized.getpalette() or []
    histogram = quantized.getcolors() or []
    entries: List[Tuple[int, Tuple[int, int, int]]] = []
    for count, index in sorted(histogram, key=lambda item: item[0], reverse=True):
        base = index * 3
        if base + 2 >= len(raw_palette):
            continue
        rgb = tuple(int(raw_palette[base + offset]) for offset in range(3))
        if max(rgb) < 14 or min(rgb) > 246:
            continue
        entries.append((int(count), rgb))  # type: ignore[arg-type]
    if not entries:
        entries = [(1, (80, 40, 160)), (1, (0, 220, 255)), (1, (255, 40, 160))]

    def saturation(rgb: Tuple[int, int, int]) -> float:
        return colorsys.rgb_to_hsv(*(channel / 255.0 for channel in rgb))[1]

    def value(rgb: Tuple[int, int, int]) -> float:
        return colorsys.rgb_to_hsv(*(channel / 255.0 for channel in rgb))[2]

    vivid = sorted(entries, key=lambda item: (saturation(item[1]) * 1.4 + value(item[1]) * 0.35), reverse=True)
    bright = sorted(entries, key=lambda item: value(item[1]), reverse=True)
    dark = sorted(entries, key=lambda item: value(item[1]))

    accents: List[Tuple[int, int, int]] = []
    for _count, rgb in vivid + entries:
        if all(sum(abs(rgb[i] - other[i]) for i in range(3)) > 42 for other in accents):
            accents.append(rgb)
        if len(accents) >= 3:
            break
    while len(accents) < 3:
        base = accents[-1] if accents else (120, 60, 200)
        accents.append(_hsv_shift(base, 0.18, 0.06, 0.05))

    light = bright[0][1] if bright else (235, 240, 255)
    mid = entries[len(entries) // 2][1]
    deep = dark[0][1] if dark else (8, 6, 20)
    deep = tuple(_clamp_channel(channel * 0.22) for channel in deep)  # type: ignore[assignment]
    pearl = tuple(_clamp_channel(channel * 0.35 + 255 * 0.65) for channel in light)  # type: ignore[assignment]
    palette = [accents[0], accents[1], accents[2], pearl, mid, deep]
    return {
        "palette": palette,
        "accents": accents,
        "bright": light,
        "dark": deep,
        "source": str(image_path),
    }


def _vec3(rgb: Tuple[int, int, int]) -> str:
    return "vec3({:.5f}, {:.5f}, {:.5f})".format(*(max(0, min(255, c)) / 255.0 for c in rgb))


# ---------------------------------------------------------------------------
# shader generation
# ---------------------------------------------------------------------------
# Accessor placeholders keep one scene body usable by all three backends:
# Vulkan uses a std140 uniform block (u.res), OpenGL 330 uses loose uniforms
# (u_res), and the Metal translator turns the Vulkan spelling into parameters.
_SHADER_BODY = r"""
// ---------------------------------------------------------------------------
// MP Visualisation Generator — generated signature scene
// Style: __STYLE_LABEL__
// The animation vocabulary follows the shipped MP Visualizer presets:
// orbiting camera + bass pull-back, SDF raymarch, liquid metaballs, accretion
// rings, mycelial conduits, kinetic shards, cover-art palette sampling,
// volumetric glow, Fresnel glass and filmic tone mapping.
// ---------------------------------------------------------------------------
const float PI = 3.14159265359;
const float STYLE = __STYLE__;
const float SPEED = __SPEED__;
const float INTENSITY = __INTENSITY__;
const float PUNCH = __PUNCH__;
const float SEED = __SEED__;
const int MARCH_STEPS = __STEPS__;
const vec3 PAL0 = __PAL0__;
const vec3 PAL1 = __PAL1__;
const vec3 PAL2 = __PAL2__;
const vec3 PAL3 = __PAL3__;
const vec3 PAL4 = __PAL4__;
const vec3 PAL5 = __PAL5__;

float hash11(float p) {
    p = fract(p * 0.1031);
    p *= p + 33.33;
    p *= p + p;
    return fract(p);
}

float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

float valueNoise3(vec3 x) {
    vec3 i = floor(x);
    vec3 f = fract(x);
    f = f * f * (3.0 - 2.0 * f);
    return mix(
        mix(mix(hash11(dot(i + vec3(0.0, 0.0, 0.0), vec3(1.0, 57.0, 113.0))),
                hash11(dot(i + vec3(1.0, 0.0, 0.0), vec3(1.0, 57.0, 113.0))), f.x),
            mix(hash11(dot(i + vec3(0.0, 1.0, 0.0), vec3(1.0, 57.0, 113.0))),
                hash11(dot(i + vec3(1.0, 1.0, 0.0), vec3(1.0, 57.0, 113.0))), f.x), f.y),
        mix(mix(hash11(dot(i + vec3(0.0, 0.0, 1.0), vec3(1.0, 57.0, 113.0))),
                hash11(dot(i + vec3(1.0, 0.0, 1.0), vec3(1.0, 57.0, 113.0))), f.x),
            mix(hash11(dot(i + vec3(0.0, 1.0, 1.0), vec3(1.0, 57.0, 113.0))),
                hash11(dot(i + vec3(1.0, 1.0, 1.0), vec3(1.0, 57.0, 113.0))), f.x), f.y), f.z);
}

mat2 rot2(float a) {
    float c = cos(a), s = sin(a);
    return mat2(c, -s, s, c);
}

float smin(float a, float b, float k) {
    float h = clamp(0.5 + 0.5 * (b - a) / max(k, 0.0001), 0.0, 1.0);
    return mix(b, a, h) - k * h * (1.0 - h);
}

float sdSphere(vec3 p, float r) {
    return length(p) - r;
}

float sdTorus(vec3 p, vec2 t) {
    vec2 q = vec2(length(p.xz) - t.x, p.y);
    return length(q) - t.y;
}

float sdBox(vec3 p, vec3 b) {
    vec3 q = abs(p) - b;
    return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0);
}

float sdCapsule(vec3 p, vec3 a, vec3 b, float r) {
    vec3 pa = p - a, ba = b - a;
    float h = clamp(dot(pa, ba) / max(dot(ba, ba), 0.0001), 0.0, 1.0);
    return length(pa - ba * h) - r;
}

vec3 paletteFast(float t) {
    float s = fract(t * 0.5 + 0.5);
    vec3 c = mix(PAL5, PAL0, smoothstep(0.0, 0.28, s));
    c = mix(c, PAL1, smoothstep(0.24, 0.50, s));
    c = mix(c, PAL2, smoothstep(0.46, 0.70, s));
    c = mix(c, PAL3, smoothstep(0.66, 0.88, s));
    c = mix(c, PAL4, smoothstep(0.86, 1.0, s));
    return c;
}

vec3 coverSample(vec2 uv) {
    vec2 q = fract(uv * 0.72 + vec2(0.5) + 0.022 * sin(__RES__.z * 0.06 + uv.yx * 5.0));
    return texture(uCoverTex, q).rgb;
}

vec3 signaturePalette(float t, vec2 uvHint) {
    vec3 procCol = paletteFast(t + 0.07 * sin(__RES__.z * 0.11 + t * 6.0));
    vec3 art = coverSample(uvHint);
    float luma = dot(art, vec3(0.299, 0.587, 0.114));
    art = clamp(mix(vec3(luma), art, 1.45), 0.0, 1.0);
    return mix(procCol * (0.86 + 0.24 * PUNCH), art, 0.30 + 0.16 * PUNCH);
}

vec3 background(vec3 rd, vec2 uv, float t) {
    float horizon = clamp(rd.y * 0.5 + 0.5, 0.0, 1.0);
    vec3 c = mix(PAL5 * 0.72, PAL0 * 0.24, horizon);
    c = mix(c, coverSample(uv + rd.xy * 0.20), 0.36);
    float neb = valueNoise3(rd * 3.4 + vec3(0.0, 0.0, t * 0.028));
    c += paletteFast(neb + 0.18) * (0.07 + 0.09 * PUNCH) * (0.45 + 0.55 * __AUDIO1__.w);
    vec3 cell = floor(rd * 145.0);
    float star = hash21(cell.xy + cell.z * 9.73 + SEED);
    if (star > 0.9935) {
        float glint = smoothstep(0.9935, 1.0, star);
        c += vec3(0.82, 0.88, 1.0) * glint * (0.55 + 0.45 * __AUDIO1__.y);
    }
    float vign = 1.0 - 0.30 * dot(uv - 0.5, uv - 0.5);
    return c * clamp(vign, 0.0, 1.0);
}

vec2 mapScene(vec3 p) {
    float t = __RES__.z;
    float subBass = __AUDIO0__.x;
    float bass = __AUDIO0__.y;
    float lowMid = __AUDIO0__.z;
    float mid = __AUDIO0__.w;
    float highMid = __AUDIO1__.x;
    float treble = __AUDIO1__.y;
    float air = __AUDIO1__.z;
    float rms = __AUDIO1__.w;
    float beat = __AUDIO2__.x;
    float bassPhase = __AUDIO2__.y;
    float midPhase = __AUDIO2__.z;
    float highPhase = __AUDIO2__.w;
    float tt = t * SPEED;

    float energy = clamp(rms * 0.55 + beat * 0.35 + bass * 0.35, 0.0, 1.8);
    vec3 w = p;
    w.xz *= rot2(tt * 0.075 + 0.14 * sin(tt * 0.11 + bassPhase * 0.1));
    w.yz *= rot2(0.06 * sin(tt * 0.09 + midPhase * 0.08));

    bool hy = STYLE < 0.5;
    bool useOrbs = hy || abs(STYLE - 1.0) < 0.5;
    bool useRings = hy || abs(STYLE - 2.0) < 0.5;
    bool useTendrils = hy || abs(STYLE - 3.0) < 0.5;
    bool useShards = hy || abs(STYLE - 4.0) < 0.5;

    float dOrb = 1e6;
    float dRing = 1e6;
    float dTendril = 1e6;
    float dShard = 1e6;

    if (useOrbs) {
        // Liquid glass core: a sphere whose surface is driven by three
        // interfering standing waves, plus three satellite metaballs.
        vec3 center = vec3(0.0, 0.05 * sin(tt * 0.47), 0.0);
        float radius = 0.54 + INTENSITY * (0.15 * bass + 0.07 * beat);
        float wobble = 0.052 * INTENSITY
            * sin(w.x * 5.2 + tt * 1.10 + bassPhase)
            * sin(w.y * 4.4 - tt * 0.90 + midPhase)
            * sin(w.z * 4.7 + tt * 0.70 + highPhase * 0.4);
        dOrb = sdSphere(w - center, radius) + wobble;
        for (int i = 0; i < 3; i++) {
            float fi = float(i);
            float a = tt * (0.33 + 0.10 * fi) + fi * 2.0944 + SEED * 0.7;
            float rad = 1.02 + 0.15 * fi + 0.10 * bass;
            vec3 c = vec3(cos(a) * rad,
                          0.34 * sin(tt * 0.53 + fi * 2.1) + 0.10 * sin(a * 2.0),
                          sin(a) * rad);
            float rr = 0.16 + 0.06 * bass + 0.025 * beat + 0.02 * fi;
            dOrb = smin(dOrb, sdSphere(w - c, rr), 0.26);
        }
    }

    if (useRings) {
        // Accretion rings: three counter-precessing toroids with a fine
        // standing-wave ripple around the rim.
        for (int i = 0; i < 3; i++) {
            float fi = float(i);
            vec3 b = w;
            b.yz *= rot2(0.52 + 0.24 * fi + tt * (0.14 + 0.05 * fi));
            b.xy *= rot2(0.37 * fi + tt * 0.065 * (fi + 1.0));
            float rad = 0.98 + 0.27 * fi + INTENSITY * (0.13 * bass + 0.04 * beat);
            float tube = 0.021 + 0.009 * treble + 0.006 * beat;
            float d = sdTorus(b, vec2(rad, tube));
            d += 0.012 * sin(atan(b.y, b.x) * 6.0 + tt * 1.35 + fi * 1.7) * (0.4 + 0.6 * PUNCH);
            dRing = min(dRing, d);
        }
    }

    if (useTendrils) {
        // Mycelial conduits: five braided capsules whose control points ride
        // the low-mid band, with illuminated beads travelling along them.
        for (int i = 0; i < 5; i++) {
            float fi = float(i);
            float phase = tt * (0.50 + 0.08 * fi) + fi * 1.2566 + SEED * 1.3;
            float amp = 0.42 + INTENSITY * 0.10 * mid;
            float x0 = -1.18 + fi * 0.59;
            float x1 = x0 + 0.55;
            vec3 a = vec3(x0,
                          amp * sin(x0 * 2.40 + phase + bassPhase * 0.16),
                          0.22 * cos(x0 * 3.10 + phase * 0.8 + midPhase * 0.16));
            vec3 b = vec3(x1,
                          amp * sin(x1 * 2.40 + phase + bassPhase * 0.16),
                          0.22 * cos(x1 * 3.10 + phase * 0.8 + midPhase * 0.16));
            dTendril = min(dTendril, sdCapsule(w, a, b, 0.042 + 0.016 * treble));
            for (int j = 0; j < 4; j++) {
                float fj = float(j);
                float along = 0.10 + 0.19 * fj + 0.03 * sin(tt * 1.3 + fi + fj);
                vec3 c = mix(a, b, along);
                float bead = 0.045 + 0.022 * beat + 0.012 * highMid;
                dTendril = min(dTendril, sdSphere(w - c, bead));
            }
        }
    }

    if (useShards) {
        // Kinetic shards: obsidian/chrome plates on independent orbits, each
        // tilted and spun, flaring with transients.
        for (int i = 0; i < 7; i++) {
            float fi = float(i);
            float a = tt * (0.24 + 0.055 * fi) + fi * 0.94 + SEED * 1.9;
            float rad = 1.28 + 0.18 * sin(fi * 2.0) + 0.075 * fi;
            vec3 c = vec3(cos(a) * rad,
                          0.56 * sin(tt * 0.31 + fi * 1.7) + 0.12 * sin(a * 2.2),
                          sin(a) * rad);
            vec3 q = w - c;
            q.xz *= rot2(tt * (0.46 + 0.09 * fi));
            q.xy *= rot2(tt * 0.28 + fi * 0.83);
            float scale = 0.10 + 0.018 * fi + INTENSITY * (0.035 * beat + 0.015 * treble);
            dShard = min(dShard, sdBox(q, vec3(scale, 0.034 + 0.008 * air, scale * 1.55)));
        }
    }

    float d = 1e6;
    float matId = 1.0;
    if (useOrbs) { d = dOrb; matId = 1.0; }
    if (useRings && dRing < d) { d = dRing; matId = 2.0; }
    if (useTendrils && dTendril < d) { d = dTendril; matId = 3.0; }
    if (useShards && dShard < d) { d = dShard; matId = 4.0; }
    return vec2(d, matId);
}

vec3 normalAt(vec3 p) {
    const float e = 0.0016;
    return normalize(vec3(
        mapScene(p + vec3(e, 0.0, 0.0)).x - mapScene(p - vec3(e, 0.0, 0.0)).x,
        mapScene(p + vec3(0.0, e, 0.0)).x - mapScene(p - vec3(0.0, e, 0.0)).x,
        mapScene(p + vec3(0.0, 0.0, e)).x - mapScene(p - vec3(0.0, 0.0, e)).x
    ));
}

void main() {
    vec2 fragCoord = vUv * __RES__.xy;
    vec2 uv = (fragCoord - 0.5 * __RES__.xy) / __RES__.y;
    vec2 screenUv = vec2(vUv.x, vUv.y);
    float t = __RES__.z;
    float subBass = __AUDIO0__.x;
    float bass = __AUDIO0__.y;
    float mid = __AUDIO0__.w;
    float treble = __AUDIO1__.y;
    float rms = __AUDIO1__.w;
    float beat = __AUDIO2__.x;
    float bassPhase = __AUDIO2__.y;
    float midPhase = __AUDIO2__.z;
    float highPhase = __AUDIO2__.w;
    float tt = t * SPEED;

    // Orbital 3D camera: slow yaw, shallow pitch, bass pull-back, beat punch.
    float camDist = 5.05 - INTENSITY * (0.44 * bass + 0.20 * beat)
        + 0.30 * sin(tt * 0.19) + 0.10 * sin(tt * 0.07 + SEED);
    float camYaw = tt * 0.16 + 0.28 * sin(tt * 0.10 + bassPhase * 0.08) + SEED * 0.6;
    float camPitch = 0.17 * sin(tt * 0.14 + midPhase * 0.06);
    vec3 ro = vec3(
        sin(camYaw) * cos(camPitch) * camDist,
        sin(camPitch) * camDist + 0.16,
        cos(camYaw) * cos(camPitch) * camDist
    );
    vec3 ta = vec3(
        0.13 * sin(tt * 0.23),
        0.10 * cos(tt * 0.19),
        0.0
    );
    vec3 ww = normalize(ta - ro);
    vec3 uu = normalize(cross(ww, vec3(0.0, 1.0, 0.0)));
    vec3 vv = normalize(cross(uu, ww));
    float roll = 0.075 * sin(tt * 0.21 + highPhase * 0.05);
    vec2 rotUv = uv * rot2(roll);
    vec3 rd = normalize(rotUv.x * uu + rotUv.y * vv + (1.34 - 0.13 * beat) * ww);

    vec3 bgCol = background(rd, screenUv, t);
    vec3 col = bgCol;
    vec3 glowAccum = vec3(0.0);
    float dist = 0.0;
    bool hit = false;
    float matId = 1.0;

    for (int i = 0; i < MARCH_STEPS; i++) {
        vec3 p = ro + rd * dist;
        vec2 h = mapScene(p);
        float d = h.x;
        float glow = exp(-d * 11.0) / (1.0 + d * d * 95.0);
        if (glow > 0.0007) {
            glowAccum += paletteFast(length(p) * 0.23 + t * 0.045 + SEED)
                * glow * (0.09 + 0.18 * PUNCH + 0.10 * beat);
        }
        if (d < 0.0022) {
            hit = true;
            matId = h.y;
            break;
        }
        dist += max(d * 0.88, 0.0025);
        if (dist > 13.0) break;
    }

    if (hit) {
        vec3 p = ro + rd * dist;
        vec3 n = normalAt(p);
        vec3 v = -rd;
        float diff = max(dot(n, normalize(vec3(0.55, 0.86, 0.45))), 0.0);
        float fres = pow(1.0 - max(dot(n, v), 0.0), 3.0);
        float spec = pow(max(dot(reflect(-normalize(vec3(0.55, 0.86, 0.45)), n), v), 0.0), 48.0);
        vec3 reflDir = reflect(rd, n);
        vec3 envRefl = background(reflDir, fract(screenUv + reflDir.xy * 0.20), t);

        vec3 surf;
        if (matId < 1.5) {
            // Liquid glass: chromatic-ish refraction of the cover art, an
            // iridescent inner swatch, deep attenuation and a glass rim.
            vec3 refr = refract(rd, n, 0.70);
            vec3 art = coverSample(fract(screenUv + refr.xy * 0.38));
            vec3 inner = paletteFast(dot(n, vec3(0.45, 0.62, 0.30)) + t * 0.045 + SEED);
            float core = pow(max(dot(n, v), 0.0), 1.5);
            surf = mix(art * 0.90, PAL5 * 1.4, 0.34 * core);
            surf = mix(surf, inner, 0.40 + 0.18 * bass);
            surf *= 0.62 + 0.48 * diff;
            surf += mix(PAL0, PAL3, 0.45) * fres * (1.05 + 0.65 * rms);
        } else if (matId < 2.5) {
            // Incandescent accretion ring.
            float wave = 0.5 + 0.5 * sin(length(p) * 19.0 - bassPhase * 2.5 + SEED);
            surf = mix(PAL1, PAL0, wave);
            surf = mix(surf, PAL3, smoothstep(0.35, 0.88, sin(p.x * 6.0 + t * 0.9)));
            surf *= 0.55 + 0.75 * diff;
            surf += PAL2 * fres * 1.10;
        } else if (matId < 3.5) {
            // Mycelial conduit with travelling light beads.
            float pulse = pow(0.5 + 0.5 * sin(p.y * 8.0 + bassPhase * 2.2 + t * 0.5), 4.0);
            surf = signaturePalette(p.y * 0.25 + p.x * 0.15 + t * 0.05, screenUv);
            surf = surf * (0.38 + 0.66 * diff) * (0.74 + 0.26 * max(dot(n, v), 0.0));
            surf += signaturePalette(t * 0.11, screenUv) * pulse * (0.65 + 0.85 * bass);
            surf += PAL2 * fres * 0.95;
        } else {
            // Kinetic shard: obsidian/chrome plate with a neon edge.
            float facet = 0.5 + 0.5 * sin(dot(p, vec3(3.0, 5.0, 7.0)) + t * 0.6 + highPhase);
            surf = mix(vec3(0.022, 0.030, 0.064), envRefl, 0.56 + 0.30 * fres);
            surf *= 0.55 + 0.52 * diff;
            surf += paletteFast(facet + t * 0.04) * fres * (1.05 + 0.75 * treble);
        }
        surf += vec3(1.0, 0.98, 0.95) * spec * (0.75 + 0.95 * beat);
        float fog = smoothstep(6.0, 13.0, dist);
        col = mix(surf, bgCol, fog * 0.72);
    }

    col += glowAccum * (0.68 + 0.30 * rms + 0.26 * beat);
    float vig = 1.0 - 0.25 * dot(uv, uv);
    col *= clamp(vig, 0.0, 1.0);
    col = (col * (2.51 * col + 0.03)) / (col * (2.43 * col + 0.59) + 0.14);
    col = pow(clamp(col, 0.0, 1.0), vec3(0.92));
    outColor = vec4(col, 1.0);
}
"""

_VULKAN_HEADER = """#version 450
layout(location = 0) in vec2 vUv;
layout(location = 0) out vec4 outColor;

layout(set = 0, binding = 0) uniform VisualizerUBO {
    vec4 res;
    vec4 audio0;
    vec4 audio1;
    vec4 audio2;
    vec4 bands0;
    vec4 bands1;
    vec4 bands2;
    vec4 bands3;
    vec4 preset;
    vec4 stream;
} u;

layout(set = 0, binding = 1) uniform sampler2D uCoverTex;

"""

_OPENGL_HEADER = """#version 330 core
in vec2 vUv;
out vec4 outColor;

uniform vec4 u_res;
uniform vec4 u_audio0;
uniform vec4 u_audio1;
uniform vec4 u_audio2;
uniform vec4 u_bands0;
uniform vec4 u_bands1;
uniform vec4 u_bands2;
uniform vec4 u_bands3;
uniform vec4 u_preset;
uniform vec4 u_stream;
uniform sampler2D uCoverTex;

"""

_ACCESSORS_VULKAN = (
    ("__RES__", "u.res"),
    ("__AUDIO0__", "u.audio0"),
    ("__AUDIO1__", "u.audio1"),
    ("__AUDIO2__", "u.audio2"),
    ("__BANDS0__", "u.bands0"),
    ("__BANDS1__", "u.bands1"),
    ("__BANDS2__", "u.bands2"),
    ("__BANDS3__", "u.bands3"),
    ("__PRESET__", "u.preset"),
    ("__STREAM__", "u.stream"),
)
_ACCESSORS_OPENGL = (
    ("__RES__", "u_res"),
    ("__AUDIO0__", "u_audio0"),
    ("__AUDIO1__", "u_audio1"),
    ("__AUDIO2__", "u_audio2"),
    ("__BANDS0__", "u_bands0"),
    ("__BANDS1__", "u_bands1"),
    ("__BANDS2__", "u_bands2"),
    ("__BANDS3__", "u_bands3"),
    ("__PRESET__", "u_preset"),
    ("__STREAM__", "u_stream"),
)


def _body_for_style(config: Dict[str, Any], *, steps: int) -> str:
    palette = config["palette"]
    body = _SHADER_BODY
    replacements = {
        "__STYLE_LABEL__": str(config.get("style_label", "Hybrid")).replace("*/", "* /"),
        "__STYLE__": f"{float(int(config.get('style', 0))):.1f}",
        "__SPEED__": f"{float(config.get('speed', 1.0)):.4f}",
        "__INTENSITY__": f"{float(config.get('intensity', 1.0)):.4f}",
        "__PUNCH__": f"{float(config.get('punch', 1.0)):.4f}",
        "__SEED__": f"{float(config.get('seed', 0.0)):.4f}",
        "__STEPS__": str(int(steps)),
        "__PAL0__": _vec3(tuple(palette[0])),
        "__PAL1__": _vec3(tuple(palette[1])),
        "__PAL2__": _vec3(tuple(palette[2])),
        "__PAL3__": _vec3(tuple(palette[3])),
        "__PAL4__": _vec3(tuple(palette[4])),
        "__PAL5__": _vec3(tuple(palette[5])),
    }
    for marker, value in replacements.items():
        body = body.replace(marker, value)
    return body


def generate_vulkan_source(config: Dict[str, Any], *, steps: int = MARCH_STEPS) -> str:
    body = _body_for_style(config, steps=steps)
    for marker, accessor in _ACCESSORS_VULKAN:
        body = body.replace(marker, accessor)
    return _VULKAN_HEADER + body


def generate_opengl_source(config: Dict[str, Any], *, steps: int = MARCH_STEPS) -> str:
    body = _body_for_style(config, steps=steps)
    for marker, accessor in _ACCESSORS_OPENGL:
        body = body.replace(marker, accessor)
    return _OPENGL_HEADER + body


def generate_metal_source(config: Dict[str, Any], *, steps: int = MARCH_STEPS) -> Optional[str]:
    """Translate the Vulkan GLSL with the visualizer's own Metal translator."""
    try:
        from mp_metal_shader import translate

        return translate(generate_vulkan_source(config, steps=steps))
    except Exception as exc:
        print(f"[MP Visualisation Generator] Metal translation failed: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# compilation
# ---------------------------------------------------------------------------
def _tool(name: str) -> Optional[str]:
    found = shutil.which(name)
    if found:
        return found
    for candidate in (
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
        f"/home/linuxbrew/.linuxbrew/bin/{name}",
        str(Path.home() / ".local" / "bin" / name),
    ):
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def compile_vulkan(source: str, destination: Path) -> Optional[Path]:
    compiler = _tool("glslangValidator") or _tool("glslc")
    if not compiler:
        return None
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mpvis-shader-") as temp:
        source_path = Path(temp) / "preset.frag"
        source_path.write_text(source, encoding="utf-8")
        target = Path(temp) / "preset.frag.spv"
        if Path(compiler).name.endswith("glslc"):
            command = [compiler, "-fshader-stage=frag", "--target-env=vulkan1.0", "-o", str(target), str(source_path)]
        else:
            command = [compiler, "-V", "--target-env", "vulkan1.0", "-o", str(target), str(source_path)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
        if result.returncode != 0 or not target.is_file():
            message = (result.stderr or result.stdout or "").strip()
            print(f"[MP Visualisation Generator] Vulkan compile failed: {message}", file=sys.stderr)
            return None
        data = target.read_bytes()
        if not data.startswith(b"\x03\x02\x23\x07"):
            print("[MP Visualisation Generator] Compiler did not emit SPIR-V", file=sys.stderr)
            return None
        destination.write_bytes(data)
        return destination


# ---------------------------------------------------------------------------
# still preview (also used as the exported preview.png)
# ---------------------------------------------------------------------------
def render_still_preview(config: Dict[str, Any], image_path: Path, destination: Path,
                         *, size: Tuple[int, int] = (PREVIEW_W, PREVIEW_H)) -> Optional[Path]:
    """A designed still of the generated scene using Pillow.

    The live preview is the real shader. This still is what an exported
    package carries, and it is the fallback when a GL context cannot be
    created (a remote session, for example).
    """
    if Image is None or ImageDraw is None:
        return None
    width, height = size
    source = Image.open(image_path).convert("RGB")
    # Cover-crop the artwork to the preview aspect before tinting it.
    source_ratio = source.width / max(1, source.height)
    target_ratio = width / max(1, height)
    if source_ratio > target_ratio:
        new_w = int(source.height * target_ratio)
        left = (source.width - new_w) // 2
        source = source.crop((left, 0, left + new_w, source.height))
    else:
        new_h = int(source.width / target_ratio)
        top = (source.height - new_h) // 2
        source = source.crop((0, top, source.width, top + new_h))
    canvas = source.resize((width, height), Image.Resampling.LANCZOS)
    canvas = ImageEnhance.Brightness(canvas).enhance(0.52)
    canvas = ImageEnhance.Color(canvas).enhance(1.25)

    palette = config["palette"]
    seed = float(config.get("seed", 0.0))
    style = int(config.get("style", 0))
    t = 0.42 + seed * 0.03
    rng = random.Random(str(seed) + str(style))

    # Deep-space gradient wash.
    overlay = Image.new("RGB", (width, height), palette[5])
    overlay_draw = ImageDraw.Draw(overlay)
    for y in range(height):
        f = y / max(1, height - 1)
        c = tuple(int(palette[5][i] * (1.0 - f) + palette[0][i] * 0.16 * f) for i in range(3))
        overlay_draw.line([(0, y), (width, y)], fill=c)
    canvas = Image.blend(canvas, overlay, 0.42)

    # Stars.
    draw = ImageDraw.Draw(canvas)
    for _ in range(170):
        x, y = rng.randrange(width), rng.randrange(height)
        b = rng.randrange(60, 210)
        draw.point((x, y), fill=(b, b, min(255, b + 20)))
    canvas = canvas.filter(ImageFilter.GaussianBlur(0.4))

    # Glow layer for the procedural scene.
    glow = Image.new("RGB", (width, height), (0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    cx, cy = width * 0.52, height * 0.47
    base = min(width, height)

    def ring(cx_: float, cy_: float, rx: float, ry: float, colour, width_px: int):
        for offset in range(4):
            alpha_col = tuple(int(c * (0.20 - offset * 0.04)) for c in colour)
            gdraw.ellipse(
                [cx_ - rx - offset * 3, cy_ - ry - offset * 3,
                 cx_ + rx + offset * 3, cy_ + ry + offset * 3],
                outline=alpha_col, width=max(1, width_px - offset),
            )

    if style in (0, 1):
        for r in range(int(base * 0.19), 0, -2):
            f = 1.0 - r / (base * 0.19)
            colour = tuple(int(palette[0][i] * (0.25 + 0.75 * f) + palette[3][i] * 0.10 * f) for i in range(3))
            gdraw.ellipse([cx - r, cy - r * 0.92, cx + r, cy + r * 0.92], fill=colour)
        for index in range(3):
            a = t + index * 2.1 + seed
            orbit = min(width, height) * (0.30 + 0.045 * index)
            x = cx + math.cos(a) * orbit
            y = cy + math.sin(a) * orbit * 0.48
            rr = base * (0.045 + 0.012 * index)
            gdraw.ellipse([x - rr, y - rr, x + rr, y + rr], fill=palette[1 + index % 3])
    if style in (0, 2):
        ring(cx, cy, base * 0.34, base * 0.13, palette[1], 3)
        ring(cx, cy, base * 0.43, base * 0.18, palette[2], 2)
        ring(cx, cy, base * 0.51, base * 0.23, palette[0], 2)
    if style in (0, 3):
        for index in range(5):
            phase = t + index * 1.25 + seed
            points = []
            for x in range(0, width, 10):
                nx = (x / width) * 2.0 - 1.0
                y = cy + math.sin(nx * 3.1 + phase) * base * 0.13 + (index - 2) * base * 0.055
                points.append((x, y))
            gdraw.line(points, fill=palette[index % 3], width=2)
    if style in (0, 4):
        for index in range(7):
            a = t * 0.6 + index * 0.9 + seed
            orbit = base * (0.30 + 0.02 * index)
            x = cx + math.cos(a) * orbit
            y = cy + math.sin(a) * orbit * 0.44
            s = base * (0.022 + 0.003 * index)
            gdraw.polygon([(x, y - s), (x + s * 0.8, y), (x, y + s), (x - s * 0.8, y)], fill=palette[(index + 1) % 4])

    glow = glow.filter(ImageFilter.GaussianBlur(base * 0.018))
    canvas = Image.blend(canvas, Image.blend(canvas, glow, 0.0), 0.0)
    canvas = Image.composite(Image.blend(canvas, glow, 0.60), canvas, glow.convert("L").point(lambda v: 255 if v > 8 else 0))

    # Vignette + a title strip at the bottom for exported stills.
    vignette = Image.new("L", (width, height), 0)
    vdraw = ImageDraw.Draw(vignette)
    vdraw.ellipse([-width * 0.25, -height * 0.3, width * 1.25, height * 1.3], fill=255)
    vignette = vignette.filter(ImageFilter.GaussianBlur(width * 0.10))
    black = Image.new("RGB", (width, height), (0, 0, 0))
    canvas = Image.composite(canvas, black, vignette.point(lambda v: int(80 + v * 0.70)))

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, format="PNG")
    return destination


# ---------------------------------------------------------------------------
# preset generation + installation
# ---------------------------------------------------------------------------
def prettify_name(stem: str) -> str:
    words = re.sub(r"[_\-.]+", " ", str(stem or "")).strip()
    words = re.sub(r"\s+", " ", words)
    return words.title() if words else "Generated Visualiser"


def build_preset(config: Dict[str, Any], *, install: bool = True, activate: bool = False) -> Dict[str, Any]:
    """Generate every backend's code and (optionally) install the package."""
    image_path = Path(str(config["image"])).expanduser().resolve()
    if not image_path.is_file():
        raise FileNotFoundError(str(image_path))
    if Image is None:
        raise RuntimeError("Pillow is required to read the background image")
    name = str(config.get("name") or "").strip() or prettify_name(image_path.stem)
    key = user_presets.key_from_name(config.get("key") or name)
    palette_info = extract_palette(image_path)
    config = {**config, "name": name, "key": key, "palette": palette_info["palette"],
              "palette_info": palette_info, "image": str(image_path)}

    vulkan = generate_vulkan_source(config, steps=MARCH_STEPS)
    opengl = generate_opengl_source(config, steps=MARCH_STEPS)
    metal = generate_metal_source(config, steps=MARCH_STEPS)

    directory = user_presets.preset_directory(key)
    directory.mkdir(parents=True, exist_ok=True)

    # Normalise the artwork to PNG so packages are portable.
    cover_path = directory / "cover.png"
    image = Image.open(image_path).convert("RGB")
    image.thumbnail((2048, 2048))
    image.save(cover_path, format="PNG")

    (directory / "preset.frag").write_text(vulkan, encoding="utf-8")
    (directory / "preset.opengl.frag").write_text(opengl, encoding="utf-8")
    if metal:
        (directory / "preset.metal").write_text(metal, encoding="utf-8")
    spv = compile_vulkan(vulkan, directory / "preset.frag.spv")
    still = render_still_preview(config, image_path, directory / "preview.png")
    (directory / "palette.json").write_text(
        json.dumps({"palette": palette_info["palette"], "source": str(image_path)}, indent=2) + "\n",
        encoding="utf-8",
    )

    meta = {
        "key": key,
        "name": name,
        "album": str(config.get("album") or f"User Generated — {name}"),
        "style": STYLE_NAMES.get(int(config.get("style", 0)), "Hybrid"),
        "style_id": int(config.get("style", 0)),
        "speed": float(config.get("speed", 1.0)),
        "intensity": float(config.get("intensity", 1.0)),
        "punch": float(config.get("punch", 1.0)),
        "seed": float(config.get("seed", 0.0)),
        "cover": "cover.png",
        "generator_version": user_presets.GENERATOR_VERSION,
        "backends": ["opengl", "vulkan", "metal"] if metal else ["opengl", "vulkan"],
        "vulkan_compiled": bool(spv),
        "preview": bool(still),
        "installed": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    entry = None
    if install:
        entry = user_presets.register_preset(directory, meta, activate=activate)
    return {
        "directory": directory,
        "meta": meta,
        "entry": entry,
        "vulkan": vulkan,
        "opengl": opengl,
        "metal": metal,
        "spv": spv,
        "preview": still,
        "palette": palette_info,
        "config": config,
    }


# ---------------------------------------------------------------------------
# GTK / OpenGL preview
# ---------------------------------------------------------------------------
class ShaderPreview:
    """A Gtk.GLArea that draws the generated shader live, with a still fallback.

    This is deliberately not a second renderer implementation: it compiles the
    same ``preset.opengl.frag`` the MP Visualizer's OpenGL backend will compile,
    so the preview is the actual preset, not a mock-up.
    """

    def __init__(self, Gtk, GLib, width: int = PREVIEW_W, height: int = PREVIEW_H):
        self.Gtk = Gtk
        self.GLib = GLib
        self.area = Gtk.GLArea()
        self.area.set_use_es(False)
        self.area.set_has_depth_buffer(False)
        self.area.set_has_stencil_buffer(False)
        self.area.set_auto_render(True)
        self.area.set_size_request(width, height)
        self.area.connect("realize", self._on_realize)
        self.area.connect("render", self._on_render)
        self.area.connect("resize", self._on_resize)
        self.image = Gtk.Image()
        self.image.set_size_request(width, height)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(220)
        self.stack.add_named(self.area, "gl")
        self.stack.add_named(self.image, "still")
        self.stack.set_visible_child_name("still")
        self._program = 0
        self._vao = 0
        self._vbo = 0
        self._texture = 0
        self._locs: Dict[str, int] = {}
        self._source = ""
        self._texture_key = ""
        self._started = time.monotonic()
        self._error = ""
        self._failed = False
        self._last_frame = 0.0
        self._image_path: Optional[Path] = None
        self._tick = GLib.timeout_add(16, self._request_frame)

    # -- public ---------------------------------------------------------
    def set_still(self, path: Optional[Path]) -> None:
        if path and Path(path).is_file():
            try:
                pixbuf = self.Gtk.Image.new_from_file(str(path)).get_pixbuf()
                if pixbuf is not None:
                    scaled = pixbuf.scale_simple(
                        PREVIEW_W, PREVIEW_H, self.Gtk.gdkPixbuf.InterpType.BILINEAR
                    ) if hasattr(self.Gtk, "gdkpixbuf") else pixbuf
                    self.image.set_from_pixbuf(scaled or pixbuf)
                    self.image.set_visible(True)
            except Exception:
                pass
        if self._failed or not self._program:
            self.stack.set_visible_child_name("still")

    def set_shader(self, source: str, image_path: Optional[Path] = None) -> None:
        self._source = source
        self._error = ""
        self._failed = False
        self._image_path = Path(image_path) if image_path else None
        self._texture_key = ""
        self._started = time.monotonic()
        if not self._program:
            if self.area.get_realized():
                self.area.make_current()
                self._init_gl()
            else:
                self.stack.set_visible_child_name("still")
        else:
            try:
                self.area.make_current()
                self._compile_program()
                self.stack.set_visible_child_name("gl" if not self._failed else "still")
            except Exception as exc:
                self._fail(str(exc))
        self.area.queue_draw()

    def error(self) -> str:
        return self._error

    # -- frame loop -----------------------------------------------------
    def _request_frame(self) -> bool:
        if self.stack.get_visible_child_name() == "gl" and not self._failed:
            self.area.queue_draw()
        return True

    def _on_realize(self, area):
        try:
            area.make_current()
            self._init_gl()
        except Exception as exc:
            self._failed = True
            self._error = str(exc)
            self.stack.set_visible_child_name("still")

    def _on_resize(self, area, width, height):
        try:
            area.make_current()
            import OpenGL.GL as GL

            GL.glViewport(0, 0, max(1, int(width)), max(1, int(height)))
        except Exception:
            pass

    def _init_gl(self) -> None:
        import OpenGL.GL as GL

        if not self._program and self._source:
            self._compile_program()
        if not self._vao:
            self._vao = int(GL.glGenVertexArrays(1))
            self._vbo = int(GL.glGenBuffers(1))
        GL.glBindVertexArray(self._vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo)
        quad = (
            -1.0, -1.0, 1.0, -1.0, -1.0, 1.0,
            -1.0, 1.0, 1.0, -1.0, 1.0, 1.0,
        )
        import ctypes

        data = (ctypes.c_float * len(quad))(*quad)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, len(quad) * 4, data, GL.GL_STATIC_DRAW)
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, GL.GL_FALSE, 2 * 4, ctypes.c_void_p(0))
        GL.glBindVertexArray(0)

    def _compile_program(self) -> None:
        if not self._source:
            return
        import OpenGL.GL as GL

        if self._program and not self._texture_key:
            return
        if self._program:
            GL.glDeleteProgram(self._program)
            self._program = 0
        vertex_source = """#version 330 core
layout(location = 0) in vec2 aPos;
out vec2 vUv;
void main() {
    vUv = aPos * 0.5 + 0.5;
    gl_Position = vec4(aPos, 0.0, 1.0);
}
"""
        vertex = GL.glCreateShader(GL.GL_VERTEX_SHADER)
        GL.glShaderSource(vertex, vertex_source)
        GL.glCompileShader(vertex)
        if not GL.glGetShaderiv(vertex, GL.GL_COMPILE_STATUS):
            raise RuntimeError(str(GL.glGetShaderInfoLog(vertex)))
        fragment = GL.glCreateShader(GL.GL_FRAGMENT_SHADER)
        GL.glShaderSource(fragment, self._source)
        GL.glCompileShader(fragment)
        if not GL.glGetShaderiv(fragment, GL.GL_COMPILE_STATUS):
            log = GL.glGetShaderInfoLog(fragment)
            if isinstance(log, bytes):
                log = log.decode("utf-8", errors="replace")
            GL.glDeleteShader(vertex)
            GL.glDeleteShader(fragment)
            raise RuntimeError(str(log))
        program = GL.glCreateProgram()
        GL.glAttachShader(program, vertex)
        GL.glAttachShader(program, fragment)
        GL.glBindFragDataLocation(program, 0, "outColor")
        GL.glLinkProgram(program)
        GL.glDeleteShader(vertex)
        GL.glDeleteShader(fragment)
        if not GL.glGetProgramiv(program, GL.GL_LINK_STATUS):
            log = GL.glGetProgramInfoLog(program)
            if isinstance(log, bytes):
                log = log.decode("utf-8", errors="replace")
            GL.glDeleteProgram(program)
            raise RuntimeError(str(log))
        self._program = int(program)
        self._locs = {
            name: int(GL.glGetUniformLocation(program, name))
            for name in (
                "u_res", "u_audio0", "u_audio1", "u_audio2",
                "u_bands0", "u_bands1", "u_bands2", "u_bands3",
                "u_preset", "u_stream", "uCoverTex",
            )
        }
        self._upload_texture()
        self.stack.set_visible_child_name("gl")

    def _upload_texture(self) -> None:
        if Image is None or not self._image_path or not Path(self._image_path).is_file():
            return
        import OpenGL.GL as GL

        key = f"{self._image_path}:{Path(self._image_path).stat().st_mtime_ns}"
        if key == self._texture_key and self._texture:
            return
        image = Image.open(self._image_path).convert("RGB")
        image.thumbnail((512, 512))
        width, height = image.size
        data = image.tobytes()
        if not self._texture:
            self._texture = int(GL.glGenTextures(1))
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._texture)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_MIRRORED_REPEAT)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_MIRRORED_REPEAT)
        GL.glTexImage2D(
            GL.GL_TEXTURE_2D, 0, GL.GL_RGB, width, height, 0,
            GL.GL_RGB, GL.GL_UNSIGNED_BYTE, data,
        )
        self._texture_key = key

    def _fail(self, message: str) -> None:
        self._failed = True
        self._error = message
        self.stack.set_visible_child_name("still")
        print(f"[MP Visualisation Generator] Preview shader unavailable: {message}", file=sys.stderr)

    def _on_render(self, area, _context):
        if self._failed:
            return False
        try:
            area.make_current()
            if not self._program:
                self._init_gl()
            if not self._program:
                return False
            import OpenGL.GL as GL

            width = max(1, int(area.get_allocated_width()))
            height = max(1, int(area.get_allocated_height()))
            now = time.monotonic()
            # Synthetic telemetry: organic-looking bass/mid/treble and a
            # periodic kick so the preview demonstrates every animation path.
            t = now - self._started
            beat_phase = (t * 1.92) % 1.0
            beat = math.exp(-7.0 * beat_phase) * 0.9
            bass = 0.34 + 0.27 * max(0.0, math.sin(t * 1.13)) + 0.20 * beat
            mid = 0.30 + 0.24 * max(0.0, math.sin(t * 1.71 + 1.2))
            treble = 0.22 + 0.30 * max(0.0, math.sin(t * 2.73 + 0.4))
            rms = 0.36 + 0.18 * math.sin(t * 0.83) + 0.16 * beat
            bands = [max(0.0, 0.25 + 0.22 * math.sin(t * (1.0 + index * 0.37) + index)) for index in range(16)]
            GL.glViewport(0, 0, width, height)
            GL.glDisable(GL.GL_BLEND)
            GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glClearColor(0.0, 0.0, 0.0, 1.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            GL.glUseProgram(self._program)

            def uniform(name: str, *values: float) -> None:
                location = self._locs.get(name, -1)
                if location >= 0:
                    GL.glUniform4f(location, *(float(v) for v in values))

            uniform("u_res", width, height, t, 1.0)
            uniform("u_audio0", 0.28 + 0.15 * beat, bass, 0.30 + 0.12 * mid, mid)
            uniform("u_audio1", 0.30 + 0.12 * treble, treble, 0.25 + 0.15 * treble, rms)
            uniform("u_audio2", beat, (t * 0.7) % 1.0, (t * 0.43) % 1.0, (t * 1.1) % 1.0)
            uniform("u_bands0", *bands[0:4])
            uniform("u_bands1", *bands[4:8])
            uniform("u_bands2", *bands[8:12])
            uniform("u_bands3", *bands[12:16])
            uniform("u_preset", 1000.0, 0.0, 1.0, t)
            uniform("u_stream", 0.0, 0.0, 0.0, 0.0)
            texture_loc = self._locs.get("uCoverTex", -1)
            if texture_loc >= 0:
                GL.glUniform1i(texture_loc, 0)
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self._texture)
            GL.glBindVertexArray(self._vao)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 6)
            GL.glBindVertexArray(0)
            self._last_frame = now
            return True
        except Exception as exc:
            self._fail(str(exc))
            return False


# ---------------------------------------------------------------------------
# GTK window (GlassHost subclass)
# ---------------------------------------------------------------------------
class GeneratorWindow(GlassHost):
    """The MP Visualisation Generator window."""

    def __init__(self, *, image: Optional[str] = None, fullscreen: bool = False,
                 cli_theme: Optional[str] = None) -> None:
        super().__init__(argv=[], embed_check=False, mode="NORMAL", cli_theme=cli_theme, font=None)
        self.theme = read_player_theme()
        self.start_fullscreen = bool(fullscreen)
        self.initial_image = Path(image).expanduser() if image else None
        self.window = None
        self.preview: Optional[ShaderPreview] = None
        self.source_area = None
        self.css_provider = None
        self.status_label = None
        self.theme_label = None
        self.installed_combo = None
        self.installed_keys: List[str] = []
        self.last_bundle: Optional[Dict[str, Any]] = None
        self._drag_origin: Optional[Tuple[int, int, int, int]] = None
        self._closing = False
        self._last_theme = None
        self._bg_phase = 0.0
        self.name_entry = None
        self.style_combo = None
        self.speed_scale = None
        self.intensity_scale = None
        self.punch_scale = None
        self.seed_spin = None
        self.activate_check = None
        self.bg_label = None
        self.selected_image: Optional[Path] = self.initial_image

    # -- window lifecycle ----------------------------------------------
    def run(self) -> int:
        GLib, Gtk, _Vte = _import_gtk()
        import gi
        gi.require_version("Gdk", "3.0")
        try:
            gi.require_version("GdkPixbuf", "2.0")
        except Exception:
            pass
        from gi.repository import Gdk

        self.GLib, self.Gtk, self.Gdk = GLib, Gtk, Gdk
        window = Gtk.Window(title=GENERATOR_TITLE)
        window.set_decorated(False)
        window.set_app_paintable(True)
        window.set_default_size(WINDOW_W, WINDOW_H)
        window.set_size_request(1024, 600)
        window.set_position(Gtk.WindowPosition.CENTER)
        window.set_resizable(True)
        try:
            window.set_wmclass("MP_Visualisation_Generator", "MP_Visualisation_Generator")
        except Exception:
            pass
        screen = window.get_screen()
        visual = screen.get_rgba_visual() if screen is not None else None
        if visual is not None:
            window.set_visual(visual)
        window.connect("delete-event", self._on_delete)
        window.connect("key-press-event", self._on_key)
        window.connect("button-press-event", self._on_press)
        window.connect("button-release-event", self._on_release)
        window.connect("motion-notify-event", self._on_motion)
        window.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.window = window

        overlay = Gtk.Overlay()
        self.backdrop = Gtk.DrawingArea()
        self.backdrop.connect("draw", self._draw_glass)
        self.backdrop.set_hexpand(True)
        self.backdrop.set_vexpand(True)
        overlay.add(self.backdrop)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.set_margin_top(16)
        content.set_margin_bottom(14)
        content.set_margin_start(18)
        content.set_margin_end(18)
        content.set_hexpand(True)
        content.set_vexpand(True)
        overlay.add_overlay(content)
        window.add(overlay)

        self._build_ui(Gtk, content)
        self._refresh_theme()
        self._refresh_installed()
        if self.selected_image:
            self._set_background_label()
            self._update_preview_still()

        window.show_all()
        if self.start_fullscreen:
            window.fullscreen()
        else:
            GLib.timeout_add(80, self._center_window)
        GLib.timeout_add(120, self._theme_poll)
        signal.signal(signal.SIGINT, lambda *_: GLib.idle_add(self.close))
        signal.signal(signal.SIGTERM, lambda *_: GLib.idle_add(self.close))
        try:
            Gtk.main()
        finally:
            if self.preview is not None:
                try:
                    GLib.source_remove(self.preview._tick)
                except Exception:
                    pass
        return 0

    def _center_window(self) -> bool:
        if self.window is None:
            return False
        try:
            screen = self.window.get_screen()
            monitor = screen.get_primary_monitor() if hasattr(screen, "get_primary_monitor") else self.Gdk.Display.get_default().get_primary_monitor()
            geometry = screen.get_monitor_geometry(monitor)
            width, height = self.window.get_size()
            self.window.move(geometry.x + (geometry.width - width) // 2,
                             geometry.y + (geometry.height - height) // 2)
        except Exception:
            pass
        return False

    def _on_delete(self, *_args) -> bool:
        self.close()
        return True

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self.Gtk.main_quit()
        except Exception:
            pass

    def _on_press(self, _widget, event) -> bool:
        if event.button == 1:
            self._drag_origin = (int(event.x_root), int(event.y_root),
                                 *[int(v) for v in self.window.get_position()])
        elif event.button == 3:
            self._cycle_theme()
        return False

    def _on_release(self, *_args) -> bool:
        self._drag_origin = None
        return False

    def _on_motion(self, _widget, event) -> bool:
        if self._drag_origin and getattr(event, "state", 0) & self.Gdk.ModifierType.BUTTON1_MASK:
            x_root, y_root, win_x, win_y = self._drag_origin
            self.window.move(win_x + int(event.x_root) - x_root,
                             win_y + int(event.y_root) - y_root)
            return True
        return False

    # -- keyboard -------------------------------------------------------
    def _typing(self) -> bool:
        try:
            focus = self.window.get_focus()
            return isinstance(focus, (self.Gtk.Entry, self.Gtk.SpinButton, self.Gtk.ComboBoxText))
        except Exception:
            return False

    def _on_key(self, _widget, event) -> bool:
        key = self.Gdk.keyval_name(event.keyval) or ""
        lower = key.lower()
        ctrl = bool(event.state & self.Gdk.ModifierType.CONTROL_MASK)
        if key == "Escape":
            self.close()
            return True
        if ctrl and lower == "q":
            self.close()
            return True
        if self._typing() and lower not in ("f11",):
            return False
        if key == "F11" or lower == "f":
            self._toggle_fullscreen()
            return True
        if lower == "c":
            self._cycle_theme()
            return True
        if lower == "l":
            self._launch_player()
            return True
        if lower == "g":
            self._launch_gui()
            return True
        if lower == "m":
            self._launch_manager()
            return True
        return False

    def _toggle_fullscreen(self) -> None:
        try:
            if self.window.get_window().get_state() & self.Gdk.WindowState.FULLSCREEN:
                self.window.unfullscreen()
            else:
                self.window.fullscreen()
        except Exception:
            try:
                self.window.unfullscreen() if self.window.is_maximized() else self.window.fullscreen()
            except Exception:
                pass

    # -- theme ----------------------------------------------------------
    def _theme_ids(self) -> List[str]:
        return list(THEME_LABELS.keys())

    def _cycle_theme(self) -> None:
        order = self._theme_ids()
        current = read_player_theme()
        try:
            index = order.index(current)
        except ValueError:
            index = -1
        self._apply_theme(order[(index + 1) % len(order)], publish=True)

    def _theme_poll(self) -> bool:
        current = read_player_theme()
        if current != self._last_theme:
            self._apply_theme(current, publish=False)
        return True

    def _apply_theme(self, theme: str, *, publish: bool) -> None:
        self.theme = theme
        self._last_theme = theme
        if publish:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            try:
                (CONFIG_DIR / "theme").write_text(theme + "\n", encoding="utf-8")
            except OSError:
                pass
        self._refresh_theme()

    def _css(self) -> str:
        colours, _opacity, _factor = get_dynamic_theme_state(self.theme)
        accent, secondary, tertiary = colours
        dark = theme_uses_dark_glass(self.theme)
        ink = glass_foreground_rgb(self.theme)
        if dark:
            field = "rgba(12, 14, 20, 0.88)"
            field2 = "rgba(26, 30, 40, 0.92)"
            edge = "rgba(255, 255, 255, 0.20)"
            ink_css = f"rgb({max(ink[0], 210)}, {max(ink[1], 210)}, {max(ink[2], 210)})"
        else:
            field = "rgba(18, 22, 32, 0.18)"
            field2 = "rgba(255, 255, 255, 0.42)"
            edge = "rgba(20, 26, 40, 0.20)"
            ink_css = f"rgb({ink[0]}, {ink[1]}, {ink[2]})"
        accent_css = f"rgb({accent[0]}, {accent[1]}, {accent[2]})"
        second_css = f"rgb({secondary[0]}, {secondary[1]}, {secondary[2]})"
        return f"""
        window {{ background: transparent; color: {ink_css}; font-family: "{self.font_name}", Sans; }}
        label {{ color: {ink_css}; }}
        .title {{ font-size: 21px; font-weight: 700; color: {accent_css}; }}
        .subtitle {{ font-size: 12px; color: {ink_css}; opacity: 0.72; }}
        .section {{ font-size: 12px; font-weight: 700; color: {second_css}; letter-spacing: 0.6px; }}
        .hint {{ font-size: 11px; opacity: 0.70; }}
        .status {{ font-size: 12px; color: {accent_css}; }}
        .warn {{ font-size: 12px; color: #ffb86b; }}
        button {{
            background-image: none;
            background-color: {field};
            border: 1px solid {edge};
            border-radius: 10px;
            color: {ink_css};
            padding: 8px 12px;
            box-shadow: none;
            text-shadow: none;
            min-height: 18px;
        }}
        button:hover {{ background-color: {field2}; border-color: {accent_css}; }}
        button:active {{ background-color: {second_css}; color: #ffffff; }}
        button.go {{
            background-image: linear-gradient(120deg, {accent_css}, {second_css});
            border: 2px solid {accent_css};
            color: #0b0d14;
            font-weight: 700;
            font-size: 14px;
            padding: 14px 16px;
            border-radius: 14px;
        }}
        button.go:hover {{ border-color: #ffffff; color: #000000; }}
        entry, spinbutton, combobox button {{
            background-image: none;
            background-color: {field};
            border: 1px solid {edge};
            border-radius: 9px;
            color: {ink_css};
            padding: 7px 9px;
            caret-color: {accent_css};
        }}
        entry:focus, spinbutton:focus, combobox button:focus {{ border-color: {accent_css}; }}
        scale trough {{ background-color: {field}; border-radius: 8px; min-height: 9px; }}
        scale highlight {{ background-image: linear-gradient(90deg, {accent_css}, {second_css}); border-radius: 8px; }}
        scale slider {{ background-color: #ffffff; border: 2px solid {accent_css}; min-width: 16px; min-height: 16px; }}
        checkbutton check {{ background-color: {field}; border: 1px solid {edge}; border-radius: 4px; }}
        checkbutton check:checked {{ background-color: {accent_css}; border-color: {accent_css}; }}
        notebook tab {{ color: {ink_css}; }}
        .preview-frame {{ border: 1px solid {edge}; border-radius: 14px; background-color: rgba(0,0,0,0.22); }}
        .installed {{ font-size: 11px; }}
        """
    def _refresh_theme(self) -> None:
        if self.window is None:
            return
        colours, _opacity, _factor = get_dynamic_theme_state(self.theme)
        provider = self.Gtk.CssProvider()
        try:
            provider.load_from_data(self._css().encode("utf-8"))
        except Exception:
            return
        screen = self.window.get_screen()
        try:
            if self.css_provider is not None:
                self.Gtk.StyleContext.remove_provider_for_screen(screen, self.css_provider)
            self.Gtk.StyleContext.add_provider_for_screen(
                screen, provider, self.Gtk.STYLE_PROVIDER_PRIORITY_USER
            )
            self.css_provider = provider
        except Exception:
            pass
        if self.theme_label is not None:
            self.theme_label.set_text(f"Theme: {THEME_LABELS.get(self.theme, self.theme)}  ·  [C] cycle")
        self.backdrop.queue_draw()

    def _draw_glass(self, area, cr) -> bool:
        width = max(1, area.get_allocated_width())
        height = max(1, area.get_allocated_height())
        colours, _opacity, _factor = get_dynamic_theme_state(self.theme)
        accent, secondary, tertiary = colours
        dark = theme_uses_dark_glass(self.theme)
        self._bg_phase += 0.01
        # Rounded body with a diagonal liquid-glass gradient.
        radius = 26.0
        cr.new_sub_path()
        cr.arc(width - radius, radius, radius, -math.pi / 2, 0)
        cr.arc(width - radius, height - radius, radius, 0, math.pi / 2)
        cr.arc(radius, height - radius, radius, math.pi / 2, math.pi)
        cr.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.clip()
        if dark:
            start = (0.035, 0.040, 0.055)
            end = tuple(min(1.0, c / 255.0 * 0.40 + 0.03) for c in accent)
        else:
            start = (0.80, 0.85, 0.93)
            end = tuple(0.62 + c / 255.0 * 0.30 for c in accent)
        gradient = self.Gdk.cairo_pattern_create_linear(width * 0.1, 0, width * 0.9, height)
        gradient.add_color_stop_rgb(0.0, *start)
        gradient.add_color_stop_rgb(0.52, *(0.68 + c / 255.0 * 0.22 for c in secondary))
        gradient.add_color_stop_rgb(1.0, *end)
        cr.set_source(gradient)
        cr.paint()
        # Soft caustics, matching the player's still-water body.
        import cairo

        for index in range(7):
            bx = (math.sin(self._bg_phase * (0.6 + index * 0.12) + index * 1.7) * 0.5 + 0.5) * width
            by = (math.cos(self._bg_phase * (0.4 + index * 0.09) + index * 2.1) * 0.5 + 0.5) * height
            r = (0.12 + 0.05 * (index % 3)) * min(width, height)
            pat = cairo.RadialGradient(bx, by, 0, bx, by, r)
            alpha = 0.06 if not dark else 0.035
            pat.add_color_stop_rgba(0.0, tertiary[0] / 255, tertiary[1] / 255, tertiary[2] / 255, alpha)
            pat.add_color_stop_rgba(1.0, 0, 0, 0, 0)
            cr.set_source(pat)
            cr.arc(bx, by, r, 0, 2 * math.pi)
            cr.fill()
        # Bezel rim.
        cr.set_line_width(3.0)
        cr.set_source_rgba(1, 1, 1, 0.34 if not dark else 0.18)
        cr.arc(width - radius, radius, radius, -math.pi / 2, 0)
        cr.stroke()
        cr.set_line_width(1.0)
        cr.set_source_rgba(accent[0] / 255, accent[1] / 255, accent[2] / 255, 0.55)
        cr.new_sub_path()
        cr.arc(width - radius, height - radius, radius, 0, math.pi / 2)
        cr.arc(radius, height - radius, radius, math.pi / 2, math.pi)
        cr.stroke()
        return True

    # -- UI construction ------------------------------------------------
    def _build_ui(self, Gtk, root) -> None:
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title = Gtk.Label(label="MP VISUALISATION GENERATOR", xalign=0)
        title.get_style_context().add_class("title")
        subtitle = Gtk.Label(label="Generate • Preview • Install 3D audio-reactive MP Visualizer presets", xalign=0)
        subtitle.get_style_context().add_class("subtitle")
        title_box.pack_start(title, False, False, 0)
        title_box.pack_start(subtitle, False, False, 0)
        header.pack_start(title_box, True, True, 0)
        self.theme_label = Gtk.Label(label="Theme")
        self.theme_label.get_style_context().add_class("hint")
        header.pack_end(self.theme_label, False, False, 0)
        for label, callback in (
            ("Theme [C]", lambda *_: self._cycle_theme()),
            ("Fullscreen [F11]", lambda *_: self._toggle_fullscreen()),
            ("✕", lambda *_: self.close()),
        ):
            button = Gtk.Button(label=label)
            button.connect("clicked", callback)
            header.pack_end(button, False, False, 0)
        root.pack_start(header, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        body.set_hexpand(True)
        body.set_vexpand(True)
        root.pack_start(body, True, True, 0)

        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        controls.set_size_request(410, -1)
        body.pack_start(controls, False, False, 0)

        def section(text: str):
            label = Gtk.Label(label=text, xalign=0)
            label.get_style_context().add_class("section")
            controls.pack_start(label, False, False, 2)
            return label

        section("1 · PRESET IDENTITY")
        self.name_entry = Gtk.Entry()
        self.name_entry.set_placeholder_text("Preset name (blank = artwork filename)")
        controls.pack_start(self.name_entry, False, False, 0)

        self.bg_label = Gtk.Label(label="No background artwork selected", xalign=0)
        self.bg_label.set_ellipsize(3)  # Pango.EllipsizeMode.END
        self.bg_label.get_style_context().add_class("hint")
        controls.pack_start(self.bg_label, False, False, 0)
        background_button = Gtk.Button(label="Choose Background Artwork…")
        background_button.connect("clicked", lambda *_: self._choose_background())
        controls.pack_start(background_button, False, False, 0)

        section("2 · ANIMATION")
        self.style_combo = Gtk.ComboBoxText()
        for style_id, label, _slug in STYLES:
            self.style_combo.append(str(style_id), label)
        self.style_combo.set_active_id("0")
        controls.pack_start(self.style_combo, False, False, 0)

        self.speed_scale = self._labelled_scale(Gtk, controls, "Motion speed", 0.45, 2.20, 1.0)
        self.intensity_scale = self._labelled_scale(Gtk, controls, "Audio intensity", 0.25, 2.20, 1.0)
        self.punch_scale = self._labelled_scale(Gtk, controls, "Palette punch", 0.20, 1.80, 1.0)
        seed_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        seed_label = Gtk.Label(label="Scene seed", xalign=0)
        seed_row.pack_start(seed_label, False, False, 0)
        self.seed_spin = Gtk.SpinButton.new_with_range(0, 9999, 1)
        try:
            self.seed_spin.set_value(random.randrange(0, 9999))
        except Exception:
            pass
        seed_row.pack_end(self.seed_spin, False, False, 0)
        controls.pack_start(seed_row, False, False, 0)

        section("3 · INSTALL")
        self.activate_check = Gtk.CheckButton(label="Set as active preset in MP Mix Manager")
        self.activate_check.set_active(True)
        controls.pack_start(self.activate_check, False, False, 0)

        generate_button = Gtk.Button(label="[Generate This MP Visualiser Preset for MP Mix Manager Now]")
        generate_button.get_style_context().add_class("go")
        generate_button.set_line_wrap(True)
        generate_button.connect("clicked", lambda *_: self._generate())
        controls.pack_start(generate_button, False, False, 4)

        self.status_label = Gtk.Label(label="Ready. Choose artwork, then generate.", xalign=0)
        self.status_label.set_line_wrap(True)
        self.status_label.get_style_context().add_class("status")
        controls.pack_start(self.status_label, False, False, 0)

        section("4 · PRESET LIBRARY")
        library_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.installed_combo = Gtk.ComboBoxText()
        self.installed_combo.set_hexpand(True)
        library_row.pack_start(self.installed_combo, True, True, 0)
        activate_button = Gtk.Button(label="Set Active")
        activate_button.connect("clicked", lambda *_: self._activate_selected())
        library_row.pack_end(activate_button, False, False, 0)
        controls.pack_start(library_row, False, False, 0)
        io_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        export_button = Gtk.Button(label="Export Preset…")
        export_button.connect("clicked", lambda *_: self._export())
        import_button = Gtk.Button(label="Import Preset…")
        import_button.connect("clicked", lambda *_: self._import())
        io_row.pack_start(export_button, True, True, 0)
        io_row.pack_start(import_button, True, True, 0)
        controls.pack_start(io_row, False, False, 0)

        hint = Gtk.Label(
            label="Esc close · C theme · F/F11 fullscreen · L MP Liquid Player · G MP Gui · M MP Mix Manager",
            xalign=0,
        )
        hint.set_line_wrap(True)
        hint.get_style_context().add_class("hint")
        controls.pack_end(hint, False, False, 0)

        preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        preview_box.set_hexpand(True)
        preview_box.set_vexpand(True)
        body.pack_start(preview_box, True, True, 0)
        preview_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        preview_title = Gtk.Label(label="LIVE SHADER PREVIEW — the generated OpenGL preset", xalign=0)
        preview_title.get_style_context().add_class("section")
        preview_header.pack_start(preview_title, True, True, 0)
        self.preview_badge = Gtk.Label(label="640 × 360")
        self.preview_badge.get_style_context().add_class("hint")
        preview_header.pack_end(self.preview_badge, False, False, 0)
        preview_box.pack_start(preview_header, False, False, 0)

        frame = Gtk.Frame()
        frame.get_style_context().add_class("preview-frame")
        self.preview = ShaderPreview(Gtk, self.GLib)
        frame.add(self.preview.stack)
        frame.set_size_request(PREVIEW_W + 4, PREVIEW_H + 4)
        frame.set_hexpand(True)
        frame.set_vexpand(True)
        preview_box.pack_start(frame, True, True, 0)

        info = Gtk.Label(
            label="Vulkan SPIR-V + OpenGL GLSL + Metal MSL are generated from the same scene. "
                  "The live preview compiles the real OpenGL shader; the installed preset is added "
                  "to the MP Visualizer preset suite automatically.",
            xalign=0,
        )
        info.set_line_wrap(True)
        info.get_style_context().add_class("hint")
        preview_box.pack_start(info, False, False, 0)

    def _labelled_scale(self, Gtk, parent, label: str, low: float, high: float, value: float):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        text = Gtk.Label(label=label, xalign=0)
        text.set_size_request(110, -1)
        row.pack_start(text, False, False, 0)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, 0.01)
        scale.set_value(value)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.set_hexpand(True)
        row.pack_start(scale, True, True, 0)
        parent.pack_start(row, False, False, 0)
        return scale

    # -- actions --------------------------------------------------------
    def _describe(self, path: Path) -> None:
        self.selected_image = path
        self._set_background_label()
        self._update_preview_still()
        if not self.name_entry.get_text().strip():
            self.name_entry.set_text(prettify_name(path.stem))

    def _set_background_label(self) -> None:
        if self.selected_image and self.selected_image.is_file():
            self.bg_label.set_text(f"Artwork: {self.selected_image.name}")
        else:
            self.bg_label.set_text("No background artwork selected")

    def _update_preview_still(self) -> None:
        if self.preview is None or not self.selected_image:
            return
        try:
            with tempfile.TemporaryDirectory(prefix="mpvis-preview-") as temp:
                config = self._config()
                still = render_still_preview(config, self.selected_image, Path(temp) / "preview.png")
                if still:
                    self.preview.set_still(still)
        except Exception:
            pass

    def _config(self) -> Dict[str, Any]:
        image = self.selected_image
        if image is None or not image.is_file():
            raise ValueError("Choose a background artwork first")
        style_id = 0
        try:
            style_id = int(self.style_combo.get_active_id() or "0")
        except Exception:
            style_id = 0
        name = self.name_entry.get_text().strip() if self.name_entry is not None else ""
        if not name:
            name = prettify_name(image.stem)
        return {
            "image": str(image),
            "name": name,
            "style": style_id,
            "style_label": STYLE_NAMES.get(style_id, "Hybrid"),
            "speed": float(self.speed_scale.get_value()),
            "intensity": float(self.intensity_scale.get_value()),
            "punch": float(self.punch_scale.get_value()),
            "seed": float(self.seed_spin.get_value()),
        }

    def _choose_background(self) -> None:
        dialog = self.Gtk.FileChooserDialog(
            title="Choose background artwork for the visualiser",
            parent=self.window,
            action=self.Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons("Cancel", self.Gtk.ResponseType.CANCEL, "Use This Artwork", self.Gtk.ResponseType.ACCEPT)
        filters = [
            ("Image files", "*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp", "*.tif", "*.tiff"),
            ("All files", "*"),
        ]
        for label, *patterns in filters:
            filter_ = self.Gtk.FileFilter()
            filter_.set_name(label)
            for pattern in patterns:
                filter_.add_pattern(pattern)
            dialog.add_filter(filter_)
        if dialog.run() == self.Gtk.ResponseType.ACCEPT:
            path = Path(dialog.get_filename() or "")
            if path.is_file():
                self._describe(path)
        dialog.destroy()

    def _generate(self) -> None:
        try:
            config = self._config()
        except Exception as exc:
            self._set_status(f"⚠ {exc}", warning=True)
            return
        self._set_status("Generating shaders and installing the preset…")
        self.window.get_window().set_cursor(self.Gdk.Cursor.new_from_name(self.Gdk.Display.get_default(), "wait"))
        try:
            bundle = build_preset(
                config,
                install=True,
                activate=bool(self.activate_check.get_active()),
            )
            self.last_bundle = bundle
            missing = []
            if bundle["spv"] is None:
                missing.append("Vulkan SPIR-V (glslangValidator not found)")
            if bundle["metal"] is None:
                missing.append("Metal MSL")
            preview_source = generate_opengl_source(bundle["config"], steps=PREVIEW_STEPS)
            if self.preview is not None:
                self.preview.set_still(bundle["preview"])
                self.preview.set_shader(preview_source, Path(bundle["config"]["image"]))
            self._refresh_installed()
            selected = bundle["entry"]["key"] if bundle["entry"] else bundle["meta"]["key"]
            self._select_installed(selected)
            note = "" if not missing else "  (partial: " + ", ".join(missing) + ")"
            self._set_status(
                f"✔ Installed “{bundle['meta']['name']}” as preset #{bundle['entry']['id'] if bundle['entry'] else '?'} "
                f"for OpenGL, Vulkan and Metal.{note}",
                warning=False,
            )
        except Exception as exc:
            self._set_status(f"⚠ Generation failed: {exc}", warning=True)
            print(f"[MP Visualisation Generator] {exc}", file=sys.stderr)
        finally:
            try:
                self.window.get_window().set_cursor(None)
            except Exception:
                pass

    def _set_status(self, message: str, *, warning: bool) -> None:
        if self.status_label is None:
            return
        self.status_label.set_text(message)
        context = self.status_label.get_style_context()
        try:
            if warning:
                context.add_class("warn")
                context.remove_class("status")
            else:
                context.remove_class("warn")
                context.add_class("status")
        except Exception:
            pass

    def _refresh_installed(self) -> None:
        if self.installed_combo is None:
            return
        self.installed_combo.remove_all()
        self.installed_keys = []
        for preset in user_presets.load_user_presets():
            self.installed_keys.append(preset["key"])
            self.installed_combo.append(preset["key"], preset["name"])
        if not self.installed_keys:
            self.installed_combo.append("__none__", "No user presets installed yet")
        else:
            active = user_presets.active_key()
            if active in self.installed_keys:
                self.installed_combo.set_active_id(active)
            else:
                self.installed_combo.set_active(0)

    def _selected_key(self) -> Optional[str]:
        key = self.installed_combo.get_active_id() if self.installed_combo else None
        return key if key in self.installed_keys else None

    def _select_installed(self, key: str) -> None:
        if self.installed_combo is None or key not in self.installed_keys:
            return
        self.installed_combo.set_active_id(key)

    def _activate_selected(self) -> None:
        key = self._selected_key()
        if not key:
            self._set_status("⚠ Choose an installed user preset first.", warning=True)
            return
        user_presets.set_active_key(key)
        self._set_status(f"✔ Active MP Visualizer preset: {key}", warning=False)

    def _export(self) -> None:
        key = self._selected_key()
        if not key:
            self._set_status("⚠ Generate or select a user preset before exporting.", warning=True)
            return
        preset = user_presets.find_user_preset(key)
        default_name = f"{preset['key']}{user_presets.PACKAGE_EXT}" if preset else f"preset{user_presets.PACKAGE_EXT}"
        dialog = self.Gtk.FileChooserDialog(
            title="Export MP Visualizer preset",
            parent=self.window,
            action=self.Gtk.FileChooserAction.SAVE,
        )
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_name(default_name)
        dialog.add_buttons("Cancel", self.Gtk.ResponseType.CANCEL, "Export", self.Gtk.ResponseType.ACCEPT)
        filter_ = self.Gtk.FileFilter()
        filter_.set_name(f"MP Visualizer preset (*{user_presets.PACKAGE_EXT})")
        filter_.add_pattern(f"*{user_presets.PACKAGE_EXT}")
        dialog.add_filter(filter_)
        if dialog.run() == self.Gtk.ResponseType.ACCEPT:
            name = dialog.get_filename() or default_name
            try:
                target = user_presets.export_package(key, Path(name))
                self._set_status(f"✔ Exported preset to {target}", warning=False)
            except Exception as exc:
                self._set_status(f"⚠ Export failed: {exc}", warning=True)
        dialog.destroy()

    def _import(self) -> None:
        dialog = self.Gtk.FileChooserDialog(
            title="Import MP Visualizer preset",
            parent=self.window,
            action=self.Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons("Cancel", self.Gtk.ResponseType.CANCEL, "Import", self.Gtk.ResponseType.ACCEPT)
        filter_ = self.Gtk.FileFilter()
        filter_.set_name(f"MP Visualizer preset (*{user_presets.PACKAGE_EXT}, *.zip)")
        filter_.add_pattern(f"*{user_presets.PACKAGE_EXT}")
        filter_.add_pattern("*.zip")
        dialog.add_filter(filter_)
        if dialog.run() == self.Gtk.ResponseType.ACCEPT:
            package = Path(dialog.get_filename() or "")
            try:
                entry = user_presets.import_package(package, activate=False)
                self._refresh_installed()
                self._select_installed(entry["key"])
                self._set_status(
                    f"✔ Imported “{entry['name']}” as preset #{entry['id']}. "
                    "OpenGL and Metal are ready; Vulkan uses the packaged SPIR-V when present.",
                    warning=False,
                )
            except Exception as exc:
                self._set_status(f"⚠ Import failed: {exc}", warning=True)
        dialog.destroy()

    # -- external apps --------------------------------------------------
    def _spawn(self, command: List[str], cwd: Optional[Path] = None) -> None:
        try:
            subprocess.Popen(
                command,
                cwd=str(cwd or ROOT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as exc:
            self._set_status(f"⚠ Could not launch: {exc}", warning=True)

    def _launch_player(self) -> None:
        launcher = ROOT / "scripts" / "launch_liquid_glass.sh"
        if launcher.is_file():
            self._spawn(["bash", str(launcher)], cwd=ROOT)
            self._set_status("▶ MP Liquid Player launched.", warning=False)
        else:
            self._set_status("⚠ scripts/launch_liquid_glass.sh not found.", warning=True)

    def _launch_gui(self) -> None:
        launcher = ROOT / "MP_Gui.sh"
        if launcher.is_file():
            self._spawn(["bash", str(launcher)], cwd=ROOT)
            self._set_status("▶ MP Gui launched.", warning=False)
        else:
            self._set_status("⚠ MP_Gui.sh not found.", warning=True)

    def _launch_manager(self) -> None:
        manager = ROOT / "Mix_Archive_Manager.sh"
        if not manager.is_file():
            self._set_status("⚠ Mix_Archive_Manager.sh not found.", warning=True)
            return
        terminals = (
            ("konsole", ["konsole", "-e", "bash", str(manager)]),
            ("x-terminal-emulator", ["x-terminal-emulator", "-e", "bash", str(manager)]),
            ("gnome-terminal", ["gnome-terminal", "--", "bash", str(manager)]),
            ("xfce4-terminal", ["xfce4-terminal", "-e", f"bash {manager}"]),
            ("mate-terminal", ["mate-terminal", "-e", f"bash {manager}"]),
            ("xterm", ["xterm", "-e", "bash", str(manager)]),
        )
        for binary, command in terminals:
            if shutil.which(binary):
                self._spawn(command, cwd=ROOT)
                self._set_status(f"▶ MP Mix Manager launched in {binary}.", warning=False)
                return
        # Last resort: run the manager directly. It is a TUI, so this is only
        # sensible when the generator itself was started from a terminal.
        self._spawn(["bash", str(manager)], cwd=ROOT)
        self._set_status("▶ MP Mix Manager launched (no terminal emulator found).", warning=False)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    image: Optional[str] = None
    fullscreen = False
    generate_only = False
    name: Optional[str] = None
    style = 0
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("--image", "-i") and index + 1 < len(args):
            image = args[index + 1]
            index += 2
            continue
        if arg in ("--name", "-n") and index + 1 < len(args):
            name = args[index + 1]
            index += 2
            continue
        if arg in ("--style", "-s") and index + 1 < len(args):
            try:
                style = int(args[index + 1])
            except ValueError:
                style = 0
            index += 2
            continue
        if arg in ("--fullscreen", "-f"):
            fullscreen = True
        elif arg in ("--generate", "-g"):
            generate_only = True
        elif arg in ("--help", "-h"):
            print(__doc__)
            return 0
        index += 1
    if generate_only:
        if not image:
            print("--generate needs --image FILE", file=sys.stderr)
            return 2
        bundle = build_preset({"image": image, "name": name or "", "style": style}, install=True, activate=True)
        print(f"Installed {bundle['meta']['name']} ({bundle['meta']['key']}) in {bundle['directory']}")
        return 0
    app = GeneratorWindow(image=image, fullscreen=fullscreen)
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
