"""Tests for the MP Assistant tab in the MP Web Companion.

The companion serves the MP Liquid Player dashboard. This feature adds a top
tab bar so the same page can host the MP Harmony Agent, and the dashboard
proxies the agent's streaming chat plus its speech endpoint so a phone on the
LAN can talk to the loopback-only agent.

Behaviour covered here:

  * the tab bar and both panels exist, with the player selected by default
  * switching tabs only hides a panel, so an engaged stream keeps playing
  * replies are proxied token-by-token as SSE and speech comes back as WAV
  * the default voice is Stephen Hawking; ``/voice`` changes it
  * a local ``/voice`` reply never reaches the model
"""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from scripts import mix_web_companion as companion


class AssistantTestBase(unittest.TestCase):
    """Redirect persisted state into a temporary home."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.config = self.home / ".config" / "mix-manager"
        self.voice_file = self.home / ".harmony_voice"

        self._patches = [
            patch.object(companion, "CONFIG_DIR", self.config),
            patch.object(companion, "WEB_ASSISTANT_VOICE_FILE", self.config / "web_assistant_voice.json"),
            patch.object(companion, "HARMONY_VOICE_FILE", self.voice_file),
        ]
        for p in self._patches:
            p.start()
        companion._HARMONY_STATUS_CACHE.update({"at": 0.0, "payload": None})

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        companion._HARMONY_STATUS_CACHE.update({"at": 0.0, "payload": None})
        self._tmp.cleanup()


class TestAssistantPage(AssistantTestBase):
    """The rendered dashboard contains the tab bar and the assistant panel."""

    def setUp(self):
        super().setUp()
        self.page = companion.render_html_page(companion.THEMES["cosmic_supernova"])

    def test_the_tab_bar_has_both_players(self):
        self.assertIn('class="app-tabs"', self.page)
        self.assertIn('id="tabPlayer"', self.page)
        self.assertIn('id="tabAssistant"', self.page)
        self.assertIn("MP Liquid Player", self.page)
        self.assertIn("MP Assistant", self.page)

    def test_the_player_tab_is_selected_first(self):
        start = self.page.index('id="tabPlayer"')
        opening = self.page.rfind("<button", 0, start)
        player = self.page[opening : start]
        self.assertIn("active", player)
        tag = self.page[start : self.page.index(">", start)]
        self.assertIn('aria-selected="true"', tag)
        self.assertIn('id="panelPlayer" role="tabpanel"', self.page)

    def test_the_assistant_panel_starts_hidden(self):
        panel = self.page[self.page.index('id="panelAssistant"') :]
        panel = panel[: panel.index(">") + 1]
        self.assertIn("hidden", panel)

    def test_switching_tabs_only_toggles_visibility(self):
        """A page reload would drop the stream, so tabs must not navigate."""
        body = self.page[self.page.index("function switchAppTab") :]
        body = body[: body.index("// MP Assistant:")]
        self.assertIn("playerPanel.hidden", body)
        self.assertIn("assistantPanel.hidden", body)
        self.assertNotIn("location.reload", body)
        self.assertNotIn("location.href", body)

    def test_the_choice_survives_a_reload(self):
        self.assertIn("mp-companion-tab", self.page)
        self.assertIn("localStorage.setItem('mp-companion-tab'", self.page)

    def test_assistant_uses_the_mobile_safe_speech_element(self):
        """A fresh Audio() is blocked on iOS; the shared element is not."""
        self.assertIn('id="assistantSpeech"', self.page)
        self.assertIn("playsinline", self.page)
        self.assertIn("unlockSpeechElement", self.page)

    def test_page_has_the_voice_pill_and_slash_hint(self):
        self.assertIn('id="assistantVoicePill"', self.page)
        self.assertIn("Stephen Hawking", self.page)
        self.assertIn("/voice", self.page)
        self.assertIn("Auto-speak", self.page)


class TestVoiceSelection(AssistantTestBase):
    """/voice on the server side: parse, persist and report."""

    def test_default_is_stephen_hawking(self):
        self.assertEqual(companion.HARMONY_DEFAULT_VOICE, "stephen_hawking_matrix")

    def test_aliases_resolve_to_real_ids(self):
        self.assertEqual(companion.normalize_harmony_voice("hawking"), "stephen_hawking_matrix")
        self.assertEqual(companion.normalize_harmony_voice("stephen_hawking"), "stephen_hawking_matrix")
        self.assertEqual(companion.normalize_harmony_voice("SMITH"), "agent_smith")
        self.assertEqual(companion.normalize_harmony_voice("trinity"), "trinity")
        self.assertIsNone(companion.normalize_harmony_voice("no-such-voice"))

    def test_listing_names_every_voice_and_marks_the_active_one(self):
        with patch.object(companion, "harmony_status", return_value={"voice": "trinity"}):
            listing = companion._voice_command_reply("/voice")
        self.assertIn("stephen_hawking_matrix", listing)
        self.assertIn("● trinity", listing)
        self.assertIn("Stephen Hawking", listing)

    def test_switching_persists_the_choice_for_this_browser(self):
        with patch.object(companion, "set_harmony_voice", return_value="neo") as setter:
            reply = companion._voice_command_reply("/voice neo")
        setter.assert_called_once_with("neo")
        self.assertIn("Neo", reply)

    def test_an_unknown_voice_lists_the_presets(self):
        with patch.object(companion, "set_harmony_voice", return_value=None):
            reply = companion._voice_command_reply("/voice unicorn")
        self.assertIn("Unknown voice", reply)
        self.assertIn("available presets", reply)

    def test_an_unreachable_agent_is_not_reported_as_an_unknown_voice(self):
        with patch.object(companion, "set_harmony_voice", return_value=None):
            reply = companion._voice_command_reply("/voice neo")
        self.assertIn("not answering", reply)
        self.assertIn("Neo", reply)
        self.assertNotIn("Unknown voice", reply)

    def test_speed_command_writes_the_shared_file(self):
        reply = companion._voice_command_reply("/voice speed 1.25")
        self.assertIn("1.25", reply)
        self.assertEqual(companion.harmony_voice_speed(), 1.25)

    def test_speed_is_clamped_to_the_agent_range(self):
        self.assertEqual(companion.set_harmony_voice_speed(99), 3.0)
        self.assertEqual(companion.set_harmony_voice_speed(0.1), 0.5)

    def test_a_saved_browser_voice_wins_over_the_agent_default(self):
        companion.save_web_assistant_voice("morpheus")
        with patch.object(companion, "set_harmony_voice") as setter:
            chosen = companion.ensure_harmony_default_voice({"active_voice": "neo"})
        self.assertEqual(chosen, "morpheus")
        setter.assert_not_called()

    def test_a_cli_voice_choice_is_respected(self):
        self.voice_file.write_text("trinity\n", encoding="utf-8")
        with patch.object(companion, "set_harmony_voice") as setter:
            chosen = companion.ensure_harmony_default_voice({"active_voice": "stephen_hawking_matrix"})
        self.assertEqual(chosen, "trinity")
        setter.assert_not_called()

    def test_a_fresh_install_falls_back_to_hawking(self):
        with patch.object(companion, "set_harmony_voice", return_value="stephen_hawking_matrix") as setter:
            chosen = companion.ensure_harmony_default_voice(None)
        self.assertEqual(chosen, "stephen_hawking_matrix")
        setter.assert_called_once_with("stephen_hawking_matrix", remember=False)


class TestLocalVoiceCommand(AssistantTestBase):
    """/voice must be answered locally instead of being sent to the model."""

    def test_non_voice_prompts_are_not_intercepted(self):
        self.assertIsNone(companion._voice_command_reply("read my latest tracklist"))
        self.assertIsNone(companion._voice_command_reply(""))


class _LiveServer:
    """Tiny stdlib HTTP server around CompanionHandler for endpoint tests."""

    def __init__(self):
        import http.server

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), companion.CompanionHandler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def get(self, path, timeout=30):
        with urllib.request.urlopen(self.base + path, timeout=timeout) as response:
            return response.status, response.read()

    def post(self, path, payload, timeout=60):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as err:
            with err:
                return err.code, err.read()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class TestAssistantEndpoints(AssistantTestBase):
    def setUp(self):
        super().setUp()
        self.server = _LiveServer()

    def tearDown(self):
        self.server.close()
        super().tearDown()

    def test_dashboard_is_still_served(self):
        status, body = self.server.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"MP Assistant", body)

    def test_status_reports_the_default_voice_offline(self):
        with patch.object(companion, "harmony_agent_url", return_value="http://127.0.0.1:1"):
            status, body = self.server.get("/api/assistant/status?force=1")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertFalse(payload["available"])
        self.assertEqual(payload["voice"], "stephen_hawking_matrix")
        self.assertTrue(payload["detail"])

    def test_voices_endpoint_lists_every_preset(self):
        status, body = self.server.get("/api/assistant/voices")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        ids = {v["id"] for v in payload["voices"]}
        self.assertEqual(ids, set(companion.HARMONY_VOICES))
        self.assertEqual(payload["default"], "stephen_hawking_matrix")

    def test_voice_endpoint_switches_and_persists(self):
        with patch.object(companion, "set_harmony_voice", return_value="oracle") as setter:
            status, body = self.server.post("/api/assistant/voice", {"voice": "oracle"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["success"])
        setter.assert_called_once_with("oracle")

    def test_an_unknown_voice_is_rejected(self):
        with patch.object(companion, "set_harmony_voice", return_value=None), \
             patch.object(companion, "harmony_status", return_value={"voice": "stephen_hawking_matrix"}):
            status, body = self.server.post("/api/assistant/voice", {"voice": "nope"})
        self.assertEqual(status, 400)
        self.assertFalse(json.loads(body)["success"])

    def test_slash_voice_is_answered_without_calling_the_agent(self):
        with patch.object(companion, "harmony_status", return_value={"voice": "stephen_hawking_matrix"}), \
             patch.object(companion.CompanionHandler, "_stream_assistant_sse") as stream:
            status, body = self.server.post(
                "/api/assistant/chat",
                {"messages": [{"role": "user", "content": "/voice"}]},
            )
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(payload["local"])
        self.assertIn("voice presets", payload["content"].lower())
        stream.assert_not_called()

    def test_a_normal_prompt_is_streamed(self):
        status, body = self.server.post(
            "/api/assistant/chat",
            {"messages": [{"role": "user", "content": "hello"}]},
        )
        text = body.decode("utf-8", "replace")
        self.assertEqual(status, 200)
        self.assertIn("data: ", text)
        self.assertIn("mp.assistant", text)

    def test_speech_without_the_agent_uses_the_local_hawking_voice(self):
        speech = companion.harmony_speech("Hello from the test suite")
        # espeak-ng may not be installed on every machine; the fallback is only
        # expected to produce RIFF audio when it is.
        if companion.shutil_which("espeak-ng") or companion.shutil_which("espeak"):
            self.assertTrue(speech[:4] == b"RIFF")
        else:
            self.assertEqual(speech, b"")

    def test_speech_endpoint_reports_a_clean_failure(self):
        with patch.object(companion, "harmony_speech", return_value=b""):
            status, body = self.server.post(
                "/api/assistant/speech", {"text": "hello"}
            )
        self.assertEqual(status, 503)
        self.assertIn("Speech synthesis failed", json.loads(body)["error"])

    def test_speech_endpoint_posts_long_text_and_returns_wav(self):
        audio = b"RIFF" + b"\x00" * 200
        with patch.object(companion, "harmony_speech", return_value=audio) as synth:
            status, body = self.server.post(
                "/api/assistant/speech",
                {"text": "x" * 5000, "voice": "stephen_hawking_matrix"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(body, audio)
        self.assertEqual(synth.call_args[0][0], "x" * 5000)


class TestAssistantStart(unittest.TestCase):
    def test_start_runs_the_manager_script(self):
        with patch.object(companion.subprocess, "Popen") as popen, \
             patch.object(companion, "get_base_dir", return_value=Path("/repo")):
            script = Path("/repo/scripts/manage_harmony_agent.sh")
            with patch.object(Path, "is_file", return_value=True):
                payload = companion.start_harmony_agent()
        self.assertTrue(payload["success"])
        argv = popen.call_args[0][0]
        self.assertEqual(argv[0], "bash")
        self.assertIn("manage_harmony_agent.sh", argv[1])
        self.assertEqual(argv[2], "start")


if __name__ == "__main__":
    unittest.main()
