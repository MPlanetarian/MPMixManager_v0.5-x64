#!/usr/bin/env python3
"""The player remembers the user's volume across launches."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import MP_Audio_Player as player  # noqa: E402


class VolumePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "player_volume"
        self.patch = patch.object(player, "PLAYER_VOLUME_FILE", self.file)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_a_missing_file_uses_the_calm_default(self):
        self.assertAlmostEqual(player._load_saved_volume(), 0.85)

    def test_saved_volume_round_trips(self):
        player._save_player_volume(0.37)
        self.assertEqual(self.file.read_text().strip(), "37")
        self.assertAlmostEqual(player._load_saved_volume(), 0.37)

    def test_bad_values_are_ignored(self):
        self.file.write_text("not a number\n")
        self.assertAlmostEqual(player._load_saved_volume(), 0.85)

    def test_set_volume_writes_the_new_level(self):
        engine = player.AudioEngine.__new__(player.AudioEngine)
        engine.volume, engine.muted = 0.50, False
        engine._sync_state_file = lambda *a, **k: None
        engine.set_volume(42)
        self.assertEqual(engine.volume, 0.42)
        self.assertAlmostEqual(player._load_saved_volume(), 0.42)

    def test_the_engine_loads_the_saved_level(self):
        player._save_player_volume(0.23)
        source = (BASE / "MP_Audio_Player.py").read_text(encoding="utf-8")
        self.assertIn("self.volume = _load_saved_volume()", source)


if __name__ == "__main__":
    unittest.main()
