#!/usr/bin/env python3
"""
Game controller bridge for the MP Mix Archive Manager (Linux).

Xbox, PlayStation (DualShock 4 and DualSense) and Steam controllers are read
through evdev and turned into the key names the manager's menus already read:
UP, DOWN, LEFT, RIGHT, ENTER, ESC, SPACE, PAGE_UP, PAGE_DOWN, CHAR:n and
CHAR:[. Each key is written to stdout on its own line, so a menu reads a pad
exactly like a keyboard.

    gamepad_bridge.py            key stream used by Mix_Archive_Manager.sh
    gamepad_bridge.py --list     controllers connected right now
    gamepad_bridge.py --monitor  live test: every button, stick and trigger
    gamepad_bridge.py --doctor   why a pad that is on is not showing up

A pad only appears here once the Linux kernel has bound a driver to it. If a
controller is switched on but not listed, `--doctor` names the missing driver.

Pads can be switched on, plugged in or removed at any time. The bridge rescans
every two seconds and exits once the manager that started it has gone away.

    D-pad or left stick          move: UP, DOWN, LEFT, RIGHT (repeats while held)
    A / Cross / Nintendo A       select (ENTER)
    B / Circle / Nintendo B      back (ESC)
    X / Square / Nintendo Y      play or pause (SPACE), the left face button
    LB / L1, RB / R1             page up, page down
    LT / L2 / ZL, RT / R2 / ZR   previous track (CHAR:[), next track (CHAR:n)

A Steam Controller arrives in "lizard mode", where it reports itself as a
keyboard and mouse instead of a pad. Opening its gamepad node -- which this
bridge does -- makes hid_steam turn lizard mode off, so the pad starts driving
the menus without Steam running. Its left pad and D-pad both arrive as
BTN_DPAD_* presses and its triggers as ABS_HAT2X / ABS_HAT2Y.
"""

import argparse
import gzip
import os
import re
import select
import struct
import sys
import time

try:
    import fcntl
except ImportError:  # Windows and macOS: evdev is Linux-only
    fcntl = None

EV_KEY = 0x01
EV_ABS = 0x03

BTN_SOUTH = 0x130  # A / Cross
BTN_EAST = 0x131  # B / Circle
BTN_NORTH = 0x133
BTN_WEST = 0x134
BTN_TL = 0x136  # LB / L1
BTN_TR = 0x137  # RB / R1
BTN_TL2 = 0x138  # ZL (digital)
BTN_TR2 = 0x139  # ZR (digital)
BTN_SELECT = 0x13A
BTN_START = 0x13B
BTN_MODE = 0x13C
BTN_THUMBL = 0x13D
BTN_THUMBR = 0x13E

ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05
ABS_HAT0X, ABS_HAT0Y = 0x10, 0x11
# hid_steam reports the triggers on HAT2 (0x14/0x15), not HAT1.
ABS_HAT2X, ABS_HAT2Y = 0x14, 0x15
HAT_CODES = (ABS_HAT0X, ABS_HAT0Y)
# The Steam Controller sends its D-pad and left pad as BTN_DPAD_* key events.
BTN_DPAD_UP, BTN_DPAD_DOWN, BTN_DPAD_LEFT, BTN_DPAD_RIGHT = 0x220, 0x221, 0x222, 0x223
DPAD_BUTTONS = {BTN_DPAD_UP: "UP", BTN_DPAD_DOWN: "DOWN", BTN_DPAD_LEFT: "LEFT", BTN_DPAD_RIGHT: "RIGHT"}

# Confirm and back follow the label each player reads: A, Cross or the Nintendo A
# confirms; B, Circle or the Nintendo B goes back. The drivers do not agree on the
# left face button (xpad reports X as BTN_NORTH, the PlayStation driver reports
# Square as BTN_WEST), so the codes are kept per family.
LAYOUTS = {
    "xbox": {"confirm": BTN_SOUTH, "back": BTN_EAST, "play_pause": BTN_NORTH, "digital_triggers": {}},
    "playstation": {"confirm": BTN_SOUTH, "back": BTN_EAST, "play_pause": BTN_WEST, "digital_triggers": {}},
    "nintendo": {
        "confirm": BTN_EAST,
        "back": BTN_SOUTH,
        "play_pause": BTN_WEST,
        "digital_triggers": {BTN_TL2: "CHAR:[", BTN_TR2: "CHAR:n"},
    },
    "steam": {"confirm": BTN_SOUTH, "back": BTN_EAST, "play_pause": BTN_NORTH, "digital_triggers": {}},
}
SHOULDER_KEYS = {BTN_TL: "PAGE_UP", BTN_TR: "PAGE_DOWN"}
TRIGGER_KEYS = {ABS_Z: "CHAR:[", ABS_RZ: "CHAR:n", ABS_HAT2Y: "CHAR:[", ABS_HAT2X: "CHAR:n"}

STICK_PRESS, STICK_RELEASE = 0.55, 0.35
HAT_PRESS, HAT_RELEASE = 0.50, 0.35
TRIGGER_PRESS, TRIGGER_RELEASE = 0.60, 0.40
REPEAT_DELAY, REPEAT_INTERVAL = 0.40, 0.12
DUPLICATE_WINDOW = 0.06
RESCAN_INTERVAL = 2.0

DEVICE_TABLE = "/proc/bus/input/devices"
FAMILY_LABELS = {"xbox": "Xbox", "playstation": "PlayStation", "nintendo": "Nintendo", "steam": "Steam"}
CODE_NAMES = {
    BTN_SOUTH: "BTN_SOUTH", BTN_EAST: "BTN_EAST", BTN_NORTH: "BTN_NORTH", BTN_WEST: "BTN_WEST",
    BTN_TL: "BTN_TL", BTN_TR: "BTN_TR", BTN_TL2: "BTN_TL2", BTN_TR2: "BTN_TR2",
    BTN_SELECT: "BTN_SELECT", BTN_START: "BTN_START", BTN_MODE: "BTN_MODE",
    BTN_THUMBL: "BTN_THUMBL", BTN_THUMBR: "BTN_THUMBR",
    ABS_X: "ABS_X", ABS_Y: "ABS_Y", ABS_Z: "ABS_Z", ABS_RX: "ABS_RX", ABS_RY: "ABS_RY",
    ABS_RZ: "ABS_RZ", ABS_HAT0X: "ABS_HAT0X", ABS_HAT0Y: "ABS_HAT0Y",
    ABS_HAT2X: "ABS_HAT2X/RT", ABS_HAT2Y: "ABS_HAT2Y/LT",
    BTN_DPAD_UP: "BTN_DPAD_UP", BTN_DPAD_DOWN: "BTN_DPAD_DOWN",
    BTN_DPAD_LEFT: "BTN_DPAD_LEFT", BTN_DPAD_RIGHT: "BTN_DPAD_RIGHT",
}
EVENT = struct.Struct("@llHHi")  # struct input_event: timeval, type, code, value
ABSINFO_SIZE = 24  # struct input_absinfo


def parse_device_table(text):
    """One dict per block of /proc/bus/input/devices."""
    devices = []
    for block in text.split("\n\n"):
        device = {"vendor": 0, "name": "", "events": [], "keys": []}
        for line in block.splitlines():
            if line.startswith("I: "):
                found = re.search(r"Vendor=([0-9a-fA-F]+)", line)
                if found:
                    device["vendor"] = int(found.group(1), 16)
            elif line.startswith("N: Name="):
                device["name"] = line[len("N: Name="):].strip().strip('"')
            elif line.startswith("H: Handlers="):
                device["events"] = [int(number) for number in re.findall(r"\bevent(\d+)", line)]
            elif line.startswith("B: KEY="):
                device["keys"] = line[len("B: KEY="):].split()
        if device["events"]:
            devices.append(device)
    return devices


def has_key(words, code):
    """Whether `code` is set in a /proc KEY bitmap (64-bit words, highest first)."""
    index, bit = divmod(code, 64)
    position = len(words) - 1 - index
    return 0 <= position < len(words) and bool((int(words[position], 16) >> bit) & 1)


def controller_family(vendor, name):
    lowered = name.lower()
    if vendor == 0x045E or "xbox" in lowered or "x-box" in lowered:
        return "xbox"
    if vendor == 0x057E or "nintendo" in lowered or "joy-con" in lowered or "pro controller" in lowered:
        return "nintendo"
    if vendor == 0x054C or "playstation" in lowered or "dualshock" in lowered or "dualsense" in lowered:
        return "playstation"
    if vendor == 0x28DE or "steam" in lowered:
        return "steam"
    return None


def find_controllers(devices):
    """(event path, name, family) for each gamepad node, the one with an A button."""
    found = []
    for device in devices:
        family = controller_family(device["vendor"], device["name"])
        if family is None or not has_key(device["keys"], BTN_SOUTH):
            continue
        for number in device["events"]:
            found.append((f"/dev/input/event{number}", device["name"], family))
    return found


def read_device_table(path=DEVICE_TABLE):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return parse_device_table(handle.read())
    except OSError:
        return []


def abs_range(fd, code):
    """(minimum, maximum) of an absolute axis, or None when the driver does not report one."""
    if fcntl is None:
        return None
    buffer = bytearray(ABSINFO_SIZE)
    try:
        fcntl.ioctl(fd, (2 << 30) | (ABSINFO_SIZE << 16) | (ord("E") << 8) | (0x40 + code), buffer)
    except OSError:
        return None
    _value, low, high = struct.unpack_from("iii", buffer)
    return (low, high) if high > low else None


class PadState:
    """Keys for one controller. Pure logic: events in, key names out."""

    def __init__(self, family, ranges=None):
        self.family = family
        self.ranges = ranges or {}
        self.layout = LAYOUTS[family]
        self.hat = {ABS_HAT0X: 0.0, ABS_HAT0Y: 0.0}
        self.stick = {ABS_X: 0.0, ABS_Y: 0.0}
        self.sources = {"hat": None, "stick": None, "dpad": None}
        self.dpad_held = []
        self.direction = None
        self.repeat_at = None
        self.trigger_held = {code: False for code in TRIGGER_KEYS}

    def _level(self, code, value, span=None):
        """Triggers scale to 0..1, sticks and hats to -1..1."""
        low, high = self.ranges.get(code, span or (-32768, 32767))
        if high <= low:
            return 0.0
        if code in TRIGGER_KEYS:
            return (value - low) / (high - low)
        return (value - (low + high) / 2) / ((high - low) / 2)

    def _hat_level(self, code, value):
        """-1..1 for a hat. Windows-style hats send -1/0/1; a Steam pad sends an analog value."""
        span = self.ranges.get(code)
        if span and span[1] - span[0] > 2:
            return self._level(code, value)
        return float((value > 0) - (value < 0))

    def _hat_direction(self):
        """The hat is analog on a Steam pad, so it needs the same hysteresis as a stick."""
        x = self._hat_level(ABS_HAT0X, self.hat[ABS_HAT0X])
        y = self._hat_level(ABS_HAT0Y, self.hat[ABS_HAT0Y])
        held = self.sources["hat"] is not None
        limit = HAT_RELEASE if held else HAT_PRESS
        if abs(x) < limit and abs(y) < limit:
            return None
        if abs(y) >= abs(x):
            return "DOWN" if y > 0 else "UP"
        return "RIGHT" if x > 0 else "LEFT"

    def _stick_direction(self):
        x, y = self.stick[ABS_X], self.stick[ABS_Y]
        held = self.sources["stick"] is not None
        if max(abs(x), abs(y)) < (STICK_RELEASE if held else STICK_PRESS):
            return None
        if abs(y) >= abs(x):
            return "DOWN" if y > 0 else "UP"
        return "RIGHT" if x > 0 else "LEFT"

    def _update(self, source, direction, now):
        self.sources[source] = direction
        current = self.sources["dpad"] or self.sources["hat"] or self.sources["stick"]
        if current == self.direction:
            return []
        self.direction = current
        self.repeat_at = now + REPEAT_DELAY if current else None
        return [current] if current else []

    def handle(self, event_type, code, value, now):
        """Keys for one input event: button presses and direction changes only."""
        if event_type == EV_KEY:
            release = value == 0
            if code in DPAD_BUTTONS:
                if value == 2:
                    return []  # kernel autorepeat: the pad already owns the repeat timing
                direction = DPAD_BUTTONS[code]
                if direction in self.dpad_held:
                    self.dpad_held.remove(direction)
                if not release:
                    self.dpad_held.append(direction)
                return self._update("dpad", self.dpad_held[-1] if self.dpad_held else None, now)
            if value != 1:
                return []
            layout = self.layout
            if code == layout["confirm"]:
                return ["ENTER"]
            if code == layout["back"]:
                return ["ESC"]
            if code == layout["play_pause"]:
                return ["SPACE"]
            key = SHOULDER_KEYS.get(code) or layout["digital_triggers"].get(code)
            return [key] if key else []
        if event_type != EV_ABS:
            return []
        if code in HAT_CODES:
            self.hat[code] = value
            return self._update("hat", self._hat_direction(), now)
        if code in self.stick:
            self.stick[code] = self._level(code, value)
            return self._update("stick", self._stick_direction(), now)
        if code in TRIGGER_KEYS:
            level = self._level(code, value, (0, 255))
            if not self.trigger_held[code] and level >= TRIGGER_PRESS:
                self.trigger_held[code] = True
                return [TRIGGER_KEYS[code]]
            if self.trigger_held[code] and level <= TRIGGER_RELEASE:
                self.trigger_held[code] = False
        return []

    def next_repeat(self):
        return self.repeat_at if self.direction else None

    def repeat(self, now):
        """The held direction again, once its repeat time has come."""
        if self.direction is None or self.repeat_at is None or now < self.repeat_at:
            return []
        self.repeat_at = now + REPEAT_INTERVAL
        return [self.direction]


class Pad:
    def __init__(self, path, fd, name, family, ranges):
        self.path = path
        self.fd = fd
        self.name = name
        self.family = family
        self.label = FAMILY_LABELS[family]
        self.state = PadState(family, ranges)


class Bridge:
    """Reads every connected pad and passes its keys to `write`, or prints them when monitoring."""

    def __init__(self, write, monitor=False):
        self.write = write
        self.monitor = monitor
        self.pads = {}
        self.refused = set()
        self.last_sent = {}

    def run(self):
        parent = os.getppid()
        next_scan = 0.0
        while os.getppid() == parent:
            now = time.monotonic()
            if now >= next_scan:
                self.rescan()
                next_scan = now + RESCAN_INTERVAL
            wait = next_scan - now
            repeats = [t for t in (pad.state.next_repeat() for pad in self.pads.values()) if t is not None]
            if repeats:
                wait = min(wait, min(repeats) - now)
            by_fd = {pad.fd: pad for pad in self.pads.values()}
            try:
                ready, _, _ = select.select(list(by_fd), [], [], max(0.0, wait))
            except InterruptedError:
                ready = []
            now = time.monotonic()
            for fd in ready:
                self._read(by_fd[fd], now)
            for pad in list(self.pads.values()):
                for key in pad.state.repeat(now):
                    self._repeat(pad, key, now)

    def rescan(self):
        wanted = {path: (name, family) for path, name, family in find_controllers(read_device_table())}
        for path in [path for path in self.pads if path not in wanted]:
            self._forget(self.pads[path])
        for path, (name, family) in wanted.items():
            if path not in self.pads:
                self._adopt(path, name, family)

    def _adopt(self, path, name, family):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as error:
            if self.monitor and path not in self.refused:
                print(f"Cannot read {path}: {error.strerror}", file=sys.stderr)
            self.refused.add(path)
            return
        self.refused.discard(path)
        ranges = {}
        for code in (ABS_X, ABS_Y, ABS_Z, ABS_RZ, ABS_RX, ABS_RY, *HAT_CODES, ABS_HAT2X, ABS_HAT2Y):
            span = abs_range(fd, code)
            if span:
                ranges[code] = span
        self.pads[path] = Pad(path, fd, name, family, ranges)
        if self.monitor:
            print(f"Connected: {FAMILY_LABELS[family]} controller ({name}) at {path}")

    def _forget(self, pad):
        self.pads.pop(pad.path, None)
        try:
            os.close(pad.fd)
        except OSError:
            pass
        if self.monitor:
            print(f"Disconnected: {pad.label} controller ({pad.name})")

    def _read(self, pad, now):
        try:
            data = os.read(pad.fd, EVENT.size * 64)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self._forget(pad)
            return
        for offset in range(0, len(data) - EVENT.size + 1, EVENT.size):
            _sec, _usec, event_type, code, value = EVENT.unpack_from(data, offset)
            keys = pad.state.handle(event_type, code, value, now)
            sent = [key for key in keys if self._send(pad, key, now)]
            if self.monitor and (event_type == EV_KEY or keys):
                self._report(pad, event_type, code, value, sent)

    def _repeat(self, pad, key, now):
        if self._send(pad, key, now) and self.monitor:
            print(f"{pad.label:<11} held    -> {key}")

    def _send(self, pad, key, now):
        """Send one key. False when the same press arrived from another node just now."""
        last = self.last_sent.get(key)
        if last and last[0] != pad.path and now - last[1] < DUPLICATE_WINDOW:
            return False
        self.last_sent[key] = (pad.path, now)
        if not self.monitor:
            return self.write(key)
        return True

    def _report(self, pad, event_type, code, value, sent):
        name = CODE_NAMES.get(code, hex(code))
        if event_type == EV_KEY:
            detail = f"{name} {({1: 'pressed', 0: 'released'}).get(value, f'value {value}')}"
        else:
            detail = f"{name} {value}"
        arrow = f"  -> {', '.join(sent)}" if sent else ""
        print(f"{pad.label:<11} {detail}{arrow}")


def write_key(key):
    try:
        os.write(sys.stdout.fileno(), (key + "\n").encode("ascii"))
        return True
    except BlockingIOError:
        return False  # the manager is busy and its queue is full: drop this repeat
    except OSError:
        raise SystemExit(0)  # the manager closed its end of the pipe


def list_controllers():
    if not sys.platform.startswith("linux"):
        print("Game controllers are read through Linux evdev only.")
        return 0
    found = find_controllers(read_device_table())
    if not found:
        print("No Xbox, PlayStation, Nintendo or Steam controller is connected.")
    for path, name, family in found:
        print(f"{FAMILY_LABELS[family]:<11} {path:<18} {name}")
    return 0


def controllers_by_vendor(devices, vendor):
    """Input nodes whose vendor matches, whether or not a driver bound them as a pad."""
    return [device for device in devices if device["vendor"] == vendor]


def kernel_has(option):
    """Whether the running kernel was built with CONFIG_<option> set."""
    try:
        with gzip.open("/proc/config.gz", "rt", errors="replace") as handle:
            text = handle.read()
    except OSError:
        try:
            with open(f"/boot/config-{os.uname().release}", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            return None  # cannot tell
    for line in text.splitlines():
        if line.startswith(f"CONFIG_{option}="):
            return True
    return False


def driver_problem(driver):
    """What to do when a controller's driver is not loaded."""
    try:
        with open(f"/lib/modules/{os.uname().release}/modules.dep", encoding="utf-8", errors="replace") as handle:
            have_module = driver in handle.read()
    except OSError:
        have_module = True  # cannot tell; assume the module exists
    if have_module:
        return f"Load it with: sudo modprobe {driver.replace('_', '-')}"
    return (f"This kernel was built without it (CONFIG_{driver.upper()} is not set). "
            "A kernel that enables it is needed; Fedora's stock kernel has it.")


def doctor():
    """Explain every pad that is physically present but not usable by the manager."""
    if not sys.platform.startswith("linux"):
        print("Game controllers are read through Linux evdev only.")
        return 0
    devices = read_device_table()
    found = find_controllers(devices)
    print(f"Controllers the Manager can read: {len(found)}")
    for path, name, family in found:
        print(f"  {FAMILY_LABELS[family]:<11} {path:<18} {name}")
    problems = []
    for vendor, driver in ((0x045E, "xpad"), (0x054C, "hid_playstation"),
                           (0x057E, "hid_nintendo"), (0x28DE, "hid_steam")):
        nodes = controllers_by_vendor(devices, vendor)
        if not nodes:
            continue
        if any(has_key(device["keys"], BTN_SOUTH) for device in nodes):
            continue
        # A Steam Controller sits in lizard mode (a keyboard and a mouse) until
        # its gamepad node is opened, so the driver can be bound and still look
        # missing. Only call it a missing driver when the module is absent.
        if vendor == 0x28DE and any("valve" in device["name"].lower()
                                    or "steam" in device["name"].lower() for device in nodes):
            problems.append((driver, nodes, "lizard"))
            continue
        problems.append((driver, nodes, "missing"))
    if not problems:
        if not found:
            print("No controller is connected. Switch one on, then run this again.")
        else:
            print("Every connected pad has a driver. Press a button in the live test if one is silent.")
        return 0
    print()
    for driver, nodes, kind in problems:
        for device in nodes:
            print(f"Present but not usable: {device['name']}")
        if kind == "lizard":
            print("  Driver: hid_steam (bound, but the pad is emulating a keyboard and mouse)")
            print("  The Manager turns that off the moment it opens the pad, so start the")
            print("  Manager and press a button. If it stays silent, hold the Steam button")
            print("  and press Y to switch the pad on, then run this again.")
        else:
            print(f"  Missing driver: {driver}")
            print(f"  {driver_problem(driver)}")
    wireless = kernel_has("BT_HIDP")
    if wireless is False:
        print()
        print("Bluetooth pads cannot connect on this kernel: CONFIG_BT_HIDP is not set.")
        print("Plug the pad in with USB, or boot a kernel built with CONFIG_BT_HIDP=m.")
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Xbox, PlayStation, Nintendo and Steam controller keys for MP Mix Manager.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true", help="show the controllers that are connected")
    mode.add_argument("--monitor", action="store_true", help="show every button and stick as it is read")
    mode.add_argument("--doctor", action="store_true", help="explain a pad that is on but not listed")
    args = parser.parse_args(argv)
    if args.list:
        return list_controllers()
    if args.doctor:
        return doctor()
    if not sys.platform.startswith("linux"):
        print("Game controllers are read through Linux evdev only.")
        return 0
    if args.monitor:
        print("Watching for Xbox, PlayStation, Nintendo and Steam controllers. Press Ctrl+C to stop.")
    else:
        os.set_blocking(sys.stdout.fileno(), False)
    try:
        Bridge(write_key, monitor=args.monitor).run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
