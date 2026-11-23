#!/usr/bin/env python3
"""The MP Visualizer window cap: sixteen at once, from every host."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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


class LauncherCapTests(unittest.TestCase):
    def test_visualizer_refuses_the_seventeenth_window(self):
        with patch.object(session, "visualizer_pids", return_value=list(range(1, 17))), \
             patch.object(vis.mp_raspberry_pi, "visualizer_supported", return_value=True):
            self.assertEqual(vis.run_visualizer(), 1)

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
