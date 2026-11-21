#!/usr/bin/env python3
"""Closing a glass window must not flash helper consoles.

The close path runs from pythonw.exe, which has no console. Every PowerShell
window scan, taskkill, and the bash movie helper therefore allocated its own
console window when it ran. They must all use CREATE_NO_WINDOW on Windows.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class CloseNoConsoleWindowsTest(unittest.TestCase):
    def test_visualizer_stop_helpers_are_hidden(self):
        source = (ROOT / "scripts" / "liquid_visualizer_session.py").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn("_quiet_kwargs", source)
        scan = source.split("def _pgrep", 1)[1].split("def _cmdline", 1)[0]
        self.assertIn("_quiet_kwargs()", scan)
        kill = source.split("def _signal_visualizer", 1)[1].split(
            "def stop_companion_visualizer", 1
        )[0]
        self.assertIn("_quiet_kwargs()", kill)

    def test_window_counter_hides_its_powershell(self):
        source = (ROOT / "scripts" / "mp_instance_limit.py").read_text(
            encoding="utf-8", errors="replace"
        )
        block = source.split("def _proc_table_from_windows", 1)[1].split(
            "\ndef ", 1
        )[0]
        self.assertIn("creationflags=0x08000000", block)

    def test_exit_video_hides_the_bash_helper(self):
        source = (ROOT / "scripts" / "exit_video.py").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn("_NO_WINDOW_FLAGS", source)
        self.assertIn("creationflags=_NO_WINDOW_FLAGS", source)


if __name__ == "__main__":
    unittest.main()
