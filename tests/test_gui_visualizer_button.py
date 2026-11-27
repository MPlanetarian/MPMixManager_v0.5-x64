#!/usr/bin/env python3
"""MP Gui has its own Visualization button, the twin of the MP Liquid Player's.

The menu runs the same companion window behind itself that the player has
behind it, but until now there was no way to turn it off from the menu: the
only control was the player's chip, which drives the *player's* companion.
Pressing **V** (or clicking the VISUALIZATION chip in the menu's player banner)
now maps and unmaps MP Gui's own background window.

Two windows, one file
---------------------
Both hosts publish to ``liquid_player_visualizer.json``, so a single ``visible``
key was shared. The player's button and MP Gui's button would then blank each
other's background. MP Gui therefore writes ``gui_visible`` and the player keeps
``visible``; each companion follows only its own host's key.
"""

import json
import os
import subprocess
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
import visualizer_player_bond as bond  # noqa: E402

MANAGER = BASE / "Mix_Archive_Manager.sh"
vis_mod = SCRIPTS / "liquid_player_visualizer.py"

_GUI_ENV = {"MP_VISUALIZER_COMPANION": "1", "MP_VISUALIZER_MATCH_GUI": "1"}
_PLAYER_ENV = {"MP_VISUALIZER_COMPANION": "1", "MP_VISUALIZER_MATCH_GUI": None}


def _renderer(env):
    """A renderer with a real bond mixin and a GLFW stand-in.

    ``_init_player_bond`` reads the launch flags, but ``_bond_targets_gui()``
    is asked later, so the env is held only for construction and the host is
    pinned on the instance. A ``None`` value removes the variable; an empty
    string would still leave ``MP_VISUALIZER_MATCH_GUI`` defined, which reads
    as "this is MP Gui".
    """
    applied = {k: v for k, v in env.items() if v is not None}
    removed = [k for k, v in env.items() if v is None]
    saved = {k: os.environ.pop(k, None) for k in removed}
    gui = applied.get("MP_VISUALIZER_MATCH_GUI") == "1"
    try:
        with patch.dict(os.environ, applied, clear=False):
            renderer = object.__new__(vis.LiquidVisualizerOpenGLRenderer)
            renderer._init_player_bond()
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value
    renderer._bond_targets_gui = lambda: gui
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


class StateKeyTest(_StateFile):
    def test_a_fresh_state_file_reads_visible_on_both_hosts(self):
        state = vis.read_player_visualizer_state()
        self.assertIs(state["visible"], True)
        self.assertIs(state["gui_visible"], True)

    def test_an_older_file_without_the_key_reads_as_visible(self):
        self.state.write_text(
            json.dumps({"active": True, "visible": False, "preset_id": 3}),
            encoding="utf-8",
        )
        state = vis.read_player_visualizer_state()
        self.assertIs(state["visible"], False)
        # The key did not exist when this file was written, so MP Gui's
        # background must not be hidden by the player's old choice.
        self.assertIs(state["gui_visible"], True)

    def test_writing_one_host_flag_keeps_the_other(self):
        vis.write_player_visualizer_state(
            True, {"id": 6, "key": "sof", "name": "SOF"}, visible=False)
        vis.set_gui_visualizer_visible(True)
        state = vis.read_player_visualizer_state()
        self.assertIs(state["visible"], False, "the player's off state was lost")
        self.assertIs(state["gui_visible"], True)
        self.assertEqual(state["preset_key"], "sof")

    def test_the_gui_toggle_never_clobbers_the_preset(self):
        vis.write_player_visualizer_state(
            True, {"id": 6, "key": "stream_of_frequency", "name": "Stream of Frequency"})
        vis.set_gui_visualizer_visible(False)
        state = vis.read_player_visualizer_state()
        self.assertEqual(state["preset_id"], 6)
        self.assertEqual(state["preset_key"], "stream_of_frequency")

    def test_the_toggle_keeps_the_window_bonded(self):
        """Hiding must not un-bond: the same window comes back in place."""
        vis.set_gui_visualizer_visible(False)
        state = vis.read_player_visualizer_state()
        self.assertIs(state["active"], True, "the companion was un-bonded")
        self.assertIs(state["gui_visible"], False)


class GuiCompanionFollowsItsOwnKeyTest(_StateFile):
    def test_a_gui_companion_now_follows_the_flag(self):
        self.assertTrue(_renderer(_GUI_ENV)._companion_follows_visibility())

    def test_the_gui_flag_selects_the_gui_host(self):
        """The env flag is what makes a companion belong to MP Gui."""
        with patch.dict(os.environ, {"MP_VISUALIZER_MATCH_GUI": "1"}, clear=False):
            renderer = object.__new__(vis.LiquidVisualizerOpenGLRenderer)
            renderer._init_player_bond()
            self.assertTrue(renderer._bond_targets_gui())
            self.assertEqual(renderer._companion_visibility_key(), "gui_visible")

    def test_a_gui_companion_reads_the_gui_key(self):
        renderer = _renderer(_GUI_ENV)
        self.assertEqual(renderer._companion_visibility_key(), "gui_visible")

    def test_a_player_companion_reads_the_player_key(self):
        renderer = _renderer(_PLAYER_ENV)
        self.assertEqual(renderer._companion_visibility_key(), "visible")

    def test_a_gui_companion_is_not_hidden_by_the_players_button(self):
        vis.write_player_visualizer_state(
            True, {"id": 0, "key": "zero", "name": "Zero"}, visible=False)
        renderer = _renderer(_GUI_ENV)
        renderer._sync_companion_visibility()
        renderer._glfw.glfwHideWindow.assert_not_called()
        self.assertTrue(renderer._companion_visible)

    def test_a_player_companion_is_not_hidden_by_the_guis_button(self):
        vis.set_gui_visualizer_visible(False)
        renderer = _renderer(_PLAYER_ENV)
        renderer._sync_companion_visibility()
        renderer._glfw.glfwHideWindow.assert_not_called()
        self.assertTrue(renderer._companion_visible)

    def test_the_gui_button_hides_and_restores_the_gui_window(self):
        renderer = _renderer(_GUI_ENV)
        vis.set_gui_visualizer_visible(False)
        renderer._sync_companion_visibility()
        renderer._glfw.glfwHideWindow.assert_called_once_with(renderer._window)
        self.assertFalse(renderer._companion_visible)

        vis.set_gui_visualizer_visible(True)
        renderer._visibility_check_at = 0.0
        renderer._sync_companion_visibility()
        renderer._glfw.glfwShowWindow.assert_called_once_with(renderer._window)
        self.assertTrue(renderer._companion_visible)

    def test_a_gui_companion_starts_hidden_when_the_button_is_off(self):
        vis.set_gui_visualizer_visible(False)
        self.assertTrue(_renderer(_GUI_ENV)._bond_should_start_hidden())

    def test_a_z_window_is_still_never_eligible(self):
        with patch.dict(os.environ, {"MP_VISUALIZER_DETACHED": "1"}, clear=False):
            self.assertFalse(_renderer(_GUI_ENV)._companion_follows_visibility())


class ShellToggleTest(unittest.TestCase):
    def _run(self, script, state_file):
        env = dict(
            os.environ,
            MP_GUI_SESSION="1",
            MP_LIQUID_VISUALIZER_STATE=str(state_file),
            MP_PY=sys.executable,
            WEATHER_ENABLED="false",
            PLANETS_ENABLED="false",
        )
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", script],
            cwd=str(BASE), env=env, capture_output=True, text=True, check=False,
        )

    def test_the_shell_toggle_writes_and_reads_the_gui_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "vis.json"
            script = r"""
            source ./Mix_Archive_Manager.sh >/dev/null 2>&1
            set_theme_colors dreamworlds
            _mp_gui_visualizer_on && printf 'start:on\n' || printf 'start:off\n'
            _toggle_mp_gui_visualizer
            _mp_gui_visualizer_on && printf 'after1:on\n' || printf 'after1:off\n'
            _toggle_mp_gui_visualizer
            _mp_gui_visualizer_on && printf 'after2:on\n' || printf 'after2:off\n'
            """
            proc = self._run(script, state)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            self.assertEqual(
                proc.stdout.split(),
                ["start:on", "after1:off", "after2:on"],
            )
            payload = json.loads(state.read_text(encoding="utf-8"))
            self.assertIs(payload["gui_visible"], True)
            self.assertIs(payload["active"], True)

    def test_the_shell_toggle_leaves_the_player_state_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "vis.json"
            state.write_text(json.dumps({
                "active": True, "visible": False, "gui_visible": True,
                "preset_id": 6, "preset_key": "stream_of_frequency",
                "preset_name": "Stream of Frequency",
            }), encoding="utf-8")
            script = r"""
            source ./Mix_Archive_Manager.sh >/dev/null 2>&1
            set_theme_colors dreamworlds
            _toggle_mp_gui_visualizer
            """
            proc = self._run(script, state)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            payload = json.loads(state.read_text(encoding="utf-8"))
            self.assertIs(payload["gui_visible"], False)
            self.assertIs(payload["visible"], False, "the player's flag was changed")
            self.assertEqual(payload["preset_key"], "stream_of_frequency")


class MenuWiringTest(unittest.TestCase):
    def setUp(self):
        self.text = MANAGER.read_text(encoding="utf-8")

    def test_v_toggles_the_visualizer_and_search_still_types_it(self):
        chunk = self.text[self.text.index("CHAR:v|CHAR:V)"):]
        chunk = chunk[:chunk.index("continue ;;") + 12]
        self.assertIn('search_mode" -eq 1', chunk)
        self.assertLess(chunk.find("search_query"), chunk.find("_toggle_mp_gui_visualizer"))

    def test_the_banner_draws_the_chip_only_in_mp_gui(self):
        self.assertIn('_mp_gui_visualizer_on && _vis_on=1', self.text)
        self.assertIn("VISUALIZATION  ", self.text)
        start = self.text.index('if [ "${MP_GUI_SESSION:-0}" = "1" ]; then\n        local _vis_on=0')
        # Run to the end of the banner block rather than a fixed offset, so
        # later edits to the chip cannot silently move the assertions out.
        end = self.text.index("banner_lines=4", start)
        chunk = self.text[start:end + len("banner_lines=5")]
        self.assertIn("banner_lines=5", chunk)
        self.assertIn("_mp_gui_vis_row", chunk)

    def test_a_click_on_the_chip_toggles_it(self):
        start = self.text.index("_menu_handle_mouse() {")
        chunk = self.text[start:start + 1400]
        self.assertIn("_mp_gui_vis_row", chunk)
        self.assertIn("_toggle_mp_gui_visualizer", chunk)

    def test_a_narrow_pane_drops_the_hint_before_the_button(self):
        """The chip must never push the pane's right border off the row."""
        start = self.text.index("        local _vis_pad=$(( inner_w + 2 - 1 - 3 - ${#_vis_face}")
        chunk = self.text[start:start + 1400]
        self.assertIn("_vis_show_hint=0", chunk)
        # The hint is only appended while it fits.
        self.assertIn('[ "$_vis_show_hint" -eq 1 ] && _vis_text+="  ${_vis_hint}"', chunk)

    def test_the_key_hint_names_v_after_the_pinned_keys(self):
        """V is appended, so the existing [B] Bash / MP Harmony hint stays intact."""
        self.assertIn("[V] Visualizer", self.text)
        # The MP Harmony part of the hint is conditional: it is only appended
        # when the optional agent is installed, so the label follows [B] Bash
        # through ${_harmony_hint} instead of being hard-coded.
        self.assertIn(
            '[B] Bash${_harmony_hint}, [C] Theme', self.text
        )
        self.assertIn('_harmony_hint=", [Shift+A] MP Harmony"', self.text)

    def test_the_cli_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "vis.json"
            env = dict(os.environ, MP_LIQUID_VISUALIZER_STATE=str(state))
            def run(*args):
                return subprocess.run(
                    [sys.executable, str(vis_mod), *args],
                    env=env, capture_output=True, text=True, check=False,
                )
            self.assertEqual(run("--gui-visible").stdout.strip(), "on")
            self.assertEqual(run("--set-gui-visible", "off").returncode, 0)
            self.assertEqual(run("--gui-visible").stdout.strip(), "off")
            self.assertEqual(run("--toggle-gui-visible").returncode, 0)
            self.assertEqual(run("--gui-visible").stdout.strip(), "on")
            self.assertEqual(run("--set-gui-visible", "nonsense").returncode, 2)


if __name__ == "__main__":
    unittest.main()
