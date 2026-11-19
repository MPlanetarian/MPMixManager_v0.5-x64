#!/usr/bin/env python3
"""MP Liquid Mixer - model, IPC and layout (no GTK imports).

Everything that can be tested without a display lives here: the four-channel
level model and its crossfader curve, the per-deck IPC client, the shared theme
cycle, the design-grid layout, and the persisted mixer state.

Version 0.0.1
"""
from __future__ import annotations

import json
import math
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

MIXER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MIXER_DIR.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

CONFIG_DIR = Path(os.environ.get("MP_LIQUID_MIXER_CONFIG_DIR", "") or (Path.home() / ".config" / "mix-manager"))
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

STATE_PATH = CONFIG_DIR / "liquid_mixer.json"
LOCK_PATH = CONFIG_DIR / "liquid_mixer.lock"
VISUALIZER_PID_PATH = CONFIG_DIR / "liquid_mixer_visualizer.pid"
THEME_PATH = CONFIG_DIR / "theme"

VERSION = "0.0.1"

# The two decks run real MP Audio Player engines. Each namespace gets its own
# socket/pid/state (MP_Audio_Player._player_file), so deck A and deck B never
# retire or silence each other while sharing the library and favorites.
DECK_NAMESPACES = {"A": "mixerdeck_a", "B": "mixerdeck_b"}
CHANNELS = ("A", "B", "C", "D")
ACTIVE_CHANNELS = ("A", "B")
MIX_MODES = ("INTERNAL", "EXTERNAL")
DEFAULT_GAIN = 0.85
DEFAULT_CROSSFADE = 0.5
DEFAULT_TRIM = 1.0
TRIM_MAX = 1.5
EQ_MIN, EQ_MAX = -12.0, 12.0
EQ_BANDS = ("low", "mid", "high")
EQ_GROUPS = {"low": (0, 1, 2), "mid": (3, 4, 5, 6), "high": (7, 8, 9)}
PITCH_MIN, PITCH_MAX = -20.0, 20.0

try:  # The player's own slider constants, so both surfaces feel identical.
    from liquid_glass_slider import DEFAULT_OPACITY, MAX_OPACITY, MIN_OPACITY
except Exception:  # pragma: no cover - the script ships beside the player
    DEFAULT_OPACITY, MIN_OPACITY, MAX_OPACITY = 0.70, 0.20, 0.80

DESIGN_W, DESIGN_H = 1920, 1080
# Windowed mode is the design grid itself, the size the mixer is composed for.
WINDOWED_W, WINDOWED_H = 1920, 1080


def windowed_size(monitor_w: int = 2784, monitor_h: int = 1740) -> Tuple[int, int]:
    """The 1920x1080 window, clamped to fit a smaller desktop."""
    monitor_w, monitor_h = max(640, int(monitor_w)), max(360, int(monitor_h))
    width = min(WINDOWED_W, monitor_w - 48)
    height = int(round(width * 9.0 / 16.0))
    if height > monitor_h - 48:
        height = monitor_h - 48
        width = int(round(height * 16.0 / 9.0))
    return max(640, width), max(360, height)


def clamp01(value: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def equal_power(t: float) -> Tuple[float, float]:
    """Crossfader gains for A and B. 0 = full A, 0.5 = both at -3 dB, 1 = full B."""
    theta = clamp01(t) * (math.pi / 2.0)
    gain_a, gain_b = math.cos(theta), math.sin(theta)
    # cos(pi/2) and sin(0) are 6.1e-17, not 0. Snap the ends so a hard cut is
    # a real mute instead of a -320 dB whisper.
    return (0.0 if gain_a < 1e-12 else gain_a, 0.0 if gain_b < 1e-12 else gain_b)


def _player_file(namespace: str, name: str) -> Path:
    """Mirror MP_Audio_Player._player_file for a deck namespace."""
    if not namespace:
        return CONFIG_DIR / name
    stem, dot, ext = name.partition(".")
    return CONFIG_DIR / f"{stem}-{namespace}{dot}{ext}"


def player_socket(namespace: str) -> Path:
    return _player_file(namespace, "mp_audio_player.sock")


def player_port(namespace: str) -> Path:
    return _player_file(namespace, "mp_audio_player.port")


def player_state(namespace: str) -> Path:
    return _player_file(namespace, "mp_player_state.json")


def player_pid(namespace: str) -> Path:
    return _player_file(namespace, "mp_player.pid")


def send_ipc(namespace: str, cmd: str, timeout: float = 1.2, **kwargs: Any) -> Optional[Dict[str, Any]]:
    """Send one JSON command to a deck's engine; None when it is not up yet."""
    sock_path, port_path = player_socket(namespace), player_port(namespace)
    use_unix = hasattr(socket, "AF_UNIX") and sock_path.exists()
    if not use_unix and not port_path.exists():
        return None
    sock = None
    try:
        sock = socket.socket(socket.AF_UNIX if use_unix else socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        target = str(sock_path) if use_unix else ("127.0.0.1", int(port_path.read_text().strip()))
        sock.connect(target)
        sock.sendall(json.dumps({"cmd": cmd, **kwargs}).encode("utf-8"))
        raw = sock.recv(65536).decode("utf-8")
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    return None


def deck_online(namespace: str) -> bool:
    return send_ipc(namespace, "status", timeout=0.35) is not None


def deck_status(namespace: str) -> Dict[str, Any]:
    """Live engine status, or the namespaced state file when IPC is down."""
    res = send_ipc(namespace, "status", timeout=0.5)
    if res and isinstance(res.get("data"), dict):
        return res["data"]
    try:
        return json.loads(player_state(namespace).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}


class MixerModel:
    """Four channels, one crossfader, two live decks.

    A and B own the two MP Liquid Player engines. C and D are wired into the
    surface (their strips and state exist) but are intentionally inactive:
    they are not sent anywhere yet, which is the documented v0.0.1 limit.
    """

    def __init__(self, gains: Optional[Dict[str, float]] = None,
                 crossfade: float = DEFAULT_CROSSFADE,
                 mode: str = "INTERNAL",
                 opacity: float = DEFAULT_OPACITY,
                 trims: Optional[Dict[str, float]] = None,
                 eq: Optional[Dict[str, Dict[str, float]]] = None) -> None:
        self.gains: Dict[str, float] = {ch: DEFAULT_GAIN for ch in CHANNELS}
        if gains:
            for ch, value in gains.items():
                if ch in self.gains:
                    self.gains[ch] = clamp01(value)
        self.trims: Dict[str, float] = {ch: DEFAULT_TRIM for ch in CHANNELS}
        if trims:
            for ch, value in trims.items():
                if ch in self.trims:
                    self.trims[ch] = max(0.0, min(TRIM_MAX, float(value)))
        self.eq: Dict[str, Dict[str, float]] = {ch: {band: 0.0 for band in EQ_BANDS} for ch in CHANNELS}
        if isinstance(eq, dict):
            for ch, bands in eq.items():
                if ch not in self.eq or not isinstance(bands, dict):
                    continue
                for band in EQ_BANDS:
                    try:
                        self.eq[ch][band] = max(EQ_MIN, min(EQ_MAX, float(bands.get(band, 0.0))))
                    except (TypeError, ValueError):
                        pass
        self.pitches: Dict[str, float] = {ch: 0.0 for ch in CHANNELS}
        self.crossfade = clamp01(crossfade)
        self.mode = mode.upper() if str(mode).upper() in MIX_MODES else "INTERNAL"
        self.opacity = min(MAX_OPACITY, max(MIN_OPACITY, float(opacity)))

    # -- channels ---------------------------------------------------------
    def channel_active(self, channel: str) -> bool:
        return channel in ACTIVE_CHANNELS

    def channel_gain(self, channel: str) -> float:
        return self.gains.get(channel, DEFAULT_GAIN)

    def set_channel(self, channel: str, value: float) -> bool:
        """Set one channel fader. Inactive channels keep their stored value."""
        if channel not in self.gains or not self.channel_active(channel):
            return False
        self.gains[channel] = clamp01(value)
        return True

    # -- pitch ------------------------------------------------------------
    def pitch(self, channel: str) -> float:
        return self.pitches.get(channel, 0.0)

    def set_pitch(self, channel: str, value: float) -> bool:
        if channel not in self.pitches:
            return False
        try:
            self.pitches[channel] = max(PITCH_MIN, min(PITCH_MAX, float(value)))
        except (TypeError, ValueError):
            return False
        return True

    # -- trim / EQ --------------------------------------------------------
    def trim(self, channel: str) -> float:
        return self.trims.get(channel, DEFAULT_TRIM)

    def set_trim(self, channel: str, value: float) -> bool:
        if channel not in self.trims:
            return False
        try:
            self.trims[channel] = max(0.0, min(TRIM_MAX, float(value)))
        except (TypeError, ValueError):
            return False
        return True

    def eq_value(self, channel: str, band: str) -> float:
        return self.eq.get(channel, {}).get(band, 0.0)

    def set_eq(self, channel: str, band: str, value: float) -> bool:
        if channel not in self.eq or band not in EQ_BANDS:
            return False
        try:
            self.eq[channel][band] = max(EQ_MIN, min(EQ_MAX, float(value)))
        except (TypeError, ValueError):
            return False
        return True

    def eq_bands(self, channel: str) -> list:
        """Ten engine EQ gains for the three mixer knobs."""
        bands = [0.0] * 10
        values = self.eq.get(channel, {})
        for band, indexes in EQ_GROUPS.items():
            gain = float(values.get(band, 0.0))
            for index in indexes:
                bands[index] = gain
        return bands

    # -- crossfader -------------------------------------------------------
    def set_crossfade(self, value: float) -> None:
        self.crossfade = clamp01(value)

    def crossfade_gains(self) -> Tuple[float, float]:
        return equal_power(self.crossfade)

    # -- mixing mode ------------------------------------------------------
    @property
    def external(self) -> bool:
        return self.mode == "EXTERNAL"

    def toggle_mode(self) -> str:
        self.mode = "EXTERNAL" if not self.external else "INTERNAL"
        return self.mode

    # -- what the decks should hear --------------------------------------
    def deck_volumes(self) -> Tuple[int, int]:
        """(A, B) volume percentages for the two engines.

        INTERNAL applies channel gain x equal-power crossfade. EXTERNAL hands
        both decks to the house mixer at unity, so the software faders and the
        crossfader are bypassed (and drawn dimmed) instead of fighting it.
        """
        if self.external:
            return 100, 100
        gain_a, gain_b = self.crossfade_gains()
        vol_a = int(round(100.0 * self.gains["A"] * self.trims["A"] * gain_a))
        vol_b = int(round(100.0 * self.gains["B"] * self.trims["B"] * gain_b))
        return max(0, min(100, vol_a)), max(0, min(100, vol_b))

    def volume_for(self, channel: str) -> int:
        vol_a, vol_b = self.deck_volumes()
        return vol_a if channel == "A" else vol_b

    # -- persistence ------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": VERSION,
            "gains": dict(self.gains),
            "trims": dict(self.trims),
            "pitches": dict(self.pitches),
            "eq": {ch: dict(bands) for ch, bands in self.eq.items()},
            "crossfade": self.crossfade,
            "mode": self.mode,
            "opacity": self.opacity,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MixerModel":
        if not isinstance(data, dict):
            return cls()
        model = cls(
            gains=data.get("gains") if isinstance(data.get("gains"), dict) else None,
            crossfade=data.get("crossfade", DEFAULT_CROSSFADE),
            mode=data.get("mode", "INTERNAL"),
            opacity=data.get("opacity", DEFAULT_OPACITY),
            trims=data.get("trims") if isinstance(data.get("trims"), dict) else None,
            eq=data.get("eq") if isinstance(data.get("eq"), dict) else None,
        )
        pitches = data.get("pitches") if isinstance(data.get("pitches"), dict) else {}
        for ch, value in pitches.items():
            model.set_pitch(ch, value)
        return model

    def save(self, path: Optional[Path] = None) -> None:
        target = Path(path or STATE_PATH)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
            os.replace(tmp, target)
        except OSError:
            pass

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "MixerModel":
        try:
            return cls.from_dict(json.loads(Path(path or STATE_PATH).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, UnicodeError):
            return cls()


# -- music folder mounts -------------------------------------------------
# The decks browse the Music Folders library, which is generated from SMB
# shares and points at ~/MUSIC_SMB/<id>/... paths. Those shares are rclone
# FUSE mounts that nothing starts on a normal desktop login, so the playlist
# is full of paths whose parent directory exists but is empty. Selecting a row
# then fails `os.path.isfile()` inside the engine and the deck simply does
# nothing. The Music Folders menu and the Web Companion each mount the shares
# themselves; the mixer, which opens the library directly, has to do it too.
MUSIC_MOUNT_RETRY_SECONDS = 30.0
_MUSIC_MOUNT_STATE: Dict[str, Any] = {"attempted_at": 0.0, "online": 0, "total": 0, "detail": ""}


def _music_folders_module():
    """The Music Folders helper, imported the way the player imports siblings."""
    try:
        import music_folders_manager as module
        return module
    except ImportError:
        pass
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        import music_folders_manager as module
        return module
    except ImportError:
        return None


def ensure_music_folders_mounted(force: bool = False,
                                 deadline_sec: float = 20.0) -> Dict[str, Any]:
    """Mount the configured SMB music shares and report what is reachable.

    Returns ``{"online": n, "total": n, "detail": "..."}``. A missing
    configuration is reported as zero folders, never as an error: a deck on a
    machine with local-only music folders has nothing to mount and must still
    start. Results are cached briefly so the mixer's status poll and both
    decks do not each spawn a fleet of rclone processes.
    """
    now = time.monotonic()
    state = _MUSIC_MOUNT_STATE
    if not force and state["attempted_at"] and now - state["attempted_at"] < MUSIC_MOUNT_RETRY_SECONDS:
        return dict(state)

    module = _music_folders_module()
    if module is None:
        state.update(attempted_at=now, online=0, total=0,
                     detail="music_folders_manager.py was not found")
        return dict(state)

    # A desktop launch has ~/.local/bin on PATH, but a service or a stripped
    # environment may not; rclone lives there on Linux.
    local_bin = str(Path.home() / ".local" / "bin")
    if Path(local_bin).is_dir() and local_bin not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = local_bin + os.pathsep + os.environ.get("PATH", "")

    try:
        config = module.load_music_config()
        folders = [f for f in module.load_folders(config)
                   if str(f.get("type", "")).lower() == "smb" and f.get("id")]
    except Exception as exc:
        state.update(attempted_at=now, online=0, total=0,
                     detail=f"could not read the Music Folders configuration: {exc}")
        return dict(state)

    if not folders:
        state.update(attempted_at=now, online=0, total=0,
                     detail="no SMB Music Folders configured")
        return dict(state)

    # Mount in parallel: six serial rclone mounts never finish inside a launch
    # window, and one offline share must not hold the other five.
    results: Dict[str, bool] = {}
    lock = threading.Lock()

    def _one(folder: Dict[str, Any]) -> None:
        try:
            ok, _msg = module.connect_folder(folder, config, interactive=False)
        except Exception:
            ok = False
        with lock:
            results[str(folder.get("id"))] = bool(ok)

    threads = []
    for folder in folders:
        thread = threading.Thread(target=_one, args=(folder,),
                                  name=f"music-mount-{folder.get('id')}", daemon=True)
        thread.start()
        threads.append(thread)
    deadline = time.monotonic() + max(1.0, float(deadline_sec))
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))

    online = sum(1 for ok in results.values() if ok)
    offline = [fid for fid in (str(f.get("id")) for f in folders)
               if not results.get(fid)]
    detail = f"{online}/{len(folders)} music shares mounted"
    if offline:
        detail += " (offline: " + ", ".join(offline) + ")"
    state.update(attempted_at=time.monotonic(), online=online,
                 total=len(folders), detail=detail)
    return dict(state)


def library_available(path: Optional[str]) -> bool:
    """True when a playlist row's file can actually be opened right now."""
    if not path:
        return False
    try:
        return os.path.isfile(str(path))
    except OSError:
        return False


# -- theme ---------------------------------------------------------------
def read_theme() -> str:
    try:
        name = THEME_PATH.read_text(encoding="utf-8").strip().lower()
    except OSError:
        name = ""
    try:
        from mp_theme_palette import normalize_theme_name
        return normalize_theme_name(name)
    except Exception:
        return name or "dreamworlds_ultra"


def write_theme(name: str) -> None:
    try:
        THEME_PATH.parent.mkdir(parents=True, exist_ok=True)
        THEME_PATH.write_text(str(name).strip().lower() + "\n", encoding="utf-8")
    except OSError:
        pass


def theme_cycle(current: Optional[str] = None) -> list:
    try:
        from mp_theme_palette import cycle_order
        order = list(cycle_order())
    except Exception:
        order = ["dreamworlds_ultra", "stream_of_frequency", "sensorium", "cosmic_supernova"]
    return order or [current or "dreamworlds_ultra"]


def next_theme(current: Optional[str] = None) -> str:
    order = theme_cycle(current)
    current = current or read_theme()
    try:
        index = order.index(current)
    except ValueError:
        index = -1
    return order[(index + 1) % len(order)]


def theme_colors(name: str) -> Tuple[Tuple[int, int, int], ...]:
    try:
        from mp_theme_palette import theme_colors as colors
        return colors(name)
    except Exception:
        return ((166, 77, 255), (247, 37, 133), (0, 229, 255))


def theme_field(name: str) -> Tuple[int, int, int]:
    try:
        from mp_theme_palette import glass_field_rgb
        return glass_field_rgb(name)
    except Exception:
        return (196, 204, 221)


def theme_console(name: str) -> Tuple[int, int, int]:
    """Theme-tinted deck pane, dark enough for the player's ANSI palette.

    The player's console colours assume a dark terminal. Using the pale glass
    field directly (Sensorium, Dreamworlds) made the deck text almost
    unreadable, so the pane is the theme's console colour mixed 55% with black:
    Into Darkness stays black, Sensorium is a warm charcoal, Stream a deep navy.
    """
    try:
        from mp_theme_palette import theme_background
        red, green, blue = theme_background(name)
    except Exception:
        red, green, blue = theme_field(name)
    return tuple(int(round(channel * 0.22)) for channel in (red, green, blue))


def deck_pane_ink(name: str) -> Tuple[int, int, int]:
    """Readable default ink for the tinted deck pane."""
    red, green, blue = theme_console(name)
    luma = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return (18, 22, 26) if luma > 150 else (236, 239, 244)


def theme_ink(name: str) -> Tuple[int, int, int]:
    try:
        from liquid_glass_opengl import glass_foreground_rgb
        return glass_foreground_rgb(name)
    except Exception:
        return (24, 28, 32)


def theme_label(name: str) -> str:
    try:
        from mp_theme_palette import theme_label as label
        return label(name)
    except Exception:
        return name


# Themes the mixer can draw itself, but which MP Audio Player's --theme
# argparse table does not accept. A deck is a player, so it has to be handed a
# name that parser knows; the mixer still paints its own chrome with the real
# theme. Mapping is by intent (what the theme looks like), never by convenience.
DECK_THEME_FALLBACK: Dict[str, str] = {
    "solarized": "retro_studio",        # deep amber / gold console
    "tokyo": "dreamworlds_neon",        # violet on deep navy
    "monokai": "cyber_neon",            # vivid magenta / green on charcoal
    "emerald": "phosphor",              # green on black
    "blood_moon": "cosmic_supernova",   # crimson / deep rose
    "matrix": "phosphor",
    "nord": "ice_cold",
}


def deck_theme(name: str) -> str:
    """The theme id to pass to a deck's MP Audio Player.

    The mixer offers more themes than the player's ``--theme`` table accepts.
    Handing the player an unknown id made argparse exit, so the deck spawn
    either died outright or fell back to whatever the shared theme file said.
    Anything the player cannot parse is mapped to its closest accepted theme.
    """
    key = str(name or "").strip().lower()
    try:
        from mp_theme_palette import normalize_theme_name
        key = normalize_theme_name(key)
    except Exception:
        pass
    return DECK_THEME_FALLBACK.get(key, key)


# -- visualizer bookkeeping ---------------------------------------------
def read_visualizer_pid() -> int:
    try:
        return int(VISUALIZER_PID_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def write_visualizer_pid(pid: int) -> None:
    try:
        if int(pid) > 0:
            VISUALIZER_PID_PATH.write_text(str(int(pid)), encoding="utf-8")
        else:
            VISUALIZER_PID_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def visualizer_pid_alive() -> Optional[int]:
    pid = read_visualizer_pid()
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    # A killed child stays a zombie until reaped; os.kill(pid, 0) still
    # succeeds, which made the mixer believe a closed visualizer was alive.
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        state = stat.rsplit(")", 1)[1].split()[0]
        if state == "Z":
            return None
    except (OSError, IndexError):
        pass
    return pid


def acquire_mixer_lock():
    """One mixer at a time: both decks live in fixed IPC namespaces.

    A second window would spawn a second pair of engines into the same
    namespaces and the two would retire each other's daemons. Returns an open
    fd held for the process lifetime, "unsupported" without fcntl, or None.
    """
    try:
        import fcntl
    except ImportError:
        return "unsupported"
    try:
        fd = os.open(str(LOCK_PATH), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    try:
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
    except OSError:
        pass
    return fd


def stop_owned_visualizer() -> None:
    pid = visualizer_pid_alive()
    if pid:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    write_visualizer_pid(0)


# -- layout --------------------------------------------------------------
def layout(width: int = DESIGN_W, height: int = DESIGN_H) -> Dict[str, Tuple[float, float, float, float]]:
    """Design-grid rectangles, scaled to the real window and letterboxed.

    The mixer is composed at 1920x1080 and scaled by the smaller axis, so a
    4K fullscreen keeps the same proportions instead of stretching.
    """
    width, height = max(320, int(width)), max(240, int(height))
    scale = min(width / DESIGN_W, height / DESIGN_H)
    ox = (width - DESIGN_W * scale) / 2.0
    oy = (height - DESIGN_H * scale) / 2.0

    def rect(x: float, y: float, w: float, h: float) -> Tuple[float, float, float, float]:
        return (ox + x * scale, oy + y * scale, w * scale, h * scale)

    boxes: Dict[str, Tuple[float, float, float, float]] = {
        "canvas": rect(0, 0, DESIGN_W, DESIGN_H),
        "top_bar": rect(0, 0, DESIGN_W, 64),
        "bottom_bar": rect(0, 1016, DESIGN_W, 64),
        "deck_a": rect(24, 88, 786, 900),
        "deck_b": rect(1110, 88, 786, 900),
        "deck_a_header": rect(24, 88, 786, 40),
        "deck_b_header": rect(1110, 88, 786, 40),
        "deck_a_term": rect(36, 138, 762, 838),
        "deck_b_term": rect(1122, 138, 762, 838),
        "mixer": rect(842, 88, 236, 900),
        "browse_button": rect(852, 686, 216, 28),
        "mode_button": rect(852, 726, 216, 40),
        "record_button": rect(852, 800, 216, 40),
        "format_button": rect(852, 770, 104, 26),
        "bitdepth_button": rect(964, 770, 104, 26),
        "credit_button": rect(852, 862, 216, 34),
        "clock_button": rect(852, 904, 216, 46),
        "export_button": rect(852, 954, 216, 30),
        "xfader": rect(852, 620, 216, 52),
        "pitch_a": rect(810, 150, 26, 420),
        "pitch_b": rect(1084, 150, 26, 420),
        "sync_a": rect(620, 94, 70, 28),
        "sync_b": rect(1706, 94, 70, 28),
        "opacity": rect(192, 1040, 1536, 24),
        "visualizer_button": rect(1600, 14, 196, 36),
        "trance_button": rect(540, 14, 300, 36),
        "close_button": rect(1836, 14, 48, 36),
    }
    # Channel strips: the volume faders are a quarter of their old height.
    strip_y, strip_h, strip_w, strip_gap = 430.0, 140.0, 48.0, 8.0
    start_x = 842 + (236 - (4 * strip_w + 3 * strip_gap)) / 2.0
    for i, channel in enumerate(CHANNELS):
        x = start_x + i * (strip_w + strip_gap)
        boxes[f"strip_{channel}"] = rect(x, strip_y, strip_w, strip_h)
        boxes[f"strip_{channel}_label"] = rect(x, strip_y - 26, strip_w, 20)
        boxes[f"strip_{channel}_value"] = rect(x, strip_y + strip_h + 6, strip_w, 18)

    # Four rows of four knobs: GAIN, TREBLE, MID, LOW, under INTERNAL.
    row_centres = {"gain": 160.0, "high": 226.0, "mid": 292.0, "low": 358.0}
    knob_x, knob_w, knob_gap = 886.0, 40.0, 4.0
    for row in ("gain", "high", "mid", "low"):
        centre = row_centres[row]
        for i, channel in enumerate(CHANNELS):
            x = knob_x + i * (knob_w + knob_gap)
            boxes[f"knob_{row}_{channel}"] = rect(x, centre - knob_w / 2, knob_w, knob_w)
            boxes[f"knob_{row}_{channel}_value"] = rect(x - 4, centre + knob_w / 2 + 2, knob_w + 8, 14)
        boxes[f"knob_{row}_label"] = rect(846, centre - 8, 36, 16)
    return boxes


def opacity_from_x(x: float, box: Tuple[float, float, float, float]) -> float:
    bx, _by, bw, _bh = box
    t = clamp01((float(x) - bx) / max(1.0, float(bw)))
    return MIN_OPACITY + t * (MAX_OPACITY - MIN_OPACITY)


def x_from_opacity(opacity: float, box: Tuple[float, float, float, float]) -> float:
    bx, _by, bw, _bh = box
    span = max(1e-6, MAX_OPACITY - MIN_OPACITY)
    t = clamp01((float(opacity) - MIN_OPACITY) / span)
    return bx + t * float(bw)


def fader_value_from_y(y: float, box: Tuple[float, float, float, float]) -> float:
    """Vertical fader: top is 1.0, bottom is 0.0."""
    _bx, by, _bw, bh = box
    return clamp01(1.0 - (float(y) - by) / max(1.0, float(bh)))


def y_from_fader_value(value: float, box: Tuple[float, float, float, float]) -> float:
    _bx, by, _bw, bh = box
    return by + (1.0 - clamp01(value)) * float(bh)


def xfader_value_from_x(x: float, box: Tuple[float, float, float, float]) -> float:
    bx, _by, bw, _bh = box
    return clamp01((float(x) - bx) / max(1.0, float(bw)))


def inside(x: float, y: float, box: Tuple[float, float, float, float]) -> bool:
    bx, by, bw, bh = box
    return bx <= x <= bx + bw and by <= y <= by + bh


def now() -> float:
    return time.monotonic()
