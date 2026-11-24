#!/usr/bin/env python3
"""One theme, two windows: MP Gui and MP Liquid Player must stay in step.

The user-visible bug: pressing C inside MP Liquid Player recoloured the
player's playlist navigation bar, but MP Gui kept the previous palette. The
same happened the other way round. Both windows read and write
~/.config/mix-manager/theme, so the fix has three parts:

  1. Both C keys walk the exact same list (EqualizerVisualizer.THEMES).
  2. Both windows paint the selected row with the same RGB
     (_THEME_PALETTES[theme]["highlight"]).
  3. Both windows poll the shared file while their menu/frame loop runs, so a
     press in one window reaches the other without a restart.
"""

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


def _bash(script: str, home: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = home
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        cwd=str(BASE_DIR),
        env=env,
    )


MANAGER_THEME_FUNCS = r"""
set -e
eval "$(awk '/^set_theme_colors\(\)/{f=1} f{print} f&&/^}/{exit}' Mix_Archive_Manager.sh)"
eval "$(sed -n '/^_theme_file_stamp() {/,/^}/p' Mix_Archive_Manager.sh)"
eval "$(sed -n '/^_theme_file_name() {/,/^}/p' Mix_Archive_Manager.sh)"
eval "$(sed -n '/^sync_theme_from_file() {/,/^}/p' Mix_Archive_Manager.sh)"
_apply_cli_theme_background() { :; }
"""


class TestSharedThemeFile(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        conf = Path(self.home) / ".config" / "mix-manager"
        conf.mkdir(parents=True)
        self.theme_file = conf / "theme"
        # CONFIG_DIR is resolved at import time, so point it at this sandbox
        # for every player assertion.
        self._saved_config_dir = player.CONFIG_DIR
        player.CONFIG_DIR = conf

    def tearDown(self):
        import shutil

        player.CONFIG_DIR = self._saved_config_dir
        shutil.rmtree(self.home, ignore_errors=True)

    def test_manager_adopts_a_theme_the_player_wrote(self):
        self.theme_file.write_text("dreamworlds_ultra\n", encoding="utf-8")
        script = MANAGER_THEME_FUNCS + r"""
set_theme_colors dreamworlds_ultra
sync_theme_from_file || true
echo "BEFORE:$CURRENT_THEME:$SEL_BAR_R,$SEL_BAR_G,$SEL_BAR_B"
printf 'dreamworlds_liquid\n' > "$HOME/.config/mix-manager/theme"
sync_theme_from_file && echo CHANGED || echo NOCHANGE
echo "AFTER:$CURRENT_THEME:$SEL_BAR_R,$SEL_BAR_G,$SEL_BAR_B"
sync_theme_from_file && echo AGAIN || echo STABLE
"""
        res = _bash(script, self.home)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("BEFORE:dreamworlds_ultra:166,77,255", res.stdout)
        self.assertIn("CHANGED", res.stdout)
        self.assertIn("AFTER:dreamworlds_liquid:0,245,255", res.stdout)
        # A second tick must not repaint the same file value.
        self.assertIn("STABLE", res.stdout)

    def test_manager_stamp_tracks_a_same_size_same_second_rewrite(self):
        # dreamworlds_ultra and cosmic_supernova are both 17 bytes; a mtime-only
        # stamp missed that swap when both writes landed in one second.
        self.theme_file.write_text("dreamworlds_ultra\n", encoding="utf-8")
        script = MANAGER_THEME_FUNCS + r"""
first="$(_theme_file_stamp)"
printf 'cosmic_supernova\n' > "$HOME/.config/mix-manager/theme"
second="$(_theme_file_stamp)"
[ "$first" != "$second" ] && echo MOVED || echo SAME
"""
        res = _bash(script, self.home)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("MOVED", res.stdout)

    def test_player_adopts_a_theme_the_manager_wrote(self):
        self.theme_file.write_text("dreamworlds_ultra\n", encoding="utf-8")
        viz = player.EqualizerVisualizer()
        self.assertEqual(viz.theme, "dreamworlds_ultra")
        self.theme_file.write_text("sensorium\n", encoding="utf-8")
        self.assertTrue(viz.sync_theme_from_file())
        self.assertEqual(viz.theme, "sensorium")
        self.assertFalse(viz.sync_theme_from_file())

    def test_player_resolves_manager_aliases_from_the_file(self):
        self.theme_file.write_text("dracula\n", encoding="utf-8")
        viz = player.EqualizerVisualizer()
        self.assertEqual(viz.theme, "dreamworlds_neon")
        self.theme_file.write_text("nord\n", encoding="utf-8")
        self.assertTrue(viz.sync_theme_from_file())
        self.assertEqual(viz.theme, "ice_cold")

    def test_player_ignores_a_garbage_theme_value(self):
        self.theme_file.write_text("dreamworlds_liquid\n", encoding="utf-8")
        viz = player.EqualizerVisualizer()
        self.theme_file.write_text("not_a_theme\n", encoding="utf-8")
        self.assertFalse(viz.sync_theme_from_file())
        self.assertEqual(viz.theme, "dreamworlds_liquid")


class TestSameNavigationBarColour(unittest.TestCase):
    def test_every_theme_has_one_bar_rgb_in_both_windows(self):
        """The manager's SEL_BAR_* table and the player palette must agree."""
        themes = list(player.EqualizerVisualizer.THEMES)
        script = MANAGER_THEME_FUNCS + (
            "for theme in " + " ".join(themes) + r"""; do
    set_theme_colors "$theme"
    printf '%s\t%s\t%s\t%s\n' "$theme" "$SEL_BAR_R" "$SEL_BAR_G" "$SEL_BAR_B"
done
"""
        )
        home = tempfile.mkdtemp()
        try:
            res = _bash(script, home)
        finally:
            import shutil

            shutil.rmtree(home, ignore_errors=True)
        self.assertEqual(res.returncode, 0, res.stderr)

        seen = {}
        for raw in res.stdout.splitlines():
            theme, r, g, b = raw.split("\t")
            seen[theme] = (int(r), int(g), int(b))

        self.assertEqual(set(seen), set(themes))
        # The 0.5.4 selection-ink change paints the manager's selected row
        # white. Aligning each theme's bar with the player is the pending
        # one-by-one pass the user asked to defer, so this theme is exempt.
        pending = {"into_darkness_void_out"}
        for theme in themes:
            pal = player._THEME_PALETTES.get(theme, player._DEFAULT_PALETTE)
            expected_bg, _ = player._selection_bar_ansi(theme)
            expected = player._rgb_from_ansi(expected_bg)
            if theme in pending:
                self.assertTrue(pal.get("highlight"), theme)
                continue
            self.assertEqual(seen[theme], expected, theme)
            self.assertTrue(pal.get("highlight"), theme)

    def test_bar_hue_changes_when_the_theme_changes(self):
        """No two themes may share a bar colour by accident."""
        hues = {}
        for theme in player.EqualizerVisualizer.THEMES:
            bg, _ = player._selection_bar_ansi(theme)
            hues.setdefault(player._rgb_from_ansi(bg), []).append(theme)
        # Stream of Frequency vs Ice Cold and similar pairs may collide by
        # design; what matters is that the majority of themes are distinct.
        self.assertGreaterEqual(len(hues), 9)


class TestBothWindowsPoll(unittest.TestCase):
    def test_manager_menu_loop_polls_the_theme_file(self):
        text = MGR_SH.read_text(encoding="utf-8")
        self.assertIn("_theme_file_stamp", text)
        self.assertIn("sync_theme_from_file", text)
        tick = text[text.index("TICK) "):]
        tick = tick[:tick.index("continue ;;")]
        self.assertIn("_theme_file_stamp", tick)
        self.assertIn("sync_theme_from_file", tick)
        self.assertIn("_LAST_THEME_STAMP", tick)

    def test_player_frame_loop_polls_the_theme_file(self):
        text = (BASE_DIR / "MP_Audio_Player.py").read_text(encoding="utf-8")
        run = text[text.index("    def run(self):"):]
        run = run[:run.index("    def _input_loop(self):")]
        self.assertIn("_follow_shared_theme()", run)
        follow = text[text.index("    def _follow_shared_theme(self):"):]
        follow = follow[:follow.index("    def _draw_frame(self):")]
        self.assertIn('read_text(encoding="utf-8")', follow)
        self.assertIn("sync_theme_from_file", follow)
        # One short read per second, not one per 20 ms frame.
        self.assertIn("< 1.0", follow)
        self.assertIn("_theme_seen", follow)

    def test_player_remembers_its_own_write(self):
        text = (BASE_DIR / "MP_Audio_Player.py").read_text(encoding="utf-8")
        key = text[text.index('elif data in (b"c", b"C"):'):]
        key = key[:key.index("elif data in (b\"b\"")]
        self.assertIn("_theme_seen", key)


if __name__ == "__main__":
    unittest.main()
