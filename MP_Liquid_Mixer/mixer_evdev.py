#!/usr/bin/env python3
"""Kontrol S4 native fader input for MP Liquid Mixer 0.0.1.

The S4 does not need MIDI mode for its faders. The snd-usb-caiaq driver
publishes them as absolute axes on the controller's evdev node, which is
readable while the device stays in its normal native mode:

    axis 18 -> channel A volume
    axis 17 -> channel B volume
    axis 19 -> channel C volume
    axis 16 -> channel D volume
    axis 23 -> crossfader

(code/name table from the S4 MK1 Linux driver: Slider.cpp). Values run 0-4095;
0 is bottom/left. The mapping can be overridden per control, and each control
can be inverted, in ~/.config/mix-manager/liquid_mixer_s4.json.

Reading evdev is shared: the standalone DJ MIDI service, DJ itself,
and this mixer can all open the node at the same time. Nothing has to be
stopped, and MIDI mode is irrelevant here.
"""
from __future__ import annotations

import json
import os
import select
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

MIXER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MIXER_DIR.parent
CONFIG_DIR = Path.home() / ".config" / "mix-manager"
MAPPING_PATH = CONFIG_DIR / "liquid_mixer_s4.json"
LOG_PATH = CONFIG_DIR / "liquid_mixer_s4.log"

DEVICE_NAME = "Kontrol S4"

# From the S4 MK1 driver's Slider.cpp. The physical channels are not in axis
# order: the USB report lists channel D first and channel A third.
DEFAULT_MAPPING: Dict[str, Any] = {
    "channels": {"A": 18, "B": 17, "C": 19, "D": 16},
    "crossfader": 23,
    "invert": {"A": False, "B": False, "C": False, "D": False, "crossfader": False},
    # The browse knob is a 0-15 quadrature counter, not an absolute fader.
    "browse": 54,
    # The two long-throw tempo faders (Slider.cpp: SLIDER PITCH CH1/CH3 and
    # CH2/CH4), plus the EV_KEY buttons (CH1_PLAY, CH2_PLAY, BROWSE_KNOB,
    # CH1_SYN, CH2_SYNC, CH1_SHIFT, CH2_SHIFT).
    "pitch": {"A": 21, "B": 22},
    "buttons": {"play:A": 263, "play:B": 319, "load": 292,
                "sync:A": 259, "sync:B": 315, "shift:A": 257, "shift:B": 313},
}

AXIS_MAX = 4095
# A 12-bit fader reports small jitter even when untouched (a few counts).
# Ignoring sub-0.4% changes keeps the channel gain and the deck IPC quiet.
AXIS_DEADBAND = 16


def _log(message: str) -> None:
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


class AxisMapping:
    """Which evdev axis drives which mixer control, and in which direction."""

    def __init__(self, data: Optional[Dict[str, Any]] = None) -> None:
        raw = data if isinstance(data, dict) else {}
        channels = raw.get("channels") if isinstance(raw.get("channels"), dict) else {}
        invert = raw.get("invert") if isinstance(raw.get("invert"), dict) else {}
        self.entries: Dict[int, Dict[str, Any]] = {}
        for label in ("A", "B", "C", "D"):
            self._add(f"channel:{label}", channels.get(label, DEFAULT_MAPPING["channels"][label]),
                      bool(invert.get(label, False)))
        self._add("crossfader", raw.get("crossfader", DEFAULT_MAPPING["crossfader"]),
                  bool(invert.get("crossfader", False)))
        self.pitch_axes: Dict[str, int] = {}
        self.pitch_invert: Dict[str, bool] = {}
        pitch_cfg = raw.get("pitch") if isinstance(raw.get("pitch"), dict) else {}
        for label in ("A", "B"):
            try:
                axis = int(pitch_cfg.get(label, DEFAULT_MAPPING["pitch"][label]))
            except (TypeError, ValueError):
                axis = int(DEFAULT_MAPPING["pitch"][label])
            self.pitch_axes[label] = axis
            self.pitch_invert[label] = bool(invert.get(f"pitch:{label}", invert.get("pitch", False)))
        self.buttons: Dict[int, str] = {}
        buttons = raw.get("buttons") if isinstance(raw.get("buttons"), dict) else {}
        for action in ("play:A", "play:B", "load", "sync:A", "sync:B", "shift:A", "shift:B"):
            self._add_button(action, buttons.get(action, DEFAULT_MAPPING["buttons"][action]))
        try:
            self.browse_axis = int(raw.get("browse", DEFAULT_MAPPING["browse"]))
        except (TypeError, ValueError):
            self.browse_axis = int(DEFAULT_MAPPING["browse"])
        self.browse_invert = bool(invert.get("browse", False))

    def _add(self, action: str, code: Any, invert: bool) -> None:
        try:
            axis = int(code)
        except (TypeError, ValueError):
            return
        if not (0 <= axis <= 1023):
            return
        self.entries[axis] = {"action": action, "invert": bool(invert)}

    def _add_button(self, action: str, code: Any) -> None:
        try:
            key = int(code)
        except (TypeError, ValueError):
            return
        if 0 <= key <= 1023:
            self.buttons[key] = action

    def resolve(self, axis: int) -> Optional[Tuple[str, bool]]:
        entry = self.entries.get(int(axis))
        return (entry["action"], entry["invert"]) if entry else None

    def resolve_button(self, code: int) -> Optional[str]:
        return self.buttons.get(int(code))

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"channels": {}, "crossfader": 0, "invert": {}, "buttons": {}}
        for axis, entry in self.entries.items():
            action = entry["action"]
            if action == "crossfader":
                out["crossfader"] = axis
            else:
                label = action.split(":", 1)[1]
                out["channels"][label] = axis
            out["invert"]["crossfader" if action == "crossfader" else action.split(":", 1)[1]] = entry["invert"]
        out["buttons"] = {action: code for code, action in self.buttons.items()}
        out["browse"] = self.browse_axis
        out["pitch"] = dict(self.pitch_axes)
        return out

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "AxisMapping":
        try:
            return cls(json.loads(Path(path or MAPPING_PATH).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, UnicodeError):
            return cls()


def scale_axis(value: int, maximum: int = AXIS_MAX, invert: bool = False) -> float:
    """evdev absolute value to 0.0-1.0."""
    maximum = max(1, int(maximum))
    value = max(0, min(maximum, int(value)))
    ratio = value / float(maximum)
    return 1.0 - ratio if invert else ratio


def apply_action(model: Any, action: str, value: float) -> bool:
    """Apply one mapped fader move to the mixer model."""
    value = 0.0 if value < 0.0 else (1.0 if value > 1.0 else float(value))
    if action == "crossfader":
        model.set_crossfade(value)
        return True
    if action.startswith("channel:"):
        return bool(model.set_channel(action.split(":", 1)[1], value))
    return False


def find_device(explicit: Optional[str] = None):
    """The S4's evdev InputDevice, or None when it is not connected/readable."""
    try:
        import evdev
    except ImportError:
        return None
    if explicit:
        try:
            return evdev.InputDevice(explicit)
        except OSError:
            return None
    try:
        paths = evdev.list_devices()
    except Exception:
        return None
    for path in paths:
        try:
            device = evdev.InputDevice(path)
        except OSError:
            continue
        if DEVICE_NAME in (device.name or ""):
            return device
        device.close()
    return None


class S4AxisListener:
    """Read the S4's absolute axes and report mapped fader movement."""

    def __init__(self, mapping: Optional[AxisMapping] = None,
                 on_change: Optional[Callable[[str, float], None]] = None,
                 device_path: Optional[str] = None) -> None:
        self.mapping = mapping or AxisMapping.load()
        self.on_change = on_change
        self.device_path = device_path or ""
        self.device = None
        self.status = "starting"
        self.last_event = 0.0
        self.last_message = ""
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._log_seen: Dict[int, Tuple[float, int]] = {}
        self._last_raw: Dict[int, int] = {}
        self._browse_last: Optional[int] = None

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="mixer-s4", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            try:
                self._thread.join(timeout=2.0)
            except RuntimeError:
                pass
        if self.device is not None:
            try:
                self.device.close()
            except Exception:
                pass
            self.device = None

    # -- reader -----------------------------------------------------------
    def _run(self) -> None:
        backoff = 1.0
        while self._running:
            if self.device is None:
                self.device = find_device(self.device_path or None)
                if self.device is None:
                    self.status = "waiting for S4"
                    time.sleep(backoff)
                    backoff = min(backoff * 1.5, 10.0)
                    continue
            self.status = f"native {self.device.path}"
            _log(f"listening on {self.device.path} ({self.device.name})")
            _log(f"mapping: {self.mapping.to_dict()}")
            backoff = 1.0
            self._emit_initial()
            try:
                while self._running:
                    r, _, _ = select.select([self.device.fd], [], [], 0.25)
                    if not r:
                        continue
                    for event in self.device.read():
                        if event.type == 3:  # EV_ABS
                            self._handle(event.code, event.value)
                        elif event.type == 1:  # EV_KEY
                            self._handle_button(event.code, event.value)
            except OSError as exc:
                _log(f"device read error: {exc}")
                self.status = "reconnecting"
            except Exception as exc:
                _log(f"unexpected reader error: {exc!r}")
                self.status = "reconnecting"
            finally:
                try:
                    self.device.close()
                except Exception:
                    pass
                self.device = None
            if self._running:
                time.sleep(1.0)

    def _axis_max(self, code: int) -> int:
        try:
            info = self.device.absinfo(code)
            if info is not None and int(info.max) > 0:
                return int(info.max)
        except Exception:
            pass
        return AXIS_MAX

    def _emit_initial(self) -> None:
        """Publish each mapped fader's current position on attach.

        Without this the mixer would keep its previous model values until the
        fader moves, so the on-screen strip would not match the hardware.
        """
        for code in sorted(self.mapping.entries):
            try:
                info = self.device.absinfo(code)
                value = int(info.value)
            except Exception:
                continue
            self._handle(code, value, initial=True)
        for axis in sorted(self.mapping.pitch_axes.values()):
            try:
                info = self.device.absinfo(axis)
                value = int(info.value)
            except Exception:
                continue
            self._handle(axis, value, initial=True)

    def _handle(self, code: int, value: int, initial: bool = False) -> None:
        if code == self.mapping.browse_axis:
            self._handle_browse(value)
            return
        for label, axis in self.mapping.pitch_axes.items():
            if code != axis:
                continue
            previous = self._last_raw.get(code)
            if not initial and previous is not None and abs(int(value) - previous) < 6:
                return
            self._last_raw[code] = int(value)
            maximum = self._axis_max(code)
            ratio = max(0.0, min(1.0, int(value) / float(max(1, maximum))))
            percent = (ratio - 0.5) * 40.0
            if abs(percent) < 0.4:
                percent = 0.0
            if self.mapping.pitch_invert.get(label):
                percent = -percent
            self.last_event = time.time()
            self.last_message = f"pitch {label} {percent:+.1f}%"
            _log(f"pitch axis {code} = {value} -> pitch:{label} {percent:+.1f}%")
            if self.on_change is not None:
                try:
                    self.on_change(f"pitch:{label}", percent)
                except Exception as exc:
                    _log(f"pitch callback failed: {exc!r}")
            return
        resolved = self.mapping.resolve(code)
        if resolved is None:
            self._log_axis(code, value, "")
            return
        previous = self._last_raw.get(code)
        if not initial and previous is not None and abs(int(value) - previous) < AXIS_DEADBAND:
            return
        self._last_raw[code] = int(value)
        action, invert = resolved
        scaled = scale_axis(value, self._axis_max(code), invert)
        self._log_axis(code, value, f"{action} {scaled:.2f}")
        self.last_event = time.time()
        self.last_message = f"axis {code} = {value} -> {action} {scaled:.2f}"
        if self.on_change is not None:
            try:
                self.on_change(action, scaled)
            except Exception as exc:
                _log(f"callback failed for {action}: {exc!r}")

    def _handle_browse(self, value: int) -> None:
        """One detent of the browse knob -> one step up or down the playlist."""
        previous = self._browse_last
        self._browse_last = int(value)
        if previous is None:
            return
        maximum = self._axis_max(self.mapping.browse_axis)
        if maximum <= 15:
            delta = (int(value) - previous) % (maximum + 1)
            if delta == 0:
                return
            direction = 1.0 if delta <= (maximum + 1) // 2 else -1.0
        else:
            delta = int(value) - previous
            if delta == 0:
                return
            direction = 1.0 if delta > 0 else -1.0
        if self.mapping.browse_invert:
            direction = -direction
        self.last_event = time.time()
        self.last_message = f"browse {int(direction):+d}"
        _log(f"browse axis {self.mapping.browse_axis} = {value} -> {int(direction):+d}")
        if self.on_change is not None:
            try:
                self.on_change("browse", direction)
            except Exception as exc:
                _log(f"browse callback failed: {exc!r}")

    def _handle_button(self, code: int, value: int) -> None:
        # 0 release, 1 press, 2 autorepeat. Shift needs its release so the
        # mixer can use Shift+Sync for "make this deck the master".
        action = self.mapping.resolve_button(code)
        if action is None:
            return
        if value not in (0, 1):
            return
        if value == 0 and not action.startswith("shift:"):
            return
        self.last_event = time.time()
        self.last_message = f"button {code} -> {action}"
        _log(f"button {code} -> {action}")
        if self.on_change is not None:
            try:
                self.on_change(action, 1.0 if value else 0.0)
            except Exception as exc:
                _log(f"button callback failed for {action}: {exc!r}")

    def _log_axis(self, code: int, raw: int, action: str) -> None:
        now = time.time()
        previous = self._log_seen.get(code)
        if previous and now - previous[0] < 0.5 and abs(raw - previous[1]) < 4:
            return
        self._log_seen[code] = (now, raw)
        suffix = f" -> {action}" if action else " (unmapped)"
        _log(f"axis {code} = {raw}{suffix}")
