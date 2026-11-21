"""Shared system tray for MP Mix Manager, MP Gui, MP Liquid Player, and the mixer."""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import mp_system_tray as tray
from tests.mp_test_bash import BASH


def proc(pid, ppid, cmdline, environ=""):
    return tray.ProcessInfo(pid, ppid, cmdline, environ)


class TestPrefilter(unittest.TestCase):
    """`classify_process` short-circuits on a substring before the argv walk.

    The guard is what keeps a status poll cheap. If a name the classifier checks
    is missing from the marker tuple, that app silently stops being detected --
    no exception, just a button that never lights up. Pin the invariant.
    """

    CHECKED_NAMES = (
        "MP_Gui.sh",
        "launch-liquid-glass",
        "launch_liquid_glass.sh",
        "Mix_Archive_Manager.sh",
        "mix-archive-manager",
        "launch-manager",
        "MP_Liquid_Mixer.py",
        "launch_liquid_mixer.sh",
        "MP_Liquid_Visualizer.py",
        "mp_liquid_visualizer_vulkan.py",
        "launch_liquid_visualizer.sh",
    )

    def test_every_checked_name_is_in_the_prefilter(self):
        """Each name must be covered by *some* marker as a substring.

        ``LIQUID_HOSTS`` holds several suffixed variants (``...py``,
        ``..._OpenGL.py``) that one shared prefix marker covers, so the check is
        containment rather than equality.
        """
        markers = tray._CLASSIFY_MARKERS
        for name in self.CHECKED_NAMES + tuple(tray.LIQUID_HOSTS):
            self.assertTrue(
                any(marker in name or name in marker for marker in markers),
                f"{name} has no marker in _CLASSIFY_MARKERS: {markers}",
            )

    def test_a_real_launcher_survives_the_prefilter(self):
        for name in self.CHECKED_NAMES + tuple(tray.LIQUID_HOSTS):
            clicked = proc(9000, 1, f"/opt/scripts/{name}")
            self.assertIsNotNone(
                tray.classify_process(clicked, {}, {os.getpid()}),
                f"{name} must still be classified after the prefilter",
            )

    def test_unrelated_processes_are_rejected_cheaply(self):
        for cmdline in ("systemd --user", "vim /opt/notes.txt", "python3 app.py"):
            self.assertIsNone(tray.classify_process(proc(9100, 1, cmdline), {}, {os.getpid()}))


class TestClassify(unittest.TestCase):
    def test_the_three_apps_and_the_console_host_are_distinct(self):
        gui = proc(10, 1, "python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py --manager")
        gui_shell = proc(11, 10, "bash /opt/Mix_Archive_Manager.sh")
        console = proc(12, 5, "bash /opt/Mix_Archive_Manager.sh")
        konsole = proc(
            5,
            1,
            "/usr/bin/konsole --fullscreen --separate -p tabtitle=MP Mix Archive Manager "
            "-e /opt/bin/mix-archive-manager",
        )
        liquid = proc(13, 1, "python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py")
        launcher = proc(14, 1, "setsid bash /opt/MP_Gui.sh")
        player_launcher = proc(15, 1, "/opt/scripts/launch-liquid-glass")
        detached = proc(
            16,
            1,
            "setsid env -u MP_GUI_SESSION -u LINES -u COLUMNS "
            "python3 /opt/MP_Audio_Player_Liquid_Glass.py --play /tmp/a.flac",
        )
        mixer = proc(17, 1, "python3 /opt/MP_Liquid_Mixer/MP_Liquid_Mixer.py")
        mixer_launcher = proc(18, 1, "bash /opt/scripts/launch_liquid_mixer.sh")
        visualizer = proc(19, 1, "python3 /opt/MP_Liquid_Visualizer.py")
        visualizer_launcher = proc(20, 1, "bash /opt/scripts/launch_liquid_visualizer.sh")
        processes = [
            gui, gui_shell, console, konsole, liquid, launcher,
            player_launcher, detached, mixer, mixer_launcher,
            visualizer, visualizer_launcher,
        ]
        by_pid = tray.index_processes(processes)

        self.assertEqual(tray.classify_process(gui, by_pid), tray.ROLE_GUI)
        self.assertIsNone(tray.classify_process(gui_shell, by_pid))
        self.assertEqual(tray.classify_process(console, by_pid), tray.ROLE_MANAGER)
        self.assertEqual(tray.classify_process(konsole, by_pid), tray.ROLE_MANAGER)
        self.assertEqual(tray.classify_process(liquid, by_pid), tray.ROLE_LIQUID)
        self.assertEqual(tray.classify_process(launcher, by_pid), tray.ROLE_GUI)
        self.assertEqual(tray.classify_process(player_launcher, by_pid), tray.ROLE_LIQUID)
        self.assertEqual(tray.classify_process(detached, by_pid), tray.ROLE_LIQUID)
        self.assertEqual(tray.classify_process(mixer, by_pid), tray.ROLE_MIXER)
        self.assertEqual(tray.classify_process(mixer_launcher, by_pid), tray.ROLE_MIXER)
        self.assertEqual(tray.classify_process(visualizer, by_pid), tray.ROLE_VISUALIZER)
        self.assertEqual(tray.classify_process(visualizer_launcher, by_pid), tray.ROLE_VISUALIZER)
        self.assertEqual(
            tray.roles_present(processes),
            {
                tray.ROLE_GUI,
                tray.ROLE_LIQUID,
                tray.ROLE_MANAGER,
                tray.ROLE_MIXER,
                tray.ROLE_VISUALIZER,
            },
        )

    def test_gui_session_environment_is_not_the_console_manager(self):
        shell = proc(20, 1, "bash /opt/Mix_Archive_Manager.sh", "MP_GUI_SESSION=1\nTERM=xterm")
        self.assertIsNone(tray.classify_process(shell, {20: shell}))

    def test_the_tray_an_editor_and_a_sourced_script_are_ignored(self):
        ignored = [
            proc(1, 0, "python3 /opt/scripts/mp_system_tray.py"),
            proc(2, 0, "nvim /opt/Mix_Archive_Manager.sh"),
            proc(3, 0, "bash -c source ./Mix_Archive_Manager.sh"),
            proc(4, 0, "grep Mix_Archive_Manager.sh"),
        ]
        for item in ignored:
            self.assertIsNone(tray.classify_process(item, tray.index_processes(ignored)), item.cmdline)

    def test_close_targets_the_console_manager_and_its_konsole(self):
        gui = proc(10, 1, "python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py --manager")
        gui_shell = proc(11, 10, "bash /opt/Mix_Archive_Manager.sh")
        console = proc(12, 5, "bash /opt/Mix_Archive_Manager.sh")
        konsole = proc(5, 1, "/usr/bin/konsole -e /opt/bin/mix-archive-manager")
        tray_proc = proc(99, 1, "python3 /opt/scripts/mp_system_tray.py")
        processes = [gui, gui_shell, console, konsole, tray_proc]
        self.assertEqual(tray.manager_pids(processes, {99}), [12, 5])


class TestMenu(unittest.TestCase):
    def test_menu_labels_and_close_is_last(self):
        labels = [label for _ident, label in tray.MENU_ENTRIES]
        self.assertEqual(
            labels,
            [
                "Launch MP Gui",
                "Launch MP Liquid Player",
                "Launch MP Liquid Mixer",
                "Launch MP Mix Manager (CLI)",
                "Launch MP Visualizer",
                "Start / Stop MP Audio Player (Web)",
                "Rescan Mix Archive Folders",
                "Rescan Music Folders",
                "Mute MP Player Audio",
                "Close MP Mix Manager (Closes everything)",
            ],
        )
        self.assertEqual(tray.MENU_ENTRIES[-1][0], "close-all")

    def test_launch_opens_new_windows_up_to_the_cap(self):
        base = ROOT
        # One MP Gui window is open: a launch opens another, it does not focus.
        opened = tray.launch_plan(
            tray.ROLE_GUI, base, {tray.ROLE_GUI}, counts={tray.ROLE_GUI: 1}
        )
        self.assertEqual(opened["action"], "spawn")
        self.assertEqual(opened["title"], "MP Mix Manager (Liquid Glass")

        spawned = tray.launch_plan(
            tray.ROLE_LIQUID, base, set(), counts={tray.ROLE_LIQUID: 3}
        )
        self.assertEqual(spawned["action"], "spawn")
        self.assertTrue(str(spawned["argv"][0]).endswith("launch-liquid-glass"))

        manager = tray.launch_plan(
            tray.ROLE_MANAGER,
            base,
            {tray.ROLE_LIQUID, tray.ROLE_GUI},
            platform_name="linux",
        )
        self.assertEqual(manager["action"], "spawn")
        self.assertTrue(str(manager["argv"][0]).endswith("launch-manager"))
        self.assertEqual(manager["title"], "MP Mix Archive Manager")

    def test_a_fifth_gui_or_player_launch_focuses_instead(self):
        for role, title in (
            (tray.ROLE_GUI, "MP Mix Manager (Liquid Glass"),
            (tray.ROLE_LIQUID, "MP Audio Player (Liquid Glass"),
        ):
            at_limit = tray.launch_plan(role, ROOT, set(), counts={role: 4})
            self.assertEqual(at_limit["action"], "focus")
            self.assertEqual(at_limit["title"], title)
            self.assertTrue(at_limit["limit"])
            self.assertEqual(at_limit["cap"], 4)
            self.assertEqual(at_limit["argv"], [])
            # Three windows still leave room for a fourth.
            self.assertEqual(
                tray.launch_plan(role, ROOT, set(), counts={role: 3})["action"],
                "spawn",
            )

    def test_the_console_manager_is_not_capped(self):
        plan = tray.launch_plan(
            tray.ROLE_MANAGER, ROOT, set(), counts={tray.ROLE_MANAGER: 99}
        )
        self.assertEqual(plan["action"], "spawn")

    def test_the_mixer_launch_uses_the_shared_launcher(self):
        plan = tray.launch_plan(tray.ROLE_MIXER, ROOT, set())
        self.assertEqual(plan["action"], "spawn")
        self.assertEqual(plan["title"], "MP Liquid Mixer")
        self.assertEqual(plan["argv"][0], "bash")
        self.assertTrue(str(plan["argv"][1]).endswith("launch_liquid_mixer.sh"))

    def test_the_visualizer_launch_uses_the_shared_launcher(self):
        plan = tray.launch_plan(tray.ROLE_VISUALIZER, ROOT, set())
        self.assertEqual(plan["action"], "spawn")
        self.assertEqual(plan["title"], "MP Liquid Visualizer")
        self.assertEqual(plan["argv"][0], "bash")
        self.assertTrue(str(plan["argv"][1]).endswith("launch_liquid_visualizer.sh"))

    def test_a_running_visualizer_is_focused_instead_of_duplicated(self):
        plan = tray.launch_plan(tray.ROLE_VISUALIZER, ROOT, {tray.ROLE_VISUALIZER})
        self.assertEqual(plan["action"], "focus")
        self.assertEqual(plan["title"], "MP Liquid Visualizer")

    def test_macos_manager_launch_opens_terminal(self):
        plan = tray.launch_plan(tray.ROLE_MANAGER, ROOT, set(), platform_name="darwin")
        self.assertEqual(plan["action"], "spawn")
        self.assertEqual(plan["argv"][:3], ["open", "-a", "Terminal"])
        self.assertTrue(str(plan["argv"][3]).endswith("manager_macos.command"))

    def test_kill_script_is_the_player_shutdown_script(self):
        script = tray.kill_all_script(ROOT)
        self.assertIsNotNone(script)
        self.assertEqual(script.name, "kill_all_mp_audio_players.sh")


class TestSingleton(unittest.TestCase):
    def test_a_second_process_does_not_take_the_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "mp_system_tray.lock"
            err_path = Path(tmp) / "holder.err"
            err_file = open(err_path, "w", encoding="utf-8")
            holder = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import os, time\n"
                    "from pathlib import Path\n"
                    "import mp_system_tray as tray\n"
                    "lock = tray.TrayLock(Path(os.environ['LOCK']))\n"
                    "assert lock.acquire()\n"
                    "time.sleep(30)\n",
                ],
                cwd=str(ROOT / "scripts"),
                env={**os.environ, "LOCK": str(lock_path)},
                stdout=subprocess.DEVNULL,
                stderr=err_file,
            )
            try:
                deadline = time.time() + 5
                while time.time() < deadline and not tray.TrayLock.held(lock_path):
                    if holder.poll() is not None:
                        err_file.close()
                        self.fail(f"lock holder exited {holder.returncode}: {err_path.read_text()}")
                    time.sleep(0.05)
                self.assertTrue(tray.TrayLock.held(lock_path))
                self.assertFalse(tray.TrayLock(lock_path).acquire())
            finally:
                err_file.close()
                if holder.poll() is None:
                    holder.terminate()
                    try:
                        holder.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        holder.kill()
                        holder.wait(timeout=2)


class TestHooks(unittest.TestCase):
    def test_each_app_starts_the_same_tray(self):
        manager = (ROOT / "Mix_Archive_Manager.sh").read_text(encoding="utf-8")
        guard = manager.index('if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then')
        self.assertLess(manager.index("_ensure_mp_system_tray() {"), guard)
        self.assertIn("_ensure_mp_system_tray", manager[guard:])
        for name in ("MP_Gui.sh", "scripts/launch-liquid-glass", "scripts/launch_liquid_glass.sh"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("mp_system_tray.sh", text, name)
            self.assertIn("--ensure", text, name)

    def test_shell_scripts_parse(self):
        for name in (
            "scripts/mp_system_tray.sh",
            "scripts/install_windows_user.sh",
            "install.sh",
            "MP_Gui.sh",
            "scripts/launch-liquid-glass",
            "scripts/launch_liquid_glass.sh",
            "Mix_Archive_Manager.sh",
        ):
            result = subprocess.run(
                [BASH, "-n", str(ROOT / name)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_help_mentions_the_shared_icon(self):
        text = (ROOT / "scripts" / "show_help.sh").read_text(encoding="utf-8")
        self.assertIn("Close MP Mix Manager (Closes everything)", text)
        self.assertIn("Launch MP Gui", text)
        self.assertIn("Launch MP Liquid Player", text)
        self.assertIn("Launch MP Mix Manager", text)
        self.assertIn("Start / Stop MP Audio Player (Web)", text)

    def test_the_installer_installs_and_starts_the_tray(self):
        install = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("mp_system_tray.py", install)
        self.assertIn("--install", install)
        ensure = (ROOT / "scripts" / "mp_system_tray.sh").read_text(encoding="utf-8")
        self.assertIn('setsid python3 "$PY" "$@"', ensure)
        self.assertLess(ensure.index("_is_darwin_host"), ensure.index("import PySide6.QtWidgets"))
        self.assertIn("if ! _is_darwin_host && ! python3 -c", ensure)


class TestInstallFiles(unittest.TestCase):
    def test_kded_autoload_is_written_without_rewriting_other_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc_path = Path(tmp) / "kded5rc"
            rc_path.write_text(
                "[Module-appmenu]\nautoload=true\n\n[Module-statusnotifierwatcher]\nautoload=false\n",
                encoding="utf-8",
            )
            tray.set_kded_autoload("statusnotifierwatcher", True, rc_path)
            text = rc_path.read_text(encoding="utf-8")
            self.assertIn("[Module-appmenu]", text)
            self.assertIn("autoload=true", text)
            self.assertRegex(text, r"\[Module-statusnotifierwatcher\]\nautoload=true")

    def test_autostart_entry_stays_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            autostart = Path(tmp) / "autostart"
            desktop = tray.write_autostart_entry(ROOT, autostart)
            text = desktop.read_text(encoding="utf-8")
            self.assertIn("mp_system_tray.sh --stay", text)
            self.assertIn("X-GNOME-Autostart-enabled=true", text)
            self.assertEqual(desktop.name, "mp-mix-manager-tray.desktop")

    def test_menu_desktop_entry_uses_stay(self):
        with tempfile.TemporaryDirectory() as tmp:
            apps = Path(tmp) / "applications"
            desktop = tray.write_desktop_entry(ROOT, apps)
            text = desktop.read_text(encoding="utf-8")
            self.assertIn("mp_system_tray.sh --stay", text)
            self.assertIn("NoDisplay=true", text)


class TestMacMenuBar(unittest.TestCase):
    def test_menu_bar_agent_uses_the_same_menu(self):
        source = (ROOT / "scripts" / "macos_menu_bar.swift").read_text(encoding="utf-8")
        self.assertIn("NSStatusItem", source)
        self.assertIn("NSStatusBar", source)
        self.assertIn("setActivationPolicy(.accessory)", source)
        for ident, label in tray.MENU_ENTRIES:
            if tray.ENTRY_COMPONENTS.get(ident) == "mixer":
                # The mixer cannot run on macOS, so the native menu bar omits it.
                continue
            self.assertIn(label, source)
        self.assertIn("--stay", source)
        self.assertIn("mp_system_tray.py", source)

    def test_launch_agent_plist_stays_in_the_aqua_session(self):
        icon = ROOT / "assets" / "mp_liquid_glass_icon.png"
        lock = Path("/tmp/mix-manager/mp_system_tray.lock")
        bundle = Path("/tmp/MP Mix Manager Tray.app")
        data = tray.macos_launch_agent_plist(
            bundle,
            ROOT,
            icon,
            lock,
            "/usr/bin/python3",
            stay=True,
        )
        text = data.decode("utf-8")
        self.assertIn(tray.MENU_BAR_LABEL, text)
        self.assertIn("--stay", text)
        self.assertIn("Aqua", text)
        self.assertIn(str(ROOT), text)
        self.assertIn(str(icon), text)
        loaded = __import__("plistlib").loads(data)
        self.assertEqual(loaded["ProgramArguments"][0], "/usr/bin/open")
        self.assertEqual(loaded["ProgramArguments"][2], str(bundle))
        self.assertEqual(loaded["ProgramArguments"][-1], "--stay")
        self.assertTrue(loaded["RunAtLoad"])

    def test_install_tray_uses_the_menu_bar_on_macos(self):
        if sys.platform != "darwin":
            self.skipTest("macOS menu bar")
        with patch.object(tray, "install_macos_tray", return_value=0) as install:
            self.assertEqual(tray.install_tray(ROOT), 0)
        install.assert_called_once_with(ROOT)

    def test_macos_install_writes_the_login_item_without_a_display(self):
        if sys.platform != "darwin":
            self.skipTest("macOS menu bar")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            config = Path(tmp) / "config"
            config.mkdir()
            binary = config / "macos_menu_bar"
            binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            binary.chmod(0o755)
            with patch.dict(os.environ, {"MP_TRAY_CONFIG_DIR": str(config), "DISPLAY": "", "WAYLAND_DISPLAY": ""}, clear=False), \
                 patch.object(tray, "compile_macos_menu_bar", return_value=binary), \
                 patch.object(tray, "start_macos_launch_agent", return_value=True) as start, \
                 patch.object(tray.TrayLock, "held", return_value=True):
                code = tray.install_macos_tray(ROOT, home=home)
            self.assertEqual(code, 0)
            plist = (home / "Library" / "LaunchAgents" / f"{tray.MENU_BAR_LABEL}.plist").read_text(encoding="utf-8")
            bundle = config / "MP Mix Manager Tray.app"
            bundled = bundle / "Contents" / "MacOS" / "macos_menu_bar"
            self.assertTrue(bundled.is_file(), bundled)
            info = (bundle / "Contents" / "Info.plist").read_text(encoding="utf-8")
            self.assertIn("LSUIElement", info)
            self.assertIn("--stay", plist)
            self.assertIn("Aqua", plist)
            self.assertIn("/usr/bin/open", plist)
            self.assertIn(str(bundle), plist)
            self.assertIn(str(ROOT), plist)
            start.assert_called_once()


class TestCloseAllPlaysTheExitVideo(unittest.TestCase):
    """The tray's Close item is a hard kill, so it plays the movie itself."""

    def setUp(self):
        import exit_video
        self.exit_video = exit_video
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(
            setattr, os.environ, "MP_INSTANCE_STATE_DIR",
            os.environ.get("MP_INSTANCE_STATE_DIR", ""),
        )
        os.environ["MP_INSTANCE_STATE_DIR"] = self._tmp.name

    def test_close_all_plays_the_movie_before_stopping_anything(self):
        order = []
        with patch.object(tray, "play_exit_video", side_effect=lambda b: order.append("video")), \
             patch.object(tray, "kill_all_script", return_value=None), \
             patch.object(tray, "read_proc_table", return_value=[]), \
             patch.object(tray, "close_windows", side_effect=lambda *a, **k: order.append("close")):
            tray.shutdown_suite(ROOT)
        self.assertEqual(order, ["video", "close"])

    def test_the_suite_marker_blocks_the_windows_from_playing_it_twice(self):
        import mp_instance_limit as limiter
        with patch.object(tray, "play_exit_video", side_effect=lambda b: limiter.mark_suite_closing()), \
             patch.object(tray, "kill_all_script", return_value=None), \
             patch.object(tray, "read_proc_table", return_value=[]), \
             patch.object(tray, "close_windows"), \
             patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True):
            tray.shutdown_suite(ROOT)
            # The marker lives only for the close, and it is gone afterwards.
            self.assertFalse(limiter.suite_closing())

    def test_the_switch_turns_it_off(self):
        calls = []
        with patch.object(tray, "play_exit_video", side_effect=lambda b: calls.append(b)), \
             patch.object(tray, "kill_all_script", return_value=None), \
             patch.object(tray, "read_proc_table", return_value=[]), \
             patch.object(tray, "close_windows"):
            tray.shutdown_suite(ROOT, play_exit=False)
        self.assertEqual(calls, [])

    def test_the_movie_plays_even_with_several_windows_open(self):
        """The tray closes everything, so it must not wait for the last one."""
        import mp_instance_limit as limiter
        with patch.object(limiter, "process_table", return_value=[
            (100, "python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py --manager"),
            (101, "python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py"),
        ]), patch.object(limiter, "_pid_alive", return_value=True):
            calls = []
            with patch.object(tray, "exit_video_module") as module:
                module.exit_video_enabled.return_value = True
                module.find_exit_video.return_value = ROOT / "Exit_MP_Video.mp4"
                module.play_exit_video.side_effect = lambda *a, **k: calls.append(k) or True
                self.assertTrue(tray.play_exit_video(ROOT))
        self.assertEqual(calls[0]["elect"], False)

    def test_the_switch_off_means_no_playback(self):
        with patch.object(tray, "exit_video_module") as module:
            module.exit_video_enabled.return_value = False
            self.assertFalse(tray.play_exit_video(ROOT))
            module.play_exit_video.assert_not_called()

    def test_a_missing_movie_is_not_an_error(self):
        with patch.object(tray, "exit_video_module") as module:
            module.exit_video_enabled.return_value = True
            module.find_exit_video.return_value = None
            self.assertFalse(tray.play_exit_video(ROOT))


class TestWindowsTrayClose(unittest.TestCase):
    """The PowerShell tray kills the suite too, so it plays the movie first."""

    def test_the_video_uses_the_forced_exit_mode(self):
        text = (ROOT / "scripts" / "windows_tray.ps1").read_text(encoding="utf-8")
        fn = text.split("function Show-MpExitVideo", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("--exit-force", fn)

    def test_the_suite_marker_is_set_and_cleared(self):
        text = (ROOT / "scripts" / "windows_tray.ps1").read_text(encoding="utf-8")
        fn = text.split("function Show-MpExitVideo", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("Set-MpSuiteClosing", fn)
        # Cleared once the suite is stopped, so a later close plays normally.
        self.assertIn("Clear-MpSuiteClosing", text)
        self.assertIn("suite.closing", text)


class TestCloseAllClosesEverything(unittest.TestCase):
    """Close All must actually finish the job on every platform.

    The reported bug: the tray's Close item left MP Liquid Player, MP Gui, the
    console manager and MP Visualizer open after the exit movie finished. Two
    independent causes are pinned here.

      1. The close ran inline in the Qt slot. `shutdown_suite()` waits for the
         whole 40-second movie with `time.sleep()`, so the tray's event loop was
         frozen and the icon looked hung. The teardown now runs on a worker
         thread and the quit is marshalled back to the GUI thread.
      2. The sweep only signalled `manager_pids()`, and relied on the kill
         script's `pgrep -f` patterns for everything else. A manager started
         through `bin/mix-archive-manager` matches none of those patterns, so
         it survived. The sweep now signals every classified role plus the
         visualizer and terminal player, then verifies and escalates.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._saved = {
            name: getattr(tray, name)
            for name in ("kill_all_script", "read_proc_table", "close_windows",
                         "play_exit_video", "suite_pids", "_signal_pids")
        }

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(tray, name, value)

    # -- 1. the sweep covers every window -------------------------------
    def test_suite_pids_includes_the_manager_gui_liquid_and_helpers(self):
        table = [
            proc(10, 1, "bash /opt/bin/mix-archive-manager"),
            proc(11, 1, "python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py --manager"),
            proc(12, 1, "python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py"),
            proc(13, 1, "python3 /opt/MP_Liquid_Visualizer.py --preset random"),
            proc(14, 1, "python3 /opt/MP_Audio_Player.py --interactive"),
            proc(15, 1, "python3 /opt/scripts/mp_system_tray.py --stay"),
        ]
        self.assertEqual(sorted(tray.suite_pids(table, {15})), [10, 11, 12, 13, 14])

    def test_a_manager_behind_a_wrapper_is_still_closed(self):
        """The exact shape of the reported bug: argv matches no kill pattern."""
        with patch.object(tray, "read_proc_table", return_value=[
            proc(10, 1, "bash /opt/bin/mix-archive-manager"),
            proc(11, 1, "python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py"),
        ]), patch.object(tray, "kill_all_script", return_value=None), \
             patch.object(tray, "play_exit_video", return_value=False), \
             patch.object(tray, "close_windows"), \
             patch.object(tray, "_still_alive", side_effect=lambda pids: []):
            signalled = []
            with patch.object(tray, "_signal_pids", side_effect=lambda pids, sig: signalled.extend(pids)):
                tray.shutdown_suite(ROOT, play_exit=False, verify=False)
        self.assertEqual(sorted(signalled), [10, 11])

    def test_the_verify_pass_kills_what_the_first_sweep_missed(self):
        """A survivor after the first sweep is re-swept, then SIGKILLed."""
        calls = []
        # A fake clock, because the verify pass waits out a real deadline.
        clock = {"now": 0.0}
        survivor = [proc(10, 1, "bash /opt/bin/mix-archive-manager")]
        reads = {"count": 0}

        def table():
            reads["count"] += 1
            # The first read is the kill sweep; everything after that still
            # sees the wrapper until the SIGKILL is issued.
            if reads["count"] == 1:
                return survivor + [proc(11, 1, "python3 /opt/MP_Audio_Player.py")]
            if any(sig == getattr(tray.signal, "SIGKILL") for sig, _ in calls):
                return []
            return list(survivor)

        def fake_time():
            clock["now"] += 1.0
            return clock["now"]

        with patch.object(tray, "read_proc_table", side_effect=table), \
             patch.object(tray, "kill_all_script", return_value=None), \
             patch.object(tray, "play_exit_video", return_value=False), \
             patch.object(tray, "close_windows"), \
             patch.object(tray, "_still_alive", side_effect=lambda pids: []), \
             patch.object(tray, "_signal_pids", side_effect=lambda pids, sig: calls.append((sig, list(pids)))), \
             patch.object(tray.time, "sleep"), \
             patch.object(tray.time, "time", side_effect=fake_time):
            tray.shutdown_suite(ROOT, play_exit=False)
        self.assertIn((getattr(tray.signal, "SIGKILL"), [10]), calls)

    def test_a_clean_close_needs_no_second_sweep(self):
        with patch.object(tray, "read_proc_table", return_value=[
            proc(10, 1, "python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py"),
        ]), patch.object(tray, "kill_all_script", return_value=None), \
             patch.object(tray, "play_exit_video", return_value=False), \
             patch.object(tray, "close_windows"), \
             patch.object(tray, "_still_alive", side_effect=lambda pids: []), \
             patch.object(tray, "_signal_pids"), \
             patch.object(tray, "_verify_suite_closed") as verify:
            tray.shutdown_suite(ROOT, play_exit=False)
        verify.assert_called_once()

    # -- 2. the movie must not freeze the icon --------------------------
    def test_the_close_runs_off_the_qt_thread(self):
        text = (ROOT / "scripts" / "mp_system_tray.py").read_text(encoding="utf-8")
        handler = text.split("def dispatch(action_id: str) -> None:", 1)[1].split(
            "\n    web_audio_action = None", 1
        )[0]
        # The slot starts a worker; it does not call shutdown_suite itself.
        self.assertIn("threading.Thread", handler)
        self.assertIn("_run_close_all", handler)
        self.assertNotIn("shutdown_suite(base)", handler)

    def test_the_worker_quits_on_the_gui_thread_after_the_teardown(self):
        text = (ROOT / "scripts" / "mp_system_tray.py").read_text(encoding="utf-8")
        worker = text.split("def _run_close_all(state_box: dict) -> None:", 1)[1].split(
            "\n    close_state = ", 1
        )[0]
        self.assertIn("shutdown_suite(base)", worker)
        # It reports through a dict rather than touching Qt from the worker.
        self.assertIn('state_box["done"] = True', worker)
        self.assertIn("finally", worker)
        self.assertNotIn("app.quit", worker)

    def test_the_quit_timer_lives_on_the_gui_thread(self):
        """A timer made on a worker thread never fires, so the tray never quits.

        QTimer needs a running event loop on the thread that created it. The
        worker sleeps through the movie, so the watcher must be created inside
        dispatch (the GUI thread) and only read the worker's dict.
        """
        text = (ROOT / "scripts" / "mp_system_tray.py").read_text(encoding="utf-8")
        handler = text.split("def dispatch(action_id: str) -> None:", 1)[1].split(
            "\n    web_audio_action = None", 1
        )[0]
        self.assertIn("watcher = QTimer()", handler)
        self.assertIn("watcher.timeout.connect(_check_closed)", handler)
        self.assertIn("app.quit()", handler)
        self.assertNotIn("QTimer.singleShot", handler)

    def test_a_second_click_cannot_start_a_second_movie(self):
        text = (ROOT / "scripts" / "mp_system_tray.py").read_text(encoding="utf-8")
        handler = text.split("def dispatch(action_id: str) -> None:", 1)[1].split(
            "\n    web_audio_action = None", 1
        )[0]
        self.assertIn('state["closing"]', handler)
        self.assertIn("close_action.setEnabled(False)", handler)

    def test_windows_tray_also_runs_the_close_off_the_message_loop(self):
        text = (ROOT / "scripts" / "windows_tray.ps1").read_text(encoding="utf-8")
        handler = text.split('if ($ActionId -eq "close") {', 1)[1].split("\n    }", 1)[0]
        self.assertIn("PowerShell]::Create()", handler)
        self.assertIn("BeginInvoke()", handler)
        self.assertIn("closeTimer", handler)
        # The inline path must be gone: no direct Show-MpExitVideo + Exit pair.
        self.assertNotIn("Show-MpExitVideo\n        Stop-MpSuite", handler)

    def test_windows_kill_list_covers_the_wrapper_launchers(self):
        text = (ROOT / "scripts" / "windows_tray.ps1").read_text(encoding="utf-8")
        fn = text.split("function Stop-MpSuite", 1)[1].split("\nfunction ", 1)[0]
        for needle in ("mix-archive-manager", "launch-manager", "MP_Liquid_Visualizer",
                       "MP_Audio_Player", "MP_Gui.sh", "dj_s4_midi"):
            self.assertIn(needle, fn, needle)

    def test_macos_close_already_runs_off_the_main_thread(self):
        text = (ROOT / "scripts" / "macos_menu_bar.swift").read_text(encoding="utf-8")
        fn = text.split("@objc func performAction", 1)[1].split("\n    func ", 1)[0]
        self.assertIn("DispatchQueue.global", fn)
        # The quit is posted back to the main queue, after runPython returns.
        self.assertLess(fn.index("runPython"), fn.index("NSApp.terminate"))


class TestTrayRescans(unittest.TestCase):
    """The tray's rescan entries run the console menu's own code paths.

    Storage Management > 4 ("Rescan Mix Archive Folders") and
    Option 2 > 3 > A ("Generate / Refresh Playlists") used to be reachable only
    from the console. The tray must call the same implementation, or the two
    front ends can disagree about what a rescan covers.
    """

    def test_every_tray_has_the_same_scan_and_mute_entries(self):
        for name in (
            "scripts/mp_system_tray.py",
            "scripts/windows_tray.ps1",
        ):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("Rescan Mix Archive Folders", text, name)
            self.assertIn("Rescan Music Folders", text, name)
            self.assertIn("Mute MP Player Audio", text, name)
            self.assertIn("Unmute MP Player Audio", text, name)
        # The menu bar posts the entries itself but takes the live mute title
        # from the tray module, so it only needs the initial label.
        text = (ROOT / "scripts" / "macos_menu_bar.swift").read_text(encoding="utf-8")
        self.assertIn("Rescan Mix Archive Folders", text)
        self.assertIn("Rescan Music Folders", text)
        self.assertIn("Mute MP Player Audio", text)
        self.assertIn('"--action-label", "mute-audio"', text)

    def test_the_manager_owns_the_archive_rescan(self):
        text = (ROOT / "Mix_Archive_Manager.sh").read_text(encoding="utf-8")
        self.assertIn("_mp_rescan_archive_run() {", text)
        self.assertIn("--rescan-archive", text)
        # It must reuse the same cache invalidation and statistics as the menu.
        fn = text.split("_mp_rescan_archive_run() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("clear_archive_dir_cache", fn)
        self.assertIn("invalidate_archive_stats_cache", fn)
        self.assertIn("show_stats --force", fn)
        self.assertIn("get_all_mix_archive_dirs --refresh", fn)
        self.assertIn("RESCAN_STATUS=", fn)

    def test_the_tray_calls_the_archive_rescan_flag(self):
        text = (ROOT / "scripts" / "mp_system_tray.py").read_text(encoding="utf-8")
        fn = text.split("def rescan_archive(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("--rescan-archive", fn)
        self.assertIn("Mix_Archive_Manager.sh", fn)

    def test_the_tray_builds_the_music_playlists_the_menu_builds(self):
        text = (ROOT / "scripts" / "mp_system_tray.py").read_text(encoding="utf-8")
        fn = text.split("def rescan_music_folders(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("music_folders_manager.py", fn)
        self.assertIn("--generate", fn)
        self.assertIn("ALL", fn)

    def test_the_rescan_reports_through_a_desktop_notification(self):
        text = (ROOT / "scripts" / "mp_system_tray.py").read_text(encoding="utf-8")
        self.assertIn("def notify(", text)
        self.assertIn("notify-send", text)
        # The tray slots post the notice only after the worker finished.
        self.assertIn("notify(title, rescan_message(action_id, result)", text)

    def test_the_scan_runs_off_the_qt_thread(self):
        text = (ROOT / "scripts" / "mp_system_tray.py").read_text(encoding="utf-8")
        fn = text.split("def _run_rescan(", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("run_rescan(action_id, base)", fn)
        self.assertIn('state_box["done"] = True', fn)
        # And the dispatch slot only starts it, so the menu never freezes.
        slot = text.split("def dispatch(action_id: str) -> None:", 1)[1].split(
            "\n    web_audio_action = None", 1
        )[0]
        self.assertIn("start_rescan(action_id)", slot)
        self.assertNotIn("run_rescan(action_id, base)", slot)

    def test_the_rescan_timer_outlives_the_slot_that_started_it(self):
        """A parentless QTimer is collected when the slot returns.

        Then its timeout never fires again and a long scan leaves the entry on
        "Rescanning…" forever. The timer and its worker must be held until the
        result is collected.
        """
        text = (ROOT / "scripts" / "mp_system_tray.py").read_text(encoding="utf-8")
        fn = text.split("def start_rescan(action_id: str) -> None:", 1)[1].split(
            "\n    def dispatch", 1
        )[0]
        self.assertIn("rescan_timers[action_id] = (watcher, worker)", fn)
        self.assertIn("rescan_timers.pop(action_id, None)", fn)

    def test_a_running_scan_says_so_and_cannot_be_clicked_twice(self):
        text = (ROOT / "scripts" / "mp_system_tray.py").read_text(encoding="utf-8")
        self.assertIn("RESCAN_BUSY_LABELS", text)
        fn = text.split("def start_rescan(action_id: str) -> None:", 1)[1].split(
            "\n    def dispatch", 1
        )[0]
        self.assertIn("action.setEnabled(False)", fn)
        self.assertIn("rescan_states[action_id][\"done\"]", fn)

    def test_the_archive_notice_wording_matches_the_scan(self):
        result = {
            "ok": True, "folders": 2, "flacs": 484, "wavs": 52,
            "pending_wavs": 7, "missing_tracklists": 5,
        }
        message = tray.archive_rescan_message(result)
        self.assertIn("2 archive folders scanned", message)
        self.assertIn("484 FLACs", message)
        self.assertIn("7 WAVs waiting for conversion", message)
        self.assertIn("5 missing tracklists", message)

    def test_a_failed_scan_says_why(self):
        message = tray.archive_rescan_message({"ok": False, "detail": "Drive is offline."})
        self.assertIn("failed", message)
        self.assertIn("Drive is offline.", message)
        music = tray.music_rescan_message({"ok": False, "detail": "No music folders are configured."})
        self.assertIn("No music folders are configured.", music)

    def test_the_rescan_result_parser_reads_the_reported_keys(self):
        # The parser is what stands between the manager's output and the
        # notice, so a renamed key must not silently show zeroes forever.
        text = (ROOT / "scripts" / "mp_system_tray.py").read_text(encoding="utf-8")
        fn = text.split("def rescan_archive(", 1)[1].split("\ndef ", 1)[0]
        for key in ("status", "folders", "flacs", "wavs", "pending_wavs", "missing_tracklists"):
            self.assertIn(f'fields.get("{key}")', fn)


class TestTrayMute(unittest.TestCase):
    """The mute entry states the action, not the state."""

    def test_the_label_follows_the_player(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)
            with patch.dict(os.environ, {"MP_PLAYER_CONFIG_DIR": str(config)}):
                self.assertEqual(tray.mute_menu_label(), "Mute MP Player Audio")
                tray.player_state_file().write_text('{"muted": true}', encoding="utf-8")
                self.assertEqual(tray.mute_menu_label(), "Unmute MP Player Audio")
                tray.player_state_file().write_text('{"muted": false}', encoding="utf-8")
                self.assertEqual(tray.mute_menu_label(), "Mute MP Player Audio")

    def test_a_missing_state_file_reads_as_audible(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"MP_PLAYER_CONFIG_DIR": tmp}):
                self.assertFalse(tray.player_muted())

    def test_the_toggle_uses_the_players_ipc_answer(self):
        import json
        import socket as socket_module
        import threading

        if not hasattr(socket_module, "AF_UNIX"):
            self.skipTest("AF_UNIX IPC")
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)
            server = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
            server.bind(str(config / "mp_audio_player.sock"))
            server.listen(2)
            replies = iter([False, True])

            def serve():
                for answer in replies:
                    conn, _ = server.accept()
                    conn.recv(4096)
                    conn.sendall(json.dumps({"status": "ok", "muted": answer}).encode())
                    conn.close()

            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            with patch.dict(os.environ, {"MP_PLAYER_CONFIG_DIR": str(config)}):
                # The state file still says audible, so an implementation that
                # only re-read the file would keep showing "Mute".
                tray.player_state_file().write_text('{"muted": false}', encoding="utf-8")
                first = tray.toggle_player_mute()
                self.assertEqual(first["muted"], False)
                second = tray.toggle_player_mute()
                self.assertEqual(second["muted"], True)
            server.close()

    def test_the_label_is_re_read_when_the_menu_opens(self):
        text = (ROOT / "scripts" / "mp_system_tray.py").read_text(encoding="utf-8")
        self.assertIn("menu.aboutToShow.connect(refresh_menu_labels)", text)
        self.assertIn("refresh_mute_label()", text)
        # The menu bar asks the same module rather than keeping its own copy.
        swift = (ROOT / "scripts" / "macos_menu_bar.swift").read_text(encoding="utf-8")
        self.assertIn('"--action-label", "mute-audio"', swift)

    def test_the_not_running_case_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"MP_PLAYER_CONFIG_DIR": tmp}):
                result = tray.toggle_player_mute()
        self.assertFalse(result["supported"])
        text = (ROOT / "scripts" / "mp_system_tray.py").read_text(encoding="utf-8")
        self.assertIn("MP Audio Player is not running", text)

    def test_the_player_cmd_fallback_the_windows_tray_relies_on_exists(self):
        """Windows PowerShell 5.1 cannot open AF_UNIX.

        The player may be listening on a Unix-domain socket there, so the
        PowerShell tray falls back to `MP_Audio_Player.py --cmd mute`. Pin that
        entry point and its exit status, or the fallback silently stops working
        and every mute click is reported as "player not running".
        """
        import subprocess

        player = ROOT / "MP_Audio_Player.py"
        source = player.read_text(encoding="utf-8")
        self.assertIn('(("--cmd",), dict(type=str))', source)
        self.assertIn('if res := send_ipc_command(args.cmd)', source)
        # A namespace with no player behind it is the "not running" case: the
        # tray reads this non-zero exit to show the notice instead of pretending
        # the mute landed.
        result = subprocess.run(
            [sys.executable, str(player), "--cmd", "mute"],
            capture_output=True,
            text=True,
            timeout=90,
            env={**os.environ, "MP_PLAYER_NAMESPACE": "tray-test-absent"},
        )
        self.assertNotEqual(result.returncode, 0)
        tray_source = (ROOT / "scripts" / "windows_tray.ps1").read_text(encoding="utf-8")
        fn = tray_source.split("function Send-MpPlayerCommand", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("--cmd $Command", fn)
        self.assertIn("$LASTEXITCODE", fn)


class TestTrayWebCompanion(unittest.TestCase):
    def test_windows_tray_launches_the_web_companion(self):
        source = (ROOT / "scripts" / "windows_tray.ps1").read_text(encoding="utf-8")
        self.assertIn('Label = "Launch MP Web Companion"', source)
        self.assertIn('$ActionId -eq "web"', source)
        self.assertIn("launch_web_companion.bat", source)
        self.assertIn("mix_web_companion.py", source)
        # Clicking it is idempotent: an already-running server is opened, not
        # started a second time.
        fn = source.split("function Start-MpWebCompanion", 1)[1].split(
            "\nfunction ", 1
        )[0]
        self.assertIn("Get-MpCompanionProcess", fn)
        self.assertIn("Start-Process -FilePath $url", fn)


if __name__ == "__main__":
    unittest.main()
