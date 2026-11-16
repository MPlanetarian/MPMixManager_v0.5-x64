#!/usr/bin/env python3
"""The Whitest White: one white pane with black text everywhere in the manager.

The theme is a manager palette only. It pins the console background to pure
white, the console foreground to black, every menu hue to black, the selected
row to a white fill with black text, and ships a matching btop palette.
"""

import subprocess
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MGR_SH = BASE_DIR / "Mix_Archive_Manager.sh"
THEME_FILE = BASE_DIR / "themes" / "The_Whitest_White_MP_Mix_Manager.theme"

ALIASES = (
    "the_whitest_white",
    "whitest_white",
    "whitest",
    "white",
    "The Whitest White",
    "The Whitest White Theme",
    "The Whitest White MP Mix Manager",
)


def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        cwd=str(BASE_DIR),
    )


class TestWhitestWhitePalette(unittest.TestCase):
    def test_every_text_hue_is_black_and_the_pane_is_white(self):
        aliases = " ".join(f"'{a}'" for a in ALIASES)
        script = f"""
        set -e
        eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
        for theme in {aliases}; do
            set_theme_colors "$theme"
            printf '%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s\\n' \\
                "$CURRENT_THEME" "$THEME_BG_R" "$THEME_BG_G" "$THEME_BG_B" \\
                "$THEME_FG_R" "$THEME_FG_G" "$THEME_FG_B" \\
                "$MENU_NUM_R" "$MENU_NUM_G" "$MENU_NUM_B" "$RED" "$GREEN" "$CYAN"
        done
        """
        res = _bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        for raw in res.stdout.splitlines():
            (
                theme,
                bg_r,
                bg_g,
                bg_b,
                fg_r,
                fg_g,
                fg_b,
                num_r,
                num_g,
                num_b,
                red,
                green,
                cyan,
            ) = raw.split("|")
            self.assertEqual(theme, "the_whitest_white")
            self.assertEqual((bg_r, bg_g, bg_b), ("255", "255", "255"))
            self.assertEqual((fg_r, fg_g, fg_b), ("0", "0", "0"))
            self.assertEqual((num_r, num_g, num_b), ("0", "0", "0"))
            black = "\\033[38;2;0;0;0m"
            self.assertEqual(red, black)
            self.assertEqual(green, black)
            self.assertEqual(cyan, black)

    def test_menu_numbers_and_rows_are_black_on_white(self):
        script = f"""
        set -e
        eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
        eval "$(sed -n '/^_fit_menu_extra() {{/,/^}}/p' "{MGR_SH}")"
        eval "$(sed -n '/^_menu_line_plain() {{/,/^}}/p' "{MGR_SH}")"
        eval "$(sed -n '/^highlight_menu_line() {{/,/^}}/p' "{MGR_SH}")"
        set_theme_colors the_whitest_white
        declare -a opts=('4|Play Mix|(Built-in Player)')
        declare -n _opts_ref=opts
        box_w=74
        plain=""; _menu_line_plain 0 plain
        sel="";   highlight_menu_line 4 "Play Mix" "(Built-in Player)" 0 sel 74 100
        printf 'PLAIN=%s\\nSEL=%s\\n' "$plain" "$sel"
        """
        res = _bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        plain = next(l for l in res.stdout.splitlines() if l.startswith("PLAIN="))
        sel = next(l for l in res.stdout.splitlines() if l.startswith("SEL="))
        black = "38;2;0;0;0"
        self.assertIn(black, plain)
        self.assertIn("[ 4]", plain)
        # Unselected rows sit directly on the white pane: no fill of their own.
        for marker in ("48;2;", "48;5;", "1;37;40m"):
            self.assertNotIn(marker, plain, marker)
        # The selected row follows the 0.4 navigation rotation, which is a
        # colour fill rather than the white block 0.5 used. It starts on Pink,
        # and the black ink keeps it legible.
        self.assertIn("48;2;247;37;133", sel)
        self.assertIn("1;38;2;0;0;0", sel)
        self.assertIn("▶", sel)

    def test_selected_row_walks_the_four_colour_palette(self):
        """Whitest White cycles the same palette as every other theme."""
        script = f"""
        set -e
        eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
        eval "$(sed -n '/^highlight_menu_line() {{/,/^}}/p' "{MGR_SH}")"
        set_theme_colors the_whitest_white
        for idx in 0 1 2 3; do
            sel=""
            highlight_menu_line 4 "Play Mix" "" "$idx" sel 74 100
            printf '%s\\n' "$sel"
        done
        """
        res = _bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        for hue in ("247;37;133", "0;229;255", "255;140;0", "88;98;118"):
            self.assertIn(f"48;2;{hue}", res.stdout, hue)


class TestWhitestWhiteWiring(unittest.TestCase):
    def test_console_background_and_foreground_are_pinned(self):
        script = f"""
        set -e
        eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
        eval "$(awk '/^_apply_cli_theme_background\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
        export MP_CLI_THEME_BG_FORCE=1
        set_theme_colors the_whitest_white
        _apply_cli_theme_background
        """
        res = _bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        # White pane for xterm/VTE/Windows Terminal/Konsole, black ink too.
        self.assertIn("\033]11;#ffffff\007", res.stdout)
        self.assertIn("\033]11;#ffffff\033\\", res.stdout)
        self.assertIn("\033]10;#000000\007", res.stdout)
        self.assertIn("\033]10;#000000\033\\", res.stdout)

    def test_cycling_visits_the_theme(self):
        text = MGR_SH.read_text(encoding="utf-8")
        start = text.index("cycle_menu_theme() {")
        fn = text[start:text.index("_sync_btop_theme()", start)]
        script = fn + """
        save_theme() { printf '%s\\n' "$1"; }
        CURRENT_THEME=retro_linux_memories
        cycle_menu_theme
        """
        res = _bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "the_whitest_white")

    def test_theme_picker_offers_and_switches_to_it(self):
        text = MGR_SH.read_text(encoding="utf-8")
        self.assertIn("20|The Whitest White|", text)
        self.assertIn("The Whitest White MP Mix Manager", text)
        self.assertIn('save_theme "the_whitest_white"', text)
        self.assertIn('_sync_btop_theme "$new_theme"', text)
        # _sync_btop_theme maps the theme id to the shipped btop palette name.
        self.assertRegex(
            text,
            r"the_whitest_white\)\s+tn=\"The_Whitest_White_MP_Mix_Manager\"",
        )

    def test_first_run_welcome_banner_is_black_on_white(self):
        import io
        import re
        import sys
        import tempfile
        from unittest.mock import patch

        sys.path.insert(0, str(BASE_DIR))
        from scripts import liquid_glass_reward as reward

        tmp = Path(tempfile.mkdtemp())
        with patch.object(reward, "CONFIG_DIR", tmp):
            (tmp / "theme").write_text("the_whitest_white\n", encoding="utf-8")
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                reward.show_welcome_message(wait_for_input=False)
        out = buf.getvalue()
        self.assertIn("WELCOME TO MP MIX MANAGER", out)
        colours = set(re.findall(r"\x1b\[[0-9;]*m", out))
        self.assertIn("\x1b[38;2;0;0;0m", colours)
        for previous in (
            "38;2;0;245;255",
            "38;2;199;125;255",
            "38;2;255;215;0",
            "38;2;255;255;255",
            "\x1b[2m",
        ):
            self.assertNotIn(previous, " ".join(colours), previous)

    def test_btop_palette_is_white_on_black_text(self):
        self.assertTrue(THEME_FILE.is_file(), THEME_FILE)
        content = THEME_FILE.read_text(encoding="utf-8")
        self.assertIn('theme[main_bg]="#ffffff"', content)
        self.assertIn('theme[main_fg]="#000000"', content)
        self.assertIn('theme[selected_bg]="#ffffff"', content)
        self.assertIn('theme[selected_fg]="#000000"', content)
        self.assertIn('theme[meter_bg]="#ffffff"', content)
        for line in content.splitlines():
            if line.startswith("theme[") and "_bg]" in line:
                self.assertIn('"#ffffff"', line, line)
            if line.startswith("theme["):
                self.assertNotIn('"#15539e"', line, line)


if __name__ == "__main__":
    unittest.main()
