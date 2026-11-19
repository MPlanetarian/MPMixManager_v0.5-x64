#!/usr/bin/env python3
"""Tests for the MP Liquid Mixer master recorder.

The recorder owns the WAV header now: parec emits raw PCM, the mixer writes
unsigned 32-bit RIFF sizes and rolls to a new part at the 4 GiB WAV ceiling.
These tests pin the ceiling, the part names, the header bytes and the rollover
without ever writing 4 GiB to disk.
"""

import datetime
import shutil
import signal
import struct
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIXER_DIR = PROJECT_ROOT / "MP_Liquid_Mixer"
sys.path.insert(0, str(MIXER_DIR))

import mixer_recording  # noqa: E402


class TestRecordingHelpers(unittest.TestCase):
    def test_recording_directory_falls_back_to_the_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "app"
            config = Path(tmp) / "cfg"
            base.mkdir()
            config.mkdir()
            out = mixer_recording.recording_dir(base, config)
            self.assertEqual(out, (base / "MP_AUDIOWAV_REC_OUTPUT").resolve())
            self.assertTrue(out.is_dir())

    def test_recording_name_is_timestamped(self):
        name = mixer_recording.recording_name(datetime.datetime(2026, 11, 18, 21, 30, 5))
        self.assertEqual(name, "MP_LIQUID_MIXER_REC_20261118_213005.wav")

    def test_command_records_raw_pcm_for_the_mixer_writer(self):
        cmd = mixer_recording.recorder_command(Path("/tmp/out.wav"))
        joined = " ".join(cmd)
        self.assertIn("parec", joined)
        self.assertIn("@DEFAULT_MONITOR@", joined)
        self.assertIn("--format=s16le", joined)
        self.assertIn("--raw", cmd)
        self.assertNotIn("--file-format=wav", joined)


class TestWavCeiling(unittest.TestCase):
    def test_the_limit_is_four_gib_minus_the_header(self):
        self.assertEqual(mixer_recording.MAX_WAV_FILE_BYTES, 4_294_967_295)
        self.assertEqual(mixer_recording.WAV_HEADER_BYTES, 44)
        self.assertEqual(mixer_recording.MAX_WAV_DATA_BYTES, 4_294_967_251)
        # The payload is rounded down to a whole 16-bit stereo frame.
        self.assertEqual(mixer_recording.MAX_PART_DATA_BYTES, 4_294_967_248)
        self.assertEqual(mixer_recording.MAX_PART_DATA_BYTES % 4, 0)
        self.assertEqual(mixer_recording.REC_MAX_FILE_BYTES, mixer_recording.MAX_PART_DATA_BYTES)

    def test_the_header_uses_unsigned_32_bit_sizes(self):
        data_bytes = 1000
        header = mixer_recording.wav_header_bytes(data_bytes)
        self.assertEqual(len(header), 44)
        fields = struct.unpack("<4sI4s4sIHHIIHH4sI", header)
        self.assertEqual(fields[0], b"RIFF")
        self.assertEqual(fields[1], 36 + data_bytes)       # I, not a signed int
        self.assertEqual(fields[2], b"WAVE")
        self.assertEqual(fields[3], b"fmt ")
        self.assertEqual(fields[4], 16)
        self.assertEqual(fields[5], 1)                     # PCM
        self.assertEqual(fields[6], 2)
        self.assertEqual(fields[7], 48000)
        self.assertEqual(fields[8], 192000)
        self.assertEqual(fields[9], 4)
        self.assertEqual(fields[10], 16)
        self.assertEqual(fields[11], b"data")
        self.assertEqual(fields[12], data_bytes)           # uint32, above 2 GiB too

    def test_a_payload_above_two_gib_is_representable(self):
        header = mixer_recording.wav_header_bytes(2**31)
        fields = struct.unpack("<4sI4s4sIHHIIHH4sI", header)
        self.assertEqual(fields[12], 2**31)
        self.assertEqual(fields[1], 36 + 2**31)
        self.assertGreater(fields[12], 2**31 - 1)

    def test_the_largest_part_fits_uint32(self):
        header = mixer_recording.wav_header_bytes(mixer_recording.MAX_PART_DATA_BYTES)
        fields = struct.unpack("<4sI4s4sIHHIIHH4sI", header)
        self.assertEqual(fields[12], mixer_recording.MAX_PART_DATA_BYTES)
        self.assertEqual(fields[1], 36 + mixer_recording.MAX_PART_DATA_BYTES)
        self.assertLessEqual(fields[1], 0xFFFFFFFF)
        with self.assertRaises(ValueError):
            mixer_recording.wav_header_bytes(0xFFFFFFFF)

    def test_part_names_append_part_n_before_the_suffix(self):
        original = Path("MP_LIQUID_MIXER_REC_20261118_213005.wav")
        self.assertEqual(mixer_recording.recording_part_path(original, 1), original)
        self.assertEqual(
            mixer_recording.recording_part_path(original, 2).name,
            "MP_LIQUID_MIXER_REC_20261118_213005_PART_2.wav",
        )
        self.assertEqual(
            mixer_recording.recording_part_path(original, 12).name,
            "MP_LIQUID_MIXER_REC_20261118_213005_PART_12.wav",
        )

    def test_flac_names_and_limits(self):
        name = mixer_recording.recording_name(datetime.datetime(2026, 11, 18, 21, 30, 5), "FLAC")
        self.assertEqual(name, "MP_LIQUID_MIXER_REC_20261118_213005.flac")
        self.assertGreater(mixer_recording.FLAC_PART_LIMIT_BYTES, 4_000_000_000)
        self.assertLess(mixer_recording.FLAC_PART_LIMIT_BYTES, mixer_recording.MAX_WAV_FILE_BYTES)


class FakeStdout:
    """A blocking pipe stand-in: pending chunks, then EOF when closed."""

    def __init__(self, chunks=()):
        self._chunks = list(chunks)
        self._event = threading.Event()
        self.closed = False

    def read(self, size=-1):
        if self._chunks:
            return self._chunks.pop(0)
        self._event.wait(5.0)
        return b""

    def close(self):
        self.closed = True
        self._event.set()


class FakeProc:
    def __init__(self, chunks=(), *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.stdout = FakeStdout(chunks)
        self.poll_result = None
        self.signals = []

    def poll(self):
        return self.poll_result

    def send_signal(self, sig):
        self.signals.append(sig)
        self.poll_result = 0
        self.stdout.close()

    def wait(self, timeout=None):
        self.poll_result = 0
        self.stdout.close()
        return 0

    def terminate(self):
        self.poll_result = 0
        self.stdout.close()

    def kill(self):
        self.poll_result = -9
        self.stdout.close()


class TestMasterRecorder(unittest.TestCase):
    def _factory(self, chunks):
        created = []

        def factory(*args, **kwargs):
            proc = FakeProc(chunks)
            created.append(proc)
            return proc

        return factory, created

    def test_start_and_stop_write_a_valid_wav(self):
        audio = (b"\x01\x02\x03\x04" * 1024)  # 4096 bytes, whole frames
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "REC"
            out.mkdir()
            factory, created = self._factory([audio])
            recorder = mixer_recording.MasterRecorder(base=Path(tmp))
            with patch("mixer_recording.shutil_which", return_value="/usr/bin/parec"), \
                 patch("mixer_recording.recording_dir", return_value=out), \
                 patch("mixer_recording.subprocess.Popen", side_effect=factory):
                path = recorder.start()
                self.assertIsNotNone(path)
                self.assertTrue(recorder.is_recording)
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if recorder._writer is not None and recorder._writer.data_bytes == len(audio):
                        break
                    time.sleep(0.01)
                saved = recorder.stop()
            self.assertEqual(saved, path)
            self.assertFalse(recorder.is_recording)
            self.assertEqual(recorder.last_saved, path)
            self.assertEqual(created[0].signals, [signal.SIGINT])
            with wave.open(str(path), "rb") as wf:
                self.assertEqual(wf.getnchannels(), 2)
                self.assertEqual(wf.getsampwidth(), 2)
                self.assertEqual(wf.getframerate(), 48000)
                self.assertEqual(wf.getnframes() * 4, len(audio))

    def test_rollover_starts_part_2_and_part_3(self):
        audio = b"\x00" * 10000
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "REC"
            out.mkdir()
            factory, _created = self._factory([audio])
            recorder = mixer_recording.MasterRecorder(base=Path(tmp))
            with patch("mixer_recording.shutil_which", return_value="/usr/bin/parec"), \
                 patch("mixer_recording.recording_dir", return_value=out), \
                 patch("mixer_recording.subprocess.Popen", side_effect=factory), \
                 patch.object(mixer_recording, "MAX_PART_DATA_BYTES", 4000):
                path = recorder.start()
                self.assertIsNotNone(path)
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if recorder.part == 3 and recorder._writer is not None \
                            and recorder._writer.data_bytes == 2000:
                        break
                    time.sleep(0.01)
                self.assertEqual(recorder.part, 3)
                saved = recorder.stop()
            part1 = Path(path)
            part2 = part1.with_name(f"{part1.stem}_PART_2{part1.suffix}")
            part3 = part1.with_name(f"{part1.stem}_PART_3{part1.suffix}")
            self.assertEqual(saved, part3)
            self.assertEqual(part1.stat().st_size, 44 + 4000)
            self.assertEqual(part2.stat().st_size, 44 + 4000)
            self.assertEqual(part3.stat().st_size, 44 + 2000)
            for part, expected in ((part1, 4000), (part2, 4000), (part3, 2000)):
                with wave.open(str(part), "rb") as wf:
                    self.assertEqual(wf.getnframes() * 4, expected)

    def test_no_data_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "REC"
            out.mkdir()
            factory, _created = self._factory([])
            recorder = mixer_recording.MasterRecorder(base=Path(tmp))
            with patch("mixer_recording.shutil_which", return_value="/usr/bin/parec"), \
                 patch("mixer_recording.recording_dir", return_value=out), \
                 patch("mixer_recording.subprocess.Popen", side_effect=factory):
                recorder.start()
                self.assertIsNone(recorder.stop())
            self.assertIsNone(recorder.last_saved)

    def test_stop_sends_sigint(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "REC"
            out.mkdir()
            factory, created = self._factory([b"\x00" * 400])
            recorder = mixer_recording.MasterRecorder(base=Path(tmp))
            with patch("mixer_recording.shutil_which", return_value="/usr/bin/parec"), \
                 patch("mixer_recording.recording_dir", return_value=out), \
                 patch("mixer_recording.subprocess.Popen", side_effect=factory):
                recorder.start()
                recorder.stop()
            self.assertEqual(created[0].signals, [signal.SIGINT])

    def test_unexpected_exit_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "REC"
            out.mkdir()
            factory, created = self._factory([])
            recorder = mixer_recording.MasterRecorder(base=Path(tmp))
            with patch("mixer_recording.shutil_which", return_value="/usr/bin/parec"), \
                 patch("mixer_recording.recording_dir", return_value=out), \
                 patch("mixer_recording.subprocess.Popen", side_effect=factory):
                recorder.start()
                created[0].poll_result = 1
                recorder.tick()
            self.assertFalse(recorder.is_recording)
            self.assertTrue(recorder.error)

    def test_missing_parec_is_reported(self):
        recorder = mixer_recording.MasterRecorder()
        with patch("mixer_recording.shutil_which", return_value=None):
            self.assertIsNone(recorder.start())
        self.assertIn("parec", recorder.error)

    def test_format_defaults_to_wav_and_toggles(self):
        recorder = mixer_recording.MasterRecorder()
        self.assertEqual(recorder.format, "WAV")
        self.assertEqual(recorder.set_format("FLAC"), "FLAC")
        self.assertEqual(recorder.format, "FLAC")
        self.assertEqual(recorder.set_format("WAV"), "WAV")

    @unittest.skipUnless(shutil.which("flac") or shutil.which("ffmpeg"),
                         "no FLAC encoder is installed")
    def test_flac_part_is_a_valid_flac_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "one.flac"
            part = mixer_recording._FlacPart(
                path, mixer_recording.find_flac(), mixer_recording.find_ffmpeg()
            )
            part.write(b"\0" * (48000 * 4))  # one second of stereo silence
            part.finalize()
            self.assertGreater(part.output_bytes, 0)
            self.assertEqual(path.read_bytes()[:4], b"fLaC")

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg command is not installed")
    def test_flac_falls_back_to_ffmpeg(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fallback.flac"
            part = mixer_recording._FlacPart(path, None, shutil.which("ffmpeg"))
            part.write(b"\0" * (48000 * 4))
            part.finalize()
            self.assertGreater(part.output_bytes, 0)
            self.assertEqual(path.read_bytes()[:4], b"fLaC")


if __name__ == "__main__":
    unittest.main()
