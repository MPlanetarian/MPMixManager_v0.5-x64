"""Tests for the Web Companion stream queue.

The companion is a pure HTTP streamer: it serves audio files to phones and
tablets and never opens a sound device or spawns a local player. These tests
pin that contract - including the guard that no local playback process can be
started from this module.
"""

import http.server
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import mix_web_companion as mwc  # noqa: E402


def _reset_autoplay(result=None):
    mwc._autoplay_done.clear()
    mwc._autoplay_result.clear()
    mwc._autoplay_result.update(result or {"status": "pending", "detail": ""})


class NoLocalPlaybackTest(unittest.TestCase):
    """The companion must never be able to make sound on the host machine."""

    def test_no_player_spawning_code_exists(self):
        for name in ("start_player_daemon", "player_script_path",
                     "wait_for_player", "send_mp_player_command",
                     "query_mp_player_socket", "is_mp_player_active"):
            self.assertFalse(hasattr(mwc, name),
                             f"{name} must not exist: the companion is stream-only")

    def test_no_player_ipc_imports(self):
        source = Path(mwc.__file__).read_text(encoding="utf-8")
        for forbidden in ("subprocess", "systemd-run", "mp_audio_player.sock"):
            self.assertNotIn(forbidden, source)

    def test_startup_never_opens_a_sound_device(self):
        """autoplay_once only catalogs files; it must not spawn a process."""
        _reset_autoplay()
        try:
            with mock.patch.object(mwc, "build_stream_queue", return_value=["/tmp/opencode/a.flac"]), \
                 mock.patch.object(mwc, "_pick_start_track", return_value=("/tmp/opencode/a.flac", 12.0)):
                result = mwc.autoplay_once()
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["track"], "a.flac")
        finally:
            _reset_autoplay()


class StreamQueueTest(unittest.TestCase):
    def setUp(self):
        _reset_autoplay()
        self.tmp = Path(tempfile.mkdtemp(prefix="mp_companion_"))

    def tearDown(self):
        _reset_autoplay()

    def _audio(self, name: str, data: bytes = b"\x00" * 16) -> Path:
        path = self.tmp / name
        path.write_bytes(data)
        return path

    def test_empty_playlist_is_reported_not_started(self):
        empty = self.tmp / "empty.m3u"
        empty.write_text("# no tracks here\n", encoding="utf-8")

        with mock.patch.object(mwc, "_config_value",
                               side_effect=lambda k, d="": str(empty) if k == "WEB_COMPANION_PLAYLIST" else d):
            result = mwc.autoplay_once()

        self.assertEqual(result["status"], "empty_playlist")

    def test_missing_playlist_file_is_treated_as_empty(self):
        missing = self.tmp / "does_not_exist.m3u"
        with mock.patch.object(mwc, "_config_value",
                               side_effect=lambda k, d="": str(missing) if k == "WEB_COMPANION_PLAYLIST" else d):
            result = mwc.autoplay_once()
        self.assertEqual(result["status"], "empty_playlist")

    def test_scan_with_no_archive_dirs_is_empty(self):
        with mock.patch.object(mwc, "_config_value", return_value=""), \
             mock.patch.object(mwc, "get_base_dir", return_value=self.tmp / "nope"):
            self.assertEqual(mwc.scan_playlist_files(), [])

    def test_m3u_playlist_is_read_in_order(self):
        first, second = self._audio("a.flac"), self._audio("b.wav")
        listing = self.tmp / "mix.m3u"
        listing.write_text(f"#EXTM3U\n{first}\n\n{second}\n", encoding="utf-8")

        with mock.patch.object(mwc, "_config_value", return_value=str(listing)):
            self.assertEqual(mwc.scan_playlist_files(), [str(first), str(second)])

    def test_playlist_ignores_non_audio_entries(self):
        audio = self._audio("real.mp3")
        listing = self.tmp / "mix.m3u"
        listing.write_text(f"{self.tmp}/notes.txt\n{audio}\n", encoding="utf-8")

        with mock.patch.object(mwc, "_config_value", return_value=str(listing)):
            self.assertEqual(mwc.scan_playlist_files(), [str(audio)])

    def test_zero_byte_files_are_not_streamable(self):
        """An interrupted conversion leaves a 0-byte file that would stall a phone."""
        good = self._audio("good.flac")
        broken = self._audio("broken.flac", b"")
        listing = self.tmp / "mix.m3u"
        listing.write_text(f"{good}\n{broken}\n", encoding="utf-8")

        archive = self.tmp / "archive"
        archive.mkdir()
        (archive / "good.flac").write_bytes(b"\x00" * 16)
        (archive / "broken.flac").write_bytes(b"")

        with mock.patch.object(mwc, "_config_value",
                               side_effect=lambda k, d="": str(archive) if k == "MIX_ARCHIVE_DIR" else d), \
             mock.patch.object(mwc, "get_base_dir", return_value=self.tmp / "nope"):
            files = mwc.scan_archive_files()

        self.assertEqual([Path(f).name for f in files], ["good.flac"])


class StreamSessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mp_companion_session_"))
        self.state = self.tmp / "web_companion_stream.json"
        self.queue = [str(self.tmp / f"{i}.flac") for i in range(5)]
        for path in self.queue:
            Path(path).write_bytes(b"\x00" * 16)
        self.patches = [
            mock.patch.object(mwc, "VOLUME_FILE", self.state),
            mock.patch.object(mwc, "build_stream_queue", return_value=self.queue),
            mock.patch.object(mwc, "probe_duration", return_value=600.0),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_mini_playlist_returns_only_three_tracks(self):
        payload = mwc.get_mini_playlist()
        self.assertEqual(len(payload["items"]), 3)
        self.assertEqual(payload["total"], 5)
        self.assertTrue(payload["items"][0]["current"])

    def test_mini_playlist_wraps_at_end_of_queue(self):
        mwc.save_stream_session({"index": 4, "path": self.queue[4]})
        payload = mwc.get_mini_playlist()
        self.assertEqual(len(payload["items"]), 3)
        self.assertEqual(payload["items"][0]["index"], 4)
        self.assertEqual(payload["items"][1]["index"], 0)
        self.assertEqual(payload["items"][2]["index"], 1)

    def test_next_advances_the_served_file(self):
        mwc.control_player("next")
        path, _ = mwc.current_stream_file()
        self.assertEqual(path, self.queue[1])

    def test_select_index_jumps_to_a_specific_mix(self):
        mwc.control_player("select_index", {"index": ["3"]})
        path, _ = mwc.current_stream_file()
        self.assertEqual(path, self.queue[3])

    def test_pause_freezes_the_reported_position(self):
        mwc.control_player("play")
        mwc.control_player("pause")
        first = mwc.get_eq_status()["position_sec"]
        second = mwc.get_eq_status()["position_sec"]
        self.assertAlmostEqual(first, second, places=2)

    def test_seek_to_moves_the_reported_position(self):
        mwc.control_player("seek_to", {"position": ["240"]})
        self.assertAlmostEqual(mwc.get_eq_status()["position_sec"], 240.0, places=1)

    def test_seek_forward_and_backward_move_by_30_seconds(self):
        mwc.control_player("seek_to", {"position": ["120"]})
        mwc.control_player("seek_forward")
        self.assertAlmostEqual(mwc.get_eq_status()["position_sec"], 150.0, places=1)
        mwc.control_player("seek_backward")
        self.assertAlmostEqual(mwc.get_eq_status()["position_sec"], 120.0, places=1)

    def test_seek_backward_stops_at_the_start_of_the_mix(self):
        mwc.control_player("seek_to", {"position": ["10"]})
        mwc.control_player("seek_backward")
        self.assertAlmostEqual(mwc.get_eq_status()["position_sec"], 0.0, places=1)

    def test_seek_forward_stops_at_the_end_of_the_mix(self):
        mwc.control_player("seek_to", {"position": ["590"]})
        mwc.control_player("seek_forward")
        self.assertAlmostEqual(mwc.get_eq_status()["position_sec"], 600.0, places=1)

    def test_first_seek_on_a_fresh_session_knows_the_duration(self):
        """duration_sec is learned from the file, so clamping works on press 1."""
        mwc.control_player("seek_to", {"position": ["590"]})
        mwc.save_stream_session({"index": 0, "path": self.queue[0],
                                 "playing": False, "position_sec": 590.0})
        mwc.control_player("seek_forward")
        self.assertAlmostEqual(mwc.get_eq_status()["position_sec"], 600.0, places=1)

    def test_volume_controls_are_bounded(self):
        mwc.control_player("set_volume", {"volume": ["250"]})
        self.assertEqual(mwc.load_stream_session()["volume"], 100)
        mwc.control_player("set_volume", {"volume": ["-10"]})
        self.assertEqual(mwc.load_stream_session()["volume"], 0)

    def test_status_reports_the_stream_player_not_a_local_daemon(self):
        status = mwc.get_playback_status(None)
        self.assertEqual(status["player"], "Web Stream")
        self.assertEqual(status["active_file"], self.queue[0])


class ThemeSelectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mp_companion_theme_"))
        self.theme_file = self.tmp / "web_companion_theme"
        self.patch = mock.patch.object(mwc, "WEB_THEME_FILE", self.theme_file)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_all_palettes_are_offered(self):
        themes = mwc.available_themes()
        self.assertEqual(len(themes), len(mwc.THEMES))
        self.assertTrue(all(t["id"] and t["name"] for t in themes))

    def test_theme_can_be_chosen_by_id(self):
        self.assertEqual(mwc.set_web_theme("dreamworlds"), "dreamworlds")
        self.assertEqual(mwc.get_current_theme_id(None), "dreamworlds")

    def test_theme_can_be_chosen_by_display_name(self):
        palette = mwc.THEMES["retro_linux_memories"]
        self.assertEqual(mwc.set_web_theme(palette["name"]), "retro_linux_memories")
        self.assertEqual(mwc.get_theme_palette(None)["id"], "retro_linux_memories")

    def test_theme_alias_is_accepted(self):
        alias, target = next(iter(mwc.THEME_ALIASES.items()))
        self.assertEqual(mwc.set_web_theme(alias), target)

    def test_unknown_theme_is_rejected(self):
        self.assertIsNone(mwc.set_web_theme("no_such_theme"))

    def test_saved_choice_survives_and_wins_over_manager_theme(self):
        mwc.set_web_theme("dreamworlds")
        with mock.patch.dict(os.environ, {"CURRENT_THEME": "cosmic_supernova"}):
            self.assertEqual(mwc.get_current_theme_id(None), "dreamworlds")


class DurationProbeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mp_companion_dur_"))
        self.file = self.tmp / "mix.wav"
        self.file.write_bytes(b"\x00" * 64)
        mwc._duration_cache.clear()

    def tearDown(self):
        mwc._duration_cache.clear()

    def test_cached_probe_returns_duration_not_file_size(self):
        """A cached hit must return the duration, never the cached byte size."""
        with mock.patch("soundfile.info") as info:
            info.return_value = mock.Mock(duration=1234.5)
            self.assertAlmostEqual(mwc.probe_duration(str(self.file)), 1234.5, places=1)
            self.assertAlmostEqual(mwc.probe_duration(str(self.file)), 1234.5, places=1)

    def test_duration_is_reprobed_when_the_file_changes(self):
        with mock.patch("soundfile.info") as info:
            info.return_value = mock.Mock(duration=100.0)
            mwc.probe_duration(str(self.file))
            self.file.write_bytes(b"\x00" * 128)
            info.return_value = mock.Mock(duration=200.0)
            self.assertAlmostEqual(mwc.probe_duration(str(self.file)), 200.0, places=1)

    def test_plain_wav_falls_back_to_header_maths(self):
        """Without soundfile, a PCM WAV header still yields the right length."""
        wav = self.tmp / "plain.wav"
        # 1 second of 48 kHz stereo 16-bit = 192000 bytes of data.
        header = (
            b"RIFF" + (36 + 192000).to_bytes(4, "little") + b"WAVE"
            + b"fmt " + (16).to_bytes(4, "little")
            + (1).to_bytes(2, "little") + (2).to_bytes(2, "little")
            + (48000).to_bytes(4, "little") + (192000).to_bytes(4, "little")
            + (4).to_bytes(2, "little") + (16).to_bytes(2, "little")
            + b"data" + (192000).to_bytes(4, "little")
        )
        wav.write_bytes(header)
        with mock.patch.dict(sys.modules, {"soundfile": None}):
            self.assertAlmostEqual(mwc._wav_duration(str(wav)), 1.0, places=2)


class StreamQueueCacheTest(unittest.TestCase):
    """The page polls several endpoints per second; the queue must be cached."""

    def setUp(self):
        mwc.invalidate_stream_queue()
        mwc._queue_cache["files"] = []
        mwc._queue_cache["built_at"] = 0.0

    def tearDown(self):
        mwc.invalidate_stream_queue()
        mwc._queue_cache["files"] = []
        mwc._queue_cache["built_at"] = 0.0

    def test_scan_runs_once_across_repeated_calls(self):
        with mock.patch.object(mwc, "scan_playlist_files",
                               return_value=["/tmp/opencode/a.flac"]) as scan:
            for _ in range(10):
                mwc.build_stream_queue()
        self.assertEqual(scan.call_count, 1)

    def test_force_rebuilds_the_queue(self):
        with mock.patch.object(mwc, "scan_playlist_files",
                               return_value=["/tmp/opencode/a.flac"]) as scan:
            mwc.build_stream_queue()
            mwc.build_stream_queue(force=True)
        self.assertEqual(scan.call_count, 2)

    def test_invalidate_triggers_a_rescan(self):
        with mock.patch.object(mwc, "scan_playlist_files",
                               return_value=["/tmp/opencode/a.flac"]) as scan:
            mwc.build_stream_queue()
            mwc.invalidate_stream_queue()
            mwc.build_stream_queue()
        self.assertEqual(scan.call_count, 2)

    def test_cached_result_is_not_mutated_by_the_caller(self):
        with mock.patch.object(mwc, "scan_playlist_files",
                               return_value=["/tmp/opencode/a.flac", "/tmp/opencode/b.flac"]):
            first = mwc.build_stream_queue()
            first.clear()
            second = mwc.build_stream_queue()
        self.assertEqual(len(second), 2)

    def test_control_action_can_request_a_rescan(self):
        with mock.patch.object(mwc, "scan_playlist_files",
                               return_value=["/tmp/opencode/a.flac"]) as scan:
            mwc.build_stream_queue()
            mwc.control_player("refresh")
            mwc.build_stream_queue()
        self.assertEqual(scan.call_count, 2)


class ServerBacklogTest(unittest.TestCase):
    def test_backlog_exceeds_stdlib_default(self):
        """A polling dashboard plus parallel range requests needs headroom."""
        self.assertGreater(mwc.CompanionServer.request_queue_size, 5)

    def test_server_threads_do_not_block_shutdown(self):
        self.assertTrue(mwc.CompanionServer.daemon_threads)

    def test_client_disconnects_are_not_logged_as_faults(self):
        """Phones close tabs constantly; that must not spam the journal."""
        server = mwc.CompanionServer.__new__(mwc.CompanionServer)
        try:
            raise BrokenPipeError(32, "Broken pipe")
        except BrokenPipeError:
            with mock.patch.object(http.server.ThreadingHTTPServer, "handle_error") as base:
                server.handle_error(None, ("127.0.0.1", 1))
                base.assert_not_called()

    def test_real_errors_still_reach_the_handler(self):
        server = mwc.CompanionServer.__new__(mwc.CompanionServer)
        try:
            raise ValueError("boom")
        except ValueError:
            with mock.patch.object(http.server.ThreadingHTTPServer, "handle_error") as base:
                server.handle_error(None, ("127.0.0.1", 1))
                base.assert_called_once()


if __name__ == "__main__":
    unittest.main()
