#!/usr/bin/env python3
"""The Z key window cycle, shared by every MP Visualizer backend.

A window the user opens with **Z** from the MP Liquid Player cycles on
successive presses:

    original size  ->  fullscreen  ->  half the Liquid Player  ->  original size

These tests pin the three properties that were broken or missing:

1. The third press restores the **size and position the window had when Z was
   first pressed**, not a constructor guess.
2. The half step is half of the **MP Liquid Player's** published window, so the
   shrunken visualizer sits at half of the player it was matching.
3. The companion window the player starts on its own never cycles. It is owned
   by the player and must stay behind it at the player's rectangle.

The cycle is one mixin so the Vulkan, Metal and OpenGL backends cannot drift
apart. The old OpenGL copy halved the window on *every* press, because it
overwrote its own default size with the half size, and the Vulkan version
restored the constructor size/offset instead of the one the window really had.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
for _path in (ROOT, ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import liquid_player_visualizer as open_gl  # noqa: E402
import mp_liquid_visualizer_metal as metal  # noqa: E402
import mp_liquid_visualizer_vulkan as vulkan  # noqa: E402
import visualizer_z_cycle as zcycle  # noqa: E402


class _FakeWindow:
    """A renderer with just enough geometry to observe the cycle.

    ``_z_apply_windowed`` records the requested geometry and makes it the live
    one, which is exactly what a real backend does through GLFW.
    """

    def __init__(self, size=(1574, 1258), pos=(510, 385), mode=zcycle.WINDOW_MODE_DEFAULT):
        self._size = tuple(size)
        self._pos = tuple(pos)
        self.window_mode = mode
        self.is_fullscreen = mode == zcycle.WINDOW_MODE_FULLSCREEN
        self._user_selected_window_mode = False
        self._player_bonded = False
        self._bond_locked = False
        self._default_windowed_size = tuple(size)
        self._default_windowed_offset = tuple(pos)
        self._half_windowed_size = (size[0] // 2, size[1] // 2)
        self._half_windowed_offset = pos
        self._z_original_size = None
        self._z_original_pos = None
        self.entered_fullscreen = 0
        self.applied = []

    # renderer primitives
    def _z_window_size(self):
        return self._size

    def _z_window_pos(self):
        return self._pos

    def _z_apply_windowed(self, width, height, x, y):
        self.applied.append((int(width), int(height), int(x), int(y)))
        self._size = (int(width), int(height))
        self._pos = (int(x), int(y))
        self.is_fullscreen = False

    def toggle_fullscreen(self):
        self.is_fullscreen = not self.is_fullscreen
        if self.is_fullscreen:
            self.entered_fullscreen += 1
        return self.is_fullscreen

    def _bond_is_locked(self):
        return self._bond_locked


class _Cycle(_FakeWindow, zcycle.ZWindowCycleMixin):
    """The mixin on top of the fake renderer."""


class TestTheCycleItself(unittest.TestCase):
    def test_three_presses_return_to_the_original_shape(self):
        # No live MP Liquid Player here: the half step must use the captured
        # original shape, not whatever window this machine happens to have open.
        window = _Cycle(size=(1574, 1258), pos=(510, 385))
        window._z_player_size = lambda: None
        original = (window._size, window._pos)

        self.assertEqual(window.cycle_window_mode(), zcycle.WINDOW_MODE_FULLSCREEN)
        self.assertTrue(window.is_fullscreen)
        self.assertEqual(window.entered_fullscreen, 1)

        self.assertEqual(window.cycle_window_mode(), zcycle.WINDOW_MODE_HALF)
        self.assertFalse(window.is_fullscreen)
        half_size, half_pos = window._size, window._pos
        self.assertEqual(half_size, (787, 629))

        self.assertEqual(window.cycle_window_mode(), zcycle.WINDOW_MODE_DEFAULT)
        self.assertEqual((window._size, window._pos), original)

        # A fourth press re-enters fullscreen and keeps the same original.
        self.assertEqual(window.cycle_window_mode(), zcycle.WINDOW_MODE_FULLSCREEN)
        self.assertEqual((window._z_original_size, window._z_original_pos), original)
        # The half step is where it was the first time, not re-derived from
        # whatever the window happened to be showing.
        self.assertEqual(window._half_windowed_size, half_size)

    def test_the_original_is_captured_from_the_live_window_on_the_first_press(self):
        """The player refits after measuring its font; the first Z must win.

        A window opened before the refit has one size, and by the time the user
        presses Z it has another. Capturing the constructor value restores a
        size the user never saw.
        """
        window = _Cycle(size=(1094, 778), pos=(72, 64))
        window._size = (1574, 1258)          # the player's later published size
        window._pos = (3389, 200)
        window._default_windowed_size = (1574, 1258)
        window._default_windowed_offset = (3389, 200)

        window.cycle_window_mode()            # -> fullscreen captures it
        self.assertEqual(window._z_original_size, (1574, 1258))
        self.assertEqual(window._z_original_pos, (3389, 200))

        window.cycle_window_mode()            # -> half
        window.cycle_window_mode()            # -> original
        self.assertEqual(window._size, (1574, 1258))
        self.assertEqual(window._pos, (3389, 200))

    def test_half_is_half_of_the_liquid_player_not_of_the_visualizer(self):
        """The user asks for half of the player the window was matching."""
        window = _Cycle(size=(1574, 1258), pos=(510, 385))
        with patch.object(_Cycle, "_z_player_size", return_value=(1574, 1258)):
            window.cycle_window_mode()        # -> fullscreen
            window.cycle_window_mode()        # -> half
        self.assertEqual(window._size, (787, 629))

        # A player that is a different size than the window scales that size.
        other = _Cycle(size=(1094, 778), pos=(72, 64))
        with patch.object(_Cycle, "_z_player_size", return_value=(1574, 1258)):
            other.cycle_window_mode()
            other.cycle_window_mode()
        self.assertEqual(other._size, (787, 629))

    def test_half_falls_back_to_the_windows_own_size_without_a_player(self):
        window = _Cycle(size=(1094, 778), pos=(72, 64))
        with patch.object(_Cycle, "_z_player_size", return_value=None):
            window.cycle_window_mode()
            window.cycle_window_mode()
        self.assertEqual(window._size, (547, 389))

    def test_half_never_goes_below_the_minimum_side(self):
        window = _Cycle(size=(100, 90), pos=(0, 0))
        with patch.object(_Cycle, "_z_player_size", return_value=(100, 90)):
            window.cycle_window_mode()
            window.cycle_window_mode()
        self.assertGreaterEqual(window._size[0], zcycle.MIN_WINDOW_SIDE)
        self.assertGreaterEqual(window._size[1], zcycle.MIN_WINDOW_SIDE)

    def test_half_is_centred_on_the_original_window(self):
        window = _Cycle(size=(1000, 800), pos=(100, 100))
        with patch.object(_Cycle, "_z_player_size", return_value=(1000, 800)):
            window.cycle_window_mode()
            window.cycle_window_mode()
        # Centre of the original is (600, 500); the 500x400 half is centred there.
        self.assertEqual(window._pos, (350, 300))

    def test_the_chosen_mode_is_marked_as_the_users_choice(self):
        """The player bond must not resize a Z-shaped window back."""
        window = _Cycle()
        window.cycle_window_mode()
        self.assertTrue(window._user_selected_window_mode)


class TestTheCompanionIsNeverCycled(unittest.TestCase):
    """The window the Liquid Player starts itself is owned by the player."""

    def test_locked_companion_ignores_z(self):
        window = _Cycle()
        window._bond_locked = True
        before = (window._size, window._pos, window.window_mode)
        for _ in range(3):
            self.assertEqual(window._cycle_window_mode_for_user(), window.window_mode)
        self.assertEqual((window._size, window._pos, window.window_mode), before)
        self.assertEqual(window.applied, [])
        self.assertFalse(window.is_fullscreen)

    def test_bonded_backdrop_ignores_z(self):
        window = _Cycle()
        window._player_bonded = True
        before = (window._size, window._pos, window.window_mode)
        window._cycle_window_mode_for_user()
        self.assertEqual((window._size, window._pos, window.window_mode), before)
        self.assertEqual(window.applied, [])

    def test_a_free_window_still_cycles(self):
        window = _Cycle()
        window._cycle_window_mode_for_user()
        self.assertTrue(window.is_fullscreen)


class TestEveryBackendSharesOneCycle(unittest.TestCase):
    """All three renderers must expose the mixin, not a private copy."""

    def test_backends_mix_in_the_cycle(self):
        for cls in (vulkan.LiquidVisualizerVulkanRenderer,
                    metal.LiquidVisualizerMetalRenderer,
                    open_gl.LiquidVisualizerOpenGLRenderer):
            with self.subTest(backend=cls.__name__):
                self.assertTrue(issubclass(cls, zcycle.ZWindowCycleMixin))
                self.assertIn(zcycle.ZWindowCycleMixin, cls.__mro__)

    def test_backends_implement_the_geometry_primitives(self):
        for cls in (vulkan.LiquidVisualizerVulkanRenderer,
                    metal.LiquidVisualizerMetalRenderer,
                    open_gl.LiquidVisualizerOpenGLRenderer):
            with self.subTest(backend=cls.__name__):
                for name in ("_z_window_size", "_z_window_pos", "_z_apply_windowed"):
                    self.assertTrue(callable(getattr(cls, name, None)), name)

    def test_the_mode_constants_live_in_one_place(self):
        self.assertIs(vulkan.WINDOW_MODE_HALF, zcycle.WINDOW_MODE_HALF)
        self.assertIs(vulkan.WINDOW_MODE_DEFAULT, zcycle.WINDOW_MODE_DEFAULT)
        self.assertIs(vulkan.WINDOW_MODE_FULLSCREEN, zcycle.WINDOW_MODE_FULLSCREEN)
        self.assertEqual(open_gl.WINDOW_MODE_HALF, zcycle.WINDOW_MODE_HALF)


class TestTheOpenGLTwinDoesNotShrinkForever(unittest.TestCase):
    """The old OpenGL copy halved the window on every Z press.

    It cycled fullscreen -> default by resizing to ``self._size`` and then set
    ``window_mode = "half"``, so the next press halved the half. Pressing Z
    three times left a quarter-size window and the size never came back.
    """

    def _renderer(self):
        renderer = object.__new__(open_gl.LiquidVisualizerOpenGLRenderer)
        renderer.screensaver = False
        renderer._glfw = MagicMock()
        renderer._window = MagicMock()
        renderer._spawned_detached = False
        renderer.is_fullscreen = False
        renderer.window_mode = zcycle.WINDOW_MODE_DEFAULT
        renderer._size = (1000, 800)
        renderer._windowed_size = (1000, 800)
        renderer.width, renderer.height = (1000, 800)
        renderer._cur_pos = (100, 100)
        renderer._default_windowed_size = (1000, 800)
        renderer._half_windowed_size = (500, 400)
        renderer._user_selected_window_mode = False
        renderer._z_original_size = None
        renderer._z_original_pos = None
        renderer._resize = False
        renderer._span_bounds = None
        renderer._span_role = ""
        renderer._saver_bounds = None
        renderer._init_player_bond()
        renderer._player_bonded = False
        renderer._bond_locked = False
        return renderer

    def test_size_returns_after_the_cycle_instead_of_halving_again(self):
        renderer = self._renderer()
        with patch.object(type(renderer), "_z_player_size", return_value=(1000, 800)):
            sizes = []
            for _ in range(6):
                renderer.cycle_window_mode()
                sizes.append(renderer._size)
        self.assertEqual(sizes[0], (1000, 800))   # fullscreen keeps the size
        self.assertEqual(sizes[1], (500, 400))    # half
        self.assertEqual(sizes[2], (1000, 800))   # back, not (250, 200)
        self.assertEqual(sizes[3], (1000, 800))
        self.assertEqual(sizes[4], (500, 400))
        self.assertEqual(sizes[5], (1000, 800))

    def test_z_key_reaches_the_cycle(self):
        renderer = self._renderer()
        renderer._on_key(None, 90, 0, 1, 0)   # Z
        self.assertTrue(renderer.is_fullscreen)
        self.assertEqual(renderer.window_mode, zcycle.WINDOW_MODE_FULLSCREEN)

    def test_locked_companion_z_key_is_a_no_op(self):
        renderer = self._renderer()
        renderer._bond_locked = True
        renderer._on_key(None, 90, 0, 1, 0)
        self.assertFalse(renderer.is_fullscreen)
        self.assertEqual(renderer._size, (1000, 800))


class TestTheMetalRendererStartsInTheRightMode(unittest.TestCase):
    """``--fullscreen`` must not make the first Z read as "leave fullscreen"."""

    def test_fullscreen_on_start_sets_the_fullscreen_mode(self):
        renderer = metal.LiquidVisualizerMetalRenderer(fullscreen=True)
        self.assertEqual(renderer.window_mode, zcycle.WINDOW_MODE_FULLSCREEN)

    def test_default_start_sets_the_default_mode(self):
        renderer = metal.LiquidVisualizerMetalRenderer()
        self.assertEqual(renderer.window_mode, zcycle.WINDOW_MODE_DEFAULT)

    def test_z_key_uses_the_shared_cycle(self):
        src = (ROOT / "scripts" / "mp_liquid_visualizer_metal.py").read_text(encoding="utf-8")
        self.assertIn("self._cycle_window_mode_for_user()", src)
        self.assertNotIn("def cycle_window_mode", src)

    def test_leaving_fullscreen_goes_through_set_window_monitor(self):
        """A plain resize would leave the window owning the whole screen.

        GLFW only drops the fullscreen monitor mode through
        ``glfwSetWindowMonitor(window, NULL, …)``; resizing alone keeps the
        window monitored, so the half step has to call it too.
        """
        renderer = metal.LiquidVisualizerMetalRenderer()
        glfw = MagicMock()
        renderer._glfw = glfw
        renderer._window = MagicMock()
        renderer._windowed_size = (1094, 734)
        renderer.width, renderer.height = (1094, 734)
        renderer._cur_pos = (160, 80)
        renderer.is_fullscreen = False
        renderer.window_mode = zcycle.WINDOW_MODE_DEFAULT
        renderer._resize = False

        renderer._cycle_window_mode_for_user()     # -> fullscreen
        self.assertTrue(renderer.is_fullscreen)
        renderer._cycle_window_mode_for_user()     # -> half
        self.assertFalse(renderer.is_fullscreen)
        glfw.glfwSetWindowMonitor.assert_called()
        args = glfw.glfwSetWindowMonitor.call_args[0]
        self.assertIsNone(args[1], "leaving fullscreen must pass a NULL monitor")


class TestTheVulkanWindowKeepsItsOwnChoice(unittest.TestCase):
    """A Z-shaped window must survive the per-frame size lock and the bond."""

    def test_poll_locks_the_half_size_after_the_cycle(self):
        renderer = object.__new__(vulkan.LiquidVisualizerVulkanRenderer)
        glfw = MagicMock()
        glfw.glfwWindowShouldClose.return_value = 0
        glfw.glfwGetWindowSize.return_value = None
        renderer._glfw = glfw
        renderer._window = MagicMock()
        renderer.should_close = False
        renderer.is_fullscreen = False
        renderer.window_mode = zcycle.WINDOW_MODE_HALF
        renderer._default_windowed_size = (1574, 1258)
        renderer._half_windowed_size = (787, 629)
        renderer._user_selected_window_mode = True
        renderer._user_toggled_windowed = True
        renderer._is_4k_cached = False
        renderer._windowed_size = (787, 629)
        renderer._player_bonded = False
        renderer._span_role = ""
        renderer._saver_bounds = None
        renderer._sync_player_bond = MagicMock()
        renderer._match_player_window = MagicMock()

        renderer.poll()
        glfw.glfwSetWindowSize.assert_called_with(renderer._window, 787, 629)
        self.assertEqual((renderer.width, renderer.height), (787, 629))


class ThePlayerSizeIsReadNotGuessed(unittest.TestCase):
    """``_z_player_size`` reads the live player, and tolerates its absence."""

    def test_reads_the_published_geometry_first(self):
        window = _Cycle()
        with patch("liquid_player_visualizer.read_published_player_window_geometry",
                   return_value=(100, 200, 1600, 1200)), \
             patch("liquid_player_visualizer.read_published_player_window_size",
                   return_value=(1000, 800)):
            self.assertEqual(window._z_player_size(), (1600, 1200))

    def test_falls_back_to_the_published_size(self):
        window = _Cycle()
        with patch("liquid_player_visualizer.read_published_player_window_geometry",
                   return_value=None), \
             patch("liquid_player_visualizer.read_published_player_window_size",
                   return_value=(1000, 800)):
            self.assertEqual(window._z_player_size(), (1000, 800))

    def test_no_player_means_no_size(self):
        window = _Cycle()
        with patch("liquid_player_visualizer.read_published_player_window_geometry",
                   return_value=None), \
             patch("liquid_player_visualizer.read_published_player_window_size",
                   return_value=None):
            self.assertIsNone(window._z_player_size())


if __name__ == "__main__":
    unittest.main()
