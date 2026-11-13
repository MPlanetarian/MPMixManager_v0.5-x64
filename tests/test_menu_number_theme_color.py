#!/usr/bin/env python3
"""Menu item numbers and their brackets follow the active theme accent."""

import subprocess
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MGR_SH = BASE_DIR / "Mix_Archive_Manager.sh"

# One truecolor hue per theme. These replace the shared light-blue menu-number cyan.
EXPECTED = {
    "stream_of_frequency": "255;237;20",
    "dreamworlds": "46;107;255",
    "dreamworlds_ultra": "165;20;255",
    "dynamic_rainbow_liquid_glass": "255;20;237",
    "cosmic_supernova": "255;129;20",
    "dreamworlds_liquid": "20;255;147",
    "dreamworlds_neon": "219;20;255",
    "retro_studio": "180;140;20",
    "cyber_neon": "255;20;20",
    "phosphor": "165;255;20",
    "ice_cold": "20;147;255",
    "retro_terminal": "255;255;255",
    "cosmic_dreamworlds_radioactive": "111;255;20",
    "cosmic_dreamworlds": "255;20;183",
    "orbital_void_reflux": "56;20;255",
    "sensorium_interface": "111;20;255",
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
    def test_bracketed_numbers_use_each_theme_accent(self):
        themes = " ".join(EXPECTED)
        script = f"""
        set -e
        eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
        eval "$(sed -n '/^_fit_menu_extra() {{/,/^}}/p' "{MGR_SH}")"
        eval "$(sed -n '/^_menu_line_plain() {{/,/^}}/p' "{MGR_SH}")"
        declare -a opts=('4|Play Mix|')
        declare -n _opts_ref=opts
        for theme in {themes}; do
            set_theme_colors "$theme"
            line=""
            _menu_line_plain 0 line
            printf '%s\\t%s\\n' "$theme" "$line"
        done
        """
        res = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        seen = {}
        for raw in res.stdout.splitlines():
            theme, line = raw.split("\t", 1)
            seen[theme] = line
        self.assertEqual(set(seen), set(EXPECTED))
        accents = []
        for theme, accent in EXPECTED.items():
            line = seen[theme]
            self.assertIn(accent, line, theme)
            self.assertIn("[ 4]", line, theme)
            # The colour applies to the brackets and the digits together.
            self.assertLess(line.find(accent), line.find("[ 4]"), theme)
            if theme != "dreamworlds":
                for blue in LIGHT_BLUE:
                    self.assertNotIn(blue, line, f"{theme} still uses {blue}")
            accents.append(accent)
        self.assertEqual(len(accents), len(set(accents)))
        # Ice Cold is the only light-blue menu-number hue.
        blues = [name for name, accent in EXPECTED.items() if _is_light_blue(accent)]
        self.assertEqual(blues, ["ice_cold"])

    def test_selection_bar_uses_the_players_navigation_hue(self):
        """The manager's selected row must be the player's playlist bar colour.

        MP Liquid Player fills the selected playlist row from
        _THEME_PALETTES[theme]["highlight"]. MP Gui walks the same theme list on
        C, so its navigation bar has to publish the identical RGB or the two
        windows look different on every theme.
        """
        import MP_Audio_Player as player

        themes = " ".join(EXPECTED)
        script = f"""
        set -e
        eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
        eval "$(sed -n '/^highlight_menu_line() {{/,/^}}/p' "{MGR_SH}")"
        for theme in {themes}; do
            set_theme_colors "$theme"
            line=""
            highlight_menu_line "0" "Play Mix" "" 0 line 74 100
            printf '%s\\t%s\\t%s\\t%s\\t%s\\n' "$theme" "$SEL_BAR_R" "$SEL_BAR_G" "$SEL_BAR_B" "$line"
        done
        """
        res = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        bars = []
        for raw in res.stdout.splitlines():
            theme, r, g, b = raw.split("\t")[:4]
            line = raw.split("\t", 4)[4]
            # The bar hue is the one the player paints behind its selection.
            player_theme = player._THEME_ALIASES.get(theme, theme)
            expected_bg, expected_fg = player._selection_bar_ansi(player_theme)
            self.assertEqual((int(r), int(g), int(b)), player._rgb_from_ansi(expected_bg), theme)
            # highlight_menu_line returns printf-ready text, so ESC is spelled \\033.
            expected_esc = expected_bg.replace("\x1b", "\\033")
            self.assertIn(expected_esc, line, f"{theme} bar does not match the player")
            # Legible text on that fill, using the same rule as the player.
            fg_colour = expected_fg.split("[1;", 1)[-1]
            self.assertIn(fg_colour, line, theme)
            # The old 1;37;40m foreground carried its own black background.
            self.assertNotIn("1;37;40m", line, theme)
            self.assertNotIn("48;5;", line, theme)
            bars.append((player_theme, (r, g, b)))
        # Themes that resolve to the same player palette share one bar colour;
        # distinct themes must not collapse onto one.
        by_theme = {}
        for player_theme, rgb in bars:
            by_theme.setdefault(player_theme, set()).add(rgb)
        for player_theme, rgbs in by_theme.items():
            self.assertEqual(len(rgbs), 1, player_theme)


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
