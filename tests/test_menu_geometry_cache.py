#!/usr/bin/env python3
"""The menu must not run a process-table scan on every keystroke.

``update_terminal_geometry`` runs at the top of every menu frame. Its Windows
helper check used ``ps -W``, which lists every Windows process and costs tens
to hundreds of milliseconds on Git Bash, so each key press waited for it.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANAGER = ROOT / "Mix_Archive_Manager.sh"


class MenuGeometryCacheTest(unittest.TestCase):
    def test_helper_liveness_is_cached_between_frames(self):
        source = MANAGER.read_text(encoding="utf-8", errors="replace")
        block = source.split("_win_console_size() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("ps -W", block)
        self.assertIn("_WIN_HIDER_CHECK_S", block)
        self.assertIn("-ge 2", block)
        # A freshly started helper must be checked before the cache is trusted.
        starter = source.split("_start_win_cursor_hider() {", 1)[1].split(
            "\n_stop_win_cursor_hider", 1
        )[0]
        self.assertIn('_WIN_HIDER_CHECK_S=""', starter)


if __name__ == "__main__":
    unittest.main()
