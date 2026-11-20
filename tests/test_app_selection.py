#!/usr/bin/env python3
"""The installer can set up all five MP apps, or only one of them.

The choice is stored once (~/.config/mix-manager/installed_apps) and every
surface reads it: the Desktop shortcuts, the application menu, the shared tray
menu and the Web Companion launch pad. These tests pin the parser, the stored
file, and each consumer, so a one-app install cannot quietly advertise the
apps it did not set up. The Linux-only mixer is a real fifth choice, including
on its own; macOS and Windows filter it out.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import mp_app_selection as selection  # noqa: E402
import mp_suite_control as suite_control  # noqa: E402
import mp_system_tray as tray  # noqa: E402
from install_desktop_shortcuts import (  # noqa: E402
    SHORTCUT_NAMES,
    desktop_shortcuts_present,
    install_desktop_shortcuts,
    selected_names,
)

INSTALL = ROOT / "install.sh"
POWERSHELL = ROOT / "scripts" / "windows_user_integration.ps1"
WINDOWS_TRAY = ROOT / "scripts" / "windows_tray.ps1"


class SelectionBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mp-app-selection-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.state = self.tmp / "installed_apps"
        patcher = patch.object(selection, "state_path", return_value=self.state)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(
            setattr, os.environ, "MP_APP_SELECTION_FILE",
            os.environ.get("MP_APP_SELECTION_FILE", ""),
        )
        os.environ["MP_APP_SELECTION_FILE"] = str(self.state)


class TestParser(unittest.TestCase):
    def test_all_is_the_canonical_order(self):
        self.assertEqual(
            list(selection.COMPONENTS),
            ["manager", "gui", "liquid", "visualizer", "mixer"],
        )
        self.assertEqual(selection.parse("all"), list(selection.COMPONENTS))

    def test_a_missing_or_empty_answer_means_all_supported(self):
        for value in (None, "", "   ", "nonsense"):
            with self.subTest(value=value):
                self.assertEqual(selection.parse(value), list(selection.COMPONENTS))

    def test_one_app_accepts_every_spelling_the_ui_can_produce(self):
        for value in ("gui", "MP Gui", "mp-gui", "mp_gui"):
            with self.subTest(value=value):
                self.assertEqual(selection.parse(value), ["gui"])
        for value in ("liquid", "player", "MP Liquid Player"):
            with self.subTest(value=value):
                self.assertEqual(selection.parse(value), ["liquid"])
        for value in ("mixer", "MP Liquid Mixer", "mp-liquid-mixer", "mp_liquid_mixer", "dj"):
            with self.subTest(value=value):
                self.assertEqual(selection.parse(value), ["mixer"])

    def test_the_mixer_is_linux_only(self):
        self.assertEqual(selection.parse("mixer", "linux"), ["mixer"])
        self.assertEqual(selection.parse("mixer", "macos"), [])
        self.assertEqual(selection.parse("mixer", "windows"), [])
        self.assertEqual(
            selection.parse("all", "macos"),
            ["manager", "gui", "liquid", "visualizer"],
        )
        self.assertNotIn("mixer", selection.supported_components("windows"))

    def test_several_apps_in_a_list(self):
        self.assertEqual(selection.parse("gui,liquid"), ["gui", "liquid"])
        self.assertEqual(selection.parse("liquid gui"), ["gui", "liquid"])
        self.assertEqual(selection.parse("visualizer;manager"), ["manager", "visualizer"])
        self.assertEqual(selection.parse("gui,mixer"), ["gui", "mixer"])

    def test_the_display_labels_are_the_shortcut_names(self):
        self.assertEqual(
            selection.labels(["liquid", "manager"]),
            ["MP Mix Manager", "MP Liquid Player"],
        )
        self.assertEqual(selection.summary(["gui"]), "MP Gui")
        self.assertEqual(selection.summary(), "all five MP applications")
        self.assertEqual(selection.summary(["gui", "mixer"]), "MP Gui and MP Liquid Mixer")


class TestStateFile(SelectionBase):
    def test_a_missing_file_reads_as_all_supported(self):
        self.assertFalse(self.state.exists())
        self.assertEqual(selection.read(), list(selection.COMPONENTS))

    def test_write_stores_one_line_in_canonical_order(self):
        path = selection.write(["liquid", "gui"])
        self.assertEqual(path, self.state)
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            "gui liquid\nmixer=off\n",
        )
        self.assertEqual(selection.read(), ["gui", "liquid"])
        self.assertTrue(selection.enabled("gui"))
        self.assertFalse(selection.enabled("manager"))
        self.assertFalse(selection.enabled("mixer"))

    def test_a_legacy_file_keeps_the_mixer_on_linux(self):
        self.state.write_text("gui\n", encoding="utf-8")
        self.assertEqual(selection.read(), ["gui", "mixer"])
        self.state.write_text("gui\nmixer=off\n", encoding="utf-8")
        self.assertEqual(selection.read(), ["gui"])

    def test_the_mixer_can_be_written_alone(self):
        selection.write(["mixer"])
        self.assertEqual(self.state.read_text(encoding="utf-8"), "mixer\n")
        self.assertEqual(selection.read(), ["mixer"])

    def test_an_empty_write_is_not_an_empty_install(self):
        selection.write([])
        self.assertEqual(selection.read(), list(selection.COMPONENTS))

    def test_none_is_an_explicit_empty_install(self):
        selection.write([], allow_empty=True)
        self.assertEqual(self.state.read_text(encoding="utf-8"), "none\n")
        self.assertEqual(selection.read(), [])
        self.assertTrue(selection.file_says_none())
        self.assertEqual(selected_names(), tuple())
        self.assertFalse(selection.enabled("gui"))

    def test_identify_accepts_one_app_and_rejects_all(self):
        self.assertEqual(selection.identify("MP Gui"), "gui")
        self.assertEqual(selection.identify("player"), "liquid")
        self.assertIsNone(selection.identify("all"))
        self.assertIsNone(selection.identify("nope"))

    def test_the_cli_sets_and_queries_the_selection(self):
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(selection.main(["--set", "gui"]), 0)
            self.assertEqual(selection.main(["--enabled", "gui"]), 0)
            self.assertEqual(selection.main(["--enabled", "liquid"]), 1)
        self.assertEqual(self.state.read_text(encoding="utf-8"), "gui\nmixer=off\n")


class TestShortcutsFollowTheSelection(SelectionBase):
    def test_all_five_still_writes_every_shortcut(self):
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp) / "Desktop"
            paths = install_desktop_shortcuts(
                ROOT,
                platform_name="linux",
                desktop_dir=desktop,
                apps_dir=Path(tmp) / "applications",
                launcher_dir=Path(tmp) / "launchers",
                components=list(selection.COMPONENTS),
            )
            self.assertEqual(
                [path.name for path in paths],
                [f"{name}.desktop" for name in SHORTCUT_NAMES],
            )

    def test_one_app_writes_only_that_shortcut(self):
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp) / "Desktop"
            apps = Path(tmp) / "applications"
            paths = install_desktop_shortcuts(
                ROOT,
                platform_name="linux",
                desktop_dir=desktop,
                apps_dir=apps,
                launcher_dir=Path(tmp) / "launchers",
                components=["gui"],
            )
            self.assertEqual([path.name for path in paths], ["MP Gui.desktop"])
            self.assertEqual(
                sorted(p.name for p in desktop.glob("*.desktop")),
                ["MP Gui.desktop"],
            )
            self.assertEqual(
                sorted(p.name for p in apps.glob("*.desktop")),
                ["MP Gui.desktop"],
            )

    def test_the_mixer_can_be_installed_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp) / "Desktop"
            apps = Path(tmp) / "applications"
            paths = install_desktop_shortcuts(
                ROOT,
                platform_name="linux",
                desktop_dir=desktop,
                apps_dir=apps,
                launcher_dir=Path(tmp) / "launchers",
                components=["mixer"],
            )
            self.assertEqual([path.name for path in paths], ["MP Liquid Mixer.desktop"])
            self.assertEqual(
                sorted(p.name for p in desktop.glob("*.desktop")),
                ["MP Liquid Mixer.desktop"],
            )
            self.assertEqual(
                sorted(p.name for p in apps.glob("*.desktop")),
                ["MP Liquid Mixer.desktop"],
            )

    def test_the_mixer_is_not_written_on_macos(self):
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp) / "Desktop"
            paths = install_desktop_shortcuts(
                ROOT,
                platform_name="macos",
                desktop_dir=desktop,
                apps_dir=Path(tmp) / "Applications",
                launcher_dir=Path(tmp) / "launchers",
                components=["mixer"],
            )
            self.assertEqual(paths, [])
            self.assertEqual(list(desktop.glob("*")) if desktop.exists() else [], [])

    def test_switching_to_one_app_removes_the_other_four(self):
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp) / "Desktop"
            apps = Path(tmp) / "applications"
            install_desktop_shortcuts(
                ROOT,
                platform_name="linux",
                desktop_dir=desktop,
                apps_dir=apps,
                launcher_dir=Path(tmp) / "launchers",
                components=list(selection.COMPONENTS),
            )
            self.assertEqual(len(list(desktop.glob("*.desktop"))), 5)
            install_desktop_shortcuts(
                ROOT,
                platform_name="linux",
                desktop_dir=desktop,
                apps_dir=apps,
                launcher_dir=Path(tmp) / "launchers",
                components=["liquid"],
            )
            self.assertEqual(
                sorted(p.name for p in desktop.glob("*.desktop")),
                ["MP Liquid Player.desktop"],
            )
            self.assertFalse((apps / "MP Gui.desktop").exists())

    def test_the_stored_selection_is_used_when_none_is_passed(self):
        selection.write(["visualizer"])
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp) / "Desktop"
            paths = install_desktop_shortcuts(
                ROOT,
                platform_name="linux",
                desktop_dir=desktop,
                apps_dir=Path(tmp) / "applications",
                launcher_dir=Path(tmp) / "launchers",
            )
            self.assertEqual(
                [path.name for path in paths],
                ["MP Liquid Visualizer.desktop"],
            )

    def test_presence_check_only_requires_the_selected_apps(self):
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp) / "Desktop"
            install_desktop_shortcuts(
                ROOT,
                platform_name="linux",
                desktop_dir=desktop,
                apps_dir=Path(tmp) / "applications",
                launcher_dir=Path(tmp) / "launchers",
                components=["gui"],
            )
            self.assertTrue(desktop_shortcuts_present(desktop, "linux", ["gui"]))
            self.assertFalse(desktop_shortcuts_present(desktop, "linux", list(selection.COMPONENTS)))

    def test_selected_names_keeps_display_order(self):
        self.assertEqual(
            selected_names(["visualizer", "manager"]),
            ("MP Mix Manager", "MP Liquid Visualizer"),
        )
        self.assertEqual(
            selected_names(["visualizer", "manager"], "macos"),
            ("MP Mix Manager", "MP Liquid Visualizer"),
        )
        self.assertEqual(selected_names(["mixer"]), ("MP Liquid Mixer",))
        self.assertEqual(selected_names(["mixer"], "macos"), ())
        self.assertEqual(selected_names([]), SHORTCUT_NAMES)
        self.assertEqual(
            selected_names([], "macos"),
            tuple(name for name in SHORTCUT_NAMES if name != "MP Liquid Mixer"),
        )

    def test_windows_shortcuts_receive_the_selection(self):
        created = []

        def writer(path, target, arguments, workdir, description, icon):
            created.append(Path(path))
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("shortcut\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp) / "Desktop"
            install_desktop_shortcuts(
                ROOT,
                platform_name="windows",
                desktop_dir=desktop,
                apps_dir=Path(tmp) / "Programs",
                launcher_dir=Path(tmp) / "launchers",
                link_writer=writer,
                components=["manager"],
            )
        on_desktop = [path.name for path in created if path.parent == desktop]
        self.assertEqual(on_desktop, ["MP Mix Manager.lnk"])


class TestTrayMenuFollowsTheSelection(SelectionBase):
    def test_a_full_install_keeps_the_whole_menu(self):
        selection.write(list(selection.COMPONENTS))
        ids = [ident for ident, _label in tray.menu_entries()]
        self.assertEqual(
            ids,
            [
                "launch-gui",
                "launch-liquid",
                "launch-mixer",
                "launch-manager",
                "launch-visualizer",
                "web-audio",
                "rescan-archive",
                "rescan-music",
                "mute-audio",
                "close-all",
            ],
        )

    def test_a_one_app_install_hides_the_other_launchers(self):
        selection.write(["gui"])
        ids = [ident for ident, _label in tray.menu_entries()]
        self.assertEqual(ids, ["launch-gui", "web-audio", "rescan-archive", "rescan-music", "mute-audio", "close-all"])

    def test_a_mixer_only_install_offers_the_mixer(self):
        selection.write(["mixer"])
        ids = [ident for ident, _label in tray.menu_entries()]
        self.assertEqual(ids, ["launch-mixer", "web-audio", "rescan-archive", "rescan-music", "mute-audio", "close-all"])

    def test_the_rescan_and_mute_entries_survive_every_selection(self):
        for chosen in (["manager"], ["gui"], ["liquid"], ["visualizer"], ["mixer"], ["gui", "liquid"]):
            with self.subTest(components=chosen):
                selection.write(chosen)
                ids = [ident for ident, _label in tray.menu_entries()]
                for ident in ("rescan-archive", "rescan-music", "mute-audio"):
                    self.assertIn(ident, ids)

    def test_close_all_and_the_web_entry_survive_every_selection(self):
        for chosen in (["manager"], ["gui"], ["liquid"], ["visualizer"], ["mixer"], ["gui", "liquid"]):
            with self.subTest(components=chosen):
                selection.write(chosen)
                ids = [ident for ident, _label in tray.menu_entries()]
                self.assertIn("close-all", ids)
                self.assertIn("web-audio", ids)

    def test_the_menu_is_never_empty(self):
        selection.write(["visualizer"])
        self.assertTrue(tray.menu_entries())


class TestWebCompanionFollowsTheSelection(SelectionBase):
    def test_only_selected_apps_get_a_launch_button(self):
        selection.write(["gui"])
        self.assertEqual([app["id"] for app in suite_control.app_definitions()], ["gui"])

    def test_a_mixer_only_install_gets_its_launch_button(self):
        selection.write(["mixer"])
        self.assertEqual([app["id"] for app in suite_control.app_definitions()], ["mixer"])
        with patch.object(suite_control, "is_supported", return_value=True), \
             patch.object(suite_control, "tty_mode_active", return_value=False), \
             patch.object(suite_control, "_spawn") as spawn:
            result = suite_control.launch_app("mixer")
        self.assertEqual(result["action"], "launch")
        self.assertEqual(result["label"], "MP Liquid Mixer")
        self.assertTrue(str(spawn.call_args.args[0][1]).endswith("launch_liquid_mixer.sh"))

    def test_launching_an_uninstalled_app_is_refused(self):
        selection.write(["gui"])
        with patch.object(suite_control, "is_supported", return_value=True), \
             patch.object(suite_control, "tty_mode_active", return_value=False), \
             patch.object(suite_control, "_spawn") as spawn:
            result = suite_control.launch_app("liquid")
        self.assertEqual(result["action"], "not_installed")
        spawn.assert_not_called()

    def test_status_payload_only_lists_the_installed_apps(self):
        selection.write(["manager", "liquid"])
        with patch.object(suite_control, "is_supported", return_value=True), \
             patch.object(suite_control, "tty_mode_active", return_value=False), \
             patch.object(suite_control, "running_roles", return_value=set()), \
             patch.object(suite_control, "tray_running", return_value=False), \
             patch.object(suite_control, "web_visualizer_status",
                          return_value={"running": False, "fullscreen": False}), \
             patch.object(suite_control, "_touch_keepalive"):
            suite_control.reset_status_cache()
            payload = suite_control.app_status()
        self.assertEqual([app["id"] for app in payload["apps"]], ["manager", "liquid"])


class TestInstallerWiring(unittest.TestCase):
    def setUp(self):
        self.text = INSTALL.read_text(encoding="utf-8")

    def test_the_installer_prompts_for_one_or_all(self):
        self.assertIn("MP_INSTALL_APPS", self.text)
        self.assertIn("[9/10] Choosing the MP Applications to Install", self.text)
        self.assertIn("All five", self.text)
        self.assertIn("Choose several", self.text)
        self.assertIn("MP Liquid Mixer only", self.text)

    def test_all_five_is_the_default_for_a_non_interactive_run(self):
        marker = self.text.index("Choosing the MP Applications")
        window = self.text[marker:marker + 3000]
        self.assertIn("installing all five MP applications", window)

    def test_a_mixer_request_is_refused_off_linux(self):
        self.assertIn("MP Liquid Mixer is Linux only", self.text)

    def test_the_selection_reaches_both_shortcut_installers(self):
        self.assertIn('--components "$MP_APPS_SELECTION"', self.text)
        self.assertIn('--components="$MP_APPS_SELECTION"', self.text)

    def test_the_help_lists_the_apps_option(self):
        self.assertIn("--apps LIST", self.text)

    def test_the_option_is_accepted_with_a_value_or_with_equals(self):
        self.assertIn("--apps|--components)", self.text)
        self.assertIn("--apps=*|--components=*", self.text)

    def test_the_selection_is_recorded_and_reported(self):
        self.assertIn("installed_apps", self.text)
        self.assertIn("Installed applications:", self.text)

    def test_shell_syntax_is_valid(self):
        import subprocess

        result = subprocess.run(["bash", "-n", str(INSTALL)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class TestWindowsWiring(unittest.TestCase):
    def test_powershell_accepts_the_component_list(self):
        text = POWERSHELL.read_text(encoding="utf-8")
        self.assertIn("[string]$Components", text)
        self.assertIn("$Wanted", text)
        self.assertIn("$keepAll", text)
        self.assertIn('$key -eq "mixer"', text)

    def test_the_component_list_is_not_shadowed_by_a_typed_parameter(self):
        # PowerShell variable names are case-insensitive. A local `$wanted = @()`
        # inside a function whose parameter is [string]$Wanted does not create an
        # array: it coerces the parameter to "" and the parse loop reads nothing.
        # Every shortcut was then "not selected" and deleted, so a Windows 10
        # install created the tray startup entry but no Desktop or Start Menu
        # shortcuts. The working list therefore has its own name.
        text = POWERSHELL.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"(?im)^\s*\$wanted\s*=\s*@\(")
        self.assertIn("$wantedItems", text)
        self.assertIn("$wantedItems.Count -eq 0", text)
        self.assertIn("$wantedItems -contains $item.Name", text)

    def test_windows_tray_filters_its_menu(self):
        text = WINDOWS_TRAY.read_text(encoding="utf-8")
        self.assertIn("Get-MpInstalledComponents", text)
        self.assertIn("installed_apps", text)


class TestMacMenuWiring(unittest.TestCase):
    def test_the_menu_bar_agent_asks_the_selection(self):
        text = (ROOT / "scripts" / "macos_menu_bar.swift").read_text(encoding="utf-8")
        self.assertIn("mp_app_selection.py", text)
        self.assertIn("entryEnabled", text)


if __name__ == "__main__":
    unittest.main()
