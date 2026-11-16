#!/usr/bin/env python3
# ==============================================================================
# mp_harmony_tts.py — one place that speaks through the MP Harmony Agent.
# ------------------------------------------------------------------------------
# MP Harmony exposes an OpenAI-compatible speech endpoint on its voice bridge
# (POST /v1/audio/speech). It renders the 11 Matrix voices (Morpheus, Agent
# Smith, Neo, Trinity, Oracle, Zion, Stephen Hawking) locally and returns a WAV.
#
# MP Reporter uses this module instead of shelling out to a separate TTS
# engine, so a single voice setting drives its spoken output.
#
# Used as a library:
#     from mp_harmony_tts import HarmonyTTS
#     tts = HarmonyTTS()
#     if tts.available():
#         ok, path, err = tts.speak_to_file("Hello", Path("/tmp/out.wav"))
#
# Used from the shell / other programs:
#     mp_harmony_tts.py --check
#     mp_harmony_tts.py --voices
#     mp_harmony_tts.py --say "Text to speak" [--voice morpheus] [--out FILE]
#     mp_harmony_tts.py --voice          Print the configured voice
#     mp_harmony_tts.py --set-voice NAME Validate and store the voice
#
# Exit status: 0 success, 1 Harmony unreachable or synthesis failed, 2 usage.
# ==============================================================================
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

HOME = Path.home()
CONFIG_DIR = HOME / ".config" / "mix-manager"
CONFIG_FILE = CONFIG_DIR / "harmony_voice"

DEFAULT_PORT = 11435
DEFAULT_VOICE = "jenny"

# Keep this in step with VOICES_MAP in MP_Harmony_Agent.py. The ids are what the
# bridge accepts; the labels are for menus. If the agent gains a voice and this
# list is stale, --voices still reports everything the bridge advertises.
KNOWN_VOICES = {
    "jenny": "Jenny (British Piper - offline) [Default]",
    "amy": "Amy (American Piper - offline)",
    "cori": "Cori (British Piper - offline)",
    "alan": "Alan (British Piper - offline)",
    "morpheus": "Morpheus (The Matrix - Master) [needs XTTS]",
    "morpheus_construct": "Morpheus (The Construct - 'What is real?')",
    "morpheus_redpill": "Morpheus (Red Pill - 'Rabbit Hole')",
    "morpheus_dojo": "Morpheus (Combat Dojo - 'Free your mind')",
    "morpheus_system": "Morpheus (The System - 'Our enemy')",
    "agent_smith": "Agent Smith (The Matrix)",
    "neo": "Neo (The Matrix)",
    "trinity": "Trinity (The Matrix)",
    "oracle": "The Oracle (The Matrix)",
    "zion": "Zion (Councillor Hamann)",
    "stephen_hawking_matrix": "Stephen Hawking (Enters the Matrix) [offline]",
}


def bridge_url() -> str:
    """Base URL of the running Harmony voice bridge, without a trailing slash."""
    explicit = os.environ.get("HARMONY_API_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    port = os.environ.get("HARMONY_PORT", "").strip() or str(DEFAULT_PORT)
    host = os.environ.get("HARMONY_HOST", "127.0.0.1").strip() or "127.0.0.1"
    return f"http://{host}:{port}"


# Voices that are neural clones rendered by the XTTS service. The rest are
# synthesised locally, so they always work.
XTTS_VOICES = {
    "morpheus", "morpheus_construct", "morpheus_redpill", "morpheus_dojo",
    "morpheus_system", "agent_smith", "neo", "trinity", "oracle", "zion",
}


def xtts_available(timeout: float = 2.0) -> bool:
    """True when the XTTS voice-cloning service answers on its port."""
    url = os.environ.get("XTTS_URL", "http://127.0.0.1:8020").rstrip("/")
    for path in ("/health", "/"):
        try:
            with urllib.request.urlopen(f"{url}{path}", timeout=timeout) as res:
                if 200 <= res.status < 300:
                    return True
        except Exception:
            continue
    return False


def configured_voice() -> str:
    """Voice chosen by the user, or the default when nothing is stored."""
    env = os.environ.get("HARMONY_VOICE", "").strip()
    if env:
        return env
    try:
        stored = CONFIG_FILE.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    except Exception:
        pass
    return DEFAULT_VOICE


def save_voice(name: str) -> bool:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(name.strip() + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


class HarmonyTTS:
    """Thin client for the MP Harmony speech endpoint."""

    def __init__(self, base_url: Optional[str] = None, voice: Optional[str] = None,
                 timeout: float = 120.0):
        self.base_url = (base_url or bridge_url()).rstrip("/")
        self.voice = voice or configured_voice()
        self.timeout = timeout

    # -- discovery ---------------------------------------------------------
    def available(self) -> bool:
        """True when the bridge answers, meaning speech can be requested."""
        try:
            req = urllib.request.Request(f"{self.base_url}/v1/models", method="GET")
            with urllib.request.urlopen(req, timeout=3.0) as res:
                return 200 <= res.status < 300
        except Exception:
            return False

    def remote_voices(self) -> dict:
        """Voices the bridge advertises, or an empty dict when unreachable."""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/voices", method="GET")
            with urllib.request.urlopen(req, timeout=5.0) as res:
                data = json.loads(res.read().decode("utf-8"))
            if isinstance(data, dict):
                voices = data.get("voices")
                if isinstance(voices, dict):
                    return voices
            if isinstance(data, list):
                return {str(v): str(v) for v in data}
        except Exception:
            pass
        return {}

    # -- synthesis ---------------------------------------------------------
    def synthesize(self, text: str, voice: Optional[str] = None,
                   speed: float = 1.0) -> Tuple[Optional[bytes], str]:
        """Return (wav_bytes, error). Exactly one of the two is meaningful."""
        text = (text or "").strip()
        if not text:
            return None, "empty text"
        payload = json.dumps({
            "input": text,
            "voice": voice or self.voice,
            "speed": speed,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/v1/audio/speech",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                body = res.read()
                if res.status != 200:
                    return None, f"HTTP {res.status}"
                if len(body) <= 44:
                    return None, "bridge returned no audio"
                return body, ""
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            return None, f"HTTP {exc.code} {detail}".strip()
        except Exception as exc:
            return None, str(exc)

    def speak_to_file(self, text: str, out_path: Path, voice: Optional[str] = None,
                      speed: float = 1.0) -> Tuple[bool, Path, str]:
        """Render text to a WAV file. Returns (ok, path, error)."""
        wav, err = self.synthesize(text, voice=voice, speed=speed)
        if not wav:
            return False, out_path, err
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(wav)
        except Exception as exc:
            return False, out_path, f"could not write {out_path}: {exc}"
        return True, out_path, ""

    def speak_to_tempfile(self, text: str, voice: Optional[str] = None,
                          speed: float = 1.0) -> Tuple[Optional[Path], str]:
        """Render text to a temporary WAV the caller is responsible for deleting."""
        wav, err = self.synthesize(text, voice=voice, speed=speed)
        if not wav:
            return None, err
        try:
            fd, name = tempfile.mkstemp(prefix="harmony-tts-", suffix=".wav")
            with os.fdopen(fd, "wb") as handle:
                handle.write(wav)
            return Path(name), ""
        except Exception as exc:
            return None, str(exc)


# ------------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------------
def _print(text: str = "", stream=None) -> None:
    print(text, file=stream or sys.stdout)


def main(argv) -> int:
    args = list(argv[1:])
    if not args or args[0] in ("-h", "--help", "help"):
        _print(__doc__ or "")
        return 0 if args else 2

    action = args[0]
    tts = HarmonyTTS()

    if action in ("--check", "check"):
        if tts.available():
            _print(f"MP Harmony voice bridge is reachable at {tts.base_url}")
            _print(f"Voice: {tts.voice} ({KNOWN_VOICES.get(tts.voice, tts.voice)})")
            return 0
        _print(f"MP Harmony voice bridge is NOT reachable at {tts.base_url}", stream=sys.stderr)
        _print("Start it with: manage_harmony_agent.sh start", stream=sys.stderr)
        return 1

    if action in ("--voices", "voices", "list-voices"):
        voices = tts.remote_voices() or KNOWN_VOICES
        active = tts.voice
        for vid in sorted(voices):
            if voices is KNOWN_VOICES:
                label = KNOWN_VOICES[vid]
            else:
                label = str(voices[vid])
            marker = "*" if vid == active else " "
            _print(f" {marker} {vid:<26} {label}")
        return 0

    if action in ("--voice", "voice"):
        _print(tts.voice)
        return 0

    if action in ("--set-voice", "set-voice"):
        if len(args) < 2 or not args[1].strip():
            _print("usage: mp_harmony_tts.py --set-voice NAME", stream=sys.stderr)
            return 2
        name = args[1].strip()
        voices = tts.remote_voices() or KNOWN_VOICES
        if name not in voices:
            matches = [v for v in voices if name in v]
            if len(matches) == 1:
                name = matches[0]
            else:
                _print(f"Unknown voice: {name}", stream=sys.stderr)
                _print("Known voices: " + ", ".join(sorted(voices)), stream=sys.stderr)
                return 2
        if not save_voice(name):
            _print(f"Could not write {CONFIG_FILE}", stream=sys.stderr)
            return 1
        _print(f"Voice set to {name} ({voices.get(name, name)})")
        # Matrix voices are cloned by the XTTS service. Without it they fall
        # back to Stephen Hawking, which is worth saying now rather than being
        # heard as "the wrong voice" later.
        if name in XTTS_VOICES and not xtts_available():
            _print("")
            _print("Note: this voice is cloned by the XTTS service, which is off.", stream=sys.stderr)
            _print("      XTTS reserves about 4 GB of VRAM, so it is opt-in.", stream=sys.stderr)
            _print("      Until it runs, speech uses the Stephen Hawking voice.", stream=sys.stderr)
        return 0

    if action in ("--say", "say"):
        text = ""
        voice = None
        out = None
        rest = args[1:]
        i = 0
        while i < len(rest):
            token = rest[i]
            if token == "--voice" and i + 1 < len(rest):
                voice = rest[i + 1]
                i += 2
                continue
            if token == "--out" and i + 1 < len(rest):
                out = rest[i + 1]
                i += 2
                continue
            text = token if not text else f"{text} {token}"
            i += 1
        if not text:
            _print("usage: mp_harmony_tts.py --say \"text\" [--voice NAME] [--out FILE]",
                   stream=sys.stderr)
            return 2
        target = Path(out) if out else None
        if target:
            ok, path, err = tts.speak_to_file(text, target, voice=voice)
            if not ok:
                _print(f"Synthesis failed: {err}", stream=sys.stderr)
                return 1
            _print(str(path))
            return 0
        path, err = tts.speak_to_tempfile(text, voice=voice)
        if not path:
            _print(f"Synthesis failed: {err}", stream=sys.stderr)
            return 1
        _print(str(path))
        return 0

    _print(f"Unknown action: {action}", stream=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
