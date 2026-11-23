#!/usr/bin/env python3
"""
MP Bass Boost for MP Liquid Player and MP Audio Player (CLI).

The dial on the side of the Skullcandy Crusher ANC 2 changes how hard the
headphone's second bass driver is driven. SoundGuys' measurements of the same
headphones put the boost at about +20 dB at 50 Hz with Crusher at 80 %, peaked
at ~45 Hz, tapering below ~30 Hz, and gone by ~500 Hz.

These tests pin:
- the two boost steps and their relationship to the headphone (High = 2/3,
  Medium = 1/3, Normal = off) and that Extreme is gone from the dial
- the DSP curve (peak at 45 Hz, sub-bass taper, no boost above ~500 Hz)
- streaming correctness of the overlap-save FIR
- persistence, aliases, the engine/status/IPC plumbing
- the `b` key, the clickable button, the per-mode colours
- the CLI flags
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import MP_Audio_Player as player

PLAYER_PY = BASE_DIR / "MP_Audio_Player.py"
ANSI = re.compile(r"\x1b\[[0-9;?<>=]*[A-Za-z~]")


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def response_db(taps, freq, sr=48000.0):
    n = 1 << 18
    spectrum = np.abs(np.fft.rfft(taps, n=n))
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    index = int(np.argmin(np.abs(freqs - freq)))
    return 20.0 * np.log10(max(spectrum[index], 1e-12))


class TempConfigCase(unittest.TestCase):
    """Every test writes its bass-mode file into a throwaway folder."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self._tmp.name)
        self._patches = [
            patch.object(player, "BASS_BOOST_FILE", self.cfg / "bass_boost"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()


class BassModeModelTests(TempConfigCase):
    def test_three_modes_in_dial_order(self):
        self.assertEqual(player.BASS_BOOST_MODES, ("Normal", "Medium", "High"))

    def test_high_is_the_loudest_mode_and_extreme_is_gone(self):
        fractions = player.BASS_BOOST_FRACTIONS
        self.assertEqual(fractions["Normal"], 0.0)
        self.assertAlmostEqual(fractions["Medium"], 1.0 / 3.0)
        self.assertAlmostEqual(fractions["High"], 2.0 / 3.0)
        self.assertNotIn("Extreme", fractions)
        self.assertNotIn("Extreme", player.BASS_MODE_STYLE)
        self.assertGreater(player.CRUSHER_BASS_FULL_GAIN_DB, 20.0)

    def test_the_dial_is_trimmed_by_three_db(self):
        """The boost is half as loud as the headphone dial it follows.

        A -3 dB trim halves the acoustic power of the boost. Medium lands at
        about +5.3 dB at 45 Hz instead of +8.3 dB. Normal stays a bypass.
        High takes that same trim and is then brought all the way down to
        about +7.3 dB at 45 Hz, well under the old +11.2 dB.
        """
        self.assertAlmostEqual(player.BASS_BOOST_TRIM_DB, 3.0, delta=0.1)
        self.assertAlmostEqual(player.BASS_BOOST_HIGH_POWER, 0.232468)
        self.assertEqual(player.bass_mode_peak_db("Normal"), 0.0)
        full = player.CRUSHER_BASS_FULL_GAIN_DB
        self.assertAlmostEqual(player.bass_mode_peak_db("Medium"), full / 3.0 - 3.0103, delta=0.02)
        trimmed_high = full * 2.0 / 3.0 - 3.0103
        high = player.bass_mode_peak_db("High")
        self.assertAlmostEqual(high, trimmed_high - player.BASS_BOOST_HIGH_EXTRA_TRIM_DB, delta=0.02)
        # Half the power is exactly -3.0103 dB, and Medium is only that trim.
        ratio = 10.0 ** ((player.bass_mode_peak_db("Medium") - full / 3.0) / 20.0)
        self.assertAlmostEqual(ratio, 0.5 ** 0.5, delta=0.01)
        # High's extra cut takes it down to the documented +7.32 dB peak.
        power = 10.0 ** ((high - trimmed_high) / 10.0)
        self.assertAlmostEqual(power, 0.232468, delta=0.01)
        self.assertAlmostEqual(high, 7.32, delta=0.02)

    def test_default_mode_is_normal(self):
        mgr = player.BassBoostManager()
        self.assertEqual(mgr.mode, "Normal")
        self.assertEqual(mgr.peak_db(), 0.0)

    def test_mode_is_persisted_for_the_next_window(self):
        player.BassBoostManager().set_mode("High")
        self.assertEqual((self.cfg / "bass_boost").read_text(encoding="utf-8"), "High")
        self.assertEqual(player.BassBoostManager().mode, "High")

    def test_aliases_and_cycle_wrap(self):
        mgr = player.BassBoostManager()
        # A saved "Extreme" from an older build lands on High, not Normal, so it
        # is not silently switched off.
        self.assertEqual(player.BassBoostManager.resolve("crusher"), "High")
        self.assertEqual(player.BassBoostManager.resolve("extreme"), "High")
        self.assertEqual(player.BassBoostManager.resolve("off"), "Normal")
        self.assertIsNone(player.BassBoostManager.resolve("warp"))
        seen = [mgr.mode]
        for _ in range(3):
            seen.append(mgr.cycle(1))
        self.assertEqual(seen, ["Normal", "Medium", "High", "Normal"])
        mgr.set_mode("Normal")
        self.assertEqual(mgr.cycle(-1), "High")

    def test_the_cli_label_is_text_only_and_still_coloured(self):
        """The CLI form must not carry a 48;2 block, but must keep the hue."""
        for mode in player.BASS_BOOST_MODES:
            label = player.bass_mode_label(mode)
            self.assertNotIn("48;2;", label, mode)
            self.assertIn(f"BASS BOOST: {mode.upper()}", strip_ansi(label))
            (r, g, b) = player.BASS_MODE_STYLE[mode][1]
            self.assertIn(f"38;2;{r};{g};{b}", label, mode)

    def test_every_mode_has_its_own_colour(self):
        colours = {m: player.BASS_MODE_STYLE[m] for m in player.BASS_BOOST_MODES}
        chips = {m: player.bass_mode_chip(m) for m in player.BASS_BOOST_MODES}
        codes = {m: re.search(r"48;2;(\d+;\d+;\d+);38;2;(\d+;\d+;\d+)", chips[m]).groups() for m in chips}
        self.assertEqual(len({c for c in codes.values()}), 3, "each mode needs its own colour")
        high_bg, high_fg = codes["High"]
        self.assertEqual(high_bg, "56;34;0")
        r, g, b = (int(x) for x in high_fg.split(";"))
        self.assertGreater(r, 200)
        self.assertLess(b, g, "High keeps the warm amber ink")
        self.assertLess(g, r)
        for mode, (bg, fg) in colours.items():
            self.assertNotEqual(bg, fg)
        self.assertIn("HIGH", strip_ansi(chips["High"]))


class CrusherCurveTests(unittest.TestCase):
    def test_peak_sits_at_45hz_and_sub_bass_tapers(self):
        freqs = [20.0, 30.0, 45.0, 50.0, 70.0, 100.0, 250.0, 500.0]
        values = dict(zip(freqs, (float(v) for v in player.crusher_bass_response_db(freqs, 25.0))))
        self.assertAlmostEqual(values[45.0], 25.0, places=4)
        self.assertGreater(values[45.0], values[30.0], "the sub-bass side must taper")
        self.assertGreater(values[30.0], values[20.0])
        self.assertLess(values[100.0], values[50.0])
        self.assertLess(values[250.0], 1.0)
        self.assertLess(values[500.0], 0.05)

    def test_boost_scales_with_the_mode(self):
        f = [45.0]
        full = player.CRUSHER_BASS_FULL_GAIN_DB
        medium = float(player.crusher_bass_response_db(f, full / 3.0)[0])
        high = float(player.crusher_bass_response_db(f, full * 2.0 / 3.0)[0])
        self.assertAlmostEqual(medium * 3.0, full, places=4)
        self.assertAlmostEqual(high * 1.5, full, places=4)
        self.assertLess(high, full, "no offered mode reaches the full boost")


class BassBoostFilterTests(unittest.TestCase):
    def _filter(self, mode, sr=48000):
        filt = player.BassBoostFilter()
        filt.set_mode(mode, sr)
        return filt

    def test_normal_is_a_true_bypass(self):
        filt = self._filter("Normal")
        self.assertFalse(filt.is_active)
        block = np.zeros((64, 2), dtype=np.float32)
        self.assertIs(filt.process(block), block)

    def test_measured_curve_matches_the_headphone(self):
        filt = self._filter("High")
        peak = player.bass_mode_peak_db("High")
        self.assertAlmostEqual(response_db(filt.taps, 45.0), peak, delta=0.6)
        self.assertGreater(response_db(filt.taps, 50.0), peak - 1.0)
        self.assertLess(response_db(filt.taps, 20.0), response_db(filt.taps, 45.0))
        self.assertLess(response_db(filt.taps, 30.0), response_db(filt.taps, 45.0))
        self.assertLess(abs(response_db(filt.taps, 1000.0)), 1.0)
        self.assertLess(abs(response_db(filt.taps, 4000.0)), 1.0)

    def test_modes_step_by_a_third_of_the_full_boost(self):
        medium = response_db(self._filter("Medium").taps, 45.0)
        high = response_db(self._filter("High").taps, 45.0)
        full = player.CRUSHER_BASS_FULL_GAIN_DB
        trim = player.BASS_BOOST_TRIM_DB
        self.assertAlmostEqual(medium, full / 3.0 - trim, delta=0.6)
        self.assertAlmostEqual(
            high,
            full * 2.0 / 3.0 - trim - player.BASS_BOOST_HIGH_EXTRA_TRIM_DB,
            delta=0.6,
        )
        self.assertLess(high, full, "the dial must stop short of the full boost")

    def test_streamed_blocks_match_a_single_pass(self):
        rng = np.random.default_rng(11)
        signal = (rng.standard_normal((4096, 2)) * 0.05).astype(np.float32)
        one_pass = self._filter("High").process(signal.copy())
        streamed_filter = self._filter("High")
        streamed = np.vstack([streamed_filter.process(signal[i : i + 1024]) for i in range(0, 4096, 1024)])
        np.testing.assert_allclose(one_pass, streamed, atol=1e-5)

    def test_odd_block_sizes_keep_the_shape(self):
        filt = self._filter("High")
        for n in (1, 37, 511, 512, 1000, 1024, 4096, 7000):
            block = (np.random.default_rng(n).standard_normal((n, 2)) * 0.05).astype(np.float32)
            self.assertEqual(filt.process(block).shape, (n, 2))

    def test_output_never_clips(self):
        rng = np.random.default_rng(3)
        hot = (rng.standard_normal((4096, 2)) * 0.9).astype(np.float32)
        out = self._filter("High").process(hot)
        self.assertLessEqual(float(np.max(np.abs(out))), 1.0 + 1e-6)

    def test_the_boost_actually_raises_bass_energy(self):
        sr = 48000
        t = np.arange(sr, dtype=np.float32) / sr
        tone = (0.05 * np.sin(2 * np.pi * 45.0 * t)).astype(np.float32)
        stereo = np.column_stack([tone, tone])
        filt = self._filter("High")
        out = np.vstack([filt.process(stereo[i : i + 1024]) for i in range(0, sr, 1024)])
        gain = 10.0 ** (player.bass_mode_peak_db("High") / 20.0)
        self.assertGreater(float(np.max(np.abs(out))), float(np.max(np.abs(stereo))) * gain * 0.85)


class BassEngineTests(TempConfigCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.track = Path(self.tmp.name) / "Mix_001.flac"
        self.track.write_bytes(b"RIFF" + b"\x00" * 64)
        self.playlist = [{"name": "Mix_001", "path": str(self.track), "size_mb": 1.0, "ext": "FLAC"}]
        self.engine = player.AudioEngine(self.playlist, auto_bluetooth=False)

    def tearDown(self):
        try:
            self.engine.shutdown()
        except Exception:
            pass
        self.tmp.cleanup()
        super().tearDown()

    def test_status_reports_the_bass_mode(self):
        st = self.engine.get_status_dict()
        self.assertEqual(st["bass_mode"], "Normal")
        self.assertEqual(tuple(st["bass_modes"]), player.BASS_BOOST_MODES)
        self.assertEqual(st["bass_percent"], 0)
        self.engine.set_bass_mode("High")
        st = self.engine.get_status_dict()
        self.assertEqual(st["bass_mode"], "High")
        self.assertEqual(st["bass_percent"], 67)
        self.assertAlmostEqual(st["bass_peak_db"], player.bass_mode_peak_db("High"), places=2)

    def test_engine_set_and_cycle_drive_the_filter(self):
        self.assertTrue(self.engine.set_bass_mode("Medium"))
        self.assertTrue(self.engine.bass_filter.is_active)
        self.assertEqual(self.engine.bass_filter.mode, "Medium")
        self.assertEqual(self.engine.cycle_bass_mode(1), "High")
        self.assertEqual(self.engine.bass_filter.mode, "High")
        self.assertEqual(self.engine.cycle_bass_mode(1), "Normal")
        self.assertFalse(self.engine.bass_filter.is_active)
        self.assertFalse(self.engine.set_bass_mode("Turbo"))
        # Extreme is off the dial, but a script or an old saved file that still
        # asks for it lands on High rather than being refused outright.
        self.assertTrue(self.engine.set_bass_mode("Extreme"))
        self.assertEqual(self.engine.bass_manager.mode, "High")

    def test_ipc_commands(self):
        server = player.PlayerIPCServer(self.engine)

        def call(**request):
            sock = MagicMock()
            sock.recv.return_value = json.dumps(request).encode("utf-8")
            server._handle_client(sock)
            return json.loads(sock.sendall.call_args[0][0].decode("utf-8"))

        payload = call(cmd="set_bass_mode", mode="Medium")
        self.assertTrue(payload["success"], payload)
        self.assertEqual(payload["bass_mode"], "Medium")
        self.assertEqual(payload["bass_modes"], list(player.BASS_BOOST_MODES))

        self.assertEqual(call(cmd="cycle_bass")["bass_mode"], "High")
        self.assertEqual(call(cmd="bass_down")["bass_mode"], "Medium")
        self.assertEqual(call(cmd="get_bass")["bass_mode"], "Medium")

        payload = call(cmd="set_bass_mode", mode="Turbo")
        self.assertFalse(payload["success"], payload)


def _plain(line: str) -> str:
    return strip_ansi(line)


class BassPlayerUITests(TempConfigCase):
    def _ui(self, engine=None, remote=False, mode="NORMAL"):
        scanner = MagicMock()
        scanner.get_mixes.return_value = []
        scanner.base_dir = Path("/tmp/mix-archive")
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "0"}, clear=False):
            ui = player.TerminalPlayerUI(engine, scanner, is_remote=remote, ui_size_mode=mode)
        # The real daemon status file must not leak into a unit test: keep the
        # synthetic status the test sets up.
        ui._get_status = lambda: ui.cached_status
        ui.cached_status = {
            "title": "Trance Roots 2026.flac", "path": "/mixes/a.flac", "state": "playing",
            "mode": "MIX_ARCHIVE", "position_fmt": "01:02", "duration_fmt": "06:40",
            "progress_pct": 15.0, "volume": 80, "muted": False, "pitch_percent": 0,
            "shuffle": False, "repeat": "all", "channels": 2, "sample_rate": 48000,
            "recording": False, "bluetooth_connected": False, "output_device": "Speakers",
            "queue_len": 0, "queue": [], "bass_mode": "Normal",
            "levels": [0.2] * 24, "peaks": [0.3] * 24,
            "levels_left": [0.2] * 24, "levels_right": [0.2] * 24,
            "peaks_left": [0.3] * 24, "peaks_right": [0.3] * 24,
            "rms_level": 0.1, "peak_db": -8.0,
        }
        return ui

    def _draw(self, ui, liquid=False):
        captured = []
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1" if liquid else "0"}, clear=False), \
             patch("MP_Audio_Player._present_frame", side_effect=lambda lines, w, h: captured.extend(lines)), \
             patch("MP_Audio_Player._win_visible_size", return_value=(120, 40)), \
             patch("sys.stdout.write"), patch("sys.stdout.flush"):
            ui._draw_frame()
        return captured

    def test_b_cycles_the_dial_without_exiting(self):
        ui = self._ui(remote=True)
        with patch.object(player, "send_ipc_command", return_value={"status": "ok", "bass_mode": "Medium"}) as ipc:
            ui._process_key(b"b")
        ipc.assert_called_once_with("cycle_bass")
        self.assertTrue(ui.running)
        self.assertEqual(ui.cached_status["bass_mode"], "Medium")
        self.assertEqual(ui.notification_msg, "Bass Boost: MEDIUM")
        self.assertNotIn("crusher", ui.notification_msg.lower())

    def test_escape_still_exits(self):
        ui = self._ui(remote=True)
        ui._process_key(b"\x1b")
        self.assertFalse(ui.running)

    def test_full_frame_shows_the_coloured_button(self):
        """Liquid gets the filled chip; the CLI keeps the text-only row.

        The CLI's Favorites row is deliberately text-only — a filled block
        there paints a colour strip across the console, and
        tests/test_player_favorites.py pins that. So the CLI is checked for the
        mode ink, and the Liquid hosts for the filled chip.
        """
        for liquid in (False, True):
            ui = self._ui(remote=True)
            for mode in player.BASS_BOOST_MODES:
                ui.cached_status["bass_mode"] = mode
                drawn = "\n".join(self._draw(ui, liquid=liquid))
                self.assertIn(f"BASS BOOST: {mode.upper()}", strip_ansi(drawn), mode)
                if liquid:
                    # The Liquid chip is themed, so its ink is chosen from the
                    # fill's luminance rather than the CLI's fixed mode ink.
                    chip = player.bass_mode_chip(mode, theme_name=ui.visualizer.theme)
                    bg = re.search(r"48;2;(\d+;\d+;\d+)", chip).group(1)
                    fg = re.search(r"38;2;(\d+;\d+;\d+)", chip).group(1)
                    self.assertIn(f"48;2;{bg}", drawn, f"{mode} chip missing")
                    self.assertIn(f"38;2;{fg}", drawn, f"{mode} chip ink missing")
                    # The chip follows the theme, so it is never one of the old
                    # fixed near-blacks that made the buttons look black.
                    self.assertNotIn("48;2;18;30;54", drawn, f"{mode} chip is the old black fill")
                else:
                    fg = re.search(r"38;2;(\d+;\d+;\d+)", player.bass_mode_label(mode)).group(1)
                    self.assertIn(f"38;2;{fg}", drawn, f"{mode} ink missing")
                    self.assertNotIn("48;2;18;30;54", drawn, "CLI row must not be a filled block")
            # A stale status file that still says Extreme must never draw the
            # removed red chip; the player falls back to Normal.
            ui.cached_status["bass_mode"] = "Extreme"
            drawn = "\n".join(self._draw(ui, liquid=liquid))
            self.assertNotIn("EXTREME", strip_ansi(drawn), "Extreme is gone")
            self.assertNotIn("38;2;255;70;70", drawn)
            self.assertIn("BASS BOOST: NORMAL", strip_ansi(drawn))

    def test_clicking_the_button_cycles_the_mode(self):
        ui = self._ui(remote=True)
        self._draw(ui, liquid=True)
        self.assertTrue(ui._bass_button_row)
        with patch.object(player, "send_ipc_command", return_value={"status": "ok", "bass_mode": "Medium"}) as ipc:
            ui._process_key(f"\x1b[<0;{ui._bass_button_span[0]};{ui._bass_button_row}M".encode())
        ipc.assert_called_once_with("cycle_bass")
        self.assertEqual(ui.cached_status["bass_mode"], "Medium")

    def test_button_is_on_full_mini_and_micro_frames(self):
        for size in ("NORMAL", "MINI", "MICRO"):
            ui = self._ui(remote=True, mode=size)
            drawn = _plain("\n".join(self._draw(ui, liquid=True)))
            self.assertIn("BASS BOOST: NORMAL", drawn, size)

    def test_the_extra_row_never_pushes_a_control_line_off_the_frame(self):
        """The Bass Boost row must not clip the footer on a default console.

        It added one line to a full 24-row frame, and MUSIC mode draws an extra
        hint line on top of that. The frame now drops its breathing row rather
        than the last control hint, so every hint the player prints stays
        visible at the sizes the shipped windows actually use.
        """
        for liquid in (False, True):
            for mode in ("MIX_ARCHIVE", "MUSIC", "FAVORITES"):
                for height in (24, 25, 26, 30):
                    ui = self._ui(remote=True)
                    ui.mode = mode
                    ui.cached_status["mode"] = mode
                    ui.scanner.get_mixes.return_value = [
                        {"name": "a.flac", "path": "/mixes/a.flac", "ext": "FLAC", "size_mb": 3.0}
                    ]
                    captured = []
                    env = {"MP_LIQUID_GLASS": "1" if liquid else "0"}
                    with patch.dict(os.environ, env, clear=False), \
                         patch("MP_Audio_Player._present_frame",
                               side_effect=lambda lines, w, h: captured.extend(lines)), \
                         patch("MP_Audio_Player._win_visible_size", return_value=(100, height)), \
                         patch("sys.stdout.write"), patch("sys.stdout.flush"):
                        ui._draw_frame()
                    self.assertLessEqual(
                        len(captured), height,
                        f"{mode} at {height} rows grew to {len(captured)} lines",
                    )
                    self.assertIn(
                        "BASS BOOST", _plain("\n".join(captured)),
                        f"{mode} at {height} rows lost the bass button",
                    )

    def test_bass_status_also_sits_in_the_now_playing_footer(self):
        ui = self._ui(remote=True)
        ui.cached_status["bass_mode"] = "High"
        captured = []
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "0"}, clear=False), \
             patch("MP_Audio_Player._present_frame",
                   side_effect=lambda lines, w, h: captured.extend(lines)), \
             patch("MP_Audio_Player._win_visible_size", return_value=(200, 50)), \
             patch("sys.stdout.write"), patch("sys.stdout.flush"):
            ui._draw_frame()
        drawn = _plain("\n".join(captured))
        self.assertIn("BASS: HIGH", drawn)


class FrameFittingTests(TempConfigCase):
    """Every player size must fit its console, or the footer keys vanish.

    The bass chip originally got its own row. That cost the full CLI frame one
    line, and at 24 rows the line came out of the playlist: the list was left
    with no visible rows, so the arrow keys moved a cursor nobody could see
    (three existing arrow-navigation tests caught it). Mini and Micro had a
    separate, pre-existing overflow from a variable-height visualizer block.

    The chip now shares the Favorites row, each renderer reserves room for its
    visualizer, and `_fit_frame` trims a frame that is still one row too tall
    without dropping the bottom hint row.
    """

    def _ui(self, mode="NORMAL", library="MIX_ARCHIVE"):
        scanner = MagicMock()
        scanner.base_dir = Path("/tmp/mix-archive")
        scanner.get_mixes.return_value = [
            {"name": f"Mix {i}.flac", "path": f"/mixes/{i}.flac", "ext": "FLAC", "size_mb": 10.0}
            for i in range(6)
        ]
        ui = player.TerminalPlayerUI(None, scanner, is_remote=True, ui_size_mode=mode)
        ui.mode = library
        ui._get_status = lambda: ui.cached_status
        ui.cached_status = {
            "title": "Trance Roots 2026.flac", "path": "/mixes/0.flac", "state": "playing",
            "mode": library, "position_fmt": "01:02", "duration_fmt": "06:40",
            "progress_pct": 15.0, "volume": 80, "muted": False, "pitch_percent": 0,
            "shuffle": False, "repeat": "all", "channels": 2, "sample_rate": 48000,
            "recording": False, "bluetooth_connected": False, "output_device": "Speakers",
            "queue_len": 0, "queue": [], "bass_mode": "High",
            "levels": [0.2] * 24, "peaks": [0.3] * 24,
            "levels_left": [0.2] * 24, "levels_right": [0.2] * 24,
            "peaks_left": [0.3] * 24, "peaks_right": [0.3] * 24,
            "rms_level": 0.1, "peak_db": -8.0,
        }
        return ui

    def _frame(self, liquid, ui, width, height):
        captured = []
        env = {"MP_LIQUID_GLASS": "1" if liquid else "0"}
        with patch.dict(os.environ, env, clear=False), \
             patch("MP_Audio_Player._present_frame",
                   side_effect=lambda lines, w, h: captured.extend(lines)), \
             patch("MP_Audio_Player._win_visible_size", return_value=(width, height)), \
             patch("sys.stdout.write"), patch("sys.stdout.flush"):
            ui._draw_frame()
        return captured

    def test_no_player_size_draws_more_rows_than_its_console(self):
        shapes = (
            ("NORMAL", 120, 40), ("NORMAL", 90, 24), ("NORMAL", 80, 24),
            ("MINI", 74, 14), ("MINI", 74, 16), ("MINI", 60, 14),
            ("MICRO", 74, 7), ("MICRO", 74, 9), ("MICRO", 74, 12),
        )
        for liquid in (False, True):
            for library in ("MIX_ARCHIVE", "MUSIC", "FAVORITES"):
                for size, width, height in shapes:
                    ui = self._ui(size, library)
                    captured = self._frame(liquid, ui, width, height)
                    self.assertLessEqual(
                        len(captured), height,
                        f"{'liquid ' if liquid else ''}{library} {size} {width}x{height} "
                        f"drew {len(captured)} rows",
                    )

    def test_the_full_cli_playlist_keeps_visible_rows(self):
        """The regression the bass row caused: an empty playlist pane."""
        ui = self._ui("NORMAL", "MIX_ARCHIVE")
        captured = self._frame(False, ui, 90, 24)
        drawn = _plain("\n".join(captured))
        self.assertIn("Mix 0.flac", drawn, "the playlist lost every visible row")
        self.assertIn("BASS BOOST", drawn)

    def test_the_bass_chip_and_favourites_share_one_row(self):
        ui = self._ui("NORMAL", "MIX_ARCHIVE")
        self._frame(False, ui, 90, 24)
        # The contract is that the two chips share one row, not the literal
        # row number: the Vertical-frame refit (9da4dc6) added a blank spacer
        # above the action row and moved both from 16 to 17 together.
        self.assertEqual(ui._bass_button_row, ui._favorites_button_row)
        self.assertGreater(ui._bass_button_row, 0)

    def test_the_mini_key_hints_survive(self):
        """The [G] MP Gui hint used to be the row that got clipped away."""
        ui = self._ui("MINI", "MIX_ARCHIVE")
        captured = self._frame(True, ui, 74, 14)
        drawn = _plain("\n".join(captured))
        self.assertIn("[G] MP Gui", drawn)
        self.assertIn("BASS", drawn)

    def test_fit_frame_keeps_the_bottom_row(self):
        lines = [f"row {i}" for i in range(20)]
        fitted = player._fit_frame(lines, 10)
        self.assertEqual(len(fitted), 10)
        self.assertEqual(fitted[0], "row 0")
        self.assertEqual(fitted[-1], "row 19", "the footer must survive")
        self.assertEqual(player._fit_frame(lines, 20), lines)
        self.assertEqual(player._fit_frame(lines, 40), lines)

    def test_clicking_the_bass_chip_lands_on_the_chip(self):
        for width in (90, 70, 55):
            ui = self._ui("NORMAL", "MIX_ARCHIVE")
            captured = self._frame(False, ui, width, 24)
            self.assertTrue(ui._bass_button_row, f"{width} drew no bass chip")
            row = _plain(captured[ui._bass_button_row - 1])
            start, end = ui._bass_button_span
            self.assertIn("BASS BOOST", row[start - 1 : end], f"{width} span misses the chip")


class BassCliTests(unittest.TestCase):
    def _run(self, *args, timeout=60):
        return subprocess.run(
            [sys.executable, str(PLAYER_PY), *args],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "MP_DISABLE_AUDIO_OUTPUT": "1", "MP_DISABLE_BLUETOOTH_MONITOR": "1"},
        )

    def test_help_lists_the_flags(self):
        res = self._run("--help")
        self.assertEqual(res.returncode, 0)
        self.assertIn("--bass-mode", res.stdout)
        self.assertIn("--list-bass-modes", res.stdout)

    def test_list_modes(self):
        res = self._run("--list-bass-modes")
        self.assertEqual(res.returncode, 0, res.stderr)
        for mode in player.BASS_BOOST_MODES:
            self.assertIn(mode, res.stdout)
        self.assertNotIn("Extreme", res.stdout)
        self.assertIn("66.7%", res.stdout.rstrip().splitlines()[-1])

    def test_set_mode_persists_when_no_daemon_runs(self):
        with tempfile.TemporaryDirectory() as cfg:
            target = Path(cfg) / "bass_boost"
            wrapper = (
                "import sys, pathlib, MP_Audio_Player as p\n"
                f"p.BASS_BOOST_FILE = pathlib.Path({str(target)!r})\n"
                # A daemon left behind by another test module would take the IPC
                # path and write its own config, so pin the no-daemon branch.
                "p.is_player_daemon_running = lambda: False\n"
                "sys.argv = ['MP_Audio_Player.py', '--bass-mode', 'high']\n"
                "try:\n    p.main()\nexcept SystemExit:\n    pass\n"
            )
            res = subprocess.run(
                [sys.executable, "-c", wrapper], cwd=str(BASE_DIR),
                capture_output=True, text=True, timeout=60,
                env={**os.environ, "MP_DISABLE_AUDIO_OUTPUT": "1",
                     "MP_DISABLE_BLUETOOTH_MONITOR": "1",
                     "XDG_CONFIG_HOME": cfg},
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), "High")

    def test_unknown_mode_is_rejected(self):
        with tempfile.TemporaryDirectory() as cfg:
            wrapper = (
                "import sys, pathlib, MP_Audio_Player as p\n"
                f"p.BASS_BOOST_FILE = pathlib.Path({str(Path(cfg) / 'bass_boost')!r})\n"
                "p.is_player_daemon_running = lambda: False\n"
                "sys.argv = ['MP_Audio_Player.py', '--bass-mode', 'warp']\n"
                "try:\n    p.main()\nexcept SystemExit as exc:\n    print('EXIT', exc.code)\n"
            )
            res = subprocess.run(
                [sys.executable, "-c", wrapper], cwd=str(BASE_DIR),
                capture_output=True, text=True, timeout=60,
                env={**os.environ, "MP_DISABLE_AUDIO_OUTPUT": "1",
                     "MP_DISABLE_BLUETOOTH_MONITOR": "1",
                     "XDG_CONFIG_HOME": cfg},
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("EXIT 1", res.stdout)


if __name__ == "__main__":
    unittest.main()
