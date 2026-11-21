#!/usr/bin/env python3
"""NVIDIA Vulkan liquid-glass window hosting the unmodified MP Audio Player."""
from __future__ import annotations
import ctypes, os, shutil, signal, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from desktop_launch_screen import window_launch_origin  # noqa: E402
from liquid_glass_font import default_liquid_glass_font  # noqa: E402
from liquid_glass_opengl import glass_foreground_rgb  # noqa: E402
from liquid_glass_vulkan import LiquidGlassRenderer, NVIDIA_VENDOR_ID, read_player_theme  # noqa: E402
try:  # The player keeps a slightly shorter bottom strip so its hints sit lower.
    from liquid_glass_slider import player_slider_strip  # noqa: E402
except ImportError:
    def player_slider_strip(_cell_height=0.0):  # type: ignore[misc]
        return 44
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

COLS, ROWS = 120, 40
# The portrait deck. It is the size M reaches first from the full window: one
# narrow column, so the glass rectangle is taller than it is wide.
VERTICAL_COLS, VERTICAL_ROWS = 56, 52


# Used when Hack is not installed. Linux uses Hack 11 when that family is present.
DEFAULT_FONT = "Monospace 11"
CONFIG_DIR = Path.home() / ".config" / "mix-manager"
FONT_CONFIG_FILE = CONFIG_DIR / "liquid_glass_font"
# The tracklist viewer draws its own viewport, but VTE still needs a scrollback
# buffer so wheel and scrollbar events are delivered to the child terminal.
TRACKLIST_SCROLLBACK = 2000
IS_WIN = sys.platform == "win32"

RECOMMENDED_FONTS = [
    ("Monospace 11", "Fallback when the Hack family is not installed"),
    ("Fira Code 11", "Modern Programming Font (Clean shapes, sleek proportions)"),
    ("Hack 11", "Linux default when the Hack family is installed"),
    ("Adwaita Mono 11", "Modern GNOME Desktop Monospace"),
    ("Liberation Mono 11", "Classic Clean Monospace"),
]


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
    c_head = cur.split()[0]
    print("\n\033[1;38;2;0;229;255m══ MP Liquid Glass Player — Available & Recommended Fonts ══\033[0m\n")
    for idx, (f_name, desc) in enumerate(RECOMMENDED_FONTS, 1):
        star = "\033[1;38;2;0;255;127m★ ACTIVE\033[0m" if (f_name == cur or f_name.split()[0] == c_head) else "        "
        print(f"  {star} \033[1;38;2;255;215;0m[{idx}]\033[0m \033[1;38;2;255;255;255m{f_name:<20s}\033[0m — {desc}")
    print(f"\n  \033[38;2;120;215;255mActive Font:\033[0m {cur}\n\n  \033[38;2;200;200;200mTo try a font on launch:\033[0m\n"
          '    python3 MP_Audio_Player_Liquid_Glass.py --font "Fira Code 11"\n'
          '    python3 MP_Audio_Player_Liquid_Glass.py --font "Hack 11"\n'
          '    python3 MP_Audio_Player_Liquid_Glass.py --font "Monospace 11"\n\n'
          "  \033[38;2;200;200;200mTo set a font as your permanent default:\033[0m\n"
          '    python3 MP_Audio_Player_Liquid_Glass.py --set-font "Fira Code 11"\n')


def player_panel_rects(cols, rows, cw, ch, ox, oy, mode="NORMAL"):
    b = lambda c, r, bc, br: (ox + c * cw, oy + r * ch, bc * cw, br * ch)
    iw = max(20, cols - 4)
    if mode in ("MICRO", "MANAGER"):
        return b(1, 0, max(20, cols - 2), rows), (0.0, 0.0, 0.0, 0.0)
    if mode == "VERTICAL":
        # Vertical is the full player frame in a portrait column, so the glass
        # covers the same two boxes it draws: the header block on top, and the
        # playlist box below the analyser. The analyser takes whatever height
        # the fixed rows leave, so the playlist's first row follows the window.
        # The offset is measured from the drawn frame at every height.
        iw = max(20, cols - 6)
        playlist_top = max(5, rows - 20)
        playlist_rows = max(2, rows - playlist_top)
        return b(2, 0, iw + 2, 4), b(2, playlist_top, iw + 2, playlist_rows)
    if mode == "MINI":
        return b(2, 0, iw, 3), b(2, 3, iw, max(1, rows - 3))
    if mode == "TRACKLIST":
        return (b(2, 0, iw, rows), (0.0, 0.0, 0.0, 0.0)) if rows <= 7 else (b(2, 0, iw, 7), b(2, 7, iw, max(1, rows - 7)))
    iw = max(20, cols - 6)
    before = 14 + (7 if rows >= 34 else (5 if rows >= 28 else 3))
    return b(2, 0, iw + 2, 4), b(2, before, iw + 2, max(1, rows - before - 5) + 2)


def _mode_grid(mode):
    """Terminal grid the host gives one mode."""
    mode = str(mode or "").strip().upper()
    if mode == "TRACKLIST":
        return 100, 35
    if mode == "VERTICAL":
        return VERTICAL_COLS, VERTICAL_ROWS
    if mode == "MINI":
        return 74, 14
    if mode == "MICRO":
        return 74, 7
    return COLS, ROWS


def _import_gtk():
    # Read the live platform, not the IS_WIN snapshot taken at import time.
    # The host must be able to answer "what would happen on Windows?" (the
    # cross-platform tests do exactly that), and on Windows this is the guard
    # that stops a Linux GTK import from being attempted at all.
    if IS_WIN or sys.platform == "win32":
        return None, None, None
    os.environ["GDK_BACKEND"] = "x11"
    from local_vte import reexec_with_local_vte
    import gi
    try:
        for mod, ver in (("Gdk", "3.0"), ("Gtk", "3.0"), ("GdkX11", "3.0"), ("Vte", "2.91")):
            gi.require_version(mod, ver)
    except ValueError:
        reexec_with_local_vte()
        raise
    from gi.repository import GdkX11, GLib, Gtk, Vte
    if not hasattr(GdkX11, "X11Display"):
        raise RuntimeError("GDK X11 backend is required for the liquid-glass window")
    return GLib, Gtk, Vte


def manager_argv():
    from windows_bash import find_bash
    return [find_bash(), str(ROOT / "Mix_Archive_Manager.sh")]


def player_argv(mode="NORMAL", extra_args=None):
    cmd = [sys.executable, str(ROOT / "MP_Audio_Player.py"), "--interactive"]
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
    env.update(TERM="xterm-256color", COLORTERM="truecolor", MP_LIQUID_GLASS="1",
               MP_LIQUID_GLASS_BACKEND="vulkan", MP_LIQUID_GLASS_PLATFORM="win32" if IS_WIN else "linux")
    # W opens an MP Black Glass pane at exactly this window's size; the child
    # is told which published geometry record is the window it is drawn in.
    env["MP_HOST_PID"] = str(os.getpid())
    return env if as_dict else [f"{k}={v}" for k, v in env.items()]


class GlassHost:
    def __init__(self, argv, embed_check=False, mode="NORMAL", cli_theme=None, font=None):
        self.argv = list(argv)
        self.embed_check = embed_check
        self.mode = mode.upper()
        self.cli_theme = cli_theme
        self.font_name = get_liquid_glass_font(font)
        self.renderer = self.gtk_window = self.terminal = self.glib = self.spawn_error = self._saved_rect = self._last_term_size = None
        self.child_xid = self.child_pid = self._last_top_click_x = self._last_top_click_y = 0
        self.child_done = self.is_maximized = self._term_dragging = False
        self.audio_drop = None
        self.slider = None
        self.clear_opacity = 0.70
        self._last_top_click = 0.0
        if self.mode == "TRACKLIST":
            self._target_cols, self._target_rows = 100, 35
        elif self.mode == "VERTICAL":
            self._target_cols, self._target_rows = VERTICAL_COLS, VERTICAL_ROWS
        elif self.mode in ("MINI", "MICRO"):
            self._target_cols, self._target_rows = 74, (14 if self.mode == "MINI" else 7)
        else:
            self._target_cols, self._target_rows = COLS, ROWS

    def _char_dims(self):
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

    def _on_close(self, x, y, w, h):
        m = max(float(self.renderer.bezel_px), 8.0)
        r = max(m * 0.30, 4.0)
        return (x - (w - m * 0.72)) ** 2 + (y - m * 0.62) ** 2 <= r * r

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
        _cw, ch = self._char_dims()
        return player_slider_strip(ch)

    def _well_rect(self):
        gx, gy = self.renderer.window_pos()
        w, h = self.renderer.window_size()
        m = self.renderer.bezel_px
        # Keep the bottom strip free for the transparency slider, exactly as
        # the OpenGL host does. Without it the terminal covers the slider and
        # the bar appears to have been removed.
        return gx + m, gy + m, max(32, w - 2 * m), max(32, h - 2 * m - self._slider_strip())

    def _place_terminal(self):
        x, y, iw, ih = self._well_rect()
        if self.gtk_window is not None:
            ix, iy, iiw, iih = int(x), int(y), int(iw), int(ih)
            self.gtk_window.move(ix, iy)
            if self._last_term_size != (iiw, iih):
                self.gtk_window.resize(iiw, iih)
                self._last_term_size = (iiw, iih)
        self.renderer.place_terminal(self.child_xid, x, y, iw, ih)
        self._place_slider()
        self._sync_panels()

    def _place_slider(self):
        """Keep the transparency bar on the strip reserved at the bottom."""
        slider = getattr(self, "slider", None)
        if slider is None or not slider.available or self.renderer is None:
            return
        try:
            gx, gy = self.renderer.window_pos()
            w, h = self.renderer.window_size()
            slider.place(int(gx), int(gy), int(w), int(h), strip=self._slider_strip())
        except Exception:
            pass

    def _on_slider_change(self, opacity):
        """Apply a drag on the transparency bar to the glass window.

        The opacity has to be written to the *renderer* as well as to this
        host. `draw()` recomputes the X11 property from `renderer.clear_opacity`
        on every frame for the dynamic themes, and the renderer keeps its own
        copy of the value, so updating only `self.clear_opacity` here was
        overwritten within one frame - which is why moving the slider left to
        right left the window at exactly the same transparency.
        """
        try:
            self.clear_opacity = float(opacity)
        except Exception:
            return
        renderer = self.renderer
        if renderer is None:
            return
        try:
            renderer.clear_opacity = float(opacity)
        except Exception:
            pass
        # The dynamic themes scale the property by an oscillation factor, and
        # `draw()` only writes it when that factor changes. Reset the memo so
        # the very next frame republishes the new base value immediately.
        try:
            renderer._last_dyn_opacity = None
        except Exception:
            pass
        win = 0
        try:
            win = int(renderer.x11_window() or 0)
        except Exception:
            win = 0
        if win:
            try:
                renderer._set_opacity(win, float(opacity))
            except Exception:
                pass

    def _attach_slider(self, Gtk, Gdk):
        """Create the transparency bar overlay for this window."""
        try:
            from liquid_glass_slider import DEFAULT_OPACITY, GlassSlider
        except ImportError:
            from scripts.liquid_glass_slider import DEFAULT_OPACITY, GlassSlider
        opacity = float(getattr(self, "clear_opacity", DEFAULT_OPACITY) or DEFAULT_OPACITY)
        slider = GlassSlider(opacity=opacity, on_change=self._on_slider_change)
        if slider.attach(Gtk, Gdk, parent=self.gtk_window):
            self.slider = slider
            try:
                from liquid_glass_opengl import get_dynamic_theme_state, read_player_theme
                colors, _, _ = get_dynamic_theme_state(read_player_theme())
                slider.set_theme(colors)
            except Exception:
                pass
            return True
        self.slider = None
        return False


    def _sync_panels(self):
        r = self.renderer
        if r is None:
            return
        cw, ch = self._char_dims()
        m = r.bezel_px
        cols, rows = self._target_cols, self._target_rows
        t = self.terminal
        if t is not None:
            tc, tr = int(t.get_column_count() or 0), int(t.get_row_count() or 0)
            if tc >= 20:
                cols = tc
            if tr >= 5:
                rows = tr
        r.set_panels(player_panel_rects(cols, rows, cw, ch, m, m, mode=self.mode))

    def toggle_maximize(self):
        if self.renderer is None:
            return
        cw, ch = self._char_dims()
        m = self.renderer.bezel_px
        if not self.is_maximized:
            self._saved_rect = (self.renderer.window_pos(), self.renderer.window_size())
            wx, wy, ww, wh = self.renderer.get_workarea()
            cols, rows = max(40, int((ww - 2 * m) // cw)), max(10, int((wh - 2 * m) // ch))
            self._target_cols, self._target_rows = cols, rows
            self.renderer.set_window_pos(wx, wy)
            self._note_player_window(ww, wh)
            self.renderer.set_window_size(ww, wh)
            self.renderer.poll()
            if self.terminal is not None:
                self.terminal.set_size(cols, rows)
            self._place_terminal()
            self.is_maximized = True
        else:
            pos, _ = self._saved_rect or (window_launch_origin(72, 64), (1280, 820))
            cols, rows = _mode_grid(self.mode)
            self._target_cols, self._target_rows = cols, rows
            self.renderer.set_window_pos(pos[0], pos[1])
            restore_w, restore_h = cols * cw + 2 * m, rows * ch + 2 * m
            self._note_player_window(restore_w, restore_h)
            self.renderer.set_window_size(restore_w, restore_h)
            self.renderer.poll()
            if self.terminal is not None:
                self.terminal.set_size(cols, rows)
            self._place_terminal()
            self.is_maximized = False

    def set_mode(self, mode: str):
        self.mode = mode.upper()
        if self.renderer is None:
            return
        self.is_maximized = False
        cols, rows = _mode_grid(self.mode)
        self._target_cols, self._target_rows = cols, rows
        cw, ch = self._char_dims()
        m = self.renderer.bezel_px
        mode_w, mode_h = cols * cw + 2 * m, rows * ch + 2 * m
        self._note_player_window(mode_w, mode_h)
        self.renderer.set_window_size(mode_w, mode_h)
        if self.terminal is not None:
            self.terminal.set_size(cols, rows)
        self._place_terminal()

    def _gui_height_extra(self) -> int:
        """The Vulkan host folds the bar into the bezel, so the text grid uses the whole height."""
        return 0

    def _remember_gui_normal_metrics(self) -> None:
        if getattr(self, "_gui_normal_px", None) or self.renderer is None:
            return
        cw, ch = self._char_dims()
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

    def _apply_gui_grid(self, cols: int, rows: int, size=None) -> None:
        cw, ch = self._char_dims()
        margin = self.renderer.bezel_px
        self._target_cols, self._target_rows = cols, rows
        width, height = size or (cols * cw + 2 * margin, rows * ch + 2 * margin)
        self._note_player_window(width, height)
        self.renderer.set_window_size(width, height)
        self.renderer.poll()
        if self.terminal is not None:
            self.terminal.set_size(cols, rows)
        self._place_terminal()
        self._recenter_window(width, height)

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
            self._apply_gui_grid(cols, rows, (width, height))
        else:
            self._apply_gui_grid(COLS, ROWS)
        self._gui_shape = shape
        self.is_maximized = False

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
        from liquid_ui_size import read_ui_size
        mode = read_ui_size(CONFIG_DIR, int(getattr(self, "child_pid", 0) or 0))
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
        kv, st = getattr(event, "keyval", 0), getattr(event, "state", 0)
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
        if kv == Gdk.KEY_K or (kv in (Gdk.KEY_k, Gdk.KEY_K) and bool(st & Gdk.ModifierType.SHIFT_MASK)):
            # Stop only this user's players, matched on argv. A bare
            # `pkill -9 -f` also killed editors that had the file open and
            # other users' players.
            _stop_other_players()
            return True
        if self._claims_hidden_mode_key(kv, st):
            _toggle_hidden_mode()
            return True
        if kv in (Gdk.KEY_z, Gdk.KEY_Z):
            spawn_new_visualizer()
            return True
        # Escape stays with the child. The player uses it to leave search and
        # the inspector, and MP Gui uses it to step back. Closing the glass
        # here threw those screens away.
        return False

    def _terminal_in_well(self):
        if not self.child_xid or not self.renderer or self.renderer.window_parent(self.child_xid) == self.renderer.x11_window():
            return False
        return all(abs(a - b) <= 8 for a, b in zip(self.renderer.window_rect(self.child_xid), self._well_rect()))

    def _on_spawn(self, _t, pid, error, *_):
        if error:
            self.spawn_error = str(error)
            if self.renderer is not None:
                self.renderer.request_close()
        else:
            self.child_pid = int(pid or 0)
            if self.audio_drop is not None:
                self.audio_drop.mark_ready()

    def _on_child_exit(self, *_):
        self.child_done = True
        if self.renderer is not None:
            self.renderer.request_close()

    def _pump_gtk(self, glib):
        ctx = glib.MainContext.default()
        pending, step = ctx.pending, ctx.iteration
        while pending():
            step(False)

    def _on_term_button_press(self, _term, event):
        from gi.repository import Gdk
        if event.button == 1:
            if event.type == getattr(Gdk.EventType, "_2BUTTON_PRESS", 5) and event.y <= 90:
                self.toggle_maximize()
                self._term_dragging = False
                return True
            mods = getattr(Gdk.ModifierType, "MOD1_MASK", 8) | getattr(Gdk.ModifierType, "SUPER_MASK", 0x4000000)
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
            ox, oy = self._term_drag_origin
            px, py = self._term_drag_press
            self.renderer.set_window_pos(int(ox + cx - px), int(oy + cy - py))
            self._place_terminal()
            return True
        return False

    def _on_term_button_release(self, _term, event):
        if event.button == 1:
            self._term_dragging = False
        return False

    def _track_drag(self, was_down, dragging, origin, press_screen):
        if getattr(self, "_os_dragging", False):
            return False, False, origin, press_screen
        r = self.renderer
        x, y = r.cursor
        w, h = r.window_size()
        down = r.mouse_down
        if down and not was_down:
            if self._on_close(x, y, w, h):
                r.request_close()
                dragging = False
            elif self._on_top_bezel(x, y, w, h):
                now = time.monotonic()
                if now - self._last_top_click < 0.45 and abs(x - self._last_top_click_x) < 35 and abs(y - self._last_top_click_y) < 35:
                    self.toggle_maximize()
                    dragging, self._last_top_click = False, 0.0
                else:
                    self._last_top_click, self._last_top_click_x, self._last_top_click_y = now, x, y
                    if not self.is_maximized:
                        dragging, origin = True, r.window_pos()
                        rx, ry = r.root_pointer()
                        press_screen = (rx, ry) if (rx or ry) else (origin[0] + x, origin[1] + y)
            elif self._on_bezel(x, y, w, h) and not self.is_maximized:
                dragging, origin = True, r.window_pos()
                rx, ry = r.root_pointer()
                press_screen = (rx, ry) if (rx or ry) else (origin[0] + x, origin[1] + y)
        elif down and dragging:
            rx, ry = r.root_pointer()
            if rx or ry:
                r.set_window_pos(int(origin[0] + rx - press_screen[0]), int(origin[1] + ry - press_screen[1]))
            else:
                r.set_window_pos(int(origin[0] + x - (press_screen[0] - origin[0])), int(origin[1] + y - (press_screen[1] - origin[1])))
        else:
            dragging = False
        return down, dragging, origin, press_screen

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
        """This host's own X11 window id, or 0 when it cannot be read.

        Published with the bond geometry so the companion stacks against this
        exact window. Without it the visualizer picks the first window whose
        title matches, which is wrong as soon as a Liquid Player and MP Gui are
        both open.
        """
        if not sys.platform.startswith("linux") or self.renderer is None:
            return 0
        try:
            return int(self.renderer.x11_window() or 0)
        except Exception:
            return 0

    def _publish_player_window_geometry(self):
        """Publish the player's position and size for the bonded visualizer.

        Mini and Micro resize the same window, so their geometry is published
        as well. Without it the visualizer kept the Normal geometry and came
        away from the player as soon as M changed the size.

        While a resize is still being applied, the size the host asked for is
        published instead of the half-applied one, so the visualizer moves
        straight to the new mode instead of stepping through it.
        """
        if self.mode == "TRACKLIST" or not sys.platform.startswith("linux") or self.renderer is None:
            return
        try:
            x, y = self.renderer.window_pos()
            width, height = self.renderer.window_size()
            target = getattr(self, "_window_target", None)
            if target and time.monotonic() < getattr(self, "_window_target_until", 0.0):
                width, height = target
            wid = self._host_window_id()
            if self.mode == "MANAGER":
                # MP Gui's companion sits behind MP Gui, so the GUI record needs
                # the position too, not just the size _note_player_window
                # publishes.
                from liquid_player_visualizer import publish_liquid_gui_window_geometry
                publish_liquid_gui_window_geometry(x, y, width, height, wid=wid)
                return
            geom = (int(x), int(y), int(width), int(height), int(wid))
            if getattr(self, "_last_published_geom", None) == geom:
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

    def _fit_to_terminal(self, term):
        cw, ch = self._char_dims()
        m = self.renderer.bezel_px
        width = self._target_cols * cw + 2 * m
        height = self._target_rows * ch + 2 * m
        self._note_player_window(width, height)
        self.renderer.set_window_size(width, height)
        term.set_size(self._target_cols, self._target_rows)
        self._recenter_window(width, height)

    def _recenter_window(self, width, height):
        """Centre the freshly sized window instead of leaving it in the corner."""
        if IS_WIN or self.renderer is None:
            return
        try:
            from desktop_launch_screen import main_window_origin
            x, y = main_window_origin(int(width), int(height), 72, 64)
            self.renderer.set_window_pos(int(x), int(y))
        except Exception:
            pass

    def run(self):
        m = LiquidGlassRenderer.bezel_px
        iw, ih = self._target_cols * 9 + 2 * m, self._target_rows * 18 + 2 * m
        if self.mode == "TRACKLIST":
            base_t = "Mix Tracklist Viewer"
        elif self.mode == "MANAGER":
            base_t = "MP Mix Manager"
        else:
            base_t = "MP Audio Player"
        title = f"{base_t} (Liquid Glass{' - Vulkan' if IS_WIN else ''})"
        # The MP Visualizer background is drawn by the host on both backends.
        # The Manager GUI and the tracklist viewer keep the plain glass.
        allow_visualizer = self.mode not in ("MANAGER", "TRACKLIST") and not getattr(self, "manager", False)
        if IS_WIN:
            self.renderer = LiquidGlassRenderer(iw, ih, title)
            self.renderer.allow_player_visualizer = allow_visualizer
            try:
                return self._run_windows()
            finally:
                self._close_then_exit_video()
        glib, gtk, vte = _import_gtk()
        self.glib = glib
        gtk.init([])
        self.renderer = LiquidGlassRenderer(iw, ih, title)
        self.renderer.allow_player_visualizer = allow_visualizer
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
        if not self.embed_check and self.mode in ("NORMAL", "MANAGER", "VERTICAL", "MINI", "MICRO"):
            # Retire the companion before the movie, not after it: leaving it
            # up put a full-size visualizer on the desktop with no window in
            # front of it for the whole clip. An embed check is only a probe
            # and must never touch a companion the user is watching.
            stop_companion_for_mode(self.mode, player_gui=True)
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
        r = self.renderer
        r.open()
        m = r.bezel_px
        r.set_window_size(self._target_cols * 9 + 2 * m, self._target_rows * 18 + 2 * m)
        self._sync_panels()
        if self.embed_check:
            r.draw(read_player_theme())
            return True

        self._sync_windows_drag()
        proc = subprocess.Popen(self.argv, cwd=str(ROOT), env=_child_env(True), creationflags=0x00000010)
        self.child_pid = proc.pid
        child_hwnd = None
        try:
            u32 = ctypes.windll.user32
            found = []
            @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            def _enum_win(hwnd, _):
                pid = ctypes.c_uint32()
                u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == proc.pid and u32.IsWindowVisible(hwnd):
                    found.append(hwnd)
                return True
            for _ in range(50):
                if proc.poll() is not None:
                    break
                u32.EnumWindows(_enum_win, 0)
                if found:
                    child_hwnd = found[0]
                    break
                time.sleep(0.02)
            if child_hwnd:
                u32.SetWindowLongPtrW(child_hwnd, -16, (u32.GetWindowLongPtrW(child_hwnd, -16) & ~0x00C40000 & ~0x00300000) | 0x80000000)
                glass_hwnd = r.win32_window()
                if glass_hwnd:
                    u32.SetParent(child_hwnd, glass_hwnd)
                from windows_console_scroll import hide_process_console_scrollbar
                hide_process_console_scrollbar(proc.pid, int(child_hwnd))
        except Exception:
            pass

        self.child_xid = child_hwnd or 0
        if child_hwnd:
            self._place_terminal()

        self._sync_windows_drag()
        down = dragging = False
        origin, press_screen, theme, theme_at = r.window_pos(), (0, 0), read_player_theme(), 0.0
        monotonic, sleep, draw, poll = time.monotonic, time.sleep, r.draw, r.poll
        scroll_at = 0.0
        focus_at = 0.0
        try:
            u32.GetForegroundWindow.restype = ctypes.c_void_p
        except Exception:
            pass
        try:
            while not r.should_close and proc.poll() is None:
                now = monotonic()
                if child_hwnd and now >= focus_at:
                    # Keep the keyboard on the console even if Windows
                    # activates the glass after the launch-time focus call.
                    focus_at = now + 0.25
                    if not getattr(self, "_os_dragging", False):
                        try:
                            if int(u32.GetForegroundWindow() or 0) == int(r.win32_window() or 0):
                                r.focus_window(self.child_xid)
                        except Exception:
                            pass
                if child_hwnd and now >= scroll_at:
                    from windows_console_scroll import hide_process_console_scrollbar
                    hide_process_console_scrollbar(proc.pid, int(child_hwnd))
                    scroll_at = now + 0.5
                if now - theme_at > 0.4:
                    theme, theme_at = read_player_theme(), now
                self._follow_child_ui_size()
                self._sync_panels()
                self._pump_windows_drag()
                draw(theme)
                down, dragging, origin, press_screen = self._track_drag(down, dragging, origin, press_screen)
                if dragging:
                    self._place_terminal()
                if not poll():
                    break
                sleep(0.008)
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
        r = self.renderer
        r.open()
        if self.mode == "MANAGER":
            r.arm_escape_close()
        if not IS_WIN and r.vendor_id != NVIDIA_VENDOR_ID:
            raise RuntimeError(r.summary)

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
        self._fit_to_terminal(term)
        r.poll()
        gdk_win = window.get_window()
        self.child_xid = int(gdk_win.get_xid()) if gdk_win is not None else 0
        if self.child_xid <= 0:
            raise RuntimeError("VTE window has no X11 id to place in the glass")
        self._place_terminal()
        r.focus_window(self.child_xid)

        from gi.repository import Gdk, Gtk
        # The transparency bar. The Vulkan pipeline draws the themed pane and
        # has no place to paint a bar, so the bar is a GTK overlay pinned to
        # the strip the terminal well keeps free.
        try:
            attached = self._attach_slider(Gtk, Gdk)
            if not attached:
                print("MP slider: overlay could not be created", file=sys.stderr)
        except Exception as exc:
            self.slider = None
            print(f"MP slider: {exc}", file=sys.stderr)
        term.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.BUTTON1_MOTION_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
        for sig, cb in (("button-press-event", self._on_term_button_press),
                        ("button-release-event", self._on_term_button_release),
                        ("motion-notify-event", self._on_term_motion_notify),
                        ("key-press-event", self._on_key_press),
                        ("child-exited", self._on_child_exit)):
            term.connect(sig, cb)
        self._install_audio_drop(term)

        term.spawn_async(vte.PtyFlags.DEFAULT, str(ROOT), self.argv, _child_env(),
                         glib.SpawnFlags.DEFAULT, None, None, -1, None, self._on_spawn)
        term.grab_focus()
        theme, theme_at, was_down, dragging = read_player_theme(), 0.0, False, False
        origin, press_screen, started = (0, 0), (0.0, 0.0), time.monotonic()
        monotonic, draw = time.monotonic, r.draw
        self._mouse_focus_until = started + 2.5 if os.environ.get("MP_TAKE_MOUSE_FOCUS") == "1" else 0.0
        self._mouse_focus_warp = self._mouse_focus_until > 0.0
        self._mouse_focus_next = 0.0

        while not r.should_close:
            now = monotonic()
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
                    if getattr(self, "slider", None) is not None:
                        try:
                            from liquid_glass_opengl import get_dynamic_theme_state
                            colors, _, _ = get_dynamic_theme_state(theme)
                            self.slider.set_theme(colors)
                        except Exception:
                            pass
            self._follow_child_ui_size()
            self._sync_player_window()
            self._publish_player_window_geometry()
            self._sync_panels()
            draw(theme)
            was_down, dragging, origin, press_screen = self._track_drag(was_down, dragging, origin, press_screen)
            self._place_terminal()
            self._pump_gtk(glib)
            if self.spawn_error:
                raise RuntimeError(self.spawn_error)
            if self.embed_check:
                if self.child_pid and self._terminal_in_well():
                    return True
                if now - started > 8:
                    raise RuntimeError(f"VTE window was not placed in the glass well (parent={r.window_parent(self.child_xid)} glass={r.x11_window()} rect={r.window_rect(self.child_xid)} pid={self.child_pid})")
        return False

    def _install_audio_drop(self, term):
        """Let audio files dropped on the player window start playback."""
        from liquid_audio_drop import AudioDropBridge, host_accepts_audio_drops
        if not host_accepts_audio_drops(self.argv):
            return
        self.audio_drop = AudioDropBridge()
        self.audio_drop.attach(term)

    def _gtk_terminal_window(self):
        from gi.repository import Gdk, Gtk
        icon = ROOT / "assets" / "mp_liquid_glass_icon.png"
        window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        if icon.is_file():
            try:
                Gtk.Window.set_default_icon_from_file(str(icon))
                window.set_icon_from_file(str(icon))
            except Exception:
                pass
        window.set_decorated(False)
        window.set_skip_taskbar_hint(True)
        window.set_skip_pager_hint(True)
        window.set_accept_focus(True)
        window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        window.set_app_paintable(True)
        vis = window.get_screen().get_rgba_visual()
        if vis is not None:
            window.set_visual(vis)
        return window

    def _apply_glass_foreground(self, term, theme_name):
        """Default ink. Pale glass needs a dark foreground; charcoal keeps a light one."""
        if not hasattr(term, "set_color_foreground"):
            return
        try:
            from gi.repository import Gdk
        except ImportError:
            return
        red, green, blue = glass_foreground_rgb(theme_name)
        fg = Gdk.RGBA()
        fg.red = red / 255.0
        fg.green = green / 255.0
        fg.blue = blue / 255.0
        fg.alpha = 1.0
        term.set_color_foreground(fg)

    def _clear_terminal_background(self, window, term):
        from gi.repository import Gdk, Gtk
        rgba = Gdk.RGBA()
        rgba.red = rgba.green = rgba.blue = rgba.alpha = 0.0
        if hasattr(term, "set_color_background"):
            term.set_color_background(rgba)
        self._apply_glass_foreground(term, read_player_theme())
        # Both are needed to fully disable bold in VTE; see the OpenGL host.
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
        prov = Gtk.CssProvider()
        prov.load_from_data(b"vte-terminal, window { background-color: transparent; padding: 0px; margin: 0px; font-weight: normal; text-shadow: 0 0 3px rgba(0, 229, 255, 0.40), 0 0 7px rgba(176, 88, 255, 0.20); letter-spacing: 0.35px; }")
        term.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        window.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _sync_windows_drag(self) -> None:
        if not IS_WIN or self.renderer is None:
            return
        from windows_glass_drag import install_on_host
        install_on_host(self)

    def _pump_windows_drag(self) -> None:
        if not IS_WIN:
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
        self._release_windows_drag()
        slider, self.slider = getattr(self, "slider", None), None
        if slider is not None:
            try:
                slider.detach()
            except Exception:
                pass
        pid, self.child_pid = self.child_pid, 0
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        win, self.gtk_window, self.terminal = self.gtk_window, None, None
        if win is not None:
            win.destroy()
            if self.glib:
                self._pump_gtk(self.glib)
        ren, self.renderer = self.renderer, None
        if ren is not None:
            ren.close()


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
        if IS_WIN:
            try:
                if subprocess.check_output(['powershell.exe', '-NoProfile', '-Command',
                    'Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*MP_Liquid_Visualizer*" -or $_.CommandLine -like "*mp_liquid_visualizer_vulkan*" } | Select-Object -ExpandProperty ProcessId'],
                    stderr=subprocess.DEVNULL, text=True).strip():
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
        # resets ``visible``; MP Gui leaves the player's on/off choice alone.
        try:
            from liquid_player_visualizer import mark_player_visualizer_attached
            mark_player_visualizer_attached(
                visible=None if _host_is_manager() else True)
        except Exception:
            pass
        kw = {"creationflags": 0x00000208} if IS_WIN else {"start_new_session": True}
        # Windows included. Without this env the companion never bonds, and a
        # shortcut launch used to open a free-floating window behind nothing.
        kw["env"] = _visualizer_env(companion=True)
        with open(CONFIG_DIR / "liquid_visualizer.log", "a") as f:
            subprocess.Popen([sys.executable, str(vis_script), "--preset", "random", "--backend", "vulkan"], cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=f, stderr=f, **kw)
        return True
    except Exception:
        return False


def _visualizer_env(companion: bool = False) -> dict:
    """Env for a spawned visualizer.

    _import_gtk() pins GDK_BACKEND=x11 and this module pins PYOPENGL_PLATFORM to
    the X11 GLX platform so the glass window can be parented by XID. The spawned
    visualizer uses GLFW with its own context, so inheriting either makes it die
    with "Attempt to retrieve context when no valid context" (plus
    "libdecor-gtk-WARNING: Failed to initialize GTK") and no window appears.

    ``companion=True`` marks the window the player owns, which stays bonded
    behind the player for its whole life. A window opened later is explicitly
    detached, so it never latches onto the player.
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


def _stop_other_players() -> None:
    """SIGKILL the other MP Audio Player processes owned by this user."""
    import signal
    me = os.getpid()
    proc = Path("/proc")
    targets = []
    if proc.is_dir():
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            if pid == me:
                continue
            try:
                argv = [a.decode("utf-8", "ignore")
                        for a in (entry / "cmdline").read_bytes().split(b"\x00") if a]
            except OSError:
                continue
            if argv and os.path.basename(argv[0]).startswith("python") and any(
                    os.path.basename(a).startswith("MP_Audio_Player") for a in argv[1:]):
                targets.append(pid)
    for pid in targets:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            continue


def _toggle_hidden_mode() -> None:
    """Hide or restore every MP window (Hidden Mode)."""
    try:
        import mp_hidden_mode as hidden
        hidden.toggle()
    except Exception:
        pass


def spawn_new_visualizer() -> bool:
    """Open a new, free-standing MP Liquid Visualizer window. Pressing Z shows it.

    This must NOT reuse the companion path. A companion (``_visualizer_env(
    companion=True)``) is by definition the window the player owns: it is
    forced onto the player's exact rectangle, stacked behind the player,
    denied focus, and refused every Z/double-click/fullscreen action. Spawning
    one here made Z look like a no-op, because the new window opened perfectly
    hidden underneath the player it was bonded to.

    Z is the user asking for a window of their own, so the child is explicitly
    detached and the state file is left alone: ``mark_player_visualizer_attached()``
    belongs to the companion path only.
    """
    try:
        vis_script = next((p for p in (ROOT / "MP_Liquid_Visualizer.py", ROOT / "scripts" / "mp_liquid_visualizer_vulkan.py") if p.is_file()), None)
        if not vis_script:
            return False
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        kw = {"creationflags": 0x00000208} if IS_WIN else {"start_new_session": True}
        # companion=False -> MP_VISUALIZER_DETACHED=1: a free window.
        kw["env"] = _visualizer_env(companion=False)
        with open(CONFIG_DIR / "liquid_visualizer.log", "a") as f:
            subprocess.Popen([sys.executable, str(vis_script), "--preset", "random", "--backend", "vulkan"], cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=f, stderr=f, **kw)
        return True
    except Exception:
        return False


def main(argv):
    if "--self-test" in argv:
        from liquid_glass_vulkan import run_self_test
        run_self_test()
        return 0
    if "--list-fonts" in argv:
        list_liquid_glass_fonts()
        return 0

    embed = "--embed-check" in argv
    opts = {"tracklist": None, "audio": None, "theme": None, "font": None, "set-font": None, "manager": False}
    flag_map = {"--tracklist": "tracklist", "--audio": "audio", "--theme": "theme", "-t": "theme",
                "--font": "font", "-f": "font", "--set-font": "set-font"}
    cleaned_argv, i, n = [], 0, len(argv)
    while i < n:
        a = argv[i]
        if a in ("--no-visualizer", "--manager"):
            if a == "--manager":
                opts["manager"] = True
            i += 1
        elif a in flag_map and i + 1 < n:
            opts[flag_map[a]] = argv[i + 1]
            i += 2
        elif "=" in a and a.split("=", 1)[0] in flag_map:
            k, v = a.split("=", 1)
            opts[flag_map[k]] = v
            i += 1
        else:
            cleaned_argv.append(a)
            i += 1
        if opts["set-font"] is not None:
            set_liquid_glass_font(opts["set-font"])
            print(f"✔ MP Liquid Glass Player default font set to: {opts['set-font']}")
            return 0

    player_gui = not embed and not opts["tracklist"] and "--self-test" not in argv
    # MP Gui and MP Liquid Player stop at four windows. The tracklist viewer,
    # the embed check, and helper flags are not counted.
    if player_gui:
        counted_role = "gui" if opts.get("manager") else "player"
        if not host_allowed(counted_role):
            # A shortcut launch discards stderr, so the refusal used to look
            # like nothing happened at all. Say it on the desktop as well.
            notify_limit(counted_role)
            return 1
    mode = "MINI" if "--mini" in argv else ("MICRO" if "--micro" in argv else ("VERTICAL" if "--vertical" in argv else "NORMAL"))
    manager = bool(opts.get("manager"))
    # Mark the MP Gui session before the companion is spawned: _visualizer_env
    # reads MP_GUI_SESSION to give that child MP_VISUALIZER_MATCH_GUI=1, which
    # is what makes it bond behind MP Gui instead of behind a Liquid Player.
    if manager and not embed and not opts["tracklist"]:
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
    if manager and not embed and not opts["tracklist"]:
        command = manager_argv()
        if not Path(command[1]).is_file():
            raise RuntimeError(f"MP Mix Manager was not found at {command[1]}")
    elif opts["tracklist"] and not embed:
        mode = "TRACKLIST"
        command = tracklist_argv(opts["tracklist"], audio_path=opts["audio"], theme=opts["theme"])
        if not Path(command[1]).is_file():
            raise RuntimeError(f"Tracklist viewer script was not found at {command[1]}")
    elif embed:
        command = embed_check_argv()
    else:
        extra = [a for a in cleaned_argv if a not in ("--self-test", "--embed-check", "--mini", "--micro")]
        if opts["theme"] and not any(a == "--theme" or a.startswith("--theme=") for a in extra):
            extra.extend(["--theme", opts["theme"]])
        command = player_argv(mode=mode, extra_args=extra)
        if not Path(command[1]).is_file():
            raise RuntimeError(f"MP Audio Player was not found at {command[1]}")

    try:
        ok = GlassHost(command, embed_check=embed, mode=mode, cli_theme=opts["theme"], font=opts["font"]).run()
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


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as exc:
        print(f"Liquid Glass: {exc}", file=sys.stderr)
        raise SystemExit(1)