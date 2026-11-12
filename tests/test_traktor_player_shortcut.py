#!/usr/bin/env python3
"""Shift+T in the terminal player opens the Traktor importer, and every
playlist path is validated before the engine opens it."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import MP_Audio_Player as player


class TestTraktorShortcut(unittest.TestCase):
    def _ui(self):
        scanner = MagicMock()
        scanner.get_mixes.return_value = []
        scanner.playlist_path = ""
        scanner.base_dir = PROJECT_ROOT
        ui = player.TerminalPlayerUI(None, scanner, is_remote=True, mode="MUSIC", folder_name="ALL")
        return ui

    def test_shift_t_is_separate_from_lowercase_tracklist(self):
        ui = self._ui()
        with patch.object(ui, "_view_selected_tracklist") as tracklist, \
             patch.object(ui, "_traktor_picker_open") as importer:
            ui._process_key(b"t")
            tracklist.assert_called_once()
            importer.assert_not_called()
        ui = self._ui()
        with patch.object(ui, "_view_selected_tracklist") as tracklist, \
             patch.object(ui, "_traktor_picker_open") as importer, \
             patch.object(player.sys.stdin, "isatty", return_value=True):
            ui._process_key(b"T")
            importer.assert_called_once()
            tracklist.assert_not_called()

    def test_picker_lists_only_generated_playlists_and_refuses_empty(self):
        ui = self._ui()
        with patch.object(player, "_traktor_importer_module") as module:
            module.return_value.music_folders_ready.return_value = (True, "ok")
            module.return_value.generated_playlists.return_value = []
            ui._traktor_picker_open()
        self.assertTrue(ui.traktor_picker_active)
        self.assertIn("generated", ui.traktor_picker_error.lower())
        box = "\n".join(ui._favorites_decision_box(100))
        self.assertIn("TRAKTOR PLAYLIST IMPORT", box)
        self.assertIn("[G] pre-generate", box)

    def test_import_is_refused_when_music_folders_are_missing(self):
        ui = self._ui()
        fake = MagicMock()
        fake.music_folders_ready.return_value = (False, "No Music Folders are configured.")
        with patch.object(player, "_traktor_importer_module", return_value=fake):
            ui._traktor_import_start()
        self.assertIn("No Music Folders", ui.notification_msg)
        fake.interactive_main.assert_not_called()

    def test_import_is_not_offered_in_liquid_glass_mode(self):
        ui = self._ui()
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}), \
             patch.object(ui, "_traktor_picker_open") as importer:
            ui._process_key(b"T")
        importer.assert_not_called()
        self.assertIn("CLI mode", ui.notification_msg)

    def test_picker_activation_validates_every_path(self):
        ui = self._ui()
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.flac"
            good.write_bytes(b"audio")
            playlist = Path(tmp) / "TRAKTOR_Test.json"
            playlist.write_text(__import__("json").dumps([
                {"name": "good.flac", "path": str(good), "ext": "FLAC"},
                {"name": "gone.flac", "path": str(Path(tmp) / "gone.flac"), "ext": "FLAC"},
            ]), encoding="utf-8")
            ui.traktor_picker_rows = [
                {"name": "Test", "json_path": str(playlist), "tracks": 2}
            ]
            ui.traktor_picker_active = True
            ui.traktor_picker_row = 0
            ui._traktor_picker_activate()
        self.assertFalse(ui.traktor_picker_active)
        self.assertIsInstance(ui.scanner, player._TraktorPlaylistScanner)
        self.assertEqual([t["path"] for t in ui.scanner.mixes], [str(good)])
        self.assertIn("1 unavailable", ui.notification_msg)

    def test_load_track_refuses_a_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "gone.flac")
            engine = MagicMock()
            engine.playlist = [{"name": "gone.flac", "path": missing}]
            engine.current_index = 0
            self.assertFalse(player.AudioEngine.load_track(engine, 0, True))

    def test_validate_accepts_only_real_audio_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "track.flac"
            good.write_bytes(b"audio")
            empty = Path(tmp) / "empty.flac"
            empty.write_bytes(b"")
            self.assertTrue(player._ensure_playable_path(str(good)))
            self.assertFalse(player._ensure_playable_path(str(empty)))
            self.assertFalse(player._ensure_playable_path(str(Path(tmp) / "missing.flac")))


if __name__ == "__main__":
    unittest.main()
