#!/usr/bin/env python3
"""MP Gui menu text matches MP Liquid Player after C changes the theme.

The player paints unselected playlist rows white on the glass, and black on
The Whitest White. The highlighted row's letters are black or white from the
theme bar's luminance. MP Gui used a per-theme hue for ordinary rows and a
row-index colour for the highlight, so several themes disagreed.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MGR_SH = BASE_DIR / "Mix_Archive_Manager.sh"
sys.path.insert(0, str(BASE_DIR))

import MP_Audio_Player as player  # noqa: E402


def _playlist_ink(theme: str) -> tuple:
    """The ink TerminalPlayerUI uses for an unselected row inside Liquid Glass."""
    if theme == "the_whitest_white":
        seq = player.theme_body_ansi(theme)
        return player._rgb_from_ansi(seq)
    return (255, 255, 255)


def _bash(script: str) -> str:
    env = dict(os.environ)
    env["MP_LIQUID_GLASS"] = "1"
    env["MP_GUI_SESSION"] = "1"
    res = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        cwd=str(BASE_DIR),
        env=env,
    )
    if res.returncode != 0:
        raise AssertionError(res.stderr or res.stdout)
    return res.stdout


PREAMBLE = f"""
set -e
eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
eval "$(sed -n '/^highlight_menu_line() {{/,/^}}/p' "{MGR_SH}")"
eval "$(sed -n '/^_fit_menu_extra() {{/,/^}}/p' "{MGR_SH}")"
eval "$(sed -n '/^_menu_line_plain() {{/,/^}}/p' "{MGR_SH}")"
"""


class TestGuiMenuNavText(unittest.TestCase):
    def test_unselected_rows_use_the_player_ink(self):
        themes = list(player.EqualizerVisualizer.THEMES)
        script = PREAMBLE + """
declare -a opts=('7|Convert to FLAC|24-bit / 96 kHz')
declare -n _opts_ref=opts
for theme in %s; do
    set_theme_colors "$theme"
    printf '%%s|%%s;%%s;%%s\\n' "$theme" "$MENU_INK_R" "$MENU_INK_G" "$MENU_INK_B"
    box_w=74
    line=""
    _menu_line_plain 0 line
    printf '%%s|%%s\\n' "$theme" "$line"
done
""" % " ".join(themes)
        seen = {}
        rows = {}
        for raw in _bash(script).splitlines():
            theme, rest = raw.split("|", 1)
            if theme not in seen:
                seen[theme] = tuple(int(v) for v in rest.split(";"))
            else:
                rows[theme] = rest
        self.assertEqual(set(seen), set(themes))
        for theme in themes:
            expected = _playlist_ink(theme)
            self.assertEqual(seen[theme], expected, theme)
            rgb = f"38;2;{expected[0]};{expected[1]};{expected[2]}"
            self.assertIn(rgb, rows[theme], theme)
            self.assertLess(rows[theme].find(rgb), rows[theme].find("Convert to FLAC"), theme)

    def test_selected_row_text_matches_the_player_bar(self):
        themes = list(player.EqualizerVisualizer.THEMES)
        script = PREAMBLE + """
for theme in %s; do
    set_theme_colors "$theme"
    line=""
    highlight_menu_line "1" "Play Mix" "" 0 line 74 100
    printf '%%s|%%s\\n' "$theme" "$line"
done
""" % " ".join(themes)
        seen = {}
        for raw in _bash(script).splitlines():
            theme, line = raw.split("|", 1)
            seen[theme] = line
        self.assertEqual(set(seen), set(themes))
        for theme in themes:
            bg, fg = player._selection_bar_ansi(theme)
            line = seen[theme]
            bg_rgb = player._rgb_from_ansi(bg)
            fg_rgb = player._rgb_from_ansi(fg)
            self.assertIsNotNone(bg_rgb, theme)
            self.assertIsNotNone(fg_rgb, theme)
            fill = "48;2;%d;%d;%d" % bg_rgb
            ink = "38;2;%d;%d;%d" % fg_rgb
            self.assertIn(fill, line, f"{theme}: bar fill differs from the player")
            # The label itself has to carry that ink, not only the pointer.
            self.assertLess(line.find(ink), line.find("Play Mix"), f"{theme}: selected text differs from the player")

    def test_cli_menu_keeps_its_theme_ink(self):
        """A plain terminal is not the glass, so its rows stay on MENU_INK."""
        script = f"""
set -e
unset MP_LIQUID_GLASS MP_GUI_SESSION
eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
set_theme_colors dreamworlds_ultra
printf '%s;%s;%s\\n' "$MENU_INK_R" "$MENU_INK_G" "$MENU_INK_B"
"""
        env = dict(os.environ)
        env.pop("MP_LIQUID_GLASS", None)
        env.pop("MP_GUI_SESSION", None)
        res = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, env=env
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "216;180;254")


if __name__ == "__main__":
    unittest.main()
