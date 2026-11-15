"""Narration of Grok, Antigravity ("agy") and Codex prompts and responses.

The MP Audio Responder narrates OpenCode turns from its SQLite database. This
suite pins the same behaviour for the three agents that keep transcript files
instead, because their layouts are the part that breaks silently:

  * a first run must not recite the archive;
  * each announcement is spoken exactly once;
  * a mid-turn assistant record (tool calls still pending) is not an answer;
  * user scaffolding (``<environment_context>``, ``<system-reminder>``) is not
    read aloud, while the user's own words inside ``<user_query>`` are;
  * Grok compaction rewrites the file in place and must not replay it;
  * a missing directory, a half-written line or a directory-as-file must
    silence only that agent, never raise;
  * Codex records the same turn twice (item and message ids), so the two
    readers must not both narrate it;
  * the agent watcher owns its own state file, so it cannot roll back the
    OpenCode watermarks.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

RESPONDER_CANDIDATES = (
    Path.home() / "MP_AI_Agent_Audio_Responder-0.1-x64" / "MP_OpenCode_Audio_Responder.sh",
    Path.home() / "Documents" / "BASH_SCRIPTS" / "MP_OpenCode_Audio_Responder.sh",
)


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
    tmp = Path(tempfile.mkdtemp(prefix="agents-engine-"))
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
class OtherAgentTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_engine()
        self.tmp = Path(tempfile.mkdtemp(prefix="agents-fixture-"))
        self.grok = self.tmp / "grok"
        self.agy = self.tmp / "agy"
        self.codex = self.tmp / "codex"
        self.ns["GROK_DIR"] = self.grok
        self.ns["AGY_DIR"] = self.agy
        self.ns["CODEX_DIR"] = self.codex
        self.ns["AGENT_STATE_FILE"] = self.tmp / "other-agents.json"
        self.queued = []
        self.ns["JOBS"].put_nowait = lambda job: self.queued.append(job)
        self.state = {"agents": {}}
        # Prime up front with no files present. Tests then create fixtures, so
        # everything they write counts as new work rather than archive backlog.
        self.ns["prime_other_agents"](self.state)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixtures ---------------------------------------------------------
    def grok_session(self, sid="sess-1", title="Fix the menus", cwd="ws"):
        d = self.grok / cwd / sid
        d.mkdir(parents=True, exist_ok=True)
        (d / "summary.json").write_text(json.dumps({"generated_title": title}))
        chat = d / "chat_history.jsonl"
        chat.write_text("")
        prompts = self.grok / cwd / "prompt_history.jsonl"
        prompts.touch()
        return chat, prompts

    def agy_session(self, sid="agy-1", title="Optimize it"):
        d = self.agy / sid / ".system_generated" / "logs"
        d.mkdir(parents=True, exist_ok=True)
        path = d / "transcript.jsonl"
        path.write_text("")
        ann = self.agy.parent / "annotations"
        ann.mkdir(parents=True, exist_ok=True)
        (ann / (sid + ".pbtxt")).write_text('title:"%s"' % title)
        self._agy_annotation_dir = ann
        return path

    def codex_session(self, sid="codex-1"):
        d = self.codex / "2026" / "11" / "15"
        d.mkdir(parents=True, exist_ok=True)
        path = d / ("rollout-2026-11-15T00-00-00-%s.jsonl" % sid)
        path.write_text(json.dumps({"type": "session_meta",
                                    "payload": {"id": sid}}) + "\n")
        return path

    def append(self, path, *records):
        with open(path, "a", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")

    def scan(self):
        """Run one scan and return only the items it queued."""
        before = len(self.queued)
        self.ns["scan_other_agents"](self.state)
        return list(self.queued[before:])

    def events(self):
        """Run one scan and return (agent, kind, text) for what it announced."""
        return [(ev["agent"], ev["kind"], ev["text"])
                for _tag, ev in self.scan()]

    def all_events(self):
        """Everything announced since setUp, across every scan."""
        return [(ev["agent"], ev["kind"], ev["text"])
                for _tag, ev in self.queued]

    # -- priming ----------------------------------------------------------
    def test_first_run_does_not_recite_the_archive(self):
        chat, prompts = self.grok_session()
        self.append(chat, {"type": "assistant", "content": "an old reply"})
        self.append(prompts, {"session_id": "sess-1", "prompt": "an old prompt"})
        self.ns["_agent_state"](self.state, "grok")["files"] = {}
        self.ns["_agent_state"](self.state, "grok")["primed"] = False
        primed = self.ns["prime_other_agents"](self.state)
        self.assertGreaterEqual(primed, 2)
        self.assertEqual(self.scan(), [], "priming must place readers at the end")

    def test_work_added_after_priming_is_spoken(self):
        chat, prompts = self.grok_session()
        self.ns["prime_other_agents"](self.state)
        self.append(prompts, {"session_id": "sess-1", "prompt": "please fix it"})
        self.append(chat, {"type": "assistant", "content": "Fixed it."})
        events = self.events()
        self.assertIn(("grok", "prompt", "please fix it"), events)
        self.assertIn(("grok", "response", "Fixed it."), events)

    # -- exactly once -----------------------------------------------------
    def test_prompt_is_queued_before_its_reply(self):
        """A prompt and its answer must not arrive out of order.

        The two readers use different clocks (the prompt has a transcript
        timestamp, the reply is dated by the file's mtime), so sorting by time
        can put the answer first. Discovery order is the real order.
        """
        chat, prompts = self.grok_session()
        self.append(prompts, {"timestamp": "2026-11-15T01:00:00Z",
                              "session_id": "sess-1", "prompt": "the question"})
        self.append(chat, {"type": "assistant", "content": "the answer"})
        kinds = [kind for _agent, kind, _text in self.events()]
        self.assertEqual(kinds, ["prompt", "response"])

    def test_nothing_is_spoken_twice(self):
        chat, prompts = self.grok_session()
        self.append(prompts, {"session_id": "sess-1", "prompt": "one"})
        self.append(chat, {"type": "assistant", "content": "two"})
        first = self.scan()
        self.assertEqual(len(first), 2)
        self.assertEqual(self.scan(), [], "a second scan must stay silent")

    # -- mid-turn replies -------------------------------------------------
    def test_reply_with_tool_calls_is_not_an_answer(self):
        chat, _ = self.grok_session()
        self.append(chat, {"type": "assistant", "content": "thinking",
                           "tool_calls": [{"id": "c1", "name": "grep"}]})
        self.assertEqual(self.scan(), [], "a mid-turn record must wait")

        # The same turn completing later, with no tool calls, is the answer.
        self.append(chat, {"type": "assistant", "content": "the answer"})
        self.assertEqual(self.events(), [("grok", "response", "the answer")])

    def test_agy_mid_turn_planner_response_is_skipped(self):
        path = self.agy_session()
        self.append(path, {"step_index": 1, "type": "PLANNER_RESPONSE",
                           "status": "DONE", "content": "checking",
                           "tool_calls": [{"name": "run_command"}]})
        self.assertEqual(self.scan(), [])
        self.append(path, {"step_index": 2, "type": "PLANNER_RESPONSE",
                           "status": "DONE", "content": "all done"})
        self.assertEqual(self.events(), [("agy", "response", "all done")])

    # -- user scaffolding -------------------------------------------------
    def test_grok_user_query_is_unwrapped(self):
        _, prompts = self.grok_session()
        self.append(prompts, {"session_id": "sess-1",
                              "prompt": "Please fix the colours"})
        self.assertEqual(self.events(), [("grok", "prompt", "Please fix the colours")])

    def test_agy_user_request_is_unwrapped(self):
        path = self.agy_session()
        self.append(path, {"step_index": 0, "type": "USER_INPUT", "status": "DONE",
                           "content": "<USER_REQUEST>\ntidy the menu\n</USER_REQUEST>"})
        self.assertEqual(self.events(), [("agy", "prompt", "tidy the menu")])

    def test_user_scaffolding_is_not_read_aloud(self):
        self.assertEqual(self.ns["_strip_user_wrappers"](
            "<environment_context>\n<cwd>/tmp</cwd>\n</environment_context>"), "")
        self.assertEqual(self.ns["_strip_user_wrappers"](
            "<system-reminder>\n## Available Skills\n</system-reminder>"), "")
        self.assertEqual(self.ns["_strip_user_wrappers"](
            'This session is being continued from a previous conversation.'), "")
        self.assertEqual(self.ns["_strip_user_wrappers"](
            "<user_query>\nkeep this\n</user_query>"), "keep this")

    def test_bash_prompts_are_skipped(self):
        _, prompts = self.grok_session()
        self.append(prompts, {"session_id": "sess-1", "prompt": "ls -la",
                              "is_bash": True})
        self.assertEqual(self.scan(), [])

    # -- compaction -------------------------------------------------------
    def test_rewritten_transcript_is_not_replayed(self):
        chat, _ = self.grok_session()
        self.append(chat, {"type": "assistant", "content": "first answer"})
        self.assertEqual(len(self.scan()), 1)

        # Grok compacts: the file is replaced by a shorter summary + the same
        # final answer. Nothing may be spoken again.
        chat.write_text("")
        self.append(chat, {"type": "system", "content": "summary of the session"})
        self.append(chat, {"type": "assistant", "content": "first answer"})
        self.assertEqual(self.scan(), [], "a rewritten file must not replay")

        # Work written after the rewrite is still narrated.
        self.append(chat, {"type": "assistant", "content": "second answer"})
        self.assertEqual(self.events(), [("grok", "response", "second answer")])
        self.assertEqual(self.all_events(), [("grok", "response", "first answer"),
                                            ("grok", "response", "second answer")])

    def test_truncated_file_is_reprimed_silently(self):
        chat, _ = self.grok_session()
        self.append(chat, {"type": "assistant", "content": "before"})
        self.scan()
        chat.write_text("")                      # truncated to nothing
        self.assertEqual(self.scan(), [])

    # -- partial writes ---------------------------------------------------
    def test_half_written_line_waits_for_the_rest(self):
        chat, _ = self.grok_session()
        with open(chat, "a", encoding="utf-8") as fh:
            fh.write('{"type":"assistant","content":"complete')
        self.assertEqual(self.scan(), [], "a partial line must not be spoken")
        with open(chat, "a", encoding="utf-8") as fh:
            fh.write('d answer"}\n')
        self.assertEqual(self.events(), [("grok", "response", "completed answer")])

    def test_malformed_and_empty_records_are_ignored(self):
        chat, prompts = self.grok_session()
        with open(chat, "a", encoding="utf-8") as fh:
            fh.write("\xff\xfe not json\n")
            fh.write(json.dumps({"type": "assistant", "content": "   "}) + "\n")
            fh.write(json.dumps({"type": "assistant", "content": "real"}) + "\n")
        self.assertEqual(self.events(), [("grok", "response", "real")])

    # -- codex ------------------------------------------------------------
    def test_codex_item_events_are_spoken(self):
        path = self.codex_session()
        self.append(path, {"type": "event_msg", "payload": {
            "type": "item_completed", "item": {
                "type": "UserMessage", "id": "u1",
                "content": [{"type": "text", "text": "run the tests"}]}}})
        self.append(path, {"type": "event_msg", "payload": {
            "type": "item_completed", "item": {
                "type": "AgentMessage", "id": "a1",
                "content": [{"type": "Text", "text": "All green."}]}}})
        self.assertEqual(self.events(), [("codex", "prompt", "run the tests"),
                                         ("codex", "response", "All green.")])

    def test_codex_dual_recording_is_not_narrated_twice(self):
        """The same turn is written as both an item event and a raw message."""
        path = self.codex_session()
        self.append(path,
                    {"type": "event_msg", "payload": {
                        "type": "item_completed", "item": {
                            "type": "UserMessage", "id": "u1",
                            "content": [{"type": "text", "text": "hello"}]}}},
                    {"type": "response_item", "payload": {
                        "type": "message", "role": "user", "id": "m1",
                        "content": [{"type": "input_text", "text": "hello"}]}})
        self.assertEqual(self.events(), [("codex", "prompt", "hello")])

    def test_codex_fallback_reads_raw_messages(self):
        path = self.codex_session()
        self.append(path,
                    {"type": "response_item", "payload": {
                        "type": "message", "role": "user", "id": "m1",
                        "content": [{"type": "input_text",
                                     "text": "<environment_context>\n<x/>\n"
                                             "</environment_context>"}]}},
                    {"type": "response_item", "payload": {
                        "type": "message", "role": "user", "id": "m2",
                        "content": [{"type": "input_text",
                                     "text": "real prompt"}]}},
                    {"type": "response_item", "payload": {
                        "type": "message", "role": "assistant", "id": "m3",
                        "status": "completed",
                        "content": [{"type": "output_text",
                                     "text": "real reply"}]}})
        self.assertEqual(self.events(), [("codex", "prompt", "real prompt"),
                                         ("codex", "response", "real reply")])

    # -- isolation --------------------------------------------------------
    def test_missing_directories_are_silent(self):
        self.ns["GROK_DIR"] = self.tmp / "absent-grok"
        self.ns["AGY_DIR"] = self.tmp / "absent-agy"
        self.ns["CODEX_DIR"] = self.tmp / "absent-codex"
        self.assertEqual(self.scan(), [])

    def test_one_broken_agent_does_not_silence_the_others(self):
        chat, prompts = self.grok_session()
        # Agy points at a directory where a file is expected.
        bad = self.agy / "broken" / ".system_generated" / "logs"
        bad.mkdir(parents=True)
        (bad / "transcript.jsonl").mkdir()
        self.append(prompts, {"session_id": "sess-1", "prompt": "still works"})
        self.assertIn(("grok", "prompt", "still works"), self.events())

    # -- state ------------------------------------------------------------
    def test_agent_state_is_its_own_file(self):
        """The watcher must never write the OpenCode state.json."""
        self.assertNotEqual(self.ns["AGENT_STATE_FILE"], self.ns["STATE_FILE"])
        self.assertTrue(str(self.ns["AGENT_STATE_FILE"]).endswith("other-agents.json"))

    def test_state_round_trips(self):
        chat, _ = self.grok_session()
        self.append(chat, {"type": "assistant", "content": "remember me"})
        self.scan()
        self.ns["save_agent_state"](self.state)
        reloaded = self.ns["load_agent_state"]()
        marks = reloaded["agents"]["grok"]
        self.assertTrue(marks.get("primed"))
        self.assertTrue(marks.get("files"))
        self.assertTrue(marks.get("seen"))

    def test_damaged_state_file_is_tolerated(self):
        self.ns["AGENT_STATE_FILE"].write_text("{not json")
        self.assertEqual(self.ns["load_agent_state"](), {"agents": {}})

    # -- routing ----------------------------------------------------------
    def test_agent_job_is_routed_to_its_handler(self):
        text = RESPONDER.read_text(encoding="utf-8")
        self.assertIn('if job[0] == "AGENT_MSG":', text)
        self.assertIn("handle_agent_job", text)

    def test_monitor_thread_is_started(self):
        text = RESPONDER.read_text(encoding="utf-8")
        self.assertIn("other_agents_monitor", text)
        self.assertIn('name="other-agents"', text)

    def test_scanner_does_not_use_the_opencode_state_file(self):
        text = RESPONDER.read_text(encoding="utf-8")
        block = text[text.index("def other_agents_monitor():"):]
        block = block[:block.index("\ndef ", 10)]
        self.assertNotIn("load_state()", block)
        self.assertNotIn("save_state(", block)

    def test_script_parses(self):
        result = subprocess.run(["bash", "-n", str(RESPONDER)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(RESPONDER.is_file(), "audio responder is not installed")
class AgentNarrationWording(unittest.TestCase):
    """The spoken heading must name the agent, so the listener knows who spoke."""

    @classmethod
    def setUpClass(cls):
        cls.ns = load_engine()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agents-tts-"))
        self.spoken = []
        self.ns["synthesise"] = self._fake_synth
        self.ns["play_audio"] = lambda wav: None
        self.ns["mp_reporter_archive_dir"] = lambda subdir=None: None
        self.ns["reporter_log"] = lambda level, msg: None
        self.ns["audio_duration"] = lambda wav: 1.0

    def _fake_synth(self, text, tts_dir, stem):
        self.spoken.append((text, stem))
        out = self.tmp / (stem + ".wav")
        out.write_bytes(b"RIFF")
        return out, None

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _entry(self, agent, kind, text, title="Tidy the menus", sid="abcdef123456"):
        return {"agent": agent, "kind": kind, "sid": sid, "title": title,
                "text": text, "key": "k", "order": 0}

    def test_prompt_names_the_agent_and_session(self):
        self.ns["handle_agent_job"](self._entry("grok", "prompt", "make it blue"),
                                    self.tmp)
        text, stem = self.spoken[0]
        self.assertTrue(text.startswith("Grok prompt, Tidy the menus."), text)
        self.assertIn("make it blue", text)
        self.assertTrue(stem.startswith("GROK_PROMPT_"), stem)

    def test_response_names_the_agent(self):
        self.ns["handle_agent_job"](self._entry("codex", "response", "Done."),
                                    self.tmp)
        text, stem = self.spoken[0]
        self.assertTrue(text.startswith("Codex response, Tidy the menus."), text)
        self.assertTrue(stem.startswith("CODEX_RESPONSE_"), stem)

    def test_antigravity_is_spoken_in_full(self):
        """The CLI is called agy; the spoken name must stay intelligible."""
        self.ns["handle_agent_job"](self._entry("agy", "prompt", "optimise it"),
                                    self.tmp)
        text, stem = self.spoken[0]
        self.assertTrue(text.startswith("Antigravity prompt,"), text)
        self.assertTrue(stem.startswith("AGY_PROMPT_"), stem)

    def test_empty_text_produces_no_audio(self):
        self.ns["handle_agent_job"](self._entry("grok", "prompt", "  "), self.tmp)
        self.assertEqual(self.spoken, [])

    def test_agent_job_is_not_a_notice_tag(self):
        """These are long narrations: they must not jump the queue."""
        self.assertNotIn("AGENT_MSG", self.ns["NOTICE_TAGS"])


if __name__ == "__main__":
    unittest.main()
