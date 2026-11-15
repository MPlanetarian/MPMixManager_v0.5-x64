#!/usr/bin/env python3
"""Headset play/pause for MP Liquid Player.

Bluetooth headphones (Skullcandy Crusher ANC 2 and every other AVRCP
handset) deliver the physical play button as a media key. On a Linux
desktop that key is handed to whichever app last registered as a media
player. This player never registered, so the press reached a video
player or the browser and a video started while the mix kept going.

Registering as an MPRIS player makes the desktop deliver Play, Pause,
Next, Previous and Stop here. A short shared debounce collapses the
duplicate that BlueZ sometimes delivers twice (once as the key, once
through mpris-proxy) so one press changes playback once.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

BUS_NAME = "org.mpris.MediaPlayer2.mp_liquid_player"
OBJECT_PATH = "/org/mpris/MediaPlayer2"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
PROP_IFACE = "org.freedesktop.DBus.Properties"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
IDENTITY = "MP Liquid Player"
DESKTOP_ENTRY = "MP Liquid Player"

# Gdk key values. Confirmed against Gdk 3. The numbers are inlined so a
# window host can recognise a headset key without importing Gdk first.
KEY_AUDIO_PLAY = 0x1008FF14
KEY_AUDIO_PAUSE = 0x1008FF31
KEY_AUDIO_STOP = 0x1008FF15
KEY_AUDIO_PREV = 0x1008FF16
KEY_AUDIO_NEXT = 0x1008FF17
KEY_AUDIO_REWIND = 0x1008FF3E
KEY_AUDIO_FORWARD = 0x1008FF97

DEBOUNCE_S = 0.35
VIDEO_EXTENSIONS = {
    ".mp4", ".m4v", ".mkv", ".avi", ".mov", ".webm", ".flv", ".ts",
    ".m2ts", ".mpg", ".mpeg", ".wmv", ".ogv",
}

# Play, Pause and PlayPause are one user action. A second copy of that
# action inside the debounce window is the duplicate delivery, not a
# second press.
_KEY_ACTIONS = {
    KEY_AUDIO_PLAY: ("transport", "toggle", {}),
    KEY_AUDIO_PAUSE: ("transport", "pause", {}),
    KEY_AUDIO_STOP: ("stop", "transport_stop", {}),
    KEY_AUDIO_NEXT: ("next", "next", {}),
    KEY_AUDIO_PREV: ("previous", "prev", {}),
    KEY_AUDIO_REWIND: ("seek_back", "seek", {"seconds": -5.0}),
    KEY_AUDIO_FORWARD: ("seek_fwd", "seek", {"seconds": 5.0}),
}


def debounce_file() -> Path:
    return Path.home() / ".config" / "mix-manager" / "headset_media_debounce.json"


def claim_headset_action(
    action: str,
    debounce_s: float = DEBOUNCE_S,
    now: Optional[float] = None,
    path: Optional[Path] = None,
) -> bool:
    """True when this headset action should run.

    The same press can arrive as a desktop media key and as an MPRIS
    call a few milliseconds later. The first one wins. ``now`` is wall
    time so two processes agree.
    """
    stamp = time.time() if now is None else float(now)
    target = Path(path) if path is not None else debounce_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        import fcntl
        with open(target, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            raw = handle.read()
            try:
                data = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                data = {}
            if not isinstance(data, dict):
                data = {}
            if action in data and stamp - float(data.get(action) or 0.0) < debounce_s:
                return False
            data[action] = stamp
            handle.seek(0)
            handle.truncate()
            json.dump(data, handle)
            handle.flush()
            return True
    except OSError:
        # A missing config directory must not swallow the button.
        return True


def playback_status(state: str) -> str:
    if state == "playing":
        return "Playing"
    if state == "paused":
        return "Paused"
    return "Stopped"


def loop_status(repeat_mode: str) -> str:
    return {"one": "Track", "all": "Playlist"}.get(str(repeat_mode), "None")


def repeat_mode_for(loop: str) -> Optional[str]:
    return {"None": "off", "Track": "one", "Playlist": "all"}.get(str(loop))


def track_object_path(path: str) -> str:
    if not path:
        return NO_TRACK
    digest = hashlib.sha1(path.encode("utf-8", "replace")).hexdigest()[:16]
    return f"/org/mpris/MediaPlayer2/Track/{digest}"


def path_from_uri(uri: str) -> str:
    text = str(uri or "").strip()
    if not text:
        return ""
    if text.startswith("file:"):
        parsed = urlparse(text)
        return unquote(parsed.path or "")
    if text.startswith("/"):
        return text
    return ""


def is_video_path(path: str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def metadata_for(track: Optional[dict], duration_sec: float) -> Dict[str, Any]:
    path = str((track or {}).get("path") or "")
    if not path:
        return {"mpris:trackid": NO_TRACK}
    title = str((track or {}).get("name") or Path(path).stem or "Unknown")
    meta: Dict[str, Any] = {
        "mpris:trackid": track_object_path(path),
        "xesam:title": title,
    }
    try:
        meta["xesam:url"] = Path(path).as_uri()
    except ValueError:
        pass
    if duration_sec and duration_sec > 0:
        meta["mpris:length"] = int(round(float(duration_sec) * 1_000_000))
    return meta


def position_us(position_sec: float) -> int:
    return int(round(max(0.0, float(position_sec or 0.0)) * 1_000_000))


class MprisController:
    """Maps MPRIS methods onto the audio engine. No D-Bus types in here."""

    def __init__(
        self,
        engine: Any,
        debounce_path: Optional[Path] = None,
        clock: Optional[Callable[[], float]] = None,
        debounce_s: float = DEBOUNCE_S,
    ):
        self.engine = engine
        self._debounce_path = debounce_path
        self._clock = clock
        self._debounce_s = debounce_s

    def _claim(self, action: str) -> bool:
        now = None if self._clock is None else self._clock()
        return claim_headset_action(
            action,
            debounce_s=self._debounce_s,
            now=now,
            path=self._debounce_path,
        )

    def play(self) -> None:
        # The Crusher (and a lot of other headsets) send Play for the
        # physical button even while the mix is already playing. That
        # press means pause. A desktop Pause or PlayPause for the same
        # press shares the claim, so the two copies cannot undo it.
        if getattr(self.engine, "state", "") == "playing":
            self.pause()
            return
        if not self._claim("transport"):
            return
        self.engine.play()

    def pause(self) -> None:
        if getattr(self.engine, "state", "") != "playing":
            return
        if not self._claim("transport"):
            return
        self.engine.pause()

    def play_pause(self) -> None:
        if not self._claim("transport"):
            return
        if getattr(self.engine, "state", "") == "playing":
            self.engine.pause()
        else:
            self.engine.play()

    def stop(self) -> None:
        if getattr(self.engine, "state", "") == "stopped":
            return
        if not self._claim("stop"):
            return
        self.engine.stop_playback(reset_position=False)

    def next(self) -> None:
        if not self._claim("next"):
            return
        self.engine.next_track()

    def previous(self) -> None:
        if not self._claim("previous"):
            return
        self.engine.prev_track()

    def seek_us(self, offset_us: int) -> int:
        action = "seek_fwd" if int(offset_us) >= 0 else "seek_back"
        if self._claim(action):
            self.engine.seek_relative(float(offset_us) / 1_000_000.0)
        return position_us(getattr(self.engine, "position_sec", 0.0))

    def set_position(self, track_id: str, position: int) -> Optional[int]:
        current = track_object_path(self._track_path())
        if str(track_id) != current or current == NO_TRACK:
            return None
        self.engine.seek(float(position) / 1_000_000.0, force=True)
        return position_us(getattr(self.engine, "position_sec", 0.0))

    def set_volume(self, volume: float) -> None:
        level = max(0.0, min(1.0, float(volume)))
        self.engine.set_volume(int(round(level * 100)))

    def set_shuffle(self, enabled: bool) -> None:
        self.engine.shuffle = bool(enabled)
        sync = getattr(self.engine, "_sync_state_file", None)
        if sync is not None:
            sync()

    def set_loop(self, status: str) -> bool:
        mode = repeat_mode_for(status)
        if mode is None:
            return False
        self.engine.repeat_mode = mode
        sync = getattr(self.engine, "_sync_state_file", None)
        if sync is not None:
            sync()
        return True

    def open_uri(self, uri: str) -> bool:
        """Load an audio file. A video URI is refused and nothing is opened."""
        path = path_from_uri(uri)
        if not path or is_video_path(path) or not os.path.isfile(path):
            return False
        engine = self.engine
        playlist = getattr(engine, "playlist", None)
        if isinstance(playlist, list):
            for index, track in enumerate(list(playlist)):
                if isinstance(track, dict) and track.get("path") == path:
                    return bool(engine.load_track(index, True))
            playlist.insert(0, {
                "name": os.path.basename(path),
                "path": path,
                "size_mb": os.path.getsize(path) / 1048576.0,
                "mtime": time.time(),
                "ext": os.path.splitext(path)[1][1:].upper(),
            })
            return bool(engine.load_track(0, True))
        return False

    def _track_path(self) -> str:
        track = getattr(self.engine, "current_track", None) or {}
        return str(track.get("path") or "") if isinstance(track, dict) else ""

    def _has_media(self) -> bool:
        if self._track_path():
            return True
        playlist = getattr(self.engine, "playlist", None) or []
        return bool(playlist)

    def volume(self) -> float:
        if getattr(self.engine, "muted", False):
            return 0.0
        return max(0.0, min(1.0, float(getattr(self.engine, "volume", 1.0) or 0.0)))

    def snapshot(self) -> Dict[str, Any]:
        engine = self.engine
        track = engine.current_track if isinstance(getattr(engine, "current_track", None), dict) else None
        has = self._has_media()
        duration = float(getattr(engine, "duration_sec", 0.0) or 0.0)
        return {
            "PlaybackStatus": playback_status(str(getattr(engine, "state", "stopped"))),
            "LoopStatus": loop_status(str(getattr(engine, "repeat_mode", "off"))),
            "Shuffle": bool(getattr(engine, "shuffle", False)),
            "Volume": round(self.volume(), 3),
            "Metadata": metadata_for(track, duration),
            "CanGoNext": has,
            "CanGoPrevious": has,
            "CanPlay": has,
            "CanPause": has,
            "CanSeek": duration > 0.0,
            "CanControl": True,
        }


def player_socket_path() -> Path:
    return Path.home() / ".config" / "mix-manager" / "mp_audio_player.sock"


def send_player_command(cmd: str, **kwargs: Any) -> bool:
    """One IPC command to the running audio daemon. False when it is down."""
    path = player_socket_path()
    if not path.exists():
        return False
    try:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(1.5)
        conn.connect(str(path))
        conn.sendall(json.dumps({"cmd": cmd, **kwargs}).encode("utf-8"))
        conn.recv(65536)
        conn.close()
        return True
    except OSError:
        return False


def headset_key_action(keyval: int) -> Optional[Tuple[str, str, Dict[str, Any]]]:
    try:
        return _KEY_ACTIONS.get(int(keyval))
    except (TypeError, ValueError):
        return None


def dispatch_headset_key(
    keyval: int,
    debounce_path: Optional[Path] = None,
    sender: Optional[Callable[..., bool]] = None,
    now: Optional[float] = None,
) -> bool:
    """Handle a headset key delivered to the player window.

    Returns True when the key was a media key, including when the
    duplicate of a press that MPRIS already handled is dropped. Other
    keys return False so the window keeps them.
    """
    found = headset_key_action(keyval)
    if found is None:
        return False
    action, command, extra = found
    if not claim_headset_action(action, now=now, path=debounce_path):
        return True
    send = sender or send_player_command
    try:
        send(command, **extra)
    except Exception:
        return True
    return True


class MprisService:
    """Session-bus MPRIS object. The GLib loop runs on a daemon thread."""

    def __init__(self, engine: Any, bus_name: str = BUS_NAME, debounce_path: Optional[Path] = None):
        self.controller = MprisController(engine, debounce_path=debounce_path)
        self.bus_name = bus_name
        self.ready = threading.Event()
        self.error: Optional[BaseException] = None
        self._loop = None
        self._thread: Optional[threading.Thread] = None
        self._claimed_name = bus_name
        self._bus_name = None
        self._bus = None
        self._published = None

    def start(self) -> bool:
        if not sys.platform.startswith("linux"):
            return False
        self._thread = threading.Thread(target=self._run, name="mp-mpris", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        loop = self._loop
        if loop is not None:
            try:
                from gi.repository import GLib
                GLib.idle_add(self._release_and_quit)
            except Exception:
                try:
                    loop.quit()
                except Exception:
                    pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _release_and_quit(self) -> bool:
        # Release the name on the GLib thread while the bus is still up.
        # Dropping it later, after the daemon is gone, raises NoReply.
        published = self._published
        self._published = None
        if published is not None:
            try:
                published.remove_from_connection()
            except Exception:
                pass
        self._bus_name = None
        bus = self._bus
        self._bus = None
        if bus is not None:
            try:
                bus.close()
            except Exception:
                pass
        loop = self._loop
        if loop is not None:
            try:
                loop.quit()
            except Exception:
                pass
        return False

    def _run(self) -> None:
        try:
            import dbus
            import dbus.service
            from dbus.mainloop.glib import DBusGMainLoop
            from gi.repository import GLib
        except Exception as exc:
            self.error = exc
            self.ready.set()
            return
        try:
            DBusGMainLoop(set_as_default=True)
            address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
            if address:
                bus = dbus.bus.BusConnection(address)
            else:
                bus = dbus.SessionBus()
            self._bus = bus
            try:
                # Kept on self. Dropping the BusName releases the well-known name.
                self._bus_name = dbus.service.BusName(self.bus_name, bus, do_not_queue=True)
                self._claimed_name = self.bus_name
            except dbus.exceptions.NameExistsException:
                self._claimed_name = f"{self.bus_name}.instance{os.getpid()}"
                self._bus_name = dbus.service.BusName(self._claimed_name, bus, do_not_queue=True)
            published = _MprisObject(bus, self.controller)
            self._published = published
            self._loop = GLib.MainLoop()
            GLib.timeout_add(200, published.publish)
            published.publish()
            self.ready.set()
            self._loop.run()
        except Exception as exc:
            self.error = exc
            self.ready.set()


def start_mpris_service(
    engine: Any,
    bus_name: str = BUS_NAME,
    debounce_path: Optional[Path] = None,
) -> Optional[MprisService]:
    """Export the engine on the session bus. None when this is not Linux."""
    if not sys.platform.startswith("linux"):
        return None
    service = MprisService(engine, bus_name=bus_name, debounce_path=debounce_path)
    if not service.start():
        return None
    return service


def _dbus_metadata(meta: Dict[str, Any]):
    import dbus
    out = {"mpris:trackid": dbus.ObjectPath(str(meta.get("mpris:trackid") or NO_TRACK))}
    if "mpris:length" in meta:
        out["mpris:length"] = dbus.Int64(int(meta["mpris:length"]))
    if meta.get("xesam:title"):
        out["xesam:title"] = dbus.String(str(meta["xesam:title"]))
    if meta.get("xesam:url"):
        out["xesam:url"] = dbus.String(str(meta["xesam:url"]))
    return dbus.Dictionary(out, signature="sv")


def _player_properties(controller: MprisController):
    import dbus
    snap = controller.snapshot()
    return {
        "PlaybackStatus": dbus.String(snap["PlaybackStatus"]),
        "LoopStatus": dbus.String(snap["LoopStatus"]),
        "Rate": dbus.Double(1.0),
        "MinimumRate": dbus.Double(1.0),
        "MaximumRate": dbus.Double(1.0),
        "Shuffle": dbus.Boolean(snap["Shuffle"]),
        "Metadata": _dbus_metadata(snap["Metadata"]),
        "Volume": dbus.Double(float(snap["Volume"])),
        "Position": dbus.Int64(position_us(getattr(controller.engine, "position_sec", 0.0))),
        "CanGoNext": dbus.Boolean(snap["CanGoNext"]),
        "CanGoPrevious": dbus.Boolean(snap["CanGoPrevious"]),
        "CanPlay": dbus.Boolean(snap["CanPlay"]),
        "CanPause": dbus.Boolean(snap["CanPause"]),
        "CanSeek": dbus.Boolean(snap["CanSeek"]),
        "CanControl": dbus.Boolean(True),
    }


def _root_properties():
    import dbus
    return {
        "CanQuit": dbus.Boolean(False),
        "Fullscreen": dbus.Boolean(False),
        "CanSetFullscreen": dbus.Boolean(False),
        "CanRaise": dbus.Boolean(False),
        "HasTrackList": dbus.Boolean(False),
        "Identity": dbus.String(IDENTITY),
        "DesktopEntry": dbus.String(DESKTOP_ENTRY),
        "SupportedUriSchemes": dbus.Array([dbus.String("file")], signature="s"),
        "SupportedMimeTypes": dbus.Array(
            [dbus.String(item) for item in (
                "audio/flac", "audio/mpeg", "audio/ogg", "audio/wav",
                "audio/x-wav", "audio/mp4", "audio/aac", "audio/x-aiff",
            )],
            signature="s",
        ),
    }


class _MprisObject:
    """Built inside the GLib thread so the class statement can import dbus."""

    def __new__(cls, bus, controller: MprisController):
        import dbus
        import dbus.service

        class Object(dbus.service.Object):
            def __init__(self, connection, owner: MprisController):
                self.owner = owner
                self._seen: Optional[Dict[str, Any]] = None
                super().__init__(connection, OBJECT_PATH)

            @dbus.service.method(PROP_IFACE, in_signature="ss", out_signature="v")
            def Get(self, interface, prop):
                props = self._interface(interface)
                if prop not in props:
                    raise dbus.exceptions.DBusException(
                        "org.freedesktop.DBus.Error.UnknownProperty",
                        f"no property {prop}",
                    )
                return props[prop]

            @dbus.service.method(PROP_IFACE, in_signature="s", out_signature="a{sv}")
            def GetAll(self, interface):
                return dbus.Dictionary(self._interface(interface), signature="sv")

            @dbus.service.method(PROP_IFACE, in_signature="ssv", out_signature="")
            def Set(self, interface, prop, value):
                if interface != PLAYER_IFACE:
                    return
                if prop == "Volume":
                    self.owner.set_volume(float(value))
                elif prop == "Shuffle":
                    self.owner.set_shuffle(bool(value))
                elif prop == "LoopStatus":
                    self.owner.set_loop(str(value))
                self.publish()

            @dbus.service.signal(PROP_IFACE, signature="sa{sv}as")
            def PropertiesChanged(self, interface, changed, invalidated):
                pass

            @dbus.service.signal(PLAYER_IFACE, signature="x")
            def Seeked(self, position):
                pass

            @dbus.service.method(ROOT_IFACE, in_signature="", out_signature="")
            def Raise(self):
                return None

            @dbus.service.method(ROOT_IFACE, in_signature="", out_signature="")
            def Quit(self):
                # CanQuit is false. A headset must not close the player.
                return None

            @dbus.service.method(PLAYER_IFACE, in_signature="", out_signature="")
            def Next(self):
                self.owner.next()
                self.publish()

            @dbus.service.method(PLAYER_IFACE, in_signature="", out_signature="")
            def Previous(self):
                self.owner.previous()
                self.publish()

            @dbus.service.method(PLAYER_IFACE, in_signature="", out_signature="")
            def Pause(self):
                self.owner.pause()
                self.publish()

            @dbus.service.method(PLAYER_IFACE, in_signature="", out_signature="")
            def PlayPause(self):
                self.owner.play_pause()
                self.publish()

            @dbus.service.method(PLAYER_IFACE, in_signature="", out_signature="")
            def Stop(self):
                self.owner.stop()
                self.publish()

            @dbus.service.method(PLAYER_IFACE, in_signature="", out_signature="")
            def Play(self):
                self.owner.play()
                self.publish()

            @dbus.service.method(PLAYER_IFACE, in_signature="x", out_signature="")
            def Seek(self, offset):
                position = self.owner.seek_us(int(offset))
                self.Seeked(dbus.Int64(position))
                self.publish()

            @dbus.service.method(PLAYER_IFACE, in_signature="ox", out_signature="")
            def SetPosition(self, track_id, position):
                updated = self.owner.set_position(str(track_id), int(position))
                if updated is not None:
                    self.Seeked(dbus.Int64(updated))
                    self.publish()

            @dbus.service.method(PLAYER_IFACE, in_signature="s", out_signature="")
            def OpenUri(self, uri):
                # Video URIs are refused inside the controller. Nothing is launched.
                self.owner.open_uri(str(uri))
                self.publish()

            def _interface(self, interface: str) -> Dict[str, Any]:
                if interface == ROOT_IFACE:
                    return _root_properties()
                if interface == PLAYER_IFACE:
                    return _player_properties(self.owner)
                return {}

            def publish(self) -> bool:
                try:
                    snap = self.owner.snapshot()
                    previous = self._seen
                    self._seen = snap
                    if previous is None:
                        self._emit(PLAYER_IFACE, _player_properties(self.owner))
                        self._emit(ROOT_IFACE, _root_properties())
                        return True
                    changed = {}
                    for key, value in snap.items():
                        if previous.get(key) != value:
                            changed[key] = _player_properties(self.owner)[key]
                    if changed:
                        self._emit(PLAYER_IFACE, changed)
                except Exception:
                    pass
                return True

            def _emit(self, interface: str, changed: Dict[str, Any]) -> None:
                import dbus
                self.PropertiesChanged(
                    interface,
                    dbus.Dictionary(changed, signature="sv"),
                    dbus.Array([], signature="s"),
                )

        return Object(bus, controller)
