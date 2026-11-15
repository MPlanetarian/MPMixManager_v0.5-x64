#!/usr/bin/env python3
"""MP Reporter OpenCode agent progress briefings: contract, rendering, dispatch.

The reporter is run by a systemd timer every ten minutes, so the contract it
has to keep is narrow and worth pinning: it must find running sessions, render
a text report and a spoken script, honour the interval bucket, and attach both
reports to one email without touching the daily reporter's 1/day limit.
"""

import importlib.util
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "scripts" / "mp_opencode_progress_reporter.py"
GUIDE = ROOT / "scripts" / "mp_opencode_progress_reporter.sh"
MENU = ROOT / "scripts" / "manage_mp_reporter.sh"
SCHEDULER = ROOT / "scripts" / "mp_scheduled_reporter.sh"
EXAMPLE = ROOT / "config.env.example"
README = ROOT / "README.md"


def _load():
    spec = importlib.util.spec_from_file_location(
        "mp_opencode_progress_reporter", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _session(title="Agent work", sid="ses_test0000000000000000000", **extra):
    session = {
        "id": sid,
        "title": title,
        "agent": "build",
        "model": "deepseek-ai/test-model",
        "parent_id": None,
        "directory": "/tmp/project",
        "created": 1_700_000_000_000,
        "updated": 1_700_000_600_000,
        "busy": int(time.time() * 1000) - 600_000,
        "tokens_in": 1234,
        "tokens_out": 56,
        "messages": [],
        "source": "api",
    }
    session.update(extra)
    return session


def _data(sessions, minutes=10):
    now = time.time()
    return {
        "generated": __import__("datetime").datetime.fromtimestamp(now),
        "window_start": __import__("datetime").datetime.fromtimestamp(now - 600),
        "window_end": __import__("datetime").datetime.fromtimestamp(now),
        "window_minutes": minutes,
        "hostname": "X299ROG",
        "source": "opencode api",
        "note": "",
        "sessions": sessions,
    }


class TestSessionCollection(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def test_running_message_is_reduced_to_report_fields(self):
        message = {
            "id": "msg_1", "type": "assistant", "agent": "build",
            "time": {"created": 1_700_000_000_000},
            "content": [
                {"type": "reasoning", "text": "thinking"},
                {"type": "text", "text": "Finished the parse fix."},
                {"type": "tool", "name": "shell",
                 "state": {"status": "completed",
                           "input": {"command": "pytest -q\necho done"}}},
            ],
        }
        progress = self.mod._message_progress(message)
        self.assertEqual(progress["text"], "Finished the parse fix.")
        self.assertEqual(progress["type"], "assistant")
        self.assertEqual(len(progress["tools"]), 1)
        self.assertIn("pytest -q", progress["tools"][0])
        self.assertNotIn("echo done", progress["tools"][0])

    def test_failed_tool_status_is_reported(self):
        message = {"type": "assistant", "content": [
            {"type": "tool", "name": "edit",
             "state": {"status": "error", "input": {"filePath": "a.py"}}}]}
        progress = self.mod._message_progress(message)
        self.assertIn("[error]", progress["tools"][0])
        self.assertIn("a.py", progress["tools"][0])

    def test_api_sessions_are_the_authority_when_reachable(self):
        active = {"data": {"ses_a": {"type": "running"},
                           "ses_b": {"type": "idle"}}}
        listing = {"data": [{"id": "ses_a", "title": "Alpha",
                             "agent": "build", "model": {"id": "m"},
                             "location": {"directory": "/p"},
                             "time": {"created": 1, "updated": 2, "busy": 2}}]}
        messages = {"data": [{"id": "msg_1", "type": "assistant",
                              "time": {"created": 2},
                              "content": [{"type": "text", "text": "hi"}]}]}

        def fake_get(endpoint, path, params=None, timeout=10.0):
            if path == "/api/session/active":
                return active
            if path == "/api/session":
                return listing
            if path.endswith("/message"):
                return messages
            raise AssertionError("unexpected path " + path)

        with mock.patch.object(self.mod, "opencode_endpoint", return_value={"url": "u"}):
            with mock.patch.object(self.mod, "_api_get", side_effect=fake_get):
                sessions, source, note = self.mod.collect_running_sessions(10)

        self.assertEqual(source, "opencode api")
        self.assertEqual(note, "")
        self.assertEqual([s["id"] for s in sessions], ["ses_a"])
        self.assertEqual(sessions[0]["messages"][0]["text"], "hi")

    def test_unreachable_api_falls_back_to_the_database(self):
        with mock.patch.object(self.mod, "opencode_endpoint", return_value={}):
            with mock.patch.object(self.mod, "_live_sessions_via_db",
                                   return_value=[_session()]):
                sessions, source, note = self.mod.collect_running_sessions(10)
        self.assertEqual(source, "opencode.db")
        self.assertIn("database", note)
        self.assertEqual(len(sessions), 1)

    def test_broken_api_also_falls_back(self):
        with mock.patch.object(self.mod, "opencode_endpoint", return_value={"url": "u"}):
            with mock.patch.object(self.mod, "_api_get",
                                   side_effect=OSError("connection refused")):
                with mock.patch.object(self.mod, "_live_sessions_via_db",
                                       return_value=[]):
                    sessions, source, _note = self.mod.collect_running_sessions(10)
        self.assertEqual(source, "opencode.db")
        self.assertEqual(sessions, [])

    def test_database_fallback_keeps_resumed_sessions(self):
        """The DB fallback must agree with OpenCode about what is running.

        ``idle_outcome`` describes the previous idle and survives into the new
        turn, so filtering on it dropped every session resumed after a finished
        task while the live API still listed them.
        """
        db = Path(tempfile.mkdtemp(prefix="fallback-db-")) / "opencode.db"
        now = int(time.time() * 1000)
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE session_v2 (
                id TEXT PRIMARY KEY, parent_id TEXT, title TEXT, agent TEXT,
                model TEXT, directory TEXT, tokens_input INTEGER,
                tokens_output INTEGER, time_created INTEGER,
                time_updated INTEGER, time_busy INTEGER, time_idle INTEGER,
                idle_outcome TEXT, time_suspended INTEGER,
                time_archived INTEGER);
            CREATE TABLE session_message (
                id TEXT PRIMARY KEY, session_id TEXT, type TEXT, seq INTEGER,
                time_created INTEGER, data TEXT);
        """)
        rows = [
            # Resumed after a finished turn: running, but outcome is stale.
            ("ses_resumed", "Resumed after success", now - 30_000, now - 120_000,
             "succeeded"),
            # Finished and quiet: must stay excluded.
            ("ses_done", "Finished and idle", now - 600_000, now - 300_000,
             "succeeded"),
            # Fresh first turn: no outcome recorded yet.
            ("ses_new", "Fresh turn", now - 10_000, None, None),
        ]
        for sid, title, busy, idle, outcome in rows:
            conn.execute(
                "INSERT INTO session_v2 (id, title, agent, model, directory,"
                " tokens_input, tokens_output, time_created, time_updated,"
                " time_busy, time_idle, idle_outcome)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, title, "build", json.dumps({"id": "m"}), "/tmp",
                 0, 0, busy, busy, busy, idle, outcome))
        conn.commit()
        conn.close()

        with mock.patch.object(self.mod, "OPENCODE_DB", db):
            sessions = self.mod._live_sessions_via_db()
        titles = {s["title"] for s in sessions}
        self.assertEqual(titles, {"Resumed after success", "Fresh turn"})


class TestReportRendering(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def test_text_report_names_every_session_and_its_progress(self):
        session = _session(title="Fix the tray close bug")
        session.update(new_messages=4, new_tool_calls=9,
                       latest_text="Both tasks are done.",
                       new_tools=["shell: pytest -q"],
                       latest_tools=[], busy_seconds=600,
                       last_activity="20:31:08", tokens_in=1000,
                       tokens_out=42, last_prompt="", parent_title="")
        text = self.mod.build_text_report(_data([session]))
        self.assertIn("OPENCODE AGENT PROGRESS BRIEFING", text)
        self.assertIn("Fix the tray close bug", text)
        self.assertIn("Both tasks are done.", text)
        self.assertIn("4 new message(s), 9 tool call(s)", text)
        self.assertIn("shell: pytest -q", text)

    def test_quiet_session_is_marked_quiet(self):
        session = _session(title="Long render")
        session.update(new_messages=0, new_tool_calls=0, latest_text="",
                       new_tools=[], latest_tools=[], busy_seconds=7200,
                       last_activity="19:00:00", tokens_in=1, tokens_out=1,
                       last_prompt="", parent_title="")
        text = self.mod.build_text_report(_data([session]))
        self.assertIn("quiet this window", text)

    def test_idle_briefing_still_renders(self):
        text = self.mod.build_text_report(_data([]))
        self.assertIn("No OpenCode agent sessions are running", text)
        script = self.mod.build_speech_script(_data([]))
        self.assertIn("No OpenCode agent sessions", script)
        self.assertIn("Stephen Hawking", script)

    def test_speech_script_counts_sessions_and_time(self):
        sessions = []
        for index in range(3):
            session = _session(title="Agent %d" % index)
            session.update(new_messages=index, new_tool_calls=index,
                           latest_text="step %d" % index, new_tools=[],
                           latest_tools=[], busy_seconds=90,
                           last_activity="20:00:00", tokens_in=0,
                           tokens_out=0, last_prompt="", parent_title="")
            sessions.append(session)
        script = self.mod.build_speech_script(_data(sessions))
        self.assertIn("3 agent sessions are running", script)
        self.assertIn("Agent 0", script)
        self.assertIn("End of transmission", script)

    def test_html_report_escapes_markup_in_titles(self):
        session = _session(title="Refactor <script> & test")
        session.update(new_messages=1, new_tool_calls=1, latest_text="ok",
                       new_tools=["read: a.py"], latest_tools=[],
                       busy_seconds=5, last_activity="20:00:00",
                       tokens_in=0, tokens_out=0, last_prompt="",
                       parent_title="Parent task")
        html = self.mod.build_html_report(_data([session]))
        self.assertIn("OpenCode Agent Progress Briefing", html)
        self.assertIn("Parent task", html)
        self.assertIn("Refactor &lt;script&gt; &amp; test", html)
        self.assertNotIn("<script>", html)

    def test_tool_collapsing_groups_repeats(self):
        lines = self.mod._collapse_tools(["a", "a", "a", "b"])
        self.assertEqual(lines, ["a (x3)", "b"])

    def test_speech_script_names_every_session_the_text_report_lists(self):
        """The audio must not stop at six while the email lists up to the cap."""
        sessions = []
        for index in range(9):
            session = _session(title="Agent %d" % index)
            session.update(new_messages=1, new_tool_calls=1, latest_text="",
                           new_tools=[], latest_tools=[], busy_seconds=60,
                           last_activity="20:00:00", tokens_in=0,
                           tokens_out=0, last_prompt="", parent_title="")
            sessions.append(session)
        data = _data(sessions)
        data["max_sessions"] = 12
        script = self.mod.build_speech_script(data)
        for index in range(9):
            self.assertIn("Agent %d" % index, script)
        self.assertNotIn("further session", script)

    def test_speech_script_counts_sessions_beyond_the_cap(self):
        sessions = []
        for index in range(15):
            session = _session(title="Agent %d" % index)
            session.update(new_messages=1, new_tool_calls=1, latest_text="",
                           new_tools=[], latest_tools=[], busy_seconds=60,
                           last_activity="20:00:00", tokens_in=0,
                           tokens_out=0, last_prompt="", parent_title="")
            sessions.append(session)
        data = _data(sessions)
        data["max_sessions"] = 12
        script = self.mod.build_speech_script(data)
        self.assertIn("Agent 11", script)
        self.assertNotIn("Agent 12", script)
        self.assertIn("3 further sessions running", script)


class TestIntervalBucket(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def test_same_bucket_is_not_due(self):
        state = {"last_bucket": self.mod.current_bucket(10)}
        self.assertFalse(self.mod.bucket_due(state, 10))
        self.assertTrue(self.mod.bucket_due(state, 10, force=True))

    def test_previous_bucket_is_due(self):
        state = {"last_bucket": self.mod.current_bucket(10) - 1}
        self.assertTrue(self.mod.bucket_due(state, 10))

    def test_window_never_claims_more_than_the_interval(self):
        old = "2026-01-01T00:00:00"
        start, end = self.mod.window_bounds({"last_sent_timestamp": old}, 10)
        self.assertLessEqual(end - start, 10 * 60 + 1)

    def test_window_uses_last_send_when_recent(self):
        recent = __import__("datetime").datetime.now() - __import__(
            "datetime").timedelta(minutes=10)
        start, end = self.mod.window_bounds(
            {"last_sent_timestamp": recent.isoformat()}, 10)
        self.assertAlmostEqual(end - start, 600, delta=5)


class TestDispatch(unittest.TestCase):
    def setUp(self):
        self.mod = _load()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.paths = {
            "REPORTS_DIR": root / "REPORTS",
            "TTS_DIR": root / "TTS",
            "STATE_FILE": root / "state.json",
            "LOG_FILE": root / "reporter.log",
        }
        for attr, path in self.paths.items():
            patch = mock.patch.object(self.mod, attr, path)
            patch.start()
            self.addCleanup(patch.stop)

    def _cfg(self, **extra):
        cfg = {
            "EMAIL_REPORTS_ENABLED": "true",
            "OPENCODE_PROGRESS_ENABLED": "true",
            "OPENCODE_PROGRESS_INTERVAL_MINUTES": "10",
            "OPENCODE_PROGRESS_RECIPIENTS": "ops@example.com",
            "OPENCODE_PROGRESS_SUBJECT_PREFIX": "OpenCode Agent Progress",
            "OPENCODE_PROGRESS_ATTACH_TEXT": "true",
            "OPENCODE_PROGRESS_ATTACH_AUDIO": "true",
            "OPENCODE_PROGRESS_TTS_ENABLED": "true",
            "OPENCODE_PROGRESS_SEND_WHEN_IDLE": "true",
            "OPENCODE_PROGRESS_ALERT_SOUND": "false",
            "OPENCODE_PROGRESS_MAX_SESSIONS": "12",
            "OPENCODE_PROGRESS_MAX_MESSAGES": "12",
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "587",
            "SMTP_USE_TLS": "true",
            "SMTP_USE_SSL": "false",
            "SMTP_USER": "sender@example.com",
            "SMTP_PASSWORD": "secret",
            "EMAIL_SENDER_NAME": "MP Reporter",
            "EMAIL_SENDER_ADDR": "sender@example.com",
            "EMAIL_RECIPIENTS": "daily@example.com",
            "CONFIRMATION_ALERT_SOUND": "",
        }
        cfg.update(extra)
        return cfg

    def test_send_attaches_text_and_audio_and_records_state(self):
        now_ms = int(time.time() * 1000)
        session = _session()
        session["messages"] = [
            {"id": "msg_1", "type": "assistant", "created": now_ms - 60_000,
             "agent": "build", "text": "Both tasks are done.",
             "tools": ["shell: pytest -q"], "reasoning": ""},
            {"id": "msg_2", "type": "assistant", "created": now_ms - 30_000,
             "agent": "build", "text": "", "tools": ["read: a.py"],
             "reasoning": ""},
        ]
        audio_file = Path(self.tmp.name) / "brief.mp3"
        audio_file.write_bytes(b"ID3fake")

        sent = {}

        class FakeSMTP:
            def __init__(self, host, port, timeout=40, context=None):
                sent["host"] = host
            def ehlo(self):
                return None
            def starttls(self, context=None):
                return None
            def login(self, user, password):
                sent["user"] = user
            def sendmail(self, user, recipients, payload):
                sent["recipients"] = recipients
                sent["payload"] = payload
            def quit(self):
                return None

        with mock.patch.object(self.mod, "load_config", return_value=self._cfg()):
            with mock.patch.object(self.mod, "collect_running_sessions",
                                   return_value=([session], "opencode api", "")):
                with mock.patch.object(self.mod, "synthesize_audio",
                                       return_value={"mp3": audio_file,
                                                     "wav": None,
                                                     "size_kb": 1.0}):
                    with mock.patch.object(self.mod.smtplib, "SMTP", FakeSMTP):
                        ok = self.mod.execute_dispatch(force=True)

        self.assertTrue(ok)
        self.assertEqual(sent["recipients"], ["ops@example.com"])
        payload = sent["payload"]
        self.assertIn("Subject: =?utf-8?q?OpenCode_Agent_Progress", payload)
        self.assertIn("Content-Type: text/plain", payload)
        self.assertIn("Content-Type: audio/mpeg", payload)
        self.assertIn("filename=\"brief.mp3\"", payload)
        self.assertTrue(
            "OpenCode Agent Progress" in payload
            or "OpenCode_Agent_Progress" in payload)
        self.assertEqual(len(list((self.paths["REPORTS_DIR"]).glob("*.txt"))), 1)
        state = json.loads(self.paths["STATE_FILE"].read_text())
        self.assertEqual(state["total_emails_sent"], 1)
        self.assertEqual(state["last_sessions"][0]["new_messages"], 2)

    def test_second_call_in_same_bucket_is_skipped(self):
        with mock.patch.object(self.mod, "load_config", return_value=self._cfg()):
            with mock.patch.object(self.mod, "collect_running_sessions",
                                   return_value=([], "opencode api", "")):
                with mock.patch.object(self.mod, "synthesize_audio",
                                       return_value={"mp3": None, "wav": None,
                                                     "size_kb": 0}):
                    with mock.patch.object(self.mod.smtplib, "SMTP") as smtp:
                        smtp.return_value.login.return_value = None
                        self.assertTrue(self.mod.execute_dispatch(force=True))
                        calls = smtp.return_value.sendmail.call_count
                        self.assertTrue(self.mod.execute_dispatch(force=False))
                        self.assertEqual(
                            smtp.return_value.sendmail.call_count, calls)

    def test_disabled_progress_reporter_does_nothing(self):
        with mock.patch.object(self.mod, "load_config",
                               return_value=self._cfg(OPENCODE_PROGRESS_ENABLED="false")):
            self.assertFalse(self.mod.execute_dispatch(force=True))

    def test_no_recipients_fails_before_smtp(self):
        with mock.patch.object(self.mod, "load_config",
                               return_value=self._cfg(OPENCODE_PROGRESS_RECIPIENTS="",
                                                      EMAIL_RECIPIENTS="")):
            with mock.patch.object(self.mod, "collect_running_sessions",
                                   return_value=([], "opencode api", "")):
                with mock.patch.object(self.mod, "synthesize_audio",
                                       return_value={"mp3": None, "wav": None,
                                                     "size_kb": 0}):
                    with mock.patch.object(self.mod.smtplib, "SMTP") as smtp:
                        self.assertFalse(self.mod.execute_dispatch(force=True))
                        smtp.assert_not_called()

    def test_missing_password_fails_before_smtp(self):
        with mock.patch.object(self.mod, "load_config",
                               return_value=self._cfg(SMTP_PASSWORD="")):
            with mock.patch.object(self.mod, "collect_running_sessions",
                                   return_value=([], "opencode api", "")):
                with mock.patch.object(self.mod, "synthesize_audio",
                                       return_value={"mp3": None, "wav": None,
                                                     "size_kb": 0}):
                    with mock.patch.object(self.mod.smtplib, "SMTP") as smtp:
                        self.assertFalse(self.mod.execute_dispatch(force=True))
                        smtp.assert_not_called()

    def test_idle_briefings_can_be_suppressed(self):
        cfg = self._cfg(OPENCODE_PROGRESS_SEND_WHEN_IDLE="false")
        with mock.patch.object(self.mod, "load_config", return_value=cfg):
            with mock.patch.object(self.mod, "collect_running_sessions",
                                   return_value=([], "opencode api", "")):
                with mock.patch.object(self.mod.smtplib, "SMTP") as smtp:
                    self.assertTrue(self.mod.execute_dispatch(force=True))
                    smtp.assert_not_called()

    def test_recipients_fall_back_to_the_daily_list(self):
        cfg = self._cfg(OPENCODE_PROGRESS_RECIPIENTS="")
        self.assertEqual(self.mod.recipients(cfg), ["daily@example.com"])

    def test_dry_run_builds_reports_without_smtp(self):
        with mock.patch.object(self.mod, "load_config", return_value=self._cfg()):
            with mock.patch.object(self.mod, "collect_running_sessions",
                                   return_value=([], "opencode api", "")):
                with mock.patch.object(self.mod, "synthesize_audio",
                                       return_value={"mp3": None, "wav": None,
                                                     "size_kb": 0}):
                    with mock.patch.object(self.mod.smtplib, "SMTP") as smtp:
                        self.assertTrue(self.mod.execute_dispatch(
                            force=True, dry_run=True))
                        smtp.assert_not_called()

    def test_concurrent_dispatch_is_rejected_by_the_lock(self):
        lock_path = self.paths["STATE_FILE"].parent / "opencode_progress.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder = open(lock_path, "w")
        self.addCleanup(holder.close)
        self.mod.fcntl.flock(holder, self.mod.fcntl.LOCK_EX | self.mod.fcntl.LOCK_NB)

        with mock.patch.object(self.mod, "load_config", return_value=self._cfg()):
            with mock.patch.object(self.mod, "collect_running_sessions",
                                   return_value=([], "opencode api", "")):
                with mock.patch.object(self.mod.smtplib, "SMTP") as smtp:
                    self.assertTrue(self.mod.execute_dispatch(force=True))
                    smtp.assert_not_called()


class TestWiring(unittest.TestCase):
    """The feature ships through config, the menu, the scheduler and the docs."""

    def test_config_example_documents_the_interval(self):
        text = EXAMPLE.read_text(encoding="utf-8")
        self.assertIn('OPENCODE_PROGRESS_INTERVAL_MINUTES="10"', text)
        self.assertIn("OPENCODE_PROGRESS_TTS_ENABLED", text)
        self.assertIn("OPENCODE_PROGRESS_ATTACH_AUDIO", text)

    def test_helper_installs_a_ten_minute_timer(self):
        text = GUIDE.read_text(encoding="utf-8")
        self.assertIn("mp-reporter-opencode-progress.timer", text)
        self.assertIn("OnUnitActiveSec", text)
        self.assertIn("DEFAULT_MINUTES=10", text)

    def test_menu_exposes_the_progress_console(self):
        text = MENU.read_text(encoding="utf-8")
        self.assertIn("opencode_progress_menu", text)
        self.assertIn("MP Reporter OpenCode progress reporting", text)

    def test_scheduler_status_reports_the_progress_timer(self):
        text = SCHEDULER.read_text(encoding="utf-8")
        self.assertIn("opencode-progress", text)
        self.assertIn("OpenCode Progress Email", text)

    def test_readme_documents_the_briefings(self):
        text = README.read_text(encoding="utf-8")
        self.assertIn("mp_opencode_progress_reporter.py", text)
        self.assertIn("every 10 minutes", text)

    def test_module_is_executable(self):
        self.assertTrue(MODULE_PATH.is_file())
        self.assertTrue(GUIDE.is_file())


if __name__ == "__main__":
    unittest.main()
