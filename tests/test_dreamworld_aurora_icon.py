#!/usr/bin/env python3
"""Every MP app and the tray share the Dreamworld Aurora icon (v0.5).

The shipped icon used to be a generated glass ring. v0.5 replaced it with
**Dreamworld Aurora**, drawn by MPlanetarian, and the whole tree points at it:
the desktop shortcuts, the running apps' window icons, the Linux hicolor
theme, the macOS bundle image and the Windows tray/shortcut icon.

``mp_liquid_glass_icon.*`` is kept as the file *name* every installer and
desktop entry already resolves. These tests lock down that the name now holds
the Aurora artwork, that the artwork cannot be silently regenerated back to
the old glass ring, and that each format (PNG / ICO / SVG) is valid.
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ASSETS = BASE / "assets"
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
if str(BASE / "scripts") not in sys.path:
    sys.path.insert(0, str(BASE / "scripts"))


class TestAuroraIconAssets(unittest.TestCase):
    def test_canonical_artwork_is_shipped(self):
        source = ASSETS / "Dreamworld_Aurora.png"
        self.assertTrue(source.is_file(), "the source artwork must ship")
        png = ASSETS / "dreamworld_aurora.png"
        ico = ASSETS / "dreamworld_aurora.ico"
        svg = ASSETS / "dreamworld_aurora.svg"
        for path in (png, ico, svg):
            self.assertTrue(path.is_file(), f"missing {path.name}")

    def test_png_is_a_512_square(self):
        from PIL import Image

        image = Image.open(ASSETS / "dreamworld_aurora.png")
        self.assertEqual(image.size, (512, 512))
        self.assertEqual(image.mode, "RGBA")

    def test_ico_carries_every_windows_size(self):
        from PIL import Image

        path = ASSETS / "dreamworld_aurora.ico"
        data = path.read_bytes()
        # ICONDIR: reserved(0) type(1) count
        reserved, kind, count = struct.unpack_from("<HHH", data, 0)
        self.assertEqual(reserved, 0)
        self.assertEqual(kind, 1, "must be an icon, not a cursor")
        self.assertGreaterEqual(count, 6, "Windows needs several sizes")

        sizes = set()
        for index in range(count):
            offset = 6 + index * 16
            width, height = data[offset], data[offset + 1]
            sizes.add((width or 256, height or 256))
        for expected in {16, 32, 48, 256}:
            self.assertIn(expected, {w for w, _ in sizes}, f"missing {expected}px")

        image = Image.open(path)
        self.assertEqual(image.size[0], image.size[1])

    def test_svg_wraps_the_artwork(self):
        text = (ASSETS / "dreamworld_aurora.svg").read_text(encoding="utf-8")
        self.assertIn("<svg", text)
        self.assertIn('viewBox="0 0 512 512"', text)
        self.assertIn("Dreamworld Aurora", text)
        self.assertIn("data:image/png;base64,", text)

    def test_legacy_name_holds_the_new_artwork(self):
        """Every installer resolves mp_liquid_glass_icon.*; it must be Aurora."""
        copy = ASSETS / "mp_liquid_glass_icon.png"
        self.assertTrue(copy.is_file())
        self.assertEqual(
            copy.read_bytes(),
            (ASSETS / "dreamworld_aurora.png").read_bytes(),
            "mp_liquid_glass_icon.png must be the Aurora artwork, not the old ring",
        )
        self.assertTrue((ASSETS / "mp_liquid_glass_icon.ico").is_file())
        self.assertTrue((ASSETS / "mp_liquid_glass_icon.svg").is_file())

    def test_windows_tray_fallback_is_the_same_icon(self):
        fallback = ASSETS / "MP_Mix_Manager_Windows_Icon.ico"
        self.assertTrue(fallback.is_file())
        self.assertEqual(
            fallback.read_bytes(),
            (ASSETS / "dreamworld_aurora.ico").read_bytes(),
            "the tray fallback must not show a stale icon",
        )


class TestIconCannotBeRegeneratedAway(unittest.TestCase):
    """The glass-ring generator used to own the shipped icon file names."""

    def test_generator_writes_a_legacy_name_only(self):
        import generate_liquid_glass_icon as icon_script

        self.assertEqual(icon_script.PNG_PATH.name, "mp_liquid_glass_icon_generated.png")
        self.assertEqual(icon_script.SVG_PATH.name, "mp_liquid_glass_icon_generated.svg")

    def test_reward_never_generates_over_the_shipped_icon(self):
        import liquid_glass_reward as reward

        source = Path(reward.__file__).read_text(encoding="utf-8")
        self.assertNotIn("generate_png_icon", source)
        self.assertNotIn("generate_svg_icon", source)
        # It restores from the committed Aurora asset instead.
        self.assertIn("AURORA_PNG", source)
        self.assertEqual(reward.AURORA_PNG.name, "dreamworld_aurora.png")


class TestEveryShortcutUsesOneIcon(unittest.TestCase):
    def test_all_shortcut_targets_share_the_icon(self):
        import install_desktop_shortcuts as shortcuts

        targets = shortcuts.shortcut_targets(BASE)
        self.assertGreaterEqual(len(targets), 5)
        icons = {spec["icon"] for spec in targets.values()}
        self.assertEqual(len(icons), 1, "every MP app must use the same icon")
        self.assertEqual(next(iter(icons)).name, "mp_liquid_glass_icon.png")

    def test_desktop_entries_name_the_icon(self):
        for name in ("MP_Liquid.desktop", "MP_Liquid_Mixer.desktop",
                     "MP_Visualization_Generator.desktop"):
            text = (BASE / name).read_text(encoding="utf-8")
            self.assertIn("Icon=mp_liquid_glass_icon", text, name)
        template = BASE / "desktop" / "MP_Liquid_Glass_Audio_Player.desktop"
        self.assertIn("Icon=mp_liquid_glass_icon", template.read_text(encoding="utf-8"))

    def test_window_icons_resolve_the_shared_png(self):
        for source in ("scripts/liquid_glass_opengl.py", "scripts/liquid_glass_vulkan.py"):
            text = (BASE / source).read_text(encoding="utf-8")
            self.assertIn('"mp_liquid_glass_icon.png"', text, source)

    def test_tray_resolves_the_shared_png(self):
        import mp_system_tray as tray

        resolved = tray.icon_file(BASE)
        self.assertEqual(resolved.name, "mp_liquid_glass_icon.png")
        self.assertTrue(resolved.is_file())


class TestLinuxThemeInstallSizes(unittest.TestCase):
    """The hicolor theme must hold true per-size images, not one 512px file."""

    def test_installer_downscales_each_size(self):
        source = (BASE / "scripts" / "install_desktop_shortcuts.py").read_text(encoding="utf-8")
        self.assertIn("resize((edge, edge)", source)
        self.assertIn("Image.LANCZOS", source)


if __name__ == "__main__":
    unittest.main()
