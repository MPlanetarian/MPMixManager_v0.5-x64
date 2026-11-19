#!/usr/bin/env python3
"""
Kontrol S4 Hardware MIDI Controller Service for MP Mix Archive Manager & MP Audio Player
Version 0.5.4 Stable (Sensorium)

Communicates with Native Instruments Kontrol S4 over Hardware ALSA MIDI Port (amidi / hw:0,0).
Translates MIDI Mode signals (Pitch Faders, Buttons, Encoders, Volume Sliders) to MP Audio Player IPC commands.
"""

from __future__ import annotations
import os, sys, time, json, socket, subprocess, threading, signal, select, argparse, platform, shutil, re, glob
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List

CONFIG_DIR = Path.home() / ".config" / "mix-manager"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

PID_FILE = CONFIG_DIR / "dj_s4_midi.pid"
LOCK_FILE = CONFIG_DIR / "dj_s4_midi.lock"
LOG_FILE = CONFIG_DIR / "dj_s4_midi.log"
STATE_FILE = CONFIG_DIR / "dj_s4_midi_state.json"
FIRST_SEEN_FLAG = CONFIG_DIR / "dj_s4_first_seen.flag"

PLAYER_SOCKET = CONFIG_DIR / "mp_audio_player.sock"
PLAYER_PORT_FILE = CONFIG_DIR / "mp_audio_player.port"
PLAYER_STATE_FILE = CONFIG_DIR / "mp_player_state.json"

# Legacy ALSA default. The S4 is normally card 0, but any other sound card
# (HDMI audio, a USB interface) can take that slot, so the real port is
# auto-detected and DEFAULT_PORT is only the last-resort fallback.
DEFAULT_PORT = "hw:0,0"
DEFAULT_DEVICE_NAME = "Kontrol S4"

# FreeBSD has no ALSA: there is no /dev/snd tree, no `amidi`, and no
# /proc/asound. MIDI arrives through the generic USB MIDI driver as
# /dev/umidi<unit>.<port>. Without this flag the daemon spawned `amidi` and
# died with FileNotFoundError as soon as it started.
IS_FREEBSD = sys.platform.startswith("freebsd") or "FreeBSD" in platform.system()


def freebsd_midi_devices() -> List[str]:
    """Raw MIDI device paths on FreeBSD, newest unit first.

    Returns an empty list on every other platform, so a caller can use it
    unconditionally as a fallback without a platform branch of its own.
    """
    if not IS_FREEBSD:
        return []
    import glob
    devices = sorted(glob.glob("/dev/umidi*.*"))
    # Prefer the device whose unit matches the requested card, then anything.
    return devices

# ANSI Colors
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
NC = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
BLUE = "\033[34m"
WHITE = "\033[37m"

def log(msg: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] [DJ S4 MIDI] {msg}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except Exception:
        pass


def send_player_ipc(cmd: str, **kwargs) -> Optional[Dict[str, Any]]:
    """Send IPC command directly to running MP Audio Player."""
    use_unix = hasattr(socket, "AF_UNIX") and PLAYER_SOCKET.exists()
    if not use_unix and not PLAYER_PORT_FILE.exists():
        return None
    try:
        s = socket.socket(socket.AF_UNIX if use_unix else socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.2)
        target = str(PLAYER_SOCKET) if use_unix else ("127.0.0.1", int(PLAYER_PORT_FILE.read_text().strip()))
        s.connect(target)
        s.sendall(json.dumps({"cmd": cmd, **kwargs}).encode("utf-8"))
        raw = s.recv(16384).decode("utf-8")
        s.close()
        if raw:
            return json.loads(raw)
    except Exception as e:
        log(f"IPC command failed ({cmd}): {e}")
    return None


def get_player_status() -> Optional[Dict[str, Any]]:
    """Fetch live status of MP Audio Player."""
    res = send_player_ipc("status")
    if res and res.get("status") == "ok":
        return res.get("data")
    if PLAYER_STATE_FILE.is_file():
        try:
            return json.loads(PLAYER_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def is_player_running() -> bool:
    st = get_player_status()
    if st:
        return st.get("running", False) or st.get("state") in ("playing", "paused")
    return False


def _read_text(path: str) -> Optional[str]:
    """Read a small text file, returning None when it is missing/unreadable."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None


def _alsa_cards(proc_root: str = "/proc") -> List[Tuple[int, str, str]]:
    """Return (index, id, description) for every ALSA card."""
    text = _read_text(f"{proc_root}/asound/cards") or ""
    cards: List[Tuple[int, str, str]] = []
    for line in text.splitlines():
        m = re.match(r"\s*(\d+)\s+\[([^\]]*)\]\s*:\s*(.*)", line)
        if m:
            cards.append((int(m.group(1)), m.group(2).strip(), m.group(3).strip()))
    return cards


def _dj_card(proc_root: str = "/proc") -> Optional[Tuple[int, str, str]]:
    """Find the ALSA card belonging to a Kontrol S4, if present."""
    for idx, card_id, desc in _alsa_cards(proc_root):
        if "dj" in f"{card_id} {desc}".lower():
            return idx, card_id, desc
    return None


def _card_midi_subdevice(proc_root: str, card: int) -> Tuple[Optional[int], str]:
    """First MIDI subdevice exposed by an ALSA card, plus its description."""
    matches = sorted(
        glob.glob(f"{proc_root}/asound/card{card}/midi*"),
        key=lambda p: int(re.sub(r"\D", "", os.path.basename(p)) or 0),
    )
    if not matches:
        return None, ""
    midi_file = matches[0]
    dev_str = os.path.basename(midi_file).replace("midi", "")
    content = _read_text(midi_file) or ""
    details = content.splitlines()[0].strip() if content else ""
    return (int(dev_str) if dev_str.isdigit() else 0), details


def rawmidi_path_for_port(port: str, dev_root: str = "/dev") -> Optional[str]:
    """Map an ALSA port ('hw:CARD,DEV') to its rawmidi node, if it exists."""
    if not port:
        return None
    if port.startswith("/dev/"):
        return port if os.path.exists(port) else None
    if port.startswith("hw:"):
        parts = port[3:].split(",")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            path = f"{dev_root}/snd/midiC{parts[0]}D{parts[1]}"
            return path if os.path.exists(path) else None
    return None


def port_from_rawmidi_path(path: str) -> Optional[str]:
    """Inverse of rawmidi_path_for_port: /dev/snd/midiC1D0 -> hw:1,0."""
    m = re.match(r".*/snd/midiC(\d+)D(\d+)$", path or "")
    return f"hw:{m.group(1)},{m.group(2)}" if m else None


def detect_dj_s4_port(proc_root: str = "/proc", dev_root: str = "/dev") -> Tuple[bool, str, str]:
    """
    Detect Kontrol S4 hardware port.

    The card index is whatever ALSA assigned to the S4, which is not always 0:
    HDMI audio or a USB interface can occupy card 0. Reading the card's own
    /proc/asound/cardN/midiM entry keeps the port correct either way.

    Returns: (is_detected, port_str, details)
    """
    # 1. Primary ALSA check: the DJ card and its first rawmidi subdevice.
    card = _dj_card(proc_root)
    if card:
        idx, card_id, desc = card
        dev_num, midi_details = _card_midi_subdevice(proc_root, idx)
        port = f"hw:{idx},{dev_num if dev_num is not None else 0}"
        details = midi_details or f"{card_id}: {desc}"
        return True, port, details

    # 2. FreeBSD: generic USB MIDI driver exposes /dev/umidi<unit>.<port>.
    if IS_FREEBSD:
        devices = freebsd_midi_devices()
        if devices:
            return True, devices[0], devices[0]

    # 3. Check amidi -l
    try:
        res = subprocess.run(["amidi", "-l"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2)
        for line in res.stdout.splitlines():
            if "dj" in line.lower():
                parts = line.split()
                port = next((p for p in parts if p.startswith("hw:")), DEFAULT_PORT)
                return True, port, line.strip()
    except Exception:
        pass

    # 4. Rawmidi node + USB input description (when /proc/asound is absent)
    midi_nodes = sorted(glob.glob(f"{dev_root}/snd/midiC*D*"))
    if midi_nodes:
        inputs = _read_text(f"{proc_root}/bus/input/devices") or ""
        if "dj" in inputs.lower():
            port = port_from_rawmidi_path(midi_nodes[0]) or DEFAULT_PORT
            return True, port, f"Kontrol S4 ({midi_nodes[0]})"

    return False, DEFAULT_PORT, ""


def resolve_port(requested: Optional[str] = None,
                 proc_root: str = "/proc", dev_root: str = "/dev") -> str:
    """Pick the port to listen on: an explicit working port, else auto-detect.

    A requested port that has no rawmidi node (for example the old hardcoded
    hw:0,0 when the S4 landed on card 1) is replaced by the detected port.
    """
    if requested and rawmidi_path_for_port(requested, dev_root):
        return requested
    detected, port, details = detect_dj_s4_port(proc_root, dev_root)
    if detected and port:
        if requested and requested != port:
            log(f"Requested port {requested} is not present; using detected {port} ({details})")
        return port
    return requested or DEFAULT_PORT


class DJS4MidiController:
    """
    Kontrol S4 MIDI Event Processor.
    Translates raw MIDI messages to MP Mix Archive Manager and MP Audio Player commands.
    """

    def __init__(self, port: Optional[str] = None):
        # None means "auto-detect now"; an explicit port is kept unless it has
        # no rawmidi node, in which case _get_rawmidi_device_path() repairs it.
        self.port = port or resolve_port()
        self.rawmidi_dev = self._get_rawmidi_device_path(self.port)
        self.running = False
        self.last_pitch_sent = 0
        self.last_pitch_time = 0.0
        self.last_vol_sent = -1
        self.last_vol_time = 0.0
        self.last_turn_time = 0.0
        self.shift_active = False

    def _get_rawmidi_device_path(self, port: str) -> Optional[str]:
        path = rawmidi_path_for_port(port)
        if path:
            return path
        if IS_FREEBSD:
            devices = freebsd_midi_devices()
            return devices[0] if devices else None
        # The S4's card number can differ from the requested one (hdmi audio
        # often takes card 0). Re-detect and adopt the real port.
        detected, detected_port, _ = detect_dj_s4_port()
        if detected and detected_port:
            fallback = rawmidi_path_for_port(detected_port)
            if fallback:
                log(f"{port} has no rawmidi node; using detected {detected_port} ({fallback})")
                self.port = detected_port
                return fallback
        return None

    def update_state(self, status: str, last_action: str = ""):
        state_data = {
            "device": DEFAULT_DEVICE_NAME,
            "port": self.port,
            "rawmidi_dev": self.rawmidi_dev,
            "status": status,
            "pid": os.getpid(),
            "updated_at": time.time(),
            "last_action": last_action,
            "shift": self.shift_active,
        }
        try:
            STATE_FILE.write_text(json.dumps(state_data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def scale_pitch_cc_to_percent(self, value: int, inverted: bool = False) -> int:
        """
        Convert standard 7-bit CC (0..127) to DJ Pitch percentage (-20% to +20%).
        Center detent is 64. A deadzone of 62..66 snaps to 0.
        """
        if 62 <= value <= 66:
            return 0
        if inverted:
            val = 127 - value
        else:
            val = value
        
        # Center is 64
        diff = val - 64
        pct = int(round((diff / 63.0) * 20.0))
        return max(-20, min(20, pct))

    def scale_pitch_bend_to_percent(self, lsb: int, msb: int, inverted: bool = False) -> int:
        """
        Convert 14-bit Pitch Bend (0..16383, center 8192) to DJ Pitch percentage (-20% to +20%).
        """
        val_14 = (msb << 7) | lsb
        diff = val_14 - 8192
        if abs(diff) < 250:
            return 0
        if inverted:
            diff = -diff
        pct = int(round((diff / 8191.0) * 20.0))
        return max(-20, min(20, pct))

    def handle_pitch_change(self, percent: int, source: str) -> str:
        now = time.time()
        # Limit rate to avoid spamming the audio resampler (max 25 updates/sec unless 0 snap)
        if percent == self.last_pitch_sent and (now - self.last_pitch_time) < 0.2:
            return f"Pitch unchanged ({percent}%)"
        if percent != 0 and abs(percent - self.last_pitch_sent) < 1 and (now - self.last_pitch_time) < 0.04:
            return f"Pitch rate-limited ({percent}%)"

        self.last_pitch_sent = percent
        self.last_pitch_time = now
        log(f"Setting pitch to {percent}% (Source: {source})")
        send_player_ipc("set_pitch", percent=percent)
        return f"DJ Pitch: {percent:+d}%"

    def handle_volume_change(self, val_127: int, channel_name: str) -> str:
        now = time.time()
        vol = max(0, min(100, int(round((val_127 / 127.0) * 100.0))))
        if vol == self.last_vol_sent and (now - self.last_vol_time) < 0.2:
            return f"Volume unchanged ({vol}%)"
        if abs(vol - self.last_vol_sent) < 2 and (now - self.last_vol_time) < 0.05:
            return f"Volume rate-limited ({vol}%)"

        self.last_vol_sent = vol
        self.last_vol_time = now
        log(f"Setting volume to {vol}% (Fader: {channel_name})")
        send_player_ipc("set_volume", volume=vol)
        return f"Volume: {vol}%"

    def process_midi_event(self, status: int, data1: int, data2: int) -> Tuple[str, str]:
        """
        Parse raw MIDI packet and execute matching MP Mix Archive Manager / Player function.
        Returns: (event_desc, action_desc)
        """
        msg_type = status & 0xF0
        channel = (status & 0x0F) + 1  # 1-indexed channel (1 to 16)
        action = ""
        desc = ""

        # =========================================================================
        # 1. PITCH BEND (0xE0) - DJ S4 High-Resolution Deck Pitch Controls
        # =========================================================================
        if msg_type == 0xE0:
            # Channel 1: Left Deck Pitch Bend
            # Channel 2: Right Deck Pitch Bend
            deck_name = "Left Deck (Deck A)" if channel == 1 else ("Right Deck (Deck B)" if channel == 2 else f"Deck Ch {channel}")
            pct = self.scale_pitch_bend_to_percent(data1, data2)
            desc = f"Pitch Bend Ch {channel} ({deck_name}): LSB={data1} MSB={data2}"
            action = self.handle_pitch_change(pct, f"{deck_name} Pitch Bend")
            return desc, action

        # =========================================================================
        # 2. CONTROL CHANGE (0xB0) - Faders, Knobs, Encoders, CC-mapped Buttons
        # =========================================================================
        if msg_type == 0xB0:
            cc = data1
            val = data2
            desc = f"CC {cc} Ch {channel}: Val={val}"

            # Left Deck Pitch Fader CCs (CC 14, CC 0, CC 1, CC 2, CC 16, CC 20)
            if (channel == 1 and cc in (14, 0, 1, 2, 16, 20)) or (channel != 2 and cc in (14, 20)):
                pct = self.scale_pitch_cc_to_percent(val)
                action = self.handle_pitch_change(pct, "Left Deck Pitch Fader")
                return desc, action

            # Right Deck Pitch Fader CCs (CC 15, CC 3, CC 4, CC 17, CC 21)
            if (channel == 2 and cc in (15, 3, 4, 17, 21)) or (channel != 1 and cc in (15, 21)):
                pct = self.scale_pitch_cc_to_percent(val)
                action = self.handle_pitch_change(pct, "Right Deck Pitch Fader")
                return desc, action

            # Mixer Volume Faders (Channels A, B, C, D)
            # Default CCs: 8, 9, 10, 11 or 7 (Standard MIDI Volume)
            if cc in (8, 9, 10, 11, 7, 19, 22):
                ch_map = {8: "Ch A", 9: "Ch B", 10: "Ch C", 11: "Ch D", 7: "Master", 19: "Ch A", 22: "Ch B"}
                action = self.handle_volume_change(val, ch_map.get(cc, f"CC {cc}"))
                return desc, action

            # Master / Gain Knobs (CC 23, 24, 25, 26)
            if cc in (23, 24, 25, 26):
                action = self.handle_volume_change(val, f"Gain CC {cc}")
                return desc, action

            # Browse Encoder Turn (CC 42, CC 43, CC 64, CC 80, CC 81, or Relative 65/63 / 1/127)
            if cc in (42, 43, 80, 81, 100):
                now = time.time()
                if (now - self.last_turn_time) > 0.08:
                    self.last_turn_time = now
                    if val > 64 or val == 1:
                        send_player_ipc("next")
                        action = "Next Track / Mix ⏩"
                    else:
                        send_player_ipc("prev")
                        action = "Previous Track / Mix ⏪"
                return desc, action

            # Loop Move / Size Encoder Turn (CC 44, CC 45)
            if cc in (44, 45):
                now = time.time()
                if (now - self.last_turn_time) > 0.08:
                    self.last_turn_time = now
                    if val > 64 or val == 1:
                        send_player_ipc("seek", seconds=30.0)
                        action = "Seek Forward +30s ⏩"
                    else:
                        send_player_ipc("seek", seconds=-30.0)
                        action = "Seek Backward -30s ⏪"
                return desc, action

            # CC-triggered Buttons (val > 0)
            if val > 0:
                # Play button (CC 32, CC 33, CC 40)
                if cc in (32, 33, 40):
                    send_player_ipc("toggle")
                    action = "Play / Pause (Toggle)"
                    return desc, action
                # Cue button (CC 34, CC 35, CC 41)
                elif cc in (34, 35, 41):
                    send_player_ipc("seek_to", position=0.0)
                    action = "Cue (Return to Start)"
                    return desc, action
                # Sync / Pitch Reset (CC 36, CC 37)
                elif cc in (36, 37):
                    send_player_ipc("reset_pitch")
                    action = "Sync (Reset Pitch to 0%)"
                    return desc, action
                # Pitch Up / Down Nudge Buttons (CC 38, CC 39)
                elif cc == 38:
                    send_player_ipc("pitch_up", step=1)
                    action = "Pitch Nudge Up (+1%)"
                    return desc, action
                elif cc == 39:
                    send_player_ipc("pitch_down", step=1)
                    action = "Pitch Nudge Down (-1%)"
                    return desc, action

            return desc, f"Ignored CC {cc}"

        # =========================================================================
        # 3. NOTE ON (0x90) / NOTE OFF (0x80) - Hardware Push Buttons & Pads
        # =========================================================================
        if msg_type in (0x90, 0x80):
            note = data1
            vel = data2
            is_on = (msg_type == 0x90) and (vel > 0)
            desc = f"Note {'ON' if is_on else 'OFF'} {note} Ch {channel} Vel={vel}"

            if not is_on:
                # Shift release
                if note in (48, 49, 12):
                    self.shift_active = False
                return desc, "Note Release"

            # Shift Button Press (Note 48, 49, 12)
            if note in (48, 49, 12):
                self.shift_active = True
                return desc, "SHIFT Button Active"

            # Play / Pause Buttons (Left: Note 0, 11, 36 | Right: Note 1, 12, 37)
            if note in (0, 1, 11, 36, 37, 50, 51):
                send_player_ipc("toggle")
                action = "Play / Pause (Toggle) ▶⏸"
                return desc, action

            # Cue Buttons (Left: Note 2, 13, 38 | Right: Note 3, 14, 39)
            if note in (2, 3, 13, 38, 39, 52, 53):
                send_player_ipc("seek_to", position=0.0)
                action = "CUE (Seek to 0:00) ⏹"
                return desc, action

            # Cup Buttons (Cue-Play)
            if note in (4, 5, 40, 41):
                send_player_ipc("seek_to", position=0.0)
                send_player_ipc("play")
                action = "CUP (Play from Start) ⏯"
                return desc, action

            # Sync Buttons (Left: Note 6, 42 | Right: Note 7, 43)
            if note in (6, 7, 42, 43):
                if self.shift_active or note in (7, 43):
                    send_player_ipc("shuffle")
                    action = "Shuffle Mode Toggled 🔀"
                else:
                    send_player_ipc("reset_pitch")
                    action = "Sync (Pitch Reset to 0%) 🎚"
                return desc, action

            # Pitch Bend / Tempo Buttons (+ / -)
            # Left Deck Pitch + / - : Note 8, 9
            # Right Deck Pitch + / - : Note 10, 11
            if note in (8, 10, 44, 46):
                send_player_ipc("pitch_up", step=1)
                action = "Pitch Nudge Up (+1%) 🔼"
                return desc, action
            if note in (9, 11, 45, 47):
                send_player_ipc("pitch_down", step=1)
                action = "Pitch Nudge Down (-1%) 🔽"
                return desc, action

            # Browse Push Encoder (Note 14, 15, 60)
            if note in (14, 15, 60, 61):
                send_player_ipc("toggle")
                action = "Browse Encoder Push (Play/Pause)"
                return desc, action

            # Loop In / Loop Out Buttons (Note 16, 17, 62, 63)
            if note in (16, 62):
                send_player_ipc("seek", seconds=-10.0)
                action = "Loop In / Quick Seek -10s ⏪"
                return desc, action
            if note in (17, 63):
                send_player_ipc("seek", seconds=10.0)
                action = "Loop Out / Quick Seek +10s ⏩"
                return desc, action

            # Hotcue Buttons 1, 2, 3, 4 (Left Deck: Note 20..23 | Right Deck: Note 24..27)
            if note in (20, 24, 64, 68):
                send_player_ipc("seek_to", position=0.0)
                action = "Hotcue 1 (Jump to 0% Start)"
                return desc, action
            elif note in (21, 25, 65, 69):
                st = get_player_status()
                dur = float(st.get("duration_sec", 0)) if st else 0.0
                send_player_ipc("seek_to", position=dur * 0.25)
                action = "Hotcue 2 (Jump to 25% Mix Position)"
                return desc, action
            elif note in (22, 26, 66, 70):
                st = get_player_status()
                dur = float(st.get("duration_sec", 0)) if st else 0.0
                send_player_ipc("seek_to", position=dur * 0.50)
                action = "Hotcue 3 (Jump to 50% Mid-Point)"
                return desc, action
            elif note in (23, 27, 67, 71):
                st = get_player_status()
                dur = float(st.get("duration_sec", 0)) if st else 0.0
                send_player_ipc("seek_to", position=dur * 0.75)
                action = "Hotcue 4 (Jump to 75% Mix Position)"
                return desc, action

            # Headphone Cue Buttons (Channel Mute) (Note 28..31, 72..75)
            if note in (28, 29, 30, 31, 72, 73, 74, 75):
                send_player_ipc("mute")
                action = "Headphone Cue (Mute / Unmute) 🔇"
                return desc, action

            # Loop Recorder REC Button (Note 32, 76)
            if note in (32, 76):
                send_player_ipc("toggle_record")
                action = "Loop Recorder REC (Toggle Recording) 🔴"
                return desc, action

            # Loop Recorder PLAY Button (Note 33, 77)
            if note in (33, 77):
                send_player_ipc("toggle")
                action = "Loop Recorder PLAY (Play/Pause)"
                return desc, action

            return desc, f"Unhandled Note {note}"

        return f"Raw MIDI: {status:02X} {data1:02X} {data2:02X}", "Ignored"

    def run_rawmidi_stream(self, monitor_mode: bool = False):
        """
        Read directly from the S4's ALSA rawmidi device (lowest latency).

        Returns when the device goes away or read() fails, so start_loop()
        can re-detect the card, wait, and reconnect.
        """
        if not self.rawmidi_dev or not os.path.exists(self.rawmidi_dev):
            log(f"No rawmidi node for {self.port}; waiting for the S4.")
            self.update_state("standby", f"waiting for rawmidi node on {self.port}")
            return

        try:
            fd = os.open(self.rawmidi_dev, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            log(f"Failed to open {self.rawmidi_dev}: {e}")
            self.update_state("standby", f"cannot open {self.rawmidi_dev}: {e}")
            return

        log(f"Connected to DJ S4 on {self.rawmidi_dev} ({self.port})")
        self.update_state("running", "Connected via ALSA Rawmidi")
        buffer = bytearray()

        try:
            while self.running:
                r, _, _ = select.select([fd], [], [], 0.1)
                if not r:
                    continue
                try:
                    chunk = os.read(fd, 256)
                except BlockingIOError:
                    continue
                except OSError as e:
                    log(f"Read error on {self.rawmidi_dev}: {e}. Re-detecting the S4.")
                    self.update_state("standby", f"read error: {e}")
                    break

                if not chunk:
                    time.sleep(0.01)
                    continue

                buffer.extend(chunk)

                # Process 3-byte standard MIDI messages
                while len(buffer) >= 3:
                    # Sync to status byte (>= 0x80)
                    while buffer and buffer[0] < 0x80:
                        buffer.pop(0)

                    if len(buffer) < 3:
                        break

                    status = buffer[0]
                    # Filter realtime messages
                    if status >= 0xF8:
                        buffer.pop(0)
                        continue

                    data1 = buffer[1]
                    data2 = buffer[2]
                    buffer = buffer[3:]

                    desc, action = self.process_midi_event(status, data1, data2)
                    self.update_state("running", f"{desc} -> {action}")
                    if monitor_mode:
                        print(f"  {CYAN}[MIDI]{NC} {WHITE}{desc:<40}{NC} ➔ {BOLD}{GREEN}{action}{NC}")
        finally:
            try:
                os.close(fd)
            except Exception:
                pass

    def run_amidi_stream(self, monitor_mode: bool = False):
        """
        Fallback MIDI stream reader using 'amidi -p <port> --dump'.
        """
        # `amidi` is an ALSA tool. It does not exist on FreeBSD or macOS, and a
        # bare Popen died with FileNotFoundError the moment the daemon started.
        # Stand by instead: the controls simply are not available until a device
        # path can be opened, which is the same outcome without the traceback.
        # Do not trust PATH alone: launchers started from a desktop session can
        # carry a trimmed environment where `which amidi` misses /usr/bin.
        amidi = shutil.which("amidi")
        if not amidi:
            for candidate in ("/usr/bin/amidi", "/usr/local/bin/amidi", "/opt/homebrew/bin/amidi"):
                if os.path.exists(candidate):
                    amidi = candidate
                    break
        if not amidi:
            log("amidi is not installed; MIDI input is unavailable on this platform.")
            self.update_state("standby", "amidi unavailable")
            # Return after a short wait so start_loop() can retry detection.
            deadline = time.time() + 5.0
            while self.running and time.time() < deadline:
                time.sleep(0.2)
            return

        cmd = [amidi, "-p", self.port, "--dump"]
        log(f"Starting amidi process: {' '.join(cmd)}")
        self.update_state("running", f"Listening via amidi on {self.port}")

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)

        try:
            for line in iter(proc.stdout.readline, ""):
                if not self.running:
                    break
                line = line.strip()
                if not line:
                    continue

                # Hex tokens (e.g. "90 3C 7F" or "B0 0E 40")
                tokens = line.split()
                i = 0
                while i + 2 < len(tokens):
                    try:
                        s_byte = int(tokens[i], 16)
                        d1_byte = int(tokens[i + 1], 16)
                        d2_byte = int(tokens[i + 2], 16)
                        desc, action = self.process_midi_event(s_byte, d1_byte, d2_byte)
                        self.update_state("running", f"{desc} -> {action}")
                        if monitor_mode:
                            print(f"  {CYAN}[MIDI]{NC} {WHITE}{desc:<40}{NC} ➔ {BOLD}{GREEN}{action}{NC}")
                        i += 3
                    except ValueError:
                        i += 1
        except Exception as e:
            log(f"amidi process error: {e}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except Exception:
                proc.kill()

    def start_loop(self, monitor_mode: bool = False):
        self.running = True
        log("DJ S4 MIDI listener daemon started.")
        if monitor_mode:
            print(f"\n{BOLD}{MAGENTA}========================================================================{NC}")
            print(f"{BOLD}{WHITE}   🎛️  DJ KONTROL S4 HARDWARE MIDI LIVE MONITOR / TEST CONSOLE{NC}")
            print(f"{BOLD}{MAGENTA}========================================================================{NC}")
            print(f"  Hardware Port   : {BOLD}{CYAN}{self.port}{NC}")
            print(f"  ALSA Device     : {BOLD}{CYAN}{self.rawmidi_dev or 'amidi stream'}{NC}")
            print(f"  Audio Player IPC: {BOLD}{GREEN if is_player_running() else YELLOW}{'Connected' if is_player_running() else 'Waiting for player'}{NC}")
            print(f"  MIDI Mode Hint  : Press {BOLD}{YELLOW}SHIFT + BROWSE{NC} (MK1) or {BOLD}{YELLOW}SHIFT + PREVIEW{NC} (MK2) on S4")
            print(f"  Exit Monitor    : Press {BOLD}Ctrl+C{NC}\n")
            print(f"  {DIM}Listening for incoming pitch faders, buttons, knobs, encoders...{NC}\n")

        # Prefer the ALSA rawmidi node for ultra-low latency, fall back to an
        # amidi dump stream, and keep retrying while the S4 is unplugged so the
        # service attaches as soon as it appears (or appears on another card).
        backoff = 1.0
        while self.running:
            self.rawmidi_dev = self._get_rawmidi_device_path(self.port)
            started = time.time()
            if self.rawmidi_dev:
                self.run_rawmidi_stream(monitor_mode=monitor_mode)
            else:
                self.run_amidi_stream(monitor_mode=monitor_mode)
            if not self.running:
                break
            if time.time() - started > 5.0:
                backoff = 1.0
            log(f"DJ S4 MIDI stream closed; retrying in {backoff:.0f}s")
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 15.0)


# =============================================================================
# DAEMON LIFECYCLE MANAGEMENT
# =============================================================================

def _pid_from_file(path: Path) -> Optional[int]:
    try:
        pid = int(path.read_text(encoding="utf-8").strip().split()[0])
        return pid if pid > 0 else None
    except Exception:
        return None


def _pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_daemon_running() -> Tuple[bool, Optional[int]]:
    # PID_FILE is authoritative; the lock file keeps a surviving daemon
    # discoverable after a manager restarts and loses the pid file.
    for path in (PID_FILE, LOCK_FILE):
        pid = _pid_from_file(path)
        if _pid_alive(pid):
            return True, pid
    PID_FILE.unlink(missing_ok=True)
    return False, None


def _iter_daemon_pids() -> List[int]:
    """Every live `dj_s4_midi.py --daemon` process except this one.

    Older builds could leak one daemon per manager refresh because the pid
    file was rewritten by each child. Reaping by cmdline makes start/stop
    converge on a single service again.
    """
    pids: List[int] = []
    if not os.path.isdir("/proc"):
        return pids
    me = os.getpid()
    for entry in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            raw = Path(entry).read_bytes()
        except OSError:
            continue
        # Argument-exact match: a shell or grep whose command *text* mentions
        # the daemon must never be reaped.
        args = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
        if len(args) < 2 or not os.path.basename(args[0]).lower().startswith("python"):
            continue
        if "--daemon" not in args:
            continue
        if not any(a.endswith("dj_s4_midi.py") for a in args[1:]):
            continue
        try:
            pid = int(entry.split("/")[2])
        except (IndexError, ValueError):
            continue
        if pid != me:
            pids.append(pid)
    return pids


def acquire_single_instance_lock(write_pid: bool = True):
    """Keep exactly one daemon on the S4's exclusive rawmidi node.

    Two daemons used to race for /dev/snd/midiC*D* and the loser spun on
    EBUSY while both overwrote the pid/state files. Returns an open fd that
    must live for the process lifetime, "unsupported" where fcntl is absent,
    or None when another daemon already owns the device.

    write_pid=False is a pure probe: it leaves the lock file untouched.
    """
    try:
        import fcntl
    except ImportError:
        return "unsupported"
    try:
        fd = os.open(str(LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    if write_pid:
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode())
        except OSError:
            pass
    return fd


def start_daemon(port: Optional[str] = None):
    running, pid = is_daemon_running()
    if running:
        print(f"{YELLOW}DJ S4 MIDI service is already running (PID: {pid}).{NC}")
        return

    # A lock holder that the pid file forgot about is still a live service.
    probe = acquire_single_instance_lock(write_pid=False)
    if probe is None:
        print(f"{YELLOW}DJ S4 MIDI service is already running "
              f"(PID: {_pid_from_file(LOCK_FILE) or 'unknown'}).{NC}")
        return
    if probe != "unsupported":
        os.close(probe)

    # Clear leaked daemons from older builds before spawning a fresh one.
    # They never held this lock, so they cannot be the service we just probed.
    for stray in _iter_daemon_pids():
        try:
            os.kill(stray, signal.SIGTERM)
            log(f"Reaped stray DJ S4 MIDI daemon (PID: {stray})")
        except OSError:
            pass
    if _iter_daemon_pids():
        time.sleep(0.5)

    # Resolve the real card: hw:0,0 is only a fallback, never the assumption.
    resolved = resolve_port(port)
    detected, _, details = detect_dj_s4_port()
    if not detected and not rawmidi_path_for_port(resolved):
        print(f"{YELLOW}Warning: Kontrol S4 hardware not currently detected. "
              f"Starting daemon anyway in standby mode on {resolved}; it will attach when the unit appears.{NC}")
    else:
        print(f"{GREEN}✓ Kontrol S4 detected on {resolved}{f' ({details})' if details else ''}{NC}")

    cmd = [sys.executable, str(Path(__file__).resolve()), "--daemon", "--port", resolved]
    with open(LOG_FILE, "a", encoding="utf-8") as out:
        p = subprocess.Popen(cmd, stdout=out, stderr=out, start_new_session=True)

    PID_FILE.write_text(str(p.pid), encoding="utf-8")
    FIRST_SEEN_FLAG.touch(exist_ok=True)
    print(f"{GREEN}✓ Kontrol S4 MIDI Control Service successfully started (PID: {p.pid}, Port: {resolved}).{NC}")


def stop_daemon():
    running, pid = is_daemon_running()
    targets = set(_iter_daemon_pids())
    if pid:
        targets.add(pid)
    if not targets:
        print(f"{DIM}DJ S4 MIDI service is not currently running.{NC}")
        PID_FILE.unlink(missing_ok=True)
        try:
            STATE_FILE.write_text(json.dumps({"status": "stopped", "pid": 0}), encoding="utf-8")
        except Exception:
            pass
        return

    stopped: List[int] = []
    failed: List[str] = []
    for target in sorted(targets):
        try:
            os.kill(target, signal.SIGTERM)
            for _ in range(20):
                time.sleep(0.1)
                try:
                    os.kill(target, 0)
                except OSError:
                    break
            else:
                os.kill(target, signal.SIGKILL)
            stopped.append(target)
        except OSError as e:
            failed.append(f"{target}: {e}")

    PID_FILE.unlink(missing_ok=True)
    try:
        STATE_FILE.write_text(json.dumps({"status": "stopped", "pid": 0}), encoding="utf-8")
    except Exception:
        pass
    if stopped:
        print(f"{GREEN}✓ Kontrol S4 MIDI Control Service stopped (PID: {', '.join(map(str, stopped))}).{NC}")
    if failed:
        print(f"{RED}Error stopping MIDI service: {'; '.join(failed)}{NC}")


def print_status():
    detected, port, details = detect_dj_s4_port()
    rawmidi = rawmidi_path_for_port(port) or ""
    daemon_run, pid = is_daemon_running()
    player_run = is_player_running()

    print(f"\n{BOLD}{MAGENTA}=== DJ KONTROL S4 MIDI HARDWARE STATUS ==={NC}\n")
    print(f"  Device Name          : {BOLD}{WHITE}{DEFAULT_DEVICE_NAME}{NC}")
    print(f"  Hardware Port        : {BOLD}{CYAN}{port}{NC} "
          f"{DIM}(ALSA Rawmidi: {rawmidi or 'no node'}){NC}")
    if detected:
        print(f"  Hardware Connection  : {BOLD}{GREEN}✓ Detected & Connected{NC} {DIM}({details}){NC}")
    else:
        print(f"  Hardware Connection  : {BOLD}{YELLOW}⚠ Not Detected / Unplugged{NC}")

    if daemon_run:
        print(f"  MIDI Service Daemon  : {BOLD}{GREEN}✓ Active & Running{NC} {DIM}(PID: {pid}){NC}")
    else:
        print(f"  MIDI Service Daemon  : {BOLD}{RED}✗ Disabled / Stopped{NC}")

    if player_run:
        st = get_player_status()
        p_pct = st.get("pitch_percent", 0) if st else 0
        state = st.get("state", "unknown") if st else "unknown"
        print(f"  MP Audio Player      : {BOLD}{GREEN}✓ Connected{NC} {DIM}(State: {state}, Pitch: {p_pct:+d}%){NC}")
    else:
        print(f"  MP Audio Player      : {DIM}Offline / Waiting for launch{NC}")

    print(f"  Supported in v0.5.4  : {BOLD}{GREEN}Kontrol S4 Exclusively{NC}\n")


def print_user_guide():
    guide_path = Path(__file__).resolve().parent.parent / "docs" / "KONTROL_S4_USER_GUIDE.md"
    if guide_path.is_file():
        try:
            content = guide_path.read_text(encoding="utf-8")
            print(content)
            return
        except Exception:
            pass

    # Fallback built-in guide
    print(f"""
{BOLD}{MAGENTA}========================================================================{NC}
{BOLD}{WHITE}   🎛️  DJ KONTROL S4 GETTING STARTED GUIDE (v0.5.4){NC}
{BOLD}{MAGENTA}========================================================================{NC}

1. SWITCHING TO MIDI MODE ON THE S4:
   • Kontrol S4 MK1: Hold {BOLD}{YELLOW}SHIFT{NC} and press {BOLD}{YELLOW}BROWSE{NC} button.
   • Kontrol S4 MK2: Hold {BOLD}{YELLOW}SHIFT{NC} and press {BOLD}{YELLOW}PREVIEW{NC} button.
   • Confirmation: The two Loop Size Displays on the S4 will read {BOLD}{GREEN}ON{NC}.

2. HARDWARE ALSA PORT (AUTO-DETECTED):
   • The S4's card number depends on what else is in the machine. If HDMI
     audio or a USB interface is present it may be {BOLD}{CYAN}hw:1,0{NC} instead of {BOLD}{CYAN}hw:0,0{NC}.
   • MP Mix Manager detects the real port itself; nothing needs to be typed.
   • To verify in terminal: run '{BOLD}amidi -l{NC}' or '{BOLD}python3 scripts/dj_s4_midi.py --status{NC}'.

3. PITCH CONTROLS (LEFT & RIGHT DECKS):
   • Moving either Tempo/Pitch Fader adjusts the MP Audio Player DJ Pitch in real-time.
   • Range: {BOLD}-20% to +20%{NC} with rock-solid deadzone center snap at 0%.
   • Pitch Bend [+] / [-] buttons: Micro-nudge pitch up or down by 1%.
   • SYNC button: Instant Pitch Reset to 0%.

4. ESSENTIAL BUTTON MAPPINGS:
   • {BOLD}PLAY ▶{NC}               : Toggle Play / Pause in MP Audio Player
   • {BOLD}CUE ⏹{NC}                : Stop / Jump to start (0:00)
   • {BOLD}BROWSE Encoder{NC}        : Turn to jump tracks (Next/Prev); Push to Play
   • {BOLD}LOOP IN / OUT{NC}         : Quick Seek -10s / +10s
   • {BOLD}HOTCUES 1..4{NC}          : Instant Jump to 0%, 25%, 50%, 75% of mix
   • {BOLD}VOLUME FADERS{NC}         : Adjust Master / Track Volume (0% - 100%)
   • {BOLD}HEADPHONE CUE{NC}         : Instant Mute / Unmute
   • {BOLD}REC (Loop Recorder){NC}   : Toggle Live DJ Mix Recording

5. CONTROLLING FROM MP MIX ARCHIVE MANAGER:
   • Use Option 18 in the Main Menu to Enable or Disable MIDI Support.
   • Monitor live MIDI messages in real-time with '{BOLD}python3 scripts/dj_s4_midi.py --monitor{NC}'.
""")


def main():
    parser = argparse.ArgumentParser(description="Kontrol S4 MIDI Controller Daemon for MP Mix Manager")
    parser.add_argument("--daemon", action="store_true", help="Run MIDI event listener loop")
    parser.add_argument("--start", action="store_true", help="Start background daemon")
    parser.add_argument("--stop", action="store_true", help="Stop background daemon")
    parser.add_argument("--restart", action="store_true", help="Restart background daemon")
    parser.add_argument("--status", action="store_true", help="Show hardware and service status")
    parser.add_argument("--monitor", action="store_true", help="Interactive live test and packet monitor")
    parser.add_argument("--guide", action="store_true", help="Print Kontrol S4 User Guide")
    parser.add_argument("--port", type=str, default=None,
                        help="ALSA MIDI port (default: auto-detect the S4's card)")
    parser.add_argument("--print-port", action="store_true",
                        help="Print the auto-detected ALSA port and exit")
    parser.add_argument("--detect-line", action="store_true",
                        help="Print PORT|RAWMIDI_NODE|DETAILS for shell menus")

    args = parser.parse_args()

    if args.print_port:
        detected, port, _ = detect_dj_s4_port()
        print(resolve_port(args.port) if detected else "")
        sys.exit(0 if detected else 1)
    if args.detect_line:
        detected, port, details = detect_dj_s4_port()
        raw = (rawmidi_path_for_port(port) or "") if detected else ""
        print(f"{port if detected else ''}|{raw}|{details}")
        return
    if args.start:
        start_daemon(port=args.port)
        return
    if args.stop:
        stop_daemon()
        return
    if args.restart:
        stop_daemon()
        time.sleep(0.5)
        start_daemon(port=args.port)
        return
    if args.status:
        print_status()
        return
    if args.guide:
        print_user_guide()
        return
    if args.monitor:
        running, pid = is_daemon_running()
        if running:
            print(f"{YELLOW}The MIDI service is already running (PID: {pid}) and owns the rawmidi port.{NC}")
            print(f"{DIM}Stop it first: python3 {Path(__file__).name} --stop{NC}")
            return
        ctrl = DJS4MidiController(port=args.port)
        try:
            ctrl.start_loop(monitor_mode=True)
        except KeyboardInterrupt:
            print(f"\n{GREEN}Exited monitor mode.{NC}")
        return

    if args.daemon:
        lock_fd = acquire_single_instance_lock()
        if lock_fd is None:
            log(f"Another DJ S4 MIDI daemon (pid {_pid_from_file(LOCK_FILE)}) already owns the device; exiting.")
            print(f"{YELLOW}DJ S4 MIDI service is already running.{NC}")
            return
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        ctrl = DJS4MidiController(port=args.port)

        def _sig_handler(sig, frame):
            ctrl.running = False
            PID_FILE.unlink(missing_ok=True)
            sys.exit(0)

        signal.signal(signal.SIGTERM, _sig_handler)
        signal.signal(signal.SIGINT, _sig_handler)

        try:
            ctrl.start_loop(monitor_mode=False)
        finally:
            PID_FILE.unlink(missing_ok=True)
        return

    # Default action if no args
    print_status()

if __name__ == "__main__":
    main()
