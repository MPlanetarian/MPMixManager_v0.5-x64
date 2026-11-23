#!/usr/bin/env python3
"""The Stream of Frequency equalisers must stay smooth in a full screen window.

A maximised Liquid Glass window is roughly 300 columns by 95 rows. At that size
the players used to spend most of a frame formatting and shipping the picture:
every coloured cell carried its own SGR pair, and two whole-screen text passes
re-parsed identical lines on every frame. The animation visibly stuttered.

These tests pin the four things that made it smooth, without re-measuring exact
timings (which depend on the machine):

  * a run of one colour emits one escape, not one per cell;
  * a space does not split a colour run;
  * bold is cleared when a bold palette entry is followed by a plain one;
  * the per-frame text passes give the same answer as the straightforward
    implementation they replaced, and are cached.

A cadence test then renders full-screen frames and keeps the median inside a
60 fps budget with generous headroom, so a regression that reintroduces the
per-cell emission fails here rather than on the user's display.
"""

from __future__ import annotations

import os
import re
import statistics
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import MP_Audio_Player
from MP_Audio_Player import (
    BOLD, NC, EqualizerVisualizer, TerminalPlayerUI, _display_width,
)

SGR = re.compile(r"\x1b\[([0-9;]*)m")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# The true full screen geometry measured on the 2784x1740 panel.
FULLSCREEN = (309, 96)


def status(step=0):
    """Audio telemetry that actually varies, so every effect has something to draw."""
    levels = [
        max(0.02, min(1.0, 0.5 + 0.45 * np.sin(step * 0.35 + i * 0.55)))
        for i in range(24)
    ]
    return {
        "title": "MPlanetarian - Stream of Frequency 163.flac",
        "path": "/archive/Stream_of_Frequency_163.flac",
        "state": "playing",
        "position_fmt": "31:12",
        "duration_fmt": "120:00",
        "progress_pct": 26.0,
        "volume": 85,
        "pitch_percent": 0,
        "levels": levels,
        "peaks": levels,
        "levels_left": levels,
        "levels_right": levels,
        "rms_level": 0.22,
        "peak_db": -6.0,
        "sample_rate": 44100,
        "channels": 2,
    }


def escape_count(text):
    return len(re.findall(r"\x1b\[", text))


class EqualizerTestBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_patch = patch.object(
            MP_Audio_Player, "CONFIG_DIR", Path(self.temp_dir.name))
        self.config_patch.start()

    def tearDown(self):
        self.config_patch.stop()
        self.temp_dir.cleanup()

    def viz(self, style):
        return EqualizerVisualizer(default_style=style)


class TestFxLinesRunLength(EqualizerTestBase):
    """`_fx_lines` is shared by the Overview and Rainbow effects."""

    def test_one_escape_per_run_not_per_cell(self):
        viz = self.viz("stream_of_frequency_overview_eq")
        gc = [["█"] * 80]
        gcol = [["\033[38;2;10;20;30m"] * 80]
        lines = viz._fx_lines(gc, gcol, 80, "h", "f")
        body = lines[1]
        # The single colour run should be described once. The frame adds its
        # own border escapes, so allow those but not one per cell.
        self.assertLess(escape_count(body), 10,
                        f"80 cells of one colour emitted {escape_count(body)} escapes")

    def test_a_space_does_not_split_a_run(self):
        viz = self.viz("stream_of_frequency_overview_eq")
        gc = [["█", " ", "█"]]
        gcol = [["\033[38;2;10;20;30m", "", "\033[38;2;10;20;30m"]]
        line = viz._fx_lines(gc, gcol, 3, "h", "f")[1]
        # One escape for the colour, none for the gap between the blocks.
        self.assertEqual(line.count("\033[38;2;10;20;30m"), 1, line)

    def test_bold_does_not_leak_into_the_next_plain_cell(self):
        """A bold palette entry followed by a plain one must clear bold."""
        viz = self.viz("stream_of_frequency_overview_eq")
        gc = [["A", "B"]]
        gcol = [["\033[1;38;2;9;9;9m", "\033[38;2;1;2;3m"]]
        line = viz._fx_lines(gc, gcol, 2, "h", "f")[1]
        self.assertIn("\033[22m", line,
                      "bold was not cleared before the plain cell")

    def test_visible_glyphs_and_colours_are_preserved(self):
        viz = self.viz("stream_of_frequency_overview_eq")
        gc = [["█", "▓", " ", "▒", "░"]]
        gcol = [["\033[38;2;1;1;1m", "\033[38;2;2;2;2m", "",
                 "\033[38;2;3;3;3m", "\033[38;2;4;4;4m"]]
        text = ANSI.sub("", viz._fx_lines(gc, gcol, 5, "h", "f")[1])
        for glyph in ("█", "▓", "▒", "░"):
            self.assertIn(glyph, text)

    def test_limited_console_still_substitutes_glyphs(self):
        viz = self.viz("stream_of_frequency_overview_eq")
        with patch("MP_Audio_Player._use_limited_console_glyphs", return_value=True):
            gc = [["▲", "●", "○", "▁"]]
            gcol = [["\033[38;2;1;1;1m"] * 4]
            text = ANSI.sub("", viz._fx_lines(gc, gcol, 4, "h", "f")[1])
        for rich in ("▲", "●", "○", "▁"):
            self.assertNotIn(rich, text)


class TestPerFrameTextPasses(EqualizerTestBase):
    """The two whole-screen text passes must stay correct and cheap."""

    def test_strip_bold_matches_the_plain_implementation(self):
        import functools

        def reference(s):
            def repl(m):
                codes = [c for c in m.group(1).split(";") if c != "1"]
                return f"\033[{';'.join(codes)}m" if codes else ""
            return re.sub(r"\033\[([0-9;]*)m", repl, s)

        cases = [
            "",
            "plain text",
            "\033[1mBold\033[0m",
            "\033[1;38;2;1;2;3mLeading bold\033[0m",
            "\033[48;2;1;2;3;38;2;4;5;6m chip \033[0m",
            "\033[38;2;1;2;3;1mMid-list bold\033[0m",
            "\033[2m\033[1mDim bold\033[0m",
            "\033[1m\033[1m\033[0m\033[1m",
            "digits 1 and 21 and 10 must not change",
        ]
        for case in cases:
            self.assertEqual(MP_Audio_Player._strip_bold_ansi(case),
                             reference(case), repr(case))

    def test_strip_bold_is_cached_for_identical_lines(self):
        line = "\033[1m\033[38;2;0;229;255m" + "x" * 200 + "\033[0m"
        first = MP_Audio_Player._strip_bold_ansi(line)
        second = MP_Audio_Player._strip_bold_ansi(line)
        self.assertEqual(first, second)
        # A cache hit returns the very same string object.
        self.assertIs(first, second)

    def test_guard_lifts_dim_ink_and_keeps_black(self):
        guard = MP_Audio_Player._guard_visualizer_text
        dim = "\033[2m\033[38;2;20;30;40mDim\033[0m"
        black = "\033[38;2;0;0;0mNotice\033[0m"
        guarded = guard(dim)
        self.assertNotIn("\033[2m", guarded)
        self.assertNotIn("38;2;20;30;40", guarded)
        self.assertEqual(guard(black), black,
                         "pure black is deliberate and must survive")

    def test_guard_keeps_opaque_button_chips(self):
        guard = MP_Audio_Player._guard_visualizer_text
        button = "\033[48;2;255;196;0;38;2;32;18;52m  FAVORITES  \033[0m"
        self.assertEqual(guard(button), button)

    def test_guard_is_cached_for_identical_lines(self):
        line = "\033[38;2;10;20;30munchanging row\033[0m"
        first = MP_Audio_Player._guard_visualizer_text(line)
        second = MP_Audio_Player._guard_visualizer_text(line)
        self.assertIs(first, second)


class TestSpectrogramGrid(EqualizerTestBase):
    def test_fullscreen_dynamic_spectrogram_draws_every_row(self):
        viz = self.viz("stream_of_frequency_eq")
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}):
            for step in range(30):
                viz.render(status(step), width=FULLSCREEN[0], height=7)
            lines = viz.render(status(30), width=FULLSCREEN[0], height=7)
        self.assertEqual(len(lines), 9)
        widths = {_display_width(line) for line in lines}
        self.assertEqual(len(widths), 1, f"ragged frame: {widths}")
        self.assertLessEqual(max(widths), FULLSCREEN[0])

    def test_energy_tiers_still_choose_their_own_glyphs(self):
        """The vectorised fill must keep the quiet/loud glyph scale."""
        viz = self.viz("stream_of_frequency_eq")
        with patch("MP_Audio_Player.time.time", return_value=1000.0):
            for _ in range(60):
                viz.render(status(), width=120, height=7)
            loud = dict(status(), levels=[0.95] * 24, levels_left=[0.95] * 24,
                        levels_right=[0.95] * 24, rms_level=0.4, peak_db=-0.2)
            loud_lines = viz.render(loud, width=120, height=7)
        text = ANSI.sub("", "\n".join(loud_lines))
        self.assertTrue(any(g in text for g in "█▓"), "loud frame drew no solid cells")


class TestFullscreenCadence(EqualizerTestBase):
    """Median whole-frame cost at true full screen, with headroom to spare."""

    def _ui(self, style):
        scanner = MagicMock()
        scanner.get_mixes.return_value = []
        return TerminalPlayerUI(
            engine=None, scanner=scanner, ui_size_mode="NORMAL",
            is_remote=True, default_style=style)

    def _median_frame_ms(self, style, width, height, frames=40):
        ui = self._ui(style)
        st = status()
        captured = []
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}), \
             patch("MP_Audio_Player._present_frame",
                   side_effect=lambda lines, w, h: captured.extend(lines)), \
             patch("MP_Audio_Player._win_visible_size", return_value=(width, height)), \
             patch.object(ui, "_get_status", MagicMock(return_value=st)):
            for step in range(60):  # fill the spectrogram history
                st = status(step)
                ui._draw_frame()
            samples = []
            for step in range(frames):
                t0 = time.perf_counter()
                ui._draw_frame()
                samples.append((time.perf_counter() - t0) * 1000.0)
        return statistics.median(samples)

    def test_stream_of_frequency_effects_hold_a_60fps_budget(self):
        for style in ("stream_of_frequency_eq", "stream_of_frequency_overview_eq"):
            ms = self._median_frame_ms(style, *FULLSCREEN)
            self.assertLess(
                ms, 16.7,
                f"{style} took {ms:.2f} ms/frame at {FULLSCREEN[0]}x{FULLSCREEN[1]}",
            )

    def test_overview_spectrogram_ships_a_small_payload(self):
        """The run-length emission is what keeps a full screen VTE cheap."""
        viz = self.viz("stream_of_frequency_overview_eq")
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}), \
             patch("MP_Audio_Player.time.time", return_value=1000.0):
            for step in range(80):  # fill the spectrogram history
                viz.render(status(step), width=FULLSCREEN[0], height=7)
            lines = viz.render(status(80), width=FULLSCREEN[0], height=7)
        payload = sum(len(line) for line in lines)
        self.assertLess(
            payload, 12 * 1024,
            f"the Overview Spectrogram wrote {payload / 1024:.1f} KiB for one frame",
        )

    def test_overview_effect_ships_a_small_payload(self):
        viz = self.viz("stream_of_frequency_overview_eq")
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}), \
             patch("MP_Audio_Player.time.time", return_value=1000.0):
            for step in range(80):
                viz.render(status(step), width=FULLSCREEN[0], height=7)
            lines = viz.render(status(80), width=FULLSCREEN[0], height=7)
        payload = sum(len(line) for line in lines)
        self.assertLess(
            payload, 12 * 1024,
            f"the Overview Effect wrote {payload / 1024:.1f} KiB for one frame",
        )


class TestDynamicSpectrogramUsesTheWholeHeight(EqualizerTestBase):
    """A seven-row panel must show the top frequency band, not stop at band 13."""

    def test_the_top_row_is_lit_by_the_highest_band(self):
        viz = self.viz("stream_of_frequency_eq")
        only_top = status()
        only_top["levels"] = [0.0] * 23 + [1.0]
        only_top["levels_left"] = list(only_top["levels"])
        only_top["levels_right"] = list(only_top["levels"])
        with patch("MP_Audio_Player.time.time", return_value=1000.0):
            for _ in range(160):
                viz.render(only_top, width=40, height=5)
            lines = viz.render(only_top, width=40, height=5)
        body = [ANSI.sub("", line) for line in lines[1:-1]]
        self.assertEqual(len(body), 5)
        self.assertGreater(
            sum(1 for ch in body[0] if ch not in " │"), 0,
            "the top row never read the highest frequency band: " + repr(body[0]),
        )
        # Particles may drift into other rows; only the top band is asserted:
        # the old nearest-row lookup never read it at this height.



if __name__ == "__main__":
    unittest.main()
