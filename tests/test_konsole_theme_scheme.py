#!/usr/bin/env python3
"""Konsole gets its theme pane through a generated colour scheme.

Konsole ignores OSC 11, so the manager's background stayed black even after the
theme table changed. Konsole does honour its own OSC 50 colour-scheme switch,
so the manager writes one scheme per theme and selects it. These tests check the
generated scheme and the sequence it sends.
"""

import os
import pty
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MGR_SH = ROOT / "Mix_Archive_Manager.sh"

BASE_SCHEME = """\
[Background]
Color=0,0,0

[BackgroundFaint]
Color=0,0,0

[BackgroundIntense]
Color=0,0,0

[Foreground]
Color=255,255,255

[Color1]
Color=178,24,24

[General]
Description=White on Black
Opacity=1
"""


def _run_in_pty(home: Path, body: str):
    master, slave = pty.openpty()
    script = f"""
    set -e
    eval "$(awk '/^set_theme_colors\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' "{MGR_SH}")"
    eval "$(sed -n '/^_konsole_default_scheme_name() {{/,/^}}/p' "{MGR_SH}")"
    eval "$(sed -n '/^_apply_konsole_theme_scheme() {{/,/^}}/p' "{MGR_SH}")"
    eval "$(sed -n '/^_restore_konsole_theme_scheme() {{/,/^}}/p' "{MGR_SH}")"
    KONSOLE_VERSION=260801
    {body}
    """
    env = dict(os.environ, HOME=str(home))
    try:
        res = subprocess.run(
            ["bash", "-c", script], stdout=slave, stderr=slave, env=env
        )
    finally:
        os.close(slave)
    chunks = []
    try:
        while True:
            data = os.read(master, 4096)
            if not data:
                break
            chunks.append(data)
    except OSError:
        pass
    finally:
        os.close(master)
    if res.returncode != 0:
        raise AssertionError(b"".join(chunks).decode("utf-8", "replace"))
    return b"".join(chunks).decode("utf-8", "replace")


class TestKonsoleThemeScheme(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        konsole = self.home / ".local" / "share" / "konsole"
        konsole.mkdir(parents=True)
        (konsole / "WhiteOnBlack.colorscheme").write_text(BASE_SCHEME, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_dreamworlds_ultra_writes_and_selects_a_purple_scheme(self):
        out = _run_in_pty(self.home, "set_theme_colors dreamworlds_ultra; _apply_konsole_theme_scheme")
        self.assertIn("\033]50;ColorScheme=MP_Theme_dreamworlds_ultra\a", out)
        scheme = self.home / ".local/share/konsole/MP_Theme_dreamworlds_ultra.colorscheme"
        self.assertTrue(scheme.is_file())
        text = scheme.read_text(encoding="utf-8")
        self.assertIn("# MP-THEME-BG: 85,74,107", text)
        self.assertIn("[Background]\nColor=85,74,107", text)
        self.assertIn("[BackgroundFaint]\nColor=85,74,107", text)
        self.assertIn("[BackgroundIntense]\nColor=85,74,107", text)
        self.assertIn("[Foreground]\nColor=226,230,238", text)
        self.assertIn("Description=MP Theme dreamworlds_ultra", text)
        # The user's ANSI palette is preserved, not replaced.
        self.assertIn("[Color1]\nColor=178,24,24", text)

    def test_restore_selects_the_default_scheme_again(self):
        out = _run_in_pty(self.home, "_restore_konsole_theme_scheme")
        self.assertIn("\033]50;ColorScheme=WhiteOnBlack", out)

    def test_scheme_is_reused_once_generated(self):
        _run_in_pty(self.home, "set_theme_colors dreamworlds_ultra; _apply_konsole_theme_scheme")
        scheme = self.home / ".local/share/konsole/MP_Theme_dreamworlds_ultra.colorscheme"
        before = scheme.read_text(encoding="utf-8")
        _run_in_pty(self.home, "set_theme_colors dreamworlds_ultra; _apply_konsole_theme_scheme")
        self.assertEqual(scheme.read_text(encoding="utf-8"), before)


    def test_manager_wires_apply_and_restore(self):
        text = MGR_SH.read_text(encoding="utf-8")
        apply_fn = text[text.index("_apply_cli_theme_background() {"):]
        apply_fn = apply_fn[:apply_fn.index("\n}\n")]
        self.assertIn("_apply_konsole_theme_scheme", apply_fn)
        restore_fn = text[text.index("_restore_cli_theme_background() {"):]
        restore_fn = restore_fn[:restore_fn.index("\n}\n")]
        self.assertIn("_restore_konsole_theme_scheme", restore_fn)


if __name__ == "__main__":
    unittest.main()
