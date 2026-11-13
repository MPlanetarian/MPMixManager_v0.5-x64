#!/usr/bin/env python3
"""MP Gui's menu items, Selection: line and EQ line are always white.

Each of the three sits on the glass pane with no fill of its own, so a theme
tint used to decide whether the text was readable. The pane is pale for most
themes (``glass_text_rgb`` in scripts/liquid_glass_opengl.py says white
"disappears" on it), and several themes set the menu text so dark that it
vanished into the pane. All three now use plain white ink with no fill of
their own, so they read the same on every theme.
"""

import subprocess
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MGR_SH = BASE_DIR / "Mix_Archive_Manager.sh"

WHITE = "38;2;255;255;255"
# Any 256/truecolor background fill means text is still painted on a block.
BG_MARKERS = ("48;2;", "48;5;", "40m", "1;37;40m")

THEMES = (
    "stream_of_frequency",
    "dreamworlds",
    "cosmic_dreamworlds",
    "dreamworlds_liquid",
    "retro_linux_memories",
    "into_darkness_void_out",
    "retro_terminal",
    "the_whitest_white",
    "gruvbox",
    "emerald",
    "classic",
    "cyberpunk",
    "matrix",
    "dracula",
    "nord",
    "tokyo",
    "monokai",
    "solarized",
)

PREAMBLE = f'''
set -e
eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
eval "$(sed -n '/^MENU_WHITE=/,/^}}/p' "{MGR_SH}" | head -12)"
'''


def run(script: str) -> str:
    res = subprocess.run(["bash", "-c", PREAMBLE + script],
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise AssertionError(res.stderr)
    return res.stdout


class TestAlwaysWhiteMenuText(unittest.TestCase):
    def test_menu_items_are_white_in_every_theme(self):
        """Unselected rows: plain white ink, no fill, no theme tint."""
        themes = " ".join(THEMES)
        script = f'''
        eval "$(sed -n '/^_fit_menu_extra() {{/,/^}}/p' "{MGR_SH}")"
        eval "$(sed -n '/^_menu_line_plain() {{/,/^}}/p' "{MGR_SH}")"
        declare -a opts=('7|Convert to FLAC|24-bit / 96 kHz')
        declare -n _opts_ref=opts
        for theme in {themes}; do
            set_theme_colors "$theme"
            box_w=74
            line=""
            _menu_line_plain 0 line
            printf '%s\\t%s\\n' "$theme" "$line"
        done
        '''
        seen = {}
        for raw in run(script).splitlines():
            theme, line = raw.split("\t", 1)
            seen[theme] = line
        self.assertEqual(set(seen), set(THEMES))
        for theme in THEMES:
            line = seen[theme]
            self.assertIn(WHITE, line, f"{theme}: menu row is not white")
            self.assertIn("Convert to FLAC", line, theme)
            # No background fill: the text sits directly on the pane.
            for marker in BG_MARKERS:
                self.assertNotIn(marker, line, f"{theme}: row still paints a background")
            # White must precede the label for the label itself to be white.
            self.assertLess(line.find(WHITE), line.find("Convert to FLAC"), theme)
            # No trailing fill either: the row ends at the label.
            self.assertNotIn("48;2;16;18;26", line, theme)

    def test_selection_line_is_white_after_the_label_in_every_theme(self):
        """Everything right of 'Selection:' is white, in all three states."""
        themes = " ".join(THEMES)
        # Each state is exercised separately: idle, with a typed buffer, and
        # while searching. The "Selection:" label itself keeps its own accent.
        script = f'''
        eval "$(sed -n '/^_compose_menu_status() {{/,/^}}/p' "{MGR_SH}")"
        declare -a opts=('7|Convert to FLAC|24-bit')
        declare -n _opts_ref=opts
        for theme in {themes}; do
            set_theme_colors "$theme"
            for state in idle typed search; do
                cur_idx=0
                menu_title=""
                _menu_exit_hint="q to exit"
                case "$state" in
                    idle)   typed_buffer=""; search_mode=0; search_query="" ;;
                    typed)  typed_buffer="7"; search_mode=0; search_query="" ;;
                    search) typed_buffer=""; search_mode=1; search_query="flac" ;;
                esac
                status_out=""
                _compose_menu_status status_out
                printf '%s\\t%s\\t%s\\n' "$theme" "$state" "$status_out"
            done
        done
        '''
        rows = [r.split("\t", 2) for r in run(script).splitlines()]
        self.assertEqual(len(rows), len(THEMES) * 3)
        for theme, state, line in rows:
            label = "Filter / Search:" if state == "search" else "Selection:"
            self.assertIn(label, line, f"{theme}/{state}")
            self.assertIn(WHITE, line, f"{theme}/{state}: no white ink")
            for marker in BG_MARKERS:
                self.assertNotIn(marker, line, f"{theme}/{state}: still paints a background")
            # The white starts at or after the label: the label keeps its hue,
            # and every character to its right is white.
            white_at = line.index(WHITE)
            label_at = line.index(label)
            self.assertLessEqual(label_at, white_at,
                                 f"{theme}/{state}: white does not follow the label")
            self.assertTrue(line.endswith("\\033[K"), theme)

    def test_eq_line_in_the_player_panel_is_white(self):
        """The EQ line below Playing is white, not the theme's cyan accent."""
        text = MGR_SH.read_text(encoding="utf-8")
        self.assertIn("${MENU_WHITE}EQ: ", text)
        # The whole EQ line is white, with no background fill behind it.
        self.assertNotIn("MENU_DARK_BG", text)
        # The EQ field must not be painted with the theme accent any more.
        self.assertNotIn('local eq_disp="${CYAN}${eq_raw}${NC}"', text)
        self.assertNotIn('${DIM}EQ:${NC} ${eq_disp}', text)


if __name__ == "__main__":
    unittest.main()
