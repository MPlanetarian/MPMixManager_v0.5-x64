"""Tests for the Web Companion desktop launch pad plumbing.

The dashboard runs as a user service, so ``mp_suite_control`` is what lets a
phone start or close the desktop suite. These tests pin the pieces that are
easy to regress: which roles the four buttons map to, which launchers they
use, how a live process table is read, and the guarantee that Close All never
signals the shared tray icon.
"""

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import mp_suite_control as control
import mp_system_tray as tray


def proc(pid, ppid, cmdline, environ=""):
    return tray.ProcessInfo(pid, ppid, cmdline, environ)


class TestRoles(unittest.TestCase):
    def test_five_buttons_in_launch_order(self):
        # app_definitions() lists installed apps only; pin all five so the
        # assertion does not depend on this machine's installed_apps file.
        with patch.object(control, "installed_components",
                          return_value=set(control.ROLE_ORDER)):
            apps = control.app_definitions()
        self.assertEqual(
            [app["id"] for app in apps],
            ["gui", "manager", "liquid", "visualizer", "mixer"],
        )
        self.assertEqual(
            [app["label"] for app in apps],
            [
                "MP Gui",
                "MP Mix Manager (CLI)",
                "MP Liquid Player",
                "MP Liquid Visualizer",
                "MP Liquid Mixer",
            ],
        )

    def test_launchers_match_the_tray_and_desktop_shortcuts(self):
        gui = control.launch_argv(control.ROLE_GUI, ROOT)
        self.assertEqual(gui[0], "bash")
        self.assertTrue(gui[1].endswith("MP_Gui.sh"))

        manager = control.launch_argv(control.ROLE_MANAGER, ROOT)
        self.assertTrue(str(manager[0]).endswith("launch-manager"))

        liquid = control.launch_argv(control.ROLE_LIQUID, ROOT)
        self.assertTrue(str(liquid[0]).endswith("launch-liquid-glass"))

        visualizer = control.launch_argv(control.ROLE_VISUALIZER, ROOT)
        self.assertTrue(visualizer[1].endswith("launch_liquid_visualizer.sh"))

        mixer = control.launch_argv(control.ROLE_MIXER, ROOT)
        self.assertEqual(mixer[0], "bash")
        self.assertTrue(mixer[1].endswith("launch_liquid_mixer.sh"))

    def test_unknown_role_is_rejected(self):
        with self.assertRaises(ValueError):
            control.launch_argv("not-an-app", ROOT)


class TestDetection(unittest.TestCase):
    def setUp(self):
        # running_roles() refuses to look at the process table on a host the
        # dashboard does not support (/proc may be absent). These tests are
        # about how a supplied table is classified, so enable the detector.
        patcher = patch.object(control, "is_supported", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_all_five_are_detected_from_a_process_list(self):
        processes = [
            proc(10, 1, "python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py --manager"),
            proc(11, 5, "bash /opt/Mix_Archive_Manager.sh"),
            proc(5, 1, "/usr/bin/konsole -e /opt/bin/mix-archive-manager"),
            proc(12, 1, "python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py"),
            proc(13, 1, "python3 /opt/MP_Liquid_Visualizer.py --preset random"),
            proc(15, 1, "python3 /opt/MP_Liquid_Mixer/MP_Liquid_Mixer.py"),
            proc(14, 1, "python3 /opt/scripts/mp_system_tray.py"),
        ]
        self.assertEqual(
            control.running_roles(processes),
            {"gui", "manager", "liquid", "visualizer", "mixer"},
        )

    def test_a_manager_hosted_in_mp_gui_is_not_the_cli(self):
        """MP_Gui.sh runs Mix_Archive_Manager.sh inside its own window."""
        processes = [
            proc(10, 1, "python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py --manager"),
            proc(11, 10, "bash /opt/Mix_Archive_Manager.sh", "MP_GUI_SESSION=1\nTERM=xterm"),
        ]
        self.assertEqual(control.running_roles(processes), {"gui"})

    def test_the_visualizer_is_not_confused_with_the_player(self):
        processes = [proc(13, 1, "python3 /opt/MP_Liquid_Visualizer.py")]
        self.assertEqual(control.running_roles(processes), {"visualizer"})

    def test_empty_table_reports_nothing_running(self):
        self.assertEqual(control.running_roles([]), set())

    def test_status_payload_carries_a_running_flag_per_app(self):
        processes = [proc(12, 1, "python3 /opt/MP_Audio_Player_Liquid_Glass.py")]
        # app_status() only lists installed apps, and that set comes from the
        # user's config. Pin all five so the assertion does not depend on what
        # this machine happens to have set up.
        control.reset_status_cache()
        with patch.object(control, "is_supported", return_value=True), \
             patch.object(control, "installed_components",
                          return_value=set(control.ROLE_ORDER)), \
             patch.object(control, "_process_table", return_value=processes):
            status = control.app_status()
        self.assertTrue(status["supported"])
        flags = {app["id"]: app["running"] for app in status["apps"]}
        self.assertEqual(
            flags,
            {
                "gui": False,
                "manager": False,
                "liquid": True,
                "visualizer": False,
                "mixer": False,
            },
        )


class TestLaunch(unittest.TestCase):
    def test_launching_when_closed_spawns_the_launcher(self):
        # Pin the installed set and the TTY flag too: a test bed may have only
        # some apps installed, and a host switched into TTY Audio Mode keeps a
        # flag file that makes launch_app refuse every desktop window.
        with patch.object(control, "is_supported", return_value=True), \
             patch.object(control, "tty_mode_active", return_value=False), \
             patch.object(control, "installed_components",
                          return_value=set(control.ROLE_ORDER)), \
             patch.object(control, "running_roles", return_value=set()), \
             patch.object(control, "_spawn") as spawn:
            result = control.launch_app(control.ROLE_GUI)
        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "launch")
        self.assertEqual(spawn.call_count, 1)

    def test_launching_when_open_focuses_instead(self):
        # launch_app refuses an app that is not installed, and the installed
        # set comes from the user's config, so pin it here too.
        with patch.object(control, "is_supported", return_value=True), \
             patch.object(control, "tty_mode_active", return_value=False), \
             patch.object(control, "installed_components",
                          return_value=set(control.ROLE_ORDER)), \
             patch.object(control, "running_roles", return_value={"liquid"}), \
             patch.object(control, "_focus", return_value=True) as focus, \
             patch.object(control, "_spawn") as spawn:
            result = control.launch_app(control.ROLE_LIQUID)
        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "focus")
        self.assertEqual(focus.call_count, 1)
        self.assertEqual(spawn.call_count, 0)

    def test_unsupported_host_does_nothing(self):
        with patch.object(control, "is_supported", return_value=False), \
             patch.object(control, "_spawn") as spawn:
            result = control.launch_app(control.ROLE_GUI)
        self.assertFalse(result["success"])
        self.assertEqual(result["action"], "unsupported")
        self.assertEqual(spawn.call_count, 0)


class TestCloseAll(unittest.TestCase):
    def test_close_all_leaves_the_tray_icon_running(self):
        calls = {}

        def fake_shutdown(base):
            calls["shutdown"] = base
            return []

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)
            with patch.object(control, "is_supported", return_value=True), \
                 patch.object(control, "running_roles", side_effect=[{"gui", "manager", "liquid", "visualizer", "mixer"}, set()]), \
                 patch.object(control, "tray_running", return_value=True), \
                 patch.object(control, "_tray_module") as module:
                module.return_value.shutdown_suite = fake_shutdown
                module.return_value.request_keepalive = lambda _cfg: tray.request_keepalive(config)
                module.return_value.config_dir = lambda: config
                module.return_value.TrayLock.held.return_value = True
                result = control.close_all_apps(ROOT)

            self.assertTrue(result["success"])
            self.assertEqual(result["remaining"], [])
            self.assertEqual(result["action"], "close_all")
            # The keepalive marker is what keeps a shortcut-started tray alive.
            self.assertTrue(tray.keepalive_active(config))
        # And the icon is reported as still up, never as one of the closed apps.
        self.assertTrue(result["tray_running"])
        self.assertEqual(
            result["closed"], ["gui", "liquid", "manager", "mixer", "visualizer"]
        )
        self.assertNotIn("tray", result["closed"])

    def test_close_all_is_unsupported_off_linux(self):
        with patch.object(control, "is_supported", return_value=False):
            result = control.close_all_apps(ROOT)
        self.assertFalse(result["success"])
        self.assertEqual(result["action"], "unsupported")

    def test_close_all_can_answer_before_the_suite_is_stopped(self):
        """The phone must not wait out the 40-second exit movie.

        ``wait=False`` acknowledges immediately and sweeps on a background
        thread. The launch pad poll is what turns the buttons off.
        """
        release = threading.Event()
        swept = []

        def slow_close(base, before):
            # Stand in for the exit movie: hold the sweep until the test says go.
            release.wait(10)
            swept.append(sorted(before))
            return set(before), set()

        with patch.object(control, "is_supported", return_value=True), \
             patch.object(control, "tty_mode_active", return_value=False), \
             patch.object(control, "running_roles", return_value={"gui", "manager"}), \
             patch.object(control, "tray_running", return_value=True), \
             patch.object(control, "_close_suite", side_effect=slow_close), \
             patch.object(control, "_close_thread", None):
            started = time.monotonic()
            result = control.close_all_apps(ROOT, wait=False)
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 1.0, "the reply must not block on the sweep")
            self.assertEqual(result["action"], "closing")
            self.assertTrue(result["closing"])
            self.assertEqual(result["remaining"], ["gui", "manager"])
            self.assertFalse(swept, "the sweep should still be running")

            release.set()
            for _ in range(100):
                if swept:
                    break
                time.sleep(0.02)
        self.assertEqual(swept, [["gui", "manager"]])

    def test_a_second_close_all_does_not_stack_a_thread(self):
        """A repeat press runs one more pass, not a second thread."""
        release = threading.Event()
        calls = []

        def slow_close(base, before):
            calls.append(sorted(before))
            release.wait(10)
            return set(before), set()

        with patch.object(control, "is_supported", return_value=True), \
             patch.object(control, "tty_mode_active", return_value=False), \
             patch.object(control, "running_roles", return_value={"gui"}), \
             patch.object(control, "tray_running", return_value=True), \
             patch.object(control, "_close_suite", side_effect=slow_close), \
             patch.object(control, "_close_thread", None):
            first = control.close_all_apps(ROOT, wait=False)
            second = control.close_all_apps(ROOT, wait=False)
            release.set()
            for _ in range(100):
                if len(calls) >= 2:
                    break
                time.sleep(0.02)
            time.sleep(0.1)
        self.assertTrue(first["closing"])
        # The second press is acknowledged and honoured with a follow-up pass,
        # because the user may have reopened something during the sweep.
        self.assertTrue(second["closing"])
        self.assertEqual(len(calls), 2, "one follow-up pass, not a stacked thread")

    def test_close_all_with_nothing_open_reports_done(self):
        with patch.object(control, "is_supported", return_value=True), \
             patch.object(control, "tty_mode_active", return_value=False), \
             patch.object(control, "running_roles", return_value=set()), \
             patch.object(control, "tray_running", return_value=True), \
             patch.object(control, "_start_background_close") as starter:
            result = control.close_all_apps(ROOT, wait=False)
        self.assertEqual(result["action"], "close_all")
        self.assertFalse(result["closing"])
        starter.assert_not_called()


class TestKeepalive(unittest.TestCase):
    def test_keepalive_expires(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)
            tray.request_keepalive(config)
            self.assertTrue(tray.keepalive_active(config))
            # A stale stamp must not hold the icon up forever.
            path = tray.keepalive_path(config)
            path.write_text("0\n", encoding="utf-8")
            self.assertFalse(tray.keepalive_active(config))

    def test_missing_keepalive_is_inactive(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(tray.keepalive_active(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
