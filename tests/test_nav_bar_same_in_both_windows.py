#!/usr/bin/env python3
"""The selected row steps through the four-colour navigation palette.

MP Mix Manager 0.4 painted the highlighted menu row from a fixed rotation
(Pink, Blue, Orange, Grey) so the bar changed colour as the user moved down a
menu. 0.5 briefly replaced that with one theme-wide fill; the rotation is back,
so these tests pin the palette and the wrap-around order.
"""

import subprocess
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MGR_SH = BASE_DIR / "Mix_Archive_Manager.sh"

# The 0.4 rotation, in order.
NAV_PALETTE = [
    "247;37;133",   # Pink
    "0;229;255",    # Blue
    "255;140;0",    # Orange
    "88;98;118",    # Grey
]

# cosmic_supernova has its own four hues; unix white-on-black stays white.
SUPERNOVA_PALETTE = ["224;86;253", "0;245;212", "254;228;64", "94;96;206"]


def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, cwd=str(BASE_DIR)
    )


def _fill(out: str) -> str:
    return next(
        part
        for part in out.split("\\033[48;2;")[1:]
        for part in [part.split("m", 1)[0]]
    )


class TestNavigationPaletteRotation(unittest.TestCase):
    def test_rows_cycle_pink_blue_orange_grey(self):
        """Row index selects the colour; the 5th row wraps back to Pink."""
        script = """
        eval "$(sed -n '/^highlight_menu_line() {/,/^}/p' Mix_Archive_Manager.sh)"
        CURRENT_THEME=dreamworlds
        for idx in 0 1 2 3 4; do
            out=""
            highlight_menu_line "1" "Row" "" "$idx" out 74 100
            printf '%s\\n' "$out"
        done
        """
        res = _bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        fills = [_fill(line) for line in res.stdout.splitlines() if line.strip()]
        self.assertEqual(fills, NAV_PALETTE + [NAV_PALETTE[0]])

    def test_every_ordinary_theme_uses_the_same_rotation(self):
        themes = (
            "dreamworlds dreamworlds_ultra cyberpunk dracula nord matrix "
            "solarized tokyo monokai gruvbox emerald classic "
            "dreamworlds_liquid dynamic_rainbow_liquid_glass "
            "sensorium stream_of_frequency "
            "retro_studio cyber_neon phosphor ice_cold retro_linux_memories "
            "into_darkness_void_out dreamworlds_neon"
        )
        script = f"""
        eval "$(sed -n '/^highlight_menu_line() {{/,/^}}/p' Mix_Archive_Manager.sh)"
        for theme in {themes}; do
            CURRENT_THEME="$theme"
            for idx in 0 1 2 3; do
                out=""
                highlight_menu_line "1" "Row" "" "$idx" out 74 100
                printf '%s\\t%s\\t%s\\n' "$theme" "$idx" "$out"
            done
        done
        """
        res = _bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        seen = {}
        for raw in res.stdout.splitlines():
            theme, idx, out = raw.split("\t", 2)
            seen.setdefault(theme, {})[int(idx)] = _fill(out)
        self.assertEqual(set(seen), set(themes.split()))
        for theme, rows in seen.items():
            self.assertEqual(
                [rows[i] for i in range(4)],
                NAV_PALETTE,
                f"{theme} does not cycle Pink/Blue/Orange/Grey",
            )

    def test_cosmic_supernova_keeps_its_own_four_hues(self):
        script = """
        eval "$(sed -n '/^highlight_menu_line() {/,/^}/p' Mix_Archive_Manager.sh)"
        CURRENT_THEME=cosmic_supernova
        for idx in 0 1 2 3; do
            out=""
            highlight_menu_line "1" "Row" "" "$idx" out 74 100
            printf '%s\\n' "$out"
        done
        """
        res = _bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        fills = [_fill(line) for line in res.stdout.splitlines() if line.strip()]
        self.assertEqual(fills, SUPERNOVA_PALETTE)

    def test_unix_white_on_black_stays_white(self):
        script = """
        eval "$(sed -n '/^highlight_menu_line() {/,/^}/p' Mix_Archive_Manager.sh)"
        CURRENT_THEME=retro_terminal
        for idx in 0 1 2 3; do
            out=""
            highlight_menu_line "1" "Row" "" "$idx" out 74 100
            printf '%s\\n' "$out"
        done
        """
        res = _bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        fills = [_fill(line) for line in res.stdout.splitlines() if line.strip()]
        self.assertEqual(fills, ["255;255;255"] * 4)

    def test_pointer_is_tinted_to_match_the_row(self):
        """The ▶ ❯ pointer keeps its own brighter shade of the row colour."""
        script = """
        eval "$(sed -n '/^highlight_menu_line() {/,/^}/p' Mix_Archive_Manager.sh)"
        CURRENT_THEME=dreamworlds
        for idx in 0 1 2 3; do
            out=""
            highlight_menu_line "1" "Row" "" "$idx" out 74 100
            printf '%s\\n' "$out"
        done
        """
        res = _bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("38;2;247;37;133", res.stdout)
        self.assertIn("38;2;160;175;205", res.stdout)
        self.assertIn("\u25b6 \u276f", res.stdout)


if __name__ == "__main__":
    unittest.main()
