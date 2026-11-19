#!/usr/bin/env python3
"""MP Liquid Mixer - MIDI DJ Mixer Profiles (S4 and S3), no GTK imports.

The mixer's native Linux path reads evdev axes. This module adds the other way
the hardware works: a Kontrol mixer in MIDI mode appears as a MIDI input port,
and its buttons, faders and encoders can then be mapped to the same mixer
actions the evdev listener uses.

Two controllers are supported, one at a time, selected by
**MIDI DJ MIXER PROFILE A / B** on the mixer's top bar:

    PROFILE A  Kontrol S4   (notes/CCs already used by scripts/s4_midi.py)
    PROFILE B  Kontrol S3   (4-channel layout, CDJ-style transport)

Only a port whose name matches the profile's controller is opened. A generic
MIDI controller (an MPK mini, for example) is never mapped, even when it is the
only device present. Detection repeats while the mixer runs, so plugging the
controller in later starts the map automatically.

Three backends are used, in order:

* **ALSA sequencer** (``aseqdump``) on Linux. Several listeners may share it,
  unlike the rawmidi node, which one process holds exclusively.
* **rawmidi** (``/dev/snd/midiCxDx``) when the sequencer is unavailable.
* **CoreMIDI** on macOS, read through ``ctypes`` so no Python MIDI package has
  to be installed. This is the path used when the mixer runs on a Mac.

Like the native listener, this thread only produces ``(action, value)`` calls;
the GUI thread applies them. A user can override individual entries in
``~/.config/mix-manager/liquid_mixer_midi.json``::

    {
      "profile": "B",
      "overrides": {
        "B": {"notes": {"0": "play:A"}, "cc": {"8": "channel:A"}}
      }
    }
"""
from __future__ import annotations

import ctypes
import ctypes.util
import glob
import json
import os
import re
import select
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

CONFIG_DIR = Path.home() / ".config" / "mix-manager"
LOG_PATH = CONFIG_DIR / "liquid_mixer_midi.log"
MAPPING_PATH = CONFIG_DIR / "liquid_mixer_midi.json"

# The two profiles and the controller each one binds to. The marker strings are
# what the ALSA/OS names the devices, matched case-insensitively.
PROFILES: Dict[str, str] = {"A": "s4", "B": "s3"}
PROFILE_DEVICE_MARKERS: Dict[str, Tuple[str, ...]] = {
    "A": ("kontrol s4", "s4"),
    "B": ("kontrol s3", "s3"),
}
PROFILE_LABELS: Dict[str, str] = {"A": "Kontrol S4", "B": "Kontrol S3"}
DEFAULT_PROFILE = "A"

# Kontrol S4 MIDI-mode notes. The numbers follow the map already used by
# scripts/s4_midi.py; controls the mixer does not implement (hotcues, loop
# recorder play) are deliberately absent rather than guessed.
S4_NOTES: Dict[int, str] = {
    0: "play:A", 1: "play:B",
    36: "play:A", 37: "play:B", 50: "play:A", 51: "play:B",
    6: "sync:A", 7: "sync:B", 42: "sync:A", 43: "sync:B",
    48: "shift:A", 49: "shift:B",
    14: "load", 15: "load", 60: "load", 61: "load",
    16: "browse:-1", 62: "browse:-1",
    17: "browse:+1", 63: "browse:+1",
    32: "record", 76: "record",
}
# CCs. 8/9 are channel A/B volume and 14/15 are deck pitch. Browse is a
# relative encoder; 32/33/40 are the S4's CC-triggered Play buttons.
S4_CC: Dict[int, str] = {
    8: "channel:A", 19: "channel:A",
    9: "channel:B", 22: "channel:B",
    14: "pitch:A", 15: "pitch:B",
    42: "browse", 43: "browse", 80: "browse", 81: "browse", 100: "browse",
    32: "play:A", 33: "play:B", 40: "play:A",
    36: "sync:A", 37: "sync:B",
}

# Kontrol S3 MIDI-mode notes. The S3 is the four-channel unit: Play/Cue per
# deck, Sync/Shift, the two FX buttons, and the browser. Notes that duplicate
# another transport live in the table so either revision of the firmware works.
S3_NOTES: Dict[int, str] = {
    0: "play:A", 1: "play:B", 2: "play:C", 3: "play:D",
    4: "sync:A", 5: "sync:B", 6: "sync:C", 7: "sync:D",
    8: "shift:A", 9: "shift:B", 10: "shift:C", 11: "shift:D",
    12: "load", 13: "load", 14: "load", 15: "load",
    16: "browse:-1", 17: "browse:+1",
    20: "effect:A", 21: "effect:B", 22: "effect:C", 23: "effect:D",
    24: "monitor:A", 25: "monitor:B", 26: "monitor:C", 27: "monitor:D",
    32: "record", 33: "record",
}
# CCs. The S3 sends the four channel faders on 8-11, the crossfader on 12, the
# four tempo faders on 14-17 and the browser encoder on 42/43. The FX depth
# knobs are 20-23.
S3_CC: Dict[int, str] = {
    8: "channel:A", 9: "channel:B", 10: "channel:C", 11: "channel:D",
    12: "crossfader",
    14: "pitch:A", 15: "pitch:B", 16: "pitch:C", 17: "pitch:D",
    20: "filter:A", 21: "filter:B", 22: "filter:C", 23: "filter:D",
    42: "browse", 43: "browse", 80: "browse", 81: "browse",
    24: "master_volume", 25: "monitor_volume",
}

PROFILE_MAPS: Dict[str, Tuple[Dict[int, str], Dict[int, str]]] = {
    "A": (S4_NOTES, S4_CC),
    "B": (S3_NOTES, S3_CC),
}

# Backwards-compatible aliases used by older tests and callers.
DEFAULT_NOTES = S4_NOTES
DEFAULT_CC = S4_CC
DEVICE_MARKER = "kontrol"


def _log(message: str) -> None:
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


def normalize_profile(profile: Optional[str]) -> str:
    name = str(profile or "").strip().upper()
    return name if name in PROFILES else DEFAULT_PROFILE


def device_markers(profile: Optional[str]) -> Tuple[str, ...]:
    return PROFILE_DEVICE_MARKERS.get(normalize_profile(profile), PROFILE_DEVICE_MARKERS[DEFAULT_PROFILE])


def name_matches_profile(name: str, profile: Optional[str]) -> bool:
    """True when an ALSA/OS port name belongs to the profile's controller."""
    text = str(name or "").lower()
    if not text:
        return False
    return any(marker in text for marker in device_markers(profile))


def _payload() -> Dict[str, Any]:
    try:
        payload = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def read_saved_profile() -> str:
    payload = _payload()
    return normalize_profile(payload.get("profile"))


def load_mapping(path: Optional[Path] = None,
                 profile: Optional[str] = None) -> Tuple[Dict[int, str], Dict[int, str]]:
    """The profile's default map plus any user overrides for that profile.

    The profile comes from the caller, from a ``"profile"`` key in the file, or
    (with no file given) from the saved selection; otherwise it is profile A.
    """
    if path is not None:
        target = Path(path)
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            payload = {}
    else:
        payload = _payload()
    if not isinstance(payload, dict):
        payload = {}
    # An explicit file is the whole answer: it must not silently inherit the
    # profile saved for the live mixer, or two callers reading different files
    # would disagree about the map.
    fallback = DEFAULT_PROFILE if path is not None else read_saved_profile()
    chosen = normalize_profile(profile if profile is not None
                               else payload.get("profile") or fallback)
    notes = dict(PROFILE_MAPS[chosen][0])
    cc = dict(PROFILE_MAPS[chosen][1])
    if not payload:
        return notes, cc
    # Overrides are namespaced per profile, so switching profiles cannot apply
    # the other controller's map by accident. A bare "notes"/"cc" block is the
    # historical single-profile form and applies to profile A.
    overrides = payload.get("overrides")
    block: Dict[str, Any] = {}
    if isinstance(overrides, dict) and isinstance(overrides.get(chosen), dict):
        block = overrides[chosen]
    elif chosen == "A" or not isinstance(overrides, dict):
        block = payload
    for source, destination in (("notes", notes), ("cc", cc)):
        values = block.get(source)
        if not isinstance(values, dict):
            continue
        for key, action in values.items():
            try:
                number = int(key)
            except (TypeError, ValueError):
                continue
            if isinstance(action, str) and action.strip():
                destination[number] = action.strip()
    return notes, cc


def save_profile(profile: str) -> str:
    """Remember the selected profile without discarding user overrides."""
    chosen = normalize_profile(profile)
    payload = _payload()
    payload["profile"] = chosen
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = MAPPING_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, MAPPING_PATH)
    except OSError:
        pass
    return chosen


def _device_name(midi_file: Path) -> str:
    try:
        for line in midi_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line:
                return line
    except OSError:
        pass
    return ""


def _rawmidi_path(card: int, device: int, dev_root: Path) -> Path:
    return Path(dev_root) / f"midiC{int(card)}D{int(device)}"


def find_sequencer_ports(profile: Optional[str] = None) -> List[Dict[str, str]]:
    """The profile's controller input ports on ALSA's sequencer, if any.

    The sequencer allows several listeners, unlike the rawmidi node, which one
    process can hold exclusively. That matters when another service or the
    controller's own editor already owns the raw device.
    """
    dump = shutil.which("aseqdump")
    if not dump:
        return []
    try:
        result = subprocess.run([dump, "-l"], capture_output=True, text=True,
                                timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    found: List[Dict[str, str]] = []
    for line in (result.stdout or "").splitlines():
        if not name_matches_profile(line, profile):
            continue
        match = re.search(r"(\d+):(\d+)", line)
        if not match:
            continue
        port = f"{int(match.group(1))}:{int(match.group(2))}"
        rest = line.split(str(match.group(0)), 1)[-1].strip()
        name = re.split(r"\s{2,}", rest)[0].strip() or PROFILE_LABELS[normalize_profile(profile)]
        found.append({"name": name, "path": port, "backend": "sequencer"})
    return found


def find_midi_devices(profile: Optional[str] = None,
                      proc_root: Path | str = "/proc/asound",
                      dev_root: Path | str = "/dev/snd") -> List[Dict[str, str]]:
    """Every readable rawmidi port belonging to the profile's controller."""
    found: List[Dict[str, str]] = []
    seen = set()
    for midi_file in sorted(glob.glob(str(Path(proc_root) / "card*" / "midi*"))):
        path = Path(midi_file)
        name = _device_name(path)
        if not name_matches_profile(name, profile):
            continue
        card_match = re.search(r"card(\d+)", str(path))
        dev_match = re.search(r"midi(\d+)$", path.name)
        if not card_match or not dev_match:
            continue
        raw = _rawmidi_path(int(card_match.group(1)), int(dev_match.group(1)), Path(dev_root))
        if not raw.exists() or str(raw) in seen:
            continue
        seen.add(str(raw))
        found.append({"name": name, "path": str(raw), "proc": str(path)})

    if found:
        return found

    # Fallback for a kernel without the /proc name, or a trimmed container:
    # amidi -l still names the device. Other controllers are skipped here too.
    try:
        result = subprocess.run(["amidi", "-l"], capture_output=True, text=True,
                                timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return found
    for line in (result.stdout or "").splitlines():
        if not name_matches_profile(line, profile):
            continue
        match = re.search(r"hw:(\d+),(\d+)", line)
        if not match:
            continue
        raw = _rawmidi_path(int(match.group(1)), int(match.group(2)), Path(dev_root))
        if raw.exists() and str(raw) not in seen:
            seen.add(str(raw))
            found.append({"name": line.strip(), "path": str(raw), "proc": ""})
    return found


# Historical names, kept for callers written before the profile split.
def find_dj_sequencer_ports() -> List[Dict[str, str]]:
    return find_sequencer_ports("A")


def find_dj_devices(proc_root: Path | str = "/proc/asound",
                         dev_root: Path | str = "/dev/snd") -> List[Dict[str, str]]:
    return find_midi_devices("A", proc_root, dev_root)


def pitch_percent_7bit(value: int) -> float:
    """A 7-bit pitch fader to -20..+20 percent, with a centre detent."""
    value = max(0, min(127, int(value)))
    if 62 <= value <= 66:
        return 0.0
    return max(-20.0, min(20.0, round((value - 64) / 63.0 * 20.0)))


def pitch_percent_14bit(lsb: int, msb: int) -> float:
    value = ((int(msb) & 0x7F) << 7) | (int(lsb) & 0x7F)
    difference = value - 8192
    if abs(difference) < 250:
        return 0.0
    return max(-20.0, min(20.0, round(difference / 8191.0 * 20.0)))


def parse_sequencer_line(line: str) -> Optional[Tuple[int, int, int]]:
    """One ``aseqdump`` event line to ``(status, data1, data2)``."""
    note_on = re.search(r"Note on\s+(\d+),\s*note\s+(\d+),\s*velocity\s+(\d+)", line)
    if note_on:
        channel = int(note_on.group(1))
        return (0x90 | (channel & 0x0F), int(note_on.group(2)), int(note_on.group(3)))
    note_off = re.search(r"Note off\s+(\d+),\s*note\s+(\d+),\s*velocity\s+(\d+)", line)
    if note_off:
        channel = int(note_off.group(1))
        return (0x80 | (channel & 0x0F), int(note_off.group(2)), int(note_off.group(3)))
    control = re.search(r"Control change\s+(\d+),\s*controller\s+(\d+),\s*value\s+(\d+)", line)
    if control:
        channel = int(control.group(1))
        return (0xB0 | (channel & 0x0F), int(control.group(2)), int(control.group(3)))
    bend = re.search(r"Pitch bend\s+(\d+),\s*value\s+(-?\d+)", line)
    if bend:
        channel = int(bend.group(1))
        value = int(bend.group(2))
        if value < 0:
            value += 8192
        value = max(0, min(16383, value))
        return (0xE0 | (channel & 0x0F), value & 0x7F, (value >> 7) & 0x7F)
    return None


# ------------------------------------------------------------------------------
# CoreMIDI (macOS), through ctypes so there is no Python MIDI dependency
# ------------------------------------------------------------------------------
class CoreMidiReader:
    """Read one CoreMIDI source by polling its packet list.

    The mixer's Mac test machine has no ``mido``/``python-rtmidi`` installed, so
    the framework is bound directly. A client and one input port are created
    for the life of the listener; every source whose name matches the profile
    is connected, and each poll drains the packets that arrived since the last
    one. This is the same message set the Linux paths deliver.
    """

    def __init__(self, profile: str = DEFAULT_PROFILE) -> None:
        self.profile = normalize_profile(profile)
        self.client = ctypes.c_uint32(0)
        self.port = ctypes.c_uint32(0)
        self.sources: List[Tuple[int, str]] = []
        self._cm = None
        self._cf = None
        self._connected: List[int] = []

    def _load(self) -> bool:
        if self._cm is not None:
            return True
        try:
            self._cm = ctypes.CDLL("/System/Library/Frameworks/CoreMIDI.framework/CoreMIDI")
            self._cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        except OSError:
            return False
        cm = self._cm
        cm.MIDIGetNumberOfSources.restype = ctypes.c_ulong
        cm.MIDIGetSource.restype = ctypes.c_uint32
        cm.MIDIGetSource.argtypes = [ctypes.c_ulong]
        cm.MIDIObjectGetStringProperty.restype = ctypes.c_int32
        cm.MIDIObjectGetStringProperty.argtypes = [
            ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        # MIDIClientCreate(name, notifyProc, notifyRefCon, *clientRef).
        # The name is a CFStringRef: passing a c_char_p here is the classic
        # ctypes trap and aborts the call with an ArgumentError.
        cm.MIDIClientCreate.restype = ctypes.c_int32
        cm.MIDIClientCreate.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        # MIDIInputPortCreate(client, name, readProc, connRefCon, *portRef).
        cm.MIDIInputPortCreate.restype = ctypes.c_int32
        cm.MIDIInputPortCreate.argtypes = [
            ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32)]
        cm.MIDIPortConnectSource.restype = ctypes.c_int32
        cm.MIDIPortConnectSource.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        cm.MIDIPortDisconnectSource.restype = ctypes.c_int32
        cm.MIDIPortDisconnectSource.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        cm.MIDIPortDispose.restype = ctypes.c_int32
        cm.MIDIPortDispose.argtypes = [ctypes.c_uint32]
        cm.MIDIClientDispose.restype = ctypes.c_int32
        cm.MIDIClientDispose.argtypes = [ctypes.c_uint32]
        cm.MIDIPacketListInit.restype = ctypes.c_void_p
        cm.MIDIPacketListInit.argtypes = [ctypes.c_void_p]
        cm.MIDIPacketListAdd.restype = ctypes.c_void_p
        cm.MIDIPacketListAdd.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_double,
            ctypes.c_ulong, ctypes.c_char_p]
        cf = self._cf
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFStringGetCString.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        try:
            self._name_key = ctypes.c_void_p.in_dll(cm, "kMIDIPropertyName")
        except ValueError:
            return False
        return True

    def _cfstring(self, text: str) -> int:
        """A CFStringRef for ``text`` (0 when the framework cannot make one)."""
        try:
            cf = self._cf
            cf.CFStringCreateWithCString.restype = ctypes.c_void_p
            cf.CFStringCreateWithCString.argtypes = [
                ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
            return int(cf.CFStringCreateWithCString(None, text.encode(), 0x08000100) or 0)
        except Exception:
            return 0

    def _source_name(self, source: int) -> str:
        value = ctypes.c_void_p()
        status = self._cm.MIDIObjectGetStringProperty(
            ctypes.c_uint32(source), self._name_key, ctypes.byref(value))
        if status != 0 or not value:
            return ""
        buffer = ctypes.create_string_buffer(256)
        if not self._cf.CFStringGetCString(value, buffer, 256, 0x08000100):
            return ""
        try:
            self._cf.CFRelease(value)
        except Exception:
            pass
        return buffer.value.decode(errors="replace")

    def open(self) -> bool:
        """Create the client and connect every matching source."""
        if not sys.platform.startswith("darwin") or not self._load():
            return False
        if not self.client.value:
            name = self._cfstring("MP Liquid Mixer")
            status = self._cm.MIDIClientCreate(
                ctypes.c_void_p(name), None, None, ctypes.byref(self.client))
            if name:
                try:
                    self._cf.CFRelease(name)
                except Exception:
                    pass
            if status != 0:
                self.client = ctypes.c_uint32(0)
                return False
        if not self.port.value:
            status = self._cm.MIDIInputPortCreate(
                self.client, None, None, None, ctypes.byref(self.port))
            if status != 0:
                self.port = ctypes.c_uint32(0)
                return False
        self.refresh_sources()
        return bool(self.sources)

    def refresh_sources(self) -> None:
        self.sources = []
        count = int(self._cm.MIDIGetNumberOfSources())
        for index in range(count):
            source = int(self._cm.MIDIGetSource(index))
            name = self._source_name(source)
            if name_matches_profile(name, self.profile):
                self.sources.append((source, name))
                if source not in self._connected:
                    if self._cm.MIDIPortConnectSource(self.port, source, None) == 0:
                        self._connected.append(source)

    def drain(self) -> List[Tuple[int, int, int]]:
        """Return the messages the read callback has collected (and clear them)."""
        with self._lock:
            events, self._pending = self._pending, []
        return events

    def close(self) -> None:
        try:
            if self._cm is not None:
                for source in list(self._connected):
                    self._cm.MIDIPortDisconnectSource(self.port, source)
                if self.port.value:
                    self._cm.MIDIPortDispose(self.port)
                if self.client.value:
                    self._cm.MIDIClientDispose(self.client)
        except Exception:
            pass
        self._connected = []
        self.client = ctypes.c_uint32(0)
        self.port = ctypes.c_uint32(0)


# MIDIReadProc is a C callback: it receives a packet list for every batch of
# messages. Keeping one module-level trampoline (a ctypes callback that stays
# referenced for the life of the process) is what makes CoreMIDI delivery work
# without a run loop.
_READ_PROC_TYPE = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)


class CoreMidiCallbackReader(CoreMidiReader):
    """CoreMIDI reader that installs a real MIDIReadProc callback."""

    def __init__(self, profile: str = DEFAULT_PROFILE,
                 on_message: Optional[Callable[[int, int, int], None]] = None) -> None:
        super().__init__(profile)
        self.on_message = on_message
        self._pending: List[Tuple[int, int, int]] = []
        self._lock = threading.Lock()
        self._proc = None

    def _packet_bytes(self, packet_list: int) -> List[Tuple[int, int, int]]:
        """Walk a MIDIPacketList and split each packet into channel messages."""
        events: List[Tuple[int, int, int]] = []
        try:
            # MIDIPacketList: UInt32 numPackets, then MIDIPacket packets whose
            # layout is { UInt64 timeStamp; UInt16 length; Byte data[256]; }.
            count = ctypes.cast(packet_list, ctypes.POINTER(ctypes.c_uint32))[0]
            offset = 4
            for _ in range(int(count)):
                packet = ctypes.cast(packet_list + offset, ctypes.POINTER(ctypes.c_ubyte))
                # timeStamp is 8 bytes, length is 2 bytes, then the data.
                length = packet[8] | (packet[9] << 8)
                data = bytes(packet[10:10 + length])
                events.extend(split_midi_bytes(data))
                # The next packet is aligned to 4 bytes after its data.
                step = 10 + length
                offset += (step + 3) & ~3
        except Exception:
            return events
        return events

    def _read_proc(self, packet_list, _ref_con, _conn_ref) -> None:
        events = self._packet_bytes(packet_list)
        if not events:
            return
        with self._lock:
            self._pending.extend(events)
        if self.on_message is not None:
            try:
                for status, data1, data2 in events:
                    self.on_message(status, data1, data2)
            except Exception:
                pass

    def open(self) -> bool:
        if not super().open():
            return False
        try:
            self._proc = _READ_PROC_TYPE(self._read_proc)
            # Replace the port created without a callback with one that has it.
            if self.port.value:
                self._cm.MIDIPortDispose(self.port)
                self.port = ctypes.c_uint32(0)
            status = self._cm.MIDIInputPortCreate(
                self.client, None, self._proc, None, ctypes.byref(self.port))
            if status != 0:
                return False
            self._connected = []
            self.refresh_sources()
        except Exception:
            return False
        return bool(self.sources)

    def drain(self) -> List[Tuple[int, int, int]]:
        with self._lock:
            events, self._pending = self._pending, []
        return events


class KontrolHidReader:
    """Read a Kontrol surface over IOKit HID, for macOS.

    macOS publishes the four-channel Kontrol as a vendor-defined HID device with
    no CoreMIDI port, so the MIDI map cannot reach it there. The report
    descriptor declares two input collections:

        Report 1  the button surface, one bit per control
        Report 2  every analog control, seven 7-bit fields and twenty-four
                  16-bit fields, all 0-4095

    A background thread runs a CFRunLoop, registers the input report callback
    and posts the counter handshake the surface needs before it streams. The
    callback translates changed fields into the same ``(action, value)`` calls
    the MIDI path produces, so the mixer does not know which transport a
    controller is using.
    """

    VENDOR_ID = 0x17CC
    PRODUCT_IDS = {"B": 0x1900, "A": 0x1700}

    def __init__(self, profile: str = DEFAULT_PROFILE,
                 on_message: Optional[Callable[[int, int, int], None]] = None,
                 on_hid: Optional[Callable[[int, int, int], None]] = None) -> None:
        self.profile = normalize_profile(profile)
        self.on_message = on_message
        self.on_hid = on_hid
        self.device_name = ""
        self.sources: List[Tuple[int, str]] = []
        self._iok = None
        self._cf = None
        self._mgr = None
        self._dev = None
        self._runloop = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._callback = None
        self._buffer = None
        self._last: Dict[int, int] = {}
        self._lock = threading.Lock()
        self._ready = threading.Event()

    def _load(self) -> bool:
        if self._iok is not None:
            return True
        try:
            self._iok = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
            self._cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        except OSError:
            return False
        iok, cf = self._iok, self._cf
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFNumberCreate.restype = ctypes.c_void_p
        cf.CFNumberCreate.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        cf.CFDictionaryCreate.restype = ctypes.c_void_p
        cf.CFDictionaryCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                          ctypes.POINTER(ctypes.c_void_p), ctypes.c_long,
                                          ctypes.c_void_p, ctypes.c_void_p]
        cf.CFSetGetCount.restype = ctypes.c_long
        cf.CFSetGetCount.argtypes = [ctypes.c_void_p]
        cf.CFSetGetValues.restype = None
        cf.CFSetGetValues.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        cf.CFRunLoopGetCurrent.restype = ctypes.c_void_p
        cf.CFRunLoopRun.restype = None
        cf.CFRunLoopRun.argtypes = []
        cf.CFRunLoopStop.restype = None
        cf.CFRunLoopStop.argtypes = [ctypes.c_void_p]
        iok.IOHIDManagerCreate.restype = ctypes.c_void_p
        iok.IOHIDManagerCreate.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        iok.IOHIDManagerSetDeviceMatching.restype = None
        iok.IOHIDManagerSetDeviceMatching.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        iok.IOHIDManagerOpen.restype = ctypes.c_int
        iok.IOHIDManagerOpen.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        iok.IOHIDManagerCopyDevices.restype = ctypes.c_void_p
        iok.IOHIDManagerCopyDevices.argtypes = [ctypes.c_void_p]
        iok.IOHIDManagerScheduleWithRunLoop.restype = None
        iok.IOHIDManagerScheduleWithRunLoop.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        iok.IOHIDDeviceOpen.restype = ctypes.c_int
        iok.IOHIDDeviceOpen.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        iok.IOHIDDeviceScheduleWithRunLoop.restype = None
        iok.IOHIDDeviceScheduleWithRunLoop.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        iok.IOHIDDeviceUnscheduleFromRunLoop.restype = None
        iok.IOHIDDeviceUnscheduleFromRunLoop.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        iok.IOHIDDeviceRegisterInputReportCallback.restype = None
        iok.IOHIDDeviceRegisterInputReportCallback.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint8), ctypes.c_long,
            ctypes.c_void_p, ctypes.c_void_p]
        iok.IOHIDDeviceSetReport.restype = ctypes.c_int
        iok.IOHIDDeviceSetReport.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long,
                                             ctypes.POINTER(ctypes.c_uint8), ctypes.c_long]
        iok.IOHIDDeviceGetProperty.restype = ctypes.c_void_p
        iok.IOHIDDeviceGetProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        return True

    def _cfstring(self, text: str) -> int:
        try:
            return int(self._cf.CFStringCreateWithCString(
                None, text.encode(), 0x08000100) or 0)
        except Exception:
            return 0

    def _cfnumber(self, value: int) -> int:
        try:
            raw = ctypes.c_int32(int(value))
            return int(self._cf.CFNumberCreate(None, 4, ctypes.byref(raw)) or 0)
        except Exception:
            return 0

    def _property(self, key: str) -> str:
        value = self._iok.IOHIDDeviceGetProperty(self._dev, ctypes.c_void_p(self._cfstring(key)))
        if not value:
            return ""
        buffer = ctypes.create_string_buffer(256)
        if not self._cf.CFStringGetCString(value, buffer, 256, 0x08000100):
            return ""
        try:
            self._cf.CFRelease(value)
        except Exception:
            pass
        return buffer.value.decode(errors="replace")

    def _matching_dict(self) -> int:
        keys = (ctypes.c_void_p * 2)(self._cfstring("VendorID"), self._cfstring("ProductID"))
        product = self.PRODUCT_IDS.get(self.profile, self.PRODUCT_IDS["A"])
        values = (ctypes.c_void_p * 2)(self._cfnumber(self.VENDOR_ID), self._cfnumber(product))
        return int(self._cf.CFDictionaryCreate(
            None, keys, values, 2, None, None) or 0)

    def _first_device(self):
        manager = self._iok.IOHIDManagerCreate(None, 0)
        if not manager:
            return None
        self._iok.IOHIDManagerSetDeviceMatching(manager, ctypes.c_void_p(self._matching_dict()))
        if self._iok.IOHIDManagerOpen(manager, 0) != 0:
            return None
        devices = self._iok.IOHIDManagerCopyDevices(manager)
        if not devices:
            return None
        count = int(self._cf.CFSetGetCount(devices))
        if count <= 0:
            return None
        array = (ctypes.c_void_p * count)()
        self._cf.CFSetGetValues(devices, array)
        self._mgr = manager
        return array[0]

    def _on_report(self, _ref, _result, _sender, _rtype, report_id, report, length) -> None:
        """Translate one report and publish only what actually changed.

        The surface streams its full state continuously, so without change
        detection a held Play button fires every few milliseconds and a
        resting fader floods the mixer with identical values. Buttons are
        edge-triggered (a press is one event; shift also reports its release)
        and analog fields are dead-banded.
        """
        raw = bytes(report[i] for i in range(int(length)))
        # IOKit hands the report id back as the first byte; the field offsets
        # below are relative to the data that follows it.
        rid = int(report_id)
        if raw and raw[0] == rid:
            raw = raw[1:]
        for action, value in decode_hid_report(rid, raw, self.profile):
            previous = self._last.get(action)
            if action.startswith("shift:"):
                if previous == value:
                    continue
            elif previous is not None:
                if action.startswith(("channel:", "filter:", "pitch:", "crossfader",
                                      "master_volume", "monitor_volume",
                                      "trim:", "eq:")):
                    if abs(float(previous) - float(value)) < 0.004:
                        continue
                elif value and previous:
                    continue
            self._last[action] = value
            if self.on_hid is not None:
                try:
                    self.on_hid(action, value)
                except Exception:
                    pass
            if self.on_message is not None:
                try:
                    self.on_message(action, value)
                except Exception:
                    pass

    def open(self) -> bool:
        """Start the HID reader thread; True once it owns the device.

        IOKit ties a manager, a device and the run loop it is scheduled on to
        one thread. Creating the manager here and scheduling it from the pump
        thread aborts the process, so the whole sequence (matching, open,
        schedule, register, run) happens on the reader thread and this call
        only waits for its verdict.
        """
        if not sys.platform.startswith("darwin"):
            return False
        self._running = True
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._pump, name="mixer-hid", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)
        return bool(self._runloop) and self._running

    def _pump(self) -> None:
        try:
            if not self._load():
                return
            device = self._first_device()
            if not device:
                return
            self._dev = device
            self._callback = ctypes.CFUNCTYPE(
                None, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_uint8),
                ctypes.c_long)(self._on_report)
            self._buffer = (ctypes.c_uint8 * 64)()
            self._iok.IOHIDDeviceOpen(device, 0)
            # The surface streams nothing until the host writes its zeroed
            # 0xD0 feature report: that is the documented "put the controls in
            # their normal state" handshake. Skip it and every read stays
            # silent until another program initialises the device.
            try:
                feature = (ctypes.c_uint8 * 83)()
                self._iok.IOHIDDeviceSetReport(device, 2, 0xD0, feature, 83)
            except Exception:
                pass
            self._iok.IOHIDDeviceRegisterInputReportCallback(
                device, self._buffer, 64, self._callback, None)
            runloop = self._cf.CFRunLoopGetCurrent()
            mode = ctypes.c_void_p(self._cfstring("kCFRunLoopDefaultMode"))
            self._iok.IOHIDManagerScheduleWithRunLoop(self._mgr, runloop, mode)
            self._iok.IOHIDDeviceScheduleWithRunLoop(device, runloop, mode)
            # The IOKit objects belong to this thread; reading a property with
            # CFStringGetCString from a second thread aborts the process. Use
            # the profile's own label, which names the same controller.
            self.device_name = PROFILE_LABELS[self.profile]
            self._runloop = runloop
            self._ready.set()
            self._cf.CFRunLoopRun()
        except Exception as exc:
            _log(f"HID run loop failed: {exc!r}")
        finally:
            self._runloop = None
            self._ready.set()

    def close(self) -> None:
        self._running = False
        runloop, self._runloop = self._runloop, None
        if runloop and self._cf is not None:
            try:
                self._cf.CFRunLoopStop(ctypes.c_void_p(runloop))
            except Exception:
                pass
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        self._dev = None
        self._mgr = None
        self._callback = None
        self._buffer = None


def _u16(payload: bytes, offset: int) -> Optional[int]:
    """Little-endian 12-bit field at ``offset``, or None when it is absent."""
    if offset + 1 >= len(payload):
        return None
    return payload[offset] | (payload[offset + 1] << 8)


def _unit(raw: int) -> float:
    return max(0.0, min(1.0, raw / 4095.0))


def _eq_db(raw: int) -> float:
    """A 12-bit centre-detented EQ knob to -12..+12 dB (2047 = flat)."""
    return round(max(-12.0, min(12.0, (raw / 4095.0) * 24.0 - 12.0)), 1)


def _pitch_percent(raw: int) -> float:
    percent = (_unit(raw) - 0.5) * 40.0
    return 0.0 if abs(percent) < 0.4 else round(percent, 1)


def decode_hid_report(report_id: int, payload: bytes,
                      profile: str = DEFAULT_PROFILE) -> List[Tuple[str, float]]:
    """Translate one Kontrol HID input report into mixer actions.

    The byte offsets are the ones the surface's own report descriptor and the
    widely used Mixxx HID script agree on: report 1 is a bitmap of buttons
    (``byte, mask`` per control) and report 2 is 16-bit analog data (a 12-bit
    value little-endian at each offset). Only the fields the mixer implements
    are claimed; an unknown report or a short payload is ignored rather than
    guessed at.
    """
    profile = normalize_profile(profile)
    actions: List[Tuple[str, float]] = []
    # Tolerate a payload that still starts with its own report id.
    if payload and payload[0] == report_id and report_id in (1, 2):
        payload = payload[1:]
    if report_id == 1:
        return decode_hid_buttons(payload, profile)
    if report_id != 2:
        return actions
    for offset, action, kind in HID_ANALOG.get(profile, ()):
        raw = _u16(payload, offset)
        if raw is None or not action:
            continue
        if kind == "unit":
            actions.append((action, _unit(raw)))
        elif kind == "eq":
            actions.append((action, _eq_db(raw)))
        elif kind == "pitch":
            actions.append((action, _pitch_percent(raw)))
    return actions


def decode_hid_buttons(payload: bytes, profile: str = DEFAULT_PROFILE) -> List[Tuple[str, float]]:
    """Report 1's ``byte, mask`` button table to pressed/released actions."""
    actions: List[Tuple[str, float]] = []
    for offset, mask, action in HID_BUTTONS.get(normalize_profile(profile), ()):
        if not action or offset >= len(payload):
            continue
        pressed = bool(payload[offset] & mask)
        if action.startswith("shift:") or action.startswith("monitor:") \
                or action.startswith("effect-toggle:") or action.startswith("effect-slot:"):
            actions.append((action, 1.0 if pressed else 0.0))
        elif pressed:
            actions.append((action, 1.0))
    return actions


# Report 2: (payload byte offset, action, unit). Offsets are the descriptor's
# 16-bit fields, in the order the surface sends them.
HID_ANALOG: Dict[str, Tuple[Tuple[int, str, str], ...]] = {
    "A": (),
    "B": (
        # Offsets are relative to the data after the report id byte (the
        # surface's own tables count from the packet start, so each is one
        # lower here).
        # Tempo faders (left deck, then right deck).
        (0x00, "pitch:A", "pitch"),
        (0x0C, "pitch:B", "pitch"),
        # Channel faders. The physical order is C, A, B, D.
        (0x02, "channel:C", "unit"),
        (0x04, "channel:A", "unit"),
        (0x06, "channel:B", "unit"),
        (0x08, "channel:D", "unit"),
        (0x0A, "crossfader", "unit"),
        # Gain (pregain) knobs, centre-detented at unity.
        (0x0E, "trim:C", "unit"),
        (0x10, "trim:A", "unit"),
        (0x12, "trim:B", "unit"),
        (0x14, "trim:D", "unit"),
        # The master and headphone knobs on this surface also drive the unit's
        # own sound card, and they read as zero when the host has not set them,
        # so they are deliberately left unmapped: claiming them would pin the
        # mixer's master trim at silence.
        (0x16, "", "unit"),
        (0x1A, "", "unit"),
        # Three-band EQ, one column per channel. Offsets are the surface's
        # own order: parameter3 (high), parameter2 (mid), parameter1 (low).
        (0x1E, "eq:C:high", "eq"), (0x20, "eq:C:mid", "eq"), (0x22, "eq:C:low", "eq"),
        (0x24, "eq:A:high", "eq"), (0x26, "eq:A:mid", "eq"), (0x28, "eq:A:low", "eq"),
        (0x2A, "eq:B:high", "eq"), (0x2C, "eq:B:mid", "eq"), (0x2E, "eq:B:low", "eq"),
        (0x30, "eq:D:high", "eq"), (0x32, "eq:D:mid", "eq"), (0x34, "eq:D:low", "eq"),
        # The FX knob directly under each channel's EQ stack: this is the
        # filter knob on the mixer and the depth of the selected effect.
        (0x36, "filter:C", "unit"),
        (0x38, "filter:A", "unit"),
        (0x3A, "filter:B", "unit"),
        (0x3C, "filter:D", "unit"),
    ),
}

# Report 1: (payload byte offset, bit mask, action). The channel faders own
# the volume, the FX knobs own the filter; these are the buttons around them.
HID_BUTTONS: Dict[str, Tuple[Tuple[int, int, str], ...]] = {
    "A": (),
    "B": (
        # Offsets are payload indices, i.e. the report id is not included.
        # Left physical deck: Shift / Cue / Play / Sync.
        (0x00, 0x01, "shift:A"),
        (0x01, 0x80, ""),            # cue
        (0x02, 0x01, "play:A"),
        (0x01, 0x08, "sync:A"),
        # Right physical deck.
        (0x03, 0x02, "shift:B"),
        (0x05, 0x01, ""),            # cue
        (0x05, 0x02, "play:B"),
        (0x04, 0x10, "sync:B"),
        # Deck switches: the left pair chooses A/C, the right pair B/D.
        (0x01, 0x02, "deck:A"),
        (0x01, 0x04, "deck:C"),
        (0x04, 0x04, "deck:B"),
        (0x04, 0x08, "deck:D"),
        # Headphone cue, one per channel.
        (0x07, 0x01, "monitor:A"),
        (0x07, 0x02, "monitor:B"),
        (0x06, 0x80, "monitor:C"),
        (0x07, 0x04, "monitor:D"),
        # FX enable, one per channel.
        (0x06, 0x10, "effect-toggle:A"),
        (0x06, 0x20, "effect-toggle:B"),
        (0x06, 0x08, "effect-toggle:C"),
        (0x06, 0x40, "effect-toggle:D"),
        # FX select: the four numbered squares, then the filter slot.
        (0x07, 0x08, "effect-slot:1"),
        (0x07, 0x10, "effect-slot:2"),
        (0x07, 0x20, "effect-slot:3"),
        (0x07, 0x40, "effect-slot:4"),
        (0x07, 0x80, ""),            # filter slot
        # Load buttons, left and right.
        (0x08, 0x01, "load"),
        (0x08, 0x08, "load"),
    ),
}


def split_midi_bytes(data: bytes) -> List[Tuple[int, int, int]]:
    """Split a raw MIDI byte stream into complete channel messages."""
    events: List[Tuple[int, int, int]] = []
    status = 0
    collected: List[int] = []
    for raw in data:
        byte = int(raw)
        if byte >= 0xF8:
            continue
        if byte >= 0x80:
            if byte >= 0xF0:
                status = 0
                collected = []
                continue
            status = byte
            collected = []
            continue
        if not status:
            continue
        collected.append(byte)
        kind = status & 0xF0
        needed = 1 if kind in (0xC0, 0xD0) else 2
        if len(collected) < needed:
            continue
        data1 = collected[0]
        data2 = collected[1] if needed == 2 else 0
        events.append((status, data1, data2))
        collected = []
    return events


class MixerMidiListener:
    """Read the selected controller's MIDI port and publish mixer actions.

    One listener is alive for the whole session. ``set_profile`` swaps the map
    and re-opens the port against the other controller without rebuilding the
    window, which is what the PROFILE A/B button on the mixer drives.
    """

    def __init__(self, on_change: Optional[Callable[[str, float], None]] = None,
                 mapping_path: Optional[Path] = None,
                 profile: str = DEFAULT_PROFILE) -> None:
        self.on_change = on_change
        self.mapping_path = mapping_path
        self._profile = normalize_profile(profile)
        self.notes: Dict[int, str] = {}
        self.cc: Dict[int, str] = {}
        self._load_map()
        self.status = "waiting for MIDI DJ MIXER"
        self.device_name = ""
        self.device_path = ""
        self.last_message = ""
        self.last_event = 0.0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._fd: Optional[int] = None
        self._seq_proc: Optional[subprocess.Popen] = None
        self._status_byte = 0
        self._data: List[int] = []
        self._core = None

    # -- profile ----------------------------------------------------------
    @property
    def profile(self) -> str:
        return self._profile

    def _load_map(self) -> None:
        self.notes, self.cc = load_mapping(self.mapping_path, self._profile)

    def set_profile(self, profile: str) -> str:
        chosen = normalize_profile(profile)
        if chosen == self._profile:
            return self._profile
        self._profile = chosen
        self._load_map()
        save_profile(chosen)
        self._teardown_port()
        self.device_name = ""
        self.device_path = ""
        self.status = f"switching to {PROFILE_LABELS[chosen]}"
        _log(f"profile -> {chosen} ({PROFILE_LABELS[chosen]})")
        return self._profile

    @property
    def connected(self) -> bool:
        return self.status.startswith("mapped")

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="mixer-midi", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._teardown_port()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _teardown_port(self) -> None:
        proc, self._seq_proc = self._seq_proc, None
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass
        core, self._core = self._core, None
        if core is not None:
            try:
                core.close()
            except Exception:
                pass
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def _run(self) -> None:
        backoff = 1.0
        while self._running:
            profile = self._profile
            if sys.platform.startswith("darwin"):
                self._run_coremidi(profile)
                if self._running:
                    time.sleep(1.0)
                continue
            ports = find_sequencer_ports(profile)
            if ports:
                if not self._running:
                    break
                self._run_sequencer(ports[0])
                if self._running:
                    time.sleep(1.0)
                continue
            devices = find_midi_devices(profile)
            if not devices:
                self.status = f"waiting for {PROFILE_LABELS[profile]} MIDI"
                self.device_name = ""
                self.device_path = ""
                time.sleep(backoff)
                backoff = min(backoff * 1.4, 8.0)
                continue
            if not self._running:
                break
            self._run_rawmidi(devices[0])
            if self._running:
                time.sleep(1.0)
            backoff = 1.0

    def _run_coremidi(self, profile: str) -> None:
        # The four-channel Kontrol publishes no CoreMIDI port on macOS: it is a
        # vendor-defined HID surface. Try MIDI first (so a controller that does
        # expose it keeps the shared map), then fall back to HID.
        # A Mac with a Kontrol surface is handled over HID straight away: the
        # four-channel unit publishes no CoreMIDI port, and creating a client
        # for a port that will never appear is wasted work (and a run-loop
        # owner that has to be torn down again).
        if self._hid_available(profile):
            self._run_hid(profile)
            return
        reader = CoreMidiCallbackReader(profile, on_message=self._dispatch)
        if not reader.open():
            self._run_hid(profile)
            return
        self._core = reader
        names = ", ".join(name for _src, name in reader.sources)
        self.device_name = names or PROFILE_LABELS[profile]
        self.device_path = "coremidi"
        self.status = f"mapped {self.device_name} (coremidi)"
        self.last_message = "mapped"
        _log(f"mapped {self.device_name} over CoreMIDI ({profile})")
        while self._running and self._profile == profile:
            time.sleep(0.2)
            try:
                reader.refresh_sources()
            except Exception:
                pass
        reader.close()
        self._core = None

    def _hid_available(self, profile: str) -> bool:
        """True when the IOKit HID device for this profile is present."""
        try:
            if not sys.platform.startswith("darwin"):
                return False
            probe = KontrolHidReader(profile)
            return bool(probe._load() and probe._first_device())
        except Exception:
            return False

    def _run_hid(self, profile: str) -> None:
        """Read the Kontrol surface directly over IOKit HID (macOS only)."""
        reader = KontrolHidReader(profile, on_message=self._dispatch_hid)
        if not reader.open():
            self.status = f"waiting for {PROFILE_LABELS[profile]} (MIDI/HID)"
            time.sleep(1.0)
            return
        self._core = reader
        self.device_name = reader.device_name or PROFILE_LABELS[profile]
        self.device_path = "hid"
        self.status = f"mapped {self.device_name} (hid)"
        self.last_message = "mapped"
        _log(f"mapped {self.device_name} over HID ({profile})")
        while self._running and self._profile == profile:
            time.sleep(0.2)
        reader.close()
        self._core = None

    def _dispatch_hid(self, action: str, value: float) -> None:
        """Publish one HID-derived action (already translated by the reader)."""
        if action.startswith("filter:"):
            self._emit(action, value, "hid filter")
            return
        if action.startswith("pitch:"):
            self._emit(action, value, "hid pitch")
            return
        if action.startswith(("channel:", "monitor:", "trim:", "eq:")):
            self._emit(action, value, "hid fader")
            return
        if action in ("crossfader", "master_volume", "monitor_volume"):
            self._emit(action, value, "hid")
            return
        self._emit(action, value, "hid button")

    def _run_sequencer(self, port: Dict[str, str]) -> None:
        """Read the ALSA sequencer port; multiple listeners may share it."""
        dump = shutil.which("aseqdump")
        if not dump:
            return
        command = [dump, "-p", port["path"]]
        line_buffer = shutil.which("stdbuf")
        if line_buffer:
            command = [line_buffer, "-oL"] + command
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except OSError as exc:
            self.status = f"cannot open MIDI sequencer: {exc}"
            return
        if not self._running:
            try:
                proc.terminate()
            except OSError:
                pass
            return
        time.sleep(0.15)
        if proc.poll() is not None:
            self.status = "the MIDI port is held by another service"
            try:
                if proc.stdout is not None:
                    proc.stdout.close()
            except OSError:
                pass
            return
        self._seq_proc = proc
        self.device_name = port["name"]
        self.device_path = port["path"]
        self.status = f"mapped {port['name']} {port['path']} (sequencer)"
        self.last_message = "mapped"
        _log(f"mapped {port['name']} on sequencer {port['path']}")
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    if not self._running:
                        break
                    event = parse_sequencer_line(line)
                    if event is not None:
                        self._dispatch(*event)
        except Exception as exc:
            _log(f"MIDI sequencer reader error: {exc!r}")
        finally:
            self._seq_proc = None
            try:
                proc.terminate()
                proc.wait(timeout=2.0)
            except (OSError, subprocess.SubprocessError):
                try:
                    proc.kill()
                except OSError:
                    pass

    def _run_rawmidi(self, device: Dict[str, str]) -> None:
        try:
            fd = os.open(device["path"], os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            if getattr(exc, "errno", None) == 16:  # EBUSY
                self.status = "the MIDI port is held by another service"
            else:
                self.status = f"cannot open MIDI: {exc}"
            return
        self._fd = fd
        self.device_name = device["name"]
        self.device_path = device["path"]
        self.status = f"mapped {device['name']} {device['path']}"
        self.last_message = "mapped"
        self._status_byte = 0
        self._data = []
        _log(f"mapped {device['name']} on {device['path']}")
        try:
            while self._running:
                ready, _, _ = select.select([fd], [], [], 0.25)
                if not ready:
                    continue
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                for status, data1, data2 in self._parse(chunk):
                    self._dispatch(status, data1, data2)
        except OSError as exc:
            _log(f"MIDI read error: {exc}")
        except Exception as exc:
            _log(f"MIDI reader error: {exc!r}")
        finally:
            self._fd = None
            try:
                os.close(fd)
            except OSError:
                pass

    def _parse(self, chunk: bytes) -> Iterable[Tuple[int, int, int]]:
        """Split an ALSA MIDI byte stream into complete channel messages."""
        events: List[Tuple[int, int, int]] = []
        for byte in chunk:
            byte = int(byte)
            if byte >= 0xF8:  # realtime clock; no data bytes
                continue
            if byte >= 0x80:
                if byte >= 0xF0:  # system common; not a control message
                    self._status_byte = 0
                    self._data = []
                    continue
                self._status_byte = byte
                self._data = []
                continue
            if not self._status_byte:
                continue
            self._data.append(byte)
            kind = self._status_byte & 0xF0
            needed = 1 if kind in (0xC0, 0xD0) else 2
            if len(self._data) < needed:
                continue
            data1 = self._data[0]
            data2 = self._data[1] if needed == 2 else 0
            events.append((self._status_byte, data1, data2))
            self._data = []
        return events

    def _dispatch(self, status: int, data1: int, data2: int) -> None:
        kind = status & 0xF0
        channel = (status & 0x0F) + 1

        if kind == 0xE0:
            action = "pitch:A" if channel == 1 else ("pitch:B" if channel == 2 else "")
            if action:
                self._emit(action, pitch_percent_14bit(data1, data2), "pitch bend")
            return

        if kind == 0xB0:
            action = self.cc.get(int(data1))
            if not action:
                return
            value = self._cc_value(action, data2)
            if value is not None:
                self._emit(action, value, f"CC {data1}={data2}")
            return

        if kind not in (0x90, 0x80):
            return

        note = int(data1)
        pressed = kind == 0x90 and int(data2) > 0
        action = self.notes.get(note)
        if not action:
            return
        if action.startswith("shift:"):
            self._emit(action, 1.0 if pressed else 0.0, f"note {note}")
            return
        if not pressed:
            return
        if action.startswith("browse:"):
            try:
                direction = int(action.split(":", 1)[1])
            except ValueError:
                return
            self._emit("browse", float(direction), f"note {note}")
            return
        self._emit(action, 1.0, f"note {note}")

    def _cc_value(self, action: str, value: int) -> Optional[float]:
        """Scale a 7-bit CC to the action's unit; None means "ignore"."""
        value = max(0, min(127, int(value)))
        if action.startswith(("channel:", "filter:", "master_volume", "monitor_volume")):
            return value / 127.0
        if action.startswith("pitch:"):
            return pitch_percent_7bit(value)
        if action == "crossfader":
            return value / 127.0
        if action == "browse":
            if value == 0:
                return None
            # Relative encoders send either 1/127 or 65/63.
            return 1.0 if value > 64 or value in (1, 65) else -1.0
        if value > 0:
            return 1.0
        return None

    def _emit(self, action: str, value: float, source: str) -> None:
        self.last_event = time.time()
        self.last_message = f"{source} -> {action}"
        if self.on_change is None:
            return
        try:
            self.on_change(action, float(value))
        except Exception as exc:
            _log(f"MIDI callback failed for {action}: {exc!r}")


# Historical name: the mixer imported this class before the profile split.
DJMidiListener = MixerMidiListener
