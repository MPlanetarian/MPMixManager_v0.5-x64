#!/usr/bin/env python3
"""A standalone MP Audio Player paints its own theme background.

The manager writes OSC 11 for its window, but the player used to keep the
terminal profile's background (usually black). Dreamworlds Ultra therefore
looked black even after its palette was changed. The player now emits the same
OSC 11 the manager does, from the shared theme-background table.
"""

import io
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

MGR_SH = ROOT / "Mix_Archive_Manager.sh"


class FakeTty(io.StringIO):
    def isatty(self):
        return True


class TestPlayerThemeBackground(unittest.TestCase):
    def test_background_table_matches_the_manager(self):
        import mp_theme_palette as palette

        themes = " ".join(palette.THEME_BACKGROUNDS)
        script = f"""
        set -e
        eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
        for theme in {themes}; do
            set_theme_colors "$theme"
            printf '%s\\t%s;%s;%s\\n' "$theme" "$THEME_BG_R" "$THEME_BG_G" "$THEME_BG_B"
        done
        """
        res = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        seen = {}
        for raw in res.stdout.splitlines():
            theme, rgb = raw.split("\t")
            seen[theme] = tuple(int(v) for v in rgb.split(";"))
        self.assertEqual(set(seen), set(palette.THEME_BACKGROUNDS))
        for theme, expected in palette.THEME_BACKGROUNDS.items():
            self.assertEqual(seen[theme], expected, theme)

    def test_player_emits_osc11_for_dreamworlds_ultra(self):
        import MP_Audio_Player as player

        buf = FakeTty()
        with patch.object(sys, "stdout", buf), patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MP_LIQUID_GLASS", None)
            os.environ.pop("MP_GUI_SESSION", None)
            player._LAST_TERMINAL_BG = None
            player.apply_terminal_theme_background("dreamworlds_ultra", force=True)
        out = buf.getvalue()
        self.assertIn("\033]11;#554a6b\007", out)
        self.assertIn("\033]11;#554a6b\033\\", out)

    def test_glass_hosts_stay_transparent(self):
        import MP_Audio_Player as player

        buf = FakeTty()
        with patch.object(sys, "stdout", buf), patch.dict(
            os.environ, {"MP_LIQUID_GLASS": "1"}, clear=False
        ):
            player._LAST_TERMINAL_BG = None
            player.apply_terminal_theme_background("dreamworlds_ultra", force=True)
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
