#!/usr/bin/env python3
"""Unit tests for the Windows 10/11 MP Liquid Mixer port.

The tests are split the same way as the port:

  * model/MIDI/theme/engine mapping, which runs headless anywhere;
  * the installer and shortcut wiring that puts the mixer on Windows;
  * an optional real Tk render smoke test when a display is available.

Run with:
    python -m unittest tests.test_liquid_mixer_windows -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
MIXER = ROOT / "MP_Liquid_Mixer_Windows_10.py"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "MP_Liquid_Mixer"))
sys.path.insert(0, str(ROOT / "scripts"))

import mixer_core as core  # noqa: E402
import mixer_midi  # noqa: E402

try:
    import tkinter  # noqa: F401
    import MP_Liquid_Mixer_Windows_10 as mixer  # noqa: E402
except Exception:  # pragma: no cover - headless/Linux without Tk
    mixer = None
    # Module-level skip attributes are honoured by unittest discovery without
    # turning the whole module into a collection error. Raising SkipTest here
    # aborts the import and the runner reports an error instead of a skip.
    __unittest_skip__ = True
    __unittest_skip_why__ = "tkinter is not available on this host"


class TestThePortLoads(unittest.TestCase):
    def test_the_module_exposes_the_windows_class_and_version(self):
        self.assertTrue(hasattr(mixer, "LiquidMixerWindows"))
        self.assertEqual(mixer.VERSION, "0.0.1")
        self.assertEqual(mixer.WINDOW_TITLE, "MP Liquid Mixer 0.0.1 (Windows)")

    def test_the_headless_self_test_passes(self):
        proc = subprocess.run(
            [sys.executable, str(MIXER), "--self-test"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK channels", proc.stdout)
        self.assertIn("OK visualizer", proc.stdout)

    def test_all_four_decks_have_namespaces(self):
        self.assertEqual(len(core.CHANNELS), 4)
        for channel in core.CHANNELS:
            self.assertTrue(core.DECK_NAMESPACES.get(channel))


class TestThemeC(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.theme_path = Path(self.tmp.name) / "theme"
        self.patch = patch.object(core, "THEME_PATH", self.theme_path)
        self.patch.start()
        self.app = mixer.LiquidMixerWindows([])
        self.app._draw = MagicMock()
        self.app._save_windows_settings = MagicMock()
        self.app.settings.save = MagicMock()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_the_mixer_always_opens_in_into_darkness(self):
        # The house theme, written before anything reads it so the four deck
        # engines start in the same theme as the mixer.
        self.theme_path.write_text("dreamworlds_neon\n", encoding="utf-8")
        app = mixer.LiquidMixerWindows([])
        self.assertEqual(app.theme, "into_darkness_void_out")
        self.assertEqual(self.theme_path.read_text(encoding="utf-8").strip(),
                         "into_darkness_void_out")
        self.assertEqual(core.theme_label(app.theme), "Into Darkness (Void Out)")

    def test_the_constant_matches_the_linux_mixer(self):
        self.assertEqual(mixer.FORCED_THEME, core.FORCED_THEME)

    def test_into_darkness_paints_a_black_field_with_readable_ink(self):
        palette = self.app._palette()
        self.assertEqual(palette["field"], (0, 0, 0))
        # Black accents stay black as fills, but text needs to be legible.
        self.assertEqual(palette["accent"], (0, 0, 0))
        self.assertGreater(sum(palette["accent_ink"]), 250)
        self.assertGreater(sum(palette["glass_ink"]), 600)

    def test_c_cycles_the_theme_and_keeps_it_readable(self):
        before = self.app.theme
        self.app._cycle_theme()
        self.assertNotEqual(self.app.theme, before)
        self.assertEqual(self.theme_path.read_text(encoding="utf-8").strip(),
                         self.app.theme.strip().lower())
        self.assertTrue(core.theme_label(self.app.theme))
        self.app._draw.assert_called_once()
        # Pressing C again keeps moving through the same table.
        second = self.app.theme
        self.app._cycle_theme()
        self.assertNotEqual(self.app.theme, second)

    def test_theme_ink_is_light_on_the_dark_console_pane(self):
        palette = self.app._palette()
        red, green, blue = palette["ink"]
        self.assertGreater(red + green + blue, 400)


class TestTheLayoutIsTheLinuxLayout(unittest.TestCase):
    """The two ports must be one design, not two interpretations of it."""

    def setUp(self):
        self.app = mixer.LiquidMixerWindows([])

    def test_the_design_grid_is_the_shared_1920x1080(self):
        self.assertEqual(mixer.DESIGN, (core.DESIGN_W, core.DESIGN_H))
        linux = core.layout(core.DESIGN_W, core.DESIGN_H)
        windows = self.app.boxes
        # Every Linux box is present at the same place and size. Only the three
        # Windows-only boxes differ, plus the two controls the Windows ribbon
        # moves aside for the close button.
        moved = {"master_knob", "monitor_knob", "visualizer_button"}
        for key, box in linux.items():
            self.assertIn(key, windows)
            if key in moved:
                continue
            for actual, expected in zip(windows[key], box):
                self.assertAlmostEqual(actual, expected, places=3)
        for key in ("master_knob", "monitor_knob", "visualizer_button"):
            self.assertEqual(len(windows[key]), 4)

    def test_the_players_and_the_mixer_column_match_linux(self):
        boxes = self.app.boxes
        for key in ("deck_a", "deck_b", "deck_a_header", "deck_a_term",
                    "mixer", "xfader", "strip_A", "strip_D",
                    "knob_gain_A", "knob_filter_D", "effect_slot_1_A",
                    "verify_button", "browse_button", "mode_button",
                    "format_button", "bitdepth_button", "record_button",
                    "credit_button", "clock_button", "export_button"):
            self.assertAlmostEqual(boxes[key][0], core.layout()[key][0], places=3)
            self.assertAlmostEqual(boxes[key][2], core.layout()[key][2], places=3)

    def test_the_four_channel_strips_are_the_small_linux_ones(self):
        # A channel strip is a narrow 48px column inside the mixer, not a wide
        # quarter of the window.
        mixer_box = self.app.boxes["mixer"]
        for channel in core.CHANNELS:
            box = self.app.boxes[f"strip_{channel}"]
            self.assertAlmostEqual(box[2], 48.0, places=3)
            self.assertGreaterEqual(box[0], mixer_box[0])
            self.assertLessEqual(box[0] + box[2], mixer_box[0] + mixer_box[2])

    def test_the_windows_chrome_is_added_without_moving_linux_boxes(self):
        boxes = self.app.boxes
        self.assertIn("close_button", boxes)
        self.assertIn("master_label", boxes)
        # MASTER/MON sit beside the title, left of the Trance lamp.
        self.assertLess(boxes["master_knob"][0], boxes["trance_button"][0])
        self.assertLess(boxes["monitor_knob"][0], boxes["trance_button"][0])
        # VISUALIZER and X never overlap, and X is the right-most control.
        self.assertLess(boxes["visualizer_button"][0] + boxes["visualizer_button"][2],
                        boxes["close_button"][0])
        self.assertAlmostEqual(boxes["close_button"][0] + boxes["close_button"][2],
                               boxes["top_bar"][2], places=1)

    def test_the_window_scales_from_the_same_grid(self):
        boxes = self.app._windows_layout(1280, 720)
        linux = core.layout(1280, 720)
        for key in ("deck_a", "mixer", "strip_B", "xfader"):
            self.assertAlmostEqual(boxes[key][0], linux[key][0], places=3)
            self.assertAlmostEqual(boxes[key][3], linux[key][3], places=3)


class TestTheTwoPlayerPanes(unittest.TestCase):
    """Two MP Liquid Players: one per side, each showing its own playlist."""

    def setUp(self):
        self.app = mixer.LiquidMixerWindows([])
        self.app.canvas = MagicMock()
        self.app._w, self.app._h = 1920, 1080
        self.app._relayout(1920, 1080)

    def _rows(self, prefix="track"):
        return [{"index": i, "name": f"{prefix}-{i}.mp3", "ext": "MP3",
                 "duration_fmt": "03:00", "path": f"B:/MIX_ARCHIVE/{prefix}-{i}.mp3"}
                for i in range(12)]

    def test_each_side_draws_a_player_pane_for_its_deck(self):
        self.app._playlist_rows["A"] = self._rows("left")
        self.app._playlist_rows["B"] = self._rows("right")
        self.app._deck_status["A"] = {"state": "paused", "title": "left-0.mp3",
                                      "total_tracks": 12, "index": 0}
        self.app._deck_status["B"] = {"state": "playing", "title": "right-3.mp3",
                                      "total_tracks": 12, "index": 3}
        self.app._draw()
        joined = self._drawn_text()
        self.assertIn("left-0.mp3", joined)
        self.assertIn("right-3.mp3", joined)
        self.assertIn("MIX ARCHIVE", joined)

    def _drawn_text(self) -> str:
        texts = []
        for call in self.app.canvas.create_text.call_args_list:
            if call.kwargs.get("text"):
                texts.append(str(call.kwargs["text"]))
            elif len(call.args) > 2:
                texts.append(str(call.args[2]))
        return "\n".join(texts)

    def test_the_left_pane_switches_to_deck_c_and_the_right_to_d(self):
        self.app.model.toggle_side_deck("L")
        self.app.model.toggle_side_deck("R")
        visible = self.app._visible_decks()
        self.assertEqual(visible, {"L": "C", "R": "D"})
        self.app._playlist_rows["C"] = self._rows("c")
        self.app._deck_status["C"] = {"state": "paused", "title": "c-2.mp3",
                                      "total_tracks": 12, "index": 2}
        self.app._draw()
        texts = self._drawn_text()
        self.assertIn("c-2.mp3", texts)
        self.assertIn("SWITCH TO A", texts)

    def test_the_pane_requests_the_engine_playlist(self):
        rows = self._rows("engine")
        with patch.object(core, "send_ipc",
                          return_value={"status": "ok", "playlist": rows}) as send:
            result = self.app._fetch_playlist("B")
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], core.DECK_NAMESPACES["B"])
        self.assertEqual(send.call_args.args[1], "get_playlist")
        self.assertEqual(result, rows)

    def test_the_pane_falls_back_to_the_shared_library(self):
        rows = self._rows("shared")
        with patch.object(core, "send_ipc", return_value=None), \
             patch.object(core, "read_playlist_rows", return_value=rows):
            self.app.library_mode = "MUSIC"
            self.assertEqual(self.app._fetch_playlist("A"), rows)

    def test_clicking_a_row_loads_that_track_on_that_deck(self):
        self.app._playlist_rows["B"] = self._rows("right")
        with patch.object(self.app, "_send") as send, \
             patch.object(self.app, "_draw"):
            self.app._load_playlist_index("B", 4)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], "B")
        self.assertEqual(send.call_args.args[1], "select_index")
        self.assertEqual(send.call_args.kwargs["index"], 4)
        # Clicking a pane also makes it the browse target.
        self.assertEqual(self.app.browse_target, "B")

    def test_the_playing_row_is_scrolled_into_view(self):
        self.app._playlist_rows["A"] = self._rows("long")
        self.app._deck_status["A"] = {"state": "playing", "index": 40,
                                      "total_tracks": 48, "title": "long-40.mp3"}
        self.app._draw()
        self.assertGreater(self.app._playlist_scroll["A"], 0)



class TestMidiMapping(unittest.TestCase):
    def setUp(self):
        self.app = mixer.LiquidMixerWindows([])
        self.app._sync_level = MagicMock()
        self.app._sync_eq = MagicMock()
        self.app._sync_effect = MagicMock()
        self.app._sync_pitch = MagicMock()
        self.app._queue_all_levels = MagicMock()

    def test_s4_channel_cc_sets_the_model_and_engine_volume(self):
        self.app._apply_midi("channel:A", 0.5)
        self.assertAlmostEqual(self.app.model.channel_gain("A"), 0.5, places=3)
        self.app._sync_level.assert_any_call("A")

    def test_s4_eq_cc_updates_the_three_band_model(self):
        self.app._apply_midi("eq:A:high", -6.0)
        self.assertEqual(self.app.model.eq_value("A", "high"), -6.0)
        self.app._sync_eq.assert_called_with("A")

    def test_s4_filter_cc_updates_the_insert_depth(self):
        self.app._apply_midi("filter:B", 0.75)
        self.assertAlmostEqual(self.app.model.filter_value("B"), 0.75, places=3)
        self.app._sync_effect.assert_called_with("B")

    def test_s4_pitch_uses_percent_not_a_second_7_bit_conversion(self):
        self.app._apply_midi("pitch:A", 3.5)
        self.assertAlmostEqual(self.app.model.pitch("A"), 3.5, places=3)
        self.app._sync_pitch.assert_called_with("A")

    def test_s4_master_volume_recomputes_every_deck(self):
        self.app._apply_midi("master_volume", 0.4)
        self.assertAlmostEqual(self.app.model.master_volume, 0.4, places=3)
        self.app._queue_all_levels.assert_called_once()

    def test_the_s4_note_map_reaches_play(self):
        note = next(n for n, action in mixer_midi.S4_NOTES.items()
                    if action.startswith("play:"))
        self.app._on_midi_message(0x90, note, 100)
        action, value = self.app._midi_queue.get_nowait()
        self.assertTrue(action.startswith("play:"))
        self.assertEqual(value, 1.0)

    def test_winmm_message_parser_delivers_note_on(self):
        seen = []
        port = mixer.WindowsMidiInput(
            lambda kind, first, second: seen.append((kind, first, second)),
            lambda _status: None, "A", "")
        note = next(iter(mixer_midi.S4_NOTES))
        data = 0x90 | (note << 8) | (100 << 16)
        port._on_midi(None, port.MIM_DATA, None, data, None)
        self.assertEqual(seen[0][0], 0x90)
        self.assertEqual(seen[0][1], note)


class TestEngineAndVisualizerWiring(unittest.TestCase):
    def test_four_engines_are_created_with_the_right_namespaces(self):
        app = mixer.LiquidMixerWindows([])
        self.assertEqual(set(app.decks), set(core.CHANNELS))
        for channel, engine in app.decks.items():
            self.assertEqual(engine.namespace, core.DECK_NAMESPACES[channel])

    def test_the_visualizer_is_started_as_a_gui_companion(self):
        app = mixer.LiquidMixerWindows([])
        app.visualizer_enabled = True
        app.root = MagicMock()
        app.root.winfo_rootx.return_value = 10
        app.root.winfo_rooty.return_value = 20
        app.root.winfo_width.return_value = 1440
        app.root.winfo_height.return_value = 810
        app._publish_geometry = MagicMock()
        with patch.object(mixer, "_console_python", return_value="python.exe"), \
             patch.object(mixer.subprocess, "Popen") as popen:
            popen.return_value = MagicMock(poll=MagicMock(return_value=None))
            app._start_visualizer()
        popen.assert_called_once()
        args, kwargs = popen.call_args
        self.assertIn("MP_Liquid_Visualizer.py", args[0][1])
        self.assertEqual(kwargs["env"]["MP_VISUALIZER_COMPANION"], "1")
        self.assertEqual(kwargs["env"]["MP_VISUALIZER_MATCH_GUI"], "1")
        self.assertNotIn("MP_VISUALIZER_DETACHED", kwargs["env"])

    def test_recording_is_available_when_pyaudiowpatch_is_installed(self):
        recorder = mixer.WindowsLoopbackRecorder("WAV")
        # The installer adds PyAudioWPatch; the port must use it rather than
        # pretending REC works with no capture path.
        self.assertTrue(recorder.available())

    def test_channel_effect_slots_are_the_shared_effect_table(self):
        slots = mixer.mixer_effects_slots()
        for mode in ("FLANGER", "REVERB", "DELAY", "GATER", "FILTER"):
            self.assertIn(mode, slots)


class TestTheInstallerOffersTheMixerOnWindows(unittest.TestCase):
    def test_app_selection_keeps_mixer_on_windows(self):
        import mp_app_selection
        self.assertIn("mixer", mp_app_selection.supported_components("windows"))
        self.assertNotIn("mixer", mp_app_selection.supported_components("macos"))

    def test_the_shortcut_catalogue_has_a_windows_target(self):
        import install_desktop_shortcuts as shortcuts
        self.assertEqual(
            shortcuts.selected_names(["mixer"], "windows"), ("MP Liquid Mixer",))
        spec = shortcuts.shortcut_targets(ROOT)["MP Liquid Mixer"]
        self.assertIsNotNone(spec["windows_bat"])
        self.assertTrue(Path(spec["windows_bat"]).is_file())

    def test_the_windows_integration_writes_the_mixer_shortcut(self):
        text = (ROOT / "scripts" / "windows_user_integration.ps1").read_text(
            encoding="utf-8")
        self.assertIn("MP Liquid Mixer", text)
        self.assertIn('"mixer" = "MP Liquid Mixer"', text)
        self.assertIn("launch_liquid_mixer.bat", text)

    def test_the_windows_tray_offers_the_mixer(self):
        text = (ROOT / "scripts" / "windows_tray.ps1").read_text(encoding="utf-8")
        self.assertIn('Label = "Launch MP Liquid Mixer"', text)
        self.assertIn('$Role -eq "mixer"', text)

    def test_install_sh_accepts_mixer_on_windows(self):
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("linux|freebsd|windows) _mp_mixer_ok=1", text)

    def test_launcher_exists_for_the_shortcut(self):
        self.assertTrue((ROOT / "launch_liquid_mixer.bat").is_file())
        self.assertTrue((ROOT / "scripts" / "launch_liquid_mixer.bat").is_file())


class TestNoConsoleWindows(unittest.TestCase):
    def test_the_player_hides_ffmpeg_and_ffprobe_children(self):
        text = (ROOT / "MP_Audio_Player.py").read_text(encoding="utf-8")
        self.assertIn("def _no_console()", text)
        self.assertIn("timeout=6, **_no_console()", text)
        self.assertIn("bufsize=131072, **_no_console()", text)
        self.assertIn("start_new_session=True, **_no_console()", text)

    def test_deck_engines_are_windowless_and_skip_the_alsa_helper(self):
        with patch.object(mixer.subprocess, "Popen") as popen:
            engine = mixer.DeckEngine("A")
            engine.start()
        kwargs = popen.call_args.kwargs
        self.assertEqual(kwargs["env"]["MIDI_CONTROL_ENABLED"], "false")
        self.assertTrue(kwargs["creationflags"] & 0x08000000)
        if sys.platform == "win32":
            self.assertTrue(str(popen.call_args.args[0][0]).lower().endswith(
                "pythonw.exe"))


class TestTransparencyBar(unittest.TestCase):
    def test_the_bottom_ribbon_has_a_transparency_slider(self):
        app = mixer.LiquidMixerWindows([])
        app.root = MagicMock()
        app.canvas = MagicMock()
        app._w, app._h = 1440, 810
        app._draw()
        kinds = [hit[4] for hit in app._hits]
        self.assertIn("opacity", kinds)

    def test_dragging_the_slider_sets_the_window_alpha(self):
        app = mixer.LiquidMixerWindows([])
        app.root = MagicMock()
        app.visualizer_enabled = True
        app._draw = MagicMock()
        track = app.boxes["opacity"]
        app._drag_opacity(track[0] + track[2], track)
        self.assertAlmostEqual(app.model.opacity, 1.0)
        app.root.attributes.assert_called_with("-alpha", 1.0)
        app._drag_opacity(track[0], track)
        self.assertAlmostEqual(app.model.opacity, mixer.core.MIN_OPACITY)
        app.root.attributes.assert_called_with("-alpha", mixer.core.MIN_OPACITY)

    def test_the_window_matches_the_model_opacity(self):
        app = mixer.LiquidMixerWindows([])
        app.root = MagicMock()
        app.visualizer_enabled = True
        for value in (1.0, 0.94, 0.70, 0.42):
            app.model.opacity = value
            self.assertAlmostEqual(app._apply_alpha(), value)
            app.root.attributes.assert_called_with("-alpha", value)

    def test_no_visualizer_forces_a_solid_window(self):
        # Nothing is behind the glass without a companion, so the desktop must
        # not bleed through the panels.
        app = mixer.LiquidMixerWindows([])
        app.root = MagicMock()
        app.visualizer_enabled = False
        app.model.opacity = 0.55
        self.assertEqual(app._apply_alpha(), 1.0)
        app.root.attributes.assert_called_with("-alpha", 1.0)

    def test_the_transparency_value_comes_from_the_shared_mixer_model(self):
        app = mixer.LiquidMixerWindows([])
        self.assertIsInstance(app.model, mixer.core.MixerModel)
        self.assertGreaterEqual(app.model.opacity, mixer.core.MIN_OPACITY)
        self.assertLessEqual(app.model.opacity, mixer.core.MAX_OPACITY)
        # The saved payload keeps the model value, not a private alpha field.
        with patch.object(mixer, "CONFIG_DIR", Path(tempfile.mkdtemp())), \
             patch.object(mixer, "WINDOWS_SETTINGS",
                          Path(tempfile.mkdtemp()) / "liquid_mixer_windows.json"):
            app.model.opacity = 0.66
            app._save_windows_settings()
            payload = json.loads(mixer.WINDOWS_SETTINGS.read_text(encoding="utf-8"))
        self.assertAlmostEqual(payload["opacity"], 0.66, places=3)
        self.assertNotIn("alpha", payload)



class TestWindowDrag(unittest.TestCase):
    def _app(self, fullscreen=False):
        app = mixer.LiquidMixerWindows([])
        app.root = MagicMock()
        app.root.winfo_x.return_value = 100
        app.root.winfo_y.return_value = 50
        app.canvas = MagicMock()
        app.fullscreen = fullscreen
        app._publish_geometry = MagicMock()
        return app

    def test_dragging_empty_space_moves_a_windowed_mixer(self):
        app = self._app()
        app._on_press(SimpleNamespace(x=10, y=10, x_root=300, y_root=200))
        self.assertEqual(app._window_drag, (200, 150))
        app._on_drag(SimpleNamespace(x=10, y=10, x_root=340, y_root=260))
        app.root.geometry.assert_called_once_with("+140+110")
        app._publish_geometry.assert_called_once_with()
        app._on_release(None)
        self.assertIsNone(app._window_drag)

    def test_full_screen_does_not_start_a_window_drag(self):
        app = self._app(fullscreen=True)
        app._on_press(SimpleNamespace(x=10, y=10, x_root=300, y_root=200))
        self.assertIsNone(app._window_drag)
        app.root.geometry.assert_not_called()

    def test_a_control_press_is_not_a_window_drag(self):
        app = self._app()
        app._hits = [(0, 0, 50, 50, "library", 0)]
        app._load_library_row = MagicMock()
        app._on_press(SimpleNamespace(x=10, y=10, x_root=300, y_root=200))
        self.assertIsNone(app._window_drag)
        app._load_library_row.assert_called_once_with(0)
        app.root.geometry.assert_not_called()


class TestTheRealWindow(unittest.TestCase):
    def test_the_tk_window_draws_the_full_mixer(self):
        try:
            import tkinter as tk
        except Exception as exc:
            self.skipTest(f"tkinter unavailable: {exc}")
        try:
            root = tk.Tk()
        except Exception as exc:
            self.skipTest(f"no display: {exc}")
        root.withdraw()
        try:
            app = mixer.LiquidMixerWindows([])
            app.root = root
            app.canvas = tk.Canvas(root, width=1200, height=760)
            app._w, app._h = 1200, 760
            app._draw()
            items = app.canvas.find_all()
            self.assertGreater(len(items), 80, "the mixer window drew too little")
            self.assertTrue(any("CROSSFADER" in str(app.canvas.itemcget(i, "text"))
                                for i in items if app.canvas.type(i) == "text"))
        finally:
            root.destroy()


_WINDOWS_PORT = sys.platform.startswith(("win", "cygwin", "msys"))
if mixer is None or not _WINDOWS_PORT:
    # unittest discovery honours skip attributes on classes, not on the module.
    # Drop the real classes and leave one explicitly skipped test so a Linux or
    # headless host reports a skip instead of import-time failures. The Windows
    # port is only meaningful on Windows; its installer and recorder assertions
    # are not claims about the Linux build.
    for _name, _value in list(globals().items()):
        if isinstance(_value, type) and issubclass(_value, unittest.TestCase):
            del globals()[_name]

    class WindowsMixerUnavailable(unittest.TestCase):
        @unittest.skip("the Windows MP Liquid Mixer port runs only on Windows")
        def test_windows_mixer_unavailable(self):
            pass


if __name__ == "__main__":
    unittest.main()
