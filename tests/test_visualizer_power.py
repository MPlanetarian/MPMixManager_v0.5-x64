#!/usr/bin/env python3
"""Unit tests for the MP Visualizer's GPU power policy.

The visualizer used to re-raymarch a full frame at the display rate no matter
what the player was doing. These tests pin the three levers that stop that,
plus the 4K fullscreen cap:

  1. While the player is paused/stopped the scene is drawn at the idle rate,
     not at the display's refresh rate. A playing window is untouched.
  2. A minimized or hidden window draws nothing at all, and the loop only
     polls for it coming back.
  3. The draw interval chosen by the loop follows those rules even though the
     display refresh is the ceiling.
  4. A 4K fullscreen surface is raymarched at 1080p and scaled up, on every
     backend, while a windowed 4K surface is still drawn natively.

Everything here runs without a GPU: the renderers are fakes and the real
draw() methods are exercised with a stubbed poll()/view_hidden().
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _path in (ROOT, ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import liquid_player_visualizer as gl_vis
import mp_liquid_visualizer_vulkan as vis

FRAME_60HZ = 1.0 / 60.0


class _Bridge:
    def __init__(self, playing: bool):
        self.is_playing = playing


class _Renderer:
    """Stand-in for the host-specific bits of a renderer."""

    def __init__(self, hidden: bool = False):
        self._hidden = hidden

    def view_hidden(self) -> bool:
        return self._hidden


class TestIdleFrameRate(unittest.TestCase):
    """A paused player must not cost a full frame every refresh."""

    def setUp(self):
        self._saved = os.environ.get("MP_VISUALIZER_IDLE_FPS")
        os.environ.pop("MP_VISUALIZER_IDLE_FPS", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("MP_VISUALIZER_IDLE_FPS", None)
        else:
            os.environ["MP_VISUALIZER_IDLE_FPS"] = self._saved

    def test_default_idle_rate_is_below_the_display_rate(self):
        self.assertEqual(vis.idle_fps(), vis.IDLE_FPS_DEFAULT)
        self.assertLess(vis.idle_fps(), 60)

    def test_paused_window_is_slowed_to_the_idle_rate(self):
        interval = vis.paced_interval(FRAME_60HZ, playing=False, hidden=False)
        self.assertAlmostEqual(1.0 / interval, float(vis.IDLE_FPS_DEFAULT), places=2)

    def test_playing_window_keeps_the_display_rate(self):
        self.assertEqual(vis.paced_interval(FRAME_60HZ, playing=True, hidden=False), FRAME_60HZ)

    def test_hidden_window_polls_slowly_even_while_playing(self):
        interval = vis.paced_interval(FRAME_60HZ, playing=True, hidden=True)
        self.assertGreaterEqual(interval, vis.HIDDEN_POLL_INTERVAL)

    def test_hidden_poll_is_the_slowest_state(self):
        """15 Hz display, paused, hidden: the hidden poll is still the ceiling."""
        slow = 1.0 / 15.0
        self.assertEqual(vis.paced_interval(slow, playing=True, hidden=True),
                         vis.HIDDEN_POLL_INTERVAL)
        # Without the hidden state the display rate is left alone.
        self.assertEqual(vis.paced_interval(slow, playing=True, hidden=False), slow)

    def test_idle_rate_can_be_disabled(self):
        os.environ["MP_VISUALIZER_IDLE_FPS"] = "0"
        self.assertEqual(vis.idle_fps(), 0)
        self.assertEqual(vis.paced_interval(FRAME_60HZ, playing=False, hidden=False), FRAME_60HZ)

    def test_idle_rate_env_override_is_clamped(self):
        os.environ["MP_VISUALIZER_IDLE_FPS"] = "3"
        self.assertEqual(vis.idle_fps(), 5)
        os.environ["MP_VISUALIZER_IDLE_FPS"] = "500"
        self.assertEqual(vis.idle_fps(), 60)
        os.environ["MP_VISUALIZER_IDLE_FPS"] = "nonsense"
        self.assertEqual(vis.idle_fps(), vis.IDLE_FPS_DEFAULT)

    def test_loop_interval_reads_the_bridge_and_the_window(self):
        self.assertEqual(
            vis.loop_interval(_Renderer(), _Bridge(playing=True), FRAME_60HZ), FRAME_60HZ)
        idle = vis.loop_interval(_Renderer(), _Bridge(playing=False), FRAME_60HZ)
        self.assertGreater(idle, FRAME_60HZ)
        hidden = vis.loop_interval(_Renderer(hidden=True), _Bridge(playing=True), FRAME_60HZ)
        self.assertGreaterEqual(hidden, vis.HIDDEN_POLL_INTERVAL)

    def test_renderer_without_the_probe_is_treated_as_visible(self):
        class Bare:
            pass
        self.assertEqual(
            vis.loop_interval(Bare(), _Bridge(playing=True), FRAME_60HZ), FRAME_60HZ)


class TestHiddenWindowSkipsDrawing(unittest.TestCase):
    """A window nobody can see must not encode a frame."""

    def _vulkan_draw(self, hidden: bool, capture: bool):
        r = object.__new__(vis.LiquidVisualizerVulkanRenderer)
        r.poll = lambda: True
        r.view_hidden = lambda: hidden
        r.poll_random_mode = lambda: False
        r._fb_size = None
        rendered = {"count": 0}

        def _render(_capture):
            rendered["count"] += 1
            return None
        r._render = _render
        # Anything below the skip would touch the real framebuffer query.
        r.framebuffer_size = lambda: (64, 64)
        r._resize = False
        r._extent = (64, 64)
        r._apply_window_shape = lambda: None
        r._write_ubo = lambda bridge: None
        r._frame_dt = lambda: 1 / 60.0
        r.draw(_Bridge(playing=True), capture)
        return rendered["count"]

    def test_hidden_window_skips_the_render(self):
        self.assertEqual(self._vulkan_draw(hidden=True, capture=False), 0)

    def test_visible_window_still_renders(self):
        self.assertEqual(self._vulkan_draw(hidden=False, capture=False), 1)

    def test_capture_still_renders_a_hidden_window(self):
        """--self-test reads the pixels back, so that path must always draw."""
        self.assertEqual(self._vulkan_draw(hidden=True, capture=True), 1)

    def test_opengl_hidden_window_skips_the_render(self):
        import types

        r = object.__new__(gl_vis.LiquidVisualizerOpenGLRenderer)
        r.poll = lambda: True
        r.view_hidden = lambda: True
        r.poll_random_mode = lambda: False
        r.framebuffer_size = lambda: (64, 64)
        layer = types.SimpleNamespace(draw_with_state=lambda *a, **k: True, _error="")
        r._layer = layer
        r._glfw = types.SimpleNamespace(glfwSwapBuffers=lambda *a: None)
        r._window = 1
        r.preset = {}
        worked = {"count": 0}

        def _draw_with_state(*_a, **_k):
            worked["count"] += 1
            return True
        layer.draw_with_state = _draw_with_state
        fake_gl = types.SimpleNamespace(GL=types.SimpleNamespace())
        sys.modules.setdefault("OpenGL", fake_gl)
        sys.modules.setdefault("OpenGL.GL", types.SimpleNamespace())
        # _apply_window_shape is checked before the layer is reached.
        r._apply_window_shape = lambda: None
        out = r.draw(_Bridge(playing=True), capture=False)
        self.assertIsNone(out)
        self.assertEqual(worked["count"], 0)


class TestMetalDrawPath(unittest.TestCase):
    """The Metal backend shares the same two gates without a Mac."""

    def _draw(self, hidden: bool, capture: bool):
        import mp_liquid_visualizer_metal as metal

        r = object.__new__(metal.LiquidVisualizerMetalRenderer)
        r.poll = lambda: True
        r.view_hidden = lambda: hidden
        r.poll_random_mode = lambda: False
        r._frame_dt = lambda: 1 / 60.0
        r._last_draw_at = 0.0
        r._gpu_time_ms = lambda: 0.0
        r._ensure_targets = lambda: None
        r._write_ubo = lambda state: None
        encoded = {"count": 0}
        r._encode_frame = lambda capture=False: encoded.__setitem__("count", encoded["count"] + 1)
        r._frame_texture = None
        r._capture = lambda: None
        r.draw(_Bridge(playing=True), capture)
        return encoded["count"]

    def test_hidden_metal_window_encodes_nothing(self):
        self.assertEqual(self._draw(hidden=True, capture=False), 0)

    def test_visible_metal_window_encodes_one_frame(self):
        self.assertEqual(self._draw(hidden=False, capture=False), 1)

    def test_metal_capture_still_encodes_a_hidden_window(self):
        self.assertEqual(self._draw(hidden=True, capture=True), 1)

    def test_metal_adaptive_scale_lowers_the_long_edge_only_when_enabled(self):
        import mp_liquid_visualizer_metal as metal

        r = object.__new__(metal.LiquidVisualizerMetalRenderer)
        r._resolution_scale = 1.0
        base = metal.march_long_edge()
        self.assertEqual(r._long_edge_for_frame(), base)
        r._resolution_scale = 0.5
        self.assertLess(r._long_edge_for_frame(), base)
        self.assertGreaterEqual(r._long_edge_for_frame(), metal.MIN_MARCH_SIDE)


class TestFourKFullscreenRenderCap(unittest.TestCase):
    """A 4K fullscreen surface is drawn at 1080p; a windowed one is not."""

    def test_fullscreen_4k_is_capped_to_1080p(self):
        self.assertEqual(gl_vis.fullscreen_render_cap(3840, 2160, True), (1920, 1080))

    def test_windowed_4k_is_left_alone(self):
        self.assertEqual(gl_vis.fullscreen_render_cap(3840, 2160, False), (3840, 2160))

    def test_fullscreen_below_4k_is_left_alone(self):
        self.assertEqual(gl_vis.fullscreen_render_cap(2560, 1440, True), (2560, 1440))
        self.assertEqual(gl_vis.fullscreen_render_cap(1920, 1080, True), (1920, 1080))

    def test_cap_keeps_the_aspect_ratio_of_a_4k_surface_with_a_bar(self):
        width, height = gl_vis.fullscreen_render_cap(3840, 2120, True)
        self.assertEqual(width, 1920)
        self.assertLess(height, 1080)

    def test_vulkan_framebuffer_policy_caps_a_native_4k_surface(self):
        r = object.__new__(vis.LiquidVisualizerVulkanRenderer)
        r.is_fullscreen = True
        r._is_4k_cached = False
        r._fb_size = (3840, 2160)
        self.assertEqual(r.framebuffer_size(), (1920, 1080))

    def test_vulkan_windowed_framebuffer_is_not_capped(self):
        r = object.__new__(vis.LiquidVisualizerVulkanRenderer)
        r.is_fullscreen = False
        r._is_4k_cached = True
        r._fb_size = (3840, 2160)
        self.assertEqual(r.framebuffer_size(), (3840, 2160))

    @unittest.skipIf(__import__("sys").platform == "darwin",
                     "macOS draws through Metal, not the OpenGL layer")
    def test_opengl_fullscreen_draw_chooses_1080p(self):
        """The OpenGL layer draws a 4K fullscreen surface through a 1080p target."""
        import types

        picked = {}

        class FakeLayer(gl_vis.PlayerVisualizerLayer):
            def _compile(self, GL, renderer=None, preset_id=0):
                return True

            def _ensure_cover(self, GL, state):
                return None

            def _ensure_target(self, GL, rw, rh):
                picked["target"] = (rw, rh)
                return True

            def _ensure_upscale(self, GL):
                return True

            def _draw_scene(self, GL, renderer, rw, rh, bridge, text_guard=True):
                picked["scene"] = (rw, rh)

            def _draw_upscale(self, GL, renderer):
                picked["upscaled"] = True

            def _ensure_bridge(self):
                return _Bridge(playing=True)

        class FakeRenderer:
            is_fullscreen = True
            _preset_id_float = 0.0
            screensaver = False
            _saver_render = None

            def window_size(self):
                return (3840, 2160)

        # _draw binds the target framebuffer and restores the viewport around the
        # scene, so those two GL entry points have to exist.
        class FakeGL:
            GL_FRAMEBUFFER = GL_VIEWPORT = 0

            def glBindFramebuffer(self, *_a):
                pass

            def glViewport(self, *_a):
                pass

            def __getattr__(self, _name):
                return lambda *_a, **_k: None

        sys.modules["OpenGL"] = types.SimpleNamespace(GL=FakeGL())
        sys.modules["OpenGL.GL"] = FakeGL()
        layer = FakeLayer()
        layer._last = 0.0
        state = {"active": True, "preset_id": 0, "preset_key": "cosmic_dreamworlds_liquid"}
        layer._draw(FakeRenderer(), 3840, 2160, state, bridge=_Bridge(playing=True),
                    text_guard=False)
        self.assertEqual(picked["scene"], (1920, 1080))
        self.assertEqual(picked["target"], (1920, 1080))
        self.assertTrue(picked["upscaled"])


if __name__ == "__main__":
    unittest.main()
