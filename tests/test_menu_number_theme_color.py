#!/usr/bin/env python3
"""Menu item numbers and their brackets follow the active theme accent.

The selected row itself is painted from the 0.4 four-colour navigation
rotation (see tests/test_nav_bar_same_in_both_windows.py). These tests cover
the other half of the menu styling: the ``[ n ]`` key beside every label keeps
one theme hue, and that hue is not the shared light-blue used before 0.4.
"""

import subprocess
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MGR_SH = BASE_DIR / "Mix_Archive_Manager.sh"

# One truecolor hue per theme. These replace the shared light-blue menu-number cyan.
EXPECTED = {
    "stream_of_frequency": "255;237;20",
    "dreamworlds": "255;20;129",
    "dreamworlds_ultra": "216;180;254",
    "dynamic_rainbow_liquid_glass": "255;20;237",
    "cosmic_supernova": "255;129;20",
    "dreamworlds_liquid": "20;255;147",
    "dreamworlds_neon": "219;20;255",
    "retro_studio": "180;140;20",
    "cyber_neon": "255;20;20",
    "phosphor": "165;255;20",
    "ice_cold": "20;147;255",
    "retro_terminal": "255;255;255",
    "sensorium": "92;36;0",
    "retro_linux_memories": "255;183;20",
    "cyberpunk": "200;0;180",
    "dracula": "255;20;75",
    "nord": "70;140;90",
    "matrix": "20;255;38",
    "solarized": "219;255;20",
    "tokyo": "255;75;20",
    "monokai": "56;255;20",
    "gruvbox": "180;70;20",
    "emerald": "20;255;93",
    "classic": "230;200;80",
}

LIGHT_BLUE = (
    "38;2;0;193;228",
    "38;2;0;242;255",
    "38;2;0;245;255",
    "38;2;0;245;212",
    "38;2;0;240;255",
    "38;2;76;201;240",
    "38;2;91;134;187",
    "38;2;153;153;255",
    "38;5;117",
    "38;5;51",
)


class TestMenuNumberThemeColor(unittest.TestCase):
    def test_menu_numbers_use_each_themes_accent(self):
        """Every theme publishes its own MENU_NUM hue, not the old light blue."""
        themes = " ".join(EXPECTED)
        script = f"""
        set -e
        eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
        for theme in {themes}; do
            set_theme_colors "$theme"
            printf '%s\\t%s;%s;%s\\n' "$theme" "$MENU_NUM_R" "$MENU_NUM_G" "$MENU_NUM_B"
        done
        """
        res = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        seen = {}
        for raw in res.stdout.splitlines():
            theme, rgb = raw.split("\t")
            seen[theme] = rgb
        self.assertEqual(set(seen), set(EXPECTED))
        for theme, rgb in EXPECTED.items():
            self.assertEqual(seen[theme], rgb, theme)
            accent = f"38;2;{rgb}"
            self.assertNotIn(accent, LIGHT_BLUE, f"{theme} still uses the old light blue")

    def test_selected_row_steps_the_navigation_rotation(self):
        """The bar is the 0.4 palette, independent of the menu-number accent."""
        script = f"""
        set -e
        eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
        eval "$(sed -n '/^highlight_menu_line() {{/,/^}}/p' "{MGR_SH}")"
        set_theme_colors cyberpunk
        for idx in 0 1 2 3; do
            line=""
            highlight_menu_line "0" "Play Mix" "" "$idx" line 74 100
            printf '%s\\n' "$line"
        done
        """
        res = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        for expected in ("247;37;133", "0;229;255", "255;140;0", "88;98;118"):
            self.assertIn(f"48;2;{expected}", res.stdout, expected)
        # The retired single-fill behaviour must not come back.
        self.assertNotIn("48;2;76;201;240", res.stdout)


def _is_light_blue(accent: str) -> bool:
    parts = [int(p) for p in accent.split(";")]
    if len(parts) != 3:
        return False
    r, g, b = [c / 255 for c in parts]
    mx, mn = max(r, g, b), min(r, g, b)
    if mx == 0 or mx - mn < 0.35 or mx < 0.45:
        return False
    d = mx - mn
    if mx == b and b >= r and b >= g:
        hue = (60 * ((r - g) / d) + 240) % 360
        return 175 <= hue <= 215
    return False


if __name__ == "__main__":
    unittest.main()
