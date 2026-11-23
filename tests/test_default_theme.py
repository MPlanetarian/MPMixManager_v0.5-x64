#!/usr/bin/env python3
"""Dynamic Liquid Glass [Rainbow Colours] is the first-run default for the
CLI manager, MP Gui, the Liquid Player and the standalone audio player.

A fresh install writes that theme to the shared file, and those windows fall
back to it when the file is missing. The MP Liquid Mixer and the auxiliary
tools keep their existing Dreamworlds Ultra fallbacks.
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


class TestRainbowIsDefault(unittest.TestCase):
    def test_shared_palette_default(self):
        """The mixer's palette fallback is deliberately unchanged."""
        import mp_theme_palette as palette

        self.assertEqual(palette.DEFAULT_THEME, "dreamworlds_ultra")
        self.assertEqual(palette.normalize_theme_name("default"), "dreamworlds_ultra")

    def test_installer_writes_rainbow(self):
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("printf '%s\\n' \"dynamic_rainbow_liquid_glass\"", text)
        self.assertIn("Default theme: Dynamic Liquid Glass [Rainbow Colours]", text)

    def test_manager_defaults_to_rainbow(self):
        script = """
        set -e
        eval "$(awk '/^set_theme_colors\\(\\)/{f=1} f{print} f&&/^}/{exit}' "Mix_Archive_Manager.sh")"
        set_theme_colors
        printf '%s\\t%s;%s;%s\\n' "$CURRENT_THEME" "$THEME_BG_R" "$THEME_BG_G" "$THEME_BG_B"
        """
        res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(res.returncode, 0, res.stderr)
        theme, bg = res.stdout.strip().split("\t")
        self.assertEqual(theme, "dynamic_rainbow_liquid_glass")
        self.assertEqual(bg, "20;12;32")

        text = MGR_SH.read_text(encoding="utf-8")
        self.assertIn('[ -n "$saved_theme" ] || saved_theme="dynamic_rainbow_liquid_glass"', text)
        self.assertIn("16|Dynamic Liquid Glass [Rainbow Colours] [Default] (New)", text)
        self.assertNotIn("2|Dreamworlds Ultra MP Mix Manager [Default]", text)
        self.assertIn("1|MPlanetarian Dreamworlds|", text)
        self.assertNotIn("1|MPlanetarian Dreamworlds [Default]", text)

        # An empty call and a name this build does not know both land on the
        # rainbow default. Cyberpunk stays available when asked for by name.
        for name, expect in (("", "dynamic_rainbow_liquid_glass"),
                             ("not-a-theme", "dynamic_rainbow_liquid_glass"),
                             ("cyberpunk", "cyberpunk")):
            probe = subprocess.run(
                ["bash", "-c", script.replace('set_theme_colors\n', f'set_theme_colors "{name}"\n', 1)],
                capture_output=True, text=True, cwd=str(ROOT),
            )
            self.assertEqual(probe.returncode, 0, probe.stderr)
            theme, bg = probe.stdout.strip().split("\t")
            self.assertEqual(theme, expect, name)
            if expect == "dynamic_rainbow_liquid_glass":
                self.assertEqual(bg, "20;12;32", name)

    def test_mp_gui_falls_back_to_rainbow(self):
        text = (ROOT / "MP_Gui.sh").read_text(encoding="utf-8")
        self.assertIn('THEME_NAME="dynamic_rainbow_liquid_glass"', text)
        self.assertNotIn('THEME_NAME="dreamworlds"\n', text)

    def test_standalone_player_falls_back_to_rainbow(self):
        import MP_Audio_Player as player

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(player, "CONFIG_DIR", Path(tmp)):
                viz = player.EqualizerVisualizer()
        self.assertEqual(viz.theme, "dynamic_rainbow_liquid_glass")
        self.assertEqual(viz.get_theme_display_name(),
                         "Dynamic Liquid Glass [Rainbow Colours] (New)")

    def test_mixer_deck_keeps_the_mixer_fallback(self):
        import MP_Audio_Player as player

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(player, "CONFIG_DIR", Path(tmp)), \
                 patch.dict(os.environ, {"MP_LIQUID_GLASS_MIXER": "1"}, clear=False):
                viz = player.EqualizerVisualizer()
        self.assertEqual(viz.theme, "dreamworlds_ultra")

    def test_web_companion_and_dj_fall_back_to_ultra(self):
        import mix_web_companion as mwc
        import dj_monitor as tm

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

    def test_conversion_script_defaults_to_ultra(self):
        for path in (ROOT / "Make_SOF_FLAC_CONVERSION.sh", ROOT / "scripts" / "Make_SOF_FLAC_CONVERSION.sh"):
            text = path.read_text(encoding="utf-8")
            self.assertIn('saved_theme="dreamworlds_ultra"', text)
            self.assertIn('case "${1:-dreamworlds_ultra}"', text)
            self.assertIn('set_theme_colors "${saved_theme:-dreamworlds_ultra}"', text)
            self.assertNotIn('saved_theme="dreamworlds"', text)
            self.assertNotIn('CURRENT_THEME="dreamworlds"', text)

        text = (ROOT / "Make_SOF_FLAC_CONVERSION.sh").read_text(encoding="utf-8")
        start = text.index("load_conversion_theme() {")
        end = text.index("\n}\nload_conversion_theme\n", start) + 2
        with tempfile.TemporaryDirectory() as tmp:
            res = subprocess.run(
                ["bash", "-c", text[start:end] + "\n"
                 "SCRIPT_DIR=/tmp PARENT_DIR=/tmp MIX_ARCHIVE_DIR=/tmp load_conversion_theme\n"
                 "printf '%s\\n' \"$CURRENT_THEME\"\n"],
                capture_output=True, text=True, cwd=str(ROOT),
                env={"HOME": tmp, "PATH": os.environ.get("PATH", "")},
            )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "dreamworlds_ultra")

    def test_bridge_defaults_to_ultra(self):
        import mp_software_bridge as bridge

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(bridge, "MM_THEME_FILE", root / "missing-mm"), \
                 patch.object(bridge, "CONGEN_THEME_FILE", root / "missing-cg"):
                self.assertEqual(
                    bridge.get_theme_names(),
                    ("dreamworlds_ultra", "dreamworlds_ultra"),
                )

    def test_tracklist_viewer_defaults_to_ultra(self):
        text = (ROOT / "scripts" / "view_tracklist_console.sh").read_text(encoding="utf-8")
        self.assertIn('local chosen_theme="dreamworlds_ultra"', text)
        self.assertIn('local raw_name="${1:-dreamworlds_ultra}"', text)


if __name__ == "__main__":
    unittest.main()
