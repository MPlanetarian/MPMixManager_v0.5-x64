#!/usr/bin/env python3
"""Hot-path costs in the Liquid Player stay off the frame when nothing changed.

The picture is the same. These tests pin the shortcuts that keep it that way:
the state file is not rebuilt while its fingerprint holds, a live frame keeps
rounded meters as arrays, a repeated playlist row is the row already painted,
and a favourite path is resolved once.
"""

from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import MP_Audio_Player
from MP_Audio_Player import AudioEngine, EqualizerVisualizer, TerminalPlayerUI


def _bare_engine():
    """An engine object that never opens a device or a Bluetooth monitor."""
    engine = AudioEngine.__new__(AudioEngine)
    engine.mode = "MIX_ARCHIVE"
    engine.playlist = [{"name": "Mix", "path": "/archive/mix.flac"}]
    engine.play_queue = []
    engine.current_index = 0
    engine.current_track = engine.playlist[0]
    engine.state = "playing"
    engine.volume = 0.85
    engine.muted = False
    engine.shuffle = False
    engine.repeat_mode = "all"
    engine.position_sec = 1.2
    engine.duration_sec = 120.0
    engine.sample_rate = 44100
    engine.channels = 2
    engine.pitch_percent = 0
    engine.pitch_factor = 1.0
    engine.output_rate = 44100
    engine.output_device_name = "Speakers"
    engine.stream_error = ""
    engine.auto_bluetooth = False
    engine.bt_router = None
    engine._cached_bt_info = {}
    engine._last_bt_query = 0.0
    engine.num_bands = 24
    for name in (
        "smoothed_levels", "smoothed_levels_left", "smoothed_levels_right",
        "peaks", "peaks_left", "peaks_right",
    ):
        setattr(engine, name, np.linspace(0.05, 0.95, 24))
    engine.recent_mono_buffer = np.linspace(-0.4, 0.4, 512)
    engine.rms_level = 0.2
    engine.peak_db = -8.0
    engine.is_recording = False
    engine.recording_current_path = ""
    engine.recording_bytes_written = 0
    engine.recording_duration_sec = 0.0
    engine.eq_manager = SimpleNamespace(
        active_preset="Flat",
        get_current_bands=lambda: [0.0] * 10,
        is_flat=lambda: True,
        get_all_preset_names=lambda: ["Flat"],
    )
    engine.bass_manager = SimpleNamespace(
        mode="off",
        peak_db=lambda: 0.0,
        fraction=lambda: 0.0,
    )
    return engine


def _ui(tracks, status):
    scanner = MagicMock()
    scanner.get_mixes.return_value = tracks
    ui = TerminalPlayerUI(engine=None, scanner=scanner, is_remote=True, ui_size_mode="NORMAL")
    ui.visualizer.theme = "dreamworlds_ultra"
    ui.visualizer.style_idx = ui.visualizer._resolve_style("stream_of_frequency_eq")
    ui.selected_row = 0
    ui._get_status = MagicMock(return_value=status)
    return ui


def _status(path="/archive/mix-a.flac"):
    levels = [0.25] * 24
    return {
        "title": "Mix A",
        "path": path,
        "state": "stopped",
        "position_fmt": "00:00",
        "duration_fmt": "03:00",
        "progress_pct": 0.0,
        "volume": 85,
        "pitch_percent": 0,
        "muted": False,
        "shuffle": False,
        "repeat": "all",
        "levels": levels,
        "peaks": levels,
        "levels_left": levels,
        "levels_right": levels,
        "peaks_left": levels,
        "peaks_right": levels,
        "rms_level": 0.0,
        "peak_db": -60.0,
        "sample_rate": 44100,
        "channels": 2,
        "queue": [],
        "queue_len": 0,
        "output_device": "Speakers",
        "bluetooth_connected": False,
    }


def _draw(ui, columns, rows):
    captured = []
    with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}), \
         patch("MP_Audio_Player._present_frame", side_effect=lambda lines, w, h: captured.extend(lines)), \
         patch("MP_Audio_Player._win_visible_size", return_value=(columns, rows)), \
         patch("MP_Audio_Player.time.time", return_value=1_700_000_000.0):
        ui._draw_frame()
    return captured


class TestLiquidPlayerFrameCost(unittest.TestCase):
    def test_unchanged_fingerprint_skips_the_status_payload(self):
        engine = _bare_engine()
        calls = {"n": 0}
        original = engine.get_status_dict

        def _counted(*args, **kwargs):
            calls["n"] += 1
            return original(*args, **kwargs)

        engine.get_status_dict = _counted
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "mp_player_state.json"
            resume = Path(tmp) / "playback_resume.json"
            with patch("MP_Audio_Player.STATE_FILE", state), \
                 patch("MP_Audio_Player.RESUME_STATE_FILE", resume):
                engine._sync_state_file()
                self.assertEqual(calls["n"], 1)
                payload = json.loads(state.read_text(encoding="utf-8"))
                self.assertIsInstance(payload["levels"], list)
                self.assertEqual(len(payload["levels"]), 24)
                engine.position_sec = 1.8
                engine._sync_state_file()
                self.assertEqual(calls["n"], 1)
                engine._sync_state_file(force=True)
                self.assertEqual(calls["n"], 2)

    def test_live_status_keeps_rounded_arrays(self):
        engine = _bare_engine()
        live = engine.get_status_dict(live=True)
        listed = engine.get_status_dict()
        self.assertIsInstance(live["levels"], np.ndarray)
        self.assertIsInstance(listed["levels"], list)
        self.assertIsInstance(live["waveform"], np.ndarray)
        self.assertEqual(live["levels"].tolist(), listed["levels"])
        self.assertEqual(live["waveform"].tolist(), listed["waveform"])

    def test_sof_list_and_rounded_array_paint_the_same_frame(self):
        levels = [round(0.15 + (i % 5) * 0.12, 3) for i in range(24)]
        rounded = np.round(np.asarray(levels, dtype=float), 3)

        def _render(meter):
            viz = EqualizerVisualizer(theme="dreamworlds_ultra", style="stream_of_frequency_eq")
            viz._sof_rng = random.Random(1)
            viz._sof_buffer = None
            viz._sof_particles = []
            viz._sof_last_time = 1_700_000_000.0
            status = {
                "state": "playing",
                "levels": meter,
                "levels_left": meter,
                "levels_right": meter,
                "rms_level": 0.22,
                "peak_db": -6.0,
                "pitch_percent": 0,
            }
            with patch("MP_Audio_Player.time.time", return_value=1_700_000_000.0):
                return viz.render(status, width=80, height=7)

        self.assertEqual(_render(levels), _render(rounded))

    def test_playlist_rows_are_reused_and_stay_byte_identical(self):
        tracks = [
            {"name": "Mix A", "path": "/archive/mix-a.flac", "ext": "FLAC", "size_mb": 120},
            {"name": "Mix B", "path": "/archive/mix-b.flac", "ext": "FLAC", "size_mb": 80},
            {"name": "Mix C Queued", "path": "/archive/mix-c.flac", "ext": "FLAC", "size_mb": 40},
        ]
        status = _status()
        status["queue"] = ["/archive/mix-c.flac", "/archive/mix-c.flac"]
        status["queue_len"] = 2
        ui = _ui(tracks, status)
        first = _draw(ui, 100, 36)
        stars = dict(ui._playlist_star_rows)
        self.assertTrue(stars)
        self.assertGreaterEqual(len(ui._playlist_line_cache), 3)
        cached = dict(ui._playlist_line_cache)
        second = _draw(ui, 100, 36)
        self.assertEqual(ui._playlist_star_rows, stars)
        self.assertEqual(ui._playlist_line_cache, cached)
        names = ("Mix A", "Mix B", "Mix C Queued")
        rows = lambda lines: [line for line in lines if any(name in line for name in names)]
        self.assertEqual(rows(first), rows(second))
        self.assertTrue(any("Q1" in line for line in rows(first)))
        ui._playlist_line_cache.clear()
        rebuilt = _draw(ui, 100, 36)
        self.assertEqual(rows(first), rows(rebuilt))
        for key in list(ui._playlist_line_cache):
            ui._playlist_line_cache[key] = f"ROWCACHE{key[0]}END"
        hit = _draw(ui, 100, 36)
        blob = "\n".join(hit)
        self.assertIn("ROWCACHE", blob)
        self.assertNotIn("Mix B", blob)

    def test_music_rows_keep_requesting_a_missing_duration(self):
        tracks = [{"name": "Song", "path": "/music/song.flac", "ext": "FLAC", "duration_fmt": "00:00"}]
        status = _status("/music/now-playing.flac")
        status["duration_fmt"] = "03:12"
        ui = _ui(tracks, status)
        ui.mode = "MUSIC"
        ui._request_duration = MagicMock()
        _draw(ui, 100, 36)
        _draw(ui, 100, 36)
        self.assertGreaterEqual(ui._request_duration.call_count, 2)

    def test_absolute_favorite_key_resolves_once(self):
        raw = f"/tmp/mp-fav-{uuid.uuid4().hex}.flac"
        with patch("os.path.abspath", wraps=os.path.abspath) as abspath:
            first = MP_Audio_Player.favorite_key(raw)
            second = MP_Audio_Player.favorite_key(raw)
        self.assertEqual(first, second)
        self.assertEqual(abspath.call_count, 1)
        self.assertEqual(MP_Audio_Player.favorite_key("rel/mix.flac"), os.path.normcase(os.path.abspath("rel/mix.flac")))
        with patch("os.path.abspath", wraps=os.path.abspath) as abspath:
            self.assertEqual(MP_Audio_Player.favorite_key("smb://host/share/Mix.FLAC"), "smb://host/share/mix.flac")
        self.assertEqual(abspath.call_count, 0)

    def test_glass_host_polls_on_a_cadence_and_skips_a_second_panel_sync(self):
        import MP_Audio_Player_Liquid_Glass_OpenGL as host_mod

        host = host_mod.GlassHost.__new__(host_mod.GlassHost)
        now = 5000.0
        self.assertTrue(host._poll_due("_ui_size_poll_at", 0.10, now))
        self.assertFalse(host._poll_due("_ui_size_poll_at", 0.10, now + 0.05))
        self.assertTrue(host._poll_due("_ui_size_poll_at", 0.10, now + 0.10))

        host.renderer = MagicMock()
        host.renderer.bezel_px = 24
        host.renderer.window_pos.return_value = (10, 20)
        host.renderer.window_size.return_value = (800, 600)
        host.gtk_window = None
        host.child_xid = 0
        host._last_well = None
        host._sync_panels = MagicMock()
        host._place_terminal(sync_panels=False)
        host.renderer.place_terminal.assert_called_once()
        host._sync_panels.assert_not_called()
        host.renderer.place_terminal.reset_mock()
        host._place_terminal(sync_panels=True)
        host.renderer.place_terminal.assert_not_called()
        host._sync_panels.assert_not_called()
        host.renderer.window_size.return_value = (900, 600)
        host._place_terminal(sync_panels=True)
        host._sync_panels.assert_called_once()


if __name__ == "__main__":
    unittest.main()
