#!/usr/bin/env python3
"""
Unit and regression tests for MP Audio Player resilience and recovery:
- Prevents premature stops on large FLAC mixes (e.g. 1.4 GB / 48kHz Airbase mixes).
- Verifies AudioResampler handles empty/partial input buffers without position runaway.
- Verifies AudioFeederThread does not mark EOF on transient I/O stalls before end of track.
- Verifies AudioEngine.play() recovers and reloads cleanly after stop_playback().
- Verifies dead daemon detection, stale socket cleanup, and auto-recovery.
- Verifies seamless switching between mixes of differing sample rates (48kHz <-> 44.1kHz).
"""

import unittest
from unittest.mock import MagicMock, patch
import os
import sys
import time
import tempfile
import threading
import subprocess
from pathlib import Path
import numpy as np
import soundfile as sf

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# PortAudio can segfault the interpreter during teardown when a real output
# stream is open (the production runner `tests/run_tests.py` already sets
# this). A direct `pytest`/`unittest` run must be hermetic too, otherwise the
# whole test file can abort *after* every test passed.
os.environ.setdefault("MP_DISABLE_AUDIO_OUTPUT", "1")
os.environ.setdefault("MP_DISABLE_BLUETOOTH_MONITOR", "1")

import MP_Audio_Player
from MP_Audio_Player import (
    AudioPrefetchBuffer,
    AudioFeederThread,
    AudioResampler,
    SoundFileAudioSource,
    AudioEngine,
    is_player_daemon_running,
    open_audio_source,
    _cleanup_stale_daemon_files,
    PID_FILE,
    SOCKET_PATH,
    PORT_FILE,
    STATE_FILE
)


class _ShortReportedSource:
    """A real decoder whose declared length is longer than the audio it holds.

    This is what libsndfile does for an MP3 whose final frame is truncated: the
    header promises more frames than ``read()`` can ever deliver.
    """

    def __init__(self, inner, extra_frames: int):
        self._inner, self._total = inner, int(inner.total_frames()) + int(extra_frames)
        self.samplerate, self.channels = inner.samplerate, inner.channels
        self.frames = self._total

    def read(self, frames):
        return self._inner.read(frames)

    def seek(self, frame):
        return self._inner.seek(frame)

    def tell(self):
        return self._inner.tell()

    def total_frames(self):
        return self._total

    def close(self):
        return self._inner.close()


class TestAudioPlayerResilience(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        # 1. Standard 44.1kHz stereo test wav
        self.mix_44k_path = str(Path(self.temp_dir.name) / "test_44k.wav")
        sr1 = 44100
        dur1 = 2.0
        t1 = np.linspace(0, dur1, int(sr1 * dur1), False)
        sine1 = (np.sin(2 * np.pi * 440 * t1) * 0.4).astype(np.float32)
        stereo1 = np.column_stack((sine1, sine1))
        sf.write(self.mix_44k_path, stereo1, sr1)

        # 2. 48kHz 24-bit test mix simulating Airbase FLAC
        self.mix_48k_path = str(Path(self.temp_dir.name) / "test_airbase_48k.flac")
        sr2 = 48000
        dur2 = 2.5
        t2 = np.linspace(0, dur2, int(sr2 * dur2), False)
        sine2 = (np.sin(2 * np.pi * 880 * t2) * 0.5).astype(np.float32)
        stereo2 = np.column_stack((sine2, sine2))
        sf.write(self.mix_48k_path, stereo2, sr2, subtype="PCM_24")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_resampler_handles_zero_frames_without_runaway(self):
        """Verifies AudioResampler handles empty read_func without index runaway."""
        resampler = AudioResampler()

        # Mock read_func returning 0 frames
        mock_read = MagicMock(return_value=np.zeros((0, 2), dtype=np.float32))

        # Request 1024 frames while source is temporarily starved (48000 -> 44100: speed_ratio = 48000/44100)
        speed_ratio = 48000.0 / 44100.0
        output = resampler.process(mock_read, 1024, speed_ratio)
        self.assertEqual(len(output), 0)
        self.assertEqual(resampler.in_pos, 0.0)

        # Next read has actual data
        chunk = np.ones((4096, 2), dtype=np.float32) * 0.5
        mock_read.return_value = chunk
        output2 = resampler.process(mock_read, 1024, speed_ratio)
        self.assertEqual(len(output2), 1024)
        self.assertGreater(resampler.in_pos, 0.0)

    def test_feeder_does_not_flag_eof_on_transient_stall(self):
        """Verifies feeder treats empty read as transient stall if well before total_frames."""
        source = MagicMock()
        source.total_frames.return_value = 5000000  # Large file (e.g. Airbase)
        source.samplerate = 48000
        source.channels = 2

        # Simulate read returning empty for 2 tries then real data
        call_count = [0]
        def fake_read(n):
            call_count[0] += 1
            if call_count[0] <= 2:
                return np.zeros((0, 2), dtype=np.float32)
            source.tell.return_value = 65536
            return np.ones((n, 2), dtype=np.float32)

        source.read.side_effect = fake_read
        source.tell.return_value = 0

        engine_mock = MagicMock()
        engine_mock._try_reopen_source.return_value = True

        buf = AudioPrefetchBuffer(48000, buffer_sec=2.0)
        feeder = AudioFeederThread(source, buf, engine_mock)
        feeder.start()

        # Let feeder loop run through transient stalls
        time.sleep(0.4)
        feeder.stop()
        feeder.join(timeout=1.0)

        # Buffer must NOT have been marked as EOF
        self.assertFalse(buf.eof)
        self.assertGreater(buf.available_frames, 0)

    def test_feeder_flags_eof_when_decoder_ends_before_its_declared_total(self):
        """A decoder that stops early must still end the track.

        libsndfile reports the frame count an MP3 header declares, which can be
        longer than the frames it can actually return. The feeder used to wait
        forever because the position was still "well before" that total, so the
        deck sat silent at 02:41 of 02:42 and the playlist never advanced.
        """
        source = MagicMock()
        source.samplerate, source.channels = 44100, 2
        source.total_frames.return_value = 44100 * 60
        source.tell.return_value = 44100 * 59
        source.read.return_value = np.zeros((0, 2), dtype=np.float32)

        buf = AudioPrefetchBuffer(44100, buffer_sec=0.2)
        feeder = AudioFeederThread(source, buf, MagicMock())
        feeder.eof_stall_grace_sec = 0.25
        feeder.start()
        try:
            deadline = time.time() + 3.0
            while time.time() < deadline and not buf.eof:
                time.sleep(0.02)
            self.assertTrue(buf.eof, "an early decode end must still flag EOF")
        finally:
            feeder.stop()
            feeder.join(timeout=1.0)

    def test_playlist_advances_when_decoder_ends_before_its_declared_length(self):
        """Reproduces the stuck deck end-to-end, then requires the next track."""
        playlist = [
            {"name": "Airbase 48k", "path": self.mix_48k_path, "size_mb": 2.0, "ext": "FLAC"},
            {"name": "Mix 44k", "path": self.mix_44k_path, "size_mb": 1.0, "ext": "WAV"},
        ]
        engine = AudioEngine(playlist, auto_bluetooth=False)
        engine.repeat_mode = "all"

        def _open_short(path):
            # Truthfully decodable audio, but a total that runs 3 s long, the
            # same mismatch libsndfile reports for the real GTA VI MP3.
            return _ShortReportedSource(SoundFileAudioSource(path), 44100 * 3)

        with patch.object(MP_Audio_Player, "open_audio_source", side_effect=_open_short):
            self.assertTrue(engine.load_track(0, True))
            engine.feeder.eof_stall_grace_sec = 0.2
            outdata = np.zeros((4096, 2), dtype=np.float32)
            deadline = time.time() + 10.0
            while time.time() < deadline and not engine.track_ended:
                engine._render_audio(outdata, 4096)
                time.sleep(0.001)
            self.assertTrue(engine.track_ended, "the early end must surface as track_ended")
            engine.check_track_end()
            self.assertEqual(engine.current_index, 1)
            self.assertEqual(engine.state, "playing")
        engine.shutdown()

    def test_remove_tracks_keeps_playing_and_drops_the_playing_row_cleanly(self):
        playlist = [
            {"name": "Airbase 48k", "path": self.mix_48k_path, "size_mb": 2.0, "ext": "FLAC"},
            {"name": "Mix 44k", "path": self.mix_44k_path, "size_mb": 1.0, "ext": "WAV"},
        ]
        engine = AudioEngine(playlist, auto_bluetooth=False)
        try:
            self.assertTrue(engine.load_track(0, True))
            self.assertEqual(engine.remove_tracks([self.mix_44k_path]), 1)
            self.assertEqual(len(engine.playlist), 1)
            self.assertEqual(engine.current_index, 0)
            self.assertEqual(engine.current_track.get("path"), self.mix_48k_path)
            self.assertEqual(engine.state, "playing")

            self.assertEqual(engine.remove_tracks([self.mix_48k_path]), 1)
            self.assertEqual(engine.playlist, [])
            self.assertIsNone(engine.current_track)
            self.assertEqual(engine.state, "stopped")
        finally:
            engine.shutdown()

    def test_audio_engine_reloads_and_recovers_after_stop(self):
        """Verifies that calling play() after stop_playback() properly restores playback."""
        playlist = [
            {"name": "Airbase 48k", "path": self.mix_48k_path, "size_mb": 2.0, "ext": "FLAC"},
            {"name": "Mix 44k", "path": self.mix_44k_path, "size_mb": 1.0, "ext": "WAV"}
        ]
        engine = AudioEngine(playlist, auto_bluetooth=False)
        # Close the stream no matter how the test ends. stop_playback() leaves
        # the PortAudio stream open by design; only shutdown() closes it, and
        # the interpreter teardown crashes inside libportaudio if it is still
        # live when the process exits.
        self.addCleanup(engine.shutdown)

        # Load first mix and start playback
        self.assertTrue(engine.load_track(0, start_playing=True))
        self.assertEqual(engine.state, "playing")
        self.assertIsNotNone(engine.feeder)

        # User or timeout stops playback
        engine.stop_playback()
        self.assertEqual(engine.state, "stopped")
        self.assertIsNone(engine.feeder)
        self.assertIsNone(engine.audio_source)

        # Previously, play() would stay stuck/silent because feeder was None.
        # With the fix, play() detects state=='stopped' and re-initializes track!
        engine.play()
        self.assertEqual(engine.state, "playing")
        self.assertIsNotNone(engine.feeder)
        self.assertIsNotNone(engine.audio_source)

        # Switch to 44.1k mix
        self.assertTrue(engine.load_track(1, start_playing=True))
        self.assertEqual(engine.current_index, 1)
        self.assertEqual(engine.state, "playing")

        engine.stop_playback()

    def test_dead_daemon_and_stale_socket_cleanup(self):
        """Verifies _cleanup_stale_daemon_files removes stale files without crashing."""
        pid_file = PID_FILE
        sock_file = SOCKET_PATH
        port_file = PORT_FILE

        # Create dummy stale files
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text("99999999")
        port_file.write_text("55555")
        try:
            sock_file.write_text("")
        except Exception:
            pass

        # Cleanup should cleanly wipe them
        _cleanup_stale_daemon_files()
        self.assertFalse(pid_file.exists())
        self.assertFalse(port_file.exists())
        self.assertFalse(sock_file.exists())

        # is_player_daemon_running should return False
        self.assertFalse(is_player_daemon_running())

    def test_audio_arg_uses_first_existing_path_line(self):
        """A launcher that joins several paths with newlines must still open the real mix."""
        good = Path(self.temp_dir.name) / "real_mix.flac"
        good.write_text("x")
        raw = f"{good}\n/tmp/vert{{}}vert{{}}\n/tmp/echo\n{good}"
        found = MP_Audio_Player._existing_audio_path(raw)
        self.assertEqual(found, str(good.resolve()))
        self.assertEqual(MP_Audio_Player._existing_audio_path(""), "")
        self.assertEqual(MP_Audio_Player._existing_audio_path("no-such-file.flac"), "")

    def test_daemon_lock_is_exclusive_across_processes(self):
        """A second playback daemon must not start, or both rewrite the status file and the playlist flickers."""
        lock = Path(self.temp_dir.name) / "mp_player.lock"
        with patch.object(MP_Audio_Player, "DAEMON_LOCK_FILE", lock):
            self.assertTrue(MP_Audio_Player._acquire_daemon_lock())
            try:
                script = (
                    "import sys\n"
                    f"sys.path.insert(0, {str(BASE_DIR)!r})\n"
                    "import MP_Audio_Player\n"
                    f"MP_Audio_Player.DAEMON_LOCK_FILE = __import__('pathlib').Path({str(lock)!r})\n"
                    "sys.exit(0 if not MP_Audio_Player._acquire_daemon_lock() else 1)\n"
                )
                proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
            finally:
                MP_Audio_Player._release_daemon_lock()

    def test_choose_daemon_owner_prefers_the_visible_player(self):
        with patch.object(MP_Audio_Player, "_pid_alive", return_value=True):
            owner = MP_Audio_Player._choose_daemon_owner([10, 20, 30], prefer_pid=20, pid_file_pid=10, ipc_pid=30)
            self.assertEqual(owner, 20)
            owner = MP_Audio_Player._choose_daemon_owner([10, 30], prefer_pid=None, pid_file_pid=10, ipc_pid=30)
            self.assertEqual(owner, 10)
            owner = MP_Audio_Player._choose_daemon_owner([30], prefer_pid=99, pid_file_pid=10, ipc_pid=None)
            self.assertEqual(owner, 30)


class TestCloseDuringReadIsSafe(unittest.TestCase):
    """Closing a source must not free libsndfile's decoder under a live read().

    ``_stop_feeder()`` calls ``source.close()`` as soon as the feeder thread is
    *asked* to stop, and ``stop()`` only clears a flag. The thread can still be
    inside ``sf.read()``, so ``sf.close()`` freed the FLAC decoder that
    libsndfile was reading through. That is a SIGSEGV inside ``read_frame_``,
    not a Python exception: the process dies with no traceback and no log line,
    which is why it looked like the player "crashed" at random while a track was
    being (re)loaded - including when a theme switch coincided with one.

    The crash could not be caught from Python, so these tests assert the
    contract instead: ``close()`` never shortens an in-flight read, the handle
    is released exactly once, and a reader that was still busy closes it.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp_dir.name) / "mix.wav")
        sf.write(self.path, np.zeros((44100, 2), dtype=np.float32), 44100)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_close_waits_for_an_in_flight_read(self):
        source = SoundFileAudioSource(self.path)
        handle = source.sf
        self.assertIsNotNone(handle)

        started, release = threading.Event(), threading.Event()

        def slow_read(*_args, **_kwargs):
            started.set()
            release.wait(timeout=5.0)
            return np.zeros((128, 2), dtype=np.float32)

        with patch.object(handle, "read", side_effect=slow_read):
            reader = threading.Thread(target=lambda: source.read(128), daemon=True)
            reader.start()
            self.assertTrue(started.wait(2.0), "the reader never entered read()")

            closer = threading.Thread(target=source.close, daemon=True)
            closer.start()
            # While the read is still in flight close() must be waiting on the
            # lock, not returning and freeing the decoder underneath it.
            time.sleep(0.15)
            self.assertTrue(closer.is_alive(),
                            "close() returned while a read was still in flight")
            self.assertIs(source.sf, handle,
                          "close() dropped the handle out from under the reader")

            release.set()
            reader.join(timeout=5.0)
            closer.join(timeout=5.0)

        self.assertFalse(reader.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertIsNone(source.sf, "the handle was not released after the read")

    def test_a_closed_source_stops_reading_and_closes_the_handle(self):
        source = SoundFileAudioSource(self.path)
        source.close()
        self.assertTrue(source._closed)
        data = source.read(1024)
        self.assertEqual(len(data), 0, "a closed source kept handing back audio")
        self.assertIsNone(source.sf)

    def test_read_then_close_then_read_does_not_reopen(self):
        source = SoundFileAudioSource(self.path)
        self.assertGreater(len(source.read(64)), 0)
        source.close()
        for _ in range(3):
            self.assertEqual(len(source.read(64)), 0)
        self.assertIsNone(source.sf, "close() was undone by a later read()")

    def test_stop_feeder_then_close_is_idempotent(self):
        source = SoundFileAudioSource(self.path)
        handle = source.sf
        with patch.object(handle, "close", wraps=handle.close) as close_spy:
            source.close()
            source.close()
            source.close()
            self.assertEqual(close_spy.call_count, 1,
                             "the soundfile handle was closed more than once")


class TestUnknownLengthAudio(unittest.TestCase):
    """A container that does not declare its total samples.

    libsndfile reports ``SF_COUNT_MAX`` (2**63-1) for these. Treated as a real
    frame count it becomes a 2.9-million-year duration and an EOF target the
    feeder can never reach, so the track plays a couple of seconds and then
    sits silent in "playing" forever.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp_dir.name) / "no_total.flac")
        sf.write(self.path, np.zeros((44100 * 2, 2), dtype=np.float32), 44100)
        data = bytearray(Path(self.path).read_bytes())
        # "fLaC" (4) + STREAMINFO block header (4) + 10 bytes of block fields.
        # The 36-bit total-samples field is the low nibble of byte 13 of the
        # block and the four bytes that follow it.
        body = 8 + 10
        data[body + 3] &= 0xF0
        for i in range(4, 8):
            data[body + i] = 0x00
        Path(self.path).write_bytes(bytes(data))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_soundfile_reports_the_unknown_length_sentinel(self):
        # Guard the fixture itself: if a future libsndfile version rescans the
        # file and finds the length, this regression test no longer applies.
        info = sf.info(self.path)
        self.assertEqual(int(info.frames), (1 << 63) - 1)

    def test_source_never_exposes_the_sentinel_as_a_length(self):
        source = open_audio_source(self.path)
        try:
            self.assertLess(source.total_frames(), 1 << 40)
            self.assertNotEqual(source.total_frames(), (1 << 63) - 1)
        finally:
            source.close()

    def test_engine_reports_a_sane_duration_and_reaches_eof(self):
        playlist = [{"name": "no_total.flac", "path": self.path,
                     "size_mb": 0.1, "ext": "FLAC"}]
        engine = AudioEngine(playlist, auto_bluetooth=False)
        self.addCleanup(engine.shutdown)
        self.assertTrue(engine.load_track(0, start_playing=False))
        self.assertLess(engine.duration_sec, 7 * 24 * 3600)
        deadline = time.time() + 8.0
        while time.time() < deadline and not engine.audio_buffer.eof:
            time.sleep(0.05)
        self.assertTrue(engine.audio_buffer.eof,
                        "the feeder never reached EOF on an unknown-length file")


if __name__ == "__main__":
    unittest.main()
