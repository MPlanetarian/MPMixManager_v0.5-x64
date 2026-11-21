#!/usr/bin/env python3
"""C must cycle the theme even after a digit was typed into the menu.

Typing an option key fills the menu's type-to-select buffer. The C handler
used to require that buffer to be empty, so after any stray digit the theme
key was appended to the buffer ("1c") and nothing changed.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANAGER = ROOT / "Mix_Archive_Manager.sh"


class MenuThemeKeyTest(unittest.TestCase):
    def test_c_cycles_theme_even_with_a_stale_type_buffer(self):
        source = MANAGER.read_text(encoding="utf-8", errors="replace")
        # The C branch must not be gated on an empty type-to-select buffer...
        self.assertNotIn(
            'if [ -z "$typed_buffer" ] && { [ "$ch" = "c" ]',
            source,
        )
        self.assertIn('if [ "$ch" = "c" ] || [ "$ch" = "C" ]; then', source)
        # ...and it must clear the buffer so the next key is a shortcut too.
        self.assertIn('cycle_menu_theme; typed_buffer="";', source)


if __name__ == "__main__":
    unittest.main()
