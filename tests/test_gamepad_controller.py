#!/usr/bin/env python3
"""
Game controller support: Xbox, PlayStation, Nintendo and Steam pads on Linux.

scripts/gamepad_bridge.py reads each pad through evdev and prints the key names
the Manager's menus already read. Mix_Archive_Manager.sh takes those keys from a
coprocess inside read_nav_key. These tests cover:

  1. Gamepads are found in a /proc/bus/input/devices table by vendor or name and
     by the A-button bit. Keyboards, mice and touchpads are ignored.
  2. Confirm and back follow each family's own labels; play/pause is the left
     face button.
  3. The D-pad and left stick repeat while held; triggers send one key per pull.
  4. Raw evdev records decode into the same keys.
  5. Two nodes reporting the same press send one key.
  6. The shell takes pad keys before the keyboard, drops stale ones, and honours
     MP_MANAGER_GAMEPAD=0.
  7. Opt-in (MP_RUN_GAMEPAD_DEVICE_TESTS=1): a virtual uinput pad drives the bridge.
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "Mix_Archive_Manager.sh"
HELPER = ROOT / "scripts" / "gamepad_bridge.py"
sys.path.insert(0, str(ROOT / "scripts"))

import gamepad_bridge as gb  # noqa: E402

IS_LINUX = sys.platform.startswith("linux")
SHELL_FUNCTIONS = (
    "_gamepad_enabled",
    "_gamepad_start",
    "_gamepad_stop",
    "_gamepad_take",
    "_gamepad_flush",
    "_gamepad_press_key",
    "_nav_mark_console",
    "_nav_pull",
    "read_nav_key",
)

# The real Xbox Series S|X entry from /proc/bus/input/devices on the test machine.
XBOX_BLOCK = """\
I: Bus=0003 Vendor=045e Product=0b12 Version=0509
N: Name="Microsoft Xbox Series S|X Controller"
P: Phys=usb-0000:00:14.0-5/input0
S: Sysfs=/devices/pci0000:00/0000:00:14.0/usb1/1-5/1-5:1.0/input/input17
U: Uniq=
H: Handlers=kbd event13 js0
B: PROP=0
B: EV=20000b
B: KEY=7cdb000000000000 0 8000000000 0 0
B: ABS=3003f
B: FF=107030000 0
"""
XBOX_KEYS = "7cdb000000000000 0 8000000000 0 0".split()


def key_words(*codes):
    """The /proc KEY words for these key codes: 64-bit words, highest first."""
    words = [0] * 12
    for code in codes:
        index, bit = divmod(code, 64)
        words[index] |= 1 << bit
    while words and words[-1] == 0:
        words.pop()
    return " ".join(f"{word:x}" for word in reversed(words))


def device_block(vendor, name, handlers, keys):
    return (
        f"I: Bus=0003 Vendor={vendor:04x} Product=0001 Version=0100\n"
        f'N: Name="{name}"\n'
        f"H: Handlers={handlers}\n"
        f"B: KEY={keys}\n"
    )


def shell_functions(*names):
    """Source text of the named functions, copied from the script."""
    lines = SCRIPT.read_text(encoding="utf-8", errors="replace").splitlines()
    chunks = []
    for name in names:
        start = next(i for i, line in enumerate(lines) if line.startswith(name + "() {"))
        end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
        chunks.append("\n".join(lines[start:end + 1]))
    return "\n".join(chunks) + "\n"


def pad_feed(keys):
    """Shell that feeds `keys` through a coprocess the way _gamepad_start does."""
    payload = "".join(key + "\\n" for key in keys)
    return "\n".join([
        f"coproc MP_GAMEPAD {{ printf '{payload}'; exec cat; }}",
        "_GAMEPAD_UP=1",
        "_GAMEPAD_STARTED_AT=$EPOCHSECONDS",
    ]) + "\n"


def run_shell(body):
    """Run `body` in bash with the gamepad and key-reading functions loaded."""
    script = "\n".join([
        f'SCRIPT_DIR="{ROOT}"',
        "_nav_pending=''; _NAV_WIN_CON=''; _NAV_ARROW_AT=''",
        "_GAMEPAD_UP=0",
        "_resize_pending() { return 1; }",
        shell_functions(*SHELL_FUNCTIONS),
        body,
    ])
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    return result.stdout.splitlines()


class ControllerDetection(unittest.TestCase):
    def test_real_xbox_node_is_found(self):
        found = gb.find_controllers(gb.parse_device_table(XBOX_BLOCK))
        self.assertEqual(found, [("/dev/input/event13", "Microsoft Xbox Series S|X Controller", "xbox")])

    def test_keyboards_mice_and_touchpads_are_not_controllers(self):
        table = "\n".join([
            device_block(0x05AC, "Apple Inc. Apple Keyboard", "sysrq kbd leds event6", key_words(0x1E, 0x1F)),
            device_block(0x3842, "EVGA EVGA TORQ X10 Gaming Mouse", "mouse0 event3", key_words(0x110, 0x111)),
            device_block(0x054C, "Sony Interactive Entertainment Wireless Controller Touchpad", "event20", key_words(0x110)),
            device_block(0x045E, "Microsoft Keyboard", "kbd event21", key_words(0x1E)),
        ])
        self.assertEqual(gb.find_controllers(gb.parse_device_table(table)), [])

    def test_gamepads_from_each_family_are_found(self):
        table = "\n".join([
            device_block(0x054C, "Sony Interactive Entertainment Wireless Controller", "js1 event18",
                         key_words(gb.BTN_SOUTH, gb.BTN_EAST, gb.BTN_WEST, gb.BTN_NORTH)),
            device_block(0x057E, "Nintendo Co., Ltd. Pro Controller", "js2 event19",
                         key_words(gb.BTN_SOUTH, gb.BTN_EAST)),
            device_block(0x28DE, "Valve Software Steam Controller", "js3 event22", key_words(gb.BTN_SOUTH)),
        ])
        self.assertEqual(gb.find_controllers(gb.parse_device_table(table)), [
            ("/dev/input/event18", "Sony Interactive Entertainment Wireless Controller", "playstation"),
            ("/dev/input/event19", "Nintendo Co., Ltd. Pro Controller", "nintendo"),
            ("/dev/input/event22", "Valve Software Steam Controller", "steam"),
        ])

    def test_the_steam_controllers_real_node_is_found(self):
        # From /proc/bus/input/devices with a Steam Controller on USB, once the
        # kernel has left lizard mode.
        block = """\
I: Bus=0003 Vendor=28de Product=1102 Version=0111
N: Name="Steam Controller"
P: Phys=usb-0000:00:14.0-9.4/input2
H: Handlers=event15 js1
B: PROP=0
B: EV=b
B: KEY=3f00000000 0 0 0 7fdb000600000000 0 0 0 0
B: ABS=33001b
"""
        self.assertEqual(gb.find_controllers(gb.parse_device_table(block)),
                         [("/dev/input/event15", "Steam Controller", "steam")])

    def test_a_steam_pad_in_lizard_mode_is_not_mistaken_for_a_keyboard(self):
        # Lizard mode: two keyboard/mouse nodes with no gamepad buttons at all.
        table = "\n".join([
            device_block(0x28DE, "Valve Software Wired Controller", "sysrq kbd leds event13",
                         key_words(0x01, 0x1C)),
            device_block(0x28DE, "Valve Software Wired Controller", "mouse2 event14",
                         key_words(0x110)),
        ])
        self.assertEqual(gb.find_controllers(gb.parse_device_table(table)), [])

    def test_key_bitmap_reads_the_highest_word_first(self):
        self.assertTrue(gb.has_key(XBOX_KEYS, gb.BTN_SOUTH))
        self.assertTrue(gb.has_key(XBOX_KEYS, gb.BTN_START))
        self.assertFalse(gb.has_key(XBOX_KEYS, 0x132))  # BTN_C is not on an Xbox pad
        self.assertFalse(gb.has_key(XBOX_KEYS, gb.BTN_TL2))  # Xbox triggers are analog


class SwitchController(unittest.TestCase):
    """The Switch Pro Controller through hid-nintendo."""

    # hid-nintendo's report for a Pro Controller over USB.
    SWITCH_BLOCK = """\
I: Bus=0003 Vendor=057e Product=2009 Version=0111
N: Name="Nintendo Co., Ltd. Pro Controller"
P: Phys=usb-0000:00:14.0-5/input0
S: Sysfs=/devices/pci0000:00/0000:00:14.0/usb1/1-5/1-5:1.0/input/input20
H: Handlers=event20 js1
B: PROP=0
B: EV=12000b
B: KEY=7cdb000000000000 0 8000000000 0 0
B: ABS=3067b
"""
    DRIVERLESS_BLOCK = """\
I: Bus=0003 Vendor=057e Product=2009 Version=0111
N: Name="Nintendo Pro Controller"
H: Handlers=event21
B: KEY=0
"""

    def test_pro_controller_is_found_and_labeled_nintendo(self):
        found = gb.find_controllers(gb.parse_device_table(self.SWITCH_BLOCK))
        self.assertEqual(found, [("/dev/input/event20", "Nintendo Co., Ltd. Pro Controller", "nintendo")])

    def test_a_pad_with_no_driver_bound_is_reported_by_the_doctor(self):
        import contextlib
        import io
        table = gb.parse_device_table(self.DRIVERLESS_BLOCK)
        self.assertEqual(gb.find_controllers(table), [])
        self.assertEqual([node["name"] for node in gb.controllers_by_vendor(table, 0x057E)],
                         ["Nintendo Pro Controller"])
        original = gb.read_device_table
        gb.read_device_table = lambda: table
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                code = gb.doctor()
        finally:
            gb.read_device_table = original
        self.assertEqual(code, 1)
        text = output.getvalue()
        self.assertIn("Present but not usable: Nintendo Pro Controller", text)
        self.assertIn("hid_nintendo", text)

    def test_the_doctor_is_quiet_when_the_driver_is_bound(self):
        import contextlib
        import io
        table = gb.parse_device_table(self.SWITCH_BLOCK)
        original = gb.read_device_table
        gb.read_device_table = lambda: table
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                code = gb.doctor()
        finally:
            gb.read_device_table = original
        self.assertEqual(code, 0)
        self.assertIn("Nintendo    /dev/input/event20", output.getvalue())
        self.assertNotIn("Missing driver", output.getvalue())


class SteamDoctor(unittest.TestCase):
    """A Steam pad in lizard mode looks like a keyboard, not a missing driver."""

    LIZARD_BLOCK = """\
I: Bus=0003 Vendor=28de Product=1102 Version=0111
N: Name="Valve Software Wired Controller"
H: Handlers=sysrq kbd leds event13
B: KEY=1f0000 0 0 0 0
"""

    def doctor_output(self, table):
        import contextlib
        import io
        original = gb.read_device_table
        gb.read_device_table = lambda: table
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                code = gb.doctor()
        finally:
            gb.read_device_table = original
        return code, output.getvalue()

    def test_lizard_mode_is_explained_without_blaming_the_driver(self):
        code, text = self.doctor_output(gb.parse_device_table(self.LIZARD_BLOCK))
        self.assertEqual(code, 1)
        self.assertIn("Present but not usable: Valve Software Wired Controller", text)
        self.assertIn("hid_steam", text)
        self.assertIn("keyboard and mouse", text)
        self.assertNotIn("Missing driver", text)

    def test_a_bound_steam_pad_is_reported_as_ready(self):
        block = """\
I: Bus=0003 Vendor=28de Product=1102 Version=0111
N: Name="Steam Controller"
H: Handlers=event15 js1
B: KEY=3f00000000 0 0 0 7fdb000600000000 0 0 0 0
"""
        code, text = self.doctor_output(gb.parse_device_table(block))
        self.assertEqual(code, 0)
        self.assertIn("Steam       /dev/input/event15", text)
        self.assertNotIn("Missing driver", text)


class FaceButtonLayouts(unittest.TestCase):
    def keys(self, family, *codes):
        pad = gb.PadState(family)
        out = []
        for code in codes:
            out += pad.handle(gb.EV_KEY, code, 1, 0.0)
        return out

    def test_confirm_and_back_follow_each_family_label(self):
        self.assertEqual(self.keys("xbox", gb.BTN_SOUTH, gb.BTN_EAST), ["ENTER", "ESC"])
        self.assertEqual(self.keys("playstation", gb.BTN_SOUTH, gb.BTN_EAST), ["ENTER", "ESC"])
        self.assertEqual(self.keys("nintendo", gb.BTN_EAST, gb.BTN_SOUTH), ["ENTER", "ESC"])

    def test_play_pause_is_the_left_face_button(self):
        self.assertEqual(self.keys("xbox", gb.BTN_NORTH), ["SPACE"])  # Xbox X
        self.assertEqual(self.keys("playstation", gb.BTN_WEST), ["SPACE"])  # Square
        self.assertEqual(self.keys("nintendo", gb.BTN_WEST), ["SPACE"])  # Nintendo Y
        self.assertEqual(self.keys("playstation", gb.BTN_NORTH), [])  # Triangle is unmapped

    def test_shoulders_page_and_only_nintendo_uses_digital_zl_zr(self):
        self.assertEqual(self.keys("xbox", gb.BTN_TL, gb.BTN_TR), ["PAGE_UP", "PAGE_DOWN"])
        self.assertEqual(self.keys("nintendo", gb.BTN_TL2, gb.BTN_TR2), ["CHAR:[", "CHAR:n"])
        self.assertEqual(self.keys("xbox", gb.BTN_TL2), [])

    def test_steam_face_buttons_follow_the_xbox_labels(self):
        self.assertEqual(self.keys("steam", gb.BTN_SOUTH, gb.BTN_EAST), ["ENTER", "ESC"])
        self.assertEqual(self.keys("steam", gb.BTN_NORTH), ["SPACE"])
        self.assertEqual(self.keys("steam", gb.BTN_TL, gb.BTN_TR), ["PAGE_UP", "PAGE_DOWN"])

    def test_playstation_uses_square_not_triangle_for_play_pause(self):
        self.assertEqual(self.keys("playstation", gb.BTN_SOUTH), ["ENTER"])  # Cross
        self.assertEqual(self.keys("playstation", gb.BTN_EAST), ["ESC"])  # Circle
        self.assertEqual(self.keys("playstation", gb.BTN_WEST), ["SPACE"])  # Square
        self.assertEqual(self.keys("playstation", gb.BTN_NORTH), [])  # Triangle

    def test_a_steam_digital_trigger_does_not_double_fire(self):
        pad = gb.PadState("steam", {gb.ABS_HAT2X: (0, 255), gb.ABS_HAT2Y: (0, 255)})
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_HAT2X, 255, 0.0), ["CHAR:n"])
        self.assertEqual(pad.handle(gb.EV_KEY, gb.BTN_TR2, 1, 0.1), [])

    def test_releases_and_kernel_autorepeat_send_nothing(self):
        pad = gb.PadState("xbox")
        self.assertEqual(pad.handle(gb.EV_KEY, gb.BTN_SOUTH, 0, 0.0), [])
        self.assertEqual(pad.handle(gb.EV_KEY, gb.BTN_SOUTH, 2, 0.0), [])


class MovementAndTriggers(unittest.TestCase):
    def test_dpad_repeats_after_a_delay_and_stops_on_release(self):
        pad = gb.PadState("xbox")
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_HAT0Y, 1, 0.0), ["DOWN"])
        self.assertEqual(pad.repeat(0.39), [])
        self.assertEqual(pad.repeat(0.41), ["DOWN"])
        self.assertEqual(pad.repeat(0.45), [])
        self.assertEqual(pad.repeat(0.54), ["DOWN"])
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_HAT0Y, 0, 0.60), [])
        self.assertEqual(pad.repeat(1.5), [])
        self.assertIsNone(pad.next_repeat())

    def test_left_stick_needs_a_real_push_and_has_hysteresis(self):
        pad = gb.PadState("xbox")
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_Y, 20000, 0.0), ["DOWN"])
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_Y, 15000, 0.1), [])  # still held
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_Y, 5000, 0.2), [])  # released
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_Y, 3000, 0.3), [])  # too small to press
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_Y, -20000, 0.4), ["UP"])

    def test_diagonal_stick_moves_along_the_dominant_axis(self):
        pad = gb.PadState("xbox")
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_X, 20000, 0.0), ["RIGHT"])
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_Y, 12000, 0.1), [])

    def test_triggers_send_one_key_per_pull(self):
        pad = gb.PadState("xbox", {gb.ABS_RZ: (0, 1023), gb.ABS_Z: (0, 1023)})
        pulls = [(0, []), (1023, ["CHAR:n"]), (1000, []), (0, []), (1023, ["CHAR:n"])]
        for step, (value, expected) in enumerate(pulls):
            self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_RZ, value, float(step)), expected, value)
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_Z, 1023, 9.0), ["CHAR:["])


class SteamController(unittest.TestCase):
    """hid_steam's own node: an analog left pad, a key-event D-pad and HAT2 triggers."""

    RANGES = {
        gb.ABS_X: (-32767, 32767), gb.ABS_Y: (-32767, 32767),
        gb.ABS_HAT0X: (-32767, 32767), gb.ABS_HAT0Y: (-32767, 32767),
        gb.ABS_HAT2X: (0, 255), gb.ABS_HAT2Y: (0, 255),
    }

    def pad(self):
        return gb.PadState("steam", self.RANGES)

    def test_the_left_pad_is_analog_and_needs_a_real_push(self):
        pad = self.pad()
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_HAT0X, 400, 0.0), [])  # resting drift
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_HAT0X, 32767, 0.1), ["RIGHT"])
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_HAT0X, 20000, 0.2), [])  # still held
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_HAT0X, 0, 0.3), [])

    def test_a_windows_style_dpad_still_works(self):
        pad = gb.PadState("xbox")
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_HAT0Y, 1, 0.0), ["DOWN"])
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_HAT0Y, 0, 0.1), [])

    def test_the_dpad_arrives_as_key_events_and_repeats(self):
        pad = self.pad()
        self.assertEqual(pad.handle(gb.EV_KEY, gb.BTN_DPAD_DOWN, 1, 0.0), ["DOWN"])
        self.assertEqual(pad.repeat(0.39), [])
        self.assertEqual(pad.repeat(0.41), ["DOWN"])
        self.assertEqual(pad.handle(gb.EV_KEY, gb.BTN_DPAD_DOWN, 0, 0.5), [])
        self.assertEqual(pad.repeat(0.9), [])

    def test_kernel_autorepeat_on_the_dpad_is_not_replayed(self):
        pad = self.pad()
        self.assertEqual(pad.handle(gb.EV_KEY, gb.BTN_DPAD_UP, 1, 0.0), ["UP"])
        self.assertEqual(pad.handle(gb.EV_KEY, gb.BTN_DPAD_UP, 2, 0.2), [])

    def test_a_diagonal_dpad_does_not_cancel_a_held_direction(self):
        pad = self.pad()
        self.assertEqual(pad.handle(gb.EV_KEY, gb.BTN_DPAD_UP, 1, 0.0), ["UP"])
        self.assertEqual(pad.handle(gb.EV_KEY, gb.BTN_DPAD_RIGHT, 1, 0.1), ["RIGHT"])
        self.assertEqual(pad.handle(gb.EV_KEY, gb.BTN_DPAD_RIGHT, 0, 0.2), ["UP"])
        self.assertEqual(pad.handle(gb.EV_KEY, gb.BTN_DPAD_UP, 0, 0.3), [])

    def test_triggers_are_reported_on_hat2_as_0_to_255(self):
        pad = self.pad()
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_HAT2Y, 255, 0.0), ["CHAR:["])  # left
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_HAT2X, 255, 0.1), ["CHAR:n"])  # right
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_HAT2X, 0, 0.2), [])
        self.assertEqual(pad.handle(gb.EV_ABS, gb.ABS_HAT2X, 255, 0.3), ["CHAR:n"])


class EventDecoding(unittest.TestCase):
    def test_raw_evdev_records_become_keys(self):
        read_end, write_end = os.pipe()
        try:
            records = b"".join(gb.EVENT.pack(0, 0, kind, code, value) for kind, code, value in [
                (gb.EV_KEY, gb.BTN_SOUTH, 1),
                (gb.EV_KEY, gb.BTN_SOUTH, 0),
                (gb.EV_ABS, gb.ABS_HAT0X, -1),
                (0, 0, 0),
            ])
            os.write(write_end, records)
            sent = []
            bridge = gb.Bridge(sent.append)
            pad = gb.Pad("/dev/input/event99", read_end, "Test pad", "xbox", {})
            bridge.pads[pad.path] = pad
            bridge._read(pad, 0.0)
            self.assertEqual(sent, ["ENTER", "LEFT"])
        finally:
            os.close(read_end)
            os.close(write_end)


class DuplicateNodes(unittest.TestCase):
    def test_same_press_from_a_second_node_is_sent_once(self):
        sent = []

        def write(key):
            sent.append(key)
            return True

        bridge = gb.Bridge(write)
        first = gb.Pad("/dev/input/event1", -1, "Xbox", "xbox", {})
        second = gb.Pad("/dev/input/event2", -1, "Steam", "steam", {})
        self.assertTrue(bridge._send(first, "ENTER", 0.00))
        self.assertFalse(bridge._send(second, "ENTER", 0.02))
        self.assertTrue(bridge._send(second, "ENTER", 0.30))
        self.assertTrue(bridge._send(first, "DOWN", 0.31))
        self.assertEqual(sent, ["ENTER", "ENTER", "DOWN"])


@unittest.skipUnless(IS_LINUX and Path("/dev/input").is_dir(), "game controllers are read through Linux evdev")
class ShellReadsPadKeys(unittest.TestCase):
    def test_pad_keys_come_before_the_keyboard_in_read_nav_key(self):
        body = (
            pad_feed(["UP", "ENTER", "CHAR:n"]) +
            "got=(); start=$SECONDS\n"
            "while (( ${#got[@]} < 3 && SECONDS - start < 8 )); do\n"
            "    key=''; read_nav_key key || true\n"
            "    case \"$key\" in ''|TICK) ;; *) got+=(\"$key\") ;; esac\n"
            "done\n"
            "printf '%s\\n' \"${got[@]}\"\n"
        )
        self.assertEqual(run_shell(body), ["UP", "ENTER", "CHAR:n"])

    def test_flush_drops_keys_queued_while_a_long_action_ran(self):
        body = (
            pad_feed(["A", "B", "C"]) +
            "sleep 0.3\n"
            "_gamepad_flush\n"
            "if _gamepad_take left; then echo \"left:$left\"; else echo empty; fi\n"
        )
        self.assertEqual(run_shell(body), ["empty"])

    def test_press_keys_match_the_raw_keys_press_enter_reads(self):
        body = (
            pad_feed(["UP", "ENTER", "SPACE", "ESC"]) +
            "sleep 0.3\n"
            "for _ in 1 2 3 4; do k=''; if _gamepad_press_key k; then "
            "printf '%s\\n' \"$(printf '%s' \"$k\" | od -An -tx1 | tr -d ' \\n')\"; "
            "else echo ignored; fi; done\n"
        )
        self.assertEqual(run_shell(body), ["ignored", "", "20", "1b"])

    def test_mp_manager_gamepad_zero_disables_the_bridge(self):
        body = (
            "MP_MANAGER_GAMEPAD=0\n"
            "if _gamepad_enabled; then echo enabled; else echo disabled; fi\n"
            "_gamepad_start\n"
            "echo \"pid:${MP_GAMEPAD_PID:-none}\"\n"
        )
        self.assertEqual(run_shell(body), ["disabled", "pid:none"])

    def test_start_and_stop_manage_the_bridge_process(self):
        body = (
            "MP_MANAGER_GAMEPAD=1\n"
            "_gamepad_start\n"
            "echo \"started:$_GAMEPAD_UP:$(kill -0 \"$MP_GAMEPAD_PID\" 2>/dev/null && echo alive || echo dead)\"\n"
            "_gamepad_stop\n"
            "echo \"stopped:$_GAMEPAD_UP:$(kill -0 \"$MP_GAMEPAD_PID\" 2>/dev/null && echo alive || echo dead)\"\n"
        )
        self.assertEqual(run_shell(body), ["started:1:alive", "stopped:0:dead"])

    def test_a_dead_bridge_is_restarted_after_the_rate_limit(self):
        body = (
            "MP_MANAGER_GAMEPAD=1\n"
            "_gamepad_start\n"
            "first=\"$MP_GAMEPAD_PID\"\n"
            "kill -9 \"$first\" 2>/dev/null\n"
            "sleep 2.5\n"
            "key=''\n"
            "if _gamepad_take key; then echo \"unexpected:$key\"; else echo missing; fi\n"
            "sleep 0.3\n"
            "if [ \"${MP_GAMEPAD_PID:-}\" != \"$first\" ] && kill -0 \"${MP_GAMEPAD_PID:-0}\" 2>/dev/null; "
            "then echo restarted; else echo dead; fi\n"
            "_gamepad_stop\n"
        )
        self.assertEqual(run_shell(body), ["missing", "restarted"])

    def test_rapid_crashes_do_not_spawn_a_bridge_per_keypress(self):
        body = (
            "MP_MANAGER_GAMEPAD=1\n"
            "_gamepad_start\n"
            "spawned=0\n"
            "for _ in 1 2 3 4 5; do\n"
            "    pid=\"${MP_GAMEPAD_PID:-}\"; [ -z \"$pid\" ] && break\n"
            "    kill -9 \"$pid\" 2>/dev/null; sleep 0.05\n"
            "    key=''; _gamepad_take key; sleep 0.2\n"
            "    [ -n \"${MP_GAMEPAD_PID:-}\" ] && (( spawned++ ))\n"
            "done\n"
            "echo \"at most one restart: $([ \"$spawned\" -le 1 ] && echo yes || echo no)\"\n"
            "_gamepad_stop\n"
        )
        self.assertEqual(run_shell(body), ["at most one restart: yes"])


@unittest.skipUnless(
    IS_LINUX and os.environ.get("MP_RUN_GAMEPAD_DEVICE_TESTS") == "1",
    "set MP_RUN_GAMEPAD_DEVICE_TESTS=1 to create a virtual pad through uinput",
)
class VirtualPadEndToEnd(unittest.TestCase):
    def test_virtual_xbox_pad_sends_keys_through_the_bridge(self):
        from evdev import AbsInfo, UInput, ecodes as e

        def axis(low, high):
            return AbsInfo(value=0, min=low, max=high, fuzz=0, flat=0, resolution=0)

        caps = {
            e.EV_KEY: [e.BTN_SOUTH, e.BTN_EAST, e.BTN_NORTH, e.BTN_WEST, e.BTN_TL, e.BTN_TR],
            e.EV_ABS: [
                (e.ABS_X, axis(-32768, 32767)),
                (e.ABS_Y, axis(-32768, 32767)),
                (e.ABS_RZ, axis(0, 1023)),
                (e.ABS_HAT0X, axis(-1, 1)),
                (e.ABS_HAT0Y, axis(-1, 1)),
            ],
        }
        pad = UInput(caps, name="MP Test Xbox Pad", vendor=0x045E, product=0x0B12)
        with tempfile.TemporaryFile() as captured:
            bridge = subprocess.Popen([sys.executable, str(HELPER)], stdout=captured, stderr=subprocess.DEVNULL)
            try:
                time.sleep(2.5)  # the bridge rescans every two seconds
                pad.write(e.EV_KEY, e.BTN_SOUTH, 1)
                pad.syn()
                pad.write(e.EV_KEY, e.BTN_SOUTH, 0)
                pad.syn()
                pad.write(e.EV_ABS, e.ABS_HAT0Y, 1)
                pad.syn()
                time.sleep(1.0)  # DOWN, then repeats while held
                pad.write(e.EV_ABS, e.ABS_HAT0Y, 0)
                pad.syn()
                time.sleep(0.3)
            finally:
                pad.close()
                bridge.terminate()
                bridge.wait(timeout=5)
            captured.seek(0)
            keys = captured.read().decode("ascii").split()
        self.assertEqual(keys[:1], ["ENTER"])
        self.assertEqual(keys[1:2], ["DOWN"])
        self.assertGreaterEqual(keys.count("DOWN"), 2)

    def test_virtual_steam_pad_sends_keys_through_the_bridge(self):
        """The Steam node's real shapes: analog left pad, key D-pad, HAT2 triggers."""
        from evdev import AbsInfo, UInput, ecodes as e

        def axis(low, high):
            return AbsInfo(value=0, min=low, max=high, fuzz=0, flat=0, resolution=0)

        caps = {
            e.EV_KEY: [e.BTN_SOUTH, e.BTN_EAST, e.BTN_NORTH, e.BTN_WEST,
                       e.BTN_TL, e.BTN_TR, e.BTN_TL2, e.BTN_TR2,
                       e.BTN_DPAD_UP, e.BTN_DPAD_DOWN, e.BTN_DPAD_LEFT, e.BTN_DPAD_RIGHT],
            e.EV_ABS: [
                (e.ABS_X, axis(-32767, 32767)),
                (e.ABS_Y, axis(-32767, 32767)),
                (e.ABS_HAT0X, axis(-32767, 32767)),
                (e.ABS_HAT0Y, axis(-32767, 32767)),
                (e.ABS_HAT2X, axis(0, 255)),
                (e.ABS_HAT2Y, axis(0, 255)),
            ],
        }
        pad = UInput(caps, name="Steam Controller", vendor=0x28DE, product=0x1102)
        with tempfile.TemporaryFile() as captured:
            bridge = subprocess.Popen([sys.executable, str(HELPER)], stdout=captured, stderr=subprocess.DEVNULL)
            try:
                time.sleep(2.5)
                pad.write(e.EV_KEY, e.BTN_SOUTH, 1)  # A -> ENTER
                pad.syn()
                pad.write(e.EV_KEY, e.BTN_SOUTH, 0)
                pad.syn()
                pad.write(e.EV_KEY, e.BTN_DPAD_DOWN, 1)  # D-pad -> DOWN + repeat
                pad.syn()
                time.sleep(1.0)
                pad.write(e.EV_KEY, e.BTN_DPAD_DOWN, 0)
                pad.syn()
                pad.write(e.EV_ABS, e.ABS_HAT2X, 255)  # right trigger -> next track
                pad.syn()
                time.sleep(0.3)
                pad.write(e.EV_ABS, e.ABS_HAT2X, 0)
                pad.syn()
                time.sleep(0.3)
            finally:
                pad.close()
                bridge.terminate()
                bridge.wait(timeout=5)
            captured.seek(0)
            keys = captured.read().decode("ascii").split()
        self.assertEqual(keys[:1], ["ENTER"])
        self.assertEqual(keys[1:2], ["DOWN"])
        self.assertGreaterEqual(keys.count("DOWN"), 2)
        self.assertIn("CHAR:n", keys)


if __name__ == "__main__":
    unittest.main()
