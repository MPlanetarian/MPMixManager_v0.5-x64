#!/usr/bin/env python3
"""Open MP Liquid Mixer from the X key, and never open a second one.

MP Gui, MP Liquid Player and the console MP Mix Manager all route X here. The
mixer already holds ``~/.config/mix-manager/liquid_mixer.lock`` with ``flock``
for its whole life, because its two deck engines live in fixed IPC
namespaces and a second window would retire the first pair. That lock is the
truth this module reads: while it is held, X raises the window that owns it
and never spawns another.

A launch claim covers the moment between ``Popen`` and the mixer taking its
own lock, so two X presses in a row cannot both start a window. The mixer
clears the claim once its window is mapped, and a claim whose process is gone
(or that is older than ``CLAIM_STALE_SECONDS``) is ignored.

``launch_mixer`` returns one of:

``opened``        a mixer window was spawned
``already-open``  a mixer held the lock; it was raised instead
``starting``      a launch claim was still live; no second window
``unsupported``   no X11 display on this platform, so the mixer cannot draw
``missing``       the mixer tree was not found beside this module
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

try:
    import fcntl
except ImportError:  # Windows has no flock; the mixer cannot draw there anyway
    fcntl = None  # type: ignore[assignment]

SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
MIXER_DIR = PROJECT_ROOT / "MP_Liquid_Mixer"

CONFIG_DIR = Path(os.environ.get("MP_LIQUID_MIXER_CONFIG_DIR", "") or (Path.home() / ".config" / "mix-manager"))
LOCK_PATH = CONFIG_DIR / "liquid_mixer.lock"
CLAIM_PATH = CONFIG_DIR / "liquid_mixer_launch.json"
CLAIM_GUARD_PATH = CONFIG_DIR / "liquid_mixer_launch.lock"
LOG_PATH = CONFIG_DIR / "liquid_mixer.log"

TITLE = "MP Liquid Mixer"
CLAIM_STALE_SECONDS = 60.0

# Indirection so a test can watch the spawn without replacing the real
# ``subprocess.Popen`` for every other caller in the process.
_popen = subprocess.Popen


# ------------------------------------------------------------------------------
# the lock: is a mixer window alive?
# ------------------------------------------------------------------------------
def mixer_lock_held() -> bool:
    """True while a *live* mixer owns ``liquid_mixer.lock``.

    A non-blocking **shared** attempt is enough: the mixer holds the same file
    with ``LOCK_EX`` for its whole life, and a shared lock cannot be taken
    while an exclusive one is held. The probe opens read-only and never writes,
    so an open mixer's pid record stays intact; a lock left behind by a closed
    window is simply free.

    A crash that leaves the file behind is the trap here: the flock it held is
    gone with the process, but the pid record survives. Confirming the pid is
    still a live mixer window before reporting "already open" is what stops a
    hard-killed session from blocking every later launch.
    """
    if fcntl is None:
        return _mixer_process_running()
    if not LOCK_PATH.exists():
        return False
    try:
        fd = os.open(str(LOCK_PATH), os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError as exc:
        held = exc.errno in (errno.EACCES, errno.EAGAIN)
        os.close(fd)
        if held and not lock_owner_alive():
            clear_stale_lock()
            return False
        return held
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    # The file exists and nobody holds it: a stale record from a crash.
    if not lock_owner_alive():
        clear_stale_lock()
    return False


def lock_owner_alive() -> bool:
    """True when the lock's pid is a running MP Liquid Mixer process."""
    try:
        pid = int(LOCK_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if pid <= 0 or not _pid_alive(pid):
        return False
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        if stat.rsplit(")", 1)[1].split()[0] == "Z":
            return False
    except (OSError, IndexError):
        pass
    try:
        with open(f"/proc/{pid}/cmdline", encoding="utf-8", errors="ignore") as handle:
            return "MP_Liquid_Mixer.py" in handle.read()
    except OSError:
        return False


def clear_stale_lock() -> None:
    """Delete a lock record that no live mixer process owns."""
    if not lock_owner_alive():
        try:
            LOCK_PATH.unlink(missing_ok=True)
        except OSError:
            pass


def _mixer_process_running() -> bool:
    """Fallback for platforms without fcntl: look for the mixer process."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "MP_Liquid_Mixer[.]py"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ------------------------------------------------------------------------------
# the launch claim: one spawn at a time
# ------------------------------------------------------------------------------
class _ClaimGuard:
    """Serialise check-and-spawn so two X presses cannot both pass the check."""

    def __init__(self) -> None:
        self._fd: Optional[int] = None

    def __enter__(self) -> "_ClaimGuard":
        if fcntl is None:
            return self
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(str(CLAIM_GUARD_PATH), os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            self._fd = None
            return self
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        except OSError:
            os.close(self._fd)
            self._fd = None
        return self

    def __exit__(self, *_exc) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_launch_claim() -> Optional[dict]:
    try:
        payload = json.loads(CLAIM_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def launch_claim_active() -> bool:
    """True while a mixer this module spawned is starting up."""
    claim = read_launch_claim()
    if not claim:
        return False
    try:
        pid = int(claim.get("pid") or 0)
        started = float(claim.get("started") or 0.0)
    except (TypeError, ValueError):
        return False
    if time.time() - started > CLAIM_STALE_SECONDS:
        return False
    return _pid_alive(pid)


def write_launch_claim(pid: int) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        body = json.dumps({"pid": int(pid), "started": time.time()}) + "\n"
        temporary = CLAIM_PATH.with_name(CLAIM_PATH.name + ".tmp")
        temporary.write_text(body, encoding="utf-8")
        os.replace(temporary, CLAIM_PATH)
    except OSError:
        pass


def release_launch_claim(pid: Optional[int] = None) -> None:
    """Drop a launch claim. Called by the mixer once it owns the lock.

    Only the launch whose pid wrote the claim may clear it; anyone else
    leaves it alone, and the pid/staleness checks in ``launch_claim_active``
    retire a claim whose mixer never started.
    """
    claim = read_launch_claim()
    if claim is not None and pid is not None:
        try:
            if int(claim.get("pid") or 0) != int(pid):
                return
        except (TypeError, ValueError):
            pass
    try:
        CLAIM_PATH.unlink(missing_ok=True)
    except OSError:
        pass


# ------------------------------------------------------------------------------
# raise the window that is already open
# ------------------------------------------------------------------------------
def focus_mixer() -> bool:
    """Bring the open mixer to the front. Best effort on every platform."""
    if sys.platform == "win32":
        return _focus_windows_window(TITLE)
    if sys.platform == "darwin":
        script = (
            'tell application "System Events"\n'
            "  repeat with proc in processes\n"
            "    try\n"
            "      repeat with w in windows of proc\n"
            f'        if name of w contains "{TITLE}" then\n'
            "          set frontmost of proc to true\n"
            '          perform action "AXRaise" of w\n'
            "          return\n"
            "        end if\n"
            "      end repeat\n"
            "    end try\n"
            "  end repeat\n"
            "end tell\n"
        )
        try:
            subprocess.run(
                ["osascript", "-e", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=False,
            )
            return True
        except (OSError, subprocess.SubprocessError):
            return False
    wmctrl = shutil.which("wmctrl")
    if wmctrl:
        try:
            result = subprocess.run(
                [wmctrl, "-a", TITLE],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
            if result.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    xdotool = shutil.which("xdotool")
    if xdotool:
        try:
            found = subprocess.run(
                [xdotool, "search", "--name", TITLE],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
                text=True,
            )
            ids = [line.strip() for line in (found.stdout or "").splitlines() if line.strip().isdigit()]
            if ids:
                window = ids[-1]
                subprocess.run(
                    [xdotool, "windowactivate", "--sync", window],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                    check=False,
                )
                subprocess.run(
                    [xdotool, "windowraise", window],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                    check=False,
                )
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    return False


def _focus_windows_window(title: str) -> bool:
    try:
        import ctypes
    except Exception:
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        found: list[int] = []

        callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)

        @callback_type(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _enum(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if buffer.value.startswith(title):
                found.append(int(hwnd))
                return False
            return True

        user32.EnumWindows(_enum, 0)
        if not found:
            return False
        hwnd = found[0]
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------------------
# launch
# ------------------------------------------------------------------------------
def mixer_supported() -> bool:
    """The mixer is an X11 Liquid Glass host, so it needs an X11 display.

    ``MP_Liquid_Mixer.main`` refuses to start without ``$DISPLAY`` (it forces
    ``GDK_BACKEND=x11`` for the window id the visualizer bonds to), so the
    launcher asks the same question instead of spawning a process that would
    only print an error.
    """
    if not sys.platform.startswith(("linux", "freebsd")):
        return False
    return bool(os.environ.get("DISPLAY"))


def mixer_command() -> Optional[list]:
    """The one launch path the shortcut, X key and run.sh all share.

    ``scripts/launch_liquid_mixer.sh`` resolves the PyGObject-capable
    interpreter and strips a parent MP window's session flags before it execs
    ``MP_Liquid_Mixer/run.sh``, so X does not duplicate that logic.
    """
    launcher = SCRIPTS_DIR / "launch_liquid_mixer.sh"
    if launcher.is_file():
        return ["bash", str(launcher)]
    run_sh = MIXER_DIR / "run.sh"
    mixer_py = MIXER_DIR / "MP_Liquid_Mixer.py"
    if run_sh.is_file():
        return ["bash", str(run_sh)]
    if mixer_py.is_file():
        return [sys.executable, str(mixer_py)]
    return None


def _child_env() -> dict:
    """A mixer started from inside another MP window is still a fresh host.

    The parent's session flags belong to the window the user pressed X in:
    leaving ``MP_GUI_SESSION`` set would give the mixer's companion visualizer
    the MP Gui record, and ``MP_PLAYER_NAMESPACE``/``MP_LIQUID_GLASS_MIXER``
    are a deck's own identity. The mixer names its own decks.
    """
    drop = {
        "GDK_BACKEND",
        "MP_GUI_SESSION",
        "MP_LIQUID_GLASS_MANAGER",
        "MP_LIQUID_GLASS_MIXER",
        "MP_PLAYER_NAMESPACE",
    }
    return {key: value for key, value in os.environ.items() if key not in drop}


def launch_mixer(base_dir: Optional[Path] = None) -> str:
    """Open the mixer, or raise the only one that may exist."""
    del base_dir  # the mixer ships beside this module; the tree is fixed
    if not mixer_supported():
        return "unsupported"
    command = mixer_command()
    if not command:
        return "missing"
    if mixer_lock_held():
        focus_mixer()
        return "already-open"
    if launch_claim_active():
        focus_mixer()
        return "starting"
    with _ClaimGuard():
        # Re-check now that this process is the only one in the critical
        # section: a press that arrived a moment ago may already have spawned.
        if mixer_lock_held():
            focus_mixer()
            return "already-open"
        if launch_claim_active():
            focus_mixer()
            return "starting"
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            log = open(LOG_PATH, "a", encoding="utf-8")
        except OSError:
            log = subprocess.DEVNULL
        extra_kwargs = {}
        if sys.platform == "win32":
            extra_kwargs["creationflags"] = 0x00000008 | 0x00000200
        else:
            extra_kwargs["start_new_session"] = True
        try:
            proc = _popen(
                command,
                cwd=str(MIXER_DIR),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                close_fds=True,
                env=_child_env(),
                **extra_kwargs,
            )
        except Exception:
            try:
                if log is not subprocess.DEVNULL:
                    log.close()
            except Exception:
                pass
            return "missing"
        try:
            if log is not subprocess.DEVNULL:
                log.close()
        except Exception:
            pass
        write_launch_claim(proc.pid)
        return "opened"


def status() -> str:
    if mixer_lock_held():
        return "running"
    if launch_claim_active():
        return "starting"
    return "free"


def main(argv: Optional[list] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    action = args[0] if args else "status"
    if action in ("status", "check"):
        print(status())
        return 0
    if action == "focus":
        return 0 if focus_mixer() else 1
    if action in ("launch", "open", "x"):
        result = launch_mixer()
        print(result)
        return 0 if result in ("opened", "already-open", "starting") else 1
    print("usage: liquid_mixer_launch.py [status|focus|launch]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
