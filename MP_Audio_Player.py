#!/usr/bin/env python3
from __future__ import annotations
import os, re, sys, time, datetime, math, random, json, socket, select, shutil, argparse, threading, subprocess, unicodedata, queue, collections, signal, functools, colorsys, errno, hashlib
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Union, Sequence

for _s in (sys.stdout, sys.stderr):
    if _s and hasattr(_s, "reconfigure"):
        try: _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass

def _get_app_version() -> str:
    if v := os.environ.get("VERSION"): return v.strip()
    s_dir = Path(__file__).resolve().parent
    for c in (s_dir / "VERSION", s_dir.parent / "VERSION"):
        if c.is_file():
            try: return c.read_text(encoding="utf-8").strip()
            except Exception: pass
    return "0.5.3 Stable (Sensorium)"

VERSION = _get_app_version()

try:
    from mp_activity_tracker import record_activity
    from mp_logging import log_error as mp_log_error
except ImportError:
    try:
        from scripts.mp_activity_tracker import record_activity
        from scripts.mp_logging import log_error as mp_log_error
    except ImportError:
        def record_activity(*a, **k): pass
        def mp_log_error(*a, **k): pass

# Raspberry Pi / low-power ARM tuning (timeouts, defaults). Stdlib-only.
try:
    import mp_raspberry_pi
except ImportError:
    try:
        from scripts import mp_raspberry_pi  # type: ignore
    except ImportError:
        class mp_raspberry_pi:  # type: ignore[no-redef]
            """Fallback when the module was not copied with the tree."""
            @staticmethod
            def is_low_power_gpu() -> bool:
                return False

            @staticmethod
            def is_raspberry_pi() -> bool:
                return False

            @staticmethod
            def apply_default_env() -> dict:
                return {}

np = sf = sd = None
def _ensure_numpy():
    global np
    if np is None:
        try:
            import numpy as _np
            np = _np
        except ImportError:
            sys.exit("Error: numpy is required for MP_Audio_Player.py")
    return np

def _as_float_vector(value, fallback_len: int = 24):
    """1-D float levels. An array that is already float64 is not copied."""
    _ensure_numpy()
    if value is None:
        return np.zeros(int(fallback_len), dtype=float)
    arr = np.asarray(value, dtype=float)
    if getattr(arr, "ndim", 1) != 1:
        arr = np.ravel(arr)
    if arr.size == 0:
        return np.zeros(int(fallback_len), dtype=float)
    return arr

def _ensure_audio_backends():
    global np, sf, sd
    _ensure_numpy()
    if sf is None:
        try: import soundfile as sf
        except ImportError: pass
    if sd is None:
        try: import sounddevice as sd
        except ImportError: pass


def portaudio_name_is_generic(name: str) -> bool:
    """True for PortAudio's placeholder names, which name no real sink.

    PortAudio reports the ALSA "default" plugin as the device name, and that is
    what the player used to print: ``Output: default``. It says nothing about
    where the sound is going, so callers replace it with the system sink.
    """
    text = str(name or "").strip().lower()
    if not text:
        return True
    if text in ("default", "default audio output", "pulse", "pipewire", "sysdefault"):
        return True
    # ALSA plugin spellings such as default:CARD=NVidia or sysdefault:CARD=x.
    return text.startswith(("default:", "sysdefault:", "defaults:"))


def output_device_label(name: str, system_sink: str = "") -> str:
    """The device name to show on the Output line.

    A real device name is returned untouched. When PortAudio only says
    "default", the system sink's own name is shown instead, because that is the
    device the sound actually reaches. If no system name is known either, the
    original placeholder is kept rather than printing an empty label.
    """
    shown = str(name or "").strip()
    sink = str(system_sink or "").strip()
    if not portaudio_name_is_generic(shown):
        return shown
    if sink and not portaudio_name_is_generic(sink):
        return sink
    return shown or "default"


def _mixer_deck_theme_fallback(name: str) -> str:
    """Map a theme this player cannot paint onto the closest one it can.

    The Liquid Mixer offers themes the player's --theme table does not carry.
    Those live in the sibling MP_Liquid_Mixer package, so it is imported from
    there by path; a tree without the mixer keeps the name unchanged.
    """
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _pkg = _Path(__file__).resolve().parent / "MP_Liquid_Mixer"
        if _pkg.is_dir() and str(_pkg) not in _sys.path:
            _sys.path.insert(0, str(_pkg))
        from mixer_core import DECK_THEME_FALLBACK as _fb
        return _fb.get(name, name)
    except Exception:
        return name


def _system_output_sink_name() -> str:
    """The system's current default sink description, or "".

    Cached because it is read while a frame is being drawn, and every call
    shells out. PipeWire/PulseAudio first (pactl describes the sink the way a
    person would name it), then wpctl, then a plain ALSA card name.
    """
    cached = globals().get("_SYS_SINK_CACHE")
    if isinstance(cached, tuple):
        stamp, value = cached
        if time.monotonic() - stamp < 10.0:
            return value
    value = ""
    try:
        if shutil.which("pactl"):
            out = subprocess.run(
                ["pactl", "get-default-sink"], capture_output=True, text=True, timeout=1.0
            ).stdout.strip()
            if out:
                desc = subprocess.run(
                    ["pactl", "list", "sinks"], capture_output=True, text=True, timeout=1.0
                ).stdout
                # Find the block for that sink and read its Description.
                block = ""
                for chunk in desc.split("Sink #"):
                    if f"Name: {out}" in chunk:
                        block = chunk
                        break
                for line in block.splitlines():
                    if line.strip().startswith("Description:"):
                        value = line.split(":", 1)[1].strip()
                        break
                if not value:
                    value = out
        if not value and shutil.which("wpctl"):
            out = subprocess.run(
                ["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"],
                capture_output=True, text=True, timeout=1.0
            ).stdout
            for line in out.splitlines():
                if "node.description" in line or "node.nick" in line:
                    value = line.split("=", 1)[1].strip().strip('"')
                    break
    except Exception:
        value = ""
    globals()["_SYS_SINK_CACHE"] = (time.monotonic(), value)
    return value


def _configured_output_device(sd_module=None):
    """Resolve MP_AUDIO_OUTPUT_DEVICE to a PortAudio output index.

    MP Mix Manager Audio Settings (Settings -> Studio Hardware) writes the
    choice. An index is used as given, a name is matched against the output
    device list (case-insensitively, first match wins), and "default" or an
    empty value means "let PortAudio decide", which is the old behaviour.
    Returns None when there is no usable selection, so the caller keeps its
    normal fallbacks.
    """
    raw = os.environ.get("MP_AUDIO_OUTPUT_DEVICE", "").strip()
    if not raw or raw.lower() in ("default", "auto", "system"):
        return None
    try:
        index = int(raw)
        return index if index >= 0 else None
    except ValueError:
        pass
    module = sd_module
    if module is None:
        _ensure_audio_backends()
        module = sd
    if module is None:
        return None
    try:
        wanted = raw.lower()
        for idx, device in enumerate(module.query_devices()):
            if int(device.get("max_output_channels", 0)) < 1:
                continue
            if str(device.get("name", "")).strip().lower() == wanted:
                return idx
        for idx, device in enumerate(module.query_devices()):
            if int(device.get("max_output_channels", 0)) < 1:
                continue
            if wanted in str(device.get("name", "")).strip().lower():
                return idx
    except Exception:
        return None
    return None

try: import termios, tty
except ImportError: termios = tty = None

_TTY_RUNTIME_MOD = None
_TTY_RUNTIME_TRIED = False

def _load_tty_runtime():
    """TTY Audio Mode helpers. Absent when the mode is off."""
    global _TTY_RUNTIME_MOD, _TTY_RUNTIME_TRIED
    if os.environ.get("MP_MIX_MANAGER_TTY_MODE") != "1":
        return None
    if _TTY_RUNTIME_TRIED:
        return _TTY_RUNTIME_MOD
    _TTY_RUNTIME_TRIED = True
    for name in ("scripts.mp_tty_runtime", "mp_tty_runtime"):
        try:
            _TTY_RUNTIME_MOD = __import__(name, fromlist=["configure_stdio"])
            return _TTY_RUNTIME_MOD
        except ImportError:
            continue
    return None

def _tty_resized() -> bool:
    runtime = _load_tty_runtime()
    if runtime is None:
        return False
    try:
        return bool(runtime.consume_resize())
    except Exception:
        return False

BOLD, DIM, ITALIC, NC = "\033[1m", "\033[2m", "\033[3m", "\033[0m"
COLOR_HOT_PURPLE, COLOR_INDIGO_BLUE, COLOR_LILAC = "\033[38;2;166;77;255m", "\033[38;2;102;102;255m", "\033[38;2;153;153;255m"
COLOR_TRAFFIC_GREEN, COLOR_TRAFFIC_AMBER, COLOR_TRAFFIC_RED = "\033[38;2;0;255;0m", "\033[38;2;255;153;51m", "\033[38;2;255;0;0m"
COLOR_MAIN_FG, COLOR_NEON_PINK, COLOR_ELECTRIC_CYAN, WHITE = "\033[38;2;209;209;224m", "\033[38;2;247;37;133m", "\033[38;2;0;229;255m", "\033[1;38;2;255;255;255m"
# The "Color Theme: <name>" notice under the equaliser. Reached by [c]/[C] and
# when another window changes the shared theme file. The letters are always
# white, on a dark chip, so the name stays readable on the pale glass, the
# light console tints, and the dark consoles. A theme hue disappears into one
# of those panes, and black disappears into the dark ones.
COLOR_THEME_NOTICE = "\033[1;48;2;20;20;28;38;2;255;255;255m"

# Into Darkness paints the opaque black void instead of the pale still-water
# field, and black on black is invisible. It keeps the light ink that the
# pale field cannot use. Every other theme, Retro Linux Memories included,
# shares that pale field with the console manager.
THEME_DARK_GLASS_INK = {
    # The two themes whose glass is an opaque dark pane, so light ink wins.
    # This set must equal the shader's theme_uses_dark_glass(): Into Darkness
    # is the black void, Retro Linux Memories is the charcoal field #2d2d2d.
    "into_darkness_void_out": "\033[1;38;2;236;240;244m",
    "retro_linux_memories": "\033[1;38;2;236;240;244m",
}
# Kept as an alias: the "Color Theme" notice was the first user of this mapping.
THEME_NOTICE_DARK_GLASS = THEME_DARK_GLASS_INK


_PLAYING_BADGE_BLACK = "\033[38;2;0;0;0m"


def dark_glass_ink(theme_name: str, pale_ink: str = _PLAYING_BADGE_BLACK) -> str:
    """Ink that stays readable on this theme's glass pane.

    ``pale_ink`` is used on the pale still-water field. Into Darkness is the
    black void pane and gets the light ink instead. The Liquid Glass PLAYING
    badge uses this. The console PLAYING badge does not: that label is always
    white. The "Color Theme" notice does not either: that name is always white.
    """
    return THEME_DARK_GLASS_INK.get(theme_name, pale_ink)


# The ▶ PLAYING label on the console player. Always white, on every theme.
# Liquid Glass does not use this: white disappears on the pale still-water
# field, and black disappears on the Into Darkness void.
_PLAYING_BADGE_WHITE = "\033[38;2;255;255;255m"


def playing_badge_ink(theme_name: str, liquid: bool = False) -> str:
    """Ink for the ▶ PLAYING label at the top of the player.

    The console player always paints it white, whichever theme is selected.
    Liquid Glass still follows the pane via ``dark_glass_ink``.
    """
    if liquid:
        return dark_glass_ink(theme_name)
    return _PLAYING_BADGE_WHITE


def theme_notice_ansi(theme_name: str) -> str:
    """"Color Theme" ink: always white, on a dark chip.

    ``theme_name`` is unused. The notice names the theme the moment it
    changes, and that name has to stay white on every pane. The console
    PLAYING badge is white too, but as plain glyphs. On Liquid Glass the
    badge still follows ``dark_glass_ink``.
    """
    del theme_name
    return COLOR_THEME_NOTICE


# The now-playing title at the top of the player is painted in one of the two
# highest-contrast inks, never a theme hue. Pink (or amber, or whatever the
# theme's accent is) has no guaranteed contrast against the pane, and the title
# is the one line read from across the room.
NOW_PLAYING_BLACK = "\033[1;38;2;0;0;0m"
NOW_PLAYING_WHITE = "\033[1;38;2;255;255;255m"


def now_playing_uses_white(theme_name: str, liquid: bool = True) -> bool:
    """True when the now-playing title should be white on this surface.

    Liquid Glass: white only on Into Darkness, the void-out black pane. The
    pane test is ``THEME_DARK_GLASS_INK``, which
    ``tests/test_theme_notice_is_black.py`` pins to the shader's own
    ``theme_uses_dark_glass``, so the title cannot drift from the surface it
    is drawn on.

    Console: whatever ink wins against ``theme_background()``. That background
    is the same still-water field the glass paints, so the two surfaces agree.
    """
    if liquid:
        return theme_name in THEME_DARK_GLASS_INK
    try:
        script_dir = str(Path(__file__).resolve().parent / "scripts")
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        from mp_theme_palette import theme_background
        red, green, blue = theme_background(theme_name)
    except Exception:
        return True
    # WCAG relative luminance; white wins unless the pane is genuinely light.
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) < 140


def now_playing_ink(theme_name: str, liquid: bool = True) -> str:
    """Pure black or pure white for the now-playing title.

    The surface decides, not the theme hue. ``liquid`` must describe the host
    being drawn into. The console background is the glass field, so the two
    answers agree; the flag stays so a caller still names the surface.
    """
    return NOW_PLAYING_WHITE if now_playing_uses_white(theme_name, liquid) else NOW_PLAYING_BLACK


RED, GREEN, YELLOW, MAGENTA, CYAN = COLOR_TRAFFIC_RED, COLOR_TRAFFIC_GREEN, COLOR_TRAFFIC_AMBER, COLOR_HOT_PURPLE, COLOR_LILAC

CONFIG_DIR = Path.home() / ".config" / "mix-manager"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

# A mixer deck runs its own engine, so it needs its own socket, pid, state and
# resume record while still sharing the library, favorites and theme with the
# other deck. MP_Liquid_Mixer sets MP_PLAYER_NAMESPACE per deck; every normal
# launch leaves it empty and keeps the historical file names untouched.
PLAYER_NAMESPACE = re.sub(r"[^A-Za-z0-9_.-]", "", os.environ.get("MP_PLAYER_NAMESPACE", "").strip())[:40]


def _player_file(name: str) -> Path:
    """Scope one player runtime file to this namespace (no-op without one)."""
    if not PLAYER_NAMESPACE:
        return CONFIG_DIR / name
    stem, dot, ext = name.partition(".")
    return CONFIG_DIR / f"{stem}-{PLAYER_NAMESPACE}{dot}{ext}"


SOCKET_PATH, PORT_FILE = _player_file("mp_audio_player.sock"), _player_file("mp_audio_player.port")
STATE_FILE, PID_FILE, DURATION_CACHE_FILE = _player_file("mp_player_state.json"), _player_file("mp_player.pid"), CONFIG_DIR / "track_durations.json"
FAVORITES_FILE = CONFIG_DIR / "favorites.json"
DAEMON_LOCK_FILE = _player_file("mp_player.lock")
RESUME_STATE_FILE = _player_file("playback_resume.json")
_DAEMON_LOCK_FH = None

# Generated playlists (DJ_Tracks_*.json) reach tens of megabytes once a library
# passes ~50k tracks. Parsing that much JSON on every window open is what made
# the player show "Loading library..." for seconds, so it reads a fast sidecar
# cache written beside the playlist instead (see scripts/playlist_cache.py).
try:
    import playlist_cache as _playlist_cache
except ImportError:
    from scripts import playlist_cache as _playlist_cache

try:
    from audio_formats import (
        AUDIO_EXTENSIONS, AUDIO_EXTENSION_SET, is_lossless_path,
        kdialog_audio_filter, macos_audio_file_types, prefers_ffmpeg,
        windows_audio_filter, zenity_audio_filter,
    )
except ImportError:
    try:
        from scripts.audio_formats import (
            AUDIO_EXTENSIONS, AUDIO_EXTENSION_SET, is_lossless_path,
            kdialog_audio_filter, macos_audio_file_types, prefers_ffmpeg,
            windows_audio_filter, zenity_audio_filter,
        )
    except ImportError:
        AUDIO_EXTENSIONS = ('.flac', '.wav', '.mp3', '.ogg', '.m4a', '.aac', '.aiff', '.wma')
        AUDIO_EXTENSION_SET = frozenset(AUDIO_EXTENSIONS)
        def is_lossless_path(path): return os.path.splitext(str(path))[1].lower() in {'.flac', '.wav', '.aiff', '.aif', '.alac', '.ape', '.wv', '.tta', '.tak', '.dsf', '.dff'}
        def prefers_ffmpeg(path): return os.path.splitext(str(path))[1].lower() not in (".flac", ".wav", ".aiff", ".aif", ".ogg", ".mp3", ".caf", ".au")
        def macos_audio_file_types(): return ["public.audio"] + [e.lstrip(".") for e in AUDIO_EXTENSIONS]
        def kdialog_audio_filter(): return "*.flac *.wav *.mp3 *.ogg *.aac *.m4a *.aiff | Audio Files (All types)"
        def zenity_audio_filter(): return "Audio Files (All types) | *.flac *.wav *.mp3 *.ogg *.aac *.m4a *.aiff"
        def windows_audio_filter(): return "Audio Files (All types)|*.flac;*.wav;*.mp3;*.ogg;*.aac;*.m4a|All Files (*.*)|*.*"
UNICODE_BLOCKS = [' ', ' ', '▂', '▃', '▄', '▅', '▆', '▇', '█']
_PRISM_LEVELS_FULL = (" ", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█")
_PRISM_LEVELS_LIMITED = (" ", "▄", "▄", "▄", "▄", "█", "█", "█", "█")
PLAYLIST_ACTIVE_MARK = "\u25ba"
_MOUSE_SGR_RE = re.compile(b"\x1b\\[<(\\d+);(\\d+);(\\d+)([mM])")
# Two left clicks on the same playlist row within this window play it, the
# same way Enter does. Matches the double-click slop the window hosts use.
PLAYLIST_DOUBLE_CLICK_SECONDS = 0.45
REC_MAX_FILE_BYTES = 3 * 1024 * 1024 * 1024


def _take_escape(buf: bytes) -> tuple:
    """Return (sequence, rest) for the escape at the front of ``buf``.

    ``sequence`` is None when ``buf`` holds only the start of a sequence and
    more bytes are needed to decide what the key was.
    """
    # Bare ESC is the Escape key once it stands alone.
    if len(buf) == 1:
        return buf, b""
    lead = buf[1:2]
    if lead == b"[":
        index = 2
        # Parameter (0x30-0x3F) and intermediate (0x20-0x2F) bytes precede the
        # final byte (0x40-0x7E). ESC [ on its own is still incomplete.
        while index < len(buf) and 0x20 <= buf[index] <= 0x3F:
            index += 1
        if index >= len(buf):
            return None, buf  # parameters not finished
        if 0x40 <= buf[index] <= 0x7E:
            return buf[:index + 1], buf[index + 1:]
        return None, buf  # not a valid CSI yet
    if lead == b"O":
        if len(buf) < 3:
            return None, buf  # SS3 needs its final byte
        return buf[:3], buf[3:]
    if lead == b"]":
        end = buf.find(b"\x07")
        if end < 0:
            return None, buf  # OSC is BEL-terminated
        return buf[:end + 1], buf[end + 1:]
    # ESC plus one printable byte is Alt+key.
    return buf[:2], buf[2:]


def split_key_burst(data: bytes) -> tuple:
    """Split one read() burst into separate key presses.

    A held arrow key arrives as several sequences in a single read, e.g.
    ``\\x1b[B\\x1b[B\\x1b[B``. ``_process_key`` used to compare the whole
    chunk against each binding, so the burst matched nothing and every press
    in it was discarded: the playlist cursor never moved while an arrow was
    held or tapped quickly. Complete sequences are returned in order, and a
    trailing partial sequence is returned as the remainder to be completed by
    the next read. Consecutive plain bytes stay together so a multi-byte
    character or a pasted word is not chopped up.
    """
    keys, buf = [], (data or b"")
    while buf:
        if buf[0] != 0x1B:
            nxt = buf.find(b"\x1b")
            if nxt < 0:
                keys.append(buf)
                break
            keys.append(buf[:nxt])
            buf = buf[nxt:]
            continue
        seq, rest = _take_escape(buf)
        if seq is None:
            return keys, buf  # incomplete: wait for the next read
        keys.append(seq)
        buf = rest
    return keys, b""

_LIMITED_GLYPHS = os.name == "nt" or sys.platform in ("win32", "cygwin") or bool(os.environ.get("MSYSTEM"))
def _use_limited_console_glyphs() -> bool: return _LIMITED_GLYPHS
def prism_level_glyph(eighth: int) -> str: return (_PRISM_LEVELS_LIMITED if _use_limited_console_glyphs() else _PRISM_LEVELS_FULL)[max(0, min(8, int(eighth)))]
def prism_peak_glyph(hot: bool, column: int = 0) -> str:
    return ("\u25b2" if hot else "\u2500") if _use_limited_console_glyphs() else (("\u2726" if column % 2 == 0 else "\u25c6") if hot else "\u2501")
def meter_peak_glyph() -> str: return "\u2500" if _use_limited_console_glyphs() else "\u2594"
def meter_level_char(frac: float, peak_here: bool) -> str:
    return "█" if frac >= 1.0 else (prism_level_glyph(max(1, min(8, int(frac * 8)))) if frac > 0.0 else (meter_peak_glyph() if peak_here else " "))

_STARDUST_SAFE = {
    "✦": "•", "✧": "°", "⋆": "·", "◆": "•", "◇": "o", "╵": "│", "▔": "─",
    "⠄": "·", "⠂": ".", "⠁": ".", "⠈": "'", "⡀": ".", "≈": "~", "≋": "~",
    "∿": "~", "∽": "~", "›": ">", "»": ">",
    # Rainbow mandala / plasma / wormhole glyphs.
    "✺": "*", "✷": "*", "✸": "*", "✹": "*", "✻": "*", "❋": "*", "❖": "*",
    "✱": "*", "✳": "*", "✴": "*", "⁂": "*", "⁕": "*", "⍟": "*", "⎈": "*",
    "⬢": "*", "⬡": "o", "◈": "<>", "◉": "O", "◎": "o", "◌": ".",
    "⌬": "o", "╱": "/", "╲": "\\", "┃": "|", "┊": "|", "╎": "|",
    "━": "-", "═": "=", "▪": ".",
    # Liquid Spectrogram / MPlanterian Overview Effect glyphs.
    "○": "o", "●": "o", "▲": "^", "▁": "_", "◍": "o", "◐": "o",
}
def console_symbol(ch: str) -> str: return _STARDUST_SAFE.get(ch, ch) if _use_limited_console_glyphs() else ch

# Precomputed high-efficiency rainbow color tables for Stream of Frequency visualizer (360 degrees of hue)
_RAINBOW_RGB_TABLE = tuple(tuple(int(c * 255) for c in colorsys.hsv_to_rgb(deg / 360.0, 1.0, 1.0)) for deg in range(360))
_RAINBOW_ANSI_BOLD = tuple(f"\033[1;38;2;{r};{g};{b}m" for r, g, b in _RAINBOW_RGB_TABLE)
_RAINBOW_ANSI_NORMAL = tuple(f"\033[38;2;{r};{g};{b}m" for r, g, b in _RAINBOW_RGB_TABLE)
_RAINBOW_ANSI_DIM = tuple(f"\033[38;2;{r//2};{g//2};{b//2}m" for r, g, b in _RAINBOW_RGB_TABLE)
_RAINBOW_ANSI_FAINT = tuple(f"\033[38;2;{max(20, int(r*0.35))};{max(20, int(g*0.35))};{max(20, int(b*0.35))}m" for r, g, b in _RAINBOW_RGB_TABLE)

# ------------------------------------------------------------------------------
# Stream of Frequency — two alternate readings of the same spectrogram buffer
# ------------------------------------------------------------------------------
# Both new effects read the *same* 16-row spectrogram history the Dynamic
# Spectrogram builds. Only the drawing changes, which is why they share the
# buffer-push helper and stay in step with each other when you cycle with E.
#
# Liquid Spectrogram: a still, mirrored pool of glowing fluid. Cool palette,
#   half-block fill for smooth viscosity, a bright meniscus at the water line
#   and caustic glints drifting gently to the right (opposite of the river).
# MPlanterian Overview Effect: a topographic contour map seen from above.
#   Symmetric terrain, contour lines at fixed elevations, a warm land palette
#   and a slow satellite sweep. Reads as a map, not as a river.
_SOF_LIQUID_RAMP = (
    (10, 16, 52), (16, 30, 92), (20, 52, 142), (16, 84, 184),
    (12, 122, 210), (10, 158, 214), (22, 190, 212), (58, 220, 208),
    (112, 240, 222), (176, 250, 240), (222, 254, 251), (245, 255, 255),
    (255, 255, 255),
)
_SOF_OVERVIEW_RAMP = (
    (8, 12, 30), (10, 30, 74), (14, 58, 112), (16, 92, 122),
    (22, 126, 108), (46, 146, 88), (92, 156, 76), (146, 162, 72),
    (188, 158, 86), (214, 178, 116), (232, 206, 156), (246, 234, 208),
    (255, 252, 242),
)


@functools.lru_cache(maxsize=4)
def _sof_ramp_ansi(ramp: Tuple[Tuple[int, int, int], ...]) -> Tuple[str, ...]:
    """TrueColor escapes for one palette, indexed by a 0..12 brightness level."""
    out = []
    for r, g, b in ramp:
        out.append(f"\033[38;2;{r};{g};{b}m")
    return tuple(out)


# A unit circle sampled once, so the full-field effects do no trigonometry for
# ring and spiral points. 1440 steps divides the common step counts evenly.
_FX_RING_RES = 1440
_FX_COS_T = tuple(math.cos(2.0 * math.pi * i / _FX_RING_RES) for i in range(_FX_RING_RES))
_FX_SIN_T = tuple(math.sin(2.0 * math.pi * i / _FX_RING_RES) for i in range(_FX_RING_RES))


@functools.lru_cache(maxsize=4)
def _fx_ansi_table(bold: bool = False) -> Tuple[Tuple[str, ...], ...]:
    """[hue 0..359][brightness 0..12] TrueColor escapes, built once.

    The full-field effects colour every lit cell every frame. A table lookup
    costs two list indexes instead of a colorsys call and an f-string.
    """
    table = []
    for r, g, b in _RAINBOW_RGB_TABLE:
        row = []
        for q in range(13):
            v = q / 12.0
            rr, gg, bb = int(round(r * v)), int(round(g * v)), int(round(b * v))
            row.append(f"\033[{'1;' if bold else ''}38;2;{rr};{gg};{bb}m")
        table.append(tuple(row))
    return tuple(table)


@functools.lru_cache(maxsize=32)
def _fx_ring_lut(steps: int) -> Tuple[Tuple[float, float, str], ...]:
    """(cos, sin, edge glyph) for a ring of `steps` points, cached.

    The glyph follows the tangent: the sides of an ellipse are vertical, the
    crown and trough are horizontal, and the four quadrants slope.
    """
    steps = max(4, int(steps))
    out = []
    for i in range(steps):
        cos_t = _FX_COS_T[(i * _FX_RING_RES) // steps % _FX_RING_RES]
        sin_t = _FX_SIN_T[(i * _FX_RING_RES) // steps % _FX_RING_RES]
        if abs(cos_t) > 0.90:
            glyph = "│"
        elif abs(sin_t) > 0.90:
            glyph = "─"
        elif cos_t * sin_t > 0.0:
            glyph = "╲"
        else:
            glyph = "╱"
        out.append((cos_t, sin_t, glyph))
    return tuple(out)


@functools.lru_cache(maxsize=16384)
def _rainbow_fx_ansi(hue_idx: int, value_q: int, bold: bool = False) -> str:
    """Cached TrueColor rainbow ANSI for a hue and a 0..12 brightness level.

    The three new full-field effects paint thousands of cells per frame. Building
    an escape sequence with colorsys for every cell is too slow, so the hue is an
    index into the precomputed rainbow table and the brightness is quantised to
    twelve steps and memoised.
    """
    r, g, b = _RAINBOW_RGB_TABLE[int(hue_idx) % 360]
    v = max(0, min(12, int(value_q))) / 12.0
    r, g, b = int(round(r * v)), int(round(g * v)), int(round(b * v))
    return f"\033[{'1;' if bold else ''}38;2;{r};{g};{b}m"

_DUST_CHOICES_LIM = {"hot": ["•", "°", "·", "˙", "+"], "mid": ["•", "°", "·", "˙", ".", "*"], "low": ["·", "˙", ".", "*", "'", ","]}
_DUST_CHOICES_FULL = {"hot": ["✦", "✧", "⋆", "•", "°"], "mid": ["•", "°", "·", "˙", "⠄", "⠂"], "low": ["·", "˙", "⠄", "⠂", "⠁", "⠈", "⡀"]}
def stardust_dust_choices(tier: str) -> List[str]: return (_DUST_CHOICES_LIM if _use_limited_console_glyphs() else _DUST_CHOICES_FULL)[tier]

def favorite_glyph(filled: bool) -> str:
    if _use_limited_console_glyphs():
        return "*" if filled else "-"
    return "\u2605" if filled else "\u2606"

@functools.lru_cache(maxsize=8192)
def _favorite_key_cached(raw: str, cwd: str) -> str:
    """Normalised favourite identity.

    The playlist asks this once per visible row, every frame. ``abspath`` walks
    the path each time, so an uncached call was most of the row cost on a tall
    Liquid Glass window. Absolute paths pass an empty cwd and share one entry.
    A relative path is keyed by the cwd it was resolved against.
    """
    try:
        if cwd:
            return os.path.normcase(os.path.abspath(os.path.join(cwd, raw)))
        return os.path.normcase(os.path.abspath(os.path.expanduser(raw)))
    except Exception:
        return os.path.normcase(raw)

def favorite_key(path: Union[str, Path, None]) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    if raw.startswith(("\\\\", "//", "smb://")):
        return raw.replace("\\", "/").rstrip("/").lower()
    cwd = "" if os.path.isabs(raw) else os.getcwd()
    return _favorite_key_cached(raw, cwd)

def music_library_label(folder_name: str) -> str:
    n = str(folder_name or "ALL").strip() or "ALL"
    return "All Music Folders" if n.upper() in ("ALL", "ALL_MUSIC_FOLDERS") else n

def _is_bundled_sample(path: Union[str, Path, None]) -> bool:
    raw = str(path or "").replace("\\", "/")
    if not raw:
        return False
    try:
        sample_root = (get_base_dir() / "MP_SAMPLES").resolve()
        target = Path(raw).resolve()
        return target == sample_root or sample_root in target.parents
    except Exception:
        return "/MP_SAMPLES/" in f"/{raw}"

def is_cloud_or_remote_path(p: Union[str, Path, None]) -> bool:
    if not p: return False
    s = str(p)
    return s.startswith(("\\\\", "//", "smb://")) or any(k in s for k in ("GoogleDrive", "google3:", "MUSIC_SMB", "/run/media/", "/mnt/", "/media/", "/gvfs/smb-share:"))

def music_share_for_path(p: Union[str, Path, None]) -> Optional[Dict[str, Any]]:
    """The configured Music Folder whose mount root contains this path.

    The generated music playlists name ``<mount_root>/<id>/...`` paths. Nothing
    mounts those SMB shares at login, so a row can exist in the playlist while
    its file does not. This maps an unreachable row back to the share that owns
    it, which is what makes an on-demand mount possible.
    """
    if not p:
        return None
    raw = str(p)
    try:
        import music_folders_manager as mfm
    except ImportError:
        try:
            from scripts import music_folders_manager as mfm
        except ImportError:
            return None
    try:
        config = mfm.load_music_config()
        mount_root = str(config.get("mount_root") or "")
        if not mount_root or not raw.startswith(mount_root):
            return None
        for folder in mfm.load_folders(config):
            if str(folder.get("type", "")).lower() != "smb" or not folder.get("id"):
                continue
            base = os.path.join(mount_root, str(folder["id"]))
            if raw == base or raw.startswith(base + os.sep):
                return folder
    except Exception:
        return None
    return None

def ensure_music_share_for_path(p: Union[str, Path, None]) -> bool:
    """Mount the SMB share that owns ``p``. True when its file is now readable.

    Used when the user picks a row whose file is missing: a share that was
    simply not mounted comes back to life here instead of failing silently.
    An already-mounted share is a no-op inside ``connect_folder``.
    """
    folder = music_share_for_path(p)
    if not folder:
        return False
    try:
        import music_folders_manager as mfm
    except ImportError:
        try:
            from scripts import music_folders_manager as mfm
        except ImportError:
            return False
    # A desktop session has ~/.local/bin on PATH; a service or a stripped
    # environment may not, and rclone lives there on Linux.
    local_bin = str(Path.home() / ".local" / "bin")
    if os.path.isdir(local_bin) and local_bin not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = local_bin + os.pathsep + os.environ.get("PATH", "")
    try:
        config = mfm.load_music_config()
        mfm.connect_folder(folder, config, interactive=False)
    except Exception as exc:
        try: mp_log_error("MP_Audio_Player", f"Could not mount music share for {p}", details=str(exc))
        except Exception: pass
        return False
    try:
        return os.path.isfile(str(p))
    except OSError:
        return False

def unavailable_media_hint(p: Union[str, Path, None]) -> str:
    """One actionable line for a row whose file cannot be opened."""
    name = os.path.basename(str(p or ""))
    folder = music_share_for_path(p)
    if folder:
        return (f"Cannot play {name}: music folder {folder.get('id')} is offline. "
                f"Check the SMB login in MP Mix Manager > Music Folders (option 2).")
    return (f"Cannot play {name}: the file is missing or on an unmounted drive. "
            f"Reconnect it, or refresh the library in Music Folders.")

@functools.lru_cache(maxsize=1)
def get_base_dir() -> Path:
    p = Path(__file__).resolve()
    return p.parent.parent if p.parent.name == "scripts" else p.parent

try:
    _sd_dir = str(get_base_dir() / "scripts")
    if _sd_dir not in sys.path: sys.path.insert(0, _sd_dir)
    from monitor_bluetooth_audio import BluetoothAudioRouter, get_bluetooth_audio_status
except Exception:
    BluetoothAudioRouter = get_bluetooth_audio_status = None

try:
    from liquid_audio_drop import (
        audio_track_items, is_drop_manifest,
        read_drop_manifest, DROP_OSC_PREFIX,
    )
except Exception:
    DROP_OSC_PREFIX = b"\x1b]5221;"
    def audio_track_items(paths): return []
    def is_drop_manifest(path): return False
    def read_drop_manifest(path): return {"paths": [], "truncated": False, "rejected": True}

try:
    from scripts import dsdiff_reader
except ImportError:
    try: import dsdiff_reader
    except ImportError: dsdiff_reader = None

def parse_env_file(filepath: Path) -> Dict[str, str]:
    env = {}
    if filepath.is_file():
        try:
            for l in filepath.read_text(encoding="utf-8", errors="ignore").splitlines():
                l = l.strip()
                if l and not l.startswith("#") and "=" in l:
                    k, v = l.split("=", 1)
                    env[k.strip()] = v.strip().strip('"\'')
        except Exception: pass
    return env

# libsndfile's "length unknown" sentinel. A container that does not declare a
# total sample count - a FLAC whose STREAMINFO total is 0, a piped or
# half-written encode - makes soundfile report this instead of a real frame
# count. It is a marker, not a length: divided by 44100 it is roughly 2.9
# million years, and the feeder then waits for an EOF that never arrives.
UNKNOWN_FRAME_COUNT = (1 << 63) - 1
# Nothing in this player is a week long. Above this a duration is a corrupt
# header, not a mix.
MAX_PLAUSIBLE_AUDIO_SEC = 7 * 24 * 3600


def sane_frame_count(frames: Any, samplerate: int = 0) -> int:
    """A frame count worth trusting, or 0 meaning "unknown length".

    libsndfile uses ``SF_COUNT_MAX`` (2**63-1) when it cannot know the length,
    and a corrupt header can produce any large number. Both must be treated as
    unknown instead of being shown as a duration and used as the EOF target;
    with an impossible target the feeder reads past the data forever and the
    track never ends.
    """
    try: frames = int(frames)
    except (TypeError, ValueError): return 0
    if frames <= 0 or frames >= UNKNOWN_FRAME_COUNT: return 0
    try: rate = int(samplerate)
    except (TypeError, ValueError): rate = 0
    if rate > 0 and frames > rate * MAX_PLAUSIBLE_AUDIO_SEC: return 0
    return frames


def sane_duration(seconds: Any) -> float:
    """A duration in seconds worth showing, or 0.0 for an unusable value."""
    try: value = float(seconds)
    except (TypeError, ValueError): return 0.0
    if not math.isfinite(value) or value <= 0.0 or value > MAX_PLAUSIBLE_AUDIO_SEC:
        return 0.0
    return value


def format_seconds(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0: return "00:00"
    s = int(seconds)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}" if h > 0 else f"{m:02d}:{sec:02d}"

# ------------------------------------------------------------------------------
# Track Play Log — one line per track started, for statistics and the AI agent.
#
# Kept apart from MP_Error.log (errors) and logs/user_activity.jsonl (app
# telemetry) so plays can be analysed on their own. The line layout is fixed:
# tab separated, one header row when the file is first created, so pandas can
# read it with read_csv(sep="\t", comment="#").
# ------------------------------------------------------------------------------
PLAY_LOG_NAME = "MP_Audio_Player_Log.log"
PLAY_LOG_HEADER = (
    "# MP Audio Player - Track Play Log (one row per track start, local time)\n"
    "# timestamp\tevent\ttitle\tpath\tfolder\tformat\tsize_mb\tduration_sec"
    "\tstart_position_sec\tplaylist_index\tplaylist\tmode\tplayer_version\n"
)


def get_play_log_path() -> Path:
    """Writable play log: the app's logs/ folder, or ~/.config/mix-manager."""
    for folder in (get_base_dir() / "logs", CONFIG_DIR):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            return folder / PLAY_LOG_NAME
        except Exception:
            continue
    return CONFIG_DIR / PLAY_LOG_NAME


def _write_play_log_row(row: str) -> bool:
    """Append `row` to the first writable play log; False if none accepted it."""
    primary = get_play_log_path()
    candidates = [primary] + [f / PLAY_LOG_NAME for f in (get_base_dir() / "logs", CONFIG_DIR) if (f / PLAY_LOG_NAME) != primary]
    for log_path in candidates:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            new_file = not (log_path.is_file() and log_path.stat().st_size > 0)
            with open(log_path, "a", encoding="utf-8") as fh:
                if new_file:
                    fh.write(PLAY_LOG_HEADER)
                fh.write(row)
            return True
        except Exception:
            continue
    return False


def _play_log_field(value: Any) -> str:
    """Collapse a value to one tab-free cell so a row stays parseable."""
    text = " ".join(str(value if value is not None else "").splitlines())
    text = text.replace("\t", " ").strip()
    return text or "-"


def log_track_played(track: Optional[Dict[str, Any]], *, mode: str = "",
                     playlist_index: int = -1, playlist: str = "",
                     duration_sec: float = 0.0, start_position: float = 0.0,
                     event: str = "PLAY") -> bool:
    """Append one date/time stamped play record for `track`.

    Never raises: a full disk or a read-only install must not stop playback.
    """
    if not isinstance(track, dict):
        return False
    try:
        path = str(track.get("path") or "")
        title = str(track.get("title") or track.get("name") or (os.path.basename(path) if path else "Unknown"))
        row = "\t".join(_play_log_field(v) for v in (
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            event,
            title,
            path,
            track.get("folder", ""),
            track.get("ext", ""),
            f"{float(track.get('size_mb') or 0.0):.2f}",
            f"{float(duration_sec or 0.0):.2f}",
            f"{float(start_position or 0.0):.2f}",
            playlist_index,
            playlist,
            mode,
            VERSION,
        ))
        return _write_play_log_row(row + "\n")
    except Exception:
        return False

def _run_bg(cmd: Union[List[str], str]) -> bool:
    try:
        # LINES and COLUMNS belong to the menu window. A new terminal that
        # inherits them draws the player past the bottom of its own window.
        env = os.environ.copy()
        env.pop("LINES", None)
        env.pop("COLUMNS", None)
        subprocess.Popen(
            cmd,
            cwd=str(get_base_dir()),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            env=env,
        )
        return True
    except Exception: return False

def _run_checked(cmd: List[str], timeout: float = 2.0) -> bool:
    try: return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=timeout).returncode == 0
    except Exception: return False

def _normalize_reveal_path(target_path: Union[str, Path, None]) -> Optional[str]:
    if target_path is None: return None
    raw = str(target_path).strip().strip('"')
    if not raw: return None
    if raw.lower().startswith("file:"):
        from urllib.parse import unquote, urlparse
        raw = unquote(urlparse(raw).path or "")
        if os.name == "nt" and len(raw) >= 3 and raw[0] == "/" and raw[2] == ":": raw = raw[1:]
    raw = os.path.expanduser(raw)
    # A Windows path is not host-relative. `abspath` would prefix the Linux
    # working directory and turn C:\\Music\\My Mix.flac into
    # /var/home/.../C:\\Music\\My Mix.flac, which is what made the Explorer
    # plan wrong whenever it was computed on a non-Windows host.
    if re.match(r"^[a-zA-Z]:[\\/]", raw):
        return raw.replace("/", "\\") if os.name != "nt" else os.path.normpath(raw)
    if os.name == "nt":
        if (m := re.match(r"^/([a-zA-Z])/(.*)$", raw.replace("\\", "/"))) and not os.path.exists(raw):
            # Build with concatenation: Python 3.9 (still the macOS system
            # interpreter) rejects a backslash inside an f-string expression.
            raw = m.group(1).upper() + ":\\" + m.group(2).replace("/", "\\")
        return os.path.normpath(raw)
    return os.path.abspath(raw)

def choose_file_manager_target(current_path: Union[str, Path, None], selected_path: Union[str, Path, None] = None) -> Optional[str]:
    for c in (current_path, selected_path):
        if (n := _normalize_reveal_path(c)) and os.path.exists(n): return n
    return None

def windows_explorer_select_command(path: str) -> str:
    """Build the Explorer "select this file" command line for a Windows path.

    `os.path.normpath` is host-dependent: running this on Linux collapses
    `C:\\Music\\My Mix.flac` to `C:\\Music\\My Mix.flac`-as-a-single-segment and
    the backslashes are lost, so the plan was wrong whenever it was computed
    off-Windows (cross-platform tests, or a WSL helper). Only normalise when the
    host actually speaks Windows paths.
    """
    s = path.replace('"', "")
    normalised = os.path.normpath(s) if os.name == "nt" else s.replace("/", "\\")
    return f'explorer.exe /select,"{normalised}"'

def _windows_shell_select(path: str) -> bool:
    if os.name != "nt": return False
    try:
        import ctypes
        sh, ole = ctypes.windll.shell32, ctypes.windll.ole32
        ole.CoInitialize(None)
        sh.ILCreateFromPathW.argtypes, sh.ILCreateFromPathW.restype = [ctypes.c_wchar_p], ctypes.c_void_p
        sh.ILFree.argtypes, sh.ILFree.restype = [ctypes.c_void_p], None
        sh.SHOpenFolderAndSelectItems.argtypes, sh.SHOpenFolderAndSelectItems.restype = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_ulong], ctypes.c_long
        if not (pidl := sh.ILCreateFromPathW(os.path.normpath(path))): return False
        try: return int(sh.SHOpenFolderAndSelectItems(pidl, 0, None, 0)) == 0
        finally: sh.ILFree(pidl)
    except Exception: return False

def _file_uri(path: str) -> str:
    from urllib.parse import quote
    t = str(path).replace("\\", "/")
    return ("file:///" if re.match(r"^[a-zA-Z]:/", t) else "file://") + quote(t if t.startswith("/") or re.match(r"^[a-zA-Z]:/", t) else "/" + t, safe="/:")

def _reveal_folder(path: str, is_file: bool) -> str:
    if not is_file: return path
    t = str(path).rstrip("\\/")
    c = max(t.rfind("/"), t.rfind("\\"))
    return t[:c] if c > 0 else t

def reveal_launch_plan(path: str, platform: str, is_file: Optional[bool] = None) -> List[Tuple[str, Union[List[str], str]]]:
    k = platform.lower()
    target = str(path) if k in ("linux", "freebsd", "darwin") else (_normalize_reveal_path(path) or str(path))
    ft = os.path.isfile(target) if is_file is None else bool(is_file)
    folder = _reveal_folder(target, ft)
    if k == "darwin": return [("bg", ["open", "-R", target] if ft else ["open", folder])]
    if k == "win32": return [("bg", windows_explorer_select_command(target) if ft else ["explorer.exe", folder])]
    if k == "wsl": return [("wsl", target if ft else folder), ("bg", ["wslview", folder])]
    plan: List[Tuple[str, Union[List[str], str]]] = []
    if ft:
        uri, name = _file_uri(target), target[len(folder) + 1:] if folder and target.startswith(folder) else os.path.basename(target)
        plan.extend([
            ("checked", ["dbus-send", "--session", "--type=method_call", "--dest=org.freedesktop.FileManager1", "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowItems", f"array:string:{uri}", "string:"]),
            ("checked", ["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1", "--object-path", "/org/freedesktop/FileManager1", "--method", "org.freedesktop.FileManager1.ShowItems", f"['{uri}']", ""]),
            ("checked", ["dbus-send", "--session", "--type=method_call", "--dest=org.xfce.FileManager", "/org/xfce/FileManager", "org.xfce.FileManager.DisplayFolderAndSelect", f"string:{folder}", f"string:{name}", "string:"]),
            ("bg", ["dolphin", "--select", target]), ("bg", ["nautilus", "--select", target]), ("bg", ["nemo", target]), ("bg", ["caja", target])
        ])
    plan.extend([("bg", [p, folder]) for p in ("thunar", "pcmanfm-qt", "pcmanfm")] + [("bg", ["gio", "open", folder]), ("bg", ["xdg-open", folder])])
    return plan

def _execute_reveal_plan(plan: List[Tuple[str, Union[List[str], str]]], which=None) -> bool:
    finder = which or shutil.which
    for mode, cmd in plan:
        if mode == "wsl":
            if not (finder("wslpath") and finder("explorer.exe")): continue
            try: conv = subprocess.run(["wslpath", "-w", str(cmd)], capture_output=True, text=True, timeout=2).stdout.strip()
            except Exception: conv = ""
            if conv and _run_bg(["cmd.exe", "/c", windows_explorer_select_command(conv) if os.path.isfile(str(cmd)) else f'explorer.exe "{conv}"']): return True
        elif isinstance(cmd, str):
            if mode == "bg" and _run_bg(cmd): return True
        elif finder(cmd[0]) and (_run_checked(cmd) if mode == "checked" else _run_bg(cmd)):
            return True
    return False

def open_in_file_manager(target_path: Union[str, Path, None]) -> bool:
    if not (norm := _normalize_reveal_path(target_path)) or not os.path.exists(norm): return False
    if sys.platform == "win32" and os.path.isfile(norm) and _windows_shell_select(norm): return True
    if sys.platform in ("darwin", "win32"): plat = sys.platform
    elif Path("/proc/version").exists() and "microsoft" in Path("/proc/version").read_text(errors="ignore").lower(): plat = "wsl"
    else: plat = "freebsd" if sys.platform.startswith("freebsd") else "linux"
    return _execute_reveal_plan(reveal_launch_plan(norm, plat))

def find_tracklist_for_mix(mix_file: Union[str, Path, None]) -> Optional[str]:
    if not mix_file or not (p := Path(mix_file).expanduser().resolve()).exists(): return None
    if (exact := p.parent / f"{p.stem}.txt").is_file(): return str(exact)
    base = get_base_dir()
    c_dirs = [d for d in {p.parent, p.parent / "FLAC_CONVERTED_OUTPUTS", p.parent.parent / "FLAC_CONVERTED_OUTPUTS", base / "FLAC_CONVERTED_OUTPUTS", base, p.parent.parent} if d.is_dir()]
    for d in c_dirs:
        if (c := d / f"{p.stem}.txt").is_file(): return str(c)
    tokens = [m.group(1) for m in (re.search(r'[_\ -](\d{2,3})([_\ -]|$)', p.stem), re.search(r'(\d{4}-\d{2}-\d{2})', p.stem)) if m]
    words = [w.lower() for w in re.sub(r'[_\ -]+', ' ', re.sub(r'MPlanetarian|Stream|of|Frequency|Part|WMI|Mix', '', p.stem, flags=re.I)).split() if len(w) >= 4]
    for d in c_dirs:
        try:
            for f in d.iterdir():
                if f.is_file() and f.suffix.lower() == ".txt":
                    fn = f.name.lower()
                    if any(t in fn for t in tokens) or any(w in fn for w in words): return str(f)
        except Exception: pass
    if (sh := base / "Mix_Archive_Manager.sh").is_file():
        try:
            out = subprocess.run(["bash", "-c", f'source "{sh}" 2>/dev/null; find_mix_tracklist "{p}" 2>/dev/null'], capture_output=True, text=True, timeout=1.5).stdout.strip()
            if out and os.path.isfile(out): return out
        except Exception: pass
    return None

def launch_liquid_glass_tracklist(
    tracklist_path: Union[str, Path],
    audio_path: Optional[Union[str, Path]] = None,
    theme: Optional[str] = None,
    base_dir: Optional[Path] = None,
) -> bool:
    if sys.platform not in ("linux", "darwin", "win32") and not sys.platform.startswith("freebsd"):
        return False
    if not tracklist_path or not (tl := Path(tracklist_path).expanduser().resolve()).is_file():
        return False
    base = base_dir or get_base_dir()

    if sys.platform == "darwin":
        candidates = [
            base / "MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py",
            base / "scripts" / "MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py",
            Path(__file__).resolve().parent / "MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py",
            base / "MP_Audio_Player_Liquid_Glass_macOS.py",
            base / "scripts" / "MP_Audio_Player_Liquid_Glass_macOS.py",
            Path(__file__).resolve().parent / "MP_Audio_Player_Liquid_Glass_macOS.py",
        ]
    else:
        backend = os.environ.get("MP_LIQUID_GLASS_BACKEND", "").strip().lower() or os.environ.get("LIQUID_GLASS_BACKEND", "").strip().lower()
        if not backend:
            for cfg in (base / "config.env", CONFIG_DIR / "config.env", Path(__file__).resolve().parent / "config.env"):
                if cfg.is_file():
                    if val := parse_env_file(cfg).get("LIQUID_GLASS_BACKEND"):
                        backend = val.strip().lower()
                        break
        if not backend:
            backend = "opengl"

        if backend == "vulkan":
            candidates = [
                base / "MP_Audio_Player_Liquid_Glass.py",
                base / "scripts" / "MP_Audio_Player_Liquid_Glass.py",
                Path(__file__).resolve().parent / "MP_Audio_Player_Liquid_Glass.py",
                base / "MP_Audio_Player_Liquid_Glass_OpenGL.py",
                base / "scripts" / "MP_Audio_Player_Liquid_Glass_OpenGL.py",
                Path(__file__).resolve().parent / "MP_Audio_Player_Liquid_Glass_OpenGL.py",
            ]
        else:
            candidates = [
                base / "MP_Audio_Player_Liquid_Glass_OpenGL.py",
                base / "scripts" / "MP_Audio_Player_Liquid_Glass_OpenGL.py",
                Path(__file__).resolve().parent / "MP_Audio_Player_Liquid_Glass_OpenGL.py",
                base / "MP_Audio_Player_Liquid_Glass.py",
                base / "scripts" / "MP_Audio_Player_Liquid_Glass.py",
                Path(__file__).resolve().parent / "MP_Audio_Player_Liquid_Glass.py",
            ]

    script_path = None
    for cand in candidates:
        if cand.is_file():
            script_path = cand
            break

    if not script_path:
        return False

    cmd = [sys.executable, str(script_path), "--tracklist", str(tl)]
    if audio_path and os.path.exists(str(audio_path)):
        cmd.extend(["--audio", str(Path(audio_path).expanduser().resolve())])
    if theme:
        cmd.extend(["--theme", str(theme)])

    log_dir = Path.home() / ".config" / "mix-manager"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    log_file = log_dir / "liquid_glass_tracklist.log"

    extra_kwargs = {}
    if sys.platform == "win32":
        extra_kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        extra_kwargs["start_new_session"] = True

    try:
        with open(log_file, "a") as log_fd:
            proc = subprocess.Popen(
                cmd,
                cwd=str(base),
                stdin=subprocess.DEVNULL,
                stdout=log_fd,
                stderr=log_fd,
                close_fds=True,
                **extra_kwargs,
            )
            return proc.pid > 0
    except Exception:
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(base),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                **extra_kwargs,
            )
            return proc.pid > 0
        except Exception as exc:
            try:
                mp_log_error("MP_Audio_Player", f"Failed launching Liquid Glass tracklist viewer: {exc}", exc)
            except Exception:
                pass
            return False


def export_tracklist_file(target_tracklist: Union[str, Path, None], fmt: str = "html",
                          base_dir: Optional[Path] = None,
                          output_dir: Optional[Union[str, Path]] = None) -> Tuple[bool, str]:
    """Export a tracklist as HTML or TEXT into the program folder's TRACKLIST_EXPORTS."""
    if not target_tracklist or not (tl := Path(target_tracklist).expanduser().resolve()).is_file():
        return False, "Tracklist file not found"
    base = Path(base_dir) if base_dir else get_base_dir()
    try:
        scripts_dir = str(base / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from tracklist_export import export_tracklist
        saved = export_tracklist(tl, fmt, base_dir=base, output_dir=output_dir)
        return True, str(saved)
    except Exception as exc:
        return False, str(exc)


def open_tracklist_window(target_tracklist: Union[str, Path, None], audio_path: Optional[Union[str, Path]] = None, theme: Optional[str] = None) -> bool:
    if not target_tracklist or not (tl := Path(target_tracklist).expanduser().resolve()).is_file(): return False
    base = get_base_dir()
    v_sh = base / "scripts" / "view_tracklist_console.sh"
    if not v_sh.is_file(): v_sh = base / "view_tracklist_console.sh"
    t_str, v_str, title = str(tl), str(v_sh), "Mix Tracklist Viewer"
    a_str = str(Path(audio_path).expanduser().resolve()) if audio_path and os.path.exists(str(audio_path)) else ""
    if a_str: os.environ["MP_TRACKLIST_AUDIO_FILE"] = a_str
    active_theme = (theme or "").strip()
    if not active_theme:
        try:
            th_file = CONFIG_DIR / "theme"
            if th_file.is_file(): active_theme = th_file.read_text(encoding="utf-8").strip()
        except Exception: pass
    if not active_theme:
        active_theme = os.environ.get("MP_TRACKLIST_THEME") or os.environ.get("CURRENT_THEME") or ""
    if active_theme:
        os.environ["MP_TRACKLIST_THEME"] = active_theme
        os.environ["CURRENT_THEME"] = active_theme

    if os.environ.get("MP_LIQUID_GLASS") == "1" or os.environ.get("TRACKLIST_VIEWER") in ("liquid_glass", "glass"):
        if launch_liquid_glass_tracklist(tl, audio_path=a_str, theme=active_theme, base_dir=base):
            return True
    sh_args = [v_str] + (["--theme", active_theme] if active_theme else []) + [t_str] + ([a_str] if a_str else [])
    if (cust := os.environ.get("TRACKLIST_VIEWER")) and cust not in ("console", "auto") and shutil.which(cust) and _run_bg([cust, t_str]): return True
    if sys.platform.startswith(("linux", "freebsd")):
        if shutil.which("konsole") and v_sh.is_file() and _run_bg(["konsole", "--hide-menubar", "--hide-tabbar", "--separate", "--qwindowtitle", title, "-p", f"tabtitle={title}", "-p", "TerminalMargin=0", "-p", "TerminalColumns=95", "-p", "TerminalRows=35", "-e", "bash"] + sh_args):
            threading.Thread(target=lambda: (time.sleep(0.15), shutil.which("xprop") and subprocess.run(["xprop", "-name", title, "-f", "_MOTIF_WM_HINTS", "32c", "-set", "_MOTIF_WM_HINTS", "0x2, 0x0, 0x0, 0x0, 0x0"], capture_output=True)), daemon=True).start()
            return True
        xfce_cmd = f"bash '{v_str}'" + (f" --theme '{active_theme}'" if active_theme else "") + f" '{t_str}'" + (f" '{a_str}'" if a_str else "")
        for term, args in [("gnome-terminal", [f"--title={title}", "--hide-menubar", "--", "bash"] + sh_args), ("xfce4-terminal", [f"--title={title}", "--hide-menubar", "--hide-borders", "-e", xfce_cmd]), ("alacritty", ["--title", title, "-e", "bash"] + sh_args), ("foot", ["-T", title, "bash"] + sh_args), ("xterm", ["-title", title, "-bd", "0", "-geometry", "95x35", "-e", "bash"] + sh_args), ("xdg-open", [t_str])]:
            if shutil.which(term) and _run_bg([term] + args): return True
    elif sys.platform == "darwin":
        if v_sh.is_file():
            mac_args = f'\\"{v_str}\\"' + (f' --theme \\"{active_theme}\\"' if active_theme else "") + f' \\"{t_str}\\"' + (f' \\"{a_str}\\"' if a_str else "")
            if _run_bg(["osascript", "-e", f'tell application "Terminal" to do script "bash {mac_args}"']): return True
        return _run_bg(["open", t_str])
    elif sys.platform == "win32":
        try: os.startfile(t_str); return True
        except Exception: pass
    return False

def export_playlist_m3u(mixes: List[Dict[str, Any]], target_path: Union[str, Path, None]) -> Tuple[bool, str]:
    if not target_path: return False, "Destination path cannot be empty"
    try:
        tp = Path(str(target_path).strip()).expanduser()
        if tp.suffix.lower() != ".m3u": tp = tp.with_suffix(".m3u")
        tp.parent.mkdir(parents=True, exist_ok=True)
        tp.write_text("#EXTM3U\n" + "".join(f"#EXTINF:-1,{m.get('name', Path(m.get('path', '')).stem)}\n{m.get('path')}\n" for m in mixes), encoding="utf-8")
        return True, str(tp.resolve())
    except Exception as e: return False, str(e)

def open_image_viewer(image_path: Union[str, Path]) -> bool:
    if not (ip := Path(image_path).expanduser().resolve()).is_file(): return False
    t_str = str(ip)
    if sys.platform.startswith(("linux", "freebsd")):
        return any(shutil.which(v) and _run_bg([v, t_str]) for v in ("gwenview", "loupe", "eog", "feh", "xdg-open"))
    if sys.platform == "darwin": return _run_bg(["open", "-a", "Preview", t_str]) or _run_bg(["open", t_str])
    if sys.platform == "win32":
        try: os.startfile(t_str); return True
        except Exception: pass
    return False

def notify_desktop(title: str, message: str) -> None:
    title, message = str(title or "MP Audio Player"), str(message or "")
    if not message: return
    try:
        if sys.platform.startswith(("linux", "freebsd")) and shutil.which("notify-send"):
            _run_bg(["notify-send", "-a", "MP Audio Player", title, message])
        elif sys.platform == "darwin":
            esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
            _run_bg(["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}"'])
    except Exception:
        pass

def _resolve_sub_dir(sub_name: str, base_dir: Optional[Path] = None) -> Path:
    base = base_dir or get_base_dir()
    mix_arc = os.environ.get("MIX_ARCHIVE_DIR")
    if not mix_arc:
        for cfg in (base / "config.env", CONFIG_DIR / "config.env"):
            if val := parse_env_file(cfg).get("MIX_ARCHIVE_DIR"):
                if os.path.isdir(val): mix_arc = val; break
    for d in ([Path(mix_arc) / sub_name] if mix_arc and os.path.isdir(mix_arc) else []) + [base / sub_name, CONFIG_DIR / sub_name]:
        try: d.mkdir(parents=True, exist_ok=True); return d.resolve()
        except Exception: pass
    fb = base / sub_name
    fb.mkdir(parents=True, exist_ok=True)
    return fb.resolve()

def get_default_spek_dir(base_dir: Optional[Path] = None, mode: str = "MIX_ARCHIVE", audio_path: Optional[str] = None) -> Path:
    return _resolve_sub_dir("SPEK_OUTPUTS", base_dir)

def get_recording_output_dir(base_dir: Optional[Path] = None) -> Path:
    return _resolve_sub_dir("MP_AUDIOWAV_REC_OUTPUT", base_dir)

def find_existing_spek(audio_path: Optional[Union[str, Path]], mode: str = "MIX_ARCHIVE", output_dir: Optional[Union[str, Path]] = None) -> Optional[str]:
    if not audio_path: return None
    p = Path(audio_path).expanduser()
    stem, clean = p.stem, re.sub(r'[\/\\:\*\?"<>\|]', '_', p.stem)
    base = get_base_dir()
    c_dirs = ([Path(output_dir)] if output_dir else []) + [get_default_spek_dir(base, mode, str(audio_path))]
    if p.parent.is_dir():
        c_dirs.extend([p.parent / "SPEK_OUTPUTS", p.parent])
        if p.parent.parent.is_dir(): c_dirs.append(p.parent.parent / "SPEK_OUTPUTS")
    c_dirs.append(base / "SPEK_OUTPUTS")
    names = list(dict.fromkeys(f"{s}{ext}" for s in (clean, stem) for ext in ("_spectrogram.png", ".spek.png", ".spek", "_spectrogram.jpg")))
    seen = set()
    for d in c_dirs:
        rd = str(d.resolve()) if d.exists() else str(d)
        if rd in seen or not d.is_dir(): continue
        seen.add(rd)
        for n in names:
            c = d / n
            try:
                if c.is_file() and c.stat().st_size > 0: return str(c.resolve())
            except Exception: pass
    return None

def generate_track_spek(audio_path: str, mode: str = "MIX_ARCHIVE", output_dir: Optional[Union[str, Path]] = None, force: bool = False) -> Tuple[bool, str]:
    if not audio_path or not os.path.isfile(audio_path): return False, "Audio file not found"
    if not force and (ex := find_existing_spek(audio_path, mode, output_dir)): return True, ex
    out_dir = Path(output_dir) if output_dir else get_default_spek_dir(get_base_dir(), mode, audio_path)
    try: out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e: return False, f"Failed to create SPEK output directory: {e}"
    clean_stem = re.sub(r'[\\/:\*\?"<>\|]', '_', Path(audio_path).stem)
    spek_file = out_dir / f"{clean_stem}_spectrogram.png"
    # Decoding a two-hour mix at full sample rate is minutes of work on a
    # Raspberry Pi. There was no timeout at all, so a stalled ffmpeg hung the
    # caller (and the IPC thread) forever. Scale the limit with file duration
    # and let a slow board take longer than a desktop.
    try:
        _size_mb = max(1.0, Path(audio_path).stat().st_size / (1024 * 1024))
    except OSError:
        _size_mb = 1.0
    _timeout = max(120, int(_size_mb * (12.0 if mp_raspberry_pi.is_low_power_gpu() else 4.0)))
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(audio_path),
             "-lavfi", "showspectrumpic=s=1920x1080:mode=combined:color=intensity:scale=log:legend=1:saturation=1.2",
             "-frames:v", "1", str(spek_file)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            timeout=_timeout,
        )
        if proc.returncode == 0 and spek_file.is_file() and spek_file.stat().st_size > 0:
            return True, str(spek_file.resolve())
        err = (proc.stderr or "").strip().splitlines()
        return False, (err[-1][:180] if err else "FFmpeg failed to produce spectrogram image")
    except subprocess.TimeoutExpired:
        return False, f"Spectrogram generation timed out after {_timeout}s"
    except Exception as exc: return False, f"Spectrogram generation error: {exc}"

def choose_export_m3u_gui() -> Optional[str]:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")): return None
    dt = os.path.join(os.path.expanduser("~/Desktop" if os.path.isdir(os.path.expanduser("~/Desktop")) else "~"), "MP_Archive_Playlist.m3u")
    for cmd in (["kdialog", "--title", "Export MP Mix Archive Playlist (.m3u)", "--getsavefilename", dt, "*.m3u | M3U Playlist (*.m3u)"], ["zenity", "--file-selection", "--save", "--confirm-overwrite", f"--filename={dt}", "--file-filter=M3U Playlist (*.m3u) | *.m3u"]):
        if shutil.which(cmd[0]):
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                return res.stdout.strip() if res.returncode == 0 and res.stdout.strip() else ""
            except Exception: pass
    return None

def choose_audio_file_gui() -> Optional[str]:
    if sys.platform == "win32":
        try:
            win_filter = windows_audio_filter().replace("'", "''")
            out = subprocess.run(["powershell", "-NoProfile", "-Command", f"[System.Reflection.Assembly]::LoadWithPartialName('System.windows.forms')|Out-Null;$f=New-Object System.Windows.Forms.OpenFileDialog;$f.Filter='{win_filter}';$f.Title='Open Audio File in MP Audio Player';if($f.ShowDialog() -eq 'OK'){{Write-Host $f.FileName}}"], capture_output=True, text=True, timeout=120).stdout.strip()
            return out if out and os.path.isfile(out) else ("" if out == "" else None)
        except Exception: pass
    elif sys.platform == "darwin":
        try:
            types = ", ".join(f'"{t}"' for t in macos_audio_file_types())
            if res := subprocess.run(["osascript", "-e", f'choose file of type {{{types}}} with prompt "Open Audio File in MP Audio Player"'], capture_output=True, text=True, timeout=120).stdout.strip():
                p = subprocess.run(["osascript", "-e", f'POSIX path of "{res}"'], capture_output=True, text=True, timeout=10).stdout.strip()
                if p and os.path.isfile(p): return p
            return ""
        except Exception: pass
    elif os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        h = os.path.expanduser("~")
        for cmd in (["kdialog", "--title", "Open Audio File in MP Audio Player", "--getopenfilename", h, kdialog_audio_filter()], ["zenity", "--file-selection", "--title=Open Audio File in MP Audio Player", f"--filename={h}/", f"--file-filter={zenity_audio_filter()}"]):
            if shutil.which(cmd[0]):
                try:
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                    return res.stdout.strip() if res.returncode == 0 and res.stdout.strip() else ""
                except Exception: pass
    return None

def load_playlist_json(playlist_path: Union[Path, str]) -> Optional[List[Dict[str, Any]]]:
    """Read a generated playlist .json, preferring the fast sidecar cache.

    Returned rows always carry ``duration_fmt``. A cache miss parses the JSON and
    refreshes the sidecar so the next launch is fast. The cache is derived data:
    if it is missing, stale, truncated, or from another version it is ignored.
    """
    p = Path(playlist_path)
    rows = _playlist_cache.read(p)
    if rows is None:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, list):
            return None
        for row in data:
            row.setdefault("duration_fmt", "00:00")
        _playlist_cache.write(p, data)
        return data
    for row in rows:
        row.setdefault("duration_fmt", "00:00")
    return rows

def _search_index(rows: Sequence[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], str, str]]:
    """Lower-cased search text per row, built once per playlist object.

    Building this used to run a regex per row, which cost about a second on a
    55k-track library the first time someone typed in the filter box. One
    str.translate with a shared table gives the same cleaned text far cheaper.
    """
    table = _search_clean_table()
    index: List[Tuple[Dict[str, Any], str, str]] = []
    append = index.append
    for row in rows:
        raw_text = " ".join(str(value) for value in row.values()).lower()
        append((row, raw_text, _search_clean_text(raw_text, table)))
    return index

@functools.lru_cache(maxsize=1)
def _search_clean_table() -> Dict[int, str]:
    # Keep a-z, 0-9 and whitespace; every other character becomes a space, which
    # is what re.sub(r'[^a-z0-9\\s]', ' ', ...) produced before. Input is already
    # lower-cased, so A-Z and non-ASCII alphanumerics both map to a space here.
    #
    # This used to enumerate all 1,114,111 Unicode codepoints, which allocated a
    # 74 MB dict and took over a second on a desktop. On a Raspberry Pi that is
    # an out-of-memory risk. Only the 128 ASCII codepoints are pre-seeded; any
    # non-ASCII character that actually appears in a library is added on demand
    # by _search_clean_text, so the table stays a few hundred entries.
    return {
        cp: " " for cp in range(0x80)
        if not (0x61 <= cp <= 0x7a or 0x30 <= cp <= 0x39 or chr(cp).isspace())
    }


_NON_ASCII_RE = re.compile(r"[^\x00-\x7f]")


def _search_clean_text(text: str, table: Dict[int, str]) -> str:
    """Translate one row's text with the shared table.

    Characters above U+007F pass through str.translate untouched when they are
    not in the table, so they are added lazily: whitespace is preserved and
    everything else becomes a space, exactly matching the original behaviour.
    """
    if _NON_ASCII_RE.search(text):
        for ch in set(text):
            cp = ord(ch)
            if cp >= 0x80 and cp not in table:
                table[cp] = ch if ch.isspace() else " "
    return text.translate(table)


def _search_clean(text: str) -> str:
    """Clean one string for the filter box.

    Public entry point so callers (and the tests) do not have to know that the
    table is populated lazily. Equivalent to the old
    ``re.sub(r"[^a-z0-9\\s]", " ", text.lower(), flags=re.I)``.
    """
    return _search_clean_text(text.lower(), _search_clean_table())

ARCHIVE_SCAN_CACHE = CONFIG_DIR / "archive_scan_cache.json"


def _archive_dirs_signature(dirs: Sequence[Path]) -> str:
    """Stable key for a set of archive roots, including their mtimes.

    A new file, a rename or a delete bumps the containing directory's mtime, so
    the root mtimes are enough to know the walk is still valid. Recursive
    mtimes would defeat the purpose, because that is the walk itself.
    """
    parts = []
    for directory in sorted({str(d) for d in dirs}):
        try:
            parts.append(f"{directory}\0{int(os.stat(directory).st_mtime)}")
        except OSError:
            parts.append(f"{directory}\0-1")
    return hashlib.sha1("\n".join(parts).encode("utf-8", "replace")).hexdigest()


def _read_archive_scan_cache(dirs: Sequence[Path]) -> Optional[List[Dict[str, Any]]]:
    """The cached walk for these archive roots, or None when it is stale."""
    try:
        payload = json.loads(ARCHIVE_SCAN_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("signature") != _archive_dirs_signature(dirs):
        return None
    rows = payload.get("mixes")
    if not isinstance(rows, list) or not rows:
        return None
    # A cached path can disappear between runs (unmounted drive, deleted mix).
    # Drop those rather than handing the UI a broken row.
    alive = [row for row in rows
             if isinstance(row, dict) and row.get("path") and os.path.isfile(row["path"])]
    return alive or None


def _write_archive_scan_cache(dirs: Sequence[Path], rows: Sequence[Dict[str, Any]]) -> None:
    """Persist the walk for the next --list-mixes / --get-latest-mix call."""
    try:
        tmp = ARCHIVE_SCAN_CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "signature": _archive_dirs_signature(dirs),
            "generated": time.time(),
            "mixes": list(rows),
        }, ensure_ascii=False), encoding="utf-8")
        tmp.replace(ARCHIVE_SCAN_CACHE)
    except (OSError, TypeError, ValueError):
        pass


class MixArchiveScanner:
    def __init__(self, base_dir: Union[Path, str]):
        self.base_dir = Path(base_dir)
        self.mixes: List[Dict[str, Any]] = []
        self._load_and_scan()

    def _get_archive_dirs(self) -> List[Path]:
        dirs, seen = [], set()
        def add(p_str):
            if not p_str: return
            try:
                p = Path(os.path.expanduser(str(p_str).strip().strip('"\''))).resolve()
                if p.is_dir() and str(p) not in seen: seen.add(str(p)); dirs.append(p)
            except Exception: pass
        def parse(ex):
            if ex:
                for pt in re.split(r'[;\n,]|(?<!^[a-zA-Z]):(?![\\/])', ex): add(pt)
        add(os.environ.get("MIX_ARCHIVE_DIR"))
        parse(os.environ.get("EXTRA_MIX_ARCHIVE_DIRS") or os.environ.get("MIX_ARCHIVE_DIRS"))
        efs = [self.base_dir / "config.env", CONFIG_DIR / "config.env", Path(__file__).resolve().parent / "config.env"]
        if sys.platform == "win32": efs.append(Path("B:/MIX_ARCHIVE/config.env"))
        for ef in efs:
            cfg = parse_env_file(ef)
            add(cfg.get("MIX_ARCHIVE_DIR"))
            parse(cfg.get("EXTRA_MIX_ARCHIVE_DIRS") or cfg.get("MIX_ARCHIVE_DIRS"))
        for n in ("MIX_ARCHIVE", "MP_SAMPLES", "FLAC_CONVERTED_OUTPUTS", "MP3_CONVERTED_OUTPUTS", "WAV_CONVERTED_OUTPUTS"): add(self.base_dir / n)
        if sys.platform == "win32":
            for d in ("B:/MIX_ARCHIVE", "B:/MIX_ARCHIVE/FLAC_CONVERTED_OUTPUTS", "X:/MIX_ARCHIVE"): add(d)
            for l in "BDEFGNVXZ": add(f"{l}:/MIX_ARCHIVE")
        else:
            for p in ("/run/media/mplanetarian/WD BLACK B/MIX_ARCHIVE", "/run/media/mplanetarian/DATA/MIX_ARCHIVE2/FLAC_CONVERTED_OUTPUTS", os.path.expanduser("~/GoogleDrive/MIX_ARCHIVE")): add(p)
        if not dirs:
            for fb in (self.base_dir / "MIX_ARCHIVE", self.base_dir / "FLAC_CONVERTED_OUTPUTS", self.base_dir, Path.home() / "Music"):
                if fb not in (Path.home(), Path("/")): add(fb)
        return dirs

    def _load_and_scan(self):
        # os.scandir carries the directory entry's type and stat data with it, so
        # a library walk costs one syscall per file instead of one per entry plus
        # an os.stat. Sorting the walk with os.walk was measurable on a share.
        #
        # The walk result is cached beside the playlist cache, keyed by the
        # archive directory mtimes. The manager shell calls --get-latest-mix and
        # --list-mixes on every menu paint, and each call used to re-walk every
        # archive (seconds on a Raspberry Pi SD card, worse on an SMB mount).
        archive_dirs = [d for d in self._get_archive_dirs()
                        if d.is_dir() and d not in (Path.home(), Path("/"))]
        cached = _read_archive_scan_cache(archive_dirs)
        if cached is not None:
            self.mixes = cached
            return

        seen, collected = set(), []
        skip = {'__pycache__', 'node_modules', '.git', 'venv', '.venv'}
        add = collected.append
        for ad in archive_dirs:
            stack = [str(ad)]
            while stack:
                current = stack.pop()
                try:
                    with os.scandir(current) as entries:
                        for entry in entries:
                            try:
                                if entry.is_dir(follow_symlinks=False):
                                    if not entry.name.startswith('.') and entry.name not in skip:
                                        stack.append(entry.path)
                                    continue
                                if not entry.is_file(follow_symlinks=False):
                                    continue
                                ext = os.path.splitext(entry.name)[1].lower()
                                if ext not in AUDIO_EXTENSIONS or entry.path in seen:
                                    continue
                                seen.add(entry.path)
                                st = entry.stat()
                                add({"name": entry.name, "path": entry.path, "size_mb": st.st_size / 1048576.0,
                                     "mtime": st.st_mtime, "ext": ext[1:].upper()})
                            except OSError:
                                continue
                except OSError:
                    continue
        collected.sort(key=lambda x: x["mtime"], reverse=True)
        self.mixes = collected
        _write_archive_scan_cache(archive_dirs, collected)

    def get_mixes(self, query: str = "") -> List[Dict[str, Any]]:
            if not (q := query.lower().strip()): 
                return self.mixes
            tokens = q.split()

            # Build the searchable text once per playlist object, not per keystroke.
            if not hasattr(self, '_search_cache_initialized') or self._search_cache_initialized != id(self.mixes):
                self._cached_search_data = _search_index(self.mixes)
                self._search_cache_initialized = id(self.mixes)

            results = []
            for m, raw_text, clean_text in self._cached_search_data:
                if all(t in raw_text or t in clean_text for t in tokens):
                    results.append(m)

            return results
    def get_latest_mix(self) -> Optional[Dict[str, Any]]:
        # The bundled first-run sample is copied into the app folder and can be
        # newer than a mix archive that was just configured. Play the archive.
        for mix in self.mixes:
            if not _is_bundled_sample(mix.get("path")):
                return mix
        return self.mixes[0] if self.mixes else None

class MusicFolderPlaylistScanner:
    def __init__(self, folder_name: Union[str, Path, None] = "ALL", playlist_path: Union[str, Path, None] = None):
        folder_text = str(folder_name) if folder_name else ""
        # A playlist file can be passed in the folder slot. A mounted share directory
        # with the same name as the folder id must not be treated as that file.
        if folder_text and (folder_text.endswith((".json", ".m3u", ".m3u8")) or os.path.isfile(folder_text)):
            playlist_path, folder_name = folder_name, "ALL"
            if (m := re.match(r'^DJ_Tracks_(.+)\.(?:json|m3u8?)$', os.path.basename(str(playlist_path)), re.I)) and m.group(1).upper() != "ALL_MUSIC_FOLDERS":
                folder_name = m.group(1)
        self.folder_name = str(folder_name or "ALL").upper()
        self.base_dir = get_base_dir()
        self.mixes: List[Dict[str, Any]] = []
        self.playlist_path = Path(playlist_path) if playlist_path else None
        self._load_playlist()

    def _load_playlist(self):
        pl_dir = self.base_dir / "PLAYLISTS_GENERATED"
        if not self.playlist_path or not self.playlist_path.is_file():
            name = self.folder_name.upper()
            if name in ("ALL", "ALL_MUSIC_FOLDERS"):
                candidates = (
                    pl_dir / "DJ_Tracks_ALL_MUSIC_FOLDERS.json",
                    pl_dir / "DJ_Tracks_ALL_MUSIC_FOLDERS.m3u8",
                )
            else:
                # A missing folder playlist must not open the combined list instead.
                candidates = (
                    pl_dir / f"DJ_Tracks_{name}.json",
                    pl_dir / f"DJ_Tracks_{name}.m3u8",
                )
            for c in candidates:
                if c.is_file():
                    self.playlist_path = c
                    break
        if not self.playlist_path or not self.playlist_path.is_file(): return
        if str(self.playlist_path).endswith(".json"):
            data = load_playlist_json(self.playlist_path)
            if data is not None:
                self.mixes = data
                return
        try:
            cur_t, cur_d, now = "", "00:00", time.time()
            for line in self.playlist_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if not (line := line.strip()): continue
                if line.startswith("#EXTINF:"):
                    p = line[8:]
                    if "," in p:
                        dp, cur_t = p.split(",", 1)
                        try: cur_d = format_seconds(float(dp.strip())) if float(dp.strip()) > 0 else "00:00"
                        except ValueError: cur_d = "00:00"
                    else: cur_t, cur_d = p, "00:00"
                elif not line.startswith("#"):
                    ext, bname = os.path.splitext(line)[1].lower(), os.path.basename(line)
                    self.mixes.append({"name": bname, "title": cur_t or os.path.splitext(bname)[0], "path": line, "ext": ext[1:].upper() if ext else "AUDIO", "size_mb": 0.0, "duration_fmt": cur_d, "mtime": now, "folder": self.folder_name})
                    cur_t, cur_d = "", "00:00"
        except Exception: pass

    def get_mixes(self, query: str = "") -> List[Dict[str, Any]]:
            if not (q := query.lower().strip()): 
                return self.mixes
            tokens = q.split()

            # Build the searchable text once per playlist object, not per keystroke.
            if not hasattr(self, '_search_cache_initialized') or self._search_cache_initialized != id(self.mixes):
                self._cached_search_data = _search_index(self.mixes)
                self._search_cache_initialized = id(self.mixes)

            results = []
            for m, raw_text, clean_text in self._cached_search_data:
                if all(t in raw_text or t in clean_text for t in tokens):
                    results.append(m)
            return results
    def get_latest_mix(self) -> Optional[Dict[str, Any]]: return self.mixes[0] if self.mixes else None

class FavoritesStore:
    """Saved mixes and music tracks. The file is the Favorites playlist."""

    def __init__(self, path: Union[str, Path, None] = None):
        self.path = Path(path) if path else FAVORITES_FILE
        self._tracks: List[Dict[str, Any]] = []
        self._keys: set = set()
        self.load()

    def load(self):
        self._tracks, self._keys = [], set()
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        rows = data.get("tracks") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict) or not row.get("path"):
                continue
            item = self._normalize(row)
            key = favorite_key(item["path"])
            if not key or key in self._keys:
                continue
            self._keys.add(key)
            self._tracks.append(item)

    def tracks(self) -> List[Dict[str, Any]]:
        return [dict(track) for track in self._tracks]

    def clear(self) -> int:
        """Empty the Favorites playlist. The audio files are left untouched."""
        removed = len(self._tracks)
        self._tracks, self._keys = [], set()
        if removed:
            self.save()
        return removed

    def add_tracks(self, tracks: Sequence[Dict[str, Any]]) -> int:
        """Append tracks that are not already in the one Favorites playlist."""
        added = 0
        for track in tracks or []:
            item = self._normalize(track)
            key = favorite_key(item.get("path"))
            if not key or key in self._keys:
                continue
            item["added"] = time.time()
            self._tracks.append(item)
            self._keys.add(key)
            added += 1
        if added:
            self.save()
        return added

    def replace_tracks(self, tracks: Sequence[Dict[str, Any]]) -> int:
        """Start the Favorites playlist over. Audio files stay on disk."""
        self._tracks, self._keys = [], set()
        count = 0
        for track in tracks or []:
            item = self._normalize(track)
            key = favorite_key(item.get("path"))
            if not key or key in self._keys:
                continue
            item["added"] = time.time()
            self._tracks.append(item)
            self._keys.add(key)
            count += 1
        self.save()
        return count

    def move(self, src: int, dst: int) -> bool:
        """Reorder one Favorites row. The audio file is not touched."""
        count = len(self._tracks)
        if not (0 <= src < count and 0 <= dst < count) or src == dst:
            return False
        item = self._tracks.pop(src)
        self._tracks.insert(dst, item)
        self.save()
        return True

    def __len__(self) -> int:
        return len(self._tracks)

    def is_favorite(self, path: Union[str, Path, None]) -> bool:
        return favorite_key(path) in self._keys

    def toggle(self, track: Dict[str, Any]) -> bool:
        """Return True when the track is now in the Favorites playlist."""
        item = self._normalize(track)
        key = favorite_key(item.get("path"))
        if not key:
            return False
        if key in self._keys:
            self._tracks = [row for row in self._tracks if favorite_key(row.get("path")) != key]
            self._keys.discard(key)
            self.save()
            return False
        item["added"] = time.time()
        self._tracks.append(item)
        self._keys.add(key)
        self.save()
        return True

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "name": "Favorites", "tracks": self._tracks}
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        self._write_m3u()

    def _write_m3u(self):
        m3u = self.path.with_suffix(".m3u8")
        lines = ["#EXTM3U", "#PLAYLIST:MP Favorites"]
        for track in self._tracks:
            name = str(track.get("name") or os.path.basename(str(track.get("path") or "")))
            lines.append(f"#EXTINF:-1,{name}")
            lines.append(str(track.get("path") or ""))
        tmp = m3u.with_name(m3u.name + ".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(m3u)

    def _normalize(self, track: Dict[str, Any]) -> Dict[str, Any]:
        path = str(track.get("path") or "")
        name = str(track.get("name") or os.path.basename(path) or "Unknown")
        ext = str(track.get("ext") or os.path.splitext(path)[1].lstrip(".")).upper() or "AUDIO"
        try:
            size = float(track.get("size_mb") or 0)
        except (TypeError, ValueError):
            size = 0.0
        return {
            "name": name,
            "path": path,
            "ext": ext,
            "size_mb": size,
            "duration_fmt": track.get("duration_fmt") or "00:00",
            "duration_sec": track.get("duration_sec") or 0,
            "source": str(track.get("source") or track.get("mode") or "").upper(),
            "mtime": track.get("mtime") or time.time(),
            "added": track.get("added") or time.time(),
        }

class FavoritesScanner:
    def __init__(self, store: Optional[FavoritesStore] = None):
        self.store = store or FavoritesStore()
        self.base_dir = get_base_dir()
        self.playlist_path = self.store.path
        self.folder_name = "FAVORITES"
        self.mixes = self.store.tracks()

    def reload(self):
        self.mixes = self.store.tracks()

    def get_mixes(self, query: str = "") -> List[Dict[str, Any]]:
        if not (q := query.lower().strip()):
            return self.mixes
        tokens = q.split()
        found = []
        for track in self.mixes:
            blob = " ".join(str(value).lower() for value in track.values())
            if all(token in blob for token in tokens):
                found.append(track)
        return found

    def get_latest_mix(self) -> Optional[Dict[str, Any]]:
        return self.mixes[0] if self.mixes else None

def load_engine_playlist(engine, playlist=None, mode: str = "MUSIC", playlist_path=None, folder_name=None, play: bool = False) -> int:
    """Replace the engine playlist. Favorites are read from the saved playlist file."""
    mode = str(mode or "MUSIC").upper()
    pl = list(playlist or [])
    if mode == "FAVORITES":
        if not pl:
            pl = FavoritesStore().tracks()
        mode = "FAVORITES"
    elif not pl:
        if mode == "MUSIC" or playlist_path or folder_name:
            pl = MusicFolderPlaylistScanner(folder_name or "ALL", playlist_path or "").get_mixes()
            mode = "MUSIC"
        else:
            pl = MixArchiveScanner(get_base_dir()).get_mixes()
            mode = "MIX_ARCHIVE"
    engine.playlist = pl
    engine.mode = mode
    # Remember where this playlist came from; the play log records it per row.
    if playlist_path:
        engine.playlist_path = str(playlist_path)
    current = ""
    track = getattr(engine, "current_track", None)
    if isinstance(track, dict):
        current = track.get("path") or ""
    match = next((i for i, row in enumerate(pl) if row.get("path") == current), None)
    if mode == "FAVORITES":
        engine.current_index = match if match is not None else 0
    if pl and play:
        engine.load_track(0, True)
    return len(pl)

class AudioResampler:
    def __init__(self):
        _ensure_numpy()
        self.reset()

    def reset(self):
        self.buffer = np.zeros((0, 2), dtype=np.float32)
        self.in_pos = 0.0

    def _to_stereo(self, arr: np.ndarray) -> np.ndarray:
        if arr.ndim == 1 or arr.shape[1] == 1:
            c = arr if arr.ndim == 1 else arr[:, 0]
            return np.column_stack((c, c)).astype(np.float32, copy=False)
        return arr[:, :2].astype(np.float32, copy=False)

    def process(self, read_func, out_frames: int, speed_ratio: float) -> np.ndarray:
        if abs(speed_ratio - 1.0) < 1e-4 and self.in_pos == 0.0 and len(self.buffer) == 0:
            raw = read_func(out_frames)
            return np.zeros((0, 2), dtype=np.float32) if len(raw) == 0 else self._to_stereo(raw)
        t_out = self.in_pos + np.arange(out_frames, dtype=np.float64) * speed_ratio
        needed = int(math.ceil(t_out[-1])) + 4
        while len(self.buffer) < needed:
            more = read_func(max(1024, needed - len(self.buffer)))
            if len(more) == 0: break
            sm = self._to_stereo(more)
            self.buffer = np.vstack((self.buffer, sm)) if len(self.buffer) else sm
        if len(self.buffer) == 0: return np.zeros((0, 2), dtype=np.float32)
        self.in_pos = t_out[-1] + speed_ratio
        if len(self.buffer) <= int(math.ceil(t_out[-1])):
            self.buffer = np.vstack((self.buffer, np.zeros((int(math.ceil(t_out[-1])) + 4 - len(self.buffer), 2), dtype=np.float32)))
        t_in = np.arange(len(self.buffer), dtype=np.float64)
        out = np.column_stack((np.interp(t_out, t_in, self.buffer[:, 0]), np.interp(t_out, t_in, self.buffer[:, 1]))).astype(np.float32)
        if (discard := int(math.floor(self.in_pos))) > 0:
            self.buffer = self.buffer[discard:]
            self.in_pos -= discard
        return out

class AudioSource:
    def read(self, frames: int) -> np.ndarray: raise NotImplementedError
    def seek(self, frame: int): raise NotImplementedError
    def tell(self) -> int: raise NotImplementedError
    def total_frames(self) -> int: raise NotImplementedError
    def close(self): raise NotImplementedError

class SoundFileAudioSource(AudioSource):
    def __init__(self, path: str, initial_sf: Optional[Any] = None):
        _ensure_audio_backends()
        self.path, self.sf, self._current_frame = str(path), initial_sf, 0
        self._read_error_reported = False
        # libsndfile keeps no lock of its own, and sf.close() frees the decoder
        # the reader is inside. Every entry point into `self.sf` takes this
        # lock, so a close cannot land in the middle of a read. Without it the
        # feeder thread segfaulted inside read_frame_ whenever the track
        # changed (or the theme switched while one was loading) because
        # _stop_feeder() closes the source as soon as the thread is asked to
        # stop, not after it has actually stopped.
        self._sf_lock = threading.RLock()
        self._closed = False
        if self.sf is None: self._open_with_retry(4)
        self.samplerate, self.channels = int(self.sf.samplerate), int(self.sf.channels)
        raw_frames = int(getattr(self.sf, "frames", 0))
        # Remember that the decoder could not tell us the length. The engine
        # then lets FFmpeg (which learns the end from the data) try first, and
        # this source still works as a fallback with an unknown length.
        self.length_unknown = raw_frames <= 0 or raw_frames >= UNKNOWN_FRAME_COUNT
        self.frames = sane_frame_count(raw_frames, self.samplerate)

    def _open_with_retry(self, max_retries: int = 4, target_frame: int = 0):
        _ensure_audio_backends()
        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
                # close() and the re-open happen under the same lock a reader
                # holds only for one read() call, never for the whole loop.
                with self._sf_lock:
                    if self._closed:
                        return
                    self._close_locked()
                    self.sf = sf.SoundFile(self.path)
                    if target_frame > 0:
                        try: self.sf.seek(target_frame)
                        except Exception: pass
                    try: self._current_frame = int(self.sf.tell())
                    except Exception: self._current_frame = target_frame
                return
            except Exception as e:
                last_exc = e
                # A path that no longer exists will not appear by retrying, and
                # the sleep below blocked the feeder thread for seconds per
                # call. Bail out immediately; only a live file is worth retrying
                # (a network mount can briefly refuse an open).
                if not os.path.exists(self.path):
                    break
                if attempt < max_retries: time.sleep(0.25 * attempt)
        if last_exc: raise last_exc

    def _close_locked(self):
        """Drop the soundfile handle. The caller already holds ``_sf_lock``."""
        handle, self.sf = self.sf, None
        if handle:
            try: handle.close()
            except Exception: pass

    def read(self, frames: int) -> np.ndarray:
        last_exc = None
        for attempt in range(1, 4):
            try:
                # Hold the lock across the whole libsndfile call. close() waits
                # for it, so the decoder cannot be freed under the reader.
                with self._sf_lock:
                    if self._closed:
                        # close() gave up waiting for this read and left the
                        # handle to us. Release it here, still under the lock.
                        self._close_locked()
                        return np.zeros((0, self.channels), dtype=np.float32)
                    if self.sf is None:
                        self._open_with_retry(3, self._current_frame)
                        if self.sf is None:
                            return np.zeros((0, self.channels), dtype=np.float32)
                    data = self.sf.read(frames, dtype='float32')
                    self._current_frame += len(data)
                    self._read_error_reported = False
                    return data
            except Exception as e:
                # Log only the failure that survives every retry. Logging inside
                # the loop turned one unreadable file into three entries per
                # read() call, and the feeder retried it thousands of times.
                last_exc = e
                if not os.path.exists(self.path):
                    break
                try: self._open_with_retry(3, self._current_frame)
                except Exception: pass
                if attempt < 3: time.sleep(0.15 * attempt)
        if last_exc is not None:
            self._log_missing_once(last_exc)
        return np.zeros((0, self.channels), dtype=np.float32)

    def _log_missing_once(self, exc: Exception) -> bool:
        """Report a permanently unreadable file once, not once per read.

        Returning True means "already reported". The flag is per-source-object,
        so a track that recovers after a transient stall still logs if it later
        fails for real, while a deleted file stops filling the log.
        """
        if getattr(self, "_read_error_reported", False):
            return True
        self._read_error_reported = True
        try: mp_log_error("MP_Audio_Player", f"Audio file unreadable, giving up: {self.path}", exc)
        except Exception: pass
        return False

    def seek(self, frame: int):
        target = max(0, min(frame, max(0, self.frames - 1) if self.frames > 0 else frame))
        for attempt in range(1, 4):
            try:
                with self._sf_lock:
                    if self._closed:
                        self._close_locked()
                        return
                    if self.sf is None:
                        self._open_with_retry(2, target)
                        return
                    self.sf.seek(target)
                    try: self._current_frame = int(self.sf.tell())
                    except Exception: self._current_frame = target
                return
            except Exception:
                try: self._open_with_retry(2, target); return
                except Exception:
                    if attempt == 3: self._current_frame = target

    def tell(self) -> int:
        try:
            with self._sf_lock:
                if self.sf: self._current_frame = int(self.sf.tell())
        except Exception: pass
        return self._current_frame

    def total_frames(self) -> int: return self.frames

    def close(self):
        """Close the handle without ever waiting on a stalled read.

        ``_stop_feeder()`` calls this as soon as the feeder thread is *asked*
        to stop, so the thread can still be inside libsndfile. Taking the lock
        here unconditionally would block the caller (the UI thread) for as long
        as a stalled network read, so the close is attempted with a short
        timeout instead: when the reader is mid-call it notices ``_closed`` on
        the way out and closes the handle itself.
        """
        acquired = self._sf_lock.acquire(timeout=0.5)
        try:
            self._closed = True
            if acquired:
                self._close_locked()
        finally:
            if acquired:
                self._sf_lock.release()

class FFmpegAudioSource(AudioSource):
    def __init__(self, path: str, target_samplerate: int = 44100):
        _ensure_audio_backends()
        self.path, self.samplerate, self.channels, self._current_frame, self.proc = str(path), target_samplerate, 2, 0, None
        self.frames = self._probe_frames()
        self._start_ffmpeg(0.0)

    def _probe_frames(self) -> int:
        try:
            out = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate:format=duration", "-of", "default=noprint_wrappers=1:nokey=1", self.path], stderr=subprocess.DEVNULL, timeout=6).decode().splitlines()
            if len(out) >= 2 and out[0].strip().isdigit(): self.samplerate = int(out[0].strip())
            dur = float(out[-1].strip()) if out else 0.0
            return int(dur * self.samplerate)
        except Exception: return 0

    def _start_ffmpeg(self, start_sec: float = 0.0):
        self.close()
        cmd = ["ffmpeg", "-v", "error"] + (["-ss", f"{start_sec:.3f}"] if start_sec > 0 else []) + ["-i", self.path, "-vn", "-f", "f32le", "-ac", "2", "-ar", str(self.samplerate), "pipe:1"]
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=131072)
            self._current_frame = int(start_sec * self.samplerate)
        except Exception as e:
            try: mp_log_error("MP_Audio_Player", f"Failed starting FFmpeg reader: {self.path}", e)
            except Exception: pass

    def read(self, frames: int) -> np.ndarray:
        if not self.proc or not self.proc.stdout: return np.zeros((0, 2), dtype=np.float32)
        try:
            if not (raw := self.proc.stdout.read(frames * 8)): return np.zeros((0, 2), dtype=np.float32)
            arr = np.frombuffer(raw, dtype=np.float32)
            nf = len(arr) // 2
            self._current_frame += nf
            return arr[:nf * 2].reshape((nf, 2))
        except Exception: return np.zeros((0, 2), dtype=np.float32)

    def seek(self, frame: int): self._start_ffmpeg(float(frame) / float(self.samplerate) if self.samplerate > 0 else 0.0)
    def tell(self) -> int: return self._current_frame
    def total_frames(self) -> int: return self.frames
    def close(self):
        proc, self.proc = self.proc, None
        if proc:
            try: proc.terminate(); proc.wait(timeout=0.3)
            except Exception:
                try: proc.kill(); proc.wait(timeout=0.3)
                except Exception: pass
            # Release the pipe handles so a track change does not leak them.
            for pipe in (proc.stdout, proc.stderr):
                try:
                    if pipe: pipe.close()
                except Exception: pass

class DSDiffAudioSource(AudioSource):
    """Play a DSDIFF (.dff) file, which FFmpeg cannot demux but can decode.

    The payload is re-blocked into DSF on the fly by dsdiff_reader and piped to
    FFmpeg with -f dsf. Seeking restarts the pipe from the matching block, so
    scrubbing works without writing a converted copy to disk.
    """

    def __init__(self, path: str, target_samplerate: int = 0):
        _ensure_audio_backends()
        self.path, self.proc, self._current_frame = str(path), None, 0
        self._feeder: Optional[threading.Thread] = None
        try:
            self.info = dsdiff_reader.read_info(self.path)
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc
        if self.info.is_compressed:
            raise RuntimeError(f"{Path(self.path).name} uses DST compression, which FFmpeg cannot decode")
        self.channels = max(1, min(2, self.info.channels))
        # The DSF copy FFmpeg sees is 1-bit DSD; its "sample rate" is the DSD
        # bit rate. Decode straight to 44.1 kHz like the other FFmpeg paths.
        self.samplerate = target_samplerate or 44100
        self.frames = int(self.info.duration * self.samplerate)
        self._start_pipe(0)

    def _block_frame(self) -> int:
        return dsdiff_reader.DSF_BLOCK_SIZE

    def _start_pipe(self, start_frame: int):
        self.close()
        start_sec = (float(start_frame) / self.samplerate) if self.samplerate > 0 else 0.0
        total = max(1, dsdiff_reader.block_count(self.info))
        start_block = min(total - 1, max(0, int(start_sec * self.samplerate / dsdiff_reader.DSF_BLOCK_SIZE)))
        cmd = ["ffmpeg", "-v", "error", "-f", "dsf", "-i", "pipe:0", "-vn",
               "-f", "f32le", "-ac", "2", "-ar", str(self.samplerate), "pipe:1"]
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, bufsize=131072)
        except Exception as exc:
            try: mp_log_error("MP_Audio_Player", f"Failed starting DSDIFF reader: {self.path}", exc)
            except Exception: pass
            return
        self._current_frame = start_block * self._block_frame()
        self._feeder = threading.Thread(target=self._feed, args=(start_block,), daemon=True,
                                        name="DSDiffFeeder")
        self._feeder.start()

    def _feed(self, start_block: int):
        """Push the re-blocked DSF into FFmpeg without deadlocking on a full pipe."""
        proc = self.proc
        if not proc or not proc.stdin:
            return
        try:
            for chunk in dsdiff_reader.iter_dsf_stream(self.path, self.info, start_block=start_block):
                if proc is not self.proc or proc.poll() is not None:
                    break
                proc.stdin.write(chunk)
            try: proc.stdin.close()
            except Exception: pass
        except Exception:
            # A closed stdin means FFmpeg exited (track change or stop); the
            # reader thread sees EOF and the engine moves on.
            try: proc.stdin.close()
            except Exception: pass

    def read(self, frames: int) -> np.ndarray:
        if not self.proc or not self.proc.stdout: return np.zeros((0, 2), dtype=np.float32)
        try:
            if not (raw := self.proc.stdout.read(frames * 8)): return np.zeros((0, 2), dtype=np.float32)
            arr = np.frombuffer(raw, dtype=np.float32)
            nf = len(arr) // 2
            self._current_frame += nf
            return arr[:nf * 2].reshape((nf, 2))
        except Exception: return np.zeros((0, 2), dtype=np.float32)

    def seek(self, frame: int):
        self._start_pipe(max(0, int(frame)))
    def tell(self) -> int: return self._current_frame
    def total_frames(self) -> int: return self.frames
    def close(self):
        proc, self.proc = self.proc, None
        if proc:
            try:
                if proc.stdin: proc.stdin.close()
            except Exception: pass
            try: proc.terminate(); proc.wait(timeout=1.0)
            except Exception:
                try: proc.kill(); proc.wait(timeout=1.0)
                except Exception: pass
            for pipe in (proc.stdout, proc.stderr):
                try:
                    if pipe: pipe.close()
                except Exception: pass
        self._feeder = None

def open_audio_source(path: str) -> AudioSource:
    # A .dff that fails DSDIFF parsing is expected input: the file may be a
    # different container, or corrupt. Falling through to FFmpeg is the normal
    # path, so it is not an error. The original cause is attached to the final
    # exception only when every backend fails, which is worth a log entry.
    dsdiff_exc: Optional[Exception] = None
    if dsdiff_reader is not None and dsdiff_reader.is_dsdiff(path):
        try: return DSDiffAudioSource(path)
        except Exception as exc: dsdiff_exc = exc
    order = (FFmpegAudioSource, SoundFileAudioSource) if prefers_ffmpeg(path) else (SoundFileAudioSource, FFmpegAudioSource)
    last_exc: Exception = RuntimeError(f"No audio backend could open {path}")
    # A container that does not declare its length (FLAC STREAMINFO total = 0)
    # opens fine in libsndfile but reports SF_COUNT_MAX and raises on the final
    # partial read. Keep that source as a last resort, but prefer FFmpeg, which
    # discovers the end from the data and ends the track cleanly.
    fallback: Optional[AudioSource] = None
    for cls in order:
        try: src = cls(path)
        except Exception as e:
            last_exc = e
            continue
        if getattr(src, "length_unknown", False):
            if fallback is None:
                fallback = src
            else:
                try: src.close()
                except Exception: pass
            continue
        if fallback is not None:
            try: fallback.close()
            except Exception: pass
        return src
    if fallback is not None:
        return fallback
    if dsdiff_exc is not None:
        raise RuntimeError(f"No audio backend could open {path} (DSDIFF: {dsdiff_exc})") from dsdiff_exc
    raise last_exc

class AudioPrefetchBuffer:
    def __init__(self, sample_rate: int, buffer_sec: float = 12.0):
        self.sample_rate, self.max_frames = sample_rate, int(buffer_sec * sample_rate)
        self.chunks, self.chunk_offset, self.available_frames, self.eof = collections.deque(), 0, 0, False
        self.lock = threading.Lock()
        self.not_empty, self.not_full = threading.Condition(self.lock), threading.Condition(self.lock)

    def clear(self):
        with self.lock:
            self.chunks.clear()
            self.chunk_offset = self.available_frames = 0
            self.eof = False
            self.not_full.notify_all()

    def set_eof(self):
        with self.lock: self.eof = True; self.not_empty.notify_all()
    def is_empty(self) -> bool:
        with self.lock: return self.available_frames == 0
    def health_sec(self) -> float:
        with self.lock: return float(self.available_frames) / float(self.sample_rate) if self.sample_rate > 0 else 0.0

    def _to_stereo(self, arr: np.ndarray) -> np.ndarray:
        # The feeder can be the first thing to run in a process (a unit test
        # constructs it directly), and numpy is imported lazily here. Without
        # this the thread died with "'NoneType' has no attribute float32" and
        # silently produced silence.
        _ensure_numpy()
        if arr.ndim == 1: return np.column_stack((arr, arr)).astype(np.float32, copy=False)
        if arr.shape[1] == 1: return np.column_stack((arr[:, 0], arr[:, 0])).astype(np.float32, copy=False)
        return arr[:, :2].astype(np.float32, copy=False)

    def write(self, data: np.ndarray, timeout: float = 0.5) -> bool:
        if len(data) == 0: return True
        ds = self._to_stereo(data)
        n = len(ds)
        with self.lock:
            while self.available_frames + n > self.max_frames:
                if not self.not_full.wait(timeout=timeout): return False
            self.chunks.append(ds)
            self.available_frames += n
            self.not_empty.notify_all()
            return True

    def read(self, frames: int) -> np.ndarray:
        with self.lock:
            if self.available_frames == 0: return np.zeros((0, 2), dtype=np.float32)
            n, parts, col = min(frames, self.available_frames), [], 0
            while col < n and self.chunks:
                front = self.chunks[0]
                avail, needed = len(front) - self.chunk_offset, n - col
                if avail <= needed:
                    parts.append(front[self.chunk_offset:])
                    col += avail
                    self.chunks.popleft()
                    self.chunk_offset = 0
                else:
                    parts.append(front[self.chunk_offset:self.chunk_offset + needed])
                    self.chunk_offset += needed
                    col += needed
                    break
            self.available_frames -= col
            self.not_full.notify_all()
            return parts[0] if len(parts) == 1 else (np.vstack(parts) if parts else np.zeros((0, 2), dtype=np.float32))

class AudioFeederThread(threading.Thread):
    def __init__(self, source: AudioSource, audio_buffer: AudioPrefetchBuffer, engine: Any, chunk_frames: int = 16384):
        super().__init__(name="AudioFeederThread", daemon=True)
        self.source, self.audio_buffer, self.engine, self.chunk_frames = source, audio_buffer, engine, chunk_frames
        self._running, self._seek_target, self.current_frame = True, None, 0
        self._seek_lock, self._wake_cond = threading.Lock(), threading.Condition(threading.Lock())

    def request_seek(self, frame: int):
        with self._seek_lock: self._seek_target = frame
        with self._wake_cond: self._wake_cond.notify_all()
        with self.audio_buffer.lock: self.audio_buffer.not_full.notify_all()

    def stop(self):
        self._running = False
        with self._wake_cond: self._wake_cond.notify_all()
        with self.audio_buffer.lock:
            self.audio_buffer.not_full.notify_all()
            self.audio_buffer.not_empty.notify_all()

    def run(self):
        cs = self.chunk_frames
        while self._running:
            # The engine drops its reference on stop/load, so a shutdown or a
            # track change can leave this thread without a source. That is a
            # normal race, not a playback error worth a traceback.
            source = self.source
            if source is None:
                break
            with self._seek_lock:
                st, self._seek_target = self._seek_target, None
            if st is not None:
                try: source.seek(st); self.current_frame = st; self.audio_buffer.clear()
                except Exception as e:
                    if not self._running: break
                    try: mp_log_error("MP_Audio_Player", f"Feeder seek error: {e}", e)
                    except Exception: pass
            if self.audio_buffer.available_frames >= self.audio_buffer.max_frames:
                with self._wake_cond: self._wake_cond.wait(timeout=0.08)
                continue
            try: chunk = source.read(cs)
            except Exception: time.sleep(0.2); continue
            if len(chunk) > 0:
                self.current_frame = source.tell()
                while self._running and self._seek_target is None and not self.audio_buffer.write(chunk, timeout=0.1): pass
            else:
                cur_pos, tot = source.tell(), source.total_frames()
                if tot > 0 and cur_pos < (tot - cs * 2): time.sleep(0.15); continue
                got = False
                for _ in range(4):
                    time.sleep(0.1)
                    try:
                        if len(vc := source.read(cs)) > 0:
                            self.audio_buffer.write(vc); self.current_frame = source.tell(); got = True; break
                    except Exception: pass
                if got: continue
                if tot <= 0 or cur_pos >= (tot - cs * 2):
                    self.audio_buffer.set_eof()
                    while self._running and self._seek_target is None:
                        with self._wake_cond: self._wake_cond.wait(timeout=0.2)

EQ_BAND_FREQS: List[float] = [31.25, 62.5, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0]
EQ_BAND_LABELS: List[str] = ["31Hz", "62Hz", "125Hz", "250Hz", "500Hz", "1kHz", "2kHz", "4kHz", "8kHz", "16kHz"]
EQ_PRESET_FILE = CONFIG_DIR / "eq_preset"
EQ_CUSTOM_FILE = CONFIG_DIR / "custom_eq_presets.json"
EQ_ACTIVE_BANDS_FILE = CONFIG_DIR / "eq_active_bands.json"
EQ_INIT_FLAG_FILE = CONFIG_DIR / ".first_run_eq_initialized"

DEFAULT_EQ_PRESETS: Dict[str, List[float]] = {
    "Flat": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "Ambient": [3.0, 2.5, 1.5, 0.0, -1.0, -1.5, -0.5, 1.0, 2.0, 2.5],
    "Trance": [4.0, 5.0, 2.0, -1.5, 0.0, 1.5, 2.5, 3.5, 4.0, 3.0],
    "Techno": [5.0, 4.5, 2.0, -2.5, -2.0, -0.5, 1.0, 2.5, 3.0, 1.5],
    "House": [3.5, 4.0, 3.0, -1.0, -1.0, 0.0, 1.5, 2.0, 3.0, 2.0],
    "Progressive": [3.0, 4.0, 2.5, -1.0, -1.0, 0.0, 1.5, 2.0, 2.5, 3.0],
    "Vapourwave": [-2.0, 1.0, 2.5, 3.5, 1.5, 0.0, -1.0, -2.5, -4.0, -6.0],
    "Sensorium Interface": [4.5, 4.0, 1.5, -1.0, 0.5, 1.5, 2.5, 3.5, 4.0, 3.0],
}

EQ_PRESET_ALIASES: Dict[str, str] = {
    "vaporwave": "Vapourwave",
    "vapor": "Vapourwave",
    "vapour": "Vapourwave",
    "progressive house": "Progressive",
    "prog": "Progressive",
    "default": "Flat",
    "sensorium": "Sensorium Interface",
    "sensorium interface": "Sensorium Interface",
    "sensorium_interface": "Sensorium Interface",
    "earth flipper": "Sensorium Interface",
    "earth_flipper": "Sensorium Interface",
}


class EqualizerFilter:
    def __init__(self, num_bands: int = 10, filter_len: int = 256):
        _ensure_numpy()
        self.band_freqs = np.array(EQ_BAND_FREQS, dtype=np.float32)
        self.filter_len = filter_len
        self.fft_len = 1024 + filter_len
        self.fft_n = int(2 ** np.ceil(np.log2(self.fft_len)))
        self.overlap = np.zeros((filter_len - 1, 2), dtype=np.float32)
        self.H = None
        self.is_flat = True
        self.current_sr = 44100
        self._lock = threading.Lock()

    def set_gains(self, gains_db: Sequence[float], sr: int = 44100):
        with self._lock:
            g = np.array(gains_db, dtype=np.float32)
            if np.all(np.abs(g) < 0.05):
                self.is_flat = True
                self.H = None
                return
            self.is_flat = False
            self.current_sr = int(sr if sr and sr > 0 else 44100)
            fft_freqs = np.fft.rfftfreq(self.filter_len, 1.0 / self.current_sr)
            log_bands = np.log10(self.band_freqs)
            log_fft = np.log10(np.maximum(10.0, fft_freqs))
            gains_interp_db = np.interp(log_fft, log_bands, g)
            lin_gain = 10.0 ** (gains_interp_db / 20.0)
            fir = np.fft.irfft(lin_gain, n=self.filter_len)
            fir = np.fft.fftshift(fir) * np.hanning(self.filter_len)
            fir_sum = np.sum(fir)
            if abs(fir_sum) > 1e-4:
                fir = fir / fir_sum
            self.H = np.fft.rfft(fir, n=self.fft_n).astype(np.complex64)[:, None]

    def process(self, chunk: np.ndarray) -> np.ndarray:
        with self._lock:
            if self.is_flat or self.H is None:
                return chunk
            n = len(chunk)
            if n == 0:
                return chunk
            if len(self.overlap) != self.filter_len - 1:
                self.overlap = np.zeros((self.filter_len - 1, 2), dtype=np.float32)
            ext = np.vstack([self.overlap, chunk])
            conv = np.fft.irfft(np.fft.rfft(ext, n=self.fft_n, axis=0) * self.H, n=self.fft_n, axis=0)
            self.overlap = chunk[-(self.filter_len - 1):].copy()
            delay = (self.filter_len - 1) // 2
            return np.clip(conv[delay : delay + n], -1.0, 1.0)


class EqualizerManager:
    def __init__(self):
        self.num_bands = 10
        self.band_freqs = list(EQ_BAND_FREQS)
        self.band_labels = list(EQ_BAND_LABELS)
        self.custom_presets: Dict[str, List[float]] = {}
        self.active_preset: str = "Flat"
        self.current_bands: List[float] = [0.0] * 10
        self._load()

    def _load(self):
        if not EQ_INIT_FLAG_FILE.is_file() and not EQ_PRESET_FILE.is_file():
            self.apply_preset("Flat", save=True)
            try:
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                EQ_INIT_FLAG_FILE.touch(exist_ok=True)
            except Exception: pass
            return

        if EQ_CUSTOM_FILE.is_file():
            try:
                data = json.loads(EQ_CUSTOM_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for k, v in data.items():
                        if isinstance(v, (list, tuple)) and len(v) == self.num_bands:
                            self.custom_presets[str(k)] = [max(-12.0, min(12.0, float(x))) for x in v]
            except Exception: pass

        preset_name = "Flat"
        if EQ_PRESET_FILE.is_file():
            try:
                saved = EQ_PRESET_FILE.read_text(encoding="utf-8").strip()
                if saved: preset_name = saved
            except Exception: pass

        self.apply_preset(preset_name, save=False)
        if self.active_preset == "Custom" and EQ_ACTIVE_BANDS_FILE.is_file():
            try:
                ab = json.loads(EQ_ACTIVE_BANDS_FILE.read_text(encoding="utf-8"))
                if isinstance(ab, (list, tuple)) and len(ab) == self.num_bands:
                    self.current_bands = [max(-12.0, min(12.0, float(x))) for x in ab]
            except Exception: pass

    def get_all_presets(self) -> Dict[str, List[float]]:
        p = dict(DEFAULT_EQ_PRESETS)
        p.update(self.custom_presets)
        return p

    def get_all_preset_names(self) -> List[str]:
        names = [k for k in DEFAULT_EQ_PRESETS.keys()]
        for c in sorted(self.custom_presets.keys()):
            if c not in names:
                names.append(c)
        return names

    def resolve_preset_name(self, name: str) -> Optional[str]:
        if not name: return None
        nl = name.strip().lower()
        if nl in EQ_PRESET_ALIASES:
            return EQ_PRESET_ALIASES[nl]
        for k in self.get_all_presets().keys():
            if k.lower() == nl:
                return k
        return None

    def apply_preset(self, name: str, save: bool = True) -> bool:
        resolved = self.resolve_preset_name(name)
        if resolved and resolved in self.get_all_presets():
            self.active_preset = resolved
            self.current_bands = list(self.get_all_presets()[resolved])
            if save:
                try: EQ_PRESET_FILE.write_text(self.active_preset, encoding="utf-8")
                except Exception: pass
                try: EQ_ACTIVE_BANDS_FILE.write_text(json.dumps(self.current_bands), encoding="utf-8")
                except Exception: pass
            return True
        if name.strip().lower() == "custom":
            self.active_preset = "Custom"
            return True
        self.active_preset = "Flat"
        self.current_bands = list(DEFAULT_EQ_PRESETS["Flat"])
        if save:
            try: EQ_PRESET_FILE.write_text("Flat", encoding="utf-8")
            except Exception: pass
            try: EQ_ACTIVE_BANDS_FILE.write_text(json.dumps(self.current_bands), encoding="utf-8")
            except Exception: pass
        return False

    def cycle_preset(self, direction: int = 1) -> str:
        names = self.get_all_preset_names()
        cur = self.active_preset
        if cur in names:
            idx = (names.index(cur) + direction) % len(names)
        else:
            idx = 0
        self.apply_preset(names[idx])
        return self.active_preset

    def set_band(self, index: int, gain_db: float, step: bool = False) -> bool:
        if not (0 <= index < self.num_bands): return False
        new_val = (self.current_bands[index] + gain_db) if step else gain_db
        new_val = round(max(-12.0, min(12.0, float(new_val))), 1)
        self.current_bands[index] = new_val
        self._check_if_preset_matched()
        try: EQ_ACTIVE_BANDS_FILE.write_text(json.dumps(self.current_bands), encoding="utf-8")
        except Exception: pass
        return True

    def set_bands(self, gains: Sequence[float]) -> bool:
        if len(gains) != self.num_bands: return False
        self.current_bands = [round(max(-12.0, min(12.0, float(x))), 1) for x in gains]
        self._check_if_preset_matched()
        try: EQ_ACTIVE_BANDS_FILE.write_text(json.dumps(self.current_bands), encoding="utf-8")
        except Exception: pass
        return True

    def _check_if_preset_matched(self):
        for name, preset_gains in self.get_all_presets().items():
            if all(abs(a - b) < 0.1 for a, b in zip(self.current_bands, preset_gains)):
                self.active_preset = name
                try: EQ_PRESET_FILE.write_text(self.active_preset, encoding="utf-8")
                except Exception: pass
                return
        self.active_preset = "Custom"
        try: EQ_PRESET_FILE.write_text("Custom", encoding="utf-8")
        except Exception: pass

    def save_custom_preset(self, name: str, gains: Optional[Sequence[float]] = None) -> bool:
        name = str(name).strip()
        if not name: name = "Custom 1"
        if name.lower() in ("flat", "ambient", "trance", "techno", "house", "progressive", "vapourwave", "vaporwave"):
            name = f"{name} (User)"
        band_vals = [round(max(-12.0, min(12.0, float(x))), 1) for x in (gains if gains is not None else self.current_bands)]
        self.custom_presets[name] = band_vals
        self.active_preset = name
        self.current_bands = list(band_vals)
        try:
            EQ_CUSTOM_FILE.write_text(json.dumps(self.custom_presets, indent=2), encoding="utf-8")
            EQ_PRESET_FILE.write_text(self.active_preset, encoding="utf-8")
            EQ_ACTIVE_BANDS_FILE.write_text(json.dumps(self.current_bands), encoding="utf-8")
        except Exception: pass
        return True

    def delete_custom_preset(self, name: str) -> bool:
        if name in self.custom_presets:
            del self.custom_presets[name]
            try: EQ_CUSTOM_FILE.write_text(json.dumps(self.custom_presets, indent=2), encoding="utf-8")
            except Exception: pass
            if self.active_preset == name:
                self.apply_preset("Flat")
            return True
        return False

    def is_flat(self) -> bool:
        return all(abs(x) < 0.05 for x in self.current_bands)

    def get_current_bands(self) -> List[float]:
        return list(self.current_bands)


# ---------------------------------------------------------------------------
# MP Bass Boost
#
# Skullcandy's Crusher line (Crusher ANC 2, Crusher Evo, Crusher 1080 ANC) does
# not boost bass with a shelf or a plain low-pass. The left ear cup carries a
# second "Crusher" driver fed by a dedicated bass amplifier, and the dial on
# the cup (press to cycle the presets, scroll for fine steps) changes how hard
# that driver is driven. SoundGuys' lab measurements of the Crusher ANC 2 show
# the result: a boost centred at roughly 45 Hz that tapers off below about
# 30 Hz (the sub-bass is deliberately rolled back), is about +20 dB at 50 Hz
# with Crusher at 80 %, and is gone by roughly 400-500 Hz so voices and hi-hats
# stay intact. The app can also switch Crusher mode off completely.
#
# We reproduce the measurable part of that behaviour - the response curve -
# with a minimum-phase FIR built from the same shape, scaled in two steps: the
# dial opens on Normal (off), then Medium at a third and High at two thirds of
# the headphone's full boost (about +25 dB). The last third is deliberately not
# offered: at 100 % a loud mix is pushed so far into the limiter that the boost
# stops sounding like the headphones and starts sounding like a broken mix. The
# extra driver cannot be emulated through a stereo DAC, so the physical
# head-rattle is the one part of the Crusher effect that stays on the
# headphones; everything the audio itself contributes is here.
#
# The whole dial is trimmed by BASS_BOOST_TRIM_DB (-3 dB) on top of that
# scaling, which halves the boost's acoustic power - the half-way point on a
# decibel scale. Normal is unaffected because its gain is already zero, so the
# dial keeps a true bypass. Medium lands at about +5.3 dB at 45 Hz. High takes
# that same half-power trim and then gives up another quarter of its acoustic
# power, which lands it at about +12.4 dB at 45 Hz.
# ---------------------------------------------------------------------------
CRUSHER_BASS_PEAK_HZ = 45.0
CRUSHER_BASS_FULL_GAIN_DB = 25.0
# Half the power of the headphone's boost: a -3.01 dB cut leaves exactly half
# the acoustic energy, which is the half-way point on a decibel scale. It is
# applied to the boost itself, not to the whole signal, so Normal stays a true
# bypass and the dial keeps its 1/3 and 2/3 steps.
BASS_BOOST_TRIM_DB = 3.0103
# High only. Three quarters of the power that the shared trim would have left
# it: 10*log10(4/3) dB off the High step, and nowhere else.
BASS_BOOST_HIGH_POWER = 0.75
BASS_BOOST_HIGH_EXTRA_TRIM_DB = round(-10.0 * math.log10(BASS_BOOST_HIGH_POWER), 4)
BASS_BOOST_MODES: Tuple[str, ...] = ("Normal", "Medium", "High")
BASS_BOOST_FRACTIONS: Dict[str, float] = {
    "Normal": 0.0,
    "Medium": 1.0 / 3.0,
    "High": 2.0 / 3.0,
}
BASS_BOOST_FILE = CONFIG_DIR / "bass_boost"
# Chip background and ink per mode: neutral blue, then green, then amber.
BASS_MODE_STYLE: Dict[str, Tuple[Tuple[int, int, int], Tuple[int, int, int]]] = {
    "Normal": ((18, 30, 54), (245, 250, 255)),
    "Medium": ((0, 46, 26), (150, 255, 170)),
    "High": ((56, 34, 0), (255, 206, 120)),
}


def theme_chip_style(
    theme_name: str,
    lift: float = 0.0,
    hue: Optional[Tuple[int, int, int]] = None,
) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
    """Background and ink for a filled button chip on ``theme_name``.

    The Liquid action row draws filled chips (VISUALIZATION, EXPORT TRACKS,
    BASS BOOST). Their backgrounds were fixed near-blacks, so on every theme
    the buttons read as black instead of belonging to the palette.

    The fill is the theme's second colour at a mid tone, so it is clearly the
    theme's hue and never a near-black. ``hue`` overrides that base for a
    control that owns its own colour (the bass modes), and is blended toward
    the theme so it still sits in the palette. ``lift`` brightens the chip
    toward the full hue and a little white, which is how an "on" chip shows
    its state. The ink is black or white picked from the fill's luminance.
    """
    try:
        script_dir = str(Path(__file__).resolve().parent / "scripts")
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        from mp_theme_palette import theme_colors
        accent, secondary, tertiary = theme_colors(theme_name)
    except Exception:
        accent, secondary, tertiary = (247, 37, 133), (166, 77, 255), (0, 229, 255)

    if hue is not None:
        base = tuple(int(round(h + (a - h) * 0.35)) for h, a in zip(hue, accent))
    else:
        base = secondary if sum(secondary) >= sum(accent) else accent
    # A theme with no usable hue (Into Darkness is pure black) would otherwise
    # paint a black chip, which is the bug this helper exists to fix. Lift such
    # a base to a neutral glass grey so the button is always visible.
    if sum(base) < 90:
        base = tuple(int(round(b * 0.25 + n * 0.75)) for b, n in zip(base, (150, 160, 185)))

    strength = 0.52
    fill = [int(round(c * strength)) for c in base]
    level = max(0.0, min(1.0, float(lift)))
    if level > 0.0:
        fill = [
            int(round(f + (b - f) * level * 0.55 + (255 - f) * level * 0.10))
            for f, b in zip(fill, base)
        ]
    fill = tuple(max(0, min(255, c)) for c in fill)
    lum = (fill[0] * 299 + fill[1] * 587 + fill[2] * 114) / 1000.0
    ink = (10, 12, 18) if lum >= 128 else (245, 250, 255)
    return fill, ink


def theme_chip(
    theme_name: str,
    text: str,
    bold: bool = True,
    lift: float = 0.0,
    hue: Optional[Tuple[int, int, int]] = None,
) -> str:
    """A filled button chip in the selected theme's colours."""
    (br, bg_, bb), (fr, fg_, fb) = theme_chip_style(theme_name, lift, hue)
    weight = "1;" if bold else ""
    return f"\033[{weight}48;2;{br};{bg_};{bb};38;2;{fr};{fg_};{fb}m{text}\033[0m"


def bass_mode_peak_db(mode: str) -> float:
    """Peak boost offered by a dial position, after the power trims.

    Every surface - the FIR design, the engine status, the CLI toast and
    ``--list-bass-modes`` - reads the level from here, so none of them can
    report the untrimmed Crusher number. Medium is the shared -3 dB trim.
    High takes that trim and then another quarter off its acoustic power.
    """
    fraction = BASS_BOOST_FRACTIONS.get(mode, 0.0)
    peak = fraction * CRUSHER_BASS_FULL_GAIN_DB - BASS_BOOST_TRIM_DB
    if mode == "High":
        peak -= BASS_BOOST_HIGH_EXTRA_TRIM_DB
    return round(max(0.0, peak), 2)


def crusher_bass_response_db(freqs, peak_db: float):
    """Crusher-mode magnitude response, in dB, at the given frequencies.

    Measured shape (SoundGuys, Crusher ANC 2): a broad peak at 45 Hz, roughly
    3 dB down by 30 Hz and by 80 Hz, tapering to nothing by ~400 Hz.
    """
    f = np.maximum(np.asarray(freqs, dtype=np.float64), 1e-6)
    octaves = np.log2(f / CRUSHER_BASS_PEAK_HZ)
    # Asymmetric in log-frequency: the sub-bass side falls faster, which is
    # what "the Crusher mode's volume tapers off those sub-bass frequencies"
    # describes in the measurements.
    sigma = np.where(f <= CRUSHER_BASS_PEAK_HZ, 0.80, 1.15)
    return float(peak_db) * np.exp(-(octaves / sigma) ** 2)


def bass_mode_chip(mode: str, bold: bool = True, theme_name: str = "",
                   face: Optional[str] = None) -> str:
    """A filled chip for the active bass mode, so every mode keeps its colour.

    This is the Liquid hosts' form: a 48;2 background block, like the
    VISUALIZATION button beside it. ``theme_name`` blends the mode hue into the
    selected theme so the chip sits in the palette instead of reading as a
    black block; without it the historic fixed fills are used, which keeps the
    CLI and ``--list-bass-modes`` output stable.
    """
    name = mode if mode in BASS_BOOST_MODES else "Normal"
    # ``face`` lets a narrow row draw a shorter chip ("BASS:HIGH") without
    # losing the mode's own colour.
    text = face if face is not None else f" BASS BOOST: {name.upper()} "
    if theme_name:
        # The mode keeps its own hue (neutral / green / amber) but is lifted
        # into the theme so it belongs to the surrounding row.
        hue = {"Normal": (150, 190, 235), "Medium": (110, 230, 140), "High": (245, 190, 95)}[name]
        return theme_chip(theme_name, text, bold=bold, lift=0.25, hue=hue)
    (br, bg_, bb), (fr, fg_, fb) = BASS_MODE_STYLE[name]
    weight = "1;" if bold else ""
    return (
        f"\033[{weight}48;2;{br};{bg_};{bb};38;2;{fr};{fg_};{fb}m"
        f" BASS BOOST: {name.upper()} \033[0m"
    )


def bass_mode_label(mode: str, bold: bool = True) -> str:
    """The CLI form: bold ink in the mode's colour, with no filled background.

    The CLI's Favorites row is deliberately text-only (a filled block there
    paints a strip of colour across the console, and a test pins that). The
    mode is still unmistakable: cyan, green and amber.
    """
    name = mode if mode in BASS_BOOST_MODES else "Normal"
    (_br, _bg, _bb), (fr, fg_, fb) = BASS_MODE_STYLE[name]
    weight = "1;" if bold else ""
    return f"\033[{weight}38;2;{fr};{fg_};{fb}m BASS BOOST: {name.upper()} \033[0m"


def bass_mode_badge(mode: str, bold: bool = True) -> str:
    """Compact status text for the footer/now-playing lines."""
    name = mode if mode in BASS_BOOST_MODES else "Normal"
    (br, bg_, bb), (fr, fg_, fb) = BASS_MODE_STYLE[name]
    weight = "1;" if bold else ""
    return f"\033[{weight}48;2;{br};{bg_};{bb};38;2;{fr};{fg_};{fb}m BASS: {name.upper()} \033[0m"


def _soft_limit(chunk: np.ndarray) -> np.ndarray:
    """Transparent below 0.8, then a tanh knee up to 1.0.

    A 12 dB boost at 45 Hz will still push an already-loud mix past full scale.
    The Crusher headphones limit in DSP rather than clip; this is the same idea
    without pumping the whole block.
    """
    peak = float(np.max(np.abs(chunk))) if chunk.size else 0.0
    if peak <= 0.8:
        return chunk
    out = np.array(chunk, copy=True)
    hot = np.abs(out) > 0.8
    mag = np.abs(out[hot])
    out[hot] = np.sign(out[hot]) * (0.8 + 0.2 * np.tanh((mag - 0.8) / 0.2))
    return out


class BassBoostFilter:
    """Minimum-phase FIR reproducing the Crusher-mode bass boost.

    Skullcandy's Crusher driver is fed by an analogue bass amplifier, so the
    effect has no look-ahead: it is a minimum-phase boost, not a linear-phase
    one. A minimum-phase FIR keeps that property (no group delay, no lip-sync
    offset against the visualiser) and still nails the measured curve.

    Overlap-save: each block is preceded by the previous block's tail, so a
    power-of-two FFT convolution is the linear convolution. The first
    ``filter_len - 1`` outputs are the wrap-around region and are discarded.
    """

    def __init__(self, filter_len: int = 4096):
        _ensure_numpy()
        self.filter_len = int(filter_len)
        self.overlap = np.zeros((self.filter_len - 1, 2), dtype=np.float64)
        self.H = None
        self.taps = None
        self.mode = "Normal"
        self.is_active = False
        self.current_sr = 44100
        self._design_key = None
        self._fft_n = 0
        self._lock = threading.Lock()

    def _design(self, peak_db: float) -> np.ndarray:
        """Minimum-phase impulse response for the given peak boost."""
        taps = self.filter_len
        design_n = max(16384, taps * 2)
        freqs = np.fft.rfftfreq(design_n, 1.0 / self.current_sr)
        mag = 10.0 ** (crusher_bass_response_db(freqs, peak_db) / 20.0)
        cep = np.fft.irfft(np.log(np.maximum(mag, 1e-9)), n=design_n)
        folded = np.zeros(design_n)
        folded[0] = cep[0]
        half = design_n // 2
        folded[1:half] = 2.0 * cep[1:half]
        folded[half] = cep[half]
        min_phase = np.fft.irfft(np.exp(np.fft.rfft(folded)), n=design_n)
        h = min_phase[:taps].copy()
        fade = min(512, max(1, taps // 8))
        if fade > 1:
            h[-fade:] *= np.hanning(fade * 2)[fade:]
        return h

    def set_mode(self, mode: str, sr: int = 44100):
        name = mode if mode in BASS_BOOST_MODES else "Normal"
        sr = int(sr if sr and sr > 0 else 44100)
        with self._lock:
            peak_db = bass_mode_peak_db(name)
            self.mode = name
            if peak_db <= 0.0:
                self.is_active = False
                self.H = None
                self.taps = None
                self._design_key = None
                self.overlap = np.zeros((self.filter_len - 1, 2), dtype=np.float64)
                return
            if self._design_key == (name, sr) and self.H is not None:
                self.is_active = True
                return
            self.current_sr = sr
            self.taps = self._design(peak_db)
            self._fft_n = 0
            self.overlap = np.zeros((self.filter_len - 1, 2), dtype=np.float64)
            self._design_key = (name, sr)
            self.is_active = True

    @staticmethod
    def _window(n: int, taps: int) -> int:
        return int(2 ** int(np.ceil(np.log2(max(n + taps, 2 * taps)))))

    def process(self, chunk: np.ndarray) -> np.ndarray:
        with self._lock:
            if not self.is_active or self.taps is None:
                return chunk
            n = len(chunk)
            if n == 0:
                return chunk
            fft_n = self._window(n, self.filter_len)
            if fft_n != self._fft_n:
                self.H = np.fft.rfft(self.taps, n=fft_n)[:, None]
                self._fft_n = fft_n
                self.overlap = np.zeros((self.filter_len - 1, 2), dtype=np.float64)
            if len(self.overlap) != self.filter_len - 1:
                self.overlap = np.zeros((self.filter_len - 1, 2), dtype=np.float64)
            ext = np.vstack([self.overlap, np.asarray(chunk, dtype=np.float64)])
            conv = np.fft.irfft(np.fft.rfft(ext, n=fft_n, axis=0) * self.H, n=fft_n, axis=0)
            self.overlap = ext[-(self.filter_len - 1):].copy()
            start = self.filter_len - 1
            out = conv[start : start + n]
            if len(out) < n:
                out = np.vstack([out, np.zeros((n - len(out), out.shape[1]), dtype=out.dtype)])
            return _soft_limit(out.astype(np.float32))


class BassBoostManager:
    """The three bass modes, persisted for the next player launch."""

    def __init__(self):
        self.mode = "Normal"
        self._load()

    def _load(self):
        try:
            saved = BASS_BOOST_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            return
        resolved = self.resolve(saved)
        if resolved:
            self.mode = resolved

    @staticmethod
    def resolve(name: str) -> Optional[str]:
        if not name:
            return None
        key = str(name).strip().lower()
        aliases = {
            "off": "Normal", "none": "Normal", "flat": "Normal", "0": "Normal",
            "med": "Medium", "medium": "Medium", "33": "Medium", "1": "Medium",
            "high": "High", "66": "High", "2": "High",
            # Extreme was removed from the dial. Anything still asking for it
            # (a saved setting from an older build, an old script) resolves to
            # High, the loudest mode that is still offered, rather than
            # silently falling back to Normal.
            "extreme": "High", "max": "High", "full": "High",
            "100": "High", "3": "High", "crusher": "High",
        }
        if key in aliases:
            return aliases[key]
        for mode in BASS_BOOST_MODES:
            if mode.lower() == key:
                return mode
        return None

    def set_mode(self, mode: str, save: bool = True) -> bool:
        resolved = self.resolve(mode)
        if not resolved:
            return False
        self.mode = resolved
        if save:
            try:
                BASS_BOOST_FILE.parent.mkdir(parents=True, exist_ok=True)
                BASS_BOOST_FILE.write_text(resolved, encoding="utf-8")
            except Exception:
                pass
        return True

    def cycle(self, direction: int = 1) -> str:
        index = BASS_BOOST_MODES.index(self.mode) if self.mode in BASS_BOOST_MODES else 0
        step = 1 if direction >= 0 else -1
        self.set_mode(BASS_BOOST_MODES[(index + step) % len(BASS_BOOST_MODES)])
        return self.mode

    def fraction(self) -> float:
        return BASS_BOOST_FRACTIONS.get(self.mode, 0.0)

    def peak_db(self) -> float:
        return bass_mode_peak_db(self.mode)


class AudioEngine:
    def __init__(self, playlist: List[Dict[str, Any]], auto_bluetooth: bool = True, mode: str = "MIX_ARCHIVE"):
        _ensure_audio_backends()
        self.mode, self.playlist, self.play_queue, self._queue_lock = str(mode or "MIX_ARCHIVE").upper(), playlist, [], threading.Lock()
        self.current_index, self.current_track = 0, playlist[0] if playlist else None
        # Name of the playlist the tracks came from, used by the play log.
        self.playlist_path = ""
        self.state, self.volume, self.muted, self.shuffle, self.repeat_mode = "stopped", 1.0, False, False, "all"
        self.position_sec, self.duration_sec, self.sample_rate, self.channels = 0.0, 0.0, 44100, 2
        self.pitch_percent, self.pitch_factor, self.resampler = 0, 1.0, AudioResampler()
        self.sf_file = self.stream = self.audio_source = self.audio_buffer = self.feeder = self.stream_error = self.pending_seek_sec = None
        # Why the last track could not be loaded ("" after a good load). The
        # UI reads this so a Play press on an offline library is explained
        # instead of silently doing nothing.
        self.last_load_error = ""
        self.output_rate, self.output_device_name, self._next_stream_retry, self.track_ended = 44100, "", 0.0, False
        self._mac_output_device = None
        self._sf_lock, self._sf_refs, self._sf_retired = threading.Lock(), 0, []

        self.num_bands = 24
        self.band_edges = np.logspace(np.log10(28), np.log10(18500), self.num_bands + 1)
        self.band_boosts = ((1.0 + (1.2 / (1.0 + np.arange(self.num_bands, dtype=np.float32) * 0.28))) * 4.6)
        for attr in ("levels", "levels_left", "levels_right", "smoothed_levels", "smoothed_levels_left", "smoothed_levels_right", "peaks", "peaks_left", "peaks_right", "peak_decay_timers", "peak_decay_timers_left", "peak_decay_timers_right"):
            setattr(self, attr, np.zeros(self.num_bands, dtype=np.float32))
        self.recent_mono_buffer = np.zeros(512, dtype=np.float32)
        self.rms_level, self.peak_db = 0.0, -60.0
        self.hanning_window, self.fft_scale = np.hanning(1024).astype(np.float32), 512.0
        self._setup_fft_bins(44100)
        self._cached_bt_info, self._last_bt_query = {}, 0.0
        self.is_recording, self.recording_current_path, self.recording_bytes_written, self.recording_duration_sec = False, "", 0, 0.0
        self._rec_queue = self._rec_worker_thread = self._rec_dir = None
        self._rec_lock, self._rec_part_index, self._rec_base_stem, self._rec_samplerate = threading.Lock(), 1, "", 48000
        # The first output-stream open runs inside _ensure_stream. bt_router has
        # to exist first: a missing attribute aborts every device attempt.
        self.auto_bluetooth, self.bt_router = auto_bluetooth, None
        self._ensure_stream()
        self.eq_manager = EqualizerManager()
        self.eq_filter = EqualizerFilter()
        self.eq_filter.set_gains(self.eq_manager.get_current_bands(), self.sample_rate)
        self.bass_manager = BassBoostManager()
        self.bass_filter = BassBoostFilter()
        self.bass_filter.set_mode(self.bass_manager.mode, self.sample_rate)
        # Bluetooth routing runs a heartbeat thread that shells out to `wpctl`
        # every 1.2 s and a `pactl subscribe` child. It is only useful when
        # automatic Bluetooth switching was actually requested, and starting it
        # unconditionally leaked a daemon (and a subprocess) for every engine
        # ever constructed -- including in the test suite, where the leftovers
        # blocked on wpctl and made the run appear to hang.
        self._start_bluetooth_router()

    def _start_bluetooth_router(self) -> None:
        if BluetoothAudioRouter is None or not self.auto_bluetooth:
            return
        if os.environ.get("MP_DISABLE_BLUETOOTH_MONITOR") == "1":
            return
        try:
            self.bt_router = BluetoothAudioRouter(
                target_pid=os.getpid(),
                on_route_change=self._on_bt_route_change,
                auto_switch=self.auto_bluetooth,
            )
            self.bt_router.start()
        except Exception:
            self.bt_router = None

    def shutdown(self) -> None:
        """Release the stream, the Bluetooth monitor and its child process.

        Nothing did this before: an engine object that went out of scope left a
        PortAudio stream open and a `pactl subscribe` child running. In a test
        run that accumulated until the machine ran out of file descriptors.
        """
        try:
            self.stop_playback(reset_position=False)
        except Exception:
            pass
        self.stop_bluetooth_router()
        try:
            self._close_stream()
        except Exception:
            pass

    def stop_bluetooth_router(self) -> None:
        """Stop the routing monitor and reap its `pactl subscribe` child."""
        router = getattr(self, "bt_router", None)
        if not router:
            return
        try:
            router.stop()
        except Exception:
            pass
        self.bt_router = None

    def _setup_fft_bins(self, sr: int):
        # A source can probe as 0 Hz (a container with no sample-rate field, or
        # a truncated header). Dividing by it raised ZeroDivisionError inside
        # load_track, which aborted playback for an otherwise playable file.
        # Fall back to the last known rate and keep the visualiser functional.
        sr = int(sr or 0)
        if sr <= 0:
            sr = int(getattr(self, "sample_rate", 0) or 0) or 44100
        freqs = np.fft.rfftfreq(1024, 1.0 / sr)
        self.band_indices = []
        for i in range(self.num_bands):
            idx = np.where((freqs >= self.band_edges[i]) & (freqs < self.band_edges[i + 1]))[0]
            if len(idx) == 0: idx = np.array([min(len(freqs) - 1, max(0, int(self.band_edges[i] / (sr / 2) * len(freqs))))])
            self.band_indices.append(idx)
        self._flat_idx = np.concatenate(self.band_indices)
        self._reduce_offsets = np.cumsum([0] + [len(x) for x in self.band_indices[:-1]])
        self._band_counts = np.array([len(x) for x in self.band_indices], dtype=np.float32)

    def _on_bt_route_change(self, device_name: str, is_bluetooth: bool, action: str, codec: Optional[str] = None):
        self._ensure_stream()
        if self.bt_router:
            try:
                self._cached_bt_info = self.bt_router.get_status(force_probe=True)
                self._last_bt_query = time.time()
                if is_bluetooth and self._cached_bt_info.get("bluetooth_codec"): codec = self._cached_bt_info["bluetooth_codec"]
            except Exception: pass
        c_sfx = f" {codec}" if codec else ""
        self.output_device_name = f"{device_name} [Bluetooth{c_sfx}]" if is_bluetooth else device_name
        self._sync_state_file()

    def set_pitch(self, pitch_pct: int):
        self.pitch_percent = max(-20, min(20, int(pitch_pct)))
        self.pitch_factor = 1.0 + (self.pitch_percent / 100.0)
        # A click on the pitch track, or a +/- press, has to move the painted
        # knob on the next frame even while the track is paused. The cheap
        # fingerprint cannot see this change on its own, so write it forced.
        self._sync_state_file(force=True)
    def change_pitch(self, delta_pct: int = 1): self.set_pitch(self.pitch_percent + delta_pct)
    def reset_pitch(self): self.set_pitch(0)

    def _stream_alive(self) -> bool:
        try: return bool(self.stream and self.stream.active)
        except Exception: return False

    def _close_stream(self):
        s, self.stream = self.stream, None
        if s:
            for fn in (s.abort, s.stop, s.close):
                try: fn()
                except Exception: pass

    def _stream_attempts(self) -> List[Tuple[Optional[int], int, Any]]:
        attempts, seen = [], set()
        def add(d, r, e):
            k = (d if d is None else int(d), int(r), e is not None)
            if r > 0 and k not in seen: seen.add(k); attempts.append((d, int(r), e))
        if sd is None: return attempts

        # MP Mix Manager Audio Settings publishes the chosen output device as
        # MP_AUDIO_OUTPUT_DEVICE (an index, a name, or "default"). It is tried
        # before the system default so the selection actually takes effect;
        # every later fallback still follows, so a device that has been
        # unplugged degrades to the default instead of leaving the player mute.
        chosen = _configured_output_device(sd)
        if chosen is not None:
            try:
                info = sd.query_devices(chosen, "output")
                native = int(info.get("default_samplerate") or 48000)
                for rate in (native, 48000, 44100, 96000, 88200):
                    add(chosen, rate, None)
            except Exception:
                pass

        if sys.platform == "darwin" and self._mac_output_device is not None:
            try:
                info = sd.query_devices(self._mac_output_device, "output")
                native_rate = int(info.get("default_samplerate") or 48000)
                for rate in (native_rate, 48000, 44100, 96000):
                    add(int(self._mac_output_device), rate, None)
                return attempts
            except Exception:
                self._mac_output_device = None

        # Windows WASAPI specific handling
        if sys.platform == "win32":
            try:
                for h in sd.query_hostapis():
                    if "WASAPI" in str(h.get("name", "")).upper() and (dev := h.get("default_output_device", -1)) is not None and int(dev) >= 0:
                        ex, nat = sd.WasapiSettings(exclusive=False, auto_convert=True), int(sd.query_devices(int(dev)).get("default_samplerate") or 48000)
                        for r in (nat, 48000, 44100): add(int(dev), r, ex)
            except Exception: pass

        # Safe macOS / cross-platform native rate query
        try:
            default_out = getattr(sd.default, 'device', None)
            default_out_idx = None
            if isinstance(default_out, (list, tuple)) and len(default_out) > 1:
                default_out_idx = default_out[1]
            elif isinstance(default_out, int):
                default_out_idx = default_out

            dev_info = sd.query_devices(default_out_idx, 'output') if default_out_idx is not None else sd.query_devices(kind='output')
            native_rate = int(dev_info.get("default_samplerate") or 48000)
            dev_idx = int(default_out_idx) if default_out_idx is not None and default_out_idx >= 0 else None
            for r in (native_rate, 44100, 48000, 96000, 88200):
                add(dev_idx, r, None)
        except Exception: pass

        try: 
            gen_out = sd.query_devices(kind="output")
            add(None, int(gen_out.get("default_samplerate") or 48000), None)
        except Exception: pass

        for r in (44100, 48000, 96000, 88200): 
            add(None, r, None)

        return attempts

    def route_to_bluetooth(self) -> Dict[str, Any]:
        """Select a Bluetooth/CoreAudio output directly for this player on macOS."""
        if sys.platform == "darwin":
            _ensure_audio_backends()
            if sd is None:
                return {"routed": False, "message": "Audio output support is missing. Install requirements.txt with this Python."}
            try:
                devices = sd.query_devices()
                hostapis = sd.query_hostapis()
                outputs = []
                for index, device in enumerate(devices):
                    if int(device.get("max_output_channels", 0)) < 1:
                        continue
                    host_index = int(device.get("hostapi", -1))
                    host_name = str(hostapis[host_index].get("name", "")) if 0 <= host_index < len(hostapis) else ""
                    if "core audio" not in host_name.lower():
                        continue
                    name = str(device.get("name", ""))
                    low = name.lower()
                    if any(term in low for term in ("bluetooth", "airpods", "beats", "headphone", "headset", "earbud", "buds")):
                        outputs.insert(0, (index, name, int(device.get("default_samplerate") or 48000)))
                    elif not any(term in low for term in ("built-in", "display audio", "hdmi")):
                        outputs.append((index, name, int(device.get("default_samplerate") or 48000)))
                if not outputs:
                    return {"routed": False, "message": "No Bluetooth audio output is available in macOS CoreAudio. Connect the headphones in System Settings > Bluetooth and Sound."}

                current = self._mac_output_device
                if current is None:
                    try:
                        default = sd.default.device
                        current = int(default[1] if isinstance(default, (tuple, list)) else default)
                    except Exception:
                        current = -1
                # CoreAudio normally switches the system output as soon as
                # Bluetooth headphones connect. Keep that selected output;
                # don't move the player to an unrelated speaker on A.
                if current >= 0:
                    current_name = str(sd.query_devices(current, "output").get("name", ""))
                    low = current_name.lower()
                    if any(term in low for term in ("bluetooth", "airpods", "beats", "headphone", "headset", "earbud", "buds", "crusher", "anc")):
                        self._mac_output_device = current
                        self.output_device_name = current_name
                        self._sync_state_file()
                        return {"routed": True, "device": current_name, "message": f"Already using {current_name}"}
                choices = [item for item in outputs if item[0] != current]
                if not choices:
                    return {"routed": True, "device": outputs[0][1], "message": f"Already using {outputs[0][1]}"}
                index, name, rate = choices[0]
                self._mac_output_device = index
                self._next_stream_retry = 0.0
                self._close_stream()
                self.output_rate = rate
                if self._ensure_stream():
                    self.output_device_name = name
                    self._sync_state_file()
                    return {"routed": True, "device": name, "message": f"Audio routed to {name}"}
                return {"routed": False, "device": name, "message": f"Could not open {name}: {self.stream_error or 'CoreAudio stream failed'}"}
            except Exception as exc:
                return {"routed": False, "message": f"Could not query macOS audio outputs: {exc}"}
        if self.bt_router:
            ok = self.bt_router.route_to_bluetooth()
            return {"routed": bool(ok), "bluetooth": self.bt_router.get_status()}
        return {"routed": False, "message": "Bluetooth audio routing is unavailable"}

    def _open_output_stream(self) -> bool:
        if sd is None: self.stream_error = "sounddevice is not installed"; return False
        errors = []
        for dev, rate, extra in self._stream_attempts():
            s = None
            try:
                kw = dict(samplerate=rate, channels=2, dtype="float32", callback=self._audio_callback, blocksize=1024, latency="high")
                if extra: kw["extra_settings"] = extra
                if dev is not None: kw["device"] = dev
                s = sd.OutputStream(**kw); s.start()
                nr = int(round(float(s.samplerate)))
                if nr != self.output_rate: self.resampler.reset()
                self.output_rate, self.stream, self.stream_error = nr or rate, s, None
                try: self.output_device_name = str(sd.query_devices(s.device[-1] if isinstance(s.device, (list, tuple)) else s.device)["name"])
                except Exception: self.output_device_name = "default"
                if self.bt_router:
                    try: threading.Thread(target=self.bt_router.check_and_route, daemon=True).start()
                    except Exception: pass
                return True
            except Exception as exc:
                errors.append(f"{dev}@{rate}: {exc}")
                if s:
                    try: s.close()
                    except Exception: pass
        self.stream, self.stream_error = None, "; ".join(errors)[-400:] if errors else "no output device"
        return False

    def _ensure_stream(self) -> bool:
        # A test or a headless tool must never open a real output device:
        # PortAudio blocks on the device server, which hangs a unit test and
        # (on some drivers) crashes the interpreter on teardown. The engine
        # still reports `stream_error`, so callers see a clean "no audio" path.
        if os.environ.get("MP_DISABLE_AUDIO_OUTPUT") == "1":
            self.stream_error = "audio output disabled by MP_DISABLE_AUDIO_OUTPUT"
            return False
        if self._stream_alive(): return True
        if time.time() < self._next_stream_retry: return False
        self._close_stream()
        if not (ok := self._open_output_stream()): self._next_stream_retry = time.time() + 1.5
        return ok

    def _acquire_sf(self):
        with self._sf_lock:
            if self.sf_file: self._sf_refs += 1
            return self.sf_file

    def _release_sf(self):
        with self._sf_lock:
            if self._sf_refs > 0: self._sf_refs -= 1
            closing, self._sf_retired = (self._sf_retired, []) if self._sf_refs == 0 and self._sf_retired else ([], self._sf_retired)
        for old in closing:
            try: old.close()
            except Exception: pass

    def _retire_sf(self, old_sf):
        if not old_sf: return
        with self._sf_lock:
            if self._sf_refs == 0: closing = [old_sf]
            else: self._sf_retired.append(old_sf); closing = []
        for old in closing:
            try: old.close()
            except Exception: pass

    def _stop_feeder(self):
        """Stop the feeder and release its source, in that order.

        ``AudioFeederThread.stop()`` only clears a flag: the thread may be
        inside ``source.read()`` when this returns. The source's ``close()`` is
        written to survive that (it holds a lock the reader also takes), but
        joining here first means the decoder is usually already idle by the time
        it is released, so the common case costs nothing and the lock is only a
        backstop for a read that is genuinely in flight.
        """
        if f := self.feeder:
            self.feeder = None
            try: f.stop()
            except Exception: pass
            try: f.join(timeout=0.5)
            except Exception: pass
        if b := self.audio_buffer:
            try: b.clear()
            except Exception: pass
        if s := self.audio_source:
            self.audio_source = None
            try: s.close()
            except Exception: pass

    def load_track(self, index: int, start_playing: bool = True, start_position: float = 0.0) -> bool:
        if not self.playlist or not (0 <= index < len(self.playlist)): return False
        # Refuse a track whose file is gone before touching any state. A
        # Traktor playlist generated elsewhere names files that may not be
        # mounted; without this the cursor moved to a dead entry and every
        # control then failed against it.
        #
        # Remember the reason on the engine as well as in the log. Play, the
        # S4's Play button and the browse knob all reach the engine without
        # going through the playlist row handler, so a bare False left them
        # doing nothing at all on an unmounted library.
        target_path = str(self.playlist[index].get("path") or "")
        if not target_path or not os.path.isfile(target_path):
            self.last_load_error = unavailable_media_hint(target_path)
            try: mp_log_error("MP_Audio_Player", f"Cannot open track (file missing or unreadable): {target_path}")
            except Exception: pass
            return False
        # A zero-byte file passes isfile() but has no audio at all. libsndfile
        # and FFmpeg both "open" it and report a plausible rate with zero
        # frames, so load_track used to return True and the player showed
        # "playing" at 00:00 forever, then silently chained through the next
        # dead rows. Treat it as unreadable so the deck says so instead.
        try:
            if os.path.getsize(target_path) <= 0:
                self.last_load_error = (f"Cannot play {os.path.basename(target_path)}: "
                                        f"the file is empty (0 bytes) and holds no audio.")
                try: mp_log_error("MP_Audio_Player", f"Unplayable audio (empty file, 0 bytes): {target_path}")
                except Exception: pass
                self.state = "stopped"
                return False
        except OSError:
            self.last_load_error = unavailable_media_hint(target_path)
            return False
        self.last_load_error = ""
        self.current_index, self.current_track = index, self.playlist[index]
        self._stop_feeder()
        try:
            src = open_audio_source(self.current_track["path"])
            # A source that probes as 0 Hz or 0 frames cannot be played and
            # would divide by zero in the duration maths below. Reject it here
            # with a clear message instead of a traceback.
            if int(getattr(src, "samplerate", 0) or 0) <= 0:
                try: src.close()
                except Exception: pass
                try: mp_log_error("MP_Audio_Player", f"Unplayable audio (no sample rate): {self.current_track.get('path', '')}")
                except Exception: pass
                self.last_load_error = (f"Cannot play {os.path.basename(str(self.current_track.get('path', '')))}: "
                                        f"the file has no readable audio stream.")
                self.state = "stopped"
                return False
            self.audio_source, self.sample_rate, self.channels = src, src.samplerate, src.channels
            # total_frames() is 0 when the container does not declare a length
            # (libsndfile's SF_COUNT_MAX sentinel). That is "unknown", not a
            # 2.9-million-year duration, and using it as the EOF target made
            # the feeder wait forever at the end of the file.
            frames = sane_frame_count(src.total_frames(), self.sample_rate)
            self.duration_sec = float(frames) / float(self.sample_rate) if frames else 0.0
            self.current_track.update(duration_sec=self.duration_sec, duration_fmt=format_seconds(self.duration_sec))
            self.position_sec = 0.0
            self.resampler.reset()
            self._setup_fft_bins(self.sample_rate)
            self.eq_filter.set_gains(self.eq_manager.get_current_bands(), self.sample_rate)
            self.bass_filter.set_mode(self.bass_manager.mode, self.sample_rate)
            with self._sf_lock: old_sf, self.sf_file = self.sf_file, getattr(src, "sf", src)
            self._retire_sf(old_sf)
            self.audio_buffer = AudioPrefetchBuffer(self.sample_rate, 12.0)
            self.feeder = AudioFeederThread(self.audio_source, self.audio_buffer, self)
            self.feeder.start()
            if start_position > 0.0:
                self.seek(start_position)
            min_pre, t0 = int(0.5 * self.sample_rate), time.time()
            while (time.time() - t0 < 1.0) and (self.audio_buffer.available_frames < min_pre) and not self.audio_buffer.eof:
                time.sleep(0.04)
            self._ensure_stream()
            self.state = "playing" if start_playing else "paused"
            self._sync_state_file(force=True)
            if start_playing:
                try: record_activity("Play Mix / Audio File", category="playback", details={"track_name": self.current_track.get("name"), "track_path": self.current_track.get("path"), "duration_sec": round(self.duration_sec, 2), "sample_rate": self.sample_rate}, status="success")
                except Exception: pass
                try: log_track_played(self.current_track, mode=self.mode, playlist_index=index, playlist=os.path.basename(str(getattr(self, "playlist_path", "") or "")), duration_sec=self.duration_sec, start_position=start_position)
                except Exception: pass
            return True
        except Exception as exc:
            self.state = "stopped"
            # A mix on an unmounted drive or a half-copied file is a normal
            # condition the UI recovers from. Log one line, not a traceback.
            if isinstance(exc, (FileNotFoundError, NotADirectoryError, PermissionError)) or (
                isinstance(exc, OSError) and getattr(exc, "errno", None) in (errno.ENOENT, errno.EIO, errno.ENODEV)
            ):
                try: mp_log_error("MP_Audio_Player", f"Cannot open track (file missing or unreadable): {self.current_track.get('path', '')}", details=f"{type(exc).__name__}: {exc}")
                except Exception: pass
                self.last_load_error = unavailable_media_hint(self.current_track.get("path"))
            else:
                try: mp_log_error("MP_Audio_Player", f"Failed loading track: {self.current_track.get('path', '')}", exc)
                except Exception: pass
                self.last_load_error = (f"Cannot play {os.path.basename(str(self.current_track.get('path', '')))}: "
                                        f"{type(exc).__name__}: {exc}")
            return False

    def _sync_state_file(self, force: bool = False):
        """Write the status file, skipping the write when nothing changed.

        The sync loop calls this 25 times a second while playing. Building the
        status dict and serialising it (24 EQ bands, waveform, spectrum) is the
        expensive part, so the cheap fingerprint is checked first. An unchanged
        player does not build the payload at all. Playback position is quantised
        to whole seconds inside the fingerprint; `force` bypasses the skip for
        state changes such as play, pause and seek.
        """
        try:
            fingerprint = self._state_fingerprint()
            if not force and fingerprint == getattr(self, "_last_state_fingerprint", None):
                return
            self._last_state_fingerprint = fingerprint
            payload = json.dumps(self.get_status_dict(), default=float, ensure_ascii=False)

            tmp = STATE_FILE.with_name(f".{STATE_FILE.name}.{os.getpid()}.tmp")
            # ensure_ascii=False keeps the EQ bars as real glyphs, matching what
            # the Manager paints. \uXXXX escapes made bash count six columns per bar.
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(STATE_FILE)
            if self.current_track and self.current_track.get("path") and self.duration_sec > 0:
                pos = round(float(self.position_sec), 2)
                if pos <= 0.0 and RESUME_STATE_FILE.is_file():
                    try:
                        prev = json.loads(RESUME_STATE_FILE.read_text(encoding="utf-8"))
                        if prev.get("path") == str(self.current_track.get("path")):
                            prev_pos = float(prev.get("position_sec", 0.0))
                            if prev_pos > 0.0:
                                pos = prev_pos
                    except Exception: pass
                # Whole-second resolution is enough for a resume pointer, and
                # it keeps the file from being rewritten several times a second.
                resume = (str(self.current_track.get("path")), int(pos), int(round(float(self.duration_sec))))
                if not force and resume == getattr(self, "_last_resume", None):
                    return
                self._last_resume = resume
                res_tmp = RESUME_STATE_FILE.with_suffix(".tmp")
                res_tmp.write_text(json.dumps({
                    "path": str(self.current_track.get("path")),
                    "position_sec": pos,
                    "duration_sec": round(float(self.duration_sec), 2),
                    "title": str(self.current_track.get("name", "")),
                    "mode": str(self.mode),
                    "player": "mp_audio_player",
                    "timestamp": time.time()
                }), encoding="utf-8")
                res_tmp.replace(RESUME_STATE_FILE)
        except Exception: pass

    def _state_fingerprint(self):
        """Cheap tuple covering everything the state file shows.

        Playback position is quantised to whole seconds. The manager header
        renders MM:SS, so a sub-second change is invisible, and quantising cuts
        the write rate from 25 per second to one per second — the difference
        between constant microSD wear and a handful of writes a minute. Level
        meters are quantised to 8 steps for the same reason.
        """
        def _q(value) -> int:
            try:
                return int(float(value) * 8)
            except (TypeError, ValueError):
                return 0

        levels = getattr(self, "smoothed_levels", None)
        levels = levels if levels is not None else []
        peaks = getattr(self, "peaks", None)
        peaks = peaks if peaks is not None else []
        track = self.current_track or {}
        return (
            self.state,
            track.get("path"),
            int(float(self.position_sec)),
            int(self.volume * 100),
            bool(self.muted),
            bool(self.shuffle),
            str(self.repeat_mode),
            int(self.current_index),
            tuple(_q(v) for v in list(levels)[:24]),
            tuple(_q(v) for v in list(peaks)[:24]),
            str(self.eq_manager.active_preset),
            str(self.bass_manager.mode),
            bool(self.is_recording),
        )

    def _audio_callback(self, outdata, frames, time_info, status):
        try: self._render_audio(outdata, frames)
        except Exception as exc: self.stream_error = str(exc); outdata.fill(0)

    def _render_audio(self, outdata, frames):
        buf = self.audio_buffer
        if buf is None or self.state != "playing": outdata.fill(0); return
        out_rate = float(self.output_rate or 48000)
        data = self.resampler.process(buf.read, frames, (float(self.sample_rate) / out_rate) * self.pitch_factor)
        rf = len(data)
        if self.feeder and self.sample_rate > 0:
            played = float(max(0, self.feeder.current_frame - buf.available_frames)) / float(self.sample_rate)
            # With no declared length (duration 0) there is nothing to clamp
            # to; let the clock follow the feeder instead of sitting at zero.
            self.position_sec = min(self.duration_sec, played) if self.duration_sec > 0 else played
        else:
            self.position_sec += float(max(rf, 0) * self.pitch_factor) / out_rate
        if rf <= 0:
            outdata.fill(0)
            if buf.eof and buf.is_empty(): self.track_ended = True
            return
        eff_vol = 0.0 if self.muted else self.volume
        sd_arr = (data if data.ndim > 1 and data.shape[1] >= 2 else np.column_stack((data, data) if data.ndim == 1 else (data[:, 0], data[:, 0])))[:, :2]
        if not self.eq_filter.is_flat:
            sd_arr = self.eq_filter.process(sd_arr)
        if self.bass_filter.is_active:
            sd_arr = self.bass_filter.process(sd_arr)
        mono = (sd_arr[:, 0] + sd_arr[:, 1]) * 0.5
        if eff_vol != 1.0: sd_arr = np.ascontiguousarray(sd_arr * np.float32(eff_vol))
        ncopy = min(rf, frames, len(sd_arr))
        if buf.eof and buf.is_empty() and np.all(sd_arr[:ncopy] == 0):
            outdata.fill(0); self.track_ended = True; return
        outdata[:ncopy] = sd_arr[:ncopy]
        if ncopy < frames:
            outdata[ncopy:].fill(0)
            if buf.eof and buf.is_empty(): self.track_ended = True
        if self.is_recording and self._rec_queue is not None and ncopy > 0:
            try: self._rec_queue.put_nowait(sd_arr[:ncopy].copy())
            except Exception: pass
        self._compute_fft_spectrum(sd_arr[:ncopy], mono[:ncopy])

    def _compute_fft_spectrum(self, stereo_data: np.ndarray, mono_data: np.ndarray):
        try:
            n = len(mono_data)
            if n >= 1024:
                m, l, r = mono_data[:1024], stereo_data[:1024, 0], stereo_data[:1024, 1]
            else:
                pad = (0, 1024 - n)
                m, l, r = np.pad(mono_data, pad), np.pad(stereo_data[:, 0], pad), np.pad(stereo_data[:, 1], pad)
            w, sc, fi, ro, bc, bst = self.hanning_window, self.fft_scale, self._flat_idx, self._reduce_offsets, self._band_counts, self.band_boosts
            m_fft, l_fft, r_fft = np.abs(np.fft.rfft(m * w)) / sc, np.abs(np.fft.rfft(l * w)) / sc, np.abs(np.fft.rfft(r * w)) / sc

            for lv, sm, pk, tm, fft_arr in (
                (self.levels, self.smoothed_levels, self.peaks, self.peak_decay_timers, m_fft),
                (self.levels_left, self.smoothed_levels_left, self.peaks_left, self.peak_decay_timers_left, l_fft),
                (self.levels_right, self.smoothed_levels_right, self.peaks_right, self.peak_decay_timers_right, r_fft),
            ):
                np.clip((np.add.reduceat(fft_arr[fi], ro) / bc) * bst, 0.0, 1.0, out=lv)
                rise = lv > sm
                sm[:] = np.where(rise, sm * 0.15 + lv * 0.85, sm * 0.80 + lv * 0.20)
                hit = sm >= pk
                pk[:] = np.where(hit, sm, pk)
                tm[:] = np.where(hit, 0.0, tm + 0.025)
                dec = (~hit) & (tm > 0.15)
                pk[:] = np.where(dec, np.maximum(0.0, pk - (0.035 + (tm - 0.15) * 0.08)), pk)

            self.recent_mono_buffer = m[:512]
            self.rms_level = float(np.sqrt(np.mean(m * m)))
            self.peak_db = 20.0 * math.log10(max(1e-5, float(np.max(np.abs(m)))))
        except Exception: pass

    def check_track_end(self):
        if self.track_ended:
            self.track_ended = False
            if self.repeat_mode == "one": self.seek(0)
            elif self.repeat_mode == "all" or bool(self.play_queue) or self.current_index < len(self.playlist) - 1: self.next_track()
            else: self.stop_playback(reset_position=True)

    def play(self):
        if self.audio_source is None or self.feeder is None or self.state == "stopped": self.load_track(self.current_index, True)
        else:
            self.state = "playing"; self._ensure_stream()
            if self.bt_router and hasattr(self.bt_router, "running") and not self.bt_router.running:
                try: self.bt_router.start()
                except Exception: pass
            self._sync_state_file()

    def pause(self): self.state = "paused"; self._sync_state_file(force=True)
    def toggle_play_pause(self): self.pause() if self.state == "playing" else self.play()

    def stop_playback(self, reset_position: bool = False):
        self.state = "stopped"
        if reset_position: self.position_sec = 0.0
        self._stop_feeder()
        with self._sf_lock: old_sf, self.sf_file = self.sf_file, None
        self._retire_sf(old_sf)
        for a in (self.smoothed_levels, self.smoothed_levels_left, self.smoothed_levels_right, self.peaks, self.peaks_left, self.peaks_right): a.fill(0)
        self._sync_state_file(force=True)

    def queue_track(self, track_or_path: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
        with self._queue_lock:
            td = dict(track_or_path) if isinstance(track_or_path, dict) else None
            if td is None and isinstance(track_or_path, str) and track_or_path:
                tp = str(track_or_path)
                td = next((dict(t) for t in self.playlist if t.get("path") == tp or t.get("name") == os.path.basename(tp)), None)
                if td is None:
                    td = {"name": os.path.basename(tp), "path": tp, "size_mb": os.path.getsize(tp) / 1048576.0 if os.path.isfile(tp) else 0.0, "mtime": time.time(), "ext": os.path.splitext(tp)[1].lstrip(".").upper()}
            if td:
                self.play_queue.append(td); self._sync_state_file()
                return {"queued": True, "track": td, "queue_len": len(self.play_queue)}
            return {"queued": False, "queue_len": len(self.play_queue)}

    def dequeue_next(self) -> Optional[Dict[str, Any]]:
        with self._queue_lock:
            if self.play_queue:
                t = self.play_queue.pop(0); self._sync_state_file(); return t
        return None

    def clear_queue(self) -> int:
        with self._queue_lock:
            n = len(self.play_queue); self.play_queue.clear(); self._sync_state_file(); return n

    def clear_playlist(self) -> int:
        """Empty the loaded playlist and the play queue. Audio files stay on disk."""
        with self._queue_lock:
            removed = len(self.playlist)
            self.play_queue.clear()
            self.playlist.clear()
        self.current_index = 0
        self.current_track = None
        self.duration_sec = 0.0
        self.stop_playback(reset_position=True)
        return removed

    def prepend_dropped_audio(self, paths) -> int:
        """Put dropped audio at the front of the playlist and play the first file."""
        items = audio_track_items(paths)
        if not items:
            return 0
        dropped = {item["path"] for item in items}
        with self._queue_lock:
            rest = [track for track in self.playlist if track.get("path") not in dropped]
            self.playlist[:] = items + rest
        self.load_track(0, True)
        return len(items)

    def get_queue(self) -> List[Dict[str, Any]]:
        with self._queue_lock: return list(self.play_queue)

    def _step_track(self, direction: int):
        if direction > 0 and (qi := self.dequeue_next()):
            qp = qi.get("path")
            for i, t in enumerate(self.playlist):
                if t.get("path") == qp:
                    if self.load_track(i, True): return
                    break
            else:
                self.playlist.insert(0, qi)
                if self.load_track(0, True): return
        if not (n := len(self.playlist)): return
        # Unreadable tracks (a sleeping SMB share, a truncated conversion) return
        # False from load_track. Trying only the first ten random draws made that
        # collapse shuffle onto whichever few files happened to be readable:
        # with 850 of 976 entries unreadable, 120 skips reached only 63 mixes.
        #
        # Shuffle now walks a freshly shuffled list of every other entry, so a
        # dead region can no longer starve it and every playable file stays
        # reachable. It is shuffled once per track change, not once per attempt.
        if self.shuffle:
            if n <= 1:
                return
            candidates = [i for i in range(n) if i != self.current_index]
            random.shuffle(candidates)
            for idx in candidates:
                if self.load_track(idx, True):
                    return
            return
        for attempt in range(1, min(10, n) + 1):
            idx = (self.current_index + attempt * direction) % n
            if self.load_track(idx, True):
                return

    def next_track(self): self._step_track(1)
    def prev_track(self): self.seek(0) if self.position_sec > 5.0 else self._step_track(-1)

    def seek(self, target_sec: float, force: bool = False):
        if self.duration_sec > 0:
            self.position_sec = target = max(0.0, min(self.duration_sec - 1.0, float(target_sec)))
            if self.feeder and self.sample_rate > 0: self.feeder.request_seek(int(target * self.sample_rate))
            self.resampler.reset()
            # A timeline click is a user-visible jump, so publish it at once
            # instead of waiting for the sync loop. `load_track` calls this to
            # apply a resume position while the track is still being set up, and
            # there the next forced write follows a line later, so it stays
            # opt-in rather than unconditional.
            if force:
                self._sync_state_file(force=True)

    def seek_relative(self, delta_sec: float): self.seek(self.position_sec + delta_sec)
    def set_volume(self, vol_pct: int): self.volume, self.muted = max(0.0, min(1.0, float(vol_pct) / 100.0)), False; self._sync_state_file()
    def change_volume(self, delta_pct: int): self.set_volume(int(self.volume * 100) + delta_pct)
    def toggle_mute(self): self.muted = not self.muted; self._sync_state_file()
    def toggle_shuffle(self): self.shuffle = not self.shuffle; self._sync_state_file()
    def cycle_repeat(self):
        m = ["all", "one", "off"]
        self.repeat_mode = m[(m.index(self.repeat_mode) + 1) % 3]
        self._sync_state_file()

    def start_recording(self) -> str:
        with self._rec_lock:
            if self.is_recording: return self.recording_current_path
            self._rec_dir = get_recording_output_dir()
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            ct = str(self.current_track.get("name") or self.current_track.get("title", "")) if isinstance(self.current_track, dict) else ""
            cl = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', os.path.splitext(ct)[0])[:32].strip("_") if ct else ""
            cl_sfx = f"_{cl}" if cl else ""
            self._rec_base_stem, self._rec_part_index = f"MP_REC_{ts}{cl_sfx}", 1
            self.recording_current_path = str(self._rec_dir / f"{self._rec_base_stem}_pt01.wav")
            self.recording_bytes_written, self.recording_duration_sec = 0, 0.0
            self._rec_samplerate = int(self.output_rate or self.sample_rate or 48000)
            self.is_recording, self._rec_queue = True, queue.Queue(maxsize=1200)
            self._rec_worker_thread = threading.Thread(target=self._recording_worker, daemon=True)
            self._rec_worker_thread.start()
            self._sync_state_file()
            return self.recording_current_path

    def stop_recording(self) -> Tuple[bool, str]:
        with self._rec_lock:
            if not self.is_recording: return False, ""
            self.is_recording, last, self.recording_current_path = False, self.recording_current_path, ""
            if self._rec_queue is not None:
                try: self._rec_queue.put_nowait(None)
                except Exception: pass
            self._sync_state_file()
            return True, last

    def toggle_recording(self) -> Tuple[bool, str]:
        return (False, self.stop_recording()[1]) if self.is_recording else (True, self.start_recording())

    def _recording_worker(self):
        sr, max_b = getattr(self, "_rec_samplerate", 48000), getattr(self, "_rec_max_bytes", None) or REC_MAX_FILE_BYTES
        def _open_w(fp: str):
            _ensure_audio_backends()
            if sf is not None: return sf.SoundFile(str(fp), mode="w", samplerate=sr, channels=2, subtype="PCM_16", format="WAV")
            import wave
            wf = wave.open(str(fp), "wb"); wf.setnchannels(2); wf.setsampwidth(2); wf.setframerate(sr); return wf
        try: cf, cb = _open_w(self.recording_current_path), 0
        except Exception: self.is_recording = False; return
        while True:
            try: chunk = self._rec_queue.get(timeout=0.2)
            except Exception:
                if not self.is_recording and (self._rec_queue is None or self._rec_queue.empty()): break
                continue
            if chunk is None: break
            if (cl := len(chunk)) <= 0: continue
            ch_b = cl * 4
            if cb + ch_b >= max_b:
                try: cf.close()
                except Exception: pass
                with self._rec_lock:
                    self._rec_part_index += 1
                    self.recording_current_path = str(self._rec_dir / f"{self._rec_base_stem}_pt{self._rec_part_index:02d}.wav")
                try: cf, cb = _open_w(self.recording_current_path), 0
                except Exception: break
            try:
                if sf is not None and isinstance(cf, sf.SoundFile): cf.write(chunk)
                else: cf.writeframes(np.clip(chunk * 32767.0, -32768, 32767).astype(np.int16).tobytes())
                cb += ch_b; self.recording_bytes_written += ch_b; self.recording_duration_sec += cl / float(sr)
            except Exception: pass
        if cf:
            try: cf.close()
            except Exception: pass
        self._sync_state_file()

    def get_mini_visualizer(self) -> str:
        if self.state != "playing": return "·······"
        step = max(1, self.num_bands // 8)
        return "".join(prism_level_glyph(int(self.smoothed_levels[i] * 8)) for i in range(0, self.num_bands, step)[:8])

    def get_status_dict(self, live: bool = False) -> Dict[str, Any]:
        pct = round((self.position_sec / self.duration_sec * 100.0), 1) if self.duration_sec > 0 else 0.0
        now = time.time()
        if self.bt_router and (not self._cached_bt_info or now - self._last_bt_query > 1.0):
            try: self._cached_bt_info, self._last_bt_query = self.bt_router.get_status(), now
            except Exception: pass
        bt = self._cached_bt_info if self.bt_router else {}
        is_bt, bt_dev, bt_c = bool(bt.get("bluetooth_connected", False)), str(bt.get("bluetooth_device", "")), str(bt.get("bluetooth_codec", ""))
        bt_c_sfx = f" {bt_c}" if bt_c else ""
        cur_out = f"{bt_dev} [Bluetooth{bt_c_sfx}]" if (is_bt and bt_dev) else str(self.output_device_name or "")
        # The drawn frame reads the meters straight away. Rounding once and
        # keeping the arrays avoids a list copy here and a second copy inside
        # the equaliser. The state file and IPC still need JSON lists.
        _ensure_numpy()
        def _meter(arr):
            rounded = np.round(np.asarray(arr, dtype=float), 3)
            return rounded if live else rounded.tolist()
        levels, peaks = _meter(self.smoothed_levels), _meter(self.peaks)
        levels_left, levels_right = _meter(self.smoothed_levels_left), _meter(self.smoothed_levels_right)
        peaks_left, peaks_right = _meter(self.peaks_left), _meter(self.peaks_right)
        waveform = _meter(self.recent_mono_buffer[::7][:80])
        return {
            "running": True, "state": str(self.state), "mode": str(self.mode), "index": int(self.current_index),
            "total_tracks": len(self.playlist), "title": str(self.current_track["name"] if self.current_track else "No Track Loaded"),
            "path": str(self.current_track["path"] if self.current_track else ""), "position_sec": round(float(self.position_sec), 2),
            "duration_sec": round(float(self.duration_sec), 2), "position_fmt": format_seconds(self.position_sec),
            "duration_fmt": format_seconds(self.duration_sec), "progress_pct": float(pct), "volume": int(self.volume * 100),
            "pitch_percent": int(self.pitch_percent), "pitch_factor": round(float(self.pitch_factor), 3), "muted": bool(self.muted),
            "shuffle": bool(self.shuffle), "repeat": str(self.repeat_mode), "sample_rate": int(self.sample_rate),
            "recording": bool(self.is_recording), "recording_file": str(self.recording_current_path),
            "recording_bytes": int(self.recording_bytes_written), "recording_duration": round(float(self.recording_duration_sec), 2),
            "queue_len": len(self.play_queue), "queue": [t.get("path") for t in self.play_queue if isinstance(t, dict)],
            "output_rate": int(self.output_rate or 0), "output_device": cur_out,
            "bluetooth_connected": is_bt, "bluetooth_device": bt_dev, "bluetooth_codec": bt_c,
            "bluetooth_auto_route": bool(getattr(self, "auto_bluetooth", True)), "audio_error": str(self.stream_error or ""),
            "last_load_error": str(getattr(self, "last_load_error", "") or ""),
            "channels": int(self.channels), "mini_eq": self.get_mini_visualizer(),
            "levels": levels, "peaks": peaks,
            "levels_left": levels_left, "levels_right": levels_right,
            "peaks_left": peaks_left, "peaks_right": peaks_right,
            "waveform": waveform,
            "rms_level": round(float(self.rms_level), 4), "peak_db": round(float(self.peak_db), 1), "pid": os.getpid(),
            "eq_preset": str(self.eq_manager.active_preset),
            "eq_bands": [round(float(x), 1) for x in self.eq_manager.get_current_bands()],
            "eq_is_flat": bool(self.eq_manager.is_flat()),
            "eq_presets": self.eq_manager.get_all_preset_names(),
            "bass_mode": str(self.bass_manager.mode),
            "bass_modes": list(BASS_BOOST_MODES),
            "bass_peak_db": self.bass_manager.peak_db(),
            "bass_percent": int(round(self.bass_manager.fraction() * 100)),
        }

    def set_bass_mode(self, mode: str) -> bool:
        ok = self.bass_manager.set_mode(mode)
        if ok:
            self.bass_filter.set_mode(self.bass_manager.mode, self.sample_rate)
        self._sync_state_file()
        return ok

    def cycle_bass_mode(self, direction: int = 1) -> str:
        mode = self.bass_manager.cycle(direction)
        self.bass_filter.set_mode(mode, self.sample_rate)
        self._sync_state_file()
        return mode

    def set_eq_preset(self, name: str) -> bool:
        ok = self.eq_manager.apply_preset(name)
        self.eq_filter.set_gains(self.eq_manager.get_current_bands(), self.sample_rate)
        self._sync_state_file()
        return ok

    def set_eq_band(self, index: int, gain_db: float, step: bool = False) -> bool:
        ok = self.eq_manager.set_band(index, gain_db, step=step)
        self.eq_filter.set_gains(self.eq_manager.get_current_bands(), self.sample_rate)
        self._sync_state_file()
        return ok

    def set_eq_bands(self, gains: Sequence[float]) -> bool:
        ok = self.eq_manager.set_bands(gains)
        self.eq_filter.set_gains(self.eq_manager.get_current_bands(), self.sample_rate)
        self._sync_state_file()
        return ok

    def save_eq_preset(self, name: str, gains: Optional[Sequence[float]] = None) -> bool:
        ok = self.eq_manager.save_custom_preset(name, gains)
        self.eq_filter.set_gains(self.eq_manager.get_current_bands(), self.sample_rate)
        self._sync_state_file()
        return ok

    def delete_eq_preset(self, name: str) -> bool:
        ok = self.eq_manager.delete_custom_preset(name)
        self.eq_filter.set_gains(self.eq_manager.get_current_bands(), self.sample_rate)
        self._sync_state_file()
        return ok

class PlayerIPCServer:
    def __init__(self, engine: AudioEngine): self.engine, self.running, self.sock = engine, True, None

    def start(self):
        if hasattr(socket, "AF_UNIX"):
            SOCKET_PATH.unlink(missing_ok=True)
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.bind(str(SOCKET_PATH))
        else:
            PORT_FILE.unlink(missing_ok=True)
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.bind(("127.0.0.1", 0))
            PORT_FILE.write_text(str(self.sock.getsockname()[1]))
        self.sock.listen(10); self.sock.setblocking(False)
        PID_FILE.write_text(str(os.getpid()))
        threading.Thread(target=self._server_loop, daemon=True).start()
        threading.Thread(target=self._state_sync_loop, daemon=True).start()

    def _server_loop(self):
        while self.running:
            try:
                if select.select([self.sock], [], [], 0.2)[0]:
                    conn, _ = self.sock.accept()
                    threading.Thread(target=self._handle_client, args=(conn,), daemon=True).start()
            except Exception: pass

    def _handle_client(self, conn: socket.socket):
        try:
            conn.settimeout(2.0)
            if not (data := conn.recv(4096).decode('utf-8')): return
            req, e, res = json.loads(data), self.engine, {"status": "ok"}
            cmd = req.get("cmd")
            if cmd == "status": res["data"] = e.get_status_dict()
            elif cmd == "drop_audio":
                added = apply_drop_audio_command(e, req)
                res["added"] = added
            elif cmd == "play":
                pos = float(req.get("position", 0.0) or 0.0)
                if f := req.get("file"):
                    def _l(tf, p):
                        # A row from a generated Music Folders playlist can name
                        # a file on an SMB share that is not mounted yet. Try the
                        # share before giving up; the UI reports last_load_error.
                        # This already runs on a worker thread, so a wedged share
                        # delays only this one request.
                        if not os.path.isfile(tf):
                            ensure_music_share_for_path(tf)
                        for i, t in enumerate(e.playlist):
                            if t["path"] == tf or t["name"] == os.path.basename(tf):
                                e.load_track(i, True, start_position=p)
                                e._sync_state_file(force=True)
                                return
                        e.playlist.insert(0, {"name": os.path.basename(tf), "path": tf, "size_mb": 0, "mtime": time.time(), "ext": os.path.splitext(tf)[1][1:].upper()})
                        e.load_track(0, True, start_position=p)
                        e._sync_state_file(force=True)
                    threading.Thread(target=_l, args=(f, pos), daemon=True).start()
                else:
                    if pos > 0: e.seek(pos)
                    e.play()
                    e._sync_state_file(force=True)
            elif cmd == "pause": e.pause()
            elif cmd in ("toggle", "play_pause"): e.toggle_play_pause(); res["state"] = e.state
            elif cmd == "transport_stop":
                # Headset Stop. The plain "stop" command quits the daemon, which
                # a headphone button must not do.
                e.stop_playback(reset_position=False)
                res["state"] = e.state
            elif cmd in ("stop", "quit"):
                try:
                    if e.current_track and e.current_track.get("path"):
                        saved_pos = round(float(e.position_sec), 2)
                        if saved_pos <= 0.0 and RESUME_STATE_FILE.is_file():
                            try:
                                prev = json.loads(RESUME_STATE_FILE.read_text(encoding="utf-8"))
                                if prev.get("path") == str(e.current_track.get("path")):
                                    prev_pos = float(prev.get("position_sec", 0.0))
                                    if prev_pos > 0.0:
                                        saved_pos = prev_pos
                            except Exception: pass
                        res_tmp = RESUME_STATE_FILE.with_suffix(".tmp")
                        res_tmp.write_text(json.dumps({
                            "path": str(e.current_track.get("path")),
                            "position_sec": saved_pos,
                            "duration_sec": round(float(e.duration_sec), 2),
                            "title": str(e.current_track.get("name", "")),
                            "mode": str(e.mode),
                            "player": "mp_audio_player",
                            "timestamp": time.time()
                        }), encoding="utf-8")
                        res_tmp.replace(RESUME_STATE_FILE)
                except Exception: pass
                e.stop_playback(reset_position=False); self.running = False
                for p in (STATE_FILE, SOCKET_PATH, PORT_FILE, PID_FILE): p.unlink(missing_ok=True)
                conn.sendall(json.dumps(res).encode('utf-8')); conn.close(); os._exit(0)
            elif cmd == "next": threading.Thread(target=e.next_track, daemon=True).start()
            elif cmd == "prev": threading.Thread(target=e.prev_track, daemon=True).start()
            elif cmd == "seek": e.seek_relative(float(req.get("seconds", 10.0)))
            elif cmd == "seek_to":
                e.seek(float(req.get("position", 0.0)), force=True)
                if req.get("resume") and e.state != "playing":
                    e.play()
            elif cmd == "set_volume": e.set_volume(int(req.get("volume", 85)))
            elif cmd == "vol_up": e.change_volume(int(req.get("step", 5)))
            elif cmd == "vol_down": e.change_volume(-int(req.get("step", 5)))
            elif cmd in ("pitch_up", "pitch_down", "set_pitch", "reset_pitch"):
                if cmd == "pitch_up": e.change_pitch(int(req.get("step", 1)))
                elif cmd == "pitch_down": e.change_pitch(-int(req.get("step", 1)))
                elif cmd == "set_pitch": e.set_pitch(int(req.get("percent", 0)))
                else: e.reset_pitch()
                res.update(pitch_percent=e.pitch_percent, pitch_factor=e.pitch_factor)
            elif cmd == "mute": e.toggle_mute(); res["muted"] = e.muted
            elif cmd == "shuffle": e.toggle_shuffle(); res["shuffle"] = e.shuffle
            elif cmd == "repeat": e.cycle_repeat(); res["repeat"] = e.repeat_mode
            elif cmd in ("record", "toggle_record"):
                ir, rf = e.toggle_recording(); res.update(recording=ir, recording_file=rf)
            elif cmd == "start_record": res.update(recording=True, recording_file=e.start_recording())
            elif cmd == "stop_record": res.update(recording=False, recording_file=e.stop_recording()[1])
            elif cmd == "select_index": threading.Thread(target=e.load_track, args=(int(req.get("index", 0)), True), daemon=True).start()
            elif cmd == "queue":
                f = req.get("file") or req.get("track")
                res.update(e.queue_track(f) if f else {"queued": False, "queue_len": len(e.play_queue)})
            elif cmd == "get_queue": res["queue"] = q = e.get_queue(); res["queue_len"] = len(q)
            elif cmd == "clear_queue": res["cleared"] = e.clear_queue()
            elif cmd == "clear_playlist": res["cleared"] = e.clear_playlist()
            elif cmd == "get_playlist": res["playlist"] = [{"index": i, "name": t["name"], "path": t["path"], "ext": t["ext"]} for i, t in enumerate(e.playlist[:100])]
            elif cmd in ("open_file_manager", "reveal"):
                t = choose_file_manager_target(req.get("file") or (e.current_track.get("path") if e.current_track else ""))
                res["success"] = bool(t) and open_in_file_manager(t)
            elif cmd in ("route_bluetooth", "switch_bluetooth"):
                res.update(e.route_to_bluetooth())
                if e.bt_router: res["bluetooth"] = e.bt_router.get_status()
                if not res.get("routed"): res["status"] = "error"
            elif cmd in ("revert_audio", "fallback_audio"):
                if e.bt_router: res.update(reverted=e.bt_router.route_to_fallback(), bluetooth=e.bt_router.get_status())
                else: res.update(status="error", message="Bluetooth router not available")
            elif cmd == "bluetooth_status": res["bluetooth"] = e.bt_router.get_status() if e.bt_router else {"bluetooth_connected": False}
            elif cmd == "toggle_bluetooth_auto":
                if e.bt_router: e.auto_bluetooth = e.bt_router.auto_switch = not getattr(e, "auto_bluetooth", True); res["auto_switch"] = e.auto_bluetooth
                else: res["status"] = "error"
            elif cmd == "set_mode": e.mode = res["mode"] = str(req.get("mode", "MIX_ARCHIVE")).upper()
            elif cmd == "get_eq":
                res.update(preset=e.eq_manager.active_preset, bands=e.eq_manager.get_current_bands(), presets=e.eq_manager.get_all_preset_names(), is_flat=e.eq_manager.is_flat())
            elif cmd == "set_eq_preset":
                ok = e.set_eq_preset(req.get("preset", "Flat"))
                res.update(success=ok, preset=e.eq_manager.active_preset, bands=e.eq_manager.get_current_bands())
            elif cmd == "set_eq_band":
                idx, gain = int(req.get("band", 0)), float(req.get("gain", 0.0))
                ok = e.set_eq_band(idx, gain, step=bool(req.get("step", False)))
                res.update(success=ok, preset=e.eq_manager.active_preset, bands=e.eq_manager.get_current_bands())
            elif cmd == "set_eq_bands":
                ok = e.set_eq_bands(req.get("bands", []))
                res.update(success=ok, preset=e.eq_manager.active_preset, bands=e.eq_manager.get_current_bands())
            elif cmd == "save_eq_preset":
                ok = e.save_eq_preset(req.get("name", "Custom"), req.get("bands"))
                res.update(success=ok, preset=e.eq_manager.active_preset, presets=e.eq_manager.get_all_preset_names())
            elif cmd == "delete_eq_preset":
                ok = e.delete_eq_preset(req.get("name", ""))
                res.update(success=ok, presets=e.eq_manager.get_all_preset_names())
            elif cmd == "reset_eq":
                e.set_eq_preset("Flat")
                res.update(success=True, preset="Flat", bands=e.eq_manager.get_current_bands())
            elif cmd in ("set_bass_mode", "set_bass"):
                ok = e.set_bass_mode(str(req.get("mode", "Normal")))
                res.update(success=ok, bass_mode=e.bass_manager.mode,
                           bass_peak_db=e.bass_manager.peak_db(), bass_modes=list(BASS_BOOST_MODES))
            elif cmd in ("cycle_bass", "bass_up"):
                res.update(success=True, bass_mode=e.cycle_bass_mode(1),
                           bass_peak_db=e.bass_manager.peak_db())
            elif cmd == "bass_down":
                res.update(success=True, bass_mode=e.cycle_bass_mode(-1),
                           bass_peak_db=e.bass_manager.peak_db())
            elif cmd == "get_bass":
                res.update(bass_mode=e.bass_manager.mode, bass_modes=list(BASS_BOOST_MODES),
                           bass_peak_db=e.bass_manager.peak_db())
            elif cmd == "load_playlist":
                loaded = load_engine_playlist(
                    e,
                    playlist=req.get("playlist") or [],
                    mode=str(req.get("mode", "MUSIC")),
                    playlist_path=req.get("playlist_path"),
                    folder_name=req.get("folder_name"),
                    play=bool(req.get("play", False)),
                )
                res.update(loaded=loaded, mode=e.mode)
            elif cmd in ("generate_spek", "spek"):
                tf = req.get("file") or (e.current_track.get("path") if e.current_track else (e.playlist[0].get("path") if e.playlist else None))
                if tf:
                    ok, sr = generate_track_spek(tf, mode=e.mode)
                    res.update(success=ok, spek_path=sr if ok else "")
                    if not ok: res.update(status="error", message=sr)
                else: res.update(status="error", message="No track available for Spek generation")
            conn.sendall(json.dumps(res, default=float).encode('utf-8'))
        except Exception as err:
            try: conn.sendall(json.dumps({"status": "error", "message": str(err)}).encode('utf-8'))
            except Exception: pass
        finally:
            try: conn.close()
            except Exception: pass

    def _state_sync_loop(self):
        while self.running:
            self.engine._sync_state_file()
            time.sleep(0.04 if self.engine.state == "playing" else 0.25)

def _cleanup_stale_daemon_files():
    for p in (PID_FILE, SOCKET_PATH, PORT_FILE): p.unlink(missing_ok=True)
    try: STATE_FILE.write_text(json.dumps({"running": False, "state": "stopped"}), encoding="utf-8")
    except OSError: pass

def _drop_paths_from_request(manifest, files) -> List[str]:
    paths: List[str] = []
    if manifest and is_drop_manifest(manifest):
        try:
            paths.extend(read_drop_manifest(manifest).get("paths") or [])
        except Exception:
            pass
    if isinstance(files, list):
        paths.extend(str(item) for item in files if item)
    return paths

def apply_drop_audio_command(engine, req: Dict[str, Any]) -> int:
    """Load a Liquid Player drop into the engine. The manifest is read before returning."""
    paths = _drop_paths_from_request((req or {}).get("manifest"), (req or {}).get("files"))
    items = audio_track_items(paths)
    if items:
        threading.Thread(
            target=engine.prepend_dropped_audio,
            args=([item["path"] for item in items],),
            daemon=True,
        ).start()
    return len(items)

def send_ipc_command(cmd: str, **kwargs) -> Optional[Dict[str, Any]]:
    use_unix = hasattr(socket, "AF_UNIX") and SOCKET_PATH.exists()
    if not use_unix and not PORT_FILE.exists(): return None
    for _ in range(4):
        try:
            s = socket.socket(socket.AF_UNIX if use_unix else socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.5)
            s.connect(str(SOCKET_PATH) if use_unix else ("127.0.0.1", int(PORT_FILE.read_text().strip())))
            s.sendall(json.dumps({"cmd": cmd, **kwargs}).encode('utf-8'))
            raw = s.recv(65536).decode('utf-8'); s.close()
            if raw: return json.loads(raw)
        except (ConnectionRefusedError, FileNotFoundError): break
        except Exception: time.sleep(0.08)
    return None

def is_player_daemon_running() -> bool:
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            if sys.platform == "win32":
                import ctypes
                k = ctypes.WinDLL("kernel32", use_last_error=True)
                if h := k.OpenProcess(0x1000, False, pid):
                    ec = ctypes.c_ulong(); ok = k.GetExitCodeProcess(h, ctypes.byref(ec)); k.CloseHandle(h)
                    if ok and ec.value == 259: return True
            else:
                os.kill(pid, 0)
                cp = Path(f"/proc/{pid}/cmdline")
                if not cp.exists() or any(x in cp.read_text(errors="ignore") for x in ("MP_Audio_Player", "python")): return True
        except (OSError, ValueError): pass
    if hasattr(socket, "AF_UNIX") and SOCKET_PATH.exists():
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(0.8); s.connect(str(SOCKET_PATH)); s.close(); return True
        except (ConnectionRefusedError, FileNotFoundError): SOCKET_PATH.unlink(missing_ok=True)
        except Exception: return True
    elif not hasattr(socket, "AF_UNIX") and PORT_FILE.exists():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.settimeout(0.8); s.connect(("127.0.0.1", int(PORT_FILE.read_text().strip()))); s.close(); return True
        except (ConnectionRefusedError, OSError): PORT_FILE.unlink(missing_ok=True)
        except Exception: return True
    return False

def _existing_audio_path(raw: Optional[str]) -> str:
    """Return the first real file named by a player argument.

    A broken launcher can stuff several paths into one argument, separated by
    newlines. Playing that whole string misses the playlist and starts a
    second mix at index 0.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    lines = [ln.strip().strip("'\"") for ln in text.replace("\r", "\n").split("\n")]
    ordered = lines + ([] if text in lines else [text])
    seen = set()
    for cand in ordered:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    return ""

def _acquire_daemon_lock() -> bool:
    """Keep a single playback daemon. A second daemon would overwrite the shared status file and the playlist would flicker."""
    global _DAEMON_LOCK_FH
    try:
        fh = open(DAEMON_LOCK_FILE, "a+", encoding="utf-8")
    except OSError:
        return True
    try:
        if sys.platform == "win32":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
    except OSError:
        pass
    _DAEMON_LOCK_FH = fh
    return True

def _release_daemon_lock() -> None:
    global _DAEMON_LOCK_FH
    fh, _DAEMON_LOCK_FH = _DAEMON_LOCK_FH, None
    if not fh:
        return
    try:
        fh.close()
    except OSError:
        pass

def _pid_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False

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
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except OSError:
            continue
    return killed


def _proc_player_namespace(pid: int) -> str:
    """The MP_PLAYER_NAMESPACE a process was started with (empty for the default)."""
    try:
        raw = (Path("/proc") / str(int(pid)) / "environ").read_bytes()
    except (OSError, ValueError):
        return ""
    marker = b"MP_PLAYER_NAMESPACE="
    for chunk in raw.split(b"\x00"):
        if chunk.startswith(marker):
            return chunk[len(marker):].decode("utf-8", "ignore")
    return ""


def _audio_player_daemon_pids() -> List[int]:
    me = os.getpid()
    found: List[int] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return found
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
        # Match on real argv, not a substring of the whole command line. A
        # shell running `pgrep -f "MP_Audio_Player.py --daemon"` mentions the
        # same text but has a single `-c` argument, and must not be killed.
        if not any(a.endswith("MP_Audio_Player.py") for a in argv):
            continue
        if "--daemon" not in argv:
            continue
        # A mixer deck's daemon belongs to that deck only. Retiring it because
        # the other deck opened would silence the first one.
        if _proc_player_namespace(pid) != PLAYER_NAMESPACE:
            continue
        found.append(pid)
    return found

def _choose_daemon_owner(pids: List[int], prefer_pid: Optional[int] = None, pid_file_pid: Optional[int] = None, ipc_pid: Optional[int] = None) -> Optional[int]:
    alive = [int(p) for p in pids if _pid_alive(p)]
    if not alive:
        return None
    for cand in (prefer_pid, pid_file_pid, ipc_pid):
        try:
            if cand is not None and int(cand) in alive:
                return int(cand)
        except (TypeError, ValueError):
            continue
    return min(alive)

def _retire_extra_player_daemons(prefer_pid: Optional[int] = None) -> None:
    """Stop every playback daemon except the one the visible player is using.

    Each daemon rewrites mp_player_state.json. The playlist follows that file,
    so two daemons make the highlight and the now-playing row swap every frame.
    SIGKILL skips the daemon's exit handler, which would otherwise delete the
    socket and pid file still used by the daemon we keep.
    """
    pids = _audio_player_daemon_pids()
    # Windows has no SIGKILL. TerminateProcess via SIGTERM skips atexit there too.
    stop_sig = getattr(signal, "SIGKILL", signal.SIGTERM)
    if prefer_pid and int(prefer_pid) == os.getpid():
        for pid in pids:
            try:
                os.kill(pid, stop_sig)
            except OSError:
                pass
        return
    if len(pids) <= 1:
        return
    pid_file_pid = None
    if PID_FILE.is_file():
        try:
            pid_file_pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid_file_pid = None
    ipc_pid = None
    status = send_ipc_command("status")
    if status and isinstance(status.get("data"), dict):
        try:
            ipc_pid = int(status["data"].get("pid") or 0) or None
        except (TypeError, ValueError):
            ipc_pid = None
    owner = _choose_daemon_owner(pids, prefer_pid, pid_file_pid, ipc_pid)
    for pid in pids:
        if pid != owner:
            try:
                os.kill(pid, stop_sig)
            except OSError:
                pass

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_WIN_CONSOLE = None

@functools.lru_cache(maxsize=4096)
def _char_display_width(ch: str) -> int:
    return 0 if (ch in ("\ufe0e", "\ufe0f") or unicodedata.combining(ch)) else (2 if ord(ch) >= 0x1F000 or unicodedata.east_asian_width(ch) in ("W", "F") else 1)

@functools.lru_cache(maxsize=2048)
def _display_width(text: str) -> int:
    return sum(_char_display_width(c) for c in _ANSI_RE.sub("", text))

def _clip_display(text: str, max_cols: int) -> str:
    if max_cols <= 0: return ""
    if _display_width(text) <= max_cols: return text
    out, cols, i, n = [], 0, 0, len(text)
    while i < n:
        if text[i] == "\033" and (m := _ANSI_RE.match(text, i)):
            out.append(m.group(0)); i = m.end(); continue
        w = _char_display_width(text[i])
        if cols + w > max_cols: break
        out.append(text[i]); cols += w; i += 1
    if i < n: out.append(NC)
    return "".join(out)

def _wrap_display(text: str, width: int) -> List[str]:
    """Split ``text`` into lines at word boundaries, each no wider than ``width``.

    The empty-playlist guidance is written for a wide console. A portrait
    column used to run it past the panel border, so it is wrapped here rather
    than clipped: the instruction is worth more as two short lines than as one
    truncated one.
    """
    width = max(4, int(width))
    words = str(text or "").split()
    if not words:
        return [""]
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if _display_width(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        # A single word longer than the column is clipped, not left to overflow.
        current = word if _display_width(word) <= width else _clip_display(word, width)
    if current:
        lines.append(current)
    return lines or [""]


def _boxed_rule(left: str, text: str, right: str, inner: int, color: str, text_color: Optional[str] = None) -> str:
    """One horizontal rule with a label in it: `left ─── label ─── right`.

    Colour is tracked explicitly rather than re-emitted optimistically. A
    terminal applies SGR codes cumulatively, so writing the border colour, then
    the text colour, then the border colour again leaves the second half of the
    rule carrying *both* codes and the outline renders in the wrong hue on one
    side. Every colour change here is emitted once, and the border colour is
    restored only when the text actually changed it or `_clip_display` ended
    with a hard reset.
    """
    inner = max(4, int(inner))
    text_color = text_color or color

    clipped = _display_width(text) > inner
    label = _clip_display(text, inner) if clipped else text

    # `_clip_display` ends with a reset, so after it the border colour must be
    # re-emitted. The label also needs the border colour back before the
    # padding dashes when it used a different ink.
    needs_restore = clipped or text_color != color
    tail = color if needs_restore else ""
    padding = "─" * max(0, inner - _display_width(label))

    if text_color == color:
        return f"  {color}{left}{label}{tail}{padding}{right}{NC}"
    return f"  {color}{left}{text_color}{label}{tail}{padding}{right}{NC}"


def _fit_text_line(cands: List[str], max_cols: int) -> str:
    for l in cands:
        if _display_width(l) <= max_cols: return l
    return _clip_display(cands[-1] if cands else "", max_cols)

RAINBOW_CYCLE_SECONDS = 24.0


def rainbow_hue_at(seconds: float) -> float:
    """Hue (0..1) on the shared 24-second rainbow cycle."""
    try:
        return (float(seconds) % RAINBOW_CYCLE_SECONDS) / RAINBOW_CYCLE_SECONDS
    except (TypeError, ValueError):
        return 0.0


def _rotate_ansi_hue(seq: str, shift: float) -> str:
    """Rotate the hue of one 24-bit SGR colour, keeping its saturation/value."""
    import colorsys
    m = re.search(r"38;2;(\d+);(\d+);(\d+)", str(seq or ""))
    if not m:
        return seq
    r, g, b = (int(m.group(i)) / 255.0 for i in (1, 2, 3))
    h, l, sat = colorsys.rgb_to_hls(r, g, b)
    r2, g2, b2 = colorsys.hls_to_rgb((h + shift) % 1.0, l, sat)
    rgb = f"38;2;{int(round(r2 * 255))};{int(round(g2 * 255))};{int(round(b2 * 255))}"
    return re.sub(r"38;2;\d+;\d+;\d+", rgb, seq, count=1)


def animate_theme_palette(theme_name: str, seconds: float,
                          base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Rotate a theme's palette colours for the frame at ``seconds``.

    Only Dynamic Liquid Glass moves. t=0 and t=24 return the base palette
    unchanged, so the resting frame and the console manager agree.
    """
    pal = dict(base if base is not None else _THEME_PALETTES.get(theme_name, _DEFAULT_PALETTE))
    if theme_name != "dynamic_rainbow_liquid_glass":
        return pal
    hue = rainbow_hue_at(seconds)
    if hue == 0.0:
        return pal
    for key, value in list(pal.items()):
        if isinstance(value, str):
            pal[key] = _rotate_ansi_hue(value, hue)
    return pal


def theme_body_ansi(theme_name: str) -> str:
    """ANSI colour for player body text. Each theme keeps its own hue."""
    script_dir = str(Path(__file__).resolve().parent / "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    from liquid_glass_opengl import theme_text_rgb
    red, green, blue = theme_text_rgb(theme_name)
    return f"\033[38;2;{red};{green};{blue}m"


def theme_console_body_ansi(theme_name: str) -> str:
    """Body ink for the console pane.

    The pane is the same still-water field the glass paints, which is pale
    for every theme except Into Darkness. The theme hue is pulled toward
    black until it stays readable on that field. Glass hosts keep
    ``theme_body_ansi`` and do not use this.
    """
    script_dir = str(Path(__file__).resolve().parent / "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    from liquid_glass_opengl import theme_text_rgb
    from mp_theme_palette import readable_ink, theme_background
    red, green, blue = readable_ink(theme_text_rgb(theme_name), theme_background(theme_name))
    return f"\033[38;2;{red};{green};{blue}m"


def theme_has_light_console(theme_name: str) -> bool:
    """True when the theme paints a light console pane.

    The console pane is the glass still-water field, so this is true for
    every theme except Into Darkness, whose field is black. Light panes need
    the theme's darkened body ink. Liquid Glass hosts keep their own glass
    surface, so callers should only apply this to the console.
    """
    try:
        script_dir = str(Path(__file__).resolve().parent / "scripts")
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        from mp_theme_palette import theme_background
        red, green, blue = theme_background(theme_name)
    except Exception:
        return False
    return (0.299 * red + 0.587 * green + 0.114 * blue) >= 140


_LAST_TERMINAL_BG: Optional[str] = None


def apply_terminal_theme_background(theme_name: str, force: bool = False) -> None:
    """Paint the terminal's default pane with the active theme's background.

    The manager has always written OSC 11 for its own window, but a player
    opened in a fresh terminal kept that terminal profile's background (usually
    black), so Dreamworlds Ultra and every other theme looked black there. The
    glass hosts are transparent by design, so they are skipped.
    """
    global _LAST_TERMINAL_BG
    if os.environ.get("MP_LIQUID_GLASS") == "1" or os.environ.get("MP_GUI_SESSION") == "1":
        return
    try:
        if not sys.stdout.isatty():
            return
    except Exception:
        return
    try:
        script_dir = str(Path(__file__).resolve().parent / "scripts")
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        from mp_theme_palette import theme_background
        red, green, blue = theme_background(theme_name)
    except Exception:
        return
    hex_rgb = f"{red:02x}{green:02x}{blue:02x}"
    if not force and hex_rgb == _LAST_TERMINAL_BG:
        return
    _LAST_TERMINAL_BG = hex_rgb
    try:
        # BEL for xterm/VTE/Windows Terminal/iTerm, ST for Konsole and kitty.
        sys.stdout.write(f"\033]11;#{hex_rgb}\007\033]11;#{hex_rgb}\033\\")
        sys.stdout.flush()
    except Exception:
        pass

def _glass_rev() -> str:
    """Selection mark. Reverse video on a color-keyed Windows console punches the letters out."""
    if sys.platform == "win32" and os.environ.get("MP_LIQUID_GLASS") == "1":
        return "\033[48;2;21;40;64m"
    return "\033[7m"

def _rgb_from_ansi(seq: str) -> Optional[Tuple[int, int, int]]:
    """Read the first 24-bit foreground or background from an ANSI sequence."""
    m = re.search(r"\033\[[0-9;]*(?:38|48);2;(\d+);(\d+);(\d+)m", str(seq or ""))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None

def _selection_bar_ansi(theme_name: str, at: Optional[float] = None) -> Tuple[str, str]:
    """Background and foreground for a selected row, taken from the active theme.

    The playlist used to paint a fixed grey bar in the console and reverse video
    in Liquid Glass, so the selection looked the same on every theme. Every other
    themed row already uses the palette, so the bar uses the theme's highlight hue
    and picks black or white text from its luminance to stay readable.
    """
    pal = _THEME_PALETTES.get(theme_name, _DEFAULT_PALETTE)
    if at is not None:
        pal = animate_theme_palette(theme_name, at, pal)
    rgb = _rgb_from_ansi(pal.get("highlight", ""))
    if rgb is None:
        try:
            script_dir = str(Path(__file__).resolve().parent / "scripts")
            if script_dir not in sys.path:
                sys.path.insert(0, script_dir)
            from liquid_glass_opengl import theme_text_rgb
            rgb = theme_text_rgb(theme_name)
        except Exception:
            rgb = _rgb_from_ansi(COLOR_HOT_PURPLE) or (247, 37, 133)
    red, green, blue = rgb
    lum = (red * 299 + green * 587 + blue * 114) // 1000
    foreground = "\033[1;38;2;0;0;0m" if lum >= 140 else "\033[1;38;2;255;255;255m"
    return f"\033[48;2;{red};{green};{blue}m", foreground

def _win_apply_glass_colorkey(kernel32, handle) -> None:
    """Paint the default console color with the host's color key so the glass shows through."""
    raw = os.environ.get("MP_LIQUID_GLASS_COLORKEY", "").strip().lstrip("#")
    if os.environ.get("MP_LIQUID_GLASS") != "1" or len(raw) != 6:
        return
    try:
        r, g, b = int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    except ValueError:
        return
    import ctypes
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

    info = INFOEX()
    info.cbSize = ctypes.sizeof(INFOEX)
    kernel32.GetConsoleScreenBufferInfoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(INFOEX)]
    kernel32.GetConsoleScreenBufferInfoEx.restype = wintypes.BOOL
    kernel32.SetConsoleScreenBufferInfoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(INFOEX)]
    kernel32.SetConsoleScreenBufferInfoEx.restype = wintypes.BOOL
    if not kernel32.GetConsoleScreenBufferInfoEx(handle, ctypes.byref(info)):
        return
    info.ColorTable[0] = r | (g << 8) | (b << 16)
    info.wAttributes = info.wAttributes & 0xFFF0
    # SetConsoleScreenBufferInfoEx treats the inclusive window rect as exclusive.
    info.srWindow.Right += 1
    info.srWindow.Bottom += 1
    kernel32.SetConsoleScreenBufferInfoEx(handle, ctypes.byref(info))
    try:
        sys.stdout.write(f"\033]11;#{raw}\007")
        sys.stdout.flush()
    except Exception:
        pass

def _win_console_begin():
    global _WIN_CONSOLE
    if sys.platform != "win32": return None
    try:
        import msvcrt, ctypes
        h, k, m = msvcrt.get_osfhandle(sys.stdout.fileno()), ctypes.WinDLL("kernel32", use_last_error=True), ctypes.c_uint32()
        if not k.GetConsoleMode(h, ctypes.byref(m)): return None
        k.SetConsoleMode(h, (m.value | 0x0004) & ~0x0002)
        _win_apply_glass_colorkey(k, h)
        _win_hide_console_scrollbar()
        _WIN_CONSOLE = {"handle": h, "mode": m.value, "kernel32": k}
        return _WIN_CONSOLE
    except Exception: return None

def _tty_size(fallback: Tuple[int, int] = (80, 24)) -> Tuple[int, int]:
    """Real window size. Ignore LINES and COLUMNS.

    shutil.get_terminal_size() returns those variables and never reads the
    window. The menu exports its own height, so a player opened from it
    draws past the bottom of the new window and the pane looks blank.
    """
    for stream in (sys.stdout, sys.stdin):
        try:
            fd = stream.fileno()
            if fd is None or fd < 0 or not stream.isatty():
                continue
            size = os.get_terminal_size(fd)
            if size.columns > 0 and size.lines > 0:
                return int(size.columns), int(size.lines)
        except (OSError, ValueError, AttributeError):
            continue
    try:
        s = shutil.get_terminal_size(fallback)
        return int(s.columns), int(s.lines)
    except Exception:
        return fallback

def _win_visible_size(fallback: Tuple[int, int] = (80, 24)) -> Tuple[int, int]:
    return _tty_size(fallback)

def _win_hide_console_scrollbar():
    """Drop the conhost scrollbar. A taller screen buffer draws it on the right."""
    if sys.platform != "win32":
        return
    try:
        script_dir = Path(__file__).resolve().parent / "scripts"
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        from windows_console_scroll import hide_attached_console_scrollbar
        hide_attached_console_scrollbar()
    except Exception:
        pass

def _win_console_end(state):
    global _WIN_CONSOLE
    if state:
        try: state["kernel32"].SetConsoleMode(state["handle"], state["mode"])
        except Exception: pass
    _WIN_CONSOLE = None

_SGR_RE = re.compile(r"\033\[([0-9;]*)m")

def _strip_bold_ansi(s: str) -> str:
    """Remove the bold parameter from every SGR sequence in one line.

    Liquid Glass draws no bold text, so this runs over every line of every
    frame. Nearly every occurrence is the leading form (`\\033[1;38;2;...m`
    from a border or the white ink, or a bare `\\033[1m`), so those two exact
    shapes are handled with plain string replacement and the regular
    expression only runs on the rare line that carries bold mid-list.

    Results are memoised per line: the header, the playlist and the key hints
    are byte-identical between frames, and re-parsing their escapes 50-60
    times a second was pure waste.
    """
    if "1" not in s:
        return s
    if ("\033[1m" not in s and "\033[1;" not in s
            and ";1m" not in s and ";1;" not in s):
        return s
    return _strip_bold_ansi_cached(s)

@functools.lru_cache(maxsize=8192)
def _strip_bold_ansi_cached(s: str) -> str:
    if ";1m" not in s and ";1;" not in s:
        # Bold is the first parameter: drop it without parsing anything.
        return s.replace("\033[1m", "").replace("\033[1;", "\033[")

    def repl(m):
        codes = [c for c in m.group(1).split(";") if c != "1"]
        return f"\033[{';'.join(codes)}m" if codes else ""

    return _SGR_RE.sub(repl, s)

_MP_VISUALIZER_TEXT = False
_LIQUID_FRAME_CACHE = None
_LIQUID_FRAME_SIZE = None
_FG_TRUECOLOR_RE = re.compile(r"\033\[((?:\d+;)*)38;2;(\d+);(\d+);(\d+)((?:;\d+)*)m")

def _lift_visualizer_rgb(r: int, g: int, b: int) -> Tuple[int, int, int]:
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    if luma >= 168:
        return r, g, b
    mix = min(1.0, (168.0 - luma) / 168.0) * 0.82
    return (
        min(255, int(r + (255 - r) * mix)),
        min(255, int(g + (255 - g) * mix)),
        min(255, int(b + (255 - b) * mix)),
    )

@functools.lru_cache(maxsize=16384)
def _lifted_truecolor_escape(prefix: str, r: int, g: int, b: int, suffix: str) -> str:
    """The brightened replacement for one foreground TrueColor SGR sequence.

    `_guard_visualizer_text` visits every coloured escape of every frame while
    the MP Visualizer is showing. The equaliser cycles through a fixed set of
    hues, and the panel ink never changes, so the same few hundred
    (prefix, r, g, b, suffix) combinations repeat forever. Caching the finished
    escape removes the arithmetic and the f-string from the per-frame path.
    """
    r, g, b = _lift_visualizer_rgb(r, g, b)
    return f"\033[{prefix}38;2;{r};{g};{b}{suffix}m"

def _guard_visualizer_text(line: str) -> str:
    """Brighten dim foregrounds. Opaque button chips keep their own colors.

    Memoised per line. Only the equaliser rows actually change between frames;
    the header, playlist and key hints are byte-identical, so re-walking their
    escapes 50-60 times a second was pure waste. The cache is bounded because
    the animated rows are never hits.
    """
    return _guard_visualizer_text_cached(line) if "\033[" in line else line

@functools.lru_cache(maxsize=4096)
def _guard_visualizer_text_cached(line: str) -> str:
    line = line.replace("\033[2m", "")

    def repl(match):
        if "48;" in (match.group(1) or ""):
            return match.group(0)
        r, g, b = int(match.group(2)), int(match.group(3)), int(match.group(4))
        # Pure black is a deliberate choice, not a dim ink: the Liquid Glass
        # PLAYING badge uses it on the pale pane. Lifting it would turn the
        # badge into ~#d1d1d1 grey. The console badge is white and is left
        # alone because its luminance is already above the lift floor.
        if (r, g, b) == (0, 0, 0):
            return match.group(0)
        return _lifted_truecolor_escape(
            match.group(1) or "", r, g, b, match.group(5) or "")

    return _FG_TRUECOLOR_RE.sub(repl, line)

_MP_VIS_API = None

def _mp_visualizer_api():
    """The visualizer helper module, imported once.

    Resolving this file's path and importing the module on every frame made
    the action row stat the install directory at the frame rate.
    """
    global _MP_VIS_API
    if _MP_VIS_API is not None:
        return _MP_VIS_API
    script_dir = Path(__file__).resolve().parent / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    import liquid_player_visualizer as api
    _MP_VIS_API = api
    return api

def _is_dead_output_error(exc: BaseException) -> bool:
    """True when the terminal/pty this process draws to is gone.

    EPIPE/EBADF/EIO on a stdout write cannot be fixed by redrawing, so callers
    stop instead of logging the same frame error every 20 ms.
    """
    if isinstance(exc, (BrokenPipeError, ValueError)):
        return True  # ValueError: "I/O operation on closed file"
    if isinstance(exc, OSError):
        return getattr(exc, "errno", None) in (errno.EPIPE, errno.EBADF, errno.EIO, errno.ENXIO, errno.ENODEV)
    return False


def _fit_frame_plan(total: int, height: int) -> Optional[Tuple[int, int]]:
    """How `_fit_frame` splits a frame: ``(keep_head, keep_tail)``, or None.

    None means the frame already fits and every row keeps its position. The
    click geometry needs the same plan, because dropping the middle band moves
    every row after it and a click recorded from the untrimmed frame would land
    on the wrong line.
    """
    if height <= 0 or total <= height:
        return None
    keep_tail = min(total - 1, max(2, height // 3))
    keep_head = max(1, height - keep_tail)
    if keep_head >= total:
        return (min(total, height), 0)
    return (keep_head, keep_tail)


def _fit_frame(lines: List[str], height: int) -> List[str]:
    """Trim a frame to the console height without losing its bottom row.

    Mini, Micro and the full CLI all end with the key hints, and Mini/Micro
    also carry the clickable action row near the bottom. A renderer that
    returned one row too many used to have that last line clipped by
    `_present_frame`, which silently removed the `[G] MP Gui` and `[b] Bass`
    hints. Dropping from the middle keeps the footer intact.

    This is a backstop: each renderer also reserves room for the visualizer
    block, which is the variable-height part.
    """
    plan = _fit_frame_plan(len(lines), height)
    if plan is None:
        return lines
    keep_head, keep_tail = plan
    if not keep_tail:
        return lines[:keep_head]
    return lines[:keep_head] + lines[len(lines) - keep_tail:]


def _fit_frame_row(index: int, plan: Optional[Tuple[int, int]], total: int) -> int:
    """1-based row of a full-frame line index after `_fit_frame`, or 0 if dropped."""
    if plan is None:
        return index + 1
    keep_head, keep_tail = plan
    if index < keep_head:
        return index + 1
    if keep_tail and index >= total - keep_tail:
        return keep_head + (index - (total - keep_tail)) + 1
    return 0


def _present_frame(lines: List[str], width: int, height: int):
    global _MP_VISUALIZER_TEXT, _LIQUID_FRAME_CACHE, _LIQUID_FRAME_SIZE
    usable = max(1, width - 1)
    guard = bool(_MP_VISUALIZER_TEXT) and os.environ.get("MP_LIQUID_GLASS") == "1"
    _MP_VISUALIZER_TEXT = False
    liquid = os.environ.get("MP_LIQUID_GLASS") == "1"
    if liquid:
        lines = [_strip_bold_ansi(l) for l in lines]
    if guard:
        lines = [_guard_visualizer_text(l) for l in lines]
    clipped = [_clip_display(l, usable) + "\033[K" for l in lines[:height]]
    clipped.extend(["\033[K"] * (height - len(clipped)))
    clear = "\033[2J" if _tty_resized() else ""
    if liquid:
        frame = tuple(clipped)
        size = (usable, height)
        full = clear or _LIQUID_FRAME_CACHE is None or _LIQUID_FRAME_SIZE != size
        if full:
            body = "\033[2J\033[H" + "\n".join(frame)
        else:
            changed = [
                f"\033[{i + 1};1H{line}"
                for i, line in enumerate(frame)
                if line != _LIQUID_FRAME_CACHE[i]
            ]
            body = "".join(changed)
            if body:
                body += "\033[H"
        _LIQUID_FRAME_CACHE, _LIQUID_FRAME_SIZE = frame, size
        if body:
            sys.stdout.write(f"\033[?2026h\033[?7l{body}\033[?2026l")
            sys.stdout.flush()
        return

    _LIQUID_FRAME_CACHE = _LIQUID_FRAME_SIZE = None
    body = "\n".join(clipped)
    # CLI mode keeps its full-frame update path; only redraw when rows differ
    # in the embedded Liquid VTE, where sending the whole screen is expensive.
    sys.stdout.write(f"\033[?2026h\033[?7l{clear}\033[H{body}\033[?2026l")
    sys.stdout.flush()

_THEME_PALETTES = {
    "stream_of_frequency": {"highlight": "\033[38;2;0;242;255m", "active": "\033[38;2;76;217;123m", "border": "\033[38;2;48;49;131m", "accent": "\033[38;2;245;237;106m"},
    "dynamic_rainbow_liquid_glass": {"highlight": "\033[38;2;176;88;255m", "active": "\033[38;2;255;45;142m", "border": "\033[38;2;176;88;255m", "accent": "\033[38;2;0;242;254m"},
    "dreamworlds_liquid": {"highlight": "\033[38;2;0;245;255m", "active": "\033[38;2;199;125;255m", "border": "\033[38;2;72;149;239m", "accent": "\033[38;2;0;245;212m"},
    "cosmic_supernova": {"highlight": "\033[38;2;224;86;253m", "active": "\033[38;2;0;245;212m", "border": "\033[38;2;224;86;253m", "accent": "\033[38;2;76;201;240m"},
    "dreamworlds_neon": {"highlight": COLOR_ELECTRIC_CYAN, "active": COLOR_NEON_PINK, "border": COLOR_NEON_PINK, "accent": COLOR_ELECTRIC_CYAN},
    "retro_studio": {"highlight": "\033[38;2;241;196;15m", "active": "\033[38;2;230;126;34m", "border": "\033[38;2;241;196;15m", "accent": "\033[38;2;243;156;18m"},
    "cyber_neon": {"highlight": "\033[38;2;76;201;240m", "active": "\033[38;2;255;0;85m", "border": "\033[38;2;76;201;240m", "accent": "\033[38;2;255;0;85m"},
    "phosphor": {"highlight": "\033[38;2;57;255;20m", "active": "\033[38;2;166;255;0m", "border": "\033[38;2;57;255;20m", "accent": "\033[38;2;0;255;102m"},
    "ice_cold": {"highlight": "\033[38;2;0;229;255m", "active": "\033[38;2;136;192;208m", "border": "\033[38;2;0;229;255m", "accent": "\033[38;2;129;161;193m"},
    "retro_terminal": {"highlight": "\033[1;37m", "active": "\033[1;37m", "border": "\033[1;37m", "accent": "\033[1;37m"},
    "retro_linux_memories": {"highlight": "\033[38;2;91;134;187m", "active": "\033[38;2;21;83;158m", "border": "\033[38;2;84;84;83m", "accent": "\033[38;2;240;173;78m"},
    "into_darkness_void_out": {"highlight": "\033[1;37m", "active": "\033[38;2;200;200;200m", "border": "\033[38;2;72;72;72m", "accent": "\033[1;37m"},
    # Blood Moon: vivid crimson selection bar, deep ruby ink and a wine-tinted
    # console field, matching scripts/mp_theme_palette.py exactly.
    "blood_moon": {"highlight": "\033[38;2;255;32;64m", "active": "\033[38;2;255;90;95m", "border": "\033[38;2;180;0;32m", "accent": "\033[38;2;137;17;34m"},
    # Sensorium: the bright chartreuse stays on the selection fill, but the ink
    # and borders are darkened so they read on the light coral console pane.
    # The champagne-gold gradient lives in _base_theme_rgb.
    "sensorium": {"highlight": "\033[38;2;200;255;61m", "active": "\033[38;2;92;36;0m", "border": "\033[38;2;11;47;107m", "accent": "\033[38;2;74;52;0m"},
    # `highlight` is the selected row's *fill*. White, so the row reads as a
    # bar on the white pane; _selection_bar_ansi picks black text from its
    # luminance. The remaining keys are ink and are black.
    "the_whitest_white": {"highlight": "\033[48;2;255;255;255m", "active": "\033[38;2;0;0;0m", "border": "\033[38;2;0;0;0m", "accent": "\033[38;2;0;0;0m"},
}
_DEFAULT_PALETTE = {"highlight": COLOR_HOT_PURPLE, "active": COLOR_NEON_PINK, "border": COLOR_HOT_PURPLE, "accent": COLOR_ELECTRIC_CYAN}

_THEME_ALIASES = {
    "stream_of_frequency": "stream_of_frequency",
    "stream_of_frequency_theme": "stream_of_frequency",
    "stream of frequency": "stream_of_frequency",
    "stream-of-frequency": "stream_of_frequency",
    "stream": "stream_of_frequency",
    "frequency": "stream_of_frequency",
    "sof": "stream_of_frequency",
    "frequency_stream": "stream_of_frequency",
    "unix_terminal": "retro_terminal", "retro_unix": "retro_terminal", "white_on_black": "retro_terminal", "old_style_unix": "retro_terminal", "terminal": "retro_terminal", "classic": "retro_terminal",
    "cosmic": "cosmic_supernova", "supernova": "cosmic_supernova",
    "sensorium": "sensorium", "sensorium interface": "sensorium", "sensorium_interface": "sensorium",
    "cyberpunk": "cyber_neon", "cyber": "cyber_neon",
    "matrix": "phosphor", "nord": "ice_cold", "dracula": "dreamworlds_neon", "gruvbox": "retro_studio",
    "dreamworlds": "dreamworlds_ultra", "default": "dreamworlds_ultra", "mplanetarian": "dreamworlds_ultra",
    "dynamic_rainbow_liquid_glass": "dynamic_rainbow_liquid_glass",
    "dynamic_liquid_glass_rainbow": "dynamic_rainbow_liquid_glass",
    "dynamic_rainbow": "dynamic_rainbow_liquid_glass",
    "rainbow_liquid_glass": "dynamic_rainbow_liquid_glass",
    "rainbow": "dynamic_rainbow_liquid_glass",
    "dynamic_liquid_glass": "dynamic_rainbow_liquid_glass",
    "dynamic_liquid_glass_[rainbow_colours]_(new)": "dynamic_rainbow_liquid_glass",
    "dynamic_liquid_glass_[rainbow_colours]": "dynamic_rainbow_liquid_glass",
    "dynamic liquid glass [rainbow colours] (new)": "dynamic_rainbow_liquid_glass",
    "dynamic liquid glass [rainbow colours]": "dynamic_rainbow_liquid_glass",
    "mp_dreamworlds_liquid_transparency": "dreamworlds_liquid", "dreamworlds_liquid_transparency": "dreamworlds_liquid",
    "liquid_transparency": "dreamworlds_liquid", "liquid": "dreamworlds_liquid", "glass": "dreamworlds_liquid",
    "liquid_glass": "dreamworlds_liquid", "mp_liquid": "dreamworlds_liquid",
    "retro_linux_memories": "retro_linux_memories",
    "into_darkness_void_out": "into_darkness_void_out",
    "into darkness (void out)": "into_darkness_void_out",
    "into darkness": "into_darkness_void_out",
    "void out": "into_darkness_void_out",
    "blood_moon": "blood_moon",
    "blood moon": "blood_moon",
    "blood-moon": "blood_moon",
    "crimson": "blood_moon",
    "vampire": "blood_moon",
    "blood_ruby": "blood_moon",
    "retro_linux": "retro_linux_memories",
    "linux_memories": "retro_linux_memories",
    "retro-linux-memories": "retro_linux_memories",
    "retro-linux": "retro_linux_memories",
    "retro linux memories": "retro_linux_memories",
    "retro linux": "retro_linux_memories",
    "veracrypt": "retro_linux_memories",
    "retrolinux": "retro_linux_memories",
    "linux": "retro_linux_memories",
    "charcoal_vault": "retro_linux_memories",
    "charcoal": "retro_linux_memories",
    "charcoal vault": "retro_linux_memories",
    "charcoal vault (veracrypt dark)": "retro_linux_memories",
    "vault": "retro_linux_memories",
    "dark_vault": "retro_linux_memories",
    "veracrypt_dark": "retro_linux_memories",
}

class EqualizerVisualizer:
    STYLES = [
        "stream_of_frequency_eq",
        "stream_of_frequency_liquid_eq",
        "stream_of_frequency_overview_eq",
        "rainbow_mandala_eq",
        "rainbow_wormhole_eq",
        "rainbow_aurora_eq",
        "stereo_eq",
        "neon_prism_eq",
        "stardust_particles_eq",
        "master_spectrum",
        "analog_vu",
        "oscilloscope",
    ]
    STYLE_NAMES = {
        "stream_of_frequency_eq": "Stream of Frequency (Dynamic Spectrogram)",
        "stream_of_frequency_liquid_eq": "Stream of Frequency (Liquid Spectrogram)",
        "stream_of_frequency_overview_eq": "Stream of Frequency (MPlanterian Overview Effect)",
        "rainbow_mandala_eq": "Rainbow Mandala Bloom (Kaleidoscopic Spectrum)",
        "rainbow_wormhole_eq": "Rainbow Wormhole (Hyperspace Plasma Tunnel)",
        "rainbow_aurora_eq": "Rainbow Aurora Cascade (Holographic Ribbons)",
        "stereo_eq": "Dual Stereo (12-Band)",
        "neon_prism_eq": "Neon Chroma Prism (Thin Hi-Res)",
        "stardust_particles_eq": "Stardust Chroma (Thin Lines & Falling Dust)",
        "master_spectrum": "Master Spectrum (24-Band)",
        "analog_vu": "Analog Studio VU",
        "oscilloscope": "Oscilloscope Waveform",
    }
    STYLE_ALIASES = {
        # Stream of Frequency — Liquid Spectrogram
        "liquid_spectrogram": "stream_of_frequency_liquid_eq",
        "stream_liquid": "stream_of_frequency_liquid_eq",
        "stream_of_frequency_liquid": "stream_of_frequency_liquid_eq",
        "liquid_stream": "stream_of_frequency_liquid_eq",
        "liquid": "stream_of_frequency_liquid_eq",
        "liquid_pool": "stream_of_frequency_liquid_eq",
        # Stream of Frequency — MPlanterian Overview Effect
        "overview": "stream_of_frequency_overview_eq",
        "overview_effect": "stream_of_frequency_overview_eq",
        "mplanetarian_overview": "stream_of_frequency_overview_eq",
        "mplanetarian_overview_effect": "stream_of_frequency_overview_eq",
        "stream_overview": "stream_of_frequency_overview_eq",
        "topographic": "stream_of_frequency_overview_eq",
        # Rainbow Mandala Bloom
        "mandala": "rainbow_mandala_eq",
        "mandala_eq": "rainbow_mandala_eq",
        "rainbow_mandala": "rainbow_mandala_eq",
        "kaleidoscope": "rainbow_mandala_eq",
        "kaleidoscopic": "rainbow_mandala_eq",
        "bloom": "rainbow_mandala_eq",
        "petals": "rainbow_mandala_eq",
        "rainbow_bloom": "rainbow_mandala_eq",
        # Rainbow Wormhole
        "wormhole": "rainbow_wormhole_eq",
        "wormhole_eq": "rainbow_wormhole_eq",
        "rainbow_wormhole": "rainbow_wormhole_eq",
        "tunnel": "rainbow_wormhole_eq",
        "hyperspace": "rainbow_wormhole_eq",
        "vortex": "rainbow_wormhole_eq",
        "plasma": "rainbow_wormhole_eq",
        # Rainbow Aurora Cascade
        "aurora": "rainbow_aurora_eq",
        "aurora_eq": "rainbow_aurora_eq",
        "rainbow_aurora": "rainbow_aurora_eq",
        "ribbons": "rainbow_aurora_eq",
        "cascade": "rainbow_aurora_eq",
        "holographic": "rainbow_aurora_eq",
        "curtains": "rainbow_aurora_eq",
    }
    THEMES = [
        "stream_of_frequency",
        "dreamworlds_ultra",
        "dynamic_rainbow_liquid_glass",
        "cosmic_supernova",
        "dreamworlds_liquid",
        "dreamworlds_neon",
        "retro_studio",
        "cyber_neon",
        "phosphor",
        "ice_cold",
        "retro_terminal",
        "sensorium",
        "retro_linux_memories",
        "the_whitest_white",
        "into_darkness_void_out",
        "blood_moon",
    ]
    THEME_NAMES = {
        "stream_of_frequency": "Stream of Frequency [Special Edition]",
        "dreamworlds_ultra": "Dreamworlds Ultra",
        "dynamic_rainbow_liquid_glass": "Dynamic Liquid Glass [Rainbow Colours] (New)",
        "cosmic_supernova": "Cosmic Supernova",
        "dreamworlds_liquid": "MP Dreamworlds Liquid Transparency",
        "dreamworlds_neon": "Dreamworlds Neon",
        "retro_studio": "Retro Studio",
        "cyber_neon": "Cyber Neon",
        "phosphor": "Phosphor",
        "ice_cold": "Ice Cold",
        "retro_terminal": "Retro Terminal",
        "sensorium": "Sensorium",
        "retro_linux_memories": "Retro Linux Memories",
        "the_whitest_white": "The Whitest White",
        "into_darkness_void_out": "Into Darkness (Void Out)",
        "blood_moon": "Blood Moon [Crimson Eclipse]",
    }

    def __init__(self, default_style: Optional[str] = None, default_theme: Optional[str] = None, theme: Optional[str] = None, style: Optional[str] = None):
        _ensure_numpy()
        self.style_idx = self.theme_idx = 0
        self.dust_particles, self.last_dust_update, self.dust_rng = [], time.time(), random.Random()
        self._sof_buffer, self._sof_particles, self._sof_last_time, self._sof_rng = None, [], time.time(), random.Random()
        self._init_rainbow_fx_state()
        selected_theme = theme or default_theme
        try:
            th_file = CONFIG_DIR / "theme"
            # A Liquid Mixer deck is started with --theme once and must still
            # follow the shared file afterwards, exactly like MP Gui and the
            # standalone player. Without this a C press in the mixer recoloured
            # the mixer chrome but left both decks on the theme they booted
            # with, so the two windows disagreed.
            _mixer_deck = os.environ.get("MP_LIQUID_GLASS_MIXER") == "1"
            if (not selected_theme or _mixer_deck) and th_file.is_file():
                _shared = th_file.read_text(encoding="utf-8").strip().lower()
                if _shared:
                    # Adopt the shared value only when it resolves to a theme
                    # this player can actually paint; a stale or foreign id
                    # must not displace the --theme the deck was started with.
                    _probe = _THEME_ALIASES.get(_shared, _shared)
                    if _probe not in self.THEMES:
                        _probe = _mixer_deck_theme_fallback(_probe)
                    if _probe in self.THEMES:
                        selected_theme = _shared
            # Dreamworlds Ultra is the built-in default when no theme has ever
            # been saved, so a bare player launch matches MP Gui and the CLI.
            if not selected_theme:
                selected_theme = "dreamworlds_ultra"
            if selected_theme:
                self.set_theme(selected_theme)
        except Exception: pass
        try:
            eq_file = CONFIG_DIR / "eq_style"
            ts = style or default_style
            if ts:
                self.style_idx = self._resolve_style(ts)
            elif eq_file.is_file():
                self.style_idx = self._resolve_style(eq_file.read_text(encoding="utf-8").strip().lower())
        except Exception: pass
        # The Stream of Frequency theme owns its matching spectrogram effect.
        # A stale persisted eq_style must not override the selected theme.
        if not (style or default_style) and selected_theme and self.theme == "stream_of_frequency":
            self.style_idx = self.STYLES.index("stream_of_frequency_eq")

    @classmethod
    def _resolve_style(cls, name: Optional[str]) -> int:
        """Return the STYLES index for a style key or a friendly alias.

        Unknown names fall back to the Stream of Frequency spectrogram, which
        keeps an existing `eq_style` file working after an effect is renamed.
        """
        key = str(name or "").strip().lower()
        if key in cls.STYLES:
            return cls.STYLES.index(key)
        if key in cls.STYLE_ALIASES:
            return cls.STYLES.index(cls.STYLE_ALIASES[key])
        if key in ("stream", "stream_of_frequency", "stream_of_frequency_eq", "frequency_stream",
                   "frequency_stream_eq", "spectrogram", "dynamic_spectrogram", "river", "rainbow_stream"):
            return cls.STYLES.index("stream_of_frequency_eq")
        if key in ("neon", "prism", "chroma", "neon_prism"):
            return cls.STYLES.index("neon_prism_eq")
        if key in ("stardust", "particles", "dust", "falling_dust", "stardust_eq", "chroma_dust"):
            return cls.STYLES.index("stardust_particles_eq")
        return cls.STYLES.index("stream_of_frequency_eq")

    def _init_rainbow_fx_state(self):
        """Animation state for the three full-field rainbow effects."""
        rng = random.Random()
        self._fx_rng = rng
        # Mandala Bloom: rotating petal rings, radial sparks, spiral trail.
        self._mandala_rings = []
        self._mandala_sparks = []
        self._mandala_last = time.time()
        self._mandala_rotation = 0.0
        self._mandala_pulse = 0.0
        # Wormhole: nested elliptical rings travelling toward the viewer.
        self._worm_rings = []
        self._worm_motes = []
        self._worm_last = time.time()
        self._worm_phase = 0.0
        # Aurora: ribbon curtains drifting in a sine lattice.
        self._aurora_curtains = []
        self._aurora_embers = []
        self._aurora_last = time.time()
        self._aurora_phase = 0.0

    @property
    def style(self) -> str: return self.STYLES[self.style_idx]
    @property
    def theme(self) -> str: return self.THEMES[self.theme_idx]
    @theme.setter
    def theme(self, name: str): self.set_theme(name)

    @property
    def b_col(self) -> str:
        return f"{BOLD}{self.get_theme_palette().get('border', COLOR_HOT_PURPLE)}"

    def set_theme(self, name: str):
        c = _THEME_ALIASES.get(str(name).strip().lower(), str(name).strip().lower())
        # A Liquid Mixer deck follows the shared theme file, which may name a
        # theme this player's table does not carry (blood_moon, tokyo, ...).
        # Map it to the closest accepted theme instead of leaving the deck on
        # whatever it booted with.
        if c not in self.THEMES:
            # Only a theme the mixer genuinely offers may be remapped. Garbage
            # must stay garbage so sync_theme_from_file() reports no change and
            # an unknown value cannot silently repaint the window.
            c = _mixer_deck_theme_fallback(c)
        if c in self.THEMES:
            self.theme_idx = self.THEMES.index(c)
            if c == "stream_of_frequency":
                self.style_idx = self.STYLES.index("stream_of_frequency_eq")

    def cycle_style(self):
        self.style_idx = (self.style_idx + 1) % len(self.STYLES)
        try: (CONFIG_DIR / "eq_style").write_text(self.style, encoding="utf-8")
        except Exception: pass

    def set_style(self, name: str) -> bool:
        """Select an effect by key or friendly alias, persisting the choice.

        Returns False for an unknown name so a CLI or IPC caller can report it
        instead of silently landing on the fallback effect.
        """
        if not name:
            return False
        key = str(name).strip().lower()
        known = key in self.STYLES or key in self.STYLE_ALIASES or self._resolve_style(key) != 0 or key == self.STYLES[0]
        if not known:
            return False
        self.style_idx = self._resolve_style(key)
        try: (CONFIG_DIR / "eq_style").write_text(self.style, encoding="utf-8")
        except Exception: pass
        return True

    def cycle_theme(self):
        self.theme_idx = (self.theme_idx + 1) % len(self.THEMES)
        try: (CONFIG_DIR / "theme").write_text(self.theme, encoding="utf-8")
        except Exception: pass
        if self.theme == "stream_of_frequency":
            self.style_idx = self.STYLES.index("stream_of_frequency_eq")
            try: (CONFIG_DIR / "eq_style").write_text(self.style, encoding="utf-8")
            except Exception: pass

    def sync_theme_from_file(self) -> bool:
        """Follow a theme the other window selected.

        MP Gui, MP Liquid Player, and the console manager all read
        ~/.config/mix-manager/theme. Pressing C in one of them rewrites that
        file, and the others poll it so a single C keypress recolours every
        open window instead of leaving one on the previous theme.

        Returns True when the theme actually changed, so the caller can clear
        its frame cache.
        """
        try:
            th_file = CONFIG_DIR / "theme"
            if not th_file.is_file():
                return False
            saved = th_file.read_text(encoding="utf-8").strip().lower()
        except Exception:
            return False
        if not saved or saved == self.theme:
            return False
        before = self.theme
        self.set_theme(saved)
        return self.theme != before

    def get_style_display_name(self) -> str: return self.STYLE_NAMES.get(self.style, self.style.replace("_", " ").title())
    def get_theme_display_name(self) -> str: return self.THEME_NAMES.get(self.theme, self.theme.replace("_", " ").title())
    def begin_frame(self, at: Optional[float] = None) -> None:
        """Stamp the frame clock so every surface shares one rainbow hue.

        The border, the selection bar, the pitch knob and the equaliser all
        read this value, so they cannot drift apart mid-frame when each painted
        its own hue from a slightly later clock.
        """
        self._frame_at = time.time() if at is None else float(at)

    def frame_time(self) -> float:
        """Seconds on the current frame clock (0.0 before the first frame)."""
        return getattr(self, "_frame_at", 0.0)

    def get_theme_palette(self, at: Optional[float] = None) -> Dict[str, str]:
        """The palette for the active theme, animated where the theme asks.

        Dynamic Liquid Glass is the one theme whose colours move: the Web
        Companion sweeps it on a 24-second cycle and the shader hosts drive it
        per frame, so the console player reads the same clock instead of a
        frozen table entry. ``at`` is seconds into the cycle; omit it to use
        the wall clock. Every other theme returns its static palette, and t=0
        is byte-for-byte the shipped entry so a still frame matches the manager.
        """
        pal = _THEME_PALETTES.get(self.theme, _DEFAULT_PALETTE)
        if self.theme != "dynamic_rainbow_liquid_glass":
            return pal
        if at is None:
            t = getattr(self, "_frame_at", None)
            if t is None:
                t = time.time()
        else:
            t = float(at)
        return animate_theme_palette(self.theme, t, pal)

    def _base_theme_rgb(self, c_frac: float, ultra_floor: float = 0.10) -> Tuple[float, float, float]:
        t = self.theme
        if t in ("stream_of_frequency", "stream", "frequency"):
            if c_frac < 0.25:
                s = c_frac / 0.25
                return 0.19 * (1.0 - s), 0.19 + 0.76 * s, 0.51 + 0.49 * s
            if c_frac < 0.50:
                s = (c_frac - 0.25) / 0.25
                return 0.0 + 0.30 * s, 0.95 - 0.10 * s, 1.0 - 0.52 * s
            if c_frac < 0.75:
                s = (c_frac - 0.50) / 0.25
                return 0.30 + 0.66 * s, 0.85 + 0.08 * s, 0.48 - 0.07 * s
            s = (c_frac - 0.75) / 0.25
            return 0.96 - 0.02 * s, 0.93 - 0.80 * s, 0.41 - 0.28 * s
        if t in ("sensorium", "sensorium_interface", "sensorium interface"):
            # Tangerine -> acid chartreuse -> electric azure. Bright at both
            # ends, so the equaliser reads as a sunrise with a blue horizon.
            if c_frac < 0.50:
                s = c_frac / 0.50
                return 1.0 - 0.22 * s, 0.48 + 0.52 * s, 0.09 + 0.15 * s
            s = (c_frac - 0.50) / 0.50
            return 0.78 - 0.55 * s, 1.0 - 0.47 * s, 0.24 + 0.76 * s
        if t == "cosmic_supernova":
            if c_frac < 0.25: s = c_frac / 0.25; return 0.0, 0.96 - 0.17 * s, 0.83 + 0.11 * s
            if c_frac < 0.55: s = (c_frac - 0.25) / 0.30; return 0.30 + 0.58 * s, 0.79 - 0.45 * s, 0.94 + 0.05 * s
            if c_frac < 0.80: s = (c_frac - 0.55) / 0.25; return 0.88 + 0.12 * s, 0.34 - 0.14 * s, 0.99 - 0.59 * s
            s = (c_frac - 0.80) / 0.20; return 1.0, 0.20 - 0.10 * s, 0.40 - 0.25 * s
        if t in ("dreamworlds_liquid", "liquid_transparency", "mp_dreamworlds_liquid_transparency"):
            if c_frac < 0.33: s = c_frac / 0.33; return 0.0, 0.96 - 0.16 * s, 1.0
            if c_frac < 0.66: s = (c_frac - 0.33) / 0.33; return 0.28 * s, 0.80 - 0.31 * s, 1.0 - 0.06 * s
            s = (c_frac - 0.66) / 0.34; return 0.28 + 0.50 * s, 0.49 * (1.0 - s), 0.94 + 0.06 * s
        if t in ("dreamworlds_ultra", "dreamworlds"):
            h6 = ((c_frac * 0.88 + 0.82) % 1.0) * 6.0
            i, f = int(h6), h6 - int(h6)
            q, u = 1.0 - f, ultra_floor
            return ((1.0, f, u), (q, 1.0, u), (u, 1.0, f), (u, q, 1.0), (f, u, 1.0), (1.0, u, q))[i % 6]
        if t in ("dynamic_rainbow_liquid_glass", "dynamic_liquid_glass_rainbow", "dynamic_rainbow", "rainbow"):
            h6 = ((c_frac * 0.88 + 0.85) % 1.0) * 6.0
            i, f = int(h6), h6 - int(h6)
            q, u = 1.0 - f, ultra_floor
            return ((1.0, f, u), (q, 1.0, u), (u, 1.0, f), (u, q, 1.0), (f, u, 1.0), (1.0, u, q))[i % 6]
        if t == "dreamworlds_neon":
            if c_frac < 0.33: s = c_frac / 0.33; return 0.0, 0.90 - 0.50 * s, 1.0
            if c_frac < 0.66: s = (c_frac - 0.33) / 0.33; return 0.70 * s + 0.20, 0.10, 1.0 - 0.20 * s
            s = (c_frac - 0.66) / 0.34; return 1.0, 0.15 + 0.40 * s, 0.50 - 0.50 * s
        if t == "retro_studio":
            if c_frac < 0.5: s = c_frac / 0.5; return 0.10 + 0.70 * s, 0.85 + 0.15 * s, 0.20 - 0.15 * s
            s = (c_frac - 0.5) / 0.5; return 0.90 + 0.10 * s, 0.85 - 0.60 * s, 0.10
        if t == "cyber_neon": return 0.45 + 0.50 * math.sin(c_frac * math.pi), 0.10 + 0.70 * c_frac, 0.95 - 0.20 * c_frac
        if t in ("retro_linux_memories", "retro_linux", "linux_memories", "veracrypt"):
            if c_frac < 0.40:
                s = c_frac / 0.40
                return 0.08 + 0.10 * s, 0.33 + 0.20 * s, 0.62 + 0.20 * s
            if c_frac < 0.80:
                s = (c_frac - 0.40) / 0.40
                return 0.18 + 0.35 * s, 0.53 + 0.30 * s, 0.82 + 0.15 * s
            s = (c_frac - 0.80) / 0.20
            return 0.53 + 0.47 * s, 0.83 + 0.17 * s, 0.97 + 0.03 * s
        if t == "phosphor": return 0.10 * (1.0 - c_frac), 0.95, 0.20
        if t == "retro_terminal": return 1.0, 1.0, 1.0
        return 0.10 + 0.75 * (c_frac ** 1.5), 0.50 + 0.45 * c_frac, 1.0

    def _get_neon_prism_color(self, c_frac: float, r_frac: float, is_peak: bool = False, is_cap: bool = False) -> str:
        if is_peak:
            return "\033[38;2;255;255;255m\033[1m" if r_frac > 0.85 else ("\033[38;2;255;235;120m\033[1m" if r_frac > 0.5 else "\033[38;2;0;255;255m\033[1m")
        r, g, b = self._base_theme_rgb(c_frac, 0.10)
        if is_cap and r_frac > 0.70:
            r, g, b = min(1.0, r * 1.25 + 0.15), min(1.0, g * 1.25 + 0.15), min(1.0, b * 1.25 + 0.15)
        else:
            v = 0.62 + 0.38 * r_frac
            r, g, b = r * v, g * v, b * v
        return f"\033[38;2;{int(r*255)};{int(g*255)};{int(b*255)}m"

    def _get_stardust_rgb(self, c_frac: float, r_frac: float, is_peak: bool = False, is_sparkle: bool = False) -> Tuple[int, int, int]:
        if is_sparkle: return (255, 255, 255) if r_frac > 0.75 else (255, 235, 140)
        if is_peak: return (255, 255, 255) if r_frac > 0.85 else ((255, 235, 120) if r_frac > 0.50 else (0, 255, 255))
        r, g, b = (0.10 + 0.75 * (c_frac ** 1.5), 0.50 + 0.45 * c_frac, 1.0) if self.theme == "retro_terminal" else self._base_theme_rgb(c_frac, 0.08)
        v = (0.58 + 0.42 * r_frac) * 255.0
        return int(min(255, max(0, r * v))), int(min(255, max(0, g * v))), int(min(255, max(0, b * v)))

    def _get_stardust_color(self, c_frac: float, r_frac: float, is_peak: bool = False, is_sparkle: bool = False) -> str:
        r, g, b = self._get_stardust_rgb(c_frac, r_frac, is_peak, is_sparkle)
        return f"\033[38;2;{r};{g};{b}m"

    def _resample_bands(self, ll: np.ndarray, lr: np.ndarray, pl: np.ndarray, pr: np.ndarray, n: int):
        if len(ll) <= 1: z = np.zeros(n); return z, z, z, z
        xi, xo = np.linspace(0.0, 1.0, len(ll)), np.linspace(0.0, 1.0, n)
        bl, br = np.interp(xo, xi, ll), np.interp(xo, xi, lr)
        return bl, br, (np.interp(xo, xi, pl) if len(pl) > 1 else bl), (np.interp(xo, xi, pr) if len(pr) > 1 else br)

    def _format_freq_labels(self, ch_w: int) -> str:
        freqs_wide = ["30", "60", "125", "250", "500", "1k", "2k", "4k", "8k", "12k", "16k", "18k"]
        freqs_narrow = ["32", "64", "125", "250", "500", "1k", "4k", "16k"]
        freqs = freqs_wide if ch_w >= 40 else freqs_narrow
        tot = sum(len(f) for f in freqs)
        if ch_w <= tot + len(freqs):
            s = " ".join(freqs)
            return f"{s:<{ch_w}s}"[:ch_w]
        rem = ch_w - tot
        gaps = len(freqs) + 1
        base = rem // gaps
        extra = rem % gaps
        parts = []
        for i, f in enumerate(freqs):
            pad = base + (1 if i < extra else 0)
            parts.append(" " * pad + f)
        rem_end = ch_w - sum(len(p) for p in parts)
        res = "".join(parts) + " " * rem_end
        return f"{res:<{ch_w}s}"[:ch_w]

    def _stereo_footer(self, ch_w_l: Union[int, bool] = 36, ch_w_r: Optional[int] = None) -> List[str]:
        if isinstance(ch_w_l, bool):
            is_wide = ch_w_l
            ch_w_l = 43 if is_wide else 36
            ch_w_r = 43 if is_wide else 36
        elif ch_w_r is None:
            ch_w_r = ch_w_l
        lbl = f"│{self._format_freq_labels(ch_w_l)}│{self._format_freq_labels(ch_w_r)}│"
        bot = f"╰{'─' * ch_w_l}┴{'─' * ch_w_r}╯"
        return [f"  {self.b_col}{lbl}{NC}", f"  {self.b_col}{bot}{NC}"]

    def render_neon_prism_eq(self, levels_l: np.ndarray, levels_r: np.ndarray, peaks_l: np.ndarray, peaks_r: np.ndarray, width: int = 80, height: int = 7, rms: float = 0.0, peak_db: float = -60.0) -> List[str]:
        # The frame is ╭ + left + ┬ + right + ╮, so the three border cells come
        # out of the inner width. Reserving only two drew a row one cell wider
        # than every other equaliser (79 against 78 at width 80) and pushed the
        # right channel past the player border.
        target_iw = max(18, width - 7 if width >= 26 else width - 5)
        # Odd cell counts are handed to the right half so the two channels are
        # as even as the column allows.
        ch_w_l = target_iw // 2
        ch_w_r = target_iw - ch_w_l
        # The band count follows the room, not a fixed 16. Every band is two
        # cells wide, so demanding 16 in a portrait column overflowed each half
        # and pushed the whole right channel off the window. 16 stays the
        # maximum on a wide console; a narrow one shows fewer, evenly split.
        n = max(4, min(16, (ch_w_l - 2) // 2))
        bl, br, pl, pr = self._resample_bands(levels_l, levels_r, peaks_l, peaks_r, n)
        is_beat = rms > 0.13 or peak_db > -5.0
        bolt = "\033[1;38;2;255;255;255m" if is_beat else ("\033[1;38;2;255;215;0m" if rms > 0.07 else COLOR_HOT_PURPLE)
        div = "\033[1;38;2;247;37;133m" if is_beat else self.b_col
        # Each half's heading must fit its own column, or the row grows past
        # the border and the right channel is pushed off the window. The long
        # form is kept on a wide console; a portrait one uses shorter words.
        def _half_label(side: str, cells: int) -> str:
            for text in (f"── {bolt}⚡{NC}{self.b_col} {side} ({n}-BAND NEON PRISM) ",
                         f"── {bolt}⚡{NC}{self.b_col} {side} ({n} BAND) ",
                         f"── {bolt}⚡{NC}{self.b_col} {side} ({n}) ",
                         f"── {bolt}⚡{NC}{self.b_col} {side} "):
                if _display_width(text) <= cells:
                    return text
            return f"── {side} "
        lh = _half_label("LEFT", ch_w_l)
        rh = _half_label("RIGHT", ch_w_r)
        hdr = f"╭{lh}{'─' * max(0, ch_w_l - _display_width(lh))}┬{rh}{'─' * max(0, ch_w_r - _display_width(rh))}╮"
        lines = [f"  {self.b_col}{hdr}{NC}"]
        for r in range(height - 1, -1, -1):
            rf = (r + 1) / float(height)
            def _side(bands, pks, ch_w):
                rem = max(0, ch_w - n * 2)
                l_pad = rem // 2
                r_pad = rem - l_pad
                pts = []
                for c in range(n):
                    frac, cf, pk = bands[c] * height - r, c / float(max(1, n - 1)), pks[c] if c < len(pks) else 0.0
                    if frac >= 1.0: col, ch = self._get_neon_prism_color(cf, rf, is_cap=(frac < 2.0)), "█"
                    elif frac > 0.0: col, ch = self._get_neon_prism_color(cf, rf, is_cap=True), prism_level_glyph(max(1, min(8, int(frac * 8))))
                    elif int(round(pk * height)) == r:
                        col = "\033[38;2;255;255;255m\033[1m" if pk >= 0.90 else self._get_neon_prism_color(cf, rf, is_peak=True)
                        ch = prism_peak_glyph(pk >= 0.90, c)
                    else: ch = " "
                    pts.append(f"{col}{ch}{NC} " if ch != " " else "  ")
                return (" " * l_pad) + "".join(pts) + (" " * r_pad)
            lines.append(f"  {self.b_col}│{NC}{_side(bl, pl, ch_w_l)}{div}│{NC}{_side(br, pr, ch_w_r)}{self.b_col}│{NC}")
        return lines + self._stereo_footer(ch_w_l, ch_w_r)

    def render_stardust_particles_eq(self, levels_l: np.ndarray, levels_r: np.ndarray, peaks_l: np.ndarray, peaks_r: np.ndarray, width: int = 80, height: int = 7, rms: float = 0.0, peak_db: float = -60.0) -> List[str]:
        target_iw = max(18, width - 7 if width >= 26 else width - 5)
        ch_w_l = target_iw // 2
        ch_w_r = target_iw - ch_w_l
        min_w = min(ch_w_l, ch_w_r)
        # Fit the bands to the narrower half instead of demanding 16 of them.
        n = max(4, min(16, (min_w - 2) // 2))
        bl, br, pl, pr = self._resample_bands(levels_l, levels_r, peaks_l, peaks_r, n)
        now, is_beat = time.time(), (rms > 0.13 or peak_db > -5.0)
        scol = "\033[1;38;2;255;255;255m" if is_beat else ("\033[1;38;2;255;215;0m" if rms > 0.07 else COLOR_HOT_PURPLE)
        div = "\033[1;38;2;247;37;133m" if is_beat else self.b_col
        spark = console_symbol("✦")
        m_l = max(0, (ch_w_l - (2 * n - 1)) // 2)
        m_r = max(0, (ch_w_r - (2 * n - 1)) // 2)
        col_map_l = [min(ch_w_l - 1, m_l + b * 2) for b in range(n)]
        col_map_r = [min(ch_w_r - 1, m_r + b * 2) for b in range(n)]
        # Each half's heading must fit its own column. The long form is used
        # when it fits; a portrait column falls back to shorter wording rather
        # than letting the row grow past the border.
        def _stardust_label(side: str, cells: int) -> str:
            for text in (f"── {scol}{spark}{NC}{self.b_col} {side} ({n}-BAND CHROMA STARDUST) ",
                         f"── {scol}{spark}{NC}{self.b_col} {side} ({n} BAND) ",
                         f"── {scol}{spark}{NC}{self.b_col} {side} ({n}) ",
                         f"── {self.b_col} {side} "):
                if _display_width(text) <= cells:
                    return text
            return f"── {side} "
        lh = _stardust_label("LEFT", ch_w_l)
        lh += "─" * max(0, ch_w_l - _display_width(lh))
        rh = _stardust_label("RIGHT", ch_w_r)
        rh += "─" * max(0, ch_w_r - _display_width(rh))
        lines = [f"  {self.b_col}╭{lh}┬{rh}╮{NC}"]

        for ch_idx, (bands, pks, ch_w, col_map) in enumerate(((bl, pl, ch_w_l, col_map_l), (br, pr, ch_w_r, col_map_r))):
            for b in range(n):
                val, pk = float(bands[b]), float(pks[b]) if b < len(pks) else float(bands[b])
                if val > 0.15 and self.dust_rng.random() < val * 0.40:
                    sym = self.dust_rng.choice(stardust_dust_choices("hot" if (val > 0.70 or pk > 0.85) else ("mid" if val > 0.35 else "low")))
                    self.dust_particles.append({
                        "ch": ch_idx, "x": float(col_map[b]) + self.dust_rng.uniform(-0.4, 0.4), "y": float(min(height - 1, val * height)) + self.dust_rng.uniform(0.1, 0.8),
                        "vx": self.dust_rng.uniform(-0.5, 0.5), "vy": self.dust_rng.uniform(0.7, 1.8), "symbol": sym,
                        "color_rgb": self._get_stardust_rgb(b / float(n - 1), min(1.0, val), is_sparkle=(val > 0.75)),
                        "life": self.dust_rng.uniform(1.8, 3.8), "max_life": 3.8, "sway_freq": self.dust_rng.uniform(1.8, 3.6),
                        "sway_amp": self.dust_rng.uniform(0.3, 0.8), "twinkle_phase": self.dust_rng.uniform(0.0, 6.28), "twinkle_speed": self.dust_rng.uniform(3.0, 9.0)
                    })

        if is_beat and len(self.dust_particles) < 70:
            for _ in range(self.dust_rng.randint(2, 4)):
                rb = self.dust_rng.randint(0, n - 1)
                rf, cf = min(1.0, max(0.2, float(bl[rb]))), rb / float(n - 1)
                tch = self.dust_rng.choice([0, 1])
                tcm = col_map_l if tch == 0 else col_map_r
                self.dust_particles.append({
                    "ch": tch, "x": float(tcm[rb]) + self.dust_rng.uniform(-0.6, 0.6),
                    "y": float(min(height - 1, bl[rb] * height + self.dust_rng.uniform(0.5, 1.5))), "vx": self.dust_rng.uniform(-0.8, 0.8), "vy": self.dust_rng.uniform(0.6, 1.5),
                    "symbol": self.dust_rng.choice(stardust_dust_choices("hot")),
                    "color_rgb": (255, 255, 255) if self.dust_rng.random() < 0.4 else self._get_stardust_rgb(cf, rf, is_sparkle=True),
                    "life": self.dust_rng.uniform(2.0, 4.0), "max_life": 4.0, "sway_freq": self.dust_rng.uniform(2.0, 4.0),
                    "sway_amp": self.dust_rng.uniform(0.5, 1.1), "twinkle_phase": self.dust_rng.uniform(0.0, 6.28), "twinkle_speed": self.dust_rng.uniform(5.0, 12.0)
                })

        if len(self.dust_particles) > 75: self.dust_particles = self.dust_particles[-75:]
        dt, self.last_dust_update, alive = max(0.005, min(0.10, now - self.last_dust_update)), now, []
        for p in self.dust_particles:
            p["life"] -= dt
            if p["life"] > 0.0:
                p_cw = ch_w_l if p.get("ch", 0) == 0 else ch_w_r
                p["x"] = max(0.0, min(float(p_cw - 1), p["x"] + (p["vx"] + math.sin(now * p["sway_freq"] + p["twinkle_phase"]) * p["sway_amp"]) * dt))
                p["y"] -= p["vy"] * dt
                if p["y"] >= 0.0: alive.append(p)
        self.dust_particles = alive

        c_grids = []
        for ch_idx, (bands, pks, ch_w, col_map) in enumerate(((bl, pl, ch_w_l, col_map_l), (br, pr, ch_w_r, col_map_r))):
            gc, gcol = [[" "] * ch_w for _ in range(height)], [[""] * ch_w for _ in range(height)]
            for b in range(n):
                col, cf, val = col_map[b], b / float(n - 1), float(bands[b])
                hv, pk_val = val * height, float(pks[b]) if b < len(pks) else val
                cr, rem = int(hv), hv - int(hv)
                pk_r = min(height - 1, max(0, int(round(pk_val * height)))) if pk_val > 0.08 else -1
                for r in range(height):
                    rf = (r + 1) / float(height)
                    if r < cr: gc[r][col], gcol[r][col] = "│", self._get_stardust_color(cf, rf)
                    elif r == cr and rem > 0.05:
                        gc[r][col], gcol[r][col] = ("│" if rem > 0.65 else (console_symbol("╵") if rem > 0.25 else "·")), self._get_stardust_color(cf, rf)
                    elif r == pk_r:
                        gc[r][col] = console_symbol("✦") if pk_val >= 0.85 else (console_symbol("✧") if pk_val >= 0.50 else "·")
                        gcol[r][col] = "\033[1;38;2;255;255;255m" if pk_val >= 0.85 else self._get_stardust_color(cf, rf, is_peak=True)

            for p in self.dust_particles:
                if p["ch"] != ch_idx: continue
                pr_i, pc_i = int(round(p["y"])), int(round(p["x"]))
                if not (0 <= pr_i < height and 0 <= pc_i < ch_w): continue
                lr_rat, tw = max(0.0, min(1.0, p["life"] / p["max_life"])), math.sin(now * p["twinkle_speed"] + p["twinkle_phase"])
                if tw > 0.78 and lr_rat > 0.25:
                    p_col = "\033[1;38;2;255;255;255m"
                    p_sym = (p["symbol"] if p["symbol"] in ("•", "°", "·", "˙", "+") else "•") if _use_limited_console_glyphs() else (p["symbol"] if p["symbol"] in ("✦", "✧", "⋆", "•") else "✦")
                else:
                    rc, gc_v, bc = p["color_rgb"]
                    fade = lr_rat * (0.60 + 0.40 * max(0.0, tw)) * (0.6 if pr_i == 0 else 1.0)
                    p_col = f"\033[38;2;{max(20, min(255, int(rc * fade)))};{max(20, min(255, int(gc_v * fade)))};{max(20, min(255, int(bc * fade)))}m"
                    p_sym = console_symbol(p["symbol"])
                    if lr_rat < 0.20 and p_sym in ("✦", "✧", "•", "°"): p_sym = "·"
                if gc[pr_i][pc_i] == " " or (p_sym in ("✦", "✧", "⋆") and tw > 0.78): gc[pr_i][pc_i], gcol[pr_i][pc_i] = p_sym, p_col
                else:
                    for off in (1, -1):
                        if 0 <= pc_i + off < ch_w and gc[pr_i][pc_i + off] == " ": gc[pr_i][pc_i + off], gcol[pr_i][pc_i + off] = p_sym, p_col; break
            c_grids.append((gc, gcol))

        (gl_c, gl_col), (gr_c, gr_col) = c_grids
        for r in range(height - 1, -1, -1):
            ls = "".join(f"{gl_col[r][c]}{gl_c[r][c]}{NC}" if gl_c[r][c] != " " else " " for c in range(ch_w_l))
            rs = "".join(f"{gr_col[r][c]}{gr_c[r][c]}{NC}" if gr_c[r][c] != " " else " " for c in range(ch_w_r))
            lines.append(f"  {self.b_col}│{NC}{ls}{div}│{NC}{rs}{self.b_col}│{NC}")
        return lines + self._stereo_footer(ch_w_l, ch_w_r)

    def _get_bar_color(self, fraction: float) -> str:
        t = self.theme
        if t in ("stream_of_frequency", "stream", "frequency"):
            return "\033[38;2;0;242;255m" if fraction < 0.30 else ("\033[38;2;76;217;123m" if fraction < 0.60 else ("\033[38;2;245;237;106m" if fraction < 0.85 else "\033[38;2;240;32;24m"))
        if t == "cosmic_supernova":
            return "\033[38;2;0;245;212m" if fraction < 0.35 else ("\033[38;2;76;201;240m" if fraction < 0.65 else ("\033[38;2;224;86;253m" if fraction < 0.85 else "\033[38;2;255;51;102m"))
        if t in ("dreamworlds_liquid", "liquid_transparency", "mp_dreamworlds_liquid_transparency"):
            return "\033[38;2;0;245;255m" if fraction < 0.45 else ("\033[38;2;0;245;212m" if fraction < 0.75 else "\033[38;2;199;125;255m")
        if t in ("dynamic_rainbow_liquid_glass", "dynamic_liquid_glass_rainbow", "dynamic_rainbow", "rainbow"):
            return "\033[38;2;0;242;254m" if fraction < 0.40 else ("\033[38;2;255;215;0m" if fraction < 0.70 else ("\033[38;2;255;45;142m" if fraction < 0.90 else "\033[38;2;176;88;255m"))
        if t in ("dreamworlds_ultra", "dreamworlds"): return COLOR_TRAFFIC_GREEN if fraction < 0.60 else (COLOR_TRAFFIC_AMBER if fraction < 0.85 else COLOR_TRAFFIC_RED)
        if t == "dreamworlds_neon": return "\033[38;2;0;229;255m" if fraction < 0.4 else ("\033[38;2;247;37;133m" if fraction < 0.75 else "\033[38;2;255;140;0m")
        if t == "retro_studio": return "\033[38;2;46;204;113m" if fraction < 0.6 else ("\033[38;2;241;196;15m" if fraction < 0.85 else "\033[38;2;231;76;60m")
        if t == "cyber_neon": return "\033[38;2;114;9;183m" if fraction < 0.5 else "\033[38;2;76;201;240m"
        if t in ("retro_linux_memories", "retro_linux", "linux_memories", "veracrypt"):
            return "\033[38;2;21;83;158m" if fraction < 0.50 else ("\033[38;2;91;134;187m" if fraction < 0.80 else ("\033[38;2;240;173;78m" if fraction < 0.95 else "\033[38;2;255;255;255m"))
        if t == "phosphor": return "\033[38;2;57;255;20m"
        return "\033[38;2;30;144;255m" if fraction < 0.5 else "\033[38;2;224;255;255m"

    def render_stereo_eq(self, levels_l: np.ndarray, levels_r: np.ndarray, peaks: np.ndarray, width: int = 80, height: int = 7) -> List[str]:
        target_iw = max(20, width - 6 if width >= 26 else width - 4)
        ch_w_l = (target_iw - 1) // 2
        ch_w_r = target_iw - 1 - ch_w_l
        min_w = min(ch_w_l, ch_w_r)
        if min_w >= 50: n = 16
        elif min_w >= 38: n = 12
        else: n = 8
        bl, br, pl, pr = self._resample_bands(levels_l, levels_r, peaks, peaks, n)

        def _calc_widths(ch_w: int) -> List[int]:
            base_bw = ch_w // n
            rem = ch_w % n
            return [base_bw + (1 if i < rem else 0) for i in range(n)]

        widths_l = _calc_widths(ch_w_l)
        widths_r = _calc_widths(ch_w_r)

        lt = f"LEFT CHANNEL ({n}-BAND)"
        rt = f"RIGHT CHANNEL ({n}-BAND)"
        lh = self._rule_label(lt, ch_w_l) if (ch_w_l >= 18) else self._rule_label(f"L ({n})", ch_w_l)
        lh += "─" * max(0, ch_w_l - _display_width(lh))
        rh = self._rule_label(rt, ch_w_r) if (ch_w_r >= 18) else self._rule_label(f"R ({n})", ch_w_r)
        rh += "─" * max(0, ch_w_r - _display_width(rh))
        hdr = f"╭{lh}┬{rh}╮"
        lines = [f"  {self.b_col}{hdr}{NC}"]

        def _render_channel_row(bands, pks, widths, r):
            pts = []
            for c in range(n):
                w_c = widths[c]
                pk = pks[c % len(pks)] if len(pks) > 0 else 0.0
                ch = meter_level_char(bands[c] * height - r, int(pk * height) == r)
                if w_c <= 2:
                    pts.append(f"{ch} " if w_c == 2 else ch)
                elif w_c == 3:
                    pts.append(f" {ch} ")
                else:
                    bar_w = 2
                    pad_tot = w_c - bar_w
                    pl = pad_tot // 2
                    pr = pad_tot - pl
                    pts.append((" " * pl) + (ch * bar_w) + (" " * pr))
            return "".join(pts)

        for r in range(height - 1, -1, -1):
            col = self._get_bar_color(r / float(height))
            ls = _render_channel_row(bl, pl, widths_l, r)
            rs = _render_channel_row(br, pr, widths_r, r)
            lines.append(f"  {self.b_col}│{NC}{col}{ls}{NC}{self.b_col}│{NC}{col}{rs}{NC}{self.b_col}│{NC}")
        return lines + self._stereo_footer(ch_w_l, ch_w_r)

    def _rule_label(self, label: str, inner: int, prefix: str = "── ") -> str:
        """A rule label that always fits ``inner`` columns.

        The deck is 40 columns wide. A fixed header such as the oscilloscope's
        name is wider than that inner width even at the smallest console, and
        the dashes after it were computed as ``max(0, ...)``, so the row simply
        grew past the box and pushed its right corner off the window. Clip the
        words instead, and keep the corner.
        """
        inner = max(4, int(inner))
        text = label if label.startswith(prefix) else f"{prefix}{label}"
        return text if _display_width(text) <= inner else _clip_display(text, inner)

    def render_master_spectrum(self, levels: np.ndarray, peaks: np.ndarray, width: int = 80, height: int = 7) -> List[str]:
        target_iw = max(20, width - 6 if width >= 26 else width - 4)
        # The bar floor must fit the box: 16 bars are 48 cells, which a
        # 40-column window cannot hold. Take the larger of 8 and the room.
        num_bars = max(4, min(48, (target_iw - 2) // 3))
        hdr = self._rule_label("HIGH-RESOLUTION MASTER FREQUENCY SPECTRUM (30Hz - 18kHz) ", target_iw)
        top_line = f"  {self.b_col}╭{hdr}{'─' * max(0, target_iw - _display_width(hdr))}╮{NC}"
        bot_line = f"  {self.b_col}╰{'─' * target_iw}╯{NC}"
        lines = [top_line]
        if len(levels) > 1:
            xo, xn = np.linspace(0, 1, len(levels)), np.linspace(0, 1, num_bars)
            il = np.interp(xn, xo, levels)
            ip = np.interp(xn, xo, peaks) if len(peaks) > 1 else np.zeros(num_bars)
        else:
            il = ip = np.zeros(num_bars)
        avail = target_iw - 2
        bar_span = num_bars * 3
        pad_tot = max(0, avail - bar_span)
        pad_l = 1 + pad_tot // 2
        pad_r = target_iw - pad_l - bar_span

        for r in range(height - 1, -1, -1):
            col = self._get_bar_color(r / float(height))
            rc = [f"{meter_level_char(val * height - r, int(ip[c] * height) == r)*2} " for c, val in enumerate(il)]
            lines.append(f"  {self.b_col}│{NC}{' ' * pad_l}{col}{''.join(rc)}{NC}{' ' * pad_r}{self.b_col}│{NC}")

        peak_str = f"PEAK HOLD {console_symbol('✦')}"
        rem_w = max(10, target_iw - _display_width(peak_str) - 4)
        fl = self._format_freq_labels(rem_w)
        ftr_mid = f"  {fl}  {peak_str}"
        if _display_width(ftr_mid) > target_iw:
            ftr_mid = _clip_display(ftr_mid, target_iw)
        ftr_pad = max(0, target_iw - _display_width(ftr_mid))
        lines.append(f"  {self.b_col}│{NC}{DIM}{CYAN}{ftr_mid}{' ' * ftr_pad}{NC}{self.b_col}│{NC}")
        lines.append(bot_line)
        return lines

    def render_analog_vu(self, peak_db: float, levels_l: np.ndarray, levels_r: np.ndarray, width: int = 80) -> List[str]:
        target_iw = max(20, width - 6 if width >= 26 else width - 4)
        hdr = "── DUAL ANALOG STUDIO VU METERS (PEAK & RMS BALLISTICS) "
        # A narrow window cannot hold the long title. Clip it to the inner
        # width instead of drawing a row wider than the box, which pushed the
        # right border off the player at width 60.
        if _display_width(hdr) > target_iw:
            hdr = _clip_display(hdr, target_iw)
        top_line = f"  {self.b_col}╭{hdr}{'─' * max(0, target_iw - _display_width(hdr))}╮{NC}"
        bot_line = f"  {self.b_col}╰{'─' * target_iw}╯{NC}"
        lines = [top_line]

        # Keep the band length inside the box so the meter row and its
        # [CLIP]/[SAFE] tag cannot push the right border off a narrow window.
        blen = max(4, min(target_iw - 28, target_iw - 24))
        scale_raw = "-40   -30   -20   -10   -7   -5   -3   -1    0  +1  +2  +3 dB"
        scale_pfx = "  "
        scale_str = scale_pfx + scale_raw
        if _display_width(scale_str) > target_iw:
            scale_str = _clip_display(scale_str, target_iw)
        scale_pad = max(0, target_iw - _display_width(scale_str))
        lines.append(f"  {self.b_col}│{NC}{scale_str}{' ' * scale_pad}{self.b_col}│{NC}")

        for name, val in (("LEFT  CHANNEL", np.mean(levels_l) if len(levels_l) else 0.0), ("RIGHT CHANNEL", np.mean(levels_r) if len(levels_r) else 0.0)):
            filled = int(val * blen)
            meter = "".join(f"{self._get_bar_color(i / blen)}█{NC}" if i < filled else f"{DIM}░{NC}" for i in range(blen))
            clip_s = f"{BOLD}{RED}[CLIP]{NC}" if filled >= blen - 1 else f"{DIM}[SAFE]{NC}"
            row_content = f"  {BOLD}{CYAN}{name}{NC} [{meter}] {clip_s}"
            if _display_width(row_content) > target_iw:
                # A narrow box cannot show the channel name beside the meter.
                # Shrink the meter rather than letting the row run past the
                # border; the clip tag carries the reading.
                short_name = "L" if name.startswith("L") else "R"
                row_content = f" {BOLD}{CYAN}{short_name}{NC}[{meter}]"
                if _display_width(row_content) > target_iw:
                    room = max(2, target_iw - 12)
                    meter = "".join(f"{self._get_bar_color(i / max(1, room))}█{NC}" if i < min(filled, room) else f"{DIM}░{NC}" for i in range(room))
                    row_content = f" {BOLD}{CYAN}{short_name}{NC}[{meter}]"
            row_pad = max(0, target_iw - _display_width(row_content))
            lines.append(f"  {self.b_col}│{NC}{row_content}{' ' * row_pad}{self.b_col}│{NC}")

        info_str = f"  Peak Audio Level: {BOLD}{YELLOW}{peak_db:+6.1f} dBFS{NC}  │  Master Headroom: {BOLD}{GREEN}{max(0.0, -peak_db):5.1f} dB{NC}"
        if _display_width(info_str) > target_iw:
            info_str = _clip_display(info_str, target_iw)
        info_pad = max(0, target_iw - _display_width(info_str))
        lines.append(f"  {self.b_col}│{NC}{info_str}{' ' * info_pad}{self.b_col}│{NC}")
        lines.append(bot_line)
        return lines

    def render_oscilloscope(self, levels: np.ndarray, status: Optional[Dict[str, Any]] = None, width: int = 80, height: int = 5) -> List[str]:
        st = status or {}
        target_iw = max(20, width - 6 if width >= 26 else width - 4)
        iw = max(16, target_iw - 4)
        hdr = self._rule_label("DYNAMIC OSCILLOSCOPE AUDIO WAVEFORM ", target_iw)
        top_l = f"  {self.b_col}╭{hdr}{'─' * max(0, target_iw - _display_width(hdr))}╮{NC}"
        bot_l = f"  {self.b_col}╰{'─' * target_iw}╯{NC}"

        hh, state, rms, pm = max(1, height // 2), st.get("state", "stopped"), float(st.get("rms_level", 0.0)), max(0.5, min(2.0, 1.0 + int(st.get("pitch_percent", 0)) / 100.0))
        now, wf_arr, xd = time.time(), _as_float_vector(st.get("waveform"), 1), np.linspace(0.0, 1.0, iw)
        if state == "playing":
            b_v = float(np.mean(levels[:4])) if len(levels) >= 4 else max(0.15, rms)
            m_v = float(np.mean(levels[4:14])) if len(levels) >= 14 else rms * 0.7
            t_v = float(np.mean(levels[14:])) if len(levels) >= 15 else rms * 0.4
            da = max(0.25, min(1.0, rms * 2.2 + (float(np.max(levels)) * 0.8 if len(levels) else 0.0)))
            sp = np.clip((np.sin(xd * (2.0 * math.pi) - now * 3.5 * pm) * 0.60 * max(0.2, b_v) + np.sin(xd * (4.0 * math.pi) + now * 6.0 * pm) * 0.28 * max(0.1, m_v) + np.sin(xd * (6.0 * math.pi) - now * 10.0 * pm) * 0.14 * max(0.05, t_v)) * da * 1.5, -0.98, 0.98)
            if len(wf_arr) >= 16 and float(np.max(np.abs(wf_arr))) > 0.01:
                pi = np.interp(xd, np.linspace(0.0, 1.0, len(wf_arr)), wf_arr)
                pts = np.clip(0.65 * ((pi / max(0.15, float(np.max(np.abs(pi))))) * min(1.0, max(0.25, rms * 2.0))) + 0.35 * sp, -0.98, 0.98)
            else: pts = sp
        elif state == "paused": pts = np.sin(xd * (2.0 * math.pi) + now * 1.2) * 0.18
        else: pts = np.zeros(iw)
        y_f = [float(p) * hh for p in pts]
        y_i = [max(-hh, min(hh, int(round(yf)))) for yf in y_f]
        lines, cx = [top_l], iw // 2
        pad_l = (target_iw - iw) // 2
        pad_r = target_iw - iw - pad_l
        for r in range(hh, -hh - 1, -1):
            rc = []
            for i in range(iw):
                py, cy = y_i[i - 1] if i > 0 else y_i[i], y_i[i]
                rmn, rmx = min(py, cy), max(py, cy)
                if rmn <= r <= rmx:
                    col = self._get_neon_prism_color(i / float(max(1, iw - 1)), min(1.0, abs(y_f[i]) / float(hh) * 0.85 + rms * 0.35), is_peak=(abs(cy) == hh and abs(y_f[i]) >= hh * 0.75))
                    ch = "━" if rmn == rmx else (("╱" if r in (rmn, rmx) else "│") if cy > py else ("╲" if r in (rmn, rmx) else "│"))
                    rc.append(f"{col}{BOLD}{ch}{NC}")
                else:
                    tk = (i - cx) % 10 == 0
                    rc.append(f"{DIM}{'┼' if tk else '·'}{NC}" if r == 0 else (f"{DIM}·{NC}" if tk else " "))
            lines.append(f"  {self.b_col}│{NC}{' ' * pad_l}{''.join(rc)}{' ' * pad_r}{self.b_col}│{NC}")
        return lines + [bot_l]

    def render_stream_of_frequency(self, status: Optional[Dict[str, Any]] = None, width: int = 80, height: int = 7) -> List[str]:
        st = status or {}
        inner_w = max(20, width - 6 if width >= 26 else width - 4)
        cw = max(16, inner_w - 2)
        hdr_text = "── 🌊 STREAM OF FREQUENCY (DYNAMIC SPECTROGRAM) " if inner_w >= 50 else ("── 🌊 STREAM OF FREQUENCY " if inner_w >= 32 else "── 🌊 STREAM ")
        if inner_w >= 70:
            ftr_text = "── FLOW (RIGHT TO LEFT) ──── 32Hz  125Hz  500Hz  2kHz  8kHz  16kHz ────"
        elif inner_w >= 36:
            ftr_text = "── FLOW (RIGHT TO LEFT) ────"
        else:
            ftr_text = "── FLOW "

        top_l = f"  {self.b_col}╭{hdr_text}{'─' * max(0, inner_w - _display_width(hdr_text))}╮{NC}"
        bot_l = f"  {self.b_col}╰{ftr_text}{'─' * max(0, inner_w - _display_width(ftr_text))}╯{NC}"

        H = max(1, height)
        self._sof_history_cols = cw
        buf_cols = max(140, cw + 40)
        if getattr(self, "_sof_buffer", None) is None or self._sof_buffer.shape[1] < buf_cols:
            self._sof_buffer = np.zeros((16, buf_cols), dtype=float)
        buf, a = self._sof_push_history(status)
        buf = self._sof_buffer_at_least(buf, buf_cols)
        self._sof_buffer = buf
        now, dt = a["now"], a["dt"]
        state = a["state"]
        rms = a["rms"]
        peak_db = a["peak_db"]
        speed_mult = a["speed"]
        slice_col = a["slice"]

        if getattr(self, "_sof_particles", None) is None:
            self._sof_particles = []

        if state == "playing":
            for r in range(H):
                buf_r = min(15, int(r * (16.0 / max(1, H))))
                val = float(slice_col[buf_r])
                if val > 0.05:
                    prob = min(0.85, val * 0.65 + (0.25 if (rms > 0.12 or peak_db > -4.5) else 0.0))
                    if self._sof_rng.random() < prob:
                        if r >= max(1, H - 2):
                            sym = self._sof_rng.choice(["✦", "✧", "⋆", "•", "°", "·"])
                        elif r >= max(1, H // 2):
                            sym = self._sof_rng.choice(["≈", "≋", "~", "∿", "∽", "·"])
                        else:
                            sym = self._sof_rng.choice(["≋", "≈", "█", "▓", "▒", "░", "◆"])
                        self._sof_particles.append({
                            "x": float(cw - 1) + self._sof_rng.uniform(-0.2, 0.2),
                            "base_y": float(r),
                            "y": float(r),
                            "vx": self._sof_rng.uniform(-20.0, -36.0) * speed_mult,
                            "wave_amp": self._sof_rng.uniform(0.15, 0.55),
                            "wave_freq": self._sof_rng.uniform(3.0, 7.5),
                            "wave_phase": self._sof_rng.uniform(0.0, 6.283),
                            "life": self._sof_rng.uniform(2.5, 4.5),
                            "max_life": 4.5,
                            "symbol": sym,
                            "hue_offset": self._sof_rng.uniform(0.0, 1.0),
                            "is_sparkle": (val > 0.70 or rms > 0.14)
                        })

            if (rms > 0.13 or peak_db > -4.5) and len(self._sof_particles) < 70:
                for _ in range(self._sof_rng.randint(2, 4)):
                    br = self._sof_rng.randint(0, H - 1)
                    self._sof_particles.append({
                        "x": float(cw - 1) + self._sof_rng.uniform(-0.5, 0.5),
                        "base_y": float(br),
                        "y": float(br),
                        "vx": self._sof_rng.uniform(-26.0, -42.0) * speed_mult,
                        "wave_amp": self._sof_rng.uniform(0.25, 0.70),
                        "wave_freq": self._sof_rng.uniform(4.0, 9.0),
                        "wave_phase": self._sof_rng.uniform(0.0, 6.283),
                        "life": self._sof_rng.uniform(2.8, 4.8),
                        "max_life": 4.8,
                        "symbol": "✦" if br >= H // 2 else "≋",
                        "hue_offset": self._sof_rng.uniform(0.0, 1.0),
                        "is_sparkle": True
                    })

        if len(self._sof_particles) > 75:
            self._sof_particles = self._sof_particles[-75:]

        alive = []
        for p in self._sof_particles:
            p["life"] -= dt
            if p["life"] > 0:
                p["x"] += p["vx"] * dt
                if p["x"] >= -0.8:
                    w_disp = math.sin(now * p["wave_freq"] + p["wave_phase"] - 0.22 * p["x"]) * p["wave_amp"]
                    p["y"] = max(0.0, min(float(H - 1), p["base_y"] + w_disp))
                    alive.append(p)
        self._sof_particles = alive

        # 2D Grid buffers
        grid_c = [[" "] * cw for _ in range(H)]
        grid_col = [[""] * cw for _ in range(H)]

        # Fill the spectrogram in one vectorised pass. Every cell's glyph and
        # colour is a pure function of its energy and its (row, column), so the
        # whole grid can be classified at once instead of in a Python loop that
        # ran once per cell on every frame. That loop was the single most
        # expensive thing in this effect, and this is the default effect.
        buf_offset = self._sof_buffer.shape[1] - cw
        if cw > 0 and H > 0:
            row_sel = np.array([min(15, int(r * (16.0 / max(1, H)))) for r in range(H)])
            energy = self._sof_buffer[row_sel, buf_offset:buf_offset + cw]

            # Hue drifts with time, and across the row and down the columns.
            cols = np.arange(cw, dtype=float)
            rows = np.arange(H, dtype=float)
            phase = ((now * 0.40 + (cw - 1 - cols) * 0.038)[None, :]
                     + (rows * 0.08)[:, None])
            h_idx = (np.mod(phase, 1.0) * 360).astype(np.int64) % 360

            half = max(1, H // 2)
            lower = np.broadcast_to((np.arange(H) >= half)[:, None], (H, cw))

            # Tier 0 is blank; each higher tier is a louder band of the scale.
            tier = np.zeros((H, cw), dtype=np.int8)
            tier[energy > 0.05] = 1
            tier[energy > 0.10] = 2
            tier[energy > 0.20] = 3
            tier[energy > 0.45] = 4
            tier[energy > 0.75] = 5

            glyphs = np.full((H, cw), " ", dtype="<U1")
            for level, (upper, lower_glyph) in (
                    (1, ("·", "·")), (2, ("∿", "∽")),
                    (3, ("≈", "~")), (4, ("≋", "≈"))):
                mask = tier == level
                if mask.any():
                    glyphs[mask] = np.where(lower[mask], lower_glyph, upper)
            loud = tier == 5
            if loud.any():
                glyphs[loud] = np.where((energy > 0.90)[loud], "█", "▓")
            grid_c = glyphs.tolist()

            # One palette lookup per tier, gathered through the hue indices.
            colours = np.full((H, cw), "", dtype=object)
            for level, table in ((1, _RAINBOW_ANSI_FAINT), (2, _RAINBOW_ANSI_DIM),
                                 (3, _RAINBOW_ANSI_NORMAL), (4, _RAINBOW_ANSI_BOLD),
                                 (5, _RAINBOW_ANSI_BOLD)):
                mask = tier == level
                if mask.any():
                    colours[mask] = np.asarray(table, dtype=object)[h_idx[mask]]
            grid_col = colours.tolist()

        # Overlay particles
        for p in self._sof_particles:
            pr = max(0, min(H - 1, int(round(p["y"]))))
            pc = int(round(p["x"]))
            if 0 <= pc < cw:
                p_hue = (now * 0.45 + (cw - 1 - pc) * 0.038 + p["hue_offset"]) % 1.0
                p_h_idx = int(p_hue * 360) % 360
                if p["is_sparkle"]:
                    p_col = "\033[1;38;2;255;255;255m" if self._sof_rng.random() < 0.35 else _RAINBOW_ANSI_BOLD[p_h_idx]
                else:
                    p_col = _RAINBOW_ANSI_BOLD[p_h_idx]
                grid_c[pr][pc] = console_symbol(p["symbol"])
                grid_col[pr][pc] = p_col

        # Build output lines (from top row H-1 down to bottom row 0).
        #
        # Colour and bold are tracked per cell but only emitted when they
        # actually change, so one escape describes a whole run of cells that
        # share a hue instead of being re-sent for every glyph. A narrow
        # terminal emits almost the same bytes as before, but a maximised
        # window used to send an escape per lit cell -- the single largest
        # piece of terminal traffic in the player -- and now sends one per
        # colour change.
        lines = [top_l]
        pad_tot = inner_w - cw
        pad_l = pad_tot // 2
        pad_r = inner_w - cw - pad_l
        bold_off = "\033[22m"
        for r in range(H - 1, -1, -1):
            row_parts = []
            bold_active = False
            color_active = None
            for c in range(cw):
                glyph = grid_c[r][c]
                if glyph == " ":
                    # A space has no ink, so the run can carry across it.
                    row_parts.append(" ")
                    continue
                color = grid_col[r][c]
                color_bold = color.startswith("\033[1;")
                if color_bold != bold_active:
                    row_parts.append(BOLD if color_bold else bold_off)
                    bold_active = color_bold
                if color_bold:
                    color = color.replace("\033[1;", "\033[", 1)
                if color != color_active:
                    row_parts.append(color)
                    color_active = color
                row_parts.append(glyph)
            row_str = "".join(row_parts)
            lines.append(f"  {self.b_col}│{NC}{' ' * pad_l}{row_str}{NC}{' ' * pad_r}{self.b_col}│{NC}")
        lines.append(bot_l)
        return lines

    # ── Stream of Frequency: shared spectrogram history ──────────────────
    def _sof_push_history(self, status: Optional[Dict[str, Any]], rows: int = 16) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
        """Advance the shared spectrogram history and return it.

        The Dynamic Spectrogram, Liquid Spectrogram and MPlanetian Overview
        Effect all draw the same rolling slice. Keeping the buffer in one place
        means the three effects stay perfectly in step: switching with E never
        restarts the history or shows a different moment of the track.
        """
        st = status or {}
        now = time.time()
        dt = max(0.005, min(0.08, now - getattr(self, "_sof_last_time", now)))
        self._sof_last_time = now

        state = st.get("state", "stopped")
        rms = float(st.get("rms_level", 0.0) or 0.0)
        peak_db = float(st.get("peak_db", -60.0) or -60.0)
        pitch_pct = int(st.get("pitch_percent", 0) or 0)
        speed_mult = max(0.5, min(2.0, 1.0 + pitch_pct / 100.0))

        lv = _as_float_vector(st.get("levels"), 24)
        ll = _as_float_vector(st.get("levels_left", lv), 24)
        lr = _as_float_vector(st.get("levels_right", lv), 24)
        mono = 0.5 * (ll + lr) if len(ll) == len(lr) and len(ll) > 0 else lv
        if len(mono) < rows:
            mono = np.pad(mono, (0, max(0, rows - len(mono))))

        if state == "playing":
            interp_x = np.linspace(0.0, 1.0, rows)
            levels_x = np.linspace(0.0, 1.0, len(mono))
            slice_col = np.clip(np.interp(interp_x, levels_x, mono), 0.0, 1.0)
            if rms > 0.12 or peak_db > -4.5:
                slice_col = np.clip(slice_col * 1.25 + 0.15, 0.0, 1.0)
        elif state == "paused":
            slice_col = np.array(
                [0.07 + 0.04 * math.sin(now * 2.2 + r * 0.4) for r in range(rows)],
                dtype=float)
        else:
            slice_col = np.zeros(rows, dtype=float)

        width = int(getattr(self, "_sof_history_cols", 0) or 0)
        info = {"state": state, "playing": state == "playing",
                "paused": state == "paused", "rms": rms, "peak_db": peak_db,
                "speed": speed_mult, "now": now, "dt": dt,
                "slice": slice_col, "rows": rows}
        if getattr(self, "_sof_buffer", None) is None or width <= 0:
            return None, info

        shift_cols = max(1, min(3, int(round(dt * 24.0 * speed_mult))))
        for _ in range(shift_cols):
            self._sof_buffer[:, :-1] = self._sof_buffer[:, 1:]
            self._sof_buffer[:, -1] = slice_col
        return self._sof_buffer, info

    def _sof_buffer_at_least(self, buf: Optional[np.ndarray], cols: int,
                             rows: int = 16) -> np.ndarray:
        if buf is None or buf.shape[0] != rows or buf.shape[1] < cols:
            buf = np.zeros((rows, max(cols, 1)), dtype=float)
        return buf

    # ── Stream of Frequency: Liquid Spectrogram ──────────────────────────
    def render_stream_of_frequency_liquid(self, status: Optional[Dict[str, Any]] = None,
                                          width: int = 80, height: int = 7) -> List[str]:
        """A still pool of liquid light rather than a river in motion.

        Where the Dynamic Spectrogram streams right to left, this one holds the
        whole recent history on screen and reads it as depth: the newest audio
        is the bright surface along the right edge, and older audio sinks into
        the dark below. Half-block cells give the surface a smooth meniscus, and
        caustic glints drift slowly rightward so the pool still breathes.
        """
        inner_w = max(20, width - 6 if width >= 26 else width - 4)
        cw = max(16, inner_w)
        H = max(1, height)
        self._sof_history_cols = cw
        buf, a = self._sof_push_history(status)
        buf = self._sof_buffer_at_least(buf, cw)
        self._sof_buffer = buf
        ramp = _sof_ramp_ansi(_SOF_LIQUID_RAMP)
        now, dt = a["now"], a["dt"]
        playing, state = a["state"] == "playing", a["state"]

        hdr = ("── 💧 STREAM OF FREQUENCY (LIQUID SPECTROGRAM) "
               if inner_w >= 52 else ("── 💧 LIQUID SPECTROGRAM " if inner_w >= 30 else "── 💧 "))
        ftr = ("── SURFACE ► RIGHT   DEPTH ▼ DOWN   CAUSTICS ────"
               if inner_w >= 50 else "── LIQUID POOL ")

        # Caustic glints drift to the right and are reborn on the left.
        glints = getattr(self, "_sof_liquid_glints", None)
        if glints is None:
            glints = self._sof_liquid_glints = []
        for g in glints:
            g["x"] += g["vx"] * dt * a["speed"]
            g["life"] -= dt
            if g["x"] > cw:
                g["x"] -= cw
        if playing:
            loud = float(np.max(a["slice"])) if a["slice"].size else 0.0
            while len(glints) < 3 + int(loud * 8):
                glints.append({
                    "x": float(self._sof_rng.uniform(-2.0, cw)),
                    "y": float(self._sof_rng.uniform(0.0, max(0.0, H - 1))),
                    "vx": float(self._sof_rng.uniform(2.4, 6.0)),
                    "life": float(self._sof_rng.uniform(1.6, 3.6)),
                })
        self._sof_liquid_glints = glints = [g for g in glints if g["life"] > 0.0][-18:]

        grid_c = [[" "] * cw for _ in range(H)]
        grid_col = [[""] * cw for _ in range(H)]
        buf_offset = buf.shape[1] - cw

        # Row 0 is the surface (newest audio); deeper rows hold older audio.
        for r in range(H):
            buf_r = min(15, int(r * (16.0 / max(1, H))))
            depth = 1.0 - (r / float(max(1, H)))          # 1.0 at surface
            row_energy = 0.0
            for c in range(cw):
                energy = float(buf[buf_r, buf_offset + c])
                if a["paused"]:
                    energy = 0.10 + 0.05 * math.sin(now * 1.8 + c * 0.22 + r * 0.7)
                if energy <= 0.04:
                    continue
                row_energy = max(row_energy, energy)
                # Viscous lift: each row reads a touch brighter at the surface.
                val = min(1.0, energy * (0.62 + 0.55 * depth))
                level = max(1, min(12, int(round(val * 12))))
                grid_col[r][c] = ramp[level]
                if val > 0.82:
                    grid_c[r][c] = "█"
                elif val > 0.58:
                    grid_c[r][c] = "▓"
                elif val > 0.34:
                    grid_c[r][c] = "▒"
                elif val > 0.16:
                    grid_c[r][c] = "░"
                else:
                    grid_c[r][c] = "·"

            # Meniscus: a bright unbroken line where the newest audio lands.
            if row_energy > 0.06:
                for c in range(cw):
                    if grid_c[r][c] != " " and (c == 0 or grid_c[r][c - 1] == " "):
                        grid_c[r][c] = "▁" if r == 0 else grid_c[r][c]

        # Surface highlight along the right edge: the newest slice of audio.
        if state == "playing" and a["slice"].size:
            for r in range(H):
                buf_r = min(15, int(r * (16.0 / max(1, H))))
                v = float(a["slice"][buf_r])
                if v > 0.18:
                    grid_c[r][cw - 1] = "█" if v > 0.7 else ("▓" if v > 0.4 else "▒")
                    grid_col[r][cw - 1] = ramp[min(12, 6 + int(v * 7))]

        # Caustic glints: soft rings floating on the surface.
        for g in glints:
            c = int(round(g["x"]))
            r = int(round(g["y"]))
            fade = max(0.0, min(1.0, g["life"] / 3.6))
            if 0 <= c < cw and 0 <= r < H:
                grid_c[r][c] = "○" if fade > 0.55 else "·"
                grid_col[r][c] = ramp[min(12, 7 + int(fade * 5))]

        lines = self._fx_lines(grid_c, grid_col, inner_w, hdr, ftr)
        return lines

    # ── Stream of Frequency: MPlanterian Overview Effect ─────────────────
    def render_stream_of_frequency_overview(self, status: Optional[Dict[str, Any]] = None,
                                            width: int = 80, height: int = 7) -> List[str]:
        """The spectrogram read as a topographic survey from above.

        Instead of a river flowing sideways, the same history is treated as
        elevation and drawn as a contour map: symmetric ridges, fixed contour
        bands that stay put as the terrain moves, a warm land-to-snow palette,
        and one slow satellite sweep crossing the field. Loud passages rise
        into bright peaks; silence is open flatland.
        """
        inner_w = max(20, width - 6 if width >= 26 else width - 4)
        cw = max(16, inner_w)
        H = max(1, height)
        self._sof_history_cols = cw
        buf, a = self._sof_push_history(status)
        buf = self._sof_buffer_at_least(buf, cw)
        self._sof_buffer = buf
        ramp = _sof_ramp_ansi(_SOF_OVERVIEW_RAMP)
        now = a["now"]
        playing, paused = a["state"] == "playing", a["state"] == "paused"

        hdr = ("── 🗺 STREAM OF FREQUENCY (MPLANTERIAN OVERVIEW EFFECT) "
               if inner_w >= 56 else ("── 🗺 MPLANTERIAN OVERVIEW " if inner_w >= 32 else "── 🗺 "))
        ftr = ("── CONTOURS • RIDGE ► ELEVATION • SATELLITE SWEEP ────"
               if inner_w >= 56 else "── TOPOGRAPHIC SURVEY ")

        grid_c = [[" "] * cw for _ in range(H)]
        grid_col = [[""] * cw for _ in range(H)]
        buf_offset = buf.shape[1] - cw

        # Read the recent history once, then normalise it to its own range. The
        # spectrum usually sits between roughly 0.15 and 1.0, so using it raw
        # floods the map; rescaling is what makes ridges and valleys emerge.
        rows_idx = [min(15, int(r * (16.0 / max(1, H)))) for r in range(H)]
        raw = buf[np.asarray(rows_idx), buf_offset:buf_offset + cw].astype(float)
        if paused:
            c_axis = np.arange(cw, dtype=float)
            r_axis = np.arange(H, dtype=float)
            raw = (0.30 + 0.22 * np.sin(now * 1.6 + c_axis * 0.19
                                        + r_axis[:, None] * 0.55))
        if raw.size:
            # Robust range: ignore the extreme tails so one loud frame does not
            # flatten everything else into the floor of the map. The index is
            # taken directly rather than interpolated, which is what the old
            # sorted-list walk did; `np.percentile` would move the boundary
            # between two samples and subtly shift the whole coastline.
            flat = np.sort(raw, axis=None)
            lo = flat[int(flat.size * 0.05)]
            hi = flat[int(flat.size * 0.95)]
        else:
            lo = hi = 0.0
        span = max(0.12, float(hi - lo))

        # Normalise, then apply a dome so ridges favour the middle of the frame
        # and the map opens into flatland toward the top and bottom edges. That
        # is what makes it read as surveyed terrain instead of a band of noise.
        mid = (H - 1) / 2.0
        if H > 1:
            dy = np.abs(np.arange(H, dtype=float) - mid) / max(1.0, mid)
            dome = (0.45 + 0.55 * (1.0 - dy * dy))[:, None]
        else:
            dome = 1.0
        field = np.clip(((raw - lo) / span) * dome, 0.0, 1.0)

        # Smooth before tracing. A raw spectrogram is speckled, and contours
        # drawn through every speckle read as noise; a couple of box-blur
        # passes turn it into coherent landmasses worth drawing contours on.
        #
        # This is a 3x3 mean over the in-bounds neighbours. It used to be a
        # Python loop over every cell, which was the single most expensive
        # thing in this effect on a maximised window; the sums are the same
        # arithmetic done on whole rows at once. Edge and corner cells divide
        # by the number of neighbours they actually have, exactly as before,
        # so the coastline of the map is unchanged.
        def _blur(grid, passes=2):
            for _ in range(passes):
                padded = np.pad(grid, 1, mode="constant", constant_values=0.0)
                total = (padded[:-2, :-2] + padded[:-2, 1:-1] + padded[:-2, 2:]
                         + padded[1:-1, :-2] + padded[1:-1, 1:-1] + padded[1:-1, 2:]
                         + padded[2:, :-2] + padded[2:, 1:-1] + padded[2:, 2:])
                ones = np.pad(np.ones_like(grid), 1, mode="constant", constant_values=0.0)
                count = (ones[:-2, :-2] + ones[:-2, 1:-1] + ones[:-2, 2:]
                         + ones[1:-1, :-2] + ones[1:-1, 1:-1] + ones[1:-1, 2:]
                         + ones[2:, :-2] + ones[2:, 1:-1] + ones[2:, 2:])
                grid = total / count
            return grid

        field = _blur(field, 2 if cw * H < 4000 else 1)

        # Contour lines are traced along the time axis: the map reads left to
        # right as time, and a contour exists where the elevation crosses a
        # fixed threshold between adjacent columns. Connecting those crossings
        # per row gives continuous slopes instead of a stipple of every cell.
        bands = 5
        thresholds = [b / float(bands) for b in range(1, bands)]
        crossings = [[False] * cw for _ in range(H)]
        for r in range(H):
            row = field[r]
            for c in range(1, cw):
                a_e, b_e = row[c - 1], row[c]
                lo_t, hi_t = (a_e, b_e) if a_e <= b_e else (b_e, a_e)
                for t in thresholds:
                    if lo_t < t <= hi_t:
                        crossings[r][c] = True
                        break

        for r in range(H):
            for c in range(cw):
                e = field[r][c]
                shade = max(1, min(12, 1 + int(e * 11)))
                if crossings[r][c]:
                    # A contour crossing: the line itself.
                    grid_c[r][c] = "≈" if H > 2 else "─"
                    grid_col[r][c] = ramp[12 if e > 0.72 else min(12, shade + 3)]
                elif e <= 0.06:
                    grid_c[r][c] = " "
                elif e > 0.86:
                    # Only the true summits get a marker.
                    grid_c[r][c] = "▲"
                    grid_col[r][c] = ramp[12]
                elif e > 0.70:
                    grid_c[r][c] = "●"
                    grid_col[r][c] = ramp[11]
                elif e > 0.45:
                    if (r + c) % 2 == 0:
                        grid_c[r][c] = "░"
                        grid_col[r][c] = ramp[shade]
                elif e > 0.22:
                    if (r * 3 + c) % 3 == 0:
                        grid_c[r][c] = "·"
                        grid_col[r][c] = ramp[shade]

        # Summit markers, one per row, on the highest point of the ridge.
        for r in range(H):
            row = field[r]
            if row.size == 0:
                continue
            top = float(row.max())
            if top > 0.60:
                for c in range(cw):
                    if row[c] >= top - 1e-9:
                        grid_c[r][c] = "▲"
                        grid_col[r][c] = ramp[12]
                        break

        # Satellite sweep: one bright column crossing the map slowly.
        sweep = int((now * 5.0 * a["speed"]) % max(1, cw))
        for r in range(H):
            if grid_c[r][sweep] == " ":
                grid_c[r][sweep] = "·"
            grid_col[r][sweep] = ramp[10] if playing else ramp[5]

        return self._fx_lines(grid_c, grid_col, inner_w, hdr, ftr)

    # ── Shared helpers for the three full-field Rainbow FX visualisers ──
    def _fx_ensure_state(self):
        if not hasattr(self, "_fx_rng"):
            self._init_rainbow_fx_state()
        _ensure_numpy()

    def _fx_audio(self, status: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Normalise player telemetry into the bands and pulse the FX need."""
        st = status or {}

        def _arr(key: str, fallback) -> np.ndarray:
            raw = st.get(key)
            try:
                arr = np.asarray(raw if raw is not None else fallback, dtype=float)
            except Exception:
                arr = np.asarray(fallback, dtype=float)
            if arr.ndim != 1 or arr.size < 2:
                arr = np.asarray(fallback, dtype=float)
            if arr.ndim != 1 or arr.size < 2:
                arr = np.zeros(24, dtype=float)
            return np.nan_to_num(np.clip(arr, 0.0, 1.0), nan=0.0)

        zero = [0.0] * 24
        base = _arr("levels", zero)
        left = _arr("levels_left", base)
        right = _arr("levels_right", base)
        peaks = _arr("peaks", base)
        mono = 0.5 * (left + right) if left.size == right.size else base
        n = 16
        if mono.size > 1:
            bands = np.interp(np.linspace(0.0, 1.0, n), np.linspace(0.0, 1.0, mono.size), mono)
            pks = np.interp(np.linspace(0.0, 1.0, n), np.linspace(0.0, 1.0, peaks.size), peaks) if peaks.size > 1 else np.zeros(n)
        else:
            bands = pks = np.zeros(n)
        bands = np.clip(bands, 0.0, 1.0)
        pks = np.clip(pks, 0.0, 1.0)
        state = str(st.get("state", "stopped"))
        rms = float(st.get("rms_level", 0.0) or 0.0)
        peak_db = float(st.get("peak_db", -60.0) or -60.0)
        try:
            pitch = int(st.get("pitch_percent", 0) or 0)
        except (TypeError, ValueError):
            pitch = 0
        speed = max(0.5, min(2.0, 1.0 + pitch / 100.0))
        energy = float(np.mean(bands)) if bands.size else 0.0
        pulse = max(0.0, min(1.0, rms * 2.4 + max(0.0, (peak_db + 30.0) / 30.0) * 0.45 + energy * 0.35))
        return {
            "st": st, "state": state, "playing": state == "playing", "paused": state == "paused",
            "bands": bands, "peaks": pks, "rms": rms, "peak_db": peak_db,
            "speed": speed, "energy": energy, "pulse": pulse,
            "beat": state == "playing" and (rms > 0.13 or peak_db > -4.5),
        }

    @staticmethod
    def _fx_grid(cw: int, height: int) -> Tuple[List[List[str]], List[List[str]]]:
        return ([[" "] * cw for _ in range(height)], [[""] * cw for _ in range(height)])

    def _fx_lines(self, gc, gcol, inner_w: int, header: str, footer: str) -> List[str]:
        """Wrap a glyph/colour grid in the same frame every equaliser uses.

        Colour and bold are emitted once per run instead of once per cell. The
        Liquid Spectrogram and the Overview Effect paint long horizontal runs
        of a single palette entry, so the old per-cell form spent thirty SGR
        sequences describing one stretch of water. On a maximised window that
        was most of the escape traffic the terminal had to parse.

        Spaces keep the previous colour rather than resetting it. A space has
        no ink, so carrying the run across it is invisible on screen and stops
        a gap in the picture from splitting one colour run into two.

        Bold is toggled explicitly. A palette can switch from a bold entry to a
        plain one between two adjacent cells, and `38;2;...` alone does not
        clear bold -- it would leak into the rest of the row. Tracking it here
        keeps every cell the weight it was drawn with.
        """
        inner_w = max(8, int(inner_w))
        header = _clip_display(header, inner_w)
        footer = _clip_display(footer, inner_w)
        limited = _use_limited_console_glyphs()
        safe = _STARDUST_SAFE
        top = f"  {self.b_col}╭{header}{'─' * max(0, inner_w - _display_width(header))}╮{NC}"
        bot = f"  {self.b_col}╰{footer}{'─' * max(0, inner_w - _display_width(footer))}╯{NC}"
        left = f"{self.b_col}│{NC}"
        right = f"{self.b_col}│{NC}"
        lines = [top]
        for r in range(len(gc)):
            row = gc[r]
            row_col = gcol[r]
            parts = []
            current = None
            bold_on = False
            for c, glyph in enumerate(row):
                if glyph == " ":
                    parts.append(" ")
                    continue
                if limited:
                    glyph = safe.get(glyph, glyph)
                colour = row_col[c]
                is_bold = colour.startswith("\033[1;")
                if is_bold != bold_on:
                    parts.append(BOLD if is_bold else "\033[22m")
                    bold_on = is_bold
                if is_bold:
                    colour = colour.replace("\033[1;", "\033[", 1)
                if colour != current:
                    parts.append(colour)
                    current = colour
                parts.append(glyph)
            if bold_on:
                parts.append("\033[22m")
            if current:
                parts.append(NC)
            lines.append(f"  {left}{''.join(parts)}{right}")
        lines.append(bot)
        return lines

    def _fx_micro_meter(self, style: str, t_now: float, levels) -> str:
        """A 14-cell readout for MICRO mode, one glyph and palette per style.

        The three Stream of Frequency readings keep their own colours here too:
        the Dynamic Spectrogram keeps drifting rainbow hues, while the Liquid
        and Overview effects use their fixed cool and warm palettes so the
        miniature still matches the full-size picture.
        """
        lv = np.asarray(levels if levels is not None else [], dtype=float)
        n = 14
        palette = None
        if style == "stream_of_frequency_liquid_eq":
            palette = _sof_ramp_ansi(_SOF_LIQUID_RAMP)
        elif style == "stream_of_frequency_overview_eq":
            palette = _sof_ramp_ansi(_SOF_OVERVIEW_RAMP)
        out = []
        for i in range(n):
            v = float(lv[i % len(lv)]) if lv.size else 0.0
            if palette is not None:
                ansi = palette[max(1, min(12, 1 + int(v * 11)))]
            else:
                hue = int(((t_now * 0.55 + i * 0.071) % 1.0) * 360)
                ansi = _rainbow_fx_ansi(hue, 5 + int(min(1.0, v) * 7), v > 0.72)
            if style == "rainbow_mandala_eq":
                glyph = "✺" if v > 0.74 else ("✴" if v > 0.48 else ("✳" if v > 0.22 else ("◦" if v > 0.05 else "·")))
            elif style == "rainbow_wormhole_eq":
                glyph = "◉" if v > 0.72 else ("◎" if v > 0.44 else ("◌" if v > 0.20 else ("∙" if v > 0.05 else "·")))
            elif style == "stream_of_frequency_liquid_eq":
                # A still pool: filled blocks, no drifting rainbow hue.
                glyph = "█" if v > 0.72 else ("▓" if v > 0.46 else ("▒" if v > 0.24 else ("░" if v > 0.08 else "·")))
            elif style == "stream_of_frequency_overview_eq":
                # A contour readout: summit marks and band lines.
                scaled = abs((v * 6.0) - round(v * 6.0))
                glyph = "▲" if (v > 0.55 and scaled < 0.25) else ("─" if v > 0.20 else ("░" if v > 0.05 else "·"))
            else:
                glyph = "✦" if v > 0.74 else ("≋" if v > 0.46 else ("∿" if v > 0.22 else ("~" if v > 0.05 else "·")))
            out.append(f"{ansi}{console_symbol(glyph)}{NC}")
        return "".join(out)

    # ── Rainbow Mandala Bloom ─────────────────────────────────────────
    def _fx_frame_geometry(self, cw: int, height: int) -> Tuple[float, float, float, float]:
        """Centre and half-extent of the cell grid, in cell units."""
        cx, cy = (cw - 1) / 2.0, (height - 1) / 2.0
        return cx, cy, max(0.5, cx), max(0.5, cy)

    def _fx_tangent_glyph(self, cos_t: float, sin_t: float, sx: float, sy: float) -> str:
        """A line glyph that follows a normalised ellipse at this angle."""
        dx, dy = abs(sx * sin_t), abs(sy * cos_t)
        if dy > dx * 2.0:
            return "│"
        if dx > dy * 2.0:
            return "─"
        return "╲" if cos_t * sin_t > 0.0 else "╱"

    def _fx_plot_ring(self, gc, gcol, cx, cy, sx, sy, steps, lut, hue_i, level_fn, table,
                      wobble_fn=None, glyph_fn=None):
        """Plot an ellipse of `steps` points and return the plotted cells."""
        pts = []
        height, cw = len(gc), len(gc[0])
        for i, (cos_t, sin_t, edge) in enumerate(lut):
            th = 2.0 * math.pi * i / steps
            wob = 1.0 if wobble_fn is None else wobble_fn(th)
            r = int(round(cy + sin_t * sy * wob))
            c = int(round(cx + cos_t * sx * wob))
            if not (0 <= r < height and 0 <= c < cw):
                continue
            gc[r][c] = glyph_fn(i, th, edge) if glyph_fn else edge
            gcol[r][c] = table[(hue_i + int(i * 360.0 / steps * 0.22)) % 360][level_fn(i, th)]
            pts.append((r, c))
        return pts

    def render_rainbow_mandala_eq(self, status: Optional[Dict[str, Any]] = None, width: int = 80, height: int = 7) -> List[str]:
        """Kaleidoscopic mandala: concentric spectrum wreaths, rosette petals,
        orbiting sparks and beat shockwaves, all in shifting rainbow hues."""
        self._fx_ensure_state()
        a = self._fx_audio(status)
        inner_w = max(20, width - 6 if width >= 26 else width - 4)
        H = max(1, int(height))
        cw = inner_w
        now = time.time()
        dt = max(0.004, min(0.12, now - getattr(self, "_mandala_last", now)))
        self._mandala_last = now
        spd, playing = a["speed"], a["playing"]
        bands, peaks, rms, beat, pulse = a["bands"], a["peaks"], a["rms"], a["beat"], a["pulse"]
        rng = self._fx_rng
        gc, gcol = self._fx_grid(cw, H)
        cx, cy, sx, sy = self._fx_frame_geometry(cw, H)
        hue_base_i = int(((now * 0.11 * spd) % 1.0) * 360)
        tbl_n, tbl_b = _fx_ansi_table(False), _fx_ansi_table(True)
        petals = 8 if cw < 56 else (12 if cw < 96 else 16)
        steps = max(64, cw * 2)
        lut = _fx_ring_lut(steps)
        n_rings = max(4, min(12, 4 + H // 2))

        # 1. Concentric spectrum wreaths: each frequency band owns a ring.
        for k in range(n_rings):
            band = float(bands[(k * 5 + 1) % len(bands)])
            peak = float(peaks[(k * 4 + 2) % len(peaks)])
            ring_r = 0.26 + 0.74 * ((k + 1) / float(n_rings)) + band * 0.05
            ring_hue_i = (hue_base_i + int(k * 34 + now * 18 * spd)) % 360
            lvl = min(12, 5 + int(band * 7) + (1 if pulse > 0.55 else 0))
            wob_amp = 0.030 + 0.045 * band
            wob_phase = now * 1.35 * spd + k * 0.8
            table = tbl_b if peak > 0.72 or band > 0.66 else tbl_n
            self._fx_plot_ring(
                gc, gcol, cx, cy, sx * ring_r, sy * ring_r, steps, lut, ring_hue_i,
                lambda i, th, _b=band: min(12, max(3, int(lvl * (0.65 + 0.35 * math.sin(th * 3.0 + wob_phase))))),
                table,
                wobble_fn=lambda th, _a=wob_amp, _p=wob_phase: 1.0 + _a * math.sin(th * 3.0 + _p),
            )

        # 2. Kaleidoscopic rosette: petals are curved lens shapes, not bare spokes,
        #    so the figure reads as a flower rather than a starburst.
        for p in range(petals * 2):
            band = float(bands[(p * 2) % len(bands)])
            peak = float(peaks[(p * 3 + 1) % len(peaks)])
            base_ang = p * math.pi / petals
            swing = 0.34 + 0.16 * math.sin(now * 0.55 * spd + p * 1.7)
            length = 0.42 + 0.50 * band + 0.04 * pulse
            petal_hue_i = (hue_base_i + int(p * 360.0 / (petals * 2))) % 360
            n_steps = max(12, int(length * min(sx, sy) * 3.5) + 14)
            for j in range(n_steps + 1):
                t = j / float(n_steps)
                rr = 0.08 + t * length
                if rr > 1.0:
                    break
                # Curve the petal so it bulges into a pointed lens.
                ang = base_ang + swing * math.sin(t * math.pi) * (0.35 + 0.65 * t)
                sin_a, cos_a = math.sin(ang), math.cos(ang)
                r = int(round(cy + sin_a * sy * rr))
                c = int(round(cx + cos_a * sx * rr))
                if not (0 <= r < H and 0 <= c < cw):
                    continue
                tip = t > 0.92
                glyph = ("✺" if peak > 0.85 else "✦") if tip else ("·" if t < 0.3 else ("∙" if t < 0.75 else "✦"))
                lvl = 12 if tip else 3 + int(t * 8)
                gc[r][c] = glyph
                gcol[r][c] = (tbl_b if tip or t > 0.82 else tbl_n)[(petal_hue_i + int(t * 70)) % 360][lvl]

        # 3. Beat shockwaves expanding from the core.
        waves = self._mandala_rings
        for w in waves:
            w["rr"] += w["v"] * dt * spd
            w["life"] -= dt
        if beat and playing and (not waves or waves[-1]["rr"] > 0.30):
            waves.append({"rr": 0.10, "v": 0.85 + rms * 0.9, "life": 1.3, "hue_i": rng.randrange(360), "steps": steps})
        self._mandala_rings = waves = [w for w in waves if w["life"] > 0.0 and w["rr"] < 1.06][-6:]
        for w in waves:
            fade = max(0.0, min(1.0, w["life"] / 1.3))
            w_steps = max(48, cw)
            w_lut = _fx_ring_lut(w_steps)
            circ_r = w["rr"]
            self._fx_plot_ring(
                gc, gcol, cx, cy, sx * circ_r, sy * circ_r, w_steps, w_lut,
                (w["hue_i"] + int(now * 60)) % 360,
                lambda i, th, _f=fade: max(2, int(11 * _f)),
                tbl_b if fade > 0.6 else tbl_n,
                glyph_fn=lambda i, th, edge, _s=w_steps: "✦" if i % max(6, _s // 8) == 0 else "·",
            )

        # 4. Orbiting sparks on a few independent orbits.
        sparks = self._mandala_sparks
        for p in sparks:
            p["angle"] += p["omega"] * dt * spd
            p["life"] -= dt
        if playing:
            loud = float(np.max(bands)) if bands.size else 0.0
            while len(sparks) < 4 + int(loud * 10):
                sparks.append({
                    "angle": rng.uniform(0.0, 2.0 * math.pi),
                    "rr": rng.uniform(0.34, 0.98),
                    "omega": rng.uniform(0.5, 2.6) * (1.0 if rng.random() > 0.5 else -1.0),
                    "life": rng.uniform(2.0, 5.0),
                    "hue": rng.uniform(0.0, 1.0),
                    "glyph": rng.choice(("✦", "✧", "⋆", "✱", "●", "◉")),
                })
        self._mandala_sparks = sparks = [p for p in sparks if p["life"] > 0.0][-26:]
        for p in sparks:
            r = int(round(cy + math.sin(p["angle"]) * sy * p["rr"]))
            c = int(round(cx + math.cos(p["angle"]) * sx * p["rr"]))
            if 0 <= r < H and 0 <= c < cw:
                hue_i = (hue_base_i + int(p["hue"] * 360) + int(now * 40)) % 360
                gc[r][c] = p["glyph"]
                gcol[r][c] = tbl_b[hue_i][12]
            for trail in (1, 2):
                tr = int(round(cy + math.sin(p["angle"] - 0.10 * trail * p["omega"]) * sy * p["rr"]))
                tc = int(round(cx + math.cos(p["angle"] - 0.10 * trail * p["omega"]) * sx * p["rr"]))
                if 0 <= tr < H and 0 <= tc < cw and gc[tr][tc] == " ":
                    gc[tr][tc] = "·"
                    gcol[tr][tc] = tbl_n[(hue_base_i + int(p["hue"] * 360)) % 360][max(2, 7 - trail * 2)]

        # 5. Pulsing core.
        core_glyph = "✺" if beat else ("✴" if pulse > 0.5 else "✳")
        core_lvl = 12 if beat else (9 if pulse > 0.4 else 7)
        for dr, dc, g, lvl in ((0, 0, core_glyph, core_lvl), (-1, 0, "·", 8), (1, 0, "·", 8),
                               (0, -1, "·", 8), (0, 1, "·", 8)):
            rr, cc = int(round(cy)) + dr, int(round(cx)) + dc
            if 0 <= rr < H and 0 <= cc < cw:
                gc[rr][cc] = g
                gcol[rr][cc] = tbl_b[(hue_base_i + int(now * 70) + dr * 40 + dc * 25) % 360][lvl]

        header = "── 🌈 RAINBOW MANDALA BLOOM (KALEIDOSCOPIC SPECTRUM) " if inner_w >= 58 else ("── 🌈 MANDALA BLOOM " if inner_w >= 30 else "── 🌈 ")
        footer = ("── PETALS %d • WREATHS %d • ORBITS %d ────" % (petals, n_rings, len(sparks))) if inner_w >= 52 else "── MANDALA "
        return self._fx_lines(gc, gcol, inner_w, header, footer)

    # ── Rainbow Wormhole ──────────────────────────────────────────────
    def render_rainbow_wormhole_eq(self, status: Optional[Dict[str, Any]] = None, width: int = 80, height: int = 7) -> List[str]:
        """Hyperspace tunnel: nested rings rushing outward, perspective wall
        beams, spectrum warp spokes and neon motes, in moving rainbow hues."""
        self._fx_ensure_state()
        a = self._fx_audio(status)
        inner_w = max(20, width - 6 if width >= 26 else width - 4)
        H = max(1, int(height))
        cw = inner_w
        now = time.time()
        dt = max(0.004, min(0.12, now - getattr(self, "_worm_last", now)))
        self._worm_last = now
        spd, playing = a["speed"], a["playing"]
        bands, peaks, rms, beat, pulse = a["bands"], a["peaks"], a["rms"], a["beat"], a["pulse"]
        rng = self._fx_rng
        gc, gcol = self._fx_grid(cw, H)
        cx, cy, sx, sy = self._fx_frame_geometry(cw, H)
        hue_base_i = int(((now * 0.13 * spd) % 1.0) * 360)
        tbl_n, tbl_b = _fx_ansi_table(False), _fx_ansi_table(True)
        flow = (0.30 + 0.55 * pulse + 0.35 * a["energy"]) * spd
        self._worm_phase = (getattr(self, "_worm_phase", 0.0) + dt * flow) % 1.0
        phase = self._worm_phase
        self._worm_flash = max(0.0, getattr(self, "_worm_flash", 0.0) - dt * 2.4)
        if beat:
            self._worm_flash = 1.0
        flash = self._worm_flash
        steps = max(64, cw * 2)
        lut = _fx_ring_lut(steps)

        # 1. Perspective tunnel walls: beams from the core through the corners.
        n_beams = 16
        for i in range(n_beams):
            ang = i * 2.0 * math.pi / n_beams + now * 0.18 * spd
            band = float(bands[(i * 3) % len(bands)])
            cos_a, sin_a = math.cos(ang), math.sin(ang)
            beam_hue_i = (hue_base_i + int(i * 360.0 / n_beams)) % 360
            length = 0.30 + 0.70 * band
            n_steps = max(10, int(length * max(sx, sy) * 1.4) + 12)
            for j in range(n_steps + 1):
                t = j / float(n_steps)
                rr = 0.04 + t * length
                if rr > 1.02:
                    break
                r = int(round(cy + sin_a * sy * rr))
                c = int(round(cx + cos_a * sx * rr))
                if not (0 <= r < H and 0 <= c < cw):
                    continue
                glyph = "·" if t < 0.4 else ("∙" if t < 0.78 else "✦")
                lvl = 3 + int(t * 8)
                gc[r][c] = glyph
                gcol[r][c] = (tbl_b if t > 0.86 else tbl_n)[(beam_hue_i + int(t * 50)) % 360][lvl]

        # 2. Nested rings racing outward toward the viewer.
        n_rings = max(5, min(14, 5 + H))
        ring_r = 0.0
        for k in range(n_rings):
            frac = ((k + phase) % n_rings) / float(n_rings)
            ring_r = 0.06 + 0.96 * (frac ** 1.5)
            band = float(bands[int(min(len(bands) - 1, frac * len(bands)))])
            peak = float(peaks[int(min(len(peaks) - 1, frac * len(peaks)))])
            ring_hue_i = (hue_base_i + int(frac * 300 + k * 12)) % 360
            lvl = min(12, 3 + int(frac * 8) + int(band * 3) + int(flash * 2))
            table = tbl_b if peak > 0.74 or frac > 0.85 else tbl_n

            def _ring_level(i, th, _l=lvl, _b=band):
                return min(12, max(2, int(_l * (0.55 + 0.45 * (1.0 - abs(math.sin(th * 2.0)))))) + int(_b * 2))

            def _ring_wobble(th, _b=band):
                return 1.0 + _b * 0.035 * math.sin(th * 3.0 + now * 1.7)

            self._fx_plot_ring(
                gc, gcol, cx, cy, sx * ring_r, sy * ring_r, steps, lut, ring_hue_i,
                _ring_level, table, wobble_fn=_ring_wobble,
            )

        # 3. Spectrum warp spokes from the singularity.
        n_spokes = len(bands)
        for i in range(n_spokes):
            band = float(bands[i])
            ang = i * 2.0 * math.pi / n_spokes - now * 0.30 * spd
            length = 0.14 + 0.86 * band
            cos_a, sin_a = math.cos(ang), math.sin(ang)
            spoke_hue_i = (hue_base_i + int(i * 360.0 / n_spokes)) % 360
            n_steps = max(8, int(length * max(sx, sy) * 1.2) + 10)
            for j in range(n_steps + 1):
                t = j / float(n_steps)
                rr = 0.05 + t * length
                if rr > 1.0:
                    break
                r = int(round(cy + sin_a * sy * rr))
                c = int(round(cx + cos_a * sx * rr))
                if not (0 <= r < H and 0 <= c < cw):
                    continue
                gc[r][c] = "·" if t < 0.55 else ("∙" if t < 0.85 else "✦")
                gcol[r][c] = (tbl_b if t > 0.9 else tbl_n)[(spoke_hue_i + int(t * 70)) % 360][3 + int(t * 8)]

        # 4. Hyperspace motes rushing outward with rainbow trails.
        motes = self._worm_motes
        for p in motes:
            p["rr"] += p["v"] * dt * spd
            p["angle"] += p["omega"] * dt * spd
            p["life"] -= dt
        if playing:
            loud = float(np.max(bands)) if bands.size else 0.0
            while len(motes) < 6 + int(loud * 12):
                motes.append({
                    "angle": rng.uniform(0.0, 2.0 * math.pi),
                    "rr": rng.uniform(0.05, 0.30),
                    "v": rng.uniform(0.35, 0.85),
                    "omega": rng.uniform(-0.7, 0.7),
                    "life": rng.uniform(1.0, 2.4),
                    "hue": rng.uniform(0.0, 1.0),
                    "glyph": rng.choice(("✦", "✧", "⋆", "●", "◉", "•")),
                })
        self._worm_motes = motes = [p for p in motes if p["life"] > 0.0 and p["rr"] < 1.05][-32:]
        for p in motes:
            fade = max(0.0, min(1.0, p["life"] / 2.4))
            hue_i = (hue_base_i + int(p["hue"] * 360)) % 360
            cos_a, sin_a = math.cos(p["angle"]), math.sin(p["angle"])
            for trail in (0, 1, 2):
                rr = p["rr"] - trail * 0.09
                if rr <= 0.0:
                    continue
                r = int(round(cy + sin_a * sy * rr))
                c = int(round(cx + cos_a * sx * rr))
                if not (0 <= r < H and 0 <= c < cw):
                    continue
                glyph = p["glyph"] if trail == 0 else "·"
                lvl = max(2, int((11 if trail == 0 else 6) * fade))
                gc[r][c] = glyph
                gcol[r][c] = (tbl_b if trail == 0 and fade > 0.6 else tbl_n)[hue_i][lvl]

        # 5. Event-horizon core.
        horizon = "◉" if beat else ("◎" if pulse > 0.45 else "◌")
        for dr, dc, g, lvl in ((0, 0, horizon, 12), (-1, 0, "·", 8), (1, 0, "·", 8), (0, -1, "·", 8), (0, 1, "·", 8),
                               (-1, -1, "✧", 10), (-1, 1, "✧", 10), (1, -1, "✧", 10), (1, 1, "✧", 10)):
            rr, cc = int(round(cy)) + dr, int(round(cx)) + dc
            if 0 <= rr < H and 0 <= cc < cw:
                gc[rr][cc] = g
                gcol[rr][cc] = tbl_b[(hue_base_i + int(now * 60) + dr * 40 + dc * 25) % 360][lvl]

        header = "── 🌌 RAINBOW WORMHOLE (HYPERSPACE PLASMA TUNNEL) " if inner_w >= 56 else ("── 🌌 WORMHOLE " if inner_w >= 28 else "── 🌌 ")
        footer = ("── VELOCITY %.2fx • RINGS %d • MOTES %d ────" % (flow, n_rings, len(motes))) if inner_w >= 52 else "── TUNNEL "
        return self._fx_lines(gc, gcol, inner_w, header, footer)

    # ── Rainbow Aurora Cascade ────────────────────────────────────────
    def render_rainbow_aurora_eq(self, status: Optional[Dict[str, Any]] = None, width: int = 80, height: int = 7) -> List[str]:
        """Holographic aurora: drifting sine ribbon curtains, spectrum poles
        behind them, a shimmering sky wash and a rising ember shower."""
        self._fx_ensure_state()
        a = self._fx_audio(status)
        inner_w = max(20, width - 6 if width >= 26 else width - 4)
        H = max(1, int(height))
        cw = inner_w
        now = time.time()
        dt = max(0.004, min(0.12, now - getattr(self, "_aurora_last", now)))
        self._aurora_last = now
        spd, playing = a["speed"], a["playing"]
        bands, peaks, rms, beat, pulse = a["bands"], a["peaks"], a["rms"], a["beat"], a["pulse"]
        rng = self._fx_rng
        gc, gcol = self._fx_grid(cw, H)
        hue_base_i = int(((now * 0.12 * spd) % 1.0) * 360)
        tbl_n, tbl_b = _fx_ansi_table(False), _fx_ansi_table(True)
        self._aurora_flash = max(0.0, getattr(self, "_aurora_flash", 0.0) - dt * 2.2)
        if beat:
            self._aurora_flash = 1.0
        flash = self._aurora_flash

        # 1. Sky wash: a soft band of glow high in the frame.
        haze_h = max(1, int(round(H * 0.34)))
        wash_phase = now * 1.1 * spd
        for r in range(haze_h):
            yf = r / float(max(1, H))
            row_c, row_col = gc[r], gcol[r]
            depth_fade = 1.0 - yf / max(0.25, H * 0.34)
            for c in range(cw):
                xf = c / float(max(1, cw))
                f = math.sin(xf * 7.0 + wash_phase) + math.sin(xf * 3.0 - wash_phase * 0.7 + yf * 5.0)
                val = (f / 2.0 + 1.0) * 0.5 * depth_fade
                if val < 0.74:
                    continue
                row_c[c] = "░" if val < 0.86 else "▒"
                row_col[c] = tbl_n[(hue_base_i + int(xf * 0.6 * 360) + int(yf * 0.2 * 360)) % 360][max(2, int(val * 6))]

        # 2. Spectrum poles rising from the floor behind the curtains.
        step = 2 if cw >= 40 else 3
        band_n = len(bands)
        for c in range(0, cw, step):
            band = float(bands[(c // step) % band_n])
            col_h = int(round(band * H))
            pole_hue = (hue_base_i + int(c / float(max(1, cw)) * 0.5 * 360)) % 360
            for j in range(min(col_h, H)):
                r = H - 1 - j
                gc[r][c] = "╎" if j % 2 else "┊"
                gcol[r][c] = tbl_n[(pole_hue + j * 9) % 360][2 + min(5, int(j / max(1, H) * 5))]

        # 3. Ribbon curtains: sine lattice whose amplitude follows the spectrum.
        curtains = self._aurora_curtains
        if not curtains:
            count = max(2, min(4, H - 1))
            for k in range(count):
                curtains.append({
                    "yf": (k + 1.0) / (count + 1.0),
                    "amp": rng.uniform(0.40, 0.85),
                    "freq": rng.uniform(0.10, 0.20),
                    "speed": rng.uniform(0.45, 0.95),
                    "phase": rng.uniform(0.0, 6.283),
                    "hue": rng.uniform(0.0, 1.0),
                })
        max_r = H - 1
        for k, cur in enumerate(curtains):
            band = float(bands[(k * 4 + 1) % len(bands)])
            peak = float(peaks[(k * 6 + 2) % len(peaks)])
            amp = cur["amp"] * (0.45 + band * 1.3) * max(0.5, H * 0.30)
            y0 = cur["yf"] * max_r
            freq = cur["freq"]
            ph = now * cur["speed"] * spd + cur["phase"]
            ph2 = -now * cur["speed"] * 0.65 * spd + cur["phase"] * 1.7
            band_hue = (hue_base_i + int(cur["hue"] * 360) + int(now * 0.05 * 360)) % 360
            crest_hits = 0
            for c in range(cw):
                wave = (math.sin(c * freq + ph)
                        + 0.55 * math.sin(c * freq * 2.2 + ph2))
                y = y0 + amp * wave * 0.55
                y = 0.0 if y < 0.0 else (float(max_r) if y > max_r else y)
                top = int(round(y))
                if not (0 <= top < H):
                    continue
                col_hue = (band_hue + int(c / float(max(1, cw)) * 0.5 * 360)) % 360
                slope = wave
                gc[top][c] = "─" if abs(slope) < 0.25 else ("╱" if slope > 0.0 else "╲")
                gcol[top][c] = (tbl_b if band > 0.55 else tbl_n)[col_hue][max(3, min(12, int(5 + band * 6 + flash * 3)))]
                # One-row-thick shimmer under the crest, only where it is bright.
                if band > 0.42 and top + 1 < H and gc[top + 1][c] == " ":
                    gc[top + 1][c] = "·"
                    gcol[top + 1][c] = tbl_n[(col_hue + 18) % 360][max(2, int(3 + band * 4))]
                if peak > 0.55 and crest_hits < 4 and (c + k * 5) % 19 == 0 and 0 <= top - 1 < H and gc[top - 1][c] == " ":
                    gc[top - 1][c] = "✧"
                    gcol[top - 1][c] = tbl_b[(hue_base_i + int(c * 0.09 * 360)) % 360][11]
                    crest_hits += 1

        # 4. Ember shower drifting through the curtains.
        embers = self._aurora_embers
        for p in embers:
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            p["life"] -= dt
        if playing:
            loud = float(np.max(bands)) if bands.size else 0.0
            while len(embers) < 5 + int(loud * 10):
                embers.append({
                    "x": rng.uniform(0.0, max(1.0, cw - 1)),
                    "y": rng.uniform(0.0, max(0.0, float(max_r))),
                    "vx": rng.uniform(-0.7, 0.7),
                    "vy": rng.choice((-1.0, -1.0, 1.0)) * rng.uniform(0.4, 1.2),
                    "life": rng.uniform(1.2, 2.8),
                    "hue": rng.uniform(0.0, 1.0),
                    "glyph": rng.choice(("✦", "✧", "⋆", "•", "°", "∙")),
                })
        self._aurora_embers = embers = [
            p for p in embers if p["life"] > 0.0 and -0.5 < p["y"] < H and -0.5 < p["x"] < cw
        ][-26:]
        for p in embers:
            fade = max(0.0, min(1.0, p["life"] / 2.8))
            twinkle = 0.55 + 0.45 * math.sin(now * 7.0 + p["hue"] * 9.0)
            hue_i = (hue_base_i + int(p["hue"] * 360) + int(now * 0.15 * 360)) % 360
            r = int(round(p["y"]))
            c = int(round(p["x"]))
            if 0 <= r < H and 0 <= c < cw:
                gc[r][c] = p["glyph"]
                gcol[r][c] = (tbl_b if fade > 0.7 else tbl_n)[hue_i][max(2, int(11 * fade * twinkle))]

        header = "── 🌈 RAINBOW AURORA CASCADE (HOLOGRAPHIC RIBBONS) " if inner_w >= 58 else ("── 🌈 AURORA CASCADE " if inner_w >= 30 else "── 🌈 ")
        footer = ("── CURTAINS %d • EMBERS %d • SPECTRUM POLES ────" % (len(curtains), len(embers))) if inner_w >= 52 else "── AURORA "
        return self._fx_lines(gc, gcol, inner_w, header, footer)

    def pitch_control_colors(self) -> Dict[str, str]:
        """Foreground inks for the DJ pitch knob, the filled run, and the track.

        C cycles the theme. The knob and the line used to stay on the
        traffic-light colours, so the CLI and the Liquid Player both kept a
        green, amber, or indigo pitch control after every other piece of
        chrome had changed. The knob takes the accent, the filled run takes
        the highlight, the centre detent takes the active ink, and the empty
        track takes the border.

        The Whitest White stores its highlight as a background fill. That is
        not an ink, so a background-only code falls back to the accent.
        """
        pal = self.get_theme_palette()

        def fg(key: str, fallback: str) -> str:
            code = str(pal.get(key) or "")
            if code.startswith("\033[") and "48;" not in code:
                return code
            if "38;" in code:
                return code
            return fallback

        accent = fg("accent", COLOR_ELECTRIC_CYAN)
        return {
            "knob": accent,
            "fill": fg("highlight", accent),
            "center": fg("active", accent),
            "track": fg("border", accent),
            "label": accent,
        }

    def render_vertical_pitch_fader(self, pitch_pct: int, total_lines: int = 10) -> List[str]:
        p, frows = max(-20, min(20, int(pitch_pct))), max(3, total_lines - 2)
        has_hint = frows % 2 == 0
        tr = frows - 1 if has_hint else frows
        cr = tr // 2
        kr = cr if p == 0 else (cr - int(round((p / 20.0) * cr)) if p > 0 else cr + int(round((-p / 20.0) * (tr - 1 - cr))))
        ink = self.pitch_control_colors()
        knob, fill, center, track = ink["knob"], ink["fill"], ink["center"], ink["track"]
        lines = [f"{self.b_col}╭── DJ PITCH ────────────╮{NC}"]
        for r in range(tr):
            if r == kr == cr: lines.append(f"{self.b_col}│{NC} {knob}[▓█0█▓]{NC} {BOLD}{knob}◄ 0.0% [CENTER]{NC}{self.b_col}│{NC}")
            elif r == kr:
                ts = f"◄ {p:+d}% ({1.0 + p/100:.2f}x)"
                lines.append(f"{self.b_col}│{NC} {knob}[▓███▓]{NC} {BOLD}{knob}{ts:<14}{NC} {self.b_col}│{NC}")
            elif r == cr: lines.append(f"{self.b_col}│{NC} {center}─── 0 ───{NC} {DIM}{track}CENTER (0%){NC}  {self.b_col}│{NC}")
            elif r == 0: lines.append(f"{self.b_col}│{NC}    {fill}▲{NC}     {DIM}+20% MAX{NC}      {self.b_col}│{NC}")
            elif r == tr - 1: lines.append(f"{self.b_col}│{NC}    {track}▼{NC}     {DIM}-20% MIN{NC}      {self.b_col}│{NC}")
            else: lines.append(f"{self.b_col}│{NC}    {track}│{NC}{' ' * 19}{self.b_col}│{NC}")
        if has_hint: lines.append(f"{self.b_col}│{NC}  {DIM}[+/-: 1% • 0: Reset]{NC}  {self.b_col}│{NC}")
        return lines + [f"{self.b_col}╰────────────────────────╯{NC}"]

    def pitch_bar_parts(self, pitch_pct: int, width: int = 36, max_cols: Optional[int] = None) -> Tuple[str, str, str]:
        """The DJ pitch track split into (prefix, track, suffix).

        The player paints the three pieces and keeps the prefix width so a
        click on the track can be turned back into a pitch percentage. Before
        this existed the line was only ever built as one string, so nothing
        knew where the -20%..+20% track started or how wide it ended up after
        the narrow-window shrink.

        The knob and the line read `pitch_control_colors()`, so a C keypress
        recolours them with the theme in both the CLI and the Liquid Player.
        """
        is_lg = bool(os.environ.get("MP_LIQUID_GLASS"))
        bold_s = "" if is_lg else BOLD
        ink = self.pitch_control_colors()
        knob, fill, center, track = ink["knob"], ink["fill"], ink["center"], ink["track"]
        p, w = max(-20, min(20, int(pitch_pct))), max(11, width)
        if w % 2 == 0: w -= 1
        cp = w // 2
        kp = cp if p == 0 else max(0, min(w - 1, cp + int(round((p / 20.0) * cp))))
        def _trk(tw, k, c):
            return "".join(f"{bold_s}{knob}●{NC}" if i == k else (f"{center}┼{NC}" if i == c else ((f"{DIM}{track}━{NC}" if (i < k and p < 0) else (f"{fill}━{NC}" if k <= i < c else f"{DIM}{track}─{NC}")) if i < c else (f"{fill}━{NC}" if c < i <= k else f"{DIM}{track}─{NC}"))) for i in range(tw))
        stag = f"{bold_s}{knob}0.0% [CENTER DETENT]{NC}" if p == 0 else f"{bold_s}{knob}{p:+d}% ({1.0 + p/100:.2f}x){NC}"
        pfx, sfx = f"  {bold_s}{ink['label']}🎚 DJ PITCH:{NC} {DIM}-20%{NC} [", f"] {DIM}+20%{NC}  {stag}  {DIM}[+/-: 1% • 0: Reset]{NC}"
        if max_cols and (_display_width(pfx) + w + _display_width(sfx) > max_cols):
            sfx = f"] {DIM}+20%{NC}  {stag}"
            if _display_width(pfx) + w + _display_width(sfx) > max_cols:
                sfx = f"] {DIM}+20%{NC}  {bold_s}{knob}{p:+d}%{NC}"
            if (avail := max_cols - _display_width(pfx) - _display_width(sfx)) < w:
                w = max(7, avail)
                if w % 2 == 0: w -= 1
                cp = w // 2
                kp = cp if p == 0 else max(0, min(w - 1, cp + int(round((p / 20.0) * cp))))
        return pfx, _trk(w, kp, cp), sfx

    def render_horizontal_pitch_bar(self, pitch_pct: int, width: int = 36, max_cols: Optional[int] = None) -> str:
        return "".join(self.pitch_bar_parts(pitch_pct, width=width, max_cols=max_cols))

    def render(self, status: Dict[str, Any], width: int = 80, height: int = 7) -> List[str]:
        s = self.style
        # The spectrogram styles read levels themselves. Building six arrays
        # here and throwing them away was paid on every frame of the default
        # equaliser, which is the one the Liquid Player opens on.
        if s == "stream_of_frequency_eq": lines = self.render_stream_of_frequency(status, width, height)
        elif s == "stream_of_frequency_liquid_eq": lines = self.render_stream_of_frequency_liquid(status, width, height)
        elif s == "stream_of_frequency_overview_eq": lines = self.render_stream_of_frequency_overview(status, width, height)
        elif s == "rainbow_mandala_eq": lines = self.render_rainbow_mandala_eq(status, width, height)
        elif s == "rainbow_wormhole_eq": lines = self.render_rainbow_wormhole_eq(status, width, height)
        elif s == "rainbow_aurora_eq": lines = self.render_rainbow_aurora_eq(status, width, height)
        else:
            lv = _as_float_vector(status.get("levels"), 24)
            pk = _as_float_vector(status.get("peaks"), 24)
            ll = _as_float_vector(status.get("levels_left", lv), 24)
            lr = _as_float_vector(status.get("levels_right", lv), 24)
            pl = _as_float_vector(status.get("peaks_left", pk), 24)
            pr = _as_float_vector(status.get("peaks_right", pk), 24)
            rms, pdb = float(status.get("rms_level", 0.0)), float(status.get("peak_db", -60.0))
            if s == "neon_prism_eq": lines = self.render_neon_prism_eq(ll, lr, pl, pr, width, height, rms, pdb)
            elif s == "stardust_particles_eq": lines = self.render_stardust_particles_eq(ll, lr, pl, pr, width, height, rms, pdb)
            elif s == "master_spectrum": lines = self.render_master_spectrum(lv, pk, width=width, height=height)
            elif s == "analog_vu": lines = self.render_analog_vu(pdb, ll, lr, width=width)
            elif s == "oscilloscope": lines = self.render_oscilloscope(lv, status, width, height)
            else: lines = self.render_stereo_eq(ll, lr, pk, width, height)
        is_lg = bool(os.environ.get("MP_LIQUID_GLASS"))
        if width >= 122 and not is_lg:
            faders = self.render_vertical_pitch_fader(int(status.get("pitch_percent", 0)), len(lines))
            pad_to = max(91, (width - 6) - 26)
            return [f"{el}{' ' * max(2, pad_to - _display_width(el))}{fl}" for el, fl in zip(lines, faders)]
        return lines

def _get_dynamic_gradient(step: float) -> Tuple[Tuple[int, int, int], Tuple[int, int, int], Tuple[int, int, int]]:
    return (
        (int(180 + 75 * math.sin(step * 0.6180339887 + 0.3)), int(180 + 75 * math.sin(step * 0.8408964152 + 2.1)), int(180 + 75 * math.sin(step * 0.7071067811 + 4.2))),
        (int(180 + 75 * math.sin(step * 0.6180339887 + 3.14159)), int(180 + 75 * math.sin(step * 0.8408964152 + 5.28318)), int(180 + 75 * math.sin(step * 0.7071067811 + 1.04719))),
        (int(60 + 52 * math.sin(step * 0.41 + 0.5)), int(48 + 42 * math.sin(step * 0.53 + 2.4)), int(78 + 58 * math.sin(step * 0.47 + 4.1)))
    )

# The four in-place sizes of the player. Vertical is the same full frame as
# NORMAL, drawn into a portrait column; Mini and Micro are the compact strips.
UI_SIZE_ORDER = ("NORMAL", "VERTICAL", "MINI", "MICRO")
# The two strips that are not the full frame.
COMPACT_UI_SIZES = ("MINI", "MICRO")
UI_SIZE_TITLES = {
    "NORMAL": "MP Audio Player",
    "VERTICAL": "MP Vertical Audio Player",
    "MINI": "MP Mini Audio Player",
    "MICRO": "MP Micro Audio Player",
}
# Vertical keeps the NORMAL aspect turned ninety degrees: 120x40 landscape
# becomes 56x52 portrait, which is 518x976 on the glass against 1094x760.
VERTICAL_COLS, VERTICAL_ROWS = 56, 52


def ui_size_step(current: str, allow_normal: bool = True) -> str:
    """The next size on the M ring: Normal, Vertical, Mini, Micro, Normal.

    The ring always closes, so pressing M four times from any size comes back
    to it. ``allow_normal`` is kept for callers that predate Vertical and
    still ask for a compact-only step, but it never removes NORMAL from the
    ring: making it unreachable left the window stuck in the compact sizes
    with no way back to the full frame.
    """
    cur = str(current or "NORMAL").strip().upper()
    if cur not in UI_SIZE_ORDER:
        cur = "NORMAL"
    start = UI_SIZE_ORDER.index(cur)
    return UI_SIZE_ORDER[(start + 1) % len(UI_SIZE_ORDER)]


def ui_size_grid(size: str) -> Tuple[int, int]:
    """Terminal columns and rows for a published size."""
    size = str(size or "").strip().upper()
    if size == "VERTICAL":
        return VERTICAL_COLS, VERTICAL_ROWS
    if size == "MINI":
        return 74, 14
    if size == "MICRO":
        return 74, 7
    return 120, 40


def ensure_mini_player_kwin_rules():
    if not sys.platform.startswith(("linux", "freebsd")):
        return
    rc_path = Path.home() / ".config" / "kwinrulesrc"
    try:
        import configparser, uuid
        cp = configparser.ConfigParser()
        if rc_path.exists():
            cp.read(str(rc_path))
        else:
            rc_path.parent.mkdir(parents=True, exist_ok=True)
            cp.add_section("General")
            cp.set("General", "count", "0")
            cp.set("General", "rules", "")

        rules_defs = [
            ("MP Mini Audio Player (Fixed Size)", "MP Mini Audio Player"),
            ("MP Micro Audio Player (Fixed Size)", "MP Micro Audio Player"),
            ("MP Vertical Audio Player (Fixed Size)", "MP Vertical Audio Player")
        ]
        modified = False
        for desc, title in rules_defs:
            found_sec = None
            for sec in cp.sections():
                if cp.has_option(sec, "description") and cp.get(sec, "description") == desc:
                    found_sec = sec
                    break
            if not found_sec:
                found_sec = str(uuid.uuid4())
                cp.add_section(found_sec)
                cp.set(found_sec, "description", desc)
                cp.set(found_sec, "wmclass", "konsole")
                cp.set(found_sec, "wmclassmatch", "1")
                cp.set(found_sec, "title", title)
                cp.set(found_sec, "titlematch", "2")
                cp.set(found_sec, "types", "1")
                cp.set(found_sec, "maximizehoriz", "false")
                cp.set(found_sec, "maximizehorizrule", "2")
                cp.set(found_sec, "maximizevert", "false")
                cp.set(found_sec, "maximizevertrule", "2")
                if not cp.has_section("General"):
                    cp.add_section("General")
                count = int(cp.get("General", "count", fallback="0")) + 1
                cp.set("General", "count", str(count))
                existing = [r.strip() for r in cp.get("General", "rules", fallback="").split(",") if r.strip()]
                existing.append(found_sec)
                cp.set("General", "rules", ",".join(existing))
                modified = True
        if modified:
            with open(str(rc_path), "w", encoding="utf-8") as f:
                cp.write(f)
            if shutil.which("qdbus"):
                subprocess.run(["qdbus", "org.kde.KWin", "/KWin", "reconfigure"], capture_output=True)
            elif shutil.which("qdbus-qt6"):
                subprocess.run(["qdbus-qt6", "org.kde.KWin", "/KWin", "reconfigure"], capture_output=True)
    except Exception:
        pass

def _setup_mini_terminal_appearance():
    if sys.platform.startswith(("linux", "freebsd")):
        if (k_dir := Path.home() / ".local" / "share" / "konsole").is_dir():
            try:
                (k_dir / "MP_Mini_Player.colorscheme").write_text(
                    "[General]\nDescription=MP Mini Player\nOpacity=0.30\nBlur=true\nColorRandomization=false\nWallpaperOpacity=1\n\n"
                    "[Background]\nColor=12,14,24\nOpacity=0.30\n\n"
                    "[BackgroundFaint]\nColor=14,16,28\nOpacity=0.30\n\n"
                    "[Foreground]\nColor=240,245,255\n\n"
                    "[ForegroundFaint]\nColor=180,190,210\n\n"
                    "[ForegroundIntense]\nColor=255,255,255\n\n"
                    "[Color0]\nColor=12,14,24\n[Color1]\nColor=255,51,102\n[Color2]\nColor=0,255,180\n"
                    "[Color3]\nColor=255,200,50\n[Color4]\nColor=102,153,255\n[Color5]\nColor=190,100,255\n"
                    "[Color6]\nColor=0,229,255\n[Color7]\nColor=240,245,255\n\n"
                    "[Color0Intense]\nColor=40,45,65\n[Color1Intense]\nColor=255,80,130\n[Color2Intense]\nColor=50,255,200\n"
                    "[Color3Intense]\nColor=255,220,90\n[Color4Intense]\nColor=130,175,255\n[Color5Intense]\nColor=210,130,255\n"
                    "[Color6Intense]\nColor=80,240,255\n[Color7Intense]\nColor=255,255,255\n",
                    encoding="utf-8"
                )
                prof = k_dir / "MP_Mini_Player.profile"
                prof.write_text(
                    "[Appearance]\nColorScheme=MP_Mini_Player\nFont=Monospace,11,-1,5,400,0,0,0,0,0,0,0,0,0,0,1,,0,0\n\n"
                    "[General]\nEnvironment=TERM=xterm-256color,COLORTERM=truecolor\nInheritContainerContext=true\nName=MP_Mini_Player\nParent=FALLBACK/\nTerminalColumns=74\nTerminalRows=14\n\n"
                    "[Scrolling]\nScrollBarPosition=0\nHistoryMode=0\n\n"
                    "[Terminal Features]\nBlinkingCursorEnabled=true\n",
                    encoding="utf-8"
                )
            except Exception: pass
        ensure_mini_player_kwin_rules()

def _build_window_cmd(mini: bool, mode: str, folder_name: str, playlist_path: str, theme: Optional[str], style: Optional[str], micro: bool = False, size: Optional[str] = None) -> List[str]:
    cmd = [sys.executable or "python3", str(Path(__file__).resolve()), "--interactive"]
    size = str(size or "").strip().upper()
    if size in ("VERTICAL", "MINI", "MICRO"):
        cmd.append(f"--{size.lower()}")
    elif micro:
        cmd.append("--micro")
    elif mini:
        cmd.append("--mini")
    if mode == "MUSIC":
        cmd.append("--music-mode")
        if folder_name and folder_name != "ALL": cmd.extend(["--music-folder", str(folder_name)])
        if playlist_path: cmd.extend(["--playlist", str(playlist_path)])
    if theme: cmd.extend(["--theme", str(theme)])
    if style: cmd.extend(["--style", str(style)])
    return cmd

def _konsole_bus_call(args: List[str]) -> str:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=1.0, stdin=subprocess.DEVNULL)
        return proc.stdout or ""
    except Exception:
        return ""

def _konsole_main_window(service: str) -> str:
    for line in _konsole_bus_call(["qdbus", service]).splitlines():
        line = line.strip()
        if line.endswith("MainWindow_1") or (line.count("/") == 2 and "MainWindow" in line):
            return line
    return "/konsole/MainWindow_1"

def _konsole_pixel_size(service: str, window: str) -> Optional[Tuple[int, int]]:
    out = _konsole_bus_call(["busctl", "--user", "get-property", service, window, "org.qtproject.Qt.QWidget", "size"])
    if m := re.search(r"\(ii\)\s+(\d+)\s+(\d+)", out):
        return int(m.group(1)), int(m.group(2))
    return None

def _set_konsole_pixel_size(service: str, window: str, width: int, height: int) -> None:
    subprocess.run(
        ["busctl", "--user", "set-property", service, window, "org.qtproject.Qt.QWidget", "size", "(ii)", str(int(width)), str(int(height))],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=1.0,
    )

_LAST_TERMINAL_TITLE = ""

def _set_terminal_title(title: str) -> None:
    """Publish a console title once. Repeating the same OSC makes macOS tabs flicker."""
    global _LAST_TERMINAL_TITLE
    if not title or title == _LAST_TERMINAL_TITLE:
        return
    _LAST_TERMINAL_TITLE = title
    try:
        sys.stdout.write(f"\033]0;{title}\007")
    except Exception:
        pass

def resize_host_terminal(columns: int, rows: int, title: str) -> None:
    """Resize the current terminal to columns by rows and set its title.

    Wayland Konsole reports the CSI 8 size without changing the window, so the
    Micro Player was drawn in the Mini Player's 14-row frame. Measure the real
    size first, resize the Konsole window over DBus, then emit CSI 8 for other terminals.
    """
    try:
        term_c, term_r = _tty_size((80, 24))
    except Exception:
        term_c, term_r = 80, 24
    service = os.environ.get("KONSOLE_DBUS_SERVICE", "")
    if service and shutil.which("qdbus") and shutil.which("busctl") and not (term_c == columns and term_r == rows):
        try:
            session = os.environ.get("KONSOLE_DBUS_SESSION", "")
            if session:
                for role in ("0", "1"):
                    subprocess.run(["qdbus", service, session, "org.kde.konsole.Session.setTitle", role, title], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=1.0)
            window = _konsole_main_window(service)
            subprocess.run(["qdbus", service, window, "org.qtproject.Qt.QWidget.setWindowTitle", title], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=1.0)
            size = _konsole_pixel_size(service, window)
            if size:
                pix_w, pix_h = size
                probe_h = max(80, pix_h + (36 if rows > term_r else -36))
                _set_konsole_pixel_size(service, window, pix_w, probe_h)
                time.sleep(0.06)
                probed = _konsole_pixel_size(service, window) or (pix_w, probe_h)
                probed_r = _tty_size((80, 24))[1]
                if probed_r != term_r and probed[1] != pix_h:
                    cell_h = max(8, int(round((probed[1] - pix_h) / (probed_r - term_r))))
                else:
                    cell_h = 19
                chrome_h = pix_h - cell_h * term_r
                _set_konsole_pixel_size(service, window, pix_w, max(80, chrome_h + cell_h * rows))
                time.sleep(0.06)
                now_c, now_r = _tty_size((80, 24))
                if now_r != rows or now_c != columns:
                    cur = _konsole_pixel_size(service, window)
                    if cur:
                        fix_h = cur[1] + (rows - now_r) * cell_h
                        fix_w = cur[0] if now_c == columns else cur[0] + (columns - now_c) * max(6, int(round(cur[0] / max(now_c, 1))))
                        _set_konsole_pixel_size(service, window, max(120, fix_w), max(80, fix_h))
        except Exception:
            pass
    try:
        if not os.environ.get("KONSOLE_DBUS_SERVICE"):
            sys.stdout.write(f"\033[8;{int(rows)};{int(columns)}t")
        _set_terminal_title(title)
        sys.stdout.flush()
    except Exception:
        pass

def launch_mini_player_window(mode: str = "MIX_ARCHIVE", folder_name: str = "ALL", playlist_path: str = "", theme: Optional[str] = None, style: Optional[str] = None, micro: bool = False, size: str = "") -> bool:
    """Open a compact player window: the tall deck, Mini, or Micro.

    ``size`` names the frame directly. ``micro`` and ``mini`` are the older
    flags and still work, so every existing caller keeps its behaviour.
    """
    size = str(size or "").strip().upper()
    if size not in COMPACT_UI_SIZES:
        size = "MICRO" if micro else "MINI"
    title = UI_SIZE_TITLES[size]
    t_cols, t_rows = ui_size_grid(size)
    t_rows = str(t_rows)
    cmd = _build_window_cmd(False, mode, folder_name, playlist_path, theme, style, micro=micro, size=size)
    base_dir = str(get_base_dir())
    if sys.platform.startswith(("linux", "freebsd")):
        _setup_mini_terminal_appearance()
        cmd_str = " ".join(f'"{c}"' for c in cmd)
        if shutil.which("konsole") and _run_bg([
            "konsole",
            "--profile", "MP_Mini_Player",
            "--hide-menubar",
            "--hide-tabbar",
            "--separate",
            "--workdir", base_dir,
            "--qwindowtitle", title,
            "-p", f"tabtitle={title}",
            "-p", f"TerminalColumns={t_cols}",
            "-p", f"TerminalRows={t_rows}",
            "-p", "ScrollBarPosition=0",
            "-e", "bash", "-c", cmd_str
        ]):
            if shutil.which("xprop"):
                def _set_hints():
                    time.sleep(0.3)
                    subprocess.run(["xprop", "-name", title, "-f", "_NET_WM_WINDOW_OPACITY", "32c", "-set", "_NET_WM_WINDOW_OPACITY", "0x4d000000"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                threading.Thread(target=_set_hints, daemon=True).start()
            return True
        geometry = f"{t_cols}x{t_rows}"
        for term, targs in [
            ("gnome-terminal", [f"--title={title}", f"--geometry={geometry}", "--", "bash", "-c", cmd_str]),
            ("xfce4-terminal", [f"--title={title}", f"--geometry={geometry}", "--hide-menubar", "--hide-borders", "-e", f"bash -c '{cmd_str}'"]),
            ("alacritty", ["--title", title, "-o", "window.opacity=0.30", "-o", f"window.dimensions.columns={t_cols}", "-o", f"window.dimensions.lines={t_rows}", "-e", "bash", "-c", cmd_str]),
            ("foot", ["-T", title, "-w", geometry, "-a", "0.30", "bash", "-c", cmd_str]),
            ("xterm", ["-title", title, "-geometry", geometry, "-e", "bash", "-c", cmd_str]),
            ("xdg-terminal-exec", ["bash", "-c", cmd_str])
        ]:
            if shutil.which(term) and _run_bg([term] + targs): return True
    elif sys.platform == "darwin":
        c_str = " ".join(f'\\"{a}\\"' for a in cmd)
        return _run_bg(["osascript", "-e", f'tell application "Terminal" to do script "{c_str}"'])
    elif sys.platform == "win32":
        if shutil.which("wt.exe") and _run_bg(["wt.exe", "-w", "new", "--title", title, "-p", "Command Prompt", "--size", f"{t_cols},{t_rows}"] + cmd): return True
        c_str = " ".join(f'"{a}"' for a in cmd)
        try: subprocess.Popen(f'start "{title}" {c_str}', shell=True); return True
        except Exception: pass
    return False

def launch_player_window(mode: str = "MIX_ARCHIVE", folder_name: str = "ALL", playlist_path: str = "", theme: Optional[str] = None, style: Optional[str] = None) -> bool:
    title, cmd = "MP Audio Player", _build_window_cmd(False, mode, folder_name, playlist_path, theme, style)
    if sys.platform.startswith(("linux", "freebsd")):
        if shutil.which("konsole") and _run_bg(["konsole", "--separate", "--workdir", str(Path.cwd()), "--qwindowtitle", title, "-p", f"tabtitle={title}", "-e"] + cmd): return True
        for term, targs in [("xdg-terminal-exec", cmd), ("gnome-terminal", [f"--title={title}", "--"] + cmd), ("xfce4-terminal", [f"--title={title}", "-e", " ".join(f'"{a}"' for a in cmd)]), ("alacritty", ["--title", title, "-e"] + cmd), ("foot", ["-T", title] + cmd), ("xterm", ["-title", title, "-e"] + cmd)]:
            if shutil.which(term) and _run_bg([term] + targs): return True
    elif sys.platform == "darwin":
        c_str = " ".join(f'\\"{a}\\"' for a in cmd)
        return _run_bg(["osascript", "-e", f'tell application "Terminal" to do script "{c_str}"'])
    elif sys.platform == "win32":
        if shutil.which("wt.exe") and _run_bg(["wt.exe", "-w", "new", "--title", title, "-p", "Command Prompt"] + cmd): return True
        c_str = " ".join(f'"{a}"' for a in cmd)
        try: subprocess.Popen(f'start "{title}" {c_str}', shell=True); return True
        except Exception: pass
    return False

def launch_liquid_glass_player(
    base_dir: Optional[Path] = None,
    mode: Optional[str] = None,
    folder_name: Optional[str] = None,
    playlist_path: Optional[str] = None,
    theme: Optional[str] = None,
    style: Optional[str] = None,
    mini: bool = False,
    micro: bool = False,
    vertical: bool = False,
    no_visualizer: bool = False,
) -> bool:
    if sys.platform not in ("linux", "darwin", "win32") and not sys.platform.startswith("freebsd"):
        return False
    base = base_dir or get_base_dir()

    if sys.platform == "darwin":
        candidates = [
            base / "MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py",
            base / "scripts" / "MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py",
            Path(__file__).resolve().parent / "MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py",
            base / "MP_Audio_Player_Liquid_Glass_macOS.py",
            base / "scripts" / "MP_Audio_Player_Liquid_Glass_macOS.py",
            Path(__file__).resolve().parent / "MP_Audio_Player_Liquid_Glass_macOS.py",
        ]
    else:
        backend = os.environ.get("LIQUID_GLASS_BACKEND", "").strip().lower()
        if not backend:
            for cfg in (base / "config.env", CONFIG_DIR / "config.env", Path(__file__).resolve().parent / "config.env"):
                if cfg.is_file():
                    if val := parse_env_file(cfg).get("LIQUID_GLASS_BACKEND"):
                        backend = val.strip().lower()
                        break
        if not backend:
            backend = "opengl"

        if backend == "vulkan":
            candidates = [
                base / "MP_Audio_Player_Liquid_Glass.py",
                base / "scripts" / "MP_Audio_Player_Liquid_Glass.py",
                Path(__file__).resolve().parent / "MP_Audio_Player_Liquid_Glass.py",
                base / "MP_Audio_Player_Liquid_Glass_OpenGL.py",
                base / "scripts" / "MP_Audio_Player_Liquid_Glass_OpenGL.py",
                Path(__file__).resolve().parent / "MP_Audio_Player_Liquid_Glass_OpenGL.py",
            ]
        else:
            candidates = [
                base / "MP_Audio_Player_Liquid_Glass_OpenGL.py",
                base / "scripts" / "MP_Audio_Player_Liquid_Glass_OpenGL.py",
                Path(__file__).resolve().parent / "MP_Audio_Player_Liquid_Glass_OpenGL.py",
                base / "MP_Audio_Player_Liquid_Glass.py",
                base / "scripts" / "MP_Audio_Player_Liquid_Glass.py",
                Path(__file__).resolve().parent / "MP_Audio_Player_Liquid_Glass.py",
            ]

    script_path = None
    for cand in candidates:
        if cand.is_file():
            script_path = cand
            break

    if not script_path:
        return False

    cmd = [sys.executable, str(script_path)]
    if no_visualizer:
        cmd.append("--no-visualizer")
    if micro:
        cmd.append("--micro")
    elif mini:
        cmd.append("--mini")
    elif vertical:
        cmd.append("--vertical")
    if str(mode or "").upper() == "MUSIC":
        cmd.append("--music-mode")
        if folder_name and str(folder_name).upper() != "ALL":
            cmd.extend(["--music-folder", str(folder_name)])
        if playlist_path:
            cmd.extend(["--playlist", str(playlist_path)])
    elif str(mode or "").upper() == "MIX_ARCHIVE":
        cmd.append("--mix-archive-mode")
    if theme:
        cmd.extend(["--theme", str(theme)])
    if style:
        cmd.extend(["--style", str(style)])

    log_dir = Path.home() / ".config" / "mix-manager"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    log_file = log_dir / "liquid_glass.log"

    # MP Liquid Player stops at four windows. The host claims its own slot
    # before it draws, and this early check only gives a clear message.
    limiter = _mp_instance_module()
    if limiter is not None and limiter.at_limit("player"):
        try:
            limiter.focus_existing("player")
            limiter.notify_limit("player")
        except Exception:
            pass
        return False

    extra_kwargs = {}
    if sys.platform == "win32":
        extra_kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        extra_kwargs["start_new_session"] = True

    try:
        with open(log_file, "a") as log_fd:
            proc = subprocess.Popen(
                cmd,
                cwd=str(base),
                stdin=subprocess.DEVNULL,
                stdout=log_fd,
                stderr=log_fd,
                close_fds=True,
                **extra_kwargs,
            )
            return proc.pid > 0
    except Exception:
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(base),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                **extra_kwargs,
            )
            return proc.pid > 0
        except Exception as exc:
            try:
                mp_log_error("MP_Audio_Player", f"Failed launching Liquid Glass player: {exc}", exc)
            except Exception:
                pass
            return False


def _mp_instance_module():
    """The shared four-window limiter, or None when the helper is missing."""
    try:
        import mp_instance_limit
        return mp_instance_limit
    except ImportError:
        pass
    try:
        scripts = get_base_dir() / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        import mp_instance_limit
        return mp_instance_limit
    except Exception:
        return None


def _mp_gui_is_running() -> bool:
    try:
        return subprocess.run(
            ["pgrep", "-f", "MP_Audio_Player_Liquid_Glas[s].*--manager"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
    except Exception:
        return False


def _windowed_mp_gui_host() -> bool:
    """True when a --manager host that maps a window is running.

    MP_Audio_Player_Liquid_Glass_macOS.py (Metal) matches the pgrep above and
    never opens a window. That process must not block a new launch.
    """
    try:
        res = subprocess.run(
            ["pgrep", "-fl", "MP_Audio_Player_Liquid_Glas[s].*--manager"],
            capture_output=True,
            text=True,
        )
    except Exception:
        return False
    if getattr(res, "returncode", 1) != 0:
        return False
    text = res.stdout if isinstance(res.stdout, str) else ""
    for line in text.splitlines():
        if "MP_Audio_Player_Liquid_Glass_OpenGL" in line:
            return True
        if "MP_Audio_Player_Liquid_Glass.py" in line:
            return True
    return False


def _focus_mp_gui() -> bool:
    wmctrl = shutil.which("wmctrl")
    if not wmctrl:
        return False
    try:
        subprocess.run(
            [wmctrl, "-a", "MP Mix Manager (Liquid Glass"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def _raise_windowed_mp_gui(base: Path) -> None:
    helper = base / "scripts" / "mp_gui_focus.sh"
    if not helper.is_file():
        alt = Path(__file__).resolve().parent / "scripts" / "mp_gui_focus.sh"
        helper = alt if alt.is_file() else helper
    if not helper.is_file():
        return
    try:
        subprocess.run(
            ["bash", "-c", 'source "$1" && mp_gui_focus_if_open', "mp-gui", str(helper)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except Exception:
        pass


def _liquid_mixer_module():
    """The shared X-key launcher, or None when the helper is missing."""
    try:
        import liquid_mixer_launch
        return liquid_mixer_launch
    except ImportError:
        pass
    try:
        scripts = get_base_dir() / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        import liquid_mixer_launch
        return liquid_mixer_launch
    except Exception:
        return None


def _liquid_mixer_notice(result: str) -> str:
    """One wording for every X press, shared by all three launchers."""
    return {
        "opened": "✦ Opened MP Liquid Mixer",
        "already-open": "MP Liquid Mixer is already open",
        "starting": "MP Liquid Mixer is already starting",
        "unsupported": "MP Liquid Mixer needs a Linux desktop (X11)",
        "missing": "MP Liquid Mixer was not found beside the player",
    }.get(result, "MP Liquid Mixer could not be opened")


def launch_liquid_mixer(base_dir: Optional[Path] = None) -> str:
    """Open MP Liquid Mixer, or raise the one that is already open.

    X is a launcher, not a second window: the mixer holds
    ``liquid_mixer.lock`` for its whole life, because both of its decks live
    in fixed IPC namespaces. The helper reads that lock, so a second X press
    can only ever focus the window that owns it. Returns one of
    ``opened`` / ``already-open`` / ``starting`` / ``unsupported`` /
    ``missing``.
    """
    helper = _liquid_mixer_module()
    if helper is None:
        return "missing"
    try:
        return helper.launch_mixer(base_dir)
    except Exception as exc:
        try:
            mp_log_error("MP_Audio_Player", f"Failed launching MP Liquid Mixer: {exc}", exc)
        except Exception:
            pass
        return "missing"


def launch_mp_gui(base_dir: Optional[Path] = None) -> bool:
    """Open MP Gui beside the player, or focus that window if it is already open.

    --keep-theme leaves the shared theme file alone so the open Liquid Player keeps its color.
    MP Gui opens up to four windows. A fifth launch focuses one instead.
    """
    # Callers pass the scanner's base_dir, which is not always a real path: a
    # test double or a partially built scanner yields a non-Path (even a mock).
    # Resolving it used to raise inside Popen and log a FileNotFoundError with
    # a "<MagicMock ...>" path, which was noise, not a launch failure.
    base: Optional[Path] = None
    if base_dir is not None:
        try:
            candidate = Path(os.fspath(base_dir))
            if candidate.is_dir():
                base = candidate
        except (TypeError, ValueError, OSError):
            base = None
    limiter = _mp_instance_module()
    if limiter is not None and limiter.at_limit("gui"):
        try:
            limiter.notify_limit("gui")
        except Exception:
            pass
        if _focus_mp_gui():
            return True
        if _windowed_mp_gui_host():
            _raise_windowed_mp_gui(base_dir or Path(__file__).resolve().parent)
        return True
    base = base or get_base_dir()
    launcher = base / "MP_Gui.sh"
    if not launcher.is_file():
        alt = Path(__file__).resolve().parent / "MP_Gui.sh"
        launcher = alt if alt.is_file() else launcher
    if not launcher.is_file():
        return False
    log_dir = Path.home() / ".config" / "mix-manager"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    log_file = log_dir / "mp_gui.log"
    extra_kwargs = {}
    if sys.platform == "win32":
        extra_kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        extra_kwargs["start_new_session"] = True
    try:
        with open(log_file, "a") as log_fd:
            proc = subprocess.Popen(
                ["bash", str(launcher), "--keep-theme"],
                cwd=str(launcher.parent),
                stdin=subprocess.DEVNULL,
                stdout=log_fd,
                stderr=log_fd,
                close_fds=True,
                **extra_kwargs,
            )
        return proc.pid > 0
    except Exception as exc:
        try:
            mp_log_error("MP_Audio_Player", f"Failed launching MP Gui: {exc}", exc)
        except Exception:
            pass
        return False


def _liquid_visualizer_size_label() -> str:
    if sys.platform == "darwin":
        return "1094x734"
    if sys.platform.startswith("linux"):
        try:
            from liquid_player_visualizer import linux_visualizer_window_size
        except ImportError:
            from scripts.liquid_player_visualizer import linux_visualizer_window_size
        width, height = linux_visualizer_window_size()
        return f"{int(width)}x{int(height)}"
    return "700x700"


def launch_liquid_visualizer(
    base_dir: Optional[Path] = None,
    preset: Optional[str] = None,
) -> bool:
    """Launch MP_Liquid_Visualizer. macOS uses 1094x734. Linux matches the Liquid Player. Other platforms use 700x700."""
    if sys.platform not in ("linux", "darwin", "win32") and not sys.platform.startswith("freebsd"):
        return False
    base = base_dir or get_base_dir()
    candidates = [
        base / "MP_Liquid_Visualizer.py",
        base / "scripts" / "mp_liquid_visualizer_vulkan.py",
        Path(__file__).resolve().parent / "MP_Liquid_Visualizer.py",
        Path(__file__).resolve().parent / "scripts" / "mp_liquid_visualizer_vulkan.py",
    ]
    script_path = None
    for cand in candidates:
        if cand.is_file():
            script_path = cand
            break
    if not script_path:
        return False

    cmd = [sys.executable, str(script_path)]
    if preset:
        cmd.extend(["--preset", str(preset)])

    log_dir = Path.home() / ".config" / "mix-manager"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    log_file = log_dir / "liquid_visualizer.log"

    extra_kwargs = {}
    if sys.platform == "win32":
        extra_kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        extra_kwargs["start_new_session"] = True

    try:
        with open(log_file, "a") as log_fd:
            proc = subprocess.Popen(
                cmd,
                cwd=str(base),
                stdin=subprocess.DEVNULL,
                stdout=log_fd,
                stderr=log_fd,
                close_fds=True,
                **extra_kwargs,
            )
            return proc.pid > 0
    except Exception:
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(base),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                **extra_kwargs,
            )
            return proc.pid > 0
        except Exception as exc:
            try:
                mp_log_error("MP_Audio_Player", f"Failed launching MP_Liquid_Visualizer: {exc}", exc)
            except Exception:
                pass
            return False

def _ensure_playable_path(path: str) -> bool:
    """True when `path` names a non-empty regular file that exists.

    Used before an imported playlist entry is handed to the audio engine. A
    zero-byte file is as unplayable as a missing one: opening it yields a
    decoder error that the engine reports as "end of playlist".
    """
    try:
        p = Path(str(path))
        return p.is_file() and p.stat().st_size > 0
    except (OSError, ValueError):
        return False


def _traktor_importer_module():
    """The Traktor playlist importer, loaded lazily.

    A module function rather than a direct import so the UI can be built (and
    tested) without the importer's optional dependencies.
    """
    try:
        import traktor_playlist_importer as importer
        return importer
    except ImportError:
        from scripts import traktor_playlist_importer as importer  # type: ignore
        return importer


class _TraktorPlaylistScanner:
    """Playlist source for the player after a Traktor playlist is imported.

    Presents the same two methods the player's own scanner does (`get_mixes`
    and `playlist_path`) so the rest of the UI needs no special case. Missing
    files are filtered out at construction, because a playlist generated on
    another machine routinely names tracks that are not mounted here.
    """

    def __init__(self, tracks: List[Dict[str, Any]], playlist_path: str = ""):
        self.mixes = [t for t in tracks if t.get("path") and os.path.isfile(t["path"])]
        self.playlist_path = playlist_path
        base = os.path.dirname(playlist_path) if playlist_path else ""
        self.base_dir = base or str(Path.home())

    def get_mixes(self, _query: str = "") -> List[Dict[str, Any]]:
        return list(self.mixes)


class TerminalPlayerUI:
    def __init__(self, engine: Optional[AudioEngine], scanner: Union[MixArchiveScanner, MusicFolderPlaylistScanner], is_remote: bool = False, default_style: Optional[str] = None, default_theme: Optional[str] = None, mode: str = "MIX_ARCHIVE", folder_name: str = "ALL", ui_size_mode: str = "NORMAL", keep_library: bool = False):
        self.engine, self.scanner, self.is_remote = engine, scanner, is_remote
        self.mode, self.folder_name, self.ui_size_mode = str(mode or "MIX_ARCHIVE").upper(), str(folder_name or "ALL").upper(), str(ui_size_mode or "NORMAL").upper()
        # A window opened as PORTRAIT, MINI or MICRO is a sized frame for its
        # whole life, so M always resizes it in place. Only the plain console
        # player turns M into "open a compact window", because that is what the
        # key means there. Tracking this at launch is what stops a sized window
        # from launching a second one the moment the ring passes NORMAL.
        self._sized_frame_at_launch = self.ui_size_mode != "NORMAL"
        self.keep_library = bool(keep_library)
        # Set when the user presses [1] or [2]. Stale daemon status must not
        # put the other library back until that daemon has caught up.
        # A launch that named its library (--mix-archive-mode or --music-mode)
        # pins it from the start: a daemon still playing the other library must
        # not drag the window across on the first status poll.
        self._user_library_mode = str(self.mode) if str(self.mode).upper() in ("MIX_ARCHIVE", "MUSIC") else None
        self.visualizer = EqualizerVisualizer(default_style=default_style, default_theme=default_theme)
        self.eq_manager = getattr(engine, "eq_manager", None) or EqualizerManager()
        self.bass_manager = getattr(engine, "bass_manager", None) or BassBoostManager()
        self.equalizer_hud_active = False
        self.eq_selected_band = 0
        self.eq_naming_mode = False
        self.eq_custom_name_input = ""
        self.running, self.inspector_hud_active = True, False
        self.search_mode, self.search_query, self.export_mode, self.export_path = False, "", False, ""
        self._playlist_clear_armed = False
        # Favorites clear decision window, DJ export and Traktor playlist prompts.
        self.favorites_clear_confirm = False
        self.fav_export_mode = ""
        self.fav_export_dest = ""
        self.fav_export_name = ""
        # Traktor playlist import picker (Shift+T).
        self.traktor_picker_active = False
        self.traktor_picker_rows: List[Dict[str, Any]] = []
        self.traktor_picker_row = 0
        self.traktor_picker_error = ""
        self.traktor_offer_paths: Optional[List[str]] = None
        self.traktor_offer_name = ""
        self.traktor_input_mode = ""
        self.traktor_playlist_name = ""
        self.traktor_history_id = ""
        self.favorites = FavoritesStore()
        self.mp_visualizer_on = False
        self.mp_visualizer_preset_id = 0
        if os.environ.get("MP_LIQUID_GLASS") == "1":
            try:
                loaded = _mp_visualizer_api().read_player_visualizer_state()
                self.mp_visualizer_on = bool(loaded.get("active"))
                self.mp_visualizer_preset_id = int(loaded.get("preset_id") or 0)
            except Exception:
                pass
        self._inbuf = b""
        self._reset_click_targets()
        # Playlist double-click state. Kept off _reset_click_targets, which runs
        # every frame, so a second click can still match a first from one frame
        # ago. A row drag reuses _playlist_drag_index, so a drag never plays.
        self._last_playlist_click_at = 0.0
        self._last_playlist_click_row = -1
        self._last_playlist_click_played = False
        # Timeline and pitch clicks show their new value before the daemon
        # echoes it back. Each entry is (value, monotonic expiry).
        self._pending_seek = None
        self._pending_pitch = None
        # "timeline" or "pitch" while a press on one of them is scrubbing.
        self._transport_drag = ""
        self.selected_row, self.scroll_offset, self.notification_msg, self.notification_time = 0, 0, "", 0.0
        # True while the visible notice is the "Color Theme: <name>" line, which
        # is drawn in white whatever the active theme is.
        self.notification_is_theme = False
        self._spek_job_running = False
        self._last_seen_bt_device = self._last_seen_bt_state = self._duration_queue = None
        self._duration_cache: Dict[str, str] = {}
        if DURATION_CACHE_FILE.exists():
            try:
                if isinstance(ld := json.loads(DURATION_CACHE_FILE.read_text(encoding="utf-8")), dict): self._duration_cache.update(ld)
            except Exception: pass
        self._duration_in_queue, self._duration_worker_started = set(self._duration_cache.keys()), False
        # Seed the first frame from the saved resume state so the now-playing row
        # shows the real track straight away instead of "Loading..." until the
        # playback engine writes its first status file.
        self.cached_status = {
            "title": "Loading...", "path": "", "state": "stopped", "position_fmt": "00:00", "duration_fmt": "00:00",
            "progress_pct": 0.0, "volume": 85, "muted": False, "shuffle": False, "repeat": "all", "mode": self.mode,
            "recording": False, "recording_file": "", "recording_bytes": 0, "recording_duration": 0.0,
            "levels": [0.0]*24, "peaks": [0.0]*24, "levels_left": [0.0]*24, "levels_right": [0.0]*24,
            "peaks_left": [0.0]*24, "peaks_right": [0.0]*24, "waveform": [0.0]*80, "rms_level": 0.0, "peak_db": -60.0
        }
        if self.is_remote:
            self._seed_status_from_resume_state()
        try:
            script_reward_path = Path(__file__).resolve().parent / "scripts"
            if str(script_reward_path) not in sys.path:
                sys.path.insert(0, str(script_reward_path))
            from liquid_glass_reward import is_welcomed, ensure_first_run_reward, setup_first_run_equalizer
            setup_first_run_equalizer()
            if not is_welcomed():
                ensure_first_run_reward(interactive=False)
                self.notification_msg = "✦ Special Reward from MPlanetarian: Liquid Glass Icon Unlocked on Desktop!"
                self.notification_time = time.time() + 6.0
        except Exception:
            pass

    def _seed_status_from_resume_state(self):
        """Fill the first frame from the resume file, when it is usable.

        The status file is written by the playback engine, which may still be
        starting. Reading the resume state here keeps the now-playing row from
        showing "Loading..." in between.
        """
        try:
            if not RESUME_STATE_FILE.is_file():
                return
            saved = json.loads(RESUME_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(saved, dict):
            return
        path = str(saved.get("path") or "")
        if not path:
            return
        title = str(saved.get("title") or os.path.basename(path))
        duration_sec = float(saved.get("duration_sec", 0.0) or 0.0)
        position_sec = float(saved.get("position_sec", 0.0) or 0.0)
        self.cached_status.update(
            title=title, path=path, duration_sec=duration_sec, duration_fmt=format_seconds(duration_sec),
            position_fmt=format_seconds(position_sec),
            progress_pct=round(position_sec / duration_sec * 100.0, 1) if duration_sec > 0 else 0.0,
        )
        for index, mix in enumerate(getattr(self.scanner, "mixes", []) or []):
            if str(mix.get("path")) == path:
                self.selected_row = index
                break

    def _save_duration_cache(self):
        try:
            tmp = DURATION_CACHE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._duration_cache), encoding="utf-8")
            tmp.replace(DURATION_CACHE_FILE)
        except Exception: pass

    def _start_duration_worker(self):
        if self._duration_worker_started: return
        self._duration_worker_started, self._duration_queue = True, queue.Queue()
        def _worker():
            _ensure_audio_backends()
            last_save = time.time()
            while self.running:
                try:
                    if not self._duration_queue: break
                    path, m_ref = self._duration_queue.get(timeout=0.4)
                except Exception: continue
                if path in self._duration_cache: continue
                if self.engine and getattr(self.engine, "state", "") == "playing" and is_cloud_or_remote_path(path): time.sleep(0.08)
                try:
                    if sf is not None and os.path.isfile(path):
                        if is_cloud_or_remote_path(path): time.sleep(0.12)
                        info = sf.info(path)
                        duration = sane_duration(getattr(info, "duration", 0.0))
                        dfmt = format_seconds(duration)
                        self._duration_cache[path] = dfmt
                        if isinstance(m_ref, dict): m_ref.update(duration_fmt=dfmt, duration_sec=duration)
                        if time.time() - last_save > 5.0: self._save_duration_cache(); last_save = time.time()
                    else: self._duration_cache[path] = "00:00"
                except Exception: self._duration_cache[path] = "00:00"
                finally:
                    if self._duration_queue: self._duration_queue.task_done()
            self._save_duration_cache()
        threading.Thread(target=_worker, daemon=True).start()

    def _request_duration(self, m: Dict[str, Any]):
        if not (p := m.get("path")) or p in self._duration_cache or p in self._duration_in_queue: return
        if not self._duration_worker_started: self._start_duration_worker()
        self._duration_in_queue.add(p)
        if self._duration_queue: self._duration_queue.put((p, m))

    def _library_config_message(self, target_mode: str) -> str:
        if target_mode == "MUSIC":
            return (
                "Music playlist is not ready. Configure Music folders with main menu option 2. "
                "Mix Archive folders use option 13."
            )
        return (
            "Mix Archive playlist is not ready. Configure Mix Archive folders with main menu option 13. "
            "Music folders use option 2."
        )

    def _switch_library_mode(self, target_mode: str):
        """Reload the Mix Archive or Music playlist already generated for that mode.

        [1] loads the Mix Archive listing. [2] loads the Music folder playlist.
        A missing playlist stays on the current mode and names main-menu options
        13 (Mix Archive folders) and 2 (Music folders).
        """
        target = str(target_mode or "").upper()
        if target not in ("MUSIC", "MIX_ARCHIVE"):
            return
        if target == "MUSIC":
            scanner = MusicFolderPlaylistScanner(folder_name=self.folder_name or "ALL")
            playlist_file = getattr(scanner, "playlist_path", None)
            ready = bool(getattr(scanner, "mixes", None)) and bool(playlist_file and Path(playlist_file).is_file())
        else:
            base = getattr(self.scanner, "base_dir", None) or get_base_dir()
            scanner = MixArchiveScanner(base)
            ready = bool(getattr(scanner, "mixes", None))
        if not ready:
            self._notify(self._library_config_message(target), 6.0)
            return
        self.mode = target
        self.scanner = scanner
        self.keep_library = target == "MUSIC"
        self._user_library_mode = target
        self.search_mode = False
        self.search_query = ""
        self.selected_row = 0
        self.scroll_offset = 0
        mixes = list(scanner.mixes)
        if target == "MUSIC":
            self.folder_name = str(getattr(scanner, "folder_name", self.folder_name) or "ALL").upper()
        if self.is_remote:
            kwargs = {"mode": target, "play": False}
            if target == "MUSIC":
                kwargs["folder_name"] = self.folder_name
                playlist_path = str(getattr(scanner, "playlist_path", "") or "")
                if playlist_path:
                    kwargs["playlist_path"] = playlist_path
            self._send_cmd("load_playlist", **kwargs)
            if isinstance(self.cached_status, dict):
                self.cached_status["mode"] = target
        elif self.engine:
            current = ""
            if getattr(self.engine, "current_track", None):
                current = self.engine.current_track.get("path") or ""
            self.engine.playlist = mixes
            self.engine.mode = target
            match = next((i for i, track in enumerate(mixes) if track.get("path") == current), None)
            self.engine.current_index = match if match is not None else 0
        noun = "tracks" if target == "MUSIC" else "mixes"
        label = "Music" if target == "MUSIC" else "Mix Archive"
        self._notify(f"Loaded {label} playlist — {len(mixes)} {noun}", 3.5)

    def _sync_mode_scanner(self, new_mode: Optional[str]):
        if not new_mode:
            return
        incoming = str(new_mode).upper()
        pinned = getattr(self, "_user_library_mode", None)
        if pinned and incoming != pinned:
            return
        # Option 2 opens this window on a music library. The mix daemon's status
        # must not replace that library with the mix archive playlist.
        if self.keep_library and self.mode == "MUSIC":
            return
        nm = incoming
        if nm != self.mode:
            self.mode = nm
        if self.mode == "FAVORITES" and not isinstance(self.scanner, FavoritesScanner):
            self.scanner = FavoritesScanner(self._favorites_store())
        elif self.mode == "MUSIC" and isinstance(self.scanner, MixArchiveScanner):
            self.scanner = MusicFolderPlaylistScanner(folder_name=self.folder_name)
        elif self.mode == "MIX_ARCHIVE" and isinstance(self.scanner, MusicFolderPlaylistScanner):
            self.scanner = MixArchiveScanner(getattr(self.scanner, "base_dir", None) or get_base_dir())

    def _get_status(self) -> Dict[str, Any]:
        if self.is_remote:
            if STATE_FILE.exists():
                try:
                    data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and data.get("title") and data.get("title") != "Loading...": self.cached_status = data
                except Exception: pass
            elif not self.cached_status.get("title") or self.cached_status.get("title") == "Loading...":
                if (res := send_ipc_command("status")) and isinstance(res.get("data"), dict): self.cached_status = res["data"]
            if self.cached_status.get("mode"): self._sync_mode_scanner(self.cached_status.get("mode"))
            self._sync_eq_from_status(self.cached_status)
            return self._apply_pending_status(self.cached_status)
        st = self.engine.get_status_dict(live=True) if self.engine else self.cached_status
        if st.get("mode"): self._sync_mode_scanner(st.get("mode"))
        self._sync_eq_from_status(st)
        return self._apply_pending_status(st)

    def _sync_eq_from_status(self, st: Dict[str, Any]) -> None:
        """Adopt the daemon's equaliser without erasing a local edit.

        The player writes each band change to the daemon, but the status file
        lags a frame or two. Adopting it unconditionally meant the value just
        set was overwritten by the stale one on the very next draw, so the deck
        equaliser looked frozen: the arrow keys moved a band for a few
        milliseconds and the old number came straight back.

        A value this window has set is therefore remembered until the daemon
        reports it (or the overlay expires, when the daemon was not listening),
        exactly like the pitch and seek overlays above. Nothing is blocked for
        longer than that, so another window's change still arrives.
        """
        now = time.monotonic()
        preset = st.get("eq_preset")
        if preset:
            self.eq_manager.active_preset = str(preset)
        bands = st.get("eq_bands")
        overlay = getattr(self, "_pending_eq_bands", None)
        if overlay is not None:
            values, expires = overlay
            if now >= expires or not isinstance(bands, list) or len(bands) != len(values) \
                    or all(abs(float(a) - float(b)) < 0.05 for a, b in zip(bands, values)):
                # The daemon has caught up (or stopped listening): drop the
                # overlay and take whatever it reports from here on.
                self._pending_eq_bands = None
            else:
                self.eq_manager.current_bands = list(values)
                return
        if isinstance(bands, list) and len(bands) == 10:
            self.eq_manager.current_bands = [float(x) for x in bands]

    def _apply_pending_status(self, st: Dict[str, Any]) -> Dict[str, Any]:
        """Overlay a click the daemon has not echoed back yet.

        A timeline or pitch click is sent to the player daemon over IPC, and the
        first status that comes back can still be the pre-click one. Painting
        that frame would snap the knob back to its old value for a moment. The
        pending value is shown until the daemon agrees, or for two seconds, so
        the click always looks instant.
        """
        now = time.monotonic()
        pitch = getattr(self, "_pending_pitch", None)
        if pitch is not None:
            value, expires = pitch
            if now >= expires or int(st.get("pitch_percent", 0) or 0) == value:
                self._pending_pitch = None
            else:
                st["pitch_percent"] = value
        seek = getattr(self, "_pending_seek", None)
        if seek is not None:
            position, expires = seek
            duration = float(st.get("duration_sec", 0.0) or 0.0)
            reported = float(st.get("position_sec", 0.0) or 0.0)
            if now >= expires or duration <= 0.0 or abs(reported - position) < 1.0:
                self._pending_seek = None
            else:
                st["position_sec"] = position
                st["position_fmt"] = format_seconds(position)
                st["progress_pct"] = round(position / duration * 100.0, 1)
        return st

    def _sync_initial_selection(self, status: Dict[str, Any]):
        if (cp := status.get("path")) and cp != getattr(self, "_last_path", None):
            self._last_path = cp
            if not self.search_query:
                for i, m in enumerate(self._get_active_tracks()):
                    if m["path"] == cp: self.selected_row = i; break

    def _remember_eq_bands(self, expires: float = 3.0) -> None:
        """Hold the bands this window just set until the daemon echoes them."""
        self._pending_eq_bands = (list(self.eq_manager.current_bands),
                                  time.monotonic() + float(expires))

    def _send_cmd(self, cmd: str, **kwargs):
        # A timeline or pitch click must paint on the very next frame, before
        # the daemon has had time to write the new value to its state file.
        # Remember the value the click resolved to, and overlay it in
        # `_get_status` until the daemon reports the same number (or this
        # overlay expires), so the knob and the clock move immediately without
        # fighting the daemon's own status.
        if cmd == "set_pitch":
            self._pending_pitch = (max(-20, min(20, int(kwargs.get("percent", 0) or 0))),
                                   time.monotonic() + 2.0)
        elif cmd == "seek_to":
            self._pending_seek = (float(kwargs.get("position", 0.0) or 0.0),
                                  time.monotonic() + 2.0)
        if self.is_remote:
            threading.Thread(target=send_ipc_command, args=(cmd,), kwargs=kwargs, daemon=True).start()
        elif self.engine:
            e = self.engine
            mapping = {"toggle": e.toggle_play_pause, "pause": e.pause, "next": e.next_track, "prev": e.prev_track, "reset_pitch": e.reset_pitch, "mute": e.toggle_mute, "shuffle": e.toggle_shuffle, "repeat": e.cycle_repeat, "stop": e.stop_playback, "record": e.toggle_recording, "toggle_record": e.toggle_recording, "start_record": e.start_recording, "stop_record": e.stop_recording}
            if cmd in mapping: mapping[cmd]()
            elif cmd == "play":
                if f := kwargs.get("file"):
                    for i, t in enumerate(e.playlist):
                        if t["path"] == f: e.load_track(i, True); break
                    else:
                        e.playlist.insert(0, {"name": os.path.basename(f), "path": f, "size_mb": 0, "mtime": time.time(), "ext": os.path.splitext(f)[1][1:].upper()})
                        e.load_track(0, True)
                else: e.play()
            elif cmd == "seek": e.seek_relative(kwargs.get("seconds", 10.0))
            elif cmd == "seek_to":
                # A timeline click is "play from here": seek, then make sure
                # the transport is running instead of leaving it paused.
                e.seek(float(kwargs.get("position", 0.0)), force=True)
                if kwargs.get("resume") and e.state != "playing":
                    e.play()
            elif cmd == "vol_up": e.change_volume(kwargs.get("step", 5))
            elif cmd == "vol_down": e.change_volume(-kwargs.get("step", 5))
            elif cmd == "pitch_up": e.change_pitch(kwargs.get("step", 1))
            elif cmd == "pitch_down": e.change_pitch(-kwargs.get("step", 1))
            elif cmd == "set_pitch": e.set_pitch(kwargs.get("percent", 0))
            elif cmd == "select_index": e.load_track(kwargs.get("index", 0), True)
            elif cmd == "queue":
                if f := (kwargs.get("file") or kwargs.get("track")): e.queue_track(f)
            elif cmd == "clear_queue": e.clear_queue()
            elif cmd == "clear_playlist": e.clear_playlist()
            elif cmd == "open_file_manager":
                if target := choose_file_manager_target(kwargs.get("file") or (e.current_track.get("path") if e.current_track else "")):
                    open_in_file_manager(target)

    def _get_active_tracks(self) -> List[Dict[str, Any]]: return self.scanner.get_mixes(self.search_query)

    def _render_inspector_hud(self, st: Optional[Dict[str, Any]] = None, iw: Optional[int] = None) -> List[str]:
        if st is None: st = self._get_status()
        if iw is None:
            w, _ = _win_visible_size((80, 24))
            iw = max(20, w - 6)
        tp = st.get("path", "")
        tname = st.get("title", os.path.basename(tp))
        folder = next((m.get("folder") for m in getattr(self.scanner, "mixes", []) if m.get("path") == tp and m.get("folder")), None) or self.folder_name
        is_net = tp.startswith(("\\\\", "//")) or is_cloud_or_remote_path(tp)
        msl = max(20, iw - 22)
        dt = (tname[:msl-3] + "...") if len(tname) > msl else tname
        ds = (tp[:msl-3] + "...") if len(tp) > msl else tp
        ext, sr, ch = os.path.splitext(tp)[1][1:].upper() or "FLAC", st.get("sample_rate", 44100), st.get("channels", 2)
        ch_s = "Stereo" if ch == 2 else ("Mono" if ch == 1 else f"{ch} Channels")
        b_col = f"{BOLD}{self.visualizer.get_theme_palette().get('border', COLOR_HOT_PURPLE)}"
        lines = [
            _boxed_rule("╭", "── ✦ TRACK AUDIO INSPECTOR (METADATA & HARDWARE HUD) ", "╮", iw, b_col),
        ]
        info_items = [
            (f" {BOLD}{WHITE}Track Title{NC}", dt),
            (f" {BOLD}{CYAN}Folder / Src{NC}", f"{'SMB Share' if is_net else 'Local Drive'} [{folder}] • Music Library"),
            (f" {BOLD}{CYAN}Format/Codec{NC}", f"{ext} {'Lossless' if is_lossless_path(tp) else 'Encoded'} • {sr/1000:.1f} kHz • {ch_s}"),
            (f" {BOLD}{CYAN}Time Position{NC}", f"{st.get('position_fmt','00:00')} / {st.get('duration_fmt','00:00')} ({st.get('progress_pct',0):.1f}%)"),
            (f" {BOLD}{CYAN}Volume/Pitch{NC}", f"Volume: {st.get('volume', 85)}%  │  Pitch: {st.get('pitch_percent', 0):+d}%"),
            (f" {BOLD}{CYAN}Audio Engine{NC}", f"Output Rate: {st.get('output_rate', 44100)/1000:.1f} kHz • Device: {st.get('output_device', 'Default Audio Device')}"),
            (f" {BOLD}{CYAN}{'Network Path' if is_net else 'File Path   '}{NC}", ds),
        ]
        for label, val in info_items:
            row_str = f"{label}  : {val}"
            pad = max(0, iw - _display_width(row_str))
            lines.append(f"  {b_col}│{NC}{row_str}{' ' * pad}{b_col}│{NC}")
        lines.append(_boxed_rule("╰", "── [G] Generate Spek • Press [I], [Esc], or [Enter] to Close ", "╯", iw, b_col))
        return lines

    def _render_equalizer_hud(self, st: Optional[Dict[str, Any]] = None, iw: Optional[int] = None, h: int = 24) -> List[str]:
        if st is None: st = self._get_status()
        if iw is None:
            w, _ = _win_visible_size((80, 24))
            iw = max(20, w - 6)

        cur_p = self.eq_manager.active_preset
        gains = self.eq_manager.current_bands
        sel_idx = self.eq_selected_band
        presets = self.eq_manager.get_all_preset_names()

        pal = self.visualizer.get_theme_palette()
        hl_col = pal.get("highlight", COLOR_HOT_PURPLE)
        act_col = pal.get("active", COLOR_NEON_PINK)
        b_col = f"{BOLD}{pal.get('border', COLOR_HOT_PURPLE)}"
        text_col = theme_body_ansi(self.visualizer.theme) if os.environ.get("MP_LIQUID_GLASS") else theme_console_body_ansi(self.visualizer.theme)

        lines = []
        lines.append(_boxed_rule("╭", "── ✦ 10-BAND STEREO GRAPHIC EQUALIZER & PRESETS ──", "╮", iw, b_col, text_col))

        p_badge = f"{BOLD}{COLOR_TRAFFIC_GREEN}[{cur_p}]{NC}" if cur_p != "Flat" else f"{BOLD}{COLOR_ELECTRIC_CYAN}[Flat (Default)]{NC}"
        sel_freq = EQ_BAND_LABELS[sel_idx]
        sel_gain = gains[sel_idx]
        status_info = f" Active: {p_badge}  •  Bass: {bass_mode_badge(self._bass_mode())}  •  Selected Band: {BOLD}{COLOR_NEON_PINK}{sel_idx+1}/10 ({sel_freq}: {sel_gain:+4.1f} dB){NC}  •  Scale: -12.0 to +12.0 dB"
        sh_info = status_info if _display_width(status_info) <= iw else _clip_display(status_info, iw)
        pad = max(0, iw - _display_width(sh_info))
        lines.append(f"  {b_col}│{NC}{sh_info}{' ' * pad}{b_col}│{NC}")

        pill_parts = []
        for i, pname in enumerate(presets[:9]):
            pnum = f"{i+1}:" if i < 7 else ""
            if pname == cur_p:
                pill_parts.append(f"{_glass_rev()}{BOLD}{COLOR_NEON_PINK} {pnum}{pname} \033[0m")
            else:
                pill_parts.append(f"{COLOR_ELECTRIC_CYAN}[{pnum}{pname}]{NC}")
        preset_row = f" Presets: " + " ".join(pill_parts)
        sh_preset = preset_row if _display_width(preset_row) <= iw else _clip_display(preset_row, iw)
        pad = max(0, iw - _display_width(sh_preset))
        lines.append(f"  {b_col}│{NC}{sh_preset}{' ' * pad}{b_col}│{NC}")
        lines.append(_boxed_rule("├", "─" * 16, "┤", iw, b_col))

        if h >= 32:
            scale_dbs = [12, 10, 8, 6, 4, 2, 0, -2, -4, -6, -8, -10, -12]
        elif h >= 26:
            scale_dbs = [12, 9, 6, 3, 0, -3, -6, -9, -12]
        else:
            scale_dbs = [12, 8, 4, 0, -4, -8, -12]

        def gain_to_row(g):
            clamped = max(-12.0, min(12.0, float(g)))
            frac = (12.0 - clamped) / 24.0
            return int(round(frac * (len(scale_dbs) - 1)))

        thumb_rows = [gain_to_row(g) for g in gains]
        avail_w = max(60, iw - 10)
        base_bw = avail_w // 10
        rem = avail_w % 10
        band_widths = [base_bw + (1 if i < rem else 0) for i in range(10)]

        for r_idx, db in enumerate(scale_dbs):
            is_zero = (db == 0)
            prefix = f"  {BOLD}{COLOR_ELECTRIC_CYAN}0 dB ├─{NC}" if is_zero else f" {DIM}{CYAN}{db:+3d} dB │{NC}"

            cells = []
            for c_idx, tr in enumerate(thumb_rows):
                is_sel = (c_idx == sel_idx)
                bw = band_widths[c_idx]
                if tr == r_idx:
                    g_val = gains[c_idx]
                    t_col = COLOR_TRAFFIC_GREEN if g_val > 0 else (COLOR_TRAFFIC_AMBER if g_val < 0 else COLOR_ELECTRIC_CYAN)
                    thumb = f"{_glass_rev()}{BOLD}{COLOR_NEON_PINK}[█]{NC}\033[0m" if is_sel else f"{BOLD}{t_col}█{NC}"
                    tw = 3 if is_sel else 1
                    pad_tot = bw - tw
                    pl = pad_tot // 2
                    pr = pad_tot - pl
                    cell = (("─" * pl) + thumb + ("─" * pr)) if is_zero else ((" " * pl) + thumb + (" " * pr))
                else:
                    pad_tot = bw - 1
                    pl = pad_tot // 2
                    pr = pad_tot - pl
                    if is_zero:
                        cell = ("─" * pl) + f"{COLOR_ELECTRIC_CYAN}┼{NC}" + ("─" * pr)
                    else:
                        tr_ch = f"{COLOR_NEON_PINK}┆{NC}" if is_sel else f"{DIM}│{NC}"
                        cell = (" " * pl) + tr_ch + (" " * pr)
                cells.append(cell)

            sep = f"{COLOR_ELECTRIC_CYAN}┤{NC}" if is_zero else f"{DIM}│{NC}"
            row_content = prefix + "".join(cells) + sep
            pad = max(0, iw - _display_width(row_content))
            lines.append(f"  {b_col}│{NC}{row_content}{' ' * pad}{b_col}│{NC}")

        f_prefix = f"  {DIM}Freq: │{NC}"
        freq_cells = []
        for i, fl in enumerate(EQ_BAND_LABELS):
            bw = band_widths[i]
            fl_padded = f"{fl:^{bw}s}"
            freq_cells.append(f"{_glass_rev()}{BOLD}{COLOR_NEON_PINK}{fl_padded}\033[0m" if i == sel_idx else f"{BOLD}{COLOR_LILAC}{fl_padded}{NC}")
        f_row = f_prefix + "".join(freq_cells) + f"{DIM}│{NC}"
        pad = max(0, iw - _display_width(f_row))
        lines.append(f"  {b_col}│{NC}{f_row}{' ' * pad}{b_col}│{NC}")

        g_prefix = f"  {DIM}Gain: │{NC}"
        gain_cells = []
        for i, g in enumerate(gains):
            bw = band_widths[i]
            g_str = f"{g:+4.1f}"
            g_padded = f"{g_str:^{bw}s}"
            if i == sel_idx:
                gain_cells.append(f"{_glass_rev()}{BOLD}{COLOR_ELECTRIC_CYAN}{g_padded}\033[0m")
            elif abs(g) > 0.05:
                gain_cells.append(f"{COLOR_TRAFFIC_GREEN if g > 0 else COLOR_TRAFFIC_AMBER}{g_padded}{NC}")
            else:
                gain_cells.append(f"{DIM}{g_padded}{NC}")
        g_row = g_prefix + "".join(gain_cells) + f"{DIM}│{NC}"
        pad = max(0, iw - _display_width(g_row))
        lines.append(f"  {b_col}│{NC}{g_row}{' ' * pad}{b_col}│{NC}")

        c_prefix = "        │"
        cursor_cells = []
        for i in range(10):
            bw = band_widths[i]
            if i == sel_idx:
                cur_padded = f"{'▲':^{bw}s}"
                cursor_cells.append(f"{BOLD}{COLOR_NEON_PINK}{cur_padded}{NC}")
            else:
                cursor_cells.append(" " * bw)
        c_row = c_prefix + "".join(cursor_cells) + f"{DIM}│{NC}"
        pad = max(0, iw - _display_width(c_row))
        lines.append(f"  {b_col}│{NC}{c_row}{' ' * pad}{b_col}│{NC}")

        if self.eq_naming_mode:
            save_prompt = f" {BOLD}{COLOR_TRAFFIC_AMBER}💾 Name Custom Preset: [{self.eq_custom_name_input}█]  (Enter: Save, Esc: Cancel){NC}"
            sh_save = save_prompt if _display_width(save_prompt) <= iw else _clip_display(save_prompt, iw)
            pad = max(0, iw - _display_width(sh_save))
            lines.append(f"  {b_col}│{NC}{sh_save}{' ' * pad}{b_col}│{NC}")
        lines.append(_boxed_rule("╰", "── [←/→] Band • [↑/↓] Slider (±0.5dB) • [P/Tab] Preset • [1-7] Preset • [S] Save • [R] Flat • [b] Bass • [E/Esc] Close ", "╯", iw, b_col, text_col))
        return lines

    def _get_target_path(self) -> Tuple[Optional[str], Optional[str]]:
        mixes = self._get_active_tracks()
        if mixes and 0 <= self.selected_row < len(mixes): return mixes[self.selected_row].get("path"), mixes[self.selected_row].get("name")
        st = self._get_status()
        if st.get("path"): return st.get("path"), st.get("title")
        latest = self.scanner.get_latest_mix()
        return (latest["path"], latest["name"]) if latest else (None, None)

    def _notify(self, msg: str, dur: float = 3.0):
        self.notification_msg, self.notification_time = msg, time.time() + dur
        self.notification_is_theme = False

    def _notify_theme(self, dur: float = 2.5):
        """The "Color Theme: <name>" notice. Always drawn in white.

        It sits just below the equaliser. The theme accent washed out on the
        light panes, and black disappeared on the dark consoles, so the name
        is white on a dark chip instead of following the theme. render is the
        only caller, so the flag is set here rather than re-derived from the
        message text.
        """
        self._notify(f"Color Theme: {self.visualizer.get_theme_display_name()}", dur)
        self.notification_is_theme = True

    def _notification_ansi(self, bold_s: str) -> str:
        """Colour prefix for the current notification line."""
        if getattr(self, "notification_is_theme", False):
            return f"{bold_s}{theme_notice_ansi(self.visualizer.theme)}"
        return f"{bold_s}{COLOR_TRAFFIC_GREEN}"

    def _entry_noun(self) -> str:
        if self.mode == "MUSIC":
            return "tracks"
        if self.mode == "FAVORITES":
            return "favorites"
        return "mixes"

    def _favorites_store(self) -> FavoritesStore:
        store = getattr(self, "favorites", None)
        if store is None:
            store = FavoritesStore()
            self.favorites = store
        return store

    def _reset_click_targets(self):
        self._favorites_button_row = 0
        self._favorites_button_span = (0, 0)
        self._favorites_button_whole_row = False
        self._fav_export_button = (0, 0, 0)
        self._visualizer_button_row = 0
        self._visualizer_button_span = (0, 0)
        self._bass_button_row = 0
        self._bass_button_span = (0, 0)
        self._export_tracks_row = 0
        self._export_tracks_span = (0, 0)
        self._playlist_star_rows = {}
        self._playlist_star_col = 14
        # The transport timeline and the DJ pitch track are click targets too.
        # Row is 1-based, span is an inclusive 1-based (first, last) column pair.
        self._timeline_row = 0
        self._timeline_span = (0, 0)
        self._pitch_row = 0
        self._pitch_span = (0, 0)

    def _remap_click_rows(self, plan: Optional[Tuple[int, int]], total: int):
        """Move recorded click rows into the frame `_fit_frame` actually paints.

        Every target below is recorded while the frame is being built, before
        the renderer trims the middle band to the console height. Without this,
        a short window dropped a few equaliser rows and every click landed that
        many lines off. A target whose row is dropped entirely is cleared, so a
        click in the freed space cannot trigger something that is not painted.
        """
        if plan is None:
            return

        def _move(row: int) -> int:
            return _fit_frame_row(int(row) - 1, plan, total) if row else 0

        self._favorites_button_row = _move(self._favorites_button_row)
        self._visualizer_button_row = _move(self._visualizer_button_row)
        self._bass_button_row = _move(self._bass_button_row)
        self._export_tracks_row = _move(self._export_tracks_row)
        self._timeline_row = _move(self._timeline_row)
        self._pitch_row = _move(self._pitch_row)
        fr, fs, fe = self._fav_export_button
        if fr:
            nr = _move(fr)
            self._fav_export_button = (nr, fs, fe) if nr else (0, 0, 0)
        if self._playlist_star_rows:
            moved = {}
            for row, index in self._playlist_star_rows.items():
                new_row = _move(row)
                if new_row:
                    moved[new_row] = index
            self._playlist_star_rows = moved

    def _arm_visualizer_text(self):
        global _MP_VISUALIZER_TEXT
        _MP_VISUALIZER_TEXT = bool(getattr(self, "mp_visualizer_on", False) and os.environ.get("MP_LIQUID_GLASS") == "1")

    def _favorite_target(self) -> Optional[Dict[str, Any]]:
        mixes = self._get_active_tracks()
        if mixes and 0 <= self.selected_row < len(mixes):
            track = dict(mixes[self.selected_row])
        else:
            st = self._get_status()
            path = st.get("path") or ""
            if not path:
                return None
            track = {"name": st.get("title") or os.path.basename(path), "path": path}
        if not track.get("path"):
            return None
        if not track.get("ext"):
            track["ext"] = os.path.splitext(str(track.get("path") or ""))[1].lstrip(".").upper()
        track.setdefault("source", self.mode)
        return track

    def _sync_open_favorites(self):
        store = self._favorites_store()
        scanner = self.scanner if isinstance(self.scanner, FavoritesScanner) else FavoritesScanner(store)
        scanner.store = store
        scanner.reload()
        self.scanner = scanner
        mixes = list(scanner.mixes)
        self.selected_row = min(self.selected_row, max(0, len(mixes) - 1)) if mixes else 0
        self.scroll_offset = min(self.scroll_offset, self.selected_row)
        if self.is_remote:
            self._send_cmd("load_playlist", mode="FAVORITES", play=False)
            if isinstance(self.cached_status, dict):
                self.cached_status["mode"] = "FAVORITES"
                self.cached_status["total_tracks"] = len(mixes)
        elif self.engine:
            current = ""
            track = getattr(self.engine, "current_track", None)
            if isinstance(track, dict):
                current = track.get("path") or ""
            self.engine.playlist = mixes
            self.engine.mode = "FAVORITES"
            match = next((i for i, row in enumerate(mixes) if row.get("path") == current), None)
            self.engine.current_index = match if match is not None else 0

    # ------------------------------------------------------------------
    # TRAKTOR PLAYLIST IMPORT (Shift+T)
    # ------------------------------------------------------------------
    def _traktor_picker_open(self) -> None:
        """Open the playlist picker, or explain why it cannot open.

        Only playlists that were generated in advance are offered: the importer
        writes them into PLAYLISTS_GENERATED with an .m3u8 beside each .json,
        and anything without both files is not usable here.
        """
        if os.environ.get("MP_LIQUID_GLASS") == "1":
            self._notify("Traktor playlist import is available in CLI mode only.", 3.5)
            return
        importer = _traktor_importer_module()
        try:
            ready, reason = importer.music_folders_ready()
        except Exception as exc:
            ready, reason = False, str(exc)
        if not ready:
            self.traktor_picker_active = True
            self.traktor_picker_rows = []
            self.traktor_picker_row = 0
            self.traktor_picker_error = reason or "Music Folders are not configured."
            return
        try:
            rows = importer.generated_playlists()
        except Exception as exc:
            rows = []
            self.traktor_picker_error = str(exc)
        else:
            self.traktor_picker_error = "" if rows else (
                "No generated Traktor playlists were found. "
                "Use music_folders_manager.sh to generate them first."
            )
        self.traktor_picker_rows = list(rows)
        self.traktor_picker_row = 0
        self.traktor_picker_active = True

    def _traktor_import_start(self) -> None:
        """Hand off to the importer's interactive flow, after the preflight.

        The importer needs the Music Folders configuration to resolve the
        Traktor collection; running it without that produces a confusing
        failure deep inside, so it is checked here and reported plainly.
        """
        importer = _traktor_importer_module()
        try:
            ready, reason = importer.music_folders_ready()
        except Exception as exc:
            ready, reason = False, str(exc)
        if not ready:
            self._notify(reason or "Music Folders are not configured.", 4.0)
            return
        try:
            importer.interactive_main()
        except Exception as exc:
            self._notify(f"Traktor import failed: {exc}", 4.5)
            return
        self._notify("Traktor import finished.", 3.0)

    def _traktor_picker_close(self) -> None:
        self.traktor_picker_active = False

    def _traktor_picker_move(self, delta: int) -> None:
        if not self.traktor_picker_rows:
            return
        self.traktor_picker_row = max(
            0, min(len(self.traktor_picker_rows) - 1, self.traktor_picker_row + delta)
        )

    def _traktor_picker_activate(self) -> None:
        """Load the highlighted playlist, dropping tracks that are not on disk.

        A generated playlist can name files on a drive that is not mounted any
        more, so every path is checked before it reaches the engine. The count
        of unavailable tracks is reported rather than silently swallowed.
        """
        self.traktor_picker_active = False
        if not self.traktor_picker_rows:
            return
        row = self.traktor_picker_rows[
            max(0, min(self.traktor_picker_row, len(self.traktor_picker_rows) - 1))
        ]
        json_path = str(row.get("json_path", ""))
        tracks: List[Dict[str, Any]] = []
        try:
            data = json.loads(Path(json_path).read_text(encoding="utf-8"))
            raw = data.get("tracks") if isinstance(data, dict) else data
            if isinstance(raw, list):
                tracks = [t for t in raw if isinstance(t, dict)]
        except (OSError, ValueError):
            tracks = []
        available = [t for t in tracks if t.get("path") and os.path.isfile(str(t["path"]))]
        missing = len(tracks) - len(available)
        if not available:
            self._notify("That playlist has no tracks available on this machine.", 4.0)
            return
        self.scanner = _TraktorPlaylistScanner(available, json_path)
        self.selected_row, self.scroll_offset = 0, 0
        name = str(row.get("name") or Path(json_path).stem)
        message = f"Loaded Traktor playlist: {name} ({len(available)} tracks)"
        if missing:
            message += f" - {missing} unavailable"
        self._notify(message, 4.5)

    def _traktor_picker_box(self, width: int) -> List[str]:
        """Draw the picker window over the playlist area."""
        rows = self.traktor_picker_rows
        lines = [
            "  TRAKTOR PLAYLIST IMPORT  ",
            f"  {len(rows)} generated playlist(s) available",
            "",
        ]
        if self.traktor_picker_error:
            lines.append(f"  {self.traktor_picker_error}")
            lines.append("  [G] pre-generate the playlists   [Esc] Cancel")
        else:
            for index, row in enumerate(rows[:12]):
                marker = "▶" if index == self.traktor_picker_row else " "
                lines.append(
                    f"  {marker} {index + 1:2d}) {row.get('name', 'playlist')}"
                    f"  ({row.get('tracks', '?')} tracks)"
                )
            lines += ["", "  [Enter] Load   [Esc] Cancel"]
        return lines

    def _toggle_hidden_mode(self):
        """Hide or restore every MP window (Hidden Mode).

        The heavy lifting lives in scripts/mp_hidden_mode.py, which uses a KWin
        script on Plasma Wayland (where the GTK hosts are native Wayland
        clients that `wmctrl` never sees) and plain X11 calls elsewhere. A
        failure is reported rather than swallowed: on a desktop this feature
        cannot support, the user needs to know the key did nothing.
        """
        try:
            script_dir = str(Path(__file__).resolve().parent / "scripts")
            if script_dir not in sys.path:
                sys.path.insert(0, script_dir)
            import mp_hidden_mode as hidden
            result = hidden.toggle()
        except Exception as exc:
            self._notify(f"Hidden Mode unavailable: {exc}", 4.0)
            return
        state = "Hidden" if result.get("hidden") else "Restored"
        detail = result.get("message") or ""
        self._notify(f"{state}. {detail}".strip(), 3.0)

    def _toggle_favorite(self):
        """Add or remove the selected mix or music track from the Favorites playlist."""
        track = self._favorite_target()
        if not track:
            self._notify("Select a mix or track, then press Shift+F to mark it as a favorite.", 3.5)
            return
        added = self._favorites_store().toggle(track)
        if self.mode == "FAVORITES":
            self._sync_open_favorites()
        name = track.get("name") or os.path.basename(str(track.get("path") or ""))
        count = len(self._favorites_store())
        if added:
            self._notify(f"Added to Favorites: {name}  ({count} saved). Press [3] or the Favorites button to open them.", 4.0)
        else:
            self._notify(f"Removed from Favorites: {name}  ({count} saved).", 3.5)

    # ------------------------------------------------------------------
    # Favorites: export for DJ set preparation and clear confirmation
    # ------------------------------------------------------------------
    def _fav_exporter_module(self):
        scripts = str(Path(__file__).resolve().parent / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import MP_Fav_Playlist_Track_Exporter as exporter
        return exporter

    def _favorites_decision_active(self) -> bool:
        self._ensure_favorites_decision_state()
        return bool(
            self.favorites_clear_confirm
            or self.fav_export_mode
            or self.traktor_offer_paths
            or self.traktor_input_mode
        )

    def _ensure_favorites_decision_state(self) -> None:
        """Default the decision-window state for UIs built without __init__."""
        defaults = {
            "favorites_clear_confirm": False,
            "fav_export_mode": "",
            "fav_export_dest": "",
            "fav_export_name": "",
            "fav_export_folder": "",
            "fav_export_kind": "local",
            "fav_export_log": [],
            "fav_export_result": None,
            "traktor_offer_paths": None,
            "traktor_offer_name": "",
            "traktor_offer_details": "",
            "traktor_input_mode": "",
            "traktor_playlist_name": "",
            "traktor_history_id": "",
            "_traktor_used_names": [],
        }
        for name, value in defaults.items():
            if not hasattr(self, name):
                setattr(self, name, value)

    def _favorites_prompt_line(self) -> str:
        """Inline hint shown on the prompt row while a Favorites decision is open."""
        self._ensure_favorites_decision_state()
        if self.favorites_clear_confirm:
            return f"  {BOLD}{COLOR_TRAFFIC_RED}Clear Favorites: [Y] Yes, clear it   [N/Esc] Cancel{NC}"
        if self.fav_export_mode == "choose":
            return (
                f"  {BOLD}{COLOR_TRAFFIC_AMBER}Export Tracks — [1] Local folder   "
                f"[2] SMB share   [3] Traktor history   [Esc] Cancel{NC}"
            )
        if self.fav_export_mode == "folder_name":
            return (
                f"  {BOLD}{COLOR_TRAFFIC_AMBER}Folder name: [{self.fav_export_folder}█]  "
                f"(Enter on empty: DJ Set Mix Preparation Folder){NC}"
            )
        if self.fav_export_mode == "progress":
            return f"  {BOLD}{COLOR_ELECTRIC_CYAN}EXPORT DECK RUNNING  ·  copy verify  ·  no delete{NC}"
        if self.fav_export_mode == "summary":
            return (
                f"  {BOLD}{COLOR_TRAFFIC_GREEN}[T] Traktor playlist   [Y] Export again   [N] Close{NC}"
            )
        if self.fav_export_mode == "again":
            return (
                f"  {BOLD}{COLOR_TRAFFIC_AMBER}Export again to another destination and another Traktor playlist?   "
                f"[Y] Yes   [N] No{NC}"
            )
        if self.fav_export_mode == "history_id":
            return (
                f"  {BOLD}{COLOR_TRAFFIC_GREEN}Traktor history ID: [{self.traktor_history_id}█]  "
                f"(Enter: Load • Esc: Cancel){NC}"
            )
        if self.fav_export_mode == "history_merge":
            return (
                f"  {BOLD}{COLOR_TRAFFIC_AMBER}Favorites already has tracks.   "
                f"[A] Add more   [N] Start a new Favorites playlist   [Esc] Cancel{NC}"
            )
        if self.fav_export_mode == "tracklist_name":
            return (
                f"  {BOLD}{COLOR_TRAFFIC_AMBER}Tracklist name: [{self.fav_export_name}█]  "
                f"(Enter: use this name • Enter on empty: date + 12-character ID • Esc: Cancel){NC}"
            )
        if self.fav_export_mode == "dest_path":
            return (
                f"  {BOLD}{COLOR_TRAFFIC_AMBER}Local export folder: [{self.fav_export_dest}█]  (Enter: Export • Esc: Cancel){NC}"
            )
        if self.fav_export_mode == "smb_path":
            return (
                f"  {BOLD}{COLOR_TRAFFIC_AMBER}Folder inside the SMB share: [{self.fav_export_dest}█]  "
                f"(Enter: Export • empty = share root • Esc: Cancel){NC}"
            )
        if self.traktor_offer_paths:
            return (
                f"  {BOLD}{COLOR_TRAFFIC_GREEN}Traktor is ready. Create a Traktor playlist for these favorites?   "
                f"[Y] Yes   [N] No{NC}"
            )
        if self.traktor_input_mode == "playlist_name":
            return (
                f"  {BOLD}{COLOR_TRAFFIC_GREEN}Traktor playlist name: [{self.traktor_playlist_name}█]  "
                f"(Enter: Continue • Esc: Cancel){NC}"
            )
        if self.traktor_input_mode == "history_id":
            return (
                f"  {BOLD}{COLOR_TRAFFIC_GREEN}Traktor history file ID: [{self.traktor_history_id}█]  "
                f"(Enter: Create playlist • empty = newest history • Esc: Cancel){NC}"
            )
        return ""

    def _favorites_decision_box(self, width: int) -> List[str]:
        """Draw the Favorites decision window over the playlist area."""
        self._ensure_favorites_decision_state()
        if getattr(self, "traktor_picker_active", False):
            return self._traktor_picker_box(width)
        if self.favorites_clear_confirm:
            count = len(self._favorites_store())
            title = "  CLEAR FAVORITES PLAYLIST?  "
            body = [
                f"This removes {count} saved favorite track(s) from the playlist.",
                "The audio files themselves are NOT deleted from disk.",
                "",
                "Press [Y] to confirm, or [N] / Esc to keep the playlist.",
                "After clearing, a Traktor playlist can be created if Traktor is ready.",
            ]
            color = COLOR_TRAFFIC_RED
        elif self.fav_export_mode == "choose":
            title = "  EXPORT TRACKS  ·  DJ SET PREP DECK  "
            count = len(self._favorites_store())
            traktor_note = (
                "Traktor is ready: a playlist can be created after the copy."
                if getattr(self, "fav_export_traktor_ready", False)
                else f"Traktor playlist unavailable: {getattr(self, 'fav_export_traktor_reason', 'not configured')}"
            )
            body = [
                f"{count} favorite track(s) will be copied. Audio files are never deleted.",
                "Every copy is size-checked and must still open as audio.",
                "",
                "[1] This computer   [2] SMB share   [3] Traktor history ID   [Esc] Cancel",
                traktor_note,
            ]
            color = COLOR_TRAFFIC_AMBER
        elif self.fav_export_mode == "folder_name":
            title = "  EXPORT FOLDER NAME  "
            body = [
                "Default folder: DJ Set Mix Preparation Folder",
                "Leave the name empty and press Enter to use that default.",
                "With no primary MIX_ARCHIVE, an empty name uses",
                "FAVORITES_EXPORTED_<date> in the program folder.",
            ]
            color = COLOR_TRAFFIC_AMBER
        elif self.fav_export_mode == "progress":
            title = "  MP-DECK // FAV-EXPORT // COPY-VERIFY // NO-DELETE  "
            log = list(getattr(self, "fav_export_log", []) or [])
            body = log[-8:] or ["DECK ARMING  waiting for the first buffer..."]
            color = COLOR_ELECTRIC_CYAN
        elif self.fav_export_mode == "summary":
            title = "  EXPORT DECK  ·  LOCKED  "
            body = self._favorites_summary_lines()
            color = COLOR_TRAFFIC_GREEN
        elif self.fav_export_mode == "again":
            title = "  EXPORT AGAIN?  "
            body = [
                "The last export is still in its folder. Nothing was deleted.",
                "The same destination folder is allowed.",
                "A Traktor playlist name that already exists will be rejected.",
                "",
                "[Y] Export again and create another Traktor playlist",
                "[N] Close this deck",
            ]
            color = COLOR_TRAFFIC_AMBER
        elif self.fav_export_mode == "history_id":
            title = "  TRAKTOR HISTORY → FAVORITES  "
            body = [
                "Enter the Traktor history file ID, name, or list number.",
                "Paths are read from the loaded Traktor collection.",
                "The audio files are copied, never deleted.",
            ]
            color = COLOR_TRAFFIC_GREEN
        elif self.fav_export_mode == "history_merge":
            title = "  ONE FAVORITES PLAYLIST  "
            count = len(self._favorites_store())
            body = [
                f"Favorites already holds {count} track(s).",
                "[A] Add the history tracks to this playlist",
                "[N] Start a new Favorites playlist from this history file",
                "Starting new clears the playlist only. Audio files stay on disk.",
            ]
            color = COLOR_TRAFFIC_AMBER
        elif self.fav_export_mode == "tracklist_name":
            title = "  TRACKLIST FILE NAME  "
            body = [
                "Leave empty to name the playlist with the date and a random",
                "12-character playlist ID, for example MP_Fav_Set_2026-11-12_A1B2C3D4E5F6.",
                "",
                "Type a custom name, then press Enter.",
            ]
            color = COLOR_TRAFFIC_AMBER
        elif self.fav_export_mode in ("dest_path", "smb_path"):
            title = "  EXPORT DESTINATION  "
            body = [
                "Local folder on this drive." if self.fav_export_mode == "dest_path"
                else "Folder inside the configured SMB share.",
                "Press Enter to start the export, or Esc to cancel.",
            ]
            color = COLOR_TRAFFIC_AMBER
        elif self.traktor_offer_paths:
            title = "  CREATE A TRAKTOR PLAYLIST?  "
            body = [
                f"Traktor History and collection.nml were found.",
                f"Playlist name: {self.traktor_playlist_name or self.traktor_offer_name}",
                getattr(self, "traktor_offer_details", ""),
                "[Y] Create the playlist now        [N] Skip",
            ]
            color = COLOR_TRAFFIC_GREEN
        elif self.traktor_input_mode == "playlist_name":
            title = "  TRAKTOR PLAYLIST NAME  "
            body = [
                "Enter the name Traktor will show in its Playlists folder.",
                "Press Enter to continue, Esc to cancel.",
            ]
            color = COLOR_TRAFFIC_GREEN
        else:
            title = "  TRAKTOR HISTORY FILE ID  "
            body = [
                "Enter the history file ID, file name or list number.",
                "Press Enter with an empty value to use the newest history file.",
            ]
            color = COLOR_TRAFFIC_GREEN
        inner = max(30, min(width - 6, 74))
        short = [text if _display_width(text) <= inner - 4 else _clip_display(text, inner - 4) for text in body]
        out = [_boxed_rule("┌", title, "┐", inner, color)]
        for text in short:
            pad = " " * max(0, inner - 2 - _display_width(text))
            out.append(f"  {color}│{NC} {text}{pad} {color}│{NC}")
        out.append(_boxed_rule("└", "", "┘", inner, color))
        return out


    def _fav_default_export_dir(self) -> str:
        """Parent folder: primary MIX_ARCHIVE, otherwise the program directory."""
        try:
            exporter = self._fav_exporter_module()
            archive = exporter.primary_mix_archive()
            if archive is not None:
                return str(archive)
            return str(exporter.APP_DIR)
        except Exception:
            return str(Path(__file__).resolve().parent)

    def _favorites_summary_lines(self) -> List[str]:
        result = getattr(self, "fav_export_result", None) or {}
        copied = len(result.get("copied") or [])
        elapsed = float(result.get("elapsed_sec") or 0.0)
        folder = str(result.get("dest") or "")
        lines = [
            f"TRACKS EXPORTED   {copied}",
            f"ELAPSED           {elapsed:.3f} s",
            f"FOLDER            {folder or '—'}",
            f"M3U               {os.path.basename(str(result.get('m3u') or '')) or '—'}",
            f"TRACKLIST         {os.path.basename(str(result.get('tracklist') or '')) or '—'}",
            "AUDIO DELETE      never",
        ]
        if not result.get("ok"):
            reason = "; ".join((result.get("errors") or ["export failed"])[:2])
            lines.append(f"STATUS            {reason}")
        else:
            lines.append("STATUS            copies verified")
        if getattr(self, "fav_export_traktor_ready", False):
            lines.append("[T] Traktor playlist   [Y] Export again   [N] Close")
        else:
            lines.append("[Y] Export again   [N] Close")
        return lines

    def _traktor_name_rejected(self, name: str) -> str:
        cleaned = (name or "").strip()
        if not cleaned:
            return ""
        used = list(getattr(self, "_traktor_used_names", []) or [])
        if cleaned in used:
            return f"Traktor playlist '{cleaned}' was already created. Choose another name."
        try:
            exists = self._fav_exporter_module().traktor_playlist_exists(cleaned)
        except Exception:
            return ""
        if exists is True:
            return f"Traktor already has a playlist named '{cleaned}'. Choose another name."
        return ""

    def _favorites_export_begin(self):
        """Open the DJ export decision window. At least one favorite is required.

        Export Track only exists on the Favorites playlist. A stale click target
        or the Shift+J shortcut must not open the deck while the Mix Archive or
        Music playlist is the one on screen.
        """
        if self.mode != "FAVORITES":
            self._ensure_favorites_decision_state()
            self.fav_export_mode = ""
            self._notify(
                "Export Tracks is only available in the Favorites playlist. Press [3] to open it first.",
                5.0,
            )
            return
        store = self._favorites_store()
        store.load()
        self._ensure_favorites_decision_state()
        if not len(store):
            self.favorites_clear_confirm = False
            self.fav_export_mode = ""
            self._notify(
                "Favorites is empty. Add at least one track with [H] before exporting a DJ set.",
                5.0,
            )
            return
        try:
            self.fav_export_traktor_ready, reason = self._fav_exporter_module().traktor_ready()
        except Exception as exc:
            self.fav_export_traktor_ready, reason = False, str(exc)
        self.fav_export_traktor_reason = reason
        self.fav_export_mode = "choose"
        self.fav_export_dest = ""
        self.fav_export_name = ""
        self._notify(
            f"DJ export: {len(store)} favorite track(s). Choose 1 for a local folder or 2 for the SMB share. Esc cancels.",
            8.0,
        )

    def _favorites_export_choose(self, choice: str):
        if choice == "1":
            self.fav_export_kind = "local"
            self.fav_export_mode = "tracklist_name"
            self.fav_export_name = ""
        elif choice == "2":
            try:
                exporter = self._fav_exporter_module()
                if not exporter.smb_configured():
                    self.fav_export_mode = ""
                    self._notify(
                        "The SMB share is not configured. Set FAV_EXPORT_SMB_HOST, FAV_EXPORT_SMB_SHARE, "
                        "FAV_EXPORT_SMB_USER and FAV_EXPORT_SMB_PASSWORD in config.env.",
                        8.0,
                    )
                    return
            except Exception as exc:
                self.fav_export_mode = ""
                self._notify(f"SMB export is unavailable: {exc}", 6.0)
                return
            self.fav_export_kind = "smb"
            self.fav_export_mode = "tracklist_name"
            self.fav_export_name = ""
        elif choice == "3":
            self.traktor_history_id = ""
            self.fav_export_mode = "history_id"
            self._notify("Enter the Traktor history file ID. Esc cancels.", 6.0)

    def _favorites_export_start(self):
        tracks = self._favorites_store().tracks()
        if not tracks:
            self.fav_export_mode = ""
            self._notify("Favorites is empty. Nothing was exported.", 4.0)
            return
        kind = getattr(self, "fav_export_kind", "local")
        destination = self.fav_export_dest.strip()
        custom_name = self.fav_export_name.strip()
        folder_name = getattr(self, "fav_export_folder", "").strip()
        self.fav_export_mode = "progress"
        self.fav_export_log = [f"{time.strftime('%H:%M:%S')}  DECK ARM  {len(tracks)} track(s)  bus={'SMB' if kind == 'smb' else 'LOCAL'}"]
        self.fav_export_result = None
        self._notify(f"Export Tracks: copying {len(tracks)} favorite track(s). Audio files are not deleted.", 6.0)

        def _worker():
            try:
                exporter = self._fav_exporter_module()
                smb = None
                preparation = kind != "smb"
                if kind == "smb":
                    settings = exporter.smb_settings()
                    settings["path"] = destination if destination else settings.get("path", "")
                    smb = {**settings, "kind": "smb"}
                    preparation = False
                result = exporter.export_favorites(
                    tracks,
                    destination=destination or None,
                    smb=smb,
                    tracklist_name=custom_name or None,
                    folder_name=folder_name,
                    preparation=preparation,
                    progress=self._fav_progress,
                )
            except Exception as exc:
                result = {"ok": False, "errors": [str(exc)], "copied": [], "failed": [], "elapsed_sec": 0.0}
            self._favorites_export_finished(result)

        threading.Thread(target=_worker, daemon=True).start()

    def _fav_progress(self, line: str):
        log = getattr(self, "fav_export_log", None)
        if not isinstance(log, list):
            self.fav_export_log = log = []
        log.append(str(line))
        if len(log) > 400:
            del log[:200]

    def _favorites_export_finished(self, result: Dict[str, Any]):
        copied = len(result.get("copied") or [])
        elapsed = float(result.get("elapsed_sec") or 0.0)
        self.fav_export_result = result
        self.fav_export_mode = "summary"
        try:
            ready, reason = self._fav_exporter_module().traktor_ready()
        except Exception as exc:
            ready, reason = False, str(exc)
        self.fav_export_traktor_ready = ready
        self.fav_export_traktor_reason = reason
        folder = os.path.basename(str(result.get("dest") or "")) or str(result.get("dest") or "")
        if result.get("ok"):
            self._notify(
                f"Export Tracks complete: {copied} track(s) in {elapsed:.3f}s → {folder}. "
                "Audio files were not deleted.",
                9.0,
            )
        else:
            reason_text = "; ".join((result.get("errors") or ["unknown error"])[:2])
            self._notify(f"Export Tracks failed after {elapsed:.3f}s: {reason_text}. Nothing was deleted.", 9.0)

    def _favorites_traktor_start(self):
        """Ask for the Traktor playlist name, then create the playlist."""
        paths = list(getattr(self, "traktor_offer_paths", None) or [])
        self.traktor_offer_paths = None
        self.traktor_input_mode = ""
        if not paths:
            return
        name = self.traktor_playlist_name.strip() or self.traktor_offer_name or "MP Favorites"
        history_id = self.traktor_history_id.strip() or None
        self._notify(f"Creating Traktor playlist '{name}'...", 5.0)

        def _worker():
            try:
                result = self._fav_exporter_module().create_traktor_playlist(paths, name, history_id)
            except Exception as exc:
                result = {"ok": False, "message": str(exc)}
            if result.get("ok"):
                missing = len(result.get("missing") or [])
                used = list(getattr(self, "_traktor_used_names", []) or [])
                if name not in used:
                    used.append(name)
                self._traktor_used_names = used
                self.fav_export_mode = "again"
                self._notify(
                    f"Traktor playlist ready: {result.get('playlist')} "
                    f"({result.get('matched', 0)} tracks{'  •  ' + str(missing) + ' not in Traktor' if missing else ''}). "
                    "Open Traktor to use it. Export again? [Y/N]",
                    9.0,
                )
            else:
                self.fav_export_mode = "summary"
                self._notify(f"Traktor playlist not created: {result.get('message', 'unknown error')}", 9.0)

        threading.Thread(target=_worker, daemon=True).start()

    def _favorites_history_import_begin(self):
        self._ensure_favorites_decision_state()
        self.traktor_history_id = ""
        self.fav_export_mode = "history_id"
        self._notify("Enter the Traktor history file ID to build the Favorites playlist.", 6.0)

    def _favorites_history_import_apply(self, mode: str):
        history_id = (self.traktor_history_id or "").strip()
        self.fav_export_mode = "progress"
        self.fav_export_log = [f"{time.strftime('%H:%M:%S')}  HISTORY  id={history_id or 'newest'}  mode={mode}"]
        try:
            loaded = self._fav_exporter_module().favorites_from_history(history_id)
        except Exception as exc:
            loaded = {"ok": False, "message": str(exc), "tracks": []}
        if not loaded.get("ok"):
            self.fav_export_mode = ""
            self._notify(loaded.get("message") or "That Traktor history file could not be imported.", 8.0)
            return
        tracks = list(loaded.get("tracks") or [])
        store = self._favorites_store()
        if mode == "new":
            count = store.replace_tracks(tracks)
            verb = "Started a new Favorites playlist"
        else:
            count = store.add_tracks(tracks)
            verb = "Added to Favorites"
        if self.mode == "FAVORITES" or True:
            self.mode = "FAVORITES"
            self._sync_open_favorites()
        missing = len(loaded.get("missing") or [])
        self.fav_export_kind = "local"
        self.fav_export_mode = "dest_path"
        self.fav_export_dest = self._fav_default_export_dir()
        self.fav_export_name = ""
        self.fav_export_folder = ""
        self._fav_progress(
            f"HISTORY LOCK  {loaded.get('history', '')}  playlist={count}  missing={missing}  delete=never"
        )
        self._notify(
            f"{verb}: {count} track(s) from {loaded.get('history', 'history')}. "
            "Press Enter to copy them into the preparation folder. Esc keeps the playlist and copies nothing.",
            9.0,
        )

    def _arm_favorites_clear(self):
        """Open the confirmation window before clearing the Favorites playlist."""
        self._ensure_favorites_decision_state()
        count = len(self._favorites_store())
        if count == 0:
            self.favorites_clear_confirm = False
            self._notify("The Favorites playlist is already empty.", 3.0)
            return
        self.favorites_clear_confirm = True
        self._notify(
            f"Clear the Favorites playlist ({count} saved)? Press Y to confirm or Esc to cancel. "
            "Audio files stay on disk.",
            8.0,
        )

    def _confirm_favorites_clear(self):
        self._ensure_favorites_decision_state()
        self.favorites_clear_confirm = False
        paths = [row.get("path") for row in self._favorites_store().tracks() if row.get("path")]
        removed = self._favorites_store().clear()
        if self.mode == "FAVORITES":
            self._sync_open_favorites()
        self._notify(
            f"Favorites cleared — {removed} track(s) removed from the playlist. Audio files were not deleted.",
            6.0,
        )
        # Offer the Traktor playlist for the tracks that were just cleared, as
        # the last chance to turn this listened-to set into a ready playlist.
        try:
            ready, _reason = self._fav_exporter_module().traktor_ready()
        except Exception:
            ready = False
        if ready and paths:
            self.traktor_offer_paths = paths
            self.traktor_offer_name = f"MP Favorites {time.strftime('%Y-%m-%d')}"
            self.traktor_offer_details = (
                f"{removed} cleared favorite track(s) are still on disk and can become a Traktor playlist."
            )
            self.traktor_playlist_name = self.traktor_offer_name
            self.traktor_history_id = ""
            self._notify(
                f"Favorites cleared — {removed} track(s) removed. Create a Traktor playlist from them? "
                "[Y] Yes   [N] No",
                9.0,
            )


    def _load_favorites(self):
        """Open the Favorites playlist. Playback of the current track continues."""
        self._favorites_store().load()
        self.mode = "FAVORITES"
        self.keep_library = True
        self._user_library_mode = "FAVORITES"
        self.search_mode = False
        self.search_query = ""
        self.equalizer_hud_active = False
        self.inspector_hud_active = False
        self.selected_row = 0
        self.scroll_offset = 0
        self._sync_open_favorites()
        count = len(self._favorites_store())
        if count:
            self._notify(f"Loaded Favorites playlist — {count} saved", 3.5)
        else:
            self._notify("Favorites playlist is empty. Select a mix or track and press [H] to add it.", 4.5)

    def _append_favorites_button(self, lines: List[str], max_cols: int, whole_row: bool = False):
        """Favorites and Bass Boost on one row above the playlist.

        The two controls share the row on purpose. Giving the bass chip its own
        row cost the CLI frame one line, and at 24 rows that line came out of
        the playlist: the list was left with zero visible rows, so the arrow
        keys moved a cursor nobody could see.
        """
        store = self._favorites_store()
        face = f"  {favorite_glyph(True)} FAVORITES  "
        # The favourites label takes the active theme as well, so a C press
        # recolours this row on the compact frames too. A bare UI built without
        # a visualizer (tests, one-shot printers) keeps a readable default.
        _theme = getattr(getattr(self, "visualizer", None), "theme", "dreamworlds_ultra")
        if whole_row:
            # The CLI action row is text only: no filled block, which used to
            # paint a solid bar across the row above the playlist header. The
            # ink still follows the theme so C recolours it.
            _ink, _ = theme_chip_style(_theme)
            button = f"\033[1;38;2;{_ink[0]};{_ink[1]};{_ink[2]}m{face}\033[0m"
        else:
            button = theme_chip(_theme, face, lift=0.32)
        count = len(store)
        mode = self._bass_mode()
        # The CLI row is text-only (see bass_mode_label): no filled block.
        chip = bass_mode_label(mode)
        chip_w = _display_width(chip)
        pct = BASS_BOOST_FRACTIONS.get(mode, 0.0) * 100.0
        if self.ui_size_mode in COMPACT_UI_SIZES:
            # The compact frames are fixed, so the button chip would push a
            # line off them. They keep the compact badge in the now-playing
            # line and the `[b] Bass` hint below.
            chip = ""
            chip_w = 0
        head = f"{button}  {count} saved"
        # The chip follows the head, separated by two spaces. Measure the head
        # as drawn (its ANSI stripped) rather than deriving the offset by hand:
        # a three-column error there made the click land on the hint text.
        bass_prefix = f"  {head}  {chip}" if chip else ""
        bass_start = 1
        if chip:
            bass_start = _display_width(f"  {head}  ") + 1
        if self.mode == "FAVORITES":
            export_bit = "  Export Tracks"
            long_tail = "   Favorites is open   click or press [3] to reload   [H] marks or removes this track"
        else:
            export_bit = ""
            long_tail = "   click this button or press [3] to open Favorites   [H] marks the selected mix or track"
        candidates = [
            f"{bass_prefix}{export_bit}{long_tail}",
            f"{bass_prefix}{export_bit}   [3] open  [H] mark  [b] bass",
            f"{bass_prefix}{export_bit}   [3] open  [b] bass",
            f"{bass_prefix}{export_bit}",
            bass_prefix,
            f"  {head}",
        ]
        line = _fit_text_line(candidates, max_cols)
        self._favorites_button_row = len(lines) + 1
        self._favorites_button_span = (3, 2 + _display_width(face))
        self._favorites_button_whole_row = bool(whole_row)
        # The bass chip is clickable only when the drawn row actually contains
        # it; a narrower console drops it and leaves Favorites alone.
        if chip and chip in line:
            self._bass_button_row = self._favorites_button_row
            self._bass_button_span = (bass_start, bass_start + chip_w - 1)
        else:
            self._bass_button_row, self._bass_button_span = 0, (0, 0)
        lines.append(line)

    def _mp_visualizer_preset(self) -> Dict[str, Any]:
        want = int(getattr(self, "mp_visualizer_preset_id", 0) or 0)
        cached = getattr(self, "_mp_vis_preset_cache", None)
        if cached is not None and cached[0] == want:
            return cached[1]
        try:
            presets = _mp_visualizer_api().visualizer_presets()
        except Exception:
            presets = []
        found = None
        for preset in presets:
            if int(preset.get("id", -1)) == want:
                found = preset
                break
        if found is None and presets:
            found = presets[0]
        if found is None:
            found = {"id": 0, "key": "cosmic_dreamworlds_liquid", "name": "Cosmic Dreamworlds (Liquid)"}
        self._mp_vis_preset_cache = (want, found)
        return found

    def _publish_mp_visualizer(self):
        preset = self._mp_visualizer_preset()
        self.mp_visualizer_preset_id = int(preset.get("id", 0))
        try:
            _mp_visualizer_api().write_player_visualizer_state(bool(self.mp_visualizer_on), preset)
        except Exception as exc:
            self._notify(f"Visualizer state was not saved: {exc}", 3.0)
        return preset

    def _ensure_standalone_mp_visualizer(self, preset):
        """Start the Linux companion if the Liquid Player has no visualizer process."""
        if not sys.platform.startswith("linux") or os.environ.get("MP_LIQUID_VISUALIZER_STATE"):
            return
        try:
            from liquid_visualizer_session import companion_visualizer_pids
            # Only a visualizer that belongs to *this* player counts. A window
            # the user opened with Z is theirs, so it must not stop the
            # companion from being spawned behind the player.
            if companion_visualizer_pids(for_gui=False):
                return
            root = Path(__file__).resolve().parent
            script = root / "MP_Liquid_Visualizer.py"
            if not script.is_file():
                script = root / "scripts" / "mp_liquid_visualizer_vulkan.py"
            if not script.is_file():
                return
            config_dir = Path.home() / ".config" / "mix-manager"
            config_dir.mkdir(parents=True, exist_ok=True)
            # Mark the child as the player's companion. Without this the new
            # window is treated as a user-opened one: it never bonds behind the
            # player and the VISUALIZATION button cannot hide or show it.
            child_env = {k: v for k, v in os.environ.items()
                         if k != "MP_VISUALIZER_STATE"}
            child_env["MP_VISUALIZER_COMPANION"] = "1"
            child_env.pop("MP_VISUALIZER_DETACHED", None)
            with (config_dir / "liquid_visualizer.log").open("a", encoding="utf-8") as log:
                subprocess.Popen(
                    [sys.executable, str(script), "--preset", str(preset.get("key") or "random")],
                    cwd=str(root), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    start_new_session=True, env=child_env,
                )
        except Exception:
            pass

    def _toggle_mp_visualizer(self):
        """Bond the standalone MP Visualizer behind Liquid Player, or restore glass."""
        self.mp_visualizer_on = not bool(getattr(self, "mp_visualizer_on", False))
        preset = self._publish_mp_visualizer()
        if self.mp_visualizer_on:
            self._ensure_standalone_mp_visualizer(preset)
            self._notify(
                f"MP Visualizer behind Liquid Player: {preset.get('id', 0)} {preset.get('name', '')}. Press 0-9 to change theme. Shift+V restores glass.",
                4.0,
            )
        else:
            self._notify("Liquid glass restored. Shift+V or the Visualization button opens the MP Visualizer.", 3.5)

    def _select_mp_visualizer_theme(self, preset_id: int):
        try:
            preset = _mp_visualizer_api().preset_by_id(int(preset_id))
        except Exception:
            preset = None
        if not preset:
            self._notify(f"No MP Visualizer theme {preset_id}. Themes are 0 through 9.", 2.5)
            return
        self.mp_visualizer_on = True
        self.mp_visualizer_preset_id = int(preset.get("id", 0))
        self._publish_mp_visualizer()
        self._notify(f"Visualizer theme {preset.get('id', 0)}: {preset.get('name', '')}", 2.5)

    def _bass_mode(self) -> str:
        """The bass mode the player is showing (engine, daemon status or local)."""
        mgr = getattr(self, "bass_manager", None)
        if self.engine is not None and not self.is_remote:
            local = getattr(mgr, "mode", None)
            if local in BASS_BOOST_MODES:
                return str(local)
        st = getattr(self, "cached_status", None) or {}
        mode = st.get("bass_mode")
        if mode in BASS_BOOST_MODES:
            return str(mode)
        local = getattr(mgr, "mode", None)
        if local in BASS_BOOST_MODES:
            return str(local)
        return "Normal"

    def _cycle_bass_mode(self, direction: int = 1):
        """Step Normal -> Medium -> High, exactly like the Crusher dial."""
        mode = self._bass_mode()
        if self.is_remote:
            res = send_ipc_command("cycle_bass" if direction >= 0 else "bass_down") or {}
            spoken = res.get("bass_mode")
            if spoken in BASS_BOOST_MODES:
                mode = spoken
                self.cached_status["bass_mode"] = mode
                if self.bass_manager is not None:
                    self.bass_manager.mode = mode
            elif self.bass_manager is not None:
                # The daemon did not answer; still move the local chip so the
                # key press is visible, and the next status poll reconciles it.
                mode = self.bass_manager.cycle(direction)
                self.cached_status["bass_mode"] = mode
        elif self.engine:
            mode = self.engine.cycle_bass_mode(direction)
            if self.bass_manager is not None:
                self.bass_manager.mode = mode
            self.cached_status["bass_mode"] = mode
        else:
            mode = self.bass_manager.cycle(direction) if self.bass_manager is not None else "Normal"
            self.cached_status["bass_mode"] = mode
        self._notify(f"Bass Boost: {mode.upper()}", 2.5)
        global _LIQUID_FRAME_CACHE
        _LIQUID_FRAME_CACHE = None
        return mode

    def _append_liquid_action_row(self, lines: List[str], max_cols: int):
        """Favorites, Visualization and Bass Boost buttons. All clickable in every player size."""
        preset = self._mp_visualizer_preset()
        on = bool(getattr(self, "mp_visualizer_on", False))
        theme_name = getattr(getattr(self, "visualizer", None), "theme", "") or ""
        fav_face = f"  {favorite_glyph(True)} FAVORITES  "
        vis_face = "  VISUALIZATION  "
        # A portrait column cannot carry the long word. The chip is a toggle
        # the user needs in every size, so it shortens rather than being
        # dropped; `vis_face` is what the click span is measured from, so it is
        # assigned the face actually drawn.
        vis_face_short = "  VISUAL  "
        vis_face_min = "  VIS  "
        export_face = " EXPORT TRACKS "
        bass_mode = self._bass_mode()
        bass_face = f" BASS BOOST: {bass_mode.upper()} "
        # Each filled chip takes the selected theme's hue, so the button row
        # belongs to the palette instead of reading as three black blocks.
        lift = 0.9 if on else 0.0
        # FAVORITES follows the same rule as VISUALIZATION and the bass chip,
        # so C recolours the whole row instead of leaving one fixed gold label.
        fav_btn = theme_chip(theme_name, fav_face, lift=lift)
        vis_btn = theme_chip(theme_name, vis_face, lift=lift)
        vis_btn_short = theme_chip(theme_name, vis_face_short, lift=lift)
        vis_btn_min = theme_chip(theme_name, vis_face_min, lift=lift)
        export_btn = theme_chip(theme_name, export_face, lift=0.32)
        bass_btn = bass_mode_chip(bass_mode, theme_name=theme_name)
        bass_face_short = f" BASS:{bass_mode.upper()} "
        bass_btn_short = bass_mode_chip(bass_mode, theme_name=theme_name,
                                        face=bass_face_short)
        gap = "   "
        # Export Tracks belongs to the Favorites playlist only. The Mix Archive
        # and Music playlists have nothing to export, so the chip is left out of
        # the row (and its click span cleared) rather than drawn as an offer
        # that cannot do anything.
        in_favorites = self.mode == "FAVORITES"
        if in_favorites:
            head = f"  {fav_btn}{gap}{vis_btn}{gap}{export_btn}{gap}{bass_btn}"
        else:
            head = f"  {fav_btn}{gap}{vis_btn}{gap}{bass_btn}"
        ident = f"{int(preset.get('id', 0))} {preset.get('name', '')}"
        tail = f"   ON  {ident}   [0-9] theme  [Shift+V] glass  [b] bass" if on else "   [Shift+V] MP Visualizer  [b] bass"
        tail_short = f"   {ident}" if on else "   [Shift+V]"

        # Every chip shrinks in the same order on every size, so the row never
        # runs past the border and the VISUALIZATION toggle is never the thing
        # that disappears. A portrait column carries the short forms; a wide
        # console keeps the long ones.
        fav_faces = (fav_face, "  ★ FAV  ", " ★ ")
        vis_faces = (vis_face, vis_face_short, vis_face_min)
        bass_faces = (bass_face, bass_face_short, " BASS ")
        gap_faces = ("   ", "  ", " ")

        def build(fi, vi, bi, gi):
            fav = theme_chip(theme_name, fav_faces[fi], bold=True, lift=0.0,
                             hue=(255, 196, 0))
            vis = (vis_btn, vis_btn_short, vis_btn_min)[vi]
            bas = (bass_btn, bass_btn_short,
                   theme_chip(theme_name, bass_faces[2], lift=0.25,
                              hue=(150, 190, 235)))[bi]
            pieces = [fav, vis]
            if in_favorites:
                pieces.append(export_btn)
            pieces.append(bas)
            g = gap_faces[gi]
            return "  " + g.join(pieces), fav_faces[fi], vis_faces[vi], bass_faces[bi]

        # Try the widest form first, then the shorter labels, then narrower
        # gaps. The first combination that fits is drawn.
        line = None
        for fi, vi, bi, gi in (
            (0, 0, 0, 0), (0, 1, 0, 0), (0, 1, 1, 0), (1, 1, 1, 0),
            (1, 1, 1, 1), (2, 2, 1, 1), (2, 2, 2, 2),
        ):
            candidate, ff, vf, bf = build(fi, vi, bi, gi)
            if _display_width(candidate) <= max_cols:
                line = candidate
                fav_face, vis_face, bass_face = ff, vf, bf
                break
        if line is None:
            # Even the smallest chips did not fit. Drop the bass chip first:
            # `b` reaches the dial from anywhere, while the VISUALIZATION
            # toggle is the one control with no other way in from the deck, so
            # it is the last thing to go.
            fav_t = theme_chip(theme_name, " ★ ", bold=True, lift=0.0, hue=(255, 196, 0))
            vis_t = vis_btn_min
            bass_t = theme_chip(theme_name, " B ", lift=0.25, hue=(150, 190, 235))
            for pieces, faces in (
                ((fav_t, vis_t, bass_t), (" ★ ", "  VIS  ", " B ")),
                ((fav_t, vis_t), (" ★ ", "  VIS  ", " B ")),
                ((vis_t,), (" ★ ", "  VIS  ", " B ")),
            ):
                candidate = "  " + " ".join(pieces)
                if _display_width(candidate) <= max_cols:
                    line = candidate
                    fav_face, vis_face, bass_face = faces
                    break
            else:
                line = ""
                fav_face, vis_face, bass_face = (" ★ ", "  VIS  ", " B ")
        # Keep the tail only when the full chips fitted; a shortened row is
        # already at its budget.
        room = max_cols - _display_width(line)
        if room >= _display_width(tail):
            line = line + tail
        elif room >= _display_width(tail_short):
            line = line + tail_short
        # Spans are 1-based terminal columns, measured from the text actually
        # drawn, so a chip dropped to fit a narrow column gets no span and a
        # click on the space where it used to be cannot fire it.
        row = len(lines) + 1
        self._export_tracks_row = 0
        self._export_tracks_span = (0, 0)
        self._favorites_button_row = 0
        self._favorites_button_span = (0, 0)
        self._favorites_button_whole_row = False
        self._visualizer_button_row = 0
        self._visualizer_button_span = (0, 0)
        self._bass_button_row = 0
        self._bass_button_span = (0, 0)
        for face, kind in ((fav_face, "fav"), (vis_face, "vis"),
                           (export_face, "export"), (bass_face, "bass")):
            if face not in line:
                continue
            at = _display_width(line[:line.index(face)])
            span = (at + 1, at + _display_width(face))
            if kind == "fav":
                self._favorites_button_row = row
                self._favorites_button_span = span
            elif kind == "vis":
                self._visualizer_button_row = row
                self._visualizer_button_span = span
            elif kind == "export":
                if in_favorites:
                    self._export_tracks_row = row
                    self._export_tracks_span = span
            else:
                self._bass_button_row = row
                self._bass_button_span = span
        lines.append(line)

    def _append_player_buttons(self, lines: List[str], max_cols: int):
        if os.environ.get("MP_LIQUID_GLASS") == "1":
            self._append_liquid_action_row(lines, max_cols)
        else:
            self._append_favorites_button(lines, max_cols, whole_row=True)

    def _on_transport_click(self, col: int, row: int, dragging: bool = False) -> bool:
        """A click on the timeline seeks; a click on the pitch track retunes.

        Both are absolute jumps, not nudges: the click position *is* the new
        value, so the track plays on from under the pointer and the pitch knob
        lands on the clicked percent. The pitch track spans the full
        -20%..+20% range, so the far left is -20%, the centre is the 0% detent,
        and the far right is +20%.

        Once the press starts on one of these tracks, motion events keep
        targeting the same control even after the pointer leaves its row, so a
        press-and-drag scrubs the way the bar looks like it should. Releasing
        clears the arming.
        """
        active = getattr(self, "_transport_drag", "")
        if dragging and active:
            if active == "timeline" and self._timeline_row and self._timeline_span[0]:
                return self._seek_from_column(col)
            if active == "pitch" and self._pitch_row and self._pitch_span[0]:
                return self._pitch_from_column(col)
            return False
        timeline_row, (tl_start, tl_end) = self._timeline_row, self._timeline_span
        if timeline_row and row == timeline_row and tl_start and tl_start <= col <= tl_end:
            return self._seek_from_column(col)
        pitch_row, (p_start, p_end) = self._pitch_row, self._pitch_span
        if pitch_row and row == pitch_row and p_start and p_start <= col <= p_end:
            return self._pitch_from_column(col)
        return False

    def _seek_from_column(self, col: int) -> bool:
        """Seek to the fractional position under ``col``, clamped to the track."""
        st = self._get_status()
        duration = float(st.get("duration_sec", 0.0) or 0.0)
        if duration <= 0.0:
            return True
        tl_start, tl_end = self._timeline_span
        col = min(max(col, tl_start), tl_end)
        span = tl_end - tl_start
        fraction = (col - tl_start) / float(span) if span > 0 else 0.0
        self._transport_drag = "timeline"
        self._send_cmd("seek_to", position=fraction * duration, resume=True)
        return True

    def _pitch_from_column(self, col: int) -> bool:
        """Set the pitch to the percent under ``col``, clamped to the track."""
        p_start, p_end = self._pitch_span
        col = min(max(col, p_start), p_end)
        span = p_end - p_start
        fraction = (col - p_start) / float(span) if span > 0 else 0.5
        percent = int(round((fraction * 2.0 - 1.0) * 20.0))
        self._transport_drag = "pitch"
        self._send_cmd("set_pitch", percent=max(-20, min(20, percent)))
        return True

    def _on_mouse_sgr(self, button: int, col: int, row: int, kind: bytes):
        motion = bool(button & 32)
        # A press that began on the timeline or the pitch track owns the drag
        # until the button comes up, so the scrub keeps tracking the pointer
        # after it leaves that one row.
        if getattr(self, "_transport_drag", ""):
            if kind == b"m":
                # Button released: the scrub ends here.
                self._transport_drag = ""
                return
            # Motion with the left button still down has button code 32 and
            # its two low bits clear; a hover (35) must not scrub.
            if motion and not (button & 3):
                self._on_transport_click(col, row, dragging=True)
                return
            self._transport_drag = ""
        if motion and getattr(self, "_playlist_drag_index", None) is not None:
            self._playlist_drag_to_row(row)
            return
        if kind == b"m" and getattr(self, "_playlist_drag_index", None) is not None:
            self._playlist_drag_to_row(row)
            self._playlist_drag_index = None
            return
        # Bit 64 is the wheel bit (64 = wheel up, 65 = wheel down); 32 is
        # motion, 28 a middle/right press and 3 a release or drag. Only a plain
        # left press may select or play a row.
        if kind != b"M" or (button & 32) or (button & 28) or (button & 3) or (button & 64):
            return
        if self._on_transport_click(col, row):
            return
        export_row = getattr(self, "_export_tracks_row", 0)
        export_span = getattr(self, "_export_tracks_span", (0, 0))
        if export_row and row == export_row and export_span[0] <= col <= export_span[1]:
            self._favorites_export_begin()
            return
        span = getattr(self, "_visualizer_button_span", (0, 0))
        vis_row = getattr(self, "_visualizer_button_row", 0)
        if vis_row and row == vis_row and span[0] <= col <= span[1]:
            self._toggle_mp_visualizer()
            return
        bass_row = getattr(self, "_bass_button_row", 0)
        bass_span = getattr(self, "_bass_button_span", (0, 0))
        if bass_row and row == bass_row and bass_span[0] <= col <= bass_span[1]:
            self._cycle_bass_mode(1)
            return
        self._on_favorites_click(col, row)

    def _play_selected_row(self):
        """Play the row under the selection bar. Enter and a playlist double-click share this."""
        mixes = self._get_active_tracks()
        if not (mixes and 0 <= self.selected_row < len(mixes)):
            return
        chosen = mixes[self.selected_row]
        path = str(chosen.get("path") or "")
        # Nothing mounts the Music Folders SMB shares at login, so a generated
        # playlist row can name a file on a share that is simply not up. Without
        # this the engine refused the track inside load_track and the deck did
        # nothing at all: no sound, no message. Mount on a worker: a wedged
        # share can take tens of seconds, and the deck must stay responsive.
        # A remote client has no view of this machine's filesystem, so the
        # check would refuse every row it is told to play. Only a local player
        # can judge whether the file is reachable.
        if not self.is_remote and path and not os.path.isfile(path):
            share = music_share_for_path(path)
            if share:
                self._notify(f"Mounting {share.get('id')} - {os.path.basename(path)}...", 6.0)

                def _mount_then_play(target=path):
                    if ensure_music_share_for_path(target):
                        self._send_cmd("play", file=target)
                    else:
                        self._notify(unavailable_media_hint(target), 6.0)

                threading.Thread(target=_mount_then_play, name="music-row-mount",
                                 daemon=True).start()
                return
            self._notify(unavailable_media_hint(path), 6.0)
            return
        if self.is_remote:
            self._send_cmd("play", file=chosen["path"])
            return
        if not self.engine:
            return
        for i, t in enumerate(self.engine.playlist):
            if t["path"] == chosen["path"]:
                self._send_cmd("select_index", index=i)
                return
        self._send_cmd("play", file=chosen["path"])

    def _on_favorites_click(self, col: int, row: int):
        if self._favorites_decision_active():
            return
        button_row = getattr(self, "_favorites_button_row", 0)
        span = getattr(self, "_favorites_button_span", (0, 0))
        whole = bool(getattr(self, "_favorites_button_whole_row", False))
        if row == button_row and button_row and (whole or span[0] <= col <= span[1]):
            self._load_favorites()
            return
        export_row, export_start, export_end = getattr(self, "_fav_export_button", (0, 0, 0))
        if export_row and row == export_row and export_start <= col <= export_end:
            self._favorites_export_begin()
            return
        index = getattr(self, "_playlist_star_rows", {}).get(row)
        star_col = getattr(self, "_playlist_star_col", 14)
        if index is not None and abs(col - star_col) <= 1:
            self.selected_row = index
            self._toggle_favorite()
            self._forget_playlist_double_click()
            return
        if index is not None:
            self.selected_row = index
            if self._take_playlist_double_click(index):
                # A second click on the same row plays it, exactly like Enter.
                # It cannot be a drag, so no reorder starts here either.
                self._playlist_drag_index = None
                self._play_selected_row()
                return
        if (
            os.environ.get("MP_LIQUID_GLASS") == "1"
            and index is not None
            and not self._favorites_decision_active()
        ):
            self.selected_row = index
            self._playlist_drag_index = index

    def _take_playlist_double_click(self, index: int) -> bool:
        """Return True when this click completes a double-click on ``index``.

        The first click arms the row; a second click on the same row inside
        ``PLAYLIST_DOUBLE_CLICK_SECONDS`` fires once and clears the arming, so a
        third click starts a fresh pair instead of playing again.
        """
        now = time.monotonic()
        last_at = getattr(self, "_last_playlist_click_at", 0.0)
        last_row = getattr(self, "_last_playlist_click_row", -1)
        played = bool(getattr(self, "_last_playlist_click_played", False))
        if not played and last_row == index and (now - last_at) <= PLAYLIST_DOUBLE_CLICK_SECONDS:
            self._last_playlist_click_at = 0.0
            self._last_playlist_click_row = -1
            self._last_playlist_click_played = True
            return True
        self._last_playlist_click_at = now
        self._last_playlist_click_row = index
        self._last_playlist_click_played = False
        return False

    def _forget_playlist_double_click(self):
        """Drop the armed click, so a star press cannot pair with the next one."""
        self._last_playlist_click_at = 0.0
        self._last_playlist_click_row = -1
        self._last_playlist_click_played = False

    def _playlist_drag_to_row(self, row: int):
        src = getattr(self, "_playlist_drag_index", None)
        dst = getattr(self, "_playlist_star_rows", {}).get(row)
        if src is None or dst is None or src == dst:
            return
        # A row that actually moved was dragged, not clicked, so the next press
        # must start a fresh click pair instead of playing from this one.
        self._forget_playlist_double_click()
        self._reorder_playlist(int(src), int(dst))
        self._playlist_drag_index = int(dst)

    def _reorder_playlist(self, src: int, dst: int):
        """Move one playlist row. Favorites are saved. Audio files stay put."""
        if self.mode == "FAVORITES":
            if self._favorites_store().move(src, dst):
                self.selected_row = dst
                self._sync_open_favorites()
            return
        rows = getattr(self.scanner, "mixes", None)
        if not isinstance(rows, list) or not (0 <= src < len(rows) and 0 <= dst < len(rows)):
            return
        item = rows.pop(src)
        rows.insert(dst, item)
        self.selected_row = dst
        engine = getattr(self, "engine", None)
        if engine is not None and isinstance(getattr(engine, "playlist", None), list):
            engine.playlist = list(rows)

    def _consume_input(self, data: bytes):
        buf = getattr(self, "_inbuf", b"") + (data or b"")
        while buf:
            if buf.startswith(b"\x1b[<"):
                match = _MOUSE_SGR_RE.match(buf)
                if not match:
                    if len(buf) < 48:
                        break
                    buf = buf[1:]
                    continue
                self._on_mouse_sgr(int(match.group(1)), int(match.group(2)), int(match.group(3)), match.group(4))
                buf = buf[match.end():]
                continue
            if buf.startswith(b"\x1b]") and (DROP_OSC_PREFIX.startswith(buf) or buf.startswith(DROP_OSC_PREFIX)):
                if len(buf) < len(DROP_OSC_PREFIX):
                    break
                end = buf.find(b"\x07", len(DROP_OSC_PREFIX))
                if end < 0:
                    if len(buf) > 4096:
                        buf = buf[1:]
                        continue
                    break
                payload = buf[len(DROP_OSC_PREFIX):end]
                buf = buf[end + 1:]
                self._play_dropped_manifest(payload.decode("utf-8", "replace").strip())
                continue
            hold = 0
            for size in range(len(DROP_OSC_PREFIX) - 1, 1, -1):
                if buf.endswith(DROP_OSC_PREFIX[:size]):
                    hold = size
                    break
            mouse_at = buf.find(b"\x1b[<", 1)
            drop_at = buf.find(DROP_OSC_PREFIX, 1 if not buf.startswith(DROP_OSC_PREFIX) else 0)
            split_points = [pos for pos in (mouse_at, drop_at) if pos > 0]
            if hold and (not split_points or min(split_points) >= len(buf) - hold):
                chunk, buf = buf[:-hold], buf[-hold:]
                if chunk:
                    self._process_key(chunk)
                break
            split = min(split_points) if split_points else -1
            chunk = buf if split < 0 else buf[:split]
            buf = b"" if split < 0 else buf[split:]
            if chunk:
                keys, partial = split_key_burst(chunk)
                for key in keys:
                    self._process_key(key)
                if partial:
                    # Keep the unfinished sequence plus the untrimmed tail for
                    # the next read, and stop: the tail is not a key press yet.
                    buf = partial + buf
                    break
        self._inbuf = buf

    def _generate_selected_spek(self):
        tp, tn = self._get_target_path()
        label = tn or (os.path.basename(tp) if tp else "this track")
        if not tp or not os.path.isfile(tp): return self._notify("No audio track available for Spek generation.", 3.0)
        if (ex := find_existing_spek(tp, mode=self.mode)) and os.path.isfile(ex) and os.path.getsize(ex) > 0:
            self._notify(f"Opening Spek: {os.path.basename(ex)}", 4.0)
            if not open_image_viewer(ex):
                self._notify(f"Spek found but the image viewer did not open: {os.path.basename(ex)}", 5.0)
            return
        if self._spek_job_running:
            return self._notify("MP Audio Player is already generating a Spek.", 4.0)
        out_dir = get_default_spek_dir(mode=self.mode, audio_path=tp)
        self._spek_job_running = True
        self._notify(f"MP Audio Player is generating a Spek for {label}. It will open when finished.", 86400.0)
        notify_desktop("MP Audio Player", f"MP Audio Player is generating a Spek for {label}")
        def _w():
            try:
                ok, res = generate_track_spek(tp, mode=self.mode, output_dir=out_dir)
                if ok and res and os.path.isfile(res):
                    self._notify(f"MP Audio Player finished the Spek. Opening {os.path.basename(res)}", 6.0)
                    notify_desktop("MP Audio Player", f"Spek ready: {os.path.basename(res)}")
                    if not open_image_viewer(res):
                        self._notify(f"Spek saved to {out_dir.name} but the image viewer did not open.", 6.0)
                else:
                    self._notify(f"Spek generation failed: {str(res)[:80]}", 6.0)
            finally:
                self._spek_job_running = False
        threading.Thread(target=_w, daemon=True).start()

    def _open_selected_in_file_manager(self):
        st, mixes = self._get_status(), self._get_active_tracks()
        sel = mixes[self.selected_row].get("path") or "" if (mixes and 0 <= self.selected_row < len(mixes)) else ""
        if not (target := choose_file_manager_target(st.get("path") or "", sel)):
            return self._notify("No audio file to show in File Manager.", 3.0)
        ok = open_in_file_manager(target)
        self._notify(f"{'Opened in' if ok else 'Failed to open'} File Manager: {os.path.basename(target)}", 3.0)

    def _toggle_recording(self):
        if self.is_remote:
            res = send_ipc_command("toggle_record") or {}
            is_rec, rec_file = bool(res.get("recording", False)), str(res.get("recording_file", ""))
        elif self.engine: is_rec, rec_file = self.engine.toggle_recording()
        else: return
        self.cached_status.update(recording=is_rec, recording_file=rec_file)
        self._notify(f"● RECORDING TO WAV STARTED: {os.path.basename(rec_file)} (3GB Max / File)" if is_rec else "■ RECORDING STOPPED & SAVED TO MP_AUDIOWAV_REC_OUTPUT", 4.0)

    def _view_selected_tracklist(self):
        tp, tn = self._get_target_path()
        if not tp: return self._notify("No mix selected to view tracklist.", 3.0)
        if (tl := find_tracklist_for_mix(tp)) and os.path.isfile(tl):
            self._notify(f"{'Opened' if open_tracklist_window(tl, tp, theme=self.visualizer.theme) else 'Failed to open'} Tracklist: {os.path.basename(tl)}", 3.5)
        else: self._notify(f"No tracklist found for: {tn or os.path.basename(tp)}", 3.5)

    def _open_audio_file(self):
        chosen = choose_audio_file_gui()
        if chosen == "": return self._notify("Open file cancelled.", 2.5)
        if chosen and os.path.isfile(chosen):
            self._send_cmd("play", file=chosen)
            item = {"name": os.path.basename(chosen), "path": chosen, "size_mb": os.path.getsize(chosen) / 1048576.0, "mtime": time.time(), "ext": os.path.splitext(chosen)[1].lower()[1:].upper()}
            if not any(m["path"] == chosen for m in self.scanner.mixes): self.scanner.mixes.insert(0, item)
            self.selected_row = 0
            return self._notify(f"Playing: {os.path.basename(chosen)}", 3.5)
        self._notify("No audio file selected. Press [F] for File Manager.", 3.0)

    def _install_dropped_tracks(self, items: List[Dict[str, Any]]) -> None:
        dropped = {item["path"] for item in items}
        current = list(getattr(self.scanner, "mixes", []) or [])
        kept = [track for track in current if track.get("path") not in dropped]
        self.scanner.mixes = list(items) + kept
        if hasattr(self.scanner, "_search_cache_initialized"):
            self.scanner._search_cache_initialized = None
        self.search_mode = False
        self.search_query = ""
        self.selected_row = 0
        self.scroll_offset = 0
        self._playlist_clear_armed = False

    def _drop_notice(self, items: List[Dict[str, Any]], truncated: bool) -> str:
        name = items[0].get("name") or os.path.basename(items[0].get("path", ""))
        if len(items) == 1:
            notice = f"Playing: {name}"
        else:
            notice = f"Playing {len(items)} audio files, starting with {name}"
        if truncated:
            notice += ". More files in that drop were left out"
        return notice

    def _play_dropped_manifest(self, manifest_path: str) -> None:
        """Play audio files dropped onto the Liquid Player window."""
        path = Path(str(manifest_path or "").strip())
        if not is_drop_manifest(path):
            return
        try:
            data = read_drop_manifest(path)
        except Exception:
            self._notify("Could not read the dropped audio files.", 3.5)
            return
        items = audio_track_items(data.get("paths") or [])
        if not items:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            if data.get("rejected"):
                self._notify("No audio files in that drop. All audio types can be dropped onto the player.", 4.0)
            return
        self._install_dropped_tracks(items)
        notice = self._drop_notice(items, bool(data.get("truncated")))
        if self.is_remote:
            def _go():
                try:
                    send_ipc_command("drop_audio", manifest=str(path))
                finally:
                    try:
                        path.unlink(missing_ok=True)
                    except Exception:
                        pass
            threading.Thread(target=_go, daemon=True).start()
        else:
            if self.engine:
                self.engine.prepend_dropped_audio([item["path"] for item in items])
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        self._notify(notice, 4.0)

    def _open_black_glass(self):
        """Open an MP Black Glass pane at exactly this window's size (W).

        The pane is its own process, so the player keeps drawing normally. The
        helper reads this window's published rectangle (through ``MP_HOST_PID``
        in the child's environment), so the pane lands on the same size and
        position as the player it was opened from - including Mini, Micro and
        Vertical, which the host publishes as the window changes.
        """
        try:
            from black_glass_window import spawn_black_glass_for_host, spawn_message
        except ImportError:
            try:
                from scripts.black_glass_window import spawn_black_glass_for_host, spawn_message
            except ImportError:
                self._notify("MP Black Glass is unavailable in this install.", 3.0)
                return
        result = spawn_black_glass_for_host("MP Liquid Player")
        if result == "opened":
            self._notify("✦ Opened MP Black Glass — R rainbow, Esc/Q close", 3.5)
            return
        self._notify(spawn_message(result) or "MP Black Glass could not be opened.", 4.0)

    def _export_playlist(self):
        if not (mixes := self.scanner.get_mixes(self.search_query)): return self._notify("No mixes in playlist to export.", 3.0)
        chosen = choose_export_m3u_gui()
        if chosen == "": return self._notify("Export cancelled.", 2.5)
        if chosen:
            ok, res = export_playlist_m3u(mixes, chosen)
            return self._notify(f"Exported {len(mixes)} mixes to: {os.path.basename(res)}" if ok else f"Export failed: {res}", 4.0)
        self.export_mode = True
        self.export_path = os.path.join(os.path.expanduser("~/Desktop" if os.path.isdir(os.path.expanduser("~/Desktop")) else "~"), "MP_Archive_Playlist.m3u")

    def _queue_selected(self):
        tp, tn = self._get_target_path()
        if not tp: return self._notify("No mix selected to queue.", 2.5)
        self._send_cmd("queue", file=tp)
        if not self.is_remote and self.engine: q_len = len(self.engine.play_queue)
        else:
            cur_q = list(self.cached_status.get("queue", []))
            if tp not in cur_q: cur_q.append(tp)
            self.cached_status.update(queue=cur_q, queue_len=len(cur_q))
            q_len = len(cur_q)
        self._notify(f"✔ Queued Mix: {tn or os.path.basename(tp)} (#{q_len} in Queue)", 3.5)

    def _playlist_entry_count(self) -> int:
        mixes = getattr(self.scanner, "mixes", None)
        return len(mixes) if isinstance(mixes, list) else 0

    def _playlist_queue_count(self) -> int:
        if self.engine and not self.is_remote:
            return len(getattr(self.engine, "play_queue", []) or [])
        try:
            return int(self.cached_status.get("queue_len", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def _arm_playlist_clear(self):
        """Ask before emptying the playlist. A second D or Delete confirms."""
        if self.mode == "FAVORITES":
            # Clearing Favorites is destructive and easy to trigger by accident,
            # so it always uses an explicit decision window.
            self._arm_favorites_clear()
            return
        removed, queued = self._playlist_entry_count(), self._playlist_queue_count()
        if removed == 0 and queued == 0:
            self._playlist_clear_armed = False
            self._notify("Playlist is already empty.", 2.5)
            return
        self._playlist_clear_armed = True
        self._notify(
            f"Clear the entire playlist ({removed} {self._entry_noun()})? Press D or Del again to confirm. Esc cancels. Audio files stay on disk.",
            6.0,
        )

    def _confirm_clear_playlist(self):
        """Empty the open playlist and queue. [1] or [2] reloads that library."""
        if self.mode == "FAVORITES":
            self._confirm_favorites_clear()
            return
        self._playlist_clear_armed = False
        removed = self._playlist_entry_count()
        self.scanner.mixes = []
        if hasattr(self.scanner, "_search_cache_initialized"):
            self.scanner._search_cache_initialized = None
        self.search_mode = False
        self.search_query = ""
        self.selected_row = 0
        self.scroll_offset = 0
        self._send_cmd("clear_playlist")
        if isinstance(self.cached_status, dict):
            self.cached_status.update(
                title="No Track Loaded", path="", state="stopped",
                position_fmt="00:00", duration_fmt="00:00", progress_pct=0.0,
                queue=[], queue_len=0, total_tracks=0,
            )
        self._notify(
            f"Playlist cleared — {removed} {self._entry_noun()} removed. Press [1], [2], or [3] to reload. Audio files were not deleted.",
            5.0,
        )

    def _publish_liquid_ui_size(self) -> None:
        """Tell the Liquid Glass window the size this player actually applied."""
        if os.environ.get("MP_LIQUID_GLASS") != "1":
            return
        try:
            from liquid_ui_size import publish_ui_size
        except ImportError:
            from scripts.liquid_ui_size import publish_ui_size
        publish_ui_size(CONFIG_DIR, os.getpid(), self.ui_size_mode)

    def _clear_liquid_ui_size(self) -> None:
        try:
            from liquid_ui_size import clear_ui_size
        except ImportError:
            try:
                from scripts.liquid_ui_size import clear_ui_size
            except ImportError:
                return
        clear_ui_size(CONFIG_DIR, os.getpid())

    def _cleanup_terminal(self):
        self._clear_liquid_ui_size()
        runtime = _load_tty_runtime()
        if runtime is not None:
            runtime.restore_terminal()
        fd = getattr(self, "_orig_fd", None)
        old_settings = getattr(self, "_orig_termios", None)
        if fd is not None and old_settings and termios:
            try: termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            except Exception: pass
        try:
            sys.stdout.write("\033]111\007\033[49m\033[?1000l\033[?1006l\033[?7h\033[?25h\033[?1049l\033]0;MP Audio Player\007\n")
            sys.stdout.flush()
        except Exception: pass
        if hasattr(self, "_win_cstate"):
            _win_console_end(self._win_cstate)

    def run(self):
        fd = sys.stdin.fileno() if sys.stdin.isatty() else None
        old_settings = None
        tty_rt = _load_tty_runtime()
        if tty_rt is not None:
            tty_rt.configure_stdio()
        if tty_rt is not None and fd is not None and tty_rt.arm_terminal(fd=fd, raw=False):
            old_settings = tty_rt.saved_attributes()
        elif fd is not None and termios and tty:
            try:
                old_settings = termios.tcgetattr(fd)
                tty.setcbreak(fd)
                new = termios.tcgetattr(fd); new[1] |= termios.OPOST
                termios.tcsetattr(fd, termios.TCSADRAIN, new)
            except Exception: pass
        cstate = _win_console_begin()
        self._orig_fd = fd
        self._orig_termios = old_settings
        self._win_cstate = cstate
        if self.ui_size_mode in COMPACT_UI_SIZES:
            _setup_mini_terminal_appearance()
            t_cols, t_rows = ui_size_grid(self.ui_size_mode)
            # Konsole on Wayland applies CSI 8 to the reported size only. The real
            # window size is set at launch (-p TerminalRows) or by resize_host_terminal.
            if not os.environ.get("KONSOLE_DBUS_SERVICE"):
                sys.stdout.write(f"\033[8;{t_rows};{t_cols}t")
            _set_terminal_title(UI_SIZE_TITLES[self.ui_size_mode])
        else:
            _set_terminal_title("MP Audio Player")
        self._publish_liquid_ui_size()
        apply_terminal_theme_background(self.visualizer.theme, force=True)
        sys.stdout.write("\033[?1049h\033[?25l\033[?7l\033[?1000h\033[?1006h\033[2J\033[H"); sys.stdout.flush()
        _win_hide_console_scrollbar()
        threading.Thread(target=self._input_loop, daemon=True).start()
        # Pace by deadline rather than sleeping a fixed time after each draw.
        # Sleeping *after* the draw made the period `draw_time + 20 ms`, so a
        # frame that cost 8 ms landed 28 ms after the previous one and the
        # equaliser visibly sped up and slowed down as its cost changed. A
        # deadline keeps the cadence constant: a cheap frame waits longer, an
        # expensive one waits less, and the picture updates at a steady rate.
        frame_period = 0.02
        next_frame = time.perf_counter()
        try:
            while self.running:
                if not self.is_remote and self.engine: self.engine.check_track_end()
                _win_hide_console_scrollbar()
                self._follow_shared_theme()
                self._follow_visualizer_preset()
                try:
                    self._draw_frame()
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exc:
                    # A closed terminal / dead pty raises EPIPE or EIO on every
                    # write. Retrying the frame forever cannot succeed, and it
                    # used to append one log entry per frame (~1692 of them).
                    # Stop the player and log the cause once instead.
                    if _is_dead_output_error(exc):
                        if not getattr(self, "_output_dead_logged", False):
                            self._output_dead_logged = True
                            try: mp_log_error("TerminalPlayerUI", f"Terminal output closed, stopping player: {exc}", exc)
                            except Exception: pass
                        self.running = False
                        break
                    if not getattr(self, "_draw_error_logged", False):
                        self._draw_error_logged = True
                        try: mp_log_error("TerminalPlayerUI", f"Error drawing frame: {exc}", exc)
                        except Exception: pass
                next_frame += frame_period
                now = time.perf_counter()
                if next_frame > now:
                    time.sleep(next_frame - now)
                elif now - next_frame > frame_period:
                    # Fell far behind (a resize, a stalled disk): resynchronise
                    # instead of running a burst of catch-up frames.
                    next_frame = now
        except (KeyboardInterrupt, SystemExit): pass
        finally:
            self.running = False
            if self.engine and not self.is_remote:
                try: self.engine.stop_recording()
                except Exception: pass
            self._cleanup_terminal()

    def _input_loop(self):
        if not sys.stdin.isatty(): return
        if sys.platform == "win32":
            import msvcrt
            km = {b"H": b"\x1b[A", b"P": b"\x1b[B", b"M": b"\x1b[C", b"K": b"\x1b[D", b"G": b"\x1b[H", b"O": b"\x1b[F", b"I": b"\x1b[5~", b"Q": b"\x1b[6~", b"S": b"\x1b[3~"}
            while self.running:
                try:
                    if msvcrt.kbhit():
                        ch = msvcrt.getch()
                        self._process_key(km.get(msvcrt.getch(), b"") if ch in (b"\x00", b"\xe0") else ch)
                    else: time.sleep(0.02)
                except Exception: time.sleep(0.02)
            return
        fd = sys.stdin.fileno()
        tty_rt = _load_tty_runtime()
        if tty_rt is not None:
            while self.running:
                try:
                    d = tty_rt.wait_for_input(fd, 0.05)
                    if not d or not self.running:
                        continue
                    if d == b"\x1b":
                        extra = tty_rt.wait_for_input(fd, 0.03)
                        if extra:
                            d += extra
                    self._consume_input(d)
                except Exception:
                    pass
            return
        while self.running:
            try:
                if select.select([fd], [], [], 0.05)[0] and self.running:
                    d = os.read(fd, 64)
                    if d == b"\x1b" and select.select([fd], [], [], 0.03)[0]: d += os.read(fd, 64)
                    self._consume_input(d)
            except Exception: pass

    def _process_key(self, data: bytes):
        if not data: return
        if b"\x1b[<" in data:
            self._consume_input(data)
            return
        if self.equalizer_hud_active and self.eq_naming_mode:
            if data in (b"\r", b"\n"):
                self.eq_naming_mode = False
                p_name = self.eq_custom_name_input.strip() or "Custom 1"
                cb = list(self.eq_manager.current_bands)
                if self.is_remote: self._send_cmd("save_eq_preset", name=p_name, bands=cb)
                elif self.engine: self.engine.save_eq_preset(p_name, cb)
                self.eq_manager.save_custom_preset(p_name, cb)
                self._notify(f"Equalizer preset saved: {p_name}", 3.0)
                return
            elif data == b"\x1b":
                self.eq_naming_mode = False
                self._notify("Preset save cancelled", 2.0)
                return
            elif data in (b"\x7f", b"\x08"):
                self.eq_custom_name_input = self.eq_custom_name_input[:-1]
                return
            else:
                try:
                    c = data.decode("utf-8", "ignore")
                    if c.isprintable(): self.eq_custom_name_input += c
                except Exception: pass
                return
        if self.export_mode:
            if data in (b"\r", b"\n"):
                self.export_mode = False
                mixes = self.scanner.get_mixes(self.search_query)
                ok, res = export_playlist_m3u(mixes, self.export_path.strip() or os.path.expanduser("~/Desktop/MP_Archive_Playlist.m3u"))
                self._notify(f"Exported {len(mixes)} mixes to: {os.path.basename(res)}" if ok else f"Export failed: {res}", 4.0)
            elif data == b"\x1b": self.export_mode = False; self._notify("Export cancelled", 2.5)
            elif data in (b"\x7f", b"\x08"): self.export_path = self.export_path[:-1]
            else:
                try: self.export_path += data.decode("utf-8", "ignore")
                except Exception: pass
            return
        self._ensure_favorites_decision_state()
        if self.favorites_clear_confirm:
            if data in (b"y", b"Y"):
                self._confirm_favorites_clear()
            elif data in (b"\x1b", b"n", b"N", b"\r", b"\n", b"q", b"Q"):
                self.favorites_clear_confirm = False
                self._notify("Favorites clear cancelled. The playlist is unchanged.", 3.5)
            return
        if self.fav_export_mode == "choose":
            choice = data.decode("utf-8", "ignore") if len(data) == 1 else ""
            if choice in ("1", "2", "3"):
                self._favorites_export_choose(choice)
            elif data in (b"\x1b", b"q", b"Q"):
                self.fav_export_mode = ""
                self._notify("DJ export cancelled.", 3.0)
            return
        if self.fav_export_mode == "tracklist_name":
            if data in (b"\r", b"\n"):
                # Empty input keeps the automatic date + 12 character playlist ID.
                if self.fav_export_kind == "smb":
                    self.fav_export_mode = "smb_path"
                    self._notify("Enter the folder inside the SMB share, then press Enter (empty for the share root).", 6.0)
                else:
                    self.fav_export_mode = "dest_path"
                    self.fav_export_dest = self._fav_default_export_dir()
                    self._notify("Enter the local export folder, then press Enter.", 6.0)
            elif data == b"\x1b":
                self.fav_export_mode = ""
                self._notify("DJ export cancelled.", 3.0)
            elif data in (b"\x7f", b"\x08"):
                self.fav_export_name = self.fav_export_name[:-1]
            else:
                try:
                    if (c := data.decode("utf-8", "ignore")).isprintable():
                        self.fav_export_name += c
                except Exception: pass
            return
        if self.fav_export_mode in ("dest_path", "smb_path"):
            if data in (b"\r", b"\n"):
                if self.fav_export_mode == "dest_path":
                    self.fav_export_mode = "folder_name"
                    self.fav_export_folder = ""
                    self._notify(
                        "Folder name. Press Enter with an empty name for DJ Set Mix Preparation Folder.",
                        6.0,
                    )
                else:
                    self._favorites_export_start()
            elif data == b"\x1b":
                self.fav_export_mode = ""
                self._notify("DJ export cancelled. No audio files were deleted.", 3.0)
            elif data in (b"\x7f", b"\x08"):
                self.fav_export_dest = self.fav_export_dest[:-1]
            else:
                try:
                    if (c := data.decode("utf-8", "ignore")).isprintable():
                        self.fav_export_dest += c
                except Exception: pass
            return
        if self.fav_export_mode == "folder_name":
            if data in (b"\r", b"\n"):
                self._favorites_export_start()
            elif data == b"\x1b":
                self.fav_export_mode = ""
                self._notify("DJ export cancelled. No audio files were deleted.", 3.0)
            elif data in (b"\x7f", b"\x08"):
                self.fav_export_folder = self.fav_export_folder[:-1]
            else:
                try:
                    if (c := data.decode("utf-8", "ignore")).isprintable():
                        self.fav_export_folder += c
                except Exception: pass
            return
        if self.fav_export_mode == "progress":
            return
        if self.fav_export_mode == "summary":
            if data in (b"t", b"T") and getattr(self, "fav_export_traktor_ready", False):
                paths = [row.get("path") for row in self._favorites_store().tracks() if row.get("path")]
                self.traktor_offer_paths = paths
                self.traktor_offer_name = (self.fav_export_result or {}).get("playlist_name") or "Favorites"
                self.traktor_playlist_name = ""
                self.traktor_history_id = ""
                self.fav_export_mode = ""
                self.traktor_input_mode = "playlist_name"
                self._notify("Enter a new Traktor playlist name. A name that already exists is rejected.", 7.0)
            elif data in (b"y", b"Y"):
                self.fav_export_mode = "choose"
                self.fav_export_dest = ""
                self.fav_export_folder = ""
                self._notify("Export again: choose a destination. The same folder is allowed. [1] Local  [2] SMB  [3] History", 7.0)
            elif data in (b"n", b"N", b"\x1b", b"q", b"Q"):
                self.fav_export_mode = ""
                self._notify("Export deck closed. The copied files were left in place.", 4.0)
            return
        if self.fav_export_mode == "again":
            if data in (b"y", b"Y", b"\r", b"\n"):
                self.fav_export_mode = "choose"
                self.fav_export_dest = ""
                self.fav_export_folder = ""
                self._notify("Export again. The same output folder is allowed. A used Traktor playlist name is rejected.", 7.0)
            elif data in (b"n", b"N", b"\x1b", b"q", b"Q"):
                self.fav_export_mode = ""
                self._notify("Export deck closed.", 3.0)
            return
        if self.fav_export_mode == "history_id":
            if data in (b"\r", b"\n"):
                if len(self._favorites_store()):
                    self.fav_export_mode = "history_merge"
                    self._notify("Add these history tracks to Favorites, or start a new Favorites playlist? [A/N]", 8.0)
                else:
                    self._favorites_history_import_apply("new")
            elif data == b"\x1b":
                self.fav_export_mode = ""
                self._notify("History import cancelled. The Favorites playlist was not changed.", 3.0)
            elif data in (b"\x7f", b"\x08"):
                self.traktor_history_id = self.traktor_history_id[:-1]
            else:
                try:
                    if (c := data.decode("utf-8", "ignore")).isprintable():
                        self.traktor_history_id += c
                except Exception: pass
            return
        if self.fav_export_mode == "history_merge":
            if data in (b"a", b"A"):
                self._favorites_history_import_apply("add")
            elif data in (b"n", b"N"):
                self._favorites_history_import_apply("new")
            elif data == b"\x1b":
                self.fav_export_mode = ""
                self._notify("History import cancelled. Favorites was left as it was.", 3.0)
            return
        if self.traktor_offer_paths:
            if data in (b"y", b"Y", b"\r", b"\n"):
                self.traktor_playlist_name = self.traktor_playlist_name or self.traktor_offer_name
                self.traktor_offer_paths = None
                self.traktor_input_mode = "playlist_name"
                self._notify("Enter the Traktor playlist name, then press Enter (empty keeps the export name).", 7.0)
            elif data in (b"n", b"N", b"\x1b", b"q", b"Q"):
                self.traktor_offer_paths = None
                self.fav_export_mode = "again"
                self._notify("Traktor playlist skipped. The exported files are ready. Export again? [Y/N]", 6.0)
            return
        if self.traktor_input_mode == "playlist_name":
            if data in (b"\r", b"\n"):
                rejection = self._traktor_name_rejected(self.traktor_playlist_name)
                if rejection:
                    self._notify(rejection, 7.0)
                    return
                self.traktor_input_mode = "history_id"
                self._notify("Enter the Traktor history file ID, then press Enter (empty uses the newest history).", 7.0)
            elif data == b"\x1b":
                self.traktor_input_mode = ""
                self._notify("Traktor playlist cancelled.", 3.0)
            elif data in (b"\x7f", b"\x08"):
                self.traktor_playlist_name = self.traktor_playlist_name[:-1]
            else:
                try:
                    if (c := data.decode("utf-8", "ignore")).isprintable():
                        self.traktor_playlist_name += c
                except Exception: pass
            return
        if self.traktor_input_mode == "history_id":
            if data in (b"\r", b"\n"):
                self._favorites_traktor_start()
            elif data == b"\x1b":
                self.traktor_input_mode = ""
                self._notify("Traktor playlist cancelled.", 3.0)
            elif data in (b"\x7f", b"\x08"):
                self.traktor_history_id = self.traktor_history_id[:-1]
            else:
                try:
                    if (c := data.decode("utf-8", "ignore")).isprintable():
                        self.traktor_history_id += c
                except Exception: pass
            return
        if self.search_mode:
            if data in (b"\r", b"\n"): self.search_mode = False
            elif data == b"\x1b": self.search_mode, self.search_query = False, ""
            elif data in (b"\x7f", b"\x08"): self.search_query, self.selected_row = self.search_query[:-1], 0
            elif data in (b"\x1b[A", b"\x1bOA"): self._navigate_list(-1)
            elif data in (b"\x1b[B", b"\x1bOB"): self._navigate_list(1)
            else:
                try:
                    if (c := data.decode("utf-8", "ignore")).isprintable(): self.search_query, self.selected_row = self.search_query + c, 0
                except Exception: pass
            return

        if data == b"K":
            self._cleanup_terminal()
            try:
                # Kill only player processes owned by this user, matched on argv rather
                # than a substring of a shell command line. `pkill -f` also hit
                # editors with the file open, other users' players, and any
                # wrapper whose command string mentioned the name.
                _kill_player_processes()
            except Exception:
                pass
            self.running = False
            return

        if self.equalizer_hud_active:
            if data in (b"\x1b[D", b"\x1bOD", b"h", b"H"):
                self.eq_selected_band = max(0, self.eq_selected_band - 1)
                return
            elif data in (b"\x1b[C", b"\x1bOC", b"l", b"L"):
                self.eq_selected_band = min(9, self.eq_selected_band + 1)
                return
            elif data in (b"b", b"B"):
                # The Crusher dial stays reachable while the EQ HUD is open.
                self._cycle_bass_mode(1)
                return
            elif data in (b"\x1b[A", b"\x1bOA", b"k", b"+", b"="):
                new_gain = round(min(12.0, self.eq_manager.current_bands[self.eq_selected_band] + 0.5), 1)
                self.eq_manager.set_band(self.eq_selected_band, new_gain)
                self._remember_eq_bands()
                if self.is_remote: self._send_cmd("set_eq_band", band=self.eq_selected_band, gain=new_gain)
                elif self.engine: self.engine.set_eq_band(self.eq_selected_band, new_gain)
                return
            elif data in (b"\x1b[B", b"\x1bOB", b"j", b"J", b"-", b"_"):
                new_gain = round(max(-12.0, self.eq_manager.current_bands[self.eq_selected_band] - 0.5), 1)
                self.eq_manager.set_band(self.eq_selected_band, new_gain)
                self._remember_eq_bands()
                if self.is_remote: self._send_cmd("set_eq_band", band=self.eq_selected_band, gain=new_gain)
                elif self.engine: self.engine.set_eq_band(self.eq_selected_band, new_gain)
                return
            elif data in (b"p", b"P", b"\t"):
                np_name = self.eq_manager.cycle_preset(1)
                self._remember_eq_bands()
                if self.is_remote: self._send_cmd("set_eq_preset", preset=np_name)
                elif self.engine: self.engine.set_eq_preset(np_name)
                self._notify(f"Equalizer Preset: {np_name}", 2.0)
                return
            elif data in (b"1", b"2", b"3", b"4", b"5", b"6", b"7"):
                p_list = ["Flat", "Ambient", "Trance", "Techno", "House", "Progressive", "Vapourwave"]
                idx = int(data.decode("ascii")) - 1
                if 0 <= idx < len(p_list):
                    t_name = p_list[idx]
                    self.eq_manager.apply_preset(t_name)
                    self._remember_eq_bands()
                    if self.is_remote: self._send_cmd("set_eq_preset", preset=t_name)
                    elif self.engine: self.engine.set_eq_preset(t_name)
                    self._notify(f"Equalizer Preset: {t_name}", 2.0)
                return
            elif data in (b"s", b"S"):
                self.eq_naming_mode = True
                self.eq_custom_name_input = ""
                return
            elif data in (b"r", b"R", b"0"):
                self.eq_manager.apply_preset("Flat")
                self._remember_eq_bands()
                if self.is_remote: self._send_cmd("set_eq_preset", preset="Flat")
                elif self.engine: self.engine.set_eq_preset("Flat")
                self._notify("Equalizer Reset to Flat (0 dB)", 2.0)
                return
            elif data in (b"d", b"D"):
                cur = self.eq_manager.active_preset
                if cur in self.eq_manager.custom_presets:
                    self.eq_manager.delete_custom_preset(cur)
                    if self.is_remote: self._send_cmd("delete_eq_preset", name=cur)
                    elif self.engine: self.engine.delete_eq_preset(cur)
                    self._notify(f"Deleted custom preset: {cur}", 2.5)
                return
            elif data in (b" ",):
                self._send_cmd("toggle")
                return
            elif data in (b"\x1b", b"E", b"\r", b"\n", b"q", b"Q"):
                self.equalizer_hud_active = False
                return
        if self.inspector_hud_active:
            if data in (b"g", b"G"): return self._generate_selected_spek()
            if data in (b"\x1b", b"i", b"I", b"\r", b"\n", b"q", b"Q", b" "): self.inspector_hud_active = False; return
        if getattr(self, "_playlist_clear_armed", False) and not self.equalizer_hud_active and not self.inspector_hud_active:
            if data in (b"d", b"D", b"\x1b[3~"):
                self._confirm_clear_playlist()
                return
            if data == b"\x1b":
                self._playlist_clear_armed = False
                self._notify("Playlist clear cancelled.", 2.0)
                return
            self._playlist_clear_armed = False
        if data in (b"i", b"I"): self.inspector_hud_active = not self.inspector_hud_active; return
        if data in (b"E",) and self.ui_size_mode not in COMPACT_UI_SIZES:
            self.equalizer_hud_active = not self.equalizer_hud_active
            if self.equalizer_hud_active: self.inspector_hud_active = False
            return
        if data in (b"\x1b[A", b"\x1bOA", b"k"): self._navigate_list(-1)
        elif data in (b"\x1b[B", b"\x1bOB", b"j"): self._navigate_list(1)
        elif data == b"J":
            # Shift+J exports the Favorites playlist; lowercase j still scrolls.
            if self.mode == "FAVORITES":
                self._favorites_export_begin()
            else:
                self._navigate_list(1)
        elif data == b"I":
            self._favorites_history_import_begin()
        elif data == b"\x1b[5~": self._navigate_list(-5)
        elif data == b"\x1b[6~": self._navigate_list(5)
        elif data in (b"\x1b[H", b"\x1b[1~", b"\x1b[7~", b"\x1bOH"): self.selected_row = 0
        elif data in (b"\x1b[F", b"\x1b[4~", b"\x1b[8~", b"\x1bOF"):
            if flt := self._get_active_tracks(): self.selected_row = len(flt) - 1
        elif data in (b"\x1b[D", b"\x1bOD"):
            # Left/Right always seek, even with the MP Visualizer on behind the
            # player. Preset stepping lives in the visualizer window itself; the
            # player keeps 0-9 for themes, so an arrow never changes a preset.
            self._send_cmd("seek", seconds=-10.0)
        elif data in (b"\x1b[C", b"\x1bOC"):
            self._send_cmd("seek", seconds=10.0)
        elif data in (b"\r", b"\n"):
            self._play_selected_row()
        elif data == b" ": self._send_cmd("toggle")
        elif data in (b"n", b"N", b">"): self._send_cmd("next")
        elif data in (b"p", b"P", b"<"): self._send_cmd("prev")
        elif data in (b"+", b"="): self._send_cmd("pitch_up", step=1)
        elif data in (b"-", b"_"): self._send_cmd("pitch_down", step=1)
        elif data == b"V" and os.environ.get("MP_LIQUID_GLASS") == "1":
            self._toggle_mp_visualizer()
        elif os.environ.get("MP_LIQUID_GLASS") == "1" and self.mp_visualizer_on and len(data) == 1 and data in b"0123456789":
            self._select_mp_visualizer_theme(int(data))
        elif data == b"0": self._send_cmd("reset_pitch")
        elif data in (b"]", b"}", b"v"):
            self._send_cmd("vol_up", step=5)
            self.cached_status.update(volume=min(100, int(self.cached_status.get("volume", 85)) + 5), muted=False)
        elif data in (b"[", b"{", b"V"):
            self._send_cmd("vol_down", step=5)
            self.cached_status.update(volume=max(0, int(self.cached_status.get("volume", 85)) - 5), muted=False)
        elif data in (b"u", b"U"):
            self._send_cmd("mute")
            im = self.engine.muted if (self.engine and not self.is_remote) else not self.cached_status.get("muted", False)
            self.cached_status["muted"] = im
            self._notify(f"Audio: {'MUTED' if im else 'UNMUTED'}", 2.0)
        elif data in (b"s", b"S"):
            self._send_cmd("shuffle")
            so = self.engine.shuffle if (self.engine and not self.is_remote) else not self.cached_status.get("shuffle", False)
            self.cached_status["shuffle"] = so
            self._notify(f"Shuffle: {'ON' if so else 'OFF'}", 2.5)
        elif data in (b"m", b"M"):
            if os.environ.get("MP_LIQUID_GLASS") == "1":
                # One ring for every size: Normal, Vertical, Mini, Micro and
                # back to Normal, so the full frame is always reachable.
                self.ui_size_mode = ui_size_step(self.ui_size_mode)
                self._publish_liquid_ui_size()
                self._notify(f"Liquid Glass Mode: {self.ui_size_mode.title()}", 2.0)
            elif self._sized_frame_at_launch or self.ui_size_mode != "NORMAL":
                # A sized frame resizes in place, including when the ring has
                # passed through NORMAL on its way round. Only the plain
                # console player asks for a compact window.
                self.ui_size_mode = ui_size_step(self.ui_size_mode)
                t_cols, t_rows = ui_size_grid(self.ui_size_mode)
                resize_host_terminal(t_cols, t_rows, UI_SIZE_TITLES[self.ui_size_mode])
            else:
                ok = launch_mini_player_window(
                    self.mode,
                    self.folder_name,
                    getattr(self.scanner, "playlist_path", "") or "",
                    self.visualizer.theme,
                    self.visualizer.style,
                )
                self._notify("✦ Launched MP Mini Audio Player in new window" if ok else "Failed to open Mini Player window", 3.0)
        elif data in (b"r", b"R"): self._toggle_recording()
        elif data in (b"l", b"L"):
            ok = launch_liquid_glass_player(
                base_dir=getattr(self.scanner, "base_dir", None),
                mode=self.mode,
                folder_name=self.folder_name,
                playlist_path=str(getattr(self.scanner, "playlist_path", "") or ""),
                theme=self.visualizer.theme,
                style=self.visualizer.style,
                mini=(self.ui_size_mode == "MINI"),
                micro=(self.ui_size_mode == "MICRO"),
                vertical=(self.ui_size_mode == "VERTICAL"),
            )
            self._notify("✦ Launched MP Audio Player (Liquid Glass)" if ok else "Failed to open Liquid Glass Player window", 3.0)
        elif data in (b"z", b"Z"):
            if sys.platform.startswith("linux") and os.environ.get("MP_LIQUID_GLASS") == "1":
                self.mp_visualizer_on = True
                preset = self._publish_mp_visualizer()
                self._ensure_standalone_mp_visualizer(preset)
                self._notify(
                    f"✦ MP Liquid Visualizer behind player — {preset.get('name', 'Cosmic Dreamworlds (Liquid)')}",
                    3.5,
                )
            else:
                ok = launch_liquid_visualizer(base_dir=getattr(self.scanner, "base_dir", None))
                self._notify(f"✦ Launched MP Liquid Visualizer [Cosmic Dreamworlds (Liquid)] — {_liquid_visualizer_size_label()} Vulkan" if ok else "Failed to launch MP Liquid Visualizer", 3.5)
        elif data in (b"y", b"Y"):
            self._send_cmd("repeat")
            modes = ["all", "one", "off"]
            cr = self.engine.repeat_mode if (self.engine and not self.is_remote) else self.cached_status.get("repeat", "all")
            nr = modes[(modes.index(cr) + 1) % 3] if cr in modes else "all"
            self.cached_status["repeat"] = nr
            self._notify(f"Repeat: {nr.upper()}", 2.5)
        elif (data in (b"e", b"E") if self.ui_size_mode in COMPACT_UI_SIZES else data == b"e"):
            self.visualizer.cycle_style()
            self._notify(f"Visualizer: {self.visualizer.get_style_display_name()}", 2.5)
        elif data in (b"c", b"C"):
            self.visualizer.cycle_theme()
            # Remember our own write so the poll loop does not repaint it as
            # an external change on the next tick.
            self._theme_seen = self.visualizer.theme
            self._notify_theme()
            apply_terminal_theme_background(self.visualizer.theme)
        elif data in (b"b", b"B"):
            # The Bass Boost dial: Normal -> Medium -> High, matching
            # the Crusher ANC 2 dial (press to cycle presets).
            self._cycle_bass_mode(1)
        elif data in (b"\x1b", b"\x03"):
            self.running = False
        elif data in (b"a", b"A"):
            if self.is_remote:
                result = send_ipc_command("route_bluetooth")
                if result is None and BluetoothAudioRouter is not None:
                    # The daemon can remain alive after its IPC socket has
                    # disappeared. Route the live player stream directly
                    # rather than reporting that connected headphones are
                    # absent just because the control socket is stale.
                    try:
                        result = {"routed": BluetoothAudioRouter().route_to_bluetooth()}
                    except Exception as exc:
                        result = {"routed": False, "message": f"Bluetooth routing failed: {exc}"}
                result = result or {}
                bi = result.get("bluetooth", {})
                c_str = f" ({bi.get('bluetooth_codec')})" if bi.get("bluetooth_codec") else ""
                message = result.get("message") or (f"🎧 Bluetooth Audio Routed: {bi.get('bluetooth_device')}{c_str}" if result.get("routed") else "No Bluetooth audio device connected.")
                self._notify(message, 4.5)
            elif self.engine and self.engine.bt_router:
                result = self.engine.route_to_bluetooth()
                bi = result.get("bluetooth") or self.engine.bt_router.get_status()
                c_str = f" ({bi.get('bluetooth_codec')})" if bi.get("bluetooth_codec") else ""
                message = result.get("message") or (f"🎧 Bluetooth Audio Routed: {bi.get('bluetooth_device')}{c_str}" if result.get("routed") else "No Bluetooth audio device connected.")
                self._notify(message, 4.5)
            elif self.engine:
                result = self.engine.route_to_bluetooth()
                self._notify(result.get("message", "Bluetooth audio routing is unavailable."), 4.5)
        elif data == b"1": self._switch_library_mode("MIX_ARCHIVE")
        elif data == b"2": self._switch_library_mode("MUSIC")
        elif data == b"3": self._load_favorites()
        # H toggles Favorites: that is the binding the README documents and
        # that has shipped since 0.4.2. Shift+F is an alias, and lowercase f
        # reveals the selected file. Hidden Mode therefore needs a modifier in
        # the terminal player (Ctrl+H, byte 0x08); the other hosts, which have
        # no text entry to collide with, bind it to plain H.
        elif data in (b"h", b"H"): self._toggle_favorite()
        elif data == b"F": self._toggle_favorite()
        elif data == b"\x08": self._toggle_hidden_mode()
        elif data in (b"o", b"O"): self._open_audio_file()
        elif data == b"T" and os.environ.get("MP_LIQUID_GLASS") == "1":
            self._notify("Traktor playlist import is available in CLI mode only.", 3.5)
        elif data == b"T" and sys.stdin.isatty():
            # Shift+T opens the Traktor playlist importer. Lowercase t stays on
            # the tracklist viewer. The picker is terminal-only: the Liquid
            # Glass hosts have no decision-window UI for it.
            self._traktor_picker_open()
        elif data in (b"t", b"T"): self._view_selected_tracklist()
        elif data == b"w" and os.environ.get("MP_LIQUID_GLASS") == "1":
            # W opens an MP Black Glass pane at exactly this window's size.
            # Shift+W keeps the playlist export on the same letter, so nothing
            # is lost; outside the glass window W is still the export key.
            self._open_black_glass()
        elif data in (b"w", b"W"): self._export_playlist()
        elif data == b"/": self.search_mode, self.search_query = True, ""
        elif data == b"f": self._open_selected_in_file_manager()
        elif data in (b"g", b"G") and os.environ.get("MP_LIQUID_GLASS") == "1":
            ok = launch_mp_gui(base_dir=getattr(self.scanner, "base_dir", None))
            self._notify("✦ Opened MP Gui" if ok else "Failed to open MP Gui", 3.0)
        elif data in (b"g", b"G"): self._generate_selected_spek()
        elif data in (b"q", b"Q"): self._queue_selected()
        elif data in (b"d", b"D", b"\x1b[3~") and not self.equalizer_hud_active and not self.inspector_hud_active:
            self._arm_playlist_clear()
        elif data in (b"x", b"X"):
            # X opens MP Liquid Mixer, matching MP Gui and the console manager.
            # Esc, q and Ctrl+C remain the exit keys. A mixer that is already
            # open is raised rather than duplicated: the mixer holds
            # liquid_mixer.lock for its whole life, because its two decks live
            # in fixed IPC namespaces.
            if os.environ.get("MP_LIQUID_GLASS_MIXER") == "1":
                # This player *is* one of the mixer's decks, so there is
                # nothing to open, and certainly not a second window.
                self._notify(_liquid_mixer_notice("already-open"), 3.5)
                return
            result = launch_liquid_mixer(getattr(self.scanner, "base_dir", None))
            if result == "unsupported":
                # A text console (TTY Audio Mode, or no X11) has nowhere to put
                # the mixer, so X keeps its old stop-and-exit meaning there
                # instead of leaving the player with a key that does nothing.
                try:
                    self._send_cmd("stop")
                except Exception:
                    pass
                self.running = False
                return
            self._notify(_liquid_mixer_notice(result), 3.5)

    def _navigate_list(self, delta: int):
        if m := self._get_active_tracks(): self.selected_row = max(0, min(len(m) - 1, self.selected_row + delta))

    def _follow_visualizer_preset(self):
        """Adopt a preset chosen inside the MP Visualizer window.

        The bonded visualizer and the player share one preset, and a key pressed
        while the visualizer has focus is published to the shared state file.
        Reading it back here (at most twice a second) keeps the player's header
        and the ``[0-9] theme`` hint on the preset that is actually on screen.
        """
        if not (getattr(self, "mp_visualizer_on", False) and os.environ.get("MP_LIQUID_GLASS") == "1"):
            return
        now = time.time()
        if now - getattr(self, "_visualizer_preset_poll_at", 0.0) < 0.5:
            return
        self._visualizer_preset_poll_at = now
        try:
            state = _mp_visualizer_api().read_player_visualizer_state()
        except Exception:
            return
        if not state.get("active"):
            return
        try:
            preset_id = int(state.get("preset_id") or 0)
        except (TypeError, ValueError):
            return
        if preset_id == int(getattr(self, "mp_visualizer_preset_id", 0) or 0):
            return
        self.mp_visualizer_preset_id = preset_id
        global _LIQUID_FRAME_CACHE
        _LIQUID_FRAME_CACHE = None
        self._notify(f"Visualizer theme {preset_id}: {state.get('preset_name', '')}", 2.5)

    def _follow_shared_theme(self):
        """Adopt a theme selected in MP Gui or the console manager.

        The shared file is one short line, read at most once a second, and the
        raw text is compared with the last value seen. Comparing the text
        rather than the mtime means two equal-length theme names written in the
        same second (dreamworlds_ultra and cosmic_supernova are both 17 bytes)
        still register. Without this the player kept its old colours when C was
        pressed elsewhere, which made the playlist navigation bar disagree
        between windows.
        """
        now = time.time()
        if now - getattr(self, "_theme_poll_at", 0.0) < 1.0:
            return
        self._theme_poll_at = now
        try:
            seen = (CONFIG_DIR / "theme").read_text(encoding="utf-8").strip().lower()
        except OSError:
            return
        if not seen or seen == getattr(self, "_theme_seen", None):
            return
        self._theme_seen = seen
        if self.visualizer.sync_theme_from_file():
            global _LIQUID_FRAME_CACHE
            _LIQUID_FRAME_CACHE = None
            self._notify_theme()
            apply_terminal_theme_background(self.visualizer.theme)

    def _present(self, lines: List[str], w: int, h: int):
        """Paint a frame and keep the recorded click rows aligned with it."""
        plan = _fit_frame_plan(len(lines), h)
        if plan is not None:
            total = len(lines)
            self._remap_click_rows(plan, total)
            lines = _fit_frame(lines, h)
        _present_frame(lines, w, h)

    def _draw_frame(self):
        w, h = _win_visible_size((80, 24))
        w, h, st = max(40, w), max(5, h), self._get_status()
        self._arm_visualizer_text()
        if self.ui_size_mode == "MINI": return self._draw_mini_frame(w, h, st)
        if self.ui_size_mode == "MICRO": return self._draw_micro_frame(w, h, st)
        self._sync_initial_selection(st)
        lines = []
        is_bt, bt_dev = bool(st.get("bluetooth_connected", False)), st.get("bluetooth_device", "")
        if self._last_seen_bt_state is not None:
            if is_bt and not self._last_seen_bt_state:
                c = st.get("bluetooth_codec", "")
                c_sfx = f" ({c})" if c else ""
                self._notify(f"🎧 Auto-switched to Bluetooth: {bt_dev}{c_sfx}", 4.0)
            elif not is_bt and self._last_seen_bt_state: self._notify("🔊 Bluetooth disconnected — Reverted to System Audio", 4.0)
        self._last_seen_bt_state, self._last_seen_bt_device = is_bt, bt_dev
        iw = max(20, w - 6)
        pal = self.visualizer.get_theme_palette()
        is_lg = bool(os.environ.get("MP_LIQUID_GLASS"))
        text_col = theme_body_ansi(self.visualizer.theme) if is_lg else theme_console_body_ansi(self.visualizer.theme)
        # Unselected playlist rows. The console pane is the pale still-water
        # field, so those rows use the theme hue pulled down until it reads.
        # Into Darkness is the black void and keeps white. Liquid Glass rows
        # stay white on that same field, and black on The Whitest White; the
        # glass host sets its own terminal foreground.
        if is_lg:
            light_pane = self.visualizer.theme == "the_whitest_white"
        else:
            light_pane = theme_has_light_console(self.visualizer.theme)
        if light_pane and is_lg:
            playlist_text_col = theme_body_ansi(self.visualizer.theme)
        elif light_pane:
            playlist_text_col = theme_console_body_ansi(self.visualizer.theme)
        else:
            playlist_text_col = WHITE
        g_key = "MP Gui" if is_lg else "Spek"
        bold_s = "" if is_lg else BOLD
        hl_col, act_col = pal.get("highlight", COLOR_HOT_PURPLE), pal.get("active", COLOR_NEON_PINK)
        b_col = pal.get("border", COLOR_HOT_PURPLE) if is_lg else f"{BOLD}{pal.get('border', COLOR_HOT_PURPLE)}"
        accent_col = pal.get("accent", COLOR_LILAC)
        if self.mode == "FAVORITES":
            btitle = "✦ MP AUDIO PLAYER (LIQUID GLASS) — FAVORITES ✦" if is_lg else "✦ MP AUDIO PLAYER — FAVORITES PLAYLIST ✦"
            bsub = "Favorites Playlist  •  [H] marks a mix or track  •  [3] or the button opens it" if is_lg else "Favorites  •  Mixes and music tracks you marked"
        elif is_lg:
            btitle = "✦ MP AUDIO PLAYER (LIQUID GLASS) — MUSIC LIBRARY ✦" if self.mode == "MUSIC" else "✦ MP AUDIO PLAYER (LIQUID GLASS) — LIQUID AUDIO MODE ✦"
            bsub = f"Liquid Glass Music Library  •  {music_library_label(self.folder_name)}" if self.mode == "MUSIC" else "Liquid Glass Translucent Surface  •  Fluid Audio Navigation"
        else:
            btitle = "✦ MP AUDIO PLAYER — MUSIC MODE (TRACKS LIBRARY) ✦" if self.mode == "MUSIC" else "✦ MP AUDIO PLAYER — DREAMWORLDS ULTRA AUDIO MODE ✦"
            bsub = f"Music Library  •  {music_library_label(self.folder_name)}  •  Local Drives & SMB Shares" if self.mode == "MUSIC" else "Dreamworlds Productions  •  Hot Purple & Traffic Light Edition"
        if is_lg and self.mp_visualizer_on:
            preset = self._mp_visualizer_preset()
            btitle = f"✦ MP LIQUID GLASS VISUALIZER — {preset.get('id', 0)} {preset.get('name', '')} ✦"
            bsub = "Press 0-9 to change the visualizer theme  •  Shift+V returns to liquid glass"
        # A portrait column is narrower than the wording these headers were
        # written for, so the title and subtitle are chosen from progressively
        # shorter forms rather than sliced mid-word.
        if self.mode == "FAVORITES":
            btitle_forms = [btitle, "✦ MP AUDIO PLAYER — FAVORITES ✦", "✦ FAVORITES ✦"]
            bsub_forms = [bsub, "Favorites Playlist", "Favorites"]
        elif self.mode == "MUSIC":
            btitle_forms = [btitle, "✦ MP AUDIO PLAYER — MUSIC ✦", "✦ MUSIC ✦"]
            bsub_forms = [bsub, music_library_label(self.folder_name), "Music Library"]
        else:
            btitle_forms = [btitle, "✦ MP AUDIO PLAYER (LIQUID GLASS) ✦", "✦ MP AUDIO PLAYER ✦", "✦ MP LIQUID ✦"]
            bsub_forms = [bsub, "Liquid Glass Translucent Surface", "Liquid Glass", "Glass"]
        btitle = next((t for t in btitle_forms if _display_width(t) <= iw), btitle_forms[-1])
        bsub = next((t for t in bsub_forms if _display_width(t) <= iw), bsub_forms[-1])
        btitle, bsub = _clip_display(btitle, max(0, iw - 1)), _clip_display(bsub, max(0, iw - 1))
        # Side bars stay on the border colour. Accent colours the subtitle text only,
        # so the header edge is one colour all the way around.
        lines.extend([
            f"  {b_col}╭{'─'*iw}╮{NC}",
            # Reset after the title ink, then re-apply the border, so the
            # closing bar carries the border colour alone. Emitting the bar
            # before the reset left it painted with border+title codes stacked,
            # which is why one side of the header differed from the other three.
            f"  {b_col}│{NC}{text_col}{btitle.center(iw)}{NC}{b_col}│{NC}",
            f"  {b_col}│{NC}{bold_s}{accent_col}{bsub.center(iw)}{NC}{b_col}│{NC}",
            f"  {b_col}╰{'─'*iw}╯{NC}",
        ])

        dim_s = DIM
        tname, maxlen = st.get("title", "Unknown Mix"), max(58, iw - 20)
        if len(tname) > maxlen: tname = tname[:maxlen - 3] + "..."
        parts = os.path.splitext(st.get("path", ""))
        ch_str, sr, ext = ("Stereo" if st.get("channels", 2) == 2 else "Mono"), st.get("sample_rate", 44100), parts[1][1:].upper() or "FLAC"
        # The console ▶ PLAYING label is white on every theme. Liquid Glass
        # still picks black on the pale pane and a light ink on the charcoal
        # panes. PAUSED/STOPPED keep their amber/red so the three states stay
        # distinguishable at a glance.
        play_ink = playing_badge_ink(self.visualizer.theme, is_lg)
        badge = f"{bold_s}{play_ink}▶ PLAYING{NC}" if st.get("state") == "playing" else (f"{bold_s}{YELLOW}⏸ PAUSED{NC}" if st.get("state") == "paused" else f"{bold_s}{RED}⏹ STOPPED{NC}")
        if self.mode == "FAVORITES":
            mode_b = f" {bold_s}{COLOR_TRAFFIC_AMBER}[FAVORITES]{NC}"
        elif self.mode == "MUSIC":
            mode_b = f" {bold_s}{COLOR_TRAFFIC_GREEN}[MUSIC MODE]{NC}"
        else:
            mode_b = f" {dim_s}{COLOR_HOT_PURPLE}[MIX ARCHIVE]{NC}"
        is_rec, flash = bool(st.get("recording", False)), (int(time.time() * 1.5) % 2 == 0)
        rec_b = f"  {bold_s}{COLOR_TRAFFIC_RED}● REC{NC}" if (is_rec and flash) else (f"  {dim_s}{RED}○ REC{NC}" if is_rec else "")
        fav_now = f" {COLOR_TRAFFIC_AMBER}{favorite_glyph(True)}{NC}" if st.get("path") and self._favorites_store().is_favorite(st.get("path")) else ""
        # The title is the one line read from across the room, so it gets the
        # highest-contrast ink the pane allows instead of a theme hue: black on
        # the pale glass, white on the charcoal themes. The ♫ carries the same
        # ink so the pair reads as one label rather than a pink bullet beside
        # a white title. `is_lg` picks the surface: the console pane under the
        # same theme is usually dark, which is the opposite answer.
        title_ink = now_playing_ink(self.visualizer.theme, is_lg)
        # Trim the title to the space the badges leave, measured on the row as
        # drawn; a fixed character budget cannot know how wide `▶ PLAYING` and
        # the mode chip are, which is why a portrait column ran over.
        title_head = f"  {badge}{mode_b}{rec_b}  {bold_s}{title_ink}♫  "
        tname = st.get("title", "Unknown Mix") or "Unknown Mix"
        avail = max(6, iw - _display_width(title_head) - _display_width(fav_now))
        if _display_width(tname) > avail:
            tname = _clip_display(tname, max(1, avail - 3)) + "..."
        lines.append(f"{title_head}{tname}{NC}{fav_now}")
        if is_rec:
            rf, rmb, rdur = os.path.basename(st.get("recording_file", "")), float(st.get("recording_bytes", 0)) / 1048576.0, format_seconds(float(st.get("recording_duration", 0.0)))
            rdot = f"{bold_s}{COLOR_TRAFFIC_RED}●{NC}" if flash else f"{dim_s}{RED}○{NC}"
            lines.append(f"  {rdot} {bold_s}{COLOR_TRAFFIC_RED}RECORDING TO WAV:{NC} {bold_s}{COLOR_ELECTRIC_CYAN}{rf}{NC} {dim_s}[{rmb:.1f} MB  •  {rdur}  •  Max 3GB/WAV]{NC}")
        out_n = bt_dev or st.get("output_device", "Speakers")
        # PortAudio names the ALSA plugin "default", which says nothing about
        # where the sound actually goes. Show the system sink's own name when
        # that is all PortAudio gave us.
        if not bt_dev:
            out_n = output_device_label(out_n, _system_output_sink_name())
        bt_b = f"{bold_s}{COLOR_NEON_PINK}🎧 Output: {out_n}{NC}" if (is_bt or "bluetooth" in str(out_n).lower()) else f"{dim_s}{CYAN}🔊 Output: {out_n}{NC}"
        s4_b = f"  │  {bold_s}\033[38;2;0;255;127m🎛️ S4 MIDI [hw:0,0]{NC}" if (CONFIG_DIR / "traktor_s4_midi.pid").exists() else ""
        src_badge = " Lossless" if (w >= 116 and is_lossless_path(st.get("path", ""))) else ""
        # Three widths, so a portrait column does not run past its border: the
        # full line, then without the device name, then without the output.
        lines.append(_fit_text_line([
            f"  {dim_s}{CYAN}Format: {ext}{src_badge}  │  Sample Rate: {sr/1000:.1f} kHz  │  Channels: {ch_str}  │  {bt_b}{s4_b}{NC}",
            f"  {dim_s}{CYAN}Format: {ext}  │  {sr/1000:.1f} kHz  │  {bt_b}{NC}",
            f"  {dim_s}{CYAN}Format: {ext}  │  {sr/1000:.1f} kHz  │  {ch_str}{NC}",
            f"  {dim_s}{CYAN}{ext} {sr/1000:.1f} kHz{NC}",
        ], max(20, w - 1)))

        # The bar keeps at least 12 cells and gives up the rest to the labels,
        # so `MM:SS ... MM:SS (nn.n%)` still fits a 56-column portrait window.
        pct = float(st.get("progress_pct", 0.0))
        label_w = 2 + _display_width(st.get("position_fmt", "00:00")) * 2 + 6 + 2
        bw = max(12, min(max(54, iw - 42), max(12, iw - label_w)))
        fw = int((pct / 100.0) * bw)
        # The track itself is the seek target. The time labels and the
        # percentage are not: `3 + len(POSITION) + 2` is the two leading spaces,
        # the `MM:SS`, and the two spaces that separate it from the track.
        # Rows are the 1-based line index SGR mouse reports, and the span is an
        # inclusive 1-based (first, last) column pair.
        timeline_row = len(lines) + 1
        timeline_start = 3 + _display_width(st.get("position_fmt", "00:00")) + 2
        timeline_span = (timeline_start, timeline_start + bw - 1)
        lines.append(f"  {bold_s}{COLOR_ELECTRIC_CYAN}{st.get('position_fmt', '00:00')}{NC}  {b_col}{'━' * fw + '●' + '─' * max(0, bw - fw - 1)}{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}{st.get('duration_fmt', '00:00')}{NC}  {text_col}({pct:.1f}%){NC}")
        pitch_pfx, pitch_trk, pitch_sfx = self.visualizer.pitch_bar_parts(int(st.get("pitch_percent", 0)), width=bw, max_cols=max(20, w - 1))
        pitch_row = len(lines) + 1
        pitch_start = _display_width(pitch_pfx) + 1
        pitch_span = (pitch_start, pitch_start + _display_width(pitch_trk) - 1)
        lines.extend([f"{pitch_pfx}{pitch_trk}{pitch_sfx}", ""])
        # A landscape window keeps its familiar 7/5/3 analyser so the playlist
        # below is untouched. The portrait column has height to spare, so there
        # the analyser takes what the fixed rows leave, with the playlist
        # keeping the larger share so it stays a playlist.
        if self.ui_size_mode == "VERTICAL":
            spare = max(0, h - 17 - 9)
            analyser_h = max(3, min(20, 8 + spare // 3))
        else:
            analyser_h = 7 if h >= 34 else (5 if h >= 28 else 3)
        lines.extend(self.visualizer.render(st, width=w, height=analyser_h) + [""])

        active_tracks = self._get_active_tracks()
        prompt_line = self._favorites_prompt_line()
        if prompt_line:
            lines.append(prompt_line)
        elif self.export_mode: lines.append(f"  {bold_s}{COLOR_TRAFFIC_AMBER}Export Playlist (.m3u) Path: [{self.export_path}█]  (Enter: Save, Esc: Cancel){NC}")
        elif self.search_mode: lines.append(f"  {bold_s}{CYAN}Filter / Search: [{self.search_query}█]{NC}")
        elif self.search_query: lines.append(f"  {bold_s}Search Filter: '{self.search_query}' (Press '/' to search)")
        elif time.time() < self.notification_time and self.notification_msg: lines.append(f"  {self._notification_ansi(bold_s)}📂 {self.notification_msg}{NC}")

        self._reset_click_targets()
        self._timeline_row, self._timeline_span = timeline_row, timeline_span
        self._pitch_row, self._pitch_span = pitch_row, pitch_span
        self._append_player_buttons(lines, max(20, w - 1))

        if self.inspector_hud_active: lines.extend(self._render_inspector_hud(st, iw))
        elif self.equalizer_hud_active: lines.extend(self._render_equalizer_hud(st, iw, h))
        else:
            q_len, q_paths = int(st.get("queue_len", 0)), list(st.get("queue", []))
            qb = f" • Queue: {q_len}" if q_len > 0 else ""
            # The title is part of the panel's top rule, so it has to fit the
            # column. A narrow portrait window used to slice it mid-word
            # (`... (14 Mixes A┐`), which reads as a broken border rather than
            # a heading. Each mode offers progressively shorter wordings and
            # the longest one that fits is used; the counts survive longest
            # because they are the part that changes.
            n_tracks = len(active_tracks)
            if self.mode == "FAVORITES":
                list_title = _fit_text_line([
                    f"── FAVORITES PLAYLIST ({n_tracks} saved{qb}) ",
                    f"── FAVORITES ({n_tracks} saved{qb}) ",
                    f"── FAVORITES ({n_tracks}{qb}) ",
                    f"── FAVORITES ",
                ], iw)
            elif self.mode == "MUSIC":
                library = music_library_label(self.folder_name)
                list_title = _fit_text_line([
                    f"── MUSIC LIBRARY: {library} ({n_tracks} Tracks{qb}) ",
                    f"── MUSIC: {library} ({n_tracks}{qb}) ",
                    f"── MUSIC ({n_tracks} tracks{qb}) ",
                    f"── MUSIC LIBRARY ",
                ], iw)
            else:
                list_title = _fit_text_line([
                    f"── MP MIX ARCHIVE PLAYLIST ({n_tracks} Mixes Available{qb}) ",
                    f"── MIX ARCHIVE PLAYLIST ({n_tracks} mixes{qb}) ",
                    f"── MIX ARCHIVE ({n_tracks} mixes{qb}) ",
                    f"── MIXES ({n_tracks}{qb}) ",
                    "── PLAYLIST ",
                ], iw)
            # The title text is part of the rule, so it shares the border ink.
            # Passing the body colour here left the title's own `──` and `─`
            # glyphs in a different hue from the rest of the panel outline.
            lines.append(_boxed_rule("┌", list_title, "┐", iw, b_col, b_col))
            # Bottom rule, one blank row, the Vol status line, and the control
            # rows. MP Liquid reserves one row less: its playlist footer rule
            # sits a row further down than the CLI's, and the playlist body
            # takes that row, so one more track is visible.
            reserve = 4 if is_lg else 5
            vrows = max(0, h - len(lines) - reserve)
            if vrows <= 0: self.scroll_offset = max(0, self.selected_row)
            elif self.selected_row < self.scroll_offset: self.scroll_offset = self.selected_row
            elif self.selected_row >= self.scroll_offset + vrows: self.scroll_offset = self.selected_row - vrows + 1
            avail_name = max(4, iw - 25)
            if self._favorites_decision_active():
                # A Favorites decision window takes over the list area so the
                # user cannot miss it and cannot touch the playlist behind it.
                box = self._favorites_decision_box(iw)
                for row in box:
                    lines.append(row)
                for _ in range(max(0, vrows - len(box))):
                    lines.append(f"  {b_col}│{NC}{' ' * iw}{b_col}│{NC}")
            elif not active_tracks:
                if self.mode == "FAVORITES":
                    em1, em2 = "Favorites playlist is empty.", "Select a mix or track and press [H] to add it here."
                elif self.mode == "MUSIC":
                    em1, em2 = "No audio tracks found in the Music playlist.", "Configure Music folders with main menu option 2. Mix Archive folders use option 13."
                else:
                    em1, em2 = "No audio mixes found in the Mix Archive playlist.", "Configure Mix Archive folders with main menu option 13. Music folders use option 2."
                # The guidance lines are longer than a portrait column, so they
                # are wrapped into the panel instead of running past its border.
                empty_rows = []
                for em in (em1, em2):
                    for chunk in _wrap_display(em, max(8, iw - 2)):
                        empty_rows.append(chunk)
                for em in empty_rows[:max(2, vrows)]:
                    lines.append(f"  {b_col}│{NC} {bold_s}{COLOR_TRAFFIC_AMBER}{em}{NC}{' ' * max(0, iw - _display_width(em) - 2)}{b_col}│{NC}")
                for _ in range(max(0, vrows - 2)): lines.append(f"  {b_col}│{NC}{' ' * iw}{b_col}│{NC}")
            else:
                # A tall Liquid Glass window paints dozens of these rows every
                # 20 ms, and only the equaliser above them is actually moving.
                # Each row is a pure function of the key below, so a repeat is
                # the same bytes. Duration is resolved first: a 00:00 music row
                # keeps asking, and a duration that arrives changes the key.
                fav_store = self._favorites_store()
                row_cache = getattr(self, "_playlist_line_cache", None)
                if row_cache is None:
                    row_cache = self._playlist_line_cache = {}
                q_index = {}
                for _qi, _qp in enumerate(q_paths):
                    if _qp not in q_index:
                        q_index[_qp] = _qi
                theme_name = self.visualizer.theme
                for i in range(self.scroll_offset, min(len(active_tracks), self.scroll_offset + vrows)):
                    m = active_tracks[i]
                    path = m["path"]
                    act, sel = (path == st.get("path")), (i == self.selected_row)
                    qi = q_index.get(path)
                    in_q = qi is not None
                    rn = str(m.get("name", ""))
                    if act:
                        pfx = f"{PLAYLIST_ACTIVE_MARK} "
                    elif in_q:
                        pfx = f"Q{qi + 1}" if qi < 9 else "Q*"
                    else:
                        pfx = "  "
                    ext_s = f"{m.get('ext', ''):<4}"[:4]
                    if self.mode == "MUSIC":
                        dur = m.get("duration_fmt")
                        if not dur or dur == "00:00":
                            if act and st.get("duration_fmt"): dur = m["duration_fmt"] = self._duration_cache[path] = st["duration_fmt"]
                            elif m.get("duration_sec"): dur = m["duration_fmt"] = self._duration_cache[path] = format_seconds(m["duration_sec"])
                            elif path in self._duration_cache: dur = m["duration_fmt"] = self._duration_cache[path]
                            else: dur = "00:00"; self._request_duration(m)
                        f_str = f"{dur:>6s}"
                    else:
                        f_str = f"{m.get('size_mb', 0):.0f}MB".rjust(6)
                    fav = fav_store.is_favorite(path)
                    # A selected or playing row carries animated ink on the
                    # rainbow themes, so its key is stamped with the frame's
                    # 0.1-second tick. Every other row keeps a stable key and
                    # stays cached.
                    bar_tick = round(self.visualizer.frame_time(), 1)
                    cache_key = (i, path, rn, ext_s, f_str, act, sel, in_q, pfx, iw, avail_name, theme_name, bold_s, self.mode, fav, _LIMITED_GLYPHS,
                                 bar_tick if (sel or act) else None)
                    # Mouse hit-testing reads these every frame, including when
                    # the painted line itself is reused.
                    self._playlist_star_col = _display_width(f"  │ {pfx}[{i+1:4d}] ") + 1
                    self._playlist_star_rows[len(lines) + 1] = i
                    cached_line = row_cache.get(cache_key)
                    if cached_line is not None:
                        lines.append(cached_line)
                        continue
                    mn = (_clip_display(rn, max(1, avail_name - 3)) + "..." if avail_name > 3 else _clip_display(rn, avail_name)) if _display_width(rn) > avail_name else rn
                    npad = " " * max(0, avail_name - _display_width(mn))
                    if len(f_str) > 6:
                        ex_l = len(f_str) - 6
                        npad = npad[:-ex_l] if len(npad) >= ex_l else ""
                    # Row ink, resolved before the star is built so the star can
                    # restore it. The star used to end with a plain NC reset,
                    # which wipes every attribute: the mix name after it then
                    # fell back to VTE's default foreground, near-black
                    # (24,26,34) on the pale pane. That is why playlist rows
                    # still read as black on every theme after the row ink
                    # itself was made white -- only the star was white.
                    sel_bg = sel_fg = ""
                    if sel:
                        sel_bg, sel_fg = _selection_bar_ansi(theme_name)
                    row_ink = sel_fg if sel else (act_col if act else playlist_text_col)
                    star = favorite_glyph(fav)
                    if star.strip():
                        # White star, then put the row's own fill and ink back.
                        star = f"{WHITE}{star}{NC}{sel_bg}{bold_s}{row_ink}"
                    ip = f" {pfx}[{i+1:4d}] {star} {mn}{npad} {ext_s} {f_str} "
                    cw = _display_width(ip)
                    ip = ip + " " * (iw - cw) if cw < iw else (_clip_display(ip, iw) if cw > iw else ip)
                    if sel:
                        # The selection bar follows the active theme instead of a
                        # fixed grey (console) or reverse video (Liquid Glass).
                        line = f"  {b_col}│{NC}{sel_bg}{sel_fg}{ip}{NC}{b_col}│{NC}"
                    # The playing row is tinted with the theme's `active` hue so
                    # it is distinguishable from the cursor row (which carries the
                    # selection fill). The tint stops at the row text: both panel
                    # bars must stay the border colour so the box outline is
                    # unbroken on every row.
                    elif act:
                        line = f"  {b_col}│{NC}{bold_s}{act_col}{ip}{NC}{b_col}│{NC}"
                    elif in_q:
                        line = f"  {b_col}│{NC}{bold_s}{playlist_text_col}{ip}{NC}{b_col}│{NC}"
                    else:
                        line = f"  {b_col}│{NC}{playlist_text_col}{ip}{NC}{b_col}│{NC}"
                    if len(row_cache) >= 4096:
                        row_cache.clear()
                    row_cache[cache_key] = line
                    lines.append(line)
            if self.mode == "FAVORITES":
                list_footer = "── ↑/↓: Scroll  •  Enter: Play  •  Export Tracks  •  [H]: Mark  •  [D]: Clear  •  [1]: Mix Archive  •  [2]: Music  •  Q: Queue  •  /: Search "
            elif self.mode == "MUSIC":
                list_footer = f"── ↑/↓: Scroll • Enter: Play • [1] Mix • [2] Music • [3] Favorites • [H] Mark • Q: Queue • D: Clear • G: {g_key} • /: Search "
            else:
                list_footer = f"── ↑/↓: Scroll  •  Enter: Play  •  [1] Mix Archive  •  [2] Music  •  [3] Favorites  •  [H] Mark  •  Q: Queue  •  D: Clear  •  G: {g_key}  •  O: Open  •  T: Tracklist "
            # The footer is part of the rule, so it shares the border ink and
            # the panel outline stays one colour all the way around.
            lines.append(_boxed_rule("└", list_footer, "┘", iw, b_col, b_col))
            if self.mode == "FAVORITES":
                # Clickable "Export Tracks" label on the list footer, when the
                # clipped footer still shows it. SGR mouse columns are 1-based.
                label = "Export Tracks"
                label_at = list_footer.find(label)
                if label_at >= 0 and label_at + len(label) <= iw:
                    start = 4 + label_at
                    self._fav_export_button = (len(lines), start, start + len(label) - 1)
                else:
                    self._fav_export_button = (0, 0, 0)

        # One blank row keeps the Vol footer off the playlist edge. MP Liquid
        # asks for a second so a tall glass window still has breathing room;
        # the trim below drops it again on a short console instead of taking a
        # control line. Its playlist footer sits one row lower than the CLI's
        # because the playlist body claims that row (see `reserve` above).
        spacer_at = len(lines)
        lines.append("")
        spacers = 1
        if is_lg:
            lines.append("")
            spacers = 2
        hint_col = WHITE if is_lg else DIM
        shuf = f"{bold_s}{COLOR_TRAFFIC_GREEN}ON{NC}" if st.get("shuffle") else f"{hint_col}OFF{NC}"
        rep = f"{bold_s}{COLOR_TRAFFIC_GREEN}{st.get('repeat', 'all').upper()}{NC}"
        vol = (f"{bold_s}{COLOR_ELECTRIC_CYAN}{st.get('volume', 85)}%{NC}" if not st.get("muted") else f"{bold_s}{COLOR_TRAFFIC_RED}MUTED{NC}")
        pval = int(st.get("pitch_percent", 0))
        pcol = self.visualizer.pitch_control_colors()["knob"]
        pbadge = f"{bold_s}{pcol}{pval:+d}% ({1.0 + pval/100:.2f}x){NC}"
        eq_n = self.visualizer.get_style_display_name()
        th_n = self.visualizer.get_theme_display_name()
        eq_p = self.eq_manager.active_preset
        eq_p_col = COLOR_TRAFFIC_GREEN if eq_p != "Flat" else COLOR_ELECTRIC_CYAN
        bass_badge = bass_mode_badge(self._bass_mode(), bool(bold_s))
        lines.append(_fit_text_line([
            f"  🔊 Vol: {vol}  │  🎚 Pitch: {pbadge}  │  🎛 EQ: {bold_s}{eq_p_col}{eq_p}{NC}  │  {bass_badge}  │  📊 Vis: {bold_s}{COLOR_NEON_PINK}{eq_n}{NC}  │  🔀 Shuf: {shuf}  │  🔁 Rep: {rep}  │  🎨 {bold_s}{COLOR_HOT_PURPLE}{th_n}{NC}",
            f"  Vol: {vol}  │  Pitch: {pbadge}  │  EQ: {bold_s}{eq_p_col}{eq_p}{NC}  │  {bass_badge}  │  Vis: {bold_s}{COLOR_NEON_PINK}{eq_n}{NC}  │  Shuf: {shuf}  │  Rep: {rep}",
            f"  Vol {vol}  Pitch {pbadge}  EQ {eq_p}  BASS {self._bass_mode().upper()}  Vis {eq_n}  Shuf {shuf}  Rep {rep}",
            f"  Vol {vol}  Pitch {pbadge}  {eq_p}",
        ], max(20, w - 1)))
        if self.mode == "MUSIC":
            g_bottom = f"[G] {g_key}  " if is_lg else ""
            lines.extend([
                _fit_text_line([
                    f"  {bold_s}{COLOR_TRAFFIC_GREEN}TRACK CONTROLS:{NC} {bold_s}{COLOR_ELECTRIC_CYAN}[3]{NC} {bold_s}Favorites{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[H]{NC} {bold_s}Mark Fav{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[1]{NC} {bold_s}Mix Archive{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[2]{NC} {bold_s}Music{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[D]{NC} {bold_s}Clear Playlist{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[Space]{NC} {bold_s}Play/Pause{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[R]{NC} {bold_s}Record WAV{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[M]{NC} {bold_s}Vertical{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[L]{NC} {bold_s}Liquid Glass{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[Z]{NC} {bold_s}MP Liquid Visualizer{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[X]{NC} {bold_s}Liquid Mixer{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[Q]{NC} {bold_s}Queue{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[G]{NC} {bold_s}{g_key}{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[I]{NC} {bold_s}Track Inspector{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[E]{NC} {bold_s}Equalizer HUD{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[b]{NC} {bold_s}Bass Boost{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[/]{NC} {bold_s}Search{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[n/p]{NC} {bold_s}Skip{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[+/-]{NC} {bold_s}Pitch{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[v/V]{NC} {bold_s}Vol{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[u]{NC} {bold_s}Mute{NC}  {bold_s}{COLOR_ELECTRIC_CYAN}[Esc]{NC} {bold_s}Exit{NC}",
                    f"  {bold_s}{COLOR_TRAFFIC_GREEN}CONTROLS:{NC} {bold_s}[3] Favorites{NC}  {bold_s}[H] Mark{NC}  {bold_s}[1] Mix Archive{NC}  {bold_s}[2] Music{NC}  {bold_s}[D] Clear{NC}  {bold_s}[Space] Play{NC}  {bold_s}[R] Rec WAV{NC}  {bold_s}[M] Vertical{NC}  {bold_s}[L] Liquid{NC}  {bold_s}[Q] Queue{NC}  {bold_s}[G] {g_key}{NC}  {bold_s}[E] Equalizer{NC}  {bold_s}[b] Bass{NC}  {bold_s}[/] Search{NC}  {bold_s}[n/p] Skip{NC}  {bold_s}[u] Mute{NC}  {bold_s}[Esc] Exit{NC}",
                    f"  {bold_s}[3] Fav  [H] Mark  [1] Mix  [2] Music  [D] Clear  Space Play  [X] Mixer  [Q] Queue  [G] {g_key}  [E] EQ  [b] Bass  [/] Search  n/p Skip  Esc Exit{NC}"
                ], max(20, w - 1)),
                _fit_text_line([
                    f"  {hint_col}{g_bottom}[0] Pitch Reset  [u] Mute  [s] Shuffle  [y] Repeat  [b] Bass Boost  [L] Liquid Glass  [Z] MP Liquid Visualizer  [X] Liquid Mixer  [w] Glass  [E] Equalizer HUD  [e] Vis Style  [c] Color Theme  [a] Audio/BT  [O] Open  [F] Files{NC}",
                    f"  {hint_col}{g_bottom}0 Reset  u Mute  s Shuf  y Rep  b Bass  L Glass  Z Liquid Vis  X Mixer  W Glass  E Equalizer  e Vis  c Theme  a BT  O Open  F Files{NC}",
                    f"  {hint_col}{g_bottom}0 Reset  u Mute  s Shuf  y Rep  b Bass  L Glass  Z Vis  X Mixer  E EQ  e Vis  c Theme  Esc Exit{NC}"
                ], max(20, w - 1))
            ])
        else:
            # The export hint only belongs to the Favorites playlist. Mix
            # Archive has no Favorites set to export, so the words are dropped
            # there rather than advertising a key that does nothing.
            export_hint = "  Export Tracks" if self.mode == "FAVORITES" else ""
            lines.append(_fit_text_line([
                f"  {hint_col}[3] Favorites  [H] Mark Fav  [1] Mix Archive  [2] Music  [D] Clear Playlist{export_hint}  [Space] Play/Pause  [R] Record WAV  [M] Vertical  [L] Liquid Glass  [Z] MP Liquid Visualizer  [X] Liquid Mixer  [w] Black Glass  [Q] Queue  [G] {g_key}  [+/-] Pitch ±1% (±20% max)  [0] Pitch Reset  [v/V] Vol  [u] Mute  [s] Shuffle  [y] Repeat  [n/p] Next/Prev  [a] Audio/BT  [O] Open  [T] Tracklist  [W] Export  [F] Files  [E] Equalizer HUD  [b] Bass Boost  [e] Vis  [Esc] Exit{NC}",
                f"  {hint_col}[3] Favorites  [H] Mark  [1] Mix Archive  [2] Music  [D] Clear{export_hint}  Space Play  R Rec  M Vertical  L Glass  Z Vis  X Mixer  W Glass  Q Queue  G {g_key}  +/- Pitch  0 Reset  v/V Vol  u Mute  s Shuf  y Rep  n/p Skip  O Open  T List  E EQ  b Bass  Esc Exit{NC}",
                f"  {hint_col}[3] Fav  [H] Mark  [1] Mix  [2] Music  [D] Clear{export_hint}  Space Play  R Rec  M Vertical  X Mixer  W Glass  Q Queue  G {g_key}  v/V Vol  u Mute  s Shuf  y Rep  n/p Skip  E EQ  b Bass  Esc Exit{NC}",
                # The narrowest form still has to name the keys a user needs
                # to reach the other windows, so G stays.
                f"  {hint_col}Space Play  M Mini  G {g_key}  E EQ  b Bass  c Theme  Esc Exit{NC}"
            ], max(20, w - 1)))
        # Drop the breathing rows rather than a control line if the frame runs
        # past the console height. The Bass Boost row made this reachable at
        # the default 24-row size, in MUSIC mode.
        while len(lines) > h and spacers and lines[spacer_at] == "":
            del lines[spacer_at]
            spacers -= 1
        # The portrait column can be taller than its content when the playlist
        # has fewer entries than rows. The spare rows belong to the playlist,
        # above the two footer rows, so it fills the glass without a blank
        # strip under the key hints. A landscape frame is never padded.
        if self.ui_size_mode == "VERTICAL":
            while len(lines) < h:
                lines.insert(max(0, len(lines) - 2), "")
        if is_lg:
            lines = [_strip_bold_ansi(l) for l in lines]
        self._present(lines, w, h)

    def _draw_mini_frame(self, w: int, h: int, st: Dict[str, Any]):
        self._arm_visualizer_text()
        self._reset_click_targets()
        self._sync_initial_selection(st)
        t_now = time.time()
        g1, g2, _ = _get_dynamic_gradient(t_now)
        c1, c2 = f"\033[38;2;{g1[0]};{g1[1]};{g1[2]}m", f"\033[38;2;{g2[0]};{g2[1]};{g2[2]}m"
        target_rows, target_cols = 14, 74
        if (w != target_cols or h != target_rows) and sys.platform.startswith(("linux", "freebsd")) and not os.environ.get("KONSOLE_DBUS_SERVICE") and not os.environ.get("MP_LIQUID_GLASS"):
            sys.stdout.write(f"\033[8;{target_rows};{target_cols}t")
        _set_terminal_title("MP Mini Audio Player")
        iw = max(24, min(w - 4, 74))
        is_lg = bool(os.environ.get("MP_LIQUID_GLASS"))
        g_key = "MP Gui" if is_lg else "Spek"
        bold_s = "" if is_lg else BOLD
        text_col = theme_body_ansi(self.visualizer.theme) if is_lg else theme_console_body_ansi(self.visualizer.theme)
        if is_lg and self.mp_visualizer_on:
            header_title = "✦ MP LIQUID GLASS VISUALIZER ✦"
        else:
            header_title = '✦ MP MINI AUDIO PLAYER (LIQUID GLASS) ✦' if is_lg else '✦ MP MINI AUDIO PLAYER ✦'
        lines = [f"  {c1}╭{'─'*iw}╮{NC}", f"  {c1}│{c2}{header_title.center(iw)}{c1}│{NC}", f"  {c1}╰{'─'*iw}╯{NC}"] if is_lg else [f"  {BOLD}{c1}╭{'─'*iw}╮{NC}", f"  {BOLD}{c1}│{BOLD}{c2}{header_title.center(iw)}{c1}│{NC}", f"  {BOLD}{c1}╰{'─'*iw}╯{NC}"]
        tname, av = st.get("title", "Unknown Track"), max(18, iw - 18)
        if _display_width(tname) > av: tname = _clip_display(tname, av - 3) + "..."
        state = st.get("state", "stopped")
        badge = f"{bold_s}{playing_badge_ink(self.visualizer.theme, is_lg)}▶ PLAY{NC}" if state == "playing" else (f"{bold_s}\033[38;2;255;191;0m⏸ PAUSE{NC}" if state == "paused" else f"{bold_s}\033[38;2;255;64;64m⏹ STOP{NC}")
        is_rec, flash = bool(st.get("recording", False)), (int(t_now * 1.5) % 2 == 0)
        rec_b = f" {bold_s}\033[38;2;255;64;64m● REC{NC}" if (is_rec and flash) else (f" \033[38;2;255;100;100m○ REC{NC}" if is_rec else "")
        mode_str = 'MUSIC' if self.mode == 'MUSIC' else 'MIX'
        # Same rule as the full frame: the highest-contrast ink the pane allows,
        # so the title never washes out or turns into a theme hue.
        lines.append(f"  {badge} {bold_s}\033[38;2;0;229;255m[{mode_str}]{NC}{rec_b} {bold_s}{now_playing_ink(self.visualizer.theme, is_lg)}♫ {tname}{NC}")
        ext, sr, ch_s = os.path.splitext(st.get("path", ""))[1][1:].upper() or "FLAC", st.get("sample_rate", 44100), ("Stereo" if st.get("channels", 2) == 2 else "Mono")
        dev_s = f"{'🎧' if st.get('bluetooth_connected') else '🔊'} {st.get('output_device', 'Speakers')}"
        if _display_width(dev_s) > 22: dev_s = dev_s[:19] + "..."
        lines.append(f"  \033[38;2;120;215;255m{ext} • {sr/1000:.1f}kHz • {ch_s}{NC}  │  {text_col}{dev_s}{NC}")
        pct, bw = float(st.get("progress_pct", 0.0)), max(16, min(34, iw - 30))
        fw = int((pct / 100.0) * bw)
        bar_fill = f"\033[38;2;0;229;255m{'━' * fw}\033[38;2;255;255;255m●{NC}"
        bar_empty = f"\033[38;2;90;105;135m{'─' * max(0, bw - fw - 1)}{NC}"
        # Mini keeps its own compact bar, so it needs its own seek target. Two
        # leading spaces, the position label, one space, then the bar itself.
        mini_timeline_row = len(lines) + 1
        mini_bar_start = 4 + _display_width(st.get("position_fmt", "00:00"))
        mini_timeline_span = (mini_bar_start, mini_bar_start + bw - 1)
        lines.append(f"  \033[38;2;0;229;255m{st.get('position_fmt', '00:00')}{NC} {bar_fill}{bar_empty} \033[38;2;0;229;255m{st.get('duration_fmt', '00:00')}{NC} \033[38;2;255;215;0m({pct:.1f}%){NC}")
        vol, pval = (f"{st.get('volume', 85)}%" if not st.get("muted") else "\033[38;2;255;64;64mMUTED\033[0m"), int(st.get("pitch_percent", 0))
        knob_col = self.visualizer.pitch_control_colors()["knob"]
        p_str = f"{knob_col}0%{NC}" if pval == 0 else f"{knob_col}{pval:+d}%{NC}"
        lines.append(f"  🔊 \033[38;2;0;229;255m{vol}{NC} │ 🎚 {p_str} │ {bass_mode_badge(self._bass_mode(), bool(bold_s))} │ 🎛 {bold_s}{c2}{self.visualizer.get_style_display_name()[:12]}{NC} │ 🎨 {c1}{self.visualizer.get_theme_display_name()[:12]}{NC}")
        # The visualizer block is variable for the wide stereo styles: it can
        # return more rows than the height asked for. Mini is a fixed 14-row
        # window, so an over-long block used to push the bottom hint off the
        # frame and hide the [G] key. Reserve the footer rows, then keep only
        # as much of the visualizer as fits.
        footer_rows = 2   # the action row and the key-hint row
        vis_i = len(lines)
        lines.extend(self.visualizer.render(st, width=iw + 4, height=(3 if h >= 14 else 2)))
        room = max(1, h - vis_i - footer_rows)
        if len(lines) - vis_i > room:
            lines = lines[: vis_i + room]
        self._timeline_row, self._timeline_span = mini_timeline_row, mini_timeline_span
        self._append_player_buttons(lines, max(20, w - 1))
        prompt_line = self._favorites_prompt_line()
        if prompt_line:
            lines.append(_fit_text_line([prompt_line], max(20, w - 1)))
        elif t_now < self.notification_time and self.notification_msg:
            lines.append(f"  {self._notification_ansi(bold_s)}📂 {self.notification_msg}{NC}")
        else:
            g_early = f"\033[38;2;0;229;255m[G]{NC} {text_col}{g_key}{NC}  " if is_lg else ""
            _mini_hints = [
                f"  \033[38;2;0;229;255m[3]{NC} {self.visualizer.get_theme_palette().get('accent', '')}Favorites{NC}  \033[38;2;0;229;255m[H]{NC} {text_col}Mark{NC}  \033[38;2;0;229;255m[X]{NC} {text_col}Mixer{NC}  \033[38;2;0;229;255m[1]{NC} {text_col}Mix{NC}  \033[38;2;0;229;255m[2]{NC} {text_col}Music{NC}  \033[38;2;0;229;255m[D]{NC} {text_col}Clear{NC}  {g_early}\033[38;2;0;229;255m[Space]{NC} {text_col}Play{NC}  \033[38;2;0;229;255m[Q]{NC} {text_col}Queue{NC}  \033[38;2;0;229;255m[R]{NC} {text_col}Rec{NC}  \033[38;2;0;229;255m[b]{NC} {text_col}Bass{NC}  \033[38;2;0;229;255m[n/p]{NC} {text_col}Skip{NC}  \033[38;2;255;215;0m[M]{NC} {text_col}Micro{NC}  \033[38;2;255;64;64mEsc{NC} {text_col}Close{NC}",
                # A 74-column Mini window cannot hold the full list; keep the
                # controls a listener reaches for, including G for MP Gui.
                f"  [3] Fav  [H] Mark  [X] Mixer  Space Play  {g_early}\033[38;2;0;229;255m[b]{NC} Bass  [M] Micro  Esc Close",
                f"  Space Play  {g_early}[X] Mixer  [b] Bass  [M] Micro  Esc Close",
            ]
            lines.append(_fit_text_line(_mini_hints, max(20, w - 1)))
        if is_lg:
            lines = [_strip_bold_ansi(l) for l in lines]
        self._present(lines, w, h)

    def _draw_micro_frame(self, w: int, h: int, st: Dict[str, Any]):
        self._arm_visualizer_text()
        self._reset_click_targets()
        self._sync_initial_selection(st)
        t_now = time.time()
        g1, g2, _ = _get_dynamic_gradient(t_now)
        c1, c2 = f"\033[38;2;{g1[0]};{g1[1]};{g1[2]}m", f"\033[38;2;{g2[0]};{g2[1]};{g2[2]}m"
        target_rows, target_cols = 7, 74
        if (w != target_cols or h != target_rows) and sys.platform.startswith(("linux", "freebsd")) and not os.environ.get("KONSOLE_DBUS_SERVICE") and not os.environ.get("MP_LIQUID_GLASS"):
            sys.stdout.write(f"\033[8;{target_rows};{target_cols}t")
        _set_terminal_title("MP Micro Audio Player")
        iw, tname = max(24, min(w - 4, 70)), st.get("title", "Unknown Track")
        avail = max(10, iw - 43)
        if _display_width(tname) > avail: tname = _clip_display(tname, avail - 2) + ".."
        is_lg = bool(os.environ.get("MP_LIQUID_GLASS"))
        g_key = "MP Gui" if is_lg else "Spek"
        bold_s = "" if is_lg else BOLD
        text_col = theme_body_ansi(self.visualizer.theme) if is_lg else theme_console_body_ansi(self.visualizer.theme)
        state = st.get("state", "stopped")
        badge = f"{bold_s}{playing_badge_ink(self.visualizer.theme, is_lg)}▶{NC}" if state == "playing" else (f"{bold_s}\033[38;2;255;191;0m⏸{NC}" if state == "paused" else f"{bold_s}\033[38;2;255;64;64m⏹{NC}")
        cs, lv = self.visualizer.style, _as_float_vector(st.get("levels", [0.0] * 24), 0)
        if cs in ("rainbow_mandala_eq", "rainbow_wormhole_eq", "rainbow_aurora_eq",
                  "stream_of_frequency_liquid_eq", "stream_of_frequency_overview_eq"):
            # Each full-field effect owns its miniature: glyph and palette both
            # come from _fx_micro_meter, so the Micro meter matches the HUD.
            m_str = self.visualizer._fx_micro_meter(cs, t_now, lv)
        elif cs == "stream_of_frequency_eq":
            p_chars = []
            for i in range(14):
                v = float(lv[i % len(lv)]) if len(lv) else 0.0
                h_idx = int(((t_now * 0.65 + (13 - i) * 0.075 + v * 0.25) % 1.0) * 360) % 360
                col = _RAINBOW_ANSI_BOLD[h_idx] if v > 0.4 else _RAINBOW_ANSI_NORMAL[h_idx]
                if v > 0.70: g = "✦" if (int(t_now * 5.0) + i) % 2 == 0 else "≋"
                elif v > 0.35: g = "≈" if (int(t_now * 4.0) + i) % 2 == 0 else "∿"
                elif v > 0.10: g = "~"
                else: g = "·" if (int(t_now * 2.5) + i) % 3 == 0 else " "
                p_chars.append(f"{col}{console_symbol(g)}{NC}")
            m_str = "".join(p_chars)
        elif cs == "oscilloscope":
            wc = ("─", "∿", "~", "≈", "∽", "∿")
            m_str = "".join(f"{c2}{wc[max(0, min(5, int(abs(math.sin(i * 0.7 + t_now * 8.0) * (lv[i % len(lv)] if len(lv) else 0.0)) * 5)))] if (lv[i % len(lv)] if len(lv) else 0.0) > 0.05 else '─'}{NC}" for i in range(14))
        elif cs == "analog_vu":
            lb, rb = int(min(5, round((np.mean(st.get("levels_left", lv)) if len(lv) else 0.0) * 5))), int(min(5, round((np.mean(st.get("levels_right", lv)) if len(lv) else 0.0) * 5)))
            m_str = f"{c1}L{NC}{c2}{'█'*lb}\033[38;2;90;105;135m{'░'*(5-lb)}{NC} {c1}R{NC}{c2}{'█'*rb}\033[38;2;90;105;135m{'░'*(5-rb)}{NC}"
        elif cs == "stardust_particles_eq":
            dc = [console_symbol(ch) for ch in (" ", "·", "˙", ":", "⋆", "✦", "◆", "█")]
            m_str = "".join(f"{c2}{dc[min(len(dc) - 1, int(val * len(dc)))]}{NC}" for val in lv[:14])
        elif cs == "neon_prism_eq":
            m_str = "".join(f"{self.visualizer._get_neon_prism_color(i / 13.0, val)}{prism_level_glyph(min(8, int(val * 9)))}{NC}" for i, val in enumerate(lv[:14]))
        else: m_str = "".join(f"{c2}{prism_level_glyph(min(8, int(val * 9)))}{NC}" for val in lv[:14])

        is_rec, flash = bool(st.get("recording", False)), (int(t_now * 1.5) % 2 == 0)
        rec_d = f" {bold_s}\033[38;2;255;64;64m●{NC}" if (is_rec and flash) else (f" \033[38;2;255;100;100m○{NC}" if is_rec else "")
        pval = int(st.get("pitch_percent", 0))
        p_txt = f"{pval:+d}%" if pval != 0 else "0%"
        vol_str = f"{st.get('volume', 85)}%" if not st.get("muted") else "\033[38;2;255;64;64mMUTED\033[0m"
        knob_col = self.visualizer.pitch_control_colors()["knob"]
        # Same rule as the full frame: contrast beats the theme hue, because the
        # title is read at a glance and the pane decides which ink is legible.
        # The pitch readout is the exception: it follows the theme accent, the
        # same ink as the full-frame knob, so C recolours it here too.
        r1 = f" {badge}{rec_d} {now_playing_ink(self.visualizer.theme, is_lg)}{tname}{NC} │ \033[38;2;0;229;255m{st.get('position_fmt', '00:00')}/{st.get('duration_fmt', '00:00')}{NC} │ Vol: \033[38;2;0;229;255m{vol_str}{NC} │ {knob_col}Pitch: {p_txt}{NC} "
        next_mode_lbl = "Full" if is_lg else "Vertical"
        r2 = f" 🎛 [{m_str}] │ {bass_mode_badge(self._bass_mode(), bool(bold_s))} │ 🎨 {c1}{self.visualizer.get_theme_display_name()[:10]}{NC} │ \033[38;2;255;215;0m[M] {next_mode_lbl}{NC} "
        if is_lg and self.mp_visualizer_on:
            micro_hdr = "✦ MP LIQUID GLASS VISUALIZER ✦"
        else:
            micro_hdr = "✦ MP MICRO AUDIO PLAYER (LIQUID GLASS) ✦" if is_lg else "✦ MP MICRO AUDIO PLAYER ✦"
        if is_lg:
            lines = [
                f"  {c1}╭─ {micro_hdr} {'─' * max(0, iw - _display_width(micro_hdr) - 4)}╮{NC}",
                f"  {c1}│{NC}{r1}{' ' * max(0, iw - _display_width(r1))}{c1}│{NC}",
                f"  {c1}│{NC}{r2}{' ' * max(0, iw - _display_width(r2))}{c1}│{NC}",
                f"  {c1}╰{'─' * iw}╯{NC}"
            ]
        else:
            lines = [
                f"  {BOLD}{c1}╭─ ✦ MP MICRO AUDIO PLAYER ✦ {'─' * max(0, iw - 28)}╮{NC}",
                f"  {BOLD}{c1}│{NC}{r1}{' ' * max(0, iw - _display_width(r1))}{BOLD}{c1}│{NC}",
                f"  {BOLD}{c1}│{NC}{r2}{' ' * max(0, iw - _display_width(r2))}{BOLD}{c1}│{NC}",
                f"  {BOLD}{c1}╰{'─' * iw}╯{NC}"
            ]
        if h >= 9:
            # Same shape of bug as Mini: the visualizer block is variable and
            # can return more rows than asked for, which pushes the action row
            # off a short Micro window. Keep the rows that fit.
            vis_i = len(lines)
            lines.extend(self.visualizer.render(st, width=iw + 4, height=(2 if h >= 12 else 1)))
            room = max(1, h - vis_i - 1)
            if len(lines) - vis_i > room:
                lines = lines[: vis_i + room]
        if t_now < self.notification_time and self.notification_msg: lines.append(f"  {self._notification_ansi(bold_s)}📂 {self.notification_msg}{NC}")
        else:
            m_hint = "[M] Full" if is_lg else "[M] Vertical"
            m_hint_short = "M Full" if is_lg else "M Vertical"
            g_micro = f"\033[1;38;2;0;229;255m[G]{NC} {text_col}{g_key}{NC}  " if is_lg else ""
            lines.append(_fit_text_line([
                f"  \033[1;38;2;0;229;255m[D]{NC} {text_col}Clear{NC}  \033[1;38;2;0;229;255m[X]{NC} {text_col}Mixer{NC}  \033[1;38;2;0;229;255m[Space]{NC} {text_col}Play{NC}  \033[1;38;2;0;229;255m[Q]{NC} {text_col}Que{NC}  \033[1;38;2;0;229;255m[R]{NC} {text_col}Rec{NC}  \033[1;38;2;0;229;255m[G]{NC} {text_col}{g_key}{NC}  \033[1;38;2;0;229;255m[n/p]{NC} {text_col}Skip{NC}  \033[1;38;2;0;229;255m[v/V]{NC} {text_col}Vol{NC}  \033[1;38;2;0;229;255m[s]{NC} {text_col}Shuf{NC}  \033[1;38;2;0;229;255m[e]{NC} {text_col}EQ{NC}  \033[1;38;2;0;229;255m[b]{NC} {text_col}Bass{NC}  \033[1;38;2;0;229;255m[c]{NC} {text_col}Theme{NC}  \033[1;38;2;255;215;0m{m_hint}{NC}  \033[1;38;2;255;64;64mEsc{NC} {text_col}Close{NC}",
                f"  \033[1;38;2;0;229;255m[D]{NC} {text_col}Clear{NC}  \033[1;38;2;0;229;255m[X]{NC} {text_col}Mixer{NC}  \033[1;38;2;0;229;255m[Space]{NC} {text_col}Play{NC}  \033[1;38;2;0;229;255m[Q]{NC} {text_col}Que{NC}  \033[1;38;2;0;229;255m[R]{NC} {text_col}Rec{NC}  \033[1;38;2;0;229;255m[n/p]{NC} {text_col}Skip{NC}  \033[1;38;2;0;229;255m[v/V]{NC} {text_col}Vol{NC}  \033[1;38;2;0;229;255m[s]{NC} {text_col}Shuf{NC}  \033[1;38;2;0;229;255m[e]{NC} {text_col}EQ{NC}  \033[1;38;2;0;229;255m[b]{NC} {text_col}Bass{NC}  \033[1;38;2;0;229;255m[c]{NC} {text_col}Theme{NC}  \033[1;38;2;255;215;0m{m_hint}{NC}  \033[1;38;2;255;64;64mEsc{NC} {text_col}Close{NC}",
                f"  \033[1;38;2;0;229;255mD{NC} {text_col}Clear{NC}  \033[1;38;2;0;229;255mX{NC} {text_col}Mix{NC}  \033[1;38;2;0;229;255mSpace{NC} {text_col}Play{NC}  \033[1;38;2;0;229;255mQ{NC} {text_col}Que{NC}  \033[1;38;2;0;229;255mR{NC} {text_col}Rec{NC}  {g_micro}\033[1;38;2;0;229;255mn/p{NC} {text_col}Skip{NC}  \033[1;38;2;0;229;255mv/V{NC} {text_col}Vol{NC}  \033[1;38;2;0;229;255ms{NC} {text_col}Shuf{NC}  \033[1;38;2;0;229;255me{NC} {text_col}EQ{NC}  \033[1;38;2;0;229;255mb{NC} {text_col}Bass{NC}  \033[1;38;2;0;229;255mc{NC} {text_col}Theme{NC}  \033[1;38;2;255;215;0m{m_hint_short}{NC}  \033[1;38;2;255;64;64mEsc{NC} {text_col}Close{NC}"
            ], max(20, w - 1)))
        self._append_player_buttons(lines, max(20, w - 1))
        if is_lg:
            lines = [_strip_bold_ansi(l) for l in lines]
        self._present(lines, w, h)

def _spawn_player_daemon(file_path: Optional[str] = None, auto_bluetooth: bool = True, mode: str = "MIX_ARCHIVE", music_folder: Optional[str] = None, playlist_path: Optional[str] = None, seek_to: Optional[float] = None, no_autoplay: bool = False) -> Optional[int]:
    if is_player_daemon_running():
        # A live PID is not enough: older/crashed startup paths can leave a
        # daemon holding the playback lock after its IPC listener disappeared.
        # Reuse a responsive daemon, but retire that orphan so playback can be
        # started by a fresh process.
        if send_ipc_command("status"):
            return None
        try:
            if time.time() - PID_FILE.stat().st_mtime < 120.0:
                # Give a newly launched daemon time to scan a large library
                # and bind its IPC socket before treating it as orphaned.
                return None
            daemon_pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            if sys.platform == "win32":
                return None
            cmdline = subprocess.run(
                ["ps", "-p", str(daemon_pid), "-o", "args="],
                capture_output=True, text=True, timeout=1.0, check=False
            ).stdout
            if "MP_Audio_Player.py" not in cmdline or "--daemon" not in cmdline:
                return None
            os.kill(daemon_pid, signal.SIGTERM)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                try:
                    os.kill(daemon_pid, 0)
                except OSError:
                    break
                time.sleep(0.05)
            else:
                # Don't start another player while the unresponsive process
                # still owns the audio engine and daemon lock.
                return None
        except (OSError, ValueError, ProcessLookupError):
            pass
    _cleanup_stale_daemon_files()
    cmd = [sys.executable, str(Path(__file__).resolve()), "--daemon"] + ([] if auto_bluetooth else ["--no-bluetooth-auto"])
    if no_autoplay:
        cmd.append("--no-autoplay")
    if mode == "MUSIC":
        cmd.append("--music-mode")
        if music_folder: cmd.extend(["--music-folder", str(music_folder)])
        if playlist_path: cmd.extend(["--playlist", str(playlist_path)])
    if seek_to is not None and float(seek_to) > 0:
        cmd.extend(["--seek-to", str(seek_to)])
    cleaned = _existing_audio_path(file_path) if file_path else ""
    if cleaned:
        cmd.append(cleaned)
    try:
        proc = subprocess.Popen(cmd, cwd=str(get_base_dir()), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        # Auto-start Traktor S4 MIDI controller service if configured
        try:
            s4_script = Path(__file__).resolve().parent / "scripts" / "traktor_s4_midi.py"
            if not s4_script.is_file(): s4_script = Path(__file__).resolve().parent / "traktor_s4_midi.py"
            if s4_script.is_file() and os.environ.get("MIDI_CONTROL_ENABLED", "true").lower() != "false":
                subprocess.Popen([sys.executable, str(s4_script), "--start"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception: pass
        return proc.pid
    except Exception as exc:
        try: mp_log_error("MP_Audio_Player", f"Failed spawning player daemon: {exc}", exc)
        except Exception: pass
        return None

def _install_windows_console_close():
    if sys.platform != "win32": return
    import ctypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    cb = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)(lambda c: (os._exit(0) if c in (2, 5, 6) else 0))
    k.SetConsoleCtrlHandler(cb, 1)

def _start_headset_controls(engine) -> None:
    """Tell the desktop that headset play/pause belongs to this player.

    Without an MPRIS registration the physical button is delivered to
    whatever video player or browser registered last, and a video starts.
    """
    if not sys.platform.startswith("linux"):
        return
    if PLAYER_NAMESPACE:
        # A mixer deck is a second engine on the same bus name; only the
        # default player claims the desktop media keys.
        return
    try:
        from mp_mpris import start_mpris_service
    except ImportError:
        try:
            from scripts.mp_mpris import start_mpris_service
        except ImportError:
            return
    try:
        start_mpris_service(engine)
    except Exception as exc:
        try:
            mp_log_error("MP_Audio_Player", f"Headset media keys unavailable: {exc}", exc)
        except Exception:
            pass

def main():
    p = argparse.ArgumentParser(description=f"MP Audio Player (MP Mix Manager v{VERSION}) - High Resolution Playback & Custom Animated Equalizer")
    p.add_argument("--version", "-v", action="version", version=f"MP Audio Player (MP Mix Manager v{VERSION})")
    p.add_argument("file", nargs="?", help="Audio file path to play immediately")
    for flags, kw in [
        (("--daemon",), dict(action="store_true")), (("--interactive", "-i"), dict(action="store_true")),
        (("--play",), dict(nargs="?", const="")), (("--pause",), dict(action="store_true")),
        (("--toggle",), dict(action="store_true")), (("--stop",), dict(action="store_true")),
        (("--next",), dict(action="store_true")), (("--prev",), dict(action="store_true")),
        (("--pitch",), dict(type=int)), (("--pitch-up",), dict(type=int, nargs="?", const=1)),
        (("--pitch-down",), dict(type=int, nargs="?", const=1)), (("--pitch-reset",), dict(action="store_true")),
        (("--vol",), dict(type=str)), (("--seek",), dict(type=float)),
        (("--seek-to", "--position"), dict(type=float, default=None)),
        (("--resume",), dict(action="store_true")),
        # A mixer deck opens ready to play but silent: no resume, no first-run
        # sample, no "latest mix". Space (or the player's own Play) starts it.
        (("--no-autoplay",), dict(action="store_true")),
        (("--status",), dict(action="store_true")), (("--status-line",), dict(action="store_true")),
        (("--play-latest",), dict(action="store_true")), (("--get-latest-mix",), dict(action="store_true")),
        (("--list-mixes",), dict(action="store_true")), (("--open-file-manager", "-f"), dict(action="store_true")),
        (("--tracklist", "-t"), dict(action="store_true")), (("--export-playlist", "-e"), dict(nargs="?", const="default")),
        (("--tracklist-export",), dict(type=str, choices=("html", "text", "txt"), default=None,
                                       help="Export the tracklist of the selected mix to HTML or TEXT in TRACKLIST_EXPORTS")),
        (("--style", "--eq"), dict(type=str, choices=EqualizerVisualizer.STYLES)), (("--theme",), dict(type=str, choices=EqualizerVisualizer.THEMES)),
        (("--cmd",), dict(type=str)), (("--music-mode",), dict(action="store_true")), (("--mix-archive-mode",), dict(action="store_true")),
        (("--music-folder",), dict(type=str, default="ALL")), (("--playlist",), dict(type=str, default="")),
        (("--no-bluetooth-auto",), dict(action="store_true")), (("--bluetooth-status",), dict(action="store_true")),
        (("--bluetooth-route",), dict(action="store_true")), (("--mini",), dict(action="store_true")),
        (("--micro",), dict(action="store_true")), (("--vertical",), dict(action="store_true")),
        (("--spek", "--generate-spek", "-g"), dict(action="store_true")),
        (("--new-window", "--window", "-w"), dict(action="store_true")), (("--only-if-running",), dict(action="store_true")),
        (("--shuffle",), dict(action="store_true")), (("--repeat",), dict(action="store_true")),
        (("--liquid", "--liquid-glass", "--glass"), dict(action="store_true", help="Launch MP Audio Player in Liquid Glass mode")),
        (("--visualizer", "--liquid-visualizer"), dict(action="store_true", help="Launch MP Liquid Visualizer (1094x734 on macOS, the Liquid Player size on Linux, 700x700 elsewhere)")),
        (("--visualizer-preset",), dict(type=str, default=None, help="Select MP Liquid Visualizer preset (e.g. 'Cosmic Dreamworlds (Liquid)')")),
        (("--list-visualizer-presets",), dict(action="store_true", help="List available MP Liquid Visualizer presets")),
        (("--queue", "-q"), dict(type=str, nargs="?", const="")), (("--clear-queue",), dict(action="store_true")),
        (("--record", "--toggle-record", "-r"), dict(action="store_true")),
        (("--eq-preset",), dict(type=str, help="Set active equalizer preset (Flat, Ambient, Trance, Techno, House, Progressive, Vapourwave, or custom)")),
        (("--list-eq-presets",), dict(action="store_true", help="List all available equalizer presets and their dB curves")),
        (("--eq-bands",), dict(type=str, help="Set 10-band equalizer gains in dB (e.g. '3.0,2.5,1.5,0,-1,-1.5,-0.5,1,2,2.5')")),
        (("--reset-eq",), dict(action="store_true", help="Reset equalizer to Flat preset (0 dB on all bands)")),
        (("--bass-mode", "--bass"), dict(type=str, help="Set MP Bass Boost mode (Normal, Medium, High)")),
        (("--cycle-bass",), dict(action="store_true", help="Step the MP Bass Boost dial up one mode")),
        (("--list-bass-modes",), dict(action="store_true", help="List the MP Bass Boost modes and their boost levels"))
    ]: p.add_argument(*flags, **kw)

    args, _ = p.parse_known_args()
    base_dir = get_base_dir()
    is_music = bool(args.music_mode or args.playlist or (args.music_folder and args.music_folder != "ALL"))
    requested_music = is_music
    current_mode, folder_target, auto_bt = ("MUSIC" if is_music else "MIX_ARCHIVE"), (args.music_folder if is_music else "ALL"), not args.no_bluetooth_auto

    if args.get_latest_mix:
        if latest := MixArchiveScanner(base_dir).get_latest_mix(): print(latest["path"]); sys.exit(0)
        sys.exit(1)

    if args.list_mixes:
        scanner = MusicFolderPlaylistScanner(folder_target, playlist_path=args.playlist) if is_music else MixArchiveScanner(base_dir)
        for m in scanner.get_mixes():
            info_col = m.get("duration_fmt", "00:00") if is_music else f"{m['size_mb']:.1f}MB"
            print(f"{m['path']}\t{info_col}\t{m['name']}")
        sys.exit(0)

    daemon_running = is_player_daemon_running()

    if args.bluetooth_status:
        if daemon_running: print(json.dumps((send_ipc_command("bluetooth_status") or {}).get("bluetooth", {}), indent=2))
        elif get_bluetooth_audio_status: print(json.dumps(get_bluetooth_audio_status(), indent=2))
        else: print(json.dumps({"bluetooth_connected": False, "error": "Router unavailable"}, indent=2))
        sys.exit(0)

    if args.bluetooth_route:
        if daemon_running:
            res = send_ipc_command("route_bluetooth")
            print("✔ Sent route command to player daemon:" if (res and res.get("routed")) else "✖ Failed to route to Bluetooth:")
            print(json.dumps(res or {}, indent=2))
        elif BluetoothAudioRouter:
            print("✔ Routed system audio to Bluetooth" if BluetoothAudioRouter().route_to_bluetooth() else "✖ Failed to route to Bluetooth")
        sys.exit(0)

    if args.open_file_manager or args.tracklist or args.tracklist_export:
        tp = (st.get("data", {}).get("path") if (daemon_running and (st := send_ipc_command("status"))) else None)
        if not tp or not os.path.exists(tp):
            if latest := MixArchiveScanner(base_dir).get_latest_mix(): tp = latest["path"]
        if args.open_file_manager:
            target = choose_file_manager_target(tp)
            ok = bool(target) and open_in_file_manager(target)
            print(f"✔ Opened in file manager: {target}" if ok else "✖ Failed to open file manager")
            sys.exit(0 if ok else 1)
        tl = find_tracklist_for_mix(tp) if tp else None
        if args.tracklist_export:
            if tl and os.path.isfile(tl):
                ok, res = export_tracklist_file(tl, args.tracklist_export, base_dir=base_dir)
                print(f"✔ Exported tracklist ({args.tracklist_export}): {res}" if ok else f"✖ Failed to export tracklist: {res}",
                      file=sys.stdout if ok else sys.stderr)
                sys.exit(0 if ok else 1)
            print("✖ Tracklist not found" if tp else "✖ No mix available", file=sys.stderr)
            sys.exit(1)
        if tl and os.path.isfile(tl) and open_tracklist_window(tl, tp, theme=args.theme): print(f"✔ Opened tracklist: {tl}"); sys.exit(0)
        print("✖ Tracklist not found" if tp else "✖ No mix available", file=sys.stderr); sys.exit(1)

    if args.export_playlist is not None:
        mixes = MixArchiveScanner(base_dir).get_mixes()
        dest = os.path.expanduser("~/Desktop/MP_Archive_Playlist.m3u") if (args.export_playlist in ("default", "") or not args.export_playlist) else args.export_playlist
        ok, res = export_playlist_m3u(mixes, dest)
        print(f"✔ Exported {len(mixes)} mixes to .m3u playlist: {res}" if ok else f"✖ Failed to export playlist: {res}")
        sys.exit(0 if ok else 1)

    if args.spek:
        tp = os.path.abspath(args.file) if (args.file and os.path.exists(args.file)) else ((st.get("data", {}).get("path") if (daemon_running and (st := send_ipc_command("status"))) else None))
        if not tp:
            if is_music:
                if mixes := MusicFolderPlaylistScanner(folder_target, playlist_path=args.playlist).get_mixes(): tp = mixes[0]["path"]
            elif latest := MixArchiveScanner(base_dir).get_latest_mix(): tp = latest["path"]
        if not tp or not os.path.isfile(tp): print("✖ No audio file available for Spek generation", file=sys.stderr); sys.exit(1)
        if (ex := find_existing_spek(tp, mode=current_mode)) and os.path.isfile(ex):
            print(f"✔ Spek already exists ({os.path.getsize(ex) // 1024} KB): {ex}")
            open_image_viewer(ex)
            sys.exit(0)
        print(f"MP Audio Player is generating a Spek for {os.path.basename(tp)}. It will open when finished.")
        notify_desktop("MP Audio Player", f"MP Audio Player is generating a Spek for {os.path.basename(tp)}")
        ok, res = generate_track_spek(tp, mode=current_mode)
        if ok:
            print(f"✔ Spek generated successfully ({os.path.getsize(res) // 1024} KB): {res}")
            open_image_viewer(res)
            sys.exit(0)
        print(f"✖ Spek generation failed: {res}", file=sys.stderr); sys.exit(1)

    if args.list_visualizer_presets:
        vis_script = base_dir / "MP_Liquid_Visualizer.py"
        if not vis_script.is_file():
            vis_script = base_dir / "scripts" / "mp_liquid_visualizer_vulkan.py"
        sys.exit(subprocess.call([sys.executable, str(vis_script), "--list-presets"]))

    if args.visualizer_preset and not args.visualizer:
        vis_script = base_dir / "MP_Liquid_Visualizer.py"
        if not vis_script.is_file():
            vis_script = base_dir / "scripts" / "mp_liquid_visualizer_vulkan.py"
        sys.exit(subprocess.call([sys.executable, str(vis_script), "--set-preset", str(args.visualizer_preset)]))

    if args.visualizer:
        ok = launch_liquid_visualizer(base_dir, preset=args.visualizer_preset)
        if ok:
            p_label = args.visualizer_preset or "Active Preset"
            vis_size = _liquid_visualizer_size_label()
            print(f"✔ Launched MP Liquid Visualizer [{p_label}] — {vis_size} Fixed Borderless Vulkan")
            sys.exit(0)
        print("✖ Failed to launch MP Liquid Visualizer", file=sys.stderr)
        sys.exit(1)

    if args.liquid:
        sys.exit(
            0
            if launch_liquid_glass_player(
                base_dir,
                mode=("MUSIC" if args.music_mode else ("MIX_ARCHIVE" if args.mix_archive_mode else None)),
                folder_name=(args.music_folder if is_music else None),
                playlist_path=(args.playlist or None),
                theme=args.theme,
                style=args.style,
                mini=bool(args.mini),
                micro=bool(args.micro),
            )
            else 1
        )

    if args.new_window:
        if daemon_running:
            try:
                st = send_ipc_command("status")
                if st and isinstance(st.get("data"), dict):
                    d_data = st["data"]
                    if d_mode := d_data.get("mode"):
                        if not args.music_mode and not args.mix_archive_mode:
                            current_mode = d_mode
                            is_music = (current_mode == "MUSIC")
            except Exception: pass
        if args.mini or args.micro:
            sys.exit(0 if launch_mini_player_window(current_mode, folder_target, args.playlist, args.theme, args.style, micro=bool(args.micro)) else 1)
        sys.exit(0 if launch_player_window(current_mode, folder_target, args.playlist, args.theme, args.style) else 1)

    if args.cmd:
        if res := send_ipc_command(args.cmd): print(json.dumps(res)); sys.exit(0)
        sys.exit(1)

    if args.play_latest:
        if latest := MixArchiveScanner(base_dir).get_latest_mix():
            if not (daemon_running and send_ipc_command("play", file=latest["path"])):
                _spawn_player_daemon(latest["path"], auto_bluetooth=auto_bt, mode=current_mode)
            sys.exit(0)
        sys.exit(1)

    if args.toggle:
        if daemon_running and send_ipc_command("toggle"): sys.exit(0)
        res_data = None
        if RESUME_STATE_FILE.is_file():
            try: res_data = json.loads(RESUME_STATE_FILE.read_text(encoding="utf-8"))
            except Exception: pass
        if res_data and res_data.get("path") and os.path.isfile(res_data["path"]):
            target_file = res_data["path"]
            target_pos = float(res_data.get("position_sec", 0.0) or 0.0)
            if daemon_running and send_ipc_command("play", file=target_file, position=target_pos):
                sys.exit(0)
            if not args.only_if_running:
                _spawn_player_daemon(target_file, auto_bluetooth=auto_bt, mode=res_data.get("mode", current_mode), seek_to=target_pos)
                sys.exit(0)
        if not args.only_if_running and (latest := MixArchiveScanner(base_dir).get_latest_mix()):
            _spawn_player_daemon(latest["path"], auto_bluetooth=auto_bt, mode=current_mode)
        sys.exit(0)

    if args.pause or args.stop or args.next or args.prev:
        if daemon_running: send_ipc_command("pause" if args.pause else ("stop" if args.stop else ("next" if args.next else "prev")))
        sys.exit(0)

    if args.shuffle:
        if daemon_running:
            res = send_ipc_command("shuffle") or {}
            print(f"✔ Shuffle mode: {'ON' if res['shuffle'] else 'OFF'}" if "shuffle" in res else "✔ Shuffle toggled")
        sys.exit(0)

    if args.repeat:
        if daemon_running:
            res = send_ipc_command("repeat") or {}
            print(f"✔ Repeat mode: {res['repeat'].upper()}" if "repeat" in res else "✔ Repeat mode cycled")
        sys.exit(0)

    if args.record:
        if daemon_running:
            res = send_ipc_command("toggle_record") or {}
            print(f"✔ Live WAV Recording Started: {res.get('recording_file', '')} (Max 3GB per WAV)" if res.get("recording") else "✔ Live WAV Recording Stopped & Saved to MP_AUDIOWAV_REC_OUTPUT")
        else: print("Player daemon not running.")
        sys.exit(0)

    if args.resume:
        res_data = None
        if RESUME_STATE_FILE.is_file():
            try: res_data = json.loads(RESUME_STATE_FILE.read_text(encoding="utf-8"))
            except Exception: pass
        if res_data and res_data.get("path") and os.path.isfile(res_data["path"]):
            target_file = res_data["path"]
            target_pos = float(res_data.get("position_sec", 0.0) or 0.0)
            if daemon_running and send_ipc_command("play", file=target_file, position=target_pos):
                print(f"✔ Resumed playback: {os.path.basename(target_file)} at {format_seconds(target_pos)}")
                sys.exit(0)
            _spawn_player_daemon(target_file, auto_bluetooth=auto_bt, mode=res_data.get("mode", current_mode), seek_to=target_pos)
            print(f"✔ Started player daemon and resumed: {os.path.basename(target_file)} at {format_seconds(target_pos)}")
            sys.exit(0)
        else:
            print("✖ No previous mix available to resume.", file=sys.stderr)
            sys.exit(1)

    # Liquid Glass always embeds this script with --interactive. A startup resume
    # also passes --play and --seek-to. Those flags must load the mix inside the
    # window. Exiting here makes the embedded terminal quit and the glass window closes.
    ui_session = bool(args.interactive or args.mini or args.micro)

    if args.play is not None and not ui_session:
        ftp = _existing_audio_path(args.play or (args.file or "")) if (args.play or args.file) else ""
        pos = float(args.seek_to) if args.seek_to is not None else None
        res_data = None
        if RESUME_STATE_FILE.is_file():
            try: res_data = json.loads(RESUME_STATE_FILE.read_text(encoding="utf-8"))
            except Exception: pass
        if not ftp and res_data and res_data.get("path") and os.path.isfile(res_data["path"]):
            ftp = res_data["path"]
            if pos is None:
                pos = float(res_data.get("position_sec", 0.0) or 0.0)
            if not args.music_mode and not args.mix_archive_mode:
                if res_data.get("mode") == "MUSIC":
                    current_mode = "MUSIC"
        elif ftp and pos is None and res_data and res_data.get("path") == ftp:
            pos = float(res_data.get("position_sec", 0.0) or 0.0)
        if pos is None:
            pos = 0.0
        if not (daemon_running and send_ipc_command("play", file=ftp or None, position=pos)):
            if not ftp and (l := MixArchiveScanner(base_dir).get_latest_mix()): ftp = l["path"]
            if ftp: _spawn_player_daemon(ftp, auto_bluetooth=auto_bt, mode=current_mode, seek_to=pos)
        sys.exit(0)

    if args.clear_queue:
        print(f"✔ Cleared {(send_ipc_command('clear_queue') or {}).get('cleared', 0)} mix(es) from playback queue." if daemon_running else "Player daemon not running.")
        sys.exit(0)

    if args.queue is not None:
        qt = os.path.abspath(args.queue or (args.file or "")) if (args.queue or args.file) else ""
        if not qt and (latest := MixArchiveScanner(base_dir).get_latest_mix()): qt = latest["path"]
        if qt and os.path.exists(qt):
            if daemon_running and (res := send_ipc_command("queue", file=qt)):
                print(f"✔ Queued track (#{res.get('queue_len', '?')} in queue): {os.path.basename(qt)}")
            else:
                _spawn_player_daemon(qt, auto_bluetooth=auto_bt, mode=current_mode)
                print(f"✔ Started player daemon with mix: {os.path.basename(qt)}")
            sys.exit(0)
        print(f"✖ File not found to queue: {qt}", file=sys.stderr); sys.exit(1)

    if args.pitch is not None or args.pitch_up is not None or args.pitch_down is not None or args.pitch_reset:
        if daemon_running:
            if args.pitch is not None: send_ipc_command("set_pitch", percent=args.pitch)
            elif args.pitch_up is not None: send_ipc_command("pitch_up", step=args.pitch_up)
            elif args.pitch_down is not None: send_ipc_command("pitch_down", step=args.pitch_down)
            else: send_ipc_command("reset_pitch")
        sys.exit(0)

    if args.vol:
        if daemon_running:
            v = args.vol
            send_ipc_command("vol_up" if v[0] == "+" else "vol_down", step=abs(int(v))) if v.startswith(("+", "-")) else send_ipc_command("set_volume", volume=int(v))
        sys.exit(0)

    if args.seek is not None:
        if daemon_running: send_ipc_command("seek", seconds=args.seek)
        sys.exit(0)

    if args.seek_to is not None and args.play is None and not args.daemon and not ui_session:
        if daemon_running: send_ipc_command("seek_to", position=args.seek_to)
        sys.exit(0)

    if args.status or args.status_line:
        data = (send_ipc_command("status") or {}).get("data") if daemon_running else None
        if args.status: print(json.dumps(data or {"running": False, "state": "stopped"}, indent=2))
        elif data:
            pval = data.get("pitch_percent", 0)
            p_str = f"Pitch: {pval:+d}%" if pval != 0 else "Pitch: 0%"
            eq_s = f"EQ: {data.get('eq_preset', 'Flat')}"
            bass_s = f"Bass: {data.get('bass_mode', 'Normal')}"
            print(f"{'▶' if data['state'] == 'playing' else '⏸'} {data['title']} [{data['position_fmt']}/{data['duration_fmt']}] ({data['mini_eq']}) {p_str} {eq_s} {bass_s} Vol: {data['volume']}%")
        else: print("○ MP Audio Player: Stopped")
        sys.exit(0)

    if args.list_eq_presets:
        mgr = EqualizerManager()
        presets = mgr.get_all_presets()
        print("MP Audio Player 10-Band Equalizer Presets:")
        print("  Frequencies:  " + "  ".join(f"{f:>6s}" for f in EQ_BAND_LABELS))
        for name in mgr.get_all_preset_names():
            gains = presets.get(name, [])
            g_str = "  ".join(f"{g:+6.1f}" for g in gains)
            d_mark = " (Default)" if name == "Flat" else ""
            print(f"  • {name:<14s}{d_mark}: {g_str} dB")
        sys.exit(0)

    if args.eq_preset:
        target_preset = args.eq_preset
        if daemon_running:
            res = send_ipc_command("set_eq_preset", preset=target_preset) or {}
            if res.get("success"):
                print(f"✔ Equalizer preset set to: {res.get('preset')}")
                sys.exit(0)
            else:
                print(f"✖ Failed to set equalizer preset: {target_preset}", file=sys.stderr)
                sys.exit(1)
        else:
            mgr = EqualizerManager()
            if mgr.apply_preset(target_preset):
                print(f"✔ Equalizer preset saved as default: {mgr.active_preset}")
                sys.exit(0)
            else:
                print(f"✖ Unknown equalizer preset: {target_preset}", file=sys.stderr)
                sys.exit(1)

    if args.reset_eq:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            EQ_INIT_FLAG_FILE.touch(exist_ok=True)
        except Exception: pass
        if daemon_running:
            send_ipc_command("reset_eq")
            print("✔ Equalizer reset to Flat (0 dB)")
            sys.exit(0)
        else:
            mgr = EqualizerManager()
            mgr.apply_preset("Flat", save=True)
            print("✔ Equalizer reset to Flat (0 dB)")
            sys.exit(0)

    if args.eq_bands:
        try:
            parts = [float(x.strip()) for x in args.eq_bands.split(",")]
            if len(parts) != 10:
                print("✖ Exactly 10 band gains (dB) required separated by commas.", file=sys.stderr)
                sys.exit(1)
            if daemon_running:
                res = send_ipc_command("set_eq_bands", bands=parts) or {}
                print(f"✔ Equalizer bands updated (Preset: {res.get('preset', 'Custom')})")
                sys.exit(0)
            else:
                mgr = EqualizerManager()
                mgr.set_bands(parts)
                print("✔ Equalizer custom bands saved.")
                sys.exit(0)
        except Exception as exc:
            print(f"✖ Invalid band values: {exc}", file=sys.stderr)
            sys.exit(1)

    if args.list_bass_modes:
        print("MP Audio Player — MP Bass Boost modes (Skullcandy Crusher ANC 2 dial):")
        for mode in BASS_BOOST_MODES:
            pct = BASS_BOOST_FRACTIONS[mode] * 100.0
            peak = bass_mode_peak_db(mode)
            note = "  (Crusher off)" if mode == "Normal" else f"  {peak:+.1f} dB at {CRUSHER_BASS_PEAK_HZ:.0f} Hz"
            print(f"  • {mode:<8s} {pct:5.1f}% of the Crusher dial{note}")
        sys.exit(0)

    if args.bass_mode:
        target_mode = BassBoostManager.resolve(args.bass_mode)
        if not target_mode:
            print(f"✖ Unknown bass mode: {args.bass_mode} (Normal, Medium, High)", file=sys.stderr)
            sys.exit(1)
        if daemon_running:
            res = send_ipc_command("set_bass_mode", mode=target_mode) or {}
            if res.get("success"):
                print(f"✔ Bass Boost: {res.get('bass_mode', target_mode)}")
                sys.exit(0)
            print(f"✖ Failed to set Bass Boost: {target_mode}", file=sys.stderr)
            sys.exit(1)
        BassBoostManager().set_mode(target_mode)
        print(f"✔ Bass Boost saved as default: {target_mode}")
        sys.exit(0)

    if args.cycle_bass:
        if daemon_running:
            res = send_ipc_command("cycle_bass") or {}
            print(f"✔ Bass Boost: {res.get('bass_mode', 'Normal')}")
            sys.exit(0)
        mgr = BassBoostManager()
        print(f"✔ Bass Boost: {mgr.cycle(1)}")
        sys.exit(0)

    if args.interactive or args.mini or args.micro or (not args.daemon and sys.stdin.isatty()):
        _tty_rt = _load_tty_runtime()
        if _tty_rt is not None:
            _tty_rt.configure_stdio()
        # No "Loading library..." splash. The playlist comes from the fast
        # sidecar cache before the first frame, so the first thing painted is the
        # real player screen, the same instant-on feel a native player has.
        d_status = send_ipc_command("status") if daemon_running else None
        if daemon_running and not d_status:
            daemon_running = False
        elif daemon_running and d_status and isinstance(d_status.get("data"), dict):
            d_data = d_status["data"]
            if d_mode := d_data.get("mode"):
                if not args.music_mode and not args.mix_archive_mode:
                    current_mode = d_mode
                    is_music = (current_mode == "MUSIC")

        scanner = MusicFolderPlaylistScanner(folder_name=folder_target, playlist_path=args.playlist) if is_music else MixArchiveScanner(base_dir)
        mixes = scanner.get_mixes()
        requested = (args.play or args.file or "") if args.play is not None else (args.file or "")
        tf = (_existing_audio_path(requested) or None) if requested else None
        spawned_pid = None

        if not daemon_running:
            resume_f, resume_pos = None, 0.0
            if not tf and args.no_autoplay:
                # A mixer deck must never inherit the previous session or the
                # first-run sample: it opens with the library and stays silent.
                pass
            elif not tf and RESUME_STATE_FILE.is_file():
                try:
                    res_data = json.loads(RESUME_STATE_FILE.read_text(encoding="utf-8"))
                    if res_data and res_data.get("path") and os.path.isfile(res_data["path"]):
                        # An explicit --mix-archive-mode or --music-mode launch
                        # must not be dragged back into the other library by a
                        # saved session: only a launch that named neither flag
                        # adopts the saved mode and file.
                        explicit_mode = bool(args.music_mode or args.mix_archive_mode)
                        saved_mode = str(res_data.get("mode") or "MIX_ARCHIVE").upper()
                        wants_music = bool(args.music_mode)
                        if explicit_mode and saved_mode != ("MUSIC" if wants_music else "MIX_ARCHIVE"):
                            resume_f, resume_pos = None, 0.0
                        else:
                            resume_f = res_data["path"]
                            resume_pos = float(res_data.get("position_sec", 0.0) or 0.0)
                        if not explicit_mode:
                            if res_data.get("mode") == "MUSIC":
                                current_mode = "MUSIC"
                                is_music = True
                                scanner = MusicFolderPlaylistScanner(folder_name=folder_target, playlist_path=args.playlist)
                                mixes = scanner.get_mixes()
                except Exception: pass
            elif not tf and not (CONFIG_DIR / ".first_run_sample_loaded").exists():
                # Resolve the bundled track from the same constants the
                # first-run helper uses, so a format change (WAV -> FLAC)
                # cannot leave a stale filename behind again.
                try:
                    from liquid_glass_reward import (
                        SAMPLE_TRACK_FILE as _sample_track,
                        SAMPLE_TRACK_DURATION_SEC as _sample_duration,
                    )
                except ImportError:
                    try:
                        from scripts.liquid_glass_reward import (
                            SAMPLE_TRACK_FILE as _sample_track,
                            SAMPLE_TRACK_DURATION_SEC as _sample_duration,
                        )
                    except ImportError:
                        _sample_track = base_dir / "MP_SAMPLES" / "MPlanetarian - The Orbits of Planets (Cosmic Dreamworlds Remake).flac"
                        _sample_duration = 79076571 / 48000
                sample_track = Path(_sample_track)
                if sample_track.is_file() and not any(not _is_bundled_sample(m.get("path")) for m in mixes):
                    resume_f = str(sample_track.resolve())
                    resume_pos = 0.0
                    try:
                        (CONFIG_DIR / ".first_run_sample_loaded").touch(exist_ok=True)
                        (CONFIG_DIR / ".first_run_eq_initialized").touch(exist_ok=True)
                        EqualizerManager().apply_preset("Flat", save=True)
                        RESUME_STATE_FILE.write_text(json.dumps({
                            "path": resume_f,
                            "position_sec": 0.0,
                            "duration_sec": _sample_duration,
                            "title": sample_track.stem,
                            "mode": "MIX_ARCHIVE",
                            "player": "mp_audio_player",
                            "timestamp": time.time()
                        }, indent=2), encoding="utf-8")
                    except Exception: pass
            latest = scanner.get_latest_mix() if not is_music else (mixes[0] if mixes else None)
            if args.no_autoplay:
                # Only an explicit file is loaded; otherwise the daemon opens
                # the playlist on track 0, paused.
                init_f = tf
            else:
                init_f = tf or resume_f or (latest["path"] if latest else (mixes[0]["path"] if mixes else None))
            if tf and args.seek_to is not None:
                init_pos = float(args.seek_to)
            elif not tf and resume_f:
                init_pos = resume_pos
            else:
                init_pos = 0.0
            # Spawn the playback engine now, but do not wait for its socket. The
            # wait loop used to hold the window back for a second or more; the
            # playlist on screen does not need playback to be ready, and the
            # status file fills in as soon as the engine is up.
            spawned_pid = _spawn_player_daemon(init_f, auto_bluetooth=auto_bt, mode=current_mode,
                                               music_folder=folder_target, playlist_path=args.playlist,
                                               seek_to=init_pos, no_autoplay=args.no_autoplay)
        else:
            spawned_pid = None
            d_data = d_status.get("data", {}) if d_status else {}
            d_state = d_data.get("state", "stopped")
            if tf:
                play_kw = {"file": tf}
                if args.seek_to is not None:
                    play_kw["position"] = float(args.seek_to)
                send_ipc_command("play", **play_kw)
            elif args.no_autoplay:
                # The deck's engine is already up; nothing starts by itself.
                pass
            elif requested_music and str(d_data.get("mode") or "").upper() != "MUSIC":
                send_ipc_command("load_playlist", mode="MUSIC", folder_name=folder_target, playlist_path=args.playlist, play=True)
            elif (not requested_music and args.mix_archive_mode
                  and str(d_data.get("mode") or "").upper() != "MIX_ARCHIVE"):
                # The mirror of the Music case: a --mix-archive-mode launch must
                # take the window back to the mix archive when the daemon is
                # still playing a music library, instead of leaving it showing
                # the other library's playlist.
                send_ipc_command("load_playlist", mode="MIX_ARCHIVE", play=False)
                if latest := scanner.get_latest_mix():
                    send_ipc_command("play", file=latest["path"])
            elif d_state == "stopped" or not d_data.get("path"):
                resume_f, resume_pos = None, 0.0
                if RESUME_STATE_FILE.is_file():
                    try:
                        res_data = json.loads(RESUME_STATE_FILE.read_text(encoding="utf-8"))
                        if res_data and res_data.get("path") and os.path.isfile(res_data["path"]):
                            resume_f = res_data["path"]
                            resume_pos = float(res_data.get("position_sec", 0.0) or 0.0)
                    except Exception: pass
                if resume_f:
                    send_ipc_command("play", file=resume_f, position=resume_pos)
                elif is_music:
                    send_ipc_command("load_playlist", mode="MUSIC", folder_name=folder_target, playlist_path=args.playlist, play=True)
                elif latest := scanner.get_latest_mix():
                    send_ipc_command("play", file=latest["path"])
        _retire_extra_player_daemons(prefer_pid=spawned_pid)

        ui_mode = "MICRO" if args.micro else ("MINI" if args.mini else ("VERTICAL" if getattr(args, "vertical", False) else "NORMAL"))
        def _term_sig(s, f):
            try:
                runtime = _load_tty_runtime()
                if runtime is not None:
                    runtime.restore_terminal()
            except Exception: pass
            try:
                sys.stdout.write("\033[?25h\033[?1049l\033[0m")
                sys.stdout.flush()
            except Exception: pass
            os._exit(0)
        for sig in (signal.SIGTERM, signal.SIGINT, getattr(signal, "SIGHUP", None)):
            if sig:
                try: signal.signal(sig, _term_sig)
                except Exception: pass
        TerminalPlayerUI(None, scanner, is_remote=True, default_style=args.style, default_theme=args.theme, mode=current_mode, folder_name=folder_target, ui_size_mode=ui_mode, keep_library=bool(requested_music and current_mode == "MUSIC")).run()
        sys.exit(0)

    if args.daemon:
        if not _acquire_daemon_lock():
            sys.exit(0)
        try: PID_FILE.write_text(str(os.getpid()))
        except OSError: pass
        _retire_extra_player_daemons(prefer_pid=os.getpid())
        _install_windows_console_close()
        def _sig(s, f):
            try:
                for p in (STATE_FILE, SOCKET_PATH, PORT_FILE, PID_FILE): p.unlink(missing_ok=True)
            except Exception: pass
            os._exit(0)
        for sig in (signal.SIGTERM, signal.SIGINT, getattr(signal, "SIGHUP", None)):
            if sig:
                try: signal.signal(sig, _sig)
                except Exception: pass

    scanner = MusicFolderPlaylistScanner(folder_name=folder_target, playlist_path=args.playlist) if is_music else MixArchiveScanner(base_dir)
    mixes, init_idx, explicit_missing = scanner.get_mixes(), 0, False
    if args.file:
        tp = _existing_audio_path(args.file)
        if not tp:
            explicit_missing = True
        else:
            for i, m in enumerate(mixes):
                if m["path"] == tp: init_idx = i; break
            else:
                mixes.insert(0, {"name": os.path.basename(tp), "path": tp, "size_mb": os.path.getsize(tp) / 1048576.0, "mtime": time.time(), "ext": os.path.splitext(tp)[1][1:].upper()})
                init_idx = 0

    init_pos = float(args.seek_to) if args.seek_to is not None else 0.0
    engine = AudioEngine(mixes, auto_bluetooth=auto_bt, mode=current_mode)
    engine.playlist_path = str(args.playlist or (getattr(scanner, "playlist_path", "") or ""))
    PlayerIPCServer(engine).start()
    if mixes and not explicit_missing: engine.load_track(init_idx, not args.no_autoplay, start_position=init_pos)
    _start_headset_controls(engine)

    try:
        while True:
            if engine.state == "playing": engine._ensure_stream()
            engine.check_track_end()
            time.sleep(0.5)
    except (KeyboardInterrupt, SystemExit): pass
    finally:
        try:
            if engine.current_track and engine.current_track.get("path"):
                saved_pos = round(float(engine.position_sec), 2)
                if saved_pos <= 0.0 and RESUME_STATE_FILE.is_file():
                    try:
                        prev = json.loads(RESUME_STATE_FILE.read_text(encoding="utf-8"))
                        if prev.get("path") == str(engine.current_track.get("path")):
                            prev_pos = float(prev.get("position_sec", 0.0))
                            if prev_pos > 0.0:
                                saved_pos = prev_pos
                    except Exception: pass
                res_tmp = RESUME_STATE_FILE.with_suffix(".tmp")
                res_tmp.write_text(json.dumps({
                    "path": str(engine.current_track.get("path")),
                    "position_sec": saved_pos,
                    "duration_sec": round(float(engine.duration_sec), 2),
                    "title": str(engine.current_track.get("name", "")),
                    "mode": str(engine.mode),
                    "player": "mp_audio_player",
                    "timestamp": time.time()
                }), encoding="utf-8")
                res_tmp.replace(RESUME_STATE_FILE)
        except Exception: pass
        engine.shutdown()
        for p in (PID_FILE, SOCKET_PATH, PORT_FILE): p.unlink(missing_ok=True)
        try: STATE_FILE.write_text(json.dumps({"running": False, "state": "stopped"}), encoding="utf-8")
        except OSError: pass
        sys.exit(0)

# ------------------------------------------------------------------------------
# Stream of Frequency — Dynamic Waves, console twin
#
# The console river is drawn with half-block cells, one column per character
# cell. Shading every cell of a 200-column terminal at 60 fps is wasted work for
# a soft gradient, so one column in five is filled from its neighbour. That is
# the console twin of the saver's SAVER_PIXEL_SCALE = 0.80.
# ------------------------------------------------------------------------------
SOF_CELL_BUDGET = 0.80
skip_mod = 5


def _sof_skip_column(col: int) -> bool:
    """True when this column is filled from its neighbour instead of drawn."""
    return skip_mod > 0 and (col % skip_mod) == (skip_mod - 1)


def _sof_river_row(col: int, row: int, rows: int, phase: float) -> int:
    """Half-block index for one river cell: the bright head near `phase`.

    Kept deliberately cheap: one multiply-add and a clamp per cell, which is
    what lets the river run inside the player's render loop.
    """
    along = col / max(1.0, float(rows))
    head = phase % 1.0
    delta = abs(along - head)
    glow = max(0.0, 1.0 - delta * 6.0)
    return int(round(glow * (rows - 1)))



if __name__ == "__main__":
    main()
