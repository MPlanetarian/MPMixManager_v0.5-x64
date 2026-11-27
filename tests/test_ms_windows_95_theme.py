#!/usr/bin/env python3
"""MS Windows 95 GUI (Nostalgia Theme): palette, btop theme, manager and player.

The theme uses the Desktop Window Manager's default Win95 colours: the silver
Window face as the pane, black WindowText ink, the navy Active Title Bar for
the selection, and the teal desktop as the secondary hue.
"""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

MGR_SH = ROOT / "Mix_Archive_Manager.sh"
THEME_FILE = ROOT / "themes" / "MS_Windows_95_GUI_MP_Mix_Manager.theme"
LABEL = "MS Windows 95 GUI (Nostalgia Theme)"

NAVY = (0, 0, 128)
TEAL = (0, 128, 128)
SILVER = (192, 192, 192)

ALIASES = (
    "ms_windows_95_gui",
    "ms_windows_95",
    "ms windows 95 gui",
    "ms windows 95 gui (nostalgia theme)",
    "ms windows 95",
    "ms-windows-95",
    "windows_95",
    "windows 95",
    "windows95",
    "win95",
    "win_95",
    "nostalgia",
    "nostalgia theme",
)


def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, cwd=str(ROOT)
    )


class TestWindows95Theme(unittest.TestCase):
    def test_player_theme_list(self):
        import MP_Audio_Player as player

        eq = player.EqualizerVisualizer()
        self.assertIn("ms_windows_95_gui", eq.THEMES)
        eq.set_theme("ms_windows_95_gui")
        self.assertEqual(eq.get_theme_display_name(), LABEL)

    def test_shared_palette_is_the_win95_desktop_defaults(self):
        from mp_theme_palette import (
            THEME_BACKGROUNDS,
            THEME_COLORS,
            THEME_LABELS,
            normalize_theme_name,
        )

        self.assertEqual(THEME_COLORS["ms_windows_95_gui"], (NAVY, TEAL, SILVER))
        # The console pane is the silver Window face; black WindowText reads
        # on it at well over the WCAG AA floor.
        self.assertEqual(THEME_BACKGROUNDS["ms_windows_95_gui"], SILVER)
        self.assertEqual(THEME_LABELS["ms_windows_95_gui"], LABEL)
        for alias in ALIASES:
            self.assertEqual(
                normalize_theme_name(alias), "ms_windows_95_gui", alias
            )

    def test_glass_body_ink_is_window_text_black(self):
        from liquid_glass_opengl import theme_text_rgb

        self.assertEqual(theme_text_rgb("ms_windows_95_gui"), (0, 0, 0))
        self.assertEqual(theme_text_rgb("windows 95"), (0, 0, 0))

    def test_manager_publishes_palette_background_and_bar(self):
        script = f"""
        set -e
        eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
        set_theme_colors ms_windows_95_gui
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
        self.assertEqual(theme, "ms_windows_95_gui")
        self.assertEqual(num, "0;0;128")        # Active Title Bar navy numbers
        self.assertEqual(bg, "192;192;192")     # silver Window face
        self.assertEqual(bar, "0;0;128")        # navy highlight bar
        self.assertEqual(ink, "0;0;0")          # black WindowText

    def test_menu_ink_is_black_and_readable_on_the_silver_face(self):
        """The rows are black WindowText, not a hue that washes out."""
        script = f"""
        set -e
        eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
        eval "$(sed -n '/^_fit_menu_extra() {{/,/^}}/p' "{MGR_SH}")"
        eval "$(sed -n '/^_menu_line_plain() {{/,/^}}/p' "{MGR_SH}")"
        set_theme_colors ms_windows_95_gui
        declare -a opts=('7|Convert to FLAC|24-bit / 96 kHz')
        declare -n _opts_ref=opts
        box_w=74
        line=""
        _menu_line_plain 0 line
        printf '%s\\n' "$line"
        """
        res = _bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        line = res.stdout.strip()
        self.assertIn("38;2;0;0;0", line)
        self.assertLess(line.find("38;2;0;0;0"), line.find("Convert to FLAC"))
        for marker in ("48;2;", "48;5;"):
            self.assertNotIn(marker, line, marker)

    def test_manager_picker_offers_windows_95(self):
        text = MGR_SH.read_text(encoding="utf-8")
        self.assertIn("23|MS Windows 95 GUI (Nostalgia Theme)|", text)
        self.assertIn('save_theme "ms_windows_95_gui"', text)
        self.assertIn("ms_windows_95_gui)              m23=", text)

    def test_cycling_visits_windows_95(self):
        text = MGR_SH.read_text(encoding="utf-8")
        start = text.index("cycle_menu_theme() {")
        fn = text[start:text.index("_sync_btop_theme()", start)]
        script = fn + """
        save_theme() { printf '%s\\n' "$1"; }
        CURRENT_THEME=blood_moon
        cycle_menu_theme
        CURRENT_THEME=ms_windows_95_gui
        cycle_menu_theme
        """
        res = _bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.splitlines(), ["ms_windows_95_gui", "stream_of_frequency"])

    def test_btop_palette_ships_with_the_theme(self):
        self.assertTrue(THEME_FILE.is_file())
        text = THEME_FILE.read_text(encoding="utf-8")
        self.assertIn('theme[main_bg]="#c0c0c0"', text)
        self.assertIn('theme[main_fg]="#000000"', text)
        self.assertIn('theme[title]="#000080"', text)
        self.assertIn('theme[selected_bg]="#000080"', text)
        self.assertIn('theme[selected_fg]="#ffffff"', text)

    def test_btop_sync_maps_the_theme_id(self):
        text = MGR_SH.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"ms_windows_95_gui\)\s+tn=\"MS_Windows_95_GUI_MP_Mix_Manager\"",
        )

    def test_web_companion_registration(self):
        import mix_web_companion as mwc

        self.assertIn("ms_windows_95_gui", mwc.THEMES)
        palette = mwc.THEMES["ms_windows_95_gui"]
        self.assertEqual(palette["name"], LABEL)
        self.assertEqual(palette["bg"], "#008080")
        self.assertEqual(palette["accent_primary"], "#000080")
        self.assertEqual(mwc.THEME_ALIASES.get("win95"), "ms_windows_95_gui")
        self.assertEqual(mwc.THEME_ALIASES.get("windows 95"), "ms_windows_95_gui")

    def test_dj_monitor_registration(self):
        import dj_monitor as tm

        self.assertIn("ms_windows_95_gui", tm.THEMES)
        row = tm.THEMES["ms_windows_95_gui"]
        self.assertEqual(row[0], LABEL)
        self.assertEqual(row[2], "#000080")
        self.assertEqual(tm.resolve_theme_id("win95"), "ms_windows_95_gui")
        self.assertEqual(tm.resolve_theme_id("nostalgia"), "ms_windows_95_gui")

    def test_tracklist_viewer_lists_the_theme(self):
        text = (ROOT / "scripts" / "view_tracklist_console.sh").read_text(encoding="utf-8")
        self.assertIn('"ms_windows_95_gui"', text)
        self.assertIn(LABEL, text)


if __name__ == "__main__":
    unittest.main()
