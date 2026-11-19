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
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import mix_web_companion as mwc  # noqa: E402

# Archive autodiscovery walks /run/media, /mnt and /Volumes so a replugged
# drive is found again. That must never run inside the suite: on a developer
# machine with 900+ mixes it makes every queue test read the real archive
# (minutes) and assert against it instead of a fixture. Emptying the list
# keeps the tests hermetic while production keeps the fallback.
mwc.ARCHIVE_MOUNT_GLOBS = ()


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


def _silent_wav_bytes(frames: int = 64, rate: int = 44100) -> bytes:
    """A minimal valid 16-bit mono PCM WAV: header plus real silent frames."""
    import struct

    data = b"\x00\x00" * frames
    header = (
        b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data" + struct.pack("<I", len(data))
    )
    return header + data


class StreamQueueTest(unittest.TestCase):
    def setUp(self):
        _reset_autoplay()
        self.tmp = Path(tempfile.mkdtemp(prefix="mp_companion_"))

    def tearDown(self):
        _reset_autoplay()

    def _audio(self, name: str, data: bytes = b"") -> Path:
        # These fixtures exist to test playlist order and filtering, so they
        # have to represent *playable* files. A WAV of 64 zero bytes is not:
        # `_wav_has_no_audio_frames` rejects an all-zero header (it matches no
        # RIFF/RF64/BW64/RIFX magic), so it was correctly filtered out and the
        # ordering test then failed. Write a real 16-bit PCM WAV header with
        # one frame of silence for .wav names, and plain bytes for the rest.
        path = self.tmp / name
        if not data:
            if name.lower().endswith((".wav", ".wave")):
                data = _silent_wav_bytes()
            else:
                data = b"\x00" * 64
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

    def test_mini_playlist_returns_the_scrollback_window(self):
        # The endpoint sends the whole scrollback (MINI_PLAYLIST_WINDOW = 20)
        # and the dashboard renders MINI_PLAYLIST_SIZE (5) of it at a time.
        # This queue is shorter than the window, so all of it is returned.
        with mock.patch.object(mwc, "host_player_payload", return_value=None):
            payload = mwc.get_mini_playlist()
        self.assertEqual(len(payload["items"]), len(self.queue))
        self.assertEqual(payload["show"], mwc.MINI_PLAYLIST_SIZE)
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

    def test_pause_does_not_start_a_stopped_mix(self):
        mwc.control_player("pause")
        mwc.control_player("pause")
        self.assertFalse(mwc.load_stream_session()["playing"])

    def test_previous_steps_backward_while_shuffle_is_on(self):
        session = mwc.load_stream_session()
        session.update(shuffle=True, index=3, path=self.queue[3], playing=True)
        mwc.save_stream_session(session)
        mwc.control_player("previous")
        path, state = mwc.current_stream_file()
        self.assertEqual(path, self.queue[2])
        self.assertTrue(state.get("shuffle"))

    def test_stream_target_is_not_forwarded_to_the_desk(self):
        host = {
            "path": self.queue[0],
            "state": "playing",
            "position_sec": 10,
            "duration_sec": 600,
            "pid": os.getpid(),
        }
        with mock.patch.object(mwc, "host_player_status", return_value=(host, "ipc_socket")), \
             mock.patch.object(mwc, "send_player_command") as send:
            ok = mwc.control_player("next", {"target": ["stream"]})
        self.assertTrue(ok)
        send.assert_not_called()
        path, _state = mwc.current_stream_file()
        self.assertEqual(path, self.queue[1])

    def test_adopt_host_copies_the_desk_file_into_the_session(self):
        host = {
            "path": self.queue[4],
            "state": "playing",
            "position_sec": 88,
            "duration_sec": 600,
            "pid": os.getpid(),
        }
        with mock.patch.object(mwc, "host_player_status", return_value=(host, "ipc_socket")):
            self.assertTrue(mwc.control_player("adopt_host", {}))
        state = mwc.load_stream_session()
        self.assertEqual(state["path"], self.queue[4])
        self.assertEqual(state["index"], 4)
        self.assertAlmostEqual(state["position_sec"], 88.0, places=1)
        self.assertTrue(state["playing"])

    def test_follow_stream_ignores_a_live_desk_player(self):
        mwc.save_stream_session({
            "index": 2,
            "path": self.queue[2],
            "playing": True,
            "position_sec": 12,
            "started_at": 0,
            "duration_sec": 600,
        })
        desk_payload = {
            "player": "MP Audio Player",
            "host_player": True,
            "active_file": "/tmp/other.flac",
            "state": "playing",
            "position_sec": 1,
            "title": "Other",
            "volume": 10,
        }
        with mock.patch.object(mwc, "host_player_payload", return_value=desk_payload):
            desk = mwc.get_playback_status(None)
            web = mwc.get_playback_status(None, "stream")
        self.assertTrue(desk.get("host_player"))
        self.assertEqual(web["player"], "Web Stream")
        self.assertEqual(web["active_file"], self.queue[2])

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

    def test_mute_toggles_without_forgetting_the_level(self):
        """Mute has to be reversible. Zeroing the level left unmute impossible."""
        mwc.control_player("set_volume", {"volume": ["80"]})
        self.assertTrue(mwc.control_player("mute"))
        state = mwc.load_stream_session()
        self.assertTrue(state["muted"])
        self.assertEqual(state["volume"], 80)
        eq = mwc.get_eq_status()
        self.assertTrue(eq["muted"])
        self.assertEqual(eq["volume"], 80)
        status = mwc.get_playback_status(None)
        self.assertTrue(status["muted"])
        self.assertEqual(status["volume"], 80)
        mwc.control_player("mute")
        state = mwc.load_stream_session()
        self.assertFalse(state["muted"])
        self.assertEqual(state["volume"], 80)

    def test_volume_buttons_clear_mute_and_stay_in_range(self):
        mwc.control_player("set_volume", {"volume": ["80"]})
        mwc.control_player("mute")
        mwc.control_player("vol_down")
        state = mwc.load_stream_session()
        self.assertFalse(state["muted"])
        self.assertEqual(state["volume"], 75)
        mwc.control_player("set_volume", {"volume": ["100"]})
        mwc.control_player("vol_up")
        self.assertEqual(mwc.load_stream_session()["volume"], 100)
        mwc.control_player("set_volume", {"volume": ["0"]})
        mwc.control_player("vol_down")
        self.assertEqual(mwc.load_stream_session()["volume"], 0)

    def test_engaged_mute_is_not_forwarded_to_the_desk(self):
        host = {
            "path": self.queue[0],
            "state": "playing",
            "position_sec": 10,
            "duration_sec": 600,
            "volume": 40,
            "muted": False,
            "pid": os.getpid(),
        }
        with mock.patch.object(mwc, "host_player_status", return_value=(host, "ipc_socket")), \
             mock.patch.object(mwc, "send_player_command") as send:
            ok = mwc.control_player("mute", {"target": ["stream"]})
        self.assertTrue(ok)
        send.assert_not_called()
        state = mwc.load_stream_session()
        self.assertTrue(state["muted"])
        self.assertEqual(state["volume"], 100)

    def test_adopt_keeps_the_desk_level(self):
        host = {
            "path": self.queue[4],
            "state": "playing",
            "position_sec": 88,
            "duration_sec": 600,
            "volume": 35,
            "muted": True,
            "pitch_percent": 0,
            "pid": os.getpid(),
        }
        with mock.patch.object(mwc, "host_player_status", return_value=(host, "ipc_socket")):
            self.assertTrue(mwc.control_player("adopt_host", {}))
        state = mwc.load_stream_session()
        self.assertEqual(state["volume"], 35)
        self.assertTrue(state["muted"])

    def test_pitch_buttons_move_the_stream_and_stay_in_range(self):
        """[-] / [+] / 0 must change the stream the browser is hearing.

        The session template used to omit pitch, and the stream control path
        ignored the three actions, so the pill stayed at 0% and the audio
        element never left 1.0x.
        """
        self.assertTrue(mwc.control_player("pitch_up"))
        self.assertEqual(mwc.load_stream_session()["pitch_percent"], 1)
        self.assertEqual(mwc.get_eq_status()["pitch_percent"], 1)
        status = mwc.get_playback_status(None)
        self.assertEqual(status["pitch_percent"], 1)
        self.assertAlmostEqual(status["pitch_factor"], 1.01, places=3)
        mwc.control_player("pitch_down")
        mwc.control_player("pitch_down")
        self.assertEqual(mwc.load_stream_session()["pitch_percent"], -1)
        mwc.control_player("set_pitch", {"percent": ["40"]})
        self.assertEqual(mwc.load_stream_session()["pitch_percent"], 20)
        mwc.control_player("set_pitch", {"percent": ["-40"]})
        self.assertEqual(mwc.load_stream_session()["pitch_percent"], -20)
        self.assertTrue(mwc.control_player("reset_pitch"))
        self.assertEqual(mwc.load_stream_session()["pitch_percent"], 0)
        self.assertEqual(mwc.get_eq_status()["pitch_percent"], 0)
        # Keys absent from the session template are dropped on the next read.
        session = mwc.load_stream_session()
        session["pitch_percent"] = 7
        mwc.save_stream_session(session)
        self.assertEqual(mwc.load_stream_session()["pitch_percent"], 7)

    def test_engaged_pitch_is_not_forwarded_to_the_desk(self):
        host = {
            "path": self.queue[0],
            "state": "playing",
            "position_sec": 10,
            "duration_sec": 600,
            "pitch_percent": 4,
            "pid": os.getpid(),
        }
        with mock.patch.object(mwc, "host_player_status", return_value=(host, "ipc_socket")), \
             mock.patch.object(mwc, "send_player_command") as send:
            ok = mwc.control_player("pitch_up", {"target": ["stream"]})
        self.assertTrue(ok)
        send.assert_not_called()
        self.assertEqual(mwc.load_stream_session()["pitch_percent"], 1)

    def test_adopt_keeps_the_desk_pitch(self):
        host = {
            "path": self.queue[4],
            "state": "playing",
            "position_sec": 88,
            "duration_sec": 600,
            "pitch_percent": -6,
            "pid": os.getpid(),
        }
        with mock.patch.object(mwc, "host_player_status", return_value=(host, "ipc_socket")):
            self.assertTrue(mwc.control_player("adopt_host", {}))
        self.assertEqual(mwc.load_stream_session()["pitch_percent"], -6)

    def test_raising_pitch_does_not_rescore_time_already_played(self):
        started = time.time() - 100
        mwc.save_stream_session({
            "index": 0,
            "path": self.queue[0],
            "playing": True,
            "position_sec": 10.0,
            "started_at": started,
            "duration_sec": 600,
            "pitch_percent": 0,
        })
        before = mwc.stream_position(mwc.load_stream_session())
        self.assertGreater(before, 100)
        mwc.control_player("set_pitch", {"percent": ["20"]})
        state = mwc.load_stream_session()
        self.assertEqual(state["pitch_percent"], 20)
        self.assertAlmostEqual(state["position_sec"], before, delta=1.0)
        self.assertGreater(state["started_at"], started)

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


class FrameProbeCacheTest(unittest.TestCase):
    """The audio-frame gate must not reopen every file on each scan.

    On the CIFS archives one probe costs several network round trips (a 2.5 GB
    mix took 5.6 s), so an uncached walk of ~1k files blocked the whole queue
    build for 196 s and every dashboard poll queued behind it.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mp_companion_frames_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.archive = self.tmp / "archive"
        self.archive.mkdir()
        self.track = self.archive / "mix.flac"
        self.track.write_bytes(b"fLaC" + b"\x00" * 4000)
        mwc._frame_probe_cache.clear()
        mwc._frame_probe_dirty = False

    def tearDown(self):
        mwc._frame_probe_cache.clear()
        mwc._frame_probe_dirty = False

    def test_second_scan_does_not_reopen_the_file(self):
        first = mwc._scan_dirs([self.archive])
        self.assertEqual(first, [str(self.track)])
        # Any reopen would prove the cache missed; the probe is the only
        # thing in the scan that opens audio files.
        with mock.patch.object(mwc, "_flac_has_no_audio_frames",
                               side_effect=AssertionError("reopened")):
            second = mwc._scan_dirs([self.archive])
        self.assertEqual(second, [str(self.track)])

    def test_verdict_is_recomputed_when_the_file_changes(self):
        mwc._scan_dirs([self.archive])
        # Grow the file; the (mtime, size) signature no longer matches.
        self.track.write_bytes(b"fLaC" + b"\x00" * 8000)
        with mock.patch.object(mwc, "_flac_has_no_audio_frames",
                               return_value=True) as probe:
            self.assertEqual(mwc._scan_dirs([self.archive]), [])
            probe.assert_called_once()

    def test_verdicts_are_persisted_and_restored(self):
        cache_file = self.tmp / "frames.json"
        with mock.patch.object(mwc, "FRAME_PROBE_CACHE_FILE", cache_file):
            mwc._scan_dirs([self.archive])
            self.assertTrue(cache_file.is_file())
            mwc._frame_probe_cache.clear()
            mwc._load_frame_probe_cache()
        self.assertTrue(mwc._frame_probe_cache)
        with mock.patch.object(mwc, "_flac_has_no_audio_frames",
                               side_effect=AssertionError("reopened")):
            self.assertEqual(mwc._scan_dirs([self.archive]), [str(self.track)])

    def test_corrupt_cache_file_is_ignored(self):
        cache_file = self.tmp / "frames.json"
        cache_file.write_text("{not json")
        with mock.patch.object(mwc, "FRAME_PROBE_CACHE_FILE", cache_file):
            mwc._load_frame_probe_cache()
        self.assertEqual(mwc._frame_probe_cache, {})


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
        # A read poll starts a background refresh on a worker thread, and that
        # thread can finish after this setUp has cleared the cache - writing a
        # fresh built_at and making the next test see a valid cache it did not
        # build (scan.call_count 0 instead of 1). Let the previous test's
        # worker land before clearing, so each test starts from its own cache.
        deadline = time.time() + 5.0
        while mwc._queue_refresh_active and time.time() < deadline:
            time.sleep(0.01)
        mwc.invalidate_stream_queue()
        mwc._queue_cache["files"] = []
        mwc._queue_cache["built_at"] = 0.0
        # These tests count how often the queue is rebuilt by mocking
        # scan_playlist_files, so the queue has to route through it. The music
        # branch no longer falls back to the archive scan (an unmounted share
        # must not be relabelled as mixes), so pin the mix library here and let
        # scan_archive_files be the one scanner these tests observe. The inner
        # reader is stubbed to an empty library so an unmocked call returns
        # nothing instead of walking the developer's 51k-file archive.
        self._patches = [
            mock.patch.object(mwc, "_read_playlist_file", return_value=[]),
            mock.patch.object(mwc, "get_library_mode", return_value=mwc.LIBRARY_MODE_MIX),
            mock.patch.object(mwc, "scan_archive_files",
                              side_effect=lambda: mwc.scan_playlist_files()),
            mock.patch.object(mwc, "scan_music_files", return_value=[]),
            mock.patch.object(mwc, "ensure_music_folders_mounted", return_value=None),
            # A stale read spawns a refresh worker. That thread outlives the
            # mocks torn down at the end of the test, so it then walks the
            # developer's real 55k-file library while holding the build lock -
            # the next test cannot take the lock for 90 s and fails. These
            # tests assert cache/lock behaviour, never the worker itself, so
            # keep the refresh from being started here.
            mock.patch.object(mwc, "_refresh_queue_in_background", return_value=None),
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

    def test_read_poll_does_not_wait_for_a_running_scan(self):
        """The prewarm scan holds the build lock for minutes on CIFS.

        A poll that arrived then queued behind it and the dashboard appeared
        frozen. It must answer from the last known queue instead.
        """
        self.assertTrue(mwc._queue_build_lock.acquire(timeout=1))
        try:
            start = time.time()
            result = mwc.build_stream_queue_cached()
            elapsed = time.time() - start
        finally:
            mwc._queue_build_lock.release()
        self.assertEqual(result, [])
        self.assertLess(elapsed, 1.0)

    def test_read_poll_answers_from_cache_while_a_scan_runs(self):
        mwc._queue_cache["mode"] = mwc.get_library_mode()
        mwc._queue_cache["built_at"] = time.time() - (mwc.QUEUE_CACHE_TTL * 10)
        mwc._queue_cache["files"] = ("/tmp/opencode/a.flac",)
        self.assertTrue(mwc._queue_build_lock.acquire(timeout=1))
        try:
            self.assertEqual(
                mwc.build_stream_queue_cached(), ["/tmp/opencode/a.flac"]
            )
        finally:
            mwc._queue_build_lock.release()
        mwc.invalidate_stream_queue()


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


class ArchiveAutodiscoveryTest(unittest.TestCase):
    """A removable archive changes mount point, and config.env does not follow.

    The field symptom: the phone connected, the dashboard loaded, and "Engage
    Audio Stream" did nothing because the queue was empty. MIX_ARCHIVE_DIR
    still named /run/media/<user>/WD BLACK B while the drive had come back as
    /mnt/X299ROG_WD_BLACK, so the scan resolved only the empty placeholder
    folders inside the install tree.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mp_archive_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        mwc.invalidate_queue_cache()
        mwc._PROBE_INFLIGHT.clear()

    def _patches(self, globs, configured=""):
        return [
            mock.patch.object(mwc, "ARCHIVE_MOUNT_GLOBS", globs),
            mock.patch.object(mwc, "_config_value", return_value=configured),
            mock.patch.object(mwc, "get_base_dir", return_value=self.tmp / "install"),
            mock.patch.dict(os.environ, {}, clear=False),
        ]

    def test_a_mounted_archive_is_found_when_the_configured_path_is_stale(self):
        mount = self.tmp / "mnt" / "DISK" / "MIX_ARCHIVE"
        (mount / "FLAC_CONVERTED_OUTPUTS").mkdir(parents=True)
        (mount / "FLAC_CONVERTED_OUTPUTS" / "mix.flac").write_bytes(b"\x00" * 4096)
        (self.tmp / "install" / "MIX_ARCHIVE").mkdir(parents=True)

        globs = (str(self.tmp / "mnt" / "*" / "MIX_ARCHIVE"),)
        patches = self._patches(globs)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        dirs = [str(d) for d in mwc._archive_dirs()]
        self.assertIn(str(mount.resolve()), dirs,
                      "the mounted archive must be discovered")

    def test_an_empty_install_tree_does_not_count_as_the_archive(self):
        """Placeholder folders must not suppress discovery.

        A fresh install creates MIX_ARCHIVE/FLAC_CONVERTED_OUTPUTS empty. If
        those count as "we already have the archive", the mount fallback never
        runs and the queue stays empty on every boot.
        """
        empty = self.tmp / "install" / "MIX_ARCHIVE"
        empty.mkdir(parents=True)
        self.assertFalse(mwc._dir_is_archive_candidate(empty))

    def test_a_populated_install_tree_does_count(self):
        populated = self.tmp / "install" / "MIX_ARCHIVE"
        (populated / "FLAC_CONVERTED_OUTPUTS").mkdir(parents=True)
        (populated / "FLAC_CONVERTED_OUTPUTS" / "mix.flac").write_bytes(b"\x00" * 4096)
        self.assertTrue(mwc._dir_is_archive_candidate(populated))

    def test_autodetect_can_be_turned_off(self):
        with mock.patch.dict(os.environ, {"WEB_COMPANION_ARCHIVE_AUTODETECT": "0"}):
            self.assertEqual(mwc._archive_mount_globs(), ())

    def test_a_rescan_forgets_which_folders_were_unreadable(self):
        """Granting access must not require a service restart.

        The negative probe cached for a minute, so after the user approved the
        volume in System Settings a rescan still skipped it and the phone kept
        reporting a privacy block.
        """
        mwc._BLOCKED_DIRS[str(self.tmp)] = time.time()
        mwc.invalidate_queue_cache()
        self.assertEqual(mwc._BLOCKED_DIRS, {})

    def test_a_slow_probe_does_not_stack_duplicate_probes(self):
        """A denied read parks for the whole TCC watchdog window.

        Every scan used to start its own probe thread, so a polling dashboard
        accumulated one parked read per request. On macOS each of those raises
        another consent request, which is what filled the kernel log with
        "watchdog expired for approval entry" for this process.
        """
        started = []

        def _slow_probe(*_args, **_kwargs):
            started.append(1)
            time.sleep(0.4)

        with mock.patch.object(mwc.os, "listdir", side_effect=_slow_probe):
            # First call starts a probe and gives up waiting.
            self.assertFalse(mwc._dir_readable(str(self.tmp), timeout=0.05))
            # A second call while it is still parked must not start another.
            self.assertFalse(mwc._dir_readable(str(self.tmp), timeout=0.05))
            self.assertEqual(len(started), 1, "a duplicate probe was started")
            time.sleep(0.5)
        # Once the parked probe returns, its mark is released.
        self.assertNotIn(str(self.tmp), mwc._PROBE_INFLIGHT)


class EngageButtonTest(unittest.TestCase):
    """The dashboard's one control must always do something observable."""

    def setUp(self):
        self.html = mwc.render_html_page(mwc.get_theme_palette(None))

    def test_volume_buttons_drive_a_gain_node(self):
        """iPhone Safari ignores audio.volume, so the level has to be a GainNode.

        Mute used to assign volume 0, which that browser also ignores, and
        there was no saved level to restore on the next tap.
        """
        self.assertIn("bumpVolume(-5); sendAction('vol_down')", self.html)
        self.assertIn("bumpVolume(5); sendAction('vol_up')", self.html)
        self.assertIn("toggleStreamMute(); sendAction('mute')", self.html)
        self.assertIn("function applyStreamVolume(percent, muted)", self.html)
        fn = self.html[self.html.index("function setMixLoudness"):]
        fn = fn[:fn.index("function ensureAudibleGain")]
        self.assertIn("mixGain.gain.value = gain", fn)
        self.assertIn("audioEl.volume = 1", fn)
        self.assertIn("audioEl.muted = false", fn)

    def test_pitch_buttons_retune_the_playing_element(self):
        """The pitch cluster has to change the element the listener hears.

        Setting playbackRate alone leaves preservesPitch at its default, so
        the tone stays put and the buttons sound as if they did nothing.
        """
        self.assertIn("function applyStreamPitch(percent)", self.html)
        fn = self.html[self.html.index("function applyStreamPitch(percent)"):]
        fn = fn[:fn.index("function bumpPitch")]
        self.assertIn("preservesPitch = false", fn)
        self.assertIn("webkitPreservesPitch = false", fn)
        self.assertIn("playbackRate", fn)
        self.assertIn("bumpPitch(-1); sendAction('pitch_down')", self.html)
        self.assertIn("bumpPitch(1); sendAction('pitch_up')", self.html)
        self.assertIn("applyStreamPitch(0); sendAction('reset_pitch')", self.html)
        self.assertIn("applyStreamPitch(data.pitch_percent)", self.html)

    def test_the_stream_element_is_playsinline(self):
        """iOS hands a non-playsinline element to the system player.

        The result on a phone is a tap that appears to do nothing: playback
        leaves the page and the overlay never clears.
        """
        start = self.html.index('<audio id="mobileAudioStream"')
        marker = self.html[start : self.html.index(">", start) + 1]
        self.assertIn("playsinline", marker)

    def test_the_stream_element_stays_in_the_render_tree(self):
        """iOS Safari rejects play() on an element that is display:none.

        The rejection leaves the Engage gate exactly where it was, so the
        tap looks dead. The element has to be painted (even at 1px) and it
        cannot live inside a tab panel, because hiding that panel is
        display:none on the ancestor.
        """
        start = self.html.index('<audio id="mobileAudioStream"')
        marker = self.html[start : self.html.index(">", start) + 1]
        self.assertNotIn("display: none", marker)
        self.assertNotIn("display:none", marker)
        self.assertIn("phone-audio", marker)
        self.assertIn('preload="none"', marker)
        css = self.html[self.html.index(".phone-audio") :]
        css = css[: css.index("}")]
        self.assertNotIn("display", css)
        self.assertNotIn("visibility", css)
        # Both elements sit on <body>, ahead of any panel that can be hidden.
        body = self.html.index("<body>")
        panel = self.html.index('id="panelPlayer"')
        self.assertLess(self.html.index('id="mobileAudioStream"'), panel)
        self.assertLess(self.html.index('id="assistantSpeech"'), panel)
        self.assertGreater(self.html.index('id="mobileAudioStream"'), body)

    def test_an_iphone_does_not_play_before_the_engage_tap(self):
        """A play() from the status poll is outside the tap.

        iOS rejects it, and the element then will not start when Engage is
        pressed. The poll must leave the element alone until that tap.
        """
        self.assertIn("function needsPlaybackGesture()", self.html)
        auto = self.html[self.html.index("function maybeAutoStartStream()") :]
        auto = auto[: auto.index("function syncHostPlayerFile")]
        self.assertLess(auto.index("needsPlaybackGesture()"), auto.index("engageStreamFromServer"))
        activate = self.html[self.html.index("function activateMobileStream()") :]
        activate = activate[: activate.index("function unlockStreamElement")]
        head = activate.split("fetch(", 1)[0]
        self.assertIn("engageStreamFromServer(lastServerResume, { gesture: true })", head)
        self.assertIn("ontouchend=\"activateMobileStream(event)\"", self.html)

    def test_the_stream_starts_automatically_on_page_load(self):
        """Engage must be pressed for the listener, not by the listener.

        The old dashboard put a full-screen gate up on every visit and waited
        for a tap, so nothing played until the button was pressed. The page now
        calls the same engage path itself as soon as the first status poll names
        a file.
        """
        auto = self.html[self.html.index("function maybeAutoStartStream()") :]
        auto = auto[: auto.index("function syncHostPlayerFile")]
        self.assertIn("streamAutoStarted = true", auto)
        self.assertIn("engageStreamFromServer(lastServerResume, { auto: true })", auto)
        # A running desk player is no longer a reason to stay silent here.
        self.assertNotIn("if (hostPlayerActive) {{", auto)
        # No branch may return without either starting audio or offering the
        # tap. `streamAutoStarted` is what stops the 2 s poll re-entering, and a
        # branch that skips it re-showed the gate over a mix that was already
        # playing (the iPhone "page stays open from the start" report). The two
        # leading guards are exempt: one is the spent-attempt check itself and
        # the other only means there is no file to talk about yet.
        body_only = auto.split("if (!activeAudioFile) return;", 1)[1]
        for branch in body_only.split("return;")[:-1]:
            self.assertTrue(
                "streamAutoStarted = true" in branch
                or "engageStreamFromServer" in branch
                or "showStreamOverlay" in branch
                or "autoElIsPlaying" in branch,
                f"a branch returns without spending the attempt or explaining: {branch[-160:]}",
            )
        # The branch that dismisses the gate for audio that already plays must
        # also spend the attempt, or the poll fights it forever.
        guard = auto[auto.index("if (audioStreamEngaged || audioElIsPlaying())"):]
        guard = guard[: guard.index("}")]
        self.assertIn("streamAutoStarted = true", guard)
        self.assertIn("hideStreamOverlay()", guard)

    def test_a_refused_automatic_start_arms_the_first_tap(self):
        """Autoplay refusal is a policy, not an error.

        When the page-load attempt is refused the gate says what to do and the
        very next interaction anywhere on the dashboard retries the stream,
        because a browser that blocked the automatic play() approves a play()
        inside a real gesture.
        """
        self.assertIn("function armFirstGestureEngage()", self.html)
        self.assertIn("function disarmGestureEngage()", self.html)
        self.assertIn("activateMobileStream();", self.html[
            self.html.index("function armFirstGestureEngage()") :
            self.html.index("function disarmGestureEngage()")])
        refused = self.html[self.html.index("function reportEngageRefused") :]
        refused = refused[: refused.index("// After a skip the element")]
        self.assertIn("armFirstGestureEngage()", refused)
        self.assertIn("NotAllowedError", refused)
        # A real tap is never downgraded to a "tap to start" notice.
        self.assertIn("if (!onGesture && (automatic || notAllowed))", refused)
        # The retry is spent once the mix is really playing.
        engaged = self.html[self.html.index("function noteStreamEngaged()") :]
        engaged = engaged[: engaged.index("function resumeAfterNetworkDrop")]
        self.assertIn("disarmGestureEngage()", engaged)

    def test_a_flapping_active_file_cannot_reload_the_player_in_a_loop(self):
        """A changing `active_file` re-points the element; a flapping one loops.

        This was shipped and hit in the field: a server that named a different
        file on alternate polls made the dashboard reload its player roughly
        every two seconds. The page must require a new file to be reported twice
        and must reject an A -> B -> A bounce, so a flapping cursor cannot drive
        playback at all.
        """
        self.assertIn("const FILE_CHANGE_CONFIRMATIONS", self.html)
        self.assertIn("let pendingFileCount", self.html)
        self.assertIn("let fileFollowFrozenUntil", self.html)
        # The change handler consults the guard before it touches the element.
        change = self.html[self.html.index(
            "if (data.active_file && activeAudioFile !== data.active_file)") :]
        guard = change.index("FILE_CHANGE_CONFIRMATIONS")
        bind = change.index("audioEl.src = '/api/stream?file='")
        self.assertLess(guard, bind,
                        "the flap guard must run before the element is re-pointed")
        self.assertIn("data.active_file === secondLastFile", change,
                      "an A -> B -> A oscillation must be refused")
        self.assertIn("fileFollowFrozenUntil", change)
        # The very first file is bound with no confirmation delay.
        self.assertIn("if (activeAudioFile) {", change)

    def test_the_gate_starts_hidden_and_opens_only_when_it_is_needed(self):
        """The dashboard must be usable while the automatic attempt runs."""
        self.assertIn('id="streamOverlay"\n', self.html)
        overlay = self.html[self.html.index('<div id="streamOverlay"') :]
        overlay = overlay[: overlay.index("<div class=\"overlay-card\">")]
        self.assertNotIn('class="show"', overlay)
        self.assertIn('id="streamOverlayLead"', self.html)
        self.assertIn("let streamOverlayWanted = false;", self.html)

    def test_an_unattended_start_cannot_write_off_library_files(self):
        """Autoplay must not permanently skip a mix because a share hiccuped.

        Advancing past an undecodable mix records it as unplayable on the host,
        so no phone is served it again. That is correct for a run a person
        started, but the page now starts by itself: a transient read failure on
        a page nobody is watching would have written off good files forever.
        """
        self.assertIn("let listenerEngaged = false;", self.html)
        # A network error is never a verdict about the file.
        error_handler = self.html[self.html.index("audioEl.addEventListener('error'") :]
        error_handler = error_handler[: error_handler.index("loadThemeList();")]
        self.assertIn("MEDIA_ERR_NETWORK", error_handler)
        self.assertLess(error_handler.index("MEDIA_ERR_NETWORK"),
                        error_handler.index("handleUnplayableMix"))
        # And an unattended run may not advance the queue at all.
        handler = self.html[self.html.index("function handleUnplayableMix(") :]
        handler = handler[: handler.index("function showPlaybackExhaustedNotice")]
        guard = handler.index("if (!listenerEngaged)")
        self.assertLess(guard, handler.index("unplayableFiles.add"))
        self.assertLess(guard, handler.index("sendAction('track_failed'"))
        # The automatic entry point does not mark the run as listener-driven.
        auto = self.html[self.html.index("function maybeAutoStartStream()") :]
        auto = auto[: auto.index("function syncHostPlayerFile")]
        self.assertIn("{ auto: true }", auto)
        self.assertNotIn("listenerEngaged = true", auto)
        # A real tap does, so the Engage button keeps the old behaviour.
        engage = self.html[self.html.index("function engageStreamFromServer(") :]
        engage = engage[: engage.index("function reportEngageRefused")]
        self.assertIn("if (onGesture)", engage)
        self.assertIn("listenerEngaged = true", engage)

    def test_an_engage_tap_with_no_queue_asks_the_host_to_rescan(self):
        self.assertIn("function rescanForEngage()", self.html)
        activate = self.html[self.html.index("function activateMobileStream()") :]
        activate = activate[: activate.index("function handlePlayPauseClick")]
        self.assertIn("rescanForEngage()", activate)
        self.assertIn("action=rescan", activate)

    def test_the_rescan_unlocks_audio_inside_the_tap(self):
        """iOS only approves play() in the gesture's own task.

        Awaiting the rescan fetch first puts the later play() outside the
        gesture, so it is rejected and the mix stays silent.
        """
        self.assertIn("unlockStreamElement", self.html)
        fn = self.html[self.html.index("function rescanForEngage()") :]
        fn = fn[: fn.index("function engageStreamFromServer") ] if "function engageStreamFromServer" in fn else fn
        head = fn.split("fetch(", 1)[0]
        self.assertIn("unlockStreamElement()", head,
                      "the unlock must run before the first await")

    def test_the_unlock_clip_is_allowed_by_the_page_csp(self):
        """A `data:` unlock clip is blocked, and the tap then does nothing.

        The dashboard sends `Content-Security-Policy: … media-src 'self'
        blob: …`. A `data:audio/wav` clip violates that directive, so `src`
        fails with MEDIA_ERR_SRC_NOT_SUPPORTED before any sound is possible.
        The element is still holding that clip when the error arrives, and the
        media-error handler cannot tell it from a genuinely undecodable mix, so
        it wrote off the file the listener was about to play. The tap then
        looked dead: no sound, and the target mix marked unplayable.

        The clip is built as a `blob:` URL, which the policy allows.
        """
        self.assertNotIn("data:audio", self.html,
                         "a data: media source is blocked by this page's CSP")
        self.assertIn("silentWavBlobUrl", self.html)
        self.assertIn("URL.createObjectURL", self.html)
        # The policy the page is actually served with must permit the clip.
        media_src = ""
        for name, value in mwc.sec.security_headers():
            if name == "Content-Security-Policy":
                for directive in value.split(";"):
                    directive = directive.strip()
                    if directive.startswith("media-src"):
                        media_src = directive
        self.assertIn("blob:", media_src,
                      "media-src must allow the blob: unlock clip")

    def test_the_unlock_error_is_not_blamed_on_the_listener_mix(self):
        """The throwaway clip's error must not mark the real mix unplayable."""
        handler = self.html[self.html.index("audioEl.addEventListener('error'") :]
        handler = handler[: handler.index("showRestartNotice")]
        self.assertIn("__unlocking", handler,
                      "the error handler must ignore the unlock clip")
        self.assertIn("beginUnlock", self.html)
        self.assertIn("endUnlock", self.html)

    def test_a_tap_explanation_survives_the_status_poll(self):
        """The 2 s poll used to overwrite what the tap had just said.

        A rescan that finds nothing writes a specific explanation. The next
        /api/status painted "Waiting for the host to queue a mix…" over it, so
        the listener saw their answer blink away and concluded the button was
        dead.
        """
        sync = self.html[self.html.index("function syncStreamOverlayDetail()") :]
        sync = sync[: sync.index("function hideStreamOverlay()")]
        self.assertIn("lastTapExplanation", sync)
        self.assertIn("TAP_EXPLANATION_HOLD_MS", sync)
        # And the hold is released once audio really starts.
        hide = self.html[self.html.index("function hideStreamOverlay()") :]
        hide = hide[: hide.index("let streamAutoStarted")] if "let streamAutoStarted" in hide else hide
        self.assertIn("lastTapExplanation = \"\"", hide)

    def test_the_disabled_autoplay_state_is_not_reported_as_a_failure(self):
        """A phone can still stream when the host did not arm the queue.

        `WEB_COMPANION_AUTOPLAY=false` only stops the host from playing on its
        own; the queue is built and /api/stream serves it. Calling that
        "Streaming disabled" put a failure banner over a working player, which
        reads as "the Engage button does not work".
        """
        detail = self.html[self.html.index("function streamUnavailableDetail(data)") :]
        detail = detail[: detail.index("function updateAutoplayNotice")]
        self.assertIn("if (state.status === 'disabled') return '';", detail)

        notice = self.html[self.html.index("function updateAutoplayNotice(state)") :]
        notice = notice[: notice.index("function showRestartNotice")]
        self.assertIn("disabled: '🎧 Stream ready", notice)
        self.assertNotIn("⏹ Streaming disabled", notice)
        self.assertNotIn("Streaming is disabled on the host", self.html)


if __name__ == "__main__":
    unittest.main()
