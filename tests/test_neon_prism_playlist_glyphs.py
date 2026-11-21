#!/usr/bin/env python3
"""Neon Chroma Prism bar caps and the highlighted playlist mark use console-safe glyphs.

Git Bash on Windows draws with Lucida Console or Consolas. Those fonts do not
include eighth-block caps, the prism peak marks, or U+25B6 ▶. Missing glyphs
show up as the wrong symbol at the top of each bar and beside the highlighted mix.
"""

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import MP_Audio_Player
from MP_Audio_Player import EqualizerVisualizer, TerminalPlayerUI, PLAYLIST_ACTIVE_MARK, _use_limited_console_glyphs


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


class TestNeonPrismPlaylistGlyphs(unittest.TestCase):
    def test_limited_console_bar_caps_and_peaks(self):
        viz = EqualizerVisualizer(style="neon_prism_eq")
        levels = np.full(16, 0.30)
        peaks = np.full(16, 0.91)
        with patch("MP_Audio_Player._use_limited_console_glyphs", return_value=True):
            lines = viz.render_neon_prism_eq(levels, levels, peaks, peaks, width=80, height=7)
        plain = strip_ansi("\n".join(lines))
        for missing in ("✦", "◆", "━", "▂", "▃", "▅", "▆", "▇", "▁"):
            self.assertNotIn(missing, plain)
        self.assertIn("▄", plain)
        self.assertIn("█", plain)
        self.assertIn("▲", plain)

    def test_full_console_keeps_eighth_caps(self):
        viz = EqualizerVisualizer(style="neon_prism_eq")
        levels = np.full(16, 0.30)
        peaks = np.full(16, 0.91)
        with patch("MP_Audio_Player._use_limited_console_glyphs", return_value=False):
            lines = viz.render_neon_prism_eq(levels, levels, peaks, peaks, width=80, height=7)
        plain = strip_ansi("\n".join(lines))
        self.assertIn("▁", plain)
        self.assertTrue(("✦" in plain) or ("◆" in plain))

    def test_highlighted_playlist_mark_is_console_pointer(self):
        mock_scanner = MagicMock()
        mock_scanner.get_mixes.return_value = [
            {"name": "Mix 01 - Astral Projection", "path": "/path/to/mix1.mp3", "folder": "Chill", "ext": "MP3", "size_mb": 12},
            {"name": "Mix 02 - Solar Eclipse", "path": "/path/to/mix2.mp3", "folder": "Trance", "ext": "MP3", "size_mb": 18},
        ]
        ui = TerminalPlayerUI(engine=None, scanner=mock_scanner, ui_size_mode="STANDARD", is_remote=True)
        ui.selected_row = 0
        st = {
            "title": "Mix 01 - Astral Projection",
            "path": "/path/to/mix1.mp3",
            "state": "playing",
            "index": 0,
            "position_fmt": "02:00",
            "duration_fmt": "60:00",
            "progress_pct": 3.3,
            "volume": 80,
            "pitch_percent": 0,
            "levels": [0.5] * 24,
            "peaks": [0.7] * 24,
            "levels_left": [0.5] * 24,
            "levels_right": [0.5] * 24,
            "peaks_left": [0.7] * 24,
            "peaks_right": [0.7] * 24,
            "rms_level": 0.1,
            "peak_db": -3.0,
        }
        captured = []
        with patch("MP_Audio_Player._present_frame", side_effect=lambda lines, w, h: captured.extend(lines)), \
             patch("MP_Audio_Player._win_visible_size", return_value=(100, 35)):
            ui._get_status = MagicMock(return_value=st)
            ui._draw_frame()
        rows = [line for line in captured if "│" in line and "Mix 01 - Astral Projection" in line]
        self.assertTrue(rows, "Highlighted playlist row was not drawn")
        row = rows[0]
        self.assertIn(PLAYLIST_ACTIVE_MARK, row)
        self.assertNotIn("▶", row)
        expected_mark = ">" if _use_limited_console_glyphs() else "\u25ba"
        self.assertEqual(PLAYLIST_ACTIVE_MARK, expected_mark)

    def test_narrow_playlist_rows_stay_inside_the_panel(self):
        """Long names, tabs and wide glyphs must not push the right border out."""
        long_name = "MPlanetarian \t – Resonant Fluid Dynamics ★ " + ("Very Long Mix Title " * 8)
        mock_scanner = MagicMock()
        mock_scanner.get_mixes.return_value = [
            {"name": long_name, "path": "/path/to/mix1.mp3", "folder": "Chill", "ext": "MP3", "size_mb": 12},
            {"name": "Short", "path": "/path/to/mix2.flac", "folder": "Trance", "ext": "FLAC", "size_mb": 18},
        ]
        ui = TerminalPlayerUI(engine=None, scanner=mock_scanner, ui_size_mode="STANDARD", is_remote=True)
        st = {
            "title": long_name, "path": "/path/to/mix1.mp3", "state": "playing",
            "position_fmt": "02:00", "duration_fmt": "60:00", "progress_pct": 3.3,
            "volume": 80, "pitch_percent": 0, "levels": [0.5] * 24, "peaks": [0.7] * 24,
        }
        captured = []
        with patch("MP_Audio_Player._present_frame", side_effect=lambda lines, w, h: captured.extend(lines)), \
             patch("MP_Audio_Player._win_visible_size", return_value=(70, 35)):
            ui._get_status = MagicMock(return_value=st)
            ui._draw_frame()
        rows = [line for line in captured if "[" in strip_ansi(line) and "│" in line]
        self.assertTrue(rows, "playlist rows were not drawn")
        for row in rows:
            self.assertNotIn("\t", strip_ansi(row))
            self.assertLessEqual(
                MP_Audio_Player._display_width(row), 70,
                "playlist row is wider than the console: " + strip_ansi(row),
            )

    def test_visible_size_reads_the_window_rect(self):
        source = (BASE_DIR / "MP_Audio_Player.py").read_text(
            encoding="utf-8", errors="replace"
        )
        block = source.split("def _win_visible_size", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("GetConsoleScreenBufferInfo", block)
        self.assertIn("srWindow.Right", block)
        self.assertIn("srWindow.Top", block)


if __name__ == "__main__":
    unittest.main()
