#!/usr/bin/env python3
"""A flagged launch must not repaint the console with the saved theme.

`./Mix_Archive_Manager.sh -v` prints one line and exits. It used to run
load_theme() first, so the window was tinted with the saved theme background
(and, on Konsole, switched to the generated MP_Theme_* colour scheme) before
the version line appeared. Only a plain launch — no arguments at all — owns the
console and paints the theme pane.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MGR_SH = BASE_DIR / "Mix_Archive_Manager.sh"


def _extract(function: str) -> str:
    text = MGR_SH.read_text(encoding="utf-8")
    start = text.index(f"{function}() {{")
    end = text.index("\n}\n", start)
    return text[start:end + 2]


def _run_load_theme(home: Path, args_present: str) -> subprocess.CompletedProcess:
    script = f"""
    set -e
    set_theme_colors() {{ CURRENT_THEME="$1"; THEME_BG_R=1; THEME_BG_G=2; THEME_BG_B=3; }}
    _theme_file_stamp() {{ printf 'stamp'; }}
    _ensure_konsole_default_scheme() {{ printf 'SCHEME-RESET\\n' >> "{home}/calls"; }}
    _apply_cli_theme_background() {{ printf 'PAINT\\n' >> "{home}/calls"; }}
    _MP_CLI_ARGS_PRESENT="{args_present}"
    {_extract("load_theme")}
    load_theme
    """
    env = dict(os.environ, HOME=str(home))
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env
    )


class TestFlaggedLaunchLeavesConsoleAlone(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        cfg = self.home / ".config/mix-manager"
        cfg.mkdir(parents=True)
        (cfg / "theme").write_text("blood_moon\n", encoding="utf-8")
        (self.home / "calls").write_text("", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _calls(self) -> str:
        return (self.home / "calls").read_text(encoding="utf-8")

    def test_flagged_launch_does_not_paint_the_theme(self):
        res = _run_load_theme(self.home, "1")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("PAINT", self._calls())
        # Konsole's scheme reset is part of the paint, so it must not run either:
        # an OSC 50 switch is what actually tinted a Konsole window.
        self.assertNotIn("SCHEME-RESET", self._calls())

    def test_plain_launch_still_paints_the_theme(self):
        res = _run_load_theme(self.home, "0")
        self.assertEqual(res.returncode, 0, res.stderr)
        calls = self._calls()
        self.assertIn("PAINT", calls)
        # Konsole keeps its scheme reset ahead of the paint.
        self.assertLess(calls.index("SCHEME-RESET"), calls.index("PAINT"))


class TestManagerWiring(unittest.TestCase):
    def test_args_are_recorded_before_the_theme_loads(self):
        text = MGR_SH.read_text(encoding="utf-8")
        self.assertIn(
            'if [ "$_MP_LAUNCHED" = "1" ] && [ "$#" -gt 0 ]; then _MP_CLI_ARGS_PRESENT=1; else _MP_CLI_ARGS_PRESENT=0; fi',
            text,
        )
        self.assertLess(
            text.index("_MP_CLI_ARGS_PRESENT=1"),
            text.index("load_theme; _STOP_AUDIO_ON_EXIT=0"),
        )

    def test_a_flagged_menu_paints_the_theme_when_it_opens(self):
        text = MGR_SH.read_text(encoding="utf-8")
        self.assertIn("_paint_theme_background_on_load() {", text)
        submenu = _extract("run_interactive_submenu")
        self.assertIn("_paint_theme_background_on_load", submenu)

    def test_the_paint_runs_once_per_session(self):
        body = _extract("_paint_theme_background_on_load")
        self.assertIn('_CLI_THEME_BG_LOADED:-0}" = "1" ] && return 0', body)
        self.assertIn("_apply_cli_theme_background", body)


if __name__ == "__main__":
    unittest.main()
