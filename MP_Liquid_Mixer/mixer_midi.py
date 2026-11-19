#!/usr/bin/env python3
"""Automatic Traktor MIDI mapping for MP Liquid Mixer.

The mixer's native S4 path reads evdev axes. This module adds the other mode
the hardware supports: a Traktor Kontrol mixer in **MIDI mode** appears as an
ALSA rawmidi port, and its push buttons and faders can then be mapped to the
same mixer actions the evdev listener already uses.

Only a device whose ALSA name contains ``Traktor`` is opened. A generic MIDI
controller (an MPK mini, for example) is never mapped, even when it is the only
device present. Detection is repeated while the mixer runs, so plugging the
Traktor in later starts the map automatically.

Like the native listener, this thread only produces ``(action, value)`` calls;
the GTK thread applies them. The default map follows the Traktor Kontrol S4
MIDI note/CC layout already used by ``scripts/traktor_s4_midi.py``. A user can
override individual entries in ``~/.config/mix-manager/liquid_mixer_midi.json``:

    {
      "notes": {"0": "play:A", "6": "sync:A"},
      "cc": {"8": "channel:A", "14": "pitch:A"}
    }
"""
from __future__ import annotations

import glob
import json
import os
import re
import select
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

CONFIG_DIR = Path.home() / ".config" / "mix-manager"
LOG_PATH = CONFIG_DIR / "liquid_mixer_midi.log"
MAPPING_PATH = CONFIG_DIR / "liquid_mixer_midi.json"

DEVICE_MARKER = "traktor"

# Traktor Kontrol S4 MIDI-mode notes. The numbers follow the map already used
# by scripts/traktor_s4_midi.py; controls the mixer does not implement (hotcues,
# loop recorder play) are deliberately absent rather than guessed.
DEFAULT_NOTES: Dict[int, str] = {
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
DEFAULT_CC: Dict[int, str] = {
    8: "channel:A", 19: "channel:A",
    9: "channel:B", 22: "channel:B",
    14: "pitch:A", 15: "pitch:B",
    42: "browse", 43: "browse", 80: "browse", 81: "browse", 100: "browse",
    32: "play:A", 33: "play:B", 40: "play:A",
    36: "sync:A", 37: "sync:B",
}


def _log(message: str) -> None:
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


def load_mapping(path: Optional[Path] = None) -> Tuple[Dict[int, str], Dict[int, str]]:
    """Default Traktor map plus a user override file, when present."""
    notes = dict(DEFAULT_NOTES)
    cc = dict(DEFAULT_CC)
    target = Path(path or MAPPING_PATH)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return notes, cc
    if not isinstance(payload, dict):
        return notes, cc
    for source, destination in (("notes", notes), ("cc", cc)):
        values = payload.get(source)
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


def find_traktor_sequencer_ports() -> List[Dict[str, str]]:
    """Traktor input ports on the ALSA sequencer, if ``aseqdump`` exists.

    The sequencer allows several listeners, unlike the rawmidi node, which one
    process can hold exclusively. That matters when the standalone Traktor
    service or Traktor itself already owns the raw device.
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
        if DEVICE_MARKER not in line.lower():
            continue
        match = re.search(r"(\d+):(\d+)", line)
        if not match:
            continue
        port = f"{int(match.group(1))}:{int(match.group(2))}"
        # aseqdump -l prints the client name and the port name; keep the
        # client name for the status line.
        rest = line.split(str(match.group(0)), 1)[-1].strip()
        name = re.split(r"\s{2,}", rest)[0].strip() or "Traktor"
        found.append({"name": name, "path": port, "backend": "sequencer"})
    return found


def find_traktor_devices(proc_root: Path | str = "/proc/asound",
                         dev_root: Path | str = "/dev/snd") -> List[Dict[str, str]]:
    """Every readable Traktor rawmidi port, and nothing else.

    ALSA's ``/proc/asound/cardN/midiM`` names the port and gives the card and
    subdevice. A device that is not named Traktor is skipped before its node is
    touched, so a generic controller can never be mapped by accident.
    """
    found: List[Dict[str, str]] = []
    seen = set()
    for midi_file in sorted(glob.glob(str(Path(proc_root) / "card*" / "midi*"))):
        path = Path(midi_file)
        name = _device_name(path)
        if DEVICE_MARKER not in name.lower():
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
    # amidi -l still names the device. Generic devices are skipped here too.
    try:
        result = subprocess.run(["amidi", "-l"], capture_output=True, text=True,
                                timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return found
    for line in (result.stdout or "").splitlines():
        if DEVICE_MARKER not in line.lower():
            continue
        match = re.search(r"hw:(\d+),(\d+)", line)
        if not match:
            continue
        raw = _rawmidi_path(int(match.group(1)), int(match.group(2)), Path(dev_root))
        if raw.exists() and str(raw) not in seen:
            seen.add(str(raw))
            found.append({"name": line.strip(), "path": str(raw), "proc": ""})
    return found


def pitch_percent_7bit(value: int) -> float:
    """Traktor's 7-bit pitch fader to -20..+20 percent, with a centre detent."""
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
    """One ``aseqdump`` event line to ``(status, data1, data2)``.

    The sequencer path is preferred because rawmidi is exclusive. ``aseqdump``
    prints one human-readable event per line; parsing that is the only way to
    share the Traktor port without an extra Python MIDI dependency.
    """
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
        # aseqdump often prints the signed value; _dispatch expects LSB/MSB.
        value = max(0, min(16383, value))
        return (0xE0 | (channel & 0x0F), value & 0x7F, (value >> 7) & 0x7F)
    return None


class TraktorMidiListener:
    """Read one Traktor MIDI port and publish the mixer's existing actions."""

    def __init__(self, on_change: Optional[Callable[[str, float], None]] = None,
                 mapping_path: Optional[Path] = None) -> None:
        self.on_change = on_change
        self.notes, self.cc = load_mapping(mapping_path)
        self.status = "waiting for Traktor MIDI"
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

    @property
    def connected(self) -> bool:
        return self.status.startswith("mapped")

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        proc, self._seq_proc = self._seq_proc, None
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            # select() has a 0.25 s timeout, so the reader notices _running
            # and closes its own fd. Closing from here raced that read and
            # produced an EBADF reconnect in the log.
            thread.join(timeout=2.0)
            if thread.is_alive():
                fd, self._fd = self._fd, None
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                thread.join(timeout=1.0)

    def _run(self) -> None:
        backoff = 1.0
        while self._running:
            ports = find_traktor_sequencer_ports()
            if ports:
                if not self._running:
                    break
                self._run_sequencer(ports[0])
                if self._running:
                    time.sleep(1.0)
                continue
            devices = find_traktor_devices()
            if not devices:
                self.status = "waiting for Traktor MIDI"
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

    def _run_sequencer(self, port: Dict[str, str]) -> None:
        """Read the ALSA sequencer port; multiple listeners may share it."""
        dump = shutil.which("aseqdump")
        if not dump:
            return
        command = [dump, "-p", port["path"]]
        # aseqdump is C stdio: without a line buffer a piped reader can wait
        # for a full block before a button press arrives.
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
            self.status = f"cannot open Traktor sequencer: {exc}"
            return
        if not self._running:
            try:
                proc.terminate()
            except OSError:
                pass
            return
        # aseqdump exits at once when the kernel sequencer port is already
        # taken (the standalone Traktor service holds the raw device). Confirm
        # it stayed up before claiming a map; otherwise fall back to rawmidi.
        time.sleep(0.15)
        if proc.poll() is not None:
            self.status = "Traktor MIDI is held by another service"
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
            _log(f"Traktor sequencer reader error: {exc!r}")
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
                self.status = "Traktor MIDI is held by another service"
            else:
                self.status = f"cannot open Traktor MIDI: {exc}"
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
            _log(f"Traktor MIDI read error: {exc}")
        except Exception as exc:
            _log(f"Traktor MIDI reader error: {exc!r}")
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
                if byte >= 0xF0:  # system common; not a Traktor control message
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
        value = max(0, min(127, int(value)))
        if action.startswith("channel:"):
            return value / 127.0
        if action.startswith("pitch:"):
            return pitch_percent_7bit(value)
        if action == "browse":
            if value == 0:
                return None
            # Traktor's relative encoders send either 1/127 or 65/63.
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
            _log(f"Traktor MIDI callback failed for {action}: {exc!r}")
