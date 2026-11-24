#!/usr/bin/env python3
"""The Visualization button hides and shows the one companion it owns.

Reported from the desktop: pressing **VISUALIZATION** in the MP Liquid Player
left the MP Visualizer window behind it on screen, and pressing the button
again did not bring anything back. The button only wrote ``active: false``,
which releases the bond — so the window either stayed up as a detached
free-floating square or was retired entirely, and a second press had to spawn
a new process.

The button now writes two independent flags:

* ``active`` — this window belongs behind the player. It stays true while the
  button is off, so the window is never un-bonded or closed.
* ``visible`` — the button's on/off. The owned companion unmaps itself while
  it is false and maps itself again in place while it is true.

Only the companion the player launched (``MP_VISUALIZER_COMPANION=1``) follows
``visible``. A window the user opened with **Z**, from the desktop shortcut, or
a spawned F10/F11 window is never touched, and neither is MP Gui's companion.
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

import liquid_player_visualizer as vis  # noqa: E402
import mp_liquid_visualizer_vulkan as vulkan_vis  # noqa: E402
import visualizer_player_bond as bond  # noqa: E402

_COMPANION_ENV = {"MP_VISUALIZER_COMPANION": "1", "MP_VISUALIZER_DETACHED": ""}
_FREE_ENV = {"MP_VISUALIZER_DETACHED": "1", "MP_VISUALIZER_COMPANION": ""}


def _renderer(module=vulkan_vis, name="LiquidVisualizerVulkanRenderer", env=None):
    """A renderer with a real bond mixin and a GLFW stand-in.

    The window itself is a MagicMock so ``glfwHideWindow``/``glfwShowWindow``
    calls can be observed without opening a GL context.
    """
    with patch.dict(os.environ, env or _COMPANION_ENV, clear=False):
        renderer = object.__new__(getattr(module, name))
        renderer._init_player_bond()
    renderer.is_fullscreen = False
    renderer._span_role = ""
    renderer.preset = {"id": 0, "key": "first"}
    renderer._windowed_size = (1094, 778)
    renderer.window_pos = MagicMock(return_value=(510, 385))
    renderer.set_window_pos = MagicMock()
    renderer.switch_preset = MagicMock()
    renderer._set_bonded_stack = MagicMock()
    renderer._set_windowed_size = MagicMock()
    renderer._glfw = MagicMock()
    renderer._glfw.glfwGetWindowAttrib.return_value = 0
    renderer._window = MagicMock()
    # The bond really talks to the X server to stack itself below the player.
    # These tests are about the map state, so the stacking call is stubbed.
    renderer._lower_below_player = MagicMock()
    renderer._clear_below_hint = MagicMock()
    return renderer


class _StateFile(unittest.TestCase):
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


class TestTheStateFileCarriesABooleanVisible(_StateFile):
    def test_write_records_both_flags(self):
        vis.write_player_visualizer_state(True, {"id": 4, "key": "four", "name": "Four"},
                                          visible=False)
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertTrue(saved["active"])
        self.assertFalse(saved["visible"])
        self.assertEqual(saved["preset_id"], 4)

    def test_a_reader_sees_the_same_pair(self):
        vis.write_player_visualizer_state(True, {"id": 2, "key": "two", "name": "Two"},
                                          visible=False)
        state = vis.read_player_visualizer_state()
        self.assertTrue(state["active"])
        self.assertFalse(state["visible"])

    def test_an_old_file_without_the_key_stays_visible(self):
        """An upgrade must not hide a window that was on screen before it."""
        self.state.write_text(json.dumps({"active": True, "preset_id": 1}) + "\n",
                              encoding="utf-8")
        state = vis.read_player_visualizer_state()
        self.assertTrue(state["active"])
        self.assertTrue(state["visible"])

    def test_a_missing_file_is_active_false_and_visible(self):
        state = vis.read_player_visualizer_state()
        self.assertFalse(state["active"])
        self.assertTrue(state["visible"])

    def test_a_preset_only_write_keeps_the_hidden_flag(self):
        """A theme change from another window must not un-hide the companion."""
        vis.write_player_visualizer_state(True, {"id": 0, "key": "zero", "name": "Zero"},
                                          visible=False)
        vis.write_player_visualizer_state(True, {"id": 7, "key": "seven", "name": "Seven"})
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertFalse(saved["visible"])
        self.assertEqual(saved["preset_id"], 7)

    def test_mark_attached_can_reset_the_flag_for_a_fresh_launch(self):
        vis.write_player_visualizer_state(True, {"id": 0, "key": "zero", "name": "Zero"},
                                          visible=False)
        vis.mark_player_visualizer_attached({"id": 0, "key": "zero", "name": "Zero"},
                                           visible=True)
        self.assertTrue(vis.read_player_visualizer_state()["visible"])

    def test_mark_attached_leaves_the_flag_alone_by_default(self):
        """MP Gui and the launch scripts must not un-hide the player's choice."""
        vis.write_player_visualizer_state(True, {"id": 0, "key": "zero", "name": "Zero"},
                                          visible=False)
        vis.mark_player_visualizer_attached({"id": 3, "key": "three", "name": "Three"})
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertFalse(saved["visible"])
        self.assertEqual(saved["preset_id"], 3)


class TestOnlyTheOwnedCompanionFollowsTheFlag(_StateFile):
    def test_the_player_companion_is_eligible(self):
        self.assertTrue(_renderer()._companion_follows_visibility())

    def test_a_z_window_is_not_eligible(self):
        renderer = _renderer(env=_FREE_ENV)
        self.assertFalse(renderer._companion_follows_visibility())

    def test_a_gui_companion_is_eligible_on_its_own_key(self):
        """MP Gui has its own Visualization button, so its companion follows too.

        The two hosts share one state file but own separate windows, so a
        single key would let one host's button blank the other's background.
        MP Gui therefore writes ``gui_visible`` and the player keeps
        ``visible``; ``tests/test_gui_visualizer_button.py`` covers the split.
        """
        with patch.dict(os.environ, {"MP_VISUALIZER_MATCH_GUI": "1"}, clear=False):
            renderer = _renderer()
            self.assertTrue(renderer._companion_follows_visibility())
            self.assertEqual(renderer._companion_visibility_key(), "gui_visible")

    def test_the_flag_is_honoured_for_the_owned_window(self):
        renderer = _renderer()
        vis.write_player_visualizer_state(True, {"id": 0, "key": "zero", "name": "Zero"},
                                          visible=False)
        renderer._sync_companion_visibility()
        renderer._glfw.glfwHideWindow.assert_called_once_with(renderer._window)
        renderer._glfw.glfwShowWindow.assert_not_called()
        self.assertFalse(renderer._companion_visible)

    def test_pressing_the_button_again_maps_the_same_window(self):
        renderer = _renderer()
        vis.write_player_visualizer_state(True, {"id": 0, "key": "zero", "name": "Zero"},
                                          visible=False)
        renderer._sync_companion_visibility()
        vis.write_player_visualizer_state(True, {"id": 0, "key": "zero", "name": "Zero"},
                                          visible=True)
        renderer._visibility_check_at = 0.0
        renderer._sync_companion_visibility()
        renderer._glfw.glfwShowWindow.assert_called_once_with(renderer._window)
        self.assertTrue(renderer._companion_visible)

    def test_a_free_window_ignores_the_flag_entirely(self):
        renderer = _renderer(env=_FREE_ENV)
        vis.write_player_visualizer_state(True, {"id": 0, "key": "zero", "name": "Zero"},
                                          visible=False)
        renderer._sync_companion_visibility()
        renderer._glfw.glfwHideWindow.assert_not_called()
        self.assertTrue(renderer._companion_visible)

    def test_the_file_is_not_read_every_frame(self):
        """The check is throttled; only a real press should reach the mapper."""
        renderer = _renderer()
        vis.write_player_visualizer_state(True, {"id": 0, "key": "zero", "name": "Zero"},
                                          visible=False)
        with patch.object(vis, "read_player_visualizer_state",
                          return_value={"active": True, "visible": False}) as reader:
            for _ in range(50):
                renderer._sync_companion_visibility()
        self.assertEqual(reader.call_count, 1)


class TestHidingNeverUnbondsTheWindow(_StateFile):
    """Hidden is a map state, not a release: the bond must survive the press."""

    def _sync(self, renderer, visible):
        geometry = (510, 385, 1094, 778)
        with patch.object(vulkan_vis, "_IS_LINUX", True), \
             patch("liquid_player_visualizer.read_player_visualizer_state",
                   return_value={"active": True, "visible": visible,
                                 "preset_id": 0}), \
             patch("liquid_player_visualizer.read_published_player_window_geometry",
                   return_value=geometry), \
             patch("liquid_player_visualizer.visualizer_presets", return_value=[]):
            renderer._player_bonded_at = 0.0
            renderer._visibility_check_at = 0.0
            renderer._sync_player_bond()
        return geometry

    def test_hidden_window_stays_bonded_and_keeps_its_geometry(self):
        renderer = _renderer()
        geometry = self._sync(renderer, visible=False)
        self.assertTrue(renderer._player_bonded)
        renderer._set_bonded_stack.assert_called_once_with(True)
        renderer._set_windowed_size.assert_called_once_with(geometry[2], geometry[3], apply=True)
        renderer.set_window_pos.assert_called_once_with(geometry[0], geometry[1])

    def test_hidden_window_is_not_restored_to_its_free_size(self):
        renderer = _renderer()
        renderer._player_bonded = True
        renderer._unbonded_window_pos = (72, 64)
        renderer._restore_size = (1094, 778)
        self._sync(renderer, visible=False)
        renderer._set_bonded_stack.assert_not_called()
        self.assertTrue(renderer._player_bonded)


class TestTheSceneIsSkippedWhereThePlayerDrawsIt(_StateFile):
    """macOS and Windows draw in-window, so the flag has to gate the shader."""

    def test_the_overlay_does_not_draw_while_hidden(self):
        layer = vis.PlayerVisualizerLayer()
        state = {"active": True, "visible": False, "preset_id": 0}
        with patch.object(layer, "_draw") as draw:
            self.assertFalse(layer.draw_with_state(object(), 64, 64, state))
        draw.assert_not_called()

    def test_the_overlay_draws_when_visible(self):
        layer = vis.PlayerVisualizerLayer()
        state = {"active": True, "visible": True, "preset_id": 0}
        with patch.object(layer, "_draw", return_value=True) as draw:
            self.assertTrue(layer.draw_with_state(object(), 64, 64, state))
        draw.assert_called_once()

    def test_the_opengl_host_reads_the_flag_from_the_file(self):
        layer = vis.PlayerVisualizerLayer()
        vis.write_player_visualizer_state(True, {"id": 0, "key": "zero", "name": "Zero"},
                                          visible=False)
        with patch.object(layer, "draw_with_state") as draw:
            self.assertFalse(layer.draw(object(), 64, 64))
        draw.assert_not_called()


class TestTheVulkanHostSkipsTheSceneWhileHidden(_StateFile):
    def test_visualizer_state_is_none_while_hidden(self):
        import liquid_glass_vulkan as glass

        renderer = object.__new__(glass.LiquidGlassRenderer)
        renderer.allow_player_visualizer = True
        vis.write_player_visualizer_state(True, {"id": 0, "key": "zero", "name": "Zero"},
                                          visible=False)
        self.assertIsNone(renderer._visualizer_state())
        vis.write_player_visualizer_state(True, {"id": 0, "key": "zero", "name": "Zero"},
                                          visible=True)
        self.assertIsNotNone(renderer._visualizer_state())


class TestEveryBackendBindsShowAndHide(unittest.TestCase):
    """All three renderers must be able to map and unmap their own window."""

    def test_the_mixin_owns_the_mapping(self):
        self.assertTrue(callable(bond.PlayerBondMixin._set_companion_visible))
        self.assertTrue(callable(bond.PlayerBondMixin._sync_companion_visibility))

    def test_each_backend_binds_the_glfw_symbols(self):
        for module in (vis, vulkan_vis, __import__("mp_liquid_visualizer_metal")):
            source = Path(module.__file__).read_text(encoding="utf-8")
            with self.subTest(module=module.__name__):
                self.assertIn("glfwHideWindow", source)
                self.assertIn("glfwShowWindow", source)

    def test_the_poll_path_runs_the_check(self):
        renderer = _renderer()
        with patch.object(renderer, "_sync_companion_visibility") as sync, \
             patch.object(type(renderer), "_bonding_applies", return_value=False):
            renderer._sync_player_bond()
        sync.assert_called_once()


@unittest.skipUnless(sys.platform.startswith("linux"),
                     "the standalone visualizer companion is Linux-only")
class TestTheSpawnerOwnsOnlyItsCompanion(_StateFile):
    """A window the user opened with Z must not stop the button's companion."""

    def _ui(self):
        import MP_Audio_Player as player
        engine = MagicMock()
        engine.current_track = None
        engine.playlist = []
        engine.mode = "MIX_ARCHIVE"
        engine.muted = False
        engine.volume = 0.8
        engine.get_status_dict.return_value = {"title": "t", "path": "/a.flac",
                                               "state": "playing", "levels": []}
        scanner = MagicMock()
        scanner.get_mixes.return_value = []
        scanner.mixes = []
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}):
            ui = player.TerminalPlayerUI(engine, scanner, is_remote=False)
        return ui

    def test_a_z_window_does_not_suppress_the_companion(self):
        ui = self._ui()
        env = {k: v for k, v in os.environ.items()
               if k != "MP_LIQUID_VISUALIZER_STATE"}
        with patch.dict(os.environ, env, clear=True), \
             patch("liquid_visualizer_session.companion_visualizer_pids",
                   return_value=[]), \
             patch("MP_Audio_Player.subprocess.Popen") as popen:
            ui._ensure_standalone_mp_visualizer({"id": 0, "key": "zero"})
        popen.assert_called_once()
        child_env = popen.call_args[1]["env"]
        self.assertEqual(child_env.get("MP_VISUALIZER_COMPANION"), "1")
        self.assertNotIn("MP_VISUALIZER_DETACHED", child_env)

    def test_an_existing_companion_is_not_spawned_twice(self):
        ui = self._ui()
        with patch("liquid_visualizer_session.companion_visualizer_pids",
                   return_value=[4242]), \
             patch("MP_Audio_Player.subprocess.Popen") as popen:
            ui._ensure_standalone_mp_visualizer({"id": 0, "key": "zero"})
        popen.assert_not_called()


class TestTheCompanionCanOpenHidden(_StateFile):
    """A companion that starts while the button is off must not flash."""

    def test_start_hidden_only_for_the_owned_companion(self):
        vis.write_player_visualizer_state(True, {"id": 0, "key": "zero", "name": "Zero"},
                                          visible=False)
        self.assertTrue(_renderer()._bond_should_start_hidden())

    def test_a_visible_flag_opens_normally(self):
        vis.write_player_visualizer_state(True, {"id": 0, "key": "zero", "name": "Zero"},
                                          visible=True)
        self.assertFalse(_renderer()._bond_should_start_hidden())

    def test_a_free_window_never_opens_hidden(self):
        renderer = _renderer(env=_FREE_ENV)
        vis.write_player_visualizer_state(True, {"id": 0, "key": "zero", "name": "Zero"},
                                          visible=False)
        self.assertFalse(renderer._bond_should_start_hidden())

    def test_every_backend_consults_the_hint(self):
        for module, name in ((vis, "LiquidVisualizerOpenGLRenderer"),
                             (vulkan_vis, "LiquidVisualizerVulkanRenderer"),
                             (__import__("mp_liquid_visualizer_metal"),
                              "LiquidVisualizerMetalRenderer")):
            source = Path(module.__file__).read_text(encoding="utf-8")
            with self.subTest(module=module.__name__):
                self.assertIn("_bond_should_start_hidden", source)


if __name__ == "__main__":
    unittest.main()
