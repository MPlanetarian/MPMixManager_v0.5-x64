"""Tests for the MP Assistant tab in the Web Companion.

The tab is a thin browser front end for the MP Harmony Agent. The agent binds
127.0.0.1:11435, which a phone cannot reach, so the companion proxies the
agent's OpenAI-compatible API on its own port. These tests pin the tab markup,
the proxy contract, and the voice default (Stephen Hawking).
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import mix_web_companion as mwc  # noqa: E402


class AssistantTabUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = mwc.render_html_page(mwc.get_theme_palette(None))

    def test_tab_bar_offers_both_sections(self):
        self.assertIn('class="tab-bar"', self.html)
        self.assertIn('id="tabBtnPlayer"', self.html)
        self.assertIn('id="tabBtnAssistant"', self.html)
        self.assertIn("MP Assistant", self.html)

    def test_both_panels_exist_and_only_one_is_initially_active(self):
        self.assertIn('id="panelPlayer"', self.html)
        self.assertIn('id="panelAssistant"', self.html)
        self.assertIn('class="tab-btn active"', self.html)
        self.assertIn('id="panelAssistant" role="tabpanel" aria-labelledby="tabBtnAssistant" hidden',
                      self.html)

    def test_switching_tabs_keeps_the_player_alive(self):
        # showTab only toggles `hidden`, so the <audio> element and the EQ poll
        # keep running while the assistant tab is open.
        self.assertIn("function showTab(name)", self.html)
        self.assertIn("playerPanel.hidden = isAssistant;", self.html)
        self.assertIn("assistantPanel.hidden = !isAssistant;", self.html)

    def test_selected_tab_is_remembered_and_deep_linkable(self):
        self.assertIn("mpCompanionTab", self.html)
        self.assertIn("location.hash", self.html)
        self.assertIn("history.replaceState", self.html)

    def test_assistant_has_a_prompt_box_and_transcript(self):
        self.assertIn('id="assistantInput"', self.html)
        self.assertIn('id="assistantLog"', self.html)
        self.assertIn('id="assistantSend"', self.html)
        self.assertIn("function sendAssistantMessage()", self.html)

    def test_enter_sends_and_shift_enter_makes_a_new_line(self):
        self.assertIn("ev.key === 'Enter' && !ev.shiftKey", self.html)

    def test_replies_are_read_from_the_sse_stream(self):
        self.assertIn("/api/assistant/chat", self.html)
        self.assertIn("res.body.getReader()", self.html)
        self.assertIn("'[DONE]'", self.html)

    def test_speech_goes_through_the_companion_origin(self):
        self.assertIn("/api/assistant/speech", self.html)
        self.assertIn('id="assistantAudio"', self.html)
        self.assertIn("function speakAssistantReply", self.html)

    def test_long_replies_are_spoken_in_sentence_chunks(self):
        # Piper is slow on long input and Safari caps URL length, so speech is
        # POSTed chunk by chunk rather than as one giant GET.
        self.assertIn("function splitForSpeech(text)", self.html)
        self.assertIn("const chunks = splitForSpeech(text);", self.html)
        self.assertIn("method: 'POST'", self.html)

    def test_mix_is_ducked_while_the_assistant_speaks(self):
        self.assertIn("duckMixForSpeech", self.html)
        self.assertIn("duckedByAssistant", self.html)

    def test_default_voice_is_stephen_hawking(self):
        self.assertIn("const DEFAULT_ASSISTANT_VOICE = 'stephen_hawking_matrix';", self.html)
        self.assertIn('value="stephen_hawking_matrix"', self.html)

    def test_new_chat_starts_a_fresh_agent_session(self):
        self.assertIn("function resetAssistantChat()", self.html)
        self.assertIn("localStorage.removeItem('mpAssistantSession')", self.html)

    def test_reply_element_is_unlocked_on_a_tap(self):
        # Phones only allow audio inside a user gesture. The model takes
        # seconds to answer, so the tap that sent the prompt has expired by
        # the time the reply is ready. A silent priming clip answers the tap
        # and marks the element as user-activated for the real reply.
        self.assertIn("function unlockAssistantAudio()", self.html)
        self.assertIn("const SILENT_WAV = 'data:audio/wav;base64,", self.html)
        self.assertIn("assistantPanel.addEventListener('pointerdown', prime);", self.html)
        self.assertIn("assistantPanel.addEventListener('touchstart', prime", self.html)

    def test_sending_a_message_primes_the_reply_element(self):
        # The send tap is the gesture that must authorise later playback.
        self.assertIn("sendAssistantMessage()", self.html)
        self.assertIn("unlockAssistantAudio();", self.html)

    def test_entering_the_tab_primes_the_reply_element(self):
        self.assertIn("function showTab(name)", self.html)
        self.assertIn("showTab(savedTab === 'assistant'", self.html)

    def test_blocked_playback_offers_a_working_retry(self):
        # A reply must never be silently text-only: if autoplay is refused,
        # a retry button is added inside the bubble and re-runs inside the tap.
        self.assertIn("const offerReplay = (retry)", self.html)
        self.assertIn("className = 'assistant-replay'", self.html)
        self.assertIn("NotAllowedError", self.html)

    def test_reply_audio_is_not_hidden_with_display_none(self):
        # iOS Safari can refuse to play an element that is display:none.
        self.assertIn('class="assistant-audio"', self.html)
        self.assertNotIn('<audio id="assistantAudio" preload="none" style="display: none;"></audio>',
                         self.html)

    def test_speaking_state_is_visible_while_the_reply_plays(self):
        self.assertIn('id="assistantSpeaking"', self.html)
        self.assertIn("badge.hidden = !isSpeaking", self.html)

    def test_stale_speech_is_cancelled_on_new_chat_or_toggle(self):
        self.assertIn("function stopAssistantSpeech()", self.html)
        self.assertIn("assistantSpeechToken += 1", self.html)
        self.assertIn("if (!speakBox.checked) stopAssistantSpeech();", self.html)


class AssistantVoiceListTest(unittest.TestCase):
    def test_stephen_hawking_is_the_default_when_offline(self):
        voices = mwc.assistant_voices()
        self.assertEqual(voices[0]["id"], "stephen_hawking_matrix")

    def test_every_voice_has_an_id_and_label(self):
        for voice in mwc.assistant_voices():
            self.assertTrue(voice["id"])
            self.assertTrue(voice["name"])


class AssistantStatusTest(unittest.TestCase):
    def _health(self, payload):
        return mock.patch.object(
            mwc, "assistant_request",
            return_value=(200, json.dumps(payload).encode("utf-8")),
        )

    def test_online_agent_reports_its_model_and_voice(self):
        with self._health({"agent": "MP Harmony AI Agent",
                           "model": "gpt-oss-pinned:latest",
                           "active_voice": "stephen_hawking_matrix",
                           "tools_count": 26}):
            status = mwc.assistant_status()
        self.assertTrue(status["available"])
        self.assertEqual(status["model"], "gpt-oss-pinned:latest")
        self.assertEqual(status["voice"], "stephen_hawking_matrix")
        self.assertEqual(status["tools_count"], 26)

    def test_unreachable_agent_is_reported_not_raised(self):
        with mock.patch.object(mwc, "assistant_request", return_value=(0, b"{}")):
            status = mwc.assistant_status()
        self.assertFalse(status["available"])
        self.assertEqual(status["status"], "offline")
        self.assertIn("11435", status["detail"])

    def test_tab_can_be_disabled_in_config(self):
        with mock.patch.dict("os.environ", {"MP_ASSISTANT_ENABLED": "false"}):
            self.assertFalse(mwc.assistant_enabled())
            status = mwc.assistant_status()
        self.assertEqual(status["status"], "disabled")


class AssistantProxyTest(unittest.TestCase):
    """The proxy is the only path from a phone to the loopback-only agent."""

    def test_health_probe_uses_the_agent_health_endpoint(self):
        with mock.patch.object(mwc, "assistant_request", return_value=(200, b"{}")) as req:
            mwc.assistant_status()
        req.assert_called_once()
        self.assertEqual(req.call_args[0][0], "/health")

    def test_default_agent_url_is_loopback(self):
        self.assertEqual(mwc.assistant_url(), "http://127.0.0.1:11435")

    def test_agent_base_url_can_be_overridden_without_a_restart(self):
        with mock.patch.dict("os.environ", {"MP_ASSISTANT_URL": "http://10.0.0.5:11435/"}):
            self.assertEqual(mwc.assistant_url(), "http://10.0.0.5:11435")
        self.assertEqual(mwc.assistant_url(), "http://127.0.0.1:11435")


if __name__ == "__main__":
    unittest.main()
