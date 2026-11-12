#!/usr/bin/env python3
"""
mp_perf_audio_report.py - Turn performance results into a spoken audio report
============================================================================

Reads the newest performance / benchmark-mode / soak JSON reports and writes a
narration script followed by a WAV audio file.

Voice selection, in order of preference:

  1. ``--voice hawking``  the Stephen Hawking clone through the local XTTS
     service (``voice-ai/xtts_server.py``), using the Hawking reference clip.
     This is the requested voice; it needs the XTTS server running.
  2. ``--engine piper``   a local neural voice (default en_GB-alan). Offline,
     fast, always available once the model file exists.
  3. ``--engine espeak``  espeak-ng, the last-resort fallback.

If the Hawking voice is requested but the XTTS service is not up, the script
says so plainly and falls back rather than producing silence. The transcript is
always written even when no audio engine is available, so the report still
reaches the reader.

Usage
-----
    ./scripts/mp_perf_audio_report.py                     # best available voice
    ./scripts/mp_perf_audio_report.py --voice hawking
    ./scripts/mp_perf_audio_report.py --engine piper --voice en_GB-alan
    ./scripts/mp_perf_audio_report.py --text-only
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
REPORT_ROOT = ROOT / "REPORTS" / "PERF"
AUDIO_ROOT = REPORT_ROOT / "audio"

VOICE_DIR = Path.home() / "voice-ai" / "voices"
HAWKING_REFERENCE = Path.home() / "Documents" / "Clone_Stephen_Hawking.wav"
XTTS_URL = "http://127.0.0.1:8020"

PIPER_CANDIDATES = [
    VOICE_DIR / "en_GB-alan-medium.onnx",
    VOICE_DIR / "en_GB-cori-medium.onnx",
    VOICE_DIR / "en_US-lessac-medium.onnx",
]


# ---------------------------------------------------------------------------
# collect the numbers to narrate
# ---------------------------------------------------------------------------
def _newest(pattern: str) -> Optional[Path]:
    if not REPORT_ROOT.is_dir():
        return None
    hits = sorted(REPORT_ROOT.rglob(pattern), key=lambda p: p.stat().st_mtime)
    return hits[-1] if hits else None


def _load(path: Optional[Path]) -> Dict[str, Any]:
    if not path:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def build_script() -> str:
    """Plain prose, because that is what reads well aloud."""
    perf = _load(_newest("perf.json"))
    bench = _load(_newest("benchmark-mode.json"))
    soak = _load(_newest("soak.json"))

    lines: List[str] = []
    lines.append("MP Mix Manager performance report.")
    lines.append(f"Generated {datetime.now().strftime('%A %d %B %Y at %H:%M')}.")

    # ---- environment of the newest perf run -----------------------------
    env = (perf.get("env") or {})
    if env:
        host = env.get("host") or "unknown host"
        system = env.get("system") or env.get("platform") or "unknown system"
        machine = env.get("machine") or ""
        lines.append(
            f"The most recent performance run was on {host}, "
            f"running {system} {machine}, with {env.get('cpu_count', 'an unknown number of')} CPU cores."
        )

    # ---- render throughput ----------------------------------------------
    results = perf.get("results") or {}
    render = ((results.get("visualizer") or {}).get("render") or {})
    if render.get("fps"):
        lines.append(
            f"The visualizer rendered at {render['fps']:.0f} frames per second, "
            f"at {render['ms_per_frame']:.1f} milliseconds per frame."
        )
        if render.get("cpu_ms_per_frame"):
            lines.append(
                f"CPU time per frame was {render['cpu_ms_per_frame']:.2f} milliseconds."
            )

    micro = results.get("_micro") or {}
    if micro.get("fft_block_us"):
        lines.append(
            f"The audio spectrum analysis, which runs on every audio block, "
            f"cost {micro['fft_block_us']:.0f} microseconds."
        )
    if micro.get("eq_block_us"):
        lines.append(
            f"The equaliser convolution cost {micro['eq_block_us']:.0f} microseconds per block."
        )

    server = ((results.get("web-companion") or {}).get("server") or {})
    if server.get("req_per_sec"):
        lines.append(
            f"The web companion served {server['req_per_sec']:.0f} requests per second, "
            f"at {server['ms_per_request']:.1f} milliseconds per request."
        )

    # ---- benchmark mode --------------------------------------------------
    summary = bench.get("summary") or {}
    if summary:
        passed = summary.get("passed", 0)
        total = summary.get("shortcuts", 0)
        lines.append(
            f"Benchmark mode loaded {passed} of {total} desktop shortcuts successfully."
        )
        if summary.get("all_ready_sec"):
            lines.append(
                f"All four shortcuts were fully loaded in {summary['all_ready_sec']:.1f} seconds."
            )
        for item in bench.get("shortcuts") or []:
            if item.get("ok") and item.get("ready_sec") is not None:
                lines.append(
                    f"{item['label']} became ready in {item['ready_sec']:.1f} seconds, "
                    f"using {item.get('rss_peak_mb', 0):.0f} megabytes of memory."
                )
            elif item.get("label"):
                lines.append(f"{item['label']} did not finish loading.")

    # ---- soak verdict ----------------------------------------------------
    s_sum = soak.get("summary") or {}
    if s_sum:
        verdict = s_sum.get("verdict", "UNKNOWN")
        lines.append(
            f"The soak test verdict was {verdict}. "
            f"{s_sum.get('shortcuts_survived', 0)} of "
            f"{s_sum.get('shortcuts_total', 0)} shortcuts survived, "
            f"and {s_sum.get('scripts_passed', 0)} of "
            f"{s_sum.get('scripts_run', 0)} parallel scripts passed, "
            f"over a soak of {s_sum.get('soak_sec', 0):.0f} seconds."
        )
        crashes = s_sum.get("crashes", 0)
        if crashes:
            lines.append(f"There were {crashes} crashes during the soak.")
        else:
            lines.append("There were no crashes during the soak.")

    if len(lines) <= 2:
        lines.append(
            "No performance results were found yet. "
            "Run the benchmark first, then generate this report again."
        )

    lines.append("End of report.")
    return " ".join(lines)


# ---------------------------------------------------------------------------
# audio engines
# ---------------------------------------------------------------------------
def _xtts_available() -> bool:
    try:
        with urllib.request.urlopen(f"{XTTS_URL}/health", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def speak_xtts(text: str, out: Path) -> tuple[bool, str]:
    """Stephen Hawking clone via the local XTTS service.

    The service loads voice latents by name from ``ref_voices/``, so the
    Hawking clip has to have been registered once with /compute_latents. The
    health endpoint lists what is available, which is what decides whether this
    path can run.
    """
    try:
        with urllib.request.urlopen(f"{XTTS_URL}/health", timeout=5) as resp:
            health = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return False, f"XTTS service not reachable at {XTTS_URL} ({type(exc).__name__})"

    available = health.get("voices") or []
    if "hawking" not in available:
        return False, (
            "XTTS is running but the 'hawking' voice is not registered "
            f"(known: {', '.join(available[:6])}…)"
        )

    payload = json.dumps({"text": text, "voice": "hawking", "speed": 1.0}).encode()
    req = urllib.request.Request(
        f"{XTTS_URL}/synthesize", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            if resp.status != 200:
                return False, f"XTTS returned {resp.status}"
            out.write_bytes(resp.read())
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    return True, "xtts (Stephen Hawking clone)"


def speak_piper(text: str, out: Path, model_name: str = "") -> tuple[bool, str]:
    exe = shutil.which("piper")
    if not exe:
        return False, "piper not installed"
    model: Optional[Path] = None
    if model_name:
        cand = VOICE_DIR / f"{model_name}.onnx"
        if cand.is_file():
            model = cand
    if model is None:
        for cand in PIPER_CANDIDATES:
            if cand.is_file():
                model = cand
                break
    if model is None:
        return False, f"no piper model in {VOICE_DIR}"
    try:
        proc = subprocess.run(
            [exe, "--model", str(model), "--output_file", str(out)],
            input=text.encode(), capture_output=True, timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        return False, err[-1] if err else "piper produced no audio"
    return True, f"piper ({model.stem})"


def speak_espeak(text: str, out: Path) -> tuple[bool, str]:
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe:
        return False, "espeak-ng not installed"
    try:
        proc = subprocess.run([exe, "-w", str(out), text],
                              capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0 or not out.is_file():
        return False, "espeak failed"
    return True, "espeak-ng"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mp_perf_audio_report.py",
        description="Generate a spoken performance report from the stored results.",
    )
    ap.add_argument("--voice", default="", help="'hawking' or a piper model name")
    ap.add_argument("--engine", choices=("auto", "xtts", "piper", "espeak"),
                    default="auto", help="force a speech engine")
    ap.add_argument("--text-only", action="store_true", help="write the transcript only")
    ap.add_argument("--out", default="", help="output WAV path")
    args = ap.parse_args(argv)

    script = build_script()
    AUDIO_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    transcript = AUDIO_ROOT / f"perf-report-{stamp}.txt"
    transcript.write_text(script, encoding="utf-8")
    print(f"transcript: {transcript}")
    print(f"words     : {len(script.split())}")

    if args.text_only:
        return 0

    out = Path(args.out).expanduser() if args.out else AUDIO_ROOT / f"perf-report-{stamp}.wav"

    attempts: List[tuple[str, Any]] = []
    want_hawking = args.voice.lower() in ("hawking", "stephen", "stephen_hawking")
    if args.engine == "xtts" or (args.engine == "auto" and want_hawking):
        attempts.append(("xtts", lambda: speak_xtts(script, out)))
    if args.engine in ("auto", "piper"):
        attempts.append(("piper", lambda: speak_piper(script, out, args.voice)))
    if args.engine in ("auto", "espeak"):
        attempts.append(("espeak", lambda: speak_espeak(script, out)))

    for name, fn in attempts:
        ok, detail = fn()
        if ok:
            print(f"voice     : {detail}")
            print(f"audio     : {out}  ({out.stat().st_size / 1024:.0f} KB)")
            return 0
        print(f"{name} unavailable: {detail}")

    print("no speech engine produced audio; the transcript is still available",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
