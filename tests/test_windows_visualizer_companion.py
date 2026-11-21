#!/usr/bin/env python3
"""The Windows companion visualizer must launch, bond and sit behind its host.

Three separate Windows defects kept it from appearing:

* the "is a visualizer running?" PowerShell query matched its own
  powershell.exe command line, so the launcher and the host always skipped it;
* os.kill(pid, 0) raises WinError 87 here, so every published geometry record
  looked stale;
* the bond itself was gated to Linux and had no Windows stacking branch.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class WindowsVisualizerCompanionTest(unittest.TestCase):
    def test_launcher_and_host_do_not_match_their_own_query(self):
        host = (ROOT / "MP_Audio_Player_Liquid_Glass_OpenGL.py").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn('$_.Name -like "python*"', host)
        for name in ("launch_mp_gui.bat", "launch_liquid_glass.bat"):
            text = (ROOT / "scripts" / name).read_text(encoding="utf-8", errors="replace")
            self.assertIn("$_.Name -like 'python*'", text, name)

    def test_windows_host_publishes_geometry(self):
        host = (ROOT / "MP_Audio_Player_Liquid_Glass_OpenGL.py").read_text(
            encoding="utf-8", errors="replace"
        )
        publish = host.split("def _publish_player_window_geometry", 1)[1].split(
            "\ndef ", 1
        )[0]
        guard = publish.split("try:", 1)[0]
        self.assertNotIn('not sys.platform.startswith("linux")', guard)
        self.assertIn('if self.mode == "TRACKLIST" or self.renderer is None:', guard)
        self.assertIn("win32_window()", host)
        windows = host.split("def _run_windows", 1)[1]
        self.assertIn("self._publish_player_window_geometry()", windows)

    def test_windows_pid_liveness_uses_the_process_handle(self):
        source = (ROOT / "scripts" / "liquid_player_visualizer.py").read_text(
            encoding="utf-8", errors="replace"
        )
        block = source.split("def _pid_alive", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("OpenProcess", block)
        self.assertIn("GetExitCodeProcess", block)

    def test_the_bond_runs_and_stacks_on_windows(self):
        source = (ROOT / "scripts" / "visualizer_player_bond.py").read_text(
            encoding="utf-8", errors="replace"
        )
        applies = source.split("def _bonding_applies", 1)[1].split(
            "\n    def ", 1
        )[0]
        self.assertIn('sys.platform != "win32"', applies)
        lower = source.split("def _lower_below_player", 1)[1].split(
            "\n    def ", 1
        )[0]
        self.assertIn("SetWindowPos", lower)
        self.assertIn("win32_window()", lower)


if __name__ == "__main__":
    unittest.main()
