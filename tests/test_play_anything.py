"""MP Harmony "Play Anything" must pick one random playlist track and play it.

The playlist lives in the MP Web Companion, because that is the queue the
listener is actually streaming (Mix Archive, Music, or Favorites). The agent
therefore has to:

* find the manager install without a hard-coded v0.3 path;
* require the companion to be running *and* enabled, starting it when needed;
* refuse an empty playlist instead of inventing a track;
* hand the chosen file to MP Audio Player and confirm it really loaded.

The agent module needs ``openai_harmony``, which is not a test dependency, so
these tests load it with a stub package and drive the tools directly.
"""

import importlib.util
import json
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
AGENT = Path("/var/home/mplanetarian/OpenAI/MP_Harmony_Agent/MP_Harmony_Agent.py")


def _stub_harmony() -> None:
    """Install a minimal openai_harmony so the agent module can import."""
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
        """Keeps ``name``/``description`` readable so schemas can be asserted."""

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
    """Load the out-of-tree MP Harmony Agent, or skip.

    The agent is a separate project (~/OpenAI/MP_Harmony_Agent) and is not part
    of this repository. When it is present but cannot be imported - a different
    openai_harmony version, a missing optional module - that is an environment
    mismatch in somebody else's tree, not a failure of this one, so it skips.
    """
    if not AGENT.is_file():
        raise unittest.SkipTest("MP Harmony Agent is not installed on this machine")
    _stub_harmony()
    spec = importlib.util.spec_from_file_location("mp_harmony_agent_play", AGENT)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:
        raise unittest.SkipTest(
            f"MP Harmony Agent could not be imported in this environment: {exc}"
        )
    return module


class PlayAnythingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = load_agent()

    def setUp(self):
        self.agent = load_agent()

    # ------------------------------------------------------------------
    # Manager discovery
    # ------------------------------------------------------------------
    def test_manager_root_is_resolved_from_the_real_install(self):
        root = self.agent.get_mp_manager_root()
        self.assertTrue(root, "the manager root must be discoverable")
        self.assertTrue((Path(root) / "MP_Audio_Player.py").is_file())

    def test_env_override_wins(self):
        with patch.dict("os.environ", {"MP_MIX_MANAGER_DIR": str(ROOT)}):
            self.assertEqual(Path(self.agent.get_mp_manager_root()).resolve(), ROOT.resolve())

    def test_roots_are_deduplicated(self):
        roots = self.agent.get_mp_manager_roots()
        self.assertEqual(len(roots), len(set(roots)))

    def test_companion_url_defaults_to_loopback(self):
        with patch.dict("os.environ", {}, clear=False):
            import os

            saved = {k: os.environ.pop(k) for k in ("WEB_COMPANION_HOST",) if k in os.environ}
            try:
                url = self.agent.get_web_companion_url()
            finally:
                os.environ.update(saved)
        self.assertTrue(url.startswith("http://"))
        self.assertNotIn("0.0.0.0", url)

    # ------------------------------------------------------------------
    # Companion gate
    # ------------------------------------------------------------------
    def test_running_and_enabled_companion_passes_without_starting(self):
        with patch.object(self.agent, "_companion_json", return_value={"state": "playing"}), \
             patch.object(self.agent, "web_companion_autostart_state",
                          return_value={"enabled": True, "state": "enabled"}), \
             patch.object(self.agent.subprocess, "run") as run:
            result = self.agent.ensure_web_companion()
        self.assertTrue(result["running"])
        self.assertTrue(result["enabled"])
        self.assertFalse(result["started"])
        run.assert_not_called()

    def test_missing_companion_is_started(self):
        calls = []

        def fake_json(path, method="GET", timeout=4.0):
            if calls:
                return {"state": "playing"}
            raise OSError("connection refused")

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        with patch.object(self.agent, "_companion_json", side_effect=fake_json), \
             patch.object(self.agent, "web_companion_autostart_state",
                          return_value={"enabled": None, "state": "unknown"}), \
             patch.object(self.agent.subprocess, "run", side_effect=fake_run), \
             patch.object(self.agent.time, "sleep", return_value=None):
            result = self.agent.ensure_web_companion()
        self.assertTrue(result["running"])
        self.assertTrue(result["started"])
        self.assertTrue(any("mix_web_companion.sh" in part for part in calls[0]))

    def test_running_but_disabled_companion_is_enabled(self):
        started = {"done": False}
        actions = []

        def fake_json(path, method="GET", timeout=4.0):
            return {"state": "playing"}

        def fake_run(argv, **kwargs):
            actions.append(argv[-1])
            started["done"] = True
            return subprocess.CompletedProcess(argv, 0, "", "")

        def fake_enabled(root=""):
            return {"enabled": True} if started["done"] else {"enabled": False}

        with patch.object(self.agent, "_companion_json", side_effect=fake_json), \
             patch.object(self.agent, "web_companion_autostart_state", side_effect=fake_enabled), \
             patch.object(self.agent.subprocess, "run", side_effect=fake_run), \
             patch.object(self.agent.time, "sleep", return_value=None):
            result = self.agent.ensure_web_companion()
        self.assertIn("enable", actions)
        self.assertTrue(result["enabled"])

    def test_missing_launcher_is_reported_not_crashed(self):
        with patch.object(self.agent, "_companion_json", side_effect=OSError("down")), \
             patch.object(self.agent, "get_mp_manager_root", return_value=""), \
             patch.object(self.agent, "web_companion_autostart_state", return_value={"enabled": None}):
            result = self.agent.ensure_web_companion()
        self.assertFalse(result["running"])
        self.assertIn("mix_web_companion.sh", result["error"])

    # ------------------------------------------------------------------
    # Play Anything
    # ------------------------------------------------------------------
    def _picked(self, **overrides):
        payload = {
            "success": True,
            "status": "playing",
            "path": "/music/track.flac",
            "track": "track.flac",
            "title": "Track",
            "artist": "Artist",
            "total": 12,
            "mode": "MUSIC",
            "mode_label": "Music",
        }
        payload.update(overrides)
        return payload

    def test_empty_playlist_is_refused_before_the_player(self):
        picked = {"success": False, "status": "empty_playlist",
                  "detail": "No tracks available in the Music playlist", "total": 0}
        with patch.object(self.agent, "ensure_web_companion",
                          return_value={"running": True, "enabled": True}), \
             patch.object(self.agent, "_companion_json", return_value=picked), \
             patch.object(self.agent, "_open_track_in_mp_audio_player") as open_track:
            result = self.agent.play_anything()
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "empty_playlist")
        self.assertEqual(result["playlist_total"], 0)
        open_track.assert_not_called()

    def test_unavailable_companion_is_refused(self):
        with patch.object(self.agent, "ensure_web_companion",
                          return_value={"running": False, "error": "not installed"}):
            result = self.agent.play_anything()
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "companion_unavailable")
        self.assertIn("not installed", result["detail"])

    def test_disabled_companion_is_refused(self):
        with patch.object(self.agent, "ensure_web_companion",
                          return_value={"running": True, "enabled": False}):
            result = self.agent.play_anything()
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "companion_not_enabled")
        self.assertIn("enable", result["detail"])

    def test_successful_pick_opens_the_track_in_the_player(self):
        with patch.object(self.agent, "ensure_web_companion",
                          return_value={"running": True, "enabled": True}), \
             patch.object(self.agent, "_companion_json", return_value=self._picked()), \
             patch.object(self.agent.os.path, "isfile", return_value=True), \
             patch.object(self.agent, "_open_track_in_mp_audio_player",
                          return_value={"success": True, "daemon_reused": True}) as open_track:
            result = self.agent.play_anything()
        open_track.assert_called_once_with("/music/track.flac")
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "playing")
        self.assertEqual(result["track"], "track.flac")
        self.assertEqual(result["playlist_total"], 12)
        self.assertIn("random", result["detail"].lower())

    def test_missing_file_is_not_reported_as_playing(self):
        with patch.object(self.agent, "ensure_web_companion",
                          return_value={"running": True, "enabled": True}), \
             patch.object(self.agent, "_companion_json", return_value=self._picked()), \
             patch.object(self.agent.os.path, "isfile", return_value=False), \
             patch.object(self.agent, "_open_track_in_mp_audio_player") as open_track:
            result = self.agent.play_anything()
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "missing_file")
        open_track.assert_not_called()

    def test_player_failure_is_reported(self):
        with patch.object(self.agent, "ensure_web_companion",
                          return_value={"running": True, "enabled": True}), \
             patch.object(self.agent, "_companion_json", return_value=self._picked()), \
             patch.object(self.agent.os.path, "isfile", return_value=True), \
             patch.object(self.agent, "_open_track_in_mp_audio_player",
                          return_value={"success": False, "error": "sounddevice missing"}):
            result = self.agent.play_anything()
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "player_error")
        self.assertIn("sounddevice", result["detail"])

    def test_http_409_body_is_decoded_as_an_empty_playlist(self):
        """urllib raises on 4xx; the refusal body must still reach the caller."""
        import io
        import urllib.error

        body = json.dumps({
            "success": False,
            "status": "empty_playlist",
            "total": 0,
            "detail": "No tracks available in the Music playlist",
        }).encode()
        error = urllib.error.HTTPError(
            "http://127.0.0.1:8888/api/play-random", 409, "Conflict", {}, io.BytesIO(body)
        )
        with patch.object(self.agent.urllib.request, "urlopen", side_effect=error):
            payload = self.agent._companion_json("/api/play-random")
        self.assertFalse(payload["success"])
        self.assertEqual(payload["status"], "empty_playlist")

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def test_tool_is_registered_and_documented(self):
        self.assertIn("play_anything", self.agent.AVAILABLE_TOOLS)
        names = [t.name for t in self.agent.tool_schemas]
        self.assertIn("play_anything", names)

    def test_schema_mentions_the_companion_and_empty_playlist(self):
        schema = next(t for t in self.agent.tool_schemas if t.name == "play_anything")
        description = schema.description.lower()
        self.assertIn("random", description)
        self.assertIn("companion", description)
        self.assertIn("zero tracks", description)

    def test_slash_command_is_available(self):
        source = AGENT.read_text(encoding="utf-8")
        self.assertIn('"/play-anything"', source)
        self.assertIn('"/play-latest"', source)
        self.assertIn('quickPrompt(\'Play anything\')', source)
        self.assertIn('quickPrompt(\'Play my latest mix\')', source)

    def test_developer_instructions_name_the_tool(self):
        source = AGENT.read_text(encoding="utf-8")
        self.assertIn("call `play_anything`", source)
        self.assertIn("call `play_latest_mix`", source)


class PlayLatestMixTests(unittest.TestCase):
    """`play_latest_mix` plays the newest archive mix.

    The player owns the archive scan, so "latest" must come from its own
    `--get-latest-mix` answer rather than a second, subtly different sort.
    """

    @classmethod
    def setUpClass(cls):
        cls.agent = load_agent()

    def setUp(self):
        self.agent = load_agent()

    def test_humanizer_strips_prefix_and_timestamp(self):
        label = self.agent._humanize_mix_name(
            "MPlanetarian_-_Stream_of_Frequency_163_THE_CELEBRATION_2026-11-08_13h45m14.flac"
        )
        self.assertEqual(label, "Stream of Frequency 163 THE CELEBRATION")

    def test_humanizer_keeps_a_plain_name(self):
        self.assertEqual(self.agent._humanize_mix_name("Some_Mix.flac"), "Some Mix")

    def test_latest_comes_from_the_player(self):
        latest = self.agent._player_latest_mix()
        self.assertTrue(latest, "a latest mix must be resolvable on this host")
        self.assertTrue(Path(latest["path"]).is_file())
        self.assertEqual(latest["source"], "MP Audio Player")

    def test_live_latest_mix_is_163_the_celebration(self):
        """Guards the requested behaviour: 163 The Celebration is the newest mix."""
        latest = self.agent._player_latest_mix()
        if not latest:
            self.skipTest("no mix archive on this host")
        name = Path(latest["path"]).name
        self.assertIn("163", name)
        self.assertIn("CELEBRATION", name.upper())

    def test_plays_the_resolved_mix(self):
        with patch.object(self.agent, "_player_latest_mix",
                          return_value={"path": "/archive/163.flac", "source": "MP Audio Player"}), \
             patch.object(self.agent.os.path, "isfile", return_value=True), \
             patch.object(self.agent, "_open_track_in_mp_audio_player",
                          return_value={"success": True, "daemon_reused": True}) as open_track:
            result = self.agent.play_latest_mix()
        open_track.assert_called_once_with("/archive/163.flac")
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "playing")
        self.assertEqual(result["path"], "/archive/163.flac")

    def test_no_mix_is_refused_without_touching_the_player(self):
        with patch.object(self.agent, "_player_latest_mix", return_value={}), \
             patch.object(self.agent, "_open_track_in_mp_audio_player") as open_track:
            result = self.agent.play_latest_mix()
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "no_mix_available")
        self.assertIn("Mix Archive", result["detail"])
        open_track.assert_not_called()

    def test_player_failure_is_reported(self):
        with patch.object(self.agent, "_player_latest_mix",
                          return_value={"path": "/archive/163.flac", "source": "MP Audio Player"}), \
             patch.object(self.agent.os.path, "isfile", return_value=True), \
             patch.object(self.agent, "_open_track_in_mp_audio_player",
                          return_value={"success": False, "error": "player refused"}):
            result = self.agent.play_latest_mix()
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "player_error")
        self.assertIn("player refused", result["detail"])

    def test_cold_start_waits_for_the_daemon_to_finish_scanning(self):
        """A cold `--play` returns before the daemon can answer `--status`.

        The daemon is still walking the archive (278 files on this host), so a
        live-start probe inside that window sees running=False. Reading that as
        failure fails a start that is actually fine.
        """
        mix = "/archive/163.flac"
        # Cold: not running, then not running again, then the track is loaded.
        sequence = [
            {},
            {"running": False, "state": "stopped"},
            {"running": True, "state": "playing", "path": mix},
        ]
        captured = {"writes": []}

        def fake_status(command):
            return sequence.pop(0) if sequence else {"running": True, "state": "playing", "path": mix}

        def fake_run(argv, **kwargs):
            captured["writes"].append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        with patch.object(self.agent, "_mp_player_command", return_value=["bash", "player"]), \
             patch.object(self.agent, "get_mp_manager_root", return_value="/mgr"), \
             patch.object(self.agent.os.path, "isfile", return_value=True), \
             patch.object(self.agent, "_player_status", side_effect=fake_status), \
             patch.object(self.agent.subprocess, "run", side_effect=fake_run), \
             patch.object(self.agent.time, "sleep", return_value=None):
            result = self.agent._open_track_in_mp_audio_player(mix)

        self.assertTrue(result["success"], result)
        self.assertEqual(result["path"], mix)
        self.assertFalse(result["daemon_reused"])

    def test_a_daemon_that_never_starts_is_still_reported(self):
        with patch.object(self.agent, "_mp_player_command", return_value=["bash", "player"]), \
             patch.object(self.agent, "get_mp_manager_root", return_value="/mgr"), \
             patch.object(self.agent.os.path, "isfile", return_value=True), \
             patch.object(self.agent, "_player_status", return_value={"running": False}), \
             patch.object(self.agent.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 0, "", "")), \
             patch.object(self.agent.time, "sleep", return_value=None):
            result = self.agent._open_track_in_mp_audio_player("/archive/163.flac")
        self.assertFalse(result["success"])
        self.assertIn("did not load", result["error"])

    def test_tool_is_registered_and_documented(self):
        self.assertIn("play_latest_mix", self.agent.AVAILABLE_TOOLS)
        names = [t.name for t in self.agent.tool_schemas]
        self.assertIn("play_latest_mix", names)

    def test_schema_names_the_trigger_phrases(self):
        schema = next(t for t in self.agent.tool_schemas if t.name == "play_latest_mix")
        description = schema.description.lower()
        self.assertIn("latest", description)
        self.assertIn("newest", description)
        self.assertIn("web companion", description)


if __name__ == "__main__":
    unittest.main()
