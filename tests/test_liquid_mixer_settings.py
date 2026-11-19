#!/usr/bin/env python3
"""Tests for the MP Liquid Mixer Settings window's model.

The GTK dialog needs a display, so it is not opened here. Everything the
dialog reads and writes - validation, defaults, persistence, the theme list,
the sink helpers and the environment it hands a deck engine - is plain Python
and is exercised directly, which is what keeps a saved settings file from ever
breaking a launch.

Version 0.0.1
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIXER_DIR = PROJECT_ROOT / "MP_Liquid_Mixer"
sys.path.insert(0, str(MIXER_DIR))

import mixer_recording  # noqa: E402
import mixer_settings  # noqa: E402


class TestSettingDefaults(unittest.TestCase):
    def test_a_fresh_settings_object_is_the_documented_default(self):
        settings = mixer_settings.Settings()
        self.assertEqual(settings.recording_format, "WAV")
        self.assertEqual(settings.recording_part_limit_gib, 4)
        self.assertEqual(settings.recording_dir, "")
        self.assertEqual(settings.recording_device, "")
        self.assertEqual(settings.master_output_device, "")
        self.assertEqual(settings.theme, "")
        self.assertTrue(settings.visualizer_enabled)
        self.assertTrue(settings.fullscreen)
        self.assertTrue(settings.startup_video)
        self.assertTrue(settings.exit_video)
        self.assertFalse(settings.decks_autoplay)
        self.assertFalse(settings.record_on_start)
        self.assertEqual(settings.eq_preset, "Flat")

    def test_an_unknown_format_falls_back_to_wav(self):
        self.assertEqual(mixer_settings.Settings(recording_format="MP3").recording_format, "WAV")
        self.assertEqual(mixer_settings.Settings(recording_format="flac").recording_format, "FLAC")

    def test_only_the_three_offered_part_limits_are_accepted(self):
        self.assertEqual(mixer_settings.Settings(recording_part_limit_gib=2).recording_part_limit_gib, 2)
        self.assertEqual(mixer_settings.Settings(recording_part_limit_gib=1).recording_part_limit_gib, 1)
        self.assertEqual(mixer_settings.Settings(recording_part_limit_gib=9).recording_part_limit_gib, 4)
        self.assertEqual(mixer_settings.Settings(recording_part_limit_gib="junk").recording_part_limit_gib, 4)

    def test_a_boolean_spelled_in_a_hand_edited_file_is_understood(self):
        settings = mixer_settings.Settings(startup_video="false", visualizer_enabled="yes",
                                           exit_video=0, decks_autoplay="on")
        self.assertFalse(settings.startup_video)
        self.assertTrue(settings.visualizer_enabled)
        self.assertFalse(settings.exit_video)
        self.assertTrue(settings.decks_autoplay)

    def test_an_unknown_equaliser_falls_back_to_flat(self):
        self.assertEqual(mixer_settings.Settings(eq_preset="Nonsense").eq_preset, "Flat")
        self.assertEqual(mixer_settings.Settings(eq_preset="Trance").eq_preset, "Trance")

    def test_an_unknown_theme_is_dropped_rather_than_written(self):
        self.assertEqual(mixer_settings.Settings(theme="definitely-not-a-theme").theme, "")
        self.assertEqual(mixer_settings.Settings(theme="sensorium").theme, "sensorium")

    def test_the_system_default_alias_means_no_override(self):
        settings = mixer_settings.Settings(master_output_device="default",
                                           recording_device="master")
        self.assertEqual(settings.master_output_device, "")
        self.assertEqual(settings.recording_device, "")


class TestSettingsPersistence(unittest.TestCase):
    def test_roundtrip_writes_every_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            original = mixer_settings.Settings(
                recording_dir="/tmp/recs",
                recording_format="FLAC",
                recording_part_limit_gib=1,
                recording_device="alsa_output.usb",
                master_output_device="alsa_output.hdmi",
                theme="cosmic_supernova",
                visualizer_enabled=False,
                fullscreen=False,
                startup_video=False,
                exit_video=False,
                decks_autoplay=True,
                record_on_start=True,
                eq_preset="Techno",
            )
            original.save(path)
            loaded = mixer_settings.Settings.load(path)
        self.assertEqual(loaded.to_dict(), original.to_dict())

    def test_a_missing_file_loads_the_defaults(self):
        loaded = mixer_settings.Settings.load(Path("/nonexistent/liquid_mixer_settings.json"))
        self.assertEqual(loaded.to_dict(), mixer_settings.Settings().to_dict())

    def test_a_corrupt_file_loads_the_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(mixer_settings.Settings.load(path).recording_format, "WAV")

    def test_a_saved_file_is_a_plain_readable_json_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            mixer_settings.Settings(recording_format="FLAC").save(path)
            data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["recording_format"], "FLAC")
        self.assertEqual(data["version"], mixer_settings.SETTINGS_VERSION)

    def test_recorder_kwargs_name_the_same_values(self):
        settings = mixer_settings.Settings(recording_format="FLAC",
                                           recording_part_limit_gib=2,
                                           recording_device="")
        self.assertEqual(settings.as_recorder_kwargs(),
                         {"format": "FLAC", "part_limit_gib": 2, "device": ""})


class TestThemeOptions(unittest.TestCase):
    def test_the_dropdown_is_populated_and_unique(self):
        options = mixer_settings.theme_options()
        self.assertGreaterEqual(len(options), 4)
        ids = [tid for tid, _label in options]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(label for _tid, label in options))

    def test_a_known_theme_normalises_and_an_unknown_one_is_rejected(self):
        self.assertEqual(mixer_settings.normalize_theme("Sensorium"), "sensorium")
        self.assertEqual(mixer_settings.normalize_theme("stream-of-frequency"), "stream_of_frequency")
        self.assertEqual(mixer_settings.normalize_theme("nope"), "")


class TestPartLimit(unittest.TestCase):
    def test_the_recorder_reads_the_settings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "liquid_mixer_settings.json"
            path.write_text(json.dumps({"recording_part_limit_gib": 2}), encoding="utf-8")
            with patch.object(mixer_recording, "SETTINGS_PATH", path):
                self.assertEqual(mixer_recording.part_limit_gib(), 2)
                self.assertEqual(mixer_recording.part_limit_bytes(),
                                 2 * 1024 * 1024 * 1024)

    def test_the_four_gib_default_keeps_the_container_maximum(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "liquid_mixer_settings.json"
            with patch.object(mixer_recording, "SETTINGS_PATH", path):
                self.assertEqual(mixer_recording.part_limit_gib(), 4)
                self.assertEqual(mixer_recording.part_limit_bytes(),
                                 mixer_recording.MAX_PART_DATA_BYTES)

    def test_a_bad_value_never_produces_a_dangerous_limit(self):
        for bad in ({"recording_part_limit_gib": 0},
                    {"recording_part_limit_gib": 500},
                    {"recording_part_limit_gib": "x"},
                    {}):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "liquid_mixer_settings.json"
                path.write_text(json.dumps(bad), encoding="utf-8")
                with patch.object(mixer_recording, "SETTINGS_PATH", path):
                    self.assertEqual(mixer_recording.part_limit_gib(), 4)

    def test_the_flac_limit_stays_below_the_container(self):
        self.assertLess(mixer_recording.flac_part_limit_bytes_for(2),
                        2 * 1024 * 1024 * 1024)
        self.assertGreater(mixer_recording.flac_part_limit_bytes_for(1), 900_000_000)

    def test_the_writer_uses_the_limit_it_is_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            part = mixer_recording._WavPart(Path(tmp) / "a.wav", limit_bytes=4000)
            part.write(b"\x00" * 4000)
            self.assertTrue(part.full)
            self.assertEqual(part.room(1000), 0)
            part.finalize()
            self.assertEqual((Path(tmp) / "a.wav").stat().st_size, 44 + 4000)


class TestRecordingSettings(unittest.TestCase):
    def test_the_format_helper_reads_the_settings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "liquid_mixer_settings.json"
            path.write_text(json.dumps({"recording_format": "FLAC"}), encoding="utf-8")
            with patch.object(mixer_recording, "SETTINGS_PATH", path):
                self.assertEqual(mixer_recording.recording_format(), "FLAC")
                self.assertEqual(mixer_recording.recording_device_setting(), "")

    def test_the_recording_dir_override_wins_and_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "my recordings"
            path = Path(tmp) / "liquid_mixer_settings.json"
            path.write_text(json.dumps({"recording_dir": str(target)}), encoding="utf-8")
            cfg = Path(tmp) / "cfg"
            cfg.mkdir()
            (cfg / "liquid_mixer_settings.json").write_text(
                json.dumps({"recording_dir": str(target)}), encoding="utf-8")
            with patch.object(mixer_recording, "SETTINGS_PATH", path):
                out = mixer_recording.recording_dir(Path(tmp) / "app", cfg)
            self.assertEqual(out, target.resolve())
            self.assertTrue(target.is_dir())

    def test_an_unwritable_override_falls_back_to_the_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "liquid_mixer_settings.json"
            # /proc is never writable; the recorder must not lose the capture.
            path.write_text(json.dumps({"recording_dir": "/proc/nope/rec"}), encoding="utf-8")
            base = Path(tmp) / "app"
            base.mkdir()
            with patch.object(mixer_recording, "SETTINGS_PATH", path):
                out = mixer_recording.recording_dir(base, Path(tmp) / "cfg")
            self.assertEqual(out, (base / mixer_recording.SUBDIR).resolve())


class TestRecorderDevice(unittest.TestCase):
    def setUp(self):
        # The recorder latches the saved GiB setting when it is constructed, so
        # a real settings file on this machine must not decide the expectation.
        self._settings_patch = patch.object(
            mixer_recording, "SETTINGS_PATH",
            Path(tempfile.gettempdir()) / "mp-mixer-absent-settings.json")
        self._settings_patch.start()
        self.addCleanup(self._settings_patch.stop)

    def test_the_default_capture_is_the_monitor_of_the_default_sink(self):
        recorder = mixer_recording.MasterRecorder()
        self.assertEqual(recorder.capture_target(), "@DEFAULT_MONITOR@")

    def test_a_named_sink_captures_that_sinks_monitor(self):
        recorder = mixer_recording.MasterRecorder()
        recorder.set_device("alsa_output.usb-headphones")
        self.assertEqual(recorder.capture_target(), "alsa_output.usb-headphones.monitor")
        recorder.set_device("alsa_output.usb-headphones.monitor")
        self.assertEqual(recorder.capture_target(), "alsa_output.usb-headphones.monitor")

    def test_the_capture_command_names_the_chosen_device(self):
        cmd = mixer_recording.recorder_command(device="bluez_output.headphones.monitor")
        self.assertIn("--device=bluez_output.headphones.monitor", cmd)

    def test_part_limit_setting_is_latched_for_the_capture(self):
        recorder = mixer_recording.MasterRecorder()
        self.assertEqual(recorder.part_limit_gib, 4)
        self.assertEqual(recorder.set_part_limit_gib(1), 1)
        self.assertEqual(recorder.part_limit_gib, 1)
        # Out-of-range values are normalised rather than trusted.
        self.assertEqual(recorder.set_part_limit_gib(12), 4)


class TestSettingsDialog(unittest.TestCase):
    """The dialog is driven with a stubbed ``run()`` and no real display use.

    GTK needs an X11 connection to build widgets, so these tests skip when the
    display cannot be opened rather than failing a headless run.
    """

    def setUp(self):
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gtk
            self.Gtk = Gtk
            Gtk.init_check([])
            if not Gtk.init_check([])[0]:
                self.skipTest("no display for GTK")
            import mixer_settings_dialog
            self.dialog_module = mixer_settings_dialog
        except Exception as exc:  # pragma: no cover - headless host
            self.skipTest(f"GTK unavailable: {exc}")

    def test_cancel_returns_none(self):
        window = self.Gtk.Window()
        window.show_all()
        Gtk = self.Gtk
        original = Gtk.Dialog.run
        Gtk.Dialog.run = lambda self: Gtk.ResponseType.CANCEL
        try:
            result = self.dialog_module.show_settings_dialog(
                window, mixer_settings.Settings())
        finally:
            Gtk.Dialog.run = original
        self.assertIsNone(result)

    def test_save_round_trips_the_snapshot(self):
        window = self.Gtk.Window()
        window.show_all()
        settings = mixer_settings.Settings(
            recording_format="FLAC", recording_part_limit_gib=1,
            eq_preset="Techno", visualizer_enabled=False,
            startup_video=False, exit_video=False, decks_autoplay=True)
        Gtk = self.Gtk
        original = Gtk.Dialog.run
        Gtk.Dialog.run = lambda self: Gtk.ResponseType.OK
        try:
            result = self.dialog_module.show_settings_dialog(window, settings)
        finally:
            Gtk.Dialog.run = original
        self.assertIsNotNone(result)
        self.assertEqual(result.recording_format, "FLAC")
        self.assertEqual(result.recording_part_limit_gib, 1)
        self.assertEqual(result.eq_preset, "Techno")
        self.assertFalse(result.visualizer_enabled)
        self.assertFalse(result.startup_video)
        self.assertFalse(result.exit_video)
        self.assertTrue(result.decks_autoplay)
        # The default folder is stored as "" so it keeps following the archive.
        self.assertEqual(result.recording_dir, "")


class TestSettingsDialogTheme(unittest.TestCase):
    """The Settings window uses the mixer's theme, and C repaints it."""

    def setUp(self):
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gtk
            self.Gtk = Gtk
            if not Gtk.init_check([])[0]:
                self.skipTest("no display for GTK")
            import mixer_settings_dialog
            self.dialog_module = mixer_settings_dialog
        except Exception as exc:  # pragma: no cover - headless host
            self.skipTest(f"GTK unavailable: {exc}")

    def test_a_theme_is_styled_and_c_cycles_it(self):
        Gtk = self.Gtk
        window = Gtk.Window()
        window.show_all()
        seen = []

        def colors(name):
            seen.append(name)
            return ((166, 77, 255), (247, 37, 133), (0, 229, 255))

        def field(name):
            return (12, 12, 16)

        def ink(name):
            return (240, 244, 250)

        cycles = iter(["sensorium", "stream_of_frequency"])
        result_theme = {"name": "into_darkness_void_out"}

        def cycle():
            result_theme["name"] = next(cycles)
            return result_theme["name"]

        original = Gtk.Dialog.run
        captured = {}

        def fake_run(dialog_self):
            captured["dialog"] = dialog_self
            # Deliver a C key the way a real window would. GTK will not accept
            # a Python stand-in for a GdkEvent, so the handler is pulled off
            # the signal and called with the minimal event it reads.
            handler = None
            for signal_id in ():
                pass
            handler = dialog_self._settings_theme_key_handler
            self.assertTrue(handler(_KeyEvent(ord("c"))))
            self.assertFalse(handler(_KeyEvent(ord("z"))))
            return Gtk.ResponseType.CANCEL

        Gtk.Dialog.run = fake_run
        try:
            self.dialog_module.show_settings_dialog(
                window, mixer_settings.Settings(),
                theme="into_darkness_void_out",
                on_cycle_theme=cycle,
                theme_colors=colors, theme_label=lambda n: n,
                theme_field=field, theme_ink=ink,
            )
        finally:
            Gtk.Dialog.run = original
        self.assertIn("into_darkness_void_out", seen)
        self.assertIn("sensorium", seen)
        # The mixer's own handler supplied the new name.
        self.assertEqual(result_theme["name"], "sensorium")

    def test_css_is_built_for_the_theme_palette(self):
        css = self.dialog_module._css_rgb((10, 20, 30), 0.5)
        self.assertEqual(css, "rgba(10,20,30,0.500)")


class _KeyEvent:
    """Minimal Gdk.EventKey stand-in with just the keyval the dialog reads."""

    def __init__(self, keyval):
        self.keyval = keyval


class TestSinkHelpers(unittest.TestCase):
    def test_the_sink_monitor_name_is_formatted_once(self):
        self.assertEqual(mixer_settings.sink_monitor(""),
                         "@DEFAULT_MONITOR@")
        self.assertEqual(mixer_settings.sink_monitor("alsa_output.x.monitor"),
                         "alsa_output.x.monitor")
        self.assertEqual(mixer_settings.sink_monitor("alsa_output.x"),
                         "alsa_output.x.monitor")

    def test_a_sink_label_marks_bluetooth_devices(self):
        self.assertIn("headphones",
                      mixer_settings.sink_label({"description": "headphones", "bluetooth": True}))
        self.assertIn("speakers",
                      mixer_settings.sink_label({"description": "speakers", "bluetooth": False}))

    def test_no_chosen_device_means_the_system_default_environment(self):
        env = mixer_settings.child_audio_env(mixer_settings.Settings())
        self.assertEqual(env["MP_AUDIO_OUTPUT_DEVICE"], "default")
        self.assertNotIn("PULSE_SINK", env)

    def test_a_chosen_device_pins_the_deck_without_changing_the_desktop(self):
        settings = mixer_settings.Settings(master_output_device="alsa_output.hdmi")
        env = mixer_settings.child_audio_env(settings)
        self.assertEqual(env["PULSE_SINK"], "alsa_output.hdmi")
        self.assertTrue(env["MP_AUDIO_OUTPUT_DEVICE"])

    def test_list_audio_sinks_never_raises_without_a_sound_server(self):
        with patch.object(mixer_settings.shutil, "which", return_value=None):
            self.assertEqual(mixer_settings.list_audio_sinks(force=True), [])


if __name__ == "__main__":
    unittest.main()
