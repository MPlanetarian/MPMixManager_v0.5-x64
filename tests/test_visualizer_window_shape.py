#!/usr/bin/env python3
"""The standalone MP Visualizer window shares the Liquid Player's rounded corners.

The player's glass window is clipped to a 36 px rounded rectangle. The
visualizer opens behind it at exactly the same size, so before this it showed
black square wedges through the player's cut-away corners. These tests pin the
matching shape, the radius, and the cases that must stay rectangular.
"""
from __future__ import annotations

import ctypes
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import liquid_glass_opengl
import liquid_glass_vulkan
import liquid_player_visualizer
import mp_liquid_visualizer_metal
import mp_liquid_visualizer_vulkan
import visualizer_window_shape


class TestSharedRadius(unittest.TestCase):
    def test_visualizer_radius_matches_the_player(self):
        radius = visualizer_window_shape.DEFAULT_CORNER_RADIUS
        self.assertEqual(radius, liquid_glass_opengl.LiquidGlassRendererOpenGL.radius_px)
        self.assertEqual(radius, liquid_glass_vulkan.LiquidGlassRenderer.radius_px)

    def test_both_visualizer_backends_own_a_shaper(self):
        vulkan = mp_liquid_visualizer_vulkan.LiquidVisualizerVulkanRenderer("cosmic_dreamworlds_liquid")
        opengl = liquid_player_visualizer.LiquidVisualizerOpenGLRenderer("cosmic_dreamworlds_liquid")
        self.assertEqual(vulkan._shaper.radius, visualizer_window_shape.DEFAULT_CORNER_RADIUS)
        self.assertEqual(opengl._shaper.radius, visualizer_window_shape.DEFAULT_CORNER_RADIUS)

    def test_the_metal_backend_owns_a_shaper_too(self):
        """macOS used Metal for the standalone visualizer and never shaped it.

        The player paints its glass into a 36 px rounded rectangle, so a square
        Metal window showed its own corners through the player's cut-aways.
        """
        metal = mp_liquid_visualizer_metal.LiquidVisualizerMetalRenderer(
            "cosmic_dreamworlds_liquid")
        self.assertEqual(metal._shaper.radius, visualizer_window_shape.DEFAULT_CORNER_RADIUS)


class TestShaperGeometry(unittest.TestCase):
    def _x11_window(self):
        """A real GLFW window plus libX11 handles, or None without a display."""
        try:
            from ctypes import byref, c_int, c_uint32, c_void_p
            import ctypes

            glfw = mp_liquid_visualizer_vulkan.load_glfw_dll()
            probe = mp_liquid_visualizer_vulkan.LiquidVisualizerVulkanRenderer.__new__(
                mp_liquid_visualizer_vulkan.LiquidVisualizerVulkanRenderer
            )
            probe._glfw = glfw
            probe._bind_glfw()
            glfw.glfwInitHint(mp_liquid_visualizer_vulkan.GLFW_PLATFORM,
                              mp_liquid_visualizer_vulkan.GLFW_PLATFORM_X11)
            if not glfw.glfwInit():
                return None
            for hint, value in ((mp_liquid_visualizer_vulkan.GLFW_CLIENT_API,
                                 mp_liquid_visualizer_vulkan.GLFW_NO_API),
                                (mp_liquid_visualizer_vulkan.GLFW_DECORATED, 0),
                                (mp_liquid_visualizer_vulkan.GLFW_VISIBLE, 1)):
                glfw.glfwWindowHint(hint, value)
            window = glfw.glfwCreateWindow(1094, 778, b"shape-test", None, None)
            if not window:
                glfw.glfwTerminate()
                return None
            glfw.glfwPollEvents()
            return glfw, window
        except Exception:
            return None

    def test_x11_mask_is_rounded_and_restores(self):
        if sys.platform.startswith("win") or sys.platform == "darwin":
            self.skipTest("X11 shape test")
        created = self._x11_window()
        if created is None:
            self.skipTest("no X11 display")
        import ctypes
        from ctypes import byref, c_int, c_uint64, c_void_p

        glfw, window = created

        class XRectangle(ctypes.Structure):
            _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short),
                        ("width", ctypes.c_ushort), ("height", ctypes.c_ushort)]

        x11 = ctypes.CDLL("libX11.so.6")
        x11.XSync.argtypes = [c_void_p, c_int]
        x11.XFree.argtypes = [c_void_p]
        ext = ctypes.CDLL("libXext.so.6")
        ext.XShapeGetRectangles.restype = ctypes.POINTER(XRectangle)
        ext.XShapeGetRectangles.argtypes = [c_void_p, c_uint64, c_int,
                                            ctypes.POINTER(c_int), ctypes.POINTER(c_int)]

        display = glfw.glfwGetX11Display(window)
        xid = glfw.glfwGetX11Window(window)

        def rect_count():
            count, ordering = c_int(), c_int()
            rects = ext.XShapeGetRectangles(display, xid, 0, byref(count), byref(ordering))
            first = (rects[0].x, rects[0].y, rects[0].width, rects[0].height) if rects else None
            if rects:
                x11.XFree(rects)
            return count.value, first

        try:
            before, plain = rect_count()
            self.assertEqual(before, 1, "an unshaped window is one rectangle")

            shaper = visualizer_window_shape.RoundedWindowShaper(visualizer_window_shape.DEFAULT_CORNER_RADIUS)
            self.assertTrue(shaper.apply(width=1094, height=778, enabled=True,
                                         x11_window=xid, x11_display=display))
            x11.XSync(display, 0)
            shaped, first = rect_count()
            self.assertGreater(shaped, 1, "the rounded mask is many scanline rectangles")
            # The first scanline starts inside the corner, by the radius.
            self.assertEqual(first[0], visualizer_window_shape.DEFAULT_CORNER_RADIUS)
            self.assertEqual(first[1], 0)

            self.assertTrue(shaper.apply(width=1, height=1, enabled=False,
                                         x11_window=xid, x11_display=display))
            x11.XSync(display, 0)
            restored, _ = rect_count()
            self.assertEqual(restored, 1, "removing the mask restores the rectangle")
        finally:
            glfw.glfwDestroyWindow(window)
            glfw.glfwTerminate()

    def test_shaper_is_idempotent(self):
        shaper = visualizer_window_shape.RoundedWindowShaper(36)
        shaper._applied = (1234, 800, 600, True)
        # The same request is answered from the cache without touching X11.
        with patch.object(shaper, "_apply_x11") as apply_x11:
            self.assertTrue(shaper.apply(width=800, height=600, enabled=True, x11_window=1234))
            apply_x11.assert_not_called()

    def test_dead_window_probe_returns_none_instead_of_aborting(self):
        """XGetGeometry on a dead id raises BadWindow, which exits Xlib by default."""
        shaper = visualizer_window_shape.RoundedWindowShaper(36)
        if not shaper._ensure_x11():
            self.skipTest("no X11")
        x11 = ctypes.CDLL("libX11.so.6")
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        display = x11.XOpenDisplay(None)
        if not display:
            self.skipTest("no X display")
        # The important part is that this returns rather than killing the
        # process through Xlib's default error handler.
        self.assertIsNone(shaper._x11_geometry(display, 0x0BADF00D))

    def test_windows_region_uses_the_player_radius(self):
        """SetWindowRgn gets a 2×36 px diameter, the same as the player's mask."""
        shaper = visualizer_window_shape.RoundedWindowShaper(36)
        gdi32 = MagicMock()
        gdi32.CreateRoundRectRgn.return_value = 4242
        user32 = MagicMock()
        user32.SetWindowRgn.return_value = 1
        fake_windll = MagicMock(gdi32=gdi32, user32=user32)
        fake_windll.windll = fake_windll
        with patch.object(visualizer_window_shape.ctypes, "windll", fake_windll, create=True), \
             patch.object(visualizer_window_shape.sys, "platform", "win32"):
            self.assertTrue(shaper.apply(width=1094, height=778, enabled=True, win32_window=99))
        args = gdi32.CreateRoundRectRgn.call_args.args
        self.assertEqual(args, (0, 0, 1095, 779, 72, 72))
        user32.SetWindowRgn.assert_called_once()

    def test_windows_unshape_clears_the_region(self):
        shaper = visualizer_window_shape.RoundedWindowShaper(36)
        user32 = MagicMock()
        user32.SetWindowRgn.return_value = 1
        fake_windll = MagicMock(gdi32=MagicMock(), user32=user32)
        fake_windll.windll = fake_windll
        with patch.object(visualizer_window_shape.ctypes, "windll", fake_windll, create=True), \
             patch.object(visualizer_window_shape.sys, "platform", "win32"):
            self.assertTrue(shaper.apply(width=1, height=1, enabled=False, win32_window=99))
        # The NULL region restores the rectangle.
        self.assertIsNone(user32.SetWindowRgn.call_args.args[1])


class TestFullscreenStaysRectangular(unittest.TestCase):
    def test_vulkan_shaper_skips_fullscreen_screensaver_and_vr(self):
        renderer = mp_liquid_visualizer_vulkan.LiquidVisualizerVulkanRenderer.__new__(
            mp_liquid_visualizer_vulkan.LiquidVisualizerVulkanRenderer
        )
        renderer._shaper = MagicMock()
        renderer._shaper.apply.return_value = True
        renderer._window = MagicMock()
        renderer.is_fullscreen = True
        renderer.screensaver = False
        renderer._saver_bounds = None
        renderer.window_size = MagicMock(return_value=(1920, 1080))
        renderer.x11_window = MagicMock(return_value=0)
        renderer.x11_display = MagicMock(return_value=None)
        renderer.win32_window = MagicMock(return_value=0)
        renderer.cocoa_window = MagicMock(return_value=0)

        renderer._apply_window_shape()
        self.assertFalse(renderer._shaper.apply.call_args.kwargs["enabled"])

        renderer.is_fullscreen = False
        renderer.screensaver = True
        renderer._apply_window_shape()
        self.assertFalse(renderer._shaper.apply.call_args.kwargs["enabled"])

        renderer.screensaver = False
        renderer._saver_bounds = (0, 0, 1920, 1080)
        renderer._apply_window_shape()
        self.assertFalse(renderer._shaper.apply.call_args.kwargs["enabled"])

        renderer._saver_bounds = None
        with patch.object(mp_liquid_visualizer_vulkan, "_vr_visualizer_side", return_value=1200):
            renderer._apply_window_shape()
        self.assertFalse(renderer._shaper.apply.call_args.kwargs["enabled"])

    def test_windowed_visualizer_uses_its_own_window_size(self):
        renderer = mp_liquid_visualizer_vulkan.LiquidVisualizerVulkanRenderer.__new__(
            mp_liquid_visualizer_vulkan.LiquidVisualizerVulkanRenderer
        )
        renderer._shaper = MagicMock()
        renderer._shaper.apply.return_value = True
        renderer._window = MagicMock()
        renderer.is_fullscreen = False
        renderer.screensaver = False
        renderer._saver_bounds = None
        renderer._windowed_size = (1094, 778)
        renderer.width, renderer.height = 1094, 778
        renderer.window_size = MagicMock(return_value=(1094, 778))
        renderer.x11_window = MagicMock(return_value=777)
        renderer.x11_display = MagicMock(return_value=888)
        renderer.win32_window = MagicMock(return_value=0)
        renderer.cocoa_window = MagicMock(return_value=0)

        with patch.object(mp_liquid_visualizer_vulkan, "_vr_visualizer_side", return_value=None):
            self.assertTrue(renderer._apply_window_shape())
        kwargs = renderer._shaper.apply.call_args.kwargs
        self.assertTrue(kwargs["enabled"])
        self.assertEqual((kwargs["width"], kwargs["height"]), (1094, 778))
        self.assertEqual(kwargs["x11_window"], 777)


class TestOpenGLVisualizerShapesItsWindow(unittest.TestCase):
    def test_opengl_renderer_exposes_native_handles(self):
        renderer = liquid_player_visualizer.LiquidVisualizerOpenGLRenderer("cosmic_dreamworlds_liquid")
        for name in ("win32_window", "cocoa_window", "x11_window", "x11_display", "_apply_window_shape"):
            self.assertTrue(callable(getattr(renderer, name)), name)

    def test_opengl_draw_keeps_the_shape_in_step(self):
        source = Path(liquid_player_visualizer.__file__).read_text(encoding="utf-8")
        # Once on open, once per draw so a resize or mode switch is covered.
        self.assertGreaterEqual(source.count("self._apply_window_shape()"), 2)


class TestMetalVisualizerShapesItsWindow(unittest.TestCase):
    """macOS runs the standalone visualizer on Metal, which had no rounding.

    The player's glass is a 36 px rounded rectangle. A square Metal window
    behind it showed its own corners through the player's cut-aways, so the
    Metal backend now applies the same mask the other two backends use.
    """

    def _renderer(self):
        renderer = mp_liquid_visualizer_metal.LiquidVisualizerMetalRenderer(
            "cosmic_dreamworlds_liquid")
        return renderer

    def test_metal_renderer_exposes_the_shape_hooks(self):
        renderer = self._renderer()
        for name in ("cocoa_window", "_apply_window_shape"):
            self.assertTrue(callable(getattr(renderer, name)), name)

    def test_metal_shape_is_applied_on_open_and_every_poll(self):
        source = Path(mp_liquid_visualizer_metal.__file__).read_text(encoding="utf-8")
        # Once while opening, and once per poll so resize/mode changes follow.
        self.assertGreaterEqual(source.count("self._apply_window_shape()"), 2)

    def test_metal_window_is_windowed_and_uses_the_player_radius(self):
        renderer = self._renderer()
        renderer._window = MagicMock()
        renderer.width, renderer.height = 1094, 778
        renderer._shaper = MagicMock()
        renderer._shaper.apply.return_value = True
        renderer._apply_window_shape()
        kwargs = renderer._shaper.apply.call_args.kwargs
        self.assertTrue(kwargs["enabled"])
        self.assertEqual((kwargs["width"], kwargs["height"]), (1094, 778))

    def test_metal_fullscreen_and_screensaver_stay_rectangular(self):
        renderer = self._renderer()
        renderer._window = MagicMock()
        renderer._shaper = MagicMock()
        renderer._shaper.apply.return_value = True
        for state in ("fullscreen", "screensaver", "saver_bounds"):
            renderer.is_fullscreen = state == "fullscreen"
            renderer.screensaver = state == "screensaver"
            renderer._saver_bounds = (0, 0, 1920, 1080) if state == "saver_bounds" else None
            renderer._apply_window_shape()
            self.assertFalse(renderer._shaper.apply.call_args.kwargs["enabled"], state)

    def test_macos_shaper_clears_the_opaque_flags(self):
        """An opaque NSWindow/CAMetalLayer fills the cut-aways with black.

        The macOS branch is reached only on ``sys.platform == "darwin"``, so
        pin the calls it makes rather than driving Objective-C here: while the
        window is shaped, both the NSWindow and the content layer must have
        their opaque flag cleared, or the rounded corners paint solid black.
        """
        source = Path(visualizer_window_shape.__file__).read_text(encoding="utf-8")
        macos_branch = source.split("def _apply_macos", 1)[1]
        self.assertIn('send_bool(window, sel(b"setOpaque:"), False)', macos_branch)
        self.assertIn('send_bool(c_void_p(layer), sel(b"setOpaque:"), False)', macos_branch)
        # The unshaped rectangle is the only case that goes back to opaque.
        self.assertIn('send_bool(window, sel(b"setOpaque:"), True)', macos_branch)

    def test_metal_layer_starts_non_opaque(self):
        """The layer must not be opaque, or the mask has nothing transparent."""
        source = Path(mp_liquid_visualizer_metal.__file__).read_text(encoding="utf-8")
        self.assertIn('objc.send(layer, "setOpaque:", c_int(0)', source)
        self.assertIn('objc.send(nswindow, "setOpaque:", c_int(0)', source)
        self.assertNotIn('objc.send(layer, "setOpaque:", c_int(1)', source)


if __name__ == "__main__":
    unittest.main()
