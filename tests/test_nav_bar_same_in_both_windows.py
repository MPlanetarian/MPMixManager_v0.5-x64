#!/usr/bin/env python3
"""The same theme must paint the same bar on every row and in both windows."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MGR_SH = BASE_DIR / "Mix_Archive_Manager.sh"
sys.path.insert(0, str(BASE_DIR))

import MP_Audio_Player as player


def _bash(script: str) -> subprocess.CompletedProcess:
    home = tempfile.mkdtemp()
    env = dict(os.environ)
    env["HOME"] = home
    try:
        return subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True,
            cwd=str(BASE_DIR), env=env,
        )
    finally:
        import shutil

        shutil.rmtree(home, ignore_errors=True)


class TestBarIsStableWithinOneTheme(unittest.TestCase):
    def test_every_row_of_a_menu_gets_the_same_fill(self):
        """One theme, one bar colour — the row index must not shade it."""
        themes = list(player.EqualizerVisualizer.THEMES)
        script = """
        set -e
        eval "$(awk '/^set_theme_colors\\(\\)/{f=1} f{print} f&&/^}/{exit}' Mix_Archive_Manager.sh)"
        eval "$(sed -n '/^highlight_menu_line() {/,/^}/p' Mix_Archive_Manager.sh)"
        for theme in __THEMES__; do
            set_theme_colors "$theme"
            for idx in 0 1 2 3; do
                out=""
                highlight_menu_line "1" "Row $idx" "" "$idx" out 74 100
                fill=$(printf '%s' "$out" | sed -n 's/.*\\\\033\\[48;2;\\([0-9;]*\\)m.*/\\1/p')
                printf '%s\\t%s\\t%s\\n' "$theme" "$idx" "$fill"
            done
        done
        """.replace("__THEMES__", " ".join(themes))
        res = _bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        fills = {}
        for raw in res.stdout.splitlines():
            theme, idx, fill = raw.split("\t")
            fills.setdefault(theme, set()).add(fill)
        self.assertEqual(set(fills), set(themes))
        for theme, seen in fills.items():
            self.assertEqual(len(seen), 1, f"{theme} paints {sorted(seen)} across its rows")

    def test_bar_is_the_player_playlist_fill(self):
        theme = "cosmic_supernova"
        script = """
        set -e
        eval "$(awk '/^set_theme_colors\\(\\)/{f=1} f{print} f&&/^}/{exit}' Mix_Archive_Manager.sh)"
        eval "$(sed -n '/^highlight_menu_line() {/,/^}/p' Mix_Archive_Manager.sh)"
        set_theme_colors __THEME__
        out=""
        highlight_menu_line "1" "Row" "" 0 out 74 100
        printf '%s' "$out"
        """.replace("__THEME__", theme)
        res = _bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        expected_bg, expected_fg = player._selection_bar_ansi(theme)
        self.assertIn(expected_bg.replace("\x1b", "\\033"), res.stdout)
        self.assertIn(expected_fg.replace("\x1b", "\\033"), res.stdout)


if __name__ == "__main__":
    unittest.main()
