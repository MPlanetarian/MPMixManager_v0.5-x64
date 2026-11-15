"""MP Harmony integration with MP Reporter and the MP Audio Responder.

These tests pin the three links that make Harmony a first-class speech source:

  * mp_harmony_tts.py speaks through the bridge's OpenAI-compatible endpoint
    and stores one shared voice setting,
  * mp_harmony_archive.py copies Harmony pairs into TTS_AUDIO/HARMONY once,
    idempotently, and never copies a half-written WAV,
  * the Responder can select Harmony as its speech engine and falls back when
    the bridge is down, and MP Reporter exposes a menu for both.

The tests do not require a running bridge; anything that would need one is
skipped, and the rest runs against temporary directories.
"""

import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
TTS = SCRIPTS / "mp_harmony_tts.py"
ARCHIVE = SCRIPTS / "mp_harmony_archive.py"
RESPONDER_MENU = SCRIPTS / "manage_mp_responder.sh"
REPORTER_MENU = SCRIPTS / "manage_mp_reporter.sh"
RESPONDER_CANDIDATES = (
    Path.home() / "MP_AI_Agent_Audio_Responder-0.1-x64" / "MP_OpenCode_Audio_Responder.sh",
    Path.home() / "Documents" / "BASH_SCRIPTS" / "MP_OpenCode_Audio_Responder.sh",
)


def _find_responder():
    for candidate in RESPONDER_CANDIDATES:
        if candidate.is_file():
            return candidate
    return RESPONDER_CANDIDATES[0]


RESPONDER = _find_responder()

sys.path.insert(0, str(SCRIPTS))


def _run(args, env_extra=None, timeout=60):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=timeout)


def _make_wav(path: Path, samples: int = 64) -> None:
    """A minimal but genuinely valid mono 16-bit WAV."""
    data = b"\x00\x00" * samples
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 22050, 44100, 2, 16)
    header += b"data" + struct.pack("<I", len(data))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + data)


class TestHarmonyTTSClient(unittest.TestCase):
    def test_module_compiles_and_exposes_the_api(self):
        import mp_harmony_tts
        self.assertTrue(hasattr(mp_harmony_tts, "HarmonyTTS"))
        self.assertTrue(hasattr(mp_harmony_tts, "configured_voice"))
        self.assertIn("morpheus", mp_harmony_tts.KNOWN_VOICES)
        self.assertIn("stephen_hawking_matrix", mp_harmony_tts.KNOWN_VOICES)

    def test_bridge_url_honours_the_environment(self):
        import importlib
        import mp_harmony_tts as mod
        old = dict(os.environ)
        try:
            os.environ["HARMONY_API_URL"] = "http://example.test:9999/"
            importlib.reload(mod)
            self.assertEqual(mod.bridge_url(), "http://example.test:9999")
            del os.environ["HARMONY_API_URL"]
            os.environ["HARMONY_PORT"] = "12345"
            importlib.reload(mod)
            self.assertIn("12345", mod.bridge_url())
        finally:
            os.environ.clear()
            os.environ.update(old)
            importlib.reload(mod)

    def test_voice_setting_round_trips_through_the_config_file(self):
        import mp_harmony_tts as mod
        with tempfile.TemporaryDirectory() as td:
            old_dir, old_file = mod.CONFIG_DIR, mod.CONFIG_FILE
            try:
                mod.CONFIG_DIR = Path(td)
                mod.CONFIG_FILE = Path(td) / "harmony_voice"
                self.assertTrue(mod.save_voice("neo"))
                self.assertEqual(mod.configured_voice(), "neo")
            finally:
                mod.CONFIG_DIR, mod.CONFIG_FILE = old_dir, old_file

    def test_cli_rejects_an_unknown_voice(self):
        result = _run([sys.executable, str(TTS), "--set-voice", "not-a-real-voice"],
                      env_extra={"HARMONY_API_URL": "http://127.0.0.1:1"})
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_check_reports_a_dead_bridge_cleanly(self):
        result = _run([sys.executable, str(TTS), "--check"],
                      env_extra={"HARMONY_API_URL": "http://127.0.0.1:1"}, timeout=20)
        self.assertEqual(result.returncode, 1)
        self.assertIn("NOT reachable", result.stderr)

    def test_synthesis_against_a_live_bridge(self):
        import mp_harmony_tts as mod
        tts = mod.HarmonyTTS()
        if not tts.available():
            self.skipTest("MP Harmony bridge is not running")
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "sample.wav"
            ok, path, err = tts.speak_to_file("integration test", out)
            self.assertTrue(ok, err)
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 44)


class TestHarmonyArchive(unittest.TestCase):
    def _seed(self, root: Path, count: int):
        audio = root / "audio_responses"
        audio.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            stem = f"agent_speech_20260101_0000{i:02d}_abc{i}"
            (audio / f"{stem}.txt").write_text(f"spoken text {i}", encoding="utf-8")
            _make_wav(audio / f"{stem}.wav")
        return audio

    def test_sync_copies_pairs_and_is_idempotent(self):
        import mp_harmony_archive as mod
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            audio = self._seed(root, 3)
            reporter = root / "MP_REPORTER"

            first = mod.sync_archive(audio, reporter)
            self.assertEqual(first["copied"], 3)
            self.assertEqual(first["failed"], 0)

            second = mod.sync_archive(audio, reporter)
            self.assertEqual(second["copied"], 0)
            self.assertEqual(second["already_archived"], 3)

            archived = reporter / "TTS_AUDIO" / "HARMONY"
            self.assertEqual(len(list(archived.glob("*.wav"))), 3)
            self.assertEqual(len(list(archived.glob("*.txt"))), 3)

    def test_sync_skips_a_half_written_wav(self):
        import mp_harmony_archive as mod
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            audio = root / "audio_responses"
            audio.mkdir()
            stem = "agent_speech_incomplete"
            (audio / f"{stem}.txt").write_text("still being written", encoding="utf-8")
            (audio / f"{stem}.wav").write_bytes(b"RIFF")   # truncated

            result = mod.sync_archive(audio, root / "MP_REPORTER")
            self.assertEqual(result["copied"], 0)
            self.assertEqual(result["total_pairs"], 0)

    def test_status_counts_pending_without_copying(self):
        import mp_harmony_archive as mod
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            audio = self._seed(root, 2)
            reporter = root / "MP_REPORTER"
            status = mod.archive_status(audio, reporter)
            self.assertEqual(status["harmony_pairs"], 2)
            self.assertEqual(status["pending"], 2)
            self.assertFalse((reporter / "TTS_AUDIO" / "HARMONY").exists())

    def test_dry_run_changes_nothing(self):
        import mp_harmony_archive as mod
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            audio = self._seed(root, 2)
            reporter = root / "MP_REPORTER"
            result = mod.sync_archive(audio, reporter, dry_run=True)
            self.assertEqual(result["copied"], 2)
            self.assertFalse((reporter / "TTS_AUDIO" / "HARMONY").exists())

    def test_cli_status_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            audio = root / "audio_responses"
            audio.mkdir()
            _make_wav(audio / "agent_speech_x.wav")
            (audio / "agent_speech_x.txt").write_text("hi", encoding="utf-8")
            result = _run([sys.executable, str(ARCHIVE), "status",
                           "--audio-dir", str(audio),
                           "--reporter-dir", str(root / "MP_REPORTER"),
                           "--json"])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["pending"], 1)


class TestResponderEngine(unittest.TestCase):
    """The responder's embedded engine selects Harmony when asked."""

    def _load_engine(self, engine: str):
        if not RESPONDER.is_file():
            self.skipTest("audio responder script not installed")
        text = RESPONDER.read_text(encoding="utf-8")
        start = text.index("exec python3 -u - <<'PYEOF'")
        start = text.index("\n", start) + 1
        end = text.index("\nPYEOF", start)
        tmp = Path(tempfile.mkdtemp(prefix="responder-engine-"))
        env_keys = {
            "STATE_DIR": "state",
            "LOG_FILE": "r.log",
            "LOCK_FILE": "r.lock",
            "PAUSE_FILE": "paused",
            "STATE_FILE": "state.json",
            "MP_OPENCODE_TTS_ENGINE": engine,
        }
        old = {}
        for key, val in env_keys.items():
            old[key] = os.environ.get(key)
            os.environ[key] = str(tmp / val) if key != "MP_OPENCODE_TTS_ENGINE" else val
        try:
            ns = {"__name__": "responder_engine"}
            exec(compile(text[start:end], "<responder>", "exec"), ns)
        finally:
            for key, val in old.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val
        return ns

    def test_engine_setting_is_read(self):
        ns = self._load_engine("harmony-first")
        self.assertEqual(ns["TTS_ENGINE"], "harmony-first")

    def test_harmony_engine_produces_audio_when_the_bridge_is_up(self):
        ns = self._load_engine("harmony")
        tts = ns.get("HARMONY_TTS")
        if tts is None or not tts.available():
            self.skipTest("MP Harmony bridge is not running")
        with tempfile.TemporaryDirectory() as td:
            wav, txt = ns["synthesise"]("responder engine test", Path(td), "probe")
            self.assertIsNotNone(wav)
            self.assertTrue(Path(wav).is_file())

    def test_harmony_only_falls_through_instead_of_failing(self):
        ns = self._load_engine("harmony")
        # Point the client at a dead port so Harmony cannot answer.
        if ns.get("HARMONY_TTS") is not None:
            ns["HARMONY_TTS"].base_url = "http://127.0.0.1:1"
        with tempfile.TemporaryDirectory() as td:
            # No espeak/ffmpeg guarantee, so only assert it does not raise.
            try:
                ns["synthesise"]("fallback probe", Path(td), "probe")
            except Exception as exc:  # pragma: no cover
                self.fail(f"synthesise raised instead of falling back: {exc}")

    def test_script_parses(self):
        if not RESPONDER.is_file():
            self.skipTest("audio responder script not installed")
        result = _run(["bash", "-n", str(RESPONDER)])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_voice_command_reports_the_shared_setting(self):
        if not RESPONDER.is_file():
            self.skipTest("audio responder script not installed")
        result = _run(["bash", str(RESPONDER), "voice"], timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Voice:", result.stdout)
        self.assertIn("Engine:", result.stdout)


class TestMenus(unittest.TestCase):
    def test_responder_menu_parses_and_reports_status(self):
        result = _run(["bash", "-n", str(RESPONDER_MENU)])
        self.assertEqual(result.returncode, 0, result.stderr)
        result = _run(["bash", str(RESPONDER_MENU), "status"], timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MP AUDIO RESPONDER", result.stdout)
        self.assertIn("Speech engine", result.stdout)

    def test_reporter_menu_exposes_the_responder(self):
        text = REPORTER_MENU.read_text(encoding="utf-8")
        self.assertIn("MP Audio Responder & Harmony Voice Control", text)
        self.assertIn("manage_mp_responder.sh", text)
        self.assertIn("responder|audio-responder|voice", text)

    def test_reporter_shell_parses(self):
        result = _run(["bash", "-n", str(REPORTER_MENU)])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reporter_cli_routes_to_the_responder_menu(self):
        result = _run(["bash", str(REPORTER_MENU), "responder", "status"], timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MP AUDIO RESPONDER", result.stdout)


class TestReporterIntegration(unittest.TestCase):
    def test_report_generator_syncs_the_archive_first(self):
        text = (SCRIPTS / "mp_report_generator.py").read_text(encoding="utf-8")
        self.assertIn("sync_harmony_archive", text)
        self.assertIn("mp_harmony_archive", text)

    def test_harmony_harvester_reads_the_archive_and_source(self):
        text = (SCRIPTS / "generate_ai_report_section.py").read_text(encoding="utf-8")
        self.assertIn('"HARMONY"', text)
        self.assertIn("archived", text)

    def test_harvester_marks_archived_pairs_and_dedupes(self):
        import generate_ai_report_section as gen
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            live = root / "live"
            archived = root / "MP_REPORTER" / "TTS_AUDIO" / "HARMONY"
            for folder in (live, archived):
                folder.mkdir(parents=True)
                _make_wav(folder / "agent_speech_x.wav")
                (folder / "agent_speech_x.txt").write_text("hello", encoding="utf-8")

            old = {k: os.environ.get(k) for k in ("HARMONY_AUDIO_DIR", "MP_REPORTER_DIR")}
            os.environ["HARMONY_AUDIO_DIR"] = str(live)
            os.environ["MP_REPORTER_DIR"] = str(root / "MP_REPORTER")
            try:
                items = gen.harvest_harmony_agent()
            finally:
                for key, val in old.items():
                    if val is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = val

            self.assertEqual(len(items), 1, "the same stem must be counted once")
            self.assertTrue(items[0]["archived"], "the archived copy is the one reported")


if __name__ == "__main__":
    unittest.main()
