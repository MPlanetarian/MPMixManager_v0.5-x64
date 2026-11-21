#!/usr/bin/env python3
"""Windows console fonts draw emoji as squares; the header uses ASCII tags."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANAGER = ROOT / "Mix_Archive_Manager.sh"


class WindowsConsoleIconsTest(unittest.TestCase):
    def test_header_icons_fall_back_to_ascii_on_windows(self):
        source = MANAGER.read_text(encoding="utf-8", errors="replace")
        self.assertIn("_mp_console_icons", source)
        for tag in ("[PC]", "[CLOUD]", "[CPU]", "[RAM]", "[DISK]"):
            self.assertIn(tag, source)
        # The fallback must run before the header is fitted to the panel.
        fit = source.index("_fit_block_to_panel _RENDERED_MAIN_HEADER")
        call = source.index('hdr="$(_mp_console_icons "$hdr")"')
        self.assertLess(call, fit)


if __name__ == "__main__":
    unittest.main()
