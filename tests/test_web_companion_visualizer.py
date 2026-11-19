"""Tests for the Web Companion's MP Visualizer button and its TTY guard.

The button owns exactly one visualizer window. These tests pin the three
states (open -> fullscreen -> close), the guarantee that another visualizer on
the machine is never touched, and the TTY Audio Mode lock-out that disables
every desktop launcher.
"""

import json
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import mp_suite_control as control
import mp_visualizer_control as vis


def live_state(tmp, pid: int = 4242, fullscreen: bool = False, instance: str = "abc123"):
    path = Path(tmp) / vis.STATE_FILE_NAME
    vis.write_state({
        "instance": instance,
        "pid": pid,
        "fullscreen": fullscreen,
        "title": "MP Liquid Visualizer",
    }, path)
    return vis.read_state(path)


class StateFileTest(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / vis.STATE_FILE_NAME
            vis.write_state({"instance": "tok", "pid": os.getpid(),
                             "fullscreen": True}, path)
            state = vis.read_state(path)
            self.assertIsNotNone(state)
            self.assertEqual(state["instance"], "tok")
            self.assertEqual(state["pid"], os.getpid())
            self.assertTrue(state["fullscreen"])

    def test_missing_or_corrupt_file_reads_as_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / vis.STATE_FILE_NAME
            self.assertIsNone(vis.read_state(path))
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(vis.read_state(path))
            path.write_text(json.dumps({"instance": "", "pid": 5}), encoding="utf-8")
            self.assertIsNone(vis.read_state(path))

    def test_a_pending_record_is_trusted_only_briefly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / vis.STATE_FILE_NAME
            path.write_text(json.dumps({
                "instance": "tok", "pid": 1, "pending": True,
                "updated": 0.0,
            }), encoding="utf-8")
            self.assertIsNone(vis.read_state(path))

    def test_a_stale_window_cannot_delete_a_newer_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / vis.STATE_FILE_NAME
            vis.write_state({"instance": "new", "pid": os.getpid()}, path)
            vis.clear_state("old", path)
            self.assertIsNotNone(vis.read_state(path))
            vis.clear_state("new", path)
            self.assertIsNone(vis.read_state(path))


class OwnWindowOnlyTest(unittest.TestCase):
    """Signals go to the recorded pid, never to a name or a process group."""

    def test_a_recycled_pid_is_not_our_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Our own pid is alive, but its command line has no token.
            state = live_state(tmp, pid=os.getpid(), instance="notinmypid")
            self.assertFalse(vis.instance_is_live(state))

    def test_toggle_is_sent_to_the_recorded_pid_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = live_state(tmp, pid=4242, fullscreen=False)
            calls = []
            with patch.object(vis, "instance_is_live", return_value=True), \
                 patch.object(vis.os, "kill",
                              side_effect=lambda pid, sig: calls.append((pid, sig))):
                self.assertTrue(vis.request_toggle_fullscreen(state))
            self.assertEqual(calls, [(4242, int(signal.SIGUSR1))])

    def test_close_then_escalates_to_the_same_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = live_state(tmp, pid=4242, fullscreen=True)
            calls = []
            # The deadline expires at once, so the escalation runs immediately.
            with patch.object(vis, "instance_is_live", return_value=True), \
                 patch.object(vis, "pid_alive", return_value=True), \
                 patch.object(vis.time, "monotonic", side_effect=[0.0, 99.0]), \
                 patch.object(vis, "signal_for",
                              side_effect=lambda name: {"SIGUSR2": 12,
                                                        "SIGKILL": 9}.get(name)), \
                 patch.object(vis.os, "kill",
                              side_effect=lambda pid, sig: calls.append((pid, sig))):
                vis.request_close(state)
            self.assertEqual(calls[0], (4242, 12))
            self.assertEqual(calls[-1], (4242, 9))
            self.assertTrue(all(pid == 4242 for pid, _ in calls))

    def test_no_window_means_no_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = live_state(tmp, pid=4242)
            with patch.object(vis, "instance_is_live", return_value=False), \
                 patch.object(vis.os, "kill") as kill:
                self.assertFalse(vis.request_toggle_fullscreen(state))
                self.assertFalse(vis.request_close(state))
            kill.assert_not_called()


class ThreeStateButtonTest(unittest.TestCase):
    def _run(self, state, action_result=None):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(control, "is_supported", return_value=True), \
                 patch.object(control, "tty_mode_active", return_value=False), \
                 patch.object(control, "config_dir", return_value=Path(tmp)), \
                 patch.object(vis, "state_path",
                              return_value=Path(tmp) / vis.STATE_FILE_NAME), \
                 patch.object(control, "running_roles", return_value=set()):
                if state is None:
                    path = Path(tmp) / vis.STATE_FILE_NAME
                    if path.exists():
                        path.unlink()
                else:
                    vis.write_state(state, Path(tmp) / vis.STATE_FILE_NAME)
                with patch.object(vis, "instance_is_live", return_value=True):
                    return control.web_visualizer_action()

    def test_first_press_opens_one_window(self):
        with patch.object(control, "_spawn", return_value=4242) as spawn:
            result = self._run(None)
        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "launch")
        self.assertEqual(spawn.call_count, 1)
        argv = spawn.call_args[0][0]
        self.assertIn("--web-instance", argv)
        env = spawn.call_args[1]["extra_env"]
        self.assertIn(vis.ENV_INSTANCE, env)
        self.assertEqual(env["MP_VISUALIZER_DETACHED"], "1")

    def test_second_press_goes_fullscreen(self):
        with patch.object(vis, "request_toggle_fullscreen", return_value=True) as toggle, \
             patch.object(control, "_spawn") as spawn:
            result = self._run({"instance": "abc123", "pid": 4242,
                                "fullscreen": False})
        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "fullscreen")
        toggle.assert_called_once()
        spawn.assert_not_called()

    def test_third_press_closes_only_that_window(self):
        with patch.object(vis, "request_close", return_value=True) as closer, \
             patch.object(control, "_spawn") as spawn:
            result = self._run({"instance": "abc123", "pid": 4242,
                                "fullscreen": True})
        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "close")
        closer.assert_called_once()
        spawn.assert_not_called()

    def test_a_tap_while_the_window_is_starting_never_spawns_twice(self):
        with patch.object(control, "_spawn") as spawn:
            result = self._run({"instance": "abc123", "pid": 4242,
                                "pending": True, "fullscreen": False})
        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "already_running")
        spawn.assert_not_called()

    def test_a_tap_while_starting_closes_when_the_window_answers(self):
        """The press must not be swallowed: a second tap cancels the start."""
        with patch.object(vis, "request_close", return_value=True) as closer, \
             patch.object(control, "_spawn") as spawn:
            result = self._run({"instance": "abc123", "pid": 4242,
                                "pending": True, "fullscreen": False})
        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "close")
        closer.assert_called_once()
        spawn.assert_not_called()

    def test_pressing_again_before_the_window_republishes_closes_it(self):
        """The reported bug: press 1 opens, press 2 fullscreens, press 3 closes.

        The window applies a toggle on its next render frame and only then
        republishes, so a third tap that arrives first used to read the stale
        "windowed" record and send a *second* toggle -- the window stayed open
        and the button looked broken.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / vis.STATE_FILE_NAME
            with patch.object(control, "is_supported", return_value=True), \
                 patch.object(control, "tty_mode_active", return_value=False), \
                 patch.object(control, "config_dir", return_value=Path(tmp)), \
                 patch.object(vis, "state_path", return_value=path), \
                 patch.object(control, "running_roles", return_value=set()), \
                 patch.object(vis, "instance_is_live", return_value=True), \
                 patch.object(vis, "request_toggle_fullscreen", return_value=True), \
                 patch.object(vis, "request_close", return_value=True) as closer:
                vis.write_state({"instance": "abc123", "pid": 4242,
                                 "fullscreen": False}, path)
                second = control.web_visualizer_action()
                # The window has not republished yet. Press again at once.
                third = control.web_visualizer_action()
        self.assertEqual(second["action"], "fullscreen")
        self.assertEqual(third["action"], "close",
                         "the third press must close, not toggle again")
        closer.assert_called_once()

    def test_a_lost_fullscreen_command_does_not_latch_the_button(self):
        """A command the window never honoured stops being trusted."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / vis.STATE_FILE_NAME
            vis.write_state({"instance": "abc123", "pid": 4242,
                             "fullscreen": False}, path)
            vis.record_command({"instance": "abc123", "pid": 4242,
                                "fullscreen": False},
                               vis.PHASE_FULLSCREEN, path)
            state = vis.read_state(path)
            self.assertEqual(state["phase"], vis.PHASE_FULLSCREEN)
            # Rewind the stamp past the grace window: the window's own
            # truthful "windowed" publish wins again.
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["command_at"] = time.time() - (vis.COMMAND_GRACE_SECONDS + 1)
            path.write_text(json.dumps(raw), encoding="utf-8")
            self.assertEqual(vis.read_state(path)["phase"], vis.PHASE_WINDOWED)

    def test_recording_a_command_keeps_the_window_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / vis.STATE_FILE_NAME
            vis.write_state({"instance": "abc123", "pid": 4242,
                             "fullscreen": False}, path)
            vis.record_command({"instance": "abc123", "pid": 4242},
                               vis.PHASE_FULLSCREEN, path)
            state = vis.read_state(path)
        self.assertEqual(state["instance"], "abc123")
        self.assertEqual(state["pid"], 4242)
        self.assertEqual(state["phase"], vis.PHASE_FULLSCREEN)

    def test_status_has_no_stray_keys(self):
        with patch.object(control, "_visualizer_state", return_value=None):
            self.assertEqual(control.web_visualizer_status(),
                             {"running": False, "fullscreen": False})


class TtyModeTest(unittest.TestCase):
    def test_flag_file_puts_the_suite_in_tty_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(control, "config_dir", return_value=Path(tmp)), \
                 patch.dict(os.environ, {"MP_MIX_MANAGER_TTY_MODE": "0"}):
                self.assertFalse(control.tty_mode_active())
                (Path(tmp) / "tty_audio_mode").write_text("1\n", encoding="utf-8")
                self.assertTrue(control.tty_mode_active())

    def test_environment_flag_is_enough(self):
        with patch.dict(os.environ, {"MP_MIX_MANAGER_TTY_MODE": "1"}):
            self.assertTrue(control.tty_mode_active())

    def test_every_launcher_is_refused_in_tty_mode(self):
        with patch.object(control, "is_supported", return_value=True), \
             patch.object(control, "tty_mode_active", return_value=True), \
             patch.object(control, "_spawn") as spawn:
            for role in control.ROLE_ORDER:
                self.assertEqual(control.launch_app(role)["action"], "tty_mode")
            self.assertEqual(control.web_visualizer_action()["action"], "tty_mode")
            self.assertEqual(control.close_all_apps()["action"], "tty_mode")
        spawn.assert_not_called()

    def test_status_payload_announces_the_disabled_buttons(self):
        with patch.object(control, "is_supported", return_value=True), \
             patch.object(control, "tty_mode_active", return_value=True), \
             patch.object(control, "running_roles", return_value=set()), \
             patch.object(control, "tray_running", return_value=False), \
             patch.object(control, "web_visualizer_status",
                          return_value={"running": False, "fullscreen": False}), \
             patch.object(control, "_touch_keepalive") as keepalive:
            control.reset_status_cache()
            payload = control.app_status()
        self.assertTrue(payload["tty_mode"])
        self.assertTrue(payload["launch_disabled"])
        keepalive.assert_not_called()


class PageMarkupTest(unittest.TestCase):
    """The dashboard HTML must carry the new button and the TTY banner."""

    def _page(self, tty_mode):
        import mix_web_companion as companion
        # The launch pad only renders a button for an installed app, and the
        # installed set lives in the user's config, so pin all four here or the
        # page depends on what this machine happens to have set up.
        with patch.object(control, "is_supported", return_value=True), \
             patch.object(control, "tty_mode_active", return_value=tty_mode), \
             patch.object(control, "installed_components",
                          return_value=set(control.ROLE_ORDER)):
            return companion.render_html_page(companion.THEMES["cosmic_supernova"])

    def test_visualizer_button_is_rendered(self):
        page = self._page(False)
        self.assertIn('id="launchVisualizer"', page)
        self.assertIn('onclick="launchVisualizer()"', page)
        self.assertIn("/api/launchpad/visualizer", page)

    def test_tty_mode_disables_and_explains(self):
        page = self._page(True)
        self.assertIn("TTY Audio Mode", page)
        self.assertIn('id="launchPadTty"', page)
        self.assertIn("disabled", page)
        self.assertIn("tty-disabled", page)


class OverlayLayoutTest(unittest.TestCase):
    """The engage gate must never scroll the button off the page."""

    def test_overlay_does_not_scroll(self):
        import mix_web_companion as companion
        page = companion.render_html_page(companion.THEMES["cosmic_supernova"])
        start = page.index("#streamOverlay {")
        block = page[start:start + 1200]
        self.assertIn("overflow: hidden", block)
        self.assertIn("justify-content: center", block)
        self.assertNotIn("overflow-y: auto", page[page.index(".overlay-card {"):][:600])


class CsrfHeaderTest(unittest.TestCase):
    """Every dashboard POST must carry the header the server demands.

    The server refuses a mutating request without ``X-MP-Companion``. The page
    used to make its POSTs bare, so every control tap was answered with
    "missing_csrf_header" and nothing happened.
    """

    def test_page_wraps_fetch_and_sends_the_required_header(self):
        import mix_web_companion as companion
        page = companion.render_html_page(companion.THEMES["cosmic_supernova"])
        self.assertIn("MP_CSRF_HEADER", page)
        self.assertIn("X-MP-Companion", page)
        self.assertIn("window.fetch = function", page)

    def test_header_name_matches_the_server(self):
        import mp_companion_security as sec
        import mix_web_companion as companion
        page = companion.render_html_page(companion.THEMES["cosmic_supernova"])
        self.assertIn(f"const MP_CSRF_HEADER = '{sec.REQUIRED_HEADER}'", page)


if __name__ == "__main__":
    unittest.main()
