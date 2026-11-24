#!/usr/bin/env python3
"""A full uninstall also removes what the installer added outside the tree.

Shortcuts and the tray autostart entry are covered by test_uninstaller.py.
This file covers the rest: services, login agents, icons, the desktop helper,
app bundles, KDE window rules, the MOTD hook and the Windows Startup entry.
A reset must never touch any of it, and no music or library file may go.
"""

from __future__ import annotations

import configparser
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from mp_app_selection import supported_components  # noqa: E402

SUPPORTED = list(supported_components())

import uninstall_apps  # noqa: E402
import uninstall_integration as integration  # noqa: E402


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class IntegrationCleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mp-uninstall-int-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.home = self.tmp / "home"
        self.config = self.home / ".config" / "mix-manager"
        self.home.mkdir()
        self.config.mkdir(parents=True)
        self.messages: list[str] = []

    def _print(self, text: str) -> None:
        self.messages.append(text)

    def _unit(self, name: str) -> Path:
        return self.home / ".config" / "systemd" / "user" / name

    def test_services_and_login_agents_are_removed(self):
        for unit in integration.SYSTEMD_UNITS:
            _write(self._unit(unit), "[Unit]\n")
        for label in integration.LAUNCHD_LABELS:
            _write(self.home / "Library" / "LaunchAgents" / f"{label}.plist", "plist")
        calls: list[list[str]] = []

        integration.remove_services(
            self.home, dry_run=False, printer=self._print, runner=lambda a: calls.append(list(a))
        )

        for unit in integration.SYSTEMD_UNITS:
            self.assertFalse(self._unit(unit).exists(), unit)
        for label in integration.LAUNCHD_LABELS:
            self.assertFalse(
                (self.home / "Library" / "LaunchAgents" / f"{label}.plist").exists(), label
            )
        flat = [" ".join(call) for call in calls]
        self.assertTrue(any("disable --now mp-web-companion.service" in line for line in flat))
        self.assertTrue(any("daemon-reload" in line for line in flat))
        self.assertTrue(any("bootout gui/" in line for line in flat))

    def test_a_full_uninstall_removes_services_icons_and_the_helper(self):
        _write(self._unit("mp-web-companion.service"), "[Unit]\n")
        _write(self.home / ".local/share/icons/hicolor/256x256/apps/mp_liquid_glass_icon.png")
        _write(self.home / ".local/share/icons/hicolor/scalable/apps/mp_liquid_glass_icon.svg")
        helper = self.home / ".local" / "bin" / "mp-desktop-exec"
        helper.parent.mkdir(parents=True)
        helper.symlink_to(ROOT / "bin" / "mp-desktop-exec")
        _write(self.config / "installed_apps", "gui\nmixer=off\n")

        remain = uninstall_apps.apply_changes(
            mode="uninstall",
            targets=["gui"],
            delete_config=False,
            config_dir=self.config,
            desktop=self.tmp / "Desktop",
            apps_dir=self.tmp / "applications",
            home=self.home,
            root=ROOT,
            dry_run=False,
        )

        self.assertEqual(remain, [])
        self.assertFalse(self._unit("mp-web-companion.service").exists())
        self.assertFalse((self.home / ".local/share/icons/hicolor/256x256/apps/mp_liquid_glass_icon.png").exists())
        self.assertFalse((self.home / ".local/share/icons/hicolor/scalable/apps/mp_liquid_glass_icon.svg").exists())
        self.assertFalse(helper.is_symlink() or helper.exists())

    def test_the_helper_is_kept_when_it_points_at_another_tree(self):
        helper = self.home / ".local" / "bin" / "mp-desktop-exec"
        helper.parent.mkdir(parents=True)
        other = self.tmp / "another-install" / "bin" / "mp-desktop-exec"
        _write(other)
        helper.symlink_to(other)

        integration.remove_desktop_helper(
            self.home, ROOT, dry_run=False, printer=self._print
        )

        self.assertTrue(helper.is_symlink())

    def test_a_real_file_at_the_helper_path_is_never_deleted(self):
        helper = self.home / ".local" / "bin" / "mp-desktop-exec"
        _write(helper, "#!/bin/sh\n")

        integration.remove_desktop_helper(
            self.home, ROOT, dry_run=False, printer=self._print
        )

        self.assertTrue(helper.is_file())

    def test_kde_window_rules_are_dropped_and_others_survive(self):
        rc = self.home / ".config" / "kwinrulesrc"
        _write(
            rc,
            "[General]\ncount=2\nrules=ours,theirs\n\n"
            "[ours]\ndescription=Mix Tracklist Viewer (Borderless)\ntitle=Mix Tracklist Viewer\n\n"
            "[theirs]\ndescription=My own rule\ntitle=konsole\n",
        )
        calls: list[list[str]] = []

        integration.remove_kwin_rules(
            self.home, dry_run=False, printer=self._print, runner=lambda a: calls.append(list(a))
        )

        parser = configparser.ConfigParser(interpolation=None)
        parser.read(rc, encoding="utf-8")
        self.assertFalse(parser.has_section("ours"))
        self.assertTrue(parser.has_section("theirs"))
        self.assertEqual(parser.get("General", "rules"), "theirs")
        self.assertEqual(parser.get("General", "count"), "1")
        self.assertTrue(any("reconfigure" in " ".join(call) for call in calls))

    def test_the_motd_hook_is_removed_and_the_rest_of_bashrc_stays(self):
        bashrc = self.home / ".bashrc"
        _write(
            bashrc,
            "export PATH=$PATH:$HOME/.local/bin\n"
            f"{integration.MOTD_BEGIN}\ncat ~/.config/mix-manager/motd\n{integration.MOTD_END}\n"
            "alias ll='ls -l'\n",
        )

        integration.remove_motd_hook(self.home, dry_run=False, printer=self._print)

        text = bashrc.read_text(encoding="utf-8")
        self.assertNotIn("MP Mix Archive Manager Dynamic MOTD", text)
        self.assertIn("alias ll='ls -l'", text)
        self.assertIn("export PATH=", text)

    def test_a_bashrc_without_the_hook_is_untouched(self):
        bashrc = self.home / ".bashrc"
        _write(bashrc, "alias ll='ls -l'\n")
        before = bashrc.read_text(encoding="utf-8")

        integration.remove_motd_hook(self.home, dry_run=False, printer=self._print)

        self.assertEqual(bashrc.read_text(encoding="utf-8"), before)

    def test_only_the_companion_bundle_is_removed(self):
        companion = self.tmp / "Applications" / "MP Web Companion.app"
        _write(
            companion / "Contents" / "Info.plist",
            "<key>CFBundleIdentifier</key>\n<string>com.mplanetarian.mp-web-companion</string>\n",
        )
        other = self.tmp / "Applications" / "Something Else.app"
        _write(
            other / "Contents" / "Info.plist",
            "<key>CFBundleIdentifier</key>\n<string>com.example.other</string>\n",
        )
        original = integration.MAC_BUNDLES
        integration.MAC_BUNDLES = (companion, other)
        self.addCleanup(setattr, integration, "MAC_BUNDLES", original)

        integration.remove_mac_bundles(
            self.home, ROOT, dry_run=False, printer=self._print, runner=lambda a: None
        )

        self.assertFalse(companion.exists())
        self.assertTrue(other.exists())

    def test_windows_startup_entry_is_removed(self):
        startup = (
            self.home / "AppData" / "Roaming" / "Microsoft" / "Windows"
            / "Start Menu" / "Programs" / "Startup"
        )
        for name in integration.WINDOWS_STARTUP_FILES:
            _write(startup / name)
        _write(startup / "Something Else.lnk")

        integration.remove_windows_startup(self.home, dry_run=False, printer=self._print)

        for name in integration.WINDOWS_STARTUP_FILES:
            self.assertFalse((startup / name).exists())
        self.assertTrue((startup / "Something Else.lnk").is_file())

    def test_dry_run_keeps_everything(self):
        _write(self._unit("mp-web-companion.service"), "[Unit]\n")
        _write(self.home / ".local/share/icons/hicolor/256x256/apps/mp_liquid_glass_icon.png")
        bashrc = self.home / ".bashrc"
        _write(bashrc, f"{integration.MOTD_BEGIN}\nhook\n{integration.MOTD_END}\n")
        before = bashrc.read_text(encoding="utf-8")

        integration.remove_all(
            self.home, ROOT, dry_run=True, printer=self._print, runner=lambda a: None
        )

        self.assertTrue(self._unit("mp-web-companion.service").exists())
        self.assertTrue(
            (self.home / ".local/share/icons/hicolor/256x256/apps/mp_liquid_glass_icon.png").is_file()
        )
        self.assertEqual(bashrc.read_text(encoding="utf-8"), before)
        self.assertTrue(any("Would stop" in line or "Would remove" in line for line in self.messages))


class ResetKeepsIntegration(unittest.TestCase):
    def test_a_reset_touches_no_service_or_shell_hook(self):
        tmp = Path(tempfile.mkdtemp(prefix="mp-reset-int-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        home = tmp / "home"
        config = home / ".config" / "mix-manager"
        config.mkdir(parents=True)
        (config / "installed_apps").write_text(
            "manager gui liquid visualizer mixer\n", encoding="utf-8"
        )
        unit = home / ".config" / "systemd" / "user" / "mp-web-companion.service"
        _write(unit, "[Unit]\n")
        bashrc = home / ".bashrc"
        _write(bashrc, f"{integration.MOTD_BEGIN}\nhook\n{integration.MOTD_END}\n")

        remain = uninstall_apps.apply_changes(
            mode="reset",
            targets=["liquid"],
            delete_config=False,
            config_dir=config,
            desktop=tmp / "Desktop",
            apps_dir=tmp / "applications",
            home=home,
            root=ROOT,
            dry_run=False,
        )

        self.assertEqual(remain, SUPPORTED)
        self.assertTrue(unit.exists())
        self.assertIn("Dynamic MOTD", bashrc.read_text(encoding="utf-8"))


class LibraryAndLogs(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mp-uninstall-lib-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.config = self.tmp / "config"
        self.config.mkdir()
        (self.config / "installed_apps").write_text("gui\nmixer=off\n", encoding="utf-8")

    def _apply(self, delete_config: bool):
        return uninstall_apps.apply_changes(
            mode="uninstall",
            targets=["gui"],
            delete_config=delete_config,
            config_dir=self.config,
            desktop=self.tmp / "Desktop",
            apps_dir=self.tmp / "applications",
            home=self.tmp / "home",
            root=ROOT,
            dry_run=False,
        )

    def test_logs_and_runtime_files_go_with_settings(self):
        for name in (
            "mp_system_tray.log",
            "mp_system_tray.keepalive",
            "mp_audio_player.sock",
            "mp_player.pid",
            "liquid_mixer.log",
            "web_companion_stream.json",
            "playback_resume.json",
            "visualizer_idle_saver.json",
        ):
            _write(self.config / name)

        self._apply(delete_config=True)

        for name in (
            "mp_system_tray.log",
            "mp_system_tray.keepalive",
            "mp_audio_player.sock",
            "mp_player.pid",
            "liquid_mixer.log",
            "web_companion_stream.json",
            "playback_resume.json",
            "visualizer_idle_saver.json",
        ):
            self.assertFalse((self.config / name).exists(), name)

    def test_the_library_survives_even_this(self):
        for name in ("music_folders.json", "favorites.json", "top_5_tracks.json"):
            _write(self.config / name)
        for name in ("playlists", "backups", "exports", "PLAYLISTS_GENERATED", "COVERS"):
            _write(self.config / name / "keep.txt")

        self._apply(delete_config=True)

        for name in ("music_folders.json", "favorites.json", "top_5_tracks.json"):
            self.assertTrue((self.config / name).is_file(), name)
        for name in ("playlists", "backups", "exports", "PLAYLISTS_GENERATED", "COVERS"):
            self.assertTrue((self.config / name / "keep.txt").is_file(), name)


if __name__ == "__main__":
    unittest.main()
