#!/usr/bin/env python3
"""
Test terminal resize handling in Mix_Archive_Manager.sh.
Verifies that when terminal window is resized (e.g. via Ctrl + / Ctrl -),
the script catches SIGWINCH, detects the geometry change, emits RESIZE,
updates _TERM_LINES, _TERM_COLS, LINES, and COLUMNS, and automatically
rescales box width and header dimensions.
"""

import fcntl
import os
import pty
import re
import select
import struct
import subprocess
import termios
import time
import unittest

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANAGER_SH = os.path.join(SCRIPT_DIR, "Mix_Archive_Manager.sh")


def _visible(raw: str) -> str:
    raw = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", raw)
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", raw)


def _run_menu_at(rows: int, cols: int, seconds: float = 20.0) -> str:
    """Start the manager on a pty of the given size and return what it painted.

    The first frames can be blank while the archive scan and the header settle,
    so keep reading until the menu body and its status line have both arrived.
    """
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    env = dict(os.environ)
    env["TERM"] = "xterm-256color"
    env["MP_MANAGER_GAMEPAD"] = "0"
    proc = subprocess.Popen(
        ["bash", MANAGER_SH], stdin=slave, stdout=slave, stderr=slave,
        env=env, cwd=SCRIPT_DIR, start_new_session=True,
    )
    os.close(slave)
    chunks = []
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if not ready:
                continue
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
            painted = _visible(b"".join(chunks).decode("utf-8", "replace"))
            if "MP Mix Archive Manager" in painted and "Selection:" in painted:
                break
    finally:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        os.close(master)
    return _visible(b"".join(chunks).decode("utf-8", "replace"))


class TestManagerResize(unittest.TestCase):
    def test_syntax_valid(self):
        result = subprocess.run(["bash", "-n", MANAGER_SH], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"Syntax error in {MANAGER_SH}: {result.stderr}")

    def test_winch_trap_present(self):
        with open(MANAGER_SH, "r", encoding="utf-8") as f:
            content = f.read(2000) # Check header
        self.assertIn("trap '_on_winch' WINCH", content)
        self.assertIn("_TERM_RESIZED=1", content)

    def _library_prefix(self) -> str:
        """Line number just before the menu helpers that need terminal control.

        These tests used to slice a fixed number of lines, which drifted
        silently whenever a helper was inserted above the menu. Anchor the
        slice on the first menu-specific function instead.
        """
        with open(MANAGER_SH, "r", encoding="utf-8") as handle:
            text = handle.read().splitlines()
        start = next((i for i, line in enumerate(text)
                      if line.startswith("read_nav_key() {")), None)
        if start is None:
            raise AssertionError("read_nav_key() not found")
        # End on the closing brace of the function that owns read_nav_key.
        depth = 0
        for i in range(start, len(text)):
            depth += text[i].count("{") - text[i].count("}")
            if depth == 0 and i > start:
                return str(i + 1)
        raise AssertionError("read_nav_key() end not found")

    def test_resize_detection_flow(self):
        prefix = self._library_prefix()
        bash_test = f'''
        SCRIPT_DIR="{SCRIPT_DIR}"
        source <(sed -n '1,{prefix}p' "{MANAGER_SH}")
        # Keep stdin open but silent so read_nav_key times out and reports TICK
        # instead of seeing EOF (which is what a closed stdin would look like).
        exec 0< <(sleep 10)
        
        # Test initial geometry
        update_terminal_geometry 1
        init_lines="$_TERM_LINES"
        init_cols="$_TERM_COLS"
        [ -n "$init_lines" ] || exit 1
        [ -n "$init_cols" ] || exit 2
        
        # Idle check should return TICK
        k=""
        read_nav_key k
        [ "$k" = "TICK" ] || exit 3
        
        # Trigger WINCH signal
        _on_winch
        [ "$_TERM_RESIZED" -eq 1 ] || exit 4
        
        # read_nav_key should now immediately emit RESIZE
        k=""
        read_nav_key k
        [ "$k" = "RESIZE" ] || exit 5
        
        # Simulate handling RESIZE
        update_terminal_geometry 1
        _TERM_RESIZED=0
        
        # Box width should adapt
        _set_menu_box_width new_w "$_TERM_COLS"
        expected_w=$(( _TERM_COLS >= 8 ? _TERM_COLS - 4 : (_TERM_COLS >= 3 ? _TERM_COLS - 2 : 1) ))
        [ "$new_w" -eq "$expected_w" ] || exit 6
        
        # Subsequent key should be TICK again (no stuck loop)
        k=""
        read_nav_key k
        [ "$k" = "TICK" ] || exit 7
        
        echo "SUCCESS"
        '''
        res = subprocess.run(["bash", "-c", bash_test], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Bash resize flow failed: {res.stderr}\nOutput: {res.stdout}")
        self.assertIn("SUCCESS", res.stdout)

    def test_scaling_dimensions_on_zoom(self):
        """Verify that _set_menu_box_width and render_main_header adapt to Ctrl+ (zoom in) and Ctrl- (zoom out) sizes."""
        prefix = self._library_prefix()
        bash_test = """
        SCRIPT_DIR=""" + f'"{SCRIPT_DIR}"' + """
        source <(sed -n '1,""" + prefix + """p' """ + f'"{MANAGER_SH}"' + """)
        is_mix_archive_configured() { return 0; }
        get_manager_uptime() { _MANAGER_UPTIME_LINE="Uptime: 1h"; }
        get_system_perf_stats() { :; }
        _reflow_audio_iface_display() { :; }
        _mp_console_icons() { printf '%s' "$1"; }
        source <(sed -n '/^render_main_header() {/,/^}/p' """ + f'"{MANAGER_SH}"' + """)
        
        # Case 1: Zoom In with Ctrl + (e.g. font becomes bigger, so window cols shrink to 90)
        _TERM_COLS=90
        _TERM_LINES=30
        _set_menu_box_width w_zoom_in "$_TERM_COLS"
        [ "$w_zoom_in" -eq 86 ] || exit 10
        render_main_header "$w_zoom_in" >/dev/null
        # Header panel width matches w_zoom_in + 2 (with 2 spaces indent)
        first_line="$(printf '%b\n' "$_RENDERED_MAIN_HEADER" | head -n 1)"
        clean_line=$(printf '%b' "$first_line" | sed 's/\\x1b\\[[0-9;]*m//g' | tr -d '\r\n')
        [ "${#clean_line}" -eq $((w_zoom_in + 2)) ] || exit 11
        
        # Case 2: Zoom Out with Ctrl - (e.g. font becomes smaller, so window cols expand to 160)
        _TERM_COLS=160
        _TERM_LINES=55
        _set_menu_box_width w_zoom_out "$_TERM_COLS"
        [ "$w_zoom_out" -eq 156 ] || exit 20
        render_main_header "$w_zoom_out" >/dev/null
        first_line="$(printf '%b\n' "$_RENDERED_MAIN_HEADER" | head -n 1)"
        clean_line=$(printf '%b' "$first_line" | sed 's/\\x1b\\[[0-9;]*m//g' | tr -d '\r\n')
        [ "${#clean_line}" -eq $((w_zoom_out + 2)) ] || exit 21
        
        echo "ZOOM_SUCCESS"
        """
        res = subprocess.run(["bash", "-c", bash_test], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Zoom scaling test failed: {res.stderr}\nOutput: {res.stdout}")
        self.assertIn("ZOOM_SUCCESS", res.stdout)

    def test_the_console_size_lock_is_gone(self):
        """The 90 x 26 .. 157 x 44 guard was removed; nothing may reinstate it.

        It blocked the menu behind a black screen whenever a resize or a font
        zoom slipped past it, so the whole guard is gone rather than patched.
        A plain resize still has to redraw, which is covered above.
        """
        with open(MANAGER_SH, "r", encoding="utf-8") as handle:
            text = handle.read()
        for dead in (
            "mp_console_zoom_hold",
            "mp_console_zoom_after_menu",
            "mp_console_zoom_classify",
            "mp_console_zoom_guard_active",
            "_mp_zoom_guard_clamp_step",
            "MP_CONSOLE_ZOOM",
            "Oversized or Undersized",
        ):
            self.assertNotIn(dead, text, f"{dead} survived the guard removal")

    def test_an_oversized_or_undersized_window_still_shows_the_menu(self):
        """Sizes outside the old band must paint the menu, not a black warning."""
        for rows, cols in ((80, 220), (12, 48)):
            with self.subTest(rows=rows, cols=cols):
                painted = _run_menu_at(rows, cols)
                self.assertNotIn("Oversized or Undersized", painted)
                self.assertIn("MP Mix Archive Manager", painted)
                self.assertIn("Selection:", painted)

if __name__ == "__main__":
    unittest.main()
