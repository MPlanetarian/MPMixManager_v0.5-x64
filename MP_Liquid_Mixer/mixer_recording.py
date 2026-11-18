#!/usr/bin/env python3
"""Master recording for MP Liquid Mixer 0.0.1.

The two deck engines each have their own recorder for their own output, but the
mixer needs one file containing what the room hears. That is the default
output's monitor source, captured with the same WAV format and output folder
the MP Audio Player uses (`MP_AUDIOWAV_REC_OUTPUT`, PCM 16-bit stereo).

`parec --file-format=wav` writes a proper RIFF header and finalises it when it
receives SIGINT, so stopping the recorder produces a playable file even if the
app was closed normally.
"""
from __future__ import annotations

import datetime
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

MIXER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MIXER_DIR.parent
CONFIG_DIR = Path.home() / ".config" / "mix-manager"
SUBDIR = "MP_AUDIOWAV_REC_OUTPUT"
SAMPLE_RATE = 48000
CHANNELS = 2


def _config_value(key: str, path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def recording_dir(base: Optional[Path] = None, config_dir: Optional[Path] = None) -> Path:
    """The player's recording folder: mix archive if configured, else the tree."""
    base = Path(base or PROJECT_ROOT)
    config_dir = Path(config_dir or CONFIG_DIR)
    mix_arc = os.environ.get("MIX_ARCHIVE_DIR") or _config_value("MIX_ARCHIVE_DIR", base / "config.env")
    if not mix_arc:
        mix_arc = _config_value("MIX_ARCHIVE_DIR", config_dir / "config.env")
    candidates = []
    if mix_arc and Path(mix_arc).is_dir():
        candidates.append(Path(mix_arc) / SUBDIR)
    candidates.extend([base / SUBDIR, config_dir / SUBDIR])
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate.resolve()
        except OSError:
            continue
    fallback = base / SUBDIR
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def recording_name(when: Optional[datetime.datetime] = None) -> str:
    stamp = (when or datetime.datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"MP_LIQUID_MIXER_REC_{stamp}.wav"


def recorder_command(path: Path, device: str = "@DEFAULT_MONITOR@", parec: str = "parec") -> list:
    return [
        parec,
        f"--device={device}",
        f"--rate={SAMPLE_RATE}",
        f"--channels={CHANNELS}",
        "--format=s16le",
        "--file-format=wav",
        str(path),
    ]


class MasterRecorder:
    """Start/stop the monitor capture and report its state to the UI."""

    def __init__(self, base: Optional[Path] = None) -> None:
        self.base = Path(base or PROJECT_ROOT)
        self.proc: Optional[subprocess.Popen] = None
        self.path: Optional[Path] = None
        self.started_at = 0.0
        self.error = ""
        self.last_saved: Optional[Path] = None

    @property
    def is_recording(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def elapsed(self) -> float:
        return max(0.0, time.time() - self.started_at) if self.started_at else 0.0

    def elapsed_label(self) -> str:
        total = int(self.elapsed())
        return f"{total // 60:02d}:{total % 60:02d}"

    def start(self) -> Optional[Path]:
        if self.is_recording:
            return self.path
        parec = shutil_which("parec")
        if not parec:
            self.error = "parec is not installed"
            return None
        out_dir = recording_dir(self.base)
        path = out_dir / recording_name()
        log = out_dir / "liquid_mixer_recording.log"
        try:
            with open(log, "a", encoding="utf-8") as handle:
                handle.write(f"\n--- start {path.name} ---\n")
                handle.flush()
                self.proc = subprocess.Popen(
                    recorder_command(path, parec=parec),
                    stdin=subprocess.DEVNULL, stdout=handle, stderr=handle,
                    start_new_session=True,
                )
        except Exception as exc:
            self.error = str(exc)
            self.proc = None
            return None
        self.path = path
        self.started_at = time.time()
        self.error = ""
        return path

    def stop(self) -> Optional[Path]:
        proc, path = self.proc, self.path
        if proc is None:
            return None
        try:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass
        self.proc = None
        self.started_at = 0.0
        if path is not None:
            try:
                if path.is_file() and path.stat().st_size > 44:
                    self.last_saved = path
                    return path
            except OSError:
                pass
        return None

    def tick(self) -> None:
        """Notice a recorder that exited on its own (unplugged device, error)."""
        if self.proc is not None and self.proc.poll() is not None:
            self.error = "recorder stopped unexpectedly"
            self.proc = None
            self.started_at = 0.0


def shutil_which(name: str) -> Optional[str]:
    from shutil import which
    return which(name)
