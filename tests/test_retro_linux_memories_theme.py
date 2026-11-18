"""Tests for Retro Linux Memories theme across MP Mix Manager and MP Liquid Audio Player."""

import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


class TestRetroLinuxMemoriesTheme(unittest.TestCase):
    def test_audio_player_registration(self):
        import MP_Audio_Player as player
        eq = player.EqualizerVisualizer()

        # Theme presence in THEMES and THEME_NAMES
        self.assertIn("retro_linux_memories", eq.THEMES)
        self.assertEqual(eq.THEME_NAMES["retro_linux_memories"], "Retro Linux Memories")

        # Palette definition
        self.assertIn("retro_linux_memories", player._THEME_PALETTES)
        palette = player._THEME_PALETTES["retro_linux_memories"]
        self.assertIn("highlight", palette)
        self.assertIn("active", palette)
        self.assertIn("border", palette)
        self.assertIn("accent", palette)

        # Aliases
        for alias in ("retro_linux_memories", "retro_linux", "linux_memories", "veracrypt", "retro linux memories"):
            self.assertEqual(player._THEME_ALIASES.get(alias), "retro_linux_memories")

        # Set theme and verify palette
        eq.set_theme("retro_linux_memories")
        self.assertEqual(eq.theme, "retro_linux_memories")
        self.assertEqual(eq.get_theme_display_name(), "Retro Linux Memories")

        # Verify gradient and bar colors
        rgb_low = eq._base_theme_rgb(0.1)
        self.assertEqual(len(rgb_low), 3)
        rgb_high = eq._base_theme_rgb(0.9)
        self.assertEqual(len(rgb_high), 3)
        bar_low = eq._get_bar_color(0.2)
        self.assertIn("21;83;158", bar_low)

    def test_liquid_glass_opengl_registration(self):
        import liquid_glass_opengl as lgo
        self.assertIn("retro_linux_memories", lgo.THEME_COLORS)
        colors = lgo.theme_rgb("retro_linux_memories")
        self.assertEqual(colors[0], (45, 45, 45))    # Charcoal field
        self.assertEqual(colors[1], (21, 83, 158))   # VeraCrypt selection blue
        self.assertEqual(colors[2], (26, 30, 33))    # Vault chrome

        for alias in ("retro_linux_memories", "retro_linux", "veracrypt", "linux_memories"):
            self.assertEqual(lgo.THEME_ALIASES.get(alias), "retro_linux_memories")
            self.assertEqual(lgo.theme_rgb(alias), colors)

    def test_liquid_glass_vulkan_registration(self):
        import liquid_glass_vulkan as lgv
        self.assertIn("retro_linux_memories", lgv.THEME_COLORS)
        colors = lgv.theme_rgb("retro_linux_memories")
        self.assertEqual(colors[0], (45, 45, 45))
        self.assertEqual(colors[1], (21, 83, 158))
        self.assertEqual(colors[2], (26, 30, 33))

        for alias in ("retro_linux_memories", "retro_linux", "veracrypt"):
            self.assertEqual(lgv.THEME_ALIASES.get(alias), "retro_linux_memories")

    def test_liquid_glass_metal_registration(self):
        import liquid_glass_metal as lgm
        self.assertIn("retro_linux_memories", lgm.THEME_COLORS)
        colors = lgm.theme_rgb("retro_linux_memories")
        self.assertEqual(colors[0], (45, 45, 45))
        self.assertEqual(colors[1], (21, 83, 158))
        self.assertEqual(colors[2], (26, 30, 33))
        self.assertEqual(lgm.THEME_ALIASES.get("retro_linux_memories"), "retro_linux_memories")

    def test_mix_web_companion_registration(self):
        import mix_web_companion as mwc
        self.assertIn("retro_linux_memories", mwc.THEMES)
        theme = mwc.THEMES["retro_linux_memories"]
        self.assertEqual(theme["name"], "Retro Linux Memories")
        self.assertEqual(theme["accent_primary"], "#15539e")
        self.assertEqual(mwc.THEME_ALIASES.get("retro_linux_memories"), "retro_linux_memories")
        self.assertEqual(mwc.THEME_ALIASES.get("veracrypt"), "retro_linux_memories")

    def test_traktor_monitor_registration(self):
        import traktor_monitor as tm
        self.assertIn("retro_linux_memories", tm.THEMES)
        theme_row = tm.THEMES["retro_linux_memories"]
        self.assertEqual(theme_row[0], "Retro Linux Memories")
        self.assertEqual(theme_row[2], "#15539e")
        self.assertEqual(tm.resolve_theme_id("retro_linux_memories"), "retro_linux_memories")
        self.assertEqual(tm.resolve_theme_id("veracrypt"), "retro_linux_memories")

    def test_view_tracklist_console_script(self):
        sh_path = ROOT / "scripts" / "view_tracklist_console.sh"
        content = sh_path.read_text(encoding="utf-8")
        self.assertIn('"retro_linux_memories"', content)
        self.assertIn("Retro Linux Memories", content)

    def test_mix_archive_manager_script(self):
        mgr_path = ROOT / "Mix_Archive_Manager.sh"
        content = mgr_path.read_text(encoding="utf-8")
        self.assertIn("retro_linux_memories", content)
        self.assertIn("Retro Linux Memories", content)

    def test_console_playlist_text_follows_the_charcoal_field(self):
        """The console pane is the charcoal field #2d2d2d.

        Unselected console rows stay white on that dark pane, the same rule
        Into Darkness uses. Liquid Glass rows stay white too.
        """
        import MP_Audio_Player as player
        from mp_theme_palette import theme_background

        self.assertEqual(theme_background("retro_linux_memories"), (45, 45, 45))
        self.assertFalse(player.theme_has_light_console("retro_linux_memories"))

        scanner = MagicMock()
        scanner.get_mixes.return_value = [
            {"name": "Mix 01 - Playing.flac", "path": "/p/1.flac", "ext": "FLAC", "size_mb": 10},
            {"name": "Mix 02 - Plain Row.flac", "path": "/p/2.flac", "ext": "FLAC", "size_mb": 11},
        ]
        st = {
            "title": "Mix 01 - Playing.flac", "path": "/p/1.flac", "state": "playing",
            "index": 0, "position_fmt": "02:00", "duration_fmt": "60:00",
            "progress_pct": 3.3, "volume": 85, "pitch_percent": 0,
            "levels": [0.0] * 24, "peaks": [0.0] * 24,
            "levels_left": [0.0] * 24, "levels_right": [0.0] * 24,
            "peaks_left": [0.0] * 24, "peaks_right": [0.0] * 24,
        }
        sgr = re.compile(r"\x1b\[([0-9;]*)m")

        def foreground_at(text, index):
            fg = None
            for match in sgr.finditer(text, 0, index):
                codes = [c for c in match.group(1).split(";") if c]
                if match.group(1) == "" or "0" in codes:
                    fg = None
                for i, code in enumerate(codes):
                    if code == "38" and codes[i + 1:i + 2] == ["2"]:
                        fg = tuple(int(v) for v in codes[i + 2:i + 5])
            return fg

        for liquid in (False, True):
            ui = player.TerminalPlayerUI(engine=None, scanner=scanner, ui_size_mode="NORMAL", is_remote=True)
            ui.visualizer.theme = "retro_linux_memories"
            ui.selected_row = 0
            captured = []
            env = {k: v for k, v in os.environ.items() if k != "MP_LIQUID_GLASS"}
            if liquid:
                env["MP_LIQUID_GLASS"] = "1"
            with patch.dict(os.environ, env, clear=True), \
                 patch("MP_Audio_Player._present_frame", side_effect=lambda lines, w, h: captured.extend(lines)), \
                 patch("MP_Audio_Player._win_visible_size", return_value=(120, 40)):
                ui._get_status = MagicMock(return_value=st)
                ui._draw_frame()
            rows = [line for line in captured if "Mix 02 - Plain Row.flac" in line and "│" in line]
            self.assertTrue(rows, "plain playlist row was not drawn")
            row = rows[0]
            at = row.find("Mix 02")
            self.assertEqual(foreground_at(row, at), (255, 255, 255), "liquid" if liquid else "console")
            self.assertNotIn("38;2;255;183;20", row)
            self.assertNotIn("38;2;100;69;0", row[row.find("Mix 02"):])

    def test_btop_theme_file(self):
        theme_path = ROOT / "themes" / "Retro_Linux_Memories_MP_Mix_Manager.theme"
        self.assertTrue(theme_path.is_file())
        content = theme_path.read_text(encoding="utf-8")
        self.assertIn('theme[selected_bg]="#15539e"', content)
        self.assertIn('theme[selected_fg]="#ffffff"', content)


if __name__ == "__main__":
    unittest.main()
