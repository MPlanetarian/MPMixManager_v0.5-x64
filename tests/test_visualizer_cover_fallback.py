#!/usr/bin/env python3
"""A missing cover must not stop the visualizer from opening.

Resonant Fluid Dynamics shipped without its artwork. The Vulkan backend
raised FileNotFoundError for it and fell back to OpenGL, so selecting that
preset looked like the visualizer had stopped loading.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mp_liquid_visualizer_vulkan as visualizer  # noqa: E402


class VisualizerCoverTest(unittest.TestCase):
    def test_resonant_fluid_dynamics_has_artwork(self):
        preset = next(
            p for p in visualizer.VISUALIZER_PRESETS
            if p["key"] == "resonant_fluid_dynamics"
        )
        self.assertTrue(
            any(Path(path).is_file() for path in preset["cover_paths"]),
            "no existing cover path for Resonant Fluid Dynamics",
        )

    def test_a_missing_cover_returns_a_texture_instead_of_raising(self):
        width, height, pixels = visualizer.load_preset_cover_rgba(
            {"key": "no_such_preset", "name": "No Such Preset", "cover_paths": []},
            tex_size=8,
        )
        self.assertEqual((width, height), (8, 8))
        self.assertEqual(len(pixels), 8 * 8 * 4)


if __name__ == "__main__":
    unittest.main()
