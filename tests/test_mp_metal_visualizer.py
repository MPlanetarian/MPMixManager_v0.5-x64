#!/usr/bin/env python3
"""Tests for the Apple Metal MP Visualizer backend.

Two layers are covered:

1. The GLSL -> MSL translator, which is pure text processing and therefore
   runs anywhere. It is the part that decides whether Metal draws the same
   picture as Vulkan, so its rules are pinned down here.
2. The backend selection and renderer contract, which have to keep working on
   Linux and Windows where Metal cannot run at all (the module must import,
   the probe must answer "no", and ``auto`` must not pick Metal).

The GPU itself is exercised by ``MP_Liquid_Visualizer.py --backend metal
--self-test`` on a Mac; these tests cover everything that can be checked
without one.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
for _path in (ROOT, ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import mp_metal_shader
import mp_liquid_visualizer_metal
import visualizer_backend


class TestShaderTranslation(unittest.TestCase):
    """The translator's rules, each of which was a real Metal compile error."""

    @classmethod
    def setUpClass(cls):
        cls.glsl = mp_metal_shader.glsl_for_metal()
        cls.msl = mp_metal_shader.translate(cls.glsl, fold_preset=7, march_steps=64)

    def test_vector_types_are_renamed(self):
        self.assertNotIn("vec2", self.msl)
        self.assertNotIn("vec3", self.msl)
        self.assertNotIn("vec4", self.msl)
        self.assertIn("float3", self.msl)

    @staticmethod
    def _code_only(msl: str) -> str:
        """Strip ``//`` comments so assertions cannot match prose."""
        import re
        return re.sub(r"//[^\n]*", "", msl)

    def test_mod_becomes_the_glsl_semantics(self):
        """Metal's fmod truncates; GLSL's mod floors. The sign differs."""
        import re
        code = self._code_only(self.msl)
        self.assertIsNone(re.search(r"(?<![A-Za-z0-9_])mod\s*\(", code))
        self.assertIn("mp_mod(", code)
        # The helper must be defined, not just referenced.
        self.assertIn("inline float  mp_mod(float  a, float  b)", code)

    def test_two_argument_atan_becomes_atan2(self):
        self.assertIn("atan2(", self.msl)

    def test_texture_calls_become_sample_calls(self):
        import re
        code = self._code_only(self.msl)
        # ``[[texture(0)]]`` is a Metal binding attribute, so only a real call
        # -- ``texture(`` followed by an identifier, not a digit -- counts.
        self.assertIsNone(re.search(r"(?<![A-Za-z0-9_.])texture\s*\(\s*[A-Za-z_]", code))
        self.assertIn(".sample(mp_cover_sampler,", code)

    def test_compound_swizzle_assignment_is_expanded(self):
        """Metal has no compound assignment through a swizzle."""
        import re
        leftover = re.findall(
            r"(?<![A-Za-z0-9_.])[A-Za-z_][A-Za-z0-9_]*\.[xyzw]{1,4}\s*[*+/-]=",
            self.msl,
        )
        self.assertEqual(leftover, [], "swizzle compound assignments remain")
        # And the expanded form is present.
        self.assertRegex(self.msl, r"\.xz = [A-Za-z_][A-Za-z0-9_]*\.xz \*")

    def test_program_scope_constants_are_constant_address_space(self):
        """Metal rejects a plain file-scope ``const``."""
        self.assertIn("constant float PI", self.msl)
        self.assertNotRegex(self.msl, r"(?m)^const\s")

    def test_uniform_block_becomes_a_struct(self):
        self.assertIn("struct MPUniforms {", self.msl)
        # Every field of the GLSL block must survive, in order.
        for field in ("res", "audio0", "audio1", "audio2", "bands0", "bands1",
                      "bands2", "bands3", "preset", "stream"):
            self.assertIn(f"float4 {field};", self.msl)

    def test_uniform_is_a_parameter_not_a_global(self):
        """Metal has no program-scope mutable globals, so ``u`` is threaded."""
        self.assertIn("constant MPUniforms &mpu", self.msl)
        self.assertIn("texture2d<float> uCoverTex", self.msl)

    def test_body_helper_always_returns(self):
        """A fall-through in a non-void function is undefined behaviour."""
        self.assertIn("return outColor;", self.msl)

    def test_fragment_entry_point_returns_a_colour(self):
        self.assertIn("fragment float4 mp_visualizer_fragment(", self.msl)
        self.assertIn("mp_visualizer_body(in.uv", self.msl)

    def test_vertex_stage_flips_v(self):
        """Metal's clip space is y-up; the shader addresses the framebuffer y-down."""
        self.assertIn("out.uv = float2(corner.x, 1.0 - corner.y);", self.msl)

    def test_preset_folding_removes_the_uniform_branch(self):
        self.assertNotIn("mpu.preset.x", self.msl)
        self.assertIn("(7.0)", self.msl)

    def test_march_steps_are_substituted(self):
        self.assertIn("for (int i = 0; i < 64; i++)", self.msl)
        self.assertNotIn("for (int i = 0; i < 84; i++)", self.msl)

    def test_every_preset_translates_to_a_distinct_program(self):
        seen = set()
        for preset in mp_liquid_visualizer_metal.__dict__.get("_PRESET_IDS", range(13)):
            translated = mp_metal_shader.translate(self.glsl, fold_preset=int(preset),
                                                   march_steps=64)
            self.assertNotIn("vec2", translated)
            seen.add(translated)
        self.assertEqual(len(seen), len(list(mp_liquid_visualizer_metal.__dict__.get(
            "_PRESET_IDS", range(13)))))

    def test_translation_is_deterministic(self):
        again = mp_metal_shader.translate(self.glsl, fold_preset=7, march_steps=64)
        self.assertEqual(again, self.msl)

    def test_translated_cache_round_trips(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            first = mp_metal_shader.translate_cached(
                ROOT / "shaders" / "mp_liquid_visualizer.frag",
                fold_preset=2, march_steps=32, cache_dir=Path(directory),
            )
            files = list(Path(directory).glob("*.metal"))
            self.assertEqual(len(files), 1)
            second = mp_metal_shader.translate_cached(
                ROOT / "shaders" / "mp_liquid_visualizer.frag",
                fold_preset=2, march_steps=32, cache_dir=Path(directory),
            )
            self.assertEqual(first, second)


class TestShaderTranslationFidelity(unittest.TestCase):
    """The Metal shader must compute the same thing as the GLSL."""

    def test_shared_math_helpers_are_present(self):
        """A silently dropped helper would change the picture."""
        msl = mp_metal_shader.translate(mp_metal_shader.glsl_for_metal(),
                                        fold_preset=0, march_steps=64)
        for helper in ("rot2", "hash11", "hash21", "hash33", "valueNoise3", "smin",
                       "sdSphere", "sdTorus", "sdBox", "sdCapsule", "sdHexPrism",
                       "sdCylinder", "sdOctahedron", "sdDodecahedron", "mapScene",
                       "calcNormal", "calcAO", "renderCosmicBackground",
                       "getPresetPalette", "sofHorizontalRiver", "sofVerticalRiver"):
            self.assertIn(helper, msl, helper)

    def test_every_preset_palette_is_present(self):
        msl = mp_metal_shader.translate(mp_metal_shader.glsl_for_metal(),
                                        fold_preset=0, march_steps=64)
        for palette in ("cosmicPalette", "orbitalVoidPalette", "mplanetarianPalette",
                        "infiniteComplexityPalette", "sensoriumPalette",
                        "resonantFluidDynamicsPalette", "chromaticStormPalette",
                        "beyondDimensionsPalette", "particleAcceleratorPalette",
                        "cosmicUniversePalette", "streamOfFrequencyPalette"):
            self.assertIn(palette, msl, palette)

    def test_chromatic_storm_sequences_are_present(self):
        msl = mp_metal_shader.translate(mp_metal_shader.glsl_for_metal(),
                                        fold_preset=0, march_steps=64)
        for sequence in ("csSeqClamshell", "csSeqHelix", "csSeqTwin",
                         "csSeqGyre", "csSeqComet", "csSeqNova"):
            self.assertIn(sequence, msl, sequence)

    def test_tone_mapping_is_preserved(self):
        msl = mp_metal_shader.translate(mp_metal_shader.glsl_for_metal(),
                                        fold_preset=0, march_steps=64)
        self.assertIn("2.51", msl)
        self.assertIn("2.43", msl)

    def test_no_glsl_specific_constructs_survive(self):
        msl = mp_metal_shader.translate(mp_metal_shader.glsl_for_metal(),
                                        fold_preset=0, march_steps=64)
        for leftover in ("#version", "layout(", "gl_Position", "gl_FragCoord",
                         "sampler2D", "inversesqrt", "outColor =", "discard"):
            if leftover in ("outColor =",):
                continue  # the helper assigns its local before returning it
            self.assertNotIn(leftover, msl, leftover)


class TestMetalBackendSelection(unittest.TestCase):
    """Backend resolution must be safe on every platform."""

    def test_metal_aliases_normalise(self):
        for alias in ("metal", "Metal", "mtl", "MTL"):
            self.assertEqual(visualizer_backend.normalise(alias), "metal")

    def test_explicit_metal_is_never_probed(self):
        with patch.object(visualizer_backend, "metal_available", return_value=False):
            self.assertEqual(visualizer_backend.resolve("metal"), "metal")

    def test_auto_prefers_metal_on_macos_with_a_device(self):
        with patch.object(visualizer_backend.sys, "platform", "darwin"), \
             patch.object(visualizer_backend, "metal_available", return_value=True), \
             patch.object(visualizer_backend, "configured_backend", return_value="auto"):
            self.assertEqual(visualizer_backend.resolve(), "metal")

    def test_auto_falls_back_when_metal_is_unavailable(self):
        with patch.object(visualizer_backend.sys, "platform", "darwin"), \
             patch.object(visualizer_backend, "metal_available", return_value=False), \
             patch.object(visualizer_backend, "configured_backend", return_value="auto"), \
             patch.object(visualizer_backend, "nvidia_vulkan_available", return_value=False):
            self.assertEqual(visualizer_backend.resolve(), "opengl")

    def test_auto_never_picks_metal_off_macos(self):
        with patch.object(visualizer_backend.sys, "platform", "linux"), \
             patch.object(visualizer_backend, "metal_available", return_value=True), \
             patch.object(visualizer_backend, "configured_backend", return_value="auto"), \
             patch.object(visualizer_backend, "nvidia_vulkan_available", return_value=True):
            self.assertEqual(visualizer_backend.resolve(), "vulkan")

    def test_recommended_backend_is_metal_on_macos(self):
        with patch.object(visualizer_backend.sys, "platform", "darwin"), \
             patch.object(visualizer_backend, "metal_available", return_value=True):
            self.assertEqual(visualizer_backend.recommended_backend(), "metal")

    def test_recommended_backend_off_macos_is_unchanged(self):
        with patch.object(visualizer_backend.sys, "platform", "linux"), \
             patch.object(visualizer_backend, "nvidia_vulkan_available", return_value=False):
            self.assertEqual(visualizer_backend.recommended_backend(), "opengl")

    def test_renderer_class_maps_metal(self):
        cls = visualizer_backend.renderer_class("metal")
        self.assertIs(cls, mp_liquid_visualizer_metal.LiquidVisualizerMetalRenderer)


class TestMetalProbe(unittest.TestCase):
    """``metal_available`` must answer, never raise."""

    def test_probe_is_false_off_macos(self):
        # Both probes have to be patched: ``is_macos`` accepts either signal, so
        # patching only ``sys.platform`` still reports darwin on a real Mac.
        with patch.object(mp_liquid_visualizer_metal.sys, "platform", "linux"), \
             patch.object(mp_liquid_visualizer_metal.platform, "system",
                          return_value="Linux"):
            self.assertFalse(mp_liquid_visualizer_metal.metal_available())

    def test_probe_does_not_raise(self):
        # Whatever the host, the probe must return a bool rather than throwing.
        self.assertIsInstance(mp_liquid_visualizer_metal.metal_available(), bool)

    def test_backend_probe_does_not_raise(self):
        self.assertIsInstance(visualizer_backend.metal_available(), bool)


class TestRenderSizing(unittest.TestCase):
    """The march resolution policy, and its documented knobs."""

    def test_scale_keeps_the_aspect_ratio(self):
        self.assertEqual(mp_liquid_visualizer_metal.scale_to_long_edge(1094, 734), (768, 515))
        self.assertEqual(mp_liquid_visualizer_metal.scale_to_long_edge(700, 700), (700, 700))

    def test_scale_never_upscales(self):
        self.assertEqual(mp_liquid_visualizer_metal.scale_to_long_edge(320, 200), (320, 200))

    def test_scale_rounds_to_at_least_two_pixels(self):
        width, height = mp_liquid_visualizer_metal.scale_to_long_edge(4000, 2)
        self.assertGreaterEqual(width, 2)
        self.assertGreaterEqual(height, 2)

    def test_saver_cap_limits_the_march(self):
        width, height = mp_liquid_visualizer_metal.saver_march_size(1920, 1200, (1280, 800))
        self.assertLessEqual(max(width, height), 1280)

    def test_march_steps_default_and_clamp(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MP_METAL_MARCH_STEPS", None)
            os.environ.pop("MP_VISUALIZER_GL_MARCH_STEPS", None)
            self.assertEqual(mp_liquid_visualizer_metal.march_steps(), 64)
        with patch.dict(os.environ, {"MP_METAL_MARCH_STEPS": "9999"}):
            self.assertEqual(mp_liquid_visualizer_metal.march_steps(), 84)
        with patch.dict(os.environ, {"MP_METAL_MARCH_STEPS": "1"}):
            self.assertEqual(mp_liquid_visualizer_metal.march_steps(), 16)

    def test_adaptive_can_be_disabled(self):
        with patch.dict(os.environ, {"MP_METAL_ADAPTIVE": "0"}):
            self.assertFalse(mp_liquid_visualizer_metal.adaptive_enabled())
        with patch.dict(os.environ, {"MP_METAL_ADAPTIVE": ""}):
            self.assertTrue(mp_liquid_visualizer_metal.adaptive_enabled())

    def test_fullscreen_march_is_a_real_1080p(self):
        """Fullscreen used to march the 768 windowed cap and look blocky."""
        self.assertEqual(
            mp_liquid_visualizer_metal.fullscreen_march_size(1920, 1080), (1920, 1080))
        # A Retina backing store is 2x the points, and is still marched at 1080p.
        self.assertEqual(
            mp_liquid_visualizer_metal.fullscreen_march_size(3840, 2160), (1920, 1080))

    def test_fullscreen_march_keeps_the_display_aspect(self):
        self.assertEqual(
            mp_liquid_visualizer_metal.fullscreen_march_size(1920, 1200), (1728, 1080))
        self.assertEqual(
            mp_liquid_visualizer_metal.fullscreen_march_size(2560, 1080), (1920, 810))

    def test_fullscreen_march_never_upscales(self):
        self.assertEqual(
            mp_liquid_visualizer_metal.fullscreen_march_size(1280, 720), (1280, 720))
        self.assertEqual(
            mp_liquid_visualizer_metal.fullscreen_march_size(1024, 768), (1024, 768))

    def test_fullscreen_long_edge_is_configurable(self):
        with patch.dict(os.environ, {"MP_METAL_FULLSCREEN_LONG_EDGE": "1280"}):
            self.assertEqual(
                mp_liquid_visualizer_metal.fullscreen_march_size(1920, 1080), (1280, 720))
        with patch.dict(os.environ, {"MP_METAL_FULLSCREEN_LONG_EDGE": "4096"}):
            self.assertEqual(
                mp_liquid_visualizer_metal.fullscreen_march_size(1920, 1080), (1920, 1080))

    def test_fullscreen_adaptation_is_on_by_default(self):
        """A Mac that cannot hold 1080p must still be allowed to retune."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MP_METAL_FULLSCREEN_ADAPTIVE", None)
            self.assertTrue(mp_liquid_visualizer_metal.fullscreen_adaptive_enabled())
        with patch.dict(os.environ, {"MP_METAL_FULLSCREEN_ADAPTIVE": "0"}):
            self.assertFalse(mp_liquid_visualizer_metal.fullscreen_adaptive_enabled())
        with patch.dict(os.environ, {"MP_METAL_FULLSCREEN_ADAPTIVE": "1"}):
            self.assertTrue(mp_liquid_visualizer_metal.fullscreen_adaptive_enabled())

    def test_fullscreen_floor_is_above_the_windowed_floor(self):
        """The whole complaint was fullscreen being softer than the window."""
        self.assertGreater(mp_liquid_visualizer_metal.FULLSCREEN_ADAPTIVE_MIN_SCALE,
                           mp_liquid_visualizer_metal.ADAPTIVE_MIN_SCALE)
        floor_edge = 1920 * mp_liquid_visualizer_metal.FULLSCREEN_ADAPTIVE_MIN_SCALE
        windowed_cap = mp_liquid_visualizer_metal.DEFAULT_LONG_EDGE
        # Even at its floor, fullscreen marches meaningfully more pixels than
        # the old windowed cap that read as blocky.
        self.assertGreater(floor_edge, windowed_cap * 1.5)

    def test_fullscreen_target_is_not_60fps(self):
        """A raymarched 1080p cannot hold 60 fps on Apple Silicon.

        The budget is in milliseconds, so a slower target is a *larger*
        number: 33.3 ms (30 fps) vs 16.7 ms (60 fps).
        """
        self.assertGreater(mp_liquid_visualizer_metal.FULLSCREEN_TARGET_MS,
                           mp_liquid_visualizer_metal.ADAPTIVE_TARGET_MS)

    def test_fullscreen_window_marches_at_1080p(self):
        renderer = mp_liquid_visualizer_metal.LiquidVisualizerMetalRenderer(
            "cosmic_dreamworlds_liquid"
        )
        renderer.is_fullscreen = True
        renderer.screensaver = False
        renderer._saver_render = None
        renderer._resolution_scale = 1.0
        renderer._fullscreen_scale = 1.0
        self.assertEqual(renderer._march_for_size(1920, 1080), (1920, 1080))

    def test_windowed_march_keeps_the_cap(self):
        renderer = mp_liquid_visualizer_metal.LiquidVisualizerMetalRenderer(
            "cosmic_dreamworlds_liquid"
        )
        renderer.is_fullscreen = False
        renderer._resolution_scale = 1.0
        self.assertEqual(renderer._march_for_size(1920, 1080), (768, 432))

    def test_fullscreen_march_ignores_the_windowed_adaptive_scale(self):
        """A windowed dip must not drag fullscreen below its own floor."""
        renderer = mp_liquid_visualizer_metal.LiquidVisualizerMetalRenderer(
            "cosmic_dreamworlds_liquid"
        )
        renderer.is_fullscreen = True
        renderer.screensaver = False
        renderer._saver_render = None
        # A deep windowed dip (0.55) belongs to the windowed controller only.
        renderer._resolution_scale = 0.55
        renderer._fullscreen_scale = 1.0
        self.assertEqual(renderer._march_for_size(1920, 1080), (1920, 1080))
        # The fullscreen controller has its own, higher floor.
        renderer._fullscreen_scale = 0.10
        width, height = renderer._march_for_size(1920, 1080)
        floor = int(round(1920 * mp_liquid_visualizer_metal.FULLSCREEN_ADAPTIVE_MIN_SCALE))
        self.assertEqual(width, floor)
        self.assertGreater(width, int(1920 * 0.55))

    def test_screensaver_fullscreen_keeps_its_own_cap(self):
        """The saver is a background effect and keeps the capped march."""
        renderer = mp_liquid_visualizer_metal.LiquidVisualizerMetalRenderer(
            "cosmic_dreamworlds_liquid", saver_render=(1280, 800)
        )
        renderer.is_fullscreen = True
        renderer.screensaver = True
        width, height = renderer._march_for_size(1920, 1080)
        self.assertLessEqual(max(width, height), 1280)



class TestRendererContract(unittest.TestCase):
    """The Metal renderer must look like the other renderers to its callers."""

    def test_class_exposes_the_renderer_surface(self):
        cls = mp_liquid_visualizer_metal.LiquidVisualizerMetalRenderer
        for name in ("open", "close", "draw", "poll", "switch_preset",
                     "window_size", "framebuffer_size", "window_pos",
                     "set_window_pos", "request_close", "toggle_fullscreen",
                     "cycle_window_mode"):
            self.assertTrue(callable(getattr(cls, name, None)), name)

    def test_class_uses_the_shared_visualizer_mixins(self):
        """F10/F11 spanning, R random mode and player bonding must keep working."""
        from visualizer_monitor_span import MonitorSpanMixin
        from visualizer_player_bond import PlayerBondMixin
        from visualizer_random_mode import RandomPresetMixin
        cls = mp_liquid_visualizer_metal.LiquidVisualizerMetalRenderer
        for mixin in (MonitorSpanMixin, RandomPresetMixin, PlayerBondMixin):
            self.assertTrue(issubclass(cls, mixin), mixin.__name__)

    def test_renderer_can_be_constructed_without_a_gpu(self):
        """Construction must not touch Metal; only ``open`` does."""
        renderer = mp_liquid_visualizer_metal.LiquidVisualizerMetalRenderer(
            "cosmic_dreamworlds_liquid"
        )
        self.assertEqual(renderer.preset["key"], "cosmic_dreamworlds_liquid")
        self.assertIsNone(renderer._bridge)
        self.assertFalse(renderer.should_close)

    def test_march_policy_matches_the_vulkan_macos_scale(self):
        """Both backends must render the same framing for the same window."""
        renderer = mp_liquid_visualizer_metal.LiquidVisualizerMetalRenderer(
            "cosmic_dreamworlds_liquid"
        )
        self.assertEqual(renderer._march_for_size(1094, 734), (768, 515))


class TestCLIIntegration(unittest.TestCase):
    def test_cli_accepts_the_metal_backend(self):
        import mp_liquid_visualizer_vulkan as vis
        with patch("sys.stdout"):
            self.assertEqual(vis.main(["--backend", "metal", "--list-presets"]), 0)

    def test_self_test_routes_to_metal(self):
        import mp_liquid_visualizer_vulkan as vis
        with patch.object(visualizer_backend, "resolve", return_value="metal"), \
             patch.object(vis, "_run_metal_self_test", return_value="ok") as metal_test:
            self.assertEqual(vis.run_self_test(backend="metal"), "ok")
        metal_test.assert_called_once()


class TestMacCompanionStacking(unittest.TestCase):
    """The owned companion must sit behind MP Gui / the Liquid Player.

    The host and the visualizer are separate processes. Ordering the
    visualizer's window "back" inside its own process does not move it behind
    another application, so macOS needs a window level below normal as well as
    a non-focusing launch.
    """

    def test_a_companion_is_ordered_below_the_host_and_does_not_take_focus(self):
        text = (ROOT / "scripts" / "mp_liquid_visualizer_metal.py").read_text(
            encoding="utf-8")
        # The host places the companion directly beneath itself through the
        # window server; a level below normal went behind Finder/Terminal and
        # a level above normal covered the host.
        self.assertIn("CGSOrderWindow", text)
        self.assertIn("_sync_macos_host", text)
        self.assertIn("read_published_gui_window_id", text)
        self.assertNotIn('"setLevel:"', text)
        self.assertIn("focus = GLFW_FALSE if companion else GLFW_TRUE", text)

    def test_a_free_window_still_comes_to_the_front(self):
        text = (ROOT / "scripts" / "mp_liquid_visualizer_metal.py").read_text(
            encoding="utf-8")
        self.assertIn('"makeKeyAndOrderFront:"', text)
        # The call must be in the non-companion branch, not applied to every
        # window: a standalone visualizer is meant to be interactive.
        companion = text.index('if getattr(self, "_bond_locked", False):')
        block = text[companion:companion + 900]
        self.assertIn('"orderFront:"', block)
        self.assertIn('"makeKeyAndOrderFront:"', block)


if __name__ == "__main__":
    unittest.main()
