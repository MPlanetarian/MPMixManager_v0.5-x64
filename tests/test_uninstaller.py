#!/usr/bin/env python3
"""The uninstaller resets or removes apps, and loops the exit movie until it finishes.

Music, mixes, playlists and the install tree stay. The exit-video window is
opened when the work starts and closed when the uninstaller process exits,
and a player that quits early is started again so the movie keeps going.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import mp_app_selection as selection  # noqa: E402
import uninstall_apps  # noqa: E402
from install_desktop_shortcuts import SHORTCUT_NAMES, selected_names  # noqa: E402

UNINSTALL = ROOT / "uninstall.sh"
LOADER = ROOT / "scripts" / "display_loader_video.sh"


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class UninstallFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mp-uninstall-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.config = self.tmp / "config"
        self.desktop = self.tmp / "Desktop"
        self.menu = self.tmp / "applications"
        self.home = self.tmp / "home"
        self.config.mkdir()
        self.desktop.mkdir()
        self.menu.mkdir()
        (self.config / "installed_apps").write_text(
            "manager gui liquid visualizer\n", encoding="utf-8"
        )
        _write(self.config / "liquid_gui_window.json", "{}")
        _write(self.config / "liquid_player_window.json", "{}")
        _write(self.config / "music_folders.json", "{}")
        _write(self.config / "favorites.json", "{}")
        _write(self.desktop / "MP Gui.desktop", "[Desktop Entry]\n")
        _write(self.desktop / "MP Liquid Player.desktop", "[Desktop Entry]\n")
        _write(self.menu / "MP Gui.desktop", "[Desktop Entry]\n")
        _write(
            self.home / ".config" / "autostart" / "mp-mix-manager-tray.desktop",
            "[Desktop Entry]\n",
        )

    def _apply(self, mode, apps, delete_config=False, dry_run=False):
        return uninstall_apps.apply_changes(
            mode=mode,
            targets=uninstall_apps.parse_targets(apps),
            delete_config=delete_config,
            config_dir=self.config,
            desktop=self.desktop,
            apps_dir=self.menu,
            home=self.home,
            root=ROOT,
            dry_run=dry_run,
        )

    def test_uninstall_one_app_keeps_its_settings_and_the_others(self):
        remain = self._apply("uninstall", "gui", delete_config=False)
        self.assertEqual(remain, ["manager", "liquid", "visualizer"])
        self.assertFalse((self.desktop / "MP Gui.desktop").exists())
        self.assertFalse((self.menu / "MP Gui.desktop").exists())
        self.assertTrue((self.desktop / "MP Liquid Player.desktop").is_file())
        self.assertTrue((self.config / "liquid_gui_window.json").is_file())
        self.assertEqual(
            (self.config / "installed_apps").read_text(encoding="utf-8"),
            "manager liquid visualizer\n",
        )
        self.assertTrue(
            (self.home / ".config" / "autostart" / "mp-mix-manager-tray.desktop").is_file()
        )

    def test_delete_config_removes_that_apps_settings_only(self):
        self._apply("uninstall", "gui", delete_config=True)
        self.assertFalse((self.config / "liquid_gui_window.json").exists())
        self.assertTrue((self.config / "liquid_player_window.json").is_file())
        self.assertTrue((self.config / "music_folders.json").is_file())
        self.assertTrue((self.config / "favorites.json").is_file())

    def test_uninstall_everything_keeps_music_and_removes_the_tray(self):
        outside = self.tmp / "outside.txt"
        outside.write_text("keep", encoding="utf-8")
        link = self.config / "liquid_player_window.json"
        link.unlink()
        link.symlink_to(outside)
        _write(self.config / "config.env", "MIX_ARCHIVE_DIR=/data\n")
        remain = self._apply("uninstall", "all", delete_config=True)
        self.assertEqual(remain, [])
        self.assertEqual((self.config / "installed_apps").read_text(encoding="utf-8"), "none\n")
        self.assertFalse((self.config / "config.env").exists())
        self.assertFalse(link.exists())
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep")
        self.assertTrue((self.config / "music_folders.json").is_file())
        self.assertTrue((self.config / "favorites.json").is_file())
        self.assertFalse((self.desktop / "MP Gui.desktop").exists())
        self.assertFalse((self.desktop / "MP Liquid Player.desktop").exists())
        self.assertFalse(
            (self.home / ".config" / "autostart" / "mp-mix-manager-tray.desktop").exists()
        )
        previous = os.environ.get("MP_APP_SELECTION_FILE")
        os.environ["MP_APP_SELECTION_FILE"] = str(self.config / "installed_apps")
        self.addCleanup(
            lambda: os.environ.__setitem__("MP_APP_SELECTION_FILE", previous)
            if previous is not None else os.environ.pop("MP_APP_SELECTION_FILE", None)
        )
        self.assertTrue(selection.file_says_none())
        self.assertEqual(selected_names(), tuple())

    def test_reset_clears_settings_and_leaves_the_shortcut(self):
        remain = self._apply("reset", "liquid")
        self.assertEqual(remain, ["manager", "gui", "liquid", "visualizer"])
        self.assertFalse((self.config / "liquid_player_window.json").exists())
        self.assertTrue((self.config / "liquid_gui_window.json").is_file())
        self.assertTrue((self.desktop / "MP Liquid Player.desktop").is_file())
        self.assertEqual(
            (self.config / "installed_apps").read_text(encoding="utf-8"),
            "manager gui liquid visualizer\n",
        )

    def test_dry_run_changes_nothing(self):
        self._apply("uninstall", "gui", delete_config=True, dry_run=True)
        self.assertTrue((self.desktop / "MP Gui.desktop").is_file())
        self.assertTrue((self.config / "liquid_gui_window.json").is_file())
        self.assertIn("gui", (self.config / "installed_apps").read_text(encoding="utf-8"))

    def test_unknown_app_is_refused(self):
        with self.assertRaises(SystemExit):
            uninstall_apps.parse_targets("gui,not-an-app")

    def test_the_install_tree_is_not_a_config_directory(self):
        with self.assertRaises(SystemExit):
            uninstall_apps.apply_changes(
                mode="uninstall",
                targets=["gui"],
                delete_config=True,
                config_dir=ROOT,
                desktop=self.desktop,
                apps_dir=self.menu,
                home=self.home,
                root=ROOT,
                dry_run=False,
            )

    def test_shared_ui_size_stays_while_the_other_app_stays(self):
        _write(self.config / "liquid_ui_size.1", "7")
        self._apply("uninstall", "gui", delete_config=True)
        self.assertTrue((self.config / "liquid_ui_size.1").is_file())
        self._apply("uninstall", "liquid", delete_config=True)
        self.assertFalse((self.config / "liquid_ui_size.1").exists())


class ExitVideoWhileUninstalling(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mp-uninstall-video-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.config = self.tmp / "config"
        self.desktop = self.tmp / "Desktop"
        self.menu = self.tmp / "applications"
        self.home = self.tmp / "home"
        for path in (self.config, self.desktop, self.menu, self.home):
            path.mkdir()
        (self.config / "installed_apps").write_text("gui\n", encoding="utf-8")
        _write(self.desktop / "MP Gui.desktop", "[Desktop Entry]\n")
        self.log = self.tmp / "ffplay-args.txt"
        self.pids = self.tmp / "ffplay-pids.txt"
        player = self.tmp / "bin" / "ffplay"
        player.parent.mkdir()
        player.write_text(
            textwrap.dedent(
                f"""\
                #!/bin/sh
                printf '%s\\n' "$*" >> "{self.log}"
                echo $$ >> "{self.pids}"
                if [ "${{MP_TEST_FFPLAY_ONCE:-}}" = "1" ]; then
                    exit 0
                fi
                trap 'exit 0' TERM INT
                while true; do sleep 0.2; done
                """
            ),
            encoding="utf-8",
        )
        player.chmod(player.stat().st_mode | stat.S_IEXEC)

    def _env(self, **extra):
        env = os.environ.copy()
        env["PATH"] = f"{self.tmp / 'bin'}:/usr/bin:/bin"
        env["DISPLAY"] = env.get("DISPLAY") or ":99"
        env["SHOW_EXIT_VIDEO"] = "true"
        env["HOME"] = str(self.home)
        env.pop("MP_CONFIG", None)
        env.update(extra)
        return env

    def _run(self, *args, env=None):
        return subprocess.run(
            ["bash", str(UNINSTALL), *args,
             "--config-dir", str(self.config),
             "--desktop", str(self.desktop),
             "--apps-dir", str(self.menu),
             "--home", str(self.home),
             "--no-stop", "--yes"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=env or self._env(),
        )

    def _player_pids(self):
        if not self.pids.is_file():
            return []
        return [line.strip() for line in self.pids.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _alive(self):
        alive = []
        for pid in self._player_pids():
            try:
                os.kill(int(pid), 0)
            except OSError:
                continue
            alive.append(pid)
        return alive

    def test_the_movie_loops_until_the_uninstaller_exits(self):
        res = self._run("--uninstall", "--apps", "gui", "--keep-config")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertIn("Exit video is playing until this finishes", res.stdout)
        self.assertIn("Exit video was playing while the work ran", res.stdout)
        logged = self.log.read_text(encoding="utf-8")
        self.assertIn("-loop", logged)
        self.assertIn("0", logged.split())
        self.assertNotIn("-autoexit", logged)
        self.assertIn("Exit_MP_Video.mp4", logged)
        self.assertFalse((self.desktop / "MP Gui.desktop").exists())
        # The window is gone once the uninstaller has finished.
        deadline = time.time() + 3
        while time.time() < deadline and self._alive():
            time.sleep(0.1)
        self.assertEqual(self._alive(), [])

    def test_a_player_that_exits_is_started_again_until_the_work_ends(self):
        res = self._run(
            "--uninstall", "--apps", "gui", "--keep-config",
            env=self._env(MP_TEST_FFPLAY_ONCE="1", MP_UNINSTALL_WORK_SECONDS="1.2"),
        )
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        launches = [line for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertGreaterEqual(len(launches), 2)
        deadline = time.time() + 3
        while time.time() < deadline and self._alive():
            time.sleep(0.1)
        self.assertEqual(self._alive(), [])
        # It does not keep restarting after the uninstaller has finished.
        after = len(launches)
        time.sleep(0.6)
        later = [line for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(later), after)

    def test_the_switch_and_a_dry_run_open_no_window(self):
        off = self._run(
            "--uninstall", "--apps", "gui", "--keep-config",
            env=self._env(SHOW_EXIT_VIDEO="false"),
        )
        self.assertEqual(off.returncode, 0, off.stderr + off.stdout)
        self.assertIn("was not opened", off.stdout)
        self.assertFalse(self.log.exists())
        self.assertFalse((self.desktop / "MP Gui.desktop").exists())

        _write(self.desktop / "MP Gui.desktop", "[Desktop Entry]\n")
        (self.config / "installed_apps").write_text("gui\n", encoding="utf-8")
        dry = self._run("--dry-run", "--uninstall", "--apps", "gui", "--delete-config")
        self.assertEqual(dry.returncode, 0, dry.stderr + dry.stdout)
        self.assertFalse(self.log.exists())
        self.assertTrue((self.desktop / "MP Gui.desktop").is_file())

    def test_loader_source_loops_every_player(self):
        text = LOADER.read_text(encoding="utf-8")
        self.assertIn("start_uninstall_exit_video", text)
        self.assertIn("stop_uninstall_exit_video", text)
        self.assertIn("-loop 0", text)
        self.assertIn("--loop-file=inf", text)
        self.assertIn("--loop", text)
        swift = (ROOT / "scripts" / "macos_borderless_loader.swift").read_text(encoding="utf-8")
        self.assertIn('args.contains("--loop")', swift)
        self.assertIn("avPlayer.seek(to: .zero)", swift)
        self.assertEqual(len(SHORTCUT_NAMES), 4)


if __name__ == "__main__":
    unittest.main()
