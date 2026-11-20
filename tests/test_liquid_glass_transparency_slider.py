"""The transparency slider must actually change the window's opacity.

The regression these tests pin: the slider moved its thumb but the glass stayed
at the startup value, in MP Liquid Player and in MP Gui. Two defects combined:

  * the host wrote the new value to its own ``clear_opacity`` (Vulkan) or only
    to ``renderer.slider_val`` (OpenGL), while ``draw()`` republished the window
    property from ``renderer.clear_opacity`` every frame, so the change was
    reverted within one frame;
  * the republish itself was guarded by ``factor != 1.0``, so a static theme -
    every theme without the rainbow oscillation - never wrote the property at
    all and the slider could not have any effect.

Every host now calls one shared ``apply_clear_opacity()``, so these tests drive
that method rather than reading the source text.
"""

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import liquid_glass_opengl as gl  # noqa: E402
import liquid_glass_vulkan as vk  # noqa: E402
import liquid_glass_slider as slider_mod  # noqa: E402

# The Vulkan renderer's class is named LiquidGlassRenderer; the OpenGL one
# carries the OpenGL suffix. Alias here so the tests read symmetrically.
VulkanRenderer = vk.LiquidGlassRenderer
OpenGLRenderer = gl.LiquidGlassRendererOpenGL


def _renderer(cls):
    """A renderer instance with the window plumbing stubbed out.

    ``__new__`` avoids creating a GL/Vulkan context: these tests only exercise
    the opacity bookkeeping, which never touches the GPU.
    """
    r = cls.__new__(cls)
    r.clear_opacity = 0.70
    r._last_dyn_opacity = None
    r._window = None
    r._glfw = None
    r._x11 = None
    r._set_opacity = MagicMock()
    r.x11_window = MagicMock(return_value=0x1234)
    return r


class ApplyClearOpacityTest(unittest.TestCase):
    """One shared method, both backends, no theme-dependent guard."""

    def test_a_static_theme_still_publishes_the_opacity(self):
        """factor == 1.0 must not suppress the write.

        This is the exact defect: the rainbow theme oscillates (factor != 1.0)
        and worked, so the feature looked fine on the default theme; every
        static theme silently never republished the property.
        """
        for cls in (OpenGLRenderer, VulkanRenderer):
            with self.subTest(renderer=cls.__name__):
                r = _renderer(cls)
                self.assertTrue(r.apply_clear_opacity(1.0))
                r._set_opacity.assert_called_once_with(0x1234, 0.70)

    def test_changing_the_base_opacity_reaches_the_window(self):
        """The slider changes clear_opacity; the next frame must apply it."""
        for cls in (OpenGLRenderer, VulkanRenderer):
            with self.subTest(renderer=cls.__name__):
                r = _renderer(cls)
                r.apply_clear_opacity(1.0)
                r._set_opacity.reset_mock()

                r.clear_opacity = 0.40          # what a slider drag now writes
                self.assertTrue(r.apply_clear_opacity(1.0))
                r._set_opacity.assert_called_once_with(0x1234, 0.40)

    def test_an_unchanged_value_is_not_rewritten_every_frame(self):
        """60 fps must not mean 60 X property writes."""
        for cls in (OpenGLRenderer, VulkanRenderer):
            with self.subTest(renderer=cls.__name__):
                r = _renderer(cls)
                self.assertTrue(r.apply_clear_opacity(1.0))
                for _ in range(5):
                    self.assertFalse(r.apply_clear_opacity(1.0))
                r._set_opacity.assert_called_once()

    def test_the_dynamic_factor_is_still_applied(self):
        r = _renderer(OpenGLRenderer)
        self.assertTrue(r.apply_clear_opacity(1.05))
        r._set_opacity.assert_called_once_with(0x1234, 0.70 * 1.05)

    def test_no_window_is_not_an_error(self):
        """A headless or pre-init renderer must not raise."""
        for cls in (OpenGLRenderer, VulkanRenderer):
            with self.subTest(renderer=cls.__name__):
                r = _renderer(cls)
                r.x11_window = MagicMock(return_value=0)
                self.assertFalse(r.apply_clear_opacity(1.0))


class HostSliderTest(unittest.TestCase):
    """The hosts must write the value the renderer reads."""

    def test_vulkan_host_writes_the_renderer_opacity(self):
        """`draw()` reads renderer.clear_opacity, so the host must set it."""
        from MP_Audio_Player_Liquid_Glass import GlassHost

        host = GlassHost.__new__(GlassHost)
        host.clear_opacity = 0.70
        host.renderer = _renderer(VulkanRenderer)
        host.renderer._last_dyn_opacity = 1234

        host._on_slider_change(0.35)

        self.assertEqual(host.clear_opacity, 0.35)
        self.assertEqual(host.renderer.clear_opacity, 0.35,
                         "the renderer would republish 0.70 on the next frame")
        self.assertIsNone(host.renderer._last_dyn_opacity,
                          "the memo must be cleared so the next frame writes")
        host.renderer._set_opacity.assert_called_once()

    def test_opengl_host_writes_clear_opacity_not_just_the_thumb(self):
        """`_update_slider_val` used to move only the thumb."""
        from MP_Audio_Player_Liquid_Glass_OpenGL import GlassHost

        host = GlassHost.__new__(GlassHost)
        host.renderer = _renderer(OpenGLRenderer)

        host._update_slider_val(40.0, 1000.0)      # far left
        self.assertAlmostEqual(host.renderer.slider_val, 0.20, places=3)
        self.assertAlmostEqual(host.renderer.clear_opacity, 0.20, places=3)

        host.renderer._set_opacity.reset_mock()
        host._update_slider_val(960.0, 1000.0)     # far right
        self.assertAlmostEqual(host.renderer.slider_val, 0.80, places=3)
        self.assertAlmostEqual(host.renderer.clear_opacity, 0.80, places=3)

    def test_a_left_to_right_drag_produces_distinct_opacities(self):
        """The reported symptom: dragging changed nothing on screen."""
        from MP_Audio_Player_Liquid_Glass_OpenGL import GlassHost

        host = GlassHost.__new__(GlassHost)
        host.renderer = _renderer(OpenGLRenderer)

        seen = []
        for x in range(40, 961, 115):
            host._update_slider_val(float(x), 1000.0)
            seen.append(round(host.renderer.clear_opacity, 3))

        self.assertGreater(len(set(seen)), 3, f"opacity barely moved: {seen}")
        self.assertLess(seen[0], seen[-1], "dragging right must increase opacity")


class SliderStripTest(unittest.TestCase):
    """The renderer must be able to answer for the strip it draws the bar in.

    The regression this pins: the OpenGL host moved the bar from a fixed 22 px
    offset to ``self._slider_strip()``, but that method only ever existed on
    ``GlassHost``. The renderer called it on every frame and raised
    ``AttributeError: 'CustomGlassRenderer' object has no attribute
    '_slider_strip'``, so the bar was never drawn and the traceback was printed
    once per frame for the life of the window.
    """

    def _renderer(self):
        import MP_Audio_Player_Liquid_Glass_OpenGL as host
        renderer = host.CustomGlassRenderer.__new__(host.CustomGlassRenderer)
        renderer._slider_strip_px = slider_mod.SLIDER_STRIP
        return renderer

    def test_the_renderer_answers_for_the_strip(self):
        renderer = self._renderer()
        self.assertIsInstance(renderer._slider_strip(), float)
        self.assertEqual(renderer._slider_strip(), float(slider_mod.SLIDER_STRIP))

    def test_the_host_publishes_the_strip_it_reserved(self):
        """MP Gui keeps 44 px; the player takes one text row less."""
        renderer = self._renderer()
        shorter = slider_mod.player_slider_strip(18)
        renderer._slider_strip_px = shorter
        self.assertEqual(renderer._slider_strip(), float(shorter))
        self.assertLess(shorter, slider_mod.SLIDER_STRIP)

    def test_a_bad_published_value_falls_back_to_the_classic_strip(self):
        renderer = self._renderer()
        renderer._slider_strip_px = None
        self.assertEqual(renderer._slider_strip(), float(slider_mod.SLIDER_STRIP))

    def test_the_host_hands_its_strip_to_the_renderer(self):
        """Source guard: the reservation and the bar must not drift apart."""
        text = (ROOT / "MP_Audio_Player_Liquid_Glass_OpenGL.py").read_text(encoding="utf-8")
        self.assertIn("self.renderer._slider_strip_px = self._slider_strip()", text)


class SliderHoverFadeTest(unittest.TestCase):
    """The OpenGL bar uses the same hover-only fade as the Vulkan overlay."""

    def _renderer(self):
        import MP_Audio_Player_Liquid_Glass_OpenGL as host
        renderer = host.CustomGlassRenderer.__new__(host.CustomGlassRenderer)
        renderer._slider_strip_px = slider_mod.SLIDER_STRIP
        renderer.cursor = (500.0, 1060.0)
        renderer.cursor_inside = True
        renderer._slider_dragging = False
        renderer._slider_alpha = 0.0
        renderer._slider_alpha_target = 0.0
        renderer._slider_fade_from = 0.0
        renderer._slider_fade_started = time.monotonic()
        return renderer

    def test_the_bar_starts_hidden(self):
        renderer = self._renderer()
        renderer.cursor_inside = False
        renderer._update_slider_fade(1000.0, 1080.0)
        self.assertEqual(renderer._slider_alpha, 0.0)

    def test_hovering_the_bottom_strip_fades_the_bar_in(self):
        renderer = self._renderer()
        renderer._update_slider_fade(1000.0, 1080.0)
        self.assertEqual(renderer._slider_alpha_target, 1.0)
        renderer._slider_fade_started = (
            time.monotonic() - slider_mod.SLIDER_FADE_IN_SECONDS / 2.0)
        renderer._update_slider_fade(1000.0, 1080.0)
        self.assertGreater(renderer._slider_alpha, 0.0)
        self.assertLess(renderer._slider_alpha, 1.0)

        renderer._slider_fade_started = (
            time.monotonic() - slider_mod.SLIDER_FADE_IN_SECONDS - 0.01)
        renderer._update_slider_fade(1000.0, 1080.0)
        self.assertEqual(renderer._slider_alpha, 1.0)

    def test_leaving_the_window_starts_a_fade_out(self):
        renderer = self._renderer()
        renderer._slider_alpha = renderer._slider_alpha_target = 1.0
        renderer.cursor_inside = False
        renderer._update_slider_fade(1000.0, 1080.0)
        self.assertEqual(renderer._slider_alpha_target, 0.0)
        renderer._slider_fade_started = (
            time.monotonic() - slider_mod.SLIDER_FADE_OUT_SECONDS - 0.01)
        renderer._update_slider_fade(1000.0, 1080.0)
        self.assertEqual(renderer._slider_alpha, 0.0)

    def test_pointer_above_the_strip_does_not_show_the_bar(self):
        renderer = self._renderer()
        renderer.cursor = (500.0, 500.0)
        renderer._update_slider_fade(1000.0, 1080.0)
        self.assertEqual(renderer._slider_alpha_target, 0.0)

    def test_dragging_keeps_the_bar_visible_outside_the_strip(self):
        renderer = self._renderer()
        renderer._slider_dragging = True
        renderer.cursor = (500.0, 200.0)
        renderer._update_slider_fade(1000.0, 1080.0)
        self.assertEqual(renderer._slider_alpha_target, 1.0)

    def test_a_hidden_bar_never_builds_the_slider_shader(self):
        import MP_Audio_Player_Liquid_Glass_OpenGL as host
        renderer = self._renderer()
        renderer.cursor_inside = False
        with patch.object(host, "_slider_gl") as slider_gl:
            renderer._draw_transparency_slider(
                100, 100, 1000, 1080, ((255, 0, 0),) * 3)
            slider_gl.assert_not_called()


class SourceGuardTest(unittest.TestCase):
    """Static guard so a fifth copy of the old guard cannot be reintroduced."""

    FILES = (
        "scripts/liquid_glass_opengl.py",
        "scripts/liquid_glass_vulkan.py",
        "MP_Audio_Player_Liquid_Glass.py",
        "MP_Audio_Player_Liquid_Glass_OpenGL.py",
        "MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py",
    )

    def test_no_opacity_write_is_guarded_by_the_animation_factor(self):
        for rel in self.FILES:
            path = ROOT / rel
            if not path.is_file():
                continue
            with self.subTest(file=rel):
                for line in path.read_text(encoding="utf-8").splitlines():
                    code = line.split("#", 1)[0]
                    if "_set_opacity" not in code:
                        continue
                    self.assertNotIn(
                        "factor != 1.0", code,
                        "the animated-theme guard suppresses the write for "
                        "static themes; call apply_clear_opacity() instead",
                    )


if __name__ == "__main__":
    unittest.main()
