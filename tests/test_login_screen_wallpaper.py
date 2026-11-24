#!/usr/bin/env python3
"""The Dreamworlds login-screen background is Centered // Scaled, never Tiled.

Covered greeters:

  GDM            #lockDialogGroup in the patched gnome-shell-theme.gresource
  Plasma Login   /etc/plasmalogin.conf      (FillMode)
  KDE lock       kscreenlockerrc            (FillMode)

FillMode codes from the org.kde.image wallpaper config:
0 Stretch, 1 PreserveAspectFit, 2 PreserveAspectCrop, 3 Tile,
4 TileVertically, 5 TileHorizontally, 6 Pad.

"Centered // Scaled" is 1 (whole picture, proportions kept, centred). Tiled is
3, and Scaled-and-cropped is 2; both were in use before this fix, so the tests
below pin the exact value rather than merely checking "not tiled".
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPT = BASE_DIR / "scripts" / "set_login_screen_wallpaper.sh"
HELPER_PY = BASE_DIR / "scripts" / "set_login_screen_wallpaper.py"
REBUILD = Path("/usr/local/bin/mp-gdm-theme-rebuild")

FILLMODE_CENTERED_SCALED = "1"
FORBIDDEN = {"3": "Tile", "4": "TileVertically", "5": "TileHorizontally", "2": "PreserveAspectCrop"}


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def group_values(text: str, group: str) -> dict[str, str]:
    """Return the key/value pairs of one INI group."""
    values: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1]
            continue
        if current == group and "=" in stripped and not stripped.startswith(("#", ";")):
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip()
    return values


@unittest.skipUnless(REBUILD.is_file(), "the GDM theme rebuild helper is not installed")
class TestGdmLoginBackground(unittest.TestCase):
    """The GDM rule must scale to fit and stay centred."""

    def _rule(self) -> str:
        text = read(REBUILD)
        self.assertIn("#lockDialogGroup", text, "rebuild script must patch the login rule")
        self.assertIn("background-size: contain", text)
        self.assertIn("background-repeat: no-repeat", text)
        self.assertNotIn("background-size: cover", text,
                         "cover crops the picture; Centered // Scaled needs contain")
        self.assertNotIn("background-size: repeat", text)
        return text

    def test_rule_uses_contain_not_cover(self):
        self._rule()

    def test_built_theme_matches_rule(self):
        built = Path("/var/lib/mp-gdm-theme/gnome-shell-theme.gresource")
        if not built.exists():
            self.skipTest("patched gresource not installed on this machine")
        with built.open("rb") as handle:
            data = handle.read()
        rules = set(re.findall(rb"#lockDialogGroup \{[^}]*\}", data))
        self.assertTrue(rules, "no #lockDialogGroup rule embedded")
        for rule in rules:
            text = rule.decode("utf-8", "replace")
            self.assertIn("background-size: contain", text)
            self.assertNotIn("background-size: cover", text)

    def test_wallpaper_is_embedded(self):
        built = Path("/var/lib/mp-gdm-theme/gnome-shell-theme.gresource")
        if not built.exists():
            self.skipTest("patched gresource not installed on this machine")
        with built.open("rb") as handle:
            data = handle.read()
        self.assertIn(b"MPlanetarian_Dreamworlds_Lockscreen.png", data)


class TestKdeLoginBackground(unittest.TestCase):
    """Both KDE layers must carry FillMode=1 (Centered // Scaled)."""

    WP_GROUP = "Greeter][Wallpaper][org.kde.image][General"
    CANDIDATES = (
        Path("/etc/plasmalogin.conf"),
        Path("/etc/xdg/kscreenlockerrc"),
        Path.home() / ".config" / "kscreenlockerrc",
    )

    def _check(self, path: Path):
        if not path.exists():
            self.skipTest(f"{path} not present on this machine")
        values = group_values(read(path), self.WP_GROUP)
        fill = values.get("FillMode")
        if fill is None:
            self.skipTest(f"{path} has no wallpaper FillMode set")
        self.assertEqual(
            fill, FILLMODE_CENTERED_SCALED,
            f"{path} is FillMode={fill}"
            f" ({FORBIDDEN.get(fill, 'not centred/scaled')}); expected 1",
        )

    def test_plasmalogin_is_centered_scaled(self):
        self._check(Path("/etc/plasmalogin.conf"))

    def test_system_xdg_lock_screen_is_centered_scaled(self):
        self._check(Path("/etc/xdg/kscreenlockerrc"))

    def test_user_lock_screen_is_centered_scaled(self):
        self._check(Path.home() / ".config" / "kscreenlockerrc")


class TestHelperScript(unittest.TestCase):
    """The fix script must exist, parse, and report status without root edits."""

    def test_script_is_executable_and_parses(self):
        self.assertTrue(SCRIPT.exists(), f"missing {SCRIPT}")
        self.assertTrue(SCRIPT.stat().st_mode & 0o111, "script is not executable")
        result = subprocess.run(
            ["bash", "-n", str(SCRIPT)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(__import__("sys").platform.startswith("linux"),
                         "the login-screen helper reads Linux greeter state")
    def test_status_mode_reports_positioning(self):
        result = subprocess.run(
            ["bash", str(SCRIPT), "--status"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("GDM", result.stdout)
        self.assertIn("Plasma Login", result.stdout)

    def test_helper_never_writes_tile_modes(self):
        text = read(SCRIPT)
        self.assertIn("FILLMODE=1", text)
        self.assertNotIn("background-size: cover", text)
        helper = read(HELPER_PY)
        self.assertIn('FILLMODE_CENTERED_SCALED = "1"', helper)
        for bad in ("3", "4", "5", "2"):
            self.assertNotIn(f'FillMode": "{bad}"', helper)


if __name__ == "__main__":
    unittest.main()
