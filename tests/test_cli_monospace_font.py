#!/usr/bin/env python3
"""The CLI manager's Konsole window uses the Monospace family.

MP Gui, MP Liquid Player, and MP Liquid Mixer take their face from
default_liquid_glass_font. The manager is a Konsole session, so its face is a
profile the launchers select and the script asks an already-open session to
use. The point size stays the user's existing Konsole size.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "scripts" / "mp_cli_font.sh"
MANAGER = ROOT / "Mix_Archive_Manager.sh"


class TestCliMonospaceFont(unittest.TestCase):
    def test_profile_keeps_the_users_point_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            konsole = home / ".local" / "share" / "konsole"
            konsole.mkdir(parents=True)
            (home / ".config").mkdir()
            (home / ".config" / "konsolerc").write_text(
                "[Desktop Entry]\nDefaultProfile=Profile 1.profile\n",
                encoding="utf-8",
            )
            (konsole / "Profile 1.profile").write_text(
                "[Appearance]\nFont=Hack,24,-1,5,400,0,0,0,0,0,0,0,0,0,0,1,,0,0\n",
                encoding="utf-8",
            )
            script = f"""
            set -e
            . "{HELPER}"
            mp_ensure_cli_font_profile
            """
            subprocess.run(
                ["bash", "-c", script],
                check=True,
                env=dict(os.environ, HOME=str(home)),
            )
            text = (konsole / "MP_Mix_Manager.profile").read_text(encoding="utf-8")
            self.assertIn("Font=Monospace,24,-1,5,400,0,0,0,0,0,0,0,0,0,0,1,,0,0", text)
            self.assertIn("Name=MP_Mix_Manager", text)
            self.assertNotIn("Hack", text)

    def test_missing_profile_falls_back_to_eleven_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            script = f"""
            set -e
            . "{HELPER}"
            mp_ensure_cli_font_profile
            """
            subprocess.run(
                ["bash", "-c", script],
                check=True,
                env=dict(os.environ, HOME=str(home)),
            )
            text = (home / ".local/share/konsole/MP_Mix_Manager.profile").read_text(encoding="utf-8")
            self.assertIn("Font=Monospace,11,-1,5,400,0,0,0,0,0,0,0,0,0,0,1,,0,0", text)

    def test_launchers_select_the_profile(self):
        for name in ("launch-manager", "launch-manager-fullscreen"):
            text = (ROOT / "bin" / name).read_text(encoding="utf-8")
            self.assertIn("--profile MP_Mix_Manager", text)
            self.assertIn("mp_ensure_cli_font_profile", text)
        windowed = (ROOT / "bin" / "launch-manager").read_text(encoding="utf-8")
        self.assertIn("exec /usr/bin/konsole --separate", windowed)
        self.assertNotIn("--fullscreen", windowed)

    def test_manager_switches_before_the_theme_and_restores_on_exit(self):
        text = MANAGER.read_text(encoding="utf-8")
        paint = text.split("_paint_theme_background_on_load() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(paint.index("_apply_cli_monospace_font"), paint.index("_ensure_konsole_default_scheme"))
        plain = text.split('if [ "${_MP_CLI_ARGS_PRESENT:-0}" != "1" ]; then', 1)[1].split("\nfi", 1)[0]
        self.assertLess(plain.index("_apply_cli_monospace_font"), plain.index("_apply_cli_theme_background"))
        self.assertIn("declare -F _apply_cli_monospace_font", plain)
        exit_fn = text.split("exit_mix_manager() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(exit_fn.index("_restore_cli_theme_background"), exit_fn.index("_restore_cli_monospace_font"))
        body = text.split("_apply_cli_monospace_font() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('MP_LIQUID_GLASS:-0}" = "1"', body)
        self.assertIn('MP_GUI_SESSION:-0}" = "1"', body)
        self.assertIn('setProfile "MP_Mix_Manager"', body)


if __name__ == "__main__":
    unittest.main()
