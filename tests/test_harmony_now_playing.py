"""A now-playing question is answered from the player, not from the model.

"What mix is playing right now?" used to take the direct-answer path, which
withholds tools. The model then said it had no real-time information while a
mix was on the desk. These tests pin the question match, the spoken answer,
and the status tool so a status request cannot fall through into playback.
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

AGENT = Path("/var/home/mplanetarian/OpenAI/MP_Harmony_Agent/MP_Harmony_Agent.py")


def _stub_harmony() -> None:
    try:
        import openai_harmony  # noqa: F401
        return
    except ImportError:
        pass
    if "openai_harmony" in sys.modules:
        return

    class _Anything:
        def __init__(self, *args, **kwargs):
            self._args = args
            self._kwargs = kwargs

        def __call__(self, *args, **kwargs):
            return self

        def __getattr__(self, name):
            return _Anything()

    class _ToolDescription(_Anything):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.name = kwargs.get("name", args[0] if args else "")
            self.description = kwargs.get("description", "")
            self.parameters = kwargs.get("parameters", {})

    module = types.ModuleType("openai_harmony")
    for name in (
        "load_harmony_encoding",
        "HarmonyEncodingName",
        "Role",
        "Author",
        "Message",
        "Conversation",
        "DeveloperContent",
        "SystemContent",
    ):
        setattr(module, name, _Anything())
    module.ToolDescription = _ToolDescription
    sys.modules["openai_harmony"] = module


def load_agent():
    if not AGENT.is_file():
        raise unittest.SkipTest("MP Harmony Agent is not installed on this machine")
    _stub_harmony()
    spec = importlib.util.spec_from_file_location("mp_harmony_agent_now_playing", AGENT)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:
        raise unittest.SkipTest(
            f"MP Harmony Agent could not be imported in this environment: {exc}"
        )
    return module


class NowPlayingQuestionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = load_agent()

    def test_the_reported_question_is_recognised(self):
        self.assertTrue(self.agent.is_now_playing_question("What mix is playing right now?"))

    def test_spoken_variants_are_recognised(self):
        for phrase in (
            "what's playing",
            "What's playing?",
            "what is playing",
            "what song is playing right now",
            "which track is playing",
            "what's the current mix",
            "what is the current mix?",
            "now playing",
            "what am I listening to",
            "what's on right now",
            "hey, what mix is playing",
            "can you tell me what's playing",
        ):
            self.assertTrue(self.agent.is_now_playing_question(phrase), phrase)

    def test_playback_commands_are_not_status_questions(self):
        for phrase in (
            "play the latest mix",
            "what is a DJ mix",
            "hello",
            "what mixes are in the archive",
            "pause",
            "what's on the menu tonight",
        ):
            self.assertFalse(self.agent.is_now_playing_question(phrase), phrase)

    def test_the_question_does_not_take_the_no_tools_path(self):
        self.assertFalse(self.agent.is_direct_question("What mix is playing right now?"))

    def test_ordinary_chat_still_skips_tools(self):
        self.assertTrue(self.agent.is_direct_question("What is a compressor?"))


class NowPlayingSpeechTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = load_agent()

    def test_humanizer_strips_the_archive_stamp(self):
        label = self.agent._humanize_mix_name(
            "MPlanetarian_-_Stream_of_Frequency_159_tranzLift_2026-10-26_12h52m42.wav"
        )
        self.assertEqual(label, "Stream of Frequency 159 tranzLift")

    def test_humanizer_matches_the_celebration_fixture(self):
        label = self.agent._humanize_mix_name(
            "MPlanetarian_-_Stream_of_Frequency_163_THE_CELEBRATION_2026-11-08_13h45m14.flac"
        )
        self.assertEqual(label, "Stream of Frequency 163 THE CELEBRATION")

    def test_playing_mix_is_spoken_with_the_clock(self):
        speech = self.agent.format_now_playing_speech({
            "running": True,
            "state": "playing",
            "title": "MPlanetarian_-_Stream_of_Frequency_159_tranzLift_2026-10-26_12h52m42.wav",
            "position_fmt": "08:46",
            "duration_fmt": "01:52:11",
            "muted": False,
        })
        self.assertEqual(
            speech,
            "Stream of Frequency 159 tranzLift is playing. "
            "You are 8 minutes and 46 seconds into a mix that runs 1 hour and 52 minutes.",
        )
        self.assertNotIn("_", speech)
        self.assertNotIn("*", speech)

    def test_paused_and_muted_are_spoken(self):
        speech = self.agent.format_now_playing_speech({
            "running": True,
            "state": "paused",
            "title": "Some_Mix.flac",
            "position_fmt": "01:00",
            "duration_fmt": "40:00",
            "muted": True,
        })
        self.assertIn("Some Mix is paused.", speech)
        self.assertIn("1 minute into a mix that runs 40 minutes.", speech)
        self.assertIn("The player is muted.", speech)

    def test_idle_player_says_nothing_is_playing(self):
        self.assertEqual(
            self.agent.format_now_playing_speech({"running": False, "state": "stopped"}),
            "Nothing is playing right now.",
        )

    def test_a_dead_player_pid_is_not_reported_as_playing(self):
        status = self.agent._desk_status({
            "running": True,
            "state": "playing",
            "title": "Ghost Mix.flac",
            "pid": 2_000_000_000,
        })
        self.assertFalse(status["running"])
        self.assertEqual(status["state"], "stopped")
        self.assertTrue(status["stale"])

    def test_status_tool_returns_speech_and_does_not_start_playback(self):
        canned = {
            "running": True,
            "state": "playing",
            "title": "Stream_of_Frequency_159.wav",
            "source": "MP Audio Player",
        }
        with patch.object(self.agent, "read_player_now_playing", return_value=dict(canned)), \
             patch.object(self.agent.subprocess, "Popen") as popen, \
             patch.object(self.agent.subprocess, "run") as run:
            result = self.agent.play_mix_or_audio(command="status")
        popen.assert_not_called()
        run.assert_not_called()
        self.assertEqual(result["command"], "status")
        self.assertIn("Stream of Frequency 159 is playing.", result["speech"])

    def test_answer_helper_skips_unrelated_questions(self):
        self.assertIsNone(self.agent.answer_now_playing("What is a compressor?"))


if __name__ == "__main__":
    unittest.main()
