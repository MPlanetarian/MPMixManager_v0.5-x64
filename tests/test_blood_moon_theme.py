#!/usr/bin/env python3
"""Blood Moon theme tests: Crimson Eclipse palette, btop theme, manager and player integration."""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

MGR_SH = ROOT / "Mix_Archive_Manager.sh"
THEME_FILE = ROOT / "themes" / "Blood_Moon_MP_Mix_Manager.theme"


def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, cwd=str(ROOT)
    )


class TestBloodMoonTheme(unittest.TestCase):
    def test_player_theme_list(self):
        import MP_Audio_Player as player

        eq = player.EqualizerVisualizer()
        self.assertIn("blood_moon", eq.THEMES)
        eq.set_theme("blood_moon")
        self.assertEqual(eq.get_theme_display_name(), "Blood Moon [Crimson Eclipse]")

    def test_shared_palette_is_vivid_crimson(self):
        from mp_theme_palette import (
            THEME_BACKGROUNDS,
            THEME_COLORS,
            THEME_LABELS,
            normalize_theme_name,
        )

        self.assertEqual(THEME_COLORS["blood_moon"], ((255, 32, 64), (255, 90, 95), (180, 0, 32)))
        self.assertEqual(THEME_BACKGROUNDS["blood_moon"], (204, 169, 188))
        self.assertEqual(THEME_LABELS["blood_moon"], "Blood Moon [Crimson Eclipse]")
        for alias in ("blood_moon", "blood moon", "blood-moon", "crimson", "vampire", "blood_ruby"):
            self.assertEqual(normalize_theme_name(alias), "blood_moon")

    def test_glass_body_ink(self):
        from liquid_glass_opengl import theme_text_rgb

        self.assertEqual(theme_text_rgb("blood_moon"), (142, 0, 25))
        self.assertEqual(theme_text_rgb("crimson"), (142, 0, 25))

    def test_manager_publishes_palette_background_and_bar(self):
        script = f"""
        set -e
        eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
        set_theme_colors blood_moon
        printf '%s\\t%s;%s;%s\\t%s;%s;%s\\t%s;%s;%s\\t%s;%s;%s\\n' \
            "$CURRENT_THEME" \
            "$MENU_NUM_R" "$MENU_NUM_G" "$MENU_NUM_B" \
            "$THEME_BG_R" "$THEME_BG_G" "$THEME_BG_B" \
            "$SEL_BAR_R" "$SEL_BAR_G" "$SEL_BAR_B" \
            "$MENU_INK_R" "$MENU_INK_G" "$MENU_INK_B"
        """
        res = _bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        theme, num, bg, bar, ink = res.stdout.strip().split("\t")
        self.assertEqual(theme, "blood_moon")
        self.assertEqual(num, "137;17;34")    # dark blood-crimson menu number
        self.assertEqual(bg, "204;169;188")   # subtle crimson wine tint field
        self.assertEqual(bar, "255;32;64")    # vivid blood crimson selection bar
        self.assertEqual(ink, "142;0;25")     # deep ruby red menu ink

    def test_manager_picker_offers_blood_moon(self):
        text = MGR_SH.read_text(encoding="utf-8")
        self.assertIn("21|Blood Moon [Crimson Eclipse]|", text)
        self.assertIn('save_theme "blood_moon"', text)

    def test_cycling_visits_blood_moon(self):
        text = MGR_SH.read_text(encoding="utf-8")
        start = text.index("cycle_menu_theme() {")
        fn = text[start:text.index("_sync_btop_theme()", start)]
        script = fn + """
        save_theme() { printf '%s\\n' "$1"; }
        CURRENT_THEME=into_darkness_void_out
        cycle_menu_theme
        """
        res = _bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "blood_moon")

    def test_btop_palette_ships_with_the_theme(self):
        self.assertTrue(THEME_FILE.is_file())
        text = THEME_FILE.read_text(encoding="utf-8")
        self.assertIn('theme[main_bg]="#220d13"', text)
        self.assertIn('theme[title]="#ff2040"', text)
        self.assertIn('theme[hi_fg]="#ff5a5f"', text)
        self.assertIn('theme[selected_bg]="#b40020"', text)


if __name__ == "__main__":
    unittest.main()
