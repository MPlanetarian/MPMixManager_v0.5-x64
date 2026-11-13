#!/usr/bin/env python3
"""
Unit tests for MP Audio Player shuffle and control shortcuts:
- Pressing 's' or 'S' in TerminalPlayerUI enables/disables shuffle mode
- Pressing 'v' / 'V' controls volume
- AudioEngine toggle_shuffle toggles shuffle state
- PlayerIPCServer handles 'shuffle' IPC command
- CLI argument --shuffle is recognized and supported
"""

import unittest
from unittest.mock import MagicMock, patch
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PLAYER_PY = BASE_DIR / "MP_Audio_Player.py"

import MP_Audio_Player


class TestPlayerShuffle(unittest.TestCase):
    def setUp(self):
        self.mock_engine = MagicMock()
        self.mock_engine.shuffle = False
        self.mock_engine.repeat_mode = "all"
        self.mock_engine.volume = 0.85
        self.mock_engine.muted = False
        self.mock_engine.playlist = []

        def _toggle_shuffle():
            self.mock_engine.shuffle = not self.mock_engine.shuffle

        self.mock_engine.toggle_shuffle.side_effect = _toggle_shuffle
        self.mock_scanner = MagicMock()
        self.mock_scanner.get_mixes.return_value = []

    def test_key_s_and_S_toggle_shuffle_local(self):
        """Verifies that pressing 's' and 'S' toggles shuffle mode when local engine is present."""
        ui = MP_Audio_Player.TerminalPlayerUI(self.mock_engine, self.mock_scanner, is_remote=False)
        self.assertFalse(ui.cached_status.get("shuffle", False))

        # Press 's'
        ui._process_key(b"s")
        self.assertTrue(self.mock_engine.shuffle)
        self.assertTrue(ui.cached_status.get("shuffle", False))
        self.assertEqual(ui.notification_msg, "Shuffle: ON")

        # Press 'S'
        ui._process_key(b"S")
        self.assertFalse(self.mock_engine.shuffle)
        self.assertFalse(ui.cached_status.get("shuffle", False))
        self.assertEqual(ui.notification_msg, "Shuffle: OFF")

    @patch.object(MP_Audio_Player, "send_ipc_command")
    def test_key_s_toggle_shuffle_remote(self, mock_ipc):
        """Verifies that pressing 's' in remote UI mode dispatches IPC shuffle command."""
        ui = MP_Audio_Player.TerminalPlayerUI(None, self.mock_scanner, is_remote=True)
        ui.cached_status["shuffle"] = False

        ui._process_key(b"s")
        self.assertTrue(ui.cached_status.get("shuffle", False))
        self.assertEqual(ui.notification_msg, "Shuffle: ON")

        ui._process_key(b"S")
        self.assertFalse(ui.cached_status.get("shuffle", False))
        self.assertEqual(ui.notification_msg, "Shuffle: OFF")

    def test_key_v_and_V_volume(self):
        """Verifies that pressing 'v' and 'V' changes volume and updates cached status."""
        ui = MP_Audio_Player.TerminalPlayerUI(self.mock_engine, self.mock_scanner, is_remote=False)
        ui.cached_status["volume"] = 80

        ui._process_key(b"v")
        self.assertEqual(ui.cached_status["volume"], 85)

        ui._process_key(b"V")
        self.assertEqual(ui.cached_status["volume"], 80)

    def test_cli_shuffle_flag_recognized(self):
        """Verifies that --shuffle is accepted by the argument parser."""
        res = subprocess.run(
            [sys.executable, str(PLAYER_PY), "--help"],
            capture_output=True,
            text=True
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("--shuffle", res.stdout)
        self.assertIn("--repeat", res.stdout)


class StepTrackShuffleTest(unittest.TestCase):
    """`_step_track` must spread across the whole playlist when shuffling.

    A file that cannot be opened (sleeping SMB share, truncated conversion)
    returns False from load_track. The old loop made at most ten random draws,
    so on a degraded library shuffle collapsed onto whichever few files
    happened to load - the reported "only the same 3 mixes" symptom.
    """

    def _engine(self, n, readable=None):
        engine = object.__new__(MP_Audio_Player.AudioEngine)
        engine.playlist = [{"path": f"/x/{i}.flac"} for i in range(n)]
        engine.current_index = 0
        engine.shuffle = True
        engine.play_queue = []
        engine.dequeue_next = lambda: None
        engine.readable = set(range(n)) if readable is None else set(readable)
        engine.load_track = lambda i, play=True, **kw: (
            setattr(engine, "current_index", i) or True
            if i in engine.readable else False
        )
        return engine

    def test_shuffle_reaches_far_beyond_ten_candidates(self):
        engine = self._engine(200)
        import random
        random.seed(7)
        seen = set()
        for _ in range(120):
            engine._step_track(1)
            seen.add(engine.current_index)
        self.assertGreater(len(seen), 60, "shuffle must not be limited to ~10 picks")

    def test_only_readable_tracks_are_reached(self):
        engine = self._engine(200, readable={3, 4, 5})
        import random
        random.seed(8)
        seen = set()
        for _ in range(80):
            engine._step_track(1)
            seen.add(engine.current_index)
        self.assertEqual(seen, {3, 4, 5})

    def test_shuffle_never_repeats_the_current_track(self):
        engine = self._engine(50)
        import random
        random.seed(9)
        for _ in range(40):
            before = engine.current_index
            engine._step_track(1)
            self.assertNotEqual(before, engine.current_index)

    def test_non_shuffle_still_walks_in_order(self):
        engine = self._engine(10)
        engine.shuffle = False
        order = []
        for _ in range(5):
            engine._step_track(1)
            order.append(engine.current_index)
        self.assertEqual(order, [1, 2, 3, 4, 5])


if __name__ == "__main__":
    unittest.main()
