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


class TestGeneratorWindowLayout(unittest.TestCase):
    """The window builder must stay a straight line of live statements.

    ``_auto_preview_now`` was once pasted into the middle of ``_build_ui``,
    which left everything after its ``return`` -- the Generate button, the
    status label, the preset library and the whole live preview panel -- as
    unreachable code. The window then opened with an empty preview area.
    """

    def test_build_ui_has_no_dead_code(self):
        import ast

        source = (ROOT / "MP_Visualization_Generator.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        window = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "GeneratorWindow"
        )
        methods = {
            item.name: item for item in window.body if isinstance(item, ast.FunctionDef)
        }
        self.assertIn("_build_ui", methods)
        self.assertIn("_auto_preview_now", methods)

        build_ui = methods["_build_ui"]
        dead = []
        for parent in ast.walk(build_ui):
            for field in ("body", "orelse", "finalbody"):
                block = getattr(parent, field, None)
                if not isinstance(block, list):
                    continue
                for index, stmt in enumerate(block[:-1]):
                    if isinstance(stmt, (ast.Return, ast.Raise)):
                        dead.append(block[index + 1].lineno)
        self.assertEqual(dead, [], f"_build_ui has unreachable code at lines {dead}")

        names = {node.id for node in ast.walk(build_ui) if isinstance(node, ast.Name)}
        attrs = {
            node.attr for node in ast.walk(build_ui)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        }
        for widget in ("ShaderPreview", "generate_button", "status_label", "installed_combo"):
            self.assertIn(widget, names | attrs, f"_build_ui no longer builds {widget}")

    def test_preview_widgets_are_created(self):
        """The live preview panel must be built, not skipped."""
        source = (ROOT / "MP_Visualization_Generator.py").read_text(encoding="utf-8")
        self.assertIn("self.preview = ShaderPreview(Gtk, self.GLib)", source)
        self.assertIn("self.preview_badge = Gtk.Label", source)

    def test_library_and_install_stay_outside_the_scroll_view(self):
        """The preset dropdown and the Preview/Generate actions must not be
        buried below the long slider list again. The library sits in its own
        full-width bar above the body and the install block is pinned below
        the scroll view; the scrollbar is also kept visible."""
        source = (ROOT / "MP_Visualization_Generator.py").read_text(encoding="utf-8")
        self.assertIn("root.pack_start(library_expander", source)
        self.assertIn("root.reorder_child(library_expander, 1)", source)
        self.assertIn("panel.pack_start(install_expander", source)
        self.assertIn("controls_scroll.set_overlay_scrolling(False)", source)
        self.assertIn('Gtk.Button(label="Preview This Scene")', source)


class TestBuiltinPresetLibrary(unittest.TestCase):
    """The library dropdown must list the visualizer's own presets.

    The generator originally listed only its own ``user_*`` presets, so the
    thirteen hand-authored scenes could not be seen, loaded or replaced.
    """

    def test_shipped_presets_are_discoverable(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import mp_liquid_visualizer_vulkan as vulkan_visualizer

        shipped = [
            preset for preset in vulkan_visualizer.VISUALIZER_PRESETS
            if int(preset.get("id", -1)) < generator.user_presets.BASE_ID
        ]
        self.assertEqual(len(shipped), 13)
        self.assertEqual(shipped[0]["key"], "cosmic_dreamworlds_liquid")
        self.assertEqual(shipped[-1]["id"], 12)

    def test_library_lists_both_kinds(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import mp_liquid_visualizer_vulkan as vulkan_visualizer

        builtin = [
            preset for preset in vulkan_visualizer.VISUALIZER_PRESETS
            if int(preset.get("id", -1)) < generator.user_presets.BASE_ID
        ]
        self.assertGreaterEqual(len(builtin), 13)
        self.assertTrue(all(preset.get("key") for preset in builtin))

    def test_library_buttons_exist(self):
        source = (ROOT / "MP_Visualization_Generator.py").read_text(encoding="utf-8")
        for label in (
            'Gtk.Button(label="Update Preset")',
            'Gtk.Button(label="Rename Preset")',
            'Gtk.Button(label="Load in Generator")',
            'Gtk.Button(label="Restore Built-in Preset")',
        ):
            self.assertIn(label, source)


class TestPresetOverrideRoundTrip(unittest.TestCase):
    """Replacing a built-in preset must not renumber the library.

    A built-in keeps its id, key and album, so the saved active preset, the
    number keys and ``--preset N`` keep pointing at the same slot. All three
    backends resolve the replacement through the shared lookup helpers.
    """

    def setUp(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import mp_visualizer_user_presets as presets

        self.presets = presets
        self.temp = tempfile.TemporaryDirectory(prefix="mpvis-override-")
        root = Path(self.temp.name)
        self._saved = (
            presets.CONFIG_DIR, presets.USER_PRESET_ROOT,
            presets.REGISTRY_FILE, presets.OVERRIDE_FILE,
        )
        presets.CONFIG_DIR = root / "config"
        presets.USER_PRESET_ROOT = presets.CONFIG_DIR / "visualizer-presets"
        presets.REGISTRY_FILE = presets.USER_PRESET_ROOT / "registry.json"
        presets.OVERRIDE_FILE = presets.USER_PRESET_ROOT / "overrides.json"

    def tearDown(self):
        (
            self.presets.CONFIG_DIR, self.presets.USER_PRESET_ROOT,
            self.presets.REGISTRY_FILE, self.presets.OVERRIDE_FILE,
        ) = self._saved
        self.temp.cleanup()

    def _generated_scene(self, key: str = "user_test_scene") -> Path:
        directory = self.presets.preset_directory(key)
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("preset.frag", "preset.opengl.frag", "preset.metal",
                     "cover.png", "preview.png"):
            (directory / name).write_text("// scene\n", encoding="utf-8")
        (directory / "preset.frag.spv").write_bytes(b"\x03\x02\x23\x07" + b"\0" * 32)
        return directory

    def test_override_serves_all_three_backends(self):
        source = self._generated_scene()
        self.presets.set_override(source, 5, {"name": "Stream of Frequency"})
        self.assertIsNotNone(self.presets.opengl_source_for(5))
        self.assertIsNotNone(self.presets.vulkan_spirv_for(5))
        self.assertIsNotNone(self.presets.metal_source_for(5))
        # A renderer passes the built-in dict, which must also resolve.
        self.assertIsNotNone(
            self.presets.opengl_source_for({"id": 5, "key": "stream_of_frequency"})
        )

    def test_override_keeps_the_builtin_number(self):
        source = self._generated_scene()
        self.presets.set_override(source, 7, {"name": "Particle Accelerator"})
        overrides = self.presets.load_overrides()
        self.assertEqual(list(overrides), [7])
        self.assertEqual(overrides[7]["id"], 7)
        self.assertEqual(overrides[7]["key"], "builtin-7")

    def test_override_does_not_touch_the_user_library(self):
        source = self._generated_scene()
        self.presets.set_override(source, 3, {"name": "Infinite Complexity"})
        self.assertEqual(self.presets.load_user_presets(), [])

    def test_rename_keeps_number_and_key(self):
        source = self._generated_scene()
        self.presets.set_override(source, 5, {"name": "Stream of Frequency"})
        self.presets.rename_override(5, "My Waves")
        override = self.presets.load_overrides()[5]
        self.assertEqual(override["name"], "My Waves")
        self.assertEqual(override["id"], 5)
        self.assertEqual(override["key"], "builtin-5")

    def test_restore_returns_the_hand_authored_preset(self):
        source = self._generated_scene()
        self.presets.set_override(source, 5, {"name": "Stream of Frequency"})
        self.assertTrue(self.presets.clear_override(5))
        self.assertEqual(self.presets.load_overrides(), {})
        self.assertIsNone(self.presets.opengl_source_for(5))
        self.assertFalse(self.presets.override_dir(5).exists())

    def test_override_is_exportable(self):
        source = self._generated_scene()
        self.presets.set_override(source, 5, {"name": "Stream of Frequency"})
        package = self.presets.export_package("builtin-5", Path(self.temp.name) / "out.mpvispreset")
        self.assertTrue(package.is_file())
        import zipfile
        with zipfile.ZipFile(package) as archive:
            self.assertIn("preset.opengl.frag", archive.namelist())

    def test_user_preset_rename_keeps_id(self):
        source = self._generated_scene("user_keepme")
        self.presets.register_preset(source, {"key": "user_keepme", "name": "Keep Me"})
        before = self.presets.find_user_preset("user_keepme")
        self.presets.rename_preset("user_keepme", "Renamed Scene")
        after = self.presets.find_user_preset("user_keepme")
        self.assertEqual(after["id"], before["id"])
        self.assertEqual(after["key"], before["key"])
        self.assertEqual(after["name"], "Renamed Scene")


class TestWheelControls(unittest.TestCase):
    """The faders, the seed and the dropdowns must respond to the wheel.

    A ``Gtk.Scale`` inside the controls panel used to swallow nothing: the
    wheel scrolled the panel instead of the value, because GTK only scrolls a
    range when it already holds focus. Every control now handles
    ``scroll-event`` itself and returns ``True``, which stops the panel from
    scrolling underneath the pointer.
    """

    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gtk
        except Exception as exc:  # pragma: no cover - GTK is present on target
            raise unittest.SkipTest(f"GTK not available: {exc}")
        cls.Gtk = Gtk

    def _window(self):
        # The handlers read self.Gdk and self.GLib, which run() normally fills
        # in. Build a bare instance and give it the two it needs.
        import gi
        from gi.repository import Gdk, GLib
        app = object.__new__(generator.GeneratorWindow)
        app.Gdk = Gdk
        app.GLib = GLib
        return app

    def _scroll(self, direction, state=0):
        from gi.repository import Gdk
        event = Gdk.Event.new(Gdk.EventType.SCROLL)
        event.direction = direction
        event.state = state
        return event

    def test_scale_moves_one_step_per_notch(self):
        app = self._window()
        Gtk = self.Gtk
        parent = Gtk.Box()
        scale = app._labelled_scale(Gtk, parent, "Motion speed", 0.20, 2.60, 1.0)
        from gi.repository import Gdk
        before = scale.get_value()
        handled = scale.emit("scroll-event", self._scroll(Gdk.ScrollDirection.UP))
        after = scale.get_value()
        self.assertTrue(handled)
        self.assertGreater(after, before)
        # 1/50 of the 2.4 span, applied once rather than twice.
        self.assertAlmostEqual(after - before, 0.048, places=4)
        scale.emit("scroll-event", self._scroll(Gdk.ScrollDirection.DOWN))
        self.assertAlmostEqual(scale.get_value(), before, places=4)

    def test_scale_clamps_at_both_ends(self):
        app = self._window()
        Gtk = self.Gtk
        from gi.repository import Gdk
        parent = Gtk.Box()
        scale = app._labelled_scale(Gtk, parent, "Bass drive", 0.10, 2.50, 1.0)
        adjustment = scale.get_adjustment()
        scale.set_value(adjustment.get_upper())
        scale.emit("scroll-event", self._scroll(Gdk.ScrollDirection.UP))
        self.assertLessEqual(scale.get_value(), adjustment.get_upper())
        scale.set_value(adjustment.get_lower())
        scale.emit("scroll-event", self._scroll(Gdk.ScrollDirection.DOWN))
        self.assertGreaterEqual(scale.get_value(), adjustment.get_lower())

    def test_modifiers_change_the_step_size(self):
        app = self._window()
        Gtk = self.Gtk
        from gi.repository import Gdk
        parent = Gtk.Box()
        scale = app._labelled_scale(Gtk, parent, "Zoom", 0.55, 1.75, 1.0)
        normal = 1.2 / 50.0

        scale.set_value(1.0)
        scale.emit("scroll-event", self._scroll(Gdk.ScrollDirection.UP))
        plain = scale.get_value() - 1.0

        scale.set_value(1.0)
        scale.emit("scroll-event", self._scroll(Gdk.ScrollDirection.UP, Gdk.ModifierType.CONTROL_MASK))
        fine = scale.get_value() - 1.0

        scale.set_value(1.0)
        scale.emit("scroll-event", self._scroll(Gdk.ScrollDirection.UP, Gdk.ModifierType.SHIFT_MASK))
        coarse = scale.get_value() - 1.0

        self.assertAlmostEqual(plain, normal, places=4)
        self.assertLess(fine, plain)
        self.assertGreater(coarse, plain)

    def test_combo_cycles_without_wrapping_the_wrong_way(self):
        app = self._window()
        Gtk = self.Gtk
        from gi.repository import Gdk
        combo = Gtk.ComboBoxText()
        for index, label in enumerate(("A", "B", "C")):
            combo.append(str(index), label)
        combo.set_active(1)
        app._bind_combo_wheel(combo)

        self.assertTrue(combo.emit("scroll-event", self._scroll(Gdk.ScrollDirection.DOWN)))
        self.assertEqual(combo.get_active(), 2)
        combo.emit("scroll-event", self._scroll(Gdk.ScrollDirection.UP))
        self.assertEqual(combo.get_active(), 1)

    def test_handler_returns_true_so_the_panel_does_not_scroll(self):
        app = self._window()
        Gtk = self.Gtk
        from gi.repository import Gdk
        parent = Gtk.Box()
        scale = app._labelled_scale(Gtk, parent, "Punch", 0.20, 2.00, 1.0)
        self.assertTrue(scale.emit("scroll-event", self._scroll(Gdk.ScrollDirection.UP)))

    def test_seed_spin_steps_by_one(self):
        app = self._window()
        Gtk = self.Gtk
        from gi.repository import Gdk
        spin = Gtk.SpinButton.new_with_range(0, 999999, 1)
        spin.set_value(500)
        app._bind_wheel(spin, step=1.0)
        spin.emit("scroll-event", self._scroll(Gdk.ScrollDirection.UP))
        self.assertEqual(spin.get_value(), 501)
        spin.emit("scroll-event", self._scroll(Gdk.ScrollDirection.DOWN))
        self.assertEqual(spin.get_value(), 500)


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
