#!/usr/bin/env python3
"""Master recording for MP Liquid Mixer 0.0.1.

The two deck engines each have their own recorder for their own output, but the
mixer needs one file containing what the room hears. That is the default
output's monitor source, captured with the same WAV format and output folder
the MP Audio Player uses (`MP_AUDIOWAV_REC_OUTPUT`, PCM 16-bit stereo).

WAV has a hard ceiling: the RIFF ``ChunkSize`` and the ``data`` ``Subchunk2Size``
are 32-bit *unsigned* fields, so the largest representable file is
4,294,967,295 bytes (4 GiB - 1). The mixer therefore records PCM from
``parec --raw`` and writes the canonical 44-byte RIFF header itself, rather than
letting the capture tool choose the container. At 4 GiB - 44 bytes of audio
(the header overhead) the current file is finalised and the recording continues
in ``<name>_PART_2.wav``, then ``_PART_3.wav``, and so on. The part files are
ordinary PCM WAV files that any editor can open.

The limit is fixed at 4 GiB and is not user-configurable, as required.
"""
from __future__ import annotations

import datetime
import os
import signal
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

MIXER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MIXER_DIR.parent
CONFIG_DIR = Path.home() / ".config" / "mix-manager"
SUBDIR = "MP_AUDIOWAV_REC_OUTPUT"
SAMPLE_RATE = 48000
CHANNELS = 2
BITS_PER_SAMPLE = 16
BYTES_PER_SAMPLE = BITS_PER_SAMPLE // 8
BLOCK_ALIGN = CHANNELS * BYTES_PER_SAMPLE

# A RIFF/WAVE file is limited by its unsigned 32-bit size fields. The largest
# legal total file is 2**32 - 1 bytes; the canonical PCM header is 44 bytes, so
# the largest audio payload is that minus 44. The payload is finally rounded
# down to a whole sample frame, because a part must never end inside a frame.
WAV_HEADER_BYTES = 44
MAX_WAV_FILE_BYTES = 4 * 1024 * 1024 * 1024 - 1  # 4,294,967,295 (uint32 max)
MAX_WAV_DATA_BYTES = MAX_WAV_FILE_BYTES - WAV_HEADER_BYTES  # 4,294,967,251
MAX_PART_DATA_BYTES = (MAX_WAV_DATA_BYTES // BLOCK_ALIGN) * BLOCK_ALIGN

# FLAC has no RIFF size field, but the mixer's fixed 4 GiB part ceiling applies
# to every recording format. The encoder buffers a little input and only writes
# a finished frame at a time, so a part is rolled this far below the 4 GiB line;
# the final file therefore cannot cross it even with a buffered tail.
FLAC_PART_SAFETY_BYTES = 16 * 1024 * 1024
FLAC_PART_LIMIT_BYTES = MAX_WAV_FILE_BYTES - FLAC_PART_SAFETY_BYTES

# The mixer's one fixed limit. Kept as a named constant so the UI, the writer
# and the tests cannot disagree about where a part ends.
REC_MAX_FILE_BYTES = MAX_PART_DATA_BYTES

RECORDING_FORMATS = ("WAV", "FLAC")

_READ_CHUNK = 262144
_HEADER_REFRESH_SECONDS = 1.0


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


def recording_name(when: Optional[datetime.datetime] = None, fmt: str = "WAV") -> str:
    stamp = (when or datetime.datetime.now()).strftime("%Y%m%d_%H%M%S")
    extension = "flac" if str(fmt).upper() == "FLAC" else "wav"
    return f"MP_LIQUID_MIXER_REC_{stamp}.{extension}"


def find_flac() -> Optional[str]:
    """Locate the FLAC encoder, including Homebrew's non-login PATH.

    The mixer is started from a desktop shortcut, which does not always get
    `/home/linuxbrew/.linuxbrew/bin` or `/opt/homebrew/bin` on PATH.
    """
    found = shutil_which("flac")
    if found:
        return found
    for candidate in (
        "/usr/bin/flac",
        "/usr/local/bin/flac",
        str(Path.home() / ".linuxbrew" / "bin" / "flac"),
        "/home/linuxbrew/.linuxbrew/bin/flac",
        "/opt/homebrew/bin/flac",
    ):
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def find_ffmpeg() -> Optional[str]:
    """Locate ffmpeg, the suite's required encoder fallback."""
    found = shutil_which("ffmpeg")
    if found:
        return found
    for candidate in ("/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def recording_part_path(original: Path | str, part: int) -> Path:
    """The name of one part: the original for part 1, then ``_PART_2``, ``_PART_3``.

    ``MP_LIQUID_MIXER_REC_20261118_213005.wav`` becomes
    ``MP_LIQUID_MIXER_REC_20261118_213005_PART_2.wav``. The suffix stays the
    lower-case ``.wav`` the recorder already publishes; the ``PART_n`` marker is
    what the user sees and what identifies the continuation.
    """
    original = Path(original)
    if part <= 1:
        return original
    return original.with_name(f"{original.stem}_PART_{part}{original.suffix}")


def _unique_path(path: Path) -> Path:
    """Never overwrite an earlier recording or part with the same name."""
    if not path.exists():
        return path
    for index in range(2, 10000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path


def wav_header_bytes(data_bytes: int, sample_rate: int = SAMPLE_RATE,
                     channels: int = CHANNELS, bits: int = BITS_PER_SAMPLE) -> bytes:
    """The canonical 44-byte PCM RIFF header for ``data_bytes`` of audio.

    Every size field is packed as unsigned 32-bit (``I``), which is what allows
    a payload above 2 GiB. A signed field would wrap at 2 GiB and is exactly the
    bug this writer exists to avoid.
    """
    block_align = channels * (bits // 8)
    byte_rate = sample_rate * block_align
    data_bytes = int(data_bytes)
    if not 0 <= data_bytes <= 0xFFFFFFFF - 36:
        raise ValueError("WAV data size does not fit the unsigned RIFF field")
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_bytes, b"WAVE",
        b"fmt ", 16, 1, channels, sample_rate, byte_rate, block_align, bits,
        b"data", data_bytes,
    )


class _WavPart:
    """One open WAV part: header + PCM payload, finalised on close."""

    def __init__(self, path: Path, sample_rate: int = SAMPLE_RATE,
                 channels: int = CHANNELS, bits: int = BITS_PER_SAMPLE) -> None:
        self.path = Path(path)
        self.sample_rate = sample_rate
        self.channels = channels
        self.bits = bits
        self.block_align = channels * (bits // 8)
        self.data_bytes = 0
        self._last_header = 0.0
        self._closed = False
        self._handle = open(self.path, "wb")
        self._sync_header()

    def _sync_header(self) -> None:
        header = wav_header_bytes(self.data_bytes, self.sample_rate, self.channels, self.bits)
        self._handle.seek(0)
        self._handle.write(header)
        self._handle.seek(0, os.SEEK_END)

    def write(self, data: bytes) -> None:
        if self._closed or not data:
            return
        self._handle.write(data)
        self.data_bytes += len(data)
        now = time.monotonic()
        if now - self._last_header >= _HEADER_REFRESH_SECONDS:
            # A crash or a pulled cable then leaves a readable file up to the
            # last refresh instead of a header that still claims zero bytes.
            self._last_header = now
            self._sync_header()

    @property
    def full(self) -> bool:
        return self.data_bytes >= MAX_PART_DATA_BYTES

    def room(self, size: int) -> int:
        return max(0, min(int(size), MAX_PART_DATA_BYTES - self.data_bytes))

    def finalize(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            # parec delivers whole frames, but a very short or interrupted
            # capture can end mid-frame. Drop the partial frame rather than
            # publishing a corrupt PCM payload.
            usable = self.data_bytes - (self.data_bytes % self.block_align)
            if usable != self.data_bytes:
                self.data_bytes = usable
                try:
                    self._handle.truncate(WAV_HEADER_BYTES + usable)
                except OSError:
                    pass
            self._sync_header()
            try:
                self._handle.flush()
                os.fsync(self._handle.fileno())
            except OSError:
                pass
        finally:
            try:
                self._handle.close()
            except OSError:
                pass


def recorder_command(path: Optional[Path] = None, device: str = "@DEFAULT_MONITOR@",
                     parec: str = "parec") -> list:
    """Capture raw PCM; MasterRecorder writes the 4 GiB-safe WAV/FLAC container.

    ``path`` is accepted for callers that used to pass the destination to
    parec, but parec no longer writes a WAV file itself. It emits raw s16le
    frames on stdout, and the mixer owns the header, the encoder and the part
    boundary.
    """
    del path
    return [
        parec,
        f"--device={device}",
        f"--rate={SAMPLE_RATE}",
        f"--channels={CHANNELS}",
        "--format=s16le",
        "--raw",
    ]


class _FlacPart:
    """One FLAC part: raw PCM in on stdin, a self-contained FLAC file out."""

    kind = "FLAC"

    def __init__(self, path: Path, flac: Optional[str] = None,
                 ffmpeg: Optional[str] = None) -> None:
        self.path = Path(path)
        self.data_bytes = 0
        self._closed = False
        if flac:
            command = [
                flac,
                "--silent",
                "--force",
                "--force-raw-format",
                "--endian=little",
                "--sign=signed",
                f"--channels={CHANNELS}",
                f"--bps={BITS_PER_SAMPLE}",
                f"--sample-rate={SAMPLE_RATE}",
                "-o", str(self.path),
                "-",
            ]
        elif ffmpeg:
            command = [
                ffmpeg,
                "-hide_banner", "-loglevel", "error",
                "-f", "s16le",
                "-ar", str(SAMPLE_RATE),
                "-ac", str(CHANNELS),
                "-i", "pipe:0",
                "-c:a", "flac",
                "-f", "flac",
                "-y", str(self.path),
            ]
        else:
            raise RuntimeError("neither flac nor ffmpeg is installed")
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    @property
    def output_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    @property
    def full(self) -> bool:
        return self.output_bytes >= FLAC_PART_LIMIT_BYTES

    def room(self, size: int) -> int:
        # FLAC's output size is what the 4 GiB ceiling applies to, not the
        # amount of PCM fed in. Accept one capture chunk and re-check after it.
        return int(size)

    def write(self, data: bytes) -> None:
        if self._closed or not data:
            return
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise BrokenPipeError("FLAC encoder is not running")
        proc.stdin.write(data)
        self.data_bytes += len(data)

    def finalize(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            try:
                proc.terminate()
                proc.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass


class MasterRecorder:
    """Start/stop the monitor capture and report its state to the UI."""

    def __init__(self, base: Optional[Path] = None) -> None:
        self.base = Path(base or PROJECT_ROOT)
        self.proc: Optional[subprocess.Popen] = None
        self.path: Optional[Path] = None
        self.started_at = 0.0
        self.error = ""
        self.last_saved: Optional[Path] = None
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._writer: Optional[_WavPart] = None
        self._base_path: Optional[Path] = None
        self._part_index = 1
        self._stop_requested = False
        self._stop_path: Optional[Path] = None
        self._format = "WAV"

    @property
    def is_recording(self) -> bool:
        proc = self.proc
        return proc is not None and proc.poll() is None

    @property
    def format(self) -> str:
        """The container the next/current part uses: ``WAV`` or ``FLAC``."""
        return self._format

    def set_format(self, fmt: str) -> str:
        """Select WAV or FLAC. A live recording is restarted in the new format."""
        chosen = "FLAC" if str(fmt).upper() == "FLAC" else "WAV"
        if chosen == self._format:
            return self._format
        was_recording = self.is_recording
        if was_recording:
            self.stop()
        self._format = chosen
        if was_recording:
            self.start()
        return self._format

    @property
    def part(self) -> int:
        """The part currently being written (1 for the original file)."""
        return self._part_index

    def elapsed(self) -> float:
        return max(0.0, time.time() - self.started_at) if self.started_at else 0.0

    def elapsed_label(self) -> str:
        total = int(self.elapsed())
        return f"{total // 60:02d}:{total % 60:02d}"

    # ------------------------------------------------------------------
    # capture lifecycle
    # ------------------------------------------------------------------
    def start(self) -> Optional[Path]:
        with self._lock:
            if self.is_recording:
                return self.path
            parec = shutil_which("parec")
            if not parec:
                self.error = "parec is not installed"
                return None
            out_dir = recording_dir(self.base)
            fmt = self._format
            flac_bin = find_flac()
            ffmpeg_bin = find_ffmpeg()
            if fmt == "FLAC" and not (flac_bin or ffmpeg_bin):
                self.error = "flac and ffmpeg are not installed"
                return None
            path = _unique_path(out_dir / recording_name(fmt=fmt))
            log = out_dir / "liquid_mixer_recording.log"
            try:
                if fmt == "FLAC":
                    writer = _FlacPart(path, flac_bin, ffmpeg_bin)
                else:
                    writer = _WavPart(path)
            except Exception as exc:
                self.error = str(exc)
                return None
            try:
                with open(log, "a", encoding="utf-8") as handle:
                    handle.write(f"\n--- start {path.name} ({fmt}, 4 GiB parts) ---\n")
                    handle.flush()
                    proc = subprocess.Popen(
                        recorder_command(parec=parec),
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=handle,
                        start_new_session=True,
                    )
            except Exception as exc:
                self.error = str(exc)
                try:
                    writer.finalize()
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                return None
            self.proc = proc
            self._writer = writer
            self._base_path = path
            self._part_index = 1
            self._stop_requested = False
            self._stop_path = None
            self.path = path
            self.started_at = time.time()
            self.error = ""
            self._thread = threading.Thread(target=self._pump, args=(proc,), daemon=True)
            self._thread.start()
            return path

    def stop(self) -> Optional[Path]:
        with self._lock:
            proc = self.proc
            self._stop_requested = True
        if proc is not None:
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
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=6.0)
            if thread.is_alive():
                # A stuck read must not hold the UI. Closing the pipe wakes the
                # reader; the writer is finalised under the lock below.
                try:
                    if proc is not None and proc.stdout is not None:
                        proc.stdout.close()
                except Exception:
                    pass
                thread.join(timeout=1.0)
        with self._lock:
            if self._writer is not None:
                self._close_current_locked()
            self.proc = None
            self.started_at = 0.0
            self._thread = None
            self._stop_requested = False
            saved = self._stop_path
            self._stop_path = None
        return saved

    def tick(self) -> None:
        """Notice a recorder that exited on its own (unplugged device, error)."""
        proc = self.proc
        if proc is None or proc.poll() is None:
            return
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        with self._lock:
            if self.proc is None:
                return
            if self._writer is not None:
                self._close_current_locked()
            if not self._stop_requested and not self.error:
                self.error = "recorder stopped unexpectedly"
            self.proc = None
            self.started_at = 0.0
            self._thread = None

    # ------------------------------------------------------------------
    # writer thread
    # ------------------------------------------------------------------
    def _pump(self, proc: subprocess.Popen) -> None:
        stream = getattr(proc, "stdout", None)
        error = ""
        try:
            while True:
                chunk = stream.read(_READ_CHUNK) if stream is not None else b""
                if not chunk:
                    break
                self._write_bytes(chunk)
        except Exception as exc:
            error = f"recording write failed: {exc}"
            try:
                proc.terminate()
            except Exception:
                pass
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            with self._lock:
                if self._writer is not None:
                    self._close_current_locked()
                if error:
                    self.error = error
                elif not self._stop_requested:
                    self.error = "recorder stopped unexpectedly"
                self.proc = None
                self.started_at = 0.0

    def _write_bytes(self, data: bytes) -> None:
        view = memoryview(data)
        with self._lock:
            while view and self._writer is not None:
                if self._writer.full:
                    self._close_current_locked()
                    self._part_index += 1
                    self._open_part_locked()
                    continue
                take = self._writer.room(len(view))
                if take <= 0:
                    self._close_current_locked()
                    self._part_index += 1
                    self._open_part_locked()
                    continue
                self._writer.write(view[:take])
                view = view[take:]

    def _open_part_locked(self) -> None:
        base = self._base_path
        if base is None:
            return
        path = _unique_path(recording_part_path(base, self._part_index))
        if self._format == "FLAC":
            self._writer = _FlacPart(path, find_flac(), find_ffmpeg())
        else:
            self._writer = _WavPart(path)
        self.path = path

    def _close_current_locked(self) -> None:
        writer = self._writer
        if writer is None:
            return
        self._writer = None
        try:
            writer.finalize()
        except OSError:
            pass
        if writer.data_bytes > 0:
            try:
                has_output = writer.path.is_file() and writer.path.stat().st_size > 0
            except OSError:
                has_output = False
            if has_output:
                self.last_saved = writer.path
                self._stop_path = writer.path

def shutil_which(name: str) -> Optional[str]:
    from shutil import which
    return which(name)
