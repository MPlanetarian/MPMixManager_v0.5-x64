#!/usr/bin/env python3
"""Vertical (portrait) MP Gui must fill the whole window with its menu.

MP Gui's portrait window is tall: 74 rows at the default shape, while the
single-column frame is only as tall as its options. That frame used to end at
its status line and leave a strip of empty rows under the footer, so the glass
wasted a tenth of its height. In Vertical the spare rows are now shared out
between the options and the footer is pinned to the bottom row.

Horizontal keeps the classic spacing: the frame ends where the options end, so
these tests also pin that the change is scoped to M's portrait window.

The menu is driven through run_interactive_submenu on a pty with a fixed set of
options, so the result does not depend on the live header cache, the weather
line or cloud-backup status of the machine running the tests.
"""

import fcntl
import os
import pty
import re
import select
import struct
import subprocess
import tempfile
import termios
import time
import unittest
from pathlib import Path

from tests.mp_test_bash import BASH

ROOT = Path(__file__).resolve().parent.parent
MANAGER = ROOT / "Mix_Archive_Manager.sh"

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b.")

# Six options over three sections: enough to look like a real submenu, few
# enough that every option fits in a portrait window at any size tested.
OPTIONS = """opts=(
"SECTION|SECTION 1: LISTEN, CONVERT & IMPORT|"
"AI|Load MP Mix Manager AI Assistant (MP Harmony) (New)|(Shift+A • starts Ollama if needed)"
"1|Play DJ Mixes (MP Audio Player - Mix Archive Mode)|(Built-in Player, Custom Animated EQ, FLAC/WAV/MP3)"
"2|Play Tracks from MUSIC Folders using MP Audio Player|(Music Library • Local Drives & SMB Shares)"
"SECTION|SECTION 2: STUDIO AUDIO, PLAYBACK & VIDEO|"
"14|Tracklist Management, Scanning & Metadata Suite|(Browse, Search, Picard, HTML Index)"
"15|Audio Players & Retro Playback Suite|(cliamp, Strawberry, VLC, Audacity, Haruna)"
"SECTION|SECTION 3: SYSTEM, NETWORK, AI & SETTINGS|"
"35|Manager Settings, Themes, Shell CLI & Reboot|(Themes, Migration, Reinstall/Reset)"
"38|Exit Manager|(or q / exit)"
)
run_interactive_submenu "PROBE" "" opts 0
"""


def _screen(raw, rows, cols):
    """Apply the escape stream to a screen and return its rows.

    The frame is measured on the screen the terminal would show, not on a
    newline split of the raw bytes: a paint can end with a carriage return or
    wrap a long status line, and either would shift a naive row count.
    """
    screen = [[" "] * cols for _ in range(rows)]
    row = col = 0
    i = 0
    token = re.compile(
        r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b."
    )
    while i < len(raw):
        match = token.match(raw, i)
        if match:
            seq = match.group(0)
            i = match.end()
            if seq == "\x1b[H":
                row = col = 0
            elif seq.endswith("H") and seq.startswith("\x1b["):
                parts = seq[2:-1].split(";")
                try:
                    row = (int(parts[0]) - 1) if parts[0] else 0
                    col = (int(parts[1]) - 1) if len(parts) > 1 and parts[1] else 0
                except ValueError:
                    pass
            elif seq == "\x1b[J":
                for r in range(row, rows):
                    for c in range(col if r == row else 0, cols):
                        screen[r][c] = " "
            elif seq == "\x1b[K":
                if 0 <= row < rows:
                    for c in range(col, cols):
                        screen[row][c] = " "
            continue
        ch = raw[i]
        i += 1
        if ch == "\r":
            col = 0
        elif ch == "\n":
            row += 1
        elif ch == "\x07":
            pass
        else:
            if col >= cols:
                col = 0
                row += 1
            if 0 <= row < rows and 0 <= col < cols:
                screen[row][col] = ch
            col += 1
    return ["".join(line) for line in screen]


def _last_used_row(lines):
    last = 0
    for index, line in enumerate(lines):
        if line.strip():
            last = index + 1
    return last


def _status_text(lines):
    """The Selection line, which can wrap on a narrow portrait window."""
    plain = [_ANSI.sub("", line) for line in lines]
    for index, line in enumerate(plain):
        if "Selection:" in line:
            return " ".join(part.strip() for part in plain[index:])
    return ""


def _run_menu(rows, cols, shape, menu=lambda: OPTIONS, timeout=90):
    """Paint one menu on a pty of the given size and return its screen.

    ``shape`` publishes MP Gui's window shape for the child before the first
    frame. ``None`` leaves it alone, which is what a plain console looks like.
    """
    home = Path(tempfile.mkdtemp(prefix="mp-vertical-"))
    config = home / ".config" / "mix-manager"
    config.mkdir(parents=True)
    for flag in (".liquid_glass_welcomed", ".first_run_eq_initialized",
                 ".first_run_sample_loaded", ".first_run_sample_prevent_autoplay"):
        (config / flag).write_text("", encoding="utf-8")
    (home / "MP_SAMPLES").mkdir()

    # The manager drops a stale shape file while it is being sourced, so the
    # shape is written by a wrapper function the menu path has already passed.
    prelude = ""
    if shape:
        prelude = (
            'mkdir -p "$HOME/.config/mix-manager"\n'
            f'printf \'%s\\n\' "{shape}" > "$HOME/.config/mix-manager/gui_window_shape.$$"\n'
        )

    script = f'''
source ./Mix_Archive_Manager.sh >/dev/null 2>&1
set_theme_colors dreamworlds
MP_GUI_SESSION=1
_TERM_COLS={cols}
_TERM_LINES={rows}
MIX_ARCHIVE_CONFIGURED=true
is_mix_archive_configured() {{ return 0; }}
get_mp_audio_player_track_info() {{ return 1; }}
update_mp_bridge_menu() {{ :; }}
_nav_mouse_enable() {{ :; }}
_nav_mouse_disable() {{ :; }}
_start_win_cursor_hider() {{ :; }}
_stop_win_cursor_hider() {{ :; }}
play_sound_effect() {{ :; }}
run_deferred_startup() {{ :; }}
mp_screen_layout_refresh() {{ _MS_ON=0; _MS_N=1; return 1; }}
mp_screen_query_widths() {{ :; }}
{prelude}{menu()}
'''

    env = dict(os.environ)
    env.update(
        HOME=str(home),
        TERM="xterm-256color",
        MP_GUI_SESSION="1",
        MP_MANAGER_GAMEPAD="0",
        MP_MANAGER_MOUSE="0",
        WEATHER_ENABLED="false",
        PLANETS_ENABLED="false",
    )
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    proc = subprocess.Popen(
        [BASH, "--noprofile", "--norc", "-c", script], stdin=slave,
        stdout=slave, stderr=subprocess.DEVNULL, cwd=str(ROOT),
        env=env, start_new_session=True,
    )
    os.close(slave)

    chunks = []
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            ready, _, _ = select.select([master], [], [], 0.15)
            if not ready:
                continue
            try:
                data = os.read(master, 1 << 20)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
            if b"Selection:" in b"".join(chunks):
                # Let the blinking selection and any tick repaint settle.
                settle = time.time() + 0.8
                while time.time() < settle:
                    ready, _, _ = select.select([master], [], [], 0.1)
                    if not ready:
                        continue
                    try:
                        more = os.read(master, 1 << 20)
                    except OSError:
                        break
                    if not more:
                        break
                    chunks.append(more)
                break
    finally:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.close(master)
        except OSError:
            pass

    raw = b"".join(chunks).decode("utf-8", "replace")
    # The menu repaints one frame per ESC[H. Keep the last one that carries a
    # status line, so a later header repaint is not measured instead.
    frames = [
        frame for frame in raw.split("\x1b[H")
        if "Selection:" in _ANSI.sub("", frame)
    ]
    if not frames:
        return []
    return _screen("\x1b[H" + frames[-1], rows, cols)


class VerticalMenuFillsTheWindowTest(unittest.TestCase):
    def test_vertical_footer_sits_on_the_last_row(self):
        lines = _run_menu(74, 109, shape="VERTICAL")
        self.assertTrue(lines, "the portrait menu did not paint")
        self.assertEqual(
            _last_used_row(lines), 74,
            f"the portrait menu ends on row {_last_used_row(lines)} of 74, so "
            "the footer floats and the space is wasted",
        )
        self.assertIn("Selection:", _status_text(lines))

    def test_vertical_fills_wider_and_taller_portrait_windows(self):
        for rows, cols in ((90, 60), (100, 76), (120, 80)):
            with self.subTest(rows=rows, cols=cols):
                lines = _run_menu(rows, cols, shape="VERTICAL")
                self.assertTrue(lines, "the portrait menu did not paint")
                self.assertEqual(
                    _last_used_row(lines), rows,
                    f"{cols}x{rows}: menu ends on row {_last_used_row(lines)}",
                )
                self.assertIn("Selection:", _status_text(lines))

    def test_vertical_keeps_a_readable_list(self):
        """Filling the window must not dissolve the menu into blank rows."""
        lines = _run_menu(74, 109, shape="VERTICAL")
        self.assertTrue(lines)
        options = [
            line for line in lines
            if re.search(r"\[(?:AI|\s*\d+)\]", _ANSI.sub("", line))
        ]
        self.assertEqual(len(options), 7, "every option should stay painted")
        blanks = 0
        for line in lines:
            if not _ANSI.sub("", line).strip():
                blanks += 1
        self.assertGreater(blanks, 0, "the spare rows should be blank, not glyphs")

    def test_a_scrolling_menu_is_not_stretched(self):
        """A list too long to fit keeps its ▲/▼ markers and is left alone.

        The fill only applies when every option already fits, so the scroll
        view still governs the height and the frame must stay inside the window.
        """
        rows = " ".join(f'"{i}|Option {i}|(hint {i})"' for i in range(1, 41))
        menu = (
            f'opts=( {rows} "0|Exit|(or q)" )\n'
            'run_interactive_submenu "LONG" "" opts 0\n'
        )
        lines = _run_menu(30, 100, shape="VERTICAL", menu=lambda: menu)
        self.assertTrue(lines, "the long menu did not paint")
        text = " ".join(_ANSI.sub("", line) for line in lines)
        self.assertIn("more option", text, "the scroll marker should be painted")
        self.assertLessEqual(_last_used_row(lines), 30)

    def test_horizontal_keeps_the_classic_spacing(self):
        """The fill is scoped to M's portrait window, so the normal window is
        unchanged: its frame ends where its options end."""
        lines = _run_menu(74, 109, shape="HORIZONTAL")
        self.assertTrue(lines, "the horizontal menu did not paint")
        self.assertLess(
            _last_used_row(lines), 74, "horizontal must not be stretched to fill"
        )

    def test_no_shape_keeps_the_classic_spacing(self):
        lines = _run_menu(74, 109, shape=None)
        self.assertTrue(lines, "the menu did not paint")
        self.assertLess(
            _last_used_row(lines), 74, "a non-portrait window must not be filled"
        )


class VerticalMenuHelperTest(unittest.TestCase):
    def test_helper_reads_only_the_current_pid(self):
        script = r"""
        export HOME="$TMPH"
        mkdir -p "$HOME/.config/mix-manager"
        source ./Mix_Archive_Manager.sh >/dev/null 2>&1
        MP_GUI_SESSION=1
        printf 'VERTICAL\n' > "$HOME/.config/mix-manager/gui_window_shape.$$"
        if _mp_gui_vertical_menu; then printf 'vertical\n'; else printf 'other\n'; fi
        printf 'HORIZONTAL\n' > "$HOME/.config/mix-manager/gui_window_shape.$$"
        if _mp_gui_vertical_menu; then printf 'vertical\n'; else printf 'other\n'; fi
        MP_GUI_SESSION=0
        printf 'VERTICAL\n' > "$HOME/.config/mix-manager/gui_window_shape.$$"
        if _mp_gui_vertical_menu; then printf 'vertical\n'; else printf 'other\n'; fi
        """
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, TMPH=tmp, MP_GUI_SESSION="1")
            proc = subprocess.run(
                [BASH, "--noprofile", "--norc", "-c", script],
                cwd=str(ROOT), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode()[-2000:])
        self.assertEqual(proc.stdout.decode().split(), ["vertical", "other", "other"])

    def test_the_fill_is_gated_on_the_portrait_helper(self):
        text = MANAGER.read_text(encoding="utf-8")
        start = text.index("_mp_gui_vertical_menu() {")
        chunk = text[start:start + 600]
        self.assertIn("gui_window_shape.$$", chunk)
        self.assertIn("VERTICAL", chunk)
        menu = text[text.index("local _fill_rows=0 _fill_gaps_on=0"):]
        menu = menu[:menu.index("local frame_buf=")]
        self.assertIn("_mp_gui_vertical_menu", menu)


if __name__ == "__main__":
    unittest.main()
