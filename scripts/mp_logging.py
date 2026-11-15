#!/usr/bin/env python3
"""
mp_logging.py - Centralized Error and Event Logging for MP Mix Archive Manager
Part of MP_Mix_Manager_v0.3

Logs errors cleanly to MP_Error.log. Every physical line of an entry carries
its own ``[YYYY-MM-DD HH:MM:SS]`` stamp so a multi-line traceback can still be
grep'd, sorted or tailed by time:

    [2026-11-14 00:38:16] [ERROR] [MP_Audio_Player] SoundFile read glitch: /x.wav
    [2026-11-14 00:38:16]   Exception: LibsndfileError: ...
    [2026-11-14 00:38:16]   Traceback:
    [2026-11-14 00:38:16] Traceback (most recent call last):
    [2026-11-14 00:38:16]   File "MP_Audio_Player.py", line 1311, in read

Identical failures are also rate limited. A detached worker that kept retrying
against a deleted file used to append ~1,700 duplicate EIO entries; repeating
the same (component, message, exception) inside ``MP_ERROR_DEDUPE_SECONDS`` now
collapses into one entry that reports how many were suppressed.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

# Resolve base directories
SCRIPT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path.home() / ".config" / "mix-manager"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

# Primary MP_Error.log paths
PRIMARY_ERROR_LOG = SCRIPT_DIR / "MP_Error.log"
BACKUP_ERROR_LOG = CONFIG_DIR / "MP_Error.log"

# One line per entry, every line stamped.
SEPARATOR = "-" * 70
ENTRY_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \[(?:ERROR|WARNING)\]")

# Dedupe window and cap, in seconds / bytes.
DEDUPE_SECONDS = float(os.environ.get("MP_ERROR_DEDUPE_SECONDS", "5") or 5)
MAX_LOG_BYTES = int(os.environ.get("MP_MAX_ERROR_LOG_BYTES", 0) or 0)

_DEDUPE_LOCK = threading.Lock()
_RECENT: dict[tuple, list] = {}


def get_error_log_path() -> Path:
    """Return writable path for MP_Error.log."""
    # A test run must never append to the developer's real error log. The suite
    # deletes temp directories while feeder threads are still reading, so each
    # run would otherwise deposit entries for files that never existed.
    if os.environ.get("MP_TEST_MODE") == "1":
        test_dir = Path(os.environ.get("MP_TEST_LOG_DIR") or (Path(tempfile.gettempdir()) / "mp-error-log"))
        try:
            test_dir.mkdir(parents=True, exist_ok=True)
            log_path = test_dir / f"MP_Error.{os.getpid()}.log"
            log_path.touch(exist_ok=True)
            return log_path
        except OSError:
            pass
    try:
        PRIMARY_ERROR_LOG.touch(exist_ok=True)
        return PRIMARY_ERROR_LOG
    except (PermissionError, OSError):
        BACKUP_ERROR_LOG.touch(exist_ok=True)
        return BACKUP_ERROR_LOG


def _stamp() -> str:
    return f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]"


def _stamped(text: str, stamp: str) -> str:
    """Prefix every physical line of ``text`` with ``stamp``."""
    return "\n".join(f"{stamp} {line}" if line else f"{stamp}"
                     for line in str(text).split("\n"))


def _rotate_if_needed(log_path: Path) -> None:
    if MAX_LOG_BYTES <= 0:
        return
    try:
        if log_path.stat().st_size <= MAX_LOG_BYTES:
            return
        rotated = log_path.with_name(f"{log_path.stem}-{datetime.now():%Y%m%d-%H%M%S}.log")
        log_path.replace(rotated)
    except OSError:
        pass


def _suppressed(key: tuple) -> tuple[bool, int]:
    """Decide whether this occurrence should be written.

    Returns ``(emit, suppressed_count)``. The first occurrence always writes.
    Repeats inside the window are counted but not written; when the window
    lapses the next occurrence writes and reports how many were folded into it.
    """
    if DEDUPE_SECONDS <= 0:
        return True, 0  # Dedupe disabled: always emit.
    now = time.monotonic()
    with _DEDUPE_LOCK:
        entry = _RECENT.get(key)
        if entry is None:
            _RECENT[key] = [now, 0]
            # Bound the table so a long-running player cannot grow it forever.
            if len(_RECENT) > 512:
                for k, (seen, _n) in list(_RECENT.items()):
                    if now - seen > DEDUPE_SECONDS:
                        _RECENT.pop(k, None)
            return True, 0
        last_seen, count = entry
        if now - last_seen < DEDUPE_SECONDS:
            entry[1] = count + 1
            return False, count
        entry[0], entry[1] = now, 0
        return True, count


def _write(entry: str) -> None:
    log_path = get_error_log_path()
    _rotate_if_needed(log_path)
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        sys.stderr.write(f"Failed writing to {log_path}: {e}\n{entry}")


def log_error(component: str, message: str, exc: Exception = None,
              details: str = None, dedupe: bool = True) -> None:
    """Log an error entry to MP_Error.log, one timestamp per line."""
    key = (component, message, type(exc).__name__ if exc is not None else "")
    emit, suppressed = _suppressed(key) if dedupe else (True, 0)
    if not emit:
        return  # Within the dedupe window; folded into the next write.

    stamp = _stamp()
    lines = [f"{stamp} [ERROR] [{component}] {message}"]
    if suppressed:
        lines.append(_stamped(f"  Repeated {suppressed} more time(s) within {DEDUPE_SECONDS:g}s", stamp))
    if details:
        lines.append(_stamped(f"  Details: {details}", stamp))
    if exc is not None:
        lines.append(_stamped(f"  Exception: {type(exc).__name__}: {exc}", stamp))
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        if tb.strip():
            lines.append(_stamped(f"  Traceback:\n{tb.rstrip()}", stamp))
    lines.append(SEPARATOR)

    _write("\n".join(lines) + "\n")


def log_warning(component: str, message: str, dedupe: bool = True) -> None:
    """Log a warning entry to MP_Error.log, one timestamp per line."""
    key = (component, f"WARNING:{message}", "")
    emit, suppressed = _suppressed(key) if dedupe else (True, 0)
    if not emit:
        return
    stamp = _stamp()
    extra = f" (repeated {suppressed} more time(s) within {DEDUPE_SECONDS:g}s)" if suppressed else ""
    _write(_stamped(f"[WARNING] [{component}] {message}{extra}", stamp) + "\n" + SEPARATOR + "\n")


def get_recent_errors(limit: int = 25) -> list[str]:
    """Retrieve the most recent error entries from MP_Error.log."""
    log_path = get_error_log_path()
    if not log_path.exists():
        return []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        entries = content.strip().split(SEPARATOR)
        recent = [e.strip() for e in entries if e.strip()]
        return recent[-limit:]
    except Exception:
        return []


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--tail":
        errs = get_recent_errors(int(sys.argv[2]) if len(sys.argv) > 2 else 20)
        if not errs:
            print("No errors logged in MP_Error.log.")
        else:
            for e in errs:
                print(e)
                print(SEPARATOR)
    elif len(sys.argv) > 2 and sys.argv[1] == "--log":
        comp = sys.argv[2]
        msg = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else "Unknown error"
        log_error(comp, msg)
        print(f"Logged error to {get_error_log_path()}")
    else:
        print(f"MP_Error.log location: {get_error_log_path()}")
