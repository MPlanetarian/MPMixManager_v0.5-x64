#!/usr/bin/env python3
"""Sensorium theme: the three Liquid Glass skins are gone, Sensorium is in.

The cover-wrapped Liquid Glass Skin themes (Cosmic Dreamworlds Radioactive,
Cosmic Dreamworlds and Orbital Void Reflux) were retired. Sensorium replaces
the old Sensorium Interface theme with a bright tangerine/chartreuse/azure
palette on a light coral console pane.
"""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
MGR_SH = ROOT / "Mix_Archive_Manager.sh"
THEME_FILE = ROOT / "themes" / "Sensorium_MP_Mix_Manager.theme"

RETIRED = (
    "cosmic_dreamworlds_radioactive",
    "cosmic_dreamworlds",
    "orbital_void_reflux",
    "sensorium_interface",
)


def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, cwd=str(ROOT)
    )


class TestSensoriumTheme(unittest.TestCase):
    def test_player_theme_list(self):
        import MP_Audio_Player as player

        eq = player.EqualizerVisualizer()
        for retired in RETIRED:
            self.assertNotIn(retired, eq.THEMES)
        self.assertIn("sensorium", eq.THEMES)
        # Sensorium sits where the old Sensorium Interface theme was: directly
        # after Retro Terminal, before Retro Linux Memories.
        self.assertEqual(eq.THEMES.index("sensorium"), eq.THEMES.index("retro_terminal") + 1)
        eq.set_theme("sensorium")
        self.assertEqual(eq.get_theme_display_name(), "Sensorium")

    def test_shared_palette_is_bright_and_unique(self):
        from mp_theme_palette import (
            THEME_BACKGROUNDS,
            THEME_COLORS,
            THEME_LABELS,
            normalize_theme_name,
        )

        self.assertEqual(THEME_COLORS["sensorium"], ((255, 122, 24), (200, 255, 61), (58, 134, 255)))
        self.assertEqual(THEME_BACKGROUNDS["sensorium"], (240, 128, 128))
        self.assertEqual(THEME_LABELS["sensorium"], "Sensorium")
        for retired in RETIRED:
            self.assertNotIn(retired, THEME_COLORS)
        # Old configs and CLI spellings migrate onto the new theme.
        for alias in ("sensorium", "sensorium_interface", "Sensorium Interface"):
            self.assertEqual(normalize_theme_name(alias), "sensorium")

    def test_glass_body_ink(self):
        from liquid_glass_opengl import theme_text_rgb

        self.assertEqual(theme_text_rgb("sensorium"), (43, 10, 16))
        self.assertEqual(theme_text_rgb("sensorium_interface"), (43, 10, 16))

    def test_light_console_pane_uses_dark_body_ink(self):
        import MP_Audio_Player as player

        self.assertTrue(player.theme_has_light_console("sensorium"))
        self.assertTrue(player.theme_has_light_console("the_whitest_white"))
        self.assertFalse(player.theme_has_light_console("dreamworlds_ultra"))
        self.assertFalse(player.theme_has_light_console("retro_linux_memories"))

    def test_manager_publishes_palette_background_and_bar(self):
        script = f"""
        set -e
        eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
        set_theme_colors sensorium
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
        self.assertEqual(theme, "sensorium")
        self.assertEqual(num, "92;36;0")      # dark tangerine menu number
        self.assertEqual(bg, "240;128;128")   # light coral pane
        self.assertEqual(bar, "200;255;61")   # chartreuse selection bar
        self.assertEqual(ink, "43;10;16")     # deep maroon menu ink

    def test_manager_picker_offers_sensorium_without_the_skins(self):
        text = MGR_SH.read_text(encoding="utf-8")
        self.assertIn("18|Sensorium|", text)
        self.assertNotIn("Liquid Glass Skin", text)
        self.assertNotIn("Cosmic Dreamworlds Radioactive", text)

    def test_btop_palette_ships_with_the_theme(self):
        self.assertTrue(THEME_FILE.is_file())
        text = THEME_FILE.read_text(encoding="utf-8")
        self.assertIn('theme[main_bg]="#f08080"', text)
        self.assertIn('theme[title]="#5c2400"', text)
        self.assertIn('theme[hi_fg]="#263d00"', text)


if __name__ == "__main__":
    unittest.main()
