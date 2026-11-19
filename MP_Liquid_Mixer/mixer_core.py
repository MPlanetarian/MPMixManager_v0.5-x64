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
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
# Channels the crossfader blends. C and D are wired into the surface but stay
# inactive, which is the documented v0.0.1 limit.
CROSSFADE_CHANNELS = ("A", "B")
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
    """Four channels, one crossfader, four live decks and four headphone cues.

    A, B, C and D each own an MP Liquid Player engine. A and B are blended by
    the crossfader; C and D are extra inputs that always play at their fader
    level. Each strip also has a headphone cue: pressing it moves that one
    engine's stream to the Bluetooth sink, leaving the other channels (and the
    system default) on the audio interface.
    """

    def __init__(self, gains: Optional[Dict[str, float]] = None,
                 crossfade: float = DEFAULT_CROSSFADE,
                 mode: str = "INTERNAL",
                 opacity: float = DEFAULT_OPACITY,
                 trims: Optional[Dict[str, float]] = None,
                 eq: Optional[Dict[str, Dict[str, float]]] = None,
                 headphones: Optional[Dict[str, bool]] = None) -> None:
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
        # Which channels the user asked to hear in the Bluetooth headphones.
        # The model records the intent; the app verifies the headphones are
        # actually connected before it routes anything.
        self.headphones: Dict[str, bool] = {ch: False for ch in CHANNELS}
        if isinstance(headphones, dict):
            for ch, value in headphones.items():
                if ch in self.headphones:
                    self.headphones[ch] = bool(value)
        self.crossfade = clamp01(crossfade)
        self.mode = mode.upper() if str(mode).upper() in MIX_MODES else "INTERNAL"
        self.opacity = min(MAX_OPACITY, max(MIN_OPACITY, float(opacity)))

    # -- channels ---------------------------------------------------------
    def channel_active(self, channel: str) -> bool:
        return channel in ACTIVE_CHANNELS

    def channel_gain(self, channel: str) -> float:
        return self.gains.get(channel, DEFAULT_GAIN)

    def set_channel(self, channel: str, value: float) -> bool:
        """Set one channel fader."""
        if channel not in self.gains or not self.channel_active(channel):
            return False
        self.gains[channel] = clamp01(value)
        return True

    # -- headphone cue ----------------------------------------------------
    def headphone(self, channel: str) -> bool:
        return bool(self.headphones.get(channel, False))

    def set_headphone(self, channel: str, enabled: bool) -> bool:
        """Remember that this channel should be heard in the headphones.

        Returns False for an unknown channel. It does not touch the audio
        path: routing needs a confirmed Bluetooth sink, which only the app can
        check, so this stays a pure model write.
        """
        if channel not in self.headphones:
            return False
        self.headphones[channel] = bool(enabled)
        return True

    def toggle_headphone(self, channel: str) -> bool:
        """Flip one channel's cue and return the new state."""
        if channel not in self.headphones:
            return False
        state = not self.headphones[channel]
        self.headphones[channel] = state
        return state

    def headphone_channels(self) -> Tuple[str, ...]:
        """Channels currently cued to the headphones, in strip order."""
        return tuple(ch for ch in CHANNELS if self.headphones.get(ch))

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
    def channel_volume(self, channel: str) -> int:
        """One channel's engine volume percentage.

        INTERNAL applies channel gain x trim; A and B are additionally scaled
        by the equal-power crossfader, C and D are not. EXTERNAL hands every
        deck to the house mixer at unity, so the software faders and the
        crossfader are bypassed (and drawn dimmed) instead of fighting it.
        """
        if self.external:
            return 100
        gain = clamp01(self.gains.get(channel, DEFAULT_GAIN))
        trim = max(0.0, float(self.trims.get(channel, DEFAULT_TRIM)))
        if channel in CROSSFADE_CHANNELS:
            gain_a, gain_b = self.crossfade_gains()
            gain *= gain_a if channel == "A" else gain_b
        return max(0, min(100, int(round(100.0 * gain * trim))))

    def channel_volumes(self) -> Dict[str, int]:
        return {ch: self.channel_volume(ch) for ch in CHANNELS}

    def deck_volumes(self) -> Tuple[int, int]:
        """(A, B) volume percentages for the two crossfaded engines."""
        return self.channel_volume("A"), self.channel_volume("B")

    def volume_for(self, channel: str) -> int:
        return self.channel_volume(channel)

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


# -- first run -----------------------------------------------------------
# A user whose very first MP launch is the mixer shortcut has configured no
# library at all, so both decks open on an empty Music Folders list and there
# is nothing to mix. The bundled release theme track is loaded into each deck
# (paused) in that one case, so the mixer is usable the moment it opens. As
# soon as either library has been configured the decks are left alone: those
# users have real tracks and must not have the theme track pushed in front of
# them on every launch.
THEME_TRACK_NAME = "MPlanetarian - The Orbits of Planets (Cosmic Dreamworlds Remake).flac"
CONFIG_ENV_PATHS = (PROJECT_ROOT / "config.env", CONFIG_DIR / "config.env")
_ARCHIVE_CONFIG_KEYS = ("MIX_ARCHIVE_CONFIGURED", "MIX_ARCHIVE_DIR",
                        "EXTRA_MIX_ARCHIVE_DIRS", "MIX_ARCHIVE_DIRS")


def _parse_env_file(path: Path) -> Dict[str, str]:
    """Read KEY="value" lines without importing the player module."""
    values: Dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"\'')
    return values


def config_values(paths: Optional[Tuple[Path, ...]] = None) -> Dict[str, str]:
    """Merged ``config.env`` values, with the process environment winning."""
    merged: Dict[str, str] = {}
    for path in (paths or CONFIG_ENV_PATHS):
        merged.update(_parse_env_file(path))
    for key in _ARCHIVE_CONFIG_KEYS + ("MUSIC_SMB_HOST", "MUSIC_SMB_SHARES"):
        value = os.environ.get(key)
        if value:
            merged[key] = value
    return merged


def music_folders_configured(values: Optional[Dict[str, str]] = None) -> bool:
    """True when at least one Music Folder has been set up on this machine."""
    try:
        module = _music_folders_module()
        if module is not None:
            folders = module.load_folders(module.load_music_config())
            if folders:
                return True
    except Exception:
        pass
    values = values if values is not None else config_values()
    # Pre-music_folders.json installs kept the SMB login in config.env.
    if str(values.get("MUSIC_SMB_HOST") or "").strip() and \
            str(values.get("MUSIC_SMB_SHARES") or "").strip():
        return True
    # A playlist that already names real tracks is a working library even if
    # the configuration file has since been moved; the decks must keep using
    # it rather than having the theme track pushed in front of it.
    if _generated_music_playlist_has_tracks():
        return True
    return False


def _generated_music_playlist_has_tracks() -> bool:
    """True when the generated ALL Music Folders playlist holds a real row."""
    playlists = (PROJECT_ROOT / "PLAYLISTS_GENERATED",
                 CONFIG_DIR / "PLAYLISTS_GENERATED")
    for folder in playlists:
        for name in ("DJ_Tracks_ALL_MUSIC_FOLDERS.json", "DJ_Tracks_ALL_MUSIC_FOLDERS.m3u8"):
            path = folder / name
            if not path.is_file():
                continue
            try:
                if name.endswith(".json"):
                    rows = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
                    if isinstance(rows, list) and any(
                            isinstance(row, dict) and str(row.get("path") or "").strip() for row in rows):
                        return True
                else:
                    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                        line = line.strip()
                        if line and not line.startswith("#"):
                            return True
            except (OSError, ValueError):
                continue
    return False


def mix_archive_configured(values: Optional[Dict[str, str]] = None) -> bool:
    """True when a Mix Archive folder has been set up on this machine."""
    values = values if values is not None else config_values()
    if str(values.get("MIX_ARCHIVE_DIR") or "").strip():
        return True
    if str(values.get("EXTRA_MIX_ARCHIVE_DIRS") or values.get("MIX_ARCHIVE_DIRS") or "").strip():
        return True
    flag = str(values.get("MIX_ARCHIVE_CONFIGURED") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def library_configured(values: Optional[Dict[str, str]] = None) -> bool:
    """True when either library has ever been configured for this user."""
    values = values if values is not None else config_values()
    return music_folders_configured(values) or mix_archive_configured(values)


def theme_track_path() -> Path:
    """The bundled release theme track, resolved from the shared constant."""
    try:
        from liquid_glass_reward import SAMPLE_TRACK_FILE
        return Path(SAMPLE_TRACK_FILE)
    except ImportError:
        pass
    try:
        from scripts.liquid_glass_reward import SAMPLE_TRACK_FILE
        return Path(SAMPLE_TRACK_FILE)
    except ImportError:
        return PROJECT_ROOT / "MP_SAMPLES" / THEME_TRACK_NAME


def first_run_preload_track(configured: Optional[bool] = None) -> Optional[str]:
    """The track a first-run deck should open with, or None.

    Only a user with neither a Music Folders nor a Mix Archive configuration
    gets the theme track. The decks still open paused; the point is that the
    mixer is playable immediately instead of showing two empty libraries.
    """
    is_configured = library_configured() if configured is None else bool(configured)
    if is_configured:
        return None
    track = theme_track_path()
    try:
        if track.is_file() and track.stat().st_size > 0:
            return str(track.resolve())
    except OSError:
        pass
    return None


def write_first_run_playlist(track: Optional[str] = None) -> Optional[Path]:
    """Write the one-row playlist a first-run deck opens with.

    A deck's library comes from a playlist file, and with no Music Folders
    configured the generated ``ALL`` playlist is empty or missing. Handing the
    deck a real playlist that holds the bundled theme track makes the UI list
    and the engine agree, so the deck shows the track ready to play instead of
    "No audio tracks found in the Music playlist."

    Returns None when the user has a configured library or the track is
    missing, so a normal launch never touches the file. A file left by an
    earlier first run is removed then, because the decks read the real library.
    """
    target = CONFIG_DIR / "first_run_mix_playlist.m3u8"
    if track is None:
        track = first_run_preload_track()
    if not track:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    try:
        duration = 0.0
        try:
            from liquid_glass_reward import SAMPLE_TRACK_DURATION_SEC
            duration = float(SAMPLE_TRACK_DURATION_SEC)
        except (ImportError, TypeError, ValueError):
            try:
                from scripts.liquid_glass_reward import SAMPLE_TRACK_DURATION_SEC
                duration = float(SAMPLE_TRACK_DURATION_SEC)
            except (ImportError, TypeError, ValueError):
                duration = 0.0
        title = Path(track).stem
        lines = ["#EXTM3U"]
        lines.append(f"#EXTINF:{int(duration) if duration > 0 else -1},{title}")
        lines.append(str(track))
        tmp = target.with_suffix(".m3u8.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, target)
        return target
    except OSError:
        return None


# -- audio verification --------------------------------------------------
# "Verify Audio Files" exists because a library can look complete and still
# contain rows that cannot play: an SMB share that is offline, a file that was
# never copied (0 bytes, as a failed bulk copy leaves), or a container the
# decoders reject. Any of those turns into a deck that "does nothing" mid-set.
#
# The check is deliberately the same test playback uses: the file has to exist,
# be non-empty, and offer a decodable audio stream. Probing tens of thousands
# of files is slow over SMB, so the work runs in a thread pool and reports
# progress through a callback the UI can draw.
VERIFY_REASONS = {
    "missing": "file is missing (share offline or path changed)",
    "empty": "file is empty (0 bytes - copy never finished)",
    "unreadable": "file cannot be decoded (corrupt or unsupported)",
}


class AudioVerdict:
    """One row's verification result."""

    __slots__ = ("path", "title", "ok", "reason", "detail")

    def __init__(self, path: str, title: str, ok: bool,
                 reason: str = "", detail: str = "") -> None:
        self.path = path
        self.title = title
        self.ok = ok
        self.reason = reason
        self.detail = detail

    def as_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "title": self.title, "ok": self.ok,
                "reason": self.reason, "detail": self.detail}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        state = "ok" if self.ok else f"bad:{self.reason}"
        return f"<AudioVerdict {state} {self.title!r}>"


def verify_audio_path(path: Optional[str], timeout: float = 45.0) -> AudioVerdict:
    """Check one library row the way the engine will when it is played.

    Order matters for speed on a network share: a missing file is decided by a
    single stat, and an empty one by its size, without starting ffprobe. Only a
    file that passes both is worth the decode probe.
    """
    name = os.path.basename(str(path or "")) or "(no path)"
    if not path:
        return AudioVerdict(str(path or ""), name, False, "missing", "no path recorded")
    try:
        stat = os.stat(str(path))
    except OSError as exc:
        return AudioVerdict(str(path), name, False, "missing", str(exc))
    if stat.st_size <= 0:
        return AudioVerdict(str(path), name, False, "empty", "0 bytes")
    probe = shutil.which("ffprobe")
    if not probe:
        # Without ffprobe the size check is all that is available; reporting a
        # file as bad would remove playable tracks, so trust the container.
        return AudioVerdict(str(path), name, True)
    try:
        # Asking for a codec name is not enough: ffprobe trusts the container
        # extension and reports "flac" for a file of random bytes. Counting the
        # audio packets actually read is what distinguishes a decodable file
        # from a plausible-looking corpse. It is still a header-level scan, not
        # a full decode, so it stays cheap over SMB.
        result = subprocess.run(
            [probe, "-v", "error", "-count_packets", "-select_streams", "a:0",
             "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=float(timeout),
        )
    except subprocess.TimeoutExpired:
        return AudioVerdict(str(path), name, False, "unreadable", "probe timed out")
    except Exception as exc:  # a broken ffprobe must not condemn the library
        return AudioVerdict(str(path), name, True, detail=f"probe failed: {exc}")
    packets = (result.stdout or "").strip().splitlines()
    first = packets[0].strip() if packets else ""
    if result.returncode != 0 or first in ("", "N/A", "0"):
        detail = (result.stderr or "").strip().splitlines()
        return AudioVerdict(str(path), name, False, "unreadable",
                            detail[0] if detail else "no audio packets could be read")
    return AudioVerdict(str(path), name, True)


def verify_audio_library(rows: List[Dict[str, Any]], workers: int = 12,
                         progress: Optional[Any] = None,
                         cancel: Optional[Any] = None) -> List[AudioVerdict]:
    """Verify every row, in parallel, reporting progress as it goes.

    ``progress(done, total, verdict)`` is called once per finished row from
    whichever worker finished it, so a UI callback must marshal to its own
    loop. ``cancel()`` returning True stops early and returns what it has.
    Results keep the playlist order, which is what the caller rewrites.
    """
    total = len(rows)
    verdicts: List[Optional[AudioVerdict]] = [None] * total
    if not total:
        return []
    done = 0
    lock = threading.Lock()
    should_stop = threading.Event()

    def work(index: int) -> None:
        nonlocal done
        if should_stop.is_set():
            return
        if cancel is not None:
            try:
                if cancel():
                    should_stop.set()
                    return
            except Exception:
                pass
        row = rows[index]
        verdict = verify_audio_path(row.get("path"))
        verdicts[index] = verdict
        with lock:
            done += 1
            if progress is not None:
                try:
                    progress(done, total, verdict)
                except Exception:
                    pass

    worker_count = max(1, min(int(workers or 1), total))
    threads = []
    # A plain thread pool keeps this dependency-free; concurrent.futures is
    # available too but the number of rows here can exceed its queue comfort.
    next_index = 0
    index_lock = threading.Lock()

    def run() -> None:
        nonlocal next_index
        while True:
            if should_stop.is_set():
                return
            with index_lock:
                if next_index >= total:
                    return
                i = next_index
                next_index += 1
            work(i)

    for n in range(worker_count):
        thread = threading.Thread(target=run, name=f"verify-{n}", daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    return [v for v in verdicts if v is not None]


def verify_summary(verdicts: List[AudioVerdict]) -> Dict[str, Any]:
    """Counts for the confirm dialog and the log line."""
    bad = [v for v in verdicts if not v.ok]
    by_reason: Dict[str, int] = {}
    for verdict in bad:
        by_reason[verdict.reason] = by_reason.get(verdict.reason, 0) + 1
    return {"total": len(verdicts), "ok": len(verdicts) - len(bad),
            "bad": len(bad), "by_reason": by_reason}


def music_playlist_json_path() -> Optional[Path]:
    """The ALL Music Folders playlist JSON the decks read, or None."""
    module = _music_folders_module()
    if module is None:
        return None
    try:
        config = module.load_music_config()
        _m3u, json_path = module.get_playlist_paths("ALL", config)
        return Path(json_path)
    except Exception:
        return None


def read_playlist_rows(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Rows from a generated playlist JSON file."""
    target = Path(path) if path is not None else music_playlist_json_path()
    if target is None:
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return []
    rows = data if isinstance(data, list) else (data.get("tracks") or [])
    return [row for row in rows if isinstance(row, dict) and row.get("path")]


def remove_rows_from_playlist(paths: Any, path: Optional[Path] = None) -> int:
    """Drop the given rows from a playlist, rewriting both files beside it.

    Returns how many rows were removed. An explicit ``path`` is written in
    place (the JSON plus an m3u8 beside it); only the real generated playlist
    goes through ``save_playlist_files``, which also refreshes the player's
    fast sidecar cache. A failure leaves the playlist untouched and reports 0,
    so a bad write cannot destroy a good library.
    """
    drop = {str(p) for p in (paths or [])}
    if not drop:
        return 0
    explicit = path is not None
    target = Path(path) if explicit else music_playlist_json_path()
    if target is None:
        return 0
    rows = read_playlist_rows(target)
    if not rows:
        return 0
    kept = [row for row in rows if str(row.get("path")) not in drop]
    removed = len(rows) - len(kept)
    if removed <= 0:
        return 0

    if explicit:
        # A caller-supplied file must never touch the live library.
        try:
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_text(json.dumps(kept, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, target)
            m3u = target.with_suffix(".m3u8")
            lines = ["#EXTM3U", "#PLAYLIST:MP DJ Tracks Database - ALL MUSIC FOLDERS"]
            for row in kept:
                lines.append(f"#EXTINF:-1,{row.get('title') or row.get('name') or ''}")
                lines.append(str(row.get("path")))
            tmp_m3u = m3u.with_suffix(".m3u8.tmp")
            tmp_m3u.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(tmp_m3u, m3u)
            return removed
        except OSError:
            return 0

    module = _music_folders_module()
    try:
        if module is not None:
            config = module.load_music_config()
            # save_playlist_files writes the m3u8, the JSON and the player's
            # fast sidecar cache, which is exactly the set the readers use.
            module.save_playlist_files(kept, "ALL", config, title="ALL MUSIC FOLDERS")
            return removed
    except Exception:
        pass
    # No helper module (a stripped tree): rewrite the JSON atomically and let
    # the next playlist generation rebuild the m3u8.
    try:
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(kept, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
        return removed
    except OSError:
        return 0


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
        # Verify sits in the free band between the channel fader values
        # (which end at 594) and the crossfader (moved from 620 to 626). It
        # belongs with the library operations, and this is the only gap in
        # the mixer column that does not move a control the user knows.
        "verify_button": rect(852, 596, 216, 24),
        "xfader": rect(852, 626, 216, 52),
        "pitch_a": rect(810, 150, 26, 420),
        "pitch_b": rect(1084, 150, 26, 420),
        "sync_a": rect(620, 94, 70, 28),
        "sync_b": rect(1706, 94, 70, 28),
        "opacity": rect(192, 1040, 1536, 24),
        "visualizer_button": rect(1600, 14, 196, 36),
        "trance_button": rect(540, 14, 300, 36),
        "settings_button": rect(1150, 14, 144, 36),
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
