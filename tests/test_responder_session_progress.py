"""Running-session progress briefing in the MP Audio Responder.

Every ten minutes the responder narrates which OpenCode agent sessions are
running and what moved, matching the picture MP Reporter emails. These tests
pin the behaviour that matters:

  * only sessions with real activity in the window are narrated,
  * a quiet window is completely silent,
  * tool calls are counted from message payloads, not as message types,
  * the briefing stays short enough for a ten-minute cadence,
  * the job is routed to its own handler and archived under its own name.
"""

import json
import os
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

RESPONDER_CANDIDATES = (
    Path.home() / "MP_AI_Agent_Audio_Responder-0.1-x64" / "MP_OpenCode_Audio_Responder.sh",
    Path.home() / "Documents" / "BASH_SCRIPTS" / "MP_OpenCode_Audio_Responder.sh",
)

SCHEMA = """
CREATE TABLE session_v2 (
    id TEXT PRIMARY KEY, project_id TEXT, workspace_id TEXT, parent_id TEXT,
    fork_session_id TEXT, fork_boundary TEXT, slug TEXT, directory TEXT,
    path TEXT, title TEXT, version TEXT, share_url TEXT,
    summary_additions INTEGER, summary_deletions INTEGER, summary_files INTEGER,
    summary_diffs TEXT, metadata TEXT, cost REAL, tokens_input INTEGER,
    tokens_output INTEGER, tokens_reasoning INTEGER, tokens_cache_read INTEGER,
    tokens_cache_write INTEGER, revert TEXT, permission TEXT, agent TEXT,
    model TEXT, time_created INTEGER, time_updated INTEGER,
    time_compacting INTEGER, time_archived INTEGER, time_suspended INTEGER,
    resume_attempts INTEGER, time_idle INTEGER, time_viewed INTEGER,
    idle_outcome TEXT, time_busy INTEGER);
CREATE TABLE session_message (
    id TEXT PRIMARY KEY, session_id TEXT, type TEXT, seq INTEGER,
    time_created INTEGER, time_updated INTEGER, data TEXT);
"""


def find_responder():
    for candidate in RESPONDER_CANDIDATES:
        if candidate.is_file():
            return candidate
    return RESPONDER_CANDIDATES[0]


RESPONDER = find_responder()


def load_engine():
    """Load the responder's embedded Python engine with an isolated state dir."""
    text = RESPONDER.read_text(encoding="utf-8")
    start = text.index("exec python3 -u - <<'PYEOF'")
    start = text.index("\n", start) + 1
    end = text.index("\nPYEOF", start)
    tmp = Path(tempfile.mkdtemp(prefix="progress-engine-"))
    env = {
        "STATE_DIR": str(tmp),
        "LOG_FILE": str(tmp / "r.log"),
        "LOCK_FILE": str(tmp / "r.lock"),
        "PAUSE_FILE": str(tmp / "paused"),
        "STATE_FILE": str(tmp / "state.json"),
    }
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        ns = {"__name__": "responder_engine"}
        exec(compile(text[start:end], "<responder>", "exec"), ns)
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return ns


@unittest.skipUnless(RESPONDER.is_file(), "audio responder is not installed")
class ProgressBriefingTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_engine()
        self.tmp = Path(tempfile.mkdtemp(prefix="progress-db-"))
        self.db = self.tmp / "opencode.db"
        conn = sqlite3.connect(self.db)
        conn.executescript(SCHEMA)
        conn.commit()
        conn.close()
        self.ns["DB_PATH"] = self.db

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixtures ---------------------------------------------------------
    def add_session(self, sid, title, *, busy_ms, idle_ms=None, outcome=None,
                    archived=None, created_ms=None):
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO session_v2 (id, title, slug, agent, model, directory,"
            " time_created, time_updated, time_busy, time_idle, idle_outcome,"
            " time_archived) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, title, title.lower(), "build",
             json.dumps({"id": "deepseek-ai/deepseek-v4.1-flash-1017"}),
             "/tmp/work", created_ms or busy_ms, busy_ms, busy_ms,
             idle_ms, outcome, archived))
        conn.commit()
        conn.close()

    def add_message(self, sid, seq, created_ms, text="working", tools=0):
        content = [{"type": "text", "text": text}] if text else []
        for i in range(tools):
            content.append({"type": "tool", "name": "shell", "id": f"t{i}"})
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO session_message (id, session_id, type, seq,"
            " time_created, time_updated, data) VALUES (?,?,?,?,?,?,?)",
            (f"{sid}-m{seq}", sid, "assistant", seq, created_ms, created_ms,
             json.dumps({"content": content})))
        conn.commit()
        conn.close()

    # -- session selection ------------------------------------------------
    def test_only_running_sessions_are_listed(self):
        now = int(time.time() * 1000)
        self.add_session("ses_run1", "Running one", busy_ms=now - 60_000)
        self.add_session("ses_run2", "Finished one", busy_ms=now - 60_000,
                         idle_ms=now - 30_000, outcome="succeeded")
        self.add_session("ses_run3", "Archived one", busy_ms=now - 60_000,
                         archived=now - 1000)
        self.add_session("ses_run4", "Idle one", busy_ms=now - 120_000,
                         idle_ms=now - 60_000)
        sessions = self.ns["collect_running_sessions"](now)
        titles = {s["title"] for s in sessions}
        self.assertEqual(titles, {"Running one"})

    def test_suspended_sessions_still_count_as_running(self):
        now = int(time.time() * 1000)
        self.add_session("ses_susp", "Parked but alive", busy_ms=now - 60_000)
        sessions = self.ns["collect_running_sessions"](now)
        self.assertEqual([s["title"] for s in sessions], ["Parked but alive"])

    def test_session_resumed_after_finishing_is_still_running(self):
        """A second turn must not be hidden by the previous turn's outcome.

        OpenCode records idle_outcome when a turn goes idle and leaves it in
        place when the session starts another turn, so ``succeeded`` survives
        into the new turn. Gating on it hid every resumed session from the
        briefing, which is what made sessions go missing.
        """
        now = int(time.time() * 1000)
        self.add_session("ses_resumed", "Resumed after success",
                         busy_ms=now - 30_000, idle_ms=now - 120_000,
                         outcome="succeeded")
        sessions = self.ns["collect_running_sessions"](now)
        self.assertEqual([s["title"] for s in sessions],
                         ["Resumed after success"])

    def test_interrupted_session_that_started_again_is_still_running(self):
        now = int(time.time() * 1000)
        self.add_session("ses_retry", "Retried after interruption",
                         busy_ms=now - 20_000, idle_ms=now - 90_000,
                         outcome="interrupted")
        sessions = self.ns["collect_running_sessions"](now)
        self.assertEqual([s["title"] for s in sessions],
                         ["Retried after interruption"])

    def test_completed_and_quiet_session_is_still_excluded(self):
        """Dropping the outcome gate must not resurrect finished sessions."""
        now = int(time.time() * 1000)
        self.add_session("ses_done", "Finished and idle",
                         busy_ms=now - 600_000, idle_ms=now - 300_000,
                         outcome="succeeded")
        sessions = self.ns["collect_running_sessions"](now)
        self.assertEqual(sessions, [])

    # -- activity counting ------------------------------------------------
    def test_tool_calls_are_counted_from_message_payloads(self):
        now = int(time.time() * 1000)
        self.add_message("ses_a", 1, now - 30_000, text="hello there", tools=3)
        activity = self.ns["session_activity_since"]("ses_a", now - 60_000)
        self.assertEqual(activity["new_messages"], 1)
        self.assertEqual(activity["new_tool_calls"], 3)
        self.assertIn("hello there", activity["latest_text"])

    def test_activity_before_the_window_is_ignored(self):
        now = int(time.time() * 1000)
        self.add_message("ses_a", 1, now - 3_600_000, text="old news", tools=2)
        activity = self.ns["session_activity_since"]("ses_a", now - 60_000)
        self.assertEqual(activity["new_messages"], 0)
        self.assertEqual(activity["new_tool_calls"], 0)

    # -- briefing text ----------------------------------------------------
    def _fresh(self, **over):
        item = {
            "id": "ses_x", "title": "Test session", "runtime_s": 600.0,
            "new_messages": 5, "new_tool_calls": 2,
            "latest_text": "The build finished and the tests passed.",
            "busy_ms": 1,
        }
        item.update(over)
        return item

    def test_briefing_names_active_sessions(self):
        text = self.ns["build_progress_briefing"]([self._fresh()], 3, 600)
        self.assertIn("Test session", text)
        self.assertIn("3 agent sessions running", text)
        self.assertIn("1 with new activity", text)
        self.assertIn("End of briefing", text)

    def test_counts_are_omitted_by_default(self):
        text = self.ns["build_progress_briefing"]([self._fresh()], 1, 600)
        self.assertNotIn("0 tool calls", text)

    def test_quiet_briefing_says_nothing_happened(self):
        text = self.ns["build_progress_briefing"]([], 2, 600)
        self.assertIn("No new activity", text)
        self.assertIn("2 agent sessions running", text)

    def test_briefing_is_short_enough_for_a_ten_minute_cadence(self):
        fresh = [self._fresh(id=f"ses_{i}", title=f"Session number {i}",
                             busy_ms=i) for i in range(8)]
        text = self.ns["build_progress_briefing"](fresh, 8, 600)
        words = len(text.split())
        # Roughly 2.6 words per second in the Hawking voice; keep it under a
        # minute so a briefing never overlaps the next one.
        self.assertLess(words, 160, f"briefing is too long: {words} words")

    def test_truncation_notice_is_not_quoted(self):
        item = self._fresh(latest_text="A long sentence that will be cut off here " * 20)
        text = self.ns["build_progress_briefing"]([item], 1, 600)
        self.assertNotIn("Audio transcript truncated", text)

    # -- the monitor ------------------------------------------------------
    def test_quiet_window_stays_silent_and_does_not_speak(self):
        now = int(time.time() * 1000)
        self.add_session("ses_q", "Quiet session", busy_ms=now - 60_000)
        state = {"last_window_end_ms": now - 600_000, "seen": {}, "total": 0}
        self.ns["_save_progress_state"](state)
        spoken = []
        self.ns["enqueue_system"] = lambda entry: spoken.append(entry)
        result = self.ns["announce_session_progress"]()
        self.assertIsNone(result)
        self.assertEqual(spoken, [], "a quiet window must not be spoken")

    def test_activity_is_spoken_once(self):
        now = int(time.time() * 1000)
        self.add_session("ses_act", "Busy session", busy_ms=now - 60_000)
        self.add_message("ses_act", 1, now - 30_000, text="making progress", tools=1)
        self.ns["_save_progress_state"](
            {"last_window_end_ms": now - 600_000, "seen": {}, "total": 0})
        spoken = []
        self.ns["enqueue_system"] = lambda entry: spoken.append(entry)
        first = self.ns["announce_session_progress"]()
        self.assertIsNotNone(first)
        self.assertIn("Busy session", first)
        self.assertEqual(len(spoken), 1)
        # A second poll in the same window must not repeat it.
        second = self.ns["announce_session_progress"]()
        self.assertIsNone(second)

    def test_forced_run_reports_even_without_activity(self):
        now = int(time.time() * 1000)
        self.add_session("ses_f", "Idle-ish session", busy_ms=now - 60_000)
        self.ns["_save_progress_state"](
            {"last_window_end_ms": now, "seen": {}, "total": 0})
        text = self.ns["announce_session_progress"](force=True, queue=False)
        self.assertIsNotNone(text)
        self.assertIn("No new activity", text)

    # -- wiring -----------------------------------------------------------
    def test_progress_is_a_notice_tag(self):
        self.assertIn("progress", self.ns["NOTICE_TAGS"])

    def test_progress_is_a_scheduled_notice_tag(self):
        self.assertIn("progress", self.ns["SCHEDULED_NOTICE_TAGS"])

    def test_error_storm_does_not_suppress_a_scheduled_briefing(self):
        """A full error backlog must not swallow a due briefing.

        SYSLOG_QUEUE_LIMIT is a budget for system errors. It used to be counted
        across every notice tag, so on a noisy machine a progress briefing was
        dropped even though it was due and the queue had room for it.
        """
        import queue as queue_mod
        ns = self.ns
        old_jobs, old_limit = ns["JOBS"], ns["SYSLOG_QUEUE_LIMIT"]
        jobs = queue_mod.Queue(maxsize=old_jobs.maxsize)
        ns["JOBS"] = jobs
        ns["SYSLOG_QUEUE_LIMIT"] = 2
        try:
            for i in range(2):
                jobs.put(("system", {"kind": "system", "message": "boom %d" % i}))
            jobs.unfinished_tasks = 2

            ns["enqueue_system"]({
                "kind": "progress",
                "message": "OpenCode progress briefing",
                "intro": "Session progress",
                "title": "session progress",
                "source": "progress",
                "label": "SESSION PROGRESS",
            })
            queued = [job[0] for job in list(jobs.queue)]
            self.assertIn("progress", queued,
                          "a due briefing must survive a full error backlog")
        finally:
            ns["JOBS"] = old_jobs
            ns["SYSLOG_QUEUE_LIMIT"] = old_limit

    def test_error_backlog_still_suppresses_another_error(self):
        import queue as queue_mod
        ns = self.ns
        old_jobs, old_limit = ns["JOBS"], ns["SYSLOG_QUEUE_LIMIT"]
        jobs = queue_mod.Queue(maxsize=old_jobs.maxsize)
        ns["JOBS"] = jobs
        ns["SYSLOG_QUEUE_LIMIT"] = 1
        try:
            jobs.put(("system", {"kind": "system", "message": "boom"}))
            jobs.unfinished_tasks = 1
            ns["enqueue_system"]({"kind": "system", "message": "second boom"})
            self.assertEqual([job[0] for job in list(jobs.queue)], ["system"],
                             "the error budget must still cap error backlog")
        finally:
            ns["JOBS"] = old_jobs
            ns["SYSLOG_QUEUE_LIMIT"] = old_limit

    def test_job_is_routed_to_the_progress_handler(self):
        text = RESPONDER.read_text(encoding="utf-8")
        self.assertIn('if job[0] == "progress":', text)
        self.assertIn("handle_progress_job", text)

    def test_monitor_thread_is_started(self):
        text = RESPONDER.read_text(encoding="utf-8")
        self.assertIn("session_progress_monitor", text)
        self.assertIn('name="session-progress"', text)

    def test_cli_exposes_the_progress_command(self):
        text = RESPONDER.read_text(encoding="utf-8")
        self.assertIn("progress|session-progress|briefing)", text)

    def test_default_interval_matches_the_reporter_timer(self):
        self.assertEqual(self.ns["PROGRESS_INTERVAL"], 600)

    def test_script_parses(self):
        result = subprocess.run(["bash", "-n", str(RESPONDER)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class TestMenuIntegration(unittest.TestCase):
    def test_responder_menu_offers_the_progress_announcement(self):
        menu = Path(__file__).resolve().parent.parent / "scripts" / "manage_mp_responder.sh"
        text = menu.read_text(encoding="utf-8")
        self.assertIn("Announce Session Progress Now", text)
        self.assertIn('progress) bash "$RESPONDER" progress', text)

    def test_responder_menu_parses(self):
        menu = Path(__file__).resolve().parent.parent / "scripts" / "manage_mp_responder.sh"
        result = subprocess.run(["bash", "-n", str(menu)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
