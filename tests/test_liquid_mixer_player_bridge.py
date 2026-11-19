#!/usr/bin/env python3
"""Tests for the player changes the mixer depends on.

* the Favorites button toggles back to the playlist the user came from
* the per-channel insert effect is available over IPC and in the audio path
* a channel's effect payload round-trips through the engine
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIXER_DIR = PROJECT_ROOT / "MP_Liquid_Mixer"
sys.path.insert(0, str(MIXER_DIR))
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import MP_Audio_Player as player  # noqa: E402
import mixer_effects  # noqa: E402


def _numpy():
    try:
        import numpy
        return numpy
    except ImportError:  # pragma: no cover
        return None


def _player():
    """A console player with the engine, scanner and terminal all stubbed."""
    ui = player.TerminalPlayerUI.__new__(player.TerminalPlayerUI)
    ui.mode = "MUSIC"
    ui.folder_name = "ALL"
    ui.keep_library = True
    ui._user_library_mode = "MUSIC"
    ui.search_mode = False
    ui.search_query = ""
    ui.selected_row = 0
    ui.scroll_offset = 0
    ui.equalizer_hud_active = False
    ui.inspector_hud_active = False
    ui.is_remote = False
    ui.engine = None
    ui.scanner = MagicMock()
    ui.scanner.base_dir = ""
    ui.scanner.playlist_path = ""
    ui.scanner.mixes = []
    ui.favorites = MagicMock()
    ui.favorites.load.return_value = []
    ui.favorites.tracks.return_value = []
    ui._favorites_return_mode = ""
    ui._favorites_return_folder = ""
    ui._sync_open_favorites = MagicMock()
    ui._notify = MagicMock()
    return ui


class TestFavoritesToggle(unittest.TestCase):
    def test_opening_favorites_remembers_the_playlist_it_came_from(self):
        ui = _player()
        ui._load_favorites()
        self.assertEqual(ui.mode, "FAVORITES")
        self.assertEqual(ui._favorites_return_mode, "MUSIC")
        self.assertEqual(ui._favorites_return_folder, "ALL")

    def test_pressing_favorites_again_returns_to_the_same_music_folder(self):
        ui = _player()
        ui.folder_name = "MUSIC004"
        ui._load_favorites()
        with patch.object(ui, "_switch_library_mode") as switch:
            def go(mode):
                ui.mode = mode
            switch.side_effect = go
            ui._load_favorites()
        self.assertEqual(ui.mode, "MUSIC")
        switch.assert_called_once_with("MUSIC")
        self.assertEqual(ui.folder_name, "MUSIC004")

    def test_the_mix_archive_is_returned_to_as_well(self):
        ui = _player()
        ui.mode = "MIX_ARCHIVE"
        ui._load_favorites()
        self.assertEqual(ui._favorites_return_mode, "MIX_ARCHIVE")
        with patch.object(ui, "_switch_library_mode") as switch:
            def go(mode):
                ui.mode = mode
            switch.side_effect = go
            ui._load_favorites()
        self.assertEqual(ui.mode, "MIX_ARCHIVE")

    def test_a_window_opened_on_favorites_just_shows_favorites(self):
        ui = _player()
        ui.mode = "FAVORITES"
        ui._load_favorites()
        self.assertEqual(ui.mode, "FAVORITES")

    def test_switching_library_clears_the_pending_return(self):
        ui = _player()
        ui._load_favorites()
        ui._favorites_return_mode = "MUSIC"
        # A deliberate library switch must not leave a stale target behind.
        with patch.object(player, "MixArchiveScanner") as scanner:
            scanner.return_value.mixes = [{"path": "/x"}]
            with patch.object(ui, "_send_cmd", create=True), \
                 patch.object(player, "get_base_dir", return_value="/tmp"):
                ui._switch_library_mode("MIX_ARCHIVE")
        self.assertEqual(ui._favorites_return_mode, "")


class TestPlayerInsertEffect(unittest.TestCase):
    def _engine(self):
        engine = player.AudioEngine.__new__(player.AudioEngine)
        engine.sample_rate = 44100
        engine.insert_effect = mixer_effects.ChannelEffect()
        engine._sync_state_file = MagicMock()
        return engine

    def test_the_engine_starts_with_a_bypassed_insert(self):
        engine = self._engine()
        self.assertFalse(engine.insert_effect.enabled)
        self.assertFalse(engine.insert_effect.active)

    def test_set_effect_configures_the_insert(self):
        engine = self._engine()
        self.assertTrue(engine.set_effect(mode="delay", amount=0.6, enabled=True))
        self.assertEqual(engine.insert_effect.mode, "DELAY")
        self.assertAlmostEqual(engine.insert_effect.amount, 0.6, places=6)
        self.assertTrue(engine.insert_effect.active)

    def test_effect_status_is_json_safe(self):
        engine = self._engine()
        engine.set_effect(mode="gater", amount=0.4, enabled=True)
        self.assertEqual(engine.effect_status(),
                         {"mode": "gater", "amount": 0.4, "enabled": True})

    def test_an_unknown_mode_falls_back_to_flanger(self):
        engine = self._engine()
        engine.set_effect(mode="phaser", amount=0.5, enabled=True)
        self.assertEqual(engine.insert_effect.mode, "FLANGER")

    def test_the_filter_mode_is_a_real_insert(self):
        engine = self._engine()
        self.assertTrue(engine.set_effect(mode="filter", amount=0.9, enabled=True))
        self.assertEqual(engine.insert_effect.mode, "FILTER")
        self.assertTrue(engine.insert_effect.active)

    def test_the_filter_centre_is_flat(self):
        np = _numpy()
        if np is None:  # pragma: no cover
            self.skipTest("numpy is not installed")
        effect = mixer_effects.ChannelEffect(mode="FILTER", amount=0.5, enabled=True)
        signal = np.sin(np.linspace(0.0, 30.0, 512)).astype(np.float32)
        stereo = np.column_stack((signal, signal))
        self.assertTrue(np.allclose(effect.process(stereo.copy()), stereo))

    def test_the_mixer_module_is_found_from_the_player(self):
        module = player._mixer_effect_module()
        self.assertIs(module, mixer_effects)


if __name__ == "__main__":
    unittest.main()
