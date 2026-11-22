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
DESIGN = (1600, 900)
WINDOWED = (1440, 810)
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
        self.theme = self._initial_theme()
        # Start from the documented defaults. A stale model saved by a
        # previous port had every fader at zero, which made the first Windows
        # launch look dead; persistence can be added after the port is proven.
        self.model = core.MixerModel()
        self.library_mode = str(self.windows.get("library", "") or "").upper()
        if self.library_mode not in ("MUSIC", "MIX_ARCHIVE"):
            self.library_mode = DeckEngine.default_library()
        self.profile = mixer_midi.normalize_profile(
            self.windows.get("midi_profile") or self.settings.midi_profile)
        self.alpha = _clamp(float(self.windows.get("alpha", 0.94) or 0.94), 0.5, 1.0)
        self.fullscreen = bool(self.windows.get("fullscreen", True))
        self.visualizer_enabled = bool(
            self.windows.get("visualizer", self.settings.visualizer_enabled))
        self.midi_device = str(self.windows.get("midi_device", "") or "")

        self.root: Optional[tk.Tk] = None
        self.canvas: Optional[tk.Canvas] = None
        self._w = DESIGN[0]
        self._h = DESIGN[1]
        self._hits: List[Tuple[float, float, float, float, str, Any]] = []
        self._drag: Optional[Tuple[str, Any, int, int]] = None
        # Pointer offset for dragging the borderless window by an empty part
        # of the surface. None while no window drag is in progress.
        self._window_drag: Optional[Tuple[int, int]] = None
        self._overlay = ""
        self._overlay_rows: List[Dict[str, Any]] = []
        self._overlay_deck = "A"
        self._status_line = "starting decks…"
        self._notice = ""
        self._notice_until = 0.0
        self._closed = False
        self._last_draw = 0.0

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

    # -- settings ---------------------------------------------------------
    @staticmethod
    def _load_windows_settings() -> Dict[str, Any]:
        try:
            return json.loads(WINDOWS_SETTINGS.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_windows_settings(self) -> None:
        payload = {
            "alpha": round(self.alpha, 3),
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
        name = "into_darkness"
        order = core.theme_cycle(name)
        if name not in order:
            name = core.read_theme()
        try:
            core.write_theme(name)
        except Exception:
            pass
        return name

    # -- lifecycle --------------------------------------------------------
    def run(self) -> int:
        if self.argv and "--self-test" in self.argv:
            return self._self_test()
        self.root = tk.Tk()
        self.root.title(WINDOW_TITLE)
        self.root.overrideredirect(True)
        # Without a companion there is nothing to show through; keep the
        # window fully solid so the desktop cannot bleed into the panels.
        self.root.attributes("-alpha", self.alpha if self.visualizer_enabled else 1.0)
        self.root.configure(bg="#05050b")
        self.canvas = tk.Canvas(self.root, bg="#05050b", highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.focus_set()
        self._place_window()
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
            self._w, self._h = screen_w, screen_h
        else:
            width, height = WINDOWED
            x = max(0, (screen_w - width) // 2)
            y = max(0, (screen_h - height) // 2)
            self.root.geometry(f"{width}x{height}+{x}+{y}")
            self._w, self._h = width, height

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
        self._w, self._h = max(640, event.width), max(480, event.height)

    def _on_key(self, event) -> None:
        key = (event.keysym or "").lower()
        char = (event.char or "").lower()
        if self._overlay == "settings":
            if char in ("1", "2", "3", "4", "5", "6", "7"):
                self._settings_action(char)
                return
            if key in ("escape", "s", "q"):
                self._overlay = ""
                self._draw()
                return
        if self._overlay == "library":
            if key in ("escape", "l", "q"):
                self._overlay = ""
                self._draw()
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
        elif char == "l" or key == "l":
            self._launch_app("liquid")
        elif char == "g" or key == "g":
            self._launch_app("gui")
        elif char == "m" or key == "m":
            self._launch_app("manager")
        elif key in ("space",):
            if self._overlay == "library":
                return
            self._toggle_deck("A")
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
            # Every second, make sure the fader levels match the model.
            if int(time.monotonic()) % 5 == 0:
                self._queue_all_levels()
            time.sleep(0.4)

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
        if self.root is not None:
            self.root.attributes("-alpha", self.alpha if self.visualizer_enabled else 1.0)
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
        if kind == "opacity":
            self._drag = ("opacity", data, 0.0, 0.0)
            self._drag_opacity(event.x, data)
            return
        if kind == "button":
            self._button_action(data)
            return
        if kind == "fader":
            channel, box = data
            self._drag = ("fader", channel, box[1], box[3])
            self._apply_fader(channel, box, event.y)
        elif kind == "crossfader":
            box = data
            self._drag = ("crossfader", None, box[0], box[2])
            self._apply_crossfader(box, event.x)
        elif kind == "knob":
            key, channel, box = data
            self._drag = ("knob", (key, channel, box, event.y), box[1], box[3])
        elif kind == "library":
            index = data
            self._load_library_row(index)

    def _on_drag(self, event) -> None:
        if self._window_drag is not None:
            dx, dy = self._window_drag
            self.root.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")
            self._publish_geometry()
            return
        if self._drag is None:
            return
        kind, data, low, high = self._drag
        if kind == "fader":
            channel, box = data
            self._apply_fader(channel, box, event.y)
        elif kind == "crossfader":
            self._apply_crossfader(data, event.x)
        elif kind == "knob":
            key, channel, box, start_y = data
            delta = (start_y - event.y) / 120.0
            self._adjust_knob(key, channel, delta)
            self._drag = ("knob", (key, channel, box, event.y), low, high)
        elif kind == "opacity":
            self._drag_opacity(event.x, data)
        self._draw()

    def _on_release(self, _event) -> None:
        was_opacity = bool(self._drag and self._drag[0] == "opacity")
        self._drag = None
        self._window_drag = None
        if was_opacity:
            self._save_windows_settings()

    def _on_double(self, event) -> None:
        kind, _data = self._hit_at(event.x, event.y)
        if kind == "opacity":
            return
        self._toggle_fullscreen()

    def _on_wheel(self, event) -> None:
        kind, data = self._hit_at(event.x, event.y)
        step = 1 if event.delta > 0 else -1
        if kind == "opacity":
            self.alpha = _clamp(self.alpha + step * 0.02, 0.5, 1.0)
            self._apply_alpha()
            self._save_windows_settings()
            self._draw()
        elif kind == "fader":
            channel, box = data
            self._adjust_fader(channel, step * 0.02)
        elif kind == "crossfader":
            self.model.set_crossfade(self.model.crossfade + step * 0.02)
            self._queue_all_levels()
        elif kind == "knob":
            key, channel, _box = data
            self._adjust_knob(key, channel, step * 0.02)
        else:
            return
        self._draw()

    # -- value application ------------------------------------------------
    def _apply_fader(self, channel: str, box, y: float) -> None:
        top, bottom = box[1], box[3]
        value = 1.0 - _clamp((y - top) / max(1.0, bottom - top), 0.0, 1.0)
        self.model.set_channel(channel, value)
        self._sync_level(channel)
        self._draw()

    def _adjust_fader(self, channel: str, delta: float) -> None:
        self.model.set_channel(channel, self.model.channel_gain(channel) + delta)
        self._sync_level(channel)

    def _apply_crossfader(self, box, x: float) -> None:
        left, right = box[0], box[2]
        value = _clamp((x - left) / max(1.0, right - left), 0.0, 1.0)
        self.model.set_crossfade(value)
        self._sync_level("A")
        self._sync_level("B")
        self._draw()

    def _adjust_knob(self, key: str, channel: str, delta: float) -> None:
        if key == "trim":
            self.model.set_trim(channel, self.model.trim(channel) + delta * 0.25)
            self._sync_level(channel)
        elif key in core.EQ_BANDS:
            self.model.set_eq(channel, key, self.model.eq_value(channel, key) + delta * 3.0)
            self._sync_eq(channel)
        elif key == "filter":
            self.model.set_filter(channel, self.model.filter_value(channel) + delta)
            self._sync_effect(channel)
        elif key == "master":
            self.model.set_master_volume(self.model.master_volume + delta)
            self._queue_all_levels()
        elif key == "monitor":
            self.model.set_monitor_volume(self.model.monitor_volume + delta)

    def _button_action(self, action: str) -> None:
        if action.startswith("deck:"):
            self._toggle_deck(action.split(":", 1)[1])
        elif action.startswith("switch:"):
            self._switch_side(action.split(":", 1)[1])
        elif action.startswith("cue:"):
            channel = action.split(":", 1)[1]
            self.model.toggle_headphone(channel)
        elif action.startswith("fx:"):
            channel = action.split(":", 1)[1]
            self.model.toggle_effect(channel)
            self._sync_effect(channel)
        elif action.startswith("slot:"):
            _tag, channel, slot = action.split(":")
            self.model.set_effect_mode(channel, slot)
            self._sync_effect(channel)
        elif action.startswith("filter-mode:"):
            channel = action.split(":", 1)[1]
            self.model.set_filter_mode(channel)
            self._sync_effect(channel)
        elif action == "theme":
            self._cycle_theme()
        elif action == "visualizer":
            self._toggle_visualizer()
        elif action == "record":
            self._toggle_recording()
        elif action == "settings":
            self._overlay = "settings"
        elif action == "library:A":
            self._open_library("A")
        elif action == "library:B":
            self._open_library("B")
        elif action == "mode":
            self.model.toggle_mode()
        elif action == "close":
            self.close()
        self._draw()

    # -- library ----------------------------------------------------------
    def _open_library(self, channel: str) -> None:
        status = self._deck_status.get(channel) or {}
        rows: List[Dict[str, Any]] = []
        try:
            reply = core.send_ipc(core.DECK_NAMESPACES[channel], "get_playlist",
                                  timeout=0.7) or {}
            data = reply.get("data") or {}
            rows = list(data.get("playlist") or [])
        except Exception:
            rows = []
        if not rows:
            try:
                rows = core.read_playlist_rows()[:80]
            except Exception:
                rows = []
        self._overlay = "library"
        self._overlay_deck = channel
        self._overlay_rows = rows[:40]
        self._draw()

    def _load_library_row(self, index: int) -> None:
        if not (0 <= index < len(self._overlay_rows)):
            return
        row = self._overlay_rows[index]
        channel = self._overlay_deck
        real_index = int(row.get("index", index))
        self._send(channel, "select_index", index=real_index)
        self._send(channel, "play")
        self._overlay = ""
        self._set_browse_target(channel)
        self._notice = f"{channel}: {_short(row.get('name') or row.get('path'), 45)}"
        self._notice_until = time.monotonic() + 2.0
        self._draw()

    # -- drawing ----------------------------------------------------------
    def _palette(self) -> Dict[str, Any]:
        accent, secondary, tertiary = core.theme_colors(self.theme)
        field = core.theme_field(self.theme)
        console = core.theme_console(self.theme)
        # The mixer panels use theme_console (a near-black pane), so the
        # glass foreground can be dark on light themes and unreadable. Use the
        # deck pane's own readable ink instead.
        ink = core.deck_pane_ink(self.theme)
        return {
            "accent": _rgb(accent, (176, 88, 255)),
            "secondary": _rgb(secondary, (0, 242, 254)),
            "tertiary": _rgb(tertiary, (255, 45, 142)),
            "field": _rgb(field, (196, 204, 221)),
            "console": _rgb(console, (10, 12, 20)),
            "ink": _rgb(ink, (236, 239, 244)),
        }

    def _tick(self) -> None:
        if self._closed or self.root is None:
            return
        self._drain_midi()
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
        w, h = self._w, self._h
        p = self._palette()
        accent, secondary, tertiary = p["accent"], p["secondary"], p["tertiary"]
        console, ink, field = p["console"], p["ink"], p["field"]
        bg = _hex(_rgb((8, 8, 16)))
        self.canvas.configure(bg=bg)
        self.canvas.create_rectangle(0, 0, w, h, fill=bg, outline="")
        # A soft top glow, so the window reads as glass rather than a flat form.
        self.canvas.create_rectangle(0, 0, w, 6, fill=_hex(accent), outline="")
        self._draw_top_bar(w, p)
        margin, top, bottom = 18, 62, 116
        deck_w = max(220, int((w - margin * 2) * 0.20))
        gap = 10
        left = margin
        right = w - margin - deck_w
        strip_left = left + deck_w + gap
        strip_right = right - gap
        strip_area = max(200, strip_right - strip_left)
        strip_w = strip_area / 4.0
        body_h = max(220, h - top - bottom - margin)
        self._draw_deck(left, top, deck_w, body_h, "A", p)
        self._draw_deck(right, top, deck_w, body_h, "B", p)
        for idx, channel in enumerate(core.CHANNELS):
            x = strip_left + idx * strip_w
            self._draw_strip(x, top, strip_w - 6, body_h, channel, p)
        self._draw_bottom(margin, h - bottom, w - margin * 2, bottom - margin, p)
        self._draw_overlay(w, h, p)

    def _draw_top_bar(self, w: int, p: Dict[str, Any]) -> None:
        accent, secondary, ink = p["accent"], p["secondary"], p["ink"]
        self.canvas.create_rectangle(0, 6, w, 58, fill=_tone(p["console"], 0.9, 2),
                                     outline=_hex(_tone(accent, 0.7)))
        self.canvas.create_text(18, 32, anchor="w",
                                text=f"MP LIQUID MIXER {VERSION}",
                                fill=_hex(accent), font=("Segoe UI", 16, "bold"))
        self.canvas.create_text(300, 32, anchor="w",
                                text=_short(core.theme_label(self.theme), 32),
                                fill=_hex(ink), font=("Segoe UI", 10, "bold"))
        midi = self.midi
        midi_text = (midi.status if midi is not None else "MIDI not started")
        midi_color = secondary if midi is not None and midi._running else _rgb((255, 190, 80))
        self.canvas.create_text(w * 0.56, 25, anchor="w", text=midi_text[:48],
                                fill=_hex(midi_color), font=("Segoe UI", 9, "bold"))
        self.canvas.create_text(w * 0.56, 43, anchor="w",
                                text=f"MODE {self.model.mode}  •  {self.library_mode}",
                                fill=_hex(ink), font=("Segoe UI", 9))
        clock = time.strftime("%a %d %b  %H:%M:%S")
        self.canvas.create_text(w - 210, 25, anchor="e", text=clock,
                                fill=_hex(ink), font=("Consolas", 10, "bold"))
        rec = "REC ON" if self.recorder is not None else "REC"
        self._button(w - 210, 8, 78, 22, rec, "record",
                     _rgb((255, 70, 90)) if self.recorder is not None else _rgb((60, 60, 76)),
                     ink)
        self._button(w - 126, 8, 58, 22, "THEME", "theme", _rgb((70, 55, 100)), ink)
        self._button(w - 62, 8, 44, 22, "X", "close", _rgb((96, 28, 34)), ink)

    def _draw_deck(self, x: int, y: int, w: int, h: int, side: str,
                   p: Dict[str, Any]) -> None:
        accent, secondary, ink = p["accent"], p["secondary"], p["ink"]
        channel = self.model.side_decks.get(side, side)
        status = self._deck_status.get(channel) or {}
        self.canvas.create_rectangle(x, y, x + w, y + h,
                                     fill=_tone(p["console"], 0.62, 4),
                                     outline=_hex(_tone(accent, 0.8)), width=2)
        self.canvas.create_text(x + 12, y + 18, anchor="w",
                                text=f"DECK {channel}", fill=_hex(accent),
                                font=("Segoe UI", 16, "bold"))
        other = self.model.other_deck_for_side(side)
        self._button(x + w - 108, y + 8, 98, 22, f"SWITCH {other}", f"switch:{side}",
                     _tone(accent, 0.45), ink, 8)
        state = str(status.get("state") or "offline")
        title = _short(status.get("title") or status.get("path") or
                       "No track loaded", 30)
        self.canvas.create_text(x + 12, y + 52, anchor="w", text=state.upper(),
                                fill=_hex(secondary), font=("Consolas", 10, "bold"))
        self.canvas.create_text(x + 12, y + 82, anchor="w", text=title,
                                fill=_hex(ink), font=("Segoe UI", 11, "bold"),
                                width=max(80, w - 24))
        pos = _fmt_time(status.get("position_sec"))
        dur = _fmt_time(status.get("duration_sec"))
        bpm = status.get("bpm") or status.get("master_bpm") or ""
        self.canvas.create_text(x + 12, y + 116, anchor="w",
                                text=f"{pos} / {dur}", fill=_hex(ink),
                                font=("Consolas", 11, "bold"))
        self.canvas.create_text(x + 12, y + 140, anchor="w",
                                text=f"PITCH {self.model.pitch(channel):+.1f}%  {bpm}",
                                fill=_hex(secondary), font=("Consolas", 9))
        by = y + h - 80
        self._button(x + 10, by, (w - 30) / 2, 28, "PLAY", f"deck:{channel}",
                     _tone(accent, 0.55), ink)
        self._button(x + 18 + (w - 30) / 2, by, (w - 30) / 2, 28, "CUE",
                     f"cue:{channel}", _tone(secondary, 0.35), ink)
        self._button(x + 10, by + 36, w - 20, 26, f"LIBRARY {channel}",
                     f"library:{side}", _tone(p["console"], 0.9, 8), ink, 9)
        peaks = status.get("peaks") or []
        level = max([float(v) for v in peaks if isinstance(v, (int, float))] or [0.0])
        self._meter(x + 12, y + 168, 14, max(30, h - 260), _clamp(level, 0.0, 1.0),
                    secondary, accent, p["console"])

    def _draw_strip(self, x: int, y: int, w: int, h: int, channel: str,
                    p: Dict[str, Any]) -> None:
        accent, secondary, tertiary, ink = p["accent"], p["secondary"], p["tertiary"], p["ink"]
        self.canvas.create_rectangle(x, y, x + w, y + h,
                                     fill=_tone(p["console"], 0.58, 3),
                                     outline=_hex(_tone(accent, 0.55)))
        self.canvas.create_text(x + 10, y + 16, anchor="w", text=f"CH {channel}",
                                fill=_hex(accent), font=("Segoe UI", 12, "bold"))
        knob_r = 15
        kx1, kx2 = x + w * 0.28, x + w * 0.72
        ky = y + 48
        self._knob(kx1, ky, knob_r, "TRIM", self.model.trim(channel) / max(1e-6, core.TRIM_MAX),
                   "trim", channel, p)
        self._knob(kx2, ky, knob_r, "HI", (self.model.eq_value(channel, "high") + 12) / 24,
                   "high", channel, p)
        self._knob(kx1, ky + 74, knob_r, "MID", (self.model.eq_value(channel, "mid") + 12) / 24,
                   "mid", channel, p)
        self._knob(kx2, ky + 74, knob_r, "LOW", (self.model.eq_value(channel, "low") + 12) / 24,
                   "low", channel, p)
        fx_y = ky + 138
        slots = list(mixer_effects_slots())
        sx = x + 8
        sw = max(24, (w - 16) / len(slots))
        for slot in slots:
            label = "FILT" if slot == "FILTER" else slot[:3]
            color = mixer_effects_slot_color(slot, accent, secondary, tertiary)
            active = (self.model.filter_mode(channel) if slot == "FILTER"
                      else self.model.effect_mode(channel) == slot)
            self._button(sx, fx_y, sw - 3, 20, label,
                         f"filter-mode:{channel}" if slot == "FILTER"
                         else f"slot:{channel}:{slot}",
                         color if active else _tone(color, 0.28), ink, 7)
            sx += sw
        self._knob(x + w / 2, fx_y + 52, 20, "FILTER", self.model.filter_value(channel),
                   "filter", channel, p)
        self._button(x + 8, fx_y + 88, (w - 20) / 2, 24, "FX",
                     f"fx:{channel}", _tone(tertiary, 0.5) if self.model.effect_on(channel)
                     else _tone(tertiary, 0.3), ink, 8)
        self._button(x + 14 + (w - 20) / 2, fx_y + 88, (w - 20) / 2, 24, "CUE",
                     f"cue:{channel}", _tone(secondary, 0.4)
                     if self.model.headphone(channel) else _tone(secondary, 0.25), ink, 8)
        fader_top = fx_y + 128
        fader_bottom = y + h - 22
        track_x = x + w * 0.38
        self.canvas.create_line(track_x, fader_top, track_x, fader_bottom,
                                fill=_hex(_tone(accent, 0.55, 18)), width=3)
        value = self.model.channel_gain(channel)
        handle_y = fader_bottom - value * (fader_bottom - fader_top)
        self.canvas.create_rectangle(track_x - 15, handle_y - 6, track_x + 15,
                                     handle_y + 6, fill=_hex(accent), outline="")
        self.canvas.create_text(track_x, handle_y - 16, text=f"{int(round(value*100))}",
                                fill=_hex(ink), font=("Consolas", 8, "bold"))
        self._hits.append((x, fader_top, x + w * 0.7, fader_bottom, "fader",
                           (channel, (x, fader_top, x + w * 0.7, fader_bottom))))
        status = self._deck_status.get(channel) or {}
        peaks = status.get("peaks") or []
        level = max([float(v) for v in peaks if isinstance(v, (int, float))] or [0.0])
        self._meter(track_x + 24, fader_top, 12, fader_bottom - fader_top,
                    _clamp(level, 0.0, 1.0), secondary, tertiary, p["console"])

    def _draw_bottom(self, x: int, y: int, w: int, h: int, p: Dict[str, Any]) -> None:
        accent, secondary, ink = p["accent"], p["secondary"], p["ink"]
        self.canvas.create_rectangle(x, y, x + w, y + h,
                                     fill=_tone(p["console"], 0.8, 2),
                                     outline=_hex(_tone(accent, 0.5)))
        cx0, cx1 = x + 220, x + w - 460
        cy = y + 30
        self.canvas.create_text(x + 16, y + 18, anchor="w", text="CROSSFADER",
                                fill=_hex(accent), font=("Segoe UI", 9, "bold"))
        self.canvas.create_rectangle(cx0, cy - 3, cx1, cy + 3,
                                     fill=_tone(p["console"], 0.9, 6), outline="")
        xf = self.model.crossfade
        hx = cx0 + xf * (cx1 - cx0)
        self.canvas.create_rectangle(hx - 7, cy - 12, hx + 7, cy + 12,
                                     fill=_hex(accent), outline="")
        self.canvas.create_text(cx0, cy + 24, text="A", fill=_hex(ink),
                                font=("Consolas", 9, "bold"))
        self.canvas.create_text(cx1, cy + 24, text="B", fill=_hex(ink),
                                font=("Consolas", 9, "bold"))
        self._hits.append((cx0 - 10, cy - 16, cx1 + 10, cy + 16, "crossfader",
                           (cx0, cy - 16, cx1, cy + 16)))
        # Master trim knob at the left of the ribbon.
        self._knob(x + 170, y + 30, 14, "MASTER",
                   self.model.master_volume, "master", "", p)
        self._knob(x + 210, y + 30, 14, "MON",
                   self.model.monitor_volume, "monitor", "", p)
        self.canvas.create_text(x + 16, y + 74, anchor="w",
                                text=f"MASTER {int(round(self.model.master_volume*100))}%   "
                                     f"MON {int(round(self.model.monitor_volume*100))}%",
                                fill=_hex(ink), font=("Consolas", 9, "bold"))
        bx = x + w - 486
        for label, action in (("MIXER", "mode"), ("VISUAL", "visualizer"),
                              ("SETTINGS", "settings")):
            self._button(bx, y + 12, 86, 26, label, action,
                         _tone(accent, 0.4), ink, 8)
            bx += 94
        self._button(bx, y + 12, 86, 26, "LIB A", "library:A",
                     _tone(secondary, 0.35), ink, 8)
        bx += 94
        self._button(bx, y + 12, 86, 26, "LIB B", "library:B",
                     _tone(secondary, 0.35), ink, 8)
        self._draw_opacity_bar(x + w - 430, y + 62, 410, p)
        notice = self._notice if time.monotonic() < self._notice_until else self._status_line
        self.canvas.create_text(x + 16, y + h - 12, anchor="w",
                                text=_short(notice, 110), fill=_hex(secondary),
                                font=("Consolas", 9, "bold"))
        self.canvas.create_text(x + w - 16, y + h - 12, anchor="e",
                                text="C theme  V visual  R rec  S settings  F11 full",
                                fill=_hex(ink), font=("Consolas", 8))

    def _draw_opacity_bar(self, x: float, y: float, width: float,
                          p: Dict[str, Any]) -> None:
        """The window-transparency track, visible on the bottom ribbon."""
        accent, secondary, ink = p["accent"], p["secondary"], p["ink"]
        self.canvas.create_text(x, y, anchor="w", text="TRANSPARENCY",
                                fill=_hex(secondary), font=("Consolas", 7, "bold"))
        x0, x1 = x + 92, x + width - 42
        if x1 <= x0:
            return
        frac = _clamp((self.alpha - 0.5) / 0.5, 0.0, 1.0)
        knob = x0 + (x1 - x0) * frac
        self.canvas.create_line(x0, y, x1, y,
                                fill=_hex(_tone(accent, 0.32)), width=4,
                                capstyle="round")
        self.canvas.create_line(x0, y, knob, y, fill=_hex(accent), width=4,
                                capstyle="round")
        self.canvas.create_oval(knob - 6, y - 6, knob + 6, y + 6,
                                fill=_hex(ink), outline=_hex(accent), width=2)
        self.canvas.create_text(x + width, y, anchor="e",
                                text=f"{int(round(self.alpha * 100))}%",
                                fill=_hex(ink), font=("Consolas", 8, "bold"))
        self._hits.append((x0 - 8, y - 10, x1 + 8, y + 10,
                           "opacity", (x0, x1)))

    def _apply_alpha(self) -> None:
        if self.root is None:
            return
        try:
            self.root.attributes(
                "-alpha", self.alpha if self.visualizer_enabled else 1.0)
        except Exception:
            pass

    def _drag_opacity(self, x: float, track) -> None:
        x0, x1 = track
        frac = _clamp((x - x0) / max(1.0, x1 - x0), 0.0, 1.0)
        self.alpha = 0.5 + 0.5 * frac
        self._apply_alpha()
        self._draw()

    def _draw_overlay(self, w: int, h: int, p: Dict[str, Any]) -> None:
        if not self._overlay:
            return
        accent, secondary, ink = p["accent"], p["secondary"], p["ink"]
        ow, oh = min(720, w - 80), min(520, h - 80)
        x, y = (w - ow) / 2, (h - oh) / 2
        self.canvas.create_rectangle(0, 0, w, h, fill="#000000", stipple="gray50",
                                     outline="")
        self.canvas.create_rectangle(x, y, x + ow, y + oh,
                                     fill=_tone(p["console"], 0.92, 6),
                                     outline=_hex(accent), width=2)
        if self._overlay == "settings":
            self.canvas.create_text(x + 20, y + 24, anchor="w", text="MIXER SETTINGS",
                                    fill=_hex(accent), font=("Segoe UI", 16, "bold"))
            rows = [
                ("1", f"Theme: {core.theme_label(self.theme)}"),
                ("2", f"Visualizer: {'On' if self.visualizer_enabled else 'Off'}"),
                ("3", f"Library: {self.library_mode}"),
                ("4", f"MIDI profile: {mixer_midi.PROFILE_LABELS.get(self.profile, self.profile)}"),
                ("5", f"Recording: {self.settings.recording_format}"),
                ("6", f"Fullscreen: {'On' if self.fullscreen else 'Off'}"),
                ("7", "Close"),
            ]
            ry = y + 66
            for key, text in rows:
                self.canvas.create_text(x + 24, ry, anchor="w", text=f"[{key}] {text}",
                                        fill=_hex(ink), font=("Segoe UI", 12))
                ry += 42
            self.canvas.create_text(x + 20, y + oh - 26, anchor="w",
                                    text=f"Alpha {int(round(self.alpha*100))}%  •  "
                                         f"MIDI device: {self.midi.device_name or 'none'}",
                                    fill=_hex(secondary), font=("Consolas", 9))
        elif self._overlay == "library":
            self.canvas.create_text(x + 20, y + 24, anchor="w",
                                    text=f"LIBRARY — DECK {self._overlay_deck}",
                                    fill=_hex(accent), font=("Segoe UI", 16, "bold"))
            ry = y + 58
            for index, row in enumerate(self._overlay_rows[:18]):
                name = _short(row.get("name") or row.get("title") or
                              Path(str(row.get("path") or "")).name, 62)
                self.canvas.create_text(x + 24, ry, anchor="w",
                                        text=f"{index + 1:02d}  {name}",
                                        fill=_hex(ink), font=("Consolas", 10))
                self._hits.append((x + 12, ry - 10, x + ow - 12, ry + 10,
                                   "library", index))
                ry += 22
            self.canvas.create_text(x + 20, y + oh - 24, anchor="w",
                                    text="Click a track to load it  •  L/Esc closes",
                                    fill=_hex(secondary), font=("Consolas", 9))

    # -- drawing primitives ----------------------------------------------
    def _button(self, x: float, y: float, w: float, h: float, label: str,
                action: str, fill, ink, size: int = 9) -> None:
        self.canvas.create_rectangle(x, y, x + w, y + h, fill=_hex(fill),
                                     outline=_hex(_tone(fill, 1.2, 18)), width=1)
        self.canvas.create_text(x + w / 2, y + h / 2, text=label,
                                fill=_hex(ink), font=("Segoe UI", size, "bold"))
        self._hits.append((x, y, x + w, y + h, "button", action))

    def _knob(self, cx: float, cy: float, radius: float, label: str,
              value: float, key: str, channel: str, p: Dict[str, Any]) -> None:
        accent, ink = p["accent"], p["ink"]
        value = _clamp(float(value or 0.0), 0.0, 1.0)
        x0, y0 = cx - radius, cy - radius
        x1, y1 = cx + radius, cy + radius
        self.canvas.create_oval(x0, y0, x1, y1,
                                fill=_tone(p["console"], 0.95, 8),
                                outline=_hex(_tone(accent, 0.7)), width=2)
        angle = math.radians(-135 + 270 * value)
        self.canvas.create_line(cx, cy, cx + math.cos(angle) * (radius - 4),
                                cy + math.sin(angle) * (radius - 4),
                                fill=_hex(accent), width=2)
        self.canvas.create_text(cx, y1 + 11, text=label, fill=_hex(ink),
                                font=("Segoe UI", 7, "bold"))
        self._hits.append((x0, y0, x1, y1, "knob", (key, channel,
                                                     (x0, y0, x1, y1))))

    def _meter(self, x: float, y: float, w: float, h: float, value: float,
               low, high, background) -> None:
        self.canvas.create_rectangle(x, y, x + w, y + h,
                                     fill=_tone(background, 0.9, 6), outline="")
        fill_h = value * h
        color = low if value < 0.72 else high
        self.canvas.create_rectangle(x, y + h - fill_h, x + w, y + h,
                                     fill=_hex(color), outline="")

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
