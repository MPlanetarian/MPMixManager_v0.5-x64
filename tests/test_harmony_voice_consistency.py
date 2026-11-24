"""The Harmony voice must never silently change who is speaking.

A Matrix voice (Morpheus, Neo, Trinity, ...) is rendered by the XTTS
voice-cloning service. When that service was down, the agent fell through to the
generic Piper narrator and spoke in a completely different (female) voice while
the rest of the system used Stephen Hawking. The audible result was two voices
at once, which is what prompted this test.

The contract now:

  * a Matrix voice that cannot be cloned falls back to another *Matrix* voice
    (Stephen Hawking), never to the generic Piper narrator,
  * the fallback is reported, so a stopped XTTS service is visible,
  * the shared voice setting is what MP Reporter reads, so every report
    speaks in the voice written there.
"""

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

HOME = Path.home()
AGENT = HOME / "OpenAI" / "MP_Harmony_Agent" / "MP_Harmony_Agent.py"
TTS_CLIENT = Path(__file__).resolve().parent.parent / "scripts" / "mp_harmony_tts.py"
VOICE_FILE = HOME / ".config" / "mix-manager" / "harmony_voice"

MATRIX_VOICES = (
    "morpheus", "morpheus_construct", "morpheus_redpill", "morpheus_dojo",
    "morpheus_system", "agent_smith", "neo", "trinity", "oracle", "zion",
)


class TestNoSilentVoiceSwitch(unittest.TestCase):
    def setUp(self):
        if not AGENT.is_file():
            self.skipTest("MP Harmony Agent is not installed")
        self.source = AGENT.read_text(encoding="utf-8")

    def test_matrix_fallback_uses_a_hawking_helper(self):
        """The Matrix branch must call the Hawking synthesiser, not Piper."""
        start = self.source.index("if req_voice.startswith(\"xtts:\") or req_voice in matrix_voices:")
        end = self.source.index("voice = get_cached_piper_voice(", start)
        branch = self.source[start:end]
        self.assertIn("_synthesize_hawking_wav", branch,
                      "a failed Matrix voice must fall back to another Matrix voice")

    def test_fallback_is_announced(self):
        start = self.source.index("if req_voice.startswith(\"xtts:\") or req_voice in matrix_voices:")
        end = self.source.index("voice = get_cached_piper_voice(", start)
        branch = self.source[start:end]
        self.assertIn("Falling back", branch,
                      "a silent fallback is what made the problem invisible")
        # XTTS is deliberately off (it reserves ~4 GB VRAM), so the message
        # explains why rather than nagging the user to start it.
        self.assertIn("4 GB", branch,
                      "the message must explain why the cloned voices are off")

    def test_hawking_helper_is_shared_by_both_paths(self):
        self.assertGreaterEqual(
            self.source.count("_synthesize_hawking_wav"), 3,
            "the helper should be defined once and used by both the explicit "
            "Hawking path and the Matrix fallback")

    def test_hawking_chain_parameters_are_intact(self):
        for token in ("en-us+m3", '"19"', "bass=g=12:f=95:w=0.6"):
            self.assertIn(token, self.source, f"Hawking voice parameter lost: {token}")

    def test_agent_still_compiles(self):
        result = subprocess.run([sys.executable, "-m", "py_compile", str(AGENT)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(AGENT.is_file(), "MP Harmony Agent is not installed")
class TestSharedVoiceSetting(unittest.TestCase):
    def test_shared_voice_file_is_jenny(self):
        """Both programs read this file, so it decides what you hear."""
        if not VOICE_FILE.is_file():
            self.skipTest("shared voice setting has not been written yet")
        value = VOICE_FILE.read_text(encoding="utf-8").strip()
        self.assertEqual(value, "jenny",
                         "the shared voice setting must stay on Jenny")

    def test_tts_client_defaults_are_consistent(self):
        text = TTS_CLIENT.read_text(encoding="utf-8")
        # The client must keep a voice list that matches the agent's.
        for voice in MATRIX_VOICES:
            self.assertIn(voice, text, f"{voice} missing from the client voice map")

    def test_default_voice_is_the_locally_synthesised_one(self):
        """The default must never be a voice that needs a service to work."""
        text = TTS_CLIENT.read_text(encoding="utf-8")
        match = re.search(r'^DEFAULT_VOICE\s*=\s*"([^"]+)"', text, re.M)
        self.assertIsNotNone(match, "DEFAULT_VOICE is not declared")
        # Jenny is a local Piper model: offline, no VRAM, no service.
        self.assertEqual(match.group(1), "jenny")
        self.assertNotIn(match.group(1), MATRIX_VOICES)

    def test_agent_default_voice_is_jenny(self):
        source = AGENT.read_text(encoding="utf-8")
        start = source.index("def get_active_harmony_voice() -> str:")
        end = source.index("\ndef ", start + 1)
        func = source[start:end]
        self.assertIn('return "jenny"', func,
                      "the agent must default to a voice it can always render")


class TestXttsStaysOff(unittest.TestCase):
    """XTTS is deliberately disabled: it reserves about 4 GB of VRAM."""

    def test_service_is_not_enabled(self):
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-enabled", "xtts-voice.service"],
                capture_output=True, text=True, timeout=10)
        except Exception:
            self.skipTest("systemctl is not available")
        # "disabled", "masked" or "not-found" all mean it will not start at boot.
        self.assertNotEqual(result.stdout.strip(), "enabled",
                            "XTTS must not start automatically; it costs ~4 GB VRAM")

    def test_nothing_auto_starts_xtts(self):
        root = Path(__file__).resolve().parent.parent
        offenders = []
        for path in list(root.glob("scripts/*.sh")) + [root / "install.sh"]:
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if re.search(r"systemctl\s+--user\s+(enable|start).*\bxtts", stripped):
                    offenders.append(f"{path.name}: {stripped[:80]}")
        self.assertEqual(offenders, [],
                         "found an automatic XTTS start; it must stay opt-in")

    def test_menus_explain_the_voices_need_xtts(self):
        root = Path(__file__).resolve().parent.parent
        for name in ("scripts/manage_harmony_agent.sh", "scripts/manage_ollama.sh"):
            text = (root / name).read_text(encoding="utf-8")
            self.assertIn("needs XTTS", text, f"{name} does not flag the cloned voices")
            self.assertIn("4 GB", text, f"{name} does not explain the VRAM cost")


if __name__ == "__main__":
    unittest.main()
