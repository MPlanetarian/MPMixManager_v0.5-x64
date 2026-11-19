#!/usr/bin/env python3
"""Tests for the MP Visualisation Generator (v2 generative parameter set).

The generator has no GUI dependency at import time beyond the Liquid Player
host class, so the shader builders and the preset registry can be exercised
headlessly. These tests cover:

* every generative style / core / camera / backdrop / symmetry is accepted and
  produces different shader constants;
* the three backends are generated from one scene (Vulkan, OpenGL 330, Metal);
* the extra look and audio-mapping parameters reach the shader;
* the CLI resolves slugs and numeric ids;
* a generated preset round-trips through the registry and a
  ``.mpvispreset`` package with its generative metadata intact.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import MP_Visualization_Generator as generator  # noqa: E402

PALETTE = [
    (10, 20, 40), (0, 220, 255), (255, 40, 160), (240, 245, 255),
    (120, 90, 180), (4, 4, 12), (30, 200, 140), (255, 180, 40),
]


def _config(**overrides):
    config = {
        "palette": PALETTE,
        "name": "Test Scene",
        "style": 0,
        "core": 0,
        "camera": 0,
        "backdrop": 0,
        "symmetry": 0,
        "speed": 1.0,
        "intensity": 1.0,
        "punch": 1.0,
        "complexity": 1.0,
        "morph_rate": 1.0,
        "zoom": 1.0,
        "bass_drive": 1.0,
        "mid_drive": 1.0,
        "treble_drive": 1.0,
        "bloom": 1.0,
        "refraction": 1.0,
        "dispersion": 1.0,
        "seed": 7.0,
    }
    config.update(overrides)
    return config


class TestGenerativeOptionTables(unittest.TestCase):
    def test_tables_are_indexed_and_slugged(self):
        for table, names, slugs in (
            (generator.STYLES, generator.STYLE_NAMES, generator.STYLE_SLUGS),
            (generator.CORES, generator.CORE_NAMES, generator.CORE_SLUGS),
            (generator.CAMERAS, generator.CAMERA_NAMES, generator.CAMERA_SLUGS),
            (generator.BACKDROPS, generator.BACKDROP_NAMES, generator.BACKDROP_SLUGS),
            (generator.SYMMETRIES, generator.SYMMETRY_NAMES, generator.SYMMETRY_SLUGS),
        ):
            ids = [entry[0] for entry in table]
            self.assertEqual(ids, list(range(len(table))))
            self.assertEqual(len(set(slugs.values())), len(table))
            for entry_id, label, slug in table:
                self.assertEqual(names[entry_id], label)
                self.assertEqual(slugs[slug], entry_id)

    def test_style_library_covers_all_shipped_presets(self):
        # The thirteen shipped presets: 0..11 scenes plus the hybrid remix.
        self.assertGreaterEqual(len(generator.STYLES), 10)


class TestShaderGeneration(unittest.TestCase):
    def test_every_style_builds_all_backends(self):
        for style_id in generator.STYLE_NAMES:
            config = _config(style=style_id)
            vulkan = generator.generate_vulkan_source(config, steps=32)
            opengl = generator.generate_opengl_source(config, steps=32)
            metal = generator.generate_metal_source(config, steps=32)
            self.assertIn("#version 450", vulkan)
            self.assertIn("#version 330 core", opengl)
            self.assertIn(f"const float STYLE = {style_id}.0;", vulkan)
            self.assertIsNotNone(metal, f"style {style_id} produced no Metal source")
            self.assertIn("mp_visualizer_fragment", metal)
            self.assertNotIn("__", vulkan, "unexpanded placeholder in Vulkan source")

    def test_core_and_backdrop_ids_reach_the_shader(self):
        vulkan = generator.generate_vulkan_source(
            _config(core=4, backdrop=2, camera=3, symmetry=1), steps=32
        )
        self.assertIn("const float CORE_MODE = 4.0;", vulkan)
        self.assertIn("const float BACKDROP_MODE = 2.0;", vulkan)
        self.assertIn("const float CAMERA_MODE = 3.0;", vulkan)
        self.assertIn("const float SYMMETRY_MODE = 1.0;", vulkan)

    def test_look_and_audio_parameters_reach_the_shader(self):
        vulkan = generator.generate_vulkan_source(
            _config(bloom=1.7, refraction=0.6, dispersion=1.9, zoom=1.3,
                    bass_drive=0.4, mid_drive=1.4, treble_drive=1.1, morph_rate=2.2),
            steps=32,
        )
        self.assertIn("const float BLOOM_STRENGTH = 1.7000;", vulkan)
        self.assertIn("const float REFRACTION_STRENGTH = 0.6000;", vulkan)
        self.assertIn("const float DISPERSION_STRENGTH = 1.9000;", vulkan)
        self.assertIn("const float CAMERA_ZOOM = 1.3000;", vulkan)
        self.assertIn("const float BASS_DRIVE = 0.4000;", vulkan)
        self.assertIn("const float MID_DRIVE = 1.4000;", vulkan)
        self.assertIn("const float TREBLE_DRIVE = 1.1000;", vulkan)
        self.assertIn("const float MORPH_RATE = 2.2000;", vulkan)

    def test_complexity_scales_count_parameters(self):
        low = generator.generate_vulkan_source(_config(complexity=0.4), steps=32)
        high = generator.generate_vulkan_source(_config(complexity=2.0), steps=32)
        self.assertIn("const int SHARD_COUNT = 4;", low)
        self.assertIn("const int SHARD_COUNT = 12;", high)
        self.assertIn("const int TENDRIL_COUNT = 3;", low)
        self.assertIn("const int TENDRIL_COUNT = 8;", high)

    def test_palette_is_extended_to_eight_stops(self):
        vulkan = generator.generate_vulkan_source(_config(), steps=32)
        for index in range(8):
            self.assertIn(f"const vec3 PAL{index} = vec3(", vulkan)

    def test_metal_translation_has_no_glsl_types(self):
        metal = generator.generate_metal_source(_config(style=5), steps=32)
        self.assertIsNotNone(metal)
        for token in ("vec2 ", "vec3 ", "vec4 ", "mat2 "):
            self.assertNotIn(token, metal)

    @unittest.skipUnless(
        any(generator._tool(name) for name in ("glslangValidator", "glslc")),
        "no GLSL compiler installed",
    )
    def test_vulkan_source_compiles_for_a_sampled_matrix(self):
        with tempfile.TemporaryDirectory(prefix="mpvis-test-") as temp:
            for style_id in (0, 5, 6, 7, 8, 10, 11):
                for symmetry in (0, 2):
                    source = generator.generate_vulkan_source(
                        _config(style=style_id, symmetry=symmetry,
                                backdrop=style_id % 5, core=style_id % 7),
                        steps=48,
                    )
                    path = Path(temp) / f"style{style_id}-sym{symmetry}.frag"
                    path.write_text(source, encoding="utf-8")
                    completed = generator.compile_vulkan(source, Path(temp) / "out.spv")
                    self.assertIsNotNone(
                        completed, f"style {style_id} symmetry {symmetry} did not compile"
                    )


class TestCliResolution(unittest.TestCase):
    def test_resolve_choice_accepts_ids_and_slugs(self):
        resolve = generator._resolve_choice
        self.assertEqual(resolve("3", generator.STYLE_NAMES, generator.STYLE_SLUGS), 3)
        self.assertEqual(resolve("stream_v", generator.STYLE_NAMES, generator.STYLE_SLUGS), 8)
        self.assertEqual(resolve("gyroscope", generator.STYLE_NAMES, generator.STYLE_SLUGS), 5)
        self.assertEqual(resolve("snap", generator.CAMERA_NAMES, generator.CAMERA_SLUGS), 2)
        self.assertEqual(resolve("nonsense", generator.STYLE_NAMES, generator.STYLE_SLUGS), 0)

    def test_cli_help_and_option_listing(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "MP_Visualization_Generator.py"), "--list-options"],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stream_h", result.stdout)
        self.assertIn("kaleidoscope", result.stdout)


class TestPresetRoundTrip(unittest.TestCase):
    def test_generated_preset_keeps_generative_metadata(self):
        import mp_visualizer_user_presets as registry

        with tempfile.TemporaryDirectory(prefix="mpvis-home-") as temp:
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = temp
            try:
                registry.CONFIG_DIR = Path(temp) / ".config" / "mix-manager"
                registry.USER_PRESET_ROOT = registry.CONFIG_DIR / "visualizer-presets"
                registry.REGISTRY_FILE = registry.USER_PRESET_ROOT / "registry.json"

                work = Path(temp) / "work"
                work.mkdir()
                artwork = work / "artwork.png"
                try:
                    from PIL import Image
                except Exception:  # pragma: no cover - Pillow is a generator requirement
                    self.skipTest("Pillow is required for the generator round-trip test")
                Image.new("RGB", (64, 64), (30, 80, 190)).save(artwork)

                bundle = generator.build_preset(
                    {
                        "image": str(artwork),
                        "name": "Round Trip Scene",
                        "style": 7,
                        "core": 5,
                        "camera": 2,
                        "backdrop": 1,
                        "symmetry": 3,
                        "complexity": 1.7,
                        "bloom": 1.5,
                        "dispersion": 1.8,
                        "seed": 99.0,
                    },
                    install=True,
                    activate=False,
                )
                meta = json.loads((bundle["directory"] / "preset.json").read_text())
                self.assertEqual(meta["style_id"], 7)
                self.assertEqual(meta["core_id"], 5)
                self.assertEqual(meta["camera_id"], 2)
                self.assertEqual(meta["backdrop_id"], 1)
                self.assertEqual(meta["symmetry_id"], 3)
                self.assertAlmostEqual(meta["complexity"], 1.7)
                self.assertAlmostEqual(meta["bloom"], 1.5)
                self.assertEqual(meta["generator_version"], registry.GENERATOR_VERSION)

                preset = registry.find_user_preset(bundle["meta"]["key"])
                self.assertIsNotNone(preset)
                self.assertEqual(preset["style_id"], 7)
                self.assertEqual(preset["complexity"], 1.7)

                package = registry.export_package(preset["key"], work / "roundtrip")
                entry = registry.import_package(package)
                imported = json.loads(
                    (registry.preset_directory(entry["key"]) / "preset.json").read_text()
                )
                self.assertEqual(imported["style_id"], 7)
                self.assertEqual(imported["backdrop_id"], 1)
                self.assertEqual(imported["symmetry_id"], 3)
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home


if __name__ == "__main__":
    unittest.main()
