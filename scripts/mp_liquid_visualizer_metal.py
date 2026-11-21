"""MP Liquid Visualizer — native Apple Metal renderer.

This is the Metal backend for the standalone MP Visualizer. It draws the same
picture as ``mp_liquid_visualizer_vulkan`` and the OpenGL renderer: the look
lives in ``shaders/mp_liquid_visualizer.frag`` and is translated to Metal
Shading Language at load time by :mod:`mp_metal_shader`, so there is one shader
source of truth rather than three copies that drift apart.

Design notes
------------
* **No MoltenVK.** The Vulkan backend on macOS runs through MoltenVK, which
  translates every pipeline at load and puts a layer between the shader and the
  GPU. Talking to Metal directly removes that layer, which is the reason this
  backend exists at all.
* **No PyObjC.** :mod:`mp_metal_bridge` drives ``objc_msgSend`` with
  ``ctypes``, which is what the rest of this project already does.
* **Window.** A GLFW window created with ``GLFW_CLIENT_API = GLFW_NO_API``; its
  Cocoa ``NSWindow`` gets a ``CAMetalLayer``. GLFW still owns the event loop,
  keyboard handling and monitor queries, so F10/F11 spanning, the screensaver
  and the window-shape code keep working unchanged.
* **March resolution.** The raymarch is the expensive part. Windowed, it
  renders at a capped long edge and scales the result onto the window with a
  second pass, so a 1094x734 window does not raymarch 803k pixels. Fullscreen
  is the exception: the picture fills the user's whole display, where an
  upscaled 768-wide march read as visibly blocky. A fullscreen window marches
  from a full 1920x1080 target instead, and anything it has to scale is
  resampled bicubically rather than bilinearly.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import hashlib
import json
import math
import os
import platform
import re
import sys
import time
from ctypes import (POINTER, Structure, byref, c_char_p, c_double, c_int,
                    c_long, c_void_p)
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
SHADER_DIR = ROOT / "shaders"


def _macos_window_for_pid(pid: int):
    """(CGWindowID, x, y, width, height) for the largest normal window of pid.

    ``glfwGetCocoaWindow`` gives the NSWindow, but its ``windowNumber`` came
    back as 0 for this borderless host, so the companion could not anchor to
    it. The window server's own list always carries the real number and
    rectangle. Only normal-layer windows larger than a dialog are considered,
    so helper windows never win.
    """
    if sys.platform != "darwin" or int(pid or 0) <= 0:
        return None
    try:
        import ctypes

        class CGPoint(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

        class CGSize(ctypes.Structure):
            _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]

        class CGRect(ctypes.Structure):
            _fields_ = [("origin", CGPoint), ("size", CGSize)]

        cg = ctypes.CDLL(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cf = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
        cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        cg.CGRectMakeWithDictionaryRepresentation.restype = ctypes.c_bool
        cg.CGRectMakeWithDictionaryRepresentation.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(CGRect)]
        cf.CFArrayGetCount.restype = ctypes.c_long
        cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
        cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
        cf.CFDictionaryGetValue.restype = ctypes.c_void_p
        cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cf.CFNumberGetValue.restype = ctypes.c_bool
        cf.CFNumberGetValue.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRelease.restype = None

        def key(name):
            return ctypes.c_void_p.in_dll(cg, name).value

        def number(value, kind=4):
            out = ctypes.c_int64()
            if not value or not cf.CFNumberGetValue(
                    value, kind, ctypes.byref(out)):
                return 0
            return int(out.value)

        keys = {
            "pid": key("kCGWindowOwnerPID"),
            "layer": key("kCGWindowLayer"),
            "number": key("kCGWindowNumber"),
            "bounds": key("kCGWindowBounds"),
        }
        array = cg.CGWindowListCopyWindowInfo(1 | 16, 0)
        if not array:
            return None
        best = None
        try:
            for index in range(cf.CFArrayGetCount(array)):
                record = cf.CFArrayGetValueAtIndex(array, index)
                if not record:
                    continue
                if number(cf.CFDictionaryGetValue(record, keys["pid"]), 3) != int(pid):
                    continue
                if number(cf.CFDictionaryGetValue(record, keys["layer"]), 3) not in (0, 1, 2):
                    continue
                bounds = cf.CFDictionaryGetValue(record, keys["bounds"])
                rect = CGRect()
                if not bounds or not cg.CGRectMakeWithDictionaryRepresentation(
                        bounds, ctypes.byref(rect)):
                    continue
                width, height = int(rect.size.width), int(rect.size.height)
                if width < 200 or height < 200:
                    continue
                wid = number(cf.CFDictionaryGetValue(record, keys["number"]))
                area = width * height
                if best is None or area > best[0]:
                    best = (area, wid, int(rect.origin.x), int(rect.origin.y),
                            width, height)
        finally:
            cf.CFRelease(array)
        return best[1:] if best else None
    except Exception:
        return None

try:  # imported as ``scripts.mp_liquid_visualizer_metal``
    from mp_metal_bridge import AutoreleasePool, MetalBridge, ObjC, CGSize, MetalError
    from mp_metal_shader import source_digest, translate_cached
    from visualizer_monitor_span import MonitorSpanMixin, ROLE_SPAWNED
    from visualizer_player_bond import PlayerBondMixin
    from visualizer_random_mode import RandomPresetMixin
    from visualizer_window_shape import DEFAULT_CORNER_RADIUS, RoundedWindowShaper
    from visualizer_z_cycle import (WINDOW_MODE_DEFAULT, WINDOW_MODE_FULLSCREEN,
                                    WINDOW_MODE_HALF, ZWindowCycleMixin)
except ImportError:  # imported from the project root's ``scripts`` directory
    from scripts.mp_metal_bridge import AutoreleasePool, MetalBridge, ObjC, CGSize, MetalError
    from scripts.mp_metal_shader import source_digest, translate_cached
    from scripts.visualizer_monitor_span import MonitorSpanMixin, ROLE_SPAWNED
    from scripts.visualizer_player_bond import PlayerBondMixin
    from scripts.visualizer_random_mode import RandomPresetMixin
    from scripts.visualizer_window_shape import DEFAULT_CORNER_RADIUS, RoundedWindowShaper
    from scripts.visualizer_z_cycle import (WINDOW_MODE_DEFAULT, WINDOW_MODE_FULLSCREEN,
                                            WINDOW_MODE_HALF, ZWindowCycleMixin)

# ---------------------------------------------------------------------------
# GLFW constants (glfw3.h)
# ---------------------------------------------------------------------------
GLFW_TRUE, GLFW_FALSE = 1, 0
GLFW_CLIENT_API = 0x00022001
GLFW_NO_API = 0
GLFW_FOCUSED = 0x00020001
GLFW_ICONIFIED = 0x00020002
GLFW_RESIZABLE = 0x00020003
GLFW_VISIBLE = 0x00020004
GLFW_DECORATED = 0x00020005
GLFW_AUTO_ICONIFY = 0x00020006
GLFW_FLOATING = 0x00020007
GLFW_FOCUS_ON_SHOW = 0x0002000C
GLFW_FRAMEBUFFER_SRGB = 0x0002100B

GLFW_PRESS = 1

GLFW_KEY_ESCAPE = 256
GLFW_KEY_Q = 81
GLFW_KEY_Z = 90
GLFW_KEY_H = 72
GLFW_KEY_R = 82
# W opens an MP Black Glass pane at the window's own size, like every other
# MP window. GLFW reports the same key code on every backend.
GLFW_KEY_W = 87
GLFW_KEY_1 = 49
GLFW_KEY_9 = 57
GLFW_KEY_EQUAL = 61
GLFW_KEY_MINUS = 45
GLFW_KEY_KP_ADD = 334
GLFW_KEY_KP_SUBTRACT = 333
GLFW_KEY_LEFT = 263
GLFW_KEY_RIGHT = 262
GLFW_KEY_F10 = 299
GLFW_KEY_F11 = 300


class GLFWvidmode(Structure):
    _fields_ = [
        ("width", c_int), ("height", c_int),
        ("redBits", c_int), ("greenBits", c_int), ("blueBits", c_int),
        ("refreshRate", c_int),
    ]


def is_macos() -> bool:
    return sys.platform == "darwin" or platform.system().lower() == "darwin"


def is_apple_silicon() -> bool:
    if not is_macos():
        return False
    return platform.machine().lower() in ("arm64", "aarch64")


def metal_available() -> bool:
    """True when this process can create a Metal device.

    Probed rather than assumed: a macOS VM without paravirtualised graphics
    reports ``darwin`` but has no ``MTLDevice``.
    """
    if not is_macos():
        return False
    try:
        library = ctypes.CDLL("/System/Library/Frameworks/Metal.framework/Metal")
        library.MTLCreateSystemDefaultDevice.restype = c_void_p
        library.MTLCreateSystemDefaultDevice.argtypes = []
        return bool(library.MTLCreateSystemDefaultDevice())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Raymarch sizing. The defaults match the Vulkan macOS policy so both backends
# frame the scene identically at the same window size.
# ---------------------------------------------------------------------------
DEFAULT_LONG_EDGE = 768
# Fullscreen is drawn from a real 1920x1080 target instead of the windowed cap.
# A fill-the-display picture at 768 wide is upscaled 2.5x on a 1080p panel and
# reads as blocky. Measured on an M2: a full 1920x1080 raymarch at the default
# 64 steps costs ~144 ms/frame (7 fps), so this is the *ceiling*, and
# ``FULLSCREEN_ADAPTIVE_MIN_SCALE`` below is what keeps the frame rate usable
# by trading resolution down toward it rather than up from 768.
FULLSCREEN_WIDTH, FULLSCREEN_HEIGHT = 1920, 1080
FULLSCREEN_LONG_EDGE = FULLSCREEN_WIDTH
MIN_MARCH_SIDE = 96
DEFAULT_MARCH_STEPS = 64


def _env_int(name: str, fallback: int, low: int, high: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return fallback
    try:
        value = int(float(raw))
    except ValueError:
        return fallback
    return max(low, min(high, value))


def march_long_edge() -> int:
    """Long edge of the raymarch target, in pixels."""
    return _env_int("MP_METAL_LONG_EDGE",
                    _env_int("MP_VISUALIZER_GL_LONG_EDGE", DEFAULT_LONG_EDGE,
                             MIN_MARCH_SIDE, 7680),
                    MIN_MARCH_SIDE, 7680)


def fullscreen_long_edge() -> int:
    """Long edge of the fullscreen raymarch target, in pixels.

    1920 by default, which is exactly 1080p on a 16:9 display. ``MP_METAL_
    FULLSCREEN_LONG_EDGE`` lowers or raises it the same way ``MP_METAL_LONG_
    EDGE`` tunes the windowed cap; it exists so a slower Mac can trade a little
    sharpness back for frame rate without giving up the whole fullscreen
    improvement.
    """
    return _env_int("MP_METAL_FULLSCREEN_LONG_EDGE", FULLSCREEN_LONG_EDGE,
                    MIN_MARCH_SIDE, 7680)


def adaptive_enabled() -> bool:
    """Whether the renderer may trade march resolution for frame rate.

    On by default: this shader is a full raymarch and a 1094x734 window cannot
    hold 60 fps on any Apple Silicon part at native march resolution. The
    Vulkan macOS path hides that by hard-capping the march at 768 pixels; this
    does the same thing but reacts to the measured frame time, so a fast GPU
    gets a sharper picture instead of a fixed cap. ``MP_METAL_ADAPTIVE=0``
    pins the resolution for reproducible captures and benchmarks.
    """
    raw = (os.environ.get("MP_METAL_ADAPTIVE") or "").strip().lower()
    if raw in ("0", "off", "false", "no"):
        return False
    return True


def fullscreen_adaptive_enabled() -> bool:
    """Whether the adaptive controller may retune a fullscreen march.

    On by default. A Mac that cannot raymarch a full 1920x1080 at a playable
    rate would otherwise drop to single-digit frame rates the moment the user
    pressed Z, which is not a trade anyone wants. The controller now works
    *from* a 1920x1080 target instead of the old 768 windowed cap, and stops at
    ``FULLSCREEN_ADAPTIVE_MIN_SCALE`` -- a much higher floor than the windowed
    one -- so the picture stays sharp while the frame rate stays usable. The
    upscale that remains is bicubic, which is what keeps it from looking
    blocky. ``MP_METAL_FULLSCREEN_ADAPTIVE=0`` pins the full 1920x1080.
    """
    raw = (os.environ.get("MP_METAL_FULLSCREEN_ADAPTIVE") or "").strip().lower()
    if raw in ("0", "off", "false", "no"):
        return False
    return True


# Frame-time targets for the adaptive march. Below MIN_SCALE the picture gets
# visibly soft, so the renderer stops there and accepts the lower frame rate
# rather than dropping resolution forever.
ADAPTIVE_MIN_SCALE = 0.55
ADAPTIVE_MAX_SCALE = 1.0
ADAPTIVE_TARGET_MS = 1000.0 / 60.0
# Fullscreen has its own, much higher floor. The windowed floor (0.55 of 768)
# is fine for a small window but is the soft picture the user complained about
# once it fills a display. Fullscreen therefore starts at a real 1920x1080 and
# only descends as far as the GPU actually needs: 0.65 of 1920 is 1248x702,
# still 1.6x the old 768 windowed cap and carried by a bicubic upscale. A
# faster Mac settles much higher, up to the full 1920x1080.
FULLSCREEN_ADAPTIVE_MIN_SCALE = 0.65
# Fullscreen aims lower than the windowed 60 fps because a raymarched 1080p is
# far more expensive; 30 fps is smooth for a slowly moving scene and leaves the
# controller room to keep the picture sharp instead of dropping to the floor.
FULLSCREEN_TARGET_MS = 1000.0 / 30.0

# MTLCommandBufferStatusCompleted
METAL_COMMAND_BUFFER_COMPLETED = 4


def march_steps() -> int:
    """Raymarch steps per pixel. Lower is faster and slightly softer."""
    return _env_int("MP_METAL_MARCH_STEPS",
                    _env_int("MP_VISUALIZER_GL_MARCH_STEPS", DEFAULT_MARCH_STEPS, 16, 84),
                    16, 84)


def scale_to_long_edge(width: int, height: int,
                       long_edge: Optional[int] = None) -> Tuple[int, int]:
    """Fit ``width``x``height`` inside ``long_edge`` keeping the aspect ratio."""
    width, height = max(2, int(width)), max(2, int(height))
    cap = max(MIN_MARCH_SIDE, int(long_edge if long_edge is not None else march_long_edge()))
    longest = max(width, height)
    if longest <= cap:
        return width, height
    scale = cap / longest
    return max(2, int(round(width * scale))), max(2, int(round(height * scale)))


def fullscreen_march_size(width: int, height: int,
                          long_edge: Optional[int] = None) -> Tuple[int, int]:
    """March size for a fullscreen window filling ``width``x``height``.

    Fullscreen is the one place the picture is *not* allowed to be soft. The
    windowed cap (768) upscaled to a display is what read as blocky, so a
    fullscreen window marches the largest image that fits a 1920x1080 box with
    its own aspect ratio -- exactly 1920x1080 on a 16:9 panel, 1728x1080 on a
    16:10 one, 1920x804 on an ultrawide. A display smaller than the box is
    still marched natively; nothing is ever upscaled past its own pixels.
    """
    width, height = max(2, int(width)), max(2, int(height))
    cap = max(MIN_MARCH_SIDE, int(long_edge if long_edge is not None else fullscreen_long_edge()))
    # The cap is a 16:9 long edge; the matching short edge is 9/16 of it.
    box_height = max(2, int(round(cap * FULLSCREEN_HEIGHT / FULLSCREEN_WIDTH)))
    scale = min(1.0, cap / width, box_height / height)
    return max(2, int(round(width * scale))), max(2, int(round(height * scale)))


def saver_march_size(width: int, height: int,
                     render_cap: Optional[Tuple[int, int]]) -> Tuple[int, int]:
    """March size for a screensaver window filling ``width``x``height``.

    The saver's ``render_cap`` is a ceiling; the march is the largest image
    with the window's aspect ratio that fits inside it, never larger.
    """
    if not render_cap:
        return scale_to_long_edge(width, height)
    cap_w = min(max(2, int(render_cap[0])), max(2, int(width)))
    cap_h = min(max(2, int(render_cap[1])), max(2, int(height)))
    return scale_to_long_edge(width, height, max(cap_w, cap_h))


def _cover_cache_dir() -> Path:
    return Path.home() / ".config" / "mix-manager" / "metal-covers"


def _cover_rgba_cached(preset: Dict[str, Any], tex_size: int) -> Tuple[int, bytes]:
    """The preset's cover art as ``tex_size``-square RGBA bytes, cached on disk.

    Decoding a 1024px PNG and Lanczos-resizing it takes about 0.5 s, which was
    paid on every launch. The resized bytes are stored next to the key's hash
    of the source path and its mtime, so replacing the artwork invalidates the
    cache instead of showing the old cover. Any failure falls back to the
    normal loader.
    """
    try:
        from mp_liquid_visualizer_vulkan import load_preset_cover_rgba
    except ImportError:
        from scripts.mp_liquid_visualizer_vulkan import load_preset_cover_rgba

    source = next((Path(p) for p in preset.get("cover_paths", []) if Path(p).is_file()), None)
    if source is None:
        # No artwork: let the loader produce its fallback (the Dynamic Waves
        # page is intentionally blank), and do not cache it.
        _size, _w, rgba = load_preset_cover_rgba(preset, tex_size=tex_size)
        return tex_size, rgba

    try:
        stat = source.stat()
        stamp = f"{source}:{stat.st_mtime_ns}:{stat.st_size}:{tex_size}"
    except OSError:
        stamp = f"{source}:?:?:{tex_size}"
    key = hashlib.sha256(stamp.encode("utf-8")).hexdigest()[:20]
    cached = _cover_cache_dir() / f"{key}.rgba"
    expected = tex_size * tex_size * 4
    try:
        if cached.is_file() and cached.stat().st_size == expected:
            return tex_size, cached.read_bytes()
    except OSError:
        pass

    size, _w, rgba = load_preset_cover_rgba(preset, tex_size=tex_size)
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        staging = cached.with_suffix(".tmp")
        staging.write_bytes(rgba)
        staging.replace(cached)
    except OSError:
        pass
    return size, rgba


def _cover_rgba(preset: Dict[str, Any], tex_size: int = 1024) -> Tuple[int, bytes]:
    return _cover_rgba_cached(preset, tex_size)


def _texture_size(objc: ObjC, texture) -> Tuple[int, int]:
    """``(width, height)`` of a Metal texture, or ``(0, 0)`` when unknown."""
    if not texture:
        return (0, 0)
    try:
        width = int(objc.send(texture, "width", restype=c_int, argtypes=[]))
        height = int(objc.send(texture, "height", restype=c_int, argtypes=[]))
        return (width, height)
    except Exception:
        return (0, 0)


# A tiny fullscreen blit used for the upscale pass. Kept in its own library so
# switching presets (which rebuilds the raymarch library) never invalidates it.
#
# This is a Catmull-Rom bicubic resample, not a bilinear one. The raymarch runs
# below the display size so it can hold its frame rate, which means this pass
# scales the picture up -- and bilinear upscaling is exactly what made
# fullscreen look blocky. Sixteen taps cost almost nothing on a GPU and keep
# edges and the bright filaments sharp while the scene is enlarged.
BLIT_MSL = """#include <metal_stdlib>
using namespace metal;

struct MPBlitOut {
    float4 position [[position]];
    float2 uv;
};

constexpr sampler mp_blit_sampler(coord::normalized,
                                  address::clamp_to_edge,
                                  filter::linear);

// Catmull-Rom (a = -0.5) reconstruction kernel.
static inline float mp_catmull_rom(float t) {
    t = fabs(t);
    if (t < 1.0) {
        return 1.5 * t * t * t - 2.5 * t * t + 1.0;
    }
    if (t < 2.0) {
        return -0.5 * t * t * t + 2.5 * t * t - 4.0 * t + 2.0;
    }
    return 0.0;
}

vertex MPBlitOut mp_blit_vertex(uint vertexID [[vertex_id]]) {
    float2 corner = float2(float((vertexID << 1) & 2), float(vertexID & 2));
    MPBlitOut out;
    out.position = float4(corner * 2.0 - 1.0, 0.0, 1.0);
    // Metal's clip space has +y up, but the shader (and Vulkan) treat the
    // framebuffer with y growing downwards. Flipping here keeps the picture
    // the same way up as every other backend.
    out.uv = float2(corner.x, 1.0 - corner.y);
    return out;
}

fragment float4 mp_blit_fragment(MPBlitOut in [[stage_in]],
                                 texture2d<float> source [[texture(0)]]) {
    float2 size = float2(float(source.get_width()), float(source.get_height()));
    // Work in texel space so the reconstruction lands on texel centres.
    float2 pos = in.uv * size - 0.5;
    float2 origin = floor(pos);
    float2 frac = pos - origin;

    float4 sum = float4(0.0);
    float weight_sum = 0.0;
    for (int j = -1; j <= 2; ++j) {
        float wy = mp_catmull_rom(float(j) - frac.y);
        for (int i = -1; i <= 2; ++i) {
            float wx = mp_catmull_rom(float(i) - frac.x);
            float w = wx * wy;
            float2 texel = (origin + float2(float(i), float(j)) + 0.5) / size;
            sum += source.sample(mp_blit_sampler, texel) * w;
            weight_sum += w;
        }
    }
    // Guard against a zero sum so a pathological sample cannot divide by 0.
    return sum / (fabs(weight_sum) < 1e-5 ? 1.0 : weight_sum);
}
"""


class LiquidVisualizerMetalRenderer(MonitorSpanMixin, RandomPresetMixin, PlayerBondMixin,
                                    ZWindowCycleMixin):
    """Metal backend for the standalone MP Visualizer.

    The public surface matches :class:`LiquidVisualizerVulkanRenderer`:
    ``open``, ``draw(bridge, capture)``, ``close``, the window geometry helpers,
    and the key handling the player and screen saver drive.
    """

    width = 700
    height = 700
    bezel_px = 7
    radius_px = 36

    def __init__(self, preset_name: Optional[str] = None, fullscreen: bool = False,
                 target_4k: bool = False,
                 span_bounds: Optional[Tuple[int, int, int, int]] = None,
                 span_role: str = "",
                 saver_render: Optional[Tuple[int, int]] = None) -> None:
        try:
            from mp_liquid_visualizer_vulkan import (
                WINDOW_HEIGHT, WINDOW_WIDTH, get_active_preset,
            )
        except ImportError:
            from scripts.mp_liquid_visualizer_vulkan import (
                WINDOW_HEIGHT, WINDOW_WIDTH, get_active_preset,
            )
        self.preset = get_active_preset(preset_name)
        self._preset_id_float = float(self.preset.get("id", 0))
        self.title = "MP Liquid Visualizer"
        self.device_name = ""
        self.cursor = (0.0, 0.0)
        self.should_close = False
        self.is_fullscreen = bool(fullscreen or target_4k)
        self.fullscreen_on_start = bool(fullscreen or target_4k)
        self.target_4k = bool(target_4k)
        self.mouse_down = False
        self.screensaver = False
        self._wake_event = False
        self._alive = False
        self._resize = True
        self._drag_active = False
        self._last_click_at = 0.0
        self._last_click_pos: Optional[Tuple[int, int]] = None
        self._user_selected_window_mode = False
        self._user_toggled_windowed = False
        self.window_mode = WINDOW_MODE_FULLSCREEN if self.fullscreen_on_start else WINDOW_MODE_DEFAULT
        # The shape the user first pressed Z on. The Metal renderer used to
        # start in "default" even with --fullscreen, so the first Z read as
        # "leave fullscreen" instead of "enter it".
        self._z_original_size: Optional[Tuple[int, int]] = None
        self._z_original_pos: Optional[Tuple[int, int]] = None
        self._saver_render = (int(saver_render[0]), int(saver_render[1])) if saver_render else None
        self._saver_bounds = None
        self._init_span_state(span_bounds, span_role)
        self._init_player_bond()
        self.init_random_mode()

        # Window geometry. macOS matches the Liquid Player's size, the same rule
        # the Vulkan renderer uses, so a bonded companion lines up exactly.
        self._windowed_size = (WINDOW_WIDTH, WINDOW_HEIGHT)
        self._windowed_offset = (72, 64)
        if is_macos():
            try:
                from liquid_player_visualizer import macos_liquid_player_window_size
            except ImportError:
                from scripts.liquid_player_visualizer import macos_liquid_player_window_size
            player_w, player_h = macos_liquid_player_window_size()
            self._windowed_size = (int(player_w), int(player_h))
            self._windowed_offset = (160, 80)
        if span_bounds:
            bx, by, bw, bh = (int(v) for v in span_bounds)
            cover = min(bw, bh)
            self._windowed_size = (cover, cover)
            self._windowed_offset = (bx + (bw - cover) // 2, by + (bh - cover) // 2)
        self.width, self.height = self._windowed_size
        self._cur_pos = self._windowed_offset
        self._saved_window_pos = self._windowed_offset
        self._default_windowed_size = tuple(self._windowed_size)
        half_w = max(64, self._windowed_size[0] // 2)
        half_h = max(64, self._windowed_size[1] // 2)
        self._half_windowed_size = (half_w, half_h)
        self._half_windowed_offset = (
            self._windowed_offset[0] + (self._windowed_size[0] - half_w) // 2,
            self._windowed_offset[1] + (self._windowed_size[1] - half_h) // 2,
        )
        self._span_backend = "metal"

        # Metal state, created in open().
        self._bridge: Optional[MetalBridge] = None
        self._blit_pipeline = None
        self._pipeline = None
        self._library = None
        self._cover_texture = None
        self._uniform_buffer = None
        self._uniform_view = None
        self._march_texture = None
        self._march_size = (0, 0)
        self._frame_texture = None
        self._frame_size = (0, 0)
        self._drawable_size = (0, 0)
        self._scale = 1.0
        self._glfw = None
        self._window = None
        self._layer = None
        self._nswindow = None
        self._shader_cache: Dict[int, Tuple[Any, Any]] = {}
        self._cover_cache: Dict[int, Any] = {}
        self._archives: List[Any] = []
        self._frame = 0
        self._last_draw_at = 0.0
        # Adaptive march resolution state. ``_resolution_scale`` multiplies the
        # configured long edge; ``_frame_times`` keeps a short history so one
        # slow frame (a preset switch, a disk hiccup) does not move it.
        self._adaptive = adaptive_enabled()
        self._resolution_scale = 1.0
        # Fullscreen runs its own scale with a higher floor; see
        # FULLSCREEN_ADAPTIVE_MIN_SCALE.
        self._fullscreen_scale = 1.0
        self._next_adapt_at = 0.0
        self._frame_times: List[float] = []
        # Command buffers retained until the GPU reports their timings.
        self._pending_buffers: List[int] = []
        self._init_span_state(span_bounds, span_role)
        # The window must share the Liquid Player's rounded silhouette. The
        # player's glass is drawn to a 36 px rounded rectangle, so a plain
        # square window shows its own corners through the player's cut-aways.
        self._shaper = RoundedWindowShaper(DEFAULT_CORNER_RADIUS)

    # -- identity --------------------------------------------------------
    @property
    def summary(self) -> str:
        return (f"MP_Liquid_Visualizer Metal device: {self.device_name or 'Apple GPU'} "
                f"| Preset: {self.preset.get('name', '?')} "
                f"| Window: {self.width}x{self.height} Fixed Borderless "
                f"| March: {self._march_size[0]}x{self._march_size[1]}")

    def window_size(self) -> Tuple[int, int]:
        return int(self.width), int(self.height)

    def framebuffer_size(self) -> Tuple[int, int]:
        return int(*self._drawable_size) if self._drawable_size else (int(self.width), int(self.height))

    def window_pos(self) -> Tuple[int, int]:
        return self._cur_pos

    def set_window_pos(self, x: int, y: int) -> None:
        self._cur_pos = (int(x), int(y))
        if self._window and self._glfw:
            self._glfw.glfwSetWindowPos(self._window, int(x), int(y))

    def request_close(self) -> None:
        self.should_close = True
        if self._window and self._glfw:
            self._glfw.glfwSetWindowShouldClose(self._window, 1)

    def set_panels(self, rects) -> None:
        """Parity with the liquid-glass host API; the visualizer draws no panels."""
        self._panels = rects

    def _set_windowed_size(self, width: int, height: int, apply: bool = False) -> None:
        """Remember a new windowed size (the player bond calls this)."""
        width, height = max(64, int(width)), max(64, int(height))
        self._windowed_size = (width, height)
        self.width, self.height = width, height
        if apply and self._window and self._glfw:
            self._glfw.glfwSetWindowSizeLimits(self._window, width, height, width, height)
            self._glfw.glfwSetWindowSize(self._window, width, height)
            self._resize = True

    # -- lifecycle -------------------------------------------------------
    def open(self):
        """Open the window and create the Metal device, pipeline and buffers."""
        if not is_macos():
            raise RuntimeError(
                "The Metal visualizer backend requires macOS on Apple Silicon."
            )
        self._glfw = self._load_glfw()
        self._bind_glfw()
        if not self._glfw.glfwInit():
            raise RuntimeError("glfwInit failed; cannot open the Metal visualizer window")
        self._window = self._create_window()
        try:
            self._bridge = MetalBridge()
        except MetalError:
            self._destroy_window()
            raise
        self.device_name = self._bridge.device_name
        self._attach_metal_layer()
        # Match the Liquid Player's rounded silhouette from the first frame,
        # so the window never appears with square corners behind the player.
        self._apply_window_shape()
        self._bind_input()
        self._compile_blit()
        self._build_pipeline(self.preset)
        self._create_cover_texture(self.preset)
        self._create_uniforms()
        self._alive = True
        self._resize = True
        if self.fullscreen_on_start:
            self.toggle_fullscreen()
        return self

    def _load_glfw(self):
        candidates = [
            "/opt/homebrew/lib/libglfw.3.dylib",
            "/opt/homebrew/lib/libglfw.dylib",
            "/usr/local/lib/libglfw.3.dylib",
            "/usr/local/lib/libglfw.dylib",
            "libglfw.3.dylib",
            "libglfw.dylib",
        ]
        for name in candidates:
            try:
                return ctypes.CDLL(name)
            except OSError:
                continue
        found = ctypes.util.find_library("glfw")
        if found:
            try:
                return ctypes.CDLL(found)
            except OSError:
                pass
        raise RuntimeError(
            "GLFW is required for the Metal visualizer window. "
            "Install it with: brew install glfw"
        )

    def _bind_glfw(self) -> None:
        g, CI, PI = self._glfw, c_int, POINTER(c_int)
        bindings = (
            ("glfwInit", c_int, []),
            ("glfwWindowHint", None, [CI, CI]),
            ("glfwCreateWindow", c_void_p, [CI, CI, c_char_p, c_void_p, c_void_p]),
            ("glfwDestroyWindow", None, [c_void_p]),
            ("glfwTerminate", None, []),
            ("glfwPollEvents", None, []),
            ("glfwWindowShouldClose", c_int, [c_void_p]),
            ("glfwSetWindowShouldClose", None, [c_void_p, CI]),
            ("glfwGetCocoaWindow", c_void_p, [c_void_p]),
            ("glfwGetWindowSize", None, [c_void_p, PI, PI]),
            ("glfwSetWindowSize", None, [c_void_p, CI, CI]),
            ("glfwGetWindowPos", None, [c_void_p, PI, PI]),
            ("glfwSetWindowPos", None, [c_void_p, CI, CI]),
            ("glfwGetFramebufferSize", None, [c_void_p, PI, PI]),
            ("glfwSetWindowSizeLimits", None, [c_void_p, CI, CI, CI, CI]),
            ("glfwSetWindowAttrib", None, [c_void_p, CI, CI]),
            ("glfwGetWindowAttrib", c_int, [c_void_p, CI]),
            ("glfwHideWindow", None, [c_void_p]),
            ("glfwShowWindow", None, [c_void_p]),
            ("glfwSetWindowMonitor", None, [c_void_p, c_void_p, CI, CI, CI, CI, CI]),
            ("glfwGetPrimaryMonitor", c_void_p, []),
            ("glfwGetMonitors", POINTER(c_void_p), [PI]),
            ("glfwGetMonitorPos", None, [c_void_p, PI, PI]),
            ("glfwGetVideoMode", POINTER(GLFWvidmode), [c_void_p]),
            ("glfwGetMonitorName", c_char_p, [c_void_p]),
            ("glfwSetKeyCallback", c_void_p, [c_void_p, c_void_p]),
            ("glfwSetMouseButtonCallback", c_void_p, [c_void_p, c_void_p]),
            ("glfwSetCursorPosCallback", c_void_p, [c_void_p, c_void_p]),
            ("glfwSetFramebufferSizeCallback", c_void_p, [c_void_p, c_void_p]),
            ("glfwFocusWindow", None, [c_void_p]),
            ("glfwRestoreWindow", None, [c_void_p]),
        )
        for name, restype, argtypes in bindings:
            if not hasattr(g, name):
                continue
            try:
                function = getattr(g, name)
                function.restype, function.argtypes = restype, argtypes
            except Exception:
                continue

    def _create_window(self):
        g = self._glfw
        # The owned companion opens unmapped when the Visualization button is
        # already off, so no frame flashes before the first poll hides it.
        start_visible = 0 if self._bond_should_start_hidden() else 1
        # A companion is a background layer. Asking for focus made it the
        # frontmost app on launch, which is why MP Gui and the player opened
        # hidden behind their own visualizer on macOS. A free window still
        # takes focus normally.
        companion = bool(getattr(self, "_bond_locked", False))
        focus = GLFW_FALSE if companion else GLFW_TRUE
        for hint, value in (
            (GLFW_CLIENT_API, GLFW_NO_API),
            (GLFW_RESIZABLE, GLFW_FALSE),
            (GLFW_DECORATED, GLFW_FALSE),
            (GLFW_VISIBLE, start_visible),
            (GLFW_FOCUSED, focus),
            (GLFW_FOCUS_ON_SHOW, focus),
            (GLFW_AUTO_ICONIFY, GLFW_FALSE),
        ):
            g.glfwWindowHint(hint, value)
        width, height = self._windowed_size
        window = g.glfwCreateWindow(int(width), int(height),
                                    self.title.encode("utf-8"), None, None)
        if not window:
            raise RuntimeError("GLFW could not open a borderless window for Metal")
        self._companion_visible = bool(start_visible)
        try:
            g.glfwSetWindowSizeLimits(window, int(width), int(height), int(width), int(height))
        except Exception:
            pass
        ox, oy = self._windowed_offset
        g.glfwSetWindowPos(window, int(ox), int(oy))
        self._cur_pos = (int(ox), int(oy))
        return window

    # -- Cocoa + CAMetalLayer --------------------------------------------
    def _attach_metal_layer(self) -> None:
        """Give the GLFW window's content view a ``CAMetalLayer``.

        GLFW created an ``NSWindow`` for us; all this does is drop a Metal layer
        into its content view. That is the supported "GLFW_NO_API + Metal" path
        and it keeps GLFW's own event handling intact.
        """
        objc = self._bridge.objc
        nswindow = self._glfw.glfwGetCocoaWindow(self._window)
        if not nswindow:
            raise RuntimeError("glfwGetCocoaWindow returned NULL for the Metal visualizer window")
        self._nswindow = nswindow
        layer = objc.send(objc.cls("CAMetalLayer"), "layer", restype=c_void_p, argtypes=[])
        if not layer:
            raise RuntimeError("could not create a CAMetalLayer")
        objc.send(layer, "setDevice:", self._bridge.device, restype=None, argtypes=[c_void_p])
        objc.send(layer, "setPixelFormat:", ctypes.c_uint64(80), restype=None,
                  argtypes=[ctypes.c_uint64])
        # framebufferOnly=0 is required: the upscale pass renders into the
        # drawable texture, which a framebuffer-only layer forbids.
        objc.send(layer, "setFramebufferOnly:", c_int(0), restype=None, argtypes=[c_int])
        # Not opaque: the window is clipped to the Liquid Player's rounded
        # rectangle (see ``_apply_window_shape``), and only a non-opaque layer
        # lets the cut-away corners show what is behind the window instead of
        # filling them with this layer's own black.
        objc.send(layer, "setOpaque:", c_int(0), restype=None, argtypes=[c_int])
        self._layer = layer

        content = objc.send(nswindow, "contentView", restype=c_void_p, argtypes=[])
        objc.send(content, "setWantsLayer:", c_int(1), restype=None, argtypes=[c_int])
        objc.send(content, "setLayer:", layer, restype=None, argtypes=[c_void_p])
        objc.send(nswindow, "setReleasedWhenClosed:", c_int(0), restype=None, argtypes=[c_int])
        objc.send(nswindow, "setOpaque:", c_int(0), restype=None, argtypes=[c_int])
        if getattr(self, "_bond_locked", False):
            # Keep the normal level; the host orders this window directly
            # beneath itself through the window server. A level below normal
            # put it behind Finder/Terminal, and a level above normal covered
            # the host, so neither can be the whole answer.
            objc.send(nswindow, "orderFront:", c_void_p(0), restype=None,
                      argtypes=[c_void_p])
        else:
            objc.send(nswindow, "makeKeyAndOrderFront:", c_void_p(0), restype=None,
                      argtypes=[c_void_p])

        # Retina: the drawable must be in backing pixels or the layer renders
        # at half resolution on every Apple Silicon laptop and Studio Display.
        scale = 1.0
        try:
            screen = objc.send(nswindow, "screen", restype=c_void_p, argtypes=[])
            if screen:
                scale = float(objc.send(screen, "backingScaleFactor",
                                        restype=c_double, argtypes=[]))
        except Exception:
            scale = 1.0
        self._scale = scale if scale and scale > 0 else 1.0
        objc.send(layer, "setContentsScale:", c_double(self._scale),
                  restype=None, argtypes=[c_double])
        self._sync_drawable_size()

    def _sync_drawable_size(self) -> None:
        """Match the layer's drawable to the window's backing size."""
        if not (self._layer and self._bridge and self._glfw and self._window):
            return
        width, height = c_int(), c_int()
        self._glfw.glfwGetFramebufferSize(self._window, byref(width), byref(height))
        w, h = max(1, int(width.value)), max(1, int(height.value))
        if (w, h) == self._drawable_size:
            return
        self._drawable_size = (w, h)
        self._bridge.objc.send(self._layer, "setDrawableSize:", CGSize(float(w), float(h)),
                               restype=None, argtypes=[CGSize])
        self.width = max(1, int(round(w / self._scale)))
        self.height = max(1, int(round(h / self._scale)))
        self._resize = True

    # -- shader + resources ----------------------------------------------
    def _march_for_size(self, width: int, height: int) -> Tuple[int, int]:
        if self._saver_render:
            return saver_march_size(width, height, self._saver_render)
        if self._want_fullscreen_march():
            # Fullscreen fills the display, so it marches a real 1080p rather
            # than the windowed cap that reads as blocky when upscaled.
            return fullscreen_march_size(width, height,
                                         self._fullscreen_long_edge_for_frame())
        return scale_to_long_edge(width, height, self._long_edge_for_frame())

    def _want_fullscreen_march(self) -> bool:
        """True when this window should march at the 1080p fullscreen size.

        Only a plain user fullscreen qualifies. A screensaver window and an
        F10/F11 span already have their own size policy (and the saver is a
        background effect), so they keep the capped march.
        """
        return bool(self.is_fullscreen and not self._saver_render
                    and not self.screensaver)

    def _long_edge_for_frame(self) -> int:
        """The march long edge for this frame, after adaptive scaling."""
        base = march_long_edge()
        scale = self._resolution_scale
        if scale >= 0.999:
            return base
        return max(MIN_MARCH_SIDE, int(round(base * scale)))

    def _fullscreen_long_edge_for_frame(self) -> int:
        """The fullscreen march long edge after adaptive scaling.

        Fullscreen runs its own controller with its own floor, because the
        windowed scale would either drag it soft (0.55 of the windowed cap) or,
        when pinned, drop a slower Mac to single-digit frame rates.
        """
        base = fullscreen_long_edge()
        if not fullscreen_adaptive_enabled():
            return base
        scale = self._fullscreen_scale
        if scale >= 0.999:
            return base
        # Clamp to the fullscreen floor here as well as in the controller: this
        # is the single place the size is decided, so a stale or externally
        # written scale can never make a fullscreen window soft.
        scale = max(FULLSCREEN_ADAPTIVE_MIN_SCALE, min(ADAPTIVE_MAX_SCALE, scale))
        return max(MIN_MARCH_SIDE, int(round(base * scale)))

    def _adapt_resolution(self, frame_ms: float) -> None:
        """Nudge the march scale toward the frame-time budget.

        The raymarch cost is very close to linear in pixel count, so the
        correction is scaled by how far off the target the last frame was.
        Changes are damped and rate-limited: without that the resolution
        visibly oscillates between two sizes while the scene animates.

        Fullscreen and windowed keep separate scales. They have different
        starting points (1920 vs 768) and different floors (0.75 vs 0.55), so
        sharing one number would either make fullscreen soft or make the
        windowed picture needlessly expensive.
        """
        if not self._adaptive:
            return
        fullscreen = self._want_fullscreen_march()
        if fullscreen and not fullscreen_adaptive_enabled():
            return
        now = time.monotonic()
        if now < self._next_adapt_at:
            return
        self._next_adapt_at = now + 0.30
        # Fullscreen has its own, lower frame-time target: a raymarched 1080p
        # cannot reach 60 fps on Apple Silicon, and pretending otherwise would
        # only force the controller down to its floor on every machine.
        target = FULLSCREEN_TARGET_MS if fullscreen else ADAPTIVE_TARGET_MS
        ratio = frame_ms / target
        if 0.85 <= ratio <= 1.20:
            return
        # Pixel count scales with the square of the long edge, so the scale
        # correction uses the square root of the time ratio.
        correction = math.sqrt(target / max(1.0, frame_ms))
        correction = max(0.85, min(1.15, correction))
        if fullscreen:
            floor = FULLSCREEN_ADAPTIVE_MIN_SCALE
            scale = self._fullscreen_scale * correction
            self._fullscreen_scale = max(floor, min(ADAPTIVE_MAX_SCALE, scale))
        else:
            floor = ADAPTIVE_MIN_SCALE
            scale = self._resolution_scale * correction
            self._resolution_scale = max(floor, min(ADAPTIVE_MAX_SCALE, scale))

    def _gpu_time_ms(self) -> float:
        """Milliseconds the most recent *completed* command buffer spent on the GPU.

        ``GPUStartTime``/``GPUEndTime`` are zero until the command buffer has
        finished executing, so the buffer has to be retained and polled on a
        later frame. Polling the status is non-blocking: reading the times of a
        buffer still in flight would stall the whole pipeline to the GPU's
        pace, which is exactly what this must not do.
        """
        pending = self._pending_buffers
        if not pending:
            return 0.0
        objc = self._bridge.objc
        sample = 0.0
        keep: List[int] = []
        for index, buffer in enumerate(pending):
            try:
                status = int(objc.send(buffer, "status", restype=c_int, argtypes=[]))
            except Exception:
                status = 0
            if status == METAL_COMMAND_BUFFER_COMPLETED:
                try:
                    start = float(objc.send(buffer, "GPUStartTime",
                                            restype=c_double, argtypes=[]))
                    end = float(objc.send(buffer, "GPUEndTime",
                                          restype=c_double, argtypes=[]))
                    if start and end > start:
                        sample = (end - start) * 1000.0
                except Exception:
                    sample = 0.0
                objc.release(buffer)
            elif index < len(pending) - 3:
                # Older than three frames and still not done: the GPU has far
                # bigger problems than resolution, so drop the sample rather
                # than hold the object forever.
                objc.release(buffer)
            else:
                keep.append(buffer)
        self._pending_buffers = keep
        return sample

    def _track_command_buffer(self, buffer) -> None:
        """Retain a command buffer so its GPU timing can be read next frame."""
        if not buffer:
            return
        try:
            self._bridge.objc.send(buffer, "retain", restype=c_void_p, argtypes=[])
            self._pending_buffers.append(buffer)
        except Exception:
            pass

    def _compile_blit(self) -> None:
        # The blit shader is a dozen lines, so it compiles instantly; only the
        # 3,400-line raymarch benefits from a binary archive.
        library = self._bridge.compile_library(BLIT_MSL, cache_key="mp-visualizer-blit")
        self._blit_pipeline = self._bridge.make_pipeline(
            library, "mp_blit_vertex", "mp_blit_fragment",
            pixel_format=80, label="MPVisualizer blit",
        )

    def _archive_path(self, preset_id: int, steps: int, digest: str) -> Path:
        """Where the compiled pipeline for this shader lives on disk."""
        import hashlib

        try:
            from mp_liquid_visualizer_vulkan import CONFIG_DIR
        except ImportError:
            from scripts.mp_liquid_visualizer_vulkan import CONFIG_DIR
        # The device name is part of the key: pipeline archives are compiled
        # for one GPU family and must not be shared across different Macs.
        device = re.sub(r"[^A-Za-z0-9]+", "_", self.device_name or "gpu").strip("_")
        key = f"p{preset_id}-s{steps}-{digest}"
        return Path(CONFIG_DIR) / "metal-pipelines" / f"{device}-{key}.binarchive"

    def _build_pipeline(self, preset: Dict[str, Any]) -> None:
        """Translate and compile this preset's folded MSL, then build its pipeline."""
        preset_id = int(preset.get("id", 0))
        steps = march_steps()
        cached = self._shader_cache.get(preset_id)
        if cached is not None and cached[0] == steps:
            self._library, self._pipeline = cached[1], cached[2]
            return
        custom_source = None
        try:
            try:
                from mp_visualizer_user_presets import metal_source_for
            except ImportError:
                from scripts.mp_visualizer_user_presets import metal_source_for  # type: ignore
            custom_source = metal_source_for(preset)
        except Exception:
            custom_source = None
        if custom_source:
            # The generator already translated this preset's GLSL with the
            # same mp_metal_shader translator, so compile the packaged MSL.
            source = custom_source
        else:
            source = translate_cached(SHADER_DIR / "mp_liquid_visualizer.frag",
                                      fold_preset=preset_id, march_steps=steps)
        digest = source_digest(source)
        # A binary archive turns the ~2.5 s shader compile into a disk load on
        # every launch after the first, which is what makes the window appear
        # instantly. It is keyed by the translated source so an edited shader
        # never reuses an old pipeline.
        archive = None
        try:
            from mp_metal_bridge import PipelineArchive
        except ImportError:
            from scripts.mp_metal_bridge import PipelineArchive
        try:
            archive = PipelineArchive(self._archive_path(preset_id, steps, digest), self._bridge)
        except Exception:
            archive = None
        library = self._bridge.compile_library(
            source, cache_key=f"mp-visualizer-p{preset_id}-s{steps}-{digest}"
        )
        pipeline = self._bridge.make_pipeline(
            library, "mp_visualizer_vertex", "mp_visualizer_fragment",
            pixel_format=80, label=f"MPVisualizer p{preset_id}",
            archive=archive,
        )
        if archive is not None:
            self._archives.append(archive)
        self._shader_cache[preset_id] = (steps, library, pipeline)
        self._library, self._pipeline = library, pipeline

    def _create_cover_texture(self, preset: Dict[str, Any]) -> None:
        texture_id = int(preset.get("id", 0))
        cached = self._cover_cache.get(texture_id)
        if cached is not None:
            self._cover_texture = cached
            return
        size, rgba = _cover_rgba_cached(preset, 1024)
        # RGBA8Unorm, shader-read only: the cover is sampled, never rendered to.
        texture = self._bridge.make_texture(size, size, pixel_format=70, usage=1)
        self._bridge.upload_texture(texture, rgba, size, size)
        self._cover_cache[texture_id] = texture
        self._cover_texture = texture

    def _create_uniforms(self) -> None:
        try:
            from mp_liquid_visualizer_vulkan import VisualizerUBO
        except ImportError:
            from scripts.mp_liquid_visualizer_vulkan import VisualizerUBO
        self._ubo_bytes = ctypes.sizeof(VisualizerUBO)
        self._uniform_buffer = self._bridge.make_buffer(self._ubo_bytes)
        pointer = self._bridge.buffer_pointer(self._uniform_buffer)
        self._uniform_view = (ctypes.c_float * (self._ubo_bytes // 4)).from_address(pointer)
        self._uniform_view[:] = [0.0] * len(self._uniform_view)

    def _ensure_targets(self) -> None:
        """(Re)create the raymarch and capture textures for the current size."""
        width, height = self._drawable_size
        march = self._march_for_size(width, height)
        bridge = self._bridge
        if march != self._march_size or not self._march_texture:
            if self._march_texture:
                bridge.release(self._march_texture)
            self._march_size = march
            self._march_texture = bridge.make_texture(
                march[0], march[1], pixel_format=80, usage=1 | 4,
            )
        self._resize = False

    def switch_preset(self, preset: Dict[str, Any]) -> None:
        if preset.get("id") == self.preset.get("id"):
            return
        self.preset = preset
        self._preset_id_float = float(preset.get("id", 0))
        # A partitioned window is a viewer, not the user's saved choice: F10/F11
        # copies and screensaver windows must not rewrite the active preset.
        if self._span_role != ROLE_SPAWNED and not self.screensaver:
            try:
                from mp_liquid_visualizer_vulkan import save_active_preset
            except ImportError:
                from scripts.mp_liquid_visualizer_vulkan import save_active_preset
            save_active_preset(preset["key"])
            self._publish_bonded_preset(preset)
        if self._bridge is not None:
            self._build_pipeline(preset)
            self._create_cover_texture(preset)
            self._resize = True

    # -- telemetry -------------------------------------------------------
    def _write_ubo(self, state: Any) -> None:
        """Publish one frame of telemetry into the mapped uniform buffer.

        The layout is the same 40-float block the Vulkan renderer writes, so
        the shader does not know which backend is driving it.
        """
        u = self._uniform_view
        u[0] = float(self._march_size[0])
        u[1] = float(self._march_size[1])
        u[2] = state.accum_time
        u[3] = 1.0 if state.is_playing else 0.0
        u[4] = state.sub_bass
        u[5] = state.bass
        u[6] = state.low_mid
        u[7] = state.mid
        u[8] = state.high_mid
        u[9] = state.treble
        u[10] = state.air
        u[11] = state.rms_energy
        u[12] = state.beat_pulse
        u[13] = state.bass_phase
        u[14] = state.mid_phase
        u[15] = state.high_phase
        bands = state.bands16
        for index in range(16):
            u[16 + index] = bands[index]
        u[32] = self._preset_id_float
        u[33] = state.pitch_factor
        u[34] = 1.0
        u[35] = getattr(state, "dynamic_time", state.accum_time)
        u[36], u[37], u[38], u[39] = self._stream_vector(state)

    def _stream_vector(self, state: Any) -> Tuple[float, float, float, float]:
        try:
            import pid_stream
        except Exception:
            return (0.0, 0.0, 0.0, 0.0)
        mode = "pid" if self.screensaver else "audio"
        now = float(getattr(state, "accum_time", 0.0) or 0.0)
        dt = max(0.0, min(0.05, self._frame_dt()))
        try:
            return pid_stream.stream_vector(mode, int(self._preset_id_float), now, dt,
                                            bridge=state, key=f"metalviz-{id(self)}")
        except Exception:
            return (0.0, 0.0, 0.0, 0.0)

    def _frame_dt(self) -> float:
        now = time.monotonic()
        last = self._last_draw_at
        self._last_draw_at = now
        if last <= 0.0:
            return 1.0 / 60.0
        return max(0.0, min(0.05, now - last))

    # -- frame -----------------------------------------------------------
    def view_hidden(self) -> bool:
        """True while the window is minimized or hidden; see the Vulkan renderer."""
        if not self._glfw or not self._window:
            return False
        try:
            return (self._glfw.glfwGetWindowAttrib(self._window, GLFW_ICONIFIED) == GLFW_TRUE
                    or self._glfw.glfwGetWindowAttrib(self._window, GLFW_VISIBLE) == GLFW_FALSE)
        except Exception:
            return False

    def _apply_window_shape(self) -> bool:
        """Clip the window to the Liquid Player's rounded corners.

        The Liquid Glass player draws its pane into a 36 px rounded rectangle,
        so the visualizer behind it is only hidden exactly where the pane is
        opaque. A plain square window therefore showed its own corners through
        the player's cut-aways. This applies the same rounded mask the Vulkan
        and OpenGL backends use, so all three share one silhouette.

        Fullscreen and screensaver windows fill their area on purpose and stay
        rectangular; leaving those modes restores the rounding.
        """
        shaper = getattr(self, "_shaper", None)
        if shaper is None or not self._window:
            return False
        if self.is_fullscreen or getattr(self, "_saver_bounds", None) or self.screensaver:
            return shaper.apply(width=1, height=1, enabled=False,
                                cocoa_window=self.cocoa_window())
        width, height = self.width, self.height
        if width < 8 or height < 8:
            return False
        return shaper.apply(width=width, height=height, enabled=True,
                            cocoa_window=self.cocoa_window())

    def cocoa_window(self) -> int:
        """The window's ``NSWindow`` pointer, or 0 on a non-macOS host."""
        nswindow = getattr(self, "_nswindow", None)
        if nswindow:
            return int(nswindow)
        glfw, window = getattr(self, "_glfw", None), getattr(self, "_window", None)
        if glfw is None or not window:
            return 0
        try:
            return int(glfw.glfwGetCocoaWindow(window) or 0)
        except Exception:
            return 0

    def _window_number(self) -> int:
        """CGWindowID of this window (the NSWindow number), or 0."""
        nswindow = self.cocoa_window()
        if not nswindow:
            return 0
        try:
            return int(self._bridge.objc.send(
                nswindow, "windowNumber", restype=c_long, argtypes=[]))
        except Exception:
            return 0

    def _order_below_host(self, host_wid: int) -> None:
        """Place this window directly beneath ``host_wid`` in the global stack.

        ``NSWindow orderWindow:relativeTo:`` only accepts a window in the same
        process. The window server's CGSOrderWindow works across processes, so
        the glass host can stay in front while the companion is immediately
        behind it, above every unrelated app.
        """
        # NSWindow windowNumber reads 0 for this borderless window, so ask the
        # window server for our own id as well as the host's.
        own = _macos_window_for_pid(os.getpid())
        mine = own[0] if own else self._window_number()
        if not mine or not host_wid:
            return
        for library in (
            "/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight",
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics",
        ):
            try:
                cg = ctypes.CDLL(library)
                cg.CGSMainConnectionID.restype = ctypes.c_int
                cg.CGSOrderWindow.argtypes = [
                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
                cg.CGSOrderWindow.restype = ctypes.c_int
                connection = int(cg.CGSMainConnectionID())
                if connection:
                    # -1 == below the relative window.
                    cg.CGSOrderWindow(connection, mine, -1, int(host_wid))
                    return
            except Exception:
                continue

    def _sync_macos_host(self) -> None:
        """Match the host rectangle and sit directly behind its window."""
        if sys.platform != "darwin" or not getattr(self, "_bond_locked", False):
            return
        if self.is_fullscreen:
            return
        now = time.monotonic()
        if now < getattr(self, "_mac_sync_at", 0.0):
            return
        self._mac_sync_at = now + 0.5
        try:
            from liquid_player_visualizer import (
                gui_window_size_path,
                player_window_size_path,
                read_published_gui_window_geometry,
                read_published_gui_window_id,
                read_published_player_window_geometry,
                read_published_player_window_id,
            )
        except ImportError:
            from scripts.liquid_player_visualizer import (
                gui_window_size_path,
                player_window_size_path,
                read_published_gui_window_geometry,
                read_published_gui_window_id,
                read_published_player_window_geometry,
                read_published_player_window_id,
            )
        gui = os.environ.get("MP_VISUALIZER_MATCH_GUI") == "1"
        geometry = (read_published_gui_window_geometry() if gui
                    else read_published_player_window_geometry())
        host_wid = (read_published_gui_window_id() if gui
                    else read_published_player_window_id())
        # The published pid is enough for the window-server lookup, which
        # returns the real CGWindowID and rectangle even when this host's
        # NSWindow windowNumber reads as 0.
        host_pid = 0
        try:
            path = gui_window_size_path() if gui else player_window_size_path()
            host_pid = int(json.loads(path.read_text(encoding="utf-8")).get("pid") or 0)
        except Exception:
            host_pid = 0
        host_window = _macos_window_for_pid(host_pid)
        if host_window:
            host_wid, x, y, width, height = host_window
            geometry = (x, y, width, height)
        if geometry:
            x, y, width, height = (int(value) for value in geometry)
            if (x, y, width, height) != getattr(self, "_mac_host_geom", None):
                self._mac_host_geom = (x, y, width, height)
                try:
                    self._glfw.glfwSetWindowSizeLimits(
                        self._window, width, height, width, height)
                except Exception:
                    pass
                self._set_windowed_size(width, height)
                try:
                    self._glfw.glfwSetWindowPos(self._window, x, y)
                    self._glfw.glfwSetWindowSize(self._window, width, height)
                    self.width, self.height = width, height
                except Exception:
                    pass
        if host_wid:
            # Reassert below the host on every sync: activating the host
            # raises it, and the companion must stay its immediate neighbour.
            self._mac_host_wid = int(host_wid)
            self._order_below_host(int(host_wid))
        # The host is raised to level 1; this window stays at level 0 but is
        # kept at the front of that level, above Finder/Terminal and below the
        # host. Reassert because activating another app can reorder level 0.
        nswindow = getattr(self, "_nswindow", None)
        if nswindow:
            try:
                self._bridge.objc.send(nswindow, "orderFront:", c_void_p(0),
                                       restype=None, argtypes=[c_void_p])
            except Exception:
                pass

    def poll(self) -> bool:
        if not self._alive or not self._window:
            return not self.should_close
        self._glfw.glfwPollEvents()
        if self._glfw.glfwWindowShouldClose(self._window):
            self.should_close = True
        self._sync_drawable_size()
        try:
            self._sync_macos_host()
        except Exception:
            pass
        try:
            self.poll_monitor_span()
        except Exception:
            pass
        # Keep the corner mask in step with every resize and window-mode
        # change. The shaper caches, so a frame where nothing moved is a
        # dictionary lookup.
        self._apply_window_shape()
        return not self.should_close

    def draw(self, bridge_state: Any, capture: bool = False):
        """Render one frame. Returns ``(w, h, bgra)`` when ``capture`` is set."""
        if not self.poll():
            return None
        # Minimized or hidden: nothing is shown, so no command buffer is encoded.
        if not capture and self.view_hidden():
            return None
        try:
            self.poll_random_mode()
        except Exception:
            pass
        try:
            from liquid_player_visualizer import march_advance
        except ImportError:
            from scripts.liquid_player_visualizer import march_advance
        march_advance(bridge_state, self._frame_dt())
        # Retune the march resolution from what the GPU actually spent on the
        # previous frame, then size the target for this one.
        gpu_ms = self._gpu_time_ms()
        if gpu_ms > 0.0:
            self._frame_times.append(gpu_ms)
            if len(self._frame_times) > 8:
                del self._frame_times[:-8]
            self._adapt_resolution(sum(self._frame_times) / len(self._frame_times))
        self._ensure_targets()
        self._write_ubo(bridge_state)
        self._encode_frame(capture=capture)
        if capture:
            return self._capture()
        return None

    def _encode_frame(self, capture: bool = False) -> None:
        bridge = self._bridge
        objc = bridge.objc
        pool = AutoreleasePool(objc)
        try:
            drawable = objc.send(self._layer, "nextDrawable", restype=c_void_p, argtypes=[])
            if not drawable and not capture:
                return
            target = None
            if capture:
                target = self._ensure_capture_texture()
            elif drawable:
                target = objc.send(drawable, "texture", restype=c_void_p, argtypes=[])
            if not target:
                return
            command_buffer = bridge.new_command_buffer()
            # When the march already matches the target exactly there is nothing
            # to scale, so the raymarch is drawn straight into the drawable and
            # the second pass is skipped entirely. That only happens with the
            # adaptive scale off and a window at or under the long-edge cap, but
            # that is exactly the case a benchmark pins, and it is one full
            # screen-resolution pass saved.
            direct = tuple(self._march_size) == _texture_size(objc, target)
            if direct:
                bridge.encode_fullscreen(
                    command_buffer, target, self._pipeline,
                    buffers=[(0, self._uniform_buffer, 0)],
                    textures=[(0, self._cover_texture)],
                )
            else:
                # Pass 1: raymarch into the capped-resolution texture.
                bridge.encode_fullscreen(
                    command_buffer, self._march_texture, self._pipeline,
                    buffers=[(0, self._uniform_buffer, 0)],
                    textures=[(0, self._cover_texture)],
                )
                # Pass 2: scale that texture over the drawable (or the capture
                # target). The march is capped, so this is usually an upscale.
                bridge.encode_fullscreen(command_buffer, target, self._blit_pipeline,
                                         textures=[(0, self._march_texture)])
            bridge.commit(command_buffer, drawable=drawable if not capture else 0,
                          wait=capture)
            # Retain this command buffer so a later frame can read the GPU's
            # own timing and retune the march resolution.
            self._track_command_buffer(command_buffer)
            self._frame += 1
        finally:
            pool.drain()

    def _ensure_capture_texture(self):
        size = self._drawable_size
        if self._frame_texture and size == self._frame_size:
            return self._frame_texture
        if self._frame_texture:
            self._bridge.release(self._frame_texture)
        self._frame_size = size
        self._frame_texture = self._bridge.make_texture(
            size[0], size[1], pixel_format=80, usage=1 | 4,
        )
        return self._frame_texture

    def _capture(self) -> Optional[Tuple[int, int, bytes]]:
        """Read the last rendered frame back as BGRA bytes."""
        if not self._frame_texture:
            return None
        return self._bridge.read_texture(self._frame_texture)

    # -- input -----------------------------------------------------------
    def _bind_input(self) -> None:
        from ctypes import CFUNCTYPE

        self._on_key_handle = CFUNCTYPE(None, c_void_p, c_int, c_int, c_int, c_int)(self._on_key)
        self._on_mouse_handle = CFUNCTYPE(None, c_void_p, c_int, c_int, c_int)(self._on_mouse)
        self._on_cursor_handle = CFUNCTYPE(None, c_void_p, c_double, c_double)(self._on_cursor)
        self._on_fb_handle = CFUNCTYPE(None, c_void_p, c_int, c_int)(self._on_fb)
        g, window = self._glfw, self._window
        g.glfwSetKeyCallback(window, ctypes.cast(self._on_key_handle, c_void_p))
        g.glfwSetMouseButtonCallback(window, ctypes.cast(self._on_mouse_handle, c_void_p))
        g.glfwSetCursorPosCallback(window, ctypes.cast(self._on_cursor_handle, c_void_p))
        g.glfwSetFramebufferSizeCallback(window, ctypes.cast(self._on_fb_handle, c_void_p))

    def _on_fb(self, _window, width, height) -> None:
        self._resize = True
        if width > 0 and height > 0:
            self._drawable_size = (int(width), int(height))

    def _on_cursor(self, _window, x, y) -> None:
        if self.screensaver:
            if getattr(self, "_saver_cursor_primed", False):
                self._wake_event = True
                self.request_close()
            self._saver_cursor_primed = True
            return
        self.cursor = (float(x), float(y))

    def _on_mouse(self, _window, button, action, _mods) -> None:
        if self.screensaver:
            self._wake_event = True
            self.request_close()
            return
        if button != 0:
            return
        self.mouse_down = action == GLFW_PRESS
        if not self.mouse_down:
            self._drag_active = False

    def _on_key(self, _window, key, _scancode, action, _mods) -> None:
        if action != GLFW_PRESS:
            return
        if self.screensaver:
            self._wake_event = True
            self.request_close()
            return
        if key in (GLFW_KEY_F10, GLFW_KEY_F11):
            try:
                if self.handle_span_key(key):
                    return
            except Exception:
                pass
        if key == GLFW_KEY_ESCAPE:
            self.request_close()
        elif key == GLFW_KEY_Q:
            if not self._player_bonded:
                self.request_close()
        elif key == GLFW_KEY_Z:
            self._cycle_window_mode_for_user()
        elif key in (GLFW_KEY_EQUAL, GLFW_KEY_KP_ADD, GLFW_KEY_RIGHT):
            self._cycle_preset(1)
        elif key in (GLFW_KEY_MINUS, GLFW_KEY_KP_SUBTRACT, GLFW_KEY_LEFT):
            self._cycle_preset(-1)
        elif GLFW_KEY_1 <= key <= GLFW_KEY_9:
            self._select_preset_slot(key - GLFW_KEY_1)
        elif key == GLFW_KEY_R:
            self.toggle_random_mode()
        elif key == GLFW_KEY_W:
            # W opens an MP Black Glass pane at exactly this window's size.
            self._open_black_glass_pane()

    def _open_black_glass_pane(self) -> bool:
        """W: open a Black Glass pane at exactly this visualizer's size."""
        try:
            from black_glass_window import spawn_black_glass
        except ImportError:
            try:
                from scripts.black_glass_window import spawn_black_glass
            except ImportError:
                return False
        try:
            width, height = self.window_size()
            x, y = self.window_pos()
        except Exception:
            return False
        return spawn_black_glass(int(width), int(height), int(x), int(y),
                                 "MP Liquid Visualizer") == "opened"

    def _cycle_preset(self, direction: int) -> None:
        try:
            from mp_liquid_visualizer_vulkan import cycle_preset
        except ImportError:
            from scripts.mp_liquid_visualizer_vulkan import cycle_preset
        nxt = cycle_preset(self.preset, direction)
        if nxt is not None:
            self.switch_preset(nxt)

    def _select_preset_slot(self, index: int) -> None:
        try:
            from mp_liquid_visualizer_vulkan import presets_by_slot
        except ImportError:
            from scripts.mp_liquid_visualizer_vulkan import presets_by_slot
        slots = presets_by_slot()
        if 0 <= index < len(slots):
            self.switch_preset(slots[index])

    # -- ZWindowCycleMixin primitives -------------------------------------
    def _z_window_size(self) -> Tuple[int, int]:
        """The live windowed size, which is what the user sees when pressing Z."""
        return int(self._windowed_size[0]), int(self._windowed_size[1])

    def _z_window_pos(self) -> Tuple[int, int]:
        return self.window_pos()

    def _z_apply_windowed(self, width: int, height: int, x: int, y: int) -> None:
        """Leave fullscreen (if needed) and place the window at that geometry."""
        self._set_windowed_size(int(width), int(height))
        if self.is_fullscreen and self._window and self._glfw:
            # GLFW only leaves fullscreen through glfwSetWindowMonitor(NULL,...),
            # so a plain resize would leave the window owning the whole screen.
            try:
                self._glfw.glfwSetWindowMonitor(self._window, None, int(x), int(y),
                                                int(width), int(height), 0)
            except Exception:
                pass
        self.is_fullscreen = False
        self._user_toggled_windowed = True
        self._apply_window_geometry(int(width), int(height), int(x), int(y))

    def _apply_window_geometry(self, width: int, height: int, x: int, y: int) -> None:
        if not (self._window and self._glfw):
            return
        try:
            self._glfw.glfwSetWindowSizeLimits(self._window, int(width), int(height),
                                               int(width), int(height))
            self._glfw.glfwSetWindowSize(self._window, int(width), int(height))
            self._glfw.glfwSetWindowPos(self._window, int(x), int(y))
        except Exception:
            pass
        self.width, self.height = int(width), int(height)
        self._cur_pos = (int(x), int(y))
        self._resize = True

    def toggle_fullscreen(self) -> bool:
        g, window = self._glfw, self._window
        if not (window and g):
            self.is_fullscreen = not self.is_fullscreen
            return self.is_fullscreen
        if not self.is_fullscreen:
            self._saved_window_pos = self.window_pos()
            monitor = g.glfwGetPrimaryMonitor()
            mode = g.glfwGetVideoMode(monitor) if monitor else None
            self.is_fullscreen = True
            if monitor and mode:
                video = mode.contents
                g.glfwSetWindowMonitor(window, monitor, 0, 0,
                                       int(video.width), int(video.height),
                                       int(video.refreshRate))
            else:
                g.glfwSetWindowSize(window, self.width, self.height)
        else:
            self.is_fullscreen = False
            ox, oy = self._saved_window_pos
            width, height = self._windowed_size
            g.glfwSetWindowMonitor(window, None, int(ox), int(oy),
                                   int(width), int(height), 0)
            try:
                g.glfwSetWindowSizeLimits(window, int(width), int(height),
                                           int(width), int(height))
            except Exception:
                pass
            self.width, self.height = int(width), int(height)
            self._cur_pos = (int(ox), int(oy))
        self._resize = True
        return self.is_fullscreen

    # -- teardown --------------------------------------------------------
    def _destroy_window(self) -> None:
        if self._window and self._glfw:
            try:
                self._glfw.glfwDestroyWindow(self._window)
            except Exception:
                pass
        self._window = None
        if self._glfw:
            try:
                self._glfw.glfwTerminate()
            except Exception:
                pass

    def close(self) -> None:
        """Tear the window down. Metal objects are owned and freed by the bridge."""
        self.should_close = True
        self._alive = False
        self._march_texture = self._frame_texture = self._cover_texture = None
        self._uniform_buffer = None
        self._shader_cache.clear()
        self._cover_cache.clear()
        # Closing an archive serialises it, which is where a fresh compile
        # becomes a disk file the next launch can load.
        for archive in self._archives:
            try:
                archive.close()
            except Exception:
                pass
        self._archives.clear()
        if self._bridge is not None:
            # Command buffers retained for GPU timing are ours, not the
            # bridge's, so they are released here before the device goes.
            for buffer in self._pending_buffers:
                try:
                    self._bridge.objc.release(buffer)
                except Exception:
                    pass
            self._pending_buffers.clear()
            self._bridge.close()
            self._bridge = None
        self._destroy_window()
