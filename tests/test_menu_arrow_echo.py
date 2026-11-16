#!/usr/bin/env python3
"""Arrow keys must not be printed on the highlighted menu row.

Bash turns echo back on between each timed `read -s`. An up or down arrow
that arrives in that gap is written by the terminal as the four characters
^[[A or ^[[B, on the row the cursor was left on. The menu holds echo off
for the whole screen and puts the previous settings back when it leaves.
"""

import fcntl
import os
import pty
import select
import struct
import termios
import time
import unittest
from pathlib import Path

from tests.mp_test_bash import BASH

ROOT = Path(__file__).resolve().parent.parent


def _read_for(master, seconds):
    buf = b""
    deadline = time.time() + seconds
    while time.time() < deadline:
        timeout = max(0.0, deadline - time.time())
        ready, _, _ = select.select([master], [], [], timeout)
        if not ready:
            continue
        try:
            chunk = os.read(master, 65536)
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
    return buf


class MenuArrowEchoTest(unittest.TestCase):
    def _run(self, script, after_ready):
        pid, master = pty.fork()
        if pid == 0:
            os.chdir(ROOT)
            env = dict(os.environ)
            env.update(TERM="xterm-256color", MP_MANAGER_MOUSE="0", MP_MANAGER_GAMEPAD="0")
            os.execvpe(BASH, [BASH, "--noprofile", "--norc", "-c", script], env)
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        buf = b""
        try:
            deadline = time.time() + 25
            ready = False
            while time.time() < deadline:
                got = _read_for(master, 0.2)
                buf += got
                if not ready and b"READY" in buf:
                    ready = True
                    buf += after_ready(master)
                    break
            self.assertTrue(ready, "menu echo harness did not start")
            # Collect the rest, including the echo flag printed on the way out.
            buf += _read_for(master, 3.0)
        finally:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
            try:
                os.close(master)
            except OSError:
                pass
        return buf

    def test_quiet_input_hides_arrows_and_restores_echo(self):
        script = r"""
source ./Mix_Archive_Manager.sh >/dev/null 2>&1
_menu_quiet_input
printf 'READY\n'
i=0
while [ "$i" -lt 25 ]; do
  ch=""
  IFS= read -rsn1 -t 0.05 ch || true
  if [ -n "$ch" ]; then printf 'KEY:%q\n' "$ch"; else printf 'TICK\n'; fi
  i=$((i + 1))
done
_menu_restore_input
python3 -c 'import termios,sys; print("ECHO", int(bool(termios.tcgetattr(0)[3] & termios.ECHO)))'
"""

        def hammer(master):
            # A few arrows across the read timeouts, then stop so nothing is
            # still arriving when the harness turns echo back on.
            out = b""
            for seq in (b"\x1b[B", b"\x1b[A", b"\x1b[B"):
                time.sleep(0.06)
                os.write(master, seq)
                out += _read_for(master, 0.04)
            return out

        text = self._run(script, hammer)
        self.assertNotIn(b"^[[A", text)
        self.assertNotIn(b"^[[B", text)
        self.assertIn(b"KEY:", text)
        self.assertIn(b"ECHO 1", text)

    def test_menu_arrows_do_not_print_brackets_on_the_row(self):
        script = r"""
source ./Mix_Archive_Manager.sh >/dev/null 2>&1
set_theme_colors retro_linux_memories
run_deferred_startup() { :; }
get_mp_audio_player_track_info() { return 1; }
update_mp_bridge_menu() { :; }
_nav_mouse_enable() { :; }
_nav_mouse_disable() { :; }
_start_win_cursor_hider() { :; }
_stop_win_cursor_hider() { :; }
play_sound_effect() { :; }
mp_screen_layout_refresh() { _MS_ON=0; _MS_N=1; return 1; }
_TERM_COLS=100
_TERM_LINES=30
printf 'READY\n'
opts=(
"1|Play DJ Mixes|(Built-in Player)"
"2|Play Tracks|(Music Library)"
"3|Run Mix Conversion|(FLAC, MP3)"
"0|Return|(or Esc / q)"
)
run_interactive_submenu "ARROW ECHO" "" opts 0
printf 'CHOICE=%s\n' "$REPLY"
python3 -c 'import termios,sys; print("ECHO", int(bool(termios.tcgetattr(0)[3] & termios.ECHO)))'
"""

        def hammer(master):
            out = _read_for(master, 2.0)
            for _ in range(12):
                os.write(master, b"\x1b[B")
                time.sleep(0.03)
                out += _read_for(master, 0.02)
            os.write(master, b"q")
            return out

        text = self._run(script, hammer)
        self.assertNotIn(b"^[[A", text)
        self.assertNotIn(b"^[[B", text)
        self.assertNotIn(b"]]", text)
        self.assertIn("▶".encode(), text)
        self.assertIn(b"CHOICE=q", text)
        self.assertIn(b"ECHO 1", text)


if __name__ == "__main__":
    unittest.main()
