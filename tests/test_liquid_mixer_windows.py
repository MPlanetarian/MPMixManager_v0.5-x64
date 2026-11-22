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
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
MIXER = ROOT / "MP_Liquid_Mixer_Windows_10.py"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "MP_Liquid_Mixer"))
sys.path.insert(0, str(ROOT / "scripts"))

import mixer_core as core  # noqa: E402
import mixer_midi  # noqa: E402
import MP_Liquid_Mixer_Windows_10 as mixer  # noqa: E402


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

    def test_install_sh_accepts_mixer_on_windows(self):
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("linux|freebsd|windows) _mp_mixer_ok=1", text)

    def test_launcher_exists_for_the_shortcut(self):
        self.assertTrue((ROOT / "launch_liquid_mixer.bat").is_file())
        self.assertTrue((ROOT / "scripts" / "launch_liquid_mixer.bat").is_file())


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


if __name__ == "__main__":
    unittest.main()
