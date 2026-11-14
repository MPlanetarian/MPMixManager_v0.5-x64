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
* **March resolution.** The raymarch is the expensive part. Like the Vulkan
  macOS path this renders at a capped long edge and scales the result onto the
  window with a second pass, so a 1094x734 window does not raymarch 803k pixels.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import hashlib
import math
import os
import platform
import re
import sys
import time
from ctypes import (POINTER, Structure, byref, c_char_p, c_double, c_int,
                    c_void_p)
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
SHADER_DIR = ROOT / "shaders"

try:  # imported as ``scripts.mp_liquid_visualizer_metal``
    from mp_metal_bridge import AutoreleasePool, MetalBridge, ObjC, CGSize, MetalError
    from mp_metal_shader import source_digest, translate_cached
    from visualizer_monitor_span import MonitorSpanMixin, ROLE_SPAWNED
    from visualizer_player_bond import PlayerBondMixin
    from visualizer_random_mode import RandomPresetMixin
except ImportError:  # imported from the project root's ``scripts`` directory
    from scripts.mp_metal_bridge import AutoreleasePool, MetalBridge, ObjC, CGSize, MetalError
    from scripts.mp_metal_shader import source_digest, translate_cached
    from scripts.visualizer_monitor_span import MonitorSpanMixin, ROLE_SPAWNED
    from scripts.visualizer_player_bond import PlayerBondMixin
    from scripts.visualizer_random_mode import RandomPresetMixin

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


# Frame-time targets for the adaptive march. Below MIN_SCALE the picture gets
# visibly soft, so the renderer stops there and accepts the lower frame rate
# rather than dropping resolution forever.
ADAPTIVE_MIN_SCALE = 0.55
ADAPTIVE_MAX_SCALE = 1.0
ADAPTIVE_TARGET_MS = 1000.0 / 60.0

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
BLIT_MSL = """#include <metal_stdlib>
using namespace metal;

struct MPBlitOut {
    float4 position [[position]];
    float2 uv;
};

constexpr sampler mp_blit_sampler(coord::normalized,
                                  address::clamp_to_edge,
                                  filter::linear);

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
    return source.sample(mp_blit_sampler, in.uv);
}
"""


class LiquidVisualizerMetalRenderer(MonitorSpanMixin, RandomPresetMixin, PlayerBondMixin):
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
        self.window_mode = "default"
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
        self._next_adapt_at = 0.0
        self._frame_times: List[float] = []
        # Command buffers retained until the GPU reports their timings.
        self._pending_buffers: List[int] = []
        self._init_span_state(span_bounds, span_role)

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
        for hint, value in (
            (GLFW_CLIENT_API, GLFW_NO_API),
            (GLFW_RESIZABLE, GLFW_FALSE),
            (GLFW_DECORATED, GLFW_FALSE),
            (GLFW_VISIBLE, GLFW_TRUE),
            (GLFW_FOCUSED, GLFW_TRUE),
            (GLFW_FOCUS_ON_SHOW, GLFW_TRUE),
            (GLFW_AUTO_ICONIFY, GLFW_FALSE),
        ):
            g.glfwWindowHint(hint, value)
        width, height = self._windowed_size
        window = g.glfwCreateWindow(int(width), int(height),
                                    self.title.encode("utf-8"), None, None)
        if not window:
            raise RuntimeError("GLFW could not open a borderless window for Metal")
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
        objc.send(layer, "setOpaque:", c_int(1), restype=None, argtypes=[c_int])
        self._layer = layer

        content = objc.send(nswindow, "contentView", restype=c_void_p, argtypes=[])
        objc.send(content, "setWantsLayer:", c_int(1), restype=None, argtypes=[c_int])
        objc.send(content, "setLayer:", layer, restype=None, argtypes=[c_void_p])
        objc.send(nswindow, "setReleasedWhenClosed:", c_int(0), restype=None, argtypes=[c_int])
        objc.send(nswindow, "setOpaque:", c_int(1), restype=None, argtypes=[c_int])
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
        return scale_to_long_edge(width, height, self._long_edge_for_frame())

    def _long_edge_for_frame(self) -> int:
        """The march long edge for this frame, after adaptive scaling."""
        base = march_long_edge()
        scale = self._resolution_scale
        if scale >= 0.999:
            return base
        return max(MIN_MARCH_SIDE, int(round(base * scale)))

    def _adapt_resolution(self, frame_ms: float) -> None:
        """Nudge the march scale toward the frame-time budget.

        The raymarch cost is very close to linear in pixel count, so the
        correction is scaled by how far off the target the last frame was.
        Changes are damped and rate-limited: without that the resolution
        visibly oscillates between two sizes while the scene animates.
        """
        if not self._adaptive:
            return
        now = time.monotonic()
        if now < self._next_adapt_at:
            return
        self._next_adapt_at = now + 0.30
        target = ADAPTIVE_TARGET_MS
        ratio = frame_ms / target
        if 0.85 <= ratio <= 1.20:
            return
        # Pixel count scales with the square of the long edge, so the scale
        # correction uses the square root of the time ratio.
        correction = math.sqrt(target / max(1.0, frame_ms))
        correction = max(0.85, min(1.15, correction))
        scale = self._resolution_scale * correction
        self._resolution_scale = max(ADAPTIVE_MIN_SCALE, min(ADAPTIVE_MAX_SCALE, scale))

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
    def poll(self) -> bool:
        if not self._alive or not self._window:
            return not self.should_close
        self._glfw.glfwPollEvents()
        if self._glfw.glfwWindowShouldClose(self._window):
            self.should_close = True
        self._sync_drawable_size()
        try:
            self.poll_monitor_span()
        except Exception:
            pass
        return not self.should_close

    def draw(self, bridge_state: Any, capture: bool = False):
        """Render one frame. Returns ``(w, h, bgra)`` when ``capture`` is set."""
        if not self.poll():
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
            self.cycle_window_mode()
        elif key in (GLFW_KEY_EQUAL, GLFW_KEY_KP_ADD, GLFW_KEY_RIGHT):
            self._cycle_preset(1)
        elif key in (GLFW_KEY_MINUS, GLFW_KEY_KP_SUBTRACT, GLFW_KEY_LEFT):
            self._cycle_preset(-1)
        elif GLFW_KEY_1 <= key <= GLFW_KEY_9:
            self._select_preset_slot(key - GLFW_KEY_1)
        elif key == GLFW_KEY_R:
            self.toggle_random_mode()

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

    def cycle_window_mode(self) -> str:
        """Z cycles fullscreen -> half size -> default size."""
        self._user_selected_window_mode = True
        if not self.is_fullscreen:
            if self.window_mode != "half":
                self.toggle_fullscreen()
                self.window_mode = "fullscreen"
                return self.window_mode
            self.window_mode = "default"
            self._apply_window_geometry(self._default_windowed_size[0],
                                        self._default_windowed_size[1],
                                        *self._windowed_offset)
            return self.window_mode
        self.toggle_fullscreen()
        self.window_mode = "half"
        self._apply_window_geometry(self._half_windowed_size[0], self._half_windowed_size[1],
                                    *self._half_windowed_offset)
        return self.window_mode

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
