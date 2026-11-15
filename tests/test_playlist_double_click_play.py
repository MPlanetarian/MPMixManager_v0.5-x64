"""Double-clicking a playlist row starts that track, exactly like Enter.

The playlist used to select a row on a click and nothing more, so a user who
double-clicked a track in the MP Liquid Player or the MP Audio Player CLI
(release 0.5.3) heard nothing happen. Enter already played the selected row;
these tests pin the click path to the same behaviour.

They also pin the two things a double-click must not break:

* a click-and-drag row reorder (Liquid Glass) must never start playback, and
* a press on the favourite star must never pair with the next row click.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import MP_Audio_Player as player  # noqa: E402


class _Scanner:
    def __init__(self, mixes):
        self.mixes = list(mixes)
        self.playlist_path = ""
        self.base_dir = ROOT
        self.folder_name = "MIXES"

    def scan(self):
        pass

    def get_mixes(self, _query=""):
        return list(self.mixes)


def _make_ui(tracks):
    """Build a TerminalPlayerUI with a stand-in engine.

    A real AudioEngine opens an audio device, and building one per test
    crashed the interpreter part-way through this suite. The double-click path
    only needs ``engine.playlist`` and ``_send_cmd``, so a stub is enough.
    """
    engine = SimpleNamespace(playlist=list(tracks))
    ui = player.TerminalPlayerUI(engine, _Scanner(tracks), is_remote=False)
    ui._send_cmd = MagicMock()
    return ui, engine


class PlaylistDoubleClickTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engines = []
        self.tracks = []
        for name in ("Mix_A.flac", "Mix_B.flac", "Mix_C.flac"):
            path = Path(self.tmp.name) / name
            path.write_bytes(b"RIFF" + b"\x00" * 16)
            self.tracks.append(
                {"name": name, "path": str(path), "size_mb": 1.0, "ext": path.suffix[1:].upper()}
            )

    def tearDown(self):
        for engine in self.engines:
            try:
                engine.stop_playback()
            except Exception:
                pass
        self.tmp.cleanup()

    def _make_ui(self):
        ui, engine = _make_ui(self.tracks)
        self.engines.append(engine)
        return ui, engine

    def _click(self, ui, row: int, col: int = 20, kind: bytes = b"M", button: int = 0):
        """One SGR press on a painted playlist row (button 0 = left)."""
        ui._on_mouse_sgr(button, col, row, kind)

    def test_two_quick_clicks_on_a_row_play_that_track(self):
        ui, _engine = self._make_ui()
        ui._playlist_star_rows = {7: 0, 8: 1, 9: 2}
        ui._playlist_star_col = 14
        self._click(ui, 8)
        self._click(ui, 8)
        ui._send_cmd.assert_called_once_with("select_index", index=1)
        self.assertEqual(ui.selected_row, 1)

    def test_one_click_only_selects_and_enables_drag(self):
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}):
            ui, _engine = self._make_ui()
            ui._playlist_star_rows = {7: 0, 8: 1}
            ui._playlist_star_col = 14
            self._click(ui, 8)
        ui._send_cmd.assert_not_called()
        self.assertEqual(ui.selected_row, 1)
        # The single click still arms the Liquid Glass row drag.
        self.assertEqual(ui._playlist_drag_index, 1)

    def test_two_clicks_on_different_rows_do_not_play(self):
        ui, _engine = self._make_ui()
        ui._playlist_star_rows = {7: 0, 8: 1, 9: 2}
        ui._playlist_star_col = 14
        self._click(ui, 7)
        self._click(ui, 8)
        ui._send_cmd.assert_not_called()

    def test_slow_second_click_does_not_play(self):
        ui, _engine = self._make_ui()
        ui._playlist_star_rows = {7: 0, 8: 1}
        ui._playlist_star_col = 14
        self._click(ui, 8)
        ui._last_playlist_click_at -= player.PLAYLIST_DOUBLE_CLICK_SECONDS + 0.05
        self._click(ui, 8)
        ui._send_cmd.assert_not_called()

    def test_a_third_click_starts_a_fresh_pair_instead_of_replaying(self):
        ui, _engine = self._make_ui()
        ui._playlist_star_rows = {7: 0, 8: 1}
        ui._playlist_star_col = 14
        self._click(ui, 8)
        self._click(ui, 8)
        self.assertEqual(ui._send_cmd.call_count, 1)
        # The third click only arms the row again; it is not a second play.
        self._click(ui, 8)
        self.assertEqual(ui._send_cmd.call_count, 1)
        # The fourth click completes the new pair.
        self._click(ui, 8)
        self.assertEqual(ui._send_cmd.call_count, 2)

    def test_double_click_clears_the_drag_so_no_reorder_starts(self):
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}):
            ui, _engine = self._make_ui()
            ui._playlist_star_rows = {7: 0, 8: 1}
            ui._playlist_star_col = 14
            self._click(ui, 8)
            self.assertEqual(ui._playlist_drag_index, 1)
            self._click(ui, 8)
        self.assertIsNone(ui._playlist_drag_index)

    def test_star_press_never_pairs_with_the_next_row_click(self):
        ui, _engine = self._make_ui()
        ui._playlist_star_rows = {7: 0, 8: 1}
        ui._playlist_star_col = 14
        ui._toggle_favorite = MagicMock()
        # A press on the star column toggles the favourite and clears arming.
        self._click(ui, 7, col=14)
        ui._toggle_favorite.assert_called_once()
        # A quick click on a row immediately after must not count as a pair.
        self._click(ui, 7, col=20)
        ui._send_cmd.assert_not_called()

    def test_enter_and_double_click_take_the_same_path(self):
        ui, _engine = self._make_ui()
        ui._playlist_star_rows = {7: 0, 8: 1}
        ui._playlist_star_col = 14
        ui.selected_row = 1
        ui._process_key(b"\r")
        enter_call = ui._send_cmd.call_args_list[-1]
        ui._send_cmd.reset_mock()
        self._click(ui, 8)
        self._click(ui, 8)
        self.assertEqual(ui._send_cmd.call_args_list[-1], enter_call)

    def test_double_click_uses_the_play_command_when_the_track_is_not_in_the_engine(self):
        """A remote or drift case falls back to ``play file=...`` like Enter does."""
        ui, engine = self._make_ui()
        ui._playlist_star_rows = {7: 0}
        ui._playlist_star_col = 14
        engine.playlist = []
        self._click(ui, 7)
        self._click(ui, 7)
        ui._send_cmd.assert_called_once_with("play", file=self.tracks[0]["path"])

    def test_scrolling_over_a_row_never_plays_it(self):
        """Wheel events are not clicks; two scroll-ups must not start playback."""
        ui, _engine = self._make_ui()
        ui._playlist_star_rows = {7: 0, 8: 1}
        ui._playlist_star_col = 14
        for _ in range(4):
            self._click(ui, 8, button=64)   # wheel up
            self._click(ui, 8, button=65)   # wheel down
        ui._send_cmd.assert_not_called()

    def test_a_completed_drag_cannot_pair_with_the_next_click(self):
        """Dragging rows is not a click, so the press after a drag must not play."""
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}):
            ui, _engine = self._make_ui()
            ui._playlist_star_rows = {7: 0, 8: 1}
            ui._playlist_star_col = 14
            ui._reorder_playlist = MagicMock()
            self._click(ui, 7)              # arms row 0
            ui._playlist_drag_to_row(8)     # real drag to row 1
            ui._reorder_playlist.assert_called_once()
            # The drag cleared the arming, so this click is a new first click.
            self._click(ui, 8)
        ui._send_cmd.assert_not_called()

    def test_clicks_on_a_non_playlist_row_do_nothing(self):
        ui, _engine = self._make_ui()
        ui._playlist_star_rows = {7: 0}
        ui._playlist_star_col = 14
        ui._favorites_button_row = 0
        ui._visualizer_button_row = 0
        ui._bass_button_row = 0
        ui._export_tracks_row = 0
        self._click(ui, 40)
        self._click(ui, 40)
        ui._send_cmd.assert_not_called()


if __name__ == "__main__":
    unittest.main()
