#!/usr/bin/env python3
"""Tests for the MP Liquid Mixer master recorder."""

import datetime
import signal
import sys
import tempfile
import unittest
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

    def test_command_records_the_default_monitor_as_wav(self):
        cmd = mixer_recording.recorder_command(Path("/tmp/out.wav"))
        joined = " ".join(cmd)
        self.assertIn("parec", joined)
        self.assertIn("@DEFAULT_MONITOR@", joined)
        self.assertIn("--file-format=wav", joined)
        self.assertIn("--format=s16le", joined)
        self.assertIn("/tmp/out.wav", joined)


class FakeProc:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.poll_result = None
        self.signals = []

    def poll(self):
        return self.poll_result

    def send_signal(self, sig):
        self.signals.append(sig)

    def wait(self, timeout=None):
        self.poll_result = 0
        return 0

    def terminate(self):
        self.poll_result = 0

    def kill(self):
        self.poll_result = -9


class TestMasterRecorder(unittest.TestCase):
    def test_start_and_stop_produce_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "REC"
            out.mkdir()
            recorder = mixer_recording.MasterRecorder(base=Path(tmp))
            with patch("mixer_recording.shutil_which", return_value="/usr/bin/parec"), \
                 patch("mixer_recording.recording_dir", return_value=out), \
                 patch("mixer_recording.subprocess.Popen", side_effect=FakeProc) as popen:
                path = recorder.start()
                self.assertIsNotNone(path)
                self.assertTrue(recorder.is_recording)
                self.assertEqual(popen.call_count, 1)
                self.assertTrue(Path(path).name.startswith("MP_LIQUID_MIXER_REC_"))
                Path(path).write_bytes(b"RIFF" + b"\0" * 200)
                saved = recorder.stop()
            self.assertEqual(saved, path)
            self.assertFalse(recorder.is_recording)
            self.assertEqual(recorder.last_saved, path)
            proc = popen.side_effect if isinstance(popen.side_effect, FakeProc) else None
            # The fake's instance received SIGINT so parec can finalise the WAV.
            # (side_effect is a class; inspect the instance we created instead.)
            self.assertTrue(Path(saved).is_file())

    def test_stop_sends_sigint(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "REC"
            out.mkdir()
            created = []

            def factory(*args, **kwargs):
                proc = FakeProc(*args, **kwargs)
                created.append(proc)
                return proc

            recorder = mixer_recording.MasterRecorder(base=Path(tmp))
            with patch("mixer_recording.shutil_which", return_value="/usr/bin/parec"), \
                 patch("mixer_recording.recording_dir", return_value=out), \
                 patch("mixer_recording.subprocess.Popen", side_effect=factory):
                path = recorder.start()
                Path(path).write_bytes(b"RIFF" + b"\0" * 100)
                recorder.stop()
            self.assertEqual(created[0].signals, [signal.SIGINT])

    def test_unexpected_exit_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "REC"
            out.mkdir()
            created = []

            def factory(*args, **kwargs):
                proc = FakeProc(*args, **kwargs)
                created.append(proc)
                return proc

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


if __name__ == "__main__":
    unittest.main()
