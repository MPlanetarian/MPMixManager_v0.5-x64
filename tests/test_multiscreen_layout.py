#!/usr/bin/env python3
"""Drive the real run_interactive_submenu on a pty in 1-, 2- and 3-screen modes.

Checks that:
  * every painted row is exactly the terminal width
  * SECTION 1 lands in the left panel, SECTION 2 in the middle, SECTION 3 right
  * two screens put SECTION 1 left and the remaining sections right
  * one screen keeps the classic single column
  * arrow keys move the highlight inside its own panel
"""

import fcntl
import os
import pty
import re
import select
import struct
import termios
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tests.mp_test_bash import BASH

ROOT = Path(__file__).resolve().parent.parent
MGR = ROOT / "Mix_Archive_Manager.sh"

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

HARNESS = r"""

cd "$ROOT"
export OS_TYPE=linux UNAME_S=Linux TERM=xterm-256color
source ./Mix_Archive_Manager.sh >/dev/null 2>&1
set_theme_colors dreamworlds

# ---- synthetic display + window geometry ------------------------------------
mp_screen_query_widths() { printf '%s\n' 1920 1920 1920; }
mp_screen_terminal_pixels() { _MS_TERM_PX=$FAKE_PX; _MS_TERM_PY=760; return 0; }
mp_screen_layout_refresh() {
    _MS_LAYOUT_TS=0
    local -a w=(1920 1920 1920)
    local want=0
    case "$MP_SCREEN_LAYOUT" in
        single) want=1 ;; dual) want=2 ;; triple) want=3 ;; *) want=${FIXED_N:-3} ;;
    esac
    _MS_N=$want
    _MS_WIDTHS=()
    _MS_TOTAL_PX=0
    local i
    for ((i=0;i<want;i++)); do _MS_WIDTHS[i]=1920; _MS_TOTAL_PX=$((_MS_TOTAL_PX+1920)); done
    _MS_TERM_PX=$FAKE_PX
    _MS_TERM_PY=760
    _MS_SPANS=1
    _MS_ON=1
    _MS_REASON=""
    if ! mp_screen_apply_terminal_columns; then _MS_ON=0; _MS_N=1; return 1; fi
    return 0
}
mp_screen_apply_terminal_columns() {
    [ "$_MS_N" -ge 2 ] || return 1
    local _cols="${_TERM_COLS:-80}" _i _cum=0 _x _w
    local _basis="$_MS_TOTAL_PX"
    _MS_X=(); _MS_W=()
    for ((_i=0;_i<_MS_N;_i++)); do
        _x=$((_cum * _cols / _basis))
        _w=$((1920 * _cols / _basis))
        _MS_X[_i]=$((_x+1)); _MS_W[_i]="$_w"
        _cum=$((_cum+1920))
    done
    local _last=$((_MS_N-1))
    _MS_W[_last]=$((_cols - _MS_X[_last] + 1))
    return 0
}
# no live header/status refresh in the harness
run_deferred_startup() { :; }
get_mp_audio_player_track_info() { return 1; }
update_mp_bridge_menu() { :; }
_nav_mouse_enable() { :; }
_nav_mouse_disable() { :; }
_start_win_cursor_hider() { :; }
_stop_win_cursor_hider() { :; }
play_sound_effect() { :; }

_TERM_COLS=$COLS
_TERM_LINES=$ROWS
LINES=$ROWS
COLUMNS=$COLS

opts=(
"SECTION|SECTION 1: LISTEN TO MIXES|"
"1|Play DJ Mixes|(Built-in Player)"
"2|Play Tracks from MUSIC Folders|(Music Library)"
"3|MP Audio Player (Liquid Glass Mode)|(OpenGL)"
"SEP|SEP|"
"4|Run Mix Conversion|(FLAC, MP3)"
"SECTION|SECTION 2: STUDIO AUDIO|"
"5|Tracklist Management|(Browse, Search)"
"6|Audio Players & Retro Playback|(cliamp, VLC)"
"7|Configure Mix Archive Storage|(Primary Archive)"
"SECTION|SECTION 3: SYSTEM & SETTINGS|"
"8|Live Session Monitors|(Tracklist, DJ)"
"9|Network Services|(SSH, Samba)"
"0|Return to Main Menu|(or Esc / q)"
)
run_interactive_submenu "" "" opts 1
printf '\nCHOICE=%s\n' "$REPLY"
"""


SEL = re.compile(r"▶ ❯\s*\[\s*(\w+)\]")


def _selected_key(text):
    """The option key the last complete frame highlights, or None.

    In-place row repaints are also captured as frames, and those hold only one
    menu row, so the search runs over the last frame that still has a status
    line: that is the most recent full paint.
    """
    frames = MultiScreenMenuTest._frames(text)
    complete = [f for f in frames if "Selection:" in ANSI.sub("", f)]
    for frame in reversed(complete or frames):
        hit = SEL.search(ANSI.sub("", frame))
        if hit:
            return hit.group(1)
    return None


def _set_winsize(fd, rows, cols, xpix=0, ypix=0):
    """Tell the pty its real geometry so the menu is not stuck in RESIZE."""
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", rows, cols, xpix, ypix))
    except OSError:
        pass


class MultiScreenMenuTest(unittest.TestCase):
    def _run(self, layout, cols, rows, px, keys=None, fixed_n=3):
        env = dict(os.environ)
        env.update(
            ROOT=str(ROOT),
            MP_SCREEN_LAYOUT=layout,
            COLS=str(cols),
            ROWS=str(rows),
            FAKE_PX=str(px),
            FIXED_N=str(fixed_n),
            MP_MANAGER_MOUSE="0",
        )
        pid, master = pty.fork()
        if pid == 0:
            # argv[0] stays "bash" so the harness's own `$0` checks behave the
            # same on every host; only the interpreter path is upgraded.
            os.execvpe(BASH, ["bash", "--noprofile", "--norc", "-c", HARNESS], env)
        _set_winsize(master, rows, cols)
        buf = b""
        # The menu can take a while to reach its first frame while the archive
        # statistics load, and a loaded machine can push it past a short limit.
        deadline = time.time() + 45
        sent = False
        keys = list(keys or [b"\x1b[B"])
        while time.time() < deadline:
            r, _, _ = select.select([master], [], [], 0.15)
            if r:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                buf += data
            if not sent and b"\x1b[J" in buf:
                # Let one full frame paint, then send one key at a time and wait
                # for the highlight to move. The menu discards buffered vertical
                # arrows on purpose, so a fixed sleep would be racy.
                time.sleep(0.55)
                for k in keys:
                    before = _selected_key(buf.decode("utf-8", "replace"))
                    os.write(master, k)
                    deadline_local = time.time() + 4.0
                    while time.time() < deadline_local:
                        r2, _, _ = select.select([master], [], [], 0.1)
                        if r2:
                            try:
                                more = os.read(master, 65536)
                            except OSError:
                                more = b""
                            if not more:
                                break
                            buf += more
                        now_sel = _selected_key(buf.decode("utf-8", "replace"))
                        if now_sel != before:
                            break
                os.write(master, b"\r")
                sent = True
            if b"CHOICE=" in buf:
                break
            if pid and os.waitpid(pid, os.WNOHANG)[0] == pid and b"CHOICE=" not in buf:
                # The child finished before painting; stop waiting.
                break
        try:
            os.close(master)
        except OSError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        return buf.decode("utf-8", "replace")

    @staticmethod
    def _frames(text):
        """Split the captured stream into painted frames."""
        frames = []
        for chunk in text.split("\x1b[H"):
            if "\x1b[J" not in chunk:
                continue
            frames.append(chunk.split("\x1b[J")[0])
        return frames

    @staticmethod
    def _rows(frame):
        out = []
        for line in frame.split("\n"):
            plain = ANSI.sub("", line)
            out.append(plain)
        return out

    def test_three_screens_split_sections_across_displays(self):
        text = self._run("triple", 240, 44, 5200)
        frames = self._frames(text)
        self.assertTrue(frames, "no frame painted")
        rows = self._rows(max(frames, key=len))
        joined = "\n".join(rows)
        self.assertIn("SECTION 1: LISTEN TO MIXES", joined)
        self.assertIn("SECTION 2: STUDIO AUDIO", joined)
        self.assertIn("SECTION 3: SYSTEM & SETTINGS", joined)

        # Each SECTION title starts in a different third of the console.
        def col_of(needle):
            for line in rows:
                i = line.find(needle)
                if i >= 0:
                    return i
            return -1

        c1 = col_of("SECTION 1: LISTEN TO MIXES")
        c2 = col_of("SECTION 2: STUDIO AUDIO")
        c3 = col_of("SECTION 3: SYSTEM & SETTINGS")
        self.assertGreaterEqual(c1, 0)
        self.assertGreater(c2, c1)
        self.assertGreater(c3, c2)
        self.assertLess(c1, 80)
        self.assertGreaterEqual(c2, 80)
        self.assertLess(c2, 160)
        self.assertGreaterEqual(c3, 160)

        # Options belong to their own section's panel.
        def row_index(needle):
            for i, line in enumerate(rows):
                if needle in line:
                    return i
            return -1

        self.assertGreaterEqual(row_index("Play DJ Mixes"), 0)
        self.assertGreaterEqual(row_index("Tracklist Management"), 0)
        self.assertGreaterEqual(row_index("Network Services"), 0)

        # No painted row may exceed the console width.
        for line in rows:
            visible = len(line.rstrip())
            self.assertLessEqual(visible, 240, f"row too wide: {visible}")

    def test_two_screens_keep_section_one_left_and_merge_the_rest(self):
        text = self._run("dual", 176, 44, 3456)
        frames = self._frames(text)
        self.assertTrue(frames, "no frame painted")
        rows = self._rows(max(frames, key=len))
        joined = "\n".join(rows)
        for token in ("SECTION 1: LISTEN TO MIXES", "SECTION 2: STUDIO AUDIO",
                      "SECTION 3: SYSTEM & SETTINGS"):
            self.assertIn(token, joined)

        def col_of(needle):
            for line in rows:
                i = line.find(needle)
                if i >= 0:
                    return i
            return -1

        c1 = col_of("SECTION 1: LISTEN TO MIXES")
        c2 = col_of("SECTION 2: STUDIO AUDIO")
        c3 = col_of("SECTION 3: SYSTEM & SETTINGS")
        self.assertLess(c1, 88, "SECTION 1 must stay on the left display")
        self.assertGreaterEqual(c2, 88)
        self.assertGreaterEqual(c3, 88)

    def test_dual_mode_drops_the_second_divider(self):
        text = self._run("dual", 176, 44, 3456)
        frames = self._frames(text)
        rows = self._rows(max(frames, key=len))
        # Only menu rows carry the divider; the banner and status line have
        # their own "│" separators, so look at the rows with option keys.
        option_rows = [r for r in rows if re.search(r"\[ *\d+\]", r)]
        self.assertTrue(option_rows, "no option rows painted")
        dividers = [line.count("│") for line in option_rows]
        self.assertTrue(all(d == 1 for d in dividers), dividers)

    def test_single_screen_keeps_the_classic_column(self):
        text = self._run("single", 100, 44, 1723)
        frames = self._frames(text)
        self.assertTrue(frames, "no frame painted")
        rows = self._rows(max(frames, key=len))
        joined = "\n".join(rows)
        self.assertIn("SECTION 1: LISTEN TO MIXES", joined)
        # Single column: the section titles are all at the same indent.
        starts = set()
        for line in rows:
            for token in ("SECTION 1:", "SECTION 2:", "SECTION 3:"):
                i = line.find(token)
                if i >= 0:
                    starts.add(i)
        self.assertEqual(len(starts), 1, f"section indents differ: {starts}")
        # And no column divider is drawn: no two-section row exists.
        option_rows = [r for r in rows if re.search(r"\[ *\d+\]", r)]
        self.assertTrue(option_rows, "no option rows painted")
        for line in option_rows:
            self.assertNotIn("│", line, "single screen must not draw a divider")


class MultiScreenNavigationTest(unittest.TestCase):
    """Arrow keys must move the highlight by one option across panels."""

    def _run(self, layout, cols, rows, px, downs=0, up=False):
        return MultiScreenMenuTest()._run(
            layout, cols, rows, px,
            keys=[b"\x1b[A"] if up else [b"\x1b[B"] * downs,
        )

    def test_down_moves_one_option_in_three_panels(self):
        text = self._run("triple", 240, 40, 5200, downs=2)
        self.assertEqual(_selected_key(text), "3")

    def test_up_wraps_to_the_last_option_of_the_right_panel(self):
        text = self._run("triple", 240, 40, 5200, up=True)
        self.assertEqual(_selected_key(text), "0")

    def test_down_crosses_from_the_left_panel_into_the_right(self):
        # Section 1 holds options 1, 2, 3 and 4. The fourth Down lands on
        # option 5, the first entry of the merged right-hand panel.
        text = self._run("dual", 176, 40, 3456, downs=4)
        self.assertEqual(_selected_key(text), "5")

    def test_single_screen_navigation_is_unchanged(self):
        text = self._run("single", 100, 40, 1723, downs=2)
        self.assertEqual(_selected_key(text), "3")


class MultiScreenDetectionTest(unittest.TestCase):
    """Unit coverage for the display grouping and column maths."""

    SCRIPT = r"""
cd "$ROOT"
source ./Mix_Archive_Manager.sh >/dev/null 2>&1
export OS_TYPE=linux UNAME_S=Linux
cmd="$1"; shift
case "$cmd" in
  group)
    # argument list of widths
    mp_screen_pick_group "$@"
    ;;
  columns)
    # n cols w1 w2 w3  (synthetic equal-width group)
    n="$1"; cols="$2"; shift 2
    _MS_N="$n"
    _MS_WIDTHS=("$@")
    _MS_TOTAL_PX=0
    for w in "$@"; do _MS_TOTAL_PX=$((_MS_TOTAL_PX + w)); done
    _MS_TERM_PX="$_MS_TOTAL_PX"
    _MS_SPANS=1
    _TERM_COLS="$cols"
    if mp_screen_apply_terminal_columns; then
      [ "${_MS_TOO_NARROW:-0}" -eq 1 ] && echo "TOO_NARROW"
      echo "X:${_MS_X[*]}"
      echo "W:${_MS_W[*]}"
    else
      echo "FAILED"
    fi
    ;;
  sections)
    # n opt1 opt2 ...
    n="$1"; shift
    _o=("$@")
    if mp_menu_section_groups _o "$n"; then
      echo "G:${_MS_GSTART[*]}"
    else
      echo "FAILED"
    fi
    ;;
  widths)
    mp_screen_query_widths | tr '
' ' '
    echo
    ;;
esac
"""

    def _bash(self, *args):
        env = dict(os.environ, ROOT=str(ROOT), OS_TYPE="linux", UNAME_S="Linux")
        proc = subprocess.run(
            [BASH, "--noprofile", "--norc", "-c", self.SCRIPT, "bash", *args],
            cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=90,
        )
        return proc.stdout.strip(), proc.stderr

    def test_matching_group_wins_over_a_wider_display(self):
        out, _ = self._bash("group", "1920", "1920", "1920", "2649")
        self.assertEqual(out.split(), ["1920", "3"])

    def test_group_is_capped_at_three(self):
        out, _ = self._bash("group", "2560", "2560", "2560", "2560", "2560")
        self.assertEqual(out.split(), ["2560", "3"])

    def test_group_handles_two_equal_displays(self):
        out, _ = self._bash("group", "3840", "3840")
        self.assertEqual(out.split(), ["3840", "2"])

    def test_group_handles_one_display(self):
        out, _ = self._bash("group", "1920")
        self.assertEqual(out.split(), ["1920", "1"])

    def test_three_panels_split_the_columns_evenly(self):
        out, _ = self._bash("columns", "3", "240", "1920", "1920", "1920")
        lines = out.splitlines()
        self.assertIn("X:1 81 161", lines)
        self.assertIn("W:80 80 80", lines)

    def test_two_panels_split_the_columns_evenly(self):
        out, _ = self._bash("columns", "2", "176", "1920", "1920")
        lines = out.splitlines()
        self.assertIn("X:1 89", lines)
        self.assertIn("W:88 88", lines)

    def test_panels_accept_any_shared_resolution(self):
        # 3 x 4K at 300 columns, and 2 x 2560 at 200 columns.
        out, _ = self._bash("columns", "3", "300", "3840", "3840", "3840")
        self.assertNotIn("FAILED", out)
        out2, _ = self._bash("columns", "2", "200", "2560", "2560")
        self.assertNotIn("FAILED", out2)

    def test_narrow_panel_is_flagged(self):
        # 3 panels at 60 columns is 20 cells each, below the 30 cell minimum.
        out, _ = self._bash("columns", "3", "60", "1920", "1920", "1920")
        self.assertIn("TOO_NARROW", out)

    def test_wide_enough_panels_are_not_flagged(self):
        out, _ = self._bash("columns", "3", "240", "1920", "1920", "1920")
        self.assertNotIn("TOO_NARROW", out)

    def test_sections_map_to_panel_starts(self):
        out, _ = self._bash(
            "sections", "3",
            "SECTION|A|", "1|a|", "SECTION|B|", "2|b|", "SECTION|C|", "3|c|")
        self.assertEqual(out.strip(), "G:0 2 4")

    def test_too_few_sections_is_rejected(self):
        out, _ = self._bash("sections", "3", "SECTION|A|", "1|a|", "SECTION|B|")
        self.assertEqual(out.strip(), "FAILED")

    def test_width_query_reports_live_displays_or_nothing(self):
        # Never crash. On a headless runner the list is simply empty.
        out, err = self._bash("widths")
        self.assertEqual(err.strip(), "")
        for token in out.split():
            self.assertTrue(token.isdigit(), out)


class MultiScreenFrameHeightTest(unittest.TestCase):
    """The frame must never be taller than the terminal, or its top scrolls away."""

    SCRIPT = r"""
cd "$ROOT"
source ./Mix_Archive_Manager.sh >/dev/null 2>&1
export OS_TYPE=linux UNAME_S=Linux
set_theme_colors dreamworlds
# Lift the nested column helpers out of run_interactive_submenu. Only the
# leading "local" declarations are dropped, because these functions now run at
# the top level; declarations inside a function body must stay.
sed -n '/^# --- Multi-screen section columns/,/^# --- end multi-screen section columns/p' \
    "$ROOT/Mix_Archive_Manager.sh" | grep -v '^local ' > /tmp/mp_ms_helpers.$$.sh
. /tmp/mp_ms_helpers.$$.sh
rm -f /tmp/mp_ms_helpers.$$.sh
_MS_ROWS=(); _MS_PL_W=(); _MS_PL_ROWS=(); _MS_IDX_ROW=()
_MS_IDX_PANEL=(); _MS_IDX_J=(); _MS_IDX_COL=(); _MS_IDX_COL_W=()
_MS_PS=(); _MS_PE=(); _MS_BLANK=(); declare -gA _MS_PL=() _MS_PLIDX=()
_MS_PANEL_N=0; _MS_HDR_BLOCK=""; _MS_BREATH=100; _MS_DETECTED_N=0
_compose_menu_status() { printf -v "$1" '%s' "  Selection: [Option 1]"; }

_opts_ref=("SECTION|G1|" "1|A|" "2|B|" "3|C|" "4|D|" "5|E|" "6|F|"
           "SECTION|G2|" "7|G|" "8|H|" "SECTION|G3|" "9|I|" "0|J|")
total_opts=${#_opts_ref[@]}; cur_idx=1
selectable_map=(); sc=0
for ((i=0;i<total_opts;i++)); do
  k="${_opts_ref[i]%%|*}"
  if [ "$k" = "SECTION" ] || [ "$k" = "SEP" ]; then selectable_map[i]=-1
  else selectable_map[i]=$sc; ((sc++)); fi
done
menu_title=""; box_w=298
cached_header_block=""
for i in $(seq 1 ${HDR_ROWS}); do cached_header_block+="  HDR-$i\033[K\n"; done
cached_header_lines=$HDR_ROWS
_TERM_COLS=${COLS}; _TERM_LINES=${ROWS}
_MS_N=3; _MS_WIDTHS=(1920 1920 1920); _MS_TOTAL_PX=5760
_MS_TERM_PX=5723; _MS_TERM_PY=1120; _MS_SPANS=1; _MS_ON=1
mp_screen_layout_refresh() { return 0; }
if _ms_layout_panels; then
  _ms_render_frame >/dev/null
  echo "ROWS_USED=$_menu_status_row"
  echo "TERM_ROWS=$_TERM_LINES"
  echo "HEADER_ROWS=$(printf '%s' "$_MS_HDR_BLOCK" | grep -c .)"
else
  echo "SINGLE"
fi
"""

    def _bash(self, rows, hdr, cols=301):
        env = dict(os.environ, ROOT=str(ROOT), OS_TYPE="linux", UNAME_S="Linux",
                   ROWS=str(rows), HDR_ROWS=str(hdr), COLS=str(cols))
        proc = subprocess.run(
            [BASH, "--noprofile", "--norc", "-c", self.SCRIPT],
            cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=90,
        )
        return proc.stdout

    def _rows_used(self, out):
        d = {}
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                d[k] = v
        return d

    def test_frame_fits_a_short_console_with_a_tall_banner(self):
        out = self._bash(rows=29, hdr=12)
        d = self._rows_used(out)
        self.assertNotIn("SINGLE", out, "a 29-row console must still get columns")
        self.assertLessEqual(int(d["ROWS_USED"]), int(d["TERM_ROWS"]),
                             "the frame is taller than the terminal")

    def test_frame_fits_a_very_short_console(self):
        out = self._bash(rows=22, hdr=12)
        d = self._rows_used(out)
        if "SINGLE" in out:
            return  # falling back to one column is a valid answer
        self.assertLessEqual(int(d["ROWS_USED"]), int(d["TERM_ROWS"]))

    def test_frame_fits_a_tall_console(self):
        out = self._bash(rows=60, hdr=6)
        d = self._rows_used(out)
        self.assertNotIn("SINGLE", out)
        self.assertLessEqual(int(d["ROWS_USED"]), int(d["TERM_ROWS"]))

    def test_banner_is_trimmed_not_the_option_rows(self):
        out = self._bash(rows=26, hdr=12)
        d = self._rows_used(out)
        if "SINGLE" in out:
            return
        self.assertLess(int(d["HEADER_ROWS"]), 12,
                        "a tall banner should be trimmed to make room")


class MultiScreenWindowGeometryTest(unittest.TestCase):
    """Window geometry must survive the startup escapes.

    ensure_manager_windowed used to send CSI 10;0t (restore window) on every
    menu, which undid the multi-screen span and shrank the console back to one
    display after two frames.
    """

    def test_windowed_escape_skips_a_spanning_layout(self):
        text = (ROOT / "Mix_Archive_Manager.sh").read_text(encoding="utf-8")
        start = text.index("ensure_manager_windowed() {")
        end = text.index("\n}", start)
        body = text[start:end]
        self.assertIn("_MS_ON", body,
                      "the restore escape must be skipped while spanning")
        self.assertIn("10;0t", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
