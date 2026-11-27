#!/usr/bin/env python3
"""Update MP Mix Manager from the tray.

The tray's Update item pulls the latest version while Exit_MP_Video.mp4 loops,
then closes the movie and the tray. A failed update must close both too, and
the whole run is capped at five minutes so a stuck git cannot leave a window
the user cannot dismiss.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRAY_PY = ROOT / "scripts" / "mp_system_tray.py"
TRAY_PS1 = ROOT / "scripts" / "windows_tray.ps1"
UPDATER = ROOT / "scripts" / "update_manager.sh"

sys.path.insert(0, str(ROOT / "scripts"))


class TestTrayUpdateMenu(unittest.TestCase):
    def test_both_trays_offer_the_update_item(self):
        import mp_system_tray as tray

        ids = [ident for ident, _label in tray.MENU_ENTRIES]
        self.assertIn("update", ids)
        # Update must not be filtered out by the installed-app selection:
        # it is not tied to one component.
        self.assertNotIn("update", tray.ENTRY_COMPONENTS)
        text = TRAY_PS1.read_text(encoding="utf-8")
        self.assertIn('Id = "update"; Label = "Update MP Mix Manager"', text)

    def test_five_minute_timeout(self):
        import mp_system_tray as tray

        self.assertEqual(tray.UPDATE_TIMEOUT_SECONDS, 300)
        text = TRAY_PS1.read_text(encoding="utf-8")
        self.assertIn("$script:updateTimeoutSeconds = 300", text)

    def test_python_side_uses_the_shared_updater(self):
        text = TRAY_PY.read_text(encoding="utf-8")
        self.assertIn('"scripts" / "update_manager.sh"', text)
        self.assertIn('"--force"', text)

    def test_windows_side_uses_the_shared_updater(self):
        text = TRAY_PS1.read_text(encoding="utf-8")
        self.assertIn("scripts\\update_manager.sh", text)
        self.assertIn("--force", text)


class TestTrayUpdateVideo(unittest.TestCase):
    def test_python_loops_the_exit_movie(self):
        text = TRAY_PY.read_text(encoding="utf-8")
        self.assertIn('"--loop-start"', text)
        self.assertIn('"--loop-stop"', text)
        self.assertIn("MP_EXIT_VIDEO_NO_TTY", text)
        self.assertIn("start_update_video", text)
        self.assertIn("stop_update_video", text)

    def test_windows_loops_the_exit_movie(self):
        text = TRAY_PS1.read_text(encoding="utf-8")
        self.assertIn('"--loop-start"', text)
        # The stop call interpolates the pid, so it is written without quotes.
        self.assertIn("--loop-stop $videoPid", text)
        self.assertIn("MP_EXIT_VIDEO_NO_TTY", text)
        self.assertIn("function Start-MpUpdateVideo", text)
        self.assertIn("function Stop-MpUpdateVideo", text)

    def test_the_video_closes_on_success_and_failure(self):
        py = TRAY_PY.read_text(encoding="utf-8")
        update = py.split("def start_update() -> None:", 1)[1]
        update = update.split("def dispatch(", 1)[0]
        # One teardown path calls the stop and then quits, whatever the result.
        self.assertIn("stop_update_video(video_pid, base)", update)
        self.assertIn("app.quit()", update)
        self.assertLess(update.find("stop_update_video(video_pid, base)"),
                        update.find("app.quit()"))

        ps = TRAY_PS1.read_text(encoding="utf-8")
        block = ps.split("function Start-MpUpdate {", 1)[1]
        block = block.split("function Get-MpCompanionProcess", 1)[0]
        self.assertIn("Stop-MpUpdateVideo", block)
        self.assertIn("Application]::Exit", block)

    def test_a_failed_update_exits_instead_of_looping(self):
        ps = TRAY_PS1.read_text(encoding="utf-8")
        block = ps.split("function Start-MpUpdate {", 1)[1]
        block = block.split("function Get-MpCompanionProcess", 1)[0]
        # The quit must be in a finally, so a timeout or an exception still
        # closes the tray and the movie.
        self.assertIn("} finally {", block)
        self.assertIn("Application]::Exit", block)

    def test_windows_stops_the_video_on_unload(self):
        ps = TRAY_PS1.read_text(encoding="utf-8")
        tail = ps.split("} finally {", 1)[1]
        self.assertIn("Stop-MpUpdateVideo", tail)


class TestUpdaterContract(unittest.TestCase):
    def test_the_updater_supports_force(self):
        text = UPDATER.read_text(encoding="utf-8")
        self.assertIn("--force", text)
        self.assertIn("FORCE_UPDATE", text)

    def test_update_message_reads_the_version(self):
        import mp_system_tray as tray

        text = "✓ MP Mix Archive Manager successfully updated to v0.5.4!\n"
        self.assertIn("v0.5.4", tray.update_message({"ok": True,
                                                   "detail": tray._update_version_line(text)}))
        self.assertIn("did not complete",
                      tray.update_message({"ok": False, "detail": "no network"}))


class TestWhichOnWindows(unittest.TestCase):
    def test_which_finds_a_windows_bash(self):
        """PATH splits on ; on Windows and a bare name needs .exe.

        The old helper split on : only and never tried the extension, so the
        tray's rescan and update calls failed with [WinError 2].
        """
        import os

        import mp_system_tray as tray

        if os.name != "nt":
            self.skipTest("Windows-only behaviour")
        saved = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = r"C:\Program Files\Git\bin;" + saved
            found = tray._which("bash")
            if found is None:
                self.skipTest("Git Bash is not installed on this machine")
            self.assertTrue(found.lower().endswith("bash.exe"), found)
            self.assertNotIn("windowsapps", found.lower(), found)
        finally:
            os.environ["PATH"] = saved


if __name__ == "__main__":
    unittest.main()
