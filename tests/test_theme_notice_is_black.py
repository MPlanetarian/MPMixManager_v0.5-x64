#!/usr/bin/env python3
"""The "Color Theme: <name>" notice is always drawn in white.

The notice appears just below the equaliser when the user presses C (or when
another window changes the shared theme file). It used to follow the theme,
then it was pinned to black for the pale glass pane. Black disappears on the
dark consoles, and a theme hue disappears on the light tints, so the name is
white on a dark chip on every theme.

The console ▶ PLAYING label is always white, on every theme. Liquid Glass
still uses black on the pale pane and a light ink on the two charcoal panes.
These tests pin that split, plus the two guards that would silently recolour
a line:

  1. ``_notify_theme`` marks the notice, and ``_notification_ansi`` returns the
     white chip for it (and the usual green for every other notice).
  2. The visualizer brightness guard, which lifts dark foregrounds, leaves a
     line that asks for pure black alone.
  3. The Liquid Glass bold-stripper does not change the colour either.
"""

import importlib.util
import re
import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import MP_Audio_Player as player

BLACK = "\033[38;2;0;0;0m"
WHITE_BADGE = "\033[38;2;255;255;255m"
WHITE_NOTICE = "\033[1;48;2;20;20;28;38;2;255;255;255m"
DARK_GLASS = {"into_darkness_void_out", "retro_linux_memories"}


class ThemeNoticeColourTests(unittest.TestCase):
    def test_notice_constant_is_white_on_a_dark_chip(self):
        self.assertEqual(player.COLOR_THEME_NOTICE, WHITE_NOTICE)
        self.assertIn("38;2;255;255;255", player.COLOR_THEME_NOTICE)
        self.assertIn("48;2;20;20;28", player.COLOR_THEME_NOTICE)

    def test_theme_notice_is_white_on_every_theme(self):
        """Pale glass, light tints and the charcoal panes all name the theme in white."""
        themes = list(player.EqualizerVisualizer.THEMES)
        self.assertGreater(len(themes), 10)
        for theme in themes:
            self.assertEqual(
                player.theme_notice_ansi(theme), WHITE_NOTICE,
                f"{theme} should use the white notice",
            )

    def test_dark_glass_set_matches_the_opengl_pane_decision(self):
        """The exception list must agree with the shader's own dark-pane test."""
        sys.path.insert(0, str(BASE_DIR / "scripts"))
        from liquid_glass_opengl import theme_uses_dark_glass

        dark = {t for t in player.EqualizerVisualizer.THEMES if theme_uses_dark_glass(t)}
        self.assertEqual(dark, DARK_GLASS)
        self.assertEqual(set(player.THEME_NOTICE_DARK_GLASS), DARK_GLASS)

    def test_theme_notice_uses_white(self):
        class UI:
            notification_is_theme = True
            visualizer = type("V", (), {"theme": "dreamworlds_neon"})()
            _notification_ansi = player.TerminalPlayerUI._notification_ansi

        self.assertEqual(UI()._notification_ansi(""), WHITE_NOTICE)

    def test_other_notices_stay_green(self):
        class UI:
            notification_is_theme = False
            visualizer = type("V", (), {"theme": "dreamworlds_neon"})()
            _notification_ansi = player.TerminalPlayerUI._notification_ansi

        self.assertEqual(UI()._notification_ansi(""), player.COLOR_TRAFFIC_GREEN)

    def test_notify_theme_marks_the_notice(self):
        calls = {}

        class UI:
            notification_msg = ""
            notification_time = 0.0
            _notify = player.TerminalPlayerUI._notify
            _notify_theme = player.TerminalPlayerUI._notify_theme
            visualizer = type("V", (), {"get_theme_display_name": lambda self: "Dreamworlds Neon"})()

        ui = UI()
        ui._notify_theme()
        self.assertTrue(ui.notification_is_theme)
        self.assertEqual(ui.notification_msg, "Color Theme: Dreamworlds Neon")

    def test_plain_notify_clears_the_theme_flag(self):
        class UI:
            notification_is_theme = True
            _notify = player.TerminalPlayerUI._notify

        ui = UI()
        ui._notify("Repeat: ON")
        self.assertFalse(ui.notification_is_theme)

    def test_brightness_guard_keeps_black(self):
        """The visualizer guard lifts dark inks to ~#d1d1d1; black must survive."""
        line = f"  {BLACK}\U0001F4C2 Color Theme: Dreamworlds Neon\033[0m"
        guarded = player._guard_visualizer_text(line)
        self.assertIn(BLACK, guarded)
        self.assertNotIn("38;2;209;209;209", guarded)

    def test_brightness_guard_keeps_a_black_run_among_other_colours(self):
        """The badge shares its line with the pink title and the cyan format."""
        line = (
            f"  {BLACK}\u25B6 PLAYING\033[0m  "
            "\033[38;2;247;37;133m\u266B  Mix\033[0m "
            "\033[38;2;0;229;255m[FLAC]\033[0m"
        )
        guarded = player._guard_visualizer_text(line)
        self.assertIn(BLACK, guarded)
        self.assertNotIn("38;2;209;209;209", guarded)

    def test_brightness_guard_still_lifts_other_colours(self):
        line = "  \033[38;2;0;255;0m\U0001F4C2 Repeat: ON\033[0m"
        self.assertIn("38;2;0;255;0", player._guard_visualizer_text(line))

    def test_bold_stripper_keeps_black(self):
        line = f"  \033[1m{BLACK}\U0001F4C2 Color Theme: X\033[0m"
        self.assertIn(BLACK, player._strip_bold_ansi(line))

    def test_both_theme_change_paths_use_the_black_helper(self):
        """Pressing C and an external theme write must both go through _notify_theme."""
        source = (BASE_DIR / "MP_Audio_Player.py").read_text(encoding="utf-8")
        # Every direct "Color Theme: ..." _notify call must live inside the
        # _notify_theme helper. Count the calls and make sure only the helper
        # body contains one.
        direct_calls = re.findall(
            r'_notify\(f"Color Theme: \{self\.visualizer\.get_theme_display_name\(\)\}"[^)]*\)',
            source,
        )
        self.assertEqual(len(direct_calls), 1, "expected exactly one direct theme notice call")
        helper_start = source.index("def _notify_theme")
        helper_end = source.index("def _notification_ansi", helper_start)
        self.assertIn(direct_calls[0], source[helper_start:helper_end])
        # The key handler and the file-poll path both call the helper.
        self.assertEqual(source.count("self._notify_theme()"), 2, "expected both theme paths to use the helper")

    # ---- the PLAYING badge -----------------------------------------------

    def test_console_playing_badge_is_white_on_every_theme(self):
        """The CLI ▶ PLAYING label does not follow the pane."""
        themes = list(player.EqualizerVisualizer.THEMES)
        self.assertGreater(len(themes), 10)
        for theme in themes:
            self.assertEqual(
                player.playing_badge_ink(theme, liquid=False), WHITE_BADGE,
                f"{theme} console PLAYING badge should be white",
            )

    def test_glass_playing_badge_is_black_on_pale_panes(self):
        pale = [t for t in player.EqualizerVisualizer.THEMES if t not in DARK_GLASS]
        for theme in pale:
            self.assertEqual(
                player.playing_badge_ink(theme, liquid=True), BLACK,
                f"{theme} glass PLAYING badge should be black",
            )

    def test_playing_badge_is_not_black_on_dark_glass_themes(self):
        for theme in DARK_GLASS:
            self.assertNotEqual(player.playing_badge_ink(theme, liquid=True), BLACK, theme)
            self.assertEqual(
                player.playing_badge_ink(theme, liquid=True),
                player.THEME_DARK_GLASS_INK[theme],
                theme,
            )

    def test_notice_stays_on_its_chip_while_the_badges_differ(self):
        """The theme name stays on the dark chip. Neither badge reuses it."""
        for theme in player.EqualizerVisualizer.THEMES:
            self.assertEqual(player.theme_notice_ansi(theme), WHITE_NOTICE, theme)
            self.assertNotEqual(
                player.theme_notice_ansi(theme),
                player.playing_badge_ink(theme, liquid=False),
                f"{theme} notice fell onto the console badge",
            )
            self.assertNotEqual(
                player.theme_notice_ansi(theme),
                player.playing_badge_ink(theme, liquid=True),
                f"{theme} notice fell onto the glass badge",
            )

    def test_playing_badge_uses_the_helper_at_every_render_site(self):
        """Full, Vertical, mini and micro frames all colour the badge from the helper."""
        source = (BASE_DIR / "MP_Audio_Player.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("playing_badge_ink(self.visualizer.theme, is_lg)"), 4,
                         "expected the badge in full, Vertical, mini and micro frames")
        # The old green play inks must be gone.
        self.assertNotIn("{GREEN}\u25B6 PLAYING", source)
        self.assertNotIn("38;2;0;255;127m\u25B6 PLAY", source)
        self.assertNotIn("38;2;0;255;127m\u25B6{NC}", source)


if __name__ == "__main__":
    unittest.main()
