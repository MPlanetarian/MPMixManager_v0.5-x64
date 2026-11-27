#!/usr/bin/env python3
"""MP Liquid Mixer 0.0.1 - Windows 10/11 port.

A native Tkinter four-channel mixer for Windows. It keeps the Linux mixer's
model, theme table, MIDI profiles and deck IPC, and replaces the GTK/VTE host
with a single glass-style window:

  * four MP Audio Player engines, one per deck, in their own IPC namespaces;
  * channel faders / trim / three-band EQ / filter / inserts / cue;
  * crossfader, master and monitor levels, per-deck meters and transport;
  * Kontrol S4 / S3 MIDI profiles read through winmm (no Python MIDI package);
  * WASAPI loopback recording through PyAudioWPatch when it is installed;
  * the MP Liquid Visualizer as a companion window directly behind the mixer;
  * the shared MP theme file, with C cycling themes like every other window.

Everything the Linux mixer keeps in ``mixer_core`` is reused unchanged, so the
two ports cannot drift apart in arithmetic or preset names. The GTK-specific
drawing and VTE terminals are the only pieces this file replaces.

Usage:
    python MP_Liquid_Mixer_Windows_10.py
    python MP_Liquid_Mixer_Windows_10.py --windowed
    python MP_Liquid_Mixer_Windows_10.py --no-visualizer
    python MP_Liquid_Mixer_Windows_10.py --midi-device "Traktor Kontrol S4"
    python MP_Liquid_Mixer_Windows_10.py --self-test
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
MIXER_DIR = ROOT / "MP_Liquid_Mixer"
SCRIPTS = ROOT / "scripts"
for _path in (ROOT, MIXER_DIR, SCRIPTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import mixer_core as core  # noqa: E402
import mixer_evdev  # noqa: E402
import mixer_midi  # noqa: E402
import mixer_settings  # noqa: E402

VERSION = "0.0.1"
WINDOW_TITLE = f"MP Liquid Mixer {VERSION} (Windows)"
# One design grid for both ports: mixer_core.layout() composes the mixer at
# 1920x1080, so the Windows window is the Linux window scaled, not a second
# interpretation of the same idea.
DESIGN = (core.DESIGN_W, core.DESIGN_H)
WINDOWED = core.windowed_size(1920 + 48, 1080 + 48)
# The mixer always opens in Into Darkness (Void Out), on every platform.
FORCED_THEME = core.FORCED_THEME
CONFIG_DIR = Path.home() / ".config" / "mix-manager"
WINDOWS_SETTINGS = CONFIG_DIR / "liquid_mixer_windows.json"
VISUALIZER_PRESET_FILE = CONFIG_DIR / "visualizer_preset"

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
KEY_EVENT = 0x0100


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _rgb(value: Any, default: Tuple[int, int, int] = (24, 28, 32)) -> Tuple[int, int, int]:
    try:
        red, green, blue = (int(v) for v in value)
        return (max(0, min(255, red)), max(0, min(255, green)), max(0, min(255, blue)))
    except Exception:
        return default


def _hex(rgb: Tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % _rgb(rgb)


def _tone(rgb: Tuple[int, int, int], factor: float, toward: int = 0) -> str:
    channels = []
    for value in _rgb(rgb):
        mixed = value * factor + toward * (1.0 - factor)
        channels.append(max(0, min(255, int(round(mixed)))))
    return "#%02x%02x%02x" % tuple(channels)


def _mix(a: Tuple[int, int, int], b: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _light(color: Tuple[int, int, int], amount: float = 0.25) -> Tuple[int, int, int]:
    return _mix(color, (255, 255, 255), amount)


def _darken(color: Tuple[int, int, int], amount: float = 0.35) -> Tuple[int, int, int]:
    return _mix(color, (0, 0, 0), amount)


def _luma(color: Tuple[int, int, int]) -> float:
    return (0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]) / 255.0


def _ink_accent(color: Tuple[int, int, int], dark_ui: bool) -> Tuple[int, int, int]:
    """Keep a theme accent readable as text on this field.

    Into Darkness and The Matrix use black accents. As a fill or a border that
    is correct; as a label it disappears, so it is brightened toward white for
    text only — the same rule the Linux mixer uses.
    """
    if dark_ui and _luma(color) < 0.28:
        return _light(color, 0.72)
    return color


def _with(base: Tuple[int, int, int], color: Tuple[int, int, int], alpha: float) -> str:
    """A colour that reads as ``color`` at ``alpha`` over the opaque ``base``."""
    return _hex(_mix(base, color, alpha))


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def _fmt_time(seconds: Any) -> str:
    try:
        total = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        total = 0
    return f"{total // 60:02d}:{total % 60:02d}"


def _short(text: Any, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: max(0, limit - 1)] + "…"


def midi_profile_label(profile: Any) -> str:
    """The controller profile's display name ("Kontrol S4"), never its id."""
    key = str(profile or "").strip().upper()
    labels = getattr(mixer_midi, "PROFILE_LABELS", {}) or {}
    return str(labels.get(key) or f"Profile {key or '?'}")


def _console_python() -> str:
    exe = sys.executable
    if sys.platform == "win32" and exe.lower().endswith("pythonw.exe"):
        candidate = exe[: -len("pythonw.exe")] + "python.exe"
        if Path(candidate).is_file():
            return candidate
    return exe


def _windowless_python() -> str:
    exe = sys.executable
    if sys.platform == "win32" and exe.lower().endswith("python.exe"):
        candidate = exe[: -len("python.exe")] + "pythonw.exe"
        if Path(candidate).is_file():
            return candidate
    return exe


def _top_level_hwnd(widget: tk.Misc) -> int:
    """The real top-level HWND of a Tk widget (winfo_id is a child on Windows)."""
    if sys.platform != "win32":
        return 0
    try:
        hwnd = int(widget.winfo_id())
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetAncestor.restype = ctypes.c_void_p
        root = int(user32.GetAncestor(ctypes.c_void_p(hwnd), 2) or 0)  # GA_ROOT
        return root or hwnd
    except Exception:
        return 0


def _post_key_to_title(title_part: str, key: str) -> bool:
    """Send one key to the first visible window whose title contains a marker."""
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        found: List[int] = []

        def visit(hwnd, _lparam):
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0 or not user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if title_part.lower() in buf.value.lower():
                found.append(int(hwnd))
                return False
            return True

        user32.EnumWindows(WNDENUMPROC(visit), 0)
        if not found:
            return False
        user32.PostMessageW(ctypes.c_void_p(found[0]), KEY_EVENT, ord(key), 0)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Windows MIDI input (winmm)
# ---------------------------------------------------------------------------
class WindowsMidiInput:
    """Read a Windows MIDI input through winmm, with no Python MIDI package."""

    MIM_DATA = 0x3C3
    CALLBACK_FUNCTION = 0x00030000

    class CAPS(ctypes.Structure):
        _fields_ = [
            ("wMid", wintypes.WORD), ("wPid", wintypes.WORD),
            ("vDriverVersion", wintypes.UINT), ("szPname", wintypes.WCHAR * 32),
            ("dwSupport", wintypes.DWORD),
        ]

    def __init__(self, on_message: Callable[[int, int, int], None],
                 on_status: Callable[[str], None],
                 profile: str = "A", device: str = "") -> None:
        self.on_message = on_message
        self.on_status = on_status
        self.profile = mixer_midi.normalize_profile(profile)
        self.device = str(device or "").strip()
        self._winmm = None
        self._handle = None
        self._callback = None
        self._lock = threading.Lock()
        self._running = False
        self.device_name = ""
        self.device_index = -1
        self.status = "MIDI not started"
        self.last_event = 0.0

    def devices(self) -> List[str]:
        names: List[str] = []
        try:
            winmm = ctypes.WinDLL("winmm")
            for index in range(int(winmm.midiInGetNumDevs())):
                caps = self.CAPS()
                if winmm.midiInGetDevCapsW(index, ctypes.byref(caps),
                                           ctypes.sizeof(caps)) == 0:
                    names.append(str(caps.szPname))
        except Exception:
            pass
        return names

    def _pick(self) -> int:
        names = self.devices()
        if not names:
            return -1
        wanted = self.device.lower()
        if wanted:
            if wanted.isdigit() and 0 <= int(wanted) < len(names):
                return int(wanted)
            for index, name in enumerate(names):
                if wanted in name.lower():
                    return index
        for index, name in enumerate(names):
            if mixer_midi.name_matches_profile(name, self.profile):
                return index
        return -1

    def start(self) -> bool:
        self._winmm = ctypes.WinDLL("winmm")
        index = self._pick()
        if index < 0:
            names = ", ".join(self.devices()) or "none"
            self.status = f"no Kontrol {self.profile} MIDI port (found: {names})"
            self.on_status(self.status)
            return False
        self.device_index = index
        self.device_name = self.devices()[index]

        CALLBACK = ctypes.WINFUNCTYPE(
            None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p)
        self._callback = CALLBACK(self._on_midi)
        handle = ctypes.c_void_p()
        rc = self._winmm.midiInOpen(
            ctypes.byref(handle), index, self._callback, 0,
            self.CALLBACK_FUNCTION)
        if rc != 0:
            self.status = f"could not open {self.device_name} (winmm {rc})"
            self.on_status(self.status)
            return False
        self._handle = handle
        self._winmm.midiInStart(handle)
        self._running = True
        self.status = f"{self.device_name} (MIDI {self.profile})"
        self.on_status(self.status)
        return True

    def _on_midi(self, _handle, message, _instance, data, _param) -> None:
        if message != self.MIM_DATA:
            return
        try:
            # winmm passes dwParam1 either as an integer or as a c_void_p
            # depending on the ctypes callback signature.
            raw = int(data.value) if hasattr(data, "value") else int(data or 0)
            status, first, second = raw & 0xFF, (raw >> 8) & 0x7F, (raw >> 16) & 0x7F
            kind = status & 0xF0
            if kind not in (0x80, 0x90, 0xB0):
                return
            self.last_event = time.time()
            self.on_message(kind, first, second if kind == 0xB0 else second)
        except Exception:
            pass

    def stop(self) -> None:
        self._running = False
        try:
            if self._handle:
                self._winmm.midiInStop(self._handle)
                self._winmm.midiInClose(self._handle)
        except Exception:
            pass
        self._handle = None
        self.status = "stopped"


# ---------------------------------------------------------------------------
# Deck engines
# ---------------------------------------------------------------------------
class DeckEngine:
    """One MP Audio Player engine in its own IPC namespace."""

    def __init__(self, channel: str, library_mode: str = "") -> None:
        self.channel = channel
        self.namespace = core.DECK_NAMESPACES[channel]
        self.library_mode = (library_mode or self.default_library()).upper()
        self.process: Optional[subprocess.Popen] = None
        self.started_at = 0.0

    @staticmethod
    def default_library() -> str:
        return "MUSIC" if core.music_folders_configured() else "MIX_ARCHIVE"

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        env = dict(os.environ)
        env["MP_PLAYER_NAMESPACE"] = self.namespace
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        # The mixer talks to the Kontrol S4 through winmm itself. The Linux
        # ALSA helper would start one extra (console) daemon per deck.
        env["MIDI_CONTROL_ENABLED"] = "false"
        # Each engine is windowless; the mixer owns the only visible window.
        cmd = [
            _windowless_python(), str(ROOT / "MP_Audio_Player.py"),
            "--daemon", "--no-bluetooth-auto", "--no-autoplay",
        ]
        if self.library_mode == "MUSIC":
            cmd += ["--music-mode", "--music-folder", "ALL"]
        else:
            cmd += ["--mix-archive-mode"]
        try:
            self.process = subprocess.Popen(
                cmd, cwd=str(ROOT), env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
            )
            self.started_at = time.monotonic()
        except Exception:
            self.process = None

    def stop(self) -> None:
        self.command("stop", _timeout=0.4)
        proc, self.process = self.process, None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def command(self, cmd: str, _timeout: float = 0.6, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return core.send_ipc(self.namespace, cmd, timeout=_timeout, **kwargs)

    def status(self) -> Dict[str, Any]:
        return core.deck_status(self.namespace)

    def set_library(self, mode: str) -> None:
        self.library_mode = "MUSIC" if str(mode).upper() == "MUSIC" else "MIX_ARCHIVE"
        self.command("set_mode", mode=self.library_mode)
        self.command("load_playlist", _timeout=1.2,
                     path=str(core.music_playlist_json_path() or ""))


# ---------------------------------------------------------------------------
# WASAPI loopback recording
# ---------------------------------------------------------------------------
class WindowsLoopbackRecorder:
    """Record the default output through PyAudioWPatch's WASAPI loopback."""

    def __init__(self, fmt: str = "WAV", directory: Optional[Path] = None) -> None:
        self.format = "FLAC" if str(fmt).upper() == "FLAC" else "WAV"
        self.directory = Path(directory) if directory else self._default_dir()
        self.path: Optional[Path] = None
        self.bytes = 0
        self.started_at = 0.0
        self.error = ""
        self._stream = None
        self._audio = None
        self._wave = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    @staticmethod
    def _default_dir() -> Path:
        try:
            import mixer_recording
            return mixer_recording.recording_dir()
        except Exception:
            return COORD_FALLBACK_RECORDINGS

    def available(self) -> bool:
        try:
            import pyaudiowpatch  # noqa: F401
            return True
        except Exception:
            return False

    def start(self) -> bool:
        if not self.available():
            self.error = "PyAudioWPatch is not installed"
            return False
        try:
            import pyaudiowpatch as pyaudio
            self._audio = pyaudio.PyAudio()
            wasapi = self._audio.get_host_api_info_by_type(pyaudio.paWASAPI)
            speakers = self._audio.get_device_info_by_index(wasapi["defaultOutputDevice"])
            target = None
            for info in self._audio.get_loopback_device_info_generator():
                if speakers["name"] in info["name"]:
                    target = info
                    break
                if target is None:
                    target = info
            if target is None:
                self.error = "no WASAPI loopback device"
                return False
            self.directory.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            self.path = self.directory / f"MP_LIQUID_MIXER_REC_{stamp}.wav"
            rate = int(target["defaultSampleRate"]) or 48000
            channels = min(2, max(1, int(target["maxInputChannels"])))
            self._stream = self._audio.open(
                format=pyaudio.paInt16, channels=channels, rate=rate,
                input=True, input_device_index=target["index"],
                frames_per_buffer=1024,
            )
            import wave
            self._wave = wave.open(str(self.path), "wb")
            self._wave.setnchannels(channels)
            self._wave.setsampwidth(2)
            self._wave.setframerate(rate)
            self.bytes = 0
            self.started_at = time.monotonic()
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="mixer-recorder",
                                            daemon=True)
            self._thread.start()
            return True
        except Exception as exc:
            self.error = str(exc)
            return False

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                data = self._stream.read(1024, exception_on_overflow=False)
                if data and self._wave is not None:
                    self._wave.writeframes(data)
                    self.bytes += len(data)
        except Exception as exc:
            self.error = str(exc)
        finally:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            try:
                self._wave.close()
            except Exception:
                pass
            try:
                self._audio.terminate()
            except Exception:
                pass

    def stop(self) -> Optional[Path]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self.path and self.format == "FLAC":
            ffmpeg = shutil.which("ffmpeg")
            if ffmpeg:
                flac = self.path.with_suffix(".flac")
                try:
                    subprocess.run(
                        [ffmpeg, "-y", "-loglevel", "error", "-i", str(self.path),
                         "-c:a", "flac", str(flac)],
                        check=False, timeout=180,
                        creationflags=CREATE_NO_WINDOW,
                    )
                    if flac.is_file() and flac.stat().st_size > 0:
                        self.path.unlink(missing_ok=True)
                        self.path = flac
                except Exception:
                    pass
        return self.path

    def duration(self) -> float:
        return max(0.0, time.monotonic() - self.started_at) if self.started_at else 0.0


COORD_FALLBACK_RECORDINGS = Path.home() / "Music" / "MP Liquid Mixer Recordings"


# ---------------------------------------------------------------------------
# The mixer window
# ---------------------------------------------------------------------------
class LiquidMixerWindows:
    """The native Windows MP Liquid Mixer."""

    def __init__(self, argv: Optional[List[str]] = None) -> None:
        self.argv = list(argv or [])
        self.settings = mixer_settings.Settings.load()
        self.windows = self._load_windows_settings()
        # The mixer opens in Into Darkness (Void Out) every time, on every
        # platform. C still cycles from there; the shared theme file is
        # rewritten so the four deck engines start in the same theme.
        self.theme = self._initial_theme()
        self.model = self._load_model()
        self.library_mode = str(self.windows.get("library", "") or "").upper()
        if self.library_mode not in ("MUSIC", "MIX_ARCHIVE"):
            self.library_mode = DeckEngine.default_library()
        self.profile = mixer_midi.normalize_profile(
            self.windows.get("midi_profile") or self.settings.midi_profile)
        self.fullscreen = bool(self.windows.get("fullscreen", True))
        self.visualizer_enabled = bool(
            self.windows.get("visualizer", self.settings.visualizer_enabled))
        self.midi_device = str(self.windows.get("midi_device", "") or "")

        self.root: Optional[tk.Tk] = None
        self.canvas: Optional[tk.Canvas] = None
        self._w = DESIGN[0]
        self._h = DESIGN[1]
        self.boxes: Dict[str, Tuple[float, float, float, float]] = self._windows_layout(*DESIGN)
        self._ui_scale = 1.0
        self._hits: List[Tuple[float, float, float, float, str, Any]] = []
        self._drag: Optional[Tuple[str, Any, Any, Any]] = None
        # Pointer offset for dragging the borderless window by an empty part
        # of the surface. None while no window drag is in progress.
        self._window_drag: Optional[Tuple[int, int]] = None
        self._overlay = ""
        self._overlay_rows: List[Dict[str, Any]] = []
        self._overlay_deck = "A"
        self._overlay_query = ""
        self._overlay_index = 0
        self._overlay_source: List[Dict[str, Any]] = []
        self._status_line = "starting decks…"
        self._notice = ""
        self._notice_until = 0.0
        self._closed = False
        self._last_draw = 0.0
        self._alpha_state: Dict[str, Any] = {}
        self._last_alpha_applied: Optional[float] = None
        self._last_base: Tuple[int, int, int] = (0, 0, 0)
        self._drag_knob_start = 0.0
        # Each player pane needs its own playlist, not just the status line:
        # the left pane shows A or C, the right pane B or D.
        self._playlist_rows: Dict[str, List[Dict[str, Any]]] = {
            channel: [] for channel in core.CHANNELS}
        self._playlist_loaded_at: Dict[str, float] = {
            channel: 0.0 for channel in core.CHANNELS}
        self._playlist_scroll: Dict[str, int] = {
            channel: 0 for channel in core.CHANNELS}
        self._duration_cache: Dict[str, str] = {}
        self.browse_target = "A"
        self.sync_master = "A"
        self._switch_flash: Dict[str, float] = {}
        self._clock_text = time.strftime("%H:%M:%S")
        self._clock_date = time.strftime("%a %d %b %Y")
        self._clock_second = int(time.time()) % 60
        # Verify Audio Files mirrors the Linux window: a worker pass, then a
        # modal prompt asking whether the dead rows should leave the list.
        self._verify_running = False
        self._verify_done = 0
        self._verify_total = 0
        self._verify_bad: List[Dict[str, Any]] = []
        self._verify_prompt = False
        self._verify_message = ""
        self._verify_cancel = threading.Event()

        self.decks = {channel: DeckEngine(channel, self.library_mode)
                      for channel in core.CHANNELS}
        self._deck_status: Dict[str, Dict[str, Any]] = {
            channel: {} for channel in core.CHANNELS}
        self._status_lock = threading.Lock()
        self._command_queue: "queue.Queue[Tuple[str, str, Dict[str, Any]]]" = queue.Queue()
        self._midi_queue: "queue.Queue[Tuple[str, float]]" = queue.Queue()
        self._command_thread: Optional[threading.Thread] = None
        self._status_thread: Optional[threading.Thread] = None
        self._worker_stop = threading.Event()
        self.midi: Optional[WindowsMidiInput] = None
        self.recorder: Optional[WindowsLoopbackRecorder] = None
        self.visualizer: Optional[subprocess.Popen] = None
        self._visualizer_at = 0.0
        self._visualizer_geometry: Optional[Tuple[int, int, int, int]] = None

    @staticmethod
    def _load_model() -> "core.MixerModel":
        """Restore saved faders, but never open with every channel dead.

        An early Windows build saved an all-zero model, which made the first
        launch look broken. A saved file is used only when it has a live
        channel; otherwise the documented defaults are used.
        """
        try:
            model = core.MixerModel.load()
        except Exception:
            return core.MixerModel()
        if not any(model.channel_gain(channel) > 0.0 for channel in core.CHANNELS):
            return core.MixerModel()
        return model

    # -- settings ---------------------------------------------------------
    @staticmethod
    def _load_windows_settings() -> Dict[str, Any]:
        try:
            return json.loads(WINDOWS_SETTINGS.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_windows_settings(self) -> None:
        payload = {
            "opacity": round(self.model.opacity, 3),
            "fullscreen": bool(self.fullscreen),
            "visualizer": bool(self.visualizer_enabled),
            "library": self.library_mode,
            "midi_profile": self.profile,
            "midi_device": self.midi_device,
        }
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            WINDOWS_SETTINGS.write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

    def _initial_theme(self) -> str:
        """Force Into Darkness (Void Out), then read back the canonical id."""
        try:
            core.write_theme(FORCED_THEME)
        except Exception:
            pass
        try:
            theme = core.read_theme()
        except Exception:
            theme = FORCED_THEME
        return theme or FORCED_THEME

    # -- lifecycle --------------------------------------------------------
    def run(self) -> int:
        if self.argv and "--self-test" in self.argv:
            return self._self_test()
        self.root = tk.Tk()
        self.root.title(WINDOW_TITLE)
        self.root.overrideredirect(True)
        # The Linux host paints a 94%-opaque field over the bonded visualizer,
        # so the Windows window uses the same idea: alpha follows the model's
        # transparency value. Without a companion there is nothing behind the
        # glass, so the window stays fully solid.
        self.root.configure(bg="#05050b")
        self.canvas = tk.Canvas(self.root, bg="#05050b", highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.focus_set()
        self._place_window()
        self._apply_alpha()
        self._bind()
        self._start_engines()
        self._start_midi()
        if self.visualizer_enabled:
            self.root.after(2200, self._start_visualizer)
        self._worker_stop.clear()
        self._command_thread = threading.Thread(
            target=self._command_worker, name="mixer-commands", daemon=True)
        self._command_thread.start()
        self._status_thread = threading.Thread(
            target=self._status_worker, name="mixer-status", daemon=True)
        self._status_thread.start()
        self.root.after(50, self._tick)
        try:
            self.root.mainloop()
        finally:
            self._shutdown()
        return 0

    def _relayout(self, width: int, height: int) -> None:
        """Rebuild the shared design grid for the live window size."""
        self._w, self._h = max(640, int(width)), max(480, int(height))
        self.boxes = self._windows_layout(self._w, self._h)
        self._ui_scale = max(0.4, self._w / float(core.DESIGN_W))

    @staticmethod
    def _windows_layout(width: int, height: int) -> Dict[str, Tuple[float, float, float, float]]:
        """The shared Linux grid plus the Windows top-bar chrome.

        Everything else is ``mixer_core.layout()`` unchanged, so the two ports
        cannot drift. Three Windows-only boxes are added: the close button the
        reference window shows at the far right, and the MASTER / MONITOR trims
        sitting beside the title where that window puts them. VISUALIZER is
        pulled left by the same amount so the three never overlap.
        """
        boxes = core.layout(width, height)
        scale = min(width / float(core.DESIGN_W), height / float(core.DESIGN_H))
        ox = (width - core.DESIGN_W * scale) / 2.0
        oy = (height - core.DESIGN_H * scale) / 2.0

        def rect(x: float, y: float, w: float, h: float) -> Tuple[float, float, float, float]:
            return (ox + x * scale, oy + y * scale, w * scale, h * scale)

        boxes["master_knob"] = rect(292, 4, 44, 44)
        boxes["monitor_knob"] = rect(376, 4, 44, 44)
        boxes["master_label"] = rect(258, 44, 180, 18)
        boxes["visualizer_button"] = rect(1596, 14, 200, 36)
        boxes["close_button"] = rect(1868, 14, 52, 36)
        return boxes

    def _place_window(self) -> None:
        if self.root is None:
            return
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.overrideredirect(True)
        if self.fullscreen:
            # An override-redirect window cannot use -fullscreen; size it to
            # the screen instead. That is the same borderless full-screen look
            # and works in Windows 10 and 11.
            self.root.geometry(f"{screen_w}x{screen_h}+0+0")
            self._relayout(screen_w, screen_h)
        else:
            width, height = WINDOWED
            width, height = min(width, screen_w), min(height, screen_h)
            x = max(0, (screen_w - width) // 2)
            y = max(0, (screen_h - height) // 2)
            self.root.geometry(f"{width}x{height}+{x}+{y}")
            self._relayout(width, height)

    def _bind(self) -> None:
        self.root.bind("<KeyPress>", self._on_key)
        self.canas_bind()
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Configure>", self._on_configure)

    def canas_bind(self) -> None:
        # Split out so tests can inspect the bindings without a display.
        pass

    def _on_configure(self, event) -> None:
        if event.width < 64 or event.height < 64:
            return
        if (event.width, event.height) == (self._w, self._h):
            return
        self._relayout(event.width, event.height)
        self._draw()

    def _on_key(self, event) -> None:
        key = (event.keysym or "").lower()
        char = (event.char or "").lower()
        if self._verify_prompt:
            if char in ("y",) or key in ("return", "kp_enter"):
                self._verify_remove_bad()
            elif char in ("n",) or key == "escape":
                self._verify_keep_bad()
            return
        if self._verify_running and key == "escape":
            self._cancel_verify_audio()
            return
        if self._overlay == "settings":
            if char in ("1", "2", "3", "4", "5", "6", "7"):
                self._settings_action(char)
                return
            if key in ("escape", "s", "q"):
                self._overlay = ""
                self._draw()
                return
        if self._overlay == "library":
            if key == "escape":
                self._overlay = ""
                self._draw()
            elif key == "up":
                self._overlay_scroll(-1)
            elif key == "down":
                self._overlay_scroll(1)
            elif key == "return":
                self._load_library_row(self._overlay_index)
            elif key == "backspace":
                self._overlay_query = self._overlay_query[:-1]
                self._refresh_overlay_rows()
            elif key in ("l", "q"):
                self._overlay = ""
                self._draw()
            elif event.char and event.char.isprintable() and len(event.char) == 1:
                self._overlay_query += event.char
                self._refresh_overlay_rows()
            return
        if key == "escape":
            if self.fullscreen:
                self._toggle_fullscreen()
            else:
                self.close()
        elif key == "f11":
            self._toggle_fullscreen()
        elif char == "c" or key == "c":
            self._cycle_theme()
        elif char == "v" or key == "v":
            self._toggle_visualizer()
        elif char == "r" or key == "r":
            self._toggle_recording()
        elif char == "s" or key == "s":
            self._overlay = "settings"
            self._draw()
        elif char == "x" or key == "x":
            self.model.toggle_mode()
            self._notice = f"Mixer mode: {self.model.mode}"
            self._notice_until = time.monotonic() + 1.8
            self._save_windows_settings()
            self._draw()
        elif char == "l" or key == "l":
            self._launch_app("liquid")
        elif char == "g" or key == "g":
            self._launch_app("gui")
        elif char == "m" or key == "m":
            self._launch_app("manager")
        elif char == "z" or key == "z":
            self._launch_app("visualizer")
        elif key == "space":
            self._toggle_deck(self.model.side_decks.get("L", "A"))
        elif key in ("up", "down", "prior", "next"):
            self._scroll_playlist(key)
        elif char in "123456789":
            _post_key_to_title("MP Liquid Visualizer", char)

    # -- engine lifecycle -------------------------------------------------
    def _start_engines(self) -> None:
        for engine in self.decks.values():
            engine.start()
        self._status_line = "starting four deck engines…"

    def _status_worker(self) -> None:
        while not self._worker_stop.is_set():
            statuses = {}
            for channel, engine in self.decks.items():
                try:
                    statuses[channel] = engine.status()
                except Exception:
                    statuses[channel] = {}
            with self._status_lock:
                self._deck_status = statuses
            # The two visible players show the playlist they are mixing.
            # Refreshing it here (on the worker, never on the Tk thread) keeps
            # the panes honest when a deck loads a list from the library.
            now = time.monotonic()
            for channel in core.CHANNELS:
                if now - self._playlist_loaded_at.get(channel, 0.0) < 1.5:
                    continue
                self._playlist_loaded_at[channel] = now
                try:
                    rows = self._fetch_playlist(channel)
                except Exception:
                    rows = []
                if rows:
                    self._playlist_rows[channel] = rows
            # Every few seconds, make sure the fader levels match the model.
            if int(time.monotonic()) % 5 == 0:
                self._queue_all_levels()
            time.sleep(0.4)

    def _fetch_playlist(self, channel: str) -> List[Dict[str, Any]]:
        """Rows for one deck: the engine's own list, else the shared one."""
        namespace = core.DECK_NAMESPACES[channel]
        reply = core.send_ipc(namespace, "get_playlist", timeout=1.6) or {}
        rows = list(reply.get("playlist") or [])
        if rows:
            return rows
        status = self._deck_status.get(channel) or {}
        total = int(status.get("total_tracks") or 0)
        if total > 0:
            # Engine is up but IPC was busy; keep what the pane already had.
            return []
        if self.library_mode == "MUSIC":
            return core.read_playlist_rows()[:200]
        try:
            import mix_archive_scanner
            return list(mix_archive_scanner.MixArchiveScanner().get_mixes()[:200])
        except Exception:
            return []

    def _command_worker(self) -> None:
        while not self._worker_stop.is_set():
            try:
                channel, cmd, kwargs = self._command_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            engine = self.decks.get(channel)
            if engine is None:
                continue
            try:
                engine.command(cmd, **kwargs)
            except Exception:
                pass

    def _send(self, channel: str, cmd: str, **kwargs: Any) -> None:
        self._command_queue.put((channel, cmd, kwargs))

    def _queue_all_levels(self) -> None:
        for channel in core.CHANNELS:
            volume = int(round(self.model.channel_volume(channel)))
            self._send(channel, "set_volume", volume=volume)
            self._send(channel, "set_eq_bands", bands=self.model.eq_bands(channel))
            self._send(channel, "set_effect", **self.model.effect_payload(channel))
            self._send(channel, "set_pitch", percent=int(round(self.model.pitch(channel))))

    def _sync_level(self, channel: str) -> None:
        self._send(channel, "set_volume",
                   volume=int(round(self.model.channel_volume(channel))))

    def _sync_pitch(self, channel: str) -> None:
        self._send(channel, "set_pitch", percent=int(round(self.model.pitch(channel))))

    def _sync_eq(self, channel: str) -> None:
        self._send(channel, "set_eq_bands", bands=self.model.eq_bands(channel))

    def _sync_effect(self, channel: str) -> None:
        self._send(channel, "set_effect", **self.model.effect_payload(channel))

    # -- MIDI -------------------------------------------------------------
    def _start_midi(self) -> None:
        self.midi = WindowsMidiInput(
            self._on_midi_message, self._on_midi_status,
            profile=self.profile, device=self.midi_device)
        threading.Thread(target=self.midi.start, name="mixer-midi", daemon=True).start()

    def _on_midi_status(self, status: str) -> None:
        self._status_line = status

    def _on_midi_message(self, kind: int, first: int, second: int) -> None:
        notes, ccs = mixer_midi.load_mapping(profile=self.profile)
        if kind == 0x90 and second > 0:
            action = notes.get(int(first))
            if action:
                self._midi_queue.put((action, 1.0))
        elif kind == 0x80 or (kind == 0x90 and second == 0):
            action = notes.get(int(first))
            if action:
                self._midi_queue.put((action, 0.0))
        elif kind == 0xB0:
            action = ccs.get(int(first))
            if action:
                if action.startswith("pitch:"):
                    value = mixer_midi.pitch_percent_7bit(int(second))
                elif action.startswith("eq:"):
                    value = (float(second) - 64.0) / 64.0 * 12.0
                else:
                    value = float(second) / 127.0
                self._midi_queue.put((action, value))

    def _drain_midi(self) -> None:
        while True:
            try:
                action, value = self._midi_queue.get_nowait()
            except queue.Empty:
                return
            try:
                self._apply_midi(action, value)
            except Exception:
                pass

    def _apply_midi(self, action: str, value: float) -> None:
        if action == "record":
            if value > 0:
                self._toggle_recording()
            return
        if action.startswith("play:"):
            if value > 0:
                self._toggle_deck(action.split(":", 1)[1])
            return
        if action.startswith("monitor:"):
            if value > 0:
                channel = action.split(":", 1)[1]
                if channel in self.model.headphones:
                    self.model.toggle_headphone(channel)
            return
        if action.startswith("effect:"):
            if value > 0:
                self.model.toggle_effect(action.split(":", 1)[1])
                self._sync_effect(action.split(":", 1)[1])
            return
        if action.startswith("deck:"):
            if value > 0:
                side = action.split(":", 1)[1]
                self.model.toggle_side_deck(side)
            return
        if action.startswith("shift:"):
            return
        if action.startswith("sync:"):
            if value > 0:
                channel = action.split(":", 1)[1]
                self._sync_to_master(channel)
            return
        if action.startswith("browse"):
            return
        if action.startswith("pitch:"):
            channel = action.split(":", 1)[1]
            self.model.set_pitch(channel, value)
            self._sync_pitch(channel)
            return
        if action.startswith("filter:"):
            channel = action.split(":", 1)[1]
            self.model.set_filter(channel, _clamp(value, 0.0, 1.0))
            self._sync_effect(channel)
            return
        if action.startswith("eq:"):
            _kind, channel, band = (action.split(":") + ["", "", ""])[:3]
            self.model.set_eq(channel, str(band).lower(), value)
            self._sync_eq(channel)
            return
        if action.startswith("trim:"):
            channel = action.split(":", 1)[1]
            self.model.set_trim(channel, _clamp(value * 2.0, 0.0, core.TRIM_MAX))
            self._sync_level(channel)
            return
        if action == "master_volume":
            self.model.set_master_volume(_clamp(value, 0.0, 1.0))
            self._queue_all_levels()
            return
        if action == "monitor_volume":
            self.model.set_monitor_volume(_clamp(value, 0.0, 1.0))
            return
        if mixer_evdev.apply_action(self.model, action, value):
            for channel in core.CHANNELS:
                self._sync_level(channel)

    def _sync_to_master(self, channel: str) -> None:
        # The Windows port keeps sync arithmetic simple and offline: copy the
        # master deck's pitch when a master is already known.
        if channel == getattr(self, "_sync_master", "A"):
            return
        master = getattr(self, "_sync_master", "A")
        self.model.set_pitch(channel, self.model.pitch(master))
        self._sync_pitch(channel)

    # -- visualizer -------------------------------------------------------
    def _start_visualizer(self) -> None:
        if not self.visualizer_enabled or self._closed:
            return
        if self.visualizer is not None and self.visualizer.poll() is None:
            return
        env = dict(os.environ)
        env["MP_VISUALIZER_COMPANION"] = "1"
        env["MP_VISUALIZER_MATCH_GUI"] = "1"
        env.pop("MP_VISUALIZER_DETACHED", None)
        if self.root is not None:
            self.root.update_idletasks()
            self._publish_geometry()
        try:
            self.visualizer = subprocess.Popen(
                [_console_python(), str(ROOT / "MP_Liquid_Visualizer.py"),
                 "--preset", "random", "--backend", "auto"],
                cwd=str(ROOT), env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
            )
        except Exception:
            self.visualizer = None
        self._publish_geometry()

    def _publish_geometry(self) -> None:
        if self.root is None:
            return
        try:
            from liquid_player_visualizer import publish_liquid_gui_window_geometry
            x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
            w, h = self.root.winfo_width(), self.root.winfo_height()
            if w < 64 or h < 64:
                return
            hwnd = _top_level_hwnd(self.root)
            geom = (int(x), int(y), int(w), int(h), int(hwnd))
            if geom != self._visualizer_geometry:
                publish_liquid_gui_window_geometry(x, y, w, h, wid=hwnd)
                self._visualizer_geometry = geom
        except Exception:
            pass

    def _toggle_visualizer(self) -> None:
        self.visualizer_enabled = not self.visualizer_enabled
        self.settings.visualizer_enabled = self.visualizer_enabled
        self._save_windows_settings()
        self._apply_alpha()
        if self.visualizer_enabled:
            self._start_visualizer()
            self._notice = "Visualizer on"
        else:
            self._stop_visualizer()
            self._notice = "Visualizer off"
        self._notice_until = time.monotonic() + 1.5
        self._draw()

    def _stop_visualizer(self) -> None:
        try:
            from liquid_visualizer_session import stop_companion_visualizer
            stop_companion_visualizer(for_gui=True)
        except Exception:
            pass
        proc, self.visualizer = self.visualizer, None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    # -- theme ------------------------------------------------------------
    def _cycle_theme(self) -> None:
        self.theme = core.next_theme(self.theme)
        core.write_theme(self.theme)
        self.settings.theme = self.theme
        try:
            self.settings.save()
        except Exception:
            pass
        self._save_windows_settings()
        self._notice = f"Theme: {core.theme_label(self.theme)}"
        self._notice_until = time.monotonic() + 1.8
        self._draw()

    # -- recording --------------------------------------------------------
    def _toggle_recording(self) -> None:
        if self.recorder is not None:
            path = self.recorder.stop()
            self.recorder = None
            self._notice = f"Recording saved: {path.name}" if path else "Recording stopped"
            self._notice_until = time.monotonic() + 2.5
            self._draw()
            return
        if not self.settings.visualizer_enabled:
            pass
        recorder = WindowsLoopbackRecorder(self.settings.recording_format)
        if not recorder.available():
            self._notice = "Recording needs PyAudioWPatch (pip install PyAudioWPatch)"
            self._notice_until = time.monotonic() + 4.0
            self._draw()
            return
        if recorder.start():
            self.recorder = recorder
            self._notice = "Recording the master output…"
        else:
            self._notice = f"Recording failed: {recorder.error}"
        self._notice_until = time.monotonic() + 3.0
        self._draw()

    # -- deck transport ---------------------------------------------------
    def _toggle_deck(self, channel: str) -> None:
        status = (self._deck_status.get(channel) or {})
        if status.get("state") == "playing":
            self._send(channel, "pause")
        else:
            self._send(channel, "play")
        self._notice = f"Deck {channel} {'pause' if status.get('state') == 'playing' else 'play'}"
        self._notice_until = time.monotonic() + 1.2

    def _switch_side(self, side: str) -> None:
        self.model.toggle_side_deck(side)
        self._draw()

    def _set_browse_target(self, channel: str) -> None:
        if channel in core.CHANNELS:
            self.browse_target = channel
        side = self.model.deck_of(channel)
        if side:
            self.model.side_decks[side] = channel

    # -- settings actions -------------------------------------------------
    def _settings_action(self, key: str) -> None:
        if key == "1":
            self._cycle_theme()
        elif key == "2":
            self._toggle_visualizer()
        elif key == "3":
            self.library_mode = ("MIX_ARCHIVE" if self.library_mode == "MUSIC"
                                 else "MUSIC")
            for engine in self.decks.values():
                engine.set_library(self.library_mode)
            self._notice = f"Library: {self.library_mode}"
            self._notice_until = time.monotonic() + 1.8
        elif key == "4":
            self.profile = "B" if self.profile == "A" else "A"
            if self.midi is not None:
                self.midi.stop()
            self._start_midi()
            self._notice = f"MIDI profile: {mixer_midi.PROFILE_LABELS.get(self.profile, self.profile)}"
            self._notice_until = time.monotonic() + 1.8
        elif key == "5":
            self.settings.recording_format = (
                "FLAC" if self.settings.recording_format == "WAV" else "WAV")
            self._notice = f"Recording format: {self.settings.recording_format}"
            self._notice_until = time.monotonic() + 1.8
        elif key == "6":
            self._toggle_fullscreen()
        elif key == "7":
            self._overlay = ""
        try:
            self.settings.midi_profile = self.profile
            self.settings.visualizer_enabled = self.visualizer_enabled
            self.settings.save()
        except Exception:
            pass
        self._save_windows_settings()
        self._draw()

    def _toggle_fullscreen(self) -> None:
        self.fullscreen = not self.fullscreen
        self._place_window()
        self._save_windows_settings()
        self._draw()

    def _launch_app(self, which: str) -> None:
        targets = {
            "liquid": ROOT / "launch_liquid_glass.bat",
            "gui": ROOT / "launch_mp_gui.bat",
            "manager": ROOT / "manager.bat",
            "visualizer": ROOT / "launch_liquid_visualizer.bat",
        }
        target = targets.get(which)
        if target and target.is_file():
            try:
                subprocess.Popen([str(target)], cwd=str(ROOT),
                                 creationflags=CREATE_NO_WINDOW)
            except Exception:
                pass

    # -- input ------------------------------------------------------------
    def _hit_at(self, x: float, y: float):
        for x0, y0, x1, y1, kind, data in reversed(self._hits):
            if x0 <= x <= x1 and y0 <= y <= y1:
                return kind, data
        return None, None

    def _on_press(self, event) -> None:
        self.canvas.focus_set()
        kind, data = self._hit_at(event.x, event.y)
        if kind is None:
            # overrideredirect(True) removes the title bar, so Windows has no
            # caption to drag. Empty surface becomes the grab handle; full
            # screen has nowhere to move to and keeps a plain click.
            if not self.fullscreen and self.root is not None:
                self._window_drag = (
                    event.x_root - self.root.winfo_x(),
                    event.y_root - self.root.winfo_y(),
                )
            return
        if self._verify_prompt:
            if kind == "button" and data == "verify-remove":
                self._verify_remove_bad()
            elif kind == "button" and data == "verify-keep":
                self._verify_keep_bad()
            else:
                self._verify_keep_bad()
            return
        if kind == "opacity":
            self._drag = ("opacity", data, 0.0, 0.0)
            self._drag_opacity(event.x, data)
            return
        if kind == "button":
            self._button_action(data)
            return
        if kind == "fader":
            channel, box = data
            self._drag = ("fader", (channel, box), box[1], box[1] + box[3])
            self._apply_fader(channel, box, event.y)
        elif kind == "crossfader":
            self._drag = ("crossfader", data, data[0] + 18 * self._ui_scale,
                          data[0] + data[2] - 18 * self._ui_scale)
            self._apply_crossfader(data, event.x)
        elif kind == "knob":
            group, channel, box = data
            low, high = self._knob_range(group)
            self._drag = ("knob", (group, channel, box, event.y, low, high), low, high)
        elif kind == "pitch":
            channel, box = data
            self._drag = ("pitch", (channel, box), box[1], box[1] + box[3])
            self._drag_pitch(channel, box, event.y)
        elif kind == "master_slider":
            self._drag = ("master", data, data[0], data[0] + data[2])
            self._drag_master(data, event.x)
        elif kind == "playlist":
            channel, index = data
            self._load_playlist_index(channel, index)
        elif kind == "settings_row":
            self._settings_action(str(data))
        elif kind == "library":
            self._overlay_index = int(data)
            self._load_library_row(int(data))

    def _on_drag(self, event) -> None:
        if self._window_drag is not None:
            dx, dy = self._window_drag
            self.root.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")
            self._publish_geometry()
            return
        if self._drag is None:
            return
        kind, data, _low, _high = self._drag
        if kind == "fader":
            channel, box = data
            self._apply_fader(channel, box, event.y)
        elif kind == "crossfader":
            self._apply_crossfader(data, event.x)
        elif kind == "knob":
            group, channel, box, start_y, low, high = data
            delta = ((start_y - event.y) / max(1.0, 140.0 * self._ui_scale)
                     * (high - low))
            start_value = self._drag_knob_start
            self._set_knob(group, channel, start_value + delta)
            self._drag = ("knob", data, _low, _high)
        elif kind == "pitch":
            channel, box = data
            self._drag_pitch(channel, box, event.y)
        elif kind == "master":
            self._drag_master(data, event.x)
        elif kind == "opacity":
            self._drag_opacity(event.x, data)
        self._draw()

    def _on_release(self, _event) -> None:
        kind = self._drag[0] if self._drag else ""
        self._drag = None
        self._window_drag = None
        if kind:
            try:
                self.model.save()
            except Exception:
                pass
        if kind in ("opacity", "master"):
            self._save_windows_settings()

    def _on_double(self, event) -> None:
        kind, _data = self._hit_at(event.x, event.y)
        if kind in ("opacity", "master_slider", "crossfader", "fader"):
            return
        self._toggle_fullscreen()

    def _on_wheel(self, event) -> None:
        kind, data = self._hit_at(event.x, event.y)
        step = 1 if event.delta > 0 else -1
        if kind == "opacity":
            self.model.opacity = _clamp(self.model.opacity + step * 0.02,
                                        core.MIN_OPACITY, core.MAX_OPACITY)
            self._apply_alpha()
            self._save_opacity()
        elif kind == "fader":
            channel, _box = data
            self._adjust_fader(channel, step * 0.02)
        elif kind == "crossfader":
            self.model.set_crossfade(self.model.crossfade + step * 0.02)
            self._sync_level("A")
            self._sync_level("B")
        elif kind == "knob":
            group, channel, _box = data
            self._adjust_knob(group, channel, step * 0.02)
        elif kind == "pitch":
            channel, _box = data
            self.model.set_pitch(channel, self.model.pitch(channel) + step * 0.5)
            self._sync_pitch(channel)
        elif kind == "master_slider":
            self.model.set_master_volume(self.model.master_volume + step * 0.02)
            self._queue_all_levels()
        elif kind == "playlist":
            channel, _index = data
            self._scroll_playlist_for(channel, step)
        else:
            return
        self._draw()

    # -- value application ------------------------------------------------
    def _apply_fader(self, channel: str, box, y: float) -> None:
        value = core.fader_value_from_y(y, box)
        if self.model.set_channel(channel, value):
            self._sync_level(channel)
            self._draw()

    def _adjust_fader(self, channel: str, delta: float) -> None:
        if self.model.set_channel(channel, self.model.channel_gain(channel) + delta):
            self._sync_level(channel)

    def _apply_crossfader(self, box, x: float) -> None:
        left = box[0] + 18 * self._ui_scale
        right = box[0] + box[2] - 18 * self._ui_scale
        value = _clamp((x - left) / max(1.0, right - left), 0.0, 1.0)
        self.model.set_crossfade(value)
        self._sync_level("A")
        self._sync_level("B")
        self._draw()

    def _drag_master(self, slider, x: float) -> None:
        value = _clamp((x - slider[0]) / max(1.0, slider[2]), 0.0, 1.0)
        self.model.set_master_volume(value)
        # The software master level scales every deck's engine volume.
        self._queue_all_levels()

    def _drag_pitch(self, channel: str, box, y: float) -> None:
        top = box[1] + 32 * self._ui_scale
        bottom = box[1] + box[3] - 32 * self._ui_scale
        t = _clamp((y - top) / max(1.0, bottom - top), 0.0, 1.0)
        pitch = core.PITCH_MAX - t * (core.PITCH_MAX - core.PITCH_MIN)
        if self.model.set_pitch(channel, pitch):
            self._sync_pitch(channel)

    def _set_knob(self, group: str, channel: str, value: float) -> None:
        if group == "master":
            self.model.set_master_volume(value)
            self._queue_all_levels()
        elif group == "monitor":
            self.model.set_monitor_volume(value)
        elif group == "filter":
            if self.model.set_filter(channel, value):
                self._sync_effect(channel)
        elif group == "gain":
            if self.model.set_trim(channel, value):
                self._sync_level(channel)
        else:
            if self.model.set_eq(channel, group, value):
                self._sync_eq(channel)

    def _adjust_knob(self, group: str, channel: str, delta: float) -> None:
        low, high = self._knob_range(group)
        span = high - low
        self._drag_knob_start = self._knob_value(group, channel) + delta * span
        self._set_knob(group, channel, _clamp(self._drag_knob_start, low, high))

    def _reset_knob(self, group: str, channel: str) -> None:
        if group == "master":
            self._set_knob(group, "", core.DEFAULT_MASTER_VOLUME)
        elif group == "monitor":
            self._set_knob(group, "", core.DEFAULT_MONITOR_VOLUME)
        elif group == "filter":
            self._set_knob(group, channel, core.DEFAULT_FILTER)
        elif group == "gain":
            self._set_knob(group, channel, core.DEFAULT_TRIM)
        else:
            self._set_knob(group, channel, 0.0)

    # -- buttons ----------------------------------------------------------
    def _button_action(self, action: str) -> None:
        if action.startswith("deck-switch:"):
            self._switch_side(action.split(":", 1)[1])
        elif action.startswith("sync:"):
            self._sync_to_master(action.split(":", 1)[1])
        elif action.startswith("cue:"):
            channel = action.split(":", 1)[1]
            self.model.toggle_headphone(channel)
        elif action.startswith("fx:"):
            channel = action.split(":", 1)[1]
            self.model.toggle_effect(channel)
            self._sync_effect(channel)
        elif action.startswith("slot:"):
            _tag, channel, slot = action.split(":")
            index = max(0, int(slot) - 1)
            mode = core.EFFECT_MODES[index] if index < len(core.EFFECT_MODES) else "FLANGER"
            self.model.set_effect_mode(channel, mode)
            self._sync_effect(channel)
        elif action.startswith("filter-mode:"):
            channel = action.split(":", 1)[1]
            self.model.set_filter_mode(channel)
            self._sync_effect(channel)
        elif action.startswith("playlist:"):
            channel, index = action.split(":", 1)[1].split(":")
            self._load_playlist_index(channel, int(index))
        elif action == "trance":
            return
        elif action == "profile":
            self._toggle_profile()
        elif action == "theme":
            self._cycle_theme()
        elif action == "visualizer":
            self._toggle_visualizer()
        elif action == "record":
            self._toggle_recording()
        elif action == "settings":
            self._overlay = "settings"
        elif action == "browse":
            self._cycle_browse_target()
        elif action == "mode":
            self.model.toggle_mode()
            self._notice = f"Mixer mode: {self.model.mode}"
            self._notice_until = time.monotonic() + 1.8
        elif action == "format":
            self._toggle_recording_format()
        elif action == "verify":
            self._start_verify_audio()
        elif action == "verify-remove":
            self._verify_remove_bad()
        elif action == "verify-keep":
            self._verify_keep_bad()
        elif action == "export":
            self._export_mixes()
        elif action == "close":
            self.close()
            return
        self._draw()

    def _toggle_profile(self) -> None:
        self.profile = "B" if self.profile != "B" else "A"
        self.settings.midi_profile = self.profile
        if self.midi is not None:
            self.midi.stop()
        self._start_midi()
        self._notice = f"MIDI profile: {midi_profile_label(self.profile)}"
        self._notice_until = time.monotonic() + 1.8
        self._save_windows_settings()

    def _cycle_browse_target(self) -> None:
        order = core.CHANNELS
        try:
            index = order.index(self.browse_target)
        except ValueError:
            index = 0
        self.browse_target = order[(index + 1) % len(order)]
        self._notice = f"Browse target: Deck {self.browse_target}"
        self._notice_until = time.monotonic() + 1.5

    def _toggle_recording_format(self) -> None:
        current = str(self.settings.recording_format or "WAV").upper()
        self.settings.recording_format = "FLAC" if current == "WAV" else "WAV"
        self._notice = f"Recording format: {self.settings.recording_format}"
        self._notice_until = time.monotonic() + 1.8
        try:
            self.settings.save()
        except Exception:
            pass

    # -- library / playlists ----------------------------------------------
    def _load_playlist_index(self, channel: str, index: int) -> None:
        rows = self._playlist_rows.get(channel) or []
        if not (0 <= index < len(rows)):
            return
        row = rows[index]
        real = int(row.get("index", index))
        self._send(channel, "select_index", index=real)
        self._set_browse_target(channel)
        self._notice = f"{channel}: {_short(row.get('name') or row.get('path'), 48)}"
        self._notice_until = time.monotonic() + 2.0
        self._draw()

    def _scroll_playlist_for(self, channel: str, delta: int) -> None:
        rows = len(self._playlist_rows.get(channel) or [])
        current = self._playlist_scroll.get(channel, 0)
        self._playlist_scroll[channel] = max(0, min(max(0, rows - 3), current + delta))

    def _scroll_playlist(self, key: str) -> None:
        channel = self.model.side_decks.get("L", "A")
        delta = -3 if key in ("up", "prior") else 3
        self._scroll_playlist_for(channel, delta)
        self._draw()

    def _open_library(self, channel: str) -> None:
        if not self._playlist_rows.get(channel):
            rows = self._fetch_playlist(channel)
            if rows:
                self._playlist_rows[channel] = rows
        rows = self._playlist_rows.get(channel) or []
        self._overlay = "library"
        self._overlay_deck = channel
        self._overlay_query = ""
        self._overlay_source = list(rows)
        self._refresh_overlay_rows()
        self._draw()

    def _refresh_overlay_rows(self) -> None:
        query = str(getattr(self, "_overlay_query", "") or "").lower().strip()
        rows = list(getattr(self, "_overlay_source", []) or [])
        if query:
            rows = [row for row in rows
                    if query in f"{row.get('name', '')} {row.get('path', '')}".lower()]
        self._overlay_rows = rows
        self._overlay_index = 0

    def _overlay_scroll(self, delta: int) -> None:
        rows = len(self._overlay_rows)
        self._overlay_index = max(0, min(max(0, rows - 1),
                                         getattr(self, "_overlay_index", 0) + delta))
        self._draw()

    def _load_library_row(self, index: int) -> None:
        rows = self._overlay_rows
        if not (0 <= index < len(rows)):
            return
        row = rows[index]
        channel = self._overlay_deck
        real = int(row.get("index", index))
        self._send(channel, "select_index", index=real)
        self._overlay = ""
        self._set_browse_target(channel)
        self._notice = f"{channel}: {_short(row.get('name') or row.get('path'), 48)}"
        self._notice_until = time.monotonic() + 2.0
        self._draw()

    # -- verify audio files ----------------------------------------------
    def _start_verify_audio(self) -> None:
        """Check every row of the browsed playlist, as the Linux mixer does."""
        if self._verify_running:
            self._cancel_verify_audio()
            return
        channel = self.model.side_decks.get("L", "A")
        rows = list(self._playlist_rows.get(channel) or [])
        if not rows:
            self._notice = "Verify: no playlist rows to check"
            self._notice_until = time.monotonic() + 2.5
            self._draw()
            return
        self._verify_running = True
        self._verify_done = 0
        self._verify_total = len(rows)
        self._verify_bad = []
        self._verify_prompt = False
        self._verify_message = "Checking audio files..."
        self._verify_cancel.clear()
        threading.Thread(target=self._verify_worker, args=(rows,), daemon=True,
                         name="mixer-verify").start()
        self._draw()

    def _verify_worker(self, rows: List[Dict[str, Any]]) -> None:
        def progress(done: int, total: int, verdict) -> None:
            self._verify_done = done
            self._verify_total = total
            if not getattr(verdict, "ok", True):
                self._verify_bad.append({"path": getattr(verdict, "path", ""),
                                         "reason": getattr(verdict, "reason", "unreadable")})

        try:
            core.verify_audio_library(rows, workers=8, progress=progress,
                                      cancel=self._verify_cancel.is_set)
        except Exception as exc:
            self._verify_message = f"Verify failed: {exc}"
        self._verify_running = False
        if self._verify_bad:
            self._verify_prompt = True
            self._verify_message = f"{len(self._verify_bad)} file(s) cannot be played"
        else:
            self._verify_message = "ALL AUDIO FILES ARE PLAYABLE"

    def _cancel_verify_audio(self) -> None:
        self._verify_cancel.set()
        self._verify_running = False
        self._verify_message = "Verify cancelled"

    def _verify_remove_bad(self) -> None:
        channel = self.model.side_decks.get("L", "A")
        paths = [row.get("path") for row in self._verify_bad if row.get("path")]
        removed = 0
        try:
            removed = core.remove_rows_from_playlist(paths)
        except Exception:
            removed = 0
        self._verify_prompt = False
        self._verify_bad = []
        self._verify_message = f"Removed {removed} unplayable row(s)"
        if removed:
            self._playlist_loaded_at[channel] = 0.0

    def _verify_keep_bad(self) -> None:
        self._verify_prompt = False
        self._verify_bad = []
        self._verify_message = "Unplayable rows kept"

    def _export_mixes(self) -> None:
        """Move finished recordings into the primary MIX_ARCHIVE."""
        import mixer_export
        if self.recorder is not None:
            self._notice = "Stop the recording before exporting mixes"
            self._notice_until = time.monotonic() + 3.0
            self._draw()
            return
        try:
            result = mixer_export.export_recordings()
            action = str(result.get("action") or "")
            moved = len(result.get("moved") or [])
            if action == "moved":
                self._notice = f"Exported {moved} recording(s) into the archive"
            elif action == "empty":
                self._notice = "Export: no finished recordings to move"
            elif action == "no_archive":
                self._notice = "Export: no MIX_ARCHIVE is configured"
            elif action == "missing_archive":
                self._notice = "Export: the archive folder is not available"
            else:
                self._notice = f"Export: {action or 'nothing moved'}"
        except Exception as exc:
            self._notice = f"Export failed: {exc}"
        self._notice_until = time.monotonic() + 4.0
        self._draw()

    # -- drawing ----------------------------------------------------------
    def _font(self, size: float, bold: bool = False) -> Tuple[str, int, str]:
        scale = self._ui_scale if self._ui_scale > 0 else 1.0
        pixels = max(6, int(round(float(size) * scale)))
        return ("Consolas", pixels, "bold" if bold else "normal")

    def _palette(self) -> Dict[str, Any]:
        """The Linux palette, so both ports paint the same colours.

        Into Darkness has a black field and black accents: fills and borders
        stay black, while text accents are brightened just enough to read,
        exactly as ``_ink_accent`` does on Linux.
        """
        accent, secondary, tertiary = core.theme_colors(self.theme)
        field = _rgb(core.theme_field(self.theme), (0, 0, 0))
        console = _rgb(core.theme_console(self.theme), (0, 0, 0))
        ink = _rgb(core.deck_pane_ink(self.theme), (236, 239, 244))
        dark_ui = _luma(field) < 0.55
        glass_ink = (245, 248, 252) if dark_ui else (20, 24, 30)
        return {
            "accent": _rgb(accent, (176, 88, 255)),
            "secondary": _rgb(secondary, (0, 242, 254)),
            "tertiary": _rgb(tertiary, (255, 45, 142)),
            "accent_ink": _ink_accent(_rgb(accent, (176, 88, 255)), dark_ui),
            "secondary_ink": _ink_accent(_rgb(secondary, (0, 242, 254)), dark_ui),
            "tertiary_ink": _ink_accent(_rgb(tertiary, (255, 45, 142)), dark_ui),
            "field": field,
            "console": console,
            "ink": ink,
            "glass_ink": glass_ink,
            "dark_ui": dark_ui,
        }

    # -- text helpers -----------------------------------------------------
    def _text(self, x: float, y: float, text: Any, size: float,
              color: Tuple[int, int, int], alpha: float = 1.0,
              bold: bool = False, anchor: str = "w",
              width: float = 0.0, base: Optional[Tuple[int, int, int]] = None) -> int:
        alpha = _clamp(float(alpha), 0.0, 1.0)
        fill = _with(base or self._last_base, color, alpha)
        return self.canvas.create_text(
            x, y, text=str(text or ""), fill=fill, anchor=anchor,
            font=self._font(size, bold), width=int(width) if width > 0 else 0)

    def _text_in_box(self, box, text: Any, size: float,
                     color: Tuple[int, int, int], alpha: float = 1.0,
                     bold: bool = False, align: str = "center",
                     pad: float = 0.0) -> None:
        """One label centred vertically inside a button rectangle."""
        x, y, w, h = box
        if align == "start":
            tx, anchor = x + pad, "w"
        elif align == "end":
            tx, anchor = x + w - pad, "e"
        else:
            tx, anchor = x + w / 2.0, "center"
        self._text(tx, y + h / 2.0, text, size, color, alpha, bold, anchor)

    def _rounded_rect(self, x: float, y: float, w: float, h: float,
                      radius: float, fill: Optional[str] = None,
                      outline: Optional[str] = None, width: float = 1.0) -> None:
        """A rounded rectangle built from two ovals and a body rectangle.

        Tk has no native rounded rectangle; this composite is the standard
        trick and keeps the mixer's pill buttons matching the Linux surface.
        """
        radius = max(0.0, min(radius, w / 2.0, h / 2.0))
        if fill:
            if radius <= 0.5:
                self.canvas.create_rectangle(x, y, x + w, y + h, fill=fill, outline="")
            else:
                self.canvas.create_oval(x, y, x + 2 * radius, y + h, fill=fill, outline="")
                self.canvas.create_oval(x + w - 2 * radius, y, x + w, y + h,
                                        fill=fill, outline="")
                self.canvas.create_rectangle(x + radius, y, x + w - radius, y + h,
                                             fill=fill, outline="")
        if outline:
            if radius <= 0.5:
                self.canvas.create_rectangle(x, y, x + w, y + h, fill="", outline=outline,
                                             width=width)
            else:
                self.canvas.create_arc(x, y, x + 2 * radius, y + h, start=90, extent=180,
                                       style="arc", outline=outline, width=width)
                self.canvas.create_arc(x + w - 2 * radius, y, x + w, y + h,
                                       start=270, extent=180, style="arc",
                                       outline=outline, width=width)
                self.canvas.create_line(x + radius, y, x + w - radius, y,
                                        fill=outline, width=width)
                self.canvas.create_line(x + radius, y + h, x + w - radius, y + h,
                                        fill=outline, width=width)

    def _panel(self, box, fill: Tuple[int, int, int], alpha: float,
               radius: float, outline: Optional[Tuple[int, int, int]] = None,
               outline_alpha: float = 1.0, width: float = 1.0) -> None:
        x, y, w, h = box
        self._rounded_rect(
            x, y, w, h, radius,
            fill=_with(self._last_base, fill, alpha),
            outline=(_with(self._last_base, outline, outline_alpha) if outline else None),
            width=width)

    # -- the window -------------------------------------------------------
    def _tick(self) -> None:
        if self._closed or self.root is None:
            return
        self._drain_midi()
        clock = time.strftime("%H:%M:%S")
        second = int(time.time()) % 60
        if second != self._clock_second:
            self._clock_second = second
            self._clock_text = clock
            self._clock_date = time.strftime("%a %d %b %Y")
        if time.monotonic() >= self._visualizer_at:
            self._visualizer_at = time.monotonic() + 0.5
            self._publish_geometry()
            if (self.visualizer_enabled and self.visualizer is not None
                    and self.visualizer.poll() is not None
                    and time.monotonic() >= getattr(self, "_visualizer_retry_at", 0.0)):
                self._visualizer_retry_at = time.monotonic() + 8.0
                self._start_visualizer()
        self._draw()
        self.root.after(80, self._tick)

    def _draw(self) -> None:
        if self.canvas is None:
            return
        self.canvas.delete("all")
        self._hits.clear()
        p = self._palette()
        # Everything is painted against the theme field. The Tk window's own
        # alpha is what lets the visualizer show through, exactly like the
        # GTK host's window opacity, so the paint itself stays opaque.
        self._last_base = p["field"]
        self.canvas.configure(bg=_hex(p["field"]))
        self.canvas.create_rectangle(0, 0, self._w, self._h,
                                     fill=_hex(p["field"]), outline="")
        self._paint_glow(p)
        self._draw_top_bar(p)
        visible = self._visible_decks()
        self._draw_deck("L", visible.get("L", "A"), p)
        self._draw_deck("R", visible.get("R", "B"), p)
        self._draw_pitch(p)
        self._draw_mixer(p)
        self._draw_bottom_bar(p)
        self._draw_master_meter(p)
        self._draw_overlay(p)
        self._draw_verify_dialog(p)

    def _paint_glow(self, p: Dict[str, Any]) -> None:
        """Two soft horizontal bands standing in for the Linux radial glow."""
        w, h = self._w, self._h
        for centre, color, alpha in ((0.10, p["accent"], 0.20),
                                     (0.55, p["secondary"], 0.10)):
            y = h * centre
            for step in range(14, 0, -1):
                band = h * 0.035 * step
                fade = alpha * (1.0 - step / 15.0) * 0.5
                self.canvas.create_rectangle(
                    0, y - band, w, y + band,
                    fill=_with(p["field"], color, fade), outline="")

    # -- top bar ----------------------------------------------------------
    def _draw_top_bar(self, p: Dict[str, Any]) -> None:
        box = self.boxes["top_bar"]
        ink, glass = p["ink"], p["glass_ink"]
        accent, secondary = p["accent_ink"], p["secondary_ink"]
        s = self._ui_scale
        x, y, w, h = box
        self.canvas.create_rectangle(x, y, x + w, y + h,
                                     fill=_with(p["field"], _darken(p["field"], 0.55), 0.72),
                                     outline="")
        self.canvas.create_rectangle(x, y + h - max(1.0, 1.2 * s), x + w,
                                     y + h, fill=_hex(accent), outline="")
        self._text_in_box(box, "MP LIQUID MIXER", 21, glass, 0.96, True,
                          "start", 28 * s)
        # The version sits under the two trims, dim and partly behind them, the
        # way the reference ribbon shows it.
        self._text_in_box(box, f"v{VERSION}", 12, glass, 0.55, False,
                          "start", 252 * s)
        # MASTER / MONITOR trims sit right beside the title, as they do in the
        # reference window, with their percentages on one line underneath.
        self._paint_ribbon_knob(self.boxes["master_knob"], "MASTER",
                                self.model.master_volume, accent, p)
        self._paint_ribbon_knob(self.boxes["monitor_knob"], "MON",
                                self.model.monitor_volume, secondary, p)
        self._text_in_box(self.boxes["master_label"],
                          f"MASTER {int(round(self.model.master_volume * 100))}%   "
                          f"MON {int(round(self.model.monitor_volume * 100))}%",
                          8.5, glass, 0.72, True, "center")

        # Trance Connection: a status lamp in the suite's button style.
        trance = self.boxes["trance_button"]
        self._panel(trance, accent, 0.16, 9 * s)
        self._rounded_rect(trance[0], trance[1], trance[2], trance[3], 9 * s,
                           outline=_with(p["field"], accent, 0.78),
                           width=max(1.0, 1.2 * s))
        self._text_in_box(trance, "Trance Connection Enabled", 11, glass, 0.95, True)
        self._hits.append((trance[0], trance[1], trance[0] + trance[2],
                           trance[1] + trance[3], "button", "trance"))

        # Controller status pill.
        pill = self.boxes["status_pill"]
        status_text, status_color = self._midi_status()
        self._panel(pill, status_color, 0.14, 9 * s)
        self._rounded_rect(pill[0], pill[1], pill[2], pill[3], 9 * s,
                           outline=_with(p["field"], status_color, 0.72),
                           width=max(1.0, 1.2 * s))
        self._text_in_box(pill, _short(status_text, 46), 10.5, status_color, 0.98, True)
        self._hits.append((pill[0], pill[1], pill[0] + pill[2], pill[1] + pill[3],
                           "button", "midi-status"))

        # SETTINGS and VISUALIZER.
        settings = self.boxes["settings_button"]
        self._panel(settings, _darken(p["field"], 0.35), 0.35, 9 * s)
        self._rounded_rect(settings[0], settings[1], settings[2], settings[3],
                           9 * s,
                           outline=_with(p["field"], accent, 0.72),
                           width=max(1.0, 1.2 * s))
        self._text_in_box(settings, "SETTINGS", 12, glass, 0.92, True)
        self._hits.append((settings[0], settings[1], settings[0] + settings[2],
                           settings[1] + settings[3], "button", "settings"))

        viz = self.boxes["visualizer_button"]
        active = self.visualizer_enabled
        self._panel(viz, accent if active else _darken(p["field"], 0.35),
                    0.30 if active else 0.35, 9 * s)
        self._rounded_rect(viz[0], viz[1], viz[2], viz[3], 9 * s,
                           outline=_with(p["field"], accent if active else glass, 0.80),
                           width=max(1.0, 1.2 * s))
        self._text_in_box(viz, f"VISUALIZER {'ON' if active else 'OFF'}", 12,
                          glass, 0.92, True)
        self._hits.append((viz[0], viz[1], viz[0] + viz[2], viz[1] + viz[3],
                           "button", "visualizer"))

        # The close button: the reference window keeps a rounded X at the very
        # right of the ribbon instead of relying on Esc alone.
        close = self.boxes["close_button"]
        self._panel(close, _darken(p["field"], 0.30), 0.55, 12 * s)
        cx = close[0] + close[2] / 2.0
        cy = close[1] + close[3] / 2.0
        arm = 9 * s
        self.canvas.create_line(cx - arm, cy - arm, cx + arm, cy + arm,
                                fill=_hex(glass), width=max(1.4, 1.8 * s))
        self.canvas.create_line(cx - arm, cy + arm, cx + arm, cy - arm,
                                fill=_hex(glass), width=max(1.4, 1.8 * s))
        self._hits.append((close[0], close[1], close[0] + close[2],
                           close[1] + close[3], "button", "close"))

        # MIDI DJ MIXER PROFILE strip, under the status pill.
        profile = self.boxes["profile_button"]
        hue = accent if self.profile == "A" else secondary
        self._panel(profile, hue, 0.14, 7 * s)
        self._rounded_rect(profile[0], profile[1], profile[2], profile[3],
                           7 * s,
                           outline=_with(p["field"], hue, 0.95),
                           width=max(1.0, 1.3 * s))
        self._text_in_box(profile, f"MIDI DJ MIXER PROFILE   {self.profile}", 8,
                          hue, 0.98, True)
        self._hits.append((profile[0], profile[1], profile[0] + profile[2],
                           profile[1] + profile[3], "button", "profile"))

    def _paint_ribbon_knob(self, box, caption: str, value: float,
                           hue: Tuple[int, int, int], p: Dict[str, Any]) -> None:
        cx = box[0] + box[2] / 2.0
        cy = box[1] + box[3] / 2.0
        radius = max(10.0, 15.0 * self._ui_scale)
        self._paint_dial(cx, cy, radius, value, 0.0, 1.0, hue, p)
        self._hits.append((box[0], box[1], box[0] + box[2], box[1] + box[3],
                           "knob", ("master" if caption == "MASTER" else "monitor",
                                    "", box)))

    def _midi_status(self) -> Tuple[str, Tuple[int, int, int]]:
        label = mixer_midi.PROFILE_LABELS.get(self.profile, "MIDI")
        midi = self.midi
        if midi is not None and midi._running:
            return f"DJ MIXER {label}  {midi.device_name} mapped", (80, 230, 140)
        if midi is not None and midi.status.startswith("no Kontrol"):
            found = midi.status.split("found:", 1)[-1].strip(" )")
            return (f"Kontrol {label} not found - check the USB cable"
                    if found in ("none", "") else
                    f"Kontrol {label} not found (found: {found})"), (250, 205, 90)
        return f"DJ MIXER {label} NOT FOUND", (250, 205, 90)

    # -- the two player panes --------------------------------------------
    def _visible_decks(self) -> Dict[str, str]:
        return {"L": self.model.side_decks.get("L", "A"),
                "R": self.model.side_decks.get("R", "B")}

    def _draw_deck(self, side: str, channel: str, p: Dict[str, Any]) -> None:
        s = self._ui_scale
        key = "a" if side == "L" else "b"
        card = self.boxes[f"deck_{key}"]
        header = self.boxes[f"deck_{key}_header"]
        term = self.boxes[f"deck_{key}_term"]
        hue = p["accent_ink"] if side == "L" else p["secondary_ink"]
        ink, glass = p["ink"], p["glass_ink"]

        # Card body and header, filled inside the card's rounded path.
        self._panel(card, _light(_darken(p["field"], 0.25), 0.08), 0.38,
                    18 * self._ui_scale)
        self._panel(header, _darken(p["field"], 0.45), 0.52, 18 * self._ui_scale)
        self._rounded_rect(card[0], card[1], card[2], card[3], 18 * self._ui_scale,
                           outline=_with(p["field"], p["accent"], 0.55),
                           width=max(1.0, 1.2 * self._ui_scale))

        status = self._deck_status.get(channel) or {}
        playing = str(status.get("state") or "").lower() == "playing"
        online = bool(status.get("total_tracks")) or bool(status.get("title"))
        led = (80, 230, 140) if playing else ((250, 205, 90) if online else (150, 160, 168))

        # Header row, matching the reference: switch button at the left, SYNC
        # near the right, and the state word at the far right.
        led_x = header[0] + 22 * s
        led_y = header[1] + header[3] / 2.0
        self.canvas.create_oval(led_x - 5.5 * s, led_y - 5.5 * s,
                                led_x + 5.5 * s, led_y + 5.5 * s,
                                fill=_hex(led), outline="")

        switch = self.boxes[f"deck_switch_{key}"]
        other = self.model.other_deck_for_side(side)
        self._panel(switch, hue, 0.14, 9 * s)
        self._rounded_rect(switch[0], switch[1], switch[2], switch[3], 9 * s,
                           outline=_with(p["field"], hue, 0.85),
                           width=max(1.2, 1.8 * s))
        self._text(switch[0] + switch[2] / 2.0, switch[1] + switch[3] / 2.0 - 5 * s,
                   f"DECK {channel}", 8.5, glass, 0.98, True, "center")
        self._text(switch[0] + switch[2] / 2.0, switch[1] + switch[3] / 2.0 + 6 * s,
                   f"SWITCH TO {other}", 5.8, glass, 0.95, True, "center")
        self._hits.append((switch[0], switch[1], switch[0] + switch[2],
                           switch[1] + switch[3], "button", f"deck-switch:{side}"))

        sync = self.boxes[f"sync_{key}"]
        master = self.sync_master == channel
        self._panel(sync, hue, 0.34 if master else 0.10, 7 * s)
        self._rounded_rect(sync[0], sync[1], sync[2], sync[3], 7 * s,
                           outline=_with(p["field"], hue, 0.90 if master else 0.55),
                           width=max(1.0, 1.1 * s))
        self._text_in_box(sync, "MASTER" if master else "SYNC", 9, glass,
                          0.98 if master else 0.85, True)
        self._hits.append((sync[0], sync[1], sync[0] + sync[2], sync[1] + sync[3],
                           "button", f"sync:{channel}"))

        label = "LIVE" if playing else ("READY" if online else "OFFLINE")
        self._text(header[0] + header[2] - 16 * s, led_y, label, 10, led, 0.95, True, "e")

        message = ""
        if not playing and str(status.get("last_load_error") or ""):
            message = str(status.get("last_load_error"))
        elif not online:
            message = "Waiting for engine..."
        if message:
            self._text(header[0] + header[2] / 2.0,
                       header[1] + header[3] + 8 * s,
                       _short(message, 74), 9.5, glass, 0.78, True, "center",
                       header[2] - 40 * s)

        self._draw_player_pane(term, channel, side, p)

    def _draw_player_pane(self, box, channel: str, side: str,
                          p: Dict[str, Any]) -> None:
        """One MP Liquid Player panel: transport, spectrum and the playlist.

        On Linux this rectangle holds a real VTE terminal running the player.
        The Windows port paints the same panel from the engine's own status and
        playlist, so the DJ sees the list each deck is mixing instead of an
        embedded terminal it cannot host.
        """
        x, y, w, h = box
        s = self._ui_scale
        ink, glass = p["ink"], p["glass_ink"]
        status = self._deck_status.get(channel) or {}
        rows = self._playlist_rows.get(channel) or []
        hue = p["accent_ink"] if side == "L" else p["secondary_ink"]
        accent = p["accent_ink"]
        cyan = p["secondary_ink"]

        self.canvas.create_rectangle(x, y, x + w, y + h,
                                     fill=_hex(_light(_darken(p["field"], 0.62), 0.03)),
                                     outline=_with(p["field"], p["accent"], 0.35))
        pad = 16 * s

        # Title strip: the visualizer name and the library the deck reads,
        # inside the bordered plate the reference pane draws.
        title_box = (x + pad, y + 8 * s, w - 2 * pad, 44 * s)
        self._rounded_rect(title_box[0], title_box[1], title_box[2], title_box[3],
                           4 * s, outline=_with(p["field"], glass, 0.55))
        self._text(x + w / 2.0, y + 24 * s,
                   "◆ MP LIQUID GLASS VISUALIZER – 0 Cosmic Dreamworlds (Liquid) ◆",
                   13, glass, 0.92, True, "center", w - 2 * pad)
        library_name = ("All Music Folders" if self.library_mode == "MUSIC"
                        else "Mix Archive")
        self._text(x + w / 2.0, y + 44 * s, library_name, 11, glass, 0.80,
                   False, "center")

        state = str(status.get("state") or "paused").upper()
        mode = str(status.get("mode") or self.library_mode).replace("_", " ")
        title = str(status.get("title") or "No track loaded")
        playing = state == "PLAYING"

        self._text(x + pad, y + 64 * s,
                   f"{'▶' if playing else '❚❚'} {state}  [{mode} MODE]  ♪ {_short(title, 58)}",
                   12, cyan if playing else ink, 0.95, True, "w", w - 2 * pad)

        fmt = str(Path(str(status.get("path") or "")).suffix or "").lstrip(".").upper() or "—"
        rate = status.get("sample_rate")
        rate_text = f"{float(rate) / 1000.0:.1f} kHz" if rate else "—"
        layout = {1: "Mono"}.get(int(status.get("channels") or 2), "Stereo")
        self._text(x + pad, y + 86 * s,
                   f"Format: {fmt:<5} |   {rate_text:>8}   |   {layout}",
                   11, ink, 0.85, False, "w")
        self._text(x + w - pad, y + 86 * s,
                   str(status.get("duration_fmt") or "00:00"), 11, ink, 0.85,
                   False, "e")

        # Transport line: position, progress, duration, percentage.
        pos = str(status.get("position_fmt") or "00:00")
        dur = str(status.get("duration_fmt") or "00:00")
        pct = float(status.get("progress_pct") or 0.0)
        self._text(x + pad, y + 106 * s, pos, 11, cyan, 0.95, True, "w")
        bar_x0, bar_x1 = x + pad + 62 * s, x + w - pad - 150 * s
        bar_y = y + 106 * s
        self.canvas.create_rectangle(bar_x0, bar_y - 2 * s, bar_x1, bar_y + 2 * s,
                                     fill=_with(p["field"], glass, 0.18), outline="")
        self.canvas.create_rectangle(bar_x0, bar_y - 2 * s,
                                     bar_x0 + (bar_x1 - bar_x0) * _clamp(pct / 100.0, 0.0, 1.0),
                                     bar_y + 2 * s, fill=_hex(cyan), outline="")
        self._text(x + w - pad, y + 106 * s, f"{dur}  ({pct:.1f}%)", 11, cyan,
                   0.95, True, "e")

        # DJ pitch and volume rows, exactly as the player prints them.
        pitch = self.model.pitch(channel)
        self._text(x + pad, y + 128 * s, "DJ PITCH:", 10, hue, 0.85, True, "w")
        pitch_bar_x0 = x + pad + 76 * s
        pitch_bar_x1 = x + w - pad - 104 * s
        py = y + 129 * s
        self.canvas.create_line(pitch_bar_x0, py, pitch_bar_x1, py,
                                fill=_with(p["field"], glass, 0.30),
                                width=max(1.0, 1.4 * s))
        centre = (pitch_bar_x0 + pitch_bar_x1) / 2.0
        frac = (pitch - core.PITCH_MIN) / max(1e-6, core.PITCH_MAX - core.PITCH_MIN)
        thumb = pitch_bar_x0 + frac * (pitch_bar_x1 - pitch_bar_x0)
        self.canvas.create_line(centre, py - 4 * s, centre, py + 4 * s,
                                fill=_with(p["field"], glass, 0.55))
        self.canvas.create_oval(thumb - 3 * s, py - 3 * s, thumb + 3 * s, py + 3 * s,
                                fill=_hex(hue), outline="")
        self._text(pitch_bar_x0 - 8 * s, py, "-20%", 8, glass, 0.55, False, "e")
        self._text(pitch_bar_x1 + 8 * s, py, "+20%", 8, glass, 0.55, False, "w")
        self._text(x + w - pad, py, f"{pitch:+.1f}%", 10, hue, 0.90, True, "e")

        volume = int(status.get("volume") or 0)
        self._text(x + pad, y + 147 * s, "VOLUME:", 10, cyan, 0.85, True, "w")
        vol_x0 = x + pad + 76 * s
        vol_x1 = x + w - pad - 104 * s
        vy = y + 148 * s
        self.canvas.create_line(vol_x0, vy, vol_x1, vy,
                                fill=_with(p["field"], glass, 0.30),
                                width=max(1.0, 1.4 * s))
        self.canvas.create_line(vol_x0, vy,
                                vol_x0 + (vol_x1 - vol_x0) * _clamp(volume / 100.0, 0.0, 1.0),
                                vy, fill=_hex(cyan), width=max(2.0, 2.4 * s))
        self._text(vol_x0 - 8 * s, vy, "0%", 8, glass, 0.55, False, "e")
        self._text(vol_x1 + 8 * s, vy, "100%", 8, glass, 0.55, False, "w")
        self._text(x + w - pad, vy, f"{volume}%", 10, cyan, 0.90, True, "e")

        # The two 16-band spectrum panes.
        spec_top = y + 168 * s
        spec_h = 196 * s
        spec_gap = 14 * s
        spec_w = (w - 2 * pad - spec_gap) / 2.0
        self._text(x + pad + spec_w / 2.0, spec_top - 7 * s,
                   "⚡ LEFT  (16-BAND NEON PRISM)", 9, hue, 0.85, True, "center")
        self._text(x + pad + spec_w + spec_gap + spec_w / 2.0, spec_top - 7 * s,
                   "⚡ RIGHT  (16-BAND NEON PRISM)", 9, cyan, 0.85, True, "center")
        left = list(status.get("levels_left") or status.get("levels") or [])
        right = list(status.get("levels_right") or status.get("levels") or [])
        self._draw_spectrum(x + pad, spec_top, spec_w, spec_h, left, hue, p)
        self._draw_spectrum(x + pad + spec_w + spec_gap, spec_top, spec_w, spec_h,
                            right, cyan, p)

        # FAVORITES / VISUALIZATION / BASS BOOST chips.
        chip_y = spec_top + spec_h + 14 * s
        chip_h = 18 * s
        chips = (("★ FAVORITES", p["glass_ink"]),
                 ("VISUALIZATION", cyan),
                 ("BASS BOOST: " + str(status.get("bass_mode") or "NORMAL").upper(), hue))
        chip_x = x + pad
        for text, color in chips:
            chip_w = (len(text) * 7.2 + 22) * s
            chip_w = min(chip_w, (w - 2 * pad) / 3.0 - 6 * s)
            self._panel((chip_x, chip_y, chip_w, chip_h), color, 0.14, 3 * s)
            self._rounded_rect(chip_x, chip_y, chip_w, chip_h, 3 * s,
                               outline=_with(p["field"], color, 0.55))
            self._text_in_box((chip_x, chip_y, chip_w, chip_h), text, 10, color,
                              0.92, True)
            chip_x += chip_w + 6 * s

        # The music library list this deck is mixing from.
        list_y = chip_y + chip_h + 18 * s
        total = int(status.get("total_tracks") or len(rows))
        header = (f"MUSIC LIBRARY: {library_name} ({total} Tracks)"
                  if self.library_mode == "MUSIC"
                  else f"MIX ARCHIVE ({total} Tracks)")
        self.canvas.create_rectangle(x + pad, list_y - 11 * s,
                                     x + w - pad, list_y + 7 * s,
                                     fill=_with(p["field"], p["glass_ink"], 0.10),
                                     outline=_with(p["field"], p["accent"], 0.35))
        self._text(x + pad + 8 * s, list_y - 2 * s, _short(header, 60), 10,
                   p["glass_ink"], 0.85, True, "w")

        active = int(status.get("index") or 0) if rows else -1
        scroll = self._playlist_scroll.get(channel, 0)
        row_h = 20 * s
        rows_top = list_y + 16 * s
        rows_visible = max(3, int((y + h - rows_top - 46 * s) / row_h))
        if active >= 0:
            # Keep the playing row on screen as the deck advances.
            if active < scroll:
                scroll = active
            elif active >= scroll + rows_visible:
                scroll = active - rows_visible + 1
            self._playlist_scroll[channel] = scroll
        if not rows:
            self._text(x + pad + 8 * s, rows_top + row_h / 2.0,
                       "reading the playlist…", 11, p["glass_ink"], 0.55, False, "w")
        for offset in range(rows_visible):
            index = scroll + offset
            if index >= len(rows):
                break
            row = rows[index]
            ry = rows_top + offset * row_h
            mine = index == active
            if mine:
                self.canvas.create_rectangle(x + pad, ry - row_h / 2.0,
                                             x + w - pad, ry + row_h / 2.0,
                                             fill=_with(p["field"], cyan, 0.22),
                                             outline="")
            name = _short(row.get("name") or Path(str(row.get("path") or "")).name,
                          max(18, int((w - 330 * s) / (6.4 * s))))
            ext = str(row.get("ext") or Path(str(row.get("path") or "")).suffix.lstrip(".")).upper()
            row_dur = self._row_duration(row, s)
            marker = "▶" if mine else " "
            self._text(x + pad + 6 * s, ry, f"{marker} [ {index + 1:>3} ]",
                       10, cyan if mine else p["glass_ink"], 0.95 if mine else 0.55,
                       True, "w")
            self._text(x + pad + 96 * s, ry, name, 10,
                       p["glass_ink"], 0.98 if mine else 0.82, mine, "w")
            self._text(x + w - pad - 62 * s, ry, _short(ext, 5), 10,
                       p["glass_ink"], 0.72, False, "e")
            self._text(x + w - pad - 6 * s, ry, row_dur, 10,
                       p["glass_ink"], 0.72, False, "e")
            self._hits.append((x + pad, ry - row_h / 2.0, x + w - pad,
                               ry + row_h / 2.0, "playlist", (channel, index)))

        # Footer hints, matching the player's own key legend.
        hint_y = y + h - 30 * s
        self._text(x + pad, hint_y,
                   "↑/↓: Scroll   Enter: Play   [1] Mix  [2] Music  [3] Favorites  [H]: Shuffle",
                   8.5, p["glass_ink"], 0.55, False, "w", w - 2 * pad)
        self._text(x + pad, hint_y + 13 * s,
                   f"Vol {volume}%   Pitch {pitch:+.1f}%   "
                   f"{'Playing' if playing else 'Paused'}   "
                   "Click a track to load it",
                   8.5, p["glass_ink"], 0.48, False, "w", w - 2 * pad)

    def _draw_spectrum(self, x: float, y: float, w: float, h: float,
                       levels: List[Any], color: Tuple[int, int, int],
                       p: Dict[str, Any]) -> None:
        """One 24-band analyser pane with the player's frequency ruler."""
        s = self._ui_scale
        self.canvas.create_rectangle(x, y, x + w, y + h,
                                     fill=_hex(_darken(p["field"], 0.60)),
                                     outline=_with(p["field"], p["accent"], 0.30))
        values = [float(v) for v in levels if isinstance(v, (int, float))][:24]
        bands = 24
        ruler_h = 22 * s
        plot_h = max(8.0, h - ruler_h - 10 * s)
        slot = w / float(bands)
        bar_w = max(2.0, slot * 0.62)
        for index in range(bands):
            level = values[index] if index < len(values) else 0.0
            level = _clamp(level, 0.0, 1.0)
            bx = x + index * slot + (slot - bar_w) / 2.0
            if level > 0.004:
                bar_h = max(1.0, plot_h * level)
                # Neon prism: cool at the bottom of the spectrum, warm at the top.
                tone = _mix(color, (255, 255, 255), 0.15 + 0.35 * index / bands)
                self.canvas.create_rectangle(bx, y + 8 * s + plot_h - bar_h,
                                             bx + bar_w, y + 8 * s + plot_h,
                                             fill=_hex(tone), outline="")
        # The dashed cyan ruler the reference pane prints under the bars.
        ruler_y = y + h - ruler_h
        dash = max(1.0, 2.0 * s)
        step = max(2.0, 4.0 * s)
        dx = x + 4 * s
        while dx < x + w - 4 * s:
            self.canvas.create_line(dx, ruler_y, min(dx + dash, x + w - 4 * s), ruler_y,
                                    fill=_hex(p["secondary_ink"]), width=max(1.0, 1.2 * s))
            dx += step
        labels = ("32", "64", "125", "250", "500", "1k", "4k", "16k")
        for index, label in enumerate(labels):
            lx = x + w * (index + 0.5) / len(labels)
            self._text(lx, y + h - 7 * s, label, 9, p["glass_ink"], 0.72, False, "center")

    def _row_duration(self, row: Dict[str, Any], s: float) -> str:
        value = row.get("duration_fmt")
        if value:
            return str(value)
        path = str(row.get("path") or "")
        if path in self._duration_cache:
            return self._duration_cache[path]
        seconds = row.get("duration_sec")
        if isinstance(seconds, (int, float)) and seconds > 0:
            text = _fmt_time(seconds)
            self._duration_cache[path] = text
            return text
        return ""

    # -- tempo sliders ----------------------------------------------------
    def _draw_pitch(self, p: Dict[str, Any]) -> None:
        s = self._ui_scale
        visible = self._visible_decks()
        for side in ("L", "R"):
            channel = visible.get(side, side)
            box = self.boxes[f"pitch_{'a' if side == 'L' else 'b'}"]
            hue = p["accent_ink"] if side == "L" else p["secondary_ink"]
            self._panel(box, _darken(p["field"], 0.58), 0.55, 10 * s)
            self._rounded_rect(box[0], box[1], box[2], box[3], 10 * s,
                               outline=_with(p["field"], hue, 0.55))
            self._text(box[0] + box[2] / 2.0, box[1] + 4 * s, "PITCH", 7,
                       p["glass_ink"], 0.55, True, "center")
            top = box[1] + 32 * s
            bottom = box[1] + box[3] - 32 * s
            cx = box[0] + box[2] / 2.0
            self.canvas.create_line(cx, top, cx, bottom,
                                    fill=_hex(_darken(p["field"], 0.5)),
                                    width=max(4.0, 5.0 * s), capstyle="round")
            centre = (top + bottom) / 2.0
            self.canvas.create_line(cx - 6 * s, centre, cx + 6 * s, centre,
                                    fill=_with(p["field"], p["glass_ink"], 0.40))
            pitch = self.model.pitch(channel)
            t = (core.PITCH_MAX - pitch) / (core.PITCH_MAX - core.PITCH_MIN)
            thumb_y = top + _clamp(t, 0.0, 1.0) * (bottom - top)
            self._rounded_rect(cx - 9 * s, thumb_y - 6 * s, 18 * s, 12 * s, 4 * s,
                               fill=_hex(_light(hue, 0.25)),
                               outline=_hex(_darken(p["field"], 0.6)))
            self._text(cx, top - 12 * s, "+20", 6.5, p["glass_ink"], 0.55, False, "center")
            self._text(cx, centre - 3 * s, "0", 6.5, p["glass_ink"], 0.55, False, "center")
            self._text(cx, bottom + 2 * s, "-20", 6.5, p["glass_ink"], 0.55, False, "center")
            self._text(cx, box[1] + box[3] + 2 * s, f"{pitch:+.1f}%", 7.5, hue, 0.90,
                       True, "center")
            self._hits.append((box[0], box[1], box[0] + box[2], box[1] + box[3],
                               "pitch", (channel, box)))

    # -- the mixer column -------------------------------------------------
    KNOB_ROWS = (
        ("gain", "GAIN", 0.0, core.TRIM_MAX, core.DEFAULT_TRIM),
        ("high", "TREBLE", core.EQ_MIN, core.EQ_MAX, 0.0),
        ("mid", "MID", core.EQ_MIN, core.EQ_MAX, 0.0),
        ("low", "LOW", core.EQ_MIN, core.EQ_MAX, 0.0),
    )

    def _knob_value(self, group: str, channel: str) -> float:
        if group == "master":
            return self.model.master_volume
        if group == "monitor":
            return self.model.monitor_volume
        if group == "filter":
            return self.model.filter_value(channel)
        if group == "gain":
            return self.model.trim(channel)
        return self.model.eq_value(channel, group)

    def _knob_range(self, group: str) -> Tuple[float, float]:
        if group in ("master", "monitor", "filter"):
            return 0.0, 1.0
        for key, _label, low, high, _default in self.KNOB_ROWS:
            if key == group:
                return low, high
        return 0.0, 1.0

    def _channel_hue(self, channel: str, p: Dict[str, Any]) -> Tuple[int, int, int]:
        if channel == "A":
            return p["accent_ink"]
        if channel == "B":
            return p["secondary_ink"]
        return (140, 148, 156)

    def _draw_mixer(self, p: Dict[str, Any]) -> None:
        s = self._ui_scale
        panel = self.boxes["mixer"]
        ink, glass = p["ink"], p["glass_ink"]
        base = _darken(p["field"], 0.55) if p["dark_ui"] else _light(p["field"], 0.55)
        self._panel(panel, base, 0.68, 20 * s)
        self._rounded_rect(panel[0], panel[1], panel[2], panel[3], 20 * s,
                           outline=_with(p["field"], glass, 0.26),
                           width=max(1.0, 1.1 * s))
        self._text(panel[0] + panel[2] / 2.0, panel[1] + 18 * s, "MIXER", 15,
                   glass, 0.85, True, "center")
        self._text(panel[0] + panel[2] / 2.0, panel[1] + 40 * s, self.model.mode, 10,
                   p["accent_ink"] if not self.model.external else (250, 205, 90),
                   0.95, True, "center")

        self._draw_knobs(p)
        self._draw_filters(p)
        self._draw_effects(p)
        for channel in core.CHANNELS:
            self._draw_strip(channel, p)
        self._draw_xfader(p)
        self._draw_mixer_buttons(p)

    def _draw_knobs(self, p: Dict[str, Any]) -> None:
        s = self._ui_scale
        for group, _label, low, high, _default in self.KNOB_ROWS:
            for channel in core.CHANNELS:
                box = self.boxes[f"knob_{group}_{channel}"]
                cx, cy = box[0] + box[2] / 2.0, box[1] + box[3] / 2.0
                self._paint_dial(cx, cy, max(11.0, 16.0 * s),
                                 self._knob_value(group, channel), low, high,
                                 self._channel_hue(channel, p), p)
                value_box = self.boxes.get(f"knob_{group}_{channel}_value")
                if value_box is not None:
                    self._text(value_box[0] + value_box[2] / 2.0, value_box[1],
                               self._knob_caption(group, self._knob_value(group, channel)),
                               8.8, p["glass_ink"], 0.85, False, "center")
                self._hits.append((box[0], box[1], box[0] + box[2], box[1] + box[3],
                                   "knob", (group, channel, box)))

    @staticmethod
    def _knob_caption(group: str, value: float) -> str:
        if group in ("gain", "filter"):
            return str(int(round(value * 100)))
        return f"{value:+.0f}" if abs(value) >= 0.5 else "0"

    def _draw_filters(self, p: Dict[str, Any]) -> None:
        s = self._ui_scale
        for channel in core.CHANNELS:
            box = self.boxes[f"knob_filter_{channel}"]
            cx, cy = box[0] + box[2] / 2.0, box[1] + box[3] / 2.0
            self._paint_dial(cx, cy, max(14.0, 24.0 * s),
                             self.model.filter_value(channel), 0.0, 1.0,
                             self._channel_hue(channel, p), p)
            value_box = self.boxes.get(f"knob_filter_{channel}_value")
            if value_box is not None:
                self._text(value_box[0] + value_box[2] / 2.0, value_box[1],
                           str(int(round(self.model.filter_value(channel) * 100))),
                           8.8, p["glass_ink"], 0.85, False, "center")
            self._hits.append((box[0], box[1], box[0] + box[2], box[1] + box[3],
                               "knob", ("filter", channel, box)))

    def _paint_dial(self, cx: float, cy: float, radius: float, value: float,
                    low: float, high: float, hue: Tuple[int, int, int],
                    p: Dict[str, Any]) -> None:
        import math as _math
        s = self._ui_scale
        t = (float(value) - low) / max(1e-6, high - low)
        t = _clamp(t, 0.0, 1.0)
        start = _math.radians(135.0)
        end = start + _math.radians(270.0) * t
        self.canvas.create_arc(cx - radius, cy - radius, cx + radius, cy + radius,
                               start=-45, extent=270, style="arc",
                               outline=_with(p["field"], p["glass_ink"], 0.16),
                               width=max(3.0, 3.2 * s))
        self.canvas.create_arc(cx - radius, cy - radius, cx + radius, cy + radius,
                               start=-45, extent=270 * t, style="arc",
                               outline=_hex(hue), width=max(3.0, 3.2 * s))
        inner = radius * 0.60
        self.canvas.create_oval(cx - inner, cy - inner, cx + inner, cy + inner,
                                fill=_hex(_darken(p["field"], 0.45)),
                                outline=_with(p["field"], p["glass_ink"], 0.35))
        self.canvas.create_line(cx, cy,
                                cx + _math.cos(end) * radius * 0.55,
                                cy + _math.sin(end) * radius * 0.55,
                                fill=_hex(hue), width=max(1.4, 1.6 * s))

    def _draw_effects(self, p: Dict[str, Any]) -> None:
        s = self._ui_scale
        for channel in core.CHANNELS:
            chosen = self.model.effect_mode(channel)
            for slot, mode in enumerate(core.EFFECT_MODES):
                box = self.boxes[f"effect_slot_{slot + 1}_{channel}"]
                color = core.EFFECT_SLOT_COLORS[slot]
                selected = (mode == chosen)
                self.canvas.create_rectangle(box[0], box[1], box[0] + box[2],
                                             box[1] + box[3],
                                             fill=_hex(color if selected
                                                       else _darken(color, 0.45)),
                                             outline=(_hex(_light(color, 0.65))
                                                      if selected else ""))
                self._hits.append((box[0], box[1], box[0] + box[2], box[1] + box[3],
                                   "button", f"slot:{channel}:{slot + 1}"))
            fbox = self.boxes[f"effect_filter_{channel}"]
            selected = (chosen == "FILTER")
            color = core.FILTER_SLOT_COLOR
            self.canvas.create_rectangle(fbox[0], fbox[1], fbox[0] + fbox[2],
                                         fbox[1] + fbox[3],
                                         fill=_hex(color if selected
                                                   else _darken(color, 0.45)),
                                         outline=(_hex(_light(color, 0.65))
                                                  if selected else ""))
            self._hits.append((fbox[0], fbox[1], fbox[0] + fbox[2], fbox[1] + fbox[3],
                               "button", f"filter-mode:{channel}"))

    def _draw_strip(self, channel: str, p: Dict[str, Any]) -> None:
        s = self._ui_scale
        box = self.boxes[f"strip_{channel}"]
        label_box = self.boxes[f"strip_{channel}_label"]
        value_box = self.boxes[f"strip_{channel}_value"]
        active = self.model.channel_active(channel)
        hue = self._channel_hue(channel, p)
        gain = self.model.channel_gain(channel)
        glass = p["glass_ink"]

        self._paint_fx_button(channel, hue, p)
        self._paint_monitor_button(channel, hue, p)
        self._text(label_box[0] + label_box[2] / 2.0, label_box[1] + 8 * s,
                   channel, 20, hue if active else (120, 128, 136),
                   0.98 if active else 0.55, True, "center")

        cx = box[0] + box[2] / 2.0
        track_w = max(5.0, 7.0 * s)
        thumb_h = max(12.0, 15.0 * s)
        thumb_w = max(30.0, 38.0 * s)
        self.canvas.create_line(cx, box[1] + thumb_h / 2.0,
                                cx, box[1] + box[3] - thumb_h / 2.0,
                                fill=_hex(_darken(p["field"], 0.4)),
                                width=track_w, capstyle="round")
        if active:
            thumb_y = core.y_from_fader_value(
                gain, (box[0], box[1] + thumb_h / 2.0, box[2], box[3] - thumb_h))
            self.canvas.create_line(cx, thumb_y, cx, box[1] + box[3] - thumb_h / 2.0,
                                    fill=_hex(hue), width=track_w, capstyle="round")
            for step in range(1, 10):
                ty = box[1] + thumb_h / 2.0 + (box[3] - thumb_h) * step / 10.0
                self.canvas.create_line(cx + 14 * s, ty, cx + 22 * s, ty,
                                        fill=_with(p["field"], glass, 0.20))
            self._rounded_rect(cx - thumb_w / 2.0, thumb_y - thumb_h / 2.0,
                               thumb_w, thumb_h, 4 * s,
                               fill=_hex(_light(hue, 0.25)),
                               outline=_hex(_darken(p["field"], 0.6)))
            self._text(value_box[0] + value_box[2] / 2.0, value_box[1] + 6 * s,
                       f"{int(round(gain * 100))}%", 10.5, glass, 0.78, False, "center")
        self._draw_channel_meter(channel, p)
        self._hits.append((box[0], box[1], box[0] + box[2], box[1] + box[3],
                           "fader", (channel, box)))

    def _paint_fx_button(self, channel: str, hue, p: Dict[str, Any]) -> None:
        s = self._ui_scale
        box = self.boxes[f"fx_{channel}"]
        enabled = self.model.effect_on(channel)
        mode = self.model.effect_mode(channel)
        if mode == "FILTER":
            color = core.FILTER_SLOT_COLOR
        elif mode in core.EFFECT_MODES:
            color = core.EFFECT_SLOT_COLORS[core.EFFECT_MODES.index(mode)]
        else:
            color = hue
        self._panel(box, color, 0.62 if enabled else 0.10, 3.5 * s)
        self._rounded_rect(box[0], box[1], box[2], box[3], 3.5 * s,
                           outline=_with(p["field"], color, 0.98 if enabled else 0.55),
                           width=max(0.9, 1.1 * s))
        # The reference window labels the bypass switch ON/OFF rather than FX,
        # so the channel's effect state reads at a glance.
        self._text_in_box(box, "ON" if enabled else "OFF", 7.8,
                          p["glass_ink"], 0.98 if enabled else 0.62, True)
        self._hits.append((box[0], box[1], box[0] + box[2], box[1] + box[3],
                           "button", f"fx:{channel}"))

    def _paint_monitor_button(self, channel: str, hue, p: Dict[str, Any]) -> None:
        import math as _math
        s = self._ui_scale
        box = self.boxes[f"monitor_{channel}"]
        cued = self.model.headphone(channel)
        color = (80, 230, 140) if cued else hue
        self._panel(box, color, 0.30 if cued else 0.08, 4 * s)
        self._rounded_rect(box[0], box[1], box[2], box[3], 4 * s,
                           outline=_with(p["field"], color, 0.95 if cued else 0.40),
                           width=max(0.9, 1.1 * s))
        cx, cy = box[0] + box[2] / 2.0, box[1] + box[3] / 2.0 - 1 * s
        r = max(3.4, 5.0 * s)
        glyph = p["glass_ink"] if not cued else (245, 248, 252)
        self.canvas.create_arc(cx - r, cy - r + 1.5 * s, cx + r, cy + r + 1.5 * s,
                               start=200, extent=140, style="arc",
                               outline=_hex(glyph), width=max(1.1, 1.5 * s))
        for dx in (-r, r):
            self._rounded_rect(cx + dx - 1.3 * s, cy + 0.2 * s, 2.6 * s, 4.6 * s,
                               1.1 * s, fill=_hex(glyph))
        self._hits.append((box[0], box[1], box[0] + box[2], box[1] + box[3],
                           "button", f"cue:{channel}"))

    def _draw_channel_meter(self, channel: str, p: Dict[str, Any]) -> None:
        box = self.boxes[f"meter_{channel}"]
        peak = self._deck_peak_db(channel)
        fraction = self.model.meter_fraction(channel, peak)
        self._draw_meter_column(box, fraction, p)

    def _deck_peak_db(self, channel: str) -> float:
        status = self._deck_status.get(channel) or {}
        value = status.get("peak_db")
        try:
            db = float(value)
        except (TypeError, ValueError):
            peaks = status.get("peaks")
            if isinstance(peaks, list) and peaks:
                try:
                    db = 20.0 * math.log10(max(1e-6, max(float(p) for p in peaks)))
                except (TypeError, ValueError):
                    db = core.METER_MIN_DB
            else:
                db = core.METER_MIN_DB
        return max(core.METER_MIN_DB, min(core.METER_MAX_DB, db))

    @staticmethod
    def _meter_zone_color(fraction: float) -> Tuple[int, int, int]:
        color = core.METER_ZONES[0][1]
        for start, zone_color in core.METER_ZONES:
            if fraction >= start:
                color = zone_color
        return color

    def _draw_meter_column(self, box, fraction: float, p: Dict[str, Any]) -> None:
        s = self._ui_scale
        x, y, w, h = box
        fraction = _clamp(float(fraction), 0.0, 1.0)
        self.canvas.create_rectangle(x, y, x + w, y + h,
                                     fill=_hex(_darken(p["field"], 0.55)), outline="")
        segments = max(6, int(h / max(3.0, 4.2 * s)))
        gap = max(1.0, 1.2 * s)
        seg_h = (h - gap * (segments - 1)) / segments
        lit = fraction * segments
        for index in range(segments):
            top = y + h - (index + 1) * seg_h - index * gap
            color = self._meter_zone_color((index + 0.5) / segments)
            if index < int(lit):
                alpha = 0.95
            elif index == int(lit) and lit - int(lit) > 0.25:
                alpha = 0.55
            else:
                alpha = 0.14
            self.canvas.create_rectangle(x + 1.0 * s, top, x + w - 1.0 * s, top + seg_h,
                                         fill=_with(p["field"], color, alpha), outline="")

    def _draw_xfader(self, p: Dict[str, Any]) -> None:
        s = self._ui_scale
        box = self.boxes["xfader"]
        cy = box[1] + box[3] / 2.0
        accent, secondary = p["accent_ink"], p["secondary_ink"]
        glass = p["glass_ink"]
        left, right = box[0] + 18 * s, box[0] + box[2] - 18 * s
        self.canvas.create_line(left, cy, right, cy,
                                fill=_hex(_darken(p["field"], 0.35)),
                                width=max(8.0, 10.0 * s), capstyle="round")
        centre_x = box[0] + box[2] / 2.0
        self.canvas.create_line(centre_x, box[1] + 6 * s, centre_x, box[1] + box[3] - 6 * s,
                                fill=_with(p["field"], glass, 0.45),
                                width=max(1.0, 1.3 * s))
        # Equal-power gradient stand-in: A half in accent, B half in secondary.
        mid = (left + right) / 2.0
        self.canvas.create_line(left, cy, mid, cy, fill=_hex(accent),
                                width=max(5.0, 6.0 * s), capstyle="round")
        self.canvas.create_line(mid, cy, right, cy, fill=_hex(secondary),
                                width=max(5.0, 6.0 * s), capstyle="round")
        thumb_x = left + self.model.crossfade * (right - left)
        self._rounded_rect(thumb_x - 7 * s, cy - 20 * s, 14 * s, 40 * s, 5 * s,
                           fill=_hex((245, 248, 252)),
                           outline=_hex(_darken(p["field"], 0.6)))
        self._text(box[0] + 4 * s, cy + 5 * s, "A", 12, accent, 0.95, True, "w")
        self._text(box[0] + box[2] - 4 * s, cy + 5 * s, "B", 12, secondary, 0.95,
                   True, "e")
        self._text(centre_x, box[1] + box[3] - 2 * s,
                   f"CROSSFADER {int(round(self.model.crossfade * 100))}%",
                   8, glass, 0.50, False, "center")
        self._hits.append((box[0], box[1], box[0] + box[2], box[1] + box[3],
                           "crossfader", box))

    def _draw_mixer_buttons(self, p: Dict[str, Any]) -> None:
        s = self._ui_scale
        glass = p["glass_ink"]
        accent, secondary = p["accent_ink"], p["secondary_ink"]
        self._paint_pill("verify_button", "Verify Audio Files", accent, 0.16,
                         p, "button", "verify", bold=True, size=9)
        self._paint_pill("browse_button", f"BROWSE  {self.browse_target}",
                         accent if self.browse_target in ("A", "C") else secondary,
                         0.16, p, "button", "browse", bold=True, size=9.5)
        self._paint_pill("mode_button", self.model.mode,
                         (250, 205, 90) if self.model.external else accent, 0.22,
                         p, "button", "mode", bold=True, size=10,
                         subtitle=("unity / external mixer" if self.model.external
                                   else "software faders live"))
        fmt_color = (250, 205, 90) if self.settings.recording_format == "FLAC" else accent
        self._paint_pill("format_button", self.settings.recording_format, fmt_color,
                         0.20, p, "button", "format", bold=True, size=10.5)
        self._paint_pill("bitdepth_button", "24-Bit", secondary, 0.14, p,
                         "button", "bitdepth", bold=True, size=10.5)

        recording = self.recorder is not None
        if recording:
            label = f"STOP   {_fmt_time(self.recorder.duration())}"
            color = (235, 64, 72)
        elif not WindowsLoopbackRecorder(self.settings.recording_format).available():
            label, color = "REC UNAVAILABLE", (250, 205, 90)
        else:
            label, color = "REC", _mix(accent, secondary, 0.5)
        box = self.boxes["record_button"]
        self._panel(box, color, 0.24 if recording else 0.16, 9 * s)
        self._rounded_rect(box[0], box[1], box[2], box[3], 9 * s,
                           outline=_with(p["field"], color, 0.90),
                           width=max(1.0, 1.2 * s))
        self._text(box[0] + box[2] / 2.0, box[1] + box[3] / 2.0, label, 10.5,
                   glass, 0.95, True, "center")
        self.canvas.create_oval(box[0] + box[2] / 2.0 - 42 * s,
                                box[1] + box[3] / 2.0 - 4 * s,
                                box[0] + box[2] / 2.0 - 34 * s,
                                box[1] + box[3] / 2.0 + 4 * s,
                                fill=_hex(color), outline="")
        caption = (f"master output -> {self.settings.recording_format}"
                   if not recording else "recording the master output")
        self._text(box[0] + box[2] / 2.0, box[1] + box[3] + 6 * s, caption,
                   7.6, glass, 0.50, False, "center", box[2] + 40 * s)
        self._hits.append((box[0], box[1], box[0] + box[2], box[1] + box[3],
                           "button", "record"))

        self._paint_pill("credit_button", "DREAMWORLDS PRODUCTIONS", secondary,
                         0.14, p, "label", "", bold=True, size=8.2)
        clock = self.boxes["clock_button"]
        self._panel(clock, accent, 0.13, 8 * s)
        self._rounded_rect(clock[0], clock[1], clock[2], clock[3], 8 * s,
                           outline=_with(p["field"], accent, 0.62))
        self._text(clock[0] + clock[2] / 2.0, clock[1] + 7 * s, self._clock_date,
                   9, glass, 0.66, True, "center")
        self._text(clock[0] + clock[2] / 2.0, clock[1] + clock[3] - 10 * s,
                   self._clock_text, 17, glass, 0.97, True, "center")
        self._paint_pill("export_button", "EXPORT MIXES",
                         _mix(accent, secondary, 0.5), 0.18, p,
                         "button", "export", bold=True, size=10)

    def _paint_pill(self, key: str, label: str, color, alpha: float,
                    p: Dict[str, Any], kind: str, action: str,
                    bold: bool = False, size: float = 9,
                    subtitle: str = "") -> None:
        s = self._ui_scale
        box = self.boxes[key]
        self._panel(box, color, alpha, 7.5 * s)
        self._rounded_rect(box[0], box[1], box[2], box[3], 7.5 * s,
                           outline=_with(p["field"], color, 0.84),
                           width=max(1.0, 1.2 * s))
        if subtitle:
            self._text(box[0] + box[2] / 2.0, box[1] + box[3] / 2.0 - 5 * s,
                       label, size, p["glass_ink"], 0.95, True, "center")
            self._text(box[0] + box[2] / 2.0, box[1] + box[3] / 2.0 + 8 * s,
                       subtitle, 7.6, p["glass_ink"], 0.55, False, "center")
        else:
            self._text_in_box(box, label, size, p["glass_ink"], 0.96, bold)
        if kind != "label":
            self._hits.append((box[0], box[1], box[0] + box[2], box[1] + box[3],
                               kind, action))

    # -- bottom ribbon ----------------------------------------------------
    def _draw_bottom_bar(self, p: Dict[str, Any]) -> None:
        s = self._ui_scale
        box = self.boxes["bottom_bar"]
        ink, glass = p["ink"], p["glass_ink"]
        x, y, w, h = box
        self.canvas.create_rectangle(x, y, x + w, y + h,
                                     fill=_with(p["field"], _darken(p["field"], 0.55), 0.72),
                                     outline="")
        self.canvas.create_rectangle(x, y, x + w, y + max(1.0, 1.0 * s),
                                     fill=_hex(p["accent_ink"]), outline="")
        hints = ("S  SETTINGS    C  THEME    V  VISUALIZER    W  BLACK GLASS    "
                 "X  MIX MODE    F11  ESC  QUIT")
        self._text(x + w - 28 * s, y + 5 * s, hints, 8.5, glass, 0.55, False, "e")
        launches = ("M  MANAGER    Z  VISUALIZER    L  LIQUID PLAYER    G  MP GUI")
        self._text(x + w - 28 * s, y + 21 * s, launches, 7.6, glass, 0.44, False, "e")
        self._draw_opacity_bar(p)

    def _draw_opacity_bar(self, p: Dict[str, Any]) -> None:
        """The transparency track, at the width and place the Linux bar uses."""
        s = self._ui_scale
        track = self.boxes["opacity"]
        glass = p["glass_ink"]
        accent = p["accent_ink"]
        cy = track[1] + track[3] / 2.0
        self._text(track[0] - 96 * s, cy, "TRANSPARENCY", 8, glass, 0.70, True, "w")
        self.canvas.create_line(track[0], cy, track[0] + track[2], cy,
                                fill=_with(p["field"], glass, 0.28),
                                width=max(4.0, 5.0 * s), capstyle="round")
        thumb_x = core.x_from_opacity(self.model.opacity, track)
        self.canvas.create_line(track[0], cy, thumb_x, cy, fill=_hex(accent),
                                width=max(4.0, 5.0 * s), capstyle="round")
        self.canvas.create_oval(thumb_x - 7 * s, cy - 7 * s,
                                thumb_x + 7 * s, cy + 7 * s,
                                fill=_hex((245, 248, 252)),
                                outline=_hex(accent), width=max(1.0, 2.0 * s))
        self._text(track[0] + track[2] + 12 * s, cy,
                   f"{int(round(self.model.opacity * 100))}%", 10, glass, 0.80,
                   True, "w")
        self._hits.append((track[0] - 8 * s, cy - 12 * s,
                           track[0] + track[2] + 8 * s, cy + 12 * s,
                           "opacity", track))

    def _draw_master_meter(self, p: Dict[str, Any]) -> None:
        s = self._ui_scale
        box = self.boxes["master_meter"]
        levels = [self._deck_peak_db(channel) for channel in core.CHANNELS]
        fraction = self.model.meter_fraction("", max(levels) if levels else core.METER_MIN_DB)
        self._draw_meter_column(box, fraction, p)
        self._text(box[0] + box[2] + 8 * s, box[1] + 1 * s, "MASTER", 7.6,
                   p["glass_ink"], 0.72, True, "w")

        slider = self.boxes["master_slider"]
        cy = slider[1] + slider[3] / 2.0
        self.canvas.create_line(slider[0], cy, slider[0] + slider[2], cy,
                                fill=_with(p["field"], p["glass_ink"], 0.22),
                                width=max(4.0, 5.0 * s), capstyle="round")
        thumb_x = slider[0] + self.model.master_volume * slider[2]
        self.canvas.create_line(slider[0], cy, thumb_x, cy,
                                fill=_hex(p["accent_ink"]),
                                width=max(4.0, 5.0 * s), capstyle="round")
        self.canvas.create_oval(thumb_x - 6 * s, cy - 6 * s,
                                thumb_x + 6 * s, cy + 6 * s,
                                fill=_hex((245, 248, 252)), outline="")
        self._text(slider[0] + slider[2] + 8 * s, slider[1] + 2 * s,
                   f"VOL {int(round(self.model.master_volume * 100))}%", 7.6,
                   p["glass_ink"], 0.80, True, "w")
        self._hits.append((slider[0] - 6 * s, cy - 10 * s,
                           slider[0] + slider[2] + 6 * s, cy + 10 * s,
                           "master_slider", slider))

    # -- alpha / transparency --------------------------------------------
    def _target_alpha(self) -> float:
        """How opaque the window should be right now.

        With the visualizer behind it the window uses the model's transparency
        value, which is what makes the scene show through the glass. Without a
        companion there is nothing behind it, so it stays fully solid rather
        than letting the desktop bleed into the panels.
        """
        if not self.visualizer_enabled:
            return 1.0
        return _clamp(float(self.model.opacity), core.MIN_OPACITY, core.MAX_OPACITY)

    def _apply_alpha(self) -> float:
        target = self._target_alpha()
        self._alpha_state = {
            "target": target,
            "applied": self._last_alpha_applied,
            "visualizer": bool(self.visualizer_enabled),
            "error": "",
        }
        if self.root is None:
            return target
        if self._last_alpha_applied is not None and abs(target - self._last_alpha_applied) < 1e-4:
            return target
        try:
            self.root.attributes("-alpha", target)
            self._last_alpha_applied = target
            self._alpha_state["applied"] = target
            self._alpha_state["error"] = ""
        except Exception as exc:  # pragma: no cover - platform specific
            self._alpha_state["error"] = str(exc)
        return target

    def _drag_opacity(self, x: float, track) -> None:
        self.model.opacity = core.opacity_from_x(x, track)
        self._apply_alpha()
        self._draw()

    def _save_opacity(self) -> None:
        try:
            self.model.save()
        except Exception:
            pass
        self._save_windows_settings()

    # -- overlays ---------------------------------------------------------
    def _draw_overlay(self, p: Dict[str, Any]) -> None:
        if not self._overlay:
            return
        s = self._ui_scale
        w, h = self._w, self._h
        accent, glass = p["accent_ink"], p["glass_ink"]
        ow, oh = min(760 * s, w - 80), min(540 * s, h - 80)
        x, y = (w - ow) / 2.0, (h - oh) / 2.0
        self.canvas.create_rectangle(0, 0, w, h, fill="#000000", stipple="gray50",
                                     outline="")
        self.canvas.create_rectangle(x, y, x + ow, y + oh,
                                     fill=_hex(_light(_darken(p["field"], 0.62), 0.03)),
                                     outline=_hex(accent), width=2)
        if self._overlay == "settings":
            self._text(x + 22 * s, y + 26 * s, "MIXER SETTINGS", 16, accent, 0.96,
                       True, "w")
            rows = [
                ("1", f"Theme: {core.theme_label(self.theme)}"),
                ("2", f"Visualizer: {'On' if self.visualizer_enabled else 'Off'}"),
                ("3", f"Library: {self.library_mode}"),
                ("4", f"MIDI profile: {midi_profile_label(self.profile)}"),
                ("5", f"Recording: {self.settings.recording_format}"),
                ("6", f"Fullscreen: {'On' if self.fullscreen else 'Off'}"),
                ("7", "Close"),
            ]
            ry = y + 68 * s
            for key, text in rows:
                self._text(x + 26 * s, ry, f"[{key}]  {text}", 12, glass, 0.92, False, "w")
                self._hits.append((x + 18 * s, ry - 14 * s, x + ow - 18 * s, ry + 14 * s,
                                   "settings_row", key))
                ry += 42 * s
            self._text(x + 22 * s, y + oh - 26 * s,
                       f"Transparency {int(round(self.model.opacity * 100))}%   "
                       f"applied {int(round((self._alpha_state.get('applied') or self._target_alpha()) * 100))}%"
                       f"   MIDI device: {getattr(self.midi, 'device_name', '') or 'none'}",
                       9, p["secondary_ink"], 0.80, False, "w")
        elif self._overlay == "library":
            self._text(x + 24 * s, y + 26 * s,
                       f"LIBRARY - DECK {self._overlay_deck}", 16, accent, 0.96,
                       True, "w")
            query = getattr(self, "_overlay_query", "")
            self._text(x + 24 * s, y + 52 * s,
                       f"Search: {query or '(type to filter)'}", 10, glass, 0.65,
                       False, "w")
            ry = y + 78 * s
            for offset, row in enumerate(self._overlay_rows[:18]):
                index = offset
                mine = index == getattr(self, "_overlay_index", 0)
                if mine:
                    self.canvas.create_rectangle(x + 12 * s, ry - 11 * s,
                                                 x + ow - 12 * s, ry + 11 * s,
                                                 fill=_hex(_mix(p["field"], p["secondary_ink"], 0.25)),
                                                 outline="")
                name = _short(row.get("name") or Path(str(row.get("path") or "")).name, 66)
                self._text(x + 24 * s, ry, f"{index + 1:02d}  {name}", 10,
                           glass, 0.98 if mine else 0.82, mine, "w")
                self._hits.append((x + 12 * s, ry - 11 * s, x + ow - 12 * s,
                                   ry + 11 * s, "library", index))
                ry += 22 * s
            self._text(x + 24 * s, y + oh - 24 * s,
                       "Click or Enter loads it  •  Esc closes", 9,
                       p["secondary_ink"], 0.75, False, "w")

    def _draw_verify_dialog(self, p: Dict[str, Any]) -> None:
        if not self._verify_prompt:
            return
        s = self._ui_scale
        w, h = self._w, self._h
        pointer_w, pointer_h = 760 * s, 300 * s
        x, y = (w - pointer_w) / 2.0, (h - pointer_h) / 2.0
        self.canvas.create_rectangle(0, 0, w, h, fill="#000000", stipple="gray50",
                                     outline="")
        self.canvas.create_rectangle(x, y, x + pointer_w, y + pointer_h,
                                     fill=_hex(_light(_darken(p["field"], 0.62), 0.03)),
                                     outline=_hex((235, 64, 72)), width=2)
        count = len(self._verify_bad)
        self._text(x + pointer_w / 2.0, y + 34 * s,
                   f"{count:,} FILE{'S' if count != 1 else ''} CANNOT BE PLAYED",
                   20, (255, 120, 120), 0.98, True, "center")
        self._text(x + pointer_w / 2.0, y + 74 * s,
                   "Remove them from the playlist so you cannot select them mid-set?",
                   12, p["glass_ink"], 0.88, False, "center")
        pad = 40 * s
        button_h = 50 * s
        button_y = y + pointer_h - pad - button_h
        button_w = (pointer_w - pad * 2 - 24 * s) / 2.0
        remove = (x + pad, button_y, button_w, button_h)
        keep = (x + pad + button_w + 24 * s, button_y, button_w, button_h)
        for box, label, colour in ((remove, "REMOVE FROM PLAYLIST", (235, 64, 72)),
                                   (keep, "KEEP THEM", (150, 160, 172))):
            self._panel(box, colour, 0.24, 10 * s)
            self._rounded_rect(box[0], box[1], box[2], box[3], 10 * s,
                               outline=_with(p["field"], colour, 0.92),
                               width=max(1.2, 1.5 * s))
            self._text_in_box(box, label, 12, p["glass_ink"], 0.97, True)
        self._hits.append((remove[0], remove[1], remove[0] + remove[2],
                           remove[1] + remove[3], "button", "verify-remove"))
        self._hits.append((keep[0], keep[1], keep[0] + keep[2],
                           keep[1] + keep[3], "button", "verify-keep"))
        self._text(x + pointer_w / 2.0, y + pointer_h - 18 * s,
                   "Y / ENTER  remove        N / ESC  keep", 9,
                   p["glass_ink"], 0.55, False, "center")

    # -- shutdown ---------------------------------------------------------
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._worker_stop.set()
        self._stop_visualizer()
        if self.midi is not None:
            self.midi.stop()
        if self.recorder is not None:
            self.recorder.stop()
            self.recorder = None
        for engine in self.decks.values():
            engine.stop()
        try:
            self.model.save()
        except Exception:
            pass
        self._save_windows_settings()
        if self.root is not None:
            try:
                self.root.destroy()
            except Exception:
                pass

    def _shutdown(self) -> None:
        if not self._closed:
            self.close()

    # -- self test --------------------------------------------------------
    def _self_test(self) -> int:
        """Headless checks the installer and CI can run without a display."""
        checks = {
            "channels": len(core.CHANNELS) == 4,
            "namespaces": all(core.DECK_NAMESPACES.get(ch) for ch in core.CHANNELS),
            "theme": bool(core.theme_label(self.theme)),
            "midi_profile": self.profile in ("A", "B"),
            "recorder": WindowsLoopbackRecorder().available(),
            "visualizer": (ROOT / "MP_Liquid_Visualizer.py").is_file(),
        }
        for name, ok in checks.items():
            print(f"{'OK' if ok else 'FAIL'} {name}")
        return 0 if all(checks.values()) else 1


def mixer_effects_slots() -> Tuple[str, ...]:
    try:
        import mixer_effects
        return tuple(mixer_effects.MODES) + (mixer_effects.FILTER_MODE,)
    except Exception:
        return ("FLANGER", "REVERB", "DELAY", "GATER", "FILTER")


def mixer_effects_slot_color(slot: str, accent, secondary, tertiary) -> Tuple[int, int, int]:
    try:
        import mixer_effects
        return _rgb(mixer_effects.MODE_COLORS.get(slot, accent))
    except Exception:
        return accent


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=WINDOW_TITLE)
    parser.add_argument("--windowed", action="store_true",
                        help="start in a 1440x810 window instead of full screen")
    parser.add_argument("--no-visualizer", action="store_true",
                        help="do not start the companion visualizer")
    parser.add_argument("--theme", default="", help="start on this theme id")
    parser.add_argument("--midi-profile", default="", choices=("", "A", "B"),
                        help="Kontrol S4 (A) or S3 (B) MIDI profile")
    parser.add_argument("--midi-device", default="",
                        help="MIDI input index or name (default: auto-detect Kontrol)")
    parser.add_argument("--music-mode", action="store_true",
                        help="decks open the Music Folders library")
    parser.add_argument("--mix-archive-mode", action="store_true",
                        help="decks open the Mix Archive library")
    parser.add_argument("--self-test", action="store_true",
                        help="run the headless checks and exit")
    args = parser.parse_args(argv)
    if args.self_test:
        app = LiquidMixerWindows(["--self-test"])
        return app.run()
    app = LiquidMixerWindows(list(sys.argv[1:]) if argv is None else list(argv))
    if args.windowed:
        app.fullscreen = False
    if args.no_visualizer:
        app.visualizer_enabled = False
    if args.theme:
        app.theme = args.theme
        core.write_theme(args.theme)
    if args.midi_profile:
        app.profile = mixer_midi.normalize_profile(args.midi_profile)
    if args.midi_device:
        app.midi_device = args.midi_device
    if args.music_mode:
        app.library_mode = "MUSIC"
    elif args.mix_archive_mode:
        app.library_mode = "MIX_ARCHIVE"
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
