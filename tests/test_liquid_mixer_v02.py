#!/usr/bin/env python3
"""Tests for the 0.0.2 mixer features.

Covers the four live decks and their playlist-window switches, the per-channel
filter knob and its four effect inserts, the master/monitor trims and cue
buttons, the forced Into Darkness theme, the visualizer number keys and the
mouse wheel. The window itself needs X11, so the paint path is exercised with
a real cairo surface and the GTK widgets are faked.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIXER_DIR = PROJECT_ROOT / "MP_Liquid_Mixer"
sys.path.insert(0, str(MIXER_DIR))
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import mixer_core  # noqa: E402
import mixer_effects  # noqa: E402


def _numpy():
    try:
        import numpy
        return numpy
    except ImportError:  # pragma: no cover - numpy is a hard requirement
        return None


class TestFourDeckModel(unittest.TestCase):
    def test_four_namespaces_are_distinct(self):
        self.assertEqual(sorted(mixer_core.DECK_NAMESPACES), ["A", "B", "C", "D"])
        self.assertEqual(len(set(mixer_core.DECK_NAMESPACES.values())), 4)

    def test_each_side_owns_two_decks(self):
        self.assertEqual(mixer_core.DECK_SIDES["L"], ("A", "C"))
        self.assertEqual(mixer_core.DECK_SIDES["R"], ("B", "D"))
        self.assertEqual(mixer_core.DEFAULT_SIDE_DECKS, {"L": "A", "R": "B"})

    def test_the_other_deck_is_the_one_a_switch_reaches(self):
        model = mixer_core.MixerModel()
        self.assertEqual(model.other_deck_for_side("L"), "C")
        self.assertEqual(model.other_deck_for_side("R"), "D")

    def test_a_hidden_deck_keeps_its_own_gain_and_pitch(self):
        model = mixer_core.MixerModel(crossfade=0.0)
        model.toggle_side_deck("L")
        self.assertTrue(model.set_channel("A", 0.3))
        self.assertTrue(model.set_channel("C", 0.9))
        model.set_pitch("C", 4.0)
        self.assertEqual(model.channel_volume("A"), 30)
        self.assertAlmostEqual(model.channel_gain("C"), 0.9, places=6)
        self.assertEqual(model.pitch("C"), 4.0)

    def test_c_and_d_ignore_the_crossfader(self):
        model = mixer_core.MixerModel(gains={"A": 1.0, "B": 1.0, "C": 1.0, "D": 1.0},
                                      crossfade=0.0)
        self.assertEqual(model.channel_volume("A"), 100)
        self.assertEqual(model.channel_volume("B"), 0)
        self.assertEqual(model.channel_volume("C"), 100)
        self.assertEqual(model.channel_volume("D"), 100)


class TestEffects(unittest.TestCase):
    def test_every_channel_defaults_to_flanger(self):
        model = mixer_core.MixerModel()
        for channel in mixer_core.CHANNELS:
            self.assertEqual(model.effect_mode(channel), "FLANGER")
            self.assertFalse(model.effect_on(channel))
            self.assertEqual(model.filter_value(channel), 0.0)

    def test_the_four_slots_map_to_the_four_effects(self):
        self.assertEqual(mixer_core.EFFECT_MODES, ("FLANGER", "REVERB", "DELAY", "GATER"))
        self.assertEqual(len(mixer_core.EFFECT_SLOT_COLORS), 4)
        # Fixed colours: Flanger red, Reverb blue, Delay yellow, Gater green.
        self.assertEqual(mixer_core.EFFECT_SLOT_COLORS[0], (235, 64, 72))
        self.assertEqual(mixer_core.EFFECT_SLOT_COLORS[1], (72, 149, 239))
        self.assertEqual(mixer_core.EFFECT_SLOT_COLORS[2], (250, 205, 90))
        self.assertEqual(mixer_core.EFFECT_SLOT_COLORS[3], (60, 220, 120))
        # The Filter switch is the separate orange square.
        self.assertEqual(mixer_core.FILTER_SLOT_COLOR, (255, 150, 40))

    def test_slot_selection_and_enable_toggle(self):
        model = mixer_core.MixerModel()
        self.assertTrue(model.set_effect_mode("B", "gater"))
        self.assertEqual(model.effect_mode("B"), "GATER")
        self.assertFalse(model.set_effect_mode("B", "chorus"))
        self.assertTrue(model.toggle_effect("B"))
        self.assertTrue(model.effect_on("B"))
        self.assertFalse(model.toggle_effect("B"))

    def test_the_filter_knob_is_the_effect_depth(self):
        model = mixer_core.MixerModel()
        model.set_filter("C", 0.65)
        model.set_effect_mode("C", "DELAY")
        model.set_effect_enabled("C", True)
        self.assertEqual(model.effect_payload("C"),
                         {"mode": "delay", "amount": 0.65, "enabled": True})

    def test_effects_and_filters_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            model = mixer_core.MixerModel()
            model.set_effect_mode("D", "REVERB")
            model.set_effect_enabled("D", True)
            model.set_filter("D", 0.4)
            model.set_master_volume(0.55)
            model.set_monitor_volume(0.35)
            model.save(path)
            loaded = mixer_core.MixerModel.load(path)
        self.assertEqual(loaded.effect_mode("D"), "REVERB")
        self.assertTrue(loaded.effect_on("D"))
        self.assertAlmostEqual(loaded.filter_value("D"), 0.4, places=6)
        self.assertAlmostEqual(loaded.master_volume, 0.55, places=6)
        self.assertAlmostEqual(loaded.monitor_volume, 0.35, places=6)

    def test_bypassing_leaves_the_audio_untouched(self):
        np = _numpy()
        if np is None:  # pragma: no cover
            self.skipTest("numpy is not installed")
        effect = mixer_effects.ChannelEffect(mode="FLANGER", amount=0.8, enabled=False)
        block = np.full((64, 2), 0.25, dtype=np.float32)
        self.assertFalse(effect.active)
        self.assertIs(effect.process(block), block)

    def test_each_effect_changes_the_signal_when_enabled(self):
        np = _numpy()
        if np is None:  # pragma: no cover
            self.skipTest("numpy is not installed")
        signal = np.sin(np.linspace(0.0, 40.0, 2048)).astype(np.float32)
        stereo = np.column_stack((signal, signal))
        for mode in mixer_effects.MODES:
            effect = mixer_effects.ChannelEffect(mode=mode, amount=0.9, enabled=True,
                                                 sample_rate=44100)
            out = effect.process(stereo.copy())
            self.assertEqual(out.shape, stereo.shape, mode)
            self.assertTrue(np.isfinite(out).all(), mode)
            self.assertFalse(np.allclose(out, stereo), mode)

    def test_mode_names_are_normalized(self):
        self.assertEqual(mixer_effects.normalize_mode("delay"), "DELAY")
        self.assertEqual(mixer_effects.normalize_mode("nonsense"), "FLANGER")
        self.assertEqual(mixer_effects.normalize_mode(None), "FLANGER")


class TestMasterAndMonitor(unittest.TestCase):
    def test_master_volume_scales_every_channel(self):
        model = mixer_core.MixerModel(gains={ch: 1.0 for ch in mixer_core.CHANNELS})
        self.assertEqual(model.channel_volume("C"), 100)
        model.set_master_volume(0.5)
        self.assertEqual(model.channel_volume("C"), 50)

    def test_monitor_volume_scales_only_a_cued_channel(self):
        model = mixer_core.MixerModel(gains={ch: 1.0 for ch in mixer_core.CHANNELS},
                                      crossfade=0.5)
        model.set_monitor_volume(0.5)
        plain = model.channel_volume("B")
        model.toggle_headphone("B")
        # Rounding happens once, on the final level, so the cued half is
        # within one percent of half the uncued level.
        self.assertLessEqual(abs(model.channel_volume("B") - plain / 2.0), 1.0)
        self.assertEqual(model.channel_volume("A"), plain)

    def test_cue_state_is_per_channel(self):
        model = mixer_core.MixerModel()
        self.assertTrue(model.toggle_headphone("D"))
        self.assertEqual(model.headphone_channels(), ("D",))
        self.assertFalse(model.toggle_headphone("D"))
        self.assertEqual(model.headphone_channels(), ())


class TestLayoutAdditions(unittest.TestCase):
    def test_filter_knob_is_below_low_and_bigger(self):
        boxes = mixer_core.layout(1920, 1080)
        low = boxes["knob_low_A"]
        filt = boxes["knob_filter_A"]
        self.assertGreater(filt[1], low[1] + low[3])
        self.assertAlmostEqual(filt[2], low[2] * 1.5, places=3)

    def test_no_eq_row_legend_pushes_the_columns_right(self):
        boxes = mixer_core.layout(1920, 1080)
        for name in boxes:
            self.assertFalse(name.endswith("_label") and name.startswith("knob_"), name)

    def test_effect_squares_and_filter_switch_sit_under_the_knob(self):
        boxes = mixer_core.layout(1920, 1080)
        for channel in mixer_core.CHANNELS:
            filt = boxes[f"knob_filter_{channel}"]
            slots = [boxes[f"effect_slot_{slot}_{channel}"] for slot in (1, 2, 3, 4)]
            switch = boxes[f"effect_filter_{channel}"]
            for slot in slots:
                self.assertGreaterEqual(slot[1], filt[1] + filt[3])
            # The orange square is on its own row beneath the four.
            self.assertGreater(switch[1], slots[0][1] + slots[0][3])
            self.assertEqual(len({round(slot[0], 2) for slot in slots}), 4)

    def test_master_and_monitor_knobs_are_top_left(self):
        boxes = mixer_core.layout(1920, 1080)
        self.assertLess(boxes["master_knob"][0], boxes["trance_button"][0])
        self.assertLess(boxes["monitor_knob"][0], boxes["trance_button"][0])
        self.assertEqual(boxes["master_knob"][1], boxes["monitor_knob"][1])

    def test_deck_switch_buttons_live_in_the_headers(self):
        boxes = mixer_core.layout(1920, 1080)
        for side in ("a", "b"):
            header = boxes[f"deck_{side}_header"]
            button = boxes[f"deck_switch_{side}"]
            self.assertGreaterEqual(button[0], header[0])
            self.assertLessEqual(button[0] + button[2], header[0] + header[2])
            self.assertLessEqual(button[1] + button[3], header[1] + header[3])

    def test_cue_buttons_sit_above_their_faders(self):
        boxes = mixer_core.layout(1920, 1080)
        for channel in mixer_core.CHANNELS:
            cue = boxes[f"monitor_{channel}"]
            fader = boxes[f"strip_{channel}"]
            self.assertLess(cue[1] + cue[3], fader[1])
            self.assertAlmostEqual(cue[0] + cue[2] / 2, fader[0] + fader[2] / 2, places=3)


class TestForcedTheme(unittest.TestCase):
    def test_the_mixer_always_opens_in_into_darkness(self):
        import MP_Liquid_Mixer as app
        with tempfile.TemporaryDirectory() as tmp:
            theme_path = Path(tmp) / "theme"
            with patch.object(mixer_core, "THEME_PATH", theme_path), \
                 patch.object(app.core, "THEME_PATH", theme_path):
                theme_path.write_text("sensorium\n", encoding="utf-8")
                mixer = app.LiquidMixer()
                written = theme_path.read_text(encoding="utf-8").strip()
        self.assertEqual(mixer.theme, mixer_core.FORCED_THEME)
        self.assertEqual(written, mixer_core.FORCED_THEME)


class _FakeArea:
    def grab_focus(self):
        pass

    def queue_draw(self):
        pass


class TestWindowActions(unittest.TestCase):
    def _mixer(self):
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        # Each test starts from the documented defaults: the on-disk state is
        # shared by every mixer in the process (and by the running app), so a
        # switch in one test must not decide the next one.
        mixer.model = mixer_core.MixerModel()
        mixer.model.save = lambda *a, **k: None
        mixer.area = _FakeArea()
        mixer.window = MagicMock()
        mixer.window.get_allocated_width.return_value = 1920
        mixer.window.get_allocated_height.return_value = 1080
        mixer.Gdk = SimpleNamespace(
            EventType=SimpleNamespace(_2BUTTON_PRESS=5, BUTTON_PRESS=4),
            ModifierType=SimpleNamespace(SHIFT_MASK=1, BUTTON1_MASK=256),
            ScrollDirection=SimpleNamespace(UP=0, DOWN=1, LEFT=2, RIGHT=3),
            KEY_c=99, KEY_w=119, KEY_v=118, KEY_r=114, KEY_x=120, KEY_m=109,
            KEY_z=122, KEY_l=108, KEY_g=103, KEY_s=115, KEY_q=113,
            KEY_Escape=65307,
            KEY_F11=65480, KEY_Return=65293, KEY_KP_Enter=65421,
            KEY_y=121, KEY_Y=89, KEY_n=110, KEY_N=78,
            **{f"KEY_{d}": 48 + d for d in range(10)},
        )
        return mixer

    def test_switch_click_flips_the_side_and_flashes(self):
        mixer = self._mixer()
        box = mixer.boxes["deck_switch_a"]
        event = SimpleNamespace(x=box[0] + 5, y=box[1] + 5, x_root=0, state=0, type=0)
        with patch.object(mixer, "_highlight_deck_track") as highlight, \
             patch.object(mixer, "_place_decks"):
            self.assertTrue(mixer._on_press(None, event))
        self.assertEqual(mixer.model.deck_for_side("L"), "C")
        self.assertEqual(mixer.browse_target, "C")
        self.assertIn("L", mixer._switch_flash)
        self.assertTrue(mixer._pulse_active())
        highlight.assert_called_once_with("C")

    def test_switch_click_toggles_back(self):
        mixer = self._mixer()
        with patch.object(mixer, "_highlight_deck_track"), patch.object(mixer, "_place_decks"):
            mixer._toggle_side("R")
            self.assertEqual(mixer.model.deck_for_side("R"), "D")
            mixer._toggle_side("R")
        self.assertEqual(mixer.model.deck_for_side("R"), "B")

    def test_effect_slot_click_selects_that_effect(self):
        mixer = self._mixer()
        box = mixer.boxes["effect_slot_3_B"]
        event = SimpleNamespace(x=box[0] + 2, y=box[1] + 2, x_root=0, state=0, type=0)
        self.assertTrue(mixer._on_press(None, event))
        self.assertEqual(mixer.model.effect_mode("B"), "DELAY")

    def test_the_orange_square_selects_the_filter(self):
        mixer = self._mixer()
        box = mixer.boxes["effect_filter_B"]
        event = SimpleNamespace(x=box[0] + 2, y=box[1] + 2, x_root=0, state=0, type=0)
        self.assertTrue(mixer._on_press(None, event))
        self.assertEqual(mixer.model.effect_mode("B"), "FILTER")
        self.assertTrue(mixer.model.filter_mode("B"))

    def test_fx_button_click_enables_the_channel(self):
        mixer = self._mixer()
        box = mixer.boxes["fx_C"]
        event = SimpleNamespace(x=box[0] + 2, y=box[1] + 2, x_root=0, state=0, type=0)
        self.assertTrue(mixer._on_press(None, event))
        self.assertTrue(mixer.model.effect_on("C"))

    def test_monitor_button_click_cues_the_channel(self):
        mixer = self._mixer()
        box = mixer.boxes["monitor_D"]
        event = SimpleNamespace(x=box[0] + 2, y=box[1] + 2, x_root=0, state=0, type=0)
        with patch.object(mixer, "_apply_monitor_routing") as route:
            self.assertTrue(mixer._on_press(None, event))
        self.assertTrue(mixer.model.headphone("D"))
        route.assert_called_once()

    def test_wheel_scrolls_a_channel_fader(self):
        mixer = self._mixer()
        mixer.model.save = lambda *a, **k: None
        mixer.model.set_channel("C", 0.5)
        box = mixer.boxes["strip_C"]
        event = SimpleNamespace(x=box[0] + 5, y=box[1] + 5, direction=0)
        self.assertTrue(mixer._on_scroll(None, event))
        self.assertGreater(mixer.model.channel_gain("C"), 0.5)
        event.direction = 1
        self.assertTrue(mixer._on_scroll(None, event))
        self.assertLess(mixer.model.channel_gain("C"), 0.51)

    def test_wheel_scrolls_a_tempo_slider(self):
        mixer = self._mixer()
        mixer.model.set_pitch("A", 0.0)
        mixer.model.save = lambda *a, **k: None
        box = mixer.boxes["pitch_a"]
        event = SimpleNamespace(x=box[0] + 5, y=box[1] + 50, direction=0)
        self.assertTrue(mixer._on_scroll(None, event))
        self.assertAlmostEqual(mixer.model.pitch("A"), mixer.WHEEL_PITCH_STEP, places=6)

    def test_wheel_scrolls_eq_and_filter_knobs(self):
        mixer = self._mixer()
        event = SimpleNamespace(x=0, y=0, direction=0)
        mixer.model.set_eq("A", "high", 0.0)
        mixer.model.save = lambda *a, **k: None
        box = mixer.boxes["knob_high_A"]
        event.x, event.y = box[0] + 5, box[1] + 5
        self.assertTrue(mixer._on_scroll(None, event))
        self.assertGreater(mixer.model.eq_value("A", "high"), 0.0)
        box = mixer.boxes["knob_filter_A"]
        event.x, event.y = box[0] + 5, box[1] + 5
        self.assertTrue(mixer._on_scroll(None, event))
        self.assertGreater(mixer.model.filter_value("A"), 0.0)

    def test_wheel_scrolls_master_and_monitor(self):
        mixer = self._mixer()
        box = mixer.boxes["master_knob"]
        event = SimpleNamespace(x=box[0] + 5, y=box[1] + 5, direction=1)
        self.assertTrue(mixer._on_scroll(None, event))
        self.assertLess(mixer.model.master_volume, 1.0)
        box = mixer.boxes["monitor_knob"]
        event.x, event.y, event.direction = box[0] + 5, box[1] + 5, 0
        self.assertTrue(mixer._on_scroll(None, event))
        self.assertGreater(mixer.model.monitor_volume, 0.85)

    def test_escape_quits_from_a_deck_too(self):
        mixer = self._mixer()
        with patch.object(mixer, "quit") as quit_mixer:
            event = SimpleNamespace(keyval=65307)
            self.assertTrue(mixer._on_key(mixer.decks.get("A", {}).get("vte") or object(), event))
        quit_mixer.assert_called_once()

    def test_number_keys_change_the_visualizer_preset(self):
        mixer = self._mixer()
        with patch.object(mixer, "_select_visualizer_slot") as select:
            mixer._on_key(object(), SimpleNamespace(keyval=51))
        select.assert_called_once_with(3)

    def test_deck_numbers_are_not_stolen_by_the_chrome(self):
        mixer = self._mixer()
        term = MagicMock()
        mixer.decks["A"] = {"vte": term, "namespace": "x"}
        with patch.object(mixer, "_select_visualizer_slot") as select:
            mixer._on_key(term, SimpleNamespace(keyval=51))
        select.assert_not_called()

    def test_profile_button_switches_to_the_other_map(self):
        mixer = self._mixer()
        mixer.profile = "A"
        mixer.model.save = lambda *a, **k: None
        listener = MagicMock()
        mixer.midi_listener = listener
        box = mixer.boxes["profile_button"]
        event = SimpleNamespace(x=box[0] + 4, y=box[1] + 4, x_root=0, state=0, type=0)
        with patch.object(mixer.settings, "save"):
            self.assertTrue(mixer._on_press(None, event))
        self.assertEqual(mixer.profile, "B")
        listener.set_profile.assert_called_once_with("B")


class TestFilterModeAndMeters(unittest.TestCase):
    def test_the_filter_square_is_a_fifth_mode(self):
        model = mixer_core.MixerModel()
        self.assertFalse(model.filter_mode("A"))
        self.assertTrue(model.set_filter_mode("A"))
        self.assertEqual(model.effect_mode("A"), "FILTER")
        self.assertTrue(model.filter_mode("A"))
        # Selecting an effect takes the knob back off the filter.
        self.assertTrue(model.set_effect_mode("A", "GATER"))
        self.assertFalse(model.filter_mode("A"))

    def test_filter_mode_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            model = mixer_core.MixerModel()
            model.set_filter_mode("C")
            model.save(path)
            loaded = mixer_core.MixerModel.load(path)
        self.assertTrue(loaded.filter_mode("C"))

    def test_the_zone_colours_run_yellow_green_yellow_red(self):
        mixer = TestWindowActions()._mixer()
        for fraction, expected in ((0.05, (235, 215, 70)),
                                   (0.5, (60, 220, 120)),
                                   (0.8, (250, 205, 90)),
                                   (0.97, (235, 64, 72))):
            self.assertEqual(mixer._meter_zone_color(fraction), expected)

    def test_a_peak_maps_onto_the_ladder(self):
        model = mixer_core.MixerModel()
        self.assertEqual(model.meter_fraction("A", -60.0), 0.0)
        self.assertEqual(model.meter_fraction("A", -120.0), 0.0)
        self.assertEqual(model.meter_fraction("A", 0.0), 1.0)
        self.assertAlmostEqual(model.meter_fraction("A", -30.0), 0.5, places=6)

    def test_the_channel_meter_reads_its_own_deck_peak(self):
        mixer = TestWindowActions()._mixer()
        mixer._deck_status = {"A": {"peak_db": -6.0}, "B": {"peak_db": -40.0},
                              "C": {"peak_db": -18.0}, "D": {"peak_db": -60.0}}
        self.assertAlmostEqual(mixer._deck_peak_db("A"), -6.0, places=6)
        self.assertAlmostEqual(mixer._deck_peak_db("B"), -40.0, places=6)
        self.assertAlmostEqual(mixer._deck_peak_db("C"), -18.0, places=6)
        self.assertAlmostEqual(mixer._deck_peak_db("D"), -60.0, places=6)

    def test_missing_peak_data_is_silence_not_an_error(self):
        mixer = TestWindowActions()._mixer()
        mixer._deck_status = {}
        self.assertEqual(mixer._deck_peak_db("A"), mixer_core.METER_MIN_DB)

    def test_the_master_slider_changes_the_master_volume(self):
        mixer = TestWindowActions()._mixer()
        box = mixer.boxes["master_slider"]
        mixer.model.set_master_volume(1.0)
        mixer._drag_master(box[0] + box[2] * 0.25)
        self.assertAlmostEqual(mixer.model.master_volume, 0.25, places=3)
        self.assertGreater(mixer._vol_dirty, 0)

    def test_master_volume_reaches_every_deck(self):
        mixer = TestWindowActions()._mixer()
        mixer.model.gains = {ch: 1.0 for ch in mixer_core.CHANNELS}
        mixer.model.set_master_volume(0.5)
        for channel in mixer_core.CHANNELS:
            self.assertLessEqual(mixer.model.volume_for(channel), 50)


class TestQuitKeys(unittest.TestCase):
    def test_escape_quits_from_the_chrome(self):
        mixer = TestWindowActions()._mixer()
        with patch.object(mixer, "quit") as quit_mixer:
            self.assertTrue(mixer._on_key(object(), SimpleNamespace(keyval=65307)))
        quit_mixer.assert_called_once()

    def test_escape_quits_from_a_deck_too(self):
        mixer = TestWindowActions()._mixer()
        term = MagicMock()
        mixer.decks["A"] = {"vte": term, "namespace": "x"}
        with patch.object(mixer, "quit") as quit_mixer:
            self.assertTrue(mixer._on_key(term, SimpleNamespace(keyval=65307)))
        quit_mixer.assert_called_once()

    def test_q_quits_from_the_chrome(self):
        mixer = TestWindowActions()._mixer()
        with patch.object(mixer, "quit") as quit_mixer:
            self.assertTrue(mixer._on_key(object(), SimpleNamespace(keyval=ord("q"))))
        quit_mixer.assert_called_once()

    def test_q_is_left_to_the_player_inside_a_deck(self):
        mixer = TestWindowActions()._mixer()
        term = MagicMock()
        mixer.decks["A"] = {"vte": term, "namespace": "x"}
        with patch.object(mixer, "quit") as quit_mixer:
            self.assertFalse(mixer._on_key(term, SimpleNamespace(keyval=ord("q"))))
        quit_mixer.assert_not_called()

    def test_shutdown_pauses_and_stops_every_deck(self):
        mixer = TestWindowActions()._mixer()
        mixer.decks = {ch: {"namespace": f"deck_{ch}"} for ch in mixer_core.CHANNELS}
        mixer.recorder = MagicMock()
        mixer.recorder.is_recording = False
        with patch.object(mixer_core, "send_ipc") as send, \
             patch.object(mixer, "_stop_decks_hard"), \
             patch.object(mixer, "_stop_verify_movie"):
            mixer._shutdown()
        sent = {(call.args[0], call.args[1]) for call in send.call_args_list}
        for ch in mixer_core.CHANNELS:
            self.assertIn((f"deck_{ch}", "pause"), sent)
            self.assertIn((f"deck_{ch}", "stop"), sent)


class TestPaintWithNewControls(unittest.TestCase):
    def test_the_whole_surface_paints_including_switched_decks(self):
        import gi
        gi.require_version("Pango", "1.0")
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import Pango, PangoCairo
        import cairo
        import MP_Liquid_Mixer as app

        mixer = app.LiquidMixer()
        mixer.Pango, mixer.PangoCairo = Pango, PangoCairo
        mixer.area = _FakeArea()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1920, 1080)
        cr = cairo.Context(surface)
        mixer._paint(cr, 1920, 1080)
        mixer.model.toggle_side_deck("L")
        mixer.model.toggle_side_deck("R")
        mixer.model.toggle_effect("A")
        mixer.model.set_effect_mode("B", "GATER")
        mixer.model.toggle_headphone("C")
        mixer._paint(cr, 1920, 1080)
        mixer._paint(cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 3840, 2160)),
                     3840, 2160)


if __name__ == "__main__":
    unittest.main()
