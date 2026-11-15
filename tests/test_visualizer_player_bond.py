"""The standalone MP Visualizer always attaches behind the MP Liquid Player.

Two regressions are locked in here:

1. Both standalone backends must bond. The Linux default is OpenGL, and the
   OpenGL twin used to have no bond code at all, so the window opened as a
   free-floating square away from the player.
2. A companion window started by the player or a launcher must *start* active.
   The state file has to be written before the child starts, or the
   visualizer's first poll sees ``active: false`` and never bonds.
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
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import liquid_player_visualizer as vis  # noqa: E402
import mp_liquid_visualizer_vulkan as vulkan_vis  # noqa: E402


def _renderer(mod, cls_name, size_attr="_size"):
    """A renderer instance built without opening a GLFW/Vulkan context."""
    renderer = object.__new__(getattr(mod, cls_name))
    renderer.is_fullscreen = False
    renderer._init_player_bond()
    renderer._span_role = ""
    renderer.preset = {"id": 0, "key": "first"}
    renderer.window_pos = MagicMock(return_value=(72, 64))
    renderer.set_window_pos = MagicMock()
    renderer.switch_preset = MagicMock()
    renderer._set_bonded_stack = MagicMock()
    renderer._set_windowed_size = MagicMock()
    if cls_name == "LiquidVisualizerOpenGLRenderer":
        renderer._size = (700, 700)
    else:
        renderer._windowed_size = (1574, 1258)
    return renderer


class TestBothBackendsShareTheBond(unittest.TestCase):
    def test_opengl_twin_mixes_in_the_player_bond(self):
        from visualizer_player_bond import PlayerBondMixin

        self.assertTrue(issubclass(vis.LiquidVisualizerOpenGLRenderer, PlayerBondMixin))
        self.assertIn("PlayerBondMixin", [c.__name__ for c in vis.LiquidVisualizerOpenGLRenderer.__mro__])

    def test_vulkan_renderer_mixes_in_the_player_bond(self):
        from visualizer_player_bond import PlayerBondMixin

        self.assertTrue(issubclass(vulkan_vis.LiquidVisualizerVulkanRenderer, PlayerBondMixin))

    def test_bonded_stack_uses_sibling_stacking_not_below_hint(self):
        """The visualizer must sit against the player, not below every window.

        ``_NET_WM_STATE_BELOW`` (``wmctrl -b add,below``) means "below every
        normal window" to KWin, so the visualizer sank under whatever else was
        on the desktop: it was visible over an empty area but hidden behind a
        maximised console, and the player then blended with the console.
        Sibling stacking keeps it adjacent to the player instead.
        """
        src = (SCRIPTS / "visualizer_player_bond.py").read_text(encoding="utf-8")
        self.assertIn("XConfigureWindow", src)
        self.assertIn("CW_SIBLING", src)
        self.assertIn("STACK_MODE_BELOW", src)
        self.assertIn("_find_player_window", src)
        # The re-assert must run from the periodic sync, otherwise the player
        # being raised above it pushes the visualizer back on top.
        self.assertIn("_next_relower_at", src)
        # The below hint must never be *added* by executable code. It may only
        # be removed, and the prohibition is documented in prose.
        self.assertNotIn('["wmctrl", "-r", self.title, "-b", "add,below"]', src)
        self.assertIn('"remove,below"', src)

    def test_opengl_poll_syncs_the_bond(self):
        """The OpenGL twin's poll must call the shared bond sync every frame."""
        renderer = object.__new__(vis.LiquidVisualizerOpenGLRenderer)
        glfw = MagicMock()
        glfw.glfwWindowShouldClose.return_value = 0
        renderer._glfw = glfw
        renderer._window = MagicMock()
        renderer.should_close = False
        renderer.is_fullscreen = False
        renderer._player_bonded = False
        renderer._span_role = ""
        renderer._saver_bounds = None
        renderer._size = (700, 700)
        renderer._user_selected_window_mode = False
        renderer._sync_player_bond = MagicMock()
        renderer._match_player_window = MagicMock()
        renderer.poll_monitor_span = MagicMock()

        renderer.poll()
        renderer._sync_player_bond.assert_called_once()


class TestOpenGLTwinBondsToThePlayer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "liquid_player_visualizer.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _sync(self, renderer, state, geometry):
        with patch.object(vulkan_vis, "_IS_LINUX", True), \
             patch("liquid_player_visualizer.read_player_visualizer_state", return_value=state), \
             patch("liquid_player_visualizer.read_published_player_window_geometry", return_value=geometry), \
             patch("liquid_player_visualizer.visualizer_presets", return_value=[]):
            renderer._sync_player_bond()

    def test_opengl_twin_takes_the_player_geometry(self):
        renderer = _renderer(vis, "LiquidVisualizerOpenGLRenderer")
        self._sync(renderer, {"active": True}, (510, 385, 1574, 1258))
        self.assertTrue(renderer._player_bonded)
        renderer._set_bonded_stack.assert_called_once_with(True)
        renderer._set_windowed_size.assert_called_once_with(1574, 1258, apply=True)
        renderer.set_window_pos.assert_called_once_with(510, 385)

    def test_opengl_twin_releases_and_restores_when_switched_off(self):
        renderer = _renderer(vis, "LiquidVisualizerOpenGLRenderer")
        renderer._player_bonded = True
        renderer._unbonded_window_pos = (72, 64)
        renderer._restore_size = (1094, 778)
        renderer._last_player_geometry = (510, 385, 1574, 1258)
        self._sync(renderer, {"active": False}, None)
        self.assertFalse(renderer._player_bonded)
        renderer._set_bonded_stack.assert_called_once_with(False)
        renderer._set_windowed_size.assert_called_once_with(1094, 778, apply=True)
        renderer.set_window_pos.assert_called_once_with(72, 64)

    def test_opengl_twin_does_not_bond_for_a_spawned_span_window(self):
        renderer = _renderer(vis, "LiquidVisualizerOpenGLRenderer")
        renderer._span_role = "spawned"
        self._sync(renderer, {"active": True}, (0, 0, 1920, 1080))
        self.assertFalse(renderer._player_bonded)
        renderer.set_window_pos.assert_not_called()


class TestCompanionStartsAttached(unittest.TestCase):
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

    def test_mark_attached_writes_active_before_the_child_starts(self):
        preset = {"id": 3, "key": "infinite_complexity", "name": "Infinite Complexity"}
        vis.mark_player_visualizer_attached(preset)
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertTrue(saved["active"])
        self.assertEqual(saved["preset_id"], 3)
        self.assertEqual(saved["preset_key"], "infinite_complexity")
        self.assertTrue(vis.read_player_visualizer_state()["active"])

    def test_mark_attached_picks_a_preset_when_none_is_given(self):
        vis.mark_player_visualizer_attached()
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertTrue(saved["active"])
        self.assertIn("preset_key", saved)

    def test_visualizer_state_is_active_for_the_first_frame(self):
        """The very first poll after open() must already see the bond."""
        vis.mark_player_visualizer_attached({"id": 1, "key": "orbital_void_reflux", "name": "Orbital Void Reflux"})
        self.assertTrue(vis.read_player_visualizer_state().get("active"))


class TestHostsMarkTheCompanionActive(unittest.TestCase):
    """Both Linux hosts write the active state before spawning the child."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "liquid_player_visualizer.json"
        self._env = patch.dict(os.environ, {
            "MP_LIQUID_VISUALIZER_STATE": str(self.state),
            "MP_LIQUID_GLASS_BACKEND": "opengl",
        })
        self._env.start()
        vis._CACHE.update(path="", mtime=None, state=None)

    def tearDown(self):
        self._env.stop()
        self.tmp.cleanup()
        vis._CACHE.update(path="", mtime=None, state=None)

    def _spawn(self, module, companion=False):
        """Spawn through the host, choosing the companion or a detached window.

        The running-companion probe is neutralised because the test runner's
        own command line can contain the visualizer marker text, which would
        look like a running companion and short-circuit the spawn.
        """
        target = module.ensure_random_visualizer_started if companion else module.spawn_new_visualizer
        with patch.object(module.subprocess, "run", return_value=MagicMock(returncode=1)), \
             patch.object(module, "_companion_already_running", return_value=False), \
             patch("subprocess.Popen") as popen:
            popen.return_value = MagicMock(pid=4242)
            started = target()
        self.assertTrue(started)
        return popen

    def test_opengl_host_marks_the_companion_active(self):
        """The companion the player starts is marked active before it spawns.

        ``ensure_random_visualizer_started`` is that path. It writes the state
        file and flags the child as the owned companion, so its first poll
        already knows it belongs behind this player.
        """
        import MP_Audio_Player_Liquid_Glass_OpenGL as host

        popen = self._spawn(host, companion=True)
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertTrue(saved["active"], "the child would never bond")
        cmd = popen.call_args[0][0]
        self.assertIn("--backend", cmd)
        # The host is OpenGL, so the companion must be too (the bond is shared
        # now, but the GPU path should still match the window in front of it).
        self.assertEqual(cmd[cmd.index("--backend") + 1], "opengl")
        self.assertEqual(popen.call_args[1]["env"].get("MP_VISUALIZER_COMPANION"), "1")

    def test_z_spawned_window_is_detached_not_bonded(self):
        """Z opens an extra window. It must not latch onto the player.

        Only the companion the player launches is permanently attached; every
        window opened afterwards is explicitly detached. Both hosts are checked:
        the Vulkan host was the only one wired to the companion path, and the
        original test only covered the OpenGL host, so Z silently opened a
        second window hidden underneath the player.
        """
        for name in ("MP_Audio_Player_Liquid_Glass_OpenGL", "MP_Audio_Player_Liquid_Glass"):
            with self.subTest(host=name):
                import importlib

                host = importlib.import_module(name)
                popen = self._spawn(host)
                env = popen.call_args[1]["env"]
                self.assertNotIn("MP_VISUALIZER_COMPANION", env,
                                 f"{name} marked the Z window as the owned companion")
                self.assertEqual(env.get("MP_VISUALIZER_DETACHED"), "1")

    def test_vulkan_host_marks_the_companion_active(self):
        import MP_Audio_Player_Liquid_Glass as host

        with patch.dict(os.environ, {"MP_LIQUID_GLASS_BACKEND": "vulkan"}):
            popen = self._spawn(host, companion=True)
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertTrue(saved["active"])
        cmd = popen.call_args[0][0]
        self.assertEqual(cmd[cmd.index("--backend") + 1], "vulkan")
        self.assertEqual(popen.call_args[1]["env"].get("MP_VISUALIZER_COMPANION"), "1")

    def test_spawn_environment_is_not_marked_as_a_player_host(self):
        import MP_Audio_Player_Liquid_Glass_OpenGL as host

        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1",
                                     "MP_LIQUID_GLASS_BACKEND": "opengl",
                                     "GDK_BACKEND": "x11",
                                     "PYOPENGL_PLATFORM": "glx"}, clear=False):
            env = host._visualizer_env()
        self.assertNotIn("MP_LIQUID_GLASS", env)
        self.assertNotIn("MP_LIQUID_GLASS_BACKEND", env)
        self.assertNotIn("GDK_BACKEND", env)
        self.assertNotIn("PYOPENGL_PLATFORM", env)


class TestBondedWindowOpensOnThePlayer(unittest.TestCase):
    """A companion that will bond opens on the player, never in the corner."""

    def test_initial_geometry_returns_the_published_rectangle(self):
        renderer = _renderer(vis, "LiquidVisualizerOpenGLRenderer")
        with patch.object(vulkan_vis, "_IS_LINUX", True), \
             patch("liquid_player_visualizer.read_player_visualizer_state", return_value={"active": True}), \
             patch("liquid_player_visualizer.read_published_player_window_geometry",
                   return_value=(510, 385, 1574, 1258)):
            self.assertEqual(renderer._bond_initial_geometry(), (510, 385, 1574, 1258))

    def test_initial_geometry_is_none_while_inactive(self):
        renderer = _renderer(vis, "LiquidVisualizerOpenGLRenderer")
        with patch.object(vulkan_vis, "_IS_LINUX", True), \
             patch("liquid_player_visualizer.read_player_visualizer_state", return_value={"active": False}), \
             patch("liquid_player_visualizer.read_published_player_window_geometry",
                   return_value=(510, 385, 1574, 1258)):
            self.assertIsNone(renderer._bond_initial_geometry())

    def test_initial_geometry_is_none_without_a_published_player(self):
        renderer = _renderer(vis, "LiquidVisualizerOpenGLRenderer")
        with patch.object(vulkan_vis, "_IS_LINUX", True), \
             patch("liquid_player_visualizer.read_player_visualizer_state", return_value={"active": True}), \
             patch("liquid_player_visualizer.read_published_player_window_geometry", return_value=None):
            self.assertIsNone(renderer._bond_initial_geometry())


class TestAStandaloneWindowNeverBonds(unittest.TestCase):
    """A window the user opened itself must not chase a stale "active" flag.

    The desktop shortcut runs the same renderer as the player's companion.
    ``liquid_player_visualizer.json`` keeps ``"active": true`` after the player
    exits, so a standalone window used to adopt it, open unfocused, and then
    bond "behind" a player that was not there. Without a player window to
    stack against, the lowering fallback pushed it to the bottom of the window
    stack, under every other app: the shortcut appeared to do nothing.

    ``MP_VISUALIZER_DETACHED=1`` is that "this one is mine" signal.
    """

    def test_detached_window_ignores_the_active_state(self):
        with patch.dict(os.environ, {"MP_VISUALIZER_DETACHED": "1"}, clear=False):
            renderer = _renderer(vis, "LiquidVisualizerOpenGLRenderer")
        with patch.object(vulkan_vis, "_IS_LINUX", True), \
             patch("liquid_player_visualizer.read_player_visualizer_state",
                   return_value={"active": True}), \
             patch("liquid_player_visualizer.read_published_player_window_geometry",
                   return_value=(510, 385, 1574, 1258)):
            self.assertFalse(renderer._bonding_applies())
            self.assertIsNone(renderer._bond_initial_geometry())

    def test_detached_window_is_not_treated_as_the_owned_companion(self):
        with patch.dict(os.environ, {"MP_VISUALIZER_DETACHED": "1",
                                     "MP_VISUALIZER_COMPANION": ""}, clear=False):
            renderer = _renderer(vis, "LiquidVisualizerOpenGLRenderer")
        self.assertFalse(renderer._bond_is_locked())


class TestBondedWindowIgnoresQuit(unittest.TestCase):
    """Q closes a free visualizer. It must not close the player's companion.

    Vulkan and Metal already skipped Q while bonded. The OpenGL window, which
    is the usual one on Linux, closed anyway and took the player's background
    with it. Escape still closes every window.
    """

    def test_q_leaves_a_bonded_window_and_closes_a_free_one(self):
        targets = (
            (vis, "LiquidVisualizerOpenGLRenderer"),
            (vulkan_vis, "LiquidVisualizerVulkanRenderer"),
        )
        try:
            import mp_liquid_visualizer_metal as metal
            targets = targets + ((metal, "LiquidVisualizerMetalRenderer"),)
        except ImportError:
            pass
        for module, name in targets:
            renderer = object.__new__(getattr(module, name))
            renderer.screensaver = False
            renderer._player_bonded = True
            renderer.request_close = MagicMock()
            with self.subTest(backend=name, bonded=True):
                renderer._on_key(None, 81, 0, 1, 0)
                renderer.request_close.assert_not_called()
            renderer._player_bonded = False
            with self.subTest(backend=name, bonded=False):
                renderer._on_key(None, 81, 0, 1, 0)
                renderer.request_close.assert_called_once()


class TestTheBondServesMPGuiToo(unittest.TestCase):
    """MP Gui's companion sits behind MP Gui, not behind the Liquid Player.

    MP_Gui.sh launches its visualizer with ``MP_VISUALIZER_MATCH_GUI=1`` and
    publishes to ``liquid_gui_window.json``. The bond used to read only the
    player record, so a GUI companion bonded to whichever Liquid Player was
    open — and did not move at all when only the GUI was up.
    """

    def test_match_gui_selects_the_gui_record(self):
        renderer = _renderer(vis, "LiquidVisualizerOpenGLRenderer")
        with patch.dict(os.environ, {"MP_VISUALIZER_MATCH_GUI": "1"}, clear=False), \
             patch.object(vulkan_vis, "_IS_LINUX", True), \
             patch("liquid_player_visualizer.read_player_visualizer_state",
                   return_value={"active": True}), \
             patch("liquid_player_visualizer.read_published_player_window_geometry",
                   return_value=(0, 0, 640, 480)), \
             patch("liquid_player_visualizer.read_published_gui_window_geometry",
                   return_value=(72, 64, 1280, 900)), \
             patch("liquid_player_visualizer.visualizer_presets", return_value=[]):
            self.assertTrue(renderer._bond_targets_gui())
            self.assertEqual(renderer._bond_initial_geometry(), (72, 64, 1280, 900))
            renderer._sync_player_bond()
        self.assertTrue(renderer._player_bonded)
        renderer._set_windowed_size.assert_called_once_with(1280, 900, apply=True)
        renderer.set_window_pos.assert_called_once_with(72, 64)

    def test_without_match_gui_the_player_record_is_used(self):
        renderer = _renderer(vis, "LiquidVisualizerOpenGLRenderer")
        with patch.dict(os.environ, {"MP_VISUALIZER_MATCH_GUI": ""}, clear=False), \
             patch.object(vulkan_vis, "_IS_LINUX", True), \
             patch("liquid_player_visualizer.read_player_visualizer_state",
                   return_value={"active": True}), \
             patch("liquid_player_visualizer.read_published_player_window_geometry",
                   return_value=(10, 20, 1094, 778)), \
             patch("liquid_player_visualizer.read_published_gui_window_geometry",
                   return_value=(72, 64, 1280, 900)):
            self.assertFalse(renderer._bond_targets_gui())
            self.assertEqual(renderer._bond_initial_geometry(), (10, 20, 1094, 778))

    def test_the_gui_companion_stacks_against_the_gui_window(self):
        """A published window id wins over the title lookup.

        Two hosts can be open at once. Stacking must target the window this
        companion was started for, which the host names explicitly.
        """
        renderer = _renderer(vis, "LiquidVisualizerOpenGLRenderer")
        with patch.dict(os.environ, {"MP_VISUALIZER_MATCH_GUI": "1"}, clear=False), \
             patch("liquid_player_visualizer.read_published_gui_window_id", return_value=0x2A00007), \
             patch("liquid_player_visualizer.read_published_gui_window_record",
                   return_value=(1280, 900, 1.0)):
            self.assertEqual(renderer._find_player_window(MagicMock()), 0x2A00007)

    def test_gui_title_is_the_stacking_target_when_no_id_is_published(self):
        renderer = _renderer(vis, "LiquidVisualizerOpenGLRenderer")
        with patch.dict(os.environ, {"MP_VISUALIZER_MATCH_GUI": "1"}, clear=False):
            self.assertEqual(renderer._bond_host_title(), "MP Mix Manager (Liquid Glass")
        with patch.dict(os.environ, {"MP_VISUALIZER_MATCH_GUI": ""}, clear=False):
            self.assertEqual(renderer._bond_host_title(), "MP Audio Player (Liquid Glass")


class TestEachHostStopsOnlyItsOwnCompanion(unittest.TestCase):
    """Closing one host must not blank the background of the other.

    The visualizer process list is shared. ``visualizer_pids`` used to return
    every visualizer, so a player close killed MP Gui's companion (and the
    GUI's forced close killed the player's).
    """

    def test_companion_pids_are_split_by_owner(self):
        import liquid_visualizer_session as sess

        def is_gui(pid):
            return pid == 200

        with patch.object(sess, "visualizer_pids", return_value=[100, 200]), \
             patch.object(sess, "_visualizer_is_gui_companion", side_effect=is_gui):
            self.assertEqual(sess.companion_visualizer_pids(for_gui=False), [100])
            self.assertEqual(sess.companion_visualizer_pids(for_gui=True), [200])

    def test_a_player_close_leaves_the_gui_companion_alone(self):
        import liquid_visualizer_session as sess

        with patch.object(sess, "visualizer_pids", return_value=[100, 200]), \
             patch.object(sess, "_visualizer_is_gui_companion", side_effect=lambda p: p == 200), \
             patch.object(sess, "_alive", return_value=False), \
             patch.object(sess, "_other_host_window_running", return_value=False), \
             patch.object(sess, "_signal_visualizer") as signal_vis:
            sess.stop_companion_visualizer()
        signalled = [call.args[0] for call in signal_vis.call_args_list]
        self.assertEqual(signalled, [100])

    def test_a_gui_close_leaves_the_player_companion_alone(self):
        import liquid_visualizer_session as sess

        with patch.object(sess, "visualizer_pids", return_value=[100, 200]), \
             patch.object(sess, "_visualizer_is_gui_companion", side_effect=lambda p: p == 200), \
             patch.object(sess, "_alive", return_value=False), \
             patch.object(sess, "_other_host_window_running", return_value=False), \
             patch.object(sess, "_signal_visualizer") as signal_vis:
            sess.stop_companion_visualizer(force=True, for_gui=True)
        signalled = [call.args[0] for call in signal_vis.call_args_list]
        self.assertEqual(signalled, [200])

    def test_another_gui_window_keeps_the_gui_companion_up(self):
        import liquid_visualizer_session as sess

        with patch.object(sess, "visualizer_pids", return_value=[200]), \
             patch.object(sess, "_other_host_window_running", return_value=True) as other, \
             patch.object(sess, "_signal_visualizer") as signal_vis:
            sess.stop_companion_visualizer(for_gui=True)
        other.assert_called_once_with(sess._PLAYER_MARK, manager=True)
        signal_vis.assert_not_called()


class TestEachHostSpawnsItsOwnCompanion(unittest.TestCase):
    """A running companion for one host must not suppress the other's.

    This is the "the visualizer window went missing" regression. Both hosts
    used one ``pgrep`` for *any* visualizer, so opening the Liquid Player and
    then MP Gui left the menu with no background window at all.
    """

    def _run(self, entries, for_gui):
        with patch.object(vis, "_iter_visualizer_processes", return_value=list(entries)):
            return vis.companion_visualizer_running(for_gui)

    def test_player_companion_does_not_suppress_the_gui_one(self):
        entries = [(100, b"PATH=/bin\x00MP_VISUALIZER_COMPANION=1\x00")]
        self.assertTrue(self._run(entries, for_gui=False))
        self.assertFalse(self._run(entries, for_gui=True))

    def test_gui_companion_does_not_suppress_the_player_one(self):
        entries = [(100, b"MP_VISUALIZER_COMPANION=1\x00MP_VISUALIZER_MATCH_GUI=1\x00")]
        self.assertTrue(self._run(entries, for_gui=True))
        self.assertFalse(self._run(entries, for_gui=False))

    def test_a_detached_z_window_never_counts(self):
        entries = [(100, b"MP_VISUALIZER_DETACHED=1\x00")]
        self.assertFalse(self._run(entries, for_gui=False))
        self.assertFalse(self._run(entries, for_gui=True))

    def test_no_visualizer_counts_as_not_running(self):
        self.assertFalse(self._run([], for_gui=False))
        self.assertFalse(self._run([], for_gui=True))

    def test_without_proc_every_visualizer_is_returned_for_either_host(self):
        """macOS and Windows have no /proc: the owner is unknown, so keep the
        old "close every visualizer" behaviour rather than leaving one up.
        """
        import liquid_visualizer_session as sess

        with patch.object(sess, "visualizer_pids", return_value=[100, 200]), \
             patch.object(sess, "_has_proc_environ", return_value=False):
            self.assertEqual(sess.companion_visualizer_pids(for_gui=False), [100, 200])
            self.assertEqual(sess.companion_visualizer_pids(for_gui=True), [100, 200])


if __name__ == "__main__":
    unittest.main()
