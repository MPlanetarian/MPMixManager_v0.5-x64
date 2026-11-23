#!/usr/bin/env python3
"""First install creates the named desktop shortcuts on every platform.

Linux also gets the MP Liquid Mixer entry, which is deliberately absent on
macOS and Windows.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from install_desktop_shortcuts import (  # noqa: E402
    MAC_APP_EXECUTABLE,
    MAC_ICON_NAME,
    SHORTCUT_NAMES,
    _is_public_windows_folder,
    desktop_dir_for,
    desktop_shortcuts_present,
    ensure_installed,
    install_desktop_shortcuts,
    linux_desktop_entry,
    mac_bundle_identifier,
    mac_finder_icon_present,
    shortcut_targets,
)


class TestDesktopShortcuts(unittest.TestCase):
    def test_there_are_five_named_shortcuts(self):
        self.assertEqual(
            list(SHORTCUT_NAMES),
            [
                "MP Liquid Player",
                "MP Mix Manager",
                "MP Gui",
                "MP Liquid Visualizer",
                "MP Liquid Mixer",
            ],
        )

    def test_linux_install_writes_five_desktop_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp) / "Desktop"
            apps = Path(tmp) / "applications"
            desktop.mkdir()
            (desktop / "MP_Liquid.desktop").write_text("old\n", encoding="utf-8")
            (desktop / "Mix Archive Manager.command").write_text("old\n", encoding="utf-8")
            # install_desktop_shortcuts() writes only the selected apps, and
            # the selection lives in the user's config. Pass all five
            # explicitly so the result does not depend on this machine.
            paths = install_desktop_shortcuts(
                ROOT,
                platform_name="linux",
                desktop_dir=desktop,
                apps_dir=apps,
                launcher_dir=Path(tmp) / "launchers",
                components=["manager", "gui", "liquid", "visualizer", "mixer"],
            )
            self.assertEqual([path.name for path in paths], [f"{name}.desktop" for name in SHORTCUT_NAMES])
            self.assertFalse((desktop / "MP_Liquid.desktop").exists())
            self.assertFalse((desktop / "Mix Archive Manager.command").exists())

            player = (desktop / "MP Liquid Player.desktop").read_text(encoding="utf-8")
            self.assertIn("Name=MP Liquid Player", player)
            self.assertIn("launch_liquid_glass.sh", player)
            self.assertNotIn("--play", player)
            self.assertIn("Terminal=false", player)

            manager = (desktop / "MP Mix Manager.desktop").read_text(encoding="utf-8")
            self.assertIn("Name=MP Mix Manager", manager)
            self.assertIn("Mix_Archive_Manager.sh", manager)
            self.assertNotIn("manager.sh", manager)
            self.assertIn("Terminal=true", manager)

            gui = (desktop / "MP Gui.desktop").read_text(encoding="utf-8")
            self.assertIn("Name=MP Gui", gui)
            self.assertIn("MP_Gui.sh", gui)
            self.assertIn("Terminal=false", gui)

            visualizer = (desktop / "MP Liquid Visualizer.desktop").read_text(encoding="utf-8")
            self.assertIn("Name=MP Liquid Visualizer", visualizer)
            self.assertIn("launch_liquid_visualizer.sh", visualizer)
            self.assertIn("Terminal=false", visualizer)

            mixer = (desktop / "MP Liquid Mixer.desktop").read_text(encoding="utf-8")
            self.assertIn("Name=MP Liquid Mixer", mixer)
            self.assertIn("launch_liquid_mixer.sh", mixer)
            self.assertIn("Icon=mp_liquid_glass_icon", mixer)
            self.assertIn("Terminal=false", mixer)

            for name in SHORTCUT_NAMES:
                menu = apps / f"{name}.desktop"
                self.assertTrue(menu.is_file(), name)
                self.assertIn(f"Name={name}", menu.read_text(encoding="utf-8"))

    def test_macos_gui_shortcuts_are_apps_and_manager_stays_a_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp) / "Desktop"
            apps = Path(tmp) / "Applications"
            desktop.mkdir()
            apps.mkdir()
            (desktop / "MP Gui.command").write_text("old\n", encoding="utf-8")
            (apps / "MP Liquid Player.command").write_text("old\n", encoding="utf-8")
            mac_components = ["manager", "gui", "liquid", "visualizer"]
            self.assertFalse(desktop_shortcuts_present(desktop, "macos", mac_components))
            written = install_desktop_shortcuts(
                ROOT,
                platform_name="macos",
                desktop_dir=desktop,
                apps_dir=apps,
                launcher_dir=Path(tmp) / "launchers",
                components=["manager", "gui", "liquid", "visualizer"],
            )
            self.assertEqual(
                [path.name for path in written],
                [
                    "MP Liquid Player.app",
                    "MP Mix Manager.command",
                    "MP Gui.app",
                    "MP Liquid Visualizer.app",
                ],
            )
            self.assertEqual([path.name for path in desktop.glob("*.command")], ["MP Mix Manager.command"])
            self.assertFalse((desktop / "MP Gui.command").exists())
            self.assertFalse((apps / "MP Liquid Player.command").exists())
            self.assertFalse((desktop / "MP Mix Manager.app").exists())

            manager = (desktop / "MP Mix Manager.command").read_text(encoding="utf-8")
            self.assertIn("Mix_Archive_Manager.sh", manager)
            self.assertIn("/opt/homebrew/bin/bash", manager)
            self.assertTrue(os.access(desktop / "MP Mix Manager.command", os.X_OK))
            self.assertTrue((apps / "MP Mix Manager.command").is_file())

            gui_apps = ("MP Liquid Player", "MP Gui", "MP Liquid Visualizer")
            expected = {
                "MP Liquid Player": "launch_liquid_glass.sh",
                "MP Gui": "MP_Gui.sh",
                "MP Liquid Visualizer": "launch_liquid_visualizer.sh",
            }
            for name in gui_apps:
                for directory in (desktop, apps):
                    app = directory / f"{name}.app"
                    exe = app / "Contents" / "MacOS" / MAC_APP_EXECUTABLE
                    plist = (app / "Contents" / "Info.plist").read_text(encoding="utf-8")
                    script = exe.read_text(encoding="utf-8")
                    self.assertTrue(os.access(exe, os.X_OK), name)
                    self.assertIn(expected[name], script)
                    self.assertNotIn("Terminal", script)
                    self.assertNotIn("--play", script)
                    self.assertIn(f"<string>{name}</string>", plist)
                    self.assertIn(mac_bundle_identifier(name), plist)
                    self.assertIn(f"<string>{MAC_APP_EXECUTABLE}</string>", plist)
                    if sys.platform == "darwin":
                        icns = app / "Contents" / "Resources" / f"{MAC_ICON_NAME}.icns"
                        self.assertTrue(icns.is_file() and icns.stat().st_size > 0, name)
                        self.assertIn("CFBundleIconFile", plist)
                        self.assertIn(MAC_ICON_NAME, plist)
                        self.assertTrue(mac_finder_icon_present(app), name)
            if sys.platform == "darwin":
                self.assertTrue(mac_finder_icon_present(desktop / "MP Mix Manager.command"))
                self.assertTrue(mac_finder_icon_present(apps / "MP Mix Manager.command"))
            self.assertTrue(desktop_shortcuts_present(desktop, "macos", mac_components))

    def test_liquid_player_desktop_names_its_wm_class(self):
        """KDE needs StartupWMClass=MP_Liquid to use the real taskbar icon."""
        spec = shortcut_targets(ROOT)["MP Liquid Player"]
        text = linux_desktop_entry("MP Liquid Player", spec, ROOT)
        self.assertIn("StartupWMClass=MP_Liquid", text)
        self.assertIn("Icon=mp_liquid_glass_icon", text)

    def test_linux_desktop_exec_survives_the_two_pass_keyfile_parse(self):
        """The Exec line must launch, not just pass desktop-file-validate.

        An Exec value is unescaped twice: once as a key-file string, then as
        the argument list. A single backslash before "$" or '"' validates but
        makes GLib refuse the entry, so the icon does nothing when clicked.
        """
        for name in SHORTCUT_NAMES:
            spec = shortcut_targets(ROOT)[name]
            text = linux_desktop_entry(name, spec, ROOT)
            exec_line = next(
                line[len("Exec="):] for line in text.splitlines() if line.startswith("Exec=")
            )
            with self.subTest(shortcut=name):
                # Two backslashes are required before $ and " in the file.
                self.assertNotIn('\\$', exec_line.replace('\\\\$', ""))
                self.assertNotIn('\\"', exec_line.replace('\\\\"', ""))
                self.assertIn('\\\\$HOME/.local/bin/mp-desktop-exec\\\\"', exec_line)
                # And the entry must survive an independent parse.
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / f"{name}.desktop"
                    path.write_text(text, encoding="utf-8")
                    parsed = subprocess.run(
                        ["desktop-file-validate", str(path)],
                        capture_output=True,
                        text=True,
                    )
                    errors = [
                        line
                        for line in parsed.stderr.splitlines()
                        if "error:" in line and "Categories" not in line
                    ]
                    self.assertEqual(errors, [], f"{name} has an invalid Exec line")

    def test_desktop_shortcuts_present_rejects_a_broken_exec(self):
        """A shortcut that exists but cannot load counts as missing."""
        from install_desktop_shortcuts import linux_shortcut_is_healthy

        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp)
            broken = (
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=MP Gui\n"
                'Exec=sh -c "exec \\"\\$HOME/.local/bin/mp-desktop-exec\\" MP_Gui.sh"\n'
                "TryExec=mp-desktop-exec\n"
                "Terminal=false\n"
            )
            for name in SHORTCUT_NAMES:
                (desktop / f"{name}.desktop").write_text(broken, encoding="utf-8")
            self.assertFalse(
                desktop_shortcuts_present(desktop, "linux", list(SHORTCUT_NAMES)),
                "a shortcut with an unloadable Exec must be treated as absent",
            )
            self.assertFalse(linux_shortcut_is_healthy(desktop / f"{SHORTCUT_NAMES[0]}.desktop"))

    def test_desktop_shortcuts_present_rejects_a_dangling_try_exec(self):
        """A TryExec that names a dead install must count as missing.

        Leaving an OpenCode worktree, or unloading a versioned folder, leaves
        ~/.local/bin/mp-desktop-exec as a symlink to a path that no longer
        exists. GLib still finds the link, but cannot run it, so it hides the
        entry and clicking the icon does nothing. The link must be followed and
        checked, otherwise every shortcut looks healthy and is never repaired.
        """
        from install_desktop_shortcuts import linux_shortcut_is_healthy

        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp) / "Desktop"
            desktop.mkdir()
            dead_tree = Path(tmp) / "MPMixManager_v9.9-x64"
            dead_helper = dead_tree / "bin" / "mp-desktop-exec"
            local_bin = Path(tmp) / ".local" / "bin"
            local_bin.mkdir(parents=True)
            helper = local_bin / "mp-desktop-exec"
            helper.symlink_to(dead_helper)

            entry = (
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=MP Gui\n"
                'Exec=sh -c "exec \\\\"\\\\$HOME/.local/bin/mp-desktop-exec\\\\" MP_Gui.sh"\n'
                f"TryExec={helper}\n"
                "Terminal=false\n"
            )
            for name in SHORTCUT_NAMES:
                (desktop / f"{name}.desktop").write_text(entry, encoding="utf-8")

            self.assertFalse(
                linux_shortcut_is_healthy(desktop / f"{SHORTCUT_NAMES[0]}.desktop"),
                "a TryExec pointing at a deleted tree must be unhealthy",
            )
            self.assertFalse(
                desktop_shortcuts_present(desktop, "linux", list(SHORTCUT_NAMES)),
                "shortcuts whose TryExec cannot run must be treated as absent",
            )

            # A resolvable helper makes the same entry healthy again, so the
            # repair runs once and does not loop.
            dead_helper.parent.mkdir(parents=True)
            dead_helper.write_text("#!/bin/sh\n", encoding="utf-8")
            dead_helper.chmod(0o755)
            self.assertTrue(linux_shortcut_is_healthy(desktop / f"{SHORTCUT_NAMES[0]}.desktop"))
            self.assertTrue(desktop_shortcuts_present(desktop, "linux", list(SHORTCUT_NAMES)))

    def test_linux_try_exec_is_absolute_not_a_bare_name(self):
        """TryExec must not rely on PATH.

        A desktop session's PATH is /usr/local/sbin:/usr/local/bin:/usr/bin,
        which does not include ~/.local/bin. A bare name there fails the
        TryExec check, and GLib then refuses to load the entry at all.
        """
        for name in SHORTCUT_NAMES:
            spec = shortcut_targets(ROOT)[name]
            text = linux_desktop_entry(name, spec, ROOT)
            try_exec = next(
                line[len("TryExec="):]
                for line in text.splitlines()
                if line.startswith("TryExec=")
            )
            with self.subTest(shortcut=name):
                self.assertTrue(try_exec.startswith("/"), try_exec)
                self.assertNotIn("$", try_exec)

    def test_shipped_desktop_files_do_not_use_single_backslash_escapes(self):
        """Every checked-in .desktop file must avoid the broken escaping."""
        shipped = sorted(ROOT.glob("*.desktop")) + sorted((ROOT / "desktop").glob("*.desktop"))
        for path in shipped:
            text = path.read_text(encoding="utf-8")
            if "mp-desktop-exec" not in text:
                continue
            exec_line = next(
                line[len("Exec="):] for line in text.splitlines() if line.startswith("Exec=")
            )
            with self.subTest(path=path.name):
                self.assertNotIn('\\$', exec_line.replace('\\\\$', ""), path.name)
                self.assertNotIn('\\"', exec_line.replace('\\\\"', ""), path.name)
                # A bare TryExec makes GLib hide the entry from the desktop.
                try_exec = [
                    line[len("TryExec="):]
                    for line in text.splitlines()
                    if line.startswith("TryExec=")
                ]
                for value in try_exec:
                    self.assertFalse(
                        ("/" not in value) or ("$" in value),
                        f"{path.name}: TryExec={value} cannot resolve in a desktop session",
                    )

    def test_windows_install_links_the_four_launchers(self):
        created = []

        def writer(path, target, arguments, workdir, description, icon):
            created.append((Path(path), Path(target).name, arguments, description, Path(icon).name))
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("shortcut\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp) / "Desktop"
            apps = Path(tmp) / "Programs"
            launchers = Path(tmp) / "launchers"
            install_desktop_shortcuts(
                ROOT,
                platform_name="windows",
                desktop_dir=desktop,
                apps_dir=apps,
                launcher_dir=launchers,
                link_writer=writer,
                components=["manager", "gui", "liquid", "visualizer"],
            )
            desktop_links = [item for item in created if item[0].parent == desktop]
            windows_names = [name for name in SHORTCUT_NAMES if name != "MP Liquid Mixer"]
            self.assertEqual([item[0].name for item in desktop_links], [f"{name}.lnk" for name in windows_names])
            by_name = {item[0].name: item for item in desktop_links}
            self.assertEqual(by_name["MP Liquid Player.lnk"][1], "launch_liquid_glass.bat")
            self.assertTrue(all(item[4] == "mp_liquid_glass_icon.png" for item in created))
            self.assertEqual(by_name["MP Mix Manager.lnk"][1], "manager.bat")
            self.assertEqual(by_name["MP Gui.lnk"][1], "launch_mp_gui.bat")
            self.assertIn("windows_find_python.cmd", (ROOT / "scripts" / "launch_mp_gui.bat").read_text(encoding="utf-8"))
            self.assertIn("--manager", (ROOT / "scripts" / "launch_mp_gui.bat").read_text(encoding="utf-8"))
            visualizer_cmd = (launchers / "MP Liquid Visualizer.cmd").read_text(encoding="utf-8")
            self.assertIn("MP_Liquid_Visualizer.py", visualizer_cmd)
            self.assertNotIn("--play", visualizer_cmd)
            self.assertEqual(len(list(apps.glob("*.lnk"))), 4)

    def test_windows_desktop_falls_back_to_the_user_profile(self):
        with patch.dict(os.environ, {"USERPROFILE": r"C:\Users\Ada"}, clear=False), \
             patch("install_desktop_shortcuts._windows_special_folder", return_value=None):
            self.assertEqual(desktop_dir_for("windows"), Path(r"C:\Users\Ada") / "Desktop")

    def test_public_desktop_is_not_the_user_profile(self):
        self.assertTrue(_is_public_windows_folder(r"C:\Users\Public\Desktop"))
        self.assertFalse(_is_public_windows_folder(r"C:\Users\Administrator\Desktop"))

    def test_ensure_installs_only_when_a_shortcut_is_missing(self):
        with patch("install_desktop_shortcuts.desktop_shortcuts_present", return_value=True), \
             patch("install_desktop_shortcuts.install_desktop_shortcuts") as install:
            self.assertFalse(ensure_installed(ROOT))
            install.assert_not_called()
        with patch("install_desktop_shortcuts.desktop_shortcuts_present", return_value=False), \
             patch("install_desktop_shortcuts.desktop_dir_for", return_value=Path("/tmp/mp-shortcuts-unused")), \
             patch("install_desktop_shortcuts.install_desktop_shortcuts", return_value=[]) as install:
            self.assertTrue(ensure_installed(ROOT))
            install.assert_called_once()


if __name__ == "__main__":
    unittest.main()
