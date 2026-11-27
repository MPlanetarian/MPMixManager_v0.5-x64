#!/usr/bin/env python3
"""The MP Visualizer window cap: sixteen at once, from every host."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BASE = Path(__file__).resolve().parent.parent
for path in (BASE, BASE / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import liquid_visualizer_session as session  # noqa: E402
from scripts import mp_liquid_visualizer_vulkan as vis  # noqa: E402


class WindowCounterTests(unittest.TestCase):
    def test_the_limit_is_sixteen(self):
        self.assertEqual(session.MAX_VISUALIZER_WINDOWS, 16)

    def test_count_excludes_the_calling_process(self):
        with patch.object(session, "visualizer_pids", return_value=[100, 200, 300]):
            self.assertEqual(session.visualizer_window_count(), 3)
            self.assertEqual(session.visualizer_window_count(exclude_pid=200), 2)

    def test_limit_is_reached_at_sixteen_others(self):
        with patch.object(session, "visualizer_pids", return_value=list(range(1, 16))):
            self.assertFalse(session.visualizer_window_limit_reached(exclude_pid=999))
        with patch.object(session, "visualizer_pids", return_value=list(range(1, 17))):
            self.assertTrue(session.visualizer_window_limit_reached(exclude_pid=999))

    def test_windows_redirector_and_child_count_as_one_window(self):
        command = r'X:\MP\venv\Scripts\pythonw.exe X:\MP\MP_Liquid_Visualizer.py'
        rows = [
            {"ProcessId": 100, "ParentProcessId": 10, "CommandLine": command},
            {"ProcessId": 101, "ParentProcessId": 100, "CommandLine": command},
            {"ProcessId": 102, "ParentProcessId": 10,
             "CommandLine": r'X:\MP\venv\Scripts\pythonw.exe X:\MP\MP_Liquid_Visualizer.py --preset 2'},
        ]
        self.assertEqual(session._dedupe_windows_process_rows(rows), [101, 102])

    def test_z_windows_receive_a_cascade_offset(self):
        with patch.dict(os.environ, {"MP_VISUALIZER_CASCADE_INDEX": "3"}):
            self.assertEqual(
                vis.visualizer_cascade_offset(),
                (3 * vis.VISUALIZER_CASCADE_STEP,
                 3 * vis.VISUALIZER_CASCADE_STEP),
            )
        with patch.dict(os.environ, {"MP_VISUALIZER_CASCADE_INDEX": "bad"}):
            self.assertEqual(vis.visualizer_cascade_offset(), (0, 0))

    def test_cascade_is_clamped_to_the_primary_monitor(self):
        class Mode:
            width = 1920
            height = 1080

        class Glfw:
            @staticmethod
            def glfwGetPrimaryMonitor():
                return 1

            @staticmethod
            def glfwGetVideoMode(_monitor):
                return Mode()

            @staticmethod
            def glfwGetMonitorPos(_monitor, x, y):
                x._obj.value = 0
                y._obj.value = 0

        self.assertEqual(
            vis.clamp_visualizer_origin(Glfw(), (1800, 1000), (700, 700)),
            (1220, 380),
        )


class LauncherCapTests(unittest.TestCase):
    def test_visualizer_refuses_the_seventeenth_window(self):
        with patch.object(session, "visualizer_pids", return_value=list(range(1, 17))), \
             patch.object(vis.mp_raspberry_pi, "visualizer_supported", return_value=True):
            self.assertEqual(vis.run_visualizer(), 1)

    @patch("subprocess.Popen")
    def test_z_spawn_passes_a_cascade_index(self, popen):
        """Each explicit Z window gets a different on-screen cascade slot."""
        import MP_Audio_Player_Liquid_Glass as vulkan_glass
        import MP_Audio_Player_Liquid_Glass_OpenGL as opengl_glass

        popen.return_value = MagicMock(pid=999)
        with patch.object(session, "visualizer_window_count", return_value=3), \
             patch.object(session, "visualizer_window_limit_reached", return_value=False):
            for module in (opengl_glass, vulkan_glass):
                popen.reset_mock()
                self.assertTrue(module.spawn_new_visualizer())
                self.assertEqual(
                    popen.call_args[1]["env"]["MP_VISUALIZER_CASCADE_INDEX"], "3")

    def test_every_launcher_names_the_cap(self):
        mgr = (BASE / "Mix_Archive_Manager.sh").read_text(encoding="utf-8")
        start = mgr.index("launch_mp_liquid_visualizer() {")
        fn = mgr[start:mgr.index("\n}\n", start)]
        self.assertIn("-ge 16", fn)
        self.assertIn("16 MP Visualizer windows", fn)

        for host in ("MP_Audio_Player_Liquid_Glass.py",
                     "MP_Audio_Player_Liquid_Glass_OpenGL.py"):
            text = (BASE / host).read_text(encoding="utf-8")
            self.assertIn("visualizer_window_limit_reached", text, host)

        mixer = (BASE / "MP_Liquid_Mixer" / "MP_Liquid_Mixer.py").read_text(encoding="utf-8")
        self.assertIn("visualizer_window_limit_reached", mixer)


if __name__ == "__main__":
    unittest.main()
