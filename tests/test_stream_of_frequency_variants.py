#!/usr/bin/env python3
"""
Unit tests for the Stream of Frequency readings in MP Audio Player.

The second reading, **Stream of Frequency (Liquid Spectrogram)**
(``stream_of_frequency_liquid_eq``), was removed because it looked bad. The
Dynamic Spectrogram stays the default, and the MPlanterian Overview Effect
remains the second selectable reading.

These tests lock in:

  * the Liquid preset is gone from the style list, names and aliases, and a
    saved ``eq_style`` naming it falls back to the Dynamic Spectrogram;
  * registration, display names, aliases and the E-key cycle;
  * the shared spectrogram history;
  * that Dynamic and Overview never draw the same picture;
  * geometry, the Overview palette and Micro-mode miniature.
"""

import math
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import MP_Audio_Player
from MP_Audio_Player import EqualizerVisualizer

DYNAMIC = "stream_of_frequency_eq"
OVERVIEW = "stream_of_frequency_overview_eq"
REMOVED = "stream_of_frequency_liquid_eq"
REMOVED_ALIASES = (
    "liquid", "liquid_spectrogram", "liquid_stream", "stream_liquid",
    "stream_of_frequency_liquid", "liquid_pool",
)

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def plain(lines):
    return ANSI.sub("", "\n".join(lines))


def status(state="playing", step=0):
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


class TestRemovedLiquidPreset(SofTestBase):
    def test_the_liquid_preset_is_gone(self):
        self.assertNotIn(REMOVED, EqualizerVisualizer.STYLES)
        self.assertNotIn(REMOVED, EqualizerVisualizer.STYLE_NAMES)
        self.assertNotIn(
            "Stream of Frequency (Liquid Spectrogram)",
            EqualizerVisualizer.STYLE_NAMES.values(),
        )

    def test_its_aliases_fall_back_to_the_dynamic_spectrogram(self):
        for alias in REMOVED_ALIASES:
            self.assertEqual(self.viz(alias).style, DYNAMIC, alias)

    def test_a_saved_liquid_choice_falls_back(self):
        viz = self.viz(DYNAMIC)
        self.assertTrue(viz.set_style(DYNAMIC))
        (Path(self.temp_dir.name) / "eq_style").write_text(REMOVED + "\n")
        self.assertEqual(EqualizerVisualizer().style, DYNAMIC)


class TestRegistration(SofTestBase):
    def test_dynamic_is_first_and_default(self):
        self.assertEqual(EqualizerVisualizer.STYLES[0], DYNAMIC)
        self.assertEqual(self.viz("stream").style, DYNAMIC)
        self.assertEqual(self.viz("not_a_real_effect").style, DYNAMIC)

    def test_overview_sits_beside_the_dynamic_spectrogram(self):
        order = EqualizerVisualizer.STYLES
        self.assertEqual(order.index(DYNAMIC) + 1, order.index(OVERVIEW))

    def test_display_names(self):
        self.assertEqual(
            EqualizerVisualizer.STYLE_NAMES[DYNAMIC],
            "Stream of Frequency (Dynamic Spectrogram)")
        self.assertEqual(
            EqualizerVisualizer.STYLE_NAMES[OVERVIEW],
            "Stream of Frequency (MPlanterian Overview Effect)")
        names = [EqualizerVisualizer.STYLE_NAMES[s] for s in EqualizerVisualizer.STYLES]
        self.assertEqual(len(names), len(set(names)))

    def test_aliases_resolve(self):
        cases = {
            "overview": OVERVIEW,
            "overview_effect": OVERVIEW,
            "mplanetarian_overview": OVERVIEW,
            "mplanetarian_overview_effect": OVERVIEW,
            "stream_overview": OVERVIEW,
            "topographic": OVERVIEW,
        }
        for alias, expected in cases.items():
            self.assertEqual(self.viz(alias).style, expected, alias)

    def test_e_key_cycle_reaches_overview_and_returns_home(self):
        viz = self.viz(DYNAMIC)
        seen = []
        for _ in range(len(EqualizerVisualizer.STYLES)):
            seen.append(viz.style)
            viz.cycle_style()
        self.assertIn(OVERVIEW, seen)
        self.assertEqual(viz.style, DYNAMIC)

    def test_choice_persists_across_restart(self):
        viz = self.viz(DYNAMIC)
        self.assertTrue(viz.set_style("overview"))
        self.assertEqual(
            (Path(self.temp_dir.name) / "eq_style").read_text().strip(), OVERVIEW)
        self.assertEqual(EqualizerVisualizer().style, OVERVIEW)


class TestSharedHistory(SofTestBase):
    def test_switching_keeps_one_history_buffer(self):
        viz = self.viz(DYNAMIC)
        viz.render(status(step=3), width=100, height=7)
        viz.set_style(OVERVIEW)
        viz.render(status(step=4), width=100, height=7)
        self.assertIsNotNone(viz._sof_buffer)
        self.assertEqual(viz._sof_buffer.shape[0], 16)

    def test_paused_and_stopped_do_not_raise(self):
        for state in ("paused", "stopped"):
            viz = self.viz(OVERVIEW)
            lines = viz.render(status(state=state), width=100, height=7)
            self.assertTrue(lines)


class TestTheOverviewPicture(SofTestBase):
    def test_dynamic_and_overview_draw_different_pictures(self):
        shots = {}
        for style in (DYNAMIC, OVERVIEW):
            viz = self.viz(style)
            for step in range(80):
                viz.render(status(step=step), width=100, height=7)
            shots[style] = plain(viz.render(status(step=80), width=100, height=7))
        self.assertNotEqual(shots[DYNAMIC], shots[OVERVIEW])

    def test_overview_header_and_geometry(self):
        viz = self.viz(OVERVIEW)
        for step in range(40):
            viz.render(status(step=step), width=100, height=7)
        lines = viz.render(status(step=41), width=100, height=7)
        self.assertEqual(len(lines), 7 + 2)
        self.assertIn("OVERVIEW", plain([lines[0]]).upper())
        widths = {MP_Audio_Player._display_width(line) for line in lines}
        self.assertEqual(len(widths), 1, widths)

    def test_micro_meter_is_fourteen_cells_and_differs_from_dynamic(self):
        viz = self.viz(OVERVIEW)
        over = viz._fx_micro_meter(OVERVIEW, 12.5, np.full(14, 0.9))
        dyn = viz._fx_micro_meter(DYNAMIC, 12.5, np.full(14, 0.9))
        self.assertEqual(len(ANSI.sub("", over)), 14)
        self.assertNotEqual(ANSI.sub("", over), ANSI.sub("", dyn))


if __name__ == "__main__":
    unittest.main()
