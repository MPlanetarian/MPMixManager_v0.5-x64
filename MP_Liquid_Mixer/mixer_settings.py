#!/usr/bin/env python3
"""MP Liquid Mixer - user settings and audio-device helpers (no GTK imports).

The mixer's Settings window writes one JSON file
(``~/.config/mix-manager/liquid_mixer_settings.json``). Everything here is
plain Python so it can be unit-tested without a display, and so the recorder
and the exporter can read the same file without importing the window.

The file is deliberately small and human-readable: a user who prefers an
editor can change a value and the mixer picks it up on its next launch. Every
value is validated on load, so a typo falls back to the documented default
instead of breaking a launch.

Version 0.0.1
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MIXER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MIXER_DIR.parent

CONFIG_DIR = Path(os.environ.get("MP_LIQUID_MIXER_CONFIG_DIR", "")
                  or (Path.home() / ".config" / "mix-manager"))
SETTINGS_PATH = CONFIG_DIR / "liquid_mixer_settings.json"

SETTINGS_VERSION = 1

# The recording part ceilings the Settings window offers. 4 GiB is the largest
# a WAV file can be; the smaller values are a user preference, not a limit of
# the container.
PART_LIMIT_CHOICES = (4, 2, 1)

RECORDING_FORMATS = ("WAV", "FLAC")

# The player's own presets (MP_Audio_Player.DEFAULT_EQ_PRESETS). Kept in sync by
# name rather than by importing the player: that module imports numpy,
# sounddevice and the whole engine, which the settings dialog must not need.
EQ_PRESETS = (
    "Flat",
    "Ambient",
    "Trance",
    "Techno",
    "House",
    "Progressive",
    "Vapourwave",
    "Sensorium Interface",
)

# Used only when mp_theme_palette cannot be imported (a stripped tree).
FALLBACK_THEMES: Tuple[Tuple[str, str], ...] = (
    ("dreamworlds_ultra", "Dreamworlds Ultra"),
    ("stream_of_frequency", "Stream of Frequency [Special Edition]"),
    ("sensorium", "Sensorium"),
    ("cosmic_supernova", "Cosmic Supernova"),
    ("into_darkness_void_out", "Into Darkness (Void Out)"),
)

DEFAULT_SETTINGS: Dict[str, Any] = {
    "version": SETTINGS_VERSION,
    # Recording
    "recording_dir": "",            # empty: MIX_ARCHIVE/MP_AUDIOWAV_REC_OUTPUT
    "recording_format": "WAV",
    "recording_part_limit_gib": 4,
    "recording_device": "",         # empty: capture the master output's monitor
    # Audio
    "master_output_device": "",     # empty: the system default sink
    # Look and feel
    "theme": "",                    # empty: the shared theme file decides
    "visualizer_enabled": True,
    "fullscreen": True,
    # Startup behaviour
    "startup_video": True,
    "exit_video": True,
    "decks_autoplay": False,
    "record_on_start": False,
    # Deck audio
    "eq_preset": "Flat",
}

_BOOL_KEYS = ("visualizer_enabled", "fullscreen", "startup_video", "exit_video",
              "decks_autoplay", "record_on_start")
_TEXT_KEYS = ("recording_dir", "recording_device", "master_output_device",
              "theme", "eq_preset")


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in ("1", "true", "yes", "on", "enabled"):
        return True
    if text in ("0", "false", "no", "off", "disabled"):
        return False
    return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class Settings:
    """One validated snapshot of the mixer's preferences."""

    def __init__(self, **values: Any) -> None:
        self.recording_dir = ""
        self.recording_format = "WAV"
        self.recording_part_limit_gib = 4
        self.recording_device = ""
        self.master_output_device = ""
        self.theme = ""
        self.visualizer_enabled = True
        self.fullscreen = True
        self.startup_video = True
        self.exit_video = True
        self.decks_autoplay = False
        self.record_on_start = False
        self.eq_preset = "Flat"
        self.update(values)

    # -- validation ------------------------------------------------------
    def update(self, values: Dict[str, Any]) -> None:
        values = values if isinstance(values, dict) else {}
        for key in _BOOL_KEYS:
            if key in values:
                setattr(self, key, _as_bool(values.get(key), getattr(self, key)))
        for key in _TEXT_KEYS:
            if key in values:
                setattr(self, key, str(values.get(key) or "").strip())
        if "recording_format" in values:
            fmt = str(values.get("recording_format") or "").strip().upper()
            self.recording_format = fmt if fmt in RECORDING_FORMATS else "WAV"
        if "recording_part_limit_gib" in values:
            limit = _as_int(values.get("recording_part_limit_gib"), 4)
            self.recording_part_limit_gib = limit if limit in PART_LIMIT_CHOICES else 4
        if self.eq_preset not in EQ_PRESETS:
            self.eq_preset = "Flat"
        if self.theme and not normalize_theme(self.theme):
            # A saved theme that no longer exists must not be written to the
            # shared theme file; an empty value means "whatever is current".
            self.theme = ""
        # A recording device of "master" means "follow the master output", the
        # default. Storing the empty string keeps the JSON unambiguous.
        if str(self.recording_device).strip().lower() in ("master", "same", "default-master"):
            self.recording_device = ""
        if str(self.master_output_device).strip().lower() in ("default", "system", "auto"):
            self.master_output_device = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": SETTINGS_VERSION,
            "recording_dir": self.recording_dir,
            "recording_format": self.recording_format,
            "recording_part_limit_gib": self.recording_part_limit_gib,
            "recording_device": self.recording_device,
            "master_output_device": self.master_output_device,
            "theme": self.theme,
            "visualizer_enabled": bool(self.visualizer_enabled),
            "fullscreen": bool(self.fullscreen),
            "startup_video": bool(self.startup_video),
            "exit_video": bool(self.exit_video),
            "decks_autoplay": bool(self.decks_autoplay),
            "record_on_start": bool(self.record_on_start),
            "eq_preset": self.eq_preset,
        }

    # -- persistence -----------------------------------------------------
    def save(self, path: Optional[Path] = None) -> Path:
        target = Path(path or SETTINGS_PATH)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, target)
        except OSError:
            pass
        return target

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Settings":
        target = Path(path or SETTINGS_PATH)
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            return cls()
        return cls(**data) if isinstance(data, dict) else cls()

    # -- helpers used by the window --------------------------------------
    def as_recorder_kwargs(self) -> Dict[str, Any]:
        """The values ``MasterRecorder`` needs, named the way it accepts them."""
        return {
            "format": self.recording_format,
            "part_limit_gib": self.recording_part_limit_gib,
            "device": self.recording_device or self.master_output_device,
        }

    def copy(self) -> "Settings":
        return Settings(**self.to_dict())


def load_settings(path: Optional[Path] = None) -> Settings:
    return Settings.load(path)


def default_recording_dir() -> Path:
    """The folder a recording lands in when no override is set.

    Imported lazily: ``mixer_recording`` owns the archive lookup, and importing
    it at module scope would make the settings module depend on the capture
    code for a single path.
    """
    try:
        import mixer_recording
        return mixer_recording.recording_dir()
    except Exception:
        return CONFIG_DIR / "MP_AUDIOWAV_REC_OUTPUT"


# Historical name, kept because the recording module and older callers spell it
# the same way.
_default_recording_dir = default_recording_dir


# ------------------------------------------------------------------------------
# theme choices
# ------------------------------------------------------------------------------
def theme_options() -> List[Tuple[str, str]]:
    """``(id, label)`` for the theme drop-down, in cycle order.

    The palette module owns the canonical ids and human labels; a tree without
    it still gets the handful of themes the mixer can always paint.
    """
    try:
        import sys
        if str(PROJECT_ROOT / "scripts") not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
        from mp_theme_palette import theme_ids, theme_label
        options = [(tid, theme_label(tid)) for tid in theme_ids()]
        if options:
            return options
    except Exception:
        pass
    return list(FALLBACK_THEMES)


def normalize_theme(name: str) -> str:
    """Canonical theme id, or "" when the name cannot be resolved."""
    key = str(name or "").strip().lower()
    if not key:
        return ""
    known = {tid for tid, _label in theme_options()}
    if key in known:
        return key
    try:
        import sys
        if str(PROJECT_ROOT / "scripts") not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
        from mp_theme_palette import THEME_ALIASES, normalize_theme_name
        for candidate in (key, key.replace("_", " "), key.replace("-", " "),
                          key.replace(" ", "_")):
            if candidate in THEME_ALIASES:
                resolved = THEME_ALIASES[candidate]
                return resolved if resolved in known else ""
            if candidate in known:
                return candidate
        resolved = normalize_theme_name(key)
        return resolved if key.replace("_", " ") == resolved.replace("_", " ") else ""
    except Exception:
        pass
    return ""


# ------------------------------------------------------------------------------
# audio sinks (PulseAudio / PipeWire via pactl, then wpctl)
# ------------------------------------------------------------------------------
def _run(argv: List[str], timeout: float = 1.5) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def _sinks_from_pactl_json() -> List[Dict[str, Any]]:
    raw = _run(["pactl", "-f", "json", "list", "sinks"])
    if not raw.strip():
        return []
    try:
        rows = json.loads(raw)
    except ValueError:
        return []
    sinks: List[Dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
        name = str(row.get("name") or props.get("node.name") or "").strip()
        if not name:
            continue
        description = str(row.get("description") or props.get("node.description") or name).strip()
        api = str(props.get("device.api") or props.get("device.bus") or "").lower()
        sinks.append({
            "name": name,
            "description": description,
            "index": int(row.get("index", -1) or -1),
            "bluetooth": bool(api == "bluez" or props.get("api.bluez5.codec") or "bluez" in api),
        })
    return sinks


def _sinks_from_pactl_text() -> List[Dict[str, Any]]:
    raw = _run(["pactl", "list", "sinks"])
    if not raw.strip():
        return []
    sinks: List[Dict[str, Any]] = []
    for block in raw.split("Sink #")[1:]:
        name = ""
        description = ""
        index = -1
        bluetooth = False
        for line in block.splitlines():
            text = line.strip()
            if text.startswith("Name:") and not name:
                name = text.split(":", 1)[1].strip()
            elif text.startswith("Description:") and not description:
                description = text.split(":", 1)[1].strip()
            elif text.startswith("device.api ="):
                bluetooth = bluetooth or "bluez" in text.lower()
            elif "bluez" in text.lower():
                bluetooth = True
        first = block.splitlines()[0].strip() if block.splitlines() else ""
        try:
            index = int(first)
        except ValueError:
            index = -1
        if name:
            sinks.append({"name": name, "description": description or name,
                          "index": index, "bluetooth": bluetooth})
    return sinks


def _sinks_from_wpctl() -> List[Dict[str, Any]]:
    raw = _run(["wpctl", "status"])
    if not raw.strip():
        return []
    sinks: List[Dict[str, Any]] = []
    in_sinks = False
    for line in raw.splitlines():
        if line.strip().endswith("Sinks:"):
            in_sinks = True
            continue
        if in_sinks and line.strip() and not line.startswith((" ", "\t")):
            in_sinks = False
        if not in_sinks:
            continue
        match = re.search(r"[*\s]*(\d+)\.\s+(.+?)(?:\[vol:.*)?$", line)
        if not match:
            continue
        name = match.group(2).strip()
        if not name or name in ("Sinks:",):
            continue
        sinks.append({"name": name, "description": name,
                      "index": int(match.group(1)), "bluetooth": "[bluez5]" in line})
    return sinks


_sink_cache: Dict[str, Any] = {"at": 0.0, "sinks": []}


def list_audio_sinks(force: bool = False) -> List[Dict[str, Any]]:
    """Every playback sink the desktop exposes, system default first.

    Returns ``[{"name", "description", "index", "bluetooth"}]``. PipeWire and
    PulseAudio both answer through ``pactl``; a system without it falls back to
    ``wpctl``. An empty list simply means the drop-down offers the system
    default only, which is always valid.
    """
    import time
    now = time.monotonic()
    if not force and _sink_cache["sinks"] and now - float(_sink_cache["at"] or 0.0) < 5.0:
        return list(_sink_cache["sinks"])
    sinks: List[Dict[str, Any]] = []
    if shutil.which("pactl"):
        sinks = _sinks_from_pactl_json() or _sinks_from_pactl_text()
    if not sinks and shutil.which("wpctl"):
        sinks = _sinks_from_wpctl()
    seen = set()
    unique: List[Dict[str, Any]] = []
    for sink in sinks:
        name = str(sink.get("name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        unique.append(sink)
    _sink_cache["at"], _sink_cache["sinks"] = now, unique
    return list(unique)


def sink_label(sink: Dict[str, Any]) -> str:
    description = str(sink.get("description") or sink.get("name") or "").strip()
    if sink.get("bluetooth"):
        return f"🎧 {description}"
    return f"🔊 {description}"


def default_sink_name() -> str:
    """The system's default sink name, or "" when it cannot be read."""
    raw = _run(["pactl", "get-default-sink"]).strip()
    if raw:
        return raw.splitlines()[0].strip()
    raw = _run(["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"])
    for line in raw.splitlines():
        if "node.name" in line:
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def sink_monitor(name: str) -> str:
    """The monitor source that records what a sink plays."""
    value = str(name or "").strip()
    if not value or value in ("@DEFAULT_SINK@", "@DEFAULT_MONITOR@", "default"):
        return "@DEFAULT_MONITOR@"
    return value if value.endswith(".monitor") else f"{value}.monitor"


def player_process_ids(namespace: str, config_dir: Optional[Path] = None) -> List[int]:
    """The engine pid recorded for a deck namespace.

    ``MP_Audio_Player`` writes its own pid and opens the output in that same
    process, so the pid file is the whole story on Linux. A wrapper process (the
    shell in a VTE) would not own the stream, which is why the pid file is used
    rather than the terminal's ``child_pid``.
    """
    directory = Path(config_dir or CONFIG_DIR)
    stem, dot, ext = "mp_player.pid".partition(".")
    name = f"{stem}-{namespace}{dot}{ext}" if namespace else "mp_player.pid"
    try:
        pid = int((directory / name).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return []
    return [pid] if pid > 0 else []


def _sink_inputs() -> List[Dict[str, Any]]:
    raw = _run(["pactl", "-f", "json", "list", "sink-inputs"])
    rows: List[Any] = []
    if raw.strip():
        try:
            rows = json.loads(raw)
        except ValueError:
            rows = []
    inputs: List[Dict[str, Any]] = []
    if isinstance(rows, list) and rows:
        for row in rows:
            if not isinstance(row, dict):
                continue
            props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
            inputs.append({
                "index": int(row.get("index", -1) or -1),
                "sink": int(row.get("sink", -1) or -1),
                "pid": str(props.get("application.process.id") or "").strip(),
                "app": str(props.get("application.name")
                           or props.get("application.process.binary")
                           or props.get("media.name") or "").strip(),
            })
        return inputs
    for block in _run(["pactl", "list", "sink-inputs"]).split("Sink Input #")[1:]:
        lines = block.splitlines()
        try:
            index = int(lines[0].strip())
        except (ValueError, IndexError):
            index = -1
        pid = ""
        app = ""
        sink = -1
        for line in lines:
            text = line.strip()
            if text.startswith("Sink:") and sink < 0:
                try:
                    sink = int(text.split(":", 1)[1].strip())
                except ValueError:
                    sink = -1
            elif text.startswith("application.process.id ="):
                pid = text.split("=", 1)[1].strip().strip('"')
            elif text.startswith("application.name =") and not app:
                app = text.split("=", 1)[1].strip().strip('"')
        inputs.append({"index": index, "sink": sink, "pid": pid, "app": app})
    return inputs


def move_player_stream(pid: int, sink: str) -> int:
    """Move every stream owned by ``pid`` to ``sink``; returns how many moved.

    A deck is an MP Audio Player engine process, so ``application.process.id``
    is the reliable way to pick out *its* stream and not another application's.
    Moving a stream is non-destructive: the process stays open and simply sends
    its audio to the chosen sink.
    """
    if not pid or not sink:
        return 0
    moved = 0
    for entry in _sink_inputs():
        if str(entry.get("pid")) != str(int(pid)):
            continue
        index = int(entry.get("index", -1))
        if index < 0:
            continue
        if _run(["pactl", "move-sink-input", str(index), sink]):
            moved += 1
    return moved


def move_namespace_streams(namespace: str, sink: str,
                           config_dir: Optional[Path] = None) -> int:
    """Move a named deck namespace's streams to ``sink``."""
    moved = 0
    for pid in player_process_ids(namespace, config_dir):
        moved += move_player_stream(pid, sink)
    return moved


def move_process_streams(namespace: str, sink: str,
                         env: Optional[Dict[str, str]] = None) -> int:
    """Send a deck's engine to ``sink`` when it can be found.

    ``env`` is accepted for symmetry with the other helpers and is not needed
    on Linux: the engine's pid file names the process that owns the stream. A
    deck that has not opened its stream yet simply reports 0 and is picked up
    by ``PULSE_SINK`` on its next launch.
    """
    del env
    return move_namespace_streams(namespace, sink)


# ------------------------------------------------------------------------------
# environment for child engines
# ------------------------------------------------------------------------------
def child_audio_env(settings: "Settings", sink: Optional[str] = None) -> Dict[str, str]:
    """Environment that pins an MP Audio Player engine to a chosen sink.

    ``PULSE_SINK`` is honoured by the ALSA pulse plugin PortAudio uses, so it
    routes the deck without changing the desktop's system default. The PortAudio
    device name is also supplied so the player does not have to rely on the
    plugin alone; either mechanism is enough, and both agreeing is the normal
    case.
    """
    env: Dict[str, str] = {}
    device = str(sink if sink is not None else settings.master_output_device or "").strip()
    if not device:
        env["MP_AUDIO_OUTPUT_DEVICE"] = "default"
        return env
    env["PULSE_SINK"] = device
    description = device
    for sink_row in list_audio_sinks():
        if str(sink_row.get("name")) == device:
            description = str(sink_row.get("description") or device)
            break
    env["MP_AUDIO_OUTPUT_DEVICE"] = description
    return env
