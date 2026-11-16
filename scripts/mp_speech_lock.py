#!/usr/bin/env python3
# ==============================================================================
# mp_speech_lock.py — one speaker at a time, across processes.
# ------------------------------------------------------------------------------
# Several programs on this machine produce speech or alert sounds:
#
#   * MP Reporter's email timer (a confirmation chime after a send)
#   * one-shot announcements  (a CLI run while a report is being read)
#
# They used to play independently, so a chime or a second announcement could
# start on top of a 140-second narration. This module serialises them with a
# POSIX advisory lock, which is shared by every process on the machine.
#
# Usage as a library:
#     from mp_speech_lock import SpeechLock
#     with SpeechLock():               # blocks until the speaker is free
#         play(wav)
#
#     if SpeechLock.acquire(block=False):   # give up instead of queueing
#         ...
#         SpeechLock.release()
#
# Usage from the shell:
#     mp_speech_lock.py run <command...>    Hold the lock while the command runs
#     mp_speech_lock.py status              Show who holds it
#
# A crashed process cannot leave the lock held: POSIX locks are released by the
# kernel when the holder exits.
# ==============================================================================
from __future__ import annotations

import errno
import fcntl
import os
import sys
import time
from pathlib import Path
from typing import Optional

DEFAULT_LOCK = Path(
    os.environ.get("MP_SPEECH_LOCK", "")
    or (Path.home() / ".local" / "state" / "mp-speech.lock")
)


class SpeechLock:
    """Exclusive, machine-wide right to produce sound."""

    def __init__(self, path: Optional[Path] = None, timeout: float = 900.0,
                 poll: float = 0.25):
        self.path = Path(path) if path else DEFAULT_LOCK
        self.timeout = timeout
        self.poll = poll
        self._fh = None

    # -- blocking context manager -----------------------------------------
    def acquire(self, block: bool = True) -> bool:
        """Take the lock. Returns False when non-blocking and already held."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")
        deadline = time.time() + self.timeout
        warned = False
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fh = fh
                return True
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    fh.close()
                    raise
                if not block:
                    fh.close()
                    return False
                if time.time() >= deadline:
                    fh.close()
                    raise TimeoutError(
                        f"speech lock still held after {self.timeout:.0f}s: {self.path}"
                    )
                if not warned:
                    warned = True
                    try:
                        holder = self.path.read_text(encoding="utf-8").strip()
                    except Exception:
                        holder = ""
                    print(
                        "[mp_speech_lock] waiting for the current announcement "
                        f"to finish{' (' + holder + ')' if holder else ''}",
                        file=sys.stderr,
                    )
                time.sleep(self.poll)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False

    def __del__(self):
        # Never leak the descriptor if a caller forgets to release.
        try:
            self.release()
        except Exception:
            pass

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            # Leave a readable owner note for `status`.
            try:
                fh.seek(0)
                fh.truncate()
            except Exception:
                pass
            fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


def lock_path() -> Path:
    return DEFAULT_LOCK


def _describe_holder(path: Path) -> str:
    """Best-effort: which process currently holds the lock."""
    try:
        import subprocess
        out = subprocess.run(
            ["fuser", str(path)], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if out:
            pids = " ".join(out.split())
            return f"held by PID(s): {pids}"
    except Exception:
        pass
    # Try a non-blocking probe in-process.
    try:
        fh = open(path, "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh, fcntl.LOCK_UN)
            return "free"
        except OSError:
            return "held by another process"
        finally:
            fh.close()
    except Exception:
        return "unknown"


def main(argv) -> int:
    """Run a command while holding the speech lock."""
    args = list(argv[1:])
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__ or "")
        return 0 if args else 2

    if args[0] in ("status", "--status"):
        path = lock_path()
        print(f"lock: {path}")
        print(_describe_holder(path))
        return 0

    if args[0] in ("run", "--run"):
        args = args[1:]
    if not args:
        print("usage: mp_speech_lock.py run <command...>", file=sys.stderr)
        return 2

    import subprocess
    with SpeechLock():
        return subprocess.run(args).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv))
