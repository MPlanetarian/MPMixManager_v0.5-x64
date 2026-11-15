#!/usr/bin/env python3
"""Left/Right arrow keys step MP Visualizer presets inside the visualizer only.

The MP Liquid Player already owns ``+``/``-`` for the playing track's pitch, so
the visualizer presets are bound to the arrow keys *in the visualizer window*:

* Inside the MP Visualizer window, Left/Right step through the whole preset
  library. Right on the last preset returns to the first, and Left on the first
  goes to the last.
* In the MP Liquid Player, Left/Right always keep their original ±10 s seek,
  whether the MP Visualizer is on behind it or not. Pressing an arrow while
  watching a visualizer video must never change the theme; the player still
  switches themes with 0-9 and Shift+V.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = BASE / "scripts"
for _path in (BASE, SCRIPTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import MP_Audio_Player as player  # noqa: E402
import liquid_player_visualizer as vis  # noqa: E402
import mp_liquid_visualizer_vulkan as vulkan_vis  # noqa: E402


def _opengl_renderer():
    renderer = vis.LiquidVisualizerOpenGLRenderer()
    renderer.preset = vulkan_vis.VISUALIZER_PRESETS[0]
    renderer.switch_preset = MagicMock()
    return renderer


def _make_player_ui(on: bool):
    """A TerminalPlayerUI with no daemon, holding the given visualizer state."""
    engine = MagicMock()
    engine.get_status_dict.return_value = {
        "title": "Mix.flac", "path": "/mixes/a.flac", "state": "playing",
        "volume": 80, "pitch_percent": 0, "shuffle": False, "repeat": "all",
    }
    scanner = MagicMock()
    scanner.get_mixes.return_value = []
    scanner.mixes = []
    with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}):
        ui = player.TerminalPlayerUI(engine, scanner, is_remote=False)
    ui.mp_visualizer_on = on
    ui.mp_visualizer_preset_id = 0
    ui.cached_status = dict(engine.get_status_dict.return_value)
    return ui


class TestOpenGLVisualizerArrowKeys(unittest.TestCase):
    def test_right_steps_forward_through_the_library(self):
        renderer = _opengl_renderer()
        expected = vulkan_vis.cycle_preset(renderer.preset, 1)
        renderer._on_key(None, vulkan_vis.GLFW_KEY_RIGHT, 0, 1, 0)
        renderer.switch_preset.assert_called_once_with(expected)

    def test_right_on_the_last_preset_wraps_to_the_first(self):
        renderer = _opengl_renderer()
        renderer.preset = vulkan_vis.VISUALIZER_PRESETS[-1]
        renderer._on_key(None, vulkan_vis.GLFW_KEY_RIGHT, 0, 1, 0)
        renderer.switch_preset.assert_called_once_with(vulkan_vis.VISUALIZER_PRESETS[0])

    def test_left_on_the_first_preset_wraps_to_the_last(self):
        renderer = _opengl_renderer()
        renderer._on_key(None, vulkan_vis.GLFW_KEY_LEFT, 0, 1, 0)
        renderer.switch_preset.assert_called_once_with(vulkan_vis.VISUALIZER_PRESETS[-1])

    def test_left_steps_backward(self):
        renderer = _opengl_renderer()
        renderer.preset = vulkan_vis.VISUALIZER_PRESETS[3]
        renderer._on_key(None, vulkan_vis.GLFW_KEY_LEFT, 0, 1, 0)
        renderer.switch_preset.assert_called_once_with(vulkan_vis.VISUALIZER_PRESETS[2])

    def test_key_release_does_not_switch(self):
        renderer = _opengl_renderer()
        renderer._on_key(None, vulkan_vis.GLFW_KEY_RIGHT, 0, 0, 0)  # GLFW_RELEASE
        renderer.switch_preset.assert_not_called()


class TestVulkanVisualizerArrowKeys(unittest.TestCase):
    def _renderer(self):
        renderer = object.__new__(vulkan_vis.LiquidVisualizerVulkanRenderer)
        renderer.screensaver = False
        renderer._span_role = ""
        renderer.preset = vulkan_vis.VISUALIZER_PRESETS[0]
        renderer.switch_preset = MagicMock()
        return renderer

    def test_arrow_codes_point_the_right_way(self):
        self.assertEqual((vulkan_vis.GLFW_KEY_LEFT, vulkan_vis.GLFW_KEY_RIGHT), (263, 262))

    def test_right_and_left_step_through_every_preset(self):
        renderer = self._renderer()
        renderer._on_key(None, vulkan_vis.GLFW_KEY_RIGHT, 0, 1, 0)
        renderer.switch_preset.assert_called_once_with(vulkan_vis.VISUALIZER_PRESETS[1])
        renderer.switch_preset.reset_mock()
        renderer.preset = vulkan_vis.VISUALIZER_PRESETS[-1]
        renderer._on_key(None, vulkan_vis.GLFW_KEY_RIGHT, 0, 1, 0)
        renderer.switch_preset.assert_called_once_with(vulkan_vis.VISUALIZER_PRESETS[0])
        renderer.switch_preset.reset_mock()
        renderer.preset = vulkan_vis.VISUALIZER_PRESETS[0]
        renderer._on_key(None, vulkan_vis.GLFW_KEY_LEFT, 0, 1, 0)
        renderer.switch_preset.assert_called_once_with(vulkan_vis.VISUALIZER_PRESETS[-1])


class TestBondedVisualizerPublishesItsPreset(unittest.TestCase):
    """A preset picked in the bonded window must not be reverted by the player."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "liquid_player_visualizer.json"
        self._env = patch.dict(os.environ, {"MP_LIQUID_VISUALIZER_STATE": str(self.state)})
        self._env.start()
        vis._CACHE.update(path="", mtime=None, state=None)

    def tearDown(self):
        self._env.stop()
        self.tmp.cleanup()
        vis._CACHE.update(path="", mtime=None, state=None)

    def _renderer(self, bonded=True):
        renderer = object.__new__(vulkan_vis.LiquidVisualizerVulkanRenderer)
        renderer.preset = vulkan_vis.VISUALIZER_PRESETS[0]
        renderer._span_role = ""
        renderer.screensaver = False
        renderer._player_bonded = bonded
        renderer._bond_locked = False
        renderer._bond_detached = False
        renderer._device = 0
        renderer._pipeline = 0
        return renderer

    def test_bonded_window_writes_the_new_preset_to_the_shared_state(self):
        renderer = self._renderer(bonded=True)
        with patch.object(vulkan_vis, "_IS_LINUX", True), \
             patch.object(vulkan_vis, "save_active_preset"):
            renderer.switch_preset(vulkan_vis.VISUALIZER_PRESETS[3])
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertTrue(saved["active"])
        self.assertEqual(saved["preset_id"], int(vulkan_vis.VISUALIZER_PRESETS[3]["id"]))

    def test_free_window_does_not_claim_the_bond(self):
        renderer = self._renderer(bonded=False)
        with patch.object(vulkan_vis, "_IS_LINUX", True), \
             patch.object(vulkan_vis, "save_active_preset"):
            renderer.switch_preset(vulkan_vis.VISUALIZER_PRESETS[3])
        self.assertFalse(self.state.exists())

    def test_spawned_span_window_writes_nothing(self):
        renderer = self._renderer(bonded=True)
        renderer._span_role = "spawned"
        with patch.object(vulkan_vis, "_IS_LINUX", True), \
             patch.object(vulkan_vis, "save_active_preset"):
            renderer.switch_preset(vulkan_vis.VISUALIZER_PRESETS[3])
        self.assertFalse(self.state.exists())

    def test_opengl_companion_publishes_through_its_switch(self):
        renderer = vis.LiquidVisualizerOpenGLRenderer()
        renderer.preset = vulkan_vis.VISUALIZER_PRESETS[0]
        renderer._span_role = ""
        renderer.screensaver = False
        renderer._player_bonded = True
        renderer._bond_locked = False
        renderer._bond_detached = False
        with patch.object(vulkan_vis, "_IS_LINUX", True):
            renderer.switch_preset(vulkan_vis.VISUALIZER_PRESETS[2])
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(saved["preset_id"], int(vulkan_vis.VISUALIZER_PRESETS[2]["id"]))

    def test_player_header_adopts_a_preset_chosen_in_the_visualizer(self):
        """A key pressed in the window with focus must update the player header."""
        ui = _make_player_ui(on=True)
        ui.mp_visualizer_preset_id = 0
        vis.write_player_visualizer_state(True, vulkan_vis.VISUALIZER_PRESETS[5])
        ui._visualizer_preset_poll_at = 0.0
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}):
            ui._follow_visualizer_preset()
        self.assertEqual(ui.mp_visualizer_preset_id, int(vulkan_vis.VISUALIZER_PRESETS[5]["id"]))

    def test_preset_poll_is_off_while_the_visualizer_is_off(self):
        ui = _make_player_ui(on=False)
        ui.mp_visualizer_preset_id = 0
        vis.write_player_visualizer_state(True, vulkan_vis.VISUALIZER_PRESETS[5])
        ui._visualizer_preset_poll_at = 0.0
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}):
            ui._follow_visualizer_preset()
        self.assertEqual(ui.mp_visualizer_preset_id, 0)


class TestPlayerArrowKeysAlwaysSeek(unittest.TestCase):
    """Left/Right seek in the MP Liquid Player, even with the visualizer on.

    Preset stepping belongs to the MP Visualizer window (Left/Right, +/- and
    1-9 there). The player only ever *shows* a preset; its own Left/Right stay
    the ±10 s seek they have always been, so an arrow can no longer change the
    theme while the user is watching a visualizer video.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "liquid_player_visualizer.json"
        self._env = patch.dict(os.environ, {
            "MP_LIQUID_VISUALIZER_STATE": str(self.state),
            "MP_LIQUID_GLASS": "1",
        })
        self._env.start()
        vis._CACHE.update(path="", mtime=None, state=None)

    def tearDown(self):
        self._env.stop()
        self.tmp.cleanup()
        vis._CACHE.update(path="", mtime=None, state=None)

    def test_arrows_seek_while_the_visualizer_is_off(self):
        ui = _make_player_ui(on=False)
        with patch.object(ui, "_send_cmd") as send:
            ui._process_key(b"\x1b[C")
            ui._process_key(b"\x1b[D")
        send.assert_any_call("seek", seconds=10.0)
        send.assert_any_call("seek", seconds=-10.0)
        self.assertEqual(send.call_count, 2)

    def test_arrows_seek_while_the_visualizer_is_on(self):
        ui = _make_player_ui(on=True)
        with patch.object(ui, "_send_cmd") as send:
            ui._process_key(b"\x1b[C")
            ui._process_key(b"\x1b[D")
        send.assert_any_call("seek", seconds=10.0)
        send.assert_any_call("seek", seconds=-10.0)
        self.assertEqual(send.call_count, 2)

    def test_arrows_never_change_the_preset_from_the_player(self):
        ui = _make_player_ui(on=True)
        ui.mp_visualizer_preset_id = 3
        with patch.object(ui, "_send_cmd"):
            ui._process_key(b"\x1b[C")
            ui._process_key(b"\x1b[D")
            ui._process_key(b"\x1bOC")
            ui._process_key(b"\x1bOD")
        self.assertEqual(ui.mp_visualizer_preset_id, 3)
        self.assertFalse(self.state.exists())

    def test_application_cursor_mode_codes_seek_too(self):
        """Some terminals send \x1bOC / \x1bOD for the arrows."""
        ui = _make_player_ui(on=True)
        with patch.object(ui, "_send_cmd") as send:
            ui._process_key(b"\x1bOC")
            ui._process_key(b"\x1bOD")
        send.assert_any_call("seek", seconds=10.0)
        send.assert_any_call("seek", seconds=-10.0)

    def test_number_keys_still_select_a_theme_in_the_player(self):
        ui = _make_player_ui(on=True)
        ui._process_key(b"5")
        self.assertEqual(ui.mp_visualizer_preset_id, 5)


if __name__ == "__main__":
    unittest.main()
