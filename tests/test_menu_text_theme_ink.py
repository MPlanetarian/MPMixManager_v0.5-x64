#!/usr/bin/env python3
"""Menu option text follows the selected theme's own colour.

0.4 painted every unselected menu row with the theme's menu-number hue
(``MENU_NUM``), so switching theme recoloured the options. 0.5 replaced that
with a fixed ``MENU_WHITE``, which made every theme look the same: white text
on a dark pane. This pins the restored behaviour and the one guard that came
with it.

Several rows are not the theme's own accent:

* ``into_darkness_void_out``, ``dreamworlds``, ``nord`` and ``solarized`` are
  white-on-dark themes, so their ink is legitimately white. ``matrix`` keeps
  its phosphor green on the black pane, and ``retro_terminal`` is a grey.
* Four themes (``dracula``, ``gruvbox``, ``cyberpunk`` and the others on a
  pale field) have an accent that is too dark on their own pane — as low as
  2.1:1. Those inks are mixed toward black until they reach the 4.5:1 WCAG AA
  floor. The hue is kept; only its lightness moves. ``dracula`` goes further:
  its navigation rows use the theme's soft lavender-gray (190, 181, 216)
  rather than the coral-pink accent, so the menu reads as light purple-gray on
  the ``#282A36`` pane while the ``[numbers]`` keep the pink accent.

``Dreamworlds`` moved to a black pane with white ink, ``Nord`` to its own
``#2e3440`` slate, ``Solarized`` to a deep amber so white stays readable on
the yellow, and ``Dynamic Liquid Glass`` to the light purple ``#d8b4fe``.

``THEME_BG`` is the console pane ``set_theme_colors`` paints for the same
theme, so contrast is measurable in the test rather than assumed.
"""

import subprocess
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MGR_SH = BASE_DIR / "Mix_Archive_Manager.sh"

# theme -> (menu ink R, G, B). Every value is published by set_theme_colors.
# Each hue is pulled toward black until it clears 4.5:1 on the still-water
# field, except where the theme keeps a dark pane of its own. Stream of
# Frequency, Dynamic Liquid Glass, Cosmic Supernova and Dreamworlds Liquid use
# dark native fields, so their hues are lightened instead. Dreamworlds, The
# Matrix, Nord and Solarized now sit on dark panes and use white ink; The
# Matrix keeps its phosphor green.
EXPECTED_INK = {
    "stream_of_frequency": (76, 217, 123),
    "sensorium": (43, 10, 16),
    "dreamworlds": (255, 255, 255),
    "dynamic_rainbow_liquid_glass": (216, 180, 254),
    "dreamworlds_liquid": (199, 125, 255),
    "dreamworlds_ultra": (80, 67, 95),
    "cosmic_supernova": (224, 86, 253),
    "retro_linux_memories": (255, 183, 20),
    "into_darkness_void_out": (255, 255, 255),
    "retro_terminal": (87, 87, 87),
    "the_whitest_white": (0, 0, 0),
    "dracula": (77, 73, 88),
    "nord": (255, 255, 255),
    "matrix": (57, 255, 20),
    "solarized": (255, 255, 255),
    "tokyo": (144, 42, 11),
    "monokai": (20, 89, 7),
    "gruvbox": (103, 73, 61),
    "emerald": (7, 91, 33),
    "classic": (99, 86, 35),
    "cyberpunk": (129, 31, 120),
    "dreamworlds_neon": (129, 12, 151),
    "retro_studio": (99, 77, 11),
    "cyber_neon": (152, 12, 12),
    "phosphor": (61, 95, 5),
    "ice_cold": (11, 83, 145),
}

# The themes whose console pane is black or a dark slate: plain white is the
# readable ink there. Dreamworlds, The Matrix, Nord and Solarized were moved to
# such panes on purpose. The Matrix keeps its phosphor green, which is not white.
LEGIT_WHITE = {
    "into_darkness_void_out",
    "dreamworlds",
    "nord",
    "solarized",
}

# Any 256/truecolor background fill means the row is painted on a block again.
BG_MARKERS = ("48;2;", "48;5;", "40m", "1;37;40m")

PREAMBLE = f'''
set -e
eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
'''

DUMP = '''
for theme in %s; do
    set_theme_colors "$theme"
    printf '%%s|%%s|%%s;%%s;%%s|%%s;%%s;%%s\\n' "$theme" "$MENU_INK" \\
        "$MENU_INK_R" "$MENU_INK_G" "$MENU_INK_B" \\
        "$THEME_BG_R" "$THEME_BG_G" "$THEME_BG_B"
done
''' % " ".join(EXPECTED_INK)


def _run(script: str) -> str:
    res = subprocess.run(["bash", "-c", PREAMBLE + script],
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise AssertionError(res.stderr)
    return res.stdout


def _luminance(rgb):
    def channel(value):
        value = value / 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(fore, back):
    hi, lo = sorted((_luminance(fore), _luminance(back)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class TestMenuTextThemeInk(unittest.TestCase):
    def test_every_theme_publishes_its_own_ink(self):
        """MENU_INK is the theme's hue, not the shared white 0.5 used."""
        seen = {}
        for raw in _run(DUMP).splitlines():
            theme, sgr, ink_rgb, pane_rgb = raw.split("|")
            ink = tuple(int(v) for v in ink_rgb.split(";"))
            pane = tuple(int(v) for v in pane_rgb.split(";"))
            seen[theme] = (sgr, ink, pane)
        self.assertEqual(set(seen), set(EXPECTED_INK))

        for theme, expected in EXPECTED_INK.items():
            sgr, ink, pane = seen[theme]
            self.assertEqual(ink, expected, f"{theme}: wrong menu ink")
            self.assertEqual(sgr, f"\033[38;2;{expected[0]};{expected[1]};{expected[2]}m",
                             f"{theme}: MENU_INK SGR disagrees with its RGB")
            self.assertNotIn("48;2;", sgr, f"{theme}: menu ink carries a fill")
            if theme not in LEGIT_WHITE:
                self.assertNotEqual(ink, (255, 255, 255),
                                    f"{theme}: option text is plain white again")

        # One ink per theme: the whole point is that switching theme changes it.
        distinct = {ink for _sgr, ink, _pane in seen.values()}
        self.assertEqual(len(distinct), len(set(EXPECTED_INK.values())))

    def test_every_ink_is_readable_on_its_own_pane(self):
        """No theme falls below 4.5:1, the WCAG AA floor for body text.

        Pale still-water panes pull each hue toward black; the dark native
        panes lighten it. Into Darkness is white on black and clears it
        easily.
        """
        for raw in _run(DUMP).splitlines():
            theme, _sgr, ink_rgb, pane_rgb = raw.split("|")
            ink = tuple(int(v) for v in ink_rgb.split(";"))
            pane = tuple(int(v) for v in pane_rgb.split(";"))
            ratio = _contrast(ink, pane)
            self.assertGreaterEqual(
                ratio, 4.5,
                f"{theme}: menu ink {ink} on pane {pane} is only {ratio:.2f}:1",
            )

    def test_unselected_row_paints_the_theme_ink(self):
        """The rendered row uses MENU_INK for the number, label and no fill."""
        script = '''
        eval "$(sed -n '/^_fit_menu_extra() {/,/^}/p' "Mix_Archive_Manager.sh")"
        eval "$(sed -n '/^_menu_line_plain() {/,/^}/p' "Mix_Archive_Manager.sh")"
        declare -a opts=('7|Convert to FLAC|24-bit / 96 kHz')
        declare -n _opts_ref=opts
        for theme in %s; do
            set_theme_colors "$theme"
            box_w=74
            line=""
            _menu_line_plain 0 line
            printf '%%s\\t%%s\\n' "$theme" "$line"
        done
        ''' % " ".join(EXPECTED_INK)
        seen = {}
        for raw in _run(script).splitlines():
            theme, line = raw.split("\t", 1)
            seen[theme] = line
        self.assertEqual(set(seen), set(EXPECTED_INK))

        for theme, ink in EXPECTED_INK.items():
            line = seen[theme]
            sgr = f"38;2;{ink[0]};{ink[1]};{ink[2]}"
            self.assertIn(sgr, line, f"{theme}: row does not use the theme ink")
            self.assertIn("Convert to FLAC", line, theme)
            # The ink must precede the label for the label itself to carry it.
            self.assertLess(line.find(sgr), line.find("Convert to FLAC"), theme)
            for marker in BG_MARKERS:
                self.assertNotIn(marker, line,
                                 f"{theme}: row still paints a background")

    def test_status_and_eq_lines_keep_the_neutral_ink(self):
        """The Selection: line and EQ line stay neutral, not theme-tinted.

        Those two sit on the pane rather than on a themed row, so they keep
        MENU_WHITE (which flips to black on The Whitest White). Only the
        option rows follow the theme.
        """
        text = MGR_SH.read_text(encoding="utf-8")
        self.assertIn("${MENU_WHITE}EQ: ", text)
        self.assertIn("${BOLD}${CYAN}Selection:${NC}${MENU_WHITE}", text)
        # The option rows must not be back on the neutral ink.
        self.assertNotIn('label_display="${MENU_WHITE}', text)
        self.assertNotIn("${MENU_WHITE}${BOLD}${key_formatted}", text)
        self.assertNotIn('${MENU_WHITE}${BOLD}$o_key)', text)


if __name__ == "__main__":
    unittest.main()
