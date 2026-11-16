"""One announcement at a time.

Everything that produces sound on this machine used to play independently:
MP Reporter's email chime and any one-shot announcement. A chime or a second
briefing could start in the middle of a 140-second narration.

These tests pin the shared lock that serialises them:

  * a second holder waits for the first,
  * a non-blocking caller can decline instead of queueing,
  * the lock is released when the holder exits, even on an exception,
  * different processes contend for the same lock,
  * MP Reporter takes it before playing.
"""

import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
LOCK_MODULE = SCRIPTS / "mp_speech_lock.py"

sys.path.insert(0, str(SCRIPTS))


class TestLockModule(unittest.TestCase):
    def setUp(self):
        import mp_speech_lock
        self.mod = mp_speech_lock
        self.tmp = Path(tempfile.mkdtemp(prefix="speech-lock-"))
        self.path = self.tmp / "speech.lock"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_second_holder_waits_for_the_first(self):
        script = textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(SCRIPTS)!r})
            from mp_speech_lock import SpeechLock
            from pathlib import Path
            path = Path({str(self.path)!r})
            if sys.argv[1] == "hold":
                with SpeechLock(path):
                    print("held", flush=True)
                    time.sleep(3)
            else:
                start = time.time()
                with SpeechLock(path):
                    print(f"waited {{time.time() - start:.1f}}", flush=True)
        """)
        script_path = self.tmp / "worker.py"
        script_path.write_text(script, encoding="utf-8")

        holder = subprocess.Popen(
            [sys.executable, str(script_path), "hold"],
            stdout=subprocess.PIPE, text=True)
        try:
            # Wait until the holder actually has the lock.
            self.assertEqual(holder.stdout.readline().strip(), "held")
            started = time.time()
            result = subprocess.run(
                [sys.executable, str(script_path), "wait"],
                capture_output=True, text=True, timeout=30)
            waited = time.time() - started
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertGreater(waited, 1.5,
                               "the second holder did not wait for the first")
        finally:
            holder.wait(timeout=30)

    def test_non_blocking_caller_can_decline(self):
        with self.mod.SpeechLock(self.path):
            self.assertFalse(
                self.mod.SpeechLock(self.path).acquire(block=False),
                "a held lock must not be granted to a non-blocking caller")

    def test_lock_is_released_after_use(self):
        lock = self.mod.SpeechLock(self.path)
        with lock:
            pass
        # Free again: a fresh non-blocking acquire must succeed.
        self.assertTrue(self.mod.SpeechLock(self.path).acquire(block=False))

    def test_lock_is_released_on_exception(self):
        lock = self.mod.SpeechLock(self.path)
        try:
            with lock:
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertTrue(self.mod.SpeechLock(self.path).acquire(block=False))

    def test_default_path_is_under_state_dir(self):
        self.assertIn("state", str(self.mod.lock_path()))
        self.assertTrue(str(self.mod.lock_path()).endswith(".lock"))

    def test_cli_run_holds_the_lock(self):
        """`mp_speech_lock.py run <cmd>` must serialise the wrapped command."""
        marker = self.tmp / "ran.txt"
        result = subprocess.run(
            [sys.executable, str(LOCK_MODULE), "run",
             sys.executable, "-c", f"open({str(marker)!r},'w').write('ok')"],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "MP_SPEECH_LOCK": str(self.path)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(marker.is_file())

    def test_cli_status_reports_free(self):
        result = subprocess.run(
            [sys.executable, str(LOCK_MODULE), "status"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "MP_SPEECH_LOCK": str(self.path)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("free", result.stdout.lower())


class TestReporterUsesTheLock(unittest.TestCase):
    def setUp(self):
        self.path = SCRIPTS / "mp_opencode_progress_reporter.py"
        self.source = self.path.read_text(encoding="utf-8")

    def test_alert_waits_for_the_lock(self):
        start = self.source.index("def play_confirmation_alert")
        end = self.source.index("\ndef ", start + 1)
        func = self.source[start:end]
        self.assertIn("_speech_lock", func,
                      "the email chime must not play over a narration")
        self.assertIn("with lock", func)

    def test_missing_lock_helper_degrades_gracefully(self):
        start = self.source.index("def _speech_lock")
        end = self.source.index("\ndef ", start + 1)
        self.assertIn("nullcontext", self.source[start:end])

    def test_reporter_compiles(self):
        result = subprocess.run([sys.executable, "-m", "py_compile", str(self.path)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class TestNoSecondPlayerRing(unittest.TestCase):
    """The 10-minute overlap had a second cause: two independent players."""

    def test_reporter_alert_is_gated_by_config(self):
        text = (SCRIPTS / "mp_opencode_progress_reporter.py").read_text(encoding="utf-8")
        self.assertIn("OPENCODE_PROGRESS_ALERT_SOUND", text,
                      "the chime must stay switchable for a silent setup")


if __name__ == "__main__":
    unittest.main()
