#!/usr/bin/env python3
"""Into Darkness (Void Out) is selectable from the manager's theme picker.

The theme existed in the palette tables, the player's ``C`` cycle, the Liquid
Glass renderers and the Liquid Mixer, but it was never a row in
**Manager Settings → Themes & Color Palette Switcher**, so a user could only
reach it with the ``C`` key. It is now row 22.
"""

import subprocess
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MGR_SH = BASE_DIR / "Mix_Archive_Manager.sh"


def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, cwd=str(BASE_DIR)
    )


class TestIntoDarknessPicker(unittest.TestCase):
    def test_picker_offers_and_switches_to_it(self):
        text = MGR_SH.read_text(encoding="utf-8")
        self.assertIn("22|Into Darkness (Void Out)|", text)
        self.assertIn('save_theme "into_darkness_void_out"', text)
        self.assertIn("into_darkness_void_out)         m22=", text)

    def test_picker_row_selects_the_void_out_theme(self):
        script = f"""
        eval "$(awk '/^manage_themes\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
        save_theme() {{ printf '%s\\n' "$1"; }}
        REPLY=0
        _row=""
        _picks=0
        run_interactive_submenu() {{
            shift 2
            local -n _o="$1"
            if [ "$_picks" -eq 0 ]; then
                for _r in "${{_o[@]}}"; do
                    case "$_r" in 22\\|*) _row="$_r" ;; esac
                done
                REPLY=22
            else
                REPLY=0
            fi
            _picks=$((_picks + 1))
        }}
        CURRENT_THEME=into_darkness_void_out
        manage_themes
        printf 'ROW=%s\\n' "$_row"
        """
        res = _bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("ROW=22|Into Darkness (Void Out)|", res.stdout)
        self.assertIn("ACTIVE (INTO DARKNESS)", res.stdout)
        # save_theme was called with the canonical void-out id.
        self.assertIn("into_darkness_void_out", res.stdout.splitlines())

    def test_picker_row_marks_the_theme_active(self):
        text = MGR_SH.read_text(encoding="utf-8")
        self.assertIn(
            'into_darkness_void_out)         m22=" ${_tw}★ ACTIVE (INTO DARKNESS)${NC}"',
            text,
        )


if __name__ == "__main__":
    unittest.main()
