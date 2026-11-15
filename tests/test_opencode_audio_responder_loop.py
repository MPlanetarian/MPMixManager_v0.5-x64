"""Regression tests for the MP OpenCode Audio Responder scan loop.

The responder used to repeat one announcement forever. The cause was a reply
that OpenCode cancelled or aborted: such a row can carry a completion stamp
without ever gaining a finish reason. The scanner treated it as unfinished,
kept it in ``pending``, and pinned the session watermark at that row, so every
poll re-read the same slice of the session and re-narrated the same prompt.

These tests extract the embedded Python engine from the shell script and drive
its real helpers (``_pending_entry``, ``note_spoken``, ``migrate_state``,
``heal_stuck_pending``) the same way the main loop does. The scenarios mirror
the bug exactly:

  * a cancelled turn must not pin the watermark;
  * the next prompt in that session must still be narrated;
  * a reply that is genuinely still streaming must still be waited for;
  * nothing may be announced twice across repeated polls;
  * the persisted state must stay small and the spoken ledger must be pruned.
"""
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

RESPONDER = Path(
    os.environ.get("MP_OPENCODE_RESPONDER")
    or (Path.home() / "Documents" / "BASH_SCRIPTS" / "MP_OpenCode_Audio_Responder.sh")
)

SCHEMA = """
CREATE TABLE session_v2 (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, workspace_id TEXT,
    parent_id TEXT, fork_session_id TEXT, fork_boundary TEXT,
    slug TEXT NOT NULL, directory TEXT NOT NULL, path TEXT, title TEXT,
    version TEXT NOT NULL, share_url TEXT, summary_additions INTEGER,
    summary_deletions INTEGER, summary_files INTEGER, summary_diffs TEXT,
    metadata TEXT, cost REAL DEFAULT 0 NOT NULL,
    tokens_input INTEGER DEFAULT 0 NOT NULL,
    tokens_output INTEGER DEFAULT 0 NOT NULL,
    tokens_reasoning INTEGER DEFAULT 0 NOT NULL,
    tokens_cache_read INTEGER DEFAULT 0 NOT NULL,
    tokens_cache_write INTEGER DEFAULT 0 NOT NULL,
    revert TEXT, permission TEXT, agent TEXT, model TEXT,
    time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL,
    time_compacting INTEGER, time_archived INTEGER,
    time_suspended INTEGER, resume_attempts INTEGER DEFAULT 0 NOT NULL,
    time_idle INTEGER, time_viewed INTEGER, idle_outcome TEXT,
    time_busy INTEGER);
CREATE TABLE session_message (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, seq INTEGER NOT NULL,
    type TEXT NOT NULL, time_created INTEGER NOT NULL, data TEXT NOT NULL);
"""


def extract_engine():
    """Pull the embedded Python engine out of the shell script."""
    text = RESPONDER.read_text(encoding="utf-8")
    start = text.index("exec python3 -u - <<'PYEOF'")
    start = text.index("\n", start) + 1
    end = text.index("\nPYEOF", start)
    return text[start:end]


class ResponderScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not RESPONDER.is_file():
            raise unittest.SkipTest("audio responder script not installed")
        cls.tmp = Path(tempfile.mkdtemp(prefix="responder-test-"))
        cls.db = cls.tmp / "opencode.db"
        cls.state_dir = cls.tmp / "state"
        cls.state_dir.mkdir(parents=True)

        conn = sqlite3.connect(cls.db)
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO session_v2 (id,project_id,slug,directory,title,version,"
            "time_created,time_updated) VALUES (?,?,?,?,?,?,?,?)",
            ("ses_test1", "proj", "slug", "/tmp", "Test session", "1", 1, 1))
        conn.commit()
        conn.close()

        engine_path = cls.tmp / "engine.py"
        engine_path.write_text(extract_engine(), encoding="utf-8")

        os.environ.update(
            STATE_DIR=str(cls.state_dir),
            LOG_FILE=str(cls.state_dir / "responder.log"),
            LOCK_FILE=str(cls.state_dir / "lock"),
            PAUSE_FILE=str(cls.state_dir / "paused"),
            STATE_FILE=str(cls.state_dir / "state.json"),
            MP_OPENCODE_DB=str(cls.db),
            MP_OPENCODE_SCRIPT_DIR=str(Path.home() / "Documents" / "BASH_SCRIPTS"),
            MP_OPENCODE_OWN_UNIT="mp-opencode-audio-responder.service",
            MP_OPENCODE_TASK_SUMMARY="false",
            MP_OPENCODE_SYSTEM_ERRORS="false",
            MP_OPENCODE_DISK_WATCH="false",
            MP_OPENCODE_MIXLOG_WATCH="false",
            MP_OPENCODE_PLAY="false",
        )
        sys.modules.pop("responder_engine", None)
        spec = importlib.util.spec_from_file_location("responder_engine", engine_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["responder_engine"] = mod
        spec.loader.exec_module(mod)
        cls.engine = mod

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        conn = sqlite3.connect(self.db)
        conn.execute("DELETE FROM session_message")
        conn.commit()
        conn.close()
        self.state = {"sessions": {"ses_test1": {"seq": 0, "pending": {}}}}
        self.now = int(time.time() * 1000)

    # -- helpers ------------------------------------------------------------
    def add(self, mid, seq, mtype, data, ts):
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO session_message (id,session_id,seq,type,time_created,data)"
            " VALUES (?,?,?,?,?,?)",
            (mid, "ses_test1", seq, mtype, ts, json.dumps(data)))
        conn.commit()
        conn.close()

    def update(self, mid, data):
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE session_message SET data=? WHERE id=?",
                     (json.dumps(data), mid))
        conn.commit()
        conn.close()

    def scan(self):
        """One pass of the main loop's decision logic."""
        e = self.engine
        out = []
        e.enqueue = lambda kind, sid, title, text, stamp: out.append(
            (kind, sid, text[:40]))
        conn = e.connect()
        try:
            sessions = e.all_sessions(conn)
            now_ms = int(time.time() * 1000)
            cutoff = now_ms - e.REPLAY_WINDOW * 1000
            fetched = {}
            for sid in sessions:
                marks = self.state["sessions"].setdefault(sid, {})
                last = int(marks.get("seq") or 0)
                fetched[sid] = conn.execute(
                    "SELECT id, session_id, seq, type, time_created, data "
                    "FROM session_message WHERE session_id=? AND seq>? "
                    "AND type IN ('user','assistant') ORDER BY seq ASC",
                    (sid, last)).fetchall()
        finally:
            conn.close()

        rows = [r for b in fetched.values() for r in b]
        rows.sort(key=lambda r: (int(r["time_created"] or 0), int(r["seq"] or 0)))
        for row in rows:
            sid = row["session_id"]
            seq = int(row["seq"] or 0)
            ts = int(row["time_created"] or 0)
            title = sessions.get(sid, (sid[-10:], 0))[0]
            marks = self.state["sessions"].setdefault(sid, {})
            pending = marks.setdefault("pending", {})
            if e.already_spoken(marks, row["id"]):
                pending.pop(row["id"], None)
                continue
            data = json.loads(row["data"])
            if row["type"] == "user":
                pending.pop(row["id"], None)
                text = (data.get("text") or "").strip()
                if text and ts >= cutoff:
                    e.enqueue("prompt", sid, title, text, "0")
                e.note_spoken(marks, row["id"], seq)
                continue
            info = data.get("time") or {}
            finish = data.get("finish")
            if not info.get("completed"):
                pending[row["id"]] = e._pending_entry(
                    pending.get(row["id"]), seq, now_ms)
                continue
            if finish is None:
                pending.pop(row["id"], None)
                e.note_spoken(marks, row["id"], seq)
                continue
            pending.pop(row["id"], None)
            if finish == "tool-calls":
                e.note_spoken(marks, row["id"], seq)
                continue
            text = e.assistant_text(data)
            if text and ts >= cutoff:
                e.enqueue("response", sid, title, text, "0")
            e.note_spoken(marks, row["id"], seq)

        stale = e.PENDING_MAX_AGE * 1000
        for sid, (title, _m) in sessions.items():
            marks = self.state["sessions"].setdefault(sid, {})
            pending = marks.get("pending") or {}
            batch = fetched.get(sid) or []
            if pending:
                for mid, value in list(pending.items()):
                    entry = e._pending_entry(value, 0, now_ms)
                    too_old = (now_ms - entry["since"]) > stale
                    unreachable = entry["seq"] <= int(marks.get("seq") or 0)
                    if too_old or unreachable:
                        pending.pop(mid, None)
            if batch:
                wm = max(int(r["seq"] or 0) for r in batch)
                if pending:
                    wm = min(wm, min(e._pending_entry(v, 0, now_ms)["seq"] - 1
                                     for v in pending.values()))
                marks["seq"] = max(int(marks.get("seq") or 0), wm)
            if pending:
                marks["pending"] = pending
            else:
                marks.pop("pending", None)
            e.prune_spoken(marks)
        return out

    # -- the bug ------------------------------------------------------------
    def test_cancelled_turn_does_not_pin_watermark(self):
        """The exact loop: completed reply with no finish reason."""
        self.add("u1", 1, "user", {"text": "Run the test"}, self.now)
        self.add("a1", 2, "assistant",
                 {"time": {"created": self.now, "completed": self.now + 1},
                  "content": [{"type": "tool", "state": {"status": "error"}}]},
                 self.now + 1)
        self.scan()
        self.assertFalse(self.state["sessions"]["ses_test1"].get("pending"),
                         "cancelled turn must not stay pending")
        self.assertGreaterEqual(self.state["sessions"]["ses_test1"]["seq"], 2,
                                "watermark must advance past the cancelled turn")

    def test_cancelled_turn_does_not_repeat_prompt(self):
        self.add("u1", 1, "user", {"text": "Run the test"}, self.now)
        self.add("a1", 2, "assistant",
                 {"time": {"created": self.now, "completed": self.now + 1},
                  "content": [{"type": "tool", "state": {"status": "error"}}]},
                 self.now + 1)
        first = self.scan()
        self.assertEqual([k for k, _s, _t in first], ["prompt"])
        for _ in range(5):
            self.assertEqual(self.scan(), [],
                             "the same prompt must never be announced twice")

    def test_session_continues_after_cancelled_turn(self):
        self.add("u1", 1, "user", {"text": "First try"}, self.now)
        self.add("a1", 2, "assistant",
                 {"time": {"created": self.now, "completed": self.now + 1},
                  "content": [{"type": "tool"}]}, self.now + 1)
        self.scan()
        self.add("u2", 3, "user", {"text": "Second try"}, self.now + 10)
        self.add("a2", 4, "assistant",
                 {"time": {"created": self.now + 10, "completed": self.now + 12},
                  "finish": "stop",
                  "content": [{"type": "text", "text": "Done properly."}]},
                 self.now + 12)
        out = self.scan()
        self.assertIn(("prompt", "ses_test1", "Second try"), out)
        self.assertTrue(any(k == "response" for k, _s, _t in out))

    # -- must keep working --------------------------------------------------
    def test_streaming_reply_is_waited_for(self):
        self.add("u1", 1, "user", {"text": "Do work"}, self.now)
        self.add("a1", 2, "assistant",
                 {"time": {"created": self.now},
                  "content": [{"type": "text", "text": "Working"}]}, self.now)
        out = self.scan()
        self.assertEqual([k for k, _s, _t in out], ["prompt"])
        self.assertIn("a1", self.state["sessions"]["ses_test1"]["pending"])

        self.update("a1", {"time": {"created": self.now, "completed": self.now + 5},
                           "finish": "stop",
                           "content": [{"type": "text", "text": "Finished."}]})
        out = self.scan()
        self.assertEqual([k for k, _s, _t in out], ["response"])
        self.assertEqual(self.scan(), [], "response must not repeat")

    def test_completed_reply_announced_once(self):
        self.add("u1", 1, "user", {"text": "Hello"}, self.now)
        self.add("a1", 2, "assistant",
                 {"time": {"created": self.now, "completed": self.now + 2},
                  "finish": "stop",
                  "content": [{"type": "text", "text": "Hi there."}]},
                 self.now + 2)
        self.assertEqual(len(self.scan()), 2)
        self.assertEqual(self.scan(), [])

    def test_tool_call_turns_are_not_narrated(self):
        self.add("a1", 1, "assistant",
                 {"time": {"created": self.now, "completed": self.now + 1},
                  "finish": "tool-calls",
                  "content": [{"type": "text", "text": "Calling a tool."}]},
                 self.now + 1)
        self.assertEqual(self.scan(), [])
        self.assertGreaterEqual(self.state["sessions"]["ses_test1"]["seq"], 1)

    def test_state_stays_small(self):
        for i in range(1, 40):
            self.add("u%d" % i, i * 2, "user", {"text": "prompt %d" % i},
                     self.now + i)
            self.add("a%d" % i, i * 2 + 1, "assistant",
                     {"time": {"created": self.now + i,
                               "completed": self.now + i + 1},
                      "finish": "stop",
                      "content": [{"type": "text", "text": "reply %d" % i}]},
                     self.now + i + 1)
        self.scan()
        marks = self.state["sessions"]["ses_test1"]
        self.assertLessEqual(len(marks.get("spoken") or {}), 10,
                             "spoken ledger must be pruned by the watermark")
        self.assertFalse(marks.get("pending"))

    # -- migration / healing ------------------------------------------------
    def test_heal_clears_stuck_marker(self):
        self.add("u1", 1, "user", {"text": "Go"}, self.now)
        self.add("a1", 2, "assistant",
                 {"time": {"created": self.now, "completed": self.now + 1},
                  "content": [{"type": "tool"}]}, self.now + 1)
        # Simulate state written by the buggy build: marker pinned at seq 1.
        state = {"sessions": {"ses_test1": {"seq": 0, "pending": {"a1": 2}}}}
        healed = self.engine.heal_stuck_pending(state)
        self.assertEqual(healed, 1)
        self.assertFalse(state["sessions"]["ses_test1"].get("pending"))
        self.assertGreaterEqual(state["sessions"]["ses_test1"]["seq"], 2)

    def test_marker_below_watermark_is_dropped(self):
        """Residue of the bug: a marker the scan can never fetch again."""
        self.add("u1", 1, "user", {"text": "Go"}, self.now)
        self.add("a1", 2, "assistant",
                 {"time": {"created": self.now, "completed": self.now + 1},
                  "content": [{"type": "tool"}]}, self.now + 1)
        self.add("u2", 3, "user", {"text": "Next"}, self.now + 10)
        # Watermark already past the stale marker.
        self.state = {"sessions": {"ses_test1": {
            "seq": 3, "pending": {"a1": {"seq": 2, "since": self.now}}}}}
        self.scan()
        self.assertFalse(self.state["sessions"]["ses_test1"].get("pending"),
                         "an unreachable marker must be dropped")

    def test_migrate_upgrades_legacy_formats(self):
        state = {"sessions": {"ses_test1": {
            "seq": 5, "pending": {"m1": 7}, "spoken": ["m0", "m1"]}}}
        self.engine.migrate_state(state)
        marks = state["sessions"]["ses_test1"]
        self.assertIsInstance(marks["pending"]["m1"], dict)
        self.assertEqual(marks["pending"]["m1"]["seq"], 7)
        self.assertIsInstance(marks["spoken"], dict)

    def test_pending_entry_accepts_legacy_int(self):
        entry = self.engine._pending_entry(42, 0, 1000)
        self.assertEqual(entry["seq"], 42)
        self.assertEqual(entry["since"], 1000)


if __name__ == "__main__":
    unittest.main()
