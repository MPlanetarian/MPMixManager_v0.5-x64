#!/usr/bin/env python3
"""Delete removes playlist rows immediately, and Shift selects several.

The old Del binding armed the whole-playlist clear and waited for a second
press, which looked like nothing had happened. Del now removes the selected
row (or the Shift selection) at once, while D keeps its clear-all meaning.
"""

import os
import sys
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

os.environ.setdefault("MP_DISABLE_AUDIO_OUTPUT", "1")
os.environ.setdefault("MP_DISABLE_BLUETOOTH_MONITOR", "1")

import MP_Audio_Player as player


class _Scanner:
    """The small slice of the scanner API the terminal playlist uses."""

    def __init__(self, rows):
        self.mixes = [dict(row) for row in rows]
        self.playlist_path = ""
        self.base_dir = PROJECT_ROOT
        self.folder_name = "ALL"
        self._search_cache_initialized = None

    def get_mixes(self, query: str = ""):
        return self.mixes


def _rows(count: int = 4):
    return [
        {
            "name": f"track {i}.flac",
            "path": str(PROJECT_ROOT / f"track{i}.flac"),
            "ext": "FLAC",
            "size_mb": 1.0,
            "duration_fmt": "03:00",
        }
        for i in range(count)
    ]


class TestPlaylistMultiSelectDelete(unittest.TestCase):
    def _ui(self, rows=None, mode="MUSIC"):
        rows = _rows() if rows is None else rows
        ui = player.TerminalPlayerUI(
            None, _Scanner(rows), is_remote=True, mode=mode, folder_name="ALL"
        )
        ui._send_cmd = MagicMock()
        return ui

    def test_delete_removes_only_the_cursor_row_at_once(self):
        ui = self._ui()
        ui.selected_row = 1

        ui._process_key(b"\x1b[3~")

        self.assertEqual([row["name"] for row in ui.scanner.mixes], ["track 0.flac", "track 2.flac", "track 3.flac"])
        self.assertFalse(ui._playlist_clear_armed)
        ui._send_cmd.assert_called_once_with("remove_tracks", paths=[str(PROJECT_ROOT / "track1.flac")])
        self.assertIn("Removed 1 track", ui.notification_msg)

    def test_shift_down_extends_and_delete_removes_the_whole_range(self):
        ui = self._ui()
        ui.selected_row = 0

        ui._process_key(b"\x1b[1;2B")  # Shift+Down: anchor 0, cursor 1
        ui._process_key(b"\x1b[1;2B")  # Shift+Down: cursor 2
        self.assertEqual(ui._selection_bounds(), (0, 2))
        self.assertEqual(ui._selected_indices(), [0, 1, 2])

        ui._process_key(b"\x1b[3~")

        self.assertEqual([row["name"] for row in ui.scanner.mixes], ["track 3.flac"])
        self.assertIsNone(ui._selection_anchor)
        self.assertEqual(ui.selected_row, 0)
        ui._send_cmd.assert_called_once_with(
            "remove_tracks",
            paths=[str(PROJECT_ROOT / f"track{i}.flac") for i in range(3)],
        )

    def test_plain_navigation_collapses_the_selection(self):
        ui = self._ui()
        ui._process_key(b"\x1b[B")
        ui._process_key(b"\x1b[1;2B")
        self.assertEqual(ui._selection_bounds(), (1, 2))

        ui._process_key(b"\x1b[B")

        self.assertIsNone(ui._selection_anchor)
        self.assertEqual(ui._selection_bounds(), (3, 3))

    def test_shift_click_selects_a_range_without_playing_or_dragging(self):
        ui = self._ui()
        ui.selected_row = 0
        ui._playlist_star_rows = {20: 0, 21: 1, 22: 2}
        ui._playlist_star_col = 8

        ui._on_favorites_click(5, 22, shift=True)

        self.assertEqual(ui._selection_bounds(), (0, 2))
        self.assertEqual(ui.selected_row, 2)
        self.assertIsNone(getattr(ui, "_playlist_drag_index", None))

    def test_d_keeps_the_clear_all_confirmation(self):
        ui = self._ui()
        ui._process_key(b"d")
        self.assertTrue(ui._playlist_clear_armed)
        ui._send_cmd.assert_not_called()
        self.assertEqual(len(ui.scanner.mixes), 4)

    def test_status_identifies_the_build_that_owns_the_shared_socket(self):
        here = str(Path(player.__file__).resolve())
        self.assertTrue(player._daemon_runs_this_build({"player_script": here}))
        self.assertFalse(player._daemon_runs_this_build({}))
        self.assertFalse(
            player._daemon_runs_this_build(
                {"player_script": str(PROJECT_ROOT.parent / "MPMixManager_v0.4-x64/MP_Audio_Player.py")}
            )
        )
        self.assertTrue(player._daemon_can_play({"player_script": here, "audio_error": ""}))
        self.assertFalse(
            player._daemon_can_play(
                {
                    "player_script": here,
                    "audio_error": "audio output disabled by MP_DISABLE_AUDIO_OUTPUT",
                }
            ),
            "a daemon started mute must not be reused",
        )

    def test_shift_selected_rows_paint_with_the_selection_bar(self):
        ui = self._ui()
        ui.selected_row = 0
        ui._extend_selection(target=2)
        st = {
            "title": "track 0.flac", "path": str(PROJECT_ROOT / "track0.flac"),
            "state": "playing", "index": 0, "position_fmt": "00:30",
            "duration_fmt": "03:00", "progress_pct": 16.7, "volume": 85,
            "pitch_percent": 0, "levels": [0.4] * 24, "peaks": [0.6] * 24,
            "levels_left": [0.4] * 24, "levels_right": [0.4] * 24,
            "peaks_left": [0.6] * 24, "peaks_right": [0.6] * 24,
            "queue_len": 0, "queue": [],
        }
        captured = []
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "0"}), \
             patch("MP_Audio_Player._present_frame",
                   side_effect=lambda lines, w, h: captured.extend(lines)), \
             patch("MP_Audio_Player._win_visible_size", return_value=(120, 40)):
            ui._get_status = MagicMock(return_value=st)
            ui._last_path = st["path"]  # an established session, not the first draw
            ui._draw_frame()
        bg, _fg = player._selection_bar_ansi(ui.visualizer.theme)
        filled = [line for line in captured if bg in line]
        self.assertGreaterEqual(len(filled), 3, "all three Shift-selected rows must be filled")

    def test_ipc_accepts_a_multi_row_request_beyond_the_old_four_kilobytes(self):
        """A Delete over many rows must reach the daemon intact."""
        server = player.PlayerIPCServer.__new__(player.PlayerIPCServer)
        engine = MagicMock()
        engine.remove_tracks.return_value = 200
        server.engine = engine
        left, right = socket.socketpair()
        try:
            paths = [f"/music/{'folder/' * 20}{i:04d}.flac" for i in range(200)]
            payload = json.dumps({"cmd": "remove_tracks", "paths": paths}).encode()
            self.assertGreater(len(payload), 4096)
            # macOS socketpair buffers are small enough that a 32 KiB
            # sendall blocks before the handler is called, deadlocking the
            # test. Send from a thread so the handler can drain the request.
            import threading
            sender = threading.Thread(target=right.sendall, args=(payload,), daemon=True)
            sender.start()
            right.settimeout(2.0)

            server._handle_client(left)
            sender.join(timeout=5)

            reply = json.loads(right.recv(65536))
            self.assertEqual(reply["status"], "ok")
            self.assertEqual(reply["removed"], 200)
            engine.remove_tracks.assert_called_once_with(paths)
        finally:
            left.close()
            right.close()

    def test_delete_removes_rows_from_the_favorites_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = player.FavoritesStore(str(Path(tmp) / "favorites.json"))
            store.add_tracks(_rows())
            ui = player.TerminalPlayerUI(
                None, player.FavoritesScanner(store), is_remote=True,
                mode="FAVORITES", folder_name="ALL",
            )
            ui._send_cmd = MagicMock()
            ui.favorites = store
            ui.selected_row = 0
            ui._process_key(b"\x1b[1;2B")

            ui._process_key(b"\x1b[3~")

            self.assertEqual(len(store), 2)
            self.assertEqual(
                [row["name"] for row in store.tracks()],
                ["track 2.flac", "track 3.flac"],
            )
            self.assertEqual([row["name"] for row in ui.scanner.mixes], ["track 2.flac", "track 3.flac"])


if __name__ == "__main__":
    unittest.main()
