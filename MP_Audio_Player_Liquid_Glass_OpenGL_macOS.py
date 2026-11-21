from __future__ import annotations
import ctypes
import ctypes.util
import faulthandler
import fcntl
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import time
from pathlib import Path
from typing import TYPE_CHECKING
from PIL import Image, ImageDraw, ImageFont

faulthandler.enable()

# ANSI palette used by the terminal emulator below. `_apply_sgr` handles the
# 16-colour forms, the 256-colour cube (38;5/48;5) and truecolor (38;2/48;2);
# these tables are what those branches resolve against.
ANSI_COLORS = {
    30: (0, 0, 0), 31: (205, 49, 49), 32: (13, 188, 121), 33: (229, 229, 16),
    34: (36, 114, 200), 35: (188, 63, 188), 36: (17, 168, 205), 37: (229, 229, 229),
    90: (102, 102, 102), 91: (241, 76, 76), 92: (35, 209, 139), 93: (245, 245, 67),
    94: (59, 142, 234), 95: (214, 112, 214), 96: (41, 184, 219), 97: (255, 255, 255),
}
ANSI_BG_COLORS = {
    40: (0, 0, 0), 41: (205, 49, 49), 42: (13, 188, 121), 43: (229, 229, 16),
    44: (36, 114, 200), 45: (188, 63, 188), 46: (17, 168, 205), 47: (229, 229, 229),
    100: (102, 102, 102), 101: (241, 76, 76), 102: (35, 209, 139), 103: (245, 245, 67),
    104: (59, 142, 234), 105: (214, 112, 214), 106: (41, 184, 219), 107: (255, 255, 255),
}


def ansi256_rgb(index: int) -> tuple:
    """Map an xterm-256 colour index to RGB.

    0-15 are the system colours, 16-231 a 6x6x6 cube and 232-255 a 24-step
    grey ramp. Anything out of range clamps to the nearest valid entry so a
    malformed SGR cannot raise inside the frame loop.
    """
    try:
        n = int(index)
    except (TypeError, ValueError):
        return (200, 235, 255)
    n = max(0, min(255, n))
    base = {
        0: (0, 0, 0), 1: (205, 49, 49), 2: (13, 188, 121), 3: (229, 229, 16),
        4: (36, 114, 200), 5: (188, 63, 188), 6: (17, 168, 205), 7: (229, 229, 229),
        8: (102, 102, 102), 9: (241, 76, 76), 10: (35, 209, 139), 11: (245, 245, 67),
        12: (59, 142, 234), 13: (214, 112, 214), 14: (41, 184, 219), 15: (255, 255, 255),
    }
    if n < 16:
        return base[n]
    if n < 232:
        n -= 16
        steps = (0, 95, 135, 175, 215, 255)
        return (steps[n // 36], steps[(n // 6) % 6], steps[n % 6])
    grey = 8 + (n - 232) * 10
    return (grey, grey, grey)


# Mono faces that actually carry the playlist marks (► ★ ◆). SFNSMono is the
# last resort: it renders those glyphs as blank boxes, which is why the
# playlist looked like it had no cursor on macOS.
OVERLAY_FONT_CANDIDATES = [
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Supplemental/Andale Mono.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]


def load_overlay_font(size: int):
    """The first candidate face that opens, falling back to PIL's default.

    The playlist cursor and favourite star are drawn as glyphs, so a font
    without them renders a blank box instead of a mark. Menlo is preferred
    because it carries both.
    """
    for path in OVERLAY_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size)
    except Exception:
        return ImageFont.load_default()

if not hasattr(ctypes, "_CData"):
    for base in getattr(ctypes.c_int, "__mro__", ()):
        if base.__name__ == "_CData":
            ctypes._CData = base
            break
if not hasattr(ctypes, "_CData"):
    for base in getattr(ctypes.c_void_p, "__mro__", ()):
        if base.__name__ == "_CData":
            ctypes._CData = base
            break

_orig_cfunctype = ctypes.CFUNCTYPE
_type_proxy_cache: dict = {}


def _is_valid_ctype(tp):
    if tp is None:
        return True
    if isinstance(tp, type):
        try:
            return issubclass(
                tp,
                (
                    ctypes._SimpleCData,
                    ctypes.Structure,
                    ctypes.Union,
                    ctypes._Pointer,
                    ctypes.Array,
                    ctypes._CFuncPtr,
                ),
            )
        except TypeError:
            return False
    return False


def _sanitize_ctype(tp):
    if _is_valid_ctype(tp):
        return tp
    if tp in _type_proxy_cache:
        return _type_proxy_cache[tp]

    orig_from_param = getattr(tp, "from_param", None)

    class _PyOpenGLTypeProxy(ctypes.c_void_p):
        @classmethod
        def from_param(cls, value):
            if value is None:
                return None
            if orig_from_param is not None:
                try:
                    res = orig_from_param(value)
                    if isinstance(res, ctypes._CData) or hasattr(
                        res, "_as_parameter_"
                    ):
                        return res
                except Exception:
                    pass
            if isinstance(value, (bytes, bytearray)):
                return ctypes.c_char_p(bytes(value))
            if isinstance(value, str):
                return ctypes.c_char_p(value.encode("utf-8"))
            if isinstance(value, int):
                return ctypes.c_void_p(value)
            if isinstance(value, ctypes._CData):
                return (
                    ctypes.byref(value)
                    if not isinstance(
                        value,
                        (
                            ctypes._Pointer,
                            ctypes.Array,
                            ctypes.c_void_p,
                            ctypes.c_char_p,
                        ),
                    )
                    else value
                )
            if hasattr(value, "ctypes"):
                return value.ctypes.data_as(ctypes.c_void_p)
            return ctypes.c_void_p.from_param(value)

    _PyOpenGLTypeProxy.__name__ = f"Proxy_{getattr(tp, '__name__', 'Param')}"
    _type_proxy_cache[tp] = _PyOpenGLTypeProxy
    return _PyOpenGLTypeProxy


def _py315_safe_cfunctype(restype, *argtypes, **kwargs):
    safe_restype = _sanitize_ctype(restype)
    safe_argtypes = tuple(_sanitize_ctype(a) for a in argtypes)
    try:
        return _orig_cfunctype(restype, *argtypes, **kwargs)
    except (TypeError, ValueError):
        return _orig_cfunctype(safe_restype, *safe_argtypes, **kwargs)


ctypes.CFUNCTYPE = _py315_safe_cfunctype

if sys.platform == "darwin":
    os.environ["PYOPENGL_PLATFORM"] = "darwin"
# PyOpenGL's default error check calls glGetError after every command.
# On Apple's OpenGL driver that waits for the GPU, so the visualizer
# cannot queue a frame ahead of the one on screen. Set this before the
# first OpenGL import. PYOPENGL_ERROR_CHECKING=1 keeps the checks.
os.environ.setdefault("PYOPENGL_ERROR_CHECKING", "0")

try:
    import OpenGL.platform.baseplatform as _bp


    def _safe_function_type_for(self, dll):
        def _builder(restype, *argtypes):
            safe_res = _sanitize_ctype(restype)
            safe_args = tuple(_sanitize_ctype(a) for a in argtypes)
            return _orig_cfunctype(safe_res, *safe_args)

        return _builder


    _bp.BasePlatform.functionTypeFor = _safe_function_type_for
except ImportError:
    pass

_orig_cdll = ctypes.CDLL
_c_func_refs: dict = {}
_FALLBACK_DYLIB = "/usr/lib/libSystem.B.dylib"
_OPENGL_FRAMEWORK = ctypes.util.find_library("OpenGL") or "/System/Library/Frameworks/OpenGL.framework/OpenGL"
_GLFW_DYLIB = None

for _cand in (
    "/opt/homebrew/lib/libglfw.3.dylib",
    "/usr/local/lib/libglfw.3.dylib",
    ctypes.util.find_library("glfw"),
    ctypes.util.find_library("glfw3"),
):
    if _cand and os.path.exists(_cand):
        _GLFW_DYLIB = _cand
        break

try:
    _RAW_MACOS_GL = _orig_cdll(_OPENGL_FRAMEWORK)
except OSError:
    _RAW_MACOS_GL = _orig_cdll(_FALLBACK_DYLIB)

_RAW_GLFW = _orig_cdll(_GLFW_DYLIB) if _GLFW_DYLIB else _RAW_MACOS_GL
_DUMMY_CFUNCTYPE = _orig_cfunctype(ctypes.c_void_p)


def _get_dummy_cfunc(name: str):
    sname = str(name)
    if sname not in _c_func_refs:
        def _noop(*args):
            return 0

        cfunc = _DUMMY_CFUNCTYPE(_noop)
        cfunc.__name__ = sname
        _c_func_refs[sname] = cfunc
    return _c_func_refs[sname]


class SafeMacOSCDLL(_orig_cdll):
    def __init__(self, name, *args, **kwargs):
        super().__init__(name, *args, **kwargs)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        if name.startswith("glfw") and _GLFW_DYLIB:
            try:
                func = _orig_cdll.__getitem__(self, name)
                super().__setattr__(name, func)
                return func
            except Exception:
                pass
        try:
            func = _orig_cdll.__getitem__(self, name)
            super().__setattr__(name, func)
            return func
        except (AttributeError, OSError, KeyError):
            if name.startswith("gl") and not name.startswith("glX") and not name.startswith("glfw"):
                try:
                    func = _orig_cdll.__getitem__(_RAW_MACOS_GL, name)
                    super().__setattr__(name, func)
                    return func
                except Exception:
                    pass
            fn = _get_dummy_cfunc(name)
            super().__setattr__(name, fn)
            return fn


def _mock_cdll(name, *args, **kwargs):
    if isinstance(name, _orig_cdll):
        return name
    sname = str(name) if name else ""
    if "glfw" in sname.lower() and _GLFW_DYLIB:
        return SafeMacOSCDLL(_GLFW_DYLIB, *args, **kwargs)
    return SafeMacOSCDLL(
        _OPENGL_FRAMEWORK if os.path.exists(_OPENGL_FRAMEWORK) else _FALLBACK_DYLIB,
        *args,
        **kwargs,
    )


if sys.platform == "darwin":
    ctypes.CDLL = _mock_cdll
    ctypes.cdll = ctypes.LibraryLoader(_mock_cdll)

ROOT = Path(__file__).resolve().parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from liquid_glass_opengl import (
    LiquidGlassRendererOpenGL,
    read_player_theme,
    get_dynamic_theme_state,
    glass_text_rgb,
    theme_text_rgb,
    theme_uses_dark_glass,
    raise_macos_process_detached,
    _rgb01,
    THEME_ALIASES,
)
try:  # Keep the hover fade timing identical to the Vulkan/OpenGL backends.
    from liquid_glass_slider import (
        SLIDER_FADE_IN_SECONDS,
        SLIDER_FADE_OUT_SECONDS,
        SLIDER_HOVER_PAD,
        SLIDER_LEFT,
        SLIDER_RIGHT_PAD,
    )
except ImportError:
    SLIDER_LEFT = 40
    SLIDER_RIGHT_PAD = 40
    SLIDER_HOVER_PAD = 16
    SLIDER_FADE_IN_SECONDS = 0.20
    SLIDER_FADE_OUT_SECONDS = 0.35
from liquid_player_visualizer import (
    macos_fullscreen_size,
    macos_liquid_player_window_size,
    macos_player_grid,
)
from liquid_visualizer_session import (
    stop_companion_for_mode,
    stop_companion_playback,
    stop_companion_visualizer,
    stop_player_audio,
)
try:  # The four-window cap ships beside the host; a lone copy still runs.
    from mp_instance_limit import host_allowed, notify_limit
except ImportError:
    def host_allowed(role):
        return True

    def notify_limit(role, message=None):
        print(message or f"{role} is already open 4 times.", file=sys.stderr)
try:  # The exit movie ships beside the host too.
    from exit_video import ExitVideo, is_last_window_open, play_player_intro
except ImportError:
    class ExitVideo:  # type: ignore[no-redef]
        """Stub used when the tree was copied without scripts/exit_video.py."""

        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            return False

        def wait(self, *_args, **_kwargs):
            return None

        def finish(self):
            pass

    def play_player_intro(*_args, **_kwargs):  # type: ignore[misc]
        """Stub: no intro movie when scripts/exit_video.py is missing."""
        return False

    def is_last_window_open(*_args, **_kwargs):  # type: ignore[misc]
        """Stub: assume the last window, so audio is never left running."""
        return True

if sys.platform == "darwin":
    os.environ["PYOPENGL_PLATFORM"] = "darwin"

COLS = 120
ROWS = 40
CHAR_W = 9
CHAR_H = 18
# The portrait deck M reaches first from the full window.
VERTICAL_COLS, VERTICAL_ROWS = 56, 52


def _mode_grid(mode):
    """Terminal grid for a window mode.

    The Linux/Windows OpenGL host defines this beside its panel geometry; this
    macOS host used the helper without carrying it, so the constructor raised
    ``NameError: _mode_grid`` and only the companion visualizer appeared.
    """
    if mode == "TRACKLIST":
        return 100, 35
    if mode == "VERTICAL":
        return VERTICAL_COLS, VERTICAL_ROWS
    if mode == "MINI":
        return 74, 14
    if mode == "MICRO":
        return 74, 7
    return COLS, ROWS


def player_panel_rects(cols=COLS, rows=ROWS, char_w=CHAR_W, char_h=CHAR_H, origin_x=0, origin_y=0, mode="NORMAL"):
    def box(col, row, box_cols, box_rows):
        return (
            float(origin_x + col * char_w),
            float(origin_y + row * char_h),
            float(box_cols * char_w),
            float(box_rows * char_h),
        )

    m = str(mode).upper()
    if m in ("MICRO", "MANAGER"):
        return box(2, 0, max(20, cols - 4), rows), (0.0, 0.0, 0.0, 0.0)
    elif m == "VERTICAL":
        # Vertical is the full player frame in a portrait column, so it uses
        # the same two panels as NORMAL: the header block and the playlist.
        iw = max(20, cols - 6)
        viz_rows = 7 if rows >= 34 else (5 if rows >= 28 else 3)
        before = 4 + 1 + 1 + 3 + (viz_rows + 3) + 2
        body_rows = max(1, rows - (before + 1) - 4)
        return (
            box(2, 0, iw + 2, 4),
            box(2, before, iw + 2, body_rows + 2),
        )
    elif m == "MINI":
        return box(2, 0, max(20, cols - 4), 3), box(2, 3, max(20, cols - 4), max(1, rows - 3))
    elif m == "TRACKLIST":
        iw = max(20, cols - 4)
        if rows <= 7:
            return box(2, 0, iw, rows), (0.0, 0.0, 0.0, 0.0)
        return box(2, 0, iw, 7), box(2, 7, iw, max(1, rows - 7))
    else:
        iw = max(20, cols - 6)
        before = 17
        body_rows = 21
        return box(2, 0, iw + 2, 4), box(2, before, iw + 2, body_rows)


def player_argv(mode="NORMAL", extra_args=None):
    cmd = [sys.executable, str(ROOT / "MP_Audio_Player.py"), "--interactive"]
    m = str(mode).upper()
    if m == "MINI":
        cmd.append("--mini")
    elif m == "MICRO":
        cmd.append("--micro")
    elif m == "VERTICAL":
        cmd.append("--vertical")
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def manager_argv():
    return ["/bin/bash", str(ROOT / "Mix_Archive_Manager.sh")]


def _child_env():
    """Environment for the program drawn inside the macOS glass window.

    The player must see MP_LIQUID_GLASS=1. With it off, G builds a spectrogram
    instead of opening MP Gui. The manager session keeps the same flag.
    """
    env = {key: value for key, value in os.environ.items()}
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env["MP_LIQUID_GLASS"] = "1"
    env["MP_LIQUID_GLASS_BACKEND"] = "opengl"
    env["PYTHONUNBUFFERED"] = "1"
    return env


class TerminalOverlay:
    """Fixed absolute coordinate terminal overlay with Retina scaling, text drop-shadow, and full background color support."""

    def __init__(self, cols=COLS, rows=ROWS, char_w=CHAR_W, char_h=CHAR_H):
        self.cols = cols
        self.rows = rows
        self.char_w = char_w
        self.char_h = char_h
        self.grid: list[list[str]] = [[" " for _ in range(cols)] for _ in range(rows)]
        self.colors: list[list[tuple]] = [[(200, 235, 255) for _ in range(cols)] for _ in range(rows)]
        self.bg_colors: list[list[tuple | None]] = [[None for _ in range(cols)] for _ in range(rows)]
        self.cursor_row = 0
        self.cursor_col = 0
        self.default_fg = (200, 235, 255)
        self.current_fg = self.default_fg
        self.current_bg = None
        self._use_dark_ink = None
        self._buf = bytearray()
        self.dirty = True
        self.texture_id = 0
        self.shader_program = None
        self.vao = None
        self.vbo = None
        self._initialized_gl = False
        self.font = None
        self.image = None
        self.img_w = self.cols * self.char_w
        self.img_h = self.rows * self.char_h
        self._last_scale_x = 1.0
        self._last_scale_y = 1.0
        self._glyph_cache: dict[tuple, any] = {}

        try:
            self.font = load_overlay_font(13)

            self.image = Image.new("RGBA", (self.img_w, self.img_h), (0, 0, 0, 0))
        except Exception:
            pass

    def init_gl(self):
        if self._initialized_gl:
            return
        try:
            from OpenGL.GL import (
                glCreateShader,
                glShaderSource,
                glCompileShader,
                glCreateProgram,
                glAttachShader,
                glLinkProgram,
                glGenVertexArrays,
                glBindVertexArray,
                glGenBuffers,
                glBindBuffer,
                glBufferData,
                glVertexAttribPointer,
                glEnableVertexAttribArray,
                GL_VERTEX_SHADER,
                GL_FRAGMENT_SHADER,
                GL_ARRAY_BUFFER,
                GL_DYNAMIC_DRAW,
                GL_FLOAT,
                GL_FALSE,
                glGenTextures,
                glBindTexture,
                glTexParameteri,
                glTexImage2D,
                GL_TEXTURE_2D,
                GL_TEXTURE_MIN_FILTER,
                GL_TEXTURE_MAG_FILTER,
                GL_NEAREST,
                GL_TEXTURE_WRAP_S,
                GL_TEXTURE_WRAP_T,
                GL_CLAMP_TO_EDGE,
                GL_RGBA,
                GL_UNSIGNED_BYTE,
            )

            vs_src = b"""
            #version 410 core
            layout(location = 0) in vec2 a_position;
            layout(location = 1) in vec2 a_uv;
            out vec2 v_uv;
            void main() {
                gl_Position = vec4(a_position, 0.0, 1.0);
                v_uv = a_uv;
            }
            """
            vs = glCreateShader(GL_VERTEX_SHADER)
            glShaderSource(vs, vs_src)
            glCompileShader(vs)

            fs_src = b"""
            #version 410 core
            in vec2 v_uv;
            uniform sampler2D u_textTex;
            out vec4 fragColor;
            void main() {
                vec4 texColor = texture(u_textTex, v_uv);
                if (texColor.a < 0.05) discard;
                fragColor = texColor;
            }
            """
            fs = glCreateShader(GL_FRAGMENT_SHADER)
            glShaderSource(fs, fs_src)
            glCompileShader(fs)

            self.shader_program = glCreateProgram()
            glAttachShader(self.shader_program, vs)
            glAttachShader(self.shader_program, fs)
            glLinkProgram(self.shader_program)

            self.vao = glGenVertexArrays(1)
            self.vbo = glGenBuffers(1)

            glBindVertexArray(self.vao)
            glBindBuffer(GL_ARRAY_BUFFER, self.vbo)
            glBufferData(GL_ARRAY_BUFFER, 6 * 4 * 4, None, GL_DYNAMIC_DRAW)

            glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 4 * 4, ctypes.c_void_p(0))
            glEnableVertexAttribArray(0)
            glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 4 * 4, ctypes.c_void_p(2 * 4))
            glEnableVertexAttribArray(1)

            glBindVertexArray(0)

            self.texture_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self.texture_id)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, self.img_w, self.img_h, 0, GL_RGBA, GL_UNSIGNED_BYTE, None)
            glBindTexture(GL_TEXTURE_2D, 0)

            self._initialized_gl = True
        except Exception as e:
            print(f"Overlay GL init error: {e}", flush=True)

    def _clamp_cursor(self):
        self.cursor_row = max(0, min(self.rows - 1, self.cursor_row))
        self.cursor_col = max(0, min(self.cols - 1, self.cursor_col))

    def feed(self, data: bytes):
        if not data:
            return
        self._buf.extend(data)
        try:
            text = self._buf.decode("utf-8", errors="replace")
        except Exception:
            return

        i = 0
        n = len(text)
        consumed = 0

        while i < n:
            ch = text[i]
            if ch == "\x1b":
                if i + 1 >= n:
                    break
                if text[i + 1] == "[":
                    end = i + 2
                    while end < n and not (64 <= ord(text[end]) <= 126):
                        end += 1
                    if end >= n:
                        break
                    cmd = text[end]
                    param_str = text[i + 2 : end]
                    params = []
                    for p in param_str.split(";"):
                        clean_p = re.sub(r"[^0-9]", "", p)
                        params.append(int(clean_p) if clean_p else 0)

                    if cmd == "m":
                        self._apply_sgr(params if params else [0])
                    elif cmd in ("H", "f"):
                        r = params[0] if len(params) > 0 and params[0] > 0 else 1
                        c = params[1] if len(params) > 1 and params[1] > 0 else 1
                        self.cursor_row = r - 1
                        self.cursor_col = c - 1
                        self._clamp_cursor()
                    elif cmd == "A":
                        self.cursor_row -= max(1, params[0] if params else 1)
                        self._clamp_cursor()
                    elif cmd == "B":
                        self.cursor_row += max(1, params[0] if params else 1)
                        self._clamp_cursor()
                    elif cmd == "C":
                        self.cursor_col += max(1, params[0] if params else 1)
                        self._clamp_cursor()
                    elif cmd == "D":
                        self.cursor_col -= max(1, params[0] if params else 1)
                        self._clamp_cursor()
                    elif cmd == "G":
                        self.cursor_col = (params[0] if params else 1) - 1
                        self._clamp_cursor()
                    elif cmd in ("L", "M", "S", "T", "r"):
                        pass
                    elif cmd == "J":
                        mode = params[0] if params else 0
                        if mode >= 2:
                            spans = [(r, 0, self.cols) for r in range(self.rows)]
                        elif mode == 1:
                            spans = [(r, 0, self.cols) for r in range(self.cursor_row)]
                            if 0 <= self.cursor_row < self.rows:
                                spans.append((self.cursor_row, 0, min(self.cols, self.cursor_col + 1)))
                        else:
                            spans = []
                            if 0 <= self.cursor_row < self.rows:
                                spans.append((self.cursor_row, max(0, self.cursor_col), self.cols))
                            spans.extend((r, 0, self.cols) for r in range(self.cursor_row + 1, self.rows))
                        for r, c0, c1 in spans:
                            if not (0 <= r < self.rows):
                                continue
                            for c in range(max(0, c0), min(self.cols, c1)):
                                self.grid[r][c] = " "
                                self.bg_colors[r][c] = None
                        self.dirty = True
                    elif cmd == "K":
                        if 0 <= self.cursor_row < self.rows:
                            for c in range(self.cursor_col, self.cols):
                                self.grid[self.cursor_row][c] = " "
                                self.bg_colors[self.cursor_row][c] = None
                            self.dirty = True
                    i = end + 1
                    consumed = i
                    continue
                else:
                    nxt = text[i + 1]
                    if nxt == "]":
                        j = i + 2
                        found = False
                        while j < n:
                            if text[j] == "\x07":
                                i = j + 1
                                consumed = i
                                found = True
                                break
                            if text[j] == "\x1b":
                                if j + 1 >= n:
                                    break
                                if text[j + 1] == "\\":
                                    i = j + 2
                                    consumed = i
                                    found = True
                                    break
                            j += 1
                        if not found:
                            break
                        continue
                    if i + 1 < n:
                        i += 2
                        consumed = i
                        continue
                    break
            elif ch == "\n":
                self.cursor_row += 1
                self.cursor_col = 0
                if self.cursor_row >= self.rows:
                    self.cursor_row = self.rows - 1
                self.dirty = True
            elif ch == "\r":
                self.cursor_col = 0
            elif ord(ch) >= 32:
                if 0 <= self.cursor_row < self.rows and 0 <= self.cursor_col < self.cols:
                    if (
                        self.grid[self.cursor_row][self.cursor_col] != ch
                        or self.colors[self.cursor_row][self.cursor_col] != self.current_fg
                        or self.bg_colors[self.cursor_row][self.cursor_col] != self.current_bg
                    ):
                        self.grid[self.cursor_row][self.cursor_col] = ch
                        self.colors[self.cursor_row][self.cursor_col] = self.current_fg
                        self.bg_colors[self.cursor_row][self.cursor_col] = self.current_bg
                        self.dirty = True
                    self.cursor_col += 1
                    if self.cursor_col >= self.cols:
                        self.cursor_col = self.cols - 1
                        self.dirty = True
            i += 1
            consumed = i

        if consumed > 0:
            try:
                remaining = text[consumed:].encode("utf-8", errors="replace")
                self._buf = bytearray(remaining)
            except Exception:
                self._buf.clear()

    def _apply_sgr(self, params):
        idx = 0
        while idx < len(params):
            p = params[idx]
            if p == 0:
                self.current_fg = self.default_fg
                self.current_bg = None
            elif 30 <= p <= 37:
                self.current_fg = ANSI_COLORS.get(p, (200, 235, 255))
            elif 90 <= p <= 97:
                self.current_fg = ANSI_COLORS.get(p, (100, 255, 255))
            elif 40 <= p <= 47 or 100 <= p <= 107:
                self.current_bg = ANSI_BG_COLORS.get(p, None)
            elif p == 38:
                if idx + 2 < len(params) and params[idx + 1] == 5:
                    self.current_fg = ansi256_rgb(params[idx + 2])
                    idx += 2
                elif idx + 4 < len(params) and params[idx + 1] == 2:
                    r = params[idx + 2]
                    g = params[idx + 3]
                    b = params[idx + 4]
                    self.current_fg = (r, g, b)
                    idx += 4
            elif p == 48:
                if idx + 2 < len(params) and params[idx + 1] == 5:
                    self.current_bg = ansi256_rgb(params[idx + 2])
                    idx += 2
                elif idx + 4 < len(params) and params[idx + 1] == 2:
                    r = params[idx + 2]
                    g = params[idx + 3]
                    b = params[idx + 4]
                    self.current_bg = (r, g, b)
                    idx += 4
            idx += 1

    def resize(self, cols: int, rows: int) -> None:
        cols = max(1, int(cols))
        rows = max(1, int(rows))
        if cols == self.cols and rows == self.rows:
            return
        self.cols = cols
        self.rows = rows
        self.grid = [[" " for _ in range(cols)] for _ in range(rows)]
        self.colors = [[self.default_fg for _ in range(cols)] for _ in range(rows)]
        self.bg_colors = [[None for _ in range(cols)] for _ in range(rows)]
        self.cursor_row = 0
        self.cursor_col = 0
        self.img_w = self.cols * self.char_w
        self.img_h = self.rows * self.char_h
        self.image = None
        self.dirty = True

    def set_theme_text(self, theme_name):
        """Reset colour for text that does not name its own colour."""
        rgb = theme_text_rgb(theme_name)
        if self.default_fg == rgb:
            return
        previous = self.default_fg
        self.default_fg = rgb
        if self.current_fg == previous:
            self.current_fg = rgb
        self.dirty = True

    def set_ink(self, theme_name, on_light_glass=True):
        """Darken body text only while it sits on the pale glass pane."""
        use_dark = bool(on_light_glass) and not theme_uses_dark_glass(theme_name)
        if self._use_dark_ink == use_dark:
            return
        self._use_dark_ink = use_dark
        self._glyph_cache.clear()
        self.dirty = True

    def ink_for_cell(self, color, background):
        if not self._use_dark_ink or background is not None:
            return color
        return glass_text_rgb(color[0], color[1], color[2])

    def render(self, fw, fh, ww, wh):
        if not self._initialized_gl:
            self.init_gl()
        if not self._initialized_gl or not self.shader_program:
            return

        scale_x = float(fw) / float(ww) if ww > 0 else 1.0
        scale_y = float(fh) / float(wh) if wh > 0 else 1.0

        target_img_w = int(self.cols * self.char_w * scale_x)
        target_img_h = int(self.rows * self.char_h * scale_y)

        if self.image is None or self.image.size != (target_img_w, target_img_h) or abs(scale_x - self._last_scale_x) > 0.01:
            self.img_w = target_img_w
            self.img_h = target_img_h
            self._last_scale_x = scale_x
            self._last_scale_y = scale_y
            self._glyph_cache.clear()
            scaled_font_size = max(8, int(13 * scale_y))
            try:
                self.font = load_overlay_font(scaled_font_size)
            except Exception:
                pass
            self.image = Image.new("RGBA", (self.img_w, self.img_h), (0, 0, 0, 0))
            self.dirty = True

            try:
                from OpenGL.GL import glBindTexture, glTexImage2D, GL_TEXTURE_2D, GL_RGBA, GL_UNSIGNED_BYTE
                glBindTexture(GL_TEXTURE_2D, self.texture_id)
                glTexImage2D(
                    GL_TEXTURE_2D,
                    0,
                    GL_RGBA,
                    self.img_w,
                    self.img_h,
                    0,
                    GL_RGBA,
                    GL_UNSIGNED_BYTE,
                    None,
                )
                glBindTexture(GL_TEXTURE_2D, 0)
            except Exception:
                pass

        try:
            from OpenGL.GL import (
                glUseProgram,
                glBindVertexArray,
                glDrawArrays,
                glBindBuffer,
                glBufferData,
                glDisable,
                glEnable,
                glBlendFunc,
                GL_DEPTH_TEST,
                GL_BLEND,
                GL_SRC_ALPHA,
                GL_ONE_MINUS_SRC_ALPHA,
                GL_TRIANGLES,
                GL_ARRAY_BUFFER,
                GL_DYNAMIC_DRAW,
                glBindTexture,
                GL_TEXTURE_2D,
                glTexImage2D,
                GL_RGBA,
                GL_UNSIGNED_BYTE,
            )
            import numpy as np

            if self.dirty and self.image:
                self.image.paste((0, 0, 0, 0), [0, 0, self.img_w, self.img_h])
                draw = ImageDraw.Draw(self.image)
                s_char_w = self.char_w * scale_x
                s_char_h = self.char_h * scale_y
                tw_base = int(s_char_w) + 2
                th_base = int(s_char_h) + 2

                for r in range(self.rows):
                    y = int(r * s_char_h)
                    for c in range(self.cols):
                        ch = self.grid[r][c]
                        bg = self.bg_colors[r][c]
                        col = self.ink_for_cell(self.colors[r][c], bg)

                        if bg is not None or ch != " ":
                            x = int(c * s_char_w)
                            if bg is not None:
                                draw.rectangle([x, y, x + int(s_char_w) + 1, y + int(s_char_h) + 1], fill=(bg[0], bg[1], bg[2], 255))
                            if ch != " ":
                                cache_key = (ch, col[0], col[1], col[2])
                                if cache_key not in self._glyph_cache:
                                    if len(self._glyph_cache) > 512:
                                        self._glyph_cache.clear()
                                    g_img = Image.new("RGBA", (tw_base, th_base), (0, 0, 0, 0))
                                    d = ImageDraw.Draw(g_img)
                                    d.text((1, 1), ch, fill=(0, 0, 0, 240), font=self.font)
                                    d.text((0, 0), ch, fill=(col[0], col[1], col[2], 255), font=self.font)
                                    self._glyph_cache[cache_key] = g_img

                                self.image.paste(self._glyph_cache[cache_key], (x, y))

                glBindTexture(GL_TEXTURE_2D, self.texture_id)
                glTexImage2D(
                    GL_TEXTURE_2D,
                    0,
                    GL_RGBA,
                    self.img_w,
                    self.img_h,
                    0,
                    GL_RGBA,
                    GL_UNSIGNED_BYTE,
                    self.image.tobytes("raw", "RGBA", 0, 1),
                )
                glBindTexture(GL_TEXTURE_2D, 0)
                self.dirty = False

            bezel = float(getattr(LiquidGlassRendererOpenGL, "bezel_px", 12.0))
            x_off = bezel * scale_x
            y_off = bezel * scale_y

            x1 = -1.0 + (2.0 * x_off / fw)
            x2 = x1 + (2.0 * self.img_w / fw)
            y2 = 1.0 - (2.0 * y_off / fh)
            y1 = y2 - (2.0 * self.img_h / fh)

            quad_data = np.array(
                [
                    x1, y1, 0.0, 1.0,
                    x2, y1, 1.0, 1.0,
                    x1, y2, 0.0, 0.0,
                    x2, y1, 1.0, 1.0,
                    x2, y2, 1.0, 0.0,
                    x1, y2, 0.0, 0.0,
                ],
                dtype=np.float32,
            )

            glDisable(GL_DEPTH_TEST)
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glUseProgram(self.shader_program)
            glBindVertexArray(self.vao)
            glBindBuffer(GL_ARRAY_BUFFER, self.vbo)
            glBufferData(GL_ARRAY_BUFFER, quad_data.nbytes, quad_data, GL_DYNAMIC_DRAW)
            glBindTexture(GL_TEXTURE_2D, self.texture_id)
            glDrawArrays(GL_TRIANGLES, 0, 6)
            glBindTexture(GL_TEXTURE_2D, 0)
            glBindVertexArray(0)
            glUseProgram(0)
        except Exception as e:
            print(f"Overlay draw error: {e}", flush=True)


# Opacity when the window opens. 0.80 is 80% opaque. The slider runs from 0.40 to 0.80.
# MP Liquid Player and MP Gui both use this host.
DEFAULT_WINDOW_OPACITY = 0.80


class CustomGlassRenderer(LiquidGlassRendererOpenGL):
    def __init__(self, overlay, host_ref, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.overlay = overlay
        self.host_ref = host_ref
        self._on_key_ref = None
        self._on_mouse_btn_ref = None
        self._on_cursor_pos_ref = None
        self._dragging = False
        self._drag_start_x = 0.0
        self._drag_start_y = 0.0
        self._last_top_click = 0.0
        self._last_top_click_x = 0.0
        self._last_top_click_y = 0.0
        
        self.clear_opacity = DEFAULT_WINDOW_OPACITY
        self.slider_val = DEFAULT_WINDOW_OPACITY
        self._slider_dragging = False
        # Hover-only bar: alpha 0 hides the track entirely. The GL loop draws
        # continuously, so the fade is advanced from time.monotonic().
        self._slider_alpha = 0.0
        self._slider_alpha_target = 0.0
        self._slider_fade_from = 0.0
        self._slider_fade_started = time.monotonic()

    def open(self):
        super().open()
        self._bind_callbacks()
        if hasattr(self, "_glfw") and hasattr(self._glfw, "glfwSetWindowOpacity") and self._window:
            try:
                self._glfw.glfwSetWindowOpacity.argtypes = [ctypes.c_void_p, ctypes.c_float]
                self._glfw.glfwSetWindowOpacity(self._window, ctypes.c_float(self.clear_opacity))
            except Exception:
                pass
        if hasattr(self._glfw, "glfwSwapInterval") and self._window:
            self._glfw.glfwSwapInterval(1)
        return self

    def _bind_callbacks(self):
        from ctypes import CFUNCTYPE, c_void_p, c_int, c_double, POINTER

        if hasattr(self._glfw, "glfwSetWindowOpacity"):
            self._glfw.glfwSetWindowOpacity.argtypes = [c_void_p, ctypes.c_float]

        @CFUNCTYPE(None, c_void_p, c_int, c_int, c_int)
        def mouse_button_callback(_window, button, action, mods):
            if button == 0:
                xpos = c_double()
                ypos = c_double()
                if hasattr(self._glfw, "glfwGetCursorPos"):
                    self._glfw.glfwGetCursorPos(_window, ctypes.byref(xpos), ctypes.byref(ypos))
                self.cursor = (float(xpos.value), float(ypos.value))
                self.mouse_down = action == 1

                ww, wh = self.window_size()
                if wh - ypos.value <= 35 and 40 <= xpos.value <= ww - 40:
                    if action == 1:
                        self._slider_dragging = True
                        self._update_slider(xpos.value, ww)
                    elif action == 0:
                        self._slider_dragging = False
                else:
                    if action == 1:
                        maximized = bool(getattr(self.host_ref, "is_maximized", False))
                        on_top = ypos.value < 48
                        now = time.monotonic()
                        if (
                            on_top
                            and now - self._last_top_click < 0.45
                            and abs(xpos.value - self._last_top_click_x) < 35
                            and abs(ypos.value - self._last_top_click_y) < 35
                        ):
                            self._dragging = False
                            self._last_top_click = 0.0
                            self.host_ref.toggle_maximize()
                        else:
                            self._last_top_click = now
                            self._last_top_click_x = xpos.value
                            self._last_top_click_y = ypos.value
                            if not maximized:
                                self._dragging = True
                                self._drag_start_x = xpos.value
                                self._drag_start_y = ypos.value
                    elif action == 0:
                        self._dragging = False
                        self._slider_dragging = False

        @CFUNCTYPE(None, c_void_p, c_double, c_double)
        def cursor_pos_callback(_window, xpos, ypos):
            self.cursor = (float(xpos), float(ypos))
            ww, wh = self.window_size()
            if self._slider_dragging:
                self._update_slider(xpos, ww)
            elif self._dragging:
                dx = xpos - self._drag_start_x
                dy = ypos - self._drag_start_y
                x_win = c_int()
                y_win = c_int()
                if hasattr(self._glfw, "glfwGetWindowPos") and hasattr(self._glfw, "glfwSetWindowPos"):
                    self._glfw.glfwGetWindowPos(_window, ctypes.byref(x_win), ctypes.byref(y_win))
                    self._glfw.glfwSetWindowPos(_window, int(x_win.value + dx), int(y_win.value + dy))

        @CFUNCTYPE(None, c_void_p, c_int, c_int, c_int, c_int)
        def key_callback(_window, key, scancode, action, mods):
            if action in (1, 2):
                if key in (81, 256):
                    os._exit(0)
                char_map = {
                    257: b"\n",
                    259: b"\x7f",
                    261: b"\x1b[3~",
                    262: b"\x1b[C",
                    263: b"\x1b[D",
                    264: b"\x1b[B",
                    265: b"\x1b[A",
                }
                if key in char_map:
                    self.host_ref.send_input(char_map[key])
                elif 32 <= key <= 126:
                    char_byte = chr(key).encode("utf-8")
                    if mods & 1 == 0:
                        char_byte = char_byte.lower()
                    self.host_ref.send_input(char_byte)

        self._on_mouse_btn_ref = mouse_button_callback
        self._on_cursor_pos_ref = cursor_pos_callback
        self._on_key_ref = key_callback

        if self._window:
            if hasattr(self._glfw, "glfwSetMouseButtonCallback"):
                self._glfw.glfwSetMouseButtonCallback.argtypes = [c_void_p, c_void_p]
                self._glfw.glfwSetMouseButtonCallback(self._window, ctypes.cast(self._on_mouse_btn_ref, c_void_p))
            if hasattr(self._glfw, "glfwSetCursorPosCallback"):
                self._glfw.glfwSetCursorPosCallback.argtypes = [c_void_p, c_void_p]
                self._glfw.glfwSetCursorPosCallback(self._window, ctypes.cast(self._on_cursor_pos_ref, c_void_p))
            if hasattr(self._glfw, "glfwSetKeyCallback"):
                self._glfw.glfwSetKeyCallback.argtypes = [c_void_p, c_void_p]
                self._glfw.glfwSetKeyCallback(self._window, ctypes.cast(self._on_key_ref, c_void_p))

            if hasattr(self._glfw, "glfwGetCursorPos"):
                self._glfw.glfwGetCursorPos.argtypes = [c_void_p, POINTER(c_double), POINTER(c_double)]
            if hasattr(self._glfw, "glfwGetWindowPos"):
                self._glfw.glfwGetWindowPos.argtypes = [c_void_p, POINTER(c_int), POINTER(c_int)]
            if hasattr(self._glfw, "glfwSetWindowPos"):
                self._glfw.glfwSetWindowPos.argtypes = [c_void_p, c_int, c_int]

    def _update_slider(self, xpos, ww):
        slider_left = 40.0
        slider_right = max(80.0, float(ww) - 40.0)
        t = max(0.0, min(1.0, (xpos - slider_left) / (slider_right - slider_left)))
        
        self.slider_val = 0.40 + t * (0.80 - 0.40)
        self.clear_opacity = self.slider_val

        if hasattr(self, "_glfw") and hasattr(self._glfw, "glfwSetWindowOpacity") and self._window:
            try:
                self._glfw.glfwSetWindowOpacity(self._window, ctypes.c_float(self.clear_opacity))
            except Exception:
                pass

        win = self.x11_window() if hasattr(self, "x11_window") else None
        if win:
            self._set_opacity(win, self.clear_opacity)

    def draw(self, theme_name, opacity=1.0, capture=False):
        if not self.poll():
            return None
        fw, fh = self.framebuffer_size()
        ww, wh = self.window_size()
        if fw < 2 or fh < 2 or ww < 2 or wh < 2:
            return None

        scale_x = float(fw) / float(ww) if ww > 0 else 1.0
        scale_y = float(fh) / float(wh) if wh > 0 else 1.0

        if self.window_size() != self._shape_size:
            self._apply_shape()

        from OpenGL import GL

        GL.glViewport(0, 0, fw, fh)
        GL.glClearColor(0.0, 0.0, 0.0, 0.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        showing_visualizer = self.draw_player_visualizer_background(fw, fh)
        self.sync_player_visualizer_opacity(bool(showing_visualizer))
        if showing_visualizer:
            self.overlay.set_ink(theme_name, on_light_glass=False)
            self.overlay.render(fw, fh, ww, wh)
            self._draw_transparency_slider(fw, fh, ww, wh)
            pixels = None
            if capture:
                raw = GL.glReadPixels(0, 0, fw, fh, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
                pixels = self._format_capture(fw, fh, raw)
            self._glfw.glfwSwapBuffers(self._window)
            return pixels
        GL.glDisable(GL.GL_BLEND)
        GL.glUseProgram(self._prog)

        colors, effective_opacity, factor = get_dynamic_theme_state(theme_name, opacity)
        self.apply_clear_opacity(factor)

        a = _rgb01(colors[0])
        b = _rgb01(colors[1])
        c = _rgb01(colors[2])
        aspect = float(fw) / max(1.0, float(fh))
        u = self._u_locs

        GL.glUniform4f(u["u_res"], float(fw), float(fh), float(time.monotonic() - getattr(self, "_start", 0)), aspect)
        GL.glUniform4f(u["u_themeA"], a[0], a[1], a[2], float(self.cursor[0]) / max(1.0, float(fw)))
        GL.glUniform4f(u["u_themeB"], b[0], b[1], b[2], float(self.cursor[1]) / max(1.0, float(fw)))
        GL.glUniform4f(u["u_themeC"], c[0], c[1], c[2], float(effective_opacity))
        GL.glUniform4f(u["u_bezel"], float(self.bezel_px) * scale_x, float(self.radius_px) * scale_y, 0.0, 1.0)

        p0 = self.panels[0]
        p1 = self.panels[1]
        GL.glUniform4f(u["u_panel0"], float(p0[0]) * scale_x, float(p0[1]) * scale_y, float(p0[2]) * scale_x, float(p0[3]) * scale_y)
        GL.glUniform4f(u["u_panel1"], float(p1[0]) * scale_x, float(p1[1]) * scale_y, float(p1[2]) * scale_x, float(p1[3]) * scale_y)

        skin_key = THEME_ALIASES.get(str(theme_name or "").strip().lower(), str(theme_name or "").strip().lower())
        tex_id = self._load_skin_texture(skin_key)
        if tex_id:
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex_id)
            if u.get("u_skinTex", -1) != -1:
                GL.glUniform1i(u["u_skinTex"], 0)
            if u.get("u_hasSkin", -1) != -1:
                GL.glUniform1f(u["u_hasSkin"], 1.0)
        else:
            if u.get("u_hasSkin", -1) != -1:
                GL.glUniform1f(u["u_hasSkin"], 0.0)

        GL.glBindVertexArray(self._vao)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 6)

        self.overlay.set_ink(theme_name, on_light_glass=True)
        self.overlay.render(fw, fh, ww, wh)
        self._draw_transparency_slider(fw, fh, ww, wh)

        pixels = None
        if capture:
            raw = GL.glReadPixels(0, 0, fw, fh, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
            pixels = self._format_capture(fw, fh, raw)

        self._glfw.glfwSwapBuffers(self._window)
        return pixels

    def _slider_hovered(self, ww, wh) -> bool:
        """True while the pointer is over the bar's bottom strip."""
        if not getattr(self, "cursor_inside", True):
            return False
        try:
            x, y = self.cursor
        except (AttributeError, TypeError, ValueError):
            return False
        left = float(SLIDER_LEFT) - float(SLIDER_HOVER_PAD)
        right = float(ww) - float(SLIDER_RIGHT_PAD) + float(SLIDER_HOVER_PAD)
        return left <= float(x) <= right and float(y) >= float(wh) - 35.0

    def _update_slider_fade(self, ww, wh) -> None:
        now = time.monotonic()
        target = 1.0 if (self._slider_dragging or self._slider_hovered(ww, wh)) else 0.0
        if target != self._slider_alpha_target:
            self._slider_alpha_target = target
            self._slider_fade_from = self._slider_alpha
            self._slider_fade_started = now
        if abs(self._slider_alpha - target) < 1e-4:
            self._slider_alpha = target
            return
        duration = (SLIDER_FADE_IN_SECONDS
                    if target >= self._slider_fade_from
                    else SLIDER_FADE_OUT_SECONDS)
        progress = (now - self._slider_fade_started) / max(0.001, duration)
        progress = 0.0 if progress < 0.0 else (1.0 if progress > 1.0 else progress)
        eased = progress * progress * (3.0 - 2.0 * progress)
        self._slider_alpha = (self._slider_fade_from
                              + (target - self._slider_fade_from) * eased)
        if progress >= 1.0:
            self._slider_alpha = target

    def _draw_transparency_slider(self, fw, fh, ww, wh):
        self._update_slider_fade(ww, wh)
        if self._slider_alpha <= 0.001:
            return
        alpha = self._slider_alpha
        try:
            from OpenGL.GL import (
                glUseProgram, glGenVertexArrays, glBindVertexArray, glGenBuffers,
                glBindBuffer, glBufferData, glVertexAttribPointer, glEnableVertexAttribArray,
                glDrawArrays, glDisable, glEnable, glBlendFunc, GL_BLEND, GL_SRC_ALPHA,
                GL_ONE_MINUS_SRC_ALPHA, GL_TRIANGLES, GL_ARRAY_BUFFER, GL_DYNAMIC_DRAW,
                GL_FLOAT, GL_FALSE, glUniform4f, glCreateShader, glShaderSource,
                glCompileShader, glCreateProgram, glAttachShader, glLinkProgram,
                GL_VERTEX_SHADER, GL_FRAGMENT_SHADER
            )
            import numpy as np

            if not hasattr(self, "_slider_prog"):
                vs_src = b"""
                #version 410 core
                layout(location = 0) in vec2 a_pos;
                void main() {
                    gl_Position = vec4(a_pos, 0.0, 1.0);
                }
                """
                fs_src = b"""
                #version 410 core
                uniform vec4 u_color;
                out vec4 fragColor;
                void main() {
                    fragColor = u_color;
                }
                """
                vs = glCreateShader(GL_VERTEX_SHADER)
                glShaderSource(vs, vs_src)
                glCompileShader(vs)
                fs = glCreateShader(GL_FRAGMENT_SHADER)
                glShaderSource(fs, fs_src)
                glCompileShader(fs)
                self._slider_prog = glCreateProgram()
                glAttachShader(self._slider_prog, vs)
                glAttachShader(self._slider_prog, fs)
                glLinkProgram(self._slider_prog)
                self._slider_vao = glGenVertexArrays(1)
                self._slider_vbo = glGenBuffers(1)

            x_left = 40.0
            x_right = float(ww) - 40.0
            y_track = 18.0
            
            ndc_x1 = (x_left / ww) * 2.0 - 1.0
            ndc_x2 = (x_right / ww) * 2.0 - 1.0
            ndc_y1 = ((wh - y_track - 4.0) / wh) * -2.0 + 1.0
            ndc_y2 = ((wh - y_track + 4.0) / wh) * -2.0 + 1.0

            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glUseProgram(self._slider_prog)
            glBindVertexArray(self._slider_vao)
            glBindBuffer(GL_ARRAY_BUFFER, self._slider_vbo)

            track_quad = np.array([
                ndc_x1, ndc_y1,  ndc_x2, ndc_y1,  ndc_x1, ndc_y2,
                ndc_x2, ndc_y1,  ndc_x2, ndc_y2,  ndc_x1, ndc_y2
            ], dtype=np.float32)

            from OpenGL.GL import glGetUniformLocation
            u_col_loc = glGetUniformLocation(self._slider_prog, "u_color")
            
            glUniform4f(u_col_loc, 0.2, 0.4, 0.6, 0.6 * alpha)
            glBufferData(GL_ARRAY_BUFFER, track_quad.nbytes, track_quad, GL_DYNAMIC_DRAW)
            glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 2 * 4, ctypes.c_void_p(0))
            glEnableVertexAttribArray(0)
            glDrawArrays(GL_TRIANGLES, 0, 6)

            t = (self.slider_val - 0.40) / (0.80 - 0.40)
            thumb_x = x_left + t * (x_right - x_left)
            
            t_x1 = ((thumb_x - 6.0) / ww) * 2.0 - 1.0
            t_x2 = ((thumb_x + 6.0) / ww) * 2.0 - 1.0
            t_y1 = ((wh - y_track - 10.0) / wh) * -2.0 + 1.0
            t_y2 = ((wh - y_track + 10.0) / wh) * -2.0 + 1.0

            thumb_quad = np.array([
                t_x1, t_y1,  t_x2, t_y1,  t_x1, t_y2,
                t_x2, t_y1,  t_x2, t_y2,  t_x1, t_y2
            ], dtype=np.float32)

            glUniform4f(u_col_loc, 0.6, 0.8, 1.0, 0.9 * alpha)
            glBufferData(GL_ARRAY_BUFFER, thumb_quad.nbytes, thumb_quad, GL_DYNAMIC_DRAW)
            glDrawArrays(GL_TRIANGLES, 0, 6)

            glBindVertexArray(0)
            glUseProgram(0)
        except Exception as e:
            print(f"Slider draw error: {e}", flush=True)


class OpenGLGlassHost:
    def __init__(self, mode="NORMAL", cli_theme=None, manager=False):
        self.mode = mode.upper()
        self.cli_theme = cli_theme
        self.manager = manager
        self.renderer = None
        self.child_pid = 0
        self.master_fd = None
        self.is_maximized = False
        self._saved_rect = None
        margin = LiquidGlassRendererOpenGL.bezel_px
        self._target_cols, self._target_rows = _mode_grid(self.mode)
        self._window_w, self._window_h = macos_liquid_player_window_size(
            self._target_cols, self._target_rows, CHAR_W, CHAR_H, margin
        )
        self.overlay = TerminalOverlay(self._target_cols, self._target_rows)

    def send_input(self, data: bytes):
        if self.master_fd is not None:
            try:
                os.write(self.master_fd, data)
            except OSError:
                pass

    def _apply_terminal_size(self, cols: int, rows: int) -> None:
        self._target_cols = int(cols)
        self._target_rows = int(rows)
        self.overlay.resize(self._target_cols, self._target_rows)
        self._sync_panels()
        if self.master_fd is None:
            return
        winsize = struct.pack("HHHH", self._target_rows, self._target_cols, 0, 0)
        try:
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            return
        if self.child_pid:
            try:
                os.kill(self.child_pid, signal.SIGWINCH)
            except OSError:
                pass

    def toggle_maximize(self):
        """Fill the work area, never larger than 1920x1080. A second call restores the player window."""
        if self.renderer is None:
            return
        if not self.is_maximized:
            self._saved_rect = (self.renderer.window_pos(), self.renderer.window_size())
            wx, wy, ww, wh = self.renderer.get_workarea()
            fw, fh = macos_fullscreen_size(ww, wh)
            x = int(wx) + max(0, (int(ww) - fw) // 2)
            y = int(wy) + max(0, (int(wh) - fh) // 2)
            self.renderer.set_window_pos(x, y)
            self.renderer.set_window_size(fw, fh)
            margin = self.renderer.bezel_px
            cols, rows = macos_player_grid(fw, fh, CHAR_W, CHAR_H, margin)
            self._apply_terminal_size(cols, rows)
            self.is_maximized = True
            return
        pos, size = self._saved_rect or ((72, 64), (self._window_w, self._window_h))
        self.renderer.set_window_pos(pos[0], pos[1])
        self.renderer.set_window_size(size[0], size[1])
        margin = self.renderer.bezel_px
        cols, rows = macos_player_grid(size[0], size[1], CHAR_W, CHAR_H, margin)
        self._apply_terminal_size(cols, rows)
        self.is_maximized = False

    def _sync_panels(self):
        if self.renderer is None:
            return
        char_w = CHAR_W
        char_h = CHAR_H
        margin = self.renderer.bezel_px
        self.renderer.set_panels(
            player_panel_rects(
                self._target_cols, self._target_rows, char_w, char_h, margin, margin, mode=self.mode
            )
        )

    def _follow_child_ui_size(self):
        """Grow or shrink with the size the player published.

        M is decided by the player, because during search that letter is text.
        Without this the window kept the full grid while the child drew the
        deck, so the tall view was cropped to the middle of a wide frame.
        """
        if self.mode in ("MANAGER", "TRACKLIST") or not self.child_pid:
            return
        try:
            from liquid_ui_size import read_ui_size
        except ImportError:
            return
        config_dir = Path.home() / ".config" / "mix-manager"
        size = read_ui_size(config_dir, int(self.child_pid))
        if not size or size == self.mode:
            return
        self.mode = size
        cols, rows = _mode_grid(size)
        margin = self.renderer.bezel_px if self.renderer else 7
        self._window_w = cols * CHAR_W + 2 * margin
        self._window_h = rows * CHAR_H + 2 * margin
        self._apply_terminal_size(cols, rows)
        if self.renderer:
            self.renderer.set_window_size(self._window_w, self._window_h)
            self._sync_panels()

    def _host_window_number(self) -> int:
        """CGWindowID of this host window (the NSWindow number), or 0."""
        try:
            r = self.renderer
            glfw = getattr(r, "_glfw", None)
            window = getattr(r, "_window", None)
            if not glfw or not window:
                return 0
            glfw.glfwGetCocoaWindow.argtypes = [ctypes.c_void_p]
            glfw.glfwGetCocoaWindow.restype = ctypes.c_void_p
            # The renderer stores the GLFW window as a c_void_p already;
            # wrapping it again raises "cannot be converted to pointer".
            handle = window if isinstance(window, ctypes.c_void_p) else ctypes.c_void_p(window)
            nswindow = glfw.glfwGetCocoaWindow(handle)
            if not nswindow:
                return 0
            objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
            objc.sel_registerName.restype = ctypes.c_void_p
            objc.sel_registerName.argtypes = [ctypes.c_char_p]
            objc.objc_msgSend.restype = ctypes.c_long
            objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            sel = ctypes.c_void_p(objc.sel_registerName(b"windowNumber"))
            ns_handle = (nswindow if isinstance(nswindow, ctypes.c_void_p)
                         else ctypes.c_void_p(nswindow))
            return int(objc.objc_msgSend(ns_handle, sel))
        except Exception:
            return 0

    def _order_visualizer_behind(self, level: int = 0) -> None:
        """Raise the glass host above normal windows.

        The companion sits at the same level and is ordered behind it. macOS
        ignores cross-process ``CGSOrderWindow`` in practice, so a shared level
        above Finder/Terminal is what keeps the visualizer directly behind the
        glass instead of behind unrelated apps.
        """
        try:
            from liquid_player_visualizer import macos_window_for_pid
            from liquid_visualizer_session import visualizer_pids
            own = macos_window_for_pid(os.getpid())
            if not own:
                return
            cg = ctypes.CDLL(
                "/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight")
            cg.CGSMainConnectionID.restype = ctypes.c_int
            cg.CGSOrderWindow.argtypes = [
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
            cg.CGSOrderWindow.restype = ctypes.c_int
            connection = int(cg.CGSMainConnectionID())
            if not connection:
                return
            # The host owns the relative window, so the window server accepts
            # ordering the companion immediately beneath it. Doing this from
            # the companion's side was silently ignored on this macOS.
            for visualizer_pid in visualizer_pids():
                window = macos_window_for_pid(visualizer_pid)
                if window:
                    cg.CGSOrderWindow(connection, int(window[0]), -1,
                                      int(own[0]))
                    break
        except Exception:
            pass

    def _publish_window_geometry(self) -> None:
        """Record the host rectangle so the macOS companion can match it."""
        if self.mode == "TRACKLIST" or self.renderer is None:
            return
        try:
            x, y = self.renderer.window_pos()
            width, height = self.renderer.window_size()
            wid = self._host_window_number()
            if self.mode == "MANAGER":
                from liquid_player_visualizer import publish_liquid_gui_window_geometry
                publish_liquid_gui_window_geometry(x, y, width, height, wid=wid)
            else:
                from liquid_player_visualizer import publish_liquid_player_window_geometry
                publish_liquid_player_window_geometry(x, y, width, height, wid=wid)
            self._order_visualizer_behind()
        except Exception:
            pass

    def run(self):
        self.master_fd, slave_fd = pty.openpty()
        winsize = struct.pack("HHHH", self._target_rows, self._target_cols, 0, 0)
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)

        if self.manager:
            os.environ["MP_GUI_SESSION"] = "1"
            os.environ["MP_LIQUID_GLASS"] = "1"
            command = manager_argv()
        else:
            extra = []
            if self.cli_theme:
                extra.extend(["--theme", self.cli_theme])
            command = player_argv(mode=self.mode, extra_args=extra)

        pid = os.fork()
        if pid == 0:
            os.close(self.master_fd)
            os.setsid()
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            try:
                err_log = open(str(ROOT / "player_stderr.log"), "w")
                os.dup2(err_log.fileno(), 2)
            except Exception:
                os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            env = _child_env()
            os.execvpe(command[0], command, env)
            sys.exit(1)

        os.close(slave_fd)
        self.child_pid = pid

        init_w, init_h = self._window_w, self._window_h
        if self.mode == "MANAGER" or self.manager:
            title = "MP Mix Manager (Liquid Glass - OpenGL / macOS)"
        else:
            title = "MP Audio Player (Liquid Glass - OpenGL / macOS)"

        self.renderer = CustomGlassRenderer(
            overlay=self.overlay, host_ref=self, width=init_w, height=init_h, title=title
        )
        self.renderer.allow_player_visualizer = self.mode not in ("MANAGER", "TRACKLIST") and not getattr(self, "manager", False)
        self.renderer.open()
        self._sync_panels()

        theme = read_player_theme()
        self.overlay.set_theme_text(theme)
        theme_at = 0.0
        geom_at = 0.0
        # open() raises once. The visualizer raises itself when its window
        # appears, which can be later, so raise again for a few seconds.
        raise_left = 6
        next_raise = time.monotonic() + 0.35
        print("DEBUG: Entering OpenGL event loop...", flush=True)

        try:
            while not self.renderer.should_close:
                now = time.monotonic()
                if raise_left and now >= next_raise:
                    raise_macos_process_detached()
                    raise_left -= 1
                    next_raise = now + 0.5
                if now - theme_at > 0.4:
                    theme = read_player_theme()
                    self.overlay.set_theme_text(theme)
                    theme_at = now
                if now - geom_at > 0.10:
                    self._publish_window_geometry()
                    geom_at = now

                if self.master_fd is not None:
                    r, _, _ = select.select([self.master_fd], [], [], 0.0)
                    if self.master_fd in r:
                        try:
                            data = os.read(self.master_fd, 16384)
                            if data:
                                self.overlay.feed(data)
                        except OSError:
                            pass

                self.renderer.poll()
                self._follow_child_ui_size()
                self.renderer.draw(theme)

                pid_res, _ = os.waitpid(self.child_pid, os.WNOHANG)
                if pid_res == self.child_pid:
                    break
        finally:
            # Destroy this window first, so closing the player disappears at
            # once instead of sitting on screen for the whole movie.
            if self.master_fd is not None:
                try:
                    os.close(self.master_fd)
                except OSError:
                    pass
            if self.child_pid:
                try:
                    os.kill(self.child_pid, signal.SIGTERM)
                    time.sleep(0.05)
                    os.kill(self.child_pid, signal.SIGKILL)
                except OSError:
                    pass
            if self.renderer:
                self.renderer.close()
                self.renderer = None
            # Retire the companion now, not after the movie: leaving it up put
            # a full-size visualizer on the desktop with nothing in front of it.
            stop_companion_for_mode(self.mode, player_gui=True)
            self._play_exit_video_after_close()

        return True

    def _play_exit_video_after_close(self):
        """Play the exit movie on the last MP window, after its own window is gone.

        The glass is already destroyed when this runs, so the movie is the only
        thing left on screen. Only the last MP Gui or MP Liquid Player window
        shows it, and only once.
        """
        if self.mode in ("NORMAL", "MANAGER"):
            # Ask before the movie is elected: electing marks this window as
            # closing, so afterwards every host would look like the last one.
            last = is_last_window_open(self.mode)
            video = ExitVideo(self.mode)
            try:
                if video.start():
                    video.wait()
            except Exception:
                pass
            finally:
                video.finish()
            # The closing movie is silent, so once the last window is gone the
            # mix has nothing left to play to. Stop it rather than leaving the
            # audio running to an empty desktop. A second player window still
            # open keeps playing, which is why this is gated on `last`.
            if last:
                stop_player_audio()


def main():
    theme_arg = None
    manager = False
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--manager":
            manager = True
            i += 1
            continue
        if arg in ("--theme", "-t") and i + 1 < len(sys.argv):
            theme_arg = sys.argv[i + 1]
            i += 2
            continue
        i += 1

    if manager:
        os.environ["MP_GUI_SESSION"] = "1"
        os.environ["MP_LIQUID_GLASS"] = "1"
        mode = "MANAGER"
    else:
        mode = "NORMAL"
    # MP Gui and MP Liquid Player stop at four windows. Helper flags are not
    # counted; the --manager window is MP Gui.
    if not host_allowed("gui" if manager else "player"):
        # A shortcut launch discards stderr, so the refusal used to look like
        # nothing happened at all. Say it on the desktop as well.
        notify_limit("gui" if manager else "player")
        return 1
    host = OpenGLGlassHost(mode=mode, cli_theme=theme_arg, manager=manager)
    try:
        # The first MP Gui and the first MP Liquid Player each play
        # Exit_MP_Video.mp4 once, before the glass appears. The movie window
        # closes when the clip ends. A second window of that same kind never
        # shows it.
        play_player_intro(mode)
        host.run()
    finally:
        # The companion is already retired when host.run() returns normally;
        # this catches a raise before that point. An embed check must never
        # touch a companion the user is watching.
        if not embed:
            stop_companion_for_mode(mode, player_gui=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        sys.exit(1)
