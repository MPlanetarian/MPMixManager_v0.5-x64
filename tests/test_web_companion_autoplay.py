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
        """The companion may *read* a running player, never *start* one."""
        source = Path(mwc.__file__).read_text(encoding="utf-8")
        for forbidden in ("systemd-run", "Popen([", "--daemon"):
            self.assertNotIn(forbidden, source)
        self.assertFalse(hasattr(mwc, "start_player_daemon"))

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

    def _audio(self, name: str, data: bytes = b"\x00" * 64) -> Path:
        # Above MIN_STREAMABLE_BYTES: these fixtures exist to test playlist
        # order and filtering, so they must represent playable files.
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
        (archive / "good.flac").write_bytes(b"\x00" * 1024)
        (archive / "broken.flac").write_bytes(b"")

        with mock.patch.object(mwc, "_config_value",
                               side_effect=lambda k, d="": str(archive) if k == "MIX_ARCHIVE_DIR" else d), \
             mock.patch.object(mwc, "get_base_dir", return_value=self.tmp / "nope"):
            files = mwc.scan_archive_files()

        self.assertEqual([Path(f).name for f in files], ["good.flac"])

    def test_header_only_wav_is_not_streamable(self):
        """A WAV with a zero-length data chunk made the phone flicker forever.

        The archive contained 44-byte RIFF/WAVE files: a valid header that
        declares no audio. They passed the old "size > 0" gate, so the queue
        served them, the <audio> element raised a media error, and the
        dashboard treated that as "the companion restarted" - reloading the
        same dead URL in a loop with the restart banner stuck on screen.
        """
        archive = self.tmp / "archive"
        archive.mkdir()
        header_only = (
            b"RIFF" + (36).to_bytes(4, "little") + b"WAVE" +
            b"fmt " + (16).to_bytes(4, "little") +
            (1).to_bytes(2, "little") + (2).to_bytes(2, "little") +
            (48000).to_bytes(4, "little") + (192000).to_bytes(4, "little") +
            (4).to_bytes(2, "little") + (16).to_bytes(2, "little") +
            b"data" + (0).to_bytes(4, "little")
        )
        self.assertEqual(len(header_only), 44)
        (archive / "empty.wav").write_bytes(header_only)
        # The same header, but claiming a real data chunk. Length is what the
        # gate judges, so this one must survive.
        (archive / "real.wav").write_bytes(
            header_only[:4] + (1036).to_bytes(4, "little") + header_only[8:40]
            + (1000).to_bytes(4, "little") + b"\x00" * 1000
        )

        with mock.patch.object(mwc, "_config_value",
                               side_effect=lambda k, d="": str(archive) if k == "MIX_ARCHIVE_DIR" else d), \
             mock.patch.object(mwc, "get_base_dir", return_value=self.tmp / "nope"):
            files = mwc.scan_archive_files()

        self.assertEqual([Path(f).name for f in files], ["real.wav"])

    def test_header_only_wav_is_refused_by_the_stream_endpoint(self):
        """Serving the bare header is what the browser turns into a media error."""
        archive = self.tmp / "archive"
        archive.mkdir()
        dead = archive / "empty.wav"
        dead.write_bytes(b"RIFF" + (36).to_bytes(4, "little") + b"WAVE" +
                         b"fmt " + (16).to_bytes(4, "little") + b"\x00" * 16 +
                         b"data" + (0).to_bytes(4, "little"))

        handler = mwc.CompanionHandler.__new__(mwc.CompanionHandler)
        handler.headers = {}
        handler.wfile = mock.MagicMock()
        handler.send_response = mock.MagicMock()
        handler.send_header = mock.MagicMock()
        handler.end_headers = mock.MagicMock()
        handler.close_connection = False
        handler._serve_audio_stream(str(dead))

        handler.send_response.assert_called_once_with(404)

    def test_a_non_wav_container_is_judged_only_by_size(self):
        """An MP3 is never header-probed for WAV frames."""
        path = self.tmp / "track.mp3"
        path.write_bytes(b"\xff\xfb" + b"\x00" * 2048)
        self.assertTrue(mwc.is_streamable_file(str(path)))
        self.assertFalse(mwc.is_streamable_file(str(self.tmp / "missing.mp3")))
        empty = self.tmp / "empty.mp3"
        empty.write_bytes(b"")
        self.assertFalse(mwc.is_streamable_file(str(empty)))
        # At or below the global floor: too small for any container to hold a
        # frame, so it is rejected without a read.
        runt = self.tmp / "runt.mp3"
        runt.write_bytes(b"\xff\xfb" + b"\x00" * 8)
        self.assertFalse(mwc.is_streamable_file(str(runt)))

    def test_a_bare_wav_header_is_rejected(self):
        """44 bytes is exactly a RIFF/WAVE header with no frames.

        This is the file that caused the flicker loop, so it must be refused
        whichever gate catches it.
        """
        hdr = self.tmp / "hdr.wav"
        hdr.write_bytes(
            b"RIFF" + (36).to_bytes(4, "little") + b"WAVE"
            + b"fmt " + (16).to_bytes(4, "little") + b"\x00" * 16
            + b"data" + (0).to_bytes(4, "little")
        )
        self.assertEqual(hdr.stat().st_size, 44)
        self.assertFalse(mwc.is_streamable_file(str(hdr)))

        # Below the global floor there is certainly no audio.
        for size in (0, 1, 16, 32):
            p = self.tmp / f"tiny{size}.wav"
            p.write_bytes(b"\x00" * size)
            self.assertFalse(mwc.is_streamable_file(str(p)), f"{size} bytes kept")

        # Claims to be RIFF but is shorter than a complete header: no frames.
        runt = self.tmp / "runt.wav"
        runt.write_bytes(b"RIFF" + (35).to_bytes(4, "little") + b"WAVE" + b"\x00" * 30)
        self.assertEqual(runt.stat().st_size, 42)
        self.assertFalse(mwc.is_streamable_file(str(runt)))

        # One byte of audio past the header is not a header stub.
        over = self.tmp / "over.wav"
        over.write_bytes(
            b"RIFF" + (37).to_bytes(4, "little") + b"WAVE"
            + b"fmt " + (16).to_bytes(4, "little") + b"\x00" * 16
            + b"data" + (1).to_bytes(4, "little") + b"\x00"
        )
        self.assertEqual(over.stat().st_size, 45)
        self.assertTrue(mwc.is_streamable_file(str(over)))

    def test_the_floor_stays_below_the_smallest_real_audio(self):
        """The size floor must never be able to reject real audio.

        Measured minimums: a single AAC ADTS frame decodes at 43 bytes, FLAC at
        66, Opus at 172, M4A at 873, WAV at 1,842. A floor at or above 43 would
        silently delete a valid file, so the constant is kept well under it.
        The exact WAV/FLAC stub checks do the precise work instead.
        """
        self.assertLess(mwc.MIN_STREAMABLE_BYTES, 43)
        self.assertGreater(mwc.MIN_STREAMABLE_BYTES, 0)

    def test_a_flac_with_metadata_but_no_frames_is_rejected(self):
        """FLAC shares WAV's failure class: a valid header and no audio.

        A truncated FLAC keeps its STREAMINFO block and no frames at all. Size
        alone cannot catch it safely (a playable FLAC can be only 66 bytes), so
        the metadata blocks are walked to find where audio would start.
        """
        stub = self.tmp / "stub.flac"
        minimal = self.tmp / "minimal.flac"

        # fLaC + STREAMINFO (last block, 34 bytes) and nothing after it.
        streaminfo = (34).to_bytes(3, "big")
        stub.write_bytes(b"fLaC" + bytes([0x80]) + streaminfo + b"\x00" * 34)
        # The same metadata plus enough bytes for a frame.
        minimal.write_bytes(stub.read_bytes() + b"\x00" * 40)

        self.assertFalse(mwc.is_streamable_file(str(stub)))
        self.assertTrue(mwc.is_streamable_file(str(minimal)))

    def test_a_valid_small_aac_is_not_dropped(self):
        """A 43-byte ADTS frame decodes to real audio; the gate must keep it."""
        tiny = self.tmp / "tiny.aac"
        tiny.write_bytes(b"\xff\xf1" + b"\x50\x80\x03\xdf\xfc" + b"\x00" * 300)
        self.assertGreater(tiny.stat().st_size, mwc.MIN_STREAMABLE_BYTES)
        self.assertTrue(mwc.is_streamable_file(str(tiny)))


class StreamSessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mp_companion_session_"))
        self.state = self.tmp / "web_companion_stream.json"
        # Eight entries: the mini playlist wraps at the end, and a five-entry
        # window starting at index 4 needs enough rows to wrap into.
        self.queue = [str(self.tmp / f"{i}.flac") for i in range(8)]
        for path in self.queue:
            Path(path).write_bytes(b"\x00" * 16)
        self.patches = [
            # Isolate host-player detection too: otherwise this suite would
            # report whatever is playing on the machine running the tests.
            mock.patch.object(mwc, "CONFIG_DIR", self.tmp),
            mock.patch.object(mwc, "VOLUME_FILE", self.state),
            mock.patch.object(mwc, "build_stream_queue", return_value=self.queue),
            mock.patch.object(mwc, "probe_duration", return_value=600.0),
        ]
        for p in self.patches:
            p.start()
        # Make sure a real daemon cannot leak in through the module-level cache.
        mwc.invalidate_queue_cache()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        mwc.invalidate_queue_cache()

    def test_mini_playlist_returns_only_five_tracks(self):
        # 0.5 changed the visible window from three entries to five
        # (MINI_PLAYLIST_SIZE) with a twenty-entry scrollback.
        with mock.patch.object(mwc, "host_player_payload", return_value=None):
            payload = mwc.get_mini_playlist()
        self.assertEqual(len(payload["items"]), mwc.MINI_PLAYLIST_SIZE)
        # `total` is the size of the whole queue, not the visible window.
        self.assertEqual(payload["total"], len(self.queue))
        self.assertTrue(payload["items"][0]["current"])

    def test_mini_playlist_stops_at_the_end_of_the_queue(self):
        # 0.5 stopped wrapping the window. A list that jumps from the last
        # track back to the first looks broken once it can be scrolled, so the
        # window is truncated at the end of the queue instead.
        last = len(self.queue) - 1
        mwc.save_stream_session({"index": last, "path": self.queue[last]})
        with mock.patch.object(mwc, "host_player_payload", return_value=None):
            payload = mwc.get_mini_playlist()
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["index"], last)

        mwc.save_stream_session({"index": 4, "path": self.queue[4]})
        with mock.patch.object(mwc, "host_player_payload", return_value=None):
            payload = mwc.get_mini_playlist()
        self.assertEqual(payload["items"][0]["index"], 4)
        self.assertEqual(payload["items"][1]["index"], 5)
        # Truncated at the end of the queue: four rows remain from index 4, so
        # the window is shorter than MINI_PLAYLIST_SIZE here.
        self.assertEqual(len(payload["items"]), len(self.queue) - 4)

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

    def test_status_reports_the_stream_player_when_no_host_player_runs(self):
        """With no live player, the phone-facing stream cursor is the fallback."""
        status = mwc.get_playback_status(None)
        self.assertEqual(status["player"], "Web Stream")
        self.assertEqual(status["active_file"], self.queue[0])
        self.assertFalse(status.get("host_player"))

    def test_status_reports_the_host_player_when_one_is_on_air(self):
        """The bug: the dashboard named a different file than the one playing.

        A live player owns the audio, so its file, title and clock must win
        over the companion's own idle queue cursor.
        """
        live_file = "/run/media/archive/MPlanetarian_-_Stream_of_Frequency_163_THE_CELEBRATION.flac"
        host_state = {
            "running": True,
            "state": "playing",
            "title": "MPlanetarian_-_Stream_of_Frequency_163_THE_CELEBRATION.flac",
            "path": live_file,
            "position_sec": 1240.0,
            "duration_sec": 6402.95,
            "volume": 90,
            "shuffle": False,
            "repeat": "all",
            "index": 0,
            "sample_rate": 48000,
            "channels": 2,
            "pid": os.getpid(),
        }
        # Seed the stream session with a *different* file, exactly like a stale
        # session that pointed at The Happy Andrew while 163 played.
        mwc.save_stream_session({
            "index": 1,
            "path": self.queue[1],
            "playing": True,
            "position_sec": 3720.99,
            "duration_sec": 6402.95,
        })
        with mock.patch.object(mwc, "host_player_status", return_value=(host_state, "ipc_socket")), \
             mock.patch.object(mwc, "find_tracklist_for_mix", return_value=""), \
             mock.patch.object(mwc, "probe_duration", return_value=6402.95):
            status = mwc.get_playback_status(None)
            eq = mwc.get_eq_status()

        self.assertEqual(status["player"], "MP Audio Player")
        self.assertTrue(status["host_player"])
        self.assertEqual(status["active_file"], live_file)
        self.assertIn("163", status["title"])
        self.assertNotIn("Happy Andrew", status["title"])
        # The clock must be the host's, not the stale session's 3720 s.
        self.assertAlmostEqual(status["position_sec"], 1240.0, places=1)
        self.assertAlmostEqual(eq["position_sec"], 1240.0, places=1)
        self.assertEqual(eq["volume"], 90)

    def test_a_dead_player_state_file_is_not_reported_as_playing(self):
        """A leftover state file from a stopped daemon must not claim the mix."""
        dead_pid = 999_999_999
        (self.tmp / "mp_player_state.json").write_text(
            json.dumps({"running": True, "state": "playing",
                        "title": "Stale.flac", "path": "/nonexistent/Stale.flac",
                        "pid": dead_pid}),
            encoding="utf-8",
        )
        (self.tmp / "mp_player.pid").write_text(str(dead_pid), encoding="utf-8")
        status = mwc.get_playback_status(None)
        self.assertFalse(status.get("host_player"))
        self.assertNotEqual(status["active_file"], "/nonexistent/Stale.flac")



class ShuffleTest(unittest.TestCase):
    """Shuffle must reorder playback, not just repaint the badge.

    `switch()` used to advance by a fixed +1 whatever the shuffle flag said, so
    turning shuffle on still played the queue in order.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mp_companion_shuffle_"))
        self.queue = [str(self.tmp / f"{i}.flac") for i in range(40)]
        for path in self.queue:
            Path(path).write_bytes(b"\x00" * 16)
        self.patches = [
            mock.patch.object(mwc, "CONFIG_DIR", self.tmp),
            mock.patch.object(mwc, "VOLUME_FILE", self.tmp / "web_companion_stream.json"),
            mock.patch.object(mwc, "build_stream_queue", side_effect=lambda: list(self.queue)),
            mock.patch.object(mwc, "build_stream_queue_cached", side_effect=lambda: list(self.queue)),
            mock.patch.object(mwc, "probe_duration", return_value=100.0),
            # No host player: exercise the browser-owned fallback path.
            mock.patch.object(mwc, "host_player_status", return_value=(None, "none")),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _next_paths(self, count):
        seen = []
        for _ in range(count):
            mwc.control_player("next")
            path, _ = mwc.current_stream_file()
            seen.append(path)
        return seen

    def test_shuffle_on_does_not_walk_the_queue_in_order(self):
        mwc.control_player("shuffle")
        self.assertTrue(mwc.load_stream_session()["shuffle"])
        paths = self._next_paths(12)
        ordered = [self.queue[(self.queue.index(self.queue[0]) + i + 1) % len(self.queue)]
                   for i in range(12)]
        self.assertNotEqual(paths, ordered)
        self.assertGreater(len(set(paths)), 4)

    def test_shuffle_off_walks_the_queue_in_order(self):
        session = mwc.load_stream_session()
        session.update(shuffle=False, index=0, path=self.queue[0], playing=True)
        mwc.save_stream_session(session)
        paths = self._next_paths(5)
        self.assertEqual(paths, [self.queue[i] for i in range(1, 6)])

    def test_shuffle_never_repeats_the_current_track(self):
        mwc.control_player("shuffle")
        for _ in range(20):
            before, _ = mwc.current_stream_file()
            mwc.control_player("next")
            after, _ = mwc.current_stream_file()
            self.assertNotEqual(before, after)


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
        # `build_stream_queue` falls through to the real library scan when the
        # mode-specific scanner is not stubbed. On a 51k-file archive that walks
        # and opens every FLAC before the test's own mock is consulted, so the
        # module took minutes and looked like a hang. Redirect both scanners.
        # A cache test must not walk the developer's archive. `build_stream_queue`
        # resolves MIX mode through scan_archive_files, so that is the function
        # whose call count these tests measure. Point it at an empty library and
        # leave the rest alone; the tests that mock scan_playlist_files patch it
        # themselves.
        # These tests mock `scan_playlist_files` and count its calls, so the
        # archive scanner has to route through it. When nothing is mocked it
        # returns an empty library instead of walking the real archive.
        self._patches = [
            mock.patch.object(mwc, "scan_archive_files",
                              side_effect=lambda: mwc.scan_playlist_files()),
            mock.patch.object(mwc, "scan_music_files", return_value=[]),
            mock.patch.object(mwc, "ensure_music_folders_mounted", return_value=None),
        ]
        for patch_ in self._patches:
            patch_.start()
            self.addCleanup(patch_.stop)

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
