#!/usr/bin/env python3
"""Tracklist viewer text stays inside the window frame.

The viewer is drawn inside a borderless terminal window whose rounded frame
overlaps the terminal grid. Text used to start at column 1 (under the frame)
and long track titles used to run past the right edge. Every line is now drawn
inside a padded content box, and the header title is centred on that box using
real terminal cell widths so the emoji cannot push it off centre.
"""

import fcntl
import os
import pty
import re
import select
import signal
import struct
import tempfile
import termios
import time
import unicodedata
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VIEWER = REPO_ROOT / "scripts" / "view_tracklist_console.sh"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def display_cells(text: str) -> int:
    """Terminal columns used by text, counting wide glyphs as two cells."""
    width = 0
    for ch in text:
        if unicodedata.combining(ch) or ch in "\ufe0e\ufe0f\u200d":
            continue
        if ord(ch) >= 0x1F000 or unicodedata.east_asian_width(ch) in ("W", "F"):
            width += 2
        else:
            width += 1
    return width


def write_wide_tracklist(path: Path) -> Path:
    lines = [
        "==================================================",
        "RELEASE INFO & SOURCE MANIFEST",
        "==================================================",
        "Title: A Deliberately Long Release Title For Clipping",
        "==================================================",
        "TRACKLIST (Extracted from DJ Database)",
        "==================================================",
    ]
    lines += [
        f"{100 + i:03d}. Very Long Artist Name Number {i} - An Extremely Long Track Title "
        f"That Would Run Past The Frame (Extended Club Remix {i})"
        for i in range(1, 21)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_viewer(tracklist: Path, rows: int = 35, columns: int = 95,
               keys=(), timeout: float = 5.0) -> str:
    """Drive the viewer in a real PTY and return the final rendered frame."""
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child process
        os.close(master)
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        for fd in (0, 1, 2):
            os.dup2(slave, fd)
        os.close(slave)
        os.environ.update({"TERM": "xterm-256color", "LINES": str(rows),
                           "COLUMNS": str(columns)})
        os.execlp("bash", "bash", str(VIEWER), str(tracklist))

    os.close(slave)
    out = b""

    def drain(delay: float = 0.3) -> None:
        nonlocal out
        while True:
            ready, _, _ = select.select([master], [], [], delay)
            if not ready:
                return
            try:
                chunk = os.read(master, 65536)
            except OSError:
                return
            if not chunk:
                return
            out += chunk

    drain(1.0)
    for key in keys:
        os.write(master, key)
        drain(0.4)
    try:
        os.close(master)
    except OSError:
        pass
    # Never block the run on a child that did not quit: a busy machine can
    # leave the viewer alive past the last drain, and an unbounded waitpid
    # turns that into a suite timeout. Poll, then reap it by force.
    deadline = time.monotonic() + max(5.0, timeout)
    while True:
        try:
            done, _status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            break
        if done:
            break
        if time.monotonic() >= deadline:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
            break
        time.sleep(0.05)
    # The last frame is the one on screen; earlier ones were cleared.
    return out.decode("utf-8", "ignore").split("\x1b[3J")[-1]


def frame_lines(raw: str):
    return [strip_ansi(line).rstrip("\r") for line in raw.split("\n")]


class TestTracklistViewerLayout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.tracklist = write_wide_tracklist(self.base / "Mix_Wide.txt")
    def tearDown(self):
        self.tmp.cleanup()

    def assert_inside_box(self, raw: str, columns: int):
        """No visible line may reach the frame on either side."""
        for line in frame_lines(raw):
            cells = display_cells(line)
            self.assertLessEqual(
                cells, columns - 2,
                f"line overflows the window ({cells} of {columns} cells): {line!r}")
            if line.strip():
                self.assertTrue(
                    line.startswith(" "),
                    f"line starts under the left frame: {line!r}")

    def test_frame_never_scrolls_and_keeps_the_top_rule(self):
        raw = run_viewer(self.tracklist)
        lines = frame_lines(raw)
        # One row per terminal row; a trailing newline used to scroll the frame.
        self.assertLessEqual(len(lines), 35)
        self.assertTrue(lines[0].strip().startswith("---"),
                        f"first frame row should be the top rule: {lines[0]!r}")

    def test_every_line_stays_inside_the_window_at_95_columns(self):
        self.assert_inside_box(run_viewer(self.tracklist, columns=95), 95)

    def test_every_line_stays_inside_the_window_at_80_columns(self):
        self.assert_inside_box(run_viewer(self.tracklist, columns=80), 80)

    def test_every_line_stays_inside_the_window_at_120_columns(self):
        self.assert_inside_box(run_viewer(self.tracklist, columns=120), 120)

    def test_status_message_is_clipped_to_the_window(self):
        # The export status contains an absolute path long enough to overflow.
        raw = run_viewer(self.tracklist, keys=[b"x"])
        self.assertIn("Tracklist exported", strip_ansi(raw))
        self.assert_inside_box(raw, 95)

    def test_header_title_is_centred_on_the_content_box(self):
        columns = 95
        raw = run_viewer(self.tracklist, columns=columns, rows=35)
        lines = frame_lines(raw)
        title = next(l for l in lines if "TRACKLIST VIEWER" in l)
        rule = next(l for l in lines if l.strip().startswith("---"))
        left = display_cells(title) - display_cells(title.lstrip())
        right = columns - left - display_cells(title.strip())
        # Emoji are two cells wide, so a perfect centre can be off by one cell.
        self.assertLessEqual(abs(left - right), 2,
                             f"title is not centred: {left} left vs {right} right")
        # The title must sit inside the same content box as the rules.
        rule_left = display_cells(rule) - display_cells(rule.lstrip())
        self.assertGreaterEqual(left, rule_left)

    def test_header_values_are_aligned_in_a_column(self):
        raw = run_viewer(self.tracklist)
        lines = frame_lines(raw)
        rows = [l for l in lines if l.lstrip().startswith("•")]
        self.assertTrue(rows, "header rows are missing")
        for label in ("File:", "Location:", "Platform:", "Theme:"):
            self.assertTrue(any(label in row for row in rows),
                            f"header row missing: {label}")
        # Every value starts in the same cell, after the fixed-width label field.
        starts = set()
        for row in rows:
            label_end = row.index(":", row.index("•")) + 1
            rest = row[label_end:]
            starts.add(label_end + len(rest) - len(rest.lstrip()))
        self.assertEqual(len(starts), 1,
                         f"header values are not aligned: {rows}")

    def test_help_rows_are_never_truncated_away(self):
        raw = strip_ansi(run_viewer(self.tracklist, columns=95, rows=35))
        for token in ("[Q]", "[C]", "[S]", "[T]", "[E]", "[G]", "[D]", "[X]", "[Y]"):
            self.assertIn(token, raw)

    def test_export_hint_drops_the_path_instead_of_cutting_it(self):
        raw = strip_ansi(run_viewer(self.tracklist, columns=100, rows=36))
        hint = next(l for l in raw.splitlines() if "[X] Export HTML" in l)
        self.assertNotIn("…", hint, f"the export hint was cut mid-path: {hint!r}")
        self.assertIn("[Y] Export TEXT", hint)

    def test_wide_window_shows_the_export_path(self):
        raw = strip_ansi(run_viewer(self.tracklist, columns=120, rows=40))
        self.assertIn("(saved to", raw)
        self.assertIn("TRACKLIST_EXPORTS/", raw)


if __name__ == "__main__":
    unittest.main()
