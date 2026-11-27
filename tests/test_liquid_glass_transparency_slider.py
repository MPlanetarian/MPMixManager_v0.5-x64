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
        self.assertAlmostEqual(host.renderer.slider_val, 0.10, places=3)
        self.assertAlmostEqual(host.renderer.clear_opacity, 0.10, places=3)

        host.renderer._set_opacity.reset_mock()
        host._update_slider_val(960.0, 1000.0)     # far right: zero transparency
        self.assertAlmostEqual(host.renderer.slider_val, 1.00, places=3)
        self.assertAlmostEqual(host.renderer.clear_opacity, 1.00, places=3)

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


class SolidRightEndTest(unittest.TestCase):
    """The far right of the bar is a solid pane, zero transparency."""

    def test_a_full_slider_publishes_opaque_even_while_the_theme_breathes(self):
        """The rainbow theme scales opacity by about ±9%. At the right end
        that breath must not open the desktop back up."""
        self.assertEqual(slider_mod.published_window_opacity(1.0, 0.91), 1.0)
        self.assertEqual(slider_mod.published_window_opacity(1.0, 1.09), 1.0)
        self.assertAlmostEqual(slider_mod.published_window_opacity(0.70, 1.05), 0.70 * 1.05)

    def test_a_full_slider_drops_the_translucent_glass_body(self):
        """Premultiplied glass is about half clear. Leaving that path on at
        opacity 1 still shows the desktop through the player."""
        self.assertFalse(slider_mod.bezel_premultiplied(True, 1.0))
        self.assertTrue(slider_mod.bezel_premultiplied(True, 0.70))
        self.assertFalse(slider_mod.bezel_premultiplied(False, 0.70))

    def test_the_shader_closes_the_glass_at_the_right_end(self):
        frag = (ROOT / "shaders" / "liquid_glass.frag").read_text(encoding="utf-8")
        gl = (ROOT / "shaders" / "liquid_glass_gl.frag").read_text(encoding="utf-8")
        for src in (frag, gl):
            self.assertIn("smoothstep(0.84, 1.0, requested)", src)
            self.assertIn("alpha = mix(alpha, rim, solid)", src)

    def test_renderer_at_full_opacity_publishes_one(self):
        for cls in (OpenGLRenderer, VulkanRenderer):
            with self.subTest(renderer=cls.__name__):
                r = _renderer(cls)
                r.clear_opacity = 1.0
                self.assertTrue(r.apply_clear_opacity(0.91))
                r._set_opacity.assert_called_once_with(0x1234, 1.0)


class WindowsOpacityTest(unittest.TestCase):
    """Windows has no X11 window id, so the property write was a silent no-op.

    ``_set_opacity`` wrote ``_NET_WM_WINDOW_OPACITY`` on everything that was
    not macOS, and ``sync_player_visualizer_opacity`` only had X11 and macOS
    branches. On Windows both fell through: the pane never applied the slider
    and never lifted to its readable 0.94 over the visualizer.
    """

    def _windows_renderer(self):
        r = OpenGLRenderer.__new__(OpenGLRenderer)
        r.clear_opacity = 0.70
        r._window = 0x1234
        r._vis_gl_opacity = None
        r._last_dyn_opacity = None
        r.x11_window = MagicMock(return_value=0)
        r._glfw = MagicMock()
        r._change_cardinal = MagicMock()
        r._x_atom = MagicMock(return_value=1)
        return r

    def test_set_opacity_uses_glfw_on_windows(self):
        import liquid_glass_opengl as glass

        r = self._windows_renderer()
        with patch.object(glass, "is_windows", return_value=True), \
             patch.object(glass, "is_macos", return_value=False):
            self.assertTrue(r._set_opacity(0, 0.55))
        r._glfw.glfwSetWindowOpacity.assert_called_once()
        value = r._glfw.glfwSetWindowOpacity.call_args[0][1]
        self.assertAlmostEqual(getattr(value, "value", value), 0.55, places=3)
        r._change_cardinal.assert_not_called()

    def test_visualizer_lifts_the_windows_pane_to_near_solid(self):
        import liquid_glass_opengl as glass

        r = self._windows_renderer()
        with patch.object(glass, "is_windows", return_value=True), \
             patch.object(glass, "is_macos", return_value=False):
            r.sync_player_visualizer_opacity(True)
            self.assertGreaterEqual(r._vis_gl_opacity, 0.94)
            r._glfw.glfwSetWindowOpacity.assert_called()
            # Leaving restores the slider value.
            r._glfw.glfwSetWindowOpacity.reset_mock()
            r.sync_player_visualizer_opacity(False)
            self.assertIsNone(r._vis_gl_opacity)
            value = r._glfw.glfwSetWindowOpacity.call_args[0][1]
            self.assertAlmostEqual(getattr(value, "value", value), 0.70, places=3)

    def test_the_windows_host_polls_the_pointer_for_the_hover_bar(self):
        """The console is a separate window over the glass on Windows.

        The transparency bar is hover-only, and the pointer over the bottom
        strip is over that console, so GLFW never delivers a cursor event and
        the bar could never fade in. The host reads the OS pointer instead.
        """
        text = (ROOT / "MP_Audio_Player_Liquid_Glass_OpenGL.py").read_text(encoding="utf-8")
        self.assertIn("_poll_windows_slider_hover", text)
        self.assertIn("GetCursorPos", text)
        # It must be called from the Windows frame loop, before the draw.
        loop = text.split("while not self.renderer.should_close and proc.poll()", 1)[1]
        loop = loop.split("finally:", 1)[0]
        self.assertIn("_poll_windows_slider_hover()", loop)
        self.assertLess(loop.find("_poll_windows_slider_hover()"),
                        loop.find("self.renderer.draw(theme)"))


class SliderBarPositionTest(unittest.TestCase):
    """The bar must be at the BOTTOM of the window.

    The regression this pins: the NDC helper was ``1 - 2*(px/height)``, the
    mirror of the correct mapping. It sent a bar 12 px above the bottom edge to
    12 px below the top edge, so the transparency bar was drawn under the
    title strip and the user could never find it. A pixel capture of the
    running window found the drawn band at y=22..29 from the window top; after
    the fix the same capture finds it at ~18 px from the bottom.
    """

    def _ndc(self):
        """The live helper, extracted from the host's source."""
        text = (ROOT / "MP_Audio_Player_Liquid_Glass_OpenGL.py").read_text(encoding="utf-8")
        block = text.split("def _ndc_y(px):", 1)[1].split("\n", 2)[2]
        namespace = {}
        exec("def _ndc_y(px, wh):\n" + block.split("\n\n")[0].replace(
            "float(wh)", "float(wh)"), namespace)
        return namespace["_ndc_y"]

    def test_the_bar_sits_near_the_bottom_not_the_top(self):
        text = (ROOT / "MP_Audio_Player_Liquid_Glass_OpenGL.py").read_text(encoding="utf-8")
        self.assertIn("-1.0 + (float(px) / float(wh)) * 2.0", text)
        self.assertNotIn("1.0 - (float(px) / float(wh)) * 2.0", text)

    def test_a_bar_above_the_bottom_maps_below_the_centre(self):
        wh = 778.0
        # The same expression the host uses.
        def ndc(px):
            return -1.0 + (px / wh) * 2.0

        # 12 px above the bottom edge is near the bottom of the screen.
        self.assertLess(ndc(12.0), -0.9)
        self.assertLess(ndc(20.0), -0.9)
        # NDC -1 is the bottom edge, +1 the top: a small distance from the
        # bottom must NOT be near +1, which is where the old mapping put it.
        self.assertGreater(1.0 - (12.0 / wh) * 2.0, 0.9)


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
