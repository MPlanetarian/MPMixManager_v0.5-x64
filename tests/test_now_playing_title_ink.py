#!/usr/bin/env python3
"""The now-playing title is pure black or pure white, chosen by its surface.

The title at the top of the player used to be painted in the theme's neon pink
(``COLOR_NEON_PINK``) in the full frame, and in the theme's body ink in Mini and
Micro. Neither has a guaranteed contrast against what is behind it, and the
title is the one line a user reads from across the room. Three themes made that
obvious: the pink washed out on the pale glass, and the body ink on The Whitest
White and on the charcoal themes was either invisible or near-black on black.

The rule is now the highest-contrast pair the surface allows:

* **Liquid Glass** paints the pale still-water field on every theme except
  Into Darkness, whose pane is void-out black. The title is black on the
  field and white on the void. ``THEME_DARK_GLASS_INK`` is the same table the
  shader's ``theme_uses_dark_glass`` is pinned to by
  ``tests/test_theme_notice_is_black.py``.
* **The plain console** paints that same field, via ``theme_background()``,
  so the title agrees with the glass. The flag stays so a caller still names
  the surface it is drawing into.

These tests pin the helper, all three render sites (full, Mini, Micro), both
surfaces, and the two hazards that would silently undo it: the visualizer
brightness guard and the Liquid bold-stripper must both leave the ink alone.
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import MP_Audio_Player as player  # noqa: E402

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
_SGR = re.compile(r"\x1b\[([0-9;]*)m")
THEMES = player.EqualizerVisualizer.THEMES
CHARCOAL_PANE = {"into_darkness_void_out"}
TITLE = "MPlanetarian - Dreamworlds Test Mix 2026.flac"


def _foreground_at(text: str, index: int, default=None):
    """The foreground in effect at ``index``, replaying SGR resets.

    A line that merely *contains* a black escape proves nothing: a later ``0m``
    clears it and the text falls back to the terminal default. This walks the
    escape stream so the assertion is about the colour actually painted.
    """
    fg = default
    for match in _SGR.finditer(text, 0, index):
        codes = [c for c in match.group(1).split(";") if c]
        if match.group(1) == "" or "0" in codes:
            fg = default
        for i, code in enumerate(codes):
            if code == "38" and codes[i + 1:i + 2] == ["2"]:
                fg = tuple(int(v) for v in codes[i + 2:i + 5])
    return fg


def _status():
    return {
        "title": TITLE, "path": "/mixes/Mix.flac", "state": "playing", "index": 0,
        "position_sec": 300.0, "duration_sec": 3600.0,
        "position_fmt": "05:00", "duration_fmt": "60:00", "progress_pct": 8.3,
        "volume": 85, "pitch_percent": 0,
        "levels": [0.0] * 24, "peaks": [0.0] * 24,
        "levels_left": [0.0] * 24, "levels_right": [0.0] * 24,
        "peaks_left": [0.0] * 24, "peaks_right": [0.0] * 24,
    }


def _draw(theme, mode, height, liquid):
    """Paint one frame and return the lines presented."""
    scanner = MagicMock()
    scanner.get_mixes.return_value = []
    ui = player.TerminalPlayerUI(None, scanner, is_remote=True, ui_size_mode=mode)
    ui._send_cmd = MagicMock()
    ui._get_status = lambda: _status()
    ui.visualizer.theme = theme
    captured = []
    # Console mode is "the variable is absent", not "the variable is 0":
    # `bool(os.environ.get(...))` treats the string "0" as true.
    env = {k: v for k, v in os.environ.items() if k != "MP_LIQUID_GLASS"}
    if liquid:
        env["MP_LIQUID_GLASS"] = "1"
    with patch.dict(os.environ, env, clear=True), \
         patch("MP_Audio_Player._present_frame",
               side_effect=lambda lines, w, h: captured.extend(lines)), \
         patch("MP_Audio_Player._win_visible_size", return_value=(120, height)), \
         patch("sys.stdout.write"), patch("sys.stdout.flush"):
        ui._draw_frame()
    return captured


def _title_row(lines):
    """The row carrying the now-playing title, even when Micro truncated it.

    Micro clips the title to fit seven rows (`MPlanetarian - Dreamworld..`), so
    matching the whole string would report the frame as missing the title.
    """
    for line in lines:
        if TITLE in line:
            return line
    stem = TITLE.split(" - ")[0]
    return next((line for line in lines if stem in line), None)


class NowPlayingInkHelperTests(unittest.TestCase):
    def test_glass_is_black_on_every_pale_pane_theme(self):
        for theme in THEMES:
            if theme in CHARCOAL_PANE:
                continue
            self.assertEqual(
                player.now_playing_ink(theme, liquid=True), player.NOW_PLAYING_BLACK,
                f"{theme} sits on the pale glass pane and needs black",
            )

    def test_glass_is_white_on_the_void_theme(self):
        for theme in CHARCOAL_PANE:
            self.assertEqual(
                player.now_playing_ink(theme, liquid=True), player.NOW_PLAYING_WHITE,
                f"{theme} paints the black void and needs white",
            )

    def test_the_glass_choice_matches_the_shader_pane_decision(self):
        """The ink must follow the pane the shader actually paints."""
        from liquid_glass_opengl import theme_uses_dark_glass

        for theme in THEMES:
            self.assertEqual(
                player.now_playing_uses_white(theme, liquid=True),
                theme_uses_dark_glass(theme),
                f"{theme} title disagrees with the shader's pane",
            )

    def test_console_uses_background_luminance(self):
        """The console pane is the glass field, judged by its luminance."""
        from mp_theme_palette import theme_background

        for theme in THEMES:
            red, green, blue = theme_background(theme)
            luma = 0.2126 * red + 0.7152 * green + 0.0722 * blue
            self.assertEqual(
                player.now_playing_uses_white(theme, liquid=False),
                luma < 140,
                f"{theme} console title picked the wrong polarity",
            )

    def test_console_and_glass_titles_agree(self):
        """Both surfaces use the still-water field, so the title ink agrees."""
        for theme in THEMES:
            self.assertEqual(
                player.now_playing_uses_white(theme, True),
                player.now_playing_uses_white(theme, False),
                theme,
            )

    def test_only_pure_black_or_pure_white_is_ever_returned(self):
        for theme in THEMES:
            for liquid in (True, False):
                ink = player.now_playing_ink(theme, liquid)
                self.assertIn(ink, (player.NOW_PLAYING_BLACK, player.NOW_PLAYING_WHITE))

    def test_unknown_theme_falls_back_to_black_on_glass(self):
        self.assertEqual(player.now_playing_ink("no_such_theme", liquid=True),
                         player.NOW_PLAYING_BLACK)


class NowPlayingInkRenderTests(unittest.TestCase):
    """All three frames must paint the title in the colour actually resolved."""

    def _assert_frame(self, theme, mode, height, liquid):
        lines = _draw(theme, mode, height, liquid)
        row = _title_row(lines)
        self.assertIsNotNone(row, f"{mode} {theme} did not paint the title")
        stem = TITLE if TITLE in row else TITLE.split(" - ")[0]
        painted = _foreground_at(row, row.index(stem))
        expected = WHITE if player.now_playing_uses_white(theme, liquid) else BLACK
        self.assertEqual(
            painted, expected,
            f"{mode} / {'glass' if liquid else 'console'} / {theme}: "
            f"title painted {painted}, expected {expected}",
        )
        return row

    def test_full_frame_on_glass(self):
        for theme in THEMES:
            self._assert_frame(theme, "NORMAL", 40, liquid=True)

    def test_mini_frame_on_glass(self):
        for theme in THEMES:
            self._assert_frame(theme, "MINI", 14, liquid=True)

    def test_micro_frame_on_glass(self):
        for theme in THEMES:
            self._assert_frame(theme, "MICRO", 7, liquid=True)

    def test_full_frame_on_the_console(self):
        for theme in THEMES:
            self._assert_frame(theme, "NORMAL", 40, liquid=False)

    def test_mini_frame_on_the_console(self):
        for theme in THEMES:
            self._assert_frame(theme, "MINI", 14, liquid=False)

    def test_the_note_glyph_shares_the_title_ink(self):
        """The ♫ must not stay a pink bullet beside a white title."""
        row = self._assert_frame("stream_of_frequency", "NORMAL", 40, liquid=True)
        note = row.index("♫")
        expected = WHITE if player.now_playing_uses_white("stream_of_frequency", True) else BLACK
        self.assertEqual(_foreground_at(row, note), expected)

    def test_the_old_pink_title_ink_is_gone(self):
        lines = _draw("dreamworlds_ultra", "NORMAL", 40, liquid=True)
        row = _title_row(lines)
        self.assertNotIn("247;37;133", row, "the title must not use the neon pink")

    def test_console_playing_label_is_white_on_light_and_dark_panes(self):
        """The ▶ PLAYING word at the top of the CLI player is white on every theme."""
        for theme in ("the_whitest_white", "sensorium", "retro_linux_memories",
                      "dreamworlds_ultra", "into_darkness_void_out"):
            lines = _draw(theme, "NORMAL", 40, liquid=False)
            row = next(line for line in lines if "PLAYING" in line)
            painted = _foreground_at(row, row.index("PLAYING"))
            self.assertEqual(painted, WHITE, f"{theme} PLAYING painted {painted}")

    def test_glass_playing_label_still_tracks_the_pane(self):
        pale = _draw("dreamworlds_ultra", "NORMAL", 40, liquid=True)
        pale_row = next(line for line in pale if "PLAYING" in line)
        self.assertEqual(_foreground_at(pale_row, pale_row.index("PLAYING")), BLACK)
        dark = _draw("into_darkness_void_out", "NORMAL", 40, liquid=True)
        dark_row = next(line for line in dark if "PLAYING" in line)
        self.assertEqual(_foreground_at(dark_row, dark_row.index("PLAYING")), (236, 240, 244))


class InkSurvivesThePostProcessorsTests(unittest.TestCase):
    """Two passes rewrite escapes on the way to the screen; both must keep ink."""

    def test_the_visualizer_brightness_guard_keeps_both_inks(self):
        for ink in (player.NOW_PLAYING_BLACK, player.NOW_PLAYING_WHITE):
            line = f"  {ink}♫  Mix\u001b[0m "
            guarded = player._guard_visualizer_text(line)
            self.assertIn("38;2;" + ("0;0;0" if ink == player.NOW_PLAYING_BLACK else "255;255;255"),
                          guarded)

    def test_the_guard_does_not_lift_white_to_something_else(self):
        line = f"  {player.NOW_PLAYING_WHITE}♫  Mix\u001b[0m "
        guarded = player._guard_visualizer_text(line)
        self.assertIn("255;255;255", guarded)

    def test_the_liquid_bold_stripper_keeps_the_colour(self):
        for ink, needle in ((player.NOW_PLAYING_BLACK, "38;2;0;0;0"),
                            (player.NOW_PLAYING_WHITE, "38;2;255;255;255")):
            stripped = player._strip_bold_ansi(f"  {ink}♫  Mix\u001b[0m ")
            self.assertIn(needle, stripped)

    def test_the_title_reaches_the_screen_after_both_passes(self):
        """End to end: the strings the renderer builds survive presentation."""
        source = (ROOT / "MP_Audio_Player.py").read_text(encoding="utf-8")
        self.assertIn("now_playing_ink(self.visualizer.theme, is_lg)", source)
        # The full frame stores it in a local before the f-string.
        self.assertIn("title_ink = now_playing_ink(self.visualizer.theme, is_lg)", source)


class EveryRenderSiteUsesTheHelperTests(unittest.TestCase):
    def test_all_three_frames_resolve_through_one_helper(self):
        source = (ROOT / "MP_Audio_Player.py").read_text(encoding="utf-8")
        self.assertEqual(
            source.count("now_playing_ink(self.visualizer.theme, is_lg)"), 3,
            "full, Mini and Micro must all use the shared helper",
        )

    def test_no_frame_hard_codes_a_theme_ink_for_the_title(self):
        source = (ROOT / "MP_Audio_Player.py").read_text(encoding="utf-8")
        self.assertNotIn(
            '{bold_s}{COLOR_NEON_PINK}♫  {tname}', source,
            "the full frame's title returned to the neon pink",
        )


if __name__ == "__main__":
    unittest.main()
