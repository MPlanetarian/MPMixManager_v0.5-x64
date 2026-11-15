#!/usr/bin/env python3
"""Arrow-key browsing of the playlist in MP Audio Player (Mix_Archive_Manager option 1).

The player is reached from Mix_Archive_Manager.sh option 1, which runs
``MP_Audio_Player.py --interactive``. Its playlist cursor is moved by Up/Down.
Commit 46b1ecd ("Add the Favorites DJ Set exporter") split the Down binding
into ``j`` plus a Shift+J export branch and dropped the escape sequences, so
Down did nothing while Up still worked. A held arrow also arrives as one read
containing several sequences, and comparing that whole burst against a single
binding matched nothing, so fast repeats were discarded too.

These tests drive the same entry point the terminal uses: one ``read()`` burst
into ``_consume_input``. They assert the cursor moves, the selection is what
plays on Enter, and that nothing regressed for the other bindings.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import MP_Audio_Player as player  # noqa: E402


def _track(index):
    return {
        "name": f"Mix {index}.flac",
        "path": f"/mixes/mix-{index}.flac",
        "ext": "FLAC",
        "size_mb": 10.0,
    }


def _ui(track_count=8, mode="MIX_ARCHIVE"):
    ui = player.TerminalPlayerUI.__new__(player.TerminalPlayerUI)
    ui.mode = mode
    ui.folder_name = "ALL"
    ui.keep_library = False
    ui._user_library_mode = None
    ui.is_remote = True  # route commands through _send_cmd, no audio hardware
    ui.engine = None
    ui.scanner = MagicMock()
    ui.scanner.base_dir = Path("/tmp/mix-archive")
    ui.scanner.mixes = [_track(i) for i in range(track_count)]
    ui.scanner.get_mixes.side_effect = (
        lambda query="": [t for t in ui.scanner.mixes if query.lower() in t["name"].lower()]
        if query
        else list(ui.scanner.mixes)
    )
    ui.export_mode = False
    ui.eq_naming_mode = False
    ui.search_mode = False
    ui.search_query = ""
    ui.selected_row = 0
    ui.scroll_offset = 0
    ui.cached_status = {"mode": mode, "title": "Mix 0", "path": ""}
    ui.notification_msg = ""
    ui.notification_time = 0.0
    ui.equalizer_hud_active = False
    ui.inspector_hud_active = False
    ui.ui_size_mode = "NORMAL"
    ui._playlist_clear_armed = False
    ui._inbuf = b""
    ui._reset_click_targets()
    return ui


class SingleKeyNavigationTests(unittest.TestCase):
    def test_down_arrow_moves_the_cursor_forward(self):
        ui = _ui()
        ui._consume_input(b"\x1b[B")
        self.assertEqual(ui.selected_row, 1)

    def test_up_arrow_moves_the_cursor_back(self):
        ui = _ui()
        ui.selected_row = 3
        ui._consume_input(b"\x1b[A")
        self.assertEqual(ui.selected_row, 2)

    def test_ss3_variants_work_for_both_arrows(self):
        ui = _ui()
        ui._consume_input(b"\x1bOB")
        self.assertEqual(ui.selected_row, 1)
        ui._consume_input(b"\x1bOA")
        self.assertEqual(ui.selected_row, 0)

    def test_j_and_k_still_scroll(self):
        ui = _ui()
        ui._consume_input(b"j")
        ui._consume_input(b"j")
        self.assertEqual(ui.selected_row, 2)
        ui._consume_input(b"k")
        self.assertEqual(ui.selected_row, 1)

    def test_cursor_clamps_at_both_ends(self):
        ui = _ui(track_count=3)
        ui._consume_input(b"\x1b[A")
        self.assertEqual(ui.selected_row, 0, "Up on the first row must stay put")
        for _ in range(10):
            ui._consume_input(b"\x1b[B")
        self.assertEqual(ui.selected_row, 2, "Down on the last row must stay put")

    def test_down_then_enter_plays_the_selected_mix(self):
        ui = _ui()
        ui._send_cmd = MagicMock()
        ui._consume_input(b"\x1b[B\x1b[B")
        ui._consume_input(b"\r")
        ui._send_cmd.assert_called_with("play", file="/mixes/mix-2.flac")

    def test_page_keys_still_jump_five_rows(self):
        ui = _ui(track_count=9)
        ui._consume_input(b"\x1b[6~")
        self.assertEqual(ui.selected_row, 5)
        ui._consume_input(b"\x1b[5~")
        self.assertEqual(ui.selected_row, 0)


class BurstNavigationTests(unittest.TestCase):
    """A held arrow key arrives as several sequences in one read()."""

    def test_repeated_down_arrow_in_one_read_moves_every_row(self):
        ui = _ui()
        ui._consume_input(b"\x1b[B" * 4)
        self.assertEqual(ui.selected_row, 4)

    def test_repeated_up_arrow_in_one_read_moves_every_row(self):
        ui = _ui()
        ui.selected_row = 5
        ui._consume_input(b"\x1b[A" * 3)
        self.assertEqual(ui.selected_row, 2)

    def test_mixed_up_and_down_burst_nets_out(self):
        ui = _ui()
        ui._consume_input(b"\x1b[B\x1b[B\x1b[B\x1b[A")
        self.assertEqual(ui.selected_row, 2)

    def test_burst_stops_at_the_end_of_the_playlist(self):
        ui = _ui(track_count=3)
        ui._consume_input(b"\x1b[B" * 20)
        self.assertEqual(ui.selected_row, 2)

    def test_partial_sequence_survives_until_the_next_read(self):
        ui = _ui()
        ui._consume_input(b"\x1b[")
        self.assertEqual(ui.selected_row, 0, "a lone ESC [ is not a key press")
        ui._consume_input(b"B")
        self.assertEqual(ui.selected_row, 1)

    def test_partial_sequence_detection_holds_a_bare_escape_byte(self):
        self.assertEqual(player.split_key_burst(b"\x1b"), ([b"\x1b"], b""))
        keys, partial = player.split_key_burst(b"\x1b[")
        self.assertEqual(keys, [])
        self.assertEqual(partial, b"\x1b[")

    def test_split_key_burst_keeps_plain_text_together(self):
        keys, partial = player.split_key_burst(b"hello")
        self.assertEqual(keys, [b"hello"])
        self.assertEqual(partial, b"")

    def test_split_key_burst_separates_sequences_from_text(self):
        keys, partial = player.split_key_burst(b"j\x1b[Bj")
        self.assertEqual(keys, [b"j", b"\x1b[B", b"j"])
        self.assertEqual(partial, b"")


class SearchModeArrowTests(unittest.TestCase):
    def test_down_arrow_in_search_mode_still_navigates(self):
        ui = _ui()
        ui.search_mode = True
        ui.search_query = "Mix"
        ui._consume_input(b"\x1b[B")
        self.assertEqual(ui.selected_row, 1)

    def test_down_arrow_does_not_enter_search_mode(self):
        ui = _ui()
        ui._consume_input(b"\x1b[B")
        self.assertFalse(ui.search_mode)


# ---------------------------------------------------------------------------
# The user-visible symptom: browse the playlist with the arrow keys.
#
# Asserting on selected_row alone would have passed for a renderer that ignored
# the row, so these drive the real _draw_frame and read which playlist line
# carries the theme's selection bar. That is the row the user sees highlighted.
# ---------------------------------------------------------------------------

import contextlib  # noqa: E402
import io  # noqa: E402
import re  # noqa: E402

NAMES = [f"MPlanetarian - Stream {i:02d}.flac" for i in range(1, 9)]
ANSI_RE = re.compile(r"\x1b\[[0-9;?<>=]*[A-Za-z~]")


class _RenderScanner:
    base_dir = "/tmp"
    playlist_path = ""

    def __init__(self):
        self.mixes = [
            {"name": n, "path": f"/archive/{n}", "ext": "FLAC", "size_mb": 10.0}
            for n in NAMES
        ]

    def get_mixes(self, query=""):
        return self.mixes

    def get_latest_mix(self):
        return self.mixes[0]


def _render_ui():
    ui = _ui(track_count=0)
    ui.is_remote = False
    ui.engine = None
    ui.scanner = _RenderScanner()
    ui.visualizer = player.EqualizerVisualizer()
    ui.eq_manager = player.EqualizerManager()
    ui.mp_visualizer_on = False
    ui.mp_visualizer_preset_id = 0
    ui.favorites = player.FavoritesStore()
    ui._duration_cache = {}
    ui._duration_queue = None
    ui._duration_in_queue = set()
    ui._duration_worker_started = True
    ui._last_seen_bt_device = None
    ui._last_seen_bt_state = None
    ui._spek_job_running = False
    ui.running = True
    ui.cached_status = {
        "mode": "MIX_ARCHIVE",
        "title": NAMES[0],
        "path": f"/archive/{NAMES[0]}",
        "state": "playing",
        "position_fmt": "00:00",
        "duration_fmt": "10:00",
        "progress_pct": 0.0,
        "levels": [0.0] * 24,
        "peaks": [0.0] * 24,
        "levels_left": [0.0] * 24,
        "levels_right": [0.0] * 24,
        "peaks_left": [0.0] * 24,
        "peaks_right": [0.0] * 24,
        "waveform": [0.0] * 80,
        "rms_level": 0.0,
        "peak_db": -60.0,
        "volume": 85,
        "queue": [],
        "queue_len": 0,
    }
    ui._get_status = lambda: ui.cached_status
    # The player always paints a frame before the first key press, and that
    # first frame snaps the cursor onto the mix that is already playing
    # (_sync_initial_selection). Draw it here so the tests start from the same
    # state the user actually has, instead of from an unpainted screen.
    _highlighted_row(ui)
    return ui


def _highlighted_row(ui):
    """Which playlist mix is drawn with the selection bar, or None."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ui._draw_frame()
    for line in buf.getvalue().split("\n"):
        if "48;2;" not in line:  # the theme highlight background
            continue
        text = ANSI_RE.sub("", line)
        for name in NAMES:
            if name in text:
                return name
    return None


class PlaylistBrowsingTests(unittest.TestCase):
    """The screen follows the arrow keys, not just the internal index."""

    def test_down_arrow_moves_the_highlight_down_one_mix(self):
        ui = _render_ui()
        self.assertEqual(_highlighted_row(ui), NAMES[0])
        ui._consume_input(b"\x1b[B")
        self.assertEqual(_highlighted_row(ui), NAMES[1])

    def test_up_arrow_moves_the_highlight_up_one_mix(self):
        ui = _render_ui()
        ui._consume_input(b"\x1b[B" * 3)
        self.assertEqual(_highlighted_row(ui), NAMES[3])
        ui._consume_input(b"\x1b[A")
        self.assertEqual(_highlighted_row(ui), NAMES[2])

    def test_held_down_arrow_walks_the_whole_playlist(self):
        ui = _render_ui()
        for expected in (1, 2, 3):
            ui._consume_input(b"\x1b[B")
            self.assertEqual(_highlighted_row(ui), NAMES[expected])
        # One read carrying the rest of a held key must not be swallowed.
        ui._consume_input(b"\x1b[B" * 4)
        self.assertEqual(_highlighted_row(ui), NAMES[7])

    def test_down_arrow_never_leaves_the_first_mix_selected(self):
        """The reported bug: it played the first mix whatever the playlist showed."""
        ui = _render_ui()
        ui._consume_input(b"\x1b[B" * 3)
        self.assertNotEqual(
            ui.selected_row,
            0,
            "Down arrow left the cursor on the first mix, so Enter replayed it",
        )


if __name__ == "__main__":
    unittest.main()
