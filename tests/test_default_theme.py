#!/usr/bin/env python3
"""Dreamworlds Ultra is the first-run default everywhere.

A fresh install must open the CLI manager, MP Gui, the Liquid Player and the
standalone audio player in Dreamworlds Ultra. The installer writes the shared
theme file, and every window falls back to Ultra when that file is missing.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

MGR_SH = ROOT / "Mix_Archive_Manager.sh"


class TestDreamworldsUltraIsDefault(unittest.TestCase):
    def test_shared_palette_default(self):
        import mp_theme_palette as palette

        self.assertEqual(palette.DEFAULT_THEME, "dreamworlds_ultra")
        self.assertEqual(palette.normalize_theme_name("default"), "dreamworlds_ultra")

    def test_installer_writes_ultra(self):
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("printf '%s\\n' \"dreamworlds_ultra\"", text)
        self.assertIn("Default theme: Dreamworlds Ultra [Default]", text)

    def test_manager_defaults_to_ultra(self):
        script = """
        set -e
        eval "$(awk '/^set_theme_colors\\(\\)/{f=1} f{print} f&&/^}/{exit}' "Mix_Archive_Manager.sh")"
        set_theme_colors
        printf '%s\\t%s;%s;%s\\n' "$CURRENT_THEME" "$THEME_BG_R" "$THEME_BG_G" "$THEME_BG_B"
        """
        res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(res.returncode, 0, res.stderr)
        theme, bg = res.stdout.strip().split("\t")
        self.assertEqual(theme, "dreamworlds_ultra")
        self.assertEqual(bg, "85;74;107")

        text = MGR_SH.read_text(encoding="utf-8")
        self.assertIn('[ -n "$saved_theme" ] || saved_theme="dreamworlds_ultra"', text)
        self.assertIn("2|Dreamworlds Ultra MP Mix Manager [Default]", text)
        self.assertIn("1|MPlanetarian Dreamworlds|", text)
        self.assertNotIn("1|MPlanetarian Dreamworlds [Default]", text)

    def test_mp_gui_falls_back_to_ultra(self):
        text = (ROOT / "MP_Gui.sh").read_text(encoding="utf-8")
        self.assertIn('THEME_NAME="dreamworlds_ultra"', text)
        self.assertNotIn('THEME_NAME="dreamworlds"\n', text)

    def test_standalone_player_falls_back_to_ultra(self):
        import MP_Audio_Player as player

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(player, "CONFIG_DIR", Path(tmp)):
                viz = player.EqualizerVisualizer()
        self.assertEqual(viz.theme, "dreamworlds_ultra")
        self.assertEqual(viz.get_theme_display_name(), "Dreamworlds Ultra")

    def test_web_companion_and_traktor_fall_back_to_ultra(self):
        import mix_web_companion as mwc
        import traktor_monitor as tm

        self.assertEqual(tm._ACTIVE_THEME_ID, "dreamworlds_ultra")
        self.assertEqual(tm.resolve_theme_id(None), "dreamworlds_ultra")
        # The web companion's last-resort theme is Ultra.
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(mwc, "WEB_THEME_FILE", Path(tmp) / "missing"), \
                 patch.dict(os.environ, {}, clear=False):
                for key in ("CURRENT_THEME", "MP_THEME", "MIX_MANAGER_THEME"):
                    os.environ.pop(key, None)
                with patch.object(mwc.Path, "home", return_value=Path(tmp)):
                    self.assertEqual(mwc.get_current_theme_id(None), "dreamworlds_ultra")

    def test_tracklist_viewer_defaults_to_ultra(self):
        text = (ROOT / "scripts" / "view_tracklist_console.sh").read_text(encoding="utf-8")
        self.assertIn('local chosen_theme="dreamworlds_ultra"', text)
        self.assertIn('local raw_name="${1:-dreamworlds_ultra}"', text)


if __name__ == "__main__":
    unittest.main()
