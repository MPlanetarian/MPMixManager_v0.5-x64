"""The Windows console scrollbar is the screen buffer hanging past the window."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from windows_bash import find_bash  # noqa: E402
from windows_console_scroll import buffer_matches_window, window_cells  # noqa: E402


class TestWindowsConsoleScroll(unittest.TestCase):
    def test_window_cells_are_inclusive(self):
        self.assertEqual(window_cells(0, 0, 79, 39), (80, 40))

    def test_taller_buffer_is_the_scrollbar(self):
        self.assertFalse(buffer_matches_window(80, 9001, 0, 0, 79, 39))
        self.assertFalse(buffer_matches_window(120, 300, 0, 0, 119, 39))

    def test_matching_buffer_has_no_scrollbar(self):
        self.assertTrue(buffer_matches_window(80, 40, 0, 0, 79, 39))

    def test_scrolled_window_still_needs_a_fit(self):
        self.assertFalse(buffer_matches_window(80, 40, 0, 10, 79, 49))

    def test_windows_bash_skips_the_store_alias(self):
        if sys.platform != "win32":
            self.skipTest("windows")
        found = find_bash().lower()
        self.assertNotIn("windowsapps", found)
        self.assertTrue(found.endswith("bash.exe"))
        self.assertTrue(Path(found).is_file())

    def test_player_and_hosts_hide_the_bar(self):
        """A windowed restore must refit the console, not just hide the bar.

        ``SetWindowPos`` changes how many rows a console window shows but leaves
        its screen buffer at the old height. Restoring MP Gui from full screen
        therefore left a 68-row buffer behind a 45-row window: the menu drew 23
        rows below the visible area and the console told ``stty size`` the space
        was there, so the window looked cut off and stuck.
        """
        player = (ROOT / "MP_Audio_Player.py").read_text(encoding="utf-8")
        opengl = (ROOT / "MP_Audio_Player_Liquid_Glass_OpenGL.py").read_text(encoding="utf-8")
        vulkan = (ROOT / "MP_Audio_Player_Liquid_Glass.py").read_text(encoding="utf-8")
        self.assertIn("_win_hide_console_scrollbar()", player)
        self.assertIn("hide_process_console_scrollbar", opengl)
        self.assertIn("hide_process_console_scrollbar", vulkan)
        self.assertIn("~0x00300000", opengl)
        # The refit runs from the same place the console is moved, and it is
        # retried while the console catches up with its new rectangle.
        self.assertIn("_fit_console_to_well()", opengl)
        self.assertIn("def _fit_console_to_well", opengl)
        self.assertIn("console_window_matches", opengl)
        fit_body = opengl.split("def _fit_console_to_well", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("_console_fit_until", fit_body)

    def test_the_fit_helpers_exist_for_both_halves(self):
        from windows_console_scroll import (  # noqa: F401
            console_buffer_matches_window,
            console_window_matches,
            fit_console_buffer_for_window,
        )
        if sys.platform != "win32":
            self.assertFalse(fit_console_buffer_for_window(0))
            self.assertFalse(console_window_matches(0, 0, 0, 10, 10))
            return
        # A null handle must fail cleanly rather than raising.
        self.assertFalse(fit_console_buffer_for_window(0))
        self.assertFalse(console_window_matches(0, 0, 0, 10, 10))

    def test_the_fit_uses_the_console_window_rect_not_the_requested_one(self):
        """The retry must wait for the console to arrive, then fit twice.

        A single fit issued while the console is still at its old rectangle
        sizes the buffer to the old size and *looks* consistent, which is what
        made the first attempt pass and the menu stay cut off.
        """
        opengl = (ROOT / "MP_Audio_Player_Liquid_Glass_OpenGL.py").read_text(encoding="utf-8")
        body = opengl.split("def _fit_console_to_well", 1)[1].split("\n    def ", 1)[0]
        settle = body.index("_console_fit_until")
        window_check = body.index("console_window_matches(hwnd, *well)")
        self.assertLess(settle, window_check)
        self.assertIn("fit_console_buffer_for_window(hwnd)", body)


    def test_hide_fits_a_real_console_buffer(self):
        if sys.platform != "win32":
            self.skipTest("windows console")
        import subprocess
        script = r"""
import ctypes, sys
from ctypes import wintypes
from windows_console_scroll import buffer_matches_window, hide_attached_console_scrollbar
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32.FreeConsole.restype = ctypes.c_int
kernel32.AllocConsole.restype = ctypes.c_int
kernel32.GetConsoleWindow.restype = wintypes.HWND
kernel32.FreeConsole()
if not kernel32.AllocConsole():
    sys.exit(2)
hwnd = int(kernel32.GetConsoleWindow() or 0)
if hwnd:
    user32.ShowWindow(hwnd, 0)
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
kernel32.CreateFileW.restype = wintypes.HANDLE
handle = kernel32.CreateFileW("CONOUT$", 0xC0000000, 3, None, 3, 0, None)
class COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]
class SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short), ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]
class INFO(ctypes.Structure):
    _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD), ("wAttributes", wintypes.WORD), ("srWindow", SMALL_RECT), ("dwMaximumWindowSize", COORD)]
kernel32.SetConsoleScreenBufferSize.argtypes = [wintypes.HANDLE, COORD]
kernel32.SetConsoleScreenBufferSize.restype = ctypes.c_int
kernel32.SetConsoleScreenBufferSize(handle, COORD(80, 400))
if not hide_attached_console_scrollbar():
    sys.exit(3)
kernel32.GetConsoleScreenBufferInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(INFO)]
kernel32.GetConsoleScreenBufferInfo.restype = ctypes.c_int
info = INFO()
if not kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
    sys.exit(4)
if not buffer_matches_window(int(info.dwSize.X), int(info.dwSize.Y), int(info.srWindow.Left), int(info.srWindow.Top), int(info.srWindow.Right), int(info.srWindow.Bottom)):
    sys.exit(5)
if int(info.dwSize.Y) >= 400:
    sys.exit(6)
sys.exit(0)
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(ROOT / "scripts"),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
