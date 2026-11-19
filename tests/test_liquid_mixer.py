#!/usr/bin/env python3
"""Tests for MP Liquid Mixer 0.0.1.

The window needs X11, so the drawing itself is not exercised here. Everything
that decides behaviour is: the equal-power curve, channel gating, External
unity, persistence, the 1920x1080 composition, coordinate maps, themes and the
per-deck player namespacing.
"""

import json
import math
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIXER_DIR = PROJECT_ROOT / "MP_Liquid_Mixer"
sys.path.insert(0, str(MIXER_DIR))
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import mixer_core  # noqa: E402


class TestEqualPower(unittest.TestCase):
    def test_endpoints(self):
        self.assertAlmostEqual(mixer_core.equal_power(0.0)[0], 1.0, places=6)
        self.assertAlmostEqual(mixer_core.equal_power(0.0)[1], 0.0, places=6)
        self.assertAlmostEqual(mixer_core.equal_power(1.0)[0], 0.0, places=6)
        self.assertAlmostEqual(mixer_core.equal_power(1.0)[1], 1.0, places=6)

    def test_centre_is_minus_three_db(self):
        gain_a, gain_b = mixer_core.equal_power(0.5)
        self.assertAlmostEqual(gain_a, math.sqrt(0.5), places=6)
        self.assertAlmostEqual(gain_b, math.sqrt(0.5), places=6)

    def test_constant_power(self):
        for step in range(21):
            gain_a, gain_b = mixer_core.equal_power(step / 20.0)
            self.assertAlmostEqual(gain_a ** 2 + gain_b ** 2, 1.0, places=6)

    def test_clamped(self):
        self.assertEqual(mixer_core.equal_power(-3.0), (1.0, 0.0))
        self.assertEqual(mixer_core.equal_power(9.0), (0.0, 1.0))


class TestMixerModel(unittest.TestCase):
    def test_defaults_are_four_live_decks(self):
        model = mixer_core.MixerModel()
        self.assertEqual(model.mode, "INTERNAL")
        for channel in ("A", "B", "C", "D"):
            self.assertTrue(model.channel_active(channel))
        self.assertEqual(model.deck_for_side("L"), "A")
        self.assertEqual(model.deck_for_side("R"), "B")

    def test_centre_blend_applies_both_channel_gains(self):
        model = mixer_core.MixerModel(gains={"A": 1.0, "B": 1.0}, crossfade=0.5)
        vol_a, vol_b = model.deck_volumes()
        self.assertEqual((vol_a, vol_b), (71, 71))

    def test_crossfader_ends(self):
        model = mixer_core.MixerModel(gains={"A": 1.0, "B": 1.0}, crossfade=0.0)
        self.assertEqual(model.deck_volumes(), (100, 0))
        model.set_crossfade(1.0)
        self.assertEqual(model.deck_volumes(), (0, 100))

    def test_channel_gain_scales_its_deck(self):
        model = mixer_core.MixerModel(gains={"A": 0.5, "B": 1.0}, crossfade=0.0)
        self.assertEqual(model.deck_volumes(), (50, 0))

    def test_every_channel_fader_moves_its_own_deck(self):
        model = mixer_core.MixerModel(gains={"A": 1.0, "B": 1.0}, crossfade=0.0)
        self.assertTrue(model.set_channel("C", 0.25))
        self.assertTrue(model.set_channel("D", 0.5))
        self.assertAlmostEqual(model.channel_gain("C"), 0.25, places=6)
        self.assertAlmostEqual(model.channel_gain("D"), 0.5, places=6)
        # C and D are not crossfaded, so their fader is the whole level.
        self.assertEqual(model.volume_for("C"), 25)
        self.assertEqual(model.volume_for("D"), 50)

    def test_side_switch_toggles_between_its_two_decks(self):
        model = mixer_core.MixerModel()
        self.assertEqual(model.toggle_side_deck("L"), "C")
        self.assertEqual(model.deck_for_side("L"), "C")
        self.assertEqual(model.toggle_side_deck("L"), "A")
        self.assertEqual(model.toggle_side_deck("R"), "D")
        self.assertEqual(model.deck_for_side("R"), "D")
        self.assertEqual(model.deck_of("D"), "R")
        self.assertEqual(model.deck_of("A"), "L")
        # Hidden decks belong to no visible playlist window.
        self.assertIsNone(model.deck_of("C"))

    def test_side_decks_survive_a_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            model = mixer_core.MixerModel()
            model.toggle_side_deck("L")
            model.toggle_side_deck("R")
            model.save(path)
            loaded = mixer_core.MixerModel.load(path)
        self.assertEqual(loaded.deck_for_side("L"), "C")
        self.assertEqual(loaded.deck_for_side("R"), "D")

    def test_external_mode_is_unity(self):
        model = mixer_core.MixerModel(gains={"A": 0.1, "B": 0.2}, crossfade=0.0)
        self.assertEqual(model.toggle_mode(), "EXTERNAL")
        self.assertEqual(model.deck_volumes(), (100, 100))
        model.set_channel("A", 0.0)
        self.assertEqual(model.deck_volumes(), (100, 100))
        self.assertEqual(model.toggle_mode(), "INTERNAL")
        self.assertEqual(model.deck_volumes(), (0, 0))

    def test_unknown_mode_falls_back_to_internal(self):
        self.assertEqual(mixer_core.MixerModel(mode="SOMETHING").mode, "INTERNAL")

    def test_opacity_is_clamped_to_the_player_range(self):
        self.assertEqual(mixer_core.MixerModel(opacity=0.0).opacity, mixer_core.MIN_OPACITY)
        self.assertEqual(mixer_core.MixerModel(opacity=1.0).opacity, mixer_core.MAX_OPACITY)

    def test_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            model = mixer_core.MixerModel(gains={"A": 0.42, "B": 0.73}, crossfade=0.8,
                                          mode="EXTERNAL", opacity=0.55)
            model.save(path)
            loaded = mixer_core.MixerModel.load(path)
        self.assertEqual(loaded.mode, "EXTERNAL")
        self.assertAlmostEqual(loaded.crossfade, 0.8, places=6)
        self.assertAlmostEqual(loaded.gains["A"], 0.42, places=6)
        self.assertAlmostEqual(loaded.gains["B"], 0.73, places=6)
        self.assertAlmostEqual(loaded.opacity, 0.55, places=6)

    def test_missing_state_loads_defaults(self):
        self.assertEqual(mixer_core.MixerModel.load(Path("/nonexistent/liquid_mixer.json")).mode, "INTERNAL")


class TestTrimAndEq(unittest.TestCase):
    def test_trim_scales_the_deck_volume(self):
        model = mixer_core.MixerModel(gains={"A": 1.0, "B": 1.0}, crossfade=0.0)
        model.set_trim("A", 0.5)
        self.assertEqual(model.deck_volumes(), (50, 0))
        model.set_trim("A", 1.5)
        self.assertEqual(model.deck_volumes(), (100, 0))  # clamped by the engine

    def test_trim_is_clamped(self):
        model = mixer_core.MixerModel()
        model.set_trim("A", -2.0)
        self.assertEqual(model.trim("A"), 0.0)
        model.set_trim("A", 99.0)
        self.assertEqual(model.trim("A"), mixer_core.TRIM_MAX)

    def test_eq_maps_three_knobs_to_ten_bands(self):
        model = mixer_core.MixerModel()
        model.set_eq("A", "low", 3.0)
        model.set_eq("A", "mid", -2.0)
        model.set_eq("A", "high", 6.0)
        bands = model.eq_bands("A")
        self.assertEqual(bands[0:3], [3.0, 3.0, 3.0])
        self.assertEqual(bands[3:7], [-2.0] * 4)
        self.assertEqual(bands[7:10], [6.0] * 3)

    def test_eq_is_clamped_and_shared_for_cd(self):
        model = mixer_core.MixerModel()
        model.set_eq("C", "low", -40.0)
        self.assertEqual(model.eq_value("C", "low"), mixer_core.EQ_MIN)
        model.set_eq("D", "high", 40.0)
        self.assertEqual(model.eq_value("D", "high"), mixer_core.EQ_MAX)
        self.assertFalse(model.set_eq("A", "presence", 1.0))

    def test_trim_and_eq_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            model = mixer_core.MixerModel()
            model.set_trim("B", 1.2)
            model.set_eq("B", "low", 4.5)
            model.set_eq("B", "high", -3.0)
            model.save(path)
            loaded = mixer_core.MixerModel.load(path)
        self.assertAlmostEqual(loaded.trim("B"), 1.2, places=6)
        self.assertAlmostEqual(loaded.eq_value("B", "low"), 4.5, places=6)
        self.assertAlmostEqual(loaded.eq_value("B", "high"), -3.0, places=6)


class TestWindowedMode(unittest.TestCase):
    def test_windowed_size_is_the_design_grid_on_this_desktop(self):
        self.assertEqual(mixer_core.windowed_size(2784, 1740), (1920, 1080))
        self.assertEqual(mixer_core.windowed_size(3840, 2160), (1920, 1080))

    def test_windowed_size_fits_a_smaller_screen(self):
        width, height = mixer_core.windowed_size(1366, 768)
        self.assertLessEqual(width, 1366 - 48)
        self.assertLessEqual(height, 768 - 48)
        self.assertAlmostEqual(width / height, 16 / 9, places=1)

    def test_double_click_on_the_top_bar_toggles(self):
        import MP_Liquid_Mixer as app

        class FakeWindow:
            def __init__(self):
                self.unfullscreened = False
                self.fullscreened = False

            def unfullscreen(self):
                self.unfullscreened = True

            def fullscreen(self):
                self.fullscreened = True

            def get_allocated_width(self):
                return 1920

            def get_allocated_height(self):
                return 1080

            def get_position(self):
                return (0, 0)

        class FakeArea:
            def grab_focus(self):
                pass

            def queue_draw(self):
                pass

        class FakeEvent:
            type = 5  # Gdk.EventType._2BUTTON_PRESS
            button = 1
            x = 400.0
            y = 24.0
            x_root = 400
            y_root = 24
            time = 0

        mixer = app.LiquidMixer()
        mixer.area = FakeArea()
        mixer.window = FakeWindow()
        mixer.start_fullscreen = True
        mixer.Gdk = type("FakeGdk", (), {
            "EventType": type("FakeEventType", (), {"_2BUTTON_PRESS": 5}),
        })
        self.assertTrue(mixer._on_press(None, FakeEvent()))
        self.assertFalse(mixer.start_fullscreen)
        self.assertTrue(mixer.window.unfullscreened)
        self.assertTrue(mixer._on_press(None, FakeEvent()))
        self.assertTrue(mixer.start_fullscreen)
        self.assertTrue(mixer.window.fullscreened)


class FakeWindow:
    def __init__(self, width=1920, height=1080, pos=(100, 100)):
        self.pos = pos
        self.width, self.height = width, height
        self.moved = None
        self.resized = None
        self.unfullscreened = False
        self.fullscreened = False

    def get_position(self):
        return self.pos

    def move(self, x, y):
        self.moved = (x, y)
        self.pos = (x, y)

    def resize(self, width, height):
        self.resized = (width, height)
        self.width, self.height = width, height

    def get_allocated_width(self):
        return self.width

    def get_allocated_height(self):
        return self.height

    def unfullscreen(self):
        self.unfullscreened = True

    def fullscreen(self):
        self.fullscreened = True


class FakeArea:
    def grab_focus(self):
        pass

    def queue_draw(self):
        pass

    def set_size_request(self, width, height):
        pass

    def get_allocated_width(self):
        return 1920

    def get_allocated_height(self):
        return 1080


class FakeFixed:
    def move(self, widget, x, y):
        pass

    def queue_resize(self):
        pass


class FakeGdk:
    class EventType:
        _2BUTTON_PRESS = 5
        BUTTON_PRESS = 4

    class ModifierType:
        BUTTON1_MASK = 256


class FakeEvent:
    def __init__(self, x=400.0, y=20.0, x_root=500, y_root=120,
                 event_type=4, state=256, button=1, time=0):
        self.x, self.y = x, y
        self.x_root, self.y_root = x_root, y_root
        self.type, self.state, self.button, self.time = event_type, state, button, time


class TestWindowMoveAndResize(unittest.TestCase):
    def _mixer(self):
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        mixer.area = FakeArea()
        mixer.fixed = FakeFixed()
        mixer.window = FakeWindow()
        mixer.Gdk = FakeGdk
        mixer.start_fullscreen = False
        return mixer

    def test_dragging_the_top_bar_moves_the_window(self):
        mixer = self._mixer()
        self.assertTrue(mixer._on_press(None, FakeEvent()))
        self.assertIsNotNone(mixer._window_drag)
        self.assertTrue(mixer._on_motion(None, FakeEvent(x_root=700, y_root=320)))
        self.assertEqual(mixer.window.moved, (300, 300))
        self.assertTrue(mixer._on_release(None, FakeEvent()))
        self.assertIsNone(mixer._window_drag)

    def test_dragging_the_corner_resizes_the_window(self):
        mixer = self._mixer()
        self.assertTrue(mixer._on_press(None, FakeEvent(x=1910, y=1070, x_root=900, y_root=900)))
        self.assertIsNotNone(mixer._window_resize)
        mixer._on_motion(None, FakeEvent(x=1910, y=1070, x_root=1000, y_root=960))
        self.assertEqual(mixer.window.resized, (2020, 1140))


class TestPitchAndSync(unittest.TestCase):
    def test_pitch_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            model = mixer_core.MixerModel()
            model.set_pitch("A", 6.5)
            model.save(path)
            loaded = mixer_core.MixerModel.load(path)
        self.assertAlmostEqual(loaded.pitch("A"), 6.5, places=6)

    def test_pitch_is_clamped(self):
        model = mixer_core.MixerModel()
        model.set_pitch("A", -99)
        self.assertEqual(model.pitch("A"), mixer_core.PITCH_MIN)
        model.set_pitch("A", 99)
        self.assertEqual(model.pitch("A"), mixer_core.PITCH_MAX)

    def test_master_switches_to_the_newly_started_deck(self):
        from MP_Liquid_Mixer import master_after_status
        self.assertEqual(
            master_after_status("A", "B", {"state": "paused", "path": "/b"},
                                {"state": "playing", "path": "/b"}, {"state": "playing"}),
            "B",
        )

    def test_master_switches_when_its_track_ends(self):
        from MP_Liquid_Mixer import master_after_status
        self.assertEqual(
            master_after_status("A", "A", {"state": "playing", "path": "/a1"},
                                {"state": "playing", "path": "/a2"}, {"state": "playing"}),
            "B",
        )
        self.assertEqual(
            master_after_status("A", "A", {"state": "playing", "path": "/a1"},
                                {"state": "stopped", "path": "/a1"}, {"state": "playing"}),
            "B",
        )

    def test_master_does_not_switch_to_an_idle_deck(self):
        from MP_Liquid_Mixer import master_after_status
        self.assertEqual(
            master_after_status("A", "A", {"state": "playing", "path": "/a1"},
                                {"state": "playing", "path": "/a2"}, {"state": "paused"}),
            "A",
        )

    def test_sync_matches_the_masters_pitch_without_bpm_tags(self):
        import MP_Liquid_Mixer as app
        from unittest.mock import patch
        mixer = app.LiquidMixer()
        mixer.area = None
        mixer.sync_master = "A"
        mixer._deck_status["A"] = {"pitch_percent": 4, "path": "/tmp/a.flac"}
        mixer._deck_status["B"] = {"path": "/tmp/b.flac"}
        mixer.model.set_pitch("B", 0.0)

        def immediate(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch.object(mixer, "_track_bpm", return_value=0.0), \
             patch("gi.repository.GLib.idle_add", side_effect=immediate):
            mixer._sync_deck("B")
            deadline = time.time() + 1.5
            while time.time() < deadline and mixer.model.pitch("B") != 4.0:
                time.sleep(0.01)
        self.assertEqual(mixer.model.pitch("B"), 4.0)
        self.assertEqual(mixer.sync_master, "A")

    def test_sync_pitch_uses_the_bpm_ratio(self):
        from MP_Liquid_Mixer import sync_pitch
        self.assertAlmostEqual(sync_pitch(0.0, 128.0, 124.0), (128 / 124 - 1) * 100, places=4)
        self.assertAlmostEqual(sync_pitch(2.0, 128.0, 124.0), (128 * 1.02 / 124 - 1) * 100, places=4)
        self.assertEqual(sync_pitch(3.5, 0.0, 124.0), 3.5)

    def test_shift_sync_makes_the_deck_master(self):
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        mixer.area = None
        mixer.sync_master = "A"
        mixer._sync_deck("B", manual=True)
        self.assertEqual(mixer.sync_master, "B")

    def test_pitch_drag_maps_top_to_plus_twenty(self):
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        mixer.area = FakeArea()
        box = mixer.boxes["pitch_a"]
        top = box[1] + 32
        bottom = box[1] + box[3] - 32
        mixer._drag_pitch("A", top)
        self.assertAlmostEqual(mixer.model.pitch("A"), 20.0, places=3)
        mixer._drag_pitch("A", bottom)
        self.assertAlmostEqual(mixer.model.pitch("A"), -20.0, places=3)
        mixer._drag_pitch("A", (top + bottom) / 2)
        self.assertEqual(mixer.model.pitch("A"), 0.0)

    def test_pitch_is_sent_to_the_decks(self):
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        mixer.model.set_pitch("A", 5)
        mixer.model.set_pitch("B", -3)
        mixer._pitch_sent = {}
        calls = []

        class Sock:
            def exists(self):
                return True

        with patch("mixer_core.player_socket", return_value=Sock()), \
             patch("mixer_core.send_ipc",
                   side_effect=lambda ns, cmd, **kw: calls.append((ns, cmd, kw.get("percent"))) or {"status": "ok"}):
            mixer._apply_pitch()
        sent = {ns: percent for ns, cmd, percent in calls if cmd == "set_pitch"}
        self.assertEqual(sent.get("mixerdeck_a"), 5)
        self.assertEqual(sent.get("mixerdeck_b"), -3)


class TestLayout(unittest.TestCase):
    def test_canvas_is_the_design_grid(self):
        boxes = mixer_core.layout(1920, 1080)
        self.assertEqual(boxes["canvas"], (0.0, 0.0, 1920.0, 1080.0))

    def test_two_decks_leave_a_wide_gap_for_the_mixer(self):
        boxes = mixer_core.layout(1920, 1080)
        deck_a = boxes["deck_a"]
        deck_b = boxes["deck_b"]
        mixer = boxes["mixer"]
        gap = deck_b[0] - (deck_a[0] + deck_a[2])
        self.assertGreaterEqual(gap, 280)
        self.assertGreater(mixer[2], 200)
        self.assertGreater(deck_a[2], mixer[2])
        # The mixer sits inside the gap, clear of both decks.
        self.assertGreater(mixer[0], deck_a[0] + deck_a[2])
        self.assertLess(mixer[0] + mixer[2], deck_b[0])

    def test_everything_stays_on_canvas(self):
        boxes = mixer_core.layout(1920, 1080)
        for name, (x, y, w, h) in boxes.items():
            if name == "canvas":
                continue
            self.assertGreaterEqual(x, 0, name)
            self.assertGreaterEqual(y, 0, name)
            self.assertLessEqual(x + w, 1920.001, name)
            self.assertLessEqual(y + h, 1080.001, name)

    def test_terminals_fit_inside_their_deck_cards(self):
        boxes = mixer_core.layout(1920, 1080)
        for channel in ("a", "b"):
            card = boxes[f"deck_{channel}"]
            term = boxes[f"deck_{channel}_term"]
            self.assertGreaterEqual(term[0], card[0])
            self.assertGreaterEqual(term[1], card[1])
            self.assertLessEqual(term[0] + term[2], card[0] + card[2])
            self.assertLessEqual(term[1] + term[3], card[1] + card[3])

    def test_four_strips_fit_the_mixer(self):
        boxes = mixer_core.layout(1920, 1080)
        mixer = boxes["mixer"]
        for channel in mixer_core.CHANNELS:
            strip = boxes[f"strip_{channel}"]
            self.assertGreater(strip[2], 20)
            self.assertGreaterEqual(strip[0], mixer[0])
            self.assertLessEqual(strip[0] + strip[2], mixer[0] + mixer[2])

    def test_4k_scales_without_stretching(self):
        boxes = mixer_core.layout(3840, 2160)
        self.assertEqual(boxes["canvas"], (0.0, 0.0, 3840.0, 2160.0))
        self.assertAlmostEqual(boxes["deck_a"][2], 786 * 2, places=3)
        gap = boxes["deck_b"][0] - (boxes["deck_a"][0] + boxes["deck_a"][2])
        self.assertAlmostEqual(gap, 300 * 2, places=3)

    def test_knob_grid_runs_gain_treble_mid_low_then_filter(self):
        boxes = mixer_core.layout(1920, 1080)
        mixer = boxes["mixer"]
        rows = ("gain", "high", "mid", "low", "filter")
        for row in rows:
            # No row carries a left-hand legend any more: the columns align
            # with the channel faders instead of being pushed right.
            self.assertNotIn(f"knob_{row}_label", boxes)
            for channel in mixer_core.CHANNELS:
                box = boxes[f"knob_{row}_{channel}"]
                self.assertGreaterEqual(box[0], mixer[0])
                self.assertLessEqual(box[0] + box[2], mixer[0] + mixer[2])
        for upper, lower in zip(rows, rows[1:]):
            self.assertLess(boxes[f"knob_{upper}_A"][1], boxes[f"knob_{lower}_A"][1])
        # The filter knob is 1.5x the size of the EQ knobs.
        self.assertAlmostEqual(boxes["knob_filter_A"][2], boxes["knob_low_A"][2] * 1.5, places=3)

    def test_knob_columns_line_up_with_the_faders(self):
        boxes = mixer_core.layout(1920, 1080)
        for channel in mixer_core.CHANNELS:
            fader_centre = boxes[f"strip_{channel}"][0] + boxes[f"strip_{channel}"][2] / 2
            knob_centre = boxes[f"knob_low_{channel}"][0] + boxes[f"knob_low_{channel}"][2] / 2
            self.assertAlmostEqual(knob_centre, fader_centre, places=3)

    def test_each_channel_has_effect_squares_fx_and_monitor_buttons(self):
        boxes = mixer_core.layout(1920, 1080)
        for channel in mixer_core.CHANNELS:
            for slot in (1, 2, 3, 4):
                self.assertIn(f"effect_slot_{slot}_{channel}", boxes)
            self.assertIn(f"effect_filter_{channel}", boxes)
            fx = boxes[f"fx_{channel}"]
            cue = boxes[f"monitor_{channel}"]
            # FX sits above the cue button, and both above the fader.
            self.assertLess(fx[1] + fx[3], cue[1])
            self.assertLess(cue[1] + cue[3], boxes[f"strip_{channel}"][1])
            self.assertIn(f"meter_{channel}", boxes)

    def test_master_and_monitor_trims_sit_in_the_top_bar(self):
        boxes = mixer_core.layout(1920, 1080)
        self.assertEqual(boxes["master_knob"][3], 44.0)
        self.assertEqual(boxes["monitor_knob"][3], 44.0)
        self.assertLess(boxes["profile_button"][1] + boxes["profile_button"][3],
                        boxes["mixer"][1] + 10)

    def test_volume_faders_sit_below_the_filter_knobs(self):
        boxes = mixer_core.layout(1920, 1080)
        self.assertGreaterEqual(boxes["strip_A"][3], 100)
        self.assertLess(boxes["knob_filter_A_value"][1] + boxes["knob_filter_A_value"][3],
                        boxes["strip_A_label"][1])

    def test_pitch_sliders_flank_the_mixer(self):
        boxes = mixer_core.layout(1920, 1080)
        self.assertLess(boxes["pitch_a"][0] + boxes["pitch_a"][2], boxes["mixer"][0] + 1)
        self.assertGreater(boxes["pitch_b"][0], boxes["mixer"][0] + boxes["mixer"][2] - 1)
        self.assertEqual(boxes["pitch_a"][1], boxes["pitch_b"][1])

    def test_sync_buttons_are_in_the_deck_headers(self):
        boxes = mixer_core.layout(1920, 1080)
        for channel in ("a", "b"):
            header = boxes[f"deck_{channel}_header"]
            sync = boxes[f"sync_{channel}"]
            self.assertGreaterEqual(sync[0], header[0])
            self.assertLessEqual(sync[0] + sync[2], header[0] + header[2])
            self.assertLessEqual(sync[1] + sync[3], header[1] + header[3])

    def test_record_button_is_inside_the_mixer(self):
        boxes = mixer_core.layout(1920, 1080)
        mixer = boxes["mixer"]
        record = boxes["record_button"]
        self.assertGreaterEqual(record[0], mixer[0])
        self.assertLessEqual(record[0] + record[2], mixer[0] + mixer[2])
        self.assertLessEqual(record[1] + record[3], mixer[1] + mixer[3])

    def test_opacity_bar_is_80_percent_and_centred(self):
        boxes = mixer_core.layout(1920, 1080)
        x, _y, width, _h = boxes["opacity"]
        self.assertAlmostEqual(width, 1920 * 0.8, places=3)
        self.assertAlmostEqual(x, 1920 * 0.1, places=3)
        self.assertAlmostEqual(x + width, 1920 * 0.9, places=3)

    def test_letterboxes_when_aspect_differs(self):
        boxes = mixer_core.layout(1920, 1200)
        self.assertAlmostEqual(boxes["canvas"][1], 60.0, places=3)
        self.assertAlmostEqual(boxes["canvas"][2], 1920.0, places=3)


class TestCoordinateMaps(unittest.TestCase):
    def test_opacity_endpoints_and_inverse(self):
        box = mixer_core.layout(1920, 1080)["opacity"]
        self.assertAlmostEqual(mixer_core.opacity_from_x(box[0], box), mixer_core.MIN_OPACITY, places=6)
        self.assertAlmostEqual(mixer_core.opacity_from_x(box[0] + box[2], box), mixer_core.MAX_OPACITY, places=6)
        for value in (0.2, 0.5, 0.8):
            x = mixer_core.x_from_opacity(value, box)
            self.assertAlmostEqual(mixer_core.opacity_from_x(x, box), value, places=5)

    def test_fader_top_is_full(self):
        box = mixer_core.layout(1920, 1080)["strip_A"]
        self.assertAlmostEqual(mixer_core.fader_value_from_y(box[1], box), 1.0, places=6)
        self.assertAlmostEqual(mixer_core.fader_value_from_y(box[1] + box[3], box), 0.0, places=6)
        mid = mixer_core.y_from_fader_value(0.5, box)
        self.assertAlmostEqual(mixer_core.fader_value_from_y(mid, box), 0.5, places=5)

    def test_crossfader_maps_left_to_right(self):
        box = mixer_core.layout(1920, 1080)["xfader"]
        self.assertAlmostEqual(mixer_core.xfader_value_from_x(box[0], box), 0.0, places=6)
        self.assertAlmostEqual(mixer_core.xfader_value_from_x(box[0] + box[2], box), 1.0, places=6)

    def test_inside(self):
        box = (10.0, 10.0, 20.0, 20.0)
        self.assertTrue(mixer_core.inside(15, 15, box))
        self.assertFalse(mixer_core.inside(31, 15, box))


class TestThemes(unittest.TestCase):
    def test_cycle_moves_to_a_different_known_theme(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "theme"
            with patch.object(mixer_core, "THEME_PATH", path):
                mixer_core.write_theme("dreamworlds_ultra")
                self.assertEqual(mixer_core.read_theme(), "dreamworlds_ultra")
                nxt = mixer_core.next_theme("dreamworlds_ultra")
                self.assertNotEqual(nxt, "dreamworlds_ultra")
                self.assertIn(nxt, mixer_core.theme_cycle())

    def test_unknown_theme_normalizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "theme"
            path.write_text("not-a-theme\n", encoding="utf-8")
            with patch.object(mixer_core, "THEME_PATH", path):
                self.assertTrue(mixer_core.read_theme())

    def test_colours_always_resolve(self):
        accent, secondary, tertiary = mixer_core.theme_colors("not-a-theme")
        for channel in (accent, secondary, tertiary):
            self.assertEqual(len(channel), 3)
            self.assertTrue(all(0 <= c <= 255 for c in channel))


class TestDeckPaths(unittest.TestCase):
    def test_deck_files_are_namespaced_like_the_player(self):
        self.assertEqual(mixer_core.player_socket("mixerdeck_a").name,
                         "mp_audio_player-mixerdeck_a.sock")
        self.assertEqual(mixer_core.player_port("mixerdeck_a").name,
                         "mp_audio_player-mixerdeck_a.port")
        self.assertEqual(mixer_core.player_state("mixerdeck_a").name,
                         "mp_player_state-mixerdeck_a.json")
        self.assertEqual(mixer_core.player_pid("mixerdeck_a").name,
                         "mp_player-mixerdeck_a.pid")
        self.assertEqual(mixer_core.player_socket("").name, "mp_audio_player.sock")

    def test_send_ipc_without_a_deck_is_quiet(self):
        self.assertIsNone(mixer_core.send_ipc("liquidmixer_missing_deck", "status", timeout=0.05))


class TestVisualizerPid(unittest.TestCase):
    def test_a_zombie_visualizer_is_not_alive(self):
        proc = subprocess.Popen(["true"])
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "viz.pid"
                path.write_text(str(proc.pid), encoding="utf-8")
                time.sleep(0.25)
                with patch.object(mixer_core, "VISUALIZER_PID_PATH", path):
                    self.assertIsNone(mixer_core.visualizer_pid_alive())
        finally:
            proc.wait(timeout=2)


class TestMixerLock(unittest.TestCase):
    def test_second_mixer_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "liquid_mixer.lock"
            with patch.object(mixer_core, "LOCK_PATH", path):
                fd = mixer_core.acquire_mixer_lock()
                if fd == "unsupported":
                    self.skipTest("platform has no fcntl")
                self.assertIsNotNone(fd)
                try:
                    self.assertIsNone(mixer_core.acquire_mixer_lock())
                finally:
                    os.close(fd)


class TestPlayerNamespaceWiring(unittest.TestCase):
    """The player must expose the two things the mixer relies on."""

    def test_player_paths_honour_the_namespace(self):
        code = (
            "import sys; sys.path.insert(0, %r); import MP_Audio_Player as p; "
            "print(p.PID_FILE.name); print(p.SOCKET_PATH.name); "
            "print(p.DAEMON_LOCK_FILE.name); print(p.RESUME_STATE_FILE.name); "
            "print(p.STATE_FILE.name)"
        ) % str(PROJECT_ROOT)
        env = dict(os.environ, MP_PLAYER_NAMESPACE="mixerdeck_test")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             env=env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        names = out.stdout.split()
        self.assertEqual(names, [
            "mp_player-mixerdeck_test.pid",
            "mp_audio_player-mixerdeck_test.sock",
            "mp_player-mixerdeck_test.lock",
            "playback_resume-mixerdeck_test.json",
            "mp_player_state-mixerdeck_test.json",
        ])

    def test_player_offers_no_autoplay(self):
        out = subprocess.run([sys.executable, str(PROJECT_ROOT / "MP_Audio_Player.py"), "--help"],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("--no-autoplay", out.stdout)


class TestDeckArgs(unittest.TestCase):
    """Decks mix tracks, so they must open the Music Folders library."""

    def test_decks_open_music_mode(self):
        import MP_Liquid_Mixer as app
        argv = app.deck_argv("sensorium")
        self.assertIn("--interactive", argv)
        self.assertIn("--music-mode", argv)
        self.assertNotIn("--mix-archive-mode", argv)
        folder = argv.index("--music-folder")
        self.assertEqual(argv[folder + 1], "ALL")
        self.assertIn("--no-autoplay", argv)
        theme = argv.index("--theme")
        self.assertEqual(argv[theme + 1], "sensorium")
        # No audio file is passed: the deck opens a library, not a mix.
        self.assertFalse(any(arg.endswith((".flac", ".wav", ".mp3")) for arg in argv))

    def test_a_first_run_deck_is_handed_the_theme_playlist(self):
        import MP_Liquid_Mixer as app
        argv = app.deck_argv("sensorium", "/tmp/first_run_mix_playlist.m3u8")
        self.assertIn("--playlist", argv)
        playlist = argv.index("--playlist")
        self.assertEqual(argv[playlist + 1], "/tmp/first_run_mix_playlist.m3u8")
        # Still paused: the track is ready, the DJ starts it.
        self.assertIn("--no-autoplay", argv)

    def test_a_normal_deck_has_no_playlist_override(self):
        import MP_Liquid_Mixer as app
        self.assertNotIn("--playlist", app.deck_argv("sensorium"))


class TestFirstRunThemeTrack(unittest.TestCase):
    """A first launch with no library opens the bundled theme track."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = Path(self._tmp.name)
        self.project = Path(self._tmp.name) / "install"
        self.project.mkdir()

    def _patched(self, configured=False):
        return (
            patch.object(mixer_core, "CONFIG_DIR", self.config),
            patch.object(mixer_core, "CONFIG_ENV_PATHS",
                         (self.project / "config.env", self.config / "config.env")),
            patch.object(mixer_core, "library_configured", return_value=configured),
            patch.object(mixer_core, "theme_track_path",
                         side_effect=lambda: Path(self._track())),
        )

    def _track(self):
        track = self.project / "MP_SAMPLES" / mixer_core.THEME_TRACK_NAME
        track.parent.mkdir(parents=True, exist_ok=True)
        if not track.is_file():
            track.write_bytes(b"fLaC")
        return str(track)

    def test_no_music_or_archive_configuration_is_a_first_run(self):
        (self.project / "config.env").write_text(
            'MIX_ARCHIVE_CONFIGURED="false"\nMIX_ARCHIVE_DIR=""\n'
            'EXTRA_MIX_ARCHIVE_DIRS=""\n', encoding="utf-8")
        values = mixer_core.config_values((self.project / "config.env",))
        with patch.object(mixer_core, "music_folders_configured", return_value=False):
            self.assertFalse(mixer_core.music_folders_configured(values))
        self.assertFalse(mixer_core.mix_archive_configured(values))

    def test_a_configured_archive_is_not_a_first_run(self):
        cfg = self.project / "config.env"
        cfg.write_text('MIX_ARCHIVE_CONFIGURED="true"\n'
                       'MIX_ARCHIVE_DIR="/mnt/archive/MIX_ARCHIVE"\n', encoding="utf-8")
        values = mixer_core.config_values((cfg,))
        self.assertTrue(mixer_core.mix_archive_configured(values))

    def test_extra_archive_dirs_also_count_as_configured(self):
        cfg = self.project / "config.env"
        cfg.write_text('EXTRA_MIX_ARCHIVE_DIRS="/mnt/extra/MIX_ARCHIVE"\n', encoding="utf-8")
        self.assertTrue(mixer_core.mix_archive_configured(mixer_core.config_values((cfg,))))

    def test_a_generated_music_playlist_counts_as_configured(self):
        folder = self.project / "PLAYLISTS_GENERATED"
        folder.mkdir(parents=True)
        (folder / "DJ_Tracks_ALL_MUSIC_FOLDERS.m3u8").write_text(
            "#EXTM3U\n#EXTINF:300,Darkstar\n/music/01-darkstar.flac\n", encoding="utf-8")
        with patch.object(mixer_core, "PROJECT_ROOT", self.project), \
             patch.object(mixer_core, "_music_folders_module", return_value=None):
            self.assertTrue(mixer_core.music_folders_configured({}))

    def test_an_empty_playlist_is_not_a_configured_library(self):
        folder = self.project / "PLAYLISTS_GENERATED"
        folder.mkdir(parents=True)
        (folder / "DJ_Tracks_ALL_MUSIC_FOLDERS.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
        with patch.object(mixer_core, "PROJECT_ROOT", self.project), \
             patch.object(mixer_core, "_music_folders_module", return_value=None):
            self.assertFalse(mixer_core.music_folders_configured({}))

    def test_theme_playlist_holds_the_track_ready_to_play(self):
        track = self._track()
        patchers = self._patched(configured=False)
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        target = mixer_core.write_first_run_playlist(track)
        self.assertIsNotNone(target)
        text = Path(target).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#EXTM3U"))
        self.assertIn(track, text)
        self.assertIn(Path(track).stem, text)

    def test_a_configured_library_never_writes_the_playlist(self):
        target = self.config / "first_run_mix_playlist.m3u8"
        target.write_text("stale\n", encoding="utf-8")
        patchers = self._patched(configured=True)
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.assertIsNone(mixer_core.write_first_run_playlist())
        self.assertFalse(target.exists())

    def test_the_mixer_loads_the_theme_track_only_on_a_first_run(self):
        import MP_Liquid_Mixer as app
        track = self._track()
        playlist = self.config / "first_run_mix_playlist.m3u8"

        with patch.object(mixer_core, "first_run_preload_track", return_value=track), \
             patch.object(mixer_core, "write_first_run_playlist", return_value=playlist):
            mixer = app.LiquidMixer()
        self.assertEqual(mixer._first_run_track, track)
        self.assertEqual(mixer._first_run_playlist, playlist)

        with patch.object(mixer_core, "first_run_preload_track", return_value=None), \
             patch.object(mixer_core, "write_first_run_playlist", return_value=None):
            normal = app.LiquidMixer()
        self.assertIsNone(normal._first_run_track)
        self.assertIsNone(normal._first_run_playlist)


class TestDeckTheme(unittest.TestCase):
    def test_console_background_follows_the_theme(self):
        dark = mixer_core.theme_console("into_darkness_void_out")
        bright = mixer_core.theme_console("stream_of_frequency")
        self.assertNotEqual(dark, bright)
        for colour in (dark, bright):
            self.assertEqual(len(colour), 3)
            self.assertTrue(all(0 <= channel <= 255 for channel in colour))

    def test_mixer_repaints_decks_on_theme_change(self):
        import MP_Liquid_Mixer as app
        self.assertTrue(callable(getattr(app.LiquidMixer, "_apply_deck_theme", None)))
        self.assertTrue(callable(getattr(app.LiquidMixer, "_style_deck_terminal", None)))


class TestEqRouting(unittest.TestCase):
    def test_eq_bands_are_sent_to_both_live_decks(self):
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        mixer.model.set_eq("A", "low", 4.0)
        mixer.model.set_eq("B", "high", -3.0)
        mixer._eq_sent = {}
        calls = []

        class Sock:
            def exists(self):
                return True

        with patch("mixer_core.player_socket", return_value=Sock()), \
             patch("mixer_core.send_ipc",
                   side_effect=lambda ns, cmd, **kw: calls.append((ns, cmd, kw.get("bands"))) or {"status": "ok"}):
            mixer._apply_eq()
        sent = {ns: bands for ns, cmd, bands in calls if cmd == "set_eq_bands"}
        self.assertIn("mixerdeck_a", sent)
        self.assertIn("mixerdeck_b", sent)
        self.assertEqual(len(sent["mixerdeck_a"]), 10)
        self.assertEqual(sent["mixerdeck_a"][0:3], [4.0, 4.0, 4.0])
        self.assertEqual(sent["mixerdeck_b"][7:10], [-3.0, -3.0, -3.0])


class TestPlayAndThemeRouting(unittest.TestCase):
    def _mixer(self):
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        mixer.area = None
        return mixer

    def test_left_play_toggles_deck_a(self):
        from unittest.mock import patch
        mixer = self._mixer()
        calls = []
        with patch("mixer_core.send_ipc",
                   side_effect=lambda ns, cmd, **kw: calls.append((ns, cmd)) or {"status": "ok"}):
            mixer._apply_s4("play:A", 1.0)
            deadline = time.time() + 1.0
            while time.time() < deadline and not calls:
                time.sleep(0.01)
        self.assertIn(("mixerdeck_a", "toggle"), calls)

    def test_right_play_toggles_deck_b(self):
        from unittest.mock import patch
        mixer = self._mixer()
        calls = []
        with patch("mixer_core.send_ipc",
                   side_effect=lambda ns, cmd, **kw: calls.append((ns, cmd)) or {"status": "ok"}):
            mixer._apply_s4("play:B", 1.0)
            deadline = time.time() + 1.0
            while time.time() < deadline and not calls:
                time.sleep(0.01)
        self.assertIn(("mixerdeck_b", "toggle"), calls)

    def test_browse_wheel_moves_the_target_deck(self):
        mixer = self._mixer()

        class FakeTerm:
            def __init__(self):
                self.keys = []

            def feed_child_binary(self, data):
                self.keys.append(bytes(data))

        term_a, term_b = FakeTerm(), FakeTerm()
        mixer.decks = {"A": {"vte": term_a}, "B": {"vte": term_b}}
        mixer.browse_target = "A"
        mixer._apply_s4("browse", 1.0)
        mixer._apply_s4("browse", -1.0)
        mixer._browse_load("A")
        self.assertEqual(term_a.keys, [b"\x1b[B", b"\x1b[A", b"\r"])
        self.assertFalse(mixer._browse_dirty["A"])
        self.assertEqual(term_b.keys, [])
        # The right Play button also focuses deck B for browsing (patched so a
        # test never toggles a real deck that happens to be running).
        with patch("mixer_core.send_ipc", return_value={"status": "ok"}):
            mixer._apply_s4("play:B", 1.0)
        self.assertEqual(mixer.browse_target, "B")
        mixer._apply_s4("browse", 1.0)
        self.assertEqual(term_b.keys, [b"\x1b[B"])

    def test_theme_repaints_each_deck_terminal(self):
        mixer = self._mixer()

        class FakeRGBA:
            def __init__(self, red, green, blue, alpha=1.0):
                self.values = (red, green, blue, alpha)

        class FakeTerm:
            def __init__(self):
                self.background = None
                self.foreground = None

            def set_color_background(self, colour):
                self.background = colour

            def set_color_foreground(self, colour):
                self.foreground = colour

        mixer.Gdk = type("FakeGdk", (), {"RGBA": FakeRGBA})
        mixer.theme = "stream_of_frequency"
        term = FakeTerm()
        mixer.decks = {"A": {"vte": term}}
        mixer._apply_deck_theme()
        background = mixer_core.theme_console("stream_of_frequency")
        self.assertEqual(tuple(round(c * 255) for c in term.background.values[:3]), background)
        self.assertIsNotNone(term.foreground)
        # Changing the theme changes the pane colour on the next apply.
        mixer.theme = "into_darkness_void_out"
        mixer._apply_deck_theme()
        self.assertNotEqual(tuple(round(c * 255) for c in term.background.values[:3]), background)


class TestBrowsePushDecision(unittest.TestCase):
    def test_playing_deck_stops(self):
        import MP_Liquid_Mixer as app
        self.assertEqual(app.browse_action({"state": "playing", "path": "/x"}, False), "toggle")
        self.assertEqual(app.browse_action({"state": "PLAYING", "path": "/x"}, True), "toggle")

    def test_paused_after_browsing_loads_the_selection(self):
        import MP_Liquid_Mixer as app
        self.assertEqual(app.browse_action({"state": "paused", "path": "/x"}, True), "load")

    def test_paused_without_browsing_resumes(self):
        import MP_Liquid_Mixer as app
        self.assertEqual(app.browse_action({"state": "paused", "path": "/x"}, False), "play")

    def test_empty_deck_loads(self):
        import MP_Liquid_Mixer as app
        self.assertEqual(app.browse_action({"state": "stopped"}, False), "load")


class TestBrowsePushActions(unittest.TestCase):
    def _mixer(self):
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        mixer.area = None
        return mixer

    def test_playing_push_sends_toggle(self):
        from unittest.mock import patch
        mixer = self._mixer()
        calls = []
        with patch("mixer_core.deck_status", return_value={"state": "playing", "path": "/x"}), \
             patch("mixer_core.send_ipc", side_effect=lambda ns, cmd, **kw: calls.append((ns, cmd)) or {"status": "ok"}):
            mixer.browse_target = "A"
            mixer._browse_push()
            deadline = time.time() + 1.0
            while time.time() < deadline and not calls:
                time.sleep(0.01)
        self.assertIn(("mixerdeck_a", "toggle"), calls)

    def test_paused_push_without_browsing_resumes(self):
        from unittest.mock import patch
        mixer = self._mixer()
        mixer._browse_dirty["A"] = False
        calls = []
        with patch("mixer_core.deck_status", return_value={"state": "paused", "path": "/x"}), \
             patch("mixer_core.send_ipc", side_effect=lambda ns, cmd, **kw: calls.append((ns, cmd)) or {"status": "ok"}):
            mixer.browse_target = "A"
            mixer._browse_push()
            deadline = time.time() + 1.0
            while time.time() < deadline and not calls:
                time.sleep(0.01)
        self.assertIn(("mixerdeck_a", "play"), calls)

    def test_paused_push_after_browsing_feeds_enter(self):
        from unittest.mock import patch
        mixer = self._mixer()

        class FakeTerm:
            def __init__(self):
                self.keys = []

            def feed_child_binary(self, data):
                self.keys.append(bytes(data))

        term = FakeTerm()
        mixer.decks = {"A": {"vte": term}}
        mixer.browse_target = "A"
        mixer._browse_dirty["A"] = True

        def immediate(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch("mixer_core.deck_status", return_value={"state": "paused", "path": "/x"}), \
             patch("gi.repository.GLib.idle_add", side_effect=immediate):
            mixer._browse_push()
            deadline = time.time() + 1.0
            while time.time() < deadline and not term.keys:
                time.sleep(0.01)
        self.assertEqual(term.keys, [b"\r"])
        self.assertFalse(mixer._browse_dirty["A"])


class TestMixerModule(unittest.TestCase):
    def test_version_and_inheritance(self):
        import MP_Liquid_Mixer as app
        from MP_Audio_Player_Liquid_Glass_OpenGL import GlassHost
        self.assertEqual(app.core.VERSION, "0.0.1")
        self.assertTrue(issubclass(app.LiquidMixer, GlassHost))

    def test_mixer_layout_uses_the_shared_theme_file(self):
        # The mixer and both decks must read the same source of truth.
        self.assertEqual(Path(mixer_core.THEME_PATH).name, "theme")


class TestLoaderVideo(unittest.TestCase):
    """Exit_MP_Video.mp4 brackets the mixer window: before it exists, and after."""

    def setUp(self):
        import MP_Liquid_Mixer as app
        self.app = app
        # These tests exercise the movie calls without writing the real
        # ~/.config/mix-manager/liquid_mixer.log.
        log_patcher = patch.object(app, "_log")
        log_patcher.start()
        self.addCleanup(log_patcher.stop)

    def test_the_shared_loader_script_is_used_for_both_playbacks(self):
        mixer = self.app.LiquidMixer()
        with patch.object(self.app.subprocess, "run") as run:
            mixer._play_loader_video("--intro-force")
            mixer._play_loader_video("--exit-force")
        self.assertEqual(run.call_count, 2)
        calls = run.call_args_list
        self.assertEqual(calls[0].args[0][:2], ["bash", str(self.app.SCRIPTS_DIR / "display_loader_video.sh")])
        self.assertEqual(calls[0].args[0][2], "--intro-force")
        self.assertEqual(calls[1].args[0][2], "--exit-force")
        for call in calls:
            self.assertEqual(call.kwargs["env"]["MP_EXIT_VIDEO_NO_TTY"], "1")
            self.assertEqual(call.kwargs["stdin"], subprocess.DEVNULL)

    def test_a_missing_movie_script_never_blocks_the_window(self):
        mixer = self.app.LiquidMixer()
        with patch.object(self.app, "SCRIPTS_DIR", Path("/nonexistent/mp-scripts")), \
             patch.object(self.app.subprocess, "run") as run:
            mixer._play_loader_video("--intro-force")
        run.assert_not_called()

    def test_run_plays_the_intro_before_the_window_and_the_exit_after(self):
        class _Fake:
            def __init__(self, *args, **kwargs):
                pass

            def __getattr__(self, name):
                return _Fake()

            def __call__(self, *args, **kwargs):
                return _Fake()

        class _FakeGtk(_Fake):
            def main(self):
                raise RuntimeError("the test stops the main loop")

        mixer = self.app.LiquidMixer()
        events = []
        # run() imports Gdk itself after _import_gtk() has pinned the versions.
        import gi
        gi.require_version("Gdk", "3.0")
        gi.require_version("Gtk", "3.0")

        def record(flag):
            events.append(flag)

        with patch.object(mixer, "_play_loader_video", side_effect=record), \
             patch.object(mixer, "_shutdown"), \
             patch.object(mixer, "_destroy_window", side_effect=lambda: events.append("destroy")), \
             patch.object(mixer, "_spawn_decks"), \
             patch.object(mixer, "_start_s4"), \
             patch.object(self.app, "_import_gtk", return_value=(_Fake(), _FakeGtk(), _Fake())):
            with self.assertRaises(RuntimeError):
                mixer.run()
        # The intro ran before the window was built; the exit ran in finally,
        # after the window was destroyed.
        self.assertEqual(events, ["--intro-force", "destroy", "--exit-force"])

    def test_a_second_launch_raises_the_open_mixer(self):
        import liquid_mixer_launch
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False), \
             patch.object(mixer_core, "acquire_mixer_lock", return_value=None), \
             patch.object(liquid_mixer_launch, "focus_mixer", return_value=True) as focus:
            self.assertEqual(self.app.main([]), 0)
        focus.assert_called_once()


class TestMixerShortcut(unittest.TestCase):
    def test_the_linux_shortcut_points_at_the_launcher(self):
        from install_desktop_shortcuts import shortcut_targets
        target = shortcut_targets(PROJECT_ROOT)["MP Liquid Mixer"]
        launcher = PROJECT_ROOT / "scripts" / "launch_liquid_mixer.sh"
        self.assertEqual(Path(target["script"]), launcher)
        self.assertTrue(launcher.is_file())
        self.assertTrue(os.access(launcher, os.X_OK))
        self.assertFalse(target["terminal"])

    def test_the_launcher_runs_the_mixer(self):
        text = (PROJECT_ROOT / "scripts" / "launch_liquid_mixer.sh").read_text(encoding="utf-8")
        self.assertIn('MP_Liquid_Mixer/run.sh', text)
        self.assertIn("MP_PYTHON", text)
        self.assertNotIn("--intro-force", text)


class TestSettingsAppliedToTheWindow(unittest.TestCase):
    """The Settings snapshot must change the live window, not only the file."""

    def setUp(self):
        # _apply_settings() really saves, so point the write at a scratch file:
        # a test run must never overwrite the user's own preferences.
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self._settings_file = Path(self._tmp.name) / "liquid_mixer_settings.json"
        self._save_patch = patch("mixer_settings.SETTINGS_PATH", self._settings_file)
        self._save_patch.start()
        self.addCleanup(self._save_patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def _mixer(self):
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        mixer.area = MagicMock()
        mixer.window = MagicMock()
        return mixer

    def test_saving_the_format_moves_the_recorder(self):
        import mixer_settings
        mixer = self._mixer()
        mixer.recorder = MagicMock()
        mixer.recorder.format = "WAV"
        with patch.object(mixer, "_route_decks_to_master"), \
             patch.object(mixer, "_apply_default_eq"), \
             patch.object(mixer.settings, "save"):
            mixer._apply_settings(mixer_settings.Settings(recording_format="FLAC"))
        mixer.recorder.set_format.assert_called_with("FLAC")

    def test_saving_the_part_limit_moves_the_recorder(self):
        import mixer_settings
        mixer = self._mixer()
        mixer.recorder = MagicMock()
        with patch.object(mixer, "_route_decks_to_master"), \
             patch.object(mixer, "_apply_default_eq"), \
             patch.object(mixer.settings, "save"):
            mixer._apply_settings(mixer_settings.Settings(recording_part_limit_gib=1))
        mixer.recorder.set_part_limit_gib.assert_called_with(1)

    def test_master_output_is_pushed_into_every_deck_environment(self):
        import mixer_settings
        mixer = self._mixer()
        mixer.decks = {
            "A": {"namespace": "mixerdeck_a", "env": {"MP_PLAYER_NAMESPACE": "mixerdeck_a"}},
            "B": {"namespace": "mixerdeck_b", "env": {"MP_PLAYER_NAMESPACE": "mixerdeck_b"}},
        }
        settings = mixer_settings.Settings(master_output_device="alsa_output.hdmi")
        with patch("mixer_settings.move_process_streams", return_value=1) as moved:
            mixer._route_decks_to_master(settings)
        for channel in ("A", "B"):
            self.assertEqual(mixer.decks[channel]["env"]["PULSE_SINK"], "alsa_output.hdmi")
        self.assertEqual(moved.call_count, len(mixer.decks))

    def test_the_default_equaliser_is_sent_to_the_decks(self):
        import mixer_settings
        mixer = self._mixer()
        mixer.decks = {"A": {"namespace": "mixerdeck_a"}}
        mixer.model.eq["A"]["low"] = 6.0
        settings = mixer_settings.Settings(eq_preset="Techno")
        with patch.object(mixer_core, "player_socket") as sock, \
             patch.object(mixer_core, "send_ipc") as send:
            sock.return_value.exists.return_value = True
            # The preset reply carries the engine's real 10-band curve, which
            # is what stops the next mixer flush from cancelling the preset.
            send.return_value = {"bands": [4.0, 3.0, 1.0, 0.0, 0.0, 1.0, 2.0, 3.0, 2.0, 1.0]}
            mixer._apply_default_eq(settings)
        preset_calls = [call for call in send.call_args_list
                        if len(call.args) > 1 and call.args[1] == "set_eq_preset"]
        self.assertEqual(len(preset_calls), 1)
        self.assertEqual(preset_calls[0].kwargs["preset"], "Techno")
        # The knobs reset to flat so the preset is what is actually heard.
        self.assertEqual(mixer.model.eq["A"]["low"], 0.0)
        # The preset's own curve is remembered as sent, not the flat bands.
        self.assertEqual(mixer._eq_sent["A"][0], 4.0)
        self.assertFalse(mixer._eq_dirty)

    def test_disabling_the_visualizer_stops_the_window_launching_one(self):
        import mixer_settings
        mixer = self._mixer()
        mixer._viz_owned = False
        mixer.recorder = MagicMock()
        mixer.with_visualizer = True
        with patch.object(mixer, "_route_decks_to_master"), \
             patch.object(mixer, "_apply_default_eq"), \
             patch.object(mixer.settings, "save"):
            mixer._apply_settings(mixer_settings.Settings(visualizer_enabled=False))
        self.assertFalse(mixer.with_visualizer)

    def test_the_startup_video_switch_is_honoured_before_the_script_runs(self):
        mixer = self._mixer()
        mixer.settings.startup_video = False
        mixer.settings.exit_video = False
        with patch("subprocess.run") as run:
            mixer._play_loader_video("--intro-force")
            mixer._play_loader_video("--exit-force")
        run.assert_not_called()

    def test_an_enabled_startup_video_still_runs_the_script(self):
        mixer = self._mixer()
        mixer.settings.startup_video = True
        with patch("subprocess.run") as run:
            mixer._play_loader_video("--intro-force")
        self.assertTrue(run.called)


class TestMixerChrome(unittest.TestCase):
    def test_new_buttons_have_their_own_boxes(self):
        boxes = mixer_core.layout()
        for key in ("trance_button", "format_button", "bitdepth_button",
                    "credit_button", "clock_button", "export_button"):
            self.assertIn(key, boxes)
        fmt, bits = boxes["format_button"], boxes["bitdepth_button"]
        self.assertLessEqual(fmt[0] + fmt[2], bits[0])
        self.assertGreaterEqual(boxes["clock_button"][1], boxes["credit_button"][1] + boxes["credit_button"][3])
        self.assertLessEqual(boxes["clock_button"][1] + boxes["clock_button"][3],
                             boxes["mixer"][1] + boxes["mixer"][3])
        self.assertGreaterEqual(boxes["export_button"][1],
                                boxes["clock_button"][1] + boxes["clock_button"][3])
        self.assertLessEqual(boxes["export_button"][1] + boxes["export_button"][3],
                             boxes["mixer"][1] + boxes["mixer"][3])

    def test_the_settings_button_sits_in_the_top_bar_without_overlaps(self):
        boxes = mixer_core.layout()
        self.assertIn("settings_button", boxes)
        button = boxes["settings_button"]
        trance = boxes["trance_button"]
        visualizer = boxes["visualizer_button"]
        self.assertGreaterEqual(button[0], trance[0] + trance[2])
        self.assertLessEqual(button[0] + button[2], visualizer[0])
        self.assertEqual(button[1], trance[1])
        self.assertEqual(button[3], trance[3])
        self.assertLessEqual(button[1] + button[3], boxes["top_bar"][3])

    def test_the_settings_button_opens_the_dialog(self):
        from types import SimpleNamespace
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        mixer.area = MagicMock()
        mixer.window = MagicMock()
        mixer.window.get_allocated_width.return_value = 1920
        mixer.window.get_allocated_height.return_value = 1080
        box = mixer.boxes["settings_button"]
        event = SimpleNamespace(x=box[0] + box[2] / 2, y=box[1] + box[3] / 2,
                                x_root=0, state=0, type=0)
        with patch.object(mixer, "_open_settings") as opened:
            self.assertTrue(mixer._on_press(None, event))
        opened.assert_called_once_with()

    def test_the_settings_button_paints_with_a_plain_label(self):
        # No emoji in the chrome: the suite's fonts do not all carry one, and a
        # missing glyph turns the button into a box.
        import inspect
        import MP_Liquid_Mixer as app
        source = inspect.getsource(app.LiquidMixer._paint_top_bar)
        self.assertIn('"SETTINGS"', source)
        self.assertNotIn("⚙", source)

    def test_the_format_button_toggles_wav_and_flac(self):
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        mixer.area = MagicMock()
        self.assertEqual(mixer.recorder.format, "WAV")
        mixer._toggle_recording_format()
        self.assertEqual(mixer.recorder.format, "FLAC")
        mixer._toggle_recording_format()
        self.assertEqual(mixer.recorder.format, "WAV")

    def test_clicking_the_format_button_toggles_the_container(self):
        from types import SimpleNamespace
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        mixer.area = MagicMock()
        mixer.window = MagicMock()
        mixer.window.get_allocated_width.return_value = 1920
        mixer.window.get_allocated_height.return_value = 1080
        box = mixer.boxes["format_button"]
        event = SimpleNamespace(x=box[0] + box[2] / 2, y=box[1] + box[3] / 2,
                                x_root=0, state=0, type=0)
        self.assertTrue(mixer._on_press(None, event))
        self.assertEqual(mixer.recorder.format, "FLAC")
        # The 24-Bit badge next to it is inert.
        bits = mixer.boxes["bitdepth_button"]
        event = SimpleNamespace(x=bits[0] + bits[2] / 2, y=bits[1] + bits[3] / 2,
                                x_root=0, state=0, type=0)
        self.assertTrue(mixer._on_press(None, event))
        self.assertEqual(mixer.recorder.format, "FLAC")

    def test_clicking_export_mixes_calls_the_export(self):
        from types import SimpleNamespace
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        mixer.area = MagicMock()
        mixer.window = MagicMock()
        mixer.window.get_allocated_width.return_value = 1920
        mixer.window.get_allocated_height.return_value = 1080
        box = mixer.boxes["export_button"]
        event = SimpleNamespace(x=box[0] + box[2] / 2, y=box[1] + box[3] / 2,
                                x_root=0, state=0, type=0)
        with patch.object(mixer, "_export_mixes") as export:
            self.assertTrue(mixer._on_press(None, event))
        export.assert_called_once()

    def test_export_is_refused_while_recording(self):
        from unittest.mock import PropertyMock
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        mixer.area = MagicMock()
        with patch.object(type(mixer.recorder), "is_recording",
                          new_callable=PropertyMock, return_value=True), \
             patch.object(app.mixer_export, "notify", return_value=True) as notify, \
             patch.object(app.mixer_export, "export_recordings") as export:
            mixer._export_mixes()
        export.assert_not_called()
        self.assertIn("Stop the current recording", notify.call_args.args[1])

    def test_export_uses_the_primary_archive_helper(self):
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        mixer.area = MagicMock()
        result = {"action": "moved", "archive": Path("/tmp/MIX_ARCHIVE"),
                  "wav": 1, "flac": 0, "failed": []}
        with patch.object(app.mixer_export, "export_recordings", return_value=result) as export, \
             patch.object(app.mixer_export, "notify_export", return_value=True) as notify:
            mixer._export_mixes()
        export.assert_called_once_with(mixer.recorder.base)
        notify.assert_called_once_with(result)

    def test_export_moves_wav_and_flac_through_the_handler(self):
        import MP_Liquid_Mixer as app
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "MIX_ARCHIVE"
            archive.mkdir()
            base = Path(tmp) / "app"
            records = base / "MP_AUDIOWAV_REC_OUTPUT"
            records.mkdir(parents=True)
            config = Path(tmp) / "cfg"
            config.mkdir()
            (records / "mix.wav").write_bytes(b"wav")
            (records / "mix.flac").write_bytes(b"flac")
            mixer = app.LiquidMixer()
            mixer.area = MagicMock()
            mixer.recorder.base = base
            with patch.dict(os.environ, {"MIX_ARCHIVE_DIR": str(archive)}, clear=False), \
                 patch.object(app.mixer_export, "notify", return_value=True):
                mixer._export_mixes()
            self.assertTrue((archive / "mix.wav").is_file())
            self.assertTrue((archive / "FLAC_CONVERTED_OUTPUTS" / "mix.flac").is_file())


class TestLaunchKeys(unittest.TestCase):
    KEYS = ("c", "v", "r", "x", "m", "z", "w", "l", "g", "F11", "Escape")

    def _mixer(self):
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        from types import SimpleNamespace
        mixer.Gdk = SimpleNamespace(**{f"KEY_{key}": index for index, key in enumerate(self.KEYS, start=100)})
        return mixer, SimpleNamespace

    def _press(self, mixer, key, method):
        from types import SimpleNamespace
        with patch.object(mixer, method) as called:
            event = SimpleNamespace(keyval=getattr(mixer.Gdk, f"KEY_{key}"), state=0)
            self.assertTrue(mixer._on_key(None, event))
        called.assert_called_once()

    def test_m_opens_the_manager(self):
        mixer, _ = self._mixer()
        self._press(mixer, "m", "_open_manager")

    def test_z_opens_the_visualizer(self):
        mixer, _ = self._mixer()
        self._press(mixer, "z", "_open_visualizer")

    def test_w_opens_the_black_glass_window(self):
        mixer, _ = self._mixer()
        self._press(mixer, "w", "_open_black_glass")

    def test_l_opens_the_liquid_player(self):
        mixer, _ = self._mixer()
        self._press(mixer, "l", "_open_liquid_player")

    def test_g_opens_mp_gui(self):
        mixer, _ = self._mixer()
        self._press(mixer, "g", "_open_mp_gui")

    def test_w_opens_a_black_glass_pane_at_the_mixer_size(self):
        mixer, _ = self._mixer()
        mixer.window = MagicMock()
        with patch("black_glass_window.spawn_black_glass_for_window",
                   return_value="opened") as spawn:
            mixer._open_black_glass()
        spawn.assert_called_once_with(mixer.window, "MP Liquid Mixer")

    def test_launch_commands_point_at_the_real_launchers(self):
        from types import SimpleNamespace
        mixer, _ = self._mixer()
        mixer.Gdk = SimpleNamespace(**{f"KEY_{key}": index for index, key in enumerate(self.KEYS, start=100)})
        with patch.object(mixer, "_launch_detached", return_value=True) as launch:
            mixer._open_manager()
        argv = launch.call_args.args[0]
        self.assertTrue(any(str(part).endswith("bin/launch-manager") for part in argv))
        with patch.object(mixer, "_launch_detached", return_value=True) as launch:
            mixer._open_visualizer()
        argv = launch.call_args.args[0]
        self.assertTrue(any(str(part).endswith("launch_liquid_visualizer.sh") for part in argv))
        with patch.object(mixer, "_launch_detached", return_value=True) as launch:
            mixer._open_mp_gui()
        argv = launch.call_args.args[0]
        self.assertIn("--keep-theme", argv)
        self.assertTrue(any(str(part).endswith("MP_Gui.sh") for part in argv))


if __name__ == "__main__":
    unittest.main()
