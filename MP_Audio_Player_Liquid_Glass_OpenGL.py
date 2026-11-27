#!/usr/bin/env python3
"""OpenGL liquid-glass window hosting the MP Audio Player with theme-matching transparency slider and drop-shadow text."""

from __future__ import annotations
import ctypes, os, shutil, signal, subprocess, sys, time, traceback
from pathlib import Path

os.environ.setdefault("PYOPENGL_PLATFORM", "wgl" if sys.platform == "win32" else "glx")

ROOT = Path(__file__).resolve().parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

try:  # Raspberry Pi / low-power ARM tuning; stdlib-only and cheap to import.
    import mp_raspberry_pi  # noqa: E402
except ImportError:
    from scripts import mp_raspberry_pi  # type: ignore  # noqa: E402


def _slider_glsl_version() -> str:
    """GLSL version the slider shaders may use on this context.

    The host never asks for more than a 3.3 core context, so anything above
    GLSL 3.30 is guaranteed to fail. On a Raspberry Pi the cap is 1.40.
    """
    if mp_raspberry_pi.is_low_power_gpu():
        return "140"
    return "330"


def _slider_vertex_source(glsl: str) -> bytes:
    """Full-screen triangle/quad vertex shader at ``glsl``."""
    if glsl in ("330", "410"):
        return (
            f"#version {glsl}\n"
            "layout(location = 0) in vec2 a_pos;\n"
            "void main() { gl_Position = vec4(a_pos, 0.0, 1.0); }\n"
        ).encode("utf-8")
    # GLSL 1.40 has no `layout(location = ...)`; the caller binds index 0.
    return (
        f"#version {glsl}\n"
        "attribute vec2 a_pos;\n"
        "void main() { gl_Position = vec4(a_pos, 0.0, 1.0); }\n"
    ).encode("utf-8")


def _slider_fragment_source(glsl: str) -> bytes:
    """Flat-colour fragment shader at ``glsl``."""
    if glsl in ("330", "410"):
        return (
            f"#version {glsl}\n"
            "uniform vec4 u_color;\n"
            "out vec4 fragColor;\n"
            "void main() { fragColor = u_color; }\n"
        ).encode("utf-8")
    return (
        f"#version {glsl}\n"
        "uniform vec4 u_color;\n"
        "void main() { gl_FragColor = u_color; }\n"
    ).encode("utf-8")

from desktop_launch_screen import window_launch_origin  # noqa: E402
from liquid_glass_font import default_liquid_glass_font  # noqa: E402
from liquid_visualizer_session import (  # noqa: E402
    stop_companion_for_mode,
    stop_companion_playback,
    stop_companion_visualizer,
    stop_player_audio,
)
try:  # The four-window cap ships beside the host; a lone copy still runs.
    from mp_instance_limit import host_allowed, notify_limit  # noqa: E402
except ImportError:
    def host_allowed(role):  # type: ignore[misc]
        return True

    def notify_limit(role, message=None):  # type: ignore[misc]
        print(message or f"{role} is already open 4 times.", file=sys.stderr)
try:  # The exit movie ships beside the host too.
    from exit_video import ExitVideo, is_last_window_open, play_player_intro  # noqa: E402
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
from liquid_glass_opengl import (
    LiquidGlassRendererOpenGL,
    read_player_theme,
    get_dynamic_theme_state,
    glass_foreground_rgb,
    _rgb01,
    THEME_ALIASES,
)
try:  # The player keeps a slightly shorter bottom strip so its hints sit lower.
    from liquid_glass_slider import (  # noqa: E402
        MAX_OPACITY,
        MIN_OPACITY,
        SLIDER_FADE_IN_SECONDS,
        SLIDER_FADE_OUT_SECONDS,
        SLIDER_HOVER_PAD,
        SLIDER_LEFT,
        SLIDER_RIGHT_PAD,
        SLIDER_STRIP,
        opacity_from_x,
        player_slider_strip,
        slider_thumb_x,
    )
except ImportError:
    SLIDER_STRIP = 44
    SLIDER_LEFT = 40
    SLIDER_RIGHT_PAD = 40
    SLIDER_HOVER_PAD = 16
    SLIDER_FADE_IN_SECONDS = 0.20
    SLIDER_FADE_OUT_SECONDS = 0.35
    MIN_OPACITY = 0.10
    MAX_OPACITY = 1.00

    def player_slider_strip(_cell_height=0.0):  # type: ignore[misc]
        return SLIDER_STRIP

    def opacity_from_x(x, width):  # type: ignore[misc]
        left = float(SLIDER_LEFT)
        right = max(left + 40.0, float(width) - float(SLIDER_RIGHT_PAD))
        t = (float(x) - left) / (right - left)
        t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
        return MIN_OPACITY + t * (MAX_OPACITY - MIN_OPACITY)

    def slider_thumb_x(opacity, width):  # type: ignore[misc]
        left = float(SLIDER_LEFT)
        right = max(left + 40.0, float(width) - float(SLIDER_RIGHT_PAD))
        span = max(1e-6, MAX_OPACITY - MIN_OPACITY)
        t = (float(opacity) - MIN_OPACITY) / span
        t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
        return left + t * (right - left)

def _kill_player_processes() -> List[int]:
    """SIGKILL every MP Audio Player owned by this user, and return the PIDs.

    The `K` shortcut used to run `pkill -9 -f "MP_Audio_Player"`, which is a
    substring match over every process command line: it killed a text editor
    that had the file open, another account's player, and any shell whose
    arguments mentioned the name. This walks /proc (or falls back to `ps`) and
    requires the executable basename to be a player script.
    """
    import signal

    me = os.getpid()
    targets: List[int] = []

    def _matches(argv: List[str]) -> bool:
        if not argv:
            return False
        # A real player invocation ends in MP_Audio_Player.py (or a player
        # derived name). A wrapper or editor mentions the path but its argv[0]
        # is bash/vim/etc.
        first = os.path.basename(argv[0])
        if first not in ("python", "python3", "python3.10", "python3.11",
                         "python3.12", "python3.13", "python3.14", "pypy3"):
            return False
        return any(os.path.basename(a).startswith("MP_Audio_Player") for a in argv[1:])

    proc = Path("/proc")
    if proc.is_dir():
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            if pid == me:
                continue
            try:
                raw = (entry / "cmdline").read_bytes()
            except OSError:
                continue
            argv = [a.decode("utf-8", "ignore") for a in raw.split(b"\x00") if a]
            if _matches(argv):
                targets.append(pid)
    else:
        # macOS and FreeBSD have no /proc.
        try:
            out = subprocess.run(
                ["ps", "-u", str(os.getuid()), "-o", "pid=,command="],
                capture_output=True, text=True, timeout=5,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            pid_text, _, command = line.partition(" ")
            if not pid_text.isdigit():
                continue
            if _matches(command.split()):
                targets.append(int(pid_text))

    killed: List[int] = []
    for pid in targets:
        if pid == me:
            continue
        try:
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            killed.append(pid)
        except OSError:
            continue
    return killed





    LiquidGlassRendererOpenGL,


COLS, ROWS = 120, 40
# The portrait deck. It is the size M reaches first from the full window: one
# narrow column, so the glass rectangle is taller than it is wide.
VERTICAL_COLS, VERTICAL_ROWS = 56, 52


# Shipped face, and the face used when a measured cell has none yet.
DEFAULT_FONT = "Monospace 11"
# Glass opacity when the window opens. VTE text remains fully opaque.
# The bar runs from 10% to 100%. The right end is a solid pane.
# MP Liquid Player and MP Gui both use this host.
DEFAULT_WINDOW_OPACITY = 0.80
CONFIG_DIR = Path.home() / ".config" / "mix-manager"
FONT_CONFIG_FILE = CONFIG_DIR / "liquid_glass_font"
# The tracklist viewer draws its own viewport, but VTE still needs a scrollback
# buffer so wheel and scrollbar events are delivered to the child terminal.
TRACKLIST_SCROLLBACK = 2000

RECOMMENDED_FONTS = [
    ("Monospace 11", "Default face for MP Liquid Player, MP Gui, and MP Liquid Mixer"),
    ("Fira Code 11", "Modern Programming Font (Clean shapes, sleek proportions)"),
    ("Hack 11", "Optional programming font"),
    ("Adwaita Mono 11", "Modern GNOME Desktop Monospace"),
    ("Liberation Mono 11", "Classic Clean Monospace"),
]

Gdk = Gtk = GLib = Vte = None


def get_liquid_glass_font(cli_font=None):
    if cli_font and str(cli_font).strip():
        return str(cli_font).strip()
    env_f = (os.environ.get("MP_LIQUID_GLASS_FONT") or os.environ.get("LIQUID_GLASS_FONT") or "").strip()
    if env_f:
        return env_f
    try:
        if FONT_CONFIG_FILE.is_file():
            saved = FONT_CONFIG_FILE.read_text(encoding="utf-8").strip()
            if saved:
                return saved
    except Exception:
        pass
    return default_liquid_glass_font()


def set_liquid_glass_font(font_str: str) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        FONT_CONFIG_FILE.write_text(f"{str(font_str).strip()}\n", encoding="utf-8")
    except Exception as exc:
        print(f"Warning: could not save font to {FONT_CONFIG_FILE}: {exc}", file=sys.stderr)


def list_liquid_glass_fonts():
    cur = get_liquid_glass_font()
    cur_prefix = cur.split()[0] if cur else ""
    print("\n\033[1;38;2;0;229;255m══ MP Liquid Glass Player — Available & Recommended Fonts ══\033[0m\n")
    for idx, (f_name, desc) in enumerate(RECOMMENDED_FONTS, 1):
        star = "\033[1;38;2;0;255;127m★ ACTIVE\033[0m" if (f_name == cur or f_name.split()[0] == cur_prefix) else "        "
        print(f"  {star} \033[1;38;2;255;215;0m[{idx}]\033[0m \033[1;38;2;255;255;255m{f_name:<20s}\033[0m — {desc}")
    print(
        f"\n  \033[38;2;120;215;255mActive Font:\033[0m {cur}\n\n"
        "  \033[38;2;200;200;200mTo try a font on launch:\033[0m\n"
        '    python3 MP_Audio_Player_Liquid_Glass_OpenGL.py --font "Fira Code 11"\n'
        '    python3 MP_Audio_Player_Liquid_Glass_OpenGL.py --font "Hack 11"\n'
        '    python3 MP_Audio_Player_Liquid_Glass_OpenGL.py --font "Monospace 11"\n\n'
        "  \033[38;2;200;200;200mTo set a font as your permanent default:\033[0m\n"
        '    python3 MP_Audio_Player_Liquid_Glass_OpenGL.py --set-font "Fira Code 11"\n'
    )


def _analyser_rows(rows):
    """Analyser height the console player reserves in a landscape frame."""
    return 7 if rows >= 34 else (5 if rows >= 28 else 3)


def _vertical_analyser_rows(rows):
    """Analyser height the console player reserves in the portrait frame."""
    spare = max(0, rows - 17 - 10 - 1)
    return max(3, min(20, 8 + spare // 3))


def _playlist_top_row(rows, mode="NORMAL"):
    """0-based row where the playlist's top rule is drawn.

    ``MP_Audio_Player.py`` draws 12 fixed rows before the playlist rule, then
    the analyser. The glass panel must start on that exact row or the first
    playlist entries sit outside it.
    """
    if str(mode).upper() == "VERTICAL":
        return 12 + _vertical_analyser_rows(rows)
    return 12 + _analyser_rows(rows)


def player_panel_rects(cols, rows, char_w, char_h, origin_x, origin_y, mode="NORMAL"):
    def box(c, r, bc, br):
        return (origin_x + c * char_w, origin_y + r * char_h, bc * char_w, br * char_h)

    iw = max(20, cols - 4)
    if mode in ("MICRO", "MANAGER"):
        return (box(1, 0, max(20, cols - 2), rows), (0.0, 0.0, 0.0, 0.0))
    if mode == "VERTICAL":
        # Vertical is the full player frame in a portrait column, so the glass
        # covers the same two boxes it draws: the header block on top, and the
        # playlist box below the analyser. The analyser takes whatever height
        # the fixed rows leave, so the playlist's first row follows the window.
        # The offset is measured from the drawn frame at every height.
        iw = max(20, cols - 6)
        playlist_top = _playlist_top_row(rows, "VERTICAL")
        playlist_rows = max(2, rows - playlist_top)
        return box(2, 0, iw + 2, 4), box(2, playlist_top, iw + 2, playlist_rows)
    if mode == "MINI":
        return (box(2, 0, iw, 3), box(2, 3, iw, max(1, rows - 3)))
    if mode == "TRACKLIST":
        return (box(2, 0, iw, rows), (0.0, 0.0, 0.0, 0.0)) if rows <= 7 else (box(2, 0, iw, 7), box(2, 7, iw, max(1, rows - 7)))
    iw = max(20, cols - 6)
    before = _playlist_top_row(rows, "NORMAL")
    return (box(2, 0, iw + 2, 4), box(2, before, iw + 2, max(1, rows - before - 5) + 2))


def _import_gtk():
    global Gdk, Gtk, GLib, Vte
    if sys.platform == "win32":
        return None, None, None
    if Gtk is not None:
        return GLib, Gtk, Vte
    os.environ["GDK_BACKEND"] = "x11"
    from local_vte import reexec_with_local_vte
    import gi
    try:
        for mod, ver in (("Gdk", "3.0"), ("Gtk", "3.0"), ("GdkX11", "3.0"), ("Vte", "2.91")):
            gi.require_version(mod, ver)
    except ValueError:
        reexec_with_local_vte()
        raise
    from gi.repository import Gdk as _Gdk, GdkX11, GLib as _GLib, Gtk as _Gtk, Vte as _Vte
    if not hasattr(GdkX11, "X11Display"):
        raise RuntimeError("GDK X11 backend is required for the liquid-glass window")
    Gdk, Gtk, GLib, Vte = _Gdk, _Gtk, _GLib, _Vte
    return GLib, Gtk, Vte


def _console_python() -> str:
    """A python.exe for the child terminal, even when the host is pythonw.

    The host runs windowless on Windows so it does not add a stray console.
    The terminal UI it embeds is a console application, so handing it
    pythonw.exe would leave it with no console to draw in.
    """
    exe = sys.executable
    if sys.platform == "win32" and exe.lower().endswith("pythonw.exe"):
        candidate = exe[:-len("pythonw.exe")] + "python.exe"
        if Path(candidate).is_file():
            return candidate
    return exe


def manager_argv():
    from windows_bash import find_bash
    return [find_bash(), str(ROOT / "Mix_Archive_Manager.sh")]


def player_argv(mode="NORMAL", extra_args=None):
    cmd = [_console_python(), str(ROOT / "MP_Audio_Player.py"), "--interactive"]
    if mode in ("VERTICAL", "MINI", "MICRO"):
        cmd.append(f"--{mode.lower()}")
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def embed_check_argv():
    return [sys.executable, "-c", "import time; print('LIQUID_GLASS_CHILD', flush=True); time.sleep(30)"]


def tracklist_argv(tracklist_path, audio_path=None, theme=None):
    v_sh = ROOT / "scripts" / "view_tracklist_console.sh"
    if not v_sh.is_file():
        v_sh = ROOT / "view_tracklist_console.sh"
    from windows_bash import find_bash
    cmd = [find_bash(), str(v_sh)]
    if theme:
        cmd.extend(["--theme", str(theme)])
    cmd.append(str(tracklist_path))
    if audio_path:
        cmd.append(str(audio_path))
    return cmd


def _child_env(as_dict=False):
    env = {k: v for k, v in os.environ.items() if k != "GDK_BACKEND"}
    env.update({
        "TERM": "xterm-256color",
        "COLORTERM": "truecolor",
        "MP_LIQUID_GLASS": "1",
        "MP_LIQUID_GLASS_BACKEND": "opengl",
        "MP_LIQUID_GLASS_PLATFORM": "win32" if sys.platform == "win32" else "linux",
        # W opens an MP Black Glass pane at exactly this window's size. The
        # child cannot ask the host directly, so it is told which published
        # geometry record belongs to the window it is drawn inside. Without
        # this it would take the newest record and could size itself from a
        # different open player.
        "MP_HOST_PID": str(os.getpid()),
    })
    if sys.platform == "win32":
        # Default console color. The host punches this exact RGB out of the console window.
        env["MP_LIQUID_GLASS_COLORKEY"] = "ff00ff"
    return env if as_dict else [f"{k}={v}" for k, v in env.items()]


def _mode_grid(mode):
    if mode == "TRACKLIST":
        return 100, 35
    if mode == "VERTICAL":
        return VERTICAL_COLS, VERTICAL_ROWS
    if mode == "MINI":
        return 74, 14
    if mode == "MICRO":
        return 74, 7
    return COLS, ROWS


def _measure_terminal_cell(font_name: str) -> tuple:
    """Pixel size of one VTE cell. The 9×18 guess is only used when VTE has no font yet."""
    if Gtk is None or Vte is None or GLib is None:
        return 9, 18
    window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    window.set_decorated(False)
    window.set_skip_taskbar_hint(True)
    window.set_skip_pager_hint(True)
    try:
        window.set_opacity(0)
    except Exception:
        pass
    term = Vte.Terminal()
    window.add(term)
    try:
        import gi
        gi.require_version("Pango", "1.0")
        from gi.repository import Pango
        desc = Pango.FontDescription.from_string(font_name or DEFAULT_FONT)
        desc.set_weight(Pango.Weight.NORMAL)
        term.set_font(desc)
    except Exception:
        pass
    window.move(-3000, -3000)
    window.show_all()
    term.realize()
    ctx = GLib.MainContext.default()
    for _ in range(8):
        while ctx.pending():
            ctx.iteration(False)
    try:
        cell_w = int(term.get_char_width() or 0)
        cell_h = int(term.get_char_height() or 0)
    except Exception:
        cell_w, cell_h = 0, 0
    window.destroy()
    while ctx.pending():
        ctx.iteration(False)
    if cell_w >= 4 and cell_h >= 8:
        return cell_w, cell_h
    return 9, 18


def _slider_gl():
    """OpenGL entry points for the transparency bar.

    Importing them on every frame showed up in the glass loop. The first call
    binds the names; later frames reuse that tuple.
    """
    cached = getattr(_slider_gl, "names", None)
    if cached is not None:
        return cached
    from OpenGL.GL import (
        glUseProgram, glGenVertexArrays, glBindVertexArray, glGenBuffers,
        glBindBuffer, glBufferData, glVertexAttribPointer, glEnableVertexAttribArray,
        glDrawArrays, glEnable, glBlendFunc, GL_BLEND, GL_SRC_ALPHA,
        GL_ONE_MINUS_SRC_ALPHA, GL_TRIANGLES, GL_ARRAY_BUFFER, GL_DYNAMIC_DRAW,
        GL_FLOAT, GL_FALSE, glUniform4f, glCreateShader, glShaderSource,
        glCompileShader, glCreateProgram, glAttachShader, glLinkProgram,
        GL_VERTEX_SHADER, GL_FRAGMENT_SHADER, glGetUniformLocation
    )
    import numpy as np
    cached = (
        glUseProgram, glGenVertexArrays, glBindVertexArray, glGenBuffers,
        glBindBuffer, glBufferData, glVertexAttribPointer, glEnableVertexAttribArray,
        glDrawArrays, glEnable, glBlendFunc, GL_BLEND, GL_SRC_ALPHA,
        GL_ONE_MINUS_SRC_ALPHA, GL_TRIANGLES, GL_ARRAY_BUFFER, GL_DYNAMIC_DRAW,
        GL_FLOAT, GL_FALSE, glUniform4f, glCreateShader, glShaderSource,
        glCompileShader, glCreateProgram, glAttachShader, glLinkProgram,
        GL_VERTEX_SHADER, GL_FRAGMENT_SHADER, glGetUniformLocation, np,
    )
    _slider_gl.names = cached
    return cached


class CustomGlassRenderer(LiquidGlassRendererOpenGL):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.slider_val = DEFAULT_WINDOW_OPACITY
        self._slider_dragging = False
        # Hover-only bar: alpha 0 means hidden. The GL loop draws continuously,
        # so time.monotonic() advances the fade without a separate timer.
        self._slider_alpha = 0.0
        self._slider_alpha_target = 0.0
        self._slider_fade_from = 0.0
        self._slider_fade_started = time.monotonic()
        # Height of the strip the host keeps clear under the terminal. The host
        # owns the mode (player vs MP Gui / tracklist) and publishes the value
        # it reserved in `_place_terminal`; until then the classic strip is
        # right for every mode.
        self._slider_strip_px = SLIDER_STRIP

    def _slider_strip(self) -> float:
        """Bottom strip reserved for the transparency bar, in pixels.

        The glass loop centred the bar with a fixed 22 px offset, then moved to
        ``_slider_strip()`` so it could follow the player's one-row-shorter
        strip. That method only exists on ``GlassHost``, so calling it from the
        renderer raised ``AttributeError`` on every frame: the slider never
        drew, and the traceback was printed once per frame for the life of the
        window.
        """
        try:
            return float(self._slider_strip_px)
        except (TypeError, ValueError):
            return float(SLIDER_STRIP)

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

        GL = getattr(self, "_GL", None)
        if GL is None:
            from OpenGL import GL
            self._GL = GL

        GL.glViewport(0, 0, fw, fh)
        GL.glClearColor(0.0, 0.0, 0.0, 0.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        showing_visualizer = self.draw_player_visualizer_background(fw, fh)
        self.sync_player_visualizer_opacity(bool(showing_visualizer))
        if showing_visualizer:
            if ww > 0 and wh > 0:
                colors, _, _ = get_dynamic_theme_state(theme_name, opacity)
                self._draw_transparency_slider(fw, fh, ww, wh, colors)
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

        slider = max(0.0, min(1.0, float(getattr(self, "clear_opacity", DEFAULT_WINDOW_OPACITY) or 0.0)))
        GL.glUniform4f(u["u_res"], float(fw), float(fh), slider, aspect)
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

        if ww > 0 and wh > 0:
            self._draw_transparency_slider(fw, fh, ww, wh, colors)

        pixels = None
        if capture:
            raw = GL.glReadPixels(0, 0, fw, fh, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
            pixels = self._format_capture(fw, fh, raw)

        self._glfw.glfwSwapBuffers(self._window)
        return pixels

    def _slider_child_bottom(self):
        """Pixels of clear space below the terminal, or ``None`` if unmeasurable.

        The GTK terminal can be taller than ``_well_rect`` asks for, because
        VTE sizes itself from its row grid. Drawing the slider from a fixed
        offset then put it underneath that terminal, which is why the bar
        looked removed in MP Gui. This reports the real free strip at the
        bottom of the window so the bar can be placed inside it.
        """
        try:
            child = int(getattr(self, "_slider_child", 0) or 0)
            if child <= 0:
                return None
            _ww, wh = self.window_size()
            if wh <= 0:
                return None
            term_size = getattr(self, "_last_term_size", None)
            cache_key = (child, _ww, wh, term_size)
            if getattr(self, "_cached_clear_bottom_key", None) == cache_key:
                return self._cached_clear_bottom
            _cx, cy, _cw, ch = self.window_rect(child)
            if ch <= 0:
                return None
            _wx, wy = self.window_pos()
            # Vertical clear space between the terminal's lower edge and the
            # bottom of the glass window.
            res = max(0.0, float((wy + wh) - (cy + ch)))
            self._cached_clear_bottom_key = cache_key
            self._cached_clear_bottom = res
            return res
        except Exception:
            return None

    def _slider_hovered(self, ww, wh) -> bool:
        """True while the pointer is over the track's bottom strip."""
        if not getattr(self, "cursor_inside", True):
            return False
        try:
            x, y = self.cursor
            strip = max(10.0, float(self._slider_strip()))
        except (AttributeError, TypeError, ValueError):
            return False
        left = float(SLIDER_LEFT) - float(SLIDER_HOVER_PAD)
        right = float(ww) - float(SLIDER_RIGHT_PAD) + float(SLIDER_HOVER_PAD)
        return left <= float(x) <= right and float(y) >= float(wh) - strip

    def _update_slider_fade(self, ww, wh) -> None:
        """Advance the hover fade, keeping the bar up during a drag."""
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

    def _draw_transparency_slider(self, fw, fh, ww, wh, colors):
        self._update_slider_fade(ww, wh)
        if self._slider_alpha <= 0.001:
            return
        alpha = self._slider_alpha
        try:
            (
                glUseProgram, glGenVertexArrays, glBindVertexArray, glGenBuffers,
                glBindBuffer, glBufferData, glVertexAttribPointer, glEnableVertexAttribArray,
                glDrawArrays, glEnable, glBlendFunc, GL_BLEND, GL_SRC_ALPHA,
                GL_ONE_MINUS_SRC_ALPHA, GL_TRIANGLES, GL_ARRAY_BUFFER, GL_DYNAMIC_DRAW,
                GL_FLOAT, GL_FALSE, glUniform4f, glCreateShader, glShaderSource,
                glCompileShader, glCreateProgram, glAttachShader, glLinkProgram,
                GL_VERTEX_SHADER, GL_FRAGMENT_SHADER, glGetUniformLocation, np,
            ) = _slider_gl()

            if not hasattr(self, "_slider_prog"):
                # These used to be `#version 410 core`, which no context below
                # OpenGL 4.1 can compile. The host only ever creates a 3.3 (or
                # lower, on a Raspberry Pi) context, so the slider silently
                # vanished everywhere. Build the source at the version this
                # context actually accepts.
                glsl = _slider_glsl_version()
                vs_src = _slider_vertex_source(glsl)
                fs_src = _slider_fragment_source(glsl)
                vs = glCreateShader(GL_VERTEX_SHADER)
                glShaderSource(vs, vs_src)
                glCompileShader(vs)
                fs = glCreateShader(GL_FRAGMENT_SHADER)
                glShaderSource(fs, fs_src)
                glCompileShader(fs)
                self._slider_prog = glCreateProgram()
                glAttachShader(self._slider_prog, vs)
                glAttachShader(self._slider_prog, fs)
                # GLSL 1.40 and below have no explicit attribute location.
                try:
                    from OpenGL.GL import glBindAttribLocation
                    glBindAttribLocation(self._slider_prog, 0, b"a_pos")
                except Exception:
                    pass
                glLinkProgram(self._slider_prog)
                self._slider_vao = glGenVertexArrays(1)
                self._slider_vbo = glGenBuffers(1)
                self._slider_u_col = glGetUniformLocation(self._slider_prog, "u_color")

            x_left = 40.0
            x_right = max(80.0, float(ww) - 40.0)
            # Sit inside the strip the terminal well reserves below itself
            # (_well_rect keeps _slider_strip() px free at the bottom, one text
            # row less than MP Gui so the player's hints sit above the bar).
            # The old fixed 22 px left the bar underneath the terminal's last
            # text row in MP Gui, so it looked missing even though it was drawn.
            y_from_bottom = max(6.0, (float(self._slider_strip()) - 8.0) / 2.0)
            # VTE can overflow that reserve with its own row grid. Measure the
            # real clear strip and centre the 8 px bar inside it, so the bar is
            # never drawn under the terminal.
            clear = self._slider_child_bottom()
            if clear is not None and clear >= 12.0:
                y_from_bottom = max(6.0, (clear - 8.0) / 2.0)

            # Derive slider panel and thumb colors dynamically from the active theme palette
            c0 = _rgb01(colors[0])
            c1 = _rgb01(colors[1])

            # NDC y grows upward: -1 is the bottom edge, +1 is the top. The
            # distance the bar sits *above the bottom* therefore maps to
            # -1 + 2*(px/height). The old `1 - 2*(px/height)` is the mirror of
            # that: it sent a bar 12 px from the bottom to 12 px from the TOP,
            # which is why the transparency bar could not be found. A pixel
            # capture of the running window located the drawn band at y=22..29
            # from the window top, exactly where this mapping put it.
            def _ndc_y(px):
                return -1.0 + (float(px) / float(wh)) * 2.0

            ndc_x1 = (x_left / ww) * 2.0 - 1.0
            ndc_x2 = (x_right / ww) * 2.0 - 1.0
            ndc_y1 = _ndc_y(y_from_bottom)
            ndc_y2 = _ndc_y(y_from_bottom + 8.0)

            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glUseProgram(self._slider_prog)
            glBindVertexArray(self._slider_vao)
            glBindBuffer(GL_ARRAY_BUFFER, self._slider_vbo)

            track_quad_key = (ndc_x1, ndc_y1, ndc_x2, ndc_y2)
            if getattr(self, "_last_track_quad_key", None) != track_quad_key:
                self._track_quad_data = np.array([
                    ndc_x1, ndc_y1,  ndc_x2, ndc_y1,  ndc_x1, ndc_y2,
                    ndc_x2, ndc_y1,  ndc_x2, ndc_y2,  ndc_x1, ndc_y2
                ], dtype=np.float32)
                self._last_track_quad_key = track_quad_key
            track_quad = self._track_quad_data

            u_col_loc = getattr(self, "_slider_u_col", None)
            if u_col_loc is None:
                u_col_loc = self._slider_u_col = glGetUniformLocation(self._slider_prog, "u_color")
            
            # Theme-matched subtle background panel strip. Track and thumb share
            # this buffer, so both are uploaded every frame: the thumb draw
            # overwrites the track, and the next frame has to put it back.
            glUniform4f(u_col_loc, c0[0] * 0.7, c0[1] * 0.7, c0[2] * 0.7, 0.55 * alpha)
            glBufferData(GL_ARRAY_BUFFER, track_quad.nbytes, track_quad, GL_DYNAMIC_DRAW)
            glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 2 * 4, ctypes.c_void_p(0))
            glEnableVertexAttribArray(0)
            glDrawArrays(GL_TRIANGLES, 0, 6)

            thumb_x = slider_thumb_x(self.slider_val, ww)

            t_x1 = ((thumb_x - 6.0) / ww) * 2.0 - 1.0
            t_x2 = ((thumb_x + 6.0) / ww) * 2.0 - 1.0
            t_y1 = _ndc_y(y_from_bottom - 4.0)
            t_y2 = _ndc_y(y_from_bottom + 12.0)

            thumb_key = (t_x1, t_y1, t_x2, t_y2)
            if getattr(self, "_last_thumb_key", None) != thumb_key:
                self._thumb_quad_data = np.array([
                    t_x1, t_y1,  t_x2, t_y1,  t_x1, t_y2,
                    t_x2, t_y1,  t_x2, t_y2,  t_x1, t_y2
                ], dtype=np.float32)
                self._last_thumb_key = thumb_key
            thumb_quad = self._thumb_quad_data

            # Theme-matched vibrant accent thumb
            glUniform4f(u_col_loc, c1[0], c1[1], c1[2], 0.95 * alpha)
            glBufferData(GL_ARRAY_BUFFER, thumb_quad.nbytes, thumb_quad, GL_DYNAMIC_DRAW)
            glDrawArrays(GL_TRIANGLES, 0, 6)

            glBindVertexArray(0)
            glUseProgram(0)
        except Exception as e:
            print(f"Slider draw error: {e}", flush=True)


def _child_console_hwnd(pid: int, timeout: float = 5.0) -> int:
    """HWND of the console created for ``pid``, or 0.

    On Windows 10 the top-level console window is reported under
    ``conhost.exe`` by ``GetWindowThreadProcessId``, so the EnumWindows-by-PID
    scan found nothing and the terminal stayed a separate hidden window behind
    the glass. A windowless host (pythonw) can attach to the child's console
    and ask ``GetConsoleWindow`` for the real HWND instead.
    """
    try:
        import ctypes
        import time

        user32 = ctypes.windll.user32
        user32.GetConsoleWindow.restype = ctypes.c_void_p
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.AttachConsole.argtypes = [ctypes.c_uint32]
        kernel32.AttachConsole.restype = ctypes.c_int
        kernel32.FreeConsole.restype = ctypes.c_int
        own_console = int(user32.GetConsoleWindow() or 0)
        deadline = time.monotonic() + max(0.2, float(timeout))
        while time.monotonic() < deadline:
            if not own_console:
                if kernel32.AttachConsole(ctypes.c_uint32(int(pid))):
                    hwnd = int(user32.GetConsoleWindow() or 0)
                    kernel32.FreeConsole()
                    if hwnd:
                        return hwnd
                elif ctypes.get_last_error() == 5:
                    # Already attached to something: drop it and retry.
                    kernel32.FreeConsole()
            time.sleep(0.05)
    except Exception:
        pass
    return 0


def _reveal_windows_console(user32, hwnd, glass_hwnd, pid):
    """Keep the player console above the glass and punch out its background.

    A child console is opaque, so the theme drawn underneath never shows.
    An owned layered window can color-key that background away. The key is
    magenta, which the player text does not use.
    """
    hwnd = int(hwnd)
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetLayeredWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_ubyte, ctypes.c_uint32]
    user32.SetLayeredWindowAttributes.restype = ctypes.c_int
    style = user32.GetWindowLongPtrW(hwnd, -16)
    # Drop caption, frame, and both scrollbars. The vertical bar is the one on the right.
    style = (style & ~0x00CF0000 & ~0x00300000) | 0x80000000 | 0x10000000
    user32.SetWindowLongPtrW(hwnd, -16, style)
    if glass_hwnd:
        user32.SetWindowLongPtrW(hwnd, -8, int(glass_hwnd))
    exstyle = user32.GetWindowLongPtrW(hwnd, -20)
    user32.SetWindowLongPtrW(hwnd, -20, (exstyle | 0x00080000 | 0x00000080) & ~0x00040000)
    user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
    user32.SetLayeredWindowAttributes(hwnd, 0x00FF00FF, 0, 0x00000001)
    _paint_console_colorkey(pid)


def _paint_console_colorkey(pid):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.AttachConsole.argtypes = [ctypes.c_uint32]
    kernel32.AttachConsole.restype = ctypes.c_int
    kernel32.FreeConsole.restype = ctypes.c_int
    if not kernel32.AttachConsole(ctypes.c_uint32(int(pid))):
        if ctypes.get_last_error() != 5:
            return
        kernel32.FreeConsole()
        if not kernel32.AttachConsole(ctypes.c_uint32(int(pid))):
            return
    try:
        from ctypes import wintypes

        class COORD(ctypes.Structure):
            _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

        class SMALL_RECT(ctypes.Structure):
            _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short), ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]

        class INFOEX(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.ULONG),
                ("dwSize", COORD),
                ("dwCursorPosition", COORD),
                ("wAttributes", wintypes.WORD),
                ("srWindow", SMALL_RECT),
                ("dwMaximumWindowSize", COORD),
                ("wPopupAttributes", wintypes.WORD),
                ("bFullscreenSupported", wintypes.BOOL),
                ("ColorTable", wintypes.DWORD * 16),
            ]

        kernel32.GetStdHandle.restype = wintypes.HANDLE
        handle = kernel32.GetStdHandle(-11)
        info = INFOEX()
        info.cbSize = ctypes.sizeof(INFOEX)
        kernel32.GetConsoleScreenBufferInfoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(INFOEX)]
        kernel32.GetConsoleScreenBufferInfoEx.restype = wintypes.BOOL
        kernel32.SetConsoleScreenBufferInfoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(INFOEX)]
        kernel32.SetConsoleScreenBufferInfoEx.restype = wintypes.BOOL
        if not kernel32.GetConsoleScreenBufferInfoEx(handle, ctypes.byref(info)):
            return
        info.ColorTable[0] = 0x00FF00FF
        info.wAttributes = info.wAttributes & 0xFFF0
        info.srWindow.Right += 1
        info.srWindow.Bottom += 1
        kernel32.SetConsoleScreenBufferInfoEx(handle, ctypes.byref(info))
        from windows_console_scroll import hide_attached_console_scrollbar
        hide_attached_console_scrollbar()
    finally:
        kernel32.FreeConsole()


class GlassHost:
    def __init__(self, argv, embed_check=False, mode="NORMAL", cli_theme=None, font=None):
        self.argv = list(argv)
        self.embed_check = embed_check
        self.mode = mode.upper()
        self.cli_theme = cli_theme
        self.font_name = get_liquid_glass_font(font)
        self.renderer = self.gtk_window = self.terminal = self.glib = self.spawn_error = self._saved_rect = None
        self.child_xid = self.child_pid = self._last_top_click_x = self._last_top_click_y = 0
        self.child_done = self.is_maximized = self._term_dragging = self._slider_dragging = False
        self.audio_drop = None
        self._last_top_click = 0.0
        self._target_cols, self._target_rows = _mode_grid(self.mode)
        self._last_well = self._last_panel_state = None

    def _dims(self):
        if self.terminal is not None:
            cached = getattr(self, "_cached_dims", None)
            if cached is not None:
                return cached
            cw, ch = int(self.terminal.get_char_width() or 0), int(self.terminal.get_char_height() or 0)
            if cw >= 4 and ch >= 8:
                self._cached_dims = (cw, ch)
                return cw, ch
        return 9, 18

    def _on_bezel(self, x, y, w, h):
        m = max(float(self.renderer.bezel_px), 24.0)
        return x < m or y < m or x >= w - m or y >= h - m

    def _on_top_bezel(self, x, y, w, h):
        return y < max(float(self.renderer.bezel_px), 24.0) + 48 and not self._on_close(x, y, w, h)

    def _on_close(self, x, y, w, _h):
        m = max(float(self.renderer.bezel_px), 8.0)
        return (x - (w - m * 0.72)) ** 2 + (y - m * 0.62) ** 2 <= max(m * 0.30, 4.0) ** 2

    def _slider_strip(self):
        """Bottom strip kept free for the transparency bar.

        The player takes one text row less than MP Gui and the tracklist
        viewer, and gives it to the terminal. That drops its last two text
        rows (the status line and the key hints) one line lower, right above
        the bar, instead of leaving a blank row of glass between them. The
        window size is unchanged: the row comes out of the strip, not the
        frame.
        """
        if getattr(self, "mode", "NORMAL") in ("MANAGER", "TRACKLIST"):
            return 44
        try:
            _cw, ch = self._dims()
        except AttributeError:
            # A partially built host (tests, or before the terminal exists)
            # keeps the classic strip.
            return 44
        return player_slider_strip(ch)

    def _well_rect(self):
        gx, gy = self.renderer.window_pos()
        w, h = self.renderer.window_size()
        m = self.renderer.bezel_px
        return gx + m, gy + m, max(32, w - 2 * m), max(32, h - 2 * m - self._slider_strip())

    def _place_terminal(self, force=False, sync_panels=True):
        rect = self._well_rect()
        if not force and rect == self._last_well:
            return
        self._last_well = rect
        x, y, iw, ih = rect
        if self.gtk_window is not None:
            self.gtk_window.move(int(x), int(y))
            if getattr(self, "_last_term_size", None) != (int(iw), int(ih)):
                self.gtk_window.resize(int(iw), int(ih))
                self._last_term_size = (int(iw), int(ih))
        # Let the renderer measure the terminal so the transparency slider can
        # be kept clear of it (VTE can be taller than the well asks for). The
        # strip this host actually reserved goes with it: the renderer centres
        # the bar inside that strip, and the player takes one text row less
        # than MP Gui and the tracklist viewer.
        if self.renderer is not None:
            try:
                self.renderer._slider_child = int(self.child_xid or 0)
                self.renderer._slider_strip_px = self._slider_strip()
            except Exception:
                pass
        self.renderer.place_terminal(self.child_xid, x, y, iw, ih)
        self._fit_console_to_well()
        # The frame loop already measured the panels before drawing. A second
        # query here would land after the shader, one frame late, and it asks
        # VTE for the cell count again.
        if sync_panels:
            self._sync_panels()

    def _fit_console_to_well(self) -> None:
        """Match the embedded console's buffer to the window it now has.

        ``SetWindowPos`` changes how many rows the console can show but leaves
        its screen buffer at the old height. Coming back from full screen the
        buffer stayed 68 rows tall behind a 45-row window, so the menu painted
        23 rows below the visible area and ``stty size`` told it the space was
        there. Fitting the buffer makes the child read the real grid.

        The console only updates its own idea of the window a frame or two
        after ``SetWindowPos``, and a fit issued in that gap would size the
        buffer to the *old* rectangle and then look consistent. So a well
        change starts a short settle window: the fit is repeated every 150 ms
        until the console's window and its buffer agree on the new rectangle.
        """
        if sys.platform != "win32" or not self.child_xid:
            return
        now = time.monotonic()
        well = getattr(self, "_last_well", None)
        if well != getattr(self, "_console_fit_well", None):
            # A new well: remember it and open the settle window.
            self._console_fit_well = well
            self._console_fit_until = now + 1.6
            self._console_fit_at = 0.0
        if now > getattr(self, "_console_fit_until", 0.0):
            return
        if now < getattr(self, "_console_fit_at", 0.0):
            return
        self._console_fit_at = now + 0.15
        try:
            from windows_console_scroll import (
                console_window_matches,
                fit_console_buffer_for_window,
            )
            hwnd = int(self.child_xid)
            if well and not console_window_matches(hwnd, *well):
                return                      # the console has not arrived yet
            fit_console_buffer_for_window(hwnd)
        except Exception:
            self._console_fit_until = 0.0

    def _poll_due(self, attr: str, interval: float, now: float) -> bool:
        """True when ``interval`` seconds have passed since the last poll.

        The glass loop used to stat the size file and the visualizer state on
        every refresh. Those change when the user asks, not 60 times a second.
        """
        if now < getattr(self, attr, 0.0):
            return False
        setattr(self, attr, now + interval)
        return True

    def _sync_panels(self):
        if self.renderer is None:
            return
        cw, ch = self._dims()
        m = self.renderer.bezel_px
        cols, rows = self._target_cols, self._target_rows
        if self.terminal is not None:
            tc, tr = int(self.terminal.get_column_count() or 0), int(self.terminal.get_row_count() or 0)
            if tc >= 20:
                cols = tc
            if tr >= 5:
                rows = tr
        state = (cols, rows, cw, ch, m, self.mode)
        if state != self._last_panel_state:
            self._last_panel_state = state
            self.renderer.set_panels(player_panel_rects(cols, rows, cw, ch, m, m, mode=self.mode))

    def _note_player_window(self, width, height):
        """Publish the outer window. Tracklist is not the player or MP Gui."""
        if self.mode == "TRACKLIST":
            return
        try:
            width_i, height_i = int(width), int(height)
        except (TypeError, ValueError):
            return
        if width_i < 64 or height_i < 64:
            return
        self._window_target = (width_i, height_i)
        self._window_target_until = time.monotonic() + 3.0
        if self.mode == "MANAGER":
            from liquid_player_visualizer import publish_liquid_gui_window_size
            publish_liquid_gui_window_size(width_i, height_i)
            return
        from liquid_player_visualizer import publish_liquid_player_window_size
        publish_liquid_player_window_size(width_i, height_i)

    def _host_window_id(self):
        """This host's own window id, or 0 when it cannot be read.

        Published with the bond geometry so the companion stacks against this
        exact window. X11 uses the XID; Windows uses the HWND, which
        ``SetWindowPos`` consumes directly. Without it the visualizer picks the
        first window whose title matches, which is wrong as soon as a Liquid
        Player and MP Gui are both open.
        """
        if self.renderer is None:
            return 0
        if sys.platform == "win32":
            try:
                return int(self.renderer.win32_window() or 0)
            except Exception:
                return 0
        if not sys.platform.startswith("linux"):
            return 0
        try:
            return int(self.renderer.x11_window() or 0)
        except Exception:
            return 0

    def _publish_player_window_geometry(self):
        """Publish the window's position and size for the bonded visualizer.

        Mini and Micro are the same window, so their geometry is published
        too. Without that the visualizer kept the Normal geometry and came
        away from the player as soon as M changed the size.

        MP Gui publishes its own geometry as well: the companion visualizer it
        launches sits behind *that* window, so it needs the GUI's position and
        not just its size.

        While a resize is still being applied, the size the host asked for is
        published instead of the half-applied one, so the visualizer moves
        straight to the new mode instead of stepping through it.
        """
        if self.mode == "TRACKLIST" or self.renderer is None:
            return
        try:
            x, y = self.renderer.window_pos()
            width, height = self.renderer.window_size()
            target = getattr(self, "_window_target", None)
            if target and time.monotonic() < getattr(self, "_window_target_until", 0.0):
                width, height = target
            wid = self._host_window_id()
            geom = (int(x), int(y), int(width), int(height), int(wid))
            if getattr(self, "_last_published_geom", None) == geom:
                return
            if self.mode == "MANAGER":
                # MP Gui's companion sits behind MP Gui, so the GUI record needs
                # the position too, not just the size _note_player_window
                # publishes.
                from liquid_player_visualizer import publish_liquid_gui_window_geometry
                publish_liquid_gui_window_geometry(x, y, width, height, wid=wid)
                self._last_published_geom = geom
                return
            self._last_published_geom = geom
            from liquid_player_visualizer import publish_liquid_player_window_geometry
            publish_liquid_player_window_geometry(x, y, width, height, wid=wid)
        except Exception:
            pass

    def _sync_player_window(self):
        """Keep the fitted size if the window manager has not applied it yet."""
        if self.mode == "TRACKLIST" or self.renderer is None:
            return
        target = getattr(self, "_window_target", None)
        if not target:
            return
        try:
            actual = tuple(int(v) for v in self.renderer.window_size())
        except Exception:
            return
        if actual != target and time.monotonic() < getattr(self, "_window_target_until", 0.0):
            self.renderer.set_window_size(*target)
            return
        if actual != target and min(actual) >= 64:
            self._note_player_window(*actual)

    def _apply_grid(self, cols, rows, pos=None, size=None):
        cw, ch = self._dims()
        m = self.renderer.bezel_px
        self._target_cols, self._target_rows = cols, rows
        if pos:
            self.renderer.set_window_pos(pos[0], pos[1])
        width, height = size or (cols * cw + 2 * m, rows * ch + 2 * m + 44)
        self._note_player_window(width, height)
        self.renderer.set_window_size(width, height)
        self.renderer.poll()
        if self.terminal is not None:
            self.terminal.set_size(cols, rows)
        self._place_terminal(force=True)

    def toggle_maximize(self):
        if self.renderer is None:
            return
        cw, ch = self._dims()
        m = self.renderer.bezel_px
        if not self.is_maximized:
            self._saved_rect = (self.renderer.window_pos(), self.renderer.window_size())
            wx, wy, ww, wh = self.renderer.get_workarea()
            self._apply_grid(max(40, int((ww - 2 * m) // cw)), max(10, int((wh - 2 * m - self._slider_strip()) // ch)), (wx, wy), (ww, wh))
            self.is_maximized = True
        else:
            pos, _ = self._saved_rect or (window_launch_origin(72, 64), (1280, 856))
            cols, rows = _mode_grid(self.mode if self.mode != "TRACKLIST" else "NORMAL")
            self._apply_grid(cols, rows, pos)
            self.is_maximized = False

    def set_mode(self, mode: str):
        self.mode = mode.upper()
        if self.renderer is None:
            return
        self.is_maximized = False
        cols, rows = _mode_grid(self.mode if self.mode != "TRACKLIST" else "NORMAL")
        self._apply_grid(cols, rows)

    def _gui_height_extra(self) -> int:
        """Pixels under the text grid. The OpenGL host keeps the slider there."""
        return 44

    def _remember_gui_normal_metrics(self) -> None:
        if getattr(self, "_gui_normal_px", None) or self.renderer is None:
            return
        cw, ch = self._dims()
        self._gui_normal_cell = (int(cw), int(ch))
        margin = int(self.renderer.bezel_px)
        extra = int(self._gui_height_extra())
        self._gui_normal_px = (
            COLS * int(cw) + 2 * margin,
            ROWS * int(ch) + 2 * margin + extra,
        )

    def _set_terminal_font_scale(self, scale: float) -> None:
        """Point size times ``scale``. 1 keeps the named face. 0.8 is the portrait mode."""
        self._font_scale = float(scale)
        self._cached_dims = None
        term = getattr(self, "terminal", None)
        if term is None:
            return
        try:
            import gi
            gi.require_version("Pango", "1.0")
            from gi.repository import Pango
            from mp_gui_window_shape import font_point_size
            desc = Pango.FontDescription.from_string(self.font_name or DEFAULT_FONT)
            desc.set_weight(Pango.Weight.NORMAL)
            if abs(float(scale) - 1.0) > 0.001:
                desc.set_size(int(round(font_point_size(self.font_name or DEFAULT_FONT, scale) * Pango.SCALE)))
            term.set_font(desc)
        except Exception:
            return
        glib = getattr(self, "glib", None)
        if glib is not None:
            try:
                self._pump_gtk(glib)
            except Exception:
                pass

    def _remeasure_cells(self, scale: float) -> tuple:
        """Cell size after a font change. Falls back to the normal cell times ``scale``."""
        from mp_gui_window_shape import scaled_cell
        self._cached_dims = None
        normal = getattr(self, "_gui_normal_cell", (9, 18))
        term = getattr(self, "terminal", None)
        cw = ch = 0
        if term is not None:
            try:
                cw = int(term.get_char_width() or 0)
                ch = int(term.get_char_height() or 0)
            except Exception:
                cw = ch = 0
        shrunk = float(scale) < 0.99 and cw < int(normal[0]) and ch < int(normal[1])
        if cw >= 4 and ch >= 8 and (float(scale) >= 0.99 or shrunk):
            self._cached_dims = (cw, ch)
            return cw, ch
        cell = scaled_cell(normal, scale)
        self._cached_dims = cell
        return cell

    def _apply_gui_window_shape(self, shape: str) -> None:
        """HORIZONTAL is the normal landscape window. VERTICAL swaps it and shrinks the text."""
        from mp_gui_window_shape import VERTICAL, VERTICAL_FONT_SCALE, portrait_window, terminal_grid
        if self.renderer is None:
            return
        self._remember_gui_normal_metrics()
        scale = VERTICAL_FONT_SCALE if shape == VERTICAL else 1.0
        self._set_terminal_font_scale(scale)
        cw, ch = self._remeasure_cells(scale)
        margin = int(self.renderer.bezel_px)
        extra = int(self._gui_height_extra())
        if shape == VERTICAL:
            width, height = portrait_window(*self._gui_normal_px)
            cols, rows = terminal_grid(width, height, cw, ch, margin, extra)
            self._apply_grid(cols, rows, size=(width, height))
        else:
            self._apply_grid(COLS, ROWS)
        self._gui_shape = shape
        self.is_maximized = False
        try:
            width, height = self.renderer.window_size()
            self._recenter_window(int(width), int(height))
        except Exception:
            pass

    def _follow_gui_window_shape(self) -> None:
        """Apply the shape the manager published. M typed into search does not publish one."""
        if self.mode != "MANAGER":
            return
        try:
            from mp_gui_window_shape import HORIZONTAL, read_gui_window_shape
        except ImportError:
            return
        shape = read_gui_window_shape(CONFIG_DIR, int(getattr(self, "child_pid", 0) or 0)) or HORIZONTAL
        if shape == getattr(self, "_gui_shape", HORIZONTAL):
            return
        if self.renderer is None:
            return
        self._apply_gui_window_shape(shape)

    def _follow_child_ui_size(self):
        """Apply the size the player published. M typed into search does not publish a new size."""
        if self.mode == "MANAGER":
            self._follow_gui_window_shape()
            return
        if self.mode == "TRACKLIST":
            return
        reader = getattr(self, "_read_ui_size", None)
        if reader is None:
            from liquid_ui_size import read_ui_size as reader
            self._read_ui_size = reader
        mode = reader(CONFIG_DIR, int(getattr(self, "child_pid", 0) or 0))
        if mode and mode != self.mode:
            self.set_mode(mode)

    def _claims_hidden_mode_key(self, keyval, state) -> bool:
        """True when this host, not the child, should hide the windows.

        The audio player uses plain H for Favorites, for search, and for the
        equalizer, and it binds Hidden Mode to Ctrl+H itself. Eating plain H
        here made Favorites unreachable for the whole life of the window.
        MP Gui's manager already decides between search and Hidden Mode, so
        plain H has to reach that script too. The tracklist viewer has no
        binding on H, so the host still hides the windows from that view.
        """
        import gi
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk
        if keyval not in (Gdk.KEY_h, Gdk.KEY_H):
            return False
        if self.mode in ("NORMAL", "VERTICAL", "MINI", "MICRO"):
            return False
        if self.mode == "MANAGER":
            return bool(state & Gdk.ModifierType.CONTROL_MASK)
        return True

    def _on_key_press(self, _term, event):
        import gi
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk
        kv = getattr(event, "keyval", 0)
        st = getattr(event, "state", 0)
        # Headset play/pause. Consumed here so a focused window cannot hand
        # the key to a video player. The daemon's MPRIS object is the path
        # the desktop uses; this covers a key that arrives at the window.
        try:
            from mp_mpris import dispatch_headset_key
            if dispatch_headset_key(kv):
                return True
        except Exception:
            pass
        # M stays with the player. Search uses it as a letter. A real mode change is published back.
        if kv == Gdk.KEY_K or (kv == Gdk.KEY_k and bool(st & Gdk.ModifierType.SHIFT_MASK)):
            # Kill only player processes owned by this user, matched on argv
            # rather than a substring of a shell command line. `pkill -f` also
            # hit editors with the file open, other users' players, and any
            # wrapper whose command string mentioned the name.
            _kill_player_processes()
            return True
        # Z opens a new MP Liquid Visualizer window, matching the Vulkan host.
        if self._claims_hidden_mode_key(kv, st):
            # Hidden Mode from a view that has no text binding on H. The
            # player and MP Gui handle their own H. The restore half is the
            # KWin script on Wayland.
            try:
                import mp_hidden_mode as hidden
                hidden.toggle()
            except Exception:
                pass
            return True
        if kv in (Gdk.KEY_z, Gdk.KEY_Z):
            spawn_new_visualizer()
            return True
        return False

    def _terminal_in_well(self):
        if not self.child_xid or self.renderer is None or self.renderer.window_parent(self.child_xid) == self.renderer.x11_window():
            return False
        ox, oy, w, h = self.renderer.window_rect(self.child_xid)
        x, y, iw, ih = self._well_rect()
        return abs(ox - x) <= 8 and abs(oy - y) <= 8 and abs(w - iw) <= 8 and abs(h - ih) <= 8

    def _on_spawn(self, _terminal, pid, error, *_rest):
        if error:
            self.spawn_error = str(error)
            if self.renderer is not None:
                self.renderer.request_close()
        else:
            self.child_pid = int(pid or 0)
            if self.audio_drop is not None:
                self.audio_drop.mark_ready()

    def _on_child_exit(self, _terminal, _status):
        self.child_done = True
        if self.renderer is not None:
            self.renderer.request_close()

    def _pump_gtk(self, glib):
        ctx = glib.MainContext.default()
        while ctx.pending():
            ctx.iteration(False)

    def _on_term_button_press(self, _term, event):
        # `Gdk` is None when PyGObject could not be imported (a headless run, a
        # Windows console host, a unit test). Reaching into Gdk.EventType then
        # raised AttributeError from an input handler, which takes the whole
        # render loop down; fall back to the numeric constants instead.
        gdk_event_type = getattr(Gdk, "EventType", None) if Gdk else None
        gdk_modifiers = getattr(Gdk, "ModifierType", None) if Gdk else None
        double_press = getattr(gdk_event_type, "_2BUTTON_PRESS", 5)
        if event.button == 1:
            if event.type == double_press and event.y <= 90:
                self.toggle_maximize()
                self._term_dragging = self._slider_dragging = False
                return True
            mods = (getattr(gdk_modifiers, "MOD1_MASK", 8)
                    | getattr(gdk_modifiers, "SUPER_MASK", 0x4000000))
            if (event.y <= 90 or bool(getattr(event, "state", 0) & mods)) and not self.is_maximized:
                self._term_dragging = True
                self._term_drag_origin = self.renderer.window_pos()
                rx, ry = self.renderer.root_pointer()
                self._term_drag_press = (rx, ry) if (rx or ry) else (int(event.x_root), int(event.y_root))
                return True
        return False

    def _on_term_motion_notify(self, _term, event):
        if self._term_dragging and not self.is_maximized:
            rx, ry = self.renderer.root_pointer()
            cx, cy = (rx, ry) if (rx or ry) else (int(event.x_root), int(event.y_root))
            self.renderer.set_window_pos(
                int(self._term_drag_origin[0] + cx - self._term_drag_press[0]),
                int(self._term_drag_origin[1] + cy - self._term_drag_press[1]),
            )
            self._place_terminal(force=True)
            return True
        return False

    def _on_term_button_release(self, _term, event):
        if event.button == 1:
            self._term_dragging = self._slider_dragging = False
        return False

    def _update_slider_val(self, x, w):
        """Move the slider thumb *and* apply the new opacity to the window.

        The old version only wrote `renderer.slider_val`, which is what paints
        the thumb. The glass opacity comes from `renderer.clear_opacity`, and
        `draw()` recomputes the X11 property from it every frame, so the thumb
        moved while the window stayed at 0.70. Writing both (and clearing the
        dynamic-factor memo) is what makes the drag actually change the glass.
        """
        opacity = opacity_from_x(x, w)
        renderer = self.renderer
        renderer.slider_val = opacity
        renderer.clear_opacity = opacity
        # `draw()` only republishes the property when the dynamic oscillation
        # factor changes; clearing the memo makes the next frame write this
        # value immediately.
        try:
            renderer._last_dyn_opacity = None
        except Exception:
            pass
        try:
            win = int(renderer.x11_window() or 0)
        except Exception:
            win = 0
        if win:
            try:
                renderer._set_opacity(win, opacity)
            except Exception:
                pass

    def _poll_windows_slider_hover(self):
        """Feed the glass renderer the real pointer position on Windows.

        The transparency bar is hover-only, and on the other platforms the
        GLFW cursor callback supplies the pointer. Here the terminal is a
        *separate console window* owned by the glass, stacked above it, so the
        pointer over the bottom strip is over the console: GLFW never receives
        a cursor event for it, ``renderer.cursor`` keeps whatever value it last
        had, and the bar never fades in. Reading the OS pointer and mapping it
        into window coordinates makes the strip reachable again.
        """
        if not self.child_xid or self.renderer is None:
            return
        try:
            import ctypes as _ct
            user32 = _ct.windll.user32

            class POINT(_ct.Structure):
                _fields_ = [("x", _ct.c_long), ("y", _ct.c_long)]

            pt = POINT()
            if not user32.GetCursorPos(_ct.byref(pt)):
                return
            try:
                x, y = self.renderer.window_pos()
            except Exception:
                return
            ww, wh = self.renderer.window_size()
            if ww <= 0 or wh <= 0:
                return
            local_x = float(pt.x - int(x))
            local_y = float(pt.y - int(y))
            self.renderer.cursor = (local_x, local_y)
            self.renderer.cursor_inside = (
                0.0 <= local_x <= float(ww) and 0.0 <= local_y <= float(wh)
            )
        except Exception:
            return

    def _track_drag(self, was_down, dragging, origin, press_screen):
        if getattr(self, "_os_dragging", False):
            return False, False, origin, press_screen
        x, y = self.renderer.cursor
        w, h = self.renderer.window_size()
        down = self.renderer.mouse_down
        if down and not was_down:
            if y >= h - self._slider_strip() and 40 <= x <= w - 40:
                self._slider_dragging = True
                self._update_slider_val(x, w)
                dragging = False
            elif self._on_close(x, y, w, h):
                self.renderer.request_close()
                dragging = False
            elif self._on_top_bezel(x, y, w, h):
                now = time.monotonic()
                if now - self._last_top_click < 0.45 and abs(x - self._last_top_click_x) < 35 and abs(y - self._last_top_click_y) < 35:
                    self.toggle_maximize()
                    dragging, self._last_top_click = False, 0.0
                else:
                    self._last_top_click, self._last_top_click_x, self._last_top_click_y = now, x, y
                    if not self.is_maximized:
                        dragging, origin = True, self.renderer.window_pos()
                        rx, ry = self.renderer.root_pointer()
                        press_screen = (rx, ry) if (rx or ry) else (origin[0] + x, origin[1] + y)
            elif self._on_bezel(x, y, w, h) and not self.is_maximized:
                dragging, origin = True, self.renderer.window_pos()
                rx, ry = self.renderer.root_pointer()
                press_screen = (rx, ry) if (rx or ry) else (origin[0] + x, origin[1] + y)
        elif down and self._slider_dragging:
            self._update_slider_val(x, w)
        elif down and dragging:
            rx, ry = self.renderer.root_pointer()
            nx = origin[0] + (rx - press_screen[0] if (rx or ry) else x - (press_screen[0] - origin[0]))
            ny = origin[1] + (ry - press_screen[1] if (rx or ry) else y - (press_screen[1] - origin[1]))
            self.renderer.set_window_pos(int(nx), int(ny))
        else:
            dragging = False
            self._slider_dragging = False
        return down, dragging, origin, press_screen

    def _fit_to_terminal(self, term):
        cw, ch = self._dims()
        m = self.renderer.bezel_px
        width = self._target_cols * cw + 2 * m
        height = self._target_rows * ch + 2 * m + 44
        self._note_player_window(width, height)
        self.renderer.set_window_size(width, height)
        term.set_size(self._target_cols, self._target_rows)
        self._recenter_window(width, height)

    def _recenter_window(self, width, height):
        """Centre the freshly sized window instead of leaving it in the corner.

        The window is created at a 9×18 guess and then refitted to the real
        VTE cell, so it grows after the first placement. Recentring here keeps
        the finished window on the primary screen instead of letting the
        growth push it off the right and bottom edges.
        """
        if not sys.platform.startswith("linux") or self.renderer is None:
            return
        try:
            from desktop_launch_screen import main_window_origin
            x, y = main_window_origin(int(width), int(height), 72, 64)
            self.renderer.set_window_pos(int(x), int(y))
        except Exception:
            pass

    def run(self):
        m = LiquidGlassRendererOpenGL.bezel_px
        iw, ih = self._target_cols * 9 + 2 * m, self._target_rows * 18 + 2 * m + 44
        if self.mode == "TRACKLIST":
            title = "Mix Tracklist Viewer (Liquid Glass - OpenGL)"
        elif self.mode == "MANAGER":
            title = "MP Mix Manager (Liquid Glass - OpenGL)"
        else:
            title = "MP Audio Player (Liquid Glass - OpenGL)"
        if sys.platform == "win32":
            self.renderer = CustomGlassRenderer(iw, ih, title)
            self.renderer.allow_player_visualizer = self.mode not in ("MANAGER", "TRACKLIST")
            try:
                return self._run_windows()
            finally:
                self._close_then_exit_video()
        glib, gtk, vte = _import_gtk()
        self.glib = glib
        gtk.init([])
        cell_w, cell_h = _measure_terminal_cell(self.font_name)
        iw, ih = self._target_cols * cell_w + 2 * m, self._target_rows * cell_h + 2 * m + 44
        self._note_player_window(iw, ih)
        self.renderer = CustomGlassRenderer(iw, ih, title)
        self.renderer.allow_player_visualizer = self.mode not in ("MANAGER", "TRACKLIST")
        try:
            return self._run_window(vte, glib)
        finally:
            self._close_then_exit_video()

    def _close_then_exit_video(self):
        """Tear the host down, then play the exit movie on the last MP window.

        The window is destroyed first, so closing the player disappears at
        once instead of sitting on screen for the whole movie. The companion
        visualizer is retired at the same moment: it is the background of this
        window, and leaving it up while the movie plays put a full-size
        raymarch window on the desktop with nothing in front of it. The movie
        then plays on its own, once, and closes itself when it ends, is
        skipped, or reaches its timeout. Every other MP Gui and MP Liquid
        Player window is counted first: one of several open windows closes
        silently and the movie waits for the last one.
        """
        self.close()
        # An embed check is a probe, not a close: it must never take a
        # companion the user is watching down with it.
        if not self.embed_check:
            stop_companion_for_mode(self.mode, player_gui=self.mode != "TRACKLIST")
        if not self.embed_check and self.mode in ("NORMAL", "MANAGER", "VERTICAL", "MINI", "MICRO"):
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

    def _run_windows(self):
        self.renderer.open()
        m = self.renderer.bezel_px
        self.renderer.set_window_size(self._target_cols * 9 + 2 * m, self._target_rows * 18 + 2 * m + 44)
        self._sync_panels()
        # MP Gui opens full screen; the player keeps its chosen size.
        if self.mode == "MANAGER":
            self.toggle_maximize()
        if self.embed_check:
            self.renderer.draw(read_player_theme())
            return True

        self._sync_windows_drag()
        proc = subprocess.Popen(self.argv, cwd=str(ROOT), env=_child_env(as_dict=True), creationflags=0x00000010)
        self.child_pid = proc.pid
        user32 = ctypes.windll.user32
        # Ask the console API first. Windows 10 reports the console window
        # under conhost.exe, so the by-PID EnumWindows scan can find nothing
        # and leave the terminal as a separate window hidden behind the glass.
        child_hwnd = _child_console_hwnd(proc.pid, timeout=5.0) or None
        if not child_hwnd:
            try:
                ENUM_PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
                found = []

                def _enum_win(hwnd, _lparam):
                    pid = ctypes.c_uint32()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if pid.value == proc.pid and user32.IsWindowVisible(hwnd):
                        found.append(hwnd)
                    return True

                cb = ENUM_PROC(_enum_win)
                for _ in range(50):
                    if proc.poll() is not None:
                        break
                    user32.EnumWindows(cb, 0)
                    if found:
                        child_hwnd = found[0]
                        break
                    time.sleep(0.02)
            except Exception:
                pass

        self.child_xid = child_hwnd or 0
        if child_hwnd:
            try:
                _reveal_windows_console(user32, child_hwnd, self.renderer.win32_window(), proc.pid)
            except Exception:
                pass
            self._place_terminal(force=True)
            # The console is a separate window owned by the glass. Without
            # this the glass keeps keyboard focus and every keystroke -- the
            # arrow keys included -- goes to GLFW instead of the menu. The
            # Linux path focuses its VTE child for the same reason.
            try:
                self.renderer.focus_window(self.child_xid)
            except Exception:
                pass
            # The Windows bond reads the same geometry records Linux uses.
            self._publish_player_window_geometry()
            # Hide the scrollbar once. Repeating this every 0.5 s attaches and
            # detaches the host from the console, which stalls the menu's
            # printf/read for a moment and made every key feel delayed.
            try:
                from windows_console_scroll import hide_process_console_scrollbar
                hide_process_console_scrollbar(proc.pid, int(child_hwnd))
            except Exception:
                pass

        self._sync_windows_drag()
        down = dragging = False
        origin, press_screen, theme, theme_at = self.renderer.window_pos(), (0, 0), read_player_theme(), 0.0
        focus_at = 0.0
        try:
            user32.GetForegroundWindow.restype = ctypes.c_void_p
        except Exception:
            pass
        try:
            while not self.renderer.should_close and proc.poll() is None:
                now = time.monotonic()
                if child_hwnd and now >= focus_at:
                    # Windows can activate the glass after the launch-time
                    # focus call and hand the keyboard back to GLFW, where the
                    # menu/player never sees C or the arrow keys. Hand focus to
                    # the console whenever the glass is the foreground window,
                    # except while the user is dragging it.
                    focus_at = now + 0.25
                    if not getattr(self, "_os_dragging", False):
                        try:
                            if int(user32.GetForegroundWindow() or 0) == int(self.renderer.win32_window() or 0):
                                self.renderer.focus_window(self.child_xid)
                        except Exception:
                            pass
                if now - theme_at > 0.4:
                    theme, theme_at = read_player_theme(), now
                if self._poll_due("_ui_size_poll_at", 0.10, now):
                    self._follow_child_ui_size()
                if self._poll_due("_geom_publish_at", 0.10, now):
                    self._publish_player_window_geometry()
                self._sync_panels()
                self._pump_windows_drag()
                self._poll_windows_slider_hover()
                self.renderer.draw(theme)
                down, dragging, origin, press_screen = self._track_drag(down, dragging, origin, press_screen)
                self._place_terminal(force=dragging, sync_panels=False)
                if not self.renderer.poll():
                    break
                time.sleep(0.008)
        finally:
            try:
                if proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass
            # The window is destroyed by _close_then_exit_video() as soon as
            # this loop returns; the exit movie then plays on its own.
        return True

    def _take_launch_mouse_focus(self, warp: bool = True) -> bool:
        """Raise the player and move the pointer into it. Used when MP Gui launches it."""
        if os.environ.get("MP_TAKE_MOUSE_FOCUS") != "1" or self.renderer is None:
            return True
        try:
            ok = bool(self.renderer.take_mouse_focus(self.child_xid, warp=warp))
        except Exception:
            return False
        if ok and self.terminal is not None:
            try:
                self.terminal.grab_focus()
            except Exception:
                pass
        return ok

    def _run_window(self, vte, glib):
        self.renderer.open()
        if self.mode == "MANAGER":
            self.renderer.arm_escape_close()
        window = self._gtk_terminal_window()
        term = vte.Terminal()
        term.set_audible_bell(False)
        if hasattr(term, "set_scrollback_lines"):
            # Tracklist mode draws its own viewport, but VTE still needs a scrollback
            # buffer so wheel and scrollbar input reaches the child process.
            term.set_scrollback_lines(TRACKLIST_SCROLLBACK if self.mode == "TRACKLIST" else 0)
        window.add(term)
        self._clear_terminal_background(window, term)
        window.show_all()
        term.realize()
        self._pump_gtk(glib)
        self.gtk_window, self.terminal = window, term
        self._apply_slider_opacity()
        self._fit_to_terminal(term)
        self.renderer.poll()
        gdk_win = window.get_window()
        self.child_xid = int(gdk_win.get_xid()) if gdk_win is not None else 0
        if self.child_xid <= 0:
            raise RuntimeError("VTE window has no X11 id to place in the glass")
        self._place_terminal(force=True)
        self.renderer.focus_window(self.child_xid)
        # MP Gui is the manager workspace: open it full screen so the whole
        # menu is visible without the user having to maximise it first.
        if self.mode == "MANAGER":
            self.toggle_maximize()
        term.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.BUTTON1_MOTION_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        term.connect("button-press-event", self._on_term_button_press)
        term.connect("button-release-event", self._on_term_button_release)
        term.connect("motion-notify-event", self._on_term_motion_notify)
        term.connect("key-press-event", self._on_key_press)
        term.connect("child-exited", self._on_child_exit)
        self._install_audio_drop(term)
        term.spawn_async(
            vte.PtyFlags.DEFAULT, str(ROOT), self.argv, _child_env(),
            glib.SpawnFlags.DEFAULT, None, None, -1, None, self._on_spawn,
        )
        term.grab_focus()
        theme, theme_at, was_down, dragging = read_player_theme(), 0.0, False, False
        origin, press_screen, started = (0, 0), (0.0, 0.0), time.monotonic()
        self._mouse_focus_until = started + 2.5 if os.environ.get("MP_TAKE_MOUSE_FOCUS") == "1" else 0.0
        self._mouse_focus_warp = self._mouse_focus_until > 0.0
        self._mouse_focus_next = 0.0
        while not self.renderer.should_close:
            now = time.monotonic()
            if self._mouse_focus_until and now < self._mouse_focus_until and now >= self._mouse_focus_next:
                if self._take_launch_mouse_focus(warp=self._mouse_focus_warp):
                    self._mouse_focus_warp = False
                self._mouse_focus_next = now + 0.35
            if now - theme_at > 0.4:
                new_theme = read_player_theme()
                theme_at = now
                if new_theme != theme:
                    theme = new_theme
                    if self.terminal is not None:
                        self._apply_glass_foreground(self.terminal, theme)
            if self._poll_due("_ui_size_poll_at", 0.10, now):
                self._follow_child_ui_size()
            self._sync_player_window()
            self._publish_player_window_geometry()
            self._sync_panels()
            self.renderer.draw(theme)
            was_down, dragging, origin, press_screen = self._track_drag(was_down, dragging, origin, press_screen)
            if self._poll_due("_vis_read_poll_at", 0.20, now):
                self._sync_visualizer_readability()
            self._place_terminal(sync_panels=False)
            self._pump_gtk(glib)
            # Frame cap. glfwSwapInterval alone cannot pace a 60 Hz panel to
            # 30 FPS when the divisor is not exact, and GTK's pump runs on its
            # own cadence, so the loop deadline does the final pacing. A
            # desktop has no cap and never reaches the sleep.
            _cap = mp_raspberry_pi.visualizer_fps_cap()
            if _cap > 0:
                _interval = 1.0 / float(_cap)
                _next = getattr(self, "_next_frame_at", 0.0)
                if _next <= 0.0:
                    _next = now + _interval
                elif _next > now:
                    _slack = _next - now
                    if _slack > 0.0015:
                        time.sleep(_slack - 0.0006)
                        now = time.monotonic()
                    _next = max(_next + _interval, now)
                else:
                    _next = now + _interval
                self._next_frame_at = _next
            if self.spawn_error:
                raise RuntimeError(self.spawn_error)
            if self.embed_check:
                if self.child_pid and self._terminal_in_well():
                    return True
                if now - started > 8:
                    raise RuntimeError(
                        f"VTE window was not placed in the glass well "
                        f"(parent={self.renderer.window_parent(self.child_xid)} "
                        f"glass={self.renderer.x11_window()} rect={self.renderer.window_rect(self.child_xid)} pid={self.child_pid})"
                    )
        return False

    def _install_audio_drop(self, term):
        """Let audio files dropped on the player window start playback."""
        from liquid_audio_drop import AudioDropBridge, host_accepts_audio_drops
        if not host_accepts_audio_drops(self.argv):
            return
        self.audio_drop = AudioDropBridge()
        self.audio_drop.attach(term)

    def _gtk_terminal_window(self):
        icon_path = ROOT / "assets" / "mp_liquid_glass_icon.png"
        window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        if icon_path.is_file():
            try:
                Gtk.Window.set_default_icon_from_file(str(icon_path))
                window.set_icon_from_file(str(icon_path))
            except Exception:
                pass
        window.set_decorated(False)
        window.set_skip_taskbar_hint(True)
        window.set_skip_pager_hint(True)
        window.set_accept_focus(True)
        window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        window.set_app_paintable(True)
        visual = window.get_screen().get_rgba_visual()
        if visual is not None:
            window.set_visual(visual)
        return window

    def _apply_glass_foreground(self, term, theme_name):
        """Default ink. Pale glass needs a dark foreground; charcoal keeps a light one."""
        if Gdk is None or not hasattr(term, "set_color_foreground"):
            return
        red, green, blue = glass_foreground_rgb(theme_name)
        term.set_color_foreground(Gdk.RGBA(red / 255.0, green / 255.0, blue / 255.0, 1.0))

    def _clear_terminal_background(self, window, term):
        if hasattr(term, "set_color_background") and Gdk is not None:
            term.set_color_background(Gdk.RGBA(0, 0, 0, 0))
        self._apply_glass_foreground(term, read_player_theme())
        # VTE needs both: `set_bold_is_bright` stops bold text from switching to
        # the bright palette, and `set_allow_bold` actually disables the bold
        # face so the glass pane is not punched through by heavy glyphs.
        if hasattr(term, "set_bold_is_bright"):
            term.set_bold_is_bright(False)
        if hasattr(term, "set_allow_bold"):
            term.set_allow_bold(False)
        self._set_terminal_font_scale(getattr(self, "_font_scale", 1.0))
        try:
            import cairo, gi
            gi.require_version("PangoCairo", "1.0")
            from gi.repository import PangoCairo
            fo = cairo.FontOptions()
            fo.set_antialias(cairo.ANTIALIAS_GRAY)
            fo.set_hint_style(cairo.HINT_STYLE_SLIGHT)
            fo.set_hint_metrics(cairo.HINT_METRICS_ON)
            if window.get_screen() is not None:
                window.get_screen().set_font_options(fo)
            if term.get_pango_context() is not None:
                PangoCairo.context_set_font_options(term.get_pango_context(), fo)
        except Exception:
            pass
        provider = Gtk.CssProvider()
        # Mac OS Style Drop Shadows applied to typography
        provider.load_from_data(
            b"vte-terminal, window { background-color: transparent; padding: 0px; margin: 0px; font-weight: normal; "
            b"text-shadow: 1px 2px 3px rgba(0, 0, 0, 0.85), 0 0 5px rgba(0, 229, 255, 0.35); letter-spacing: 0.35px; }"
        )
        term.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        window.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._glass_css_provider = provider

    def _apply_slider_opacity(self):
        """Keep terminal text opaque; the GL shader applies the glass opacity."""
        if self.gtk_window is None:
            return
        try:
            self.gtk_window.set_opacity(1.0)
        except Exception:
            pass

    def _sync_visualizer_readability(self):
        """Keep player text solid and outlined while the visualizer is the background."""
        if self.gtk_window is None or not getattr(self.renderer, "allow_player_visualizer", False):
            return
        try:
            reader = getattr(self, "_read_vis_state", None)
            if reader is None:
                from liquid_player_visualizer import read_player_visualizer_state as reader
                self._read_vis_state = reader
            state = reader()
            # The outline exists to keep text readable over the scene. When
            # the Visualization button hides the window the pane is ordinary
            # glass again, so the outline goes with it.
            active = bool(state.get("active")) and state.get("visible") is not False
        except Exception:
            return
        if active == getattr(self, "_vis_text_on", False):
            return
        if self.terminal is None or Gtk is None:
            return
        self._vis_text_on = active
        context = self.terminal.get_style_context()
        previous = getattr(self, "_vis_text_css", None)
        if previous is not None:
            context.remove_provider(previous)
            self._vis_text_css = None
        if not active:
            return
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b"vte-terminal { background-color: transparent; font-weight: normal; "
            b"text-shadow: 0 0 2px #000000, 1px 1px 0 #000000, -1px 1px 0 #000000, "
            b"1px -1px 0 #000000, -1px -1px 0 #000000, 0 2px 4px rgba(0,0,0,0.95); }"
        )
        context.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self._vis_text_css = provider

    def _sync_windows_drag(self) -> None:
        if sys.platform != "win32" or self.renderer is None:
            return
        from windows_glass_drag import install_on_host
        install_on_host(self)

    def _pump_windows_drag(self) -> None:
        if sys.platform != "win32":
            return
        from windows_glass_drag import release_stuck_mouse
        release_stuck_mouse(self.renderer)
        drag = getattr(self, "_glass_drag", None)
        if drag is not None:
            drag.update()

    def _release_windows_drag(self) -> None:
        if sys.platform != "win32":
            return
        from windows_glass_drag import release_on_host
        release_on_host(self)

    def close(self):
        if self.mode == "NORMAL":
            try:
                from liquid_player_visualizer import preset_by_id, read_player_visualizer_state, write_player_visualizer_state
                state = read_player_visualizer_state()
                if state.get("active"):
                    preset = preset_by_id(int(state.get("preset_id", 0))) or {"id": 0, "key": "cosmic_dreamworlds_liquid", "name": "Cosmic Dreamworlds (Liquid)"}
                    write_player_visualizer_state(False, preset)
            except Exception:
                pass
        self._release_windows_drag()
        pid, self.child_pid = self.child_pid, 0
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        window, self.gtk_window, self.terminal = self.gtk_window, None, None
        if window is not None:
            window.destroy()
            if self.glib:
                self._pump_gtk(self.glib)
        renderer, self.renderer = self.renderer, None
        if renderer is not None:
            renderer.close()


def _visualizer_env(companion: bool = False) -> dict:
    """Environment for a spawned visualizer process.

    _import_gtk() forces two GTK/X11-specific variables so the glass window can
    be parented by XID: GDK_BACKEND=x11 and PYOPENGL_PLATFORM=glx. Both are
    correct for this GTK host but fatal for the visualizer child, which uses
    GLFW and its own GL context:

      GDK_BACKEND=x11        -> "libdecor-gtk-WARNING: Failed to initialize GTK"
      PYOPENGL_PLATFORM=glx  -> PyOpenGL binds GLX, gets no context, and raises
                                "Attempt to retrieve context when no valid context"

    The child then dies before drawing and no window ever appears. Strip both so
    it negotiates Wayland/EGL itself.

    ``companion=True`` marks the child as the window the player owns. That
    child is bonded behind the player for its whole life. Extra windows opened
    later (Z, the Manager) leave the flag off and are explicitly detached, so
    they never latch onto the player just because a companion is running.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("GDK_BACKEND", "PYOPENGL_PLATFORM",
                        "MP_LIQUID_GLASS", "MP_LIQUID_GLASS_BACKEND",
                        "MP_VISUALIZER_COMPANION", "MP_VISUALIZER_DETACHED",
                        "MP_VISUALIZER_MATCH_GUI")}
    if companion:
        env["MP_VISUALIZER_COMPANION"] = "1"
        # An MP Gui companion belongs behind MP Gui, not behind whichever
        # Liquid Player is open. The flag selects the GUI record and the GUI
        # window for geometry and stacking.
        if os.environ.get("MP_GUI_SESSION") == "1" or os.environ.get("MP_LIQUID_GLASS_MANAGER") == "1":
            env["MP_VISUALIZER_MATCH_GUI"] = "1"
    else:
        env["MP_VISUALIZER_DETACHED"] = "1"
    return env


def spawn_new_visualizer() -> bool:
    """Open a new MP Liquid Visualizer window. Pressing Z must always show one.

    Unlike ensure_random_visualizer_started this never consults the running
    processes: the window may have been closed by hand, in which case Z has
    to bring it back.
    """
    try:
        try:
            from liquid_visualizer_session import (
                MAX_VISUALIZER_WINDOWS,
                visualizer_window_count,
                visualizer_window_limit_reached,
            )
        except ImportError:
            from scripts.liquid_visualizer_session import (
                MAX_VISUALIZER_WINDOWS,
                visualizer_window_count,
                visualizer_window_limit_reached,
            )
        try:
            limit_hit = visualizer_window_limit_reached(exclude_pid=os.getpid())
        except Exception:
            limit_hit = False
        if limit_hit:
            with open(CONFIG_DIR / "liquid_visualizer.log", "a") as f:
                f.write(f"[spawn_new_visualizer] {MAX_VISUALIZER_WINDOWS} windows "
                        "already open; refusing another.\n")
            return False
        vis_script = next((p for p in (ROOT / "MP_Liquid_Visualizer.py", ROOT / "scripts" / "mp_liquid_visualizer_vulkan.py") if p.is_file()), None)
        if not vis_script:
            return False
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        extra_kw = {"creationflags": 0x00000208} if sys.platform == "win32" else {"start_new_session": True}
        # Z opens an extra window: it is explicitly detached from the
        # player, while the companion the player starts stays locked.
        extra_kw["env"] = _visualizer_env(companion=False)
        try:
            open_windows = visualizer_window_count(exclude_pid=os.getpid())
        except Exception:
            open_windows = 0
        extra_kw["env"]["MP_VISUALIZER_CASCADE_INDEX"] = str(
            min(max(0, open_windows), MAX_VISUALIZER_WINDOWS - 1))
        with open(CONFIG_DIR / "liquid_visualizer.log", "a") as f:
            f.write(f"\n--- spawn_new_visualizer: {vis_script.name} ---\n")
            f.flush()
            subprocess.Popen([sys.executable, str(vis_script), "--preset", "random", "--backend", "opengl"], cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=f, stderr=f, **extra_kw)
        return True
    except Exception as exc:
        try:
            with open(CONFIG_DIR / "liquid_visualizer.log", "a") as f:
                f.write(f"[spawn_new_visualizer] failed: {exc!r}\n")
        except Exception:
            pass
        return False


def _companion_already_running() -> bool:
    """True when the companion this host owns is already on screen."""
    try:
        from liquid_player_visualizer import companion_visualizer_running
    except ImportError:
        from scripts.liquid_player_visualizer import companion_visualizer_running
    try:
        return bool(companion_visualizer_running(
            os.environ.get("MP_GUI_SESSION") == "1"))
    except Exception:
        return False


def _host_is_manager() -> bool:
    """True when this host is MP Gui rather than the MP Liquid Player."""
    return (os.environ.get("MP_GUI_SESSION") == "1"
            or os.environ.get("MP_LIQUID_GLASS_MANAGER") == "1")


def ensure_random_visualizer_started() -> bool:
    try:
        from liquid_visualizer_session import companion_visualizer_enabled
        if not companion_visualizer_enabled():
            return False
    except ImportError:
        try:
            from scripts.liquid_visualizer_session import companion_visualizer_enabled
            if not companion_visualizer_enabled():
                return False
        except Exception:
            pass
    except Exception:
        pass
    try:
        if sys.platform == "win32":
            try:
                out = subprocess.check_output(
                    ["powershell.exe", "-NoProfile", "-Command",
                     'Get-CimInstance Win32_Process | Where-Object { $_.Name -like "python*" -and ($_.CommandLine -like "*MP_Liquid_Visualizer*" -or $_.CommandLine -like "*mp_liquid_visualizer_vulkan*") } | Select-Object -ExpandProperty ProcessId'],
                    stderr=subprocess.DEVNULL, text=True,
                    creationflags=0x08000000,
                ).strip()
                if out:
                    return False
            except Exception:
                pass
        elif _companion_already_running():
            # Only *this* host's companion counts. A bare pgrep for any
            # visualizer made MP Gui skip its own spawn whenever a Liquid
            # Player was already up, which is why its background window went
            # missing.
            return False

        vis_script = next((p for p in (ROOT / "MP_Liquid_Visualizer.py", ROOT / "scripts" / "mp_liquid_visualizer_vulkan.py") if p.is_file()), None)
        if not vis_script:
            return False

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # Write the active state before the child starts, so its first poll
        # already knows it belongs behind this player. Only the Liquid Player
        # resets ``visible``: MP Gui must not un-hide a companion the player
        # deliberately turned off.
        try:
            from liquid_player_visualizer import mark_player_visualizer_attached
            mark_player_visualizer_attached(
                visible=None if _host_is_manager() else True)
        except Exception:
            pass
        extra_kw = {"creationflags": 0x00000208} if sys.platform == "win32" else {"start_new_session": True}
        # Windows included. Without this env the companion never bonds.
        extra_kw["env"] = _visualizer_env(companion=True)
        with open(CONFIG_DIR / "liquid_visualizer.log", "a") as f:
            subprocess.Popen([sys.executable, str(vis_script), "--preset", "random", "--backend", "opengl"], cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=f, stderr=f, **extra_kw)
        return True
    except Exception:
        return False


def main(argv):
    # Raspberry Pi performance defaults (raymarch resolution, march steps,
    # Bluetooth polling). No-op on a desktop GPU, and an explicit environment
    # value always wins.
    mp_raspberry_pi.apply_default_env()
    if "--self-test" in argv:
        from liquid_glass_opengl import run_self_test
        run_self_test()
        return 0
    if "--list-fonts" in argv:
        list_liquid_glass_fonts()
        return 0

    embed = "--embed-check" in argv
    manager = "--manager" in argv
    tracklist_path = audio_path = theme_arg = font_arg = None
    cleaned_argv = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--no-visualizer", "--manager"):
            i += 1
        elif arg in ("--tracklist", "--audio", "--theme", "-t", "--font", "-f", "--set-font") and i + 1 < len(argv):
            val = argv[i + 1]
            if arg == "--set-font":
                set_liquid_glass_font(val)
                print(f"✔ MP Liquid Glass Player default font set to: {val}")
                return 0
            if arg == "--tracklist":
                tracklist_path = val
            elif arg == "--audio":
                audio_path = val
            elif arg in ("--theme", "-t"):
                theme_arg = val
            else:
                font_arg = val
            i += 2
        elif arg.startswith(("--tracklist=", "--audio=", "--theme=", "--font=", "--set-font=")):
            k, val = arg.split("=", 1)
            if k == "--set-font":
                set_liquid_glass_font(val)
                print(f"✔ MP Liquid Glass Player default font set to: {val}")
                return 0
            if k == "--tracklist":
                tracklist_path = val
            elif k == "--audio":
                audio_path = val
            elif k == "--theme":
                theme_arg = val
            else:
                font_arg = val
            i += 1
        else:
            cleaned_argv.append(arg)
            i += 1

    player_gui = not embed and not tracklist_path
    # MP Gui and MP Liquid Player stop at four windows. The tracklist viewer,
    # the embed check, and helper flags are not counted.
    if player_gui:
        counted_role = "gui" if manager else "player"
        if not host_allowed(counted_role):
            # A shortcut launch discards stderr, so the refusal used to look
            # like nothing happened at all. Say it on the desktop as well.
            notify_limit(counted_role)
            return 1
    mode = "MINI" if "--mini" in argv else ("MICRO" if "--micro" in argv else ("VERTICAL" if "--vertical" in argv else "NORMAL"))
    # Mark the MP Gui session before the companion is spawned: _visualizer_env
    # reads MP_GUI_SESSION to give that child MP_VISUALIZER_MATCH_GUI=1, which
    # is what makes it bond behind MP Gui instead of behind a Liquid Player.
    if manager and not embed and not tracklist_path:
        mode = "MANAGER"
        os.environ["MP_GUI_SESSION"] = "1"
        os.environ["MP_LIQUID_GLASS"] = "1"
    # The first MP Gui and the first MP Liquid Player each play
    # Exit_MP_Video.mp4 once, before the glass and its companion visualizer.
    # The movie window closes when the clip ends. A second window of that
    # same kind never shows it.
    if player_gui:
        play_player_intro(mode)
    if player_gui and "--no-visualizer" not in argv:
        ensure_random_visualizer_started()

    if manager and not embed and not tracklist_path:
        command = manager_argv()
        if not Path(command[1]).is_file():
            raise RuntimeError(f"MP Mix Manager was not found at {command[1]}")
    elif tracklist_path and not embed:
        mode = "TRACKLIST"
        command = tracklist_argv(tracklist_path, audio_path=audio_path, theme=theme_arg)
        if not Path(command[1]).is_file():
            raise RuntimeError(f"Tracklist viewer script was not found at {command[1]}")
    elif embed:
        command = embed_check_argv()
    else:
        extra = [a for a in cleaned_argv if a not in ("--self-test", "--embed-check", "--mini", "--micro")]
        if theme_arg and not any(a == "--theme" or a.startswith("--theme=") for a in extra):
            extra.extend(["--theme", theme_arg])
        command = player_argv(mode=mode, extra_args=extra)
        if not Path(command[1]).is_file():
            raise RuntimeError(f"MP Audio Player was not found at {command[1]}")

    try:
        ok = GlassHost(command, embed_check=embed, mode=mode, cli_theme=theme_arg, font=font_arg).run()
        if embed:
            if not ok:
                raise RuntimeError("VTE window was not placed in the glass well")
            print("LIQUID_GLASS_EMBED_OK", flush=True)
        return 0
    finally:
        # The companion is already retired by _close_then_exit_video(); this
        # catches a raise before it gets there. An embed check must not touch
        # a companion at all.
        if not embed:
            stop_companion_for_mode(mode, player_gui=player_gui)


def _report_startup_failure(exc: BaseException) -> None:
    """Leave a trace when the host dies before a window appears.

    The Windows launchers start this host with pythonw.exe, so there is no
    console and stderr goes nowhere: a missing glfw3.dll or a missing module
    looked exactly like "Launch MP Gui does nothing". Write the traceback next
    to the other MP logs and, on Windows, put the message on screen as well.
    """
    message = f"{type(exc).__name__}: {exc}"
    log = CONFIG_DIR / "mp_gui.log"
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        detail = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(
                f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] launch failed\n{detail}"
            )
    except OSError:
        pass
    if sys.platform == "win32" and not os.environ.get("MP_GUI_NO_DIALOG"):
        try:
            ctypes.windll.user32.MessageBoxW(
                None,
                f"MP Gui could not start.\n\n{message}\n\n"
                f"The full error was written to:\n{log}",
                "MP Mix Manager",
                0x10,
            )
        except Exception:
            pass


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as exc:
        print(f"Liquid Glass (OpenGL): {exc}", file=sys.stderr)
        _report_startup_failure(exc)
        raise SystemExit(1)
