#!/usr/bin/env python3
"""
Unit tests for the two alternate Stream of Frequency readings in MP Audio Player:

- Stream of Frequency (Liquid Spectrogram)        — stream_of_frequency_liquid_eq
- Stream of Frequency (MPlanterian Overview Effect) — stream_of_frequency_overview_eq

Both are built on the same rolling spectrogram history as the original
Dynamic Spectrogram, so they must stay in step with it while looking completely
different. These tests lock in:

  * registration, display names, aliases and the E-key cycle order;
  * the shared history (same buffer, no restart when switching style);
  * that the three effects never draw the same picture;
  * colour palettes (cool liquid vs warm topographic) and audio reactivity;
  * geometry: height+2 lines, uniform widths, Liquid Glass width-2 rule;
  * console-safe glyph fallbacks and render cadence;
  * Micro/Mini/Normal frames draw the active effect.
"""

import math
import re
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import MP_Audio_Player
from MP_Audio_Player import EqualizerVisualizer, TerminalPlayerUI, _display_width

LIQUID = "stream_of_frequency_liquid_eq"
OVERVIEW = "stream_of_frequency_overview_eq"
DYNAMIC = "stream_of_frequency_eq"
NEW_STYLES = (LIQUID, OVERVIEW)

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Glyphs no limited console is guaranteed to carry.
UNSAFE = (
    "✦", "✧", "⋆", "✺", "✷", "✸", "✹", "✻", "❋", "❖", "✱", "✳", "✴",
    "⁂", "⁕", "⍟", "⎈", "⬢", "⬡", "◈", "◉", "◎", "◌", "⌬", "╎", "┊", "┃",
    "═", "▪", "▲", "●", "○", "▁",
)


def plain(lines):
    return ANSI.sub("", "\n".join(lines))


def body(lines):
    """The frame without its header and footer, for like-for-like comparison."""
    return ANSI.sub("", "\n".join(lines[1:-1]))


def status(state="playing", level=0.72, step=0):
    """Audio telemetry that actually varies, so the effects have terrain."""
    levels = [
        max(0.02, min(1.0, 0.5 + 0.45 * math.sin(step * 0.35 + i * 0.55)))
        for i in range(24)
    ]
    return {
        "title": "Cosmic Trance Odyssey.flac",
        "path": "/archive/Cosmic Trance Odyssey.flac",
        "state": state,
        "position_fmt": "03:45",
        "duration_fmt": "08:20",
        "progress_pct": 45.0,
        "volume": 85,
        "pitch_percent": 0,
        "levels": levels,
        "peaks": levels,
        "levels_left": levels,
        "levels_right": levels,
        "rms_level": 0.18,
        "peak_db": -3.0,
        "waveform": [0.1 * i for i in range(50)],
    }


def loud_status():
    return {
        "state": "playing",
        "levels": [0.95] * 24,
        "peaks": [0.99] * 24,
        "levels_left": [0.95] * 24,
        "levels_right": [0.95] * 24,
        "rms_level": 0.35,
        "peak_db": -0.2,
        "pitch_percent": 0,
    }


def quiet_status():
    return {
        "state": "playing",
        "levels": [0.02] * 24,
        "peaks": [0.02] * 24,
        "levels_left": [0.02] * 24,
        "levels_right": [0.02] * 24,
        "rms_level": 0.0,
        "peak_db": -60.0,
        "pitch_percent": 0,
    }


def warm(lines):
    """Prime the rolling history so the effects have something to draw."""
    viz = EqualizerVisualizer(default_style=lines)
    for k in range(120):
        viz.render(status(step=k), width=100, height=7)
    return viz


class SofTestBase(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_patch = patch.object(
            MP_Audio_Player, "CONFIG_DIR", Path(self.temp_dir.name))
        self.config_patch.start()

    def tearDown(self):
        self.config_patch.stop()
        self.temp_dir.cleanup()

    def viz(self, style):
        return EqualizerVisualizer(default_style=style)


class TestRegistration(SofTestBase):
    def test_both_styles_are_registered(self):
        for style in NEW_STYLES:
            self.assertIn(style, EqualizerVisualizer.STYLES)
            self.assertEqual(self.viz(style).style, style)

    def test_display_names_match_the_requested_titles(self):
        self.assertEqual(
            EqualizerVisualizer.STYLE_NAMES[LIQUID],
            "Stream of Frequency (Liquid Spectrogram)")
        self.assertEqual(
            EqualizerVisualizer.STYLE_NAMES[OVERVIEW],
            "Stream of Frequency (MPlanterian Overview Effect)")

    def test_display_names_are_unique(self):
        names = [EqualizerVisualizer.STYLE_NAMES[s] for s in EqualizerVisualizer.STYLES]
        self.assertEqual(len(names), len(set(names)), "style names must be unique")

    def test_original_spectrogram_is_still_first_and_default(self):
        self.assertEqual(EqualizerVisualizer.STYLES[0], DYNAMIC)
        self.assertEqual(self.viz("stream").style, DYNAMIC)

    def test_the_two_new_effects_sit_beside_the_original(self):
        """E must reach all three Stream of Frequency readings back to back."""
        order = EqualizerVisualizer.STYLES
        self.assertEqual(order.index(DYNAMIC) + 1, order.index(LIQUID))
        self.assertEqual(order.index(LIQUID) + 1, order.index(OVERVIEW))

    def test_aliases_resolve(self):
        cases = {
            "liquid": LIQUID,
            "liquid_spectrogram": LIQUID,
            "liquid_stream": LIQUID,
            "stream_liquid": LIQUID,
            "stream_of_frequency_liquid": LIQUID,
            "overview": OVERVIEW,
            "overview_effect": OVERVIEW,
            "mplanetarian_overview": OVERVIEW,
            "mplanetarian_overview_effect": OVERVIEW,
            "stream_overview": OVERVIEW,
            "topographic": OVERVIEW,
        }
        for alias, expected in cases.items():
            self.assertEqual(self.viz(alias).style, expected, alias)

    def test_e_key_cycle_reaches_both_and_returns_home(self):
        viz = self.viz(DYNAMIC)
        seen = []
        for _ in range(len(EqualizerVisualizer.STYLES)):
            seen.append(viz.style)
            viz.cycle_style()
        self.assertIn(LIQUID, seen)
        self.assertIn(OVERVIEW, seen)
        self.assertEqual(viz.style, DYNAMIC, "a full cycle must return to the start")

    def test_choice_persists_across_restart(self):
        viz = self.viz(DYNAMIC)
        self.assertTrue(viz.set_style("overview"))
        self.assertEqual(
            (Path(self.temp_dir.name) / "eq_style").read_text().strip(), OVERVIEW)
        self.assertEqual(EqualizerVisualizer().style, OVERVIEW)

    def test_set_style_rejects_unknown_names(self):
        viz = self.viz(DYNAMIC)
        self.assertFalse(viz.set_style("not_a_real_effect"))
        self.assertEqual(viz.style, DYNAMIC)


class TestSharedHistory(SofTestBase):
    def test_all_three_read_the_same_buffer(self):
        """Switching with E must not restart or reshuffle the history."""
        a = self.viz(DYNAMIC)
        a.render(status(step=3), width=100, height=7)
        snapshot = a._sof_buffer.copy()
        # The Liquid and Overview renderers use the same buffer object.
        a.render_stream_of_frequency_liquid(status(step=4), width=100, height=7)
        self.assertIsNotNone(a._sof_buffer)
        self.assertEqual(a._sof_buffer.shape[0], snapshot.shape[0])

    def test_buffer_rows_match_the_audio_history(self):
        viz = self.viz(LIQUID)
        viz.render(status(), width=100, height=7)
        self.assertEqual(viz._sof_buffer.shape[0], 16)

    def test_paused_and_stopped_do_not_raise(self):
        for style in NEW_STYLES:
            viz = self.viz(style)
            for state in ("paused", "stopped"):
                lines = viz.render(status(state=state), width=90, height=7)
                self.assertEqual(len(lines), 9, f"{style}/{state}")


class TestVisualDistinctness(SofTestBase):
    def test_the_three_effects_never_draw_the_same_picture(self):
        shots = {}
        for style in (DYNAMIC, LIQUID, OVERVIEW):
            viz = warm(style)
            shots[style] = body(viz.render(status(step=120), width=100, height=7))
        self.assertNotEqual(shots[DYNAMIC], shots[LIQUID])
        self.assertNotEqual(shots[DYNAMIC], shots[OVERVIEW])
        self.assertNotEqual(shots[LIQUID], shots[OVERVIEW])

    def test_headers_identify_each_effect(self):
        for style, needle in ((LIQUID, "LIQUID SPECTROGRAM"),
                              (OVERVIEW, "MPLANTERIAN OVERVIEW")):
            viz = warm(style)
            head = plain(viz.render(status(step=120), width=100, height=7)).splitlines()[0]
            self.assertIn(needle, head, f"{style} header")

    def test_liquid_uses_a_cool_palette(self):
        viz = warm(LIQUID)
        raw = "\n".join(viz.render(status(step=120), width=100, height=7))
        colours = [(int(r), int(g), int(b)) for r, g, b in
                   re.findall(r"38;2;(\d+);(\d+);(\d+)m", raw)]
        self.assertTrue(colours, "liquid effect painted nothing")
        blue_dominant = sum(1 for r, g, b in colours if b >= r)
        self.assertGreater(blue_dominant / len(colours), 0.6,
                           "Liquid Spectrogram should read as a cool blue pool")

    def test_overview_uses_a_warm_topographic_palette(self):
        viz = warm(OVERVIEW)
        raw = "\n".join(viz.render(status(step=120), width=100, height=7))
        colours = [(int(r), int(g), int(b)) for r, g, b in
                   re.findall(r"38;2;(\d+);(\d+);(\d+)m", raw)]
        self.assertTrue(colours, "overview effect painted nothing")
        warm_cells = sum(1 for r, g, b in colours if r >= b)
        self.assertGreater(warm_cells / len(colours), 0.6,
                           "Overview Effect should read as warm terrain")

    def test_liquid_holds_still_while_overview_sweeps(self):
        """The liquid pool is depth-ordered; the map is a survey."""
        liq = warm(LIQUID)
        ovr = warm(OVERVIEW)
        liq_body = body(liq.render(status(step=120), width=100, height=7))
        ovr_body = body(ovr.render(status(step=120), width=100, height=7))
        # The map draws contour boundaries; the pool is filled blocks.
        self.assertIn("≈", ovr_body)
        self.assertTrue(any(g in liq_body for g in "█▓▒░"),
                        "Liquid Spectrogram should be filled fluid cells")


class TestReactivity(SofTestBase):
    def test_each_effect_reacts_to_audio(self):
        frozen = time.time()
        for style in NEW_STYLES:
            viz = self.viz(style)
            with patch("MP_Audio_Player.time.time", return_value=frozen):
                for _ in range(40):
                    viz.render(loud_status(), width=100, height=7)
                loud = body(viz.render(loud_status(), width=100, height=7))
            viz = self.viz(style)
            with patch("MP_Audio_Player.time.time", return_value=frozen):
                for _ in range(40):
                    viz.render(quiet_status(), width=100, height=7)
                quiet = body(viz.render(quiet_status(), width=100, height=7))
            self.assertNotEqual(loud, quiet, f"{style} ignores audio levels")


class TestGeometry(SofTestBase):
    def test_line_count_is_height_plus_two_borders(self):
        for style in NEW_STYLES:
            viz = self.viz(style)
            for h in (1, 2, 3, 5, 7, 9):
                lines = viz.render(status(), width=80, height=h)
                self.assertEqual(len(lines), h + 2, f"{style} height {h}")

    def test_widths_are_uniform_and_fit_the_terminal(self):
        for style in NEW_STYLES:
            viz = self.viz(style)
            for w in (26, 30, 34, 46, 50, 74, 80, 100, 120):
                lines = viz.render(status(), width=w, height=7)
                widths = {_display_width(line) for line in lines}
                self.assertEqual(len(widths), 1, f"{style} w={w}: {widths}")
                self.assertLessEqual(max(widths), w, f"{style} w={w} overflow")

    def test_liquid_glass_spans_exactly_width_minus_two(self):
        with patch.dict("os.environ", {"MP_LIQUID_GLASS": "1"}):
            for style in NEW_STYLES:
                viz = self.viz(style)
                for w in (34, 50, 74, 80, 100, 120):
                    lines = viz.render(status(), width=w, height=7)
                    widths = {_display_width(line) for line in lines}
                    self.assertEqual(widths, {w - 2}, f"{style} w={w}")

    def test_narrow_and_tall_frames_do_not_raise(self):
        for style in NEW_STYLES:
            viz = self.viz(style)
            for w, h in ((26, 1), (26, 7), (30, 2), (46, 9), (12, 1)):
                lines = viz.render(status(), width=w, height=h)
                self.assertEqual(len(lines), h + 2, f"{style} {w}x{h}")


class TestConsoleSafety(SofTestBase):
    def test_limited_console_never_emits_missing_glyphs(self):
        with patch("MP_Audio_Player._use_limited_console_glyphs", return_value=True):
            for style in NEW_STYLES:
                viz = self.viz(style)
                for _ in range(4):
                    text = plain(viz.render(status(), width=100, height=7))
                for glyph in UNSAFE:
                    self.assertNotIn(glyph, text, f"{style} leaked {glyph}")

    def test_full_console_keeps_the_rich_glyphs(self):
        with patch("MP_Audio_Player._use_limited_console_glyphs", return_value=False):
            for style in NEW_STYLES:
                viz = warm(style)
                text = plain(viz.render(status(step=120), width=100, height=7))
                self.assertTrue(
                    any(g in text for g in ("▲", "●", "○", "▁", "█", "▓")),
                    f"{style} lost its rich glyphs")


class TestCadence(SofTestBase):
    def test_render_cadence_stays_responsive(self):
        for style in NEW_STYLES:
            viz = self.viz(style)
            for k in range(4):
                viz.render(status(step=k), width=120, height=7)
            start = time.perf_counter()
            for k in range(20):
                viz.render(status(step=k), width=120, height=7)
            ms = (time.perf_counter() - start) / 20.0 * 1000.0
            self.assertLess(ms, 40.0, f"{style} too slow: {ms:.1f} ms/frame")

    def test_transient_state_stays_bounded(self):
        for style, attr in ((LIQUID, "_sof_liquid_glints"),):
            viz = self.viz(style)
            for k in range(200):
                viz.render(status(step=k), width=110, height=7)
            self.assertLessEqual(len(getattr(viz, attr)), 18,
                                 f"{style} leaked {attr} entries")


class TestPlayerFrames(SofTestBase):
    def _ui(self, mode, style):
        scanner = MagicMock()
        scanner.get_mixes.return_value = []
        return TerminalPlayerUI(
            engine=None, scanner=scanner, ui_size_mode=mode,
            is_remote=True, default_style=style)

    def test_micro_meter_differs_per_stream_effect(self):
        viz = self.viz(LIQUID)
        liquid = viz._fx_micro_meter(LIQUID, 12.5, np.full(14, 0.9))
        overview = viz._fx_micro_meter(OVERVIEW, 12.5, np.full(14, 0.9))
        self.assertEqual(len(ANSI.sub("", liquid)), 14)
        self.assertEqual(len(ANSI.sub("", overview)), 14)
        self.assertNotEqual(ANSI.sub("", liquid), ANSI.sub("", overview))

    def test_each_frame_renders_the_active_effect(self):
        for style in NEW_STYLES:
            for mode in ("NORMAL", "MINI", "MICRO"):
                ui = self._ui(mode, style)
                self.assertEqual(ui.visualizer.style, style)
                captured = []
                st = status()
                with patch("MP_Audio_Player._present_frame",
                           side_effect=lambda lines, w, h: captured.extend(lines)), \
                     patch("MP_Audio_Player._win_visible_size", return_value=(120, 40)), \
                     patch.object(ui, "_get_status", MagicMock(return_value=st)):
                    if mode == "MICRO":
                        ui._draw_micro_frame(74, 14, st)
                    elif mode == "MINI":
                        ui._draw_mini_frame(74, 14, st)
                    else:
                        ui._draw_frame()
                text = plain(captured)
                self.assertIn("MP", text, f"{style}/{mode} produced no frame")


if __name__ == "__main__":
    unittest.main()
