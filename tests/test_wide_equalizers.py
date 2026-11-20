import unittest
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from MP_Audio_Player import EqualizerVisualizer, TerminalPlayerUI, EqualizerManager, _display_width


class TestWideEqualizers(unittest.TestCase):
    def setUp(self):
        self.status = {
            "state": "playing",
            "levels": [0.5] * 24,
            "peaks": [0.6] * 24,
            "levels_left": [0.5] * 24,
            "levels_right": [0.5] * 24,
            "peaks_left": [0.6] * 24,
            "peaks_right": [0.6] * 24,
            "rms_level": 0.2,
            "peak_db": -3.0,
            "pitch_percent": 0,
            "waveform": [0.1 * i for i in range(50)],
        }

    def test_all_visualizer_styles_stretch_to_full_width_liquid_glass(self):
        """In MP Liquid Player (width=120, MP_LIQUID_GLASS=1), visualizers must stretch across full width."""
        os.environ["MP_LIQUID_GLASS"] = "1"
        try:
            for w in [80, 100, 120]:
                expected_line_w = w - 2
                for style in EqualizerVisualizer.STYLES:
                    vis = EqualizerVisualizer(default_style=style)
                    lines = vis.render(self.status, width=w, height=7)
                    self.assertGreater(len(lines), 0)
                    for idx, line in enumerate(lines):
                        lw = _display_width(line)
                        self.assertEqual(
                            lw,
                            expected_line_w,
                            f"Style {style} width mismatch at line {idx} for window width {w}: got {lw}, expected {expected_line_w}",
                        )
        finally:
            os.environ.pop("MP_LIQUID_GLASS", None)

    def test_neon_and_stardust_bands_fill_each_half(self):
        """The two two-channel styles spread their bands, not a centred cluster.

        They used to place every band two cells from the last one, which left
        most of a wide standalone player empty and put the cluster in the
        middle. The mixer's narrower deck must still fit, so the fix uses
        equal slots across the half instead of raising the 16-band cap.
        """
        import re

        ansi = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
        levels = np.full(24, 0.5)
        peaks = np.full(24, 0.75)

        for style in ("neon_prism_eq", "stardust_particles_eq"):
            for width in (120, 80, 74):
                vis = EqualizerVisualizer(default_style=style)
                if style == "neon_prism_eq":
                    raw = vis.render_neon_prism_eq(
                        levels, levels, peaks, peaks, width=width, height=7)
                else:
                    vis.dust_rng.seed(1)
                    raw = vis.render_stardust_particles_eq(
                        levels, levels, peaks, peaks, width=width, height=7)
                plain = [ansi.sub("", line) for line in raw]
                # A row in the body with the most lit cells.
                body = plain[1:-2]
                row = max(body, key=lambda line: sum(ch not in " │" for ch in line))
                parts = row[2:].split("│")
                left, right = parts[1], parts[2]
                for half in (left, right):
                    lit = [i for i, ch in enumerate(half) if ch != " "]
                    self.assertTrue(lit, f"{style} at {width}: empty half")
                    self.assertLessEqual(lit[0], 3, f"{style} at {width}: left margin too wide")
                    self.assertLessEqual(len(half) - 1 - lit[-1], 3,
                                         f"{style} at {width}: right margin too wide")
                for line in plain:
                    self.assertLessEqual(len(line), width, f"{style} at {width}")

    def test_equalizer_hud_stretches_to_full_width(self):
        """10-band Equalizer HUD must stretch cleanly to match inner width (iw=114 => line width=118)."""
        engine = MagicMock()
        engine.eq_manager = EqualizerManager()
        scanner = MagicMock()
        ui = TerminalPlayerUI(engine, scanner)

        hud_lines = ui._render_equalizer_hud(iw=114)
        self.assertGreater(len(hud_lines), 0)
        for idx, line in enumerate(hud_lines):
            lw = _display_width(line)
            self.assertEqual(
                lw,
                118,
                f"HUD width mismatch at line {idx}: got {lw}, expected 118",
            )


if __name__ == "__main__":
    unittest.main()
