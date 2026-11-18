#!/usr/bin/env python3
"""The CLI manager paints a different console background for every theme.

Windows Terminal, Linux VTE/Konsole/xterm, and iTerm take OSC 11. Terminal.app
and the classic Windows console are handled beside that sequence. MP Gui and
the liquid-glass hosts keep their own pane, so they must not receive OSC 11.
"""

import subprocess
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MGR_SH = BASE_DIR / "Mix_Archive_Manager.sh"

THEMES = (
    "stream_of_frequency",
    "dreamworlds",
    "dreamworlds_ultra",
    "dynamic_rainbow_liquid_glass",
    "cosmic_supernova",
    "dreamworlds_liquid",
    "dreamworlds_neon",
    "retro_studio",
    "cyber_neon",
    "phosphor",
    "ice_cold",
    "retro_terminal",
    "sensorium",
    "retro_linux_memories",
    "cyberpunk",
    "dracula",
    "nord",
    "matrix",
    "solarized",
    "tokyo",
    "monokai",
    "gruvbox",
    "emerald",
    "classic",
    "into_darkness_void_out",
    "the_whitest_white",
)


def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        cwd=str(BASE_DIR),
    )


class TestCliThemeBackground(unittest.TestCase):
    def test_each_theme_has_its_own_background(self):
        themes = " ".join(THEMES)
        script = f"""
        set -e
        eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
        for theme in {themes}; do
            set_theme_colors "$theme"
            printf '%s\\t%s\\t%s\\t%s\\n' "$theme" "$THEME_BG_R" "$THEME_BG_G" "$THEME_BG_B"
        done
        """
        res = _bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        seen = {}
        for raw in res.stdout.splitlines():
            theme, r, g, b = raw.split("\t")
            seen[theme] = (int(r), int(g), int(b))
        self.assertEqual(set(seen), set(THEMES))
        # The console pane is the glass still-water field. MPlanetarian
        # Dreamworlds is the one theme painted on black, because that is the
        # field its Liquid Glass skin uses; its menu ink is lightened to match
        # (see tests/test_menu_text_theme_ink.py). Into Darkness is also the
        # void-out black. Four themes on the C cycle keep native dark terminal
        # fields, so cycling shows more than the two void-out panes.
        self.assertEqual(seen["dreamworlds"], (0, 0, 0))
        self.assertEqual(seen["dreamworlds_ultra"], (186, 180, 220))
        self.assertEqual(seen["sensorium"], (195, 196, 191))
        self.assertEqual(seen["into_darkness_void_out"], (0, 0, 0))
        self.assertEqual(seen["retro_linux_memories"], (45, 45, 45))
        self.assertEqual(seen["stream_of_frequency"], (12, 13, 23))
        self.assertEqual(seen["dynamic_rainbow_liquid_glass"], (20, 12, 32))
        self.assertEqual(seen["cosmic_supernova"], (10, 8, 20))
        self.assertEqual(seen["dreamworlds_liquid"], (6, 20, 28))
        self.assertEqual(seen["matrix"], (0, 0, 0))
        self.assertEqual(seen["nord"], (46, 52, 64))
        self.assertEqual(seen["solarized"], (140, 105, 0))
        alias = _bash(
            f"""
            set -e
            eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
            for theme in default mplanetarian mplanetarian_dreamworlds; do
                set_theme_colors "$theme"
                printf '%s\\t%s\\t%s\\t%s\\n' "$theme" "$THEME_BG_R" "$THEME_BG_G" "$THEME_BG_B"
            done
            """
        )
        self.assertEqual(alias.returncode, 0, alias.stderr)
        expected_alias = {
            "default": (186, 180, 220),
            "mplanetarian": (0, 0, 0),
            "mplanetarian_dreamworlds": (0, 0, 0),
        }
        for raw in alias.stdout.splitlines():
            theme, r, g, b = raw.split("\t")
            self.assertEqual((int(r), int(g), int(b)), expected_alias[theme], theme)
        for theme, (r, g, b) in seen.items():
            for channel in (r, g, b):
                self.assertGreaterEqual(channel, 0, theme)
                self.assertLessEqual(channel, 255, theme)
            luma = 0.299 * r + 0.587 * g + 0.114 * b
            # Dreamworlds is painted on the same void-out black as Into
            # Darkness: that is the field its Liquid Glass skin uses, and its
            # menu ink is lightened to stay readable on it. The four native
            # dark terminal palettes do the same.
            dark = {
                "into_darkness_void_out": (0, 0, 0),
                "dreamworlds": (0, 0, 0),
                "matrix": (0, 0, 0),
                "retro_linux_memories": (45, 45, 45),
                "stream_of_frequency": (12, 13, 23),
                "dynamic_rainbow_liquid_glass": (20, 12, 32),
                "cosmic_supernova": (10, 8, 20),
                "dreamworlds_liquid": (6, 20, 28),
                "nord": (46, 52, 64),
                "solarized": (140, 105, 0),
            }
            if theme in dark:
                self.assertEqual((r, g, b), dark[theme], theme)
            else:
                self.assertGreaterEqual(
                    luma, 140,
                    f"{theme} should use the pale still-water field",
                )
        # Aliases the glass collapses share one field, apart from Dreamworlds,
        # which is deliberately black while Dreamworlds Ultra keeps the pale
        # field.
        shared = {
            "dreamworlds_ultra": "dreamworlds_ultra",
            "dracula": "dreamworlds_neon",
            "gruvbox": "retro_studio",
            "cyberpunk": "cyber_neon",
            "classic": "retro_terminal",
        }
        for alias, canonical in shared.items():
            self.assertEqual(seen[alias], seen[canonical], alias)
        own = [rgb for theme, rgb in seen.items() if theme not in shared]
        # Dreamworlds, The Matrix and Into Darkness are all the black pane by
        # design, so two duplicates are expected; every other field stays
        # distinct.
        self.assertEqual(len(set(own)), len(own) - 2,
                         "only Dreamworlds, The Matrix and Into Darkness may share the black pane")

    def test_theme_switch_emits_osc11_for_windows_mac_and_linux(self):
        script = f"""
        set -e
        eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
        eval "$(awk '/^_apply_cli_theme_background\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
        export MP_CLI_THEME_BG_FORCE=1
        unset MP_LIQUID_GLASS MP_GUI_SESSION
        set_theme_colors dreamworlds_ultra
        _apply_cli_theme_background
        printf '\\n--\\n'
        set_theme_colors retro_linux_memories
        _apply_cli_theme_background
        """
        res = _bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        chunks = res.stdout.split("\n--\n")
        self.assertEqual(len(chunks), 2)
        # BEL form and ST form, so Windows Terminal, Terminal-class Linux, and Konsole all take it.
        self.assertIn("\033]11;#bab4dc\007", chunks[0])
        self.assertIn("\033]11;#bab4dc\033\\", chunks[0])
        self.assertIn("\033]11;#2d2d2d\007", chunks[1])
        self.assertIn("\033]11;#2d2d2d\033\\", chunks[1])

    def test_glass_hosts_do_not_take_a_console_background(self):
        script = f"""
        set -e
        eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
        eval "$(awk '/^_apply_cli_theme_background\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
        export MP_CLI_THEME_BG_FORCE=1
        set_theme_colors matrix
        MP_LIQUID_GLASS=1 _apply_cli_theme_background
        unset MP_LIQUID_GLASS
        _CLI_THEME_BG_HEX=""
        MP_GUI_SESSION=1 _apply_cli_theme_background
        """
        res = _bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("]11;", res.stdout)

    def test_platform_hooks_and_restore_are_wired(self):
        text = MGR_SH.read_text(encoding="utf-8")
        self.assertIn("SetConsoleScreenBufferInfoEx", text)
        self.assertIn('set background color of t to wanted', text)
        self.assertIn("_apply_cli_theme_background", text)
        self.assertIn("_restore_cli_theme_background", text)
        # Konsole's scheme reset must run before the theme pane is painted.
        load = text.split("load_theme() {", 1)[1].split("\n_ensure_konsole_default_scheme()", 1)[0]
        self.assertLess(load.find("_ensure_konsole_default_scheme"), load.find("_apply_cli_theme_background"))
        self.assertIn('_sync_btop_theme "$new_theme"; _apply_cli_theme_background', text)


if __name__ == "__main__":
    unittest.main()
