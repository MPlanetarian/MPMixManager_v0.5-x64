#!/usr/bin/env python3
"""Keep MP Gui and MP Liquid Player to four live instances at a time.

Every launch path asks this module before a window is created: the desktop
shortcuts, the global shortcut keys, the shared tray icon, the player menu,
VR Mode, and the Liquid Glass hosts themselves. A claim file per live process
makes the count atomic, so two launches that arrive together cannot both take
the last slot.

Only MP Gui (a --manager host) and MP Liquid Player are capped. The tracklist
viewer, the MP Liquid Visualizer, and the console manager are not.

The same module decides when the MP exit video may be shown. A closing window
marks itself, then asks whether any MP Gui or MP Liquid Player window is still
live. The answer is "yes" only for the last one, counting both roles together,
so closing one of two open windows stays silent.

Exit codes for the command line: 0 allowed / claimed / last window, 3 at the
limit, 1 not the last window, 2 bad usage. Launchers use 3 to focus the window
that is already open.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

MAX_INSTANCES = 4

ROLE_GUI = "gui"
ROLE_PLAYER = "player"
ROLES = (ROLE_GUI, ROLE_PLAYER)
ROLE_LABELS = {
    ROLE_GUI: "MP Gui",
    ROLE_PLAYER: "MP Liquid Player",
}
FOCUS_TITLES = {
    ROLE_GUI: "MP Mix Manager (Liquid Glass",
    ROLE_PLAYER: "MP Audio Player (Liquid Glass",
}

# Liquid Glass hosts. All four draw either the player or the MP Gui window.
HOST_SCRIPTS = (
    "MP_Audio_Player_Liquid_Glass.py",
    "MP_Audio_Player_Liquid_Glass_OpenGL.py",
    "MP_Audio_Player_Liquid_Glass_macOS.py",
    "MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py",
)

# A launcher claims a slot before it execs the host. A claim that outlives its
# launch window is stale even while the pid is alive (the pid may be recycled).
CLAIM_GRACE_SECONDS = 120.0
LOCK_STALE_SECONDS = 10.0
LOCK_WAIT_SECONDS = 3.0


def config_dir() -> Path:
    override = os.environ.get("MP_INSTANCE_CONFIG_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".config" / "mix-manager"


def state_dir() -> Path:
    path = Path(os.environ.get("MP_INSTANCE_STATE_DIR", "") or (config_dir() / "instances"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def limit_message(role: str) -> str:
    label = ROLE_LABELS.get(role, "MP instance")
    return (
        f"{label} is already open {MAX_INSTANCES} times. "
        f"Close one window before opening another ({MAX_INSTANCES} is the maximum)."
    )


def _command_tokens(cmdline: str) -> list[str]:
    return cmdline.replace("\x00", " ").split()


# Interpreters that run a host script as their own program. An editor, a
# `tail -f`, or a shell whose *arguments* mention the host are not windows, so
# only these may introduce a host script — never bash/sh.
_HOST_INTERPRETERS = (
    "python", "python2", "python3", "pypy", "pypy3",
)


def _interpreter_name(token: str) -> str:
    # Backslashes are path separators on Windows but not on POSIX, so normalise
    # them before Path: tests run this module on Linux against Windows command
    # lines, and `Path(r"C:\venv\Scripts\pythonw.exe").name` would otherwise
    # return the whole string and lose the interpreter name.
    return Path(token.strip("'\" ").replace("\\", "/")).name


def _is_host_interpreter(token: str) -> bool:
    name = _interpreter_name(token)
    # The Windows launchers use pythonw.exe. The suffix is not part of the
    # interpreter name, and leaving it on meant no Windows GUI host ever
    # counted toward the four-window cap or the exit-video election.
    if name.lower().endswith(".exe"):
        name = name[:-4]
    if name in _HOST_INTERPRETERS:
        return True
    # Versioned builds: python3.14, python3.11, pypy3.10, plus the windowless
    # pythonw / pythonw3.13 spellings used by the Windows .bat launchers.
    return name.startswith(("python3.", "python2.", "pypy3.", "pythonw"))


def _host_script_index(tokens: list[str]) -> int | None:
    """Index of the token that actually runs a Liquid Glass host, or None.

    A window is either the host script executed directly (a shebang run) or a
    script handed to a Python interpreter. Scanning *every* token counted a
    text editor, a `tail`, or a resident shell that merely mentioned the file
    name as an open window, so four such processes filled the cap and the real
    player was refused after its intro movie.
    """
    index = _skip_wrappers(tokens)
    if index is None:
        return None
    if _interpreter_name(tokens[index]) in HOST_SCRIPTS:
        return index
    if not _is_host_interpreter(tokens[index]):
        return None
    for cursor in range(index + 1, len(tokens)):
        token = tokens[cursor]
        if token.startswith("-"):
            # `-c code` / `-m module` are programs, not script paths: the
            # argument that follows them is not a file the interpreter runs.
            if token in ("-c", "-m"):
                return None
            continue
        if Path(token.strip("'\" ").replace("\\", "/")).name in HOST_SCRIPTS:
            return cursor
        # The first non-flag argument is the script (or module) being run.
        return None
    return None


# Prefixes a launcher puts in front of the interpreter. They are transparent:
# `env FOO=1 python3 Host.py` draws the same window as `python3 Host.py`.
_WRAPPER_COMMANDS = ("env", "nice", "nohup", "setsid", "stdbuf", "timeout", "command", "exec")

# Wrapper flags that take the following token as their value, so it must not be
# mistaken for the script. (`env -u LINES python3 Host.py`, `nice -n 5 ...`.)
_WRAPPER_VALUE_FLAGS = {
    "env": ("-u", "--unset", "-C", "--chdir", "-S", "--split-string"),
    "nice": ("-n", "--adjustment"),
    "stdbuf": ("-i", "-o", "-e", "--input", "--output", "--error"),
    "timeout": ("-s", "--signal", "-k", "--kill-after"),
    "setsid": (),
    "nohup": (),
    "command": (),
    "exec": ("-a",),
}


def _skip_wrappers(tokens: list[str]) -> int | None:
    """Index of the real command after any wrapper prefixes, or None."""
    index = 0
    while index < len(tokens):
        name = _interpreter_name(tokens[index])
        if name not in _WRAPPER_COMMANDS:
            return index
        value_flags = _WRAPPER_VALUE_FLAGS.get(name, ())
        index += 1
        while index < len(tokens):
            token = tokens[index]
            # env's KEY=VALUE assignments are part of the prefix.
            if name == "env" and "=" in token and not token.startswith("-"):
                index += 1
                continue
            if token in value_flags:
                index += 2
                continue
            if token.startswith("-"):
                index += 1
                continue
            # `timeout DURATION COMMAND`: the leading duration is not the command.
            if name == "timeout" and token.replace(".", "", 1).isdigit():
                index += 1
                continue
            break
    return None


def role_from_cmdline(cmdline: str) -> str | None:
    """Return the capped role this command line draws, or None."""
    if not cmdline:
        return None
    lowered = cmdline.lower()
    if "mp_instance_limit" in lowered:
        return None
    tokens = _command_tokens(cmdline)
    if _host_script_index(tokens) is None:
        return None
    if any(tok in ("--self-test", "--list-fonts", "--embed-check") for tok in tokens):
        return None
    if any(tok.startswith("--set-font") for tok in tokens):
        return None
    if any(tok == "--tracklist" or tok.startswith("--tracklist=") for tok in tokens):
        return None
    if "--manager" in tokens:
        return ROLE_GUI
    return ROLE_PLAYER


def _proc_table_from_proc() -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return found
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        found.append((int(entry.name), raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()))
    return found


def _proc_table_from_ps() -> list[tuple[int, str]]:
    try:
        result = subprocess.run(
            ["ps", "-ax", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    found: list[tuple[int, str]] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        found.append((int(parts[0]), parts[1]))
    return found


def _drop_windows_redirectors(
    rows: list[tuple[int, int, str]]
) -> list[tuple[int, str]]:
    """Collapse a venv's python.exe/pythonw.exe redirector into its child.

    A Windows venv installs a small redirector as `Scripts/pythonw.exe`. When
    the launcher starts it, the redirector spawns the base interpreter with
    the *same* command line, so the process table showed two hosts for one
    window. The real interpreter is the child: it owns the window and the
    guard claim. Drop the redirector row so one window takes one slot.
    """
    child_commands: dict[int, list[str]] = {}
    for _pid, parent, command in rows:
        child_commands.setdefault(parent, []).append(command)
    kept: list[tuple[int, str]] = []
    for pid, _parent, command in rows:
        if command in child_commands.get(pid, ()):
            # This process is the redirector; its child is the real one.
            continue
        kept.append((pid, command))
    return kept


def _proc_table_from_windows() -> list[tuple[int, str]]:
    script = (
        "Get-CimInstance Win32_Process | "
        "ForEach-Object { \"$($_.ProcessId) $($_.ParentProcessId) $($_.CommandLine)\" }"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            # The close path calls this from pythonw, where a visible console
            # would flash on the desktop for every window that closes.
            creationflags=0x08000000,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    rows: list[tuple[int, int, str]] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        rows.append((int(parts[0]), int(parts[1]), parts[2]))
    return _drop_windows_redirectors(rows)


def process_table() -> list[tuple[int, str]]:
    try:
        # Tests and isolated tooling can supply a fixed table. Without this a
        # subprocess launched by a test still scans the real process table, so
        # a player the user happens to have open changes the result.
        override = os.environ.get("MP_INSTANCE_PROC_TABLE", "")
        if override == "empty":
            return []
        if sys.platform == "win32":
            return _proc_table_from_windows()
        found = _proc_table_from_proc()
        if found:
            return found
        return _proc_table_from_ps()
    except Exception:
        # Counting must never keep a window from opening. An unreadable
        # process table reports no live hosts.
        return []


def role_pids(role: str, table: list[tuple[int, str]] | None = None) -> set[int]:
    pids: set[int] = set()
    for pid, cmdline in table if table is not None else process_table():
        if role_from_cmdline(cmdline) == role:
            pids.add(pid)
    return pids


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # os.kill(pid, 0) raises WinError 87 for a live Windows process, which
        # made every claim look dead and pruned it. Query the process handle.
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return code.value == 259
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:
        return False
    return True


def _role_state_dir(role: str) -> Path:
    path = state_dir() / role
    path.mkdir(parents=True, exist_ok=True)
    return path


def _claim_file(role: str, pid: int) -> Path:
    return _role_state_dir(role) / f"{int(pid)}.claim"


@contextmanager
def _state_lock(role: str):
    """Atomic mkdir lock, portable to Windows and avoidable stale locks."""
    lock = state_dir() / f"{role}.lock"
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            os.mkdir(lock)
            break
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                age = 0.0
            if age > LOCK_STALE_SECONDS:
                try:
                    os.rmdir(lock)
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"instance lock is busy: {lock}")
            time.sleep(0.02)
    try:
        yield
    finally:
        try:
            os.rmdir(lock)
        except OSError:
            pass


def _live_claims(role: str, hosts: set[int]) -> set[int]:
    claims: set[int] = set()
    now = time.time()
    for path in _role_state_dir(role).glob("*.claim"):
        try:
            pid = int(path.stem)
        except ValueError:
            continue
        if not _pid_alive(pid):
            try:
                path.unlink()
            except OSError:
                pass
            continue
        try:
            age = now - path.stat().st_mtime
        except OSError:
            age = 0.0
        if pid in hosts or age <= CLAIM_GRACE_SECONDS:
            claims.add(pid)
        else:
            try:
                path.unlink()
            except OSError:
                pass
    return claims


def instance_count(role: str, exclude_pid: int | None = None) -> int:
    """Live hosts plus pending launch claims, counted once per process.

    `exclude_pid` drops one process from both halves of the count, so a host
    (or its launcher, whose claim it inherited through exec) can ask how many
    *other* windows of that role are open.
    """
    hosts = role_pids(role)
    if exclude_pid:
        hosts.discard(int(exclude_pid))
    try:
        with _state_lock(role):
            claims = _live_claims(role, hosts)
    except (TimeoutError, OSError):
        claims = set()
    counted = hosts | claims
    if exclude_pid:
        counted.discard(int(exclude_pid))
    return len(counted)


def at_limit(role: str, exclude_pid: int | None = None) -> bool:
    return instance_count(role, exclude_pid) >= MAX_INSTANCES


def claim_instance(role: str, pid: int | None = None) -> bool:
    """Reserve one of the four slots for pid. False when all four are taken."""
    pid = int(pid or os.getpid())
    try:
        with _state_lock(role):
            hosts = role_pids(role)
            # The caller may already look like a host (a Liquid Glass process
            # claiming for itself). It must not count against its own slot.
            hosts.discard(pid)
            claims = _live_claims(role, hosts)
            if pid in claims:
                return True
            if len(hosts | claims) >= MAX_INSTANCES:
                return False
            _claim_file(role, pid).write_text(f"{pid}\n", encoding="utf-8")
            return True
    except (TimeoutError, OSError):
        # Never let a broken lock file stop the app. Fall back to a plain count.
        return instance_count(role, exclude_pid=pid) < MAX_INSTANCES


def release_instance(role: str, pid: int | None = None) -> None:
    pid = int(pid or os.getpid())
    try:
        _claim_file(role, pid).unlink()
    except OSError:
        pass


def guard_process(role: str) -> bool:
    """Claim a slot for this process and release it at exit.

    The Liquid Glass hosts call this before they open a window, so a launch
    that skipped every wrapper still cannot become a fifth instance.
    """
    pid = os.getpid()
    if not claim_instance(role, pid):
        return False
    atexit.register(release_instance, role, pid)
    return True


def guard_launch(role: str) -> bool:
    """Reserve a slot for the window this launcher is about to exec.

    The claim belongs to the launcher pid, which exec keeps in place. When the
    launcher cannot be kept (a .bat that starts a detached process), the host
    claims for itself through guard_process.
    """
    return claim_instance(role, os.getpid())


def host_role(role: str) -> str:
    """The role a Liquid Glass host should count as, or "" when it is exempt."""
    return role if role in ROLES else ""


def host_allowed(role: str) -> bool:
    """Claim a slot for a Liquid Glass host. True when the window may open.

    The four host scripts call this before they draw anything, so a launch
    that skipped every launcher still cannot become a fifth window.
    """
    counted = host_role(role)
    if not counted:
        return True
    return guard_process(counted)


def focus_existing(role: str) -> bool:
    """Bring an already-open window of this role forward."""
    title = FOCUS_TITLES.get(role)
    if not title:
        return False
    if sys.platform == "win32":
        return _focus_windows_window(title)
    if sys.platform.startswith(("linux", "freebsd")):
        wmctrl = shutil.which("wmctrl")
        if not wmctrl:
            return False
        try:
            result = subprocess.run(
                [wmctrl, "-a", title],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    if sys.platform == "darwin":
        script = (
            'tell application "System Events"\n'
            "  repeat with proc in processes\n"
            "    try\n"
            "      repeat with w in windows of proc\n"
            f'        if name of w contains "{title}" then\n'
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
    return False


def _focus_windows_window(title: str) -> bool:
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value.startswith(title):
            found.append(int(hwnd))
            return False
        return True

    try:
        user32.EnumWindows(_callback, 0)
        if not found:
            return False
        hwnd = found[0]
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)
        user32.ShowWindow(hwnd, 5)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def notify_limit(role: str, message: str | None = None) -> None:
    label = ROLE_LABELS.get(role, "MP Mix Manager")
    text = message or limit_message(role)
    print(text, file=sys.stderr)
    if sys.platform.startswith(("linux", "freebsd")):
        sender = shutil.which("notify-send")
        if sender:
            try:
                subprocess.run(
                    [sender, "-a", "MP Mix Manager", "-i", "dialog-warning", label, text],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass
    elif sys.platform == "darwin":
        script = f'display notification "{text}" with title "{label}"'
        try:
            subprocess.run(
                ["osascript", "-e", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass


def status() -> dict[str, dict[str, int]]:
    return {
        role: {"running": instance_count(role), "maximum": MAX_INSTANCES}
        for role in ROLES
    }


# ------------------------------------------------------------------------------
# Exit video election: is this the last MP window?
# ------------------------------------------------------------------------------
# MP Gui and MP Liquid Player share one namespace here. The exit video plays
# once, for the last window of either role, and stays silent while another
# window of either kind is still on screen.
CLOSING_GRACE_SECONDS = 300.0


def _closing_dir() -> Path:
    path = state_dir() / "closing"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _closing_file(pid: int) -> Path:
    return _closing_dir() / f"{int(pid)}.closing"


# A suite-wide close (the tray's Close MP Mix Manager item) writes this marker
# so the windows being killed do not each try to play the movie again. It
# carries a timestamp and is ignored once stale, so a crashed tray cannot mute
# a later, ordinary window close.
SUITE_CLOSE_FILE_NAME = "suite.closing"
SUITE_CLOSE_GRACE_SECONDS = 180.0


def _suite_close_file() -> Path:
    return state_dir() / SUITE_CLOSE_FILE_NAME


def mark_suite_closing() -> None:
    """Announce that the whole suite is being closed by the tray."""
    try:
        _suite_close_file().write_text(f"{time.time():.3f}\n", encoding="utf-8")
    except OSError:
        pass


def clear_suite_closing() -> None:
    try:
        _suite_close_file().unlink()
    except OSError:
        pass


def suite_closing() -> bool:
    """True while a tray-driven close is in progress."""
    path = _suite_close_file()
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    if age > SUITE_CLOSE_GRACE_SECONDS:
        try:
            path.unlink()
        except OSError:
            pass
        return False
    return True


def _live_closing() -> set[int]:
    """Pids that already announced they are closing, dropping dead markers."""
    marked: set[int] = set()
    now = time.time()
    for path in _closing_dir().glob("*.closing"):
        try:
            pid = int(path.stem)
        except ValueError:
            continue
        if not _pid_alive(pid):
            try:
                path.unlink()
            except OSError:
                pass
            continue
        try:
            age = now - path.stat().st_mtime
        except OSError:
            age = 0.0
        if age > CLOSING_GRACE_SECONDS:
            # A marker this old belongs to a close that never finished.
            try:
                path.unlink()
            except OSError:
                pass
            continue
        marked.add(pid)
    return marked


def remaining_windows(exclude_pid: int | None = None, table: list[tuple[int, str]] | None = None) -> set[int]:
    """Every MP Gui and MP Liquid Player window still open, closing ones removed."""
    exclude = int(exclude_pid or 0)
    closing = _live_closing()
    remaining: set[int] = set()
    for role in ROLES:
        remaining |= role_pids(role, table)
    remaining -= closing
    if exclude:
        remaining.discard(exclude)
    return remaining


def is_last_window(role: str = "", pid: int | None = None) -> bool:
    """True when no other capped MP window is left, so the exit video may play.

    The caller's own pid is ignored (a host always counts itself as closing),
    and every window that already started closing is ignored too. Marking and
    counting happen under one lock, so two windows closed at the same instant
    cannot both be elected: the first still sees the second, only the second
    sees none.

    A suite close in progress (the tray's Close item) returns False: the tray
    plays the movie itself before it stops the windows, so they must not play
    it a second time.
    """
    pid = int(pid or os.getpid())
    if suite_closing():
        return False
    # One lock for the whole election, not one per role: a Gui and a Player
    # closing at the same instant must serialise their mark-then-count, or
    # both could read the other as still open and neither would play.
    try:
        with _state_lock("exit"):
            try:
                _closing_file(pid).write_text(f"{pid}\n", encoding="utf-8")
            except OSError:
                pass
            return not remaining_windows(pid)
    except (TimeoutError, OSError):
        # A broken lock must not swallow the video on the only window open.
        return not remaining_windows(pid)


def end_closing(role: str = "", pid: int | None = None) -> None:
    """Drop this process's closing marker once the exit video is over."""
    pid = int(pid or os.getpid())
    try:
        _closing_file(pid).unlink()
    except OSError:
        pass


def release_all(pid: int | None = None) -> None:
    """Drop every claim and closing marker this process owns."""
    pid = int(pid or os.getpid())
    for role in ROLES:
        release_instance(role, pid)
    end_closing("", pid)


def _role_list(role: str) -> list[str]:
    return list(ROLES) if role == "all" else [role]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mp_instance_limit.py",
        description=f"Cap MP Gui and MP Liquid Player at {MAX_INSTANCES} instances.",
    )
    parser.add_argument(
        "command",
        choices=(
            "status", "count", "check", "claim", "release", "notify", "focus",
            "last-window", "end-closing", "suite-closing", "end-suite-closing",
        ),
    )
    parser.add_argument("role", nargs="?", default="all", choices=(*ROLES, "all"))
    parser.add_argument("--pid", type=int, default=None)
    parser.add_argument(
        "--exclude-pid",
        type=int,
        default=None,
        help="leave this process out of the count (its own claim is not a sibling)",
    )
    args = parser.parse_args(argv)

    if args.command == "status":
        print(json.dumps(status(), indent=2))
        return 0

    if args.command == "count":
        if args.role == "all":
            print(json.dumps({role: instance_count(role, args.exclude_pid) for role in ROLES}))
        else:
            print(instance_count(args.role, args.exclude_pid))
        return 0

    if args.command == "check":
        blocked = any(at_limit(role) for role in _role_list(args.role))
        return 3 if blocked else 0

    if args.command == "claim":
        if not claim_instance(args.role, args.pid):
            return 3
        return 0

    if args.command == "release":
        for role in _role_list(args.role):
            release_instance(role, args.pid)
        return 0

    if args.command == "notify":
        for role in _role_list(args.role):
            notify_limit(role)
        return 0

    if args.command == "focus":
        focused = False
        for role in _role_list(args.role):
            focused = focus_existing(role) or focused
        return 0 if focused else 1

    if args.command == "last-window":
        # Exit code 0 elects this process, 1 means another window is still open.
        role = args.role if args.role in ROLES else ""
        return 0 if is_last_window(role, args.pid) else 1

    if args.command == "end-closing":
        role = args.role if args.role in ROLES else ""
        end_closing(role, args.pid)
        return 0

    if args.command == "suite-closing":
        # The tray is closing everything and plays the movie itself.
        mark_suite_closing()
        return 0

    if args.command == "end-suite-closing":
        clear_suite_closing()
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
