#!/usr/bin/env python3
"""Tests for the S4 native fader axes used by MP Liquid Mixer 0.0.1."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIXER_DIR = PROJECT_ROOT / "MP_Liquid_Mixer"
sys.path.insert(0, str(MIXER_DIR))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import mixer_core  # noqa: E402
import mixer_evdev  # noqa: E402


class FakeDevice:
    """The bits of an evdev InputDevice the listener actually uses."""

    def __init__(self, values=None, maximum=4095):
        self.path = "/dev/input/event-test"
        self.name = "Kontrol S4"
        self._values = dict(values or {})
        self._max = maximum

    def absinfo(self, code):
        return type("AbsInfo", (), {"value": self._values.get(code, 0),
                                    "max": self._max, "min": 0})()

    def close(self):
        pass


class TestScaleAxis(unittest.TestCase):
    def test_endpoints(self):
        self.assertEqual(mixer_evdev.scale_axis(0), 0.0)
        self.assertEqual(mixer_evdev.scale_axis(4095), 1.0)
        self.assertAlmostEqual(mixer_evdev.scale_axis(2048), 2048 / 4095, places=6)

    def test_inverted(self):
        self.assertEqual(mixer_evdev.scale_axis(0, invert=True), 1.0)
        self.assertEqual(mixer_evdev.scale_axis(4095, invert=True), 0.0)

    def test_clamped_and_custom_max(self):
        self.assertEqual(mixer_evdev.scale_axis(-100), 0.0)
        self.assertEqual(mixer_evdev.scale_axis(99999), 1.0)
        self.assertEqual(mixer_evdev.scale_axis(100, maximum=100), 1.0)


class TestAxisMapping(unittest.TestCase):
    def test_native_s4_defaults(self):
        mapping = mixer_evdev.AxisMapping()
        self.assertEqual(mapping.resolve(18), ("channel:A", False))
        self.assertEqual(mapping.resolve(17), ("channel:B", False))
        self.assertEqual(mapping.resolve(19), ("channel:C", False))
        self.assertEqual(mapping.resolve(16), ("channel:D", False))
        self.assertEqual(mapping.resolve(23), ("crossfader", False))
        self.assertIsNone(mapping.resolve(21))  # pitch fader, not a mixer fader

    def test_custom_axes_and_invert(self):
        mapping = mixer_evdev.AxisMapping({
            "channels": {"A": 40, "B": 41},
            "crossfader": 42,
            "invert": {"A": True, "crossfader": True},
        })
        self.assertEqual(mapping.resolve(40), ("channel:A", True))
        self.assertEqual(mapping.resolve(41), ("channel:B", False))
        self.assertEqual(mapping.resolve(42), ("crossfader", True))

    def test_missing_entries_keep_defaults(self):
        mapping = mixer_evdev.AxisMapping({"channels": {"A": 40}})
        self.assertEqual(mapping.resolve(40), ("channel:A", False))
        self.assertEqual(mapping.resolve(17), ("channel:B", False))
        self.assertEqual(mapping.resolve(23), ("crossfader", False))

    def test_load_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s4.json"
            path.write_text(json.dumps({"channels": {"A": 30}, "crossfader": 31}), encoding="utf-8")
            mapping = mixer_evdev.AxisMapping.load(path)
        self.assertEqual(mapping.resolve(30), ("channel:A", False))
        self.assertEqual(mapping.resolve(31), ("crossfader", False))

    def test_missing_file_uses_defaults(self):
        loaded = mixer_evdev.AxisMapping.load(Path("/nonexistent/s4.json"))
        self.assertEqual(loaded.resolve(18), ("channel:A", False))


class TestApplyAction(unittest.TestCase):
    def test_channel_a(self):
        model = mixer_core.MixerModel(gains={"A": 1.0, "B": 1.0}, crossfade=0.0)
        self.assertTrue(mixer_evdev.apply_action(model, "channel:A", 0.25))
        self.assertAlmostEqual(model.channel_gain("A"), 0.25, places=6)

    def test_all_four_channel_faders_are_applied(self):
        model = mixer_core.MixerModel()
        self.assertTrue(mixer_evdev.apply_action(model, "channel:C", 0.10))
        self.assertTrue(mixer_evdev.apply_action(model, "channel:D", 0.20))
        self.assertAlmostEqual(model.channel_gain("C"), 0.10, places=6)
        self.assertAlmostEqual(model.channel_gain("D"), 0.20, places=6)

    def test_crossfader(self):
        model = mixer_core.MixerModel(gains={"A": 1.0, "B": 1.0})
        self.assertTrue(mixer_evdev.apply_action(model, "crossfader", 0.0))
        self.assertEqual(model.deck_volumes(), (100, 0))
        mixer_evdev.apply_action(model, "crossfader", 1.0)
        self.assertEqual(model.deck_volumes(), (0, 100))


class TestButtons(unittest.TestCase):
    def test_default_play_buttons(self):
        mapping = mixer_evdev.AxisMapping()
        self.assertEqual(mapping.resolve_button(263), "play:A")
        self.assertEqual(mapping.resolve_button(319), "play:B")
        self.assertIsNone(mapping.resolve_button(264))

    def test_custom_button_keeps_the_other_default(self):
        mapping = mixer_evdev.AxisMapping({"buttons": {"play:A": 300}})
        self.assertEqual(mapping.resolve_button(300), "play:A")
        self.assertEqual(mapping.resolve_button(319), "play:B")

    def test_press_only(self):
        seen = []
        listener = mixer_evdev.S4AxisListener(
            mapping=mixer_evdev.AxisMapping(),
            on_change=lambda action, value: seen.append((action, value)),
        )
        listener._handle_button(263, 1)
        listener._handle_button(263, 0)
        listener._handle_button(263, 2)
        listener._handle_button(319, 1)
        self.assertEqual(seen, [("play:A", 1.0), ("play:B", 1.0)])

    def test_browse_push_is_a_load_button(self):
        self.assertEqual(mixer_evdev.AxisMapping().resolve_button(292), "load")

    def test_buttons_are_in_the_exported_mapping(self):
        exported = mixer_evdev.AxisMapping().to_dict()
        for action, code in {"play:A": 263, "play:B": 319, "load": 292,
                             "sync:A": 259, "sync:B": 315,
                             "shift:A": 257, "shift:B": 313}.items():
            self.assertEqual(exported["buttons"][action], code)
        self.assertEqual(exported["browse"], 54)
        self.assertEqual(exported["pitch"], {"A": 21, "B": 22})


class TestBrowseKnob(unittest.TestCase):
    def _listener(self, seen, invert=False):
        listener = mixer_evdev.S4AxisListener(
            mapping=mixer_evdev.AxisMapping({"invert": {"browse": invert}}),
            on_change=lambda action, value: seen.append((action, value)),
        )
        listener.device = FakeDevice(maximum=15)
        return listener

    def test_clockwise_and_counter_clockwise(self):
        seen = []
        listener = self._listener(seen)
        for value in (0, 1, 2, 1):
            listener._handle(54, value)
        self.assertEqual([round(v) for _, v in seen], [1, 1, -1])

    def test_wraparound(self):
        seen = []
        listener = self._listener(seen)
        for value in (15, 0, 1, 0, 15):
            listener._handle(54, value)
        self.assertEqual([round(v) for _, v in seen], [1, 1, -1, -1])

    def test_inverted(self):
        seen = []
        listener = self._listener(seen, invert=True)
        for value in (0, 1):
            listener._handle(54, value)
        self.assertEqual(round(seen[0][1]), -1)

    def test_first_value_is_a_reference_only(self):
        seen = []
        listener = self._listener(seen)
        listener._handle(54, 7)
        self.assertEqual(seen, [])
        listener._handle(54, 8)
        self.assertEqual(round(seen[0][1]), 1)


class TestPitchAxesAndSyncButtons(unittest.TestCase):
    def _listener(self, seen, mapping=None):
        listener = mixer_evdev.S4AxisListener(
            mapping=mapping or mixer_evdev.AxisMapping(),
            on_change=lambda action, value: seen.append((action, value)),
        )
        listener.device = FakeDevice(maximum=4095)
        return listener

    def test_pitch_axes_map_to_percent(self):
        seen = []
        listener = self._listener(seen)
        listener._handle(21, 4095)   # top = +20%
        listener._handle(22, 0)      # bottom = -20%
        listener._handle(21, 2048)   # centre = 0%
        self.assertEqual([round(v, 1) for _, v in seen], [20.0, -20.0, 0.0])
        self.assertEqual([a for a, _ in seen], ["pitch:A", "pitch:B", "pitch:A"])

    def test_pitch_near_centre_snaps_to_zero(self):
        seen = []
        listener = self._listener(seen)
        listener._handle(21, 2035)  # ~0.13% off centre
        self.assertEqual(seen[0][0], "pitch:A")
        self.assertEqual(seen[0][1], 0.0)

    def test_pitch_invert(self):
        seen = []
        mapping = mixer_evdev.AxisMapping({"invert": {"pitch:A": True}})
        listener = self._listener(seen, mapping)
        listener._handle(21, 4095)
        self.assertAlmostEqual(seen[0][1], -20.0, places=3)

    def test_sync_and_shift_buttons(self):
        mapping = mixer_evdev.AxisMapping()
        self.assertEqual(mapping.resolve_button(259), "sync:A")
        self.assertEqual(mapping.resolve_button(315), "sync:B")
        self.assertEqual(mapping.resolve_button(257), "shift:A")
        self.assertEqual(mapping.resolve_button(313), "shift:B")

    def test_shift_release_is_reported(self):
        seen = []
        listener = mixer_evdev.S4AxisListener(
            mapping=mixer_evdev.AxisMapping(),
            on_change=lambda action, value: seen.append((action, value)),
        )
        listener._handle_button(257, 1)
        listener._handle_button(257, 0)
        listener._handle_button(259, 0)  # sync release is ignored
        listener._handle_button(259, 1)
        self.assertEqual(seen, [("shift:A", 1.0), ("shift:A", 0.0), ("sync:A", 1.0)])


class TestListenerDecoding(unittest.TestCase):
    def _listener(self, seen, values=None, mapping=None):
        listener = mixer_evdev.S4AxisListener(
            mapping=mapping or mixer_evdev.AxisMapping(),
            on_change=lambda action, value: seen.append((action, value)),
        )
        listener.device = FakeDevice(values or {})
        return listener

    def test_channel_axis_is_scaled_and_reported(self):
        seen = []
        listener = self._listener(seen, {18: 1024})
        listener._handle(18, 1024)
        self.assertEqual(seen[0][0], "channel:A")
        self.assertAlmostEqual(seen[0][1], 1024 / 4095, places=6)

    def test_crossfader_axis(self):
        seen = []
        listener = self._listener(seen, {23: 0})
        listener._handle(23, 0)
        self.assertEqual(seen, [("crossfader", 0.0)])

    def test_unmapped_axis_is_ignored(self):
        seen = []
        listener = self._listener(seen)
        listener._handle(60, 2048)
        self.assertEqual(seen, [])

    def test_invert(self):
        seen = []
        mapping = mixer_evdev.AxisMapping({"invert": {"A": True}})
        listener = self._listener(seen, {18: 0}, mapping=mapping)
        listener._handle(18, 0)
        self.assertEqual(seen[0][0], "channel:A")
        self.assertEqual(seen[0][1], 1.0)

    def test_deadband_ignores_small_jitter(self):
        seen = []
        listener = self._listener(seen, {18: 2000})
        listener._handle(18, 2000)
        listener._handle(18, 2008)   # below the 16-count deadband
        self.assertEqual(len(seen), 1)
        listener._handle(18, 2020)   # above it
        self.assertEqual(len(seen), 2)

    def test_initial_state_is_published(self):
        seen = []
        listener = self._listener(seen, {18: 4095, 17: 0, 19: 2048, 16: 1024, 23: 2048})
        listener._emit_initial()
        actions = [action for action, _ in seen]
        self.assertIn("channel:A", actions)
        self.assertIn("channel:B", actions)
        self.assertIn("crossfader", actions)
        self.assertIn("pitch:A", actions)
        self.assertIn("pitch:B", actions)
        self.assertEqual(len(seen), 7)


if __name__ == "__main__":
    unittest.main()
