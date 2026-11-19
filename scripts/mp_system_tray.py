#!/usr/bin/env python3
"""One system tray icon shared by MP Mix Manager, MP Gui, and MP Liquid Player.

Right-click the icon to launch any of the three, or to close the console
manager and everything that belongs to it (the GUI, the Liquid Player,
playback, and the visualizer). The icon stays while at least one of the
three is open. MP Gui and MP Liquid Player open up to four windows; a fifth
launch focuses a window that is already open.
"""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

ROLE_MANAGER = "manager"
ROLE_GUI = "gui"
ROLE_LIQUID = "liquid"

# MP Gui and MP Liquid Player are capped at four windows. The console manager
# is not: it is one window and the tray only ever focuses it.
try:
    import mp_instance_limit as instance_limit
except ImportError:  # pragma: no cover - the tray lives beside the helper
    instance_limit = None

try:
    import mp_web_service as web_service
except ImportError:  # pragma: no cover - the tray lives beside the helper
    web_service = None  # type: ignore[assignment]

try:
    import exit_video as exit_video_module
except ImportError:  # pragma: no cover - the tray lives beside the helper
    exit_video_module = None  # type: ignore[assignment]

MENU_LAUNCH_GUI = "Launch MP Gui"
MENU_LAUNCH_LIQUID = "Launch MP Liquid Player"
MENU_LAUNCH_MANAGER = "Launch MP Mix Manager"
MENU_CLOSE_ALL = "Close MP Mix Manager (Closes everything)"
# Label is filled in from the live service state: Stop while the companion is
# serving, Start while it is not. Several dashboards may be connected, which is
# why the label is re-read whenever the menu is opened.
MENU_WEB_AUDIO = "Start / Stop MP Audio Player (Web)"
# Rescans run the same code paths as the console manager's Storage menu, so the
# tray and the console cannot drift apart. The labels carry the menu path they
# mirror to keep the two discoverable from each other.
MENU_RESCAN_ARCHIVE = "Rescan Mix Archive Folders"
MENU_RESCAN_MUSIC = "Rescan Music Folders"
# The mute entry flips between these two labels, read from the live player
# state whenever the menu opens, so it always states what a click will do.
MENU_MUTE_AUDIO = "Mute MP Player Audio"
MENU_UNMUTE_AUDIO = "Unmute MP Player Audio"

MENU_ENTRIES = (
    ("launch-gui", MENU_LAUNCH_GUI),
    ("launch-liquid", MENU_LAUNCH_LIQUID),
    ("launch-manager", MENU_LAUNCH_MANAGER),
    ("web-audio", MENU_WEB_AUDIO),
    ("rescan-archive", MENU_RESCAN_ARCHIVE),
    ("rescan-music", MENU_RESCAN_MUSIC),
    ("mute-audio", MENU_MUTE_AUDIO),
    ("close-all", MENU_CLOSE_ALL),
)

# Menu entry -> the install component that owns it. An install that set up
# only one app must not offer to launch the three that are not there, so the
# menu is filtered through the stored selection.
ENTRY_COMPONENTS = {
    "launch-gui": "gui",
    "launch-liquid": "liquid",
    "launch-manager": "manager",
}


def app_selection_module():
    """The shared selection helper, or None when it cannot be imported."""
    try:
        import mp_app_selection as selection
    except ImportError:  # pragma: no cover - the module ships beside the tray
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import mp_app_selection as selection  # type: ignore[no-redef]
        except ImportError:
            return None
    return selection


def installed_components() -> set[str]:
    """The apps this install set up. All four when the file is absent."""
    selection = app_selection_module()
    if selection is None:
        return {"manager", "gui", "liquid", "visualizer"}
    try:
        return set(selection.read())
    except Exception:
        return {"manager", "gui", "liquid", "visualizer"}


def menu_entries(components: set[str] | None = None) -> tuple[tuple[str, str], ...]:
    """The menu entries that belong to the installed apps.

    Close All and the Web Companion entry stay: they are not tied to one of
    the four apps.
    """
    chosen = installed_components() if components is None else set(components)
    if {"manager", "gui", "liquid"} <= chosen:
        return MENU_ENTRIES
    return tuple(
        (ident, label)
        for ident, label in MENU_ENTRIES
        if ENTRY_COMPONENTS.get(ident) is None or ENTRY_COMPONENTS[ident] in chosen
    )

FOCUS_TITLES = {
    ROLE_GUI: "MP Mix Manager (Liquid Glass",
    ROLE_LIQUID: "MP Audio Player (Liquid Glass",
    ROLE_MANAGER: "MP Mix Archive Manager",
}

CLOSE_WINDOW_TITLES = (
    "MP Mix Archive Manager",
    "MP Mix Manager (Liquid Glass",
    "MP Audio Player (Liquid Glass",
)

LIQUID_HOSTS = (
    "MP_Audio_Player_Liquid_Glass.py",
    "MP_Audio_Player_Liquid_Glass_OpenGL.py",
    "MP_Audio_Player_Liquid_Glass_macOS.py",
    "MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py",
)

# Substrings that can possibly make `classify_process` return a role. Used as
# a cheap prefilter so an unrelated process is rejected with a few `in` tests
# instead of a full argv walk. Every name checked below must appear here, or a
# real app would stop being detected.
_CLASSIFY_MARKERS = (
    "MP_Audio_Player_Liquid_Glass",
    "MP_Gui.sh",
    "launch-liquid-glass",
    "launch_liquid_glass.sh",
    "Mix_Archive_Manager.sh",
    "mix-archive-manager",
    "launch-manager",
)

# Processes that belong to the suite but hold no capped role of their own:
# the standalone MP Liquid Visualizer, the terminal player (and its daemon),
# the MIDI companion, and the launcher wrappers. The tray signals these itself
# as well as running the kill script, so a Close All still closes them when the
# script is missing or its patterns do not match the way this host launched
# them.
SUITE_HELPER_SCRIPTS = (
    "MP_Liquid_Visualizer.py",
    "mp_liquid_visualizer_vulkan.py",
    "mp_liquid_visualizer_metal.py",
    "MP_Audio_Player.py",
    "MP_Audio_Player.sh",
    "traktor_s4_midi.py",
    "launch_liquid_visualizer",
    "launch_liquid_visualizer.sh",
    "launch-liquid-glass",
    "launch_liquid_glass.sh",
    "launch_mp_gui",
    "MP_Gui.sh",
    "mp_liquid_visualizer_vulkan",
)

# MP Gui and MP Liquid Player may have at most four windows. Launching a fifth
# focuses a window instead, which also covers the tray menu staying open.
MAX_CAPPED_INSTANCES = 4

ABSENT_QUIT_SECONDS = 8
LAUNCH_GRACE_SECONDS = 20

# The Web Companion's "Close All" button closes the three apps but must keep
# the icon up. A tray started by a shortcut (not the autostart entry) normally
# quits once nothing is open, so the companion drops this marker first and the
# poll loop below honours it until the dashboard is done with the icon.
KEEPALIVE_FILE = "mp_system_tray.keepalive"
KEEPALIVE_SECONDS = 60

_WRAPPERS = {"setsid", "nohup", "nice", "stdbuf", "timeout"}
_INTERPRETERS = {"bash", "sh", "dash", "python", "python3"}


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    cmdline: str
    environ: str = ""


class TrayLock:
    """One tray process. The lock file stays open for the life of the holder."""

    def __init__(self, path: Path):
        self.path = path
        self._fd = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            return self._acquire_windows()
        return self._acquire_posix()

    def _acquire_posix(self) -> bool:
        import fcntl

        fd = open(self.path, "a+")
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fd.close()
            return False
        fd.seek(0)
        fd.truncate()
        fd.write(str(os.getpid()))
        fd.flush()
        self._fd = fd
        return True

    def _acquire_windows(self) -> bool:
        import msvcrt

        # msvcrt locks a byte range, so the file has to contain that byte.
        if not self.path.exists() or self.path.stat().st_size < 1:
            self.path.write_bytes(b" " * 32)
        fd = open(self.path, "r+b")
        fd.seek(0)
        try:
            msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            fd.close()
            return False
        payload = str(os.getpid()).encode("ascii", "replace")[:31].ljust(32, b" ")
        fd.seek(0)
        fd.write(payload)
        fd.flush()
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        if os.name == "nt":
            self._release_windows()
            return
        import fcntl

        try:
            fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
        finally:
            self._fd.close()
            self._fd = None

    def _release_windows(self) -> None:
        import msvcrt

        fd = self._fd
        self._fd = None
        try:
            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            fd.close()

    @staticmethod
    def held(path: Path) -> bool:
        if not path.exists():
            return False
        if os.name == "nt":
            return TrayLock._held_windows(path)
        import fcntl

        fd = open(path, "a+")
        try:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            return False
        finally:
            fd.close()

    @staticmethod
    def _held_windows(path: Path) -> bool:
        import msvcrt

        if path.stat().st_size < 1:
            return False
        fd = open(path, "r+b")
        try:
            fd.seek(0)
            try:
                msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            try:
                fd.seek(0)
                msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            return False
        finally:
            fd.close()


def default_base_dir() -> Path:
    here = Path(__file__).resolve().parent
    base = here.parent
    if (base / "Mix_Archive_Manager.sh").is_file():
        return base
    if (here / "Mix_Archive_Manager.sh").is_file():
        return here
    return base


def config_dir() -> Path:
    override = os.environ.get("MP_TRAY_CONFIG_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".config" / "mix-manager"


def keepalive_path(config: Path | None = None) -> Path:
    return (config or config_dir()) / KEEPALIVE_FILE


def request_keepalive(config: Path | None = None) -> Path:
    """Ask a running tray to stay up even with no app open."""
    path = keepalive_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{time.time():.0f}\n", encoding="utf-8")
    return path


def keepalive_active(config: Path | None = None) -> bool:
    path = keepalive_path(config)
    try:
        stamp = float(path.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return False
    return (time.time() - stamp) < KEEPALIVE_SECONDS


def icon_file(base: Path) -> Path:
    png = base / "assets" / "mp_liquid_glass_icon.png"
    if png.is_file():
        return png
    return base / "assets" / "MP_Mix_Manager_Windows_Icon.ico"


def command_argv(cmdline: str) -> list[str]:
    """Drop setsid, nohup, and env so the real program is argv[0]."""
    parts = cmdline.split()
    index = 0
    while index < len(parts):
        name = Path(parts[index]).name
        if name in _WRAPPERS:
            index += 1
            while index < len(parts) and parts[index].startswith("-"):
                flag = parts[index]
                index += 1
                if flag in {"-n", "-p", "-o", "-s"} and index < len(parts):
                    index += 1
            continue
        if name == "env":
            index += 1
            while index < len(parts):
                token = parts[index]
                if token in {"-u", "--unset"}:
                    index += 2
                    continue
                if token.startswith("-u") and token != "-u":
                    index += 1
                    continue
                if token.startswith("-"):
                    index += 1
                    continue
                if "=" in token and not token.startswith("/"):
                    index += 1
                    continue
                break
            continue
        break
    return parts[index:]


def _basename(token: str) -> str:
    """The last path component, without building a Path object.

    ``Path(token).name`` was the single hottest call in the status poll: the
    classifier runs it for every process on the machine, several times each.
    ``rsplit`` gives the same answer for the polished argv tokens this sees and
    allocates nothing.
    """
    return token.rstrip("/").rsplit("/", 1)[-1]


def _basename_is(arg: str, script_name: str) -> bool:
    token = arg.strip("'\"")
    if not token or token.startswith("-") or any(ch.isspace() for ch in token):
        return False
    return _basename(token) == script_name


def invoked_script(cmdline: str, script_name: str) -> bool:
    argv = command_argv(cmdline)
    if not argv:
        return False
    if _basename_is(argv[0], script_name):
        return True
    exe = _basename(argv[0].strip("'\""))
    if exe in _INTERPRETERS or exe.startswith("python"):
        for arg in argv[1:]:
            if arg.startswith("-"):
                continue
            return _basename_is(arg, script_name)
    return False


def _hosted_by_gui(proc: ProcessInfo, by_pid: dict[int, ProcessInfo]) -> bool:
    # The GUI runs Mix_Archive_Manager.sh inside its own window. That shell
    # is not the console manager, even when its environment cannot be read.
    environ = proc.environ or process_environ(proc.pid)
    if "MP_GUI_SESSION=1" in environ.split("\n"):
        return True
    seen: set[int] = set()
    current = proc.ppid
    while current > 1 and current not in seen:
        seen.add(current)
        parent = by_pid.get(current)
        if parent is None:
            return False
        for name in LIQUID_HOSTS:
            if invoked_script(parent.cmdline, name) and "--manager" in command_argv(parent.cmdline):
                return True
        current = parent.ppid
    return False


def _dedicated_manager_konsole(cmdline: str) -> bool:
    argv = command_argv(cmdline)
    if not argv or Path(argv[0]).name != "konsole":
        return False
    return "mix-archive-manager" in cmdline or "Mix_Archive_Manager.sh" in cmdline


def classify_process(
    proc: ProcessInfo,
    by_pid: dict[int, ProcessInfo] | None = None,
    self_pids: set[int] | None = None,
) -> str | None:
    if self_pids and proc.pid in self_pids:
        return None
    cmdline = proc.cmdline
    if "mp_system_tray" in cmdline:
        return None
    # A substring guard before the real classification. The marker set is tiny
    # and most of a machine's processes match none of it, so one cheap `in`
    # per marker avoids launching `command_argv` and the argv walks below for
    # every unrelated process. This was the bulk of the Web Companion's status
    # poll cost, which runs for every phone on the network every few seconds.
    if not any(marker in cmdline for marker in _CLASSIFY_MARKERS):
        return None
    parents = by_pid or {}
    for name in LIQUID_HOSTS:
        if invoked_script(cmdline, name):
            if "--manager" in command_argv(cmdline):
                return ROLE_GUI
            return ROLE_LIQUID
    if invoked_script(cmdline, "MP_Gui.sh"):
        return ROLE_GUI
    if invoked_script(cmdline, "launch-liquid-glass") or invoked_script(cmdline, "launch_liquid_glass.sh"):
        return ROLE_LIQUID
    if (
        invoked_script(cmdline, "Mix_Archive_Manager.sh")
        or invoked_script(cmdline, "mix-archive-manager")
        or invoked_script(cmdline, "launch-manager")
    ):
        if _hosted_by_gui(proc, parents):
            return None
        return ROLE_MANAGER
    if _dedicated_manager_konsole(proc.cmdline):
        return ROLE_MANAGER
    return None


def index_processes(processes: list[ProcessInfo]) -> dict[int, ProcessInfo]:
    return {proc.pid: proc for proc in processes}


def roles_present(
    processes: list[ProcessInfo],
    self_pids: set[int] | None = None,
) -> set[str]:
    by_pid = index_processes(processes)
    found: set[str] = set()
    for proc in processes:
        role = classify_process(proc, by_pid, self_pids)
        if role:
            found.add(role)
    return found


def manager_pids(
    processes: list[ProcessInfo],
    self_pids: set[int] | None = None,
) -> list[int]:
    by_pid = index_processes(processes)
    found = []
    for proc in processes:
        if classify_process(proc, by_pid, self_pids) == ROLE_MANAGER:
            found.append(proc.pid)
    return found


def suite_pids(
    processes: list[ProcessInfo],
    self_pids: set[int] | None = None,
) -> list[int]:
    """Every pid the tray owns: the three roles plus their helper processes.

    `manager_pids()` alone was not enough for a Close All. The kill script
    covers most of this by command-line pattern, but the console manager and
    MP Gui are only matched by accident of argv, and nothing signalled them
    directly. When the kill script was missing or its patterns missed (for
    example a manager started through `bin/mix-archive-manager`, or an install
    tree in another path), those windows simply stayed open.

    Signalling the classified roles covers every window the tray can see,
    whatever launched it. `SUITE_HELPER_SCRIPTS` then adds the visualizer and
    the terminal player, which hold no capped role but are part of the suite.
    """
    by_pid = index_processes(processes)
    me = {os.getpid()}
    if self_pids:
        me |= {int(pid) for pid in self_pids}
    found = []
    for proc in processes:
        if proc.pid in me or "mp_system_tray" in proc.cmdline:
            continue
        if classify_process(proc, by_pid, me):
            found.append(proc.pid)
            continue
        for script in SUITE_HELPER_SCRIPTS:
            if invoked_script(proc.cmdline, script):
                found.append(proc.pid)
                break
    return found


def role_instance_count(
    role: str,
    processes: list[ProcessInfo] | None = None,
    self_pids: set[int] | None = None,
) -> int:
    """Live windows for MP Gui or MP Liquid Player, launch claims included.

    The shared limiter is authoritative. The process-table count is the
    fallback when the helper cannot be imported.
    """
    cap_role = {ROLE_GUI: "gui", ROLE_LIQUID: "player"}.get(role)
    if cap_role is None:
        return 0
    if instance_limit is not None:
        try:
            return instance_limit.instance_count(cap_role)
        except Exception:
            pass
    table = processes if processes is not None else read_proc_table()
    by_pid = index_processes(table)
    return sum(
        1
        for proc in table
        if (proc.pid not in (self_pids or set()))
        and classify_process(proc, by_pid, self_pids) == role
    )


def capped_role_at_limit(
    role: str,
    processes: list[ProcessInfo] | None = None,
    self_pids: set[int] | None = None,
    counts: dict[str, int] | None = None,
) -> bool:
    if role not in (ROLE_GUI, ROLE_LIQUID):
        return False
    if counts is not None:
        return counts.get(role, 0) >= MAX_CAPPED_INSTANCES
    return role_instance_count(role, processes, self_pids) >= MAX_CAPPED_INSTANCES


def launch_plan(
    role: str,
    base: Path,
    running: set[str],
    platform_name: str | None = None,
    processes: list[ProcessInfo] | None = None,
    self_pids: set[int] | None = None,
    counts: dict[str, int] | None = None,
) -> dict[str, object]:
    title = FOCUS_TITLES[role]
    # MP Gui and MP Liquid Player open new windows up to the cap. Only the
    # fifth launch focuses instead. The console manager is one window, so its
    # plan stays focus-first.
    if role in (ROLE_GUI, ROLE_LIQUID):
        if capped_role_at_limit(role, processes, self_pids, counts):
            return {
                "action": "focus",
                "title": title,
                "argv": [],
                "limit": True,
                "cap": MAX_CAPPED_INSTANCES,
            }
    elif role in running:
        return {"action": "focus", "title": title, "argv": []}
    host = platform_name or sys.platform
    if role == ROLE_GUI:
        argv = ["bash", str(base / "MP_Gui.sh")]
    elif role == ROLE_LIQUID:
        launcher = base / "scripts" / "launch-liquid-glass"
        if not launcher.is_file():
            launcher = base / "scripts" / "launch_liquid_glass.sh"
        argv = [str(launcher)]
    elif host == "darwin":
        command = base / "manager_macos.command"
        if not command.is_file():
            command = base / "Mix_Archive_Manager.sh"
        argv = ["open", "-a", "Terminal", str(command)]
    else:
        launcher = base / "bin" / "launch-manager"
        if not launcher.is_file():
            launcher = base / "bin" / "mix-archive-manager"
        argv = [str(launcher)]
    return {"action": "spawn", "title": title, "argv": argv}


def kill_all_script(base: Path) -> Path | None:
    for candidate in (
        base / "scripts" / "kill_all_mp_audio_players.sh",
        base / "kill_all_mp_audio_players.sh",
    ):
        if candidate.is_file():
            return candidate
    return None


def process_environ(pid: int) -> str:
    """One process's environment, NUL-separated as newlines.

    Read on demand rather than in :func:`read_proc_table`: only the console
    manager's classification needs it, and slurping `/proc/*/environ` for every
    process on the machine dominated the cost of a single status poll.
    """
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\x00", b"\n").decode("utf-8", "replace")


def read_proc_table() -> list[ProcessInfo]:
    proc_root = Path("/proc")
    if proc_root.is_dir():
        found: list[ProcessInfo] = []
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                raw = (entry / "cmdline").read_bytes()
                stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not raw:
                continue
            cmdline = raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()
            ppid = _ppid_from_stat(stat)
            found.append(ProcessInfo(int(entry.name), ppid, cmdline, ""))
        return found
    return _read_proc_table_ps()


def _ppid_from_stat(stat_text: str) -> int:
    marker = stat_text.rfind(")")
    if marker < 0:
        return 0
    fields = stat_text[marker + 2 :].split()
    if len(fields) < 2:
        return 0
    try:
        return int(fields[1])
    except ValueError:
        return 0


def _read_proc_table_ps() -> list[ProcessInfo]:
    try:
        result = subprocess.run(
            ["ps", "-ax", "-o", "pid=,ppid=,command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    found: list[ProcessInfo] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        found.append(ProcessInfo(pid, ppid, parts[2], ""))
    return found


def _applescript_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _focus_window_macos(title: str) -> bool:
    safe = _applescript_quote(title)
    script = (
        'tell application "System Events"\n'
        "  repeat with proc in processes\n"
        "    try\n"
        "      repeat with w in windows of proc\n"
        f'        if name of w contains "{safe}" then\n'
        "          set frontmost of proc to true\n"
        '          perform action "AXRaise" of w\n'
        '          return "ok"\n'
        "        end if\n"
        "      end repeat\n"
        "    end try\n"
        "  end repeat\n"
        "end tell\n"
        'return "miss"\n'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode == 0 and "ok" in result.stdout:
        return True
    if "Mix Archive Manager" in title:
        try:
            raised = subprocess.run(
                ["open", "-a", "Terminal"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return raised.returncode == 0
    return False


def _close_windows_macos(titles: tuple[str, ...]) -> None:
    checks = " or ".join(
        f'name of w contains "{_applescript_quote(title)}"' for title in titles
    )
    script = (
        'tell application "System Events"\n'
        "  repeat with proc in processes\n"
        "    try\n"
        "      repeat with w in windows of proc\n"
        f"        if {checks} then\n"
        "          try\n"
        '            click (first button of w whose subrole is "AXCloseButton")\n'
        "          end try\n"
        "        end if\n"
        "      end repeat\n"
        "    end try\n"
        "  end repeat\n"
        "end tell\n"
    )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return


def focus_window(title: str) -> bool:
    if sys.platform == "darwin":
        return _focus_window_macos(title)
    wmctrl = _which("wmctrl")
    if not wmctrl:
        return False
    listed = subprocess.run([wmctrl, "-l"], capture_output=True, text=True, timeout=3, check=False)
    if listed.returncode != 0:
        return False
    for line in listed.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4 or title not in parts[3]:
            continue
        activated = subprocess.run(
            [wmctrl, "-i", "-a", parts[0]],
            capture_output=True,
            timeout=3,
            check=False,
        )
        return activated.returncode == 0
    return False


def close_windows(titles: tuple[str, ...] = CLOSE_WINDOW_TITLES) -> None:
    if sys.platform == "darwin":
        _close_windows_macos(titles)
        return
    wmctrl = _which("wmctrl")
    if not wmctrl:
        return
    listed = subprocess.run([wmctrl, "-l"], capture_output=True, text=True, timeout=3, check=False)
    if listed.returncode != 0:
        return
    for line in listed.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        if any(title in parts[3] for title in titles):
            subprocess.run([wmctrl, "-i", "-c", parts[0]], capture_output=True, timeout=3, check=False)


def _which(name: str) -> str | None:
    for directory in os.environ.get("PATH", "").split(":"):
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _signal_pids(pids: list[int], sig: int) -> None:
    for pid in pids:
        if pid <= 1 or pid == os.getpid():
            continue
        try:
            os.kill(pid, sig)
        except OSError:
            continue


def _still_alive(pids: list[int]) -> list[int]:
    alive = []
    for pid in pids:
        try:
            os.kill(pid, 0)
        except OSError:
            continue
        alive.append(pid)
    return alive


def play_exit_video(base: Path) -> bool:
    """Play the closing movie before the tray tears the suite down.

    The tray's Close item terminates every MP process at once, so the windows
    never reach the shutdown path that normally plays `Exit_MP_Video.mp4`.
    Playing it here keeps the tray identical to closing the last window: the
    movie runs first, while the windows are still on screen, and the suite is
    only stopped once it ends.

    Returns True when the movie played. An unset `SHOW_EXIT_VIDEO` still means
    play; only an explicit off value suppresses it.
    """
    if exit_video_module is None:
        return False
    try:
        if not exit_video_module.exit_video_enabled(base):
            return False
        if exit_video_module.find_exit_video(base) is None:
            return False
        return bool(exit_video_module.play_exit_video("MANAGER", base, elect=False))
    except Exception:
        return False


def shutdown_suite(base: Path, play_exit: bool = True, verify: bool = True) -> list[int]:
    """Stop playback, the GUI, the Liquid Player, and the console manager.

    `play_exit` shows `Exit_MP_Video.mp4` first, because the close below is a
    hard kill that leaves the windows no chance to show it themselves. While
    the suite-close marker is set the windows also decline to play it, so the
    movie never runs twice for one close.

    `verify` re-reads the process table until every MP role is gone and
    escalates to SIGKILL on anything the first sweep missed, so "Close" cannot
    leave a window behind. The check only runs where a process is actually
    being stopped, so a suite close with nothing open is still instant.
    """
    if play_exit:
        if instance_limit is not None:
            try:
                instance_limit.mark_suite_closing()
            except Exception:
                pass
        play_exit_video(base)
    _run_kill_script(base)
    pids = suite_pids(read_proc_table(), {os.getpid()})
    _signal_pids(pids, signal.SIGTERM)
    deadline = time.time() + 1.5
    alive = _still_alive(pids)
    while alive and time.time() < deadline:
        time.sleep(0.15)
        alive = _still_alive(pids)
    if alive:
        _signal_pids(alive, signal.SIGKILL)
    close_windows()
    if verify:
        pids = _verify_suite_closed(base, pids)
    if play_exit and instance_limit is not None:
        try:
            instance_limit.clear_suite_closing()
        except Exception:
            pass
    return pids


def _run_kill_script(base: Path) -> None:
    """Run the shared kill script, if this tree ships one."""
    script = kill_all_script(base)
    if script is None:
        return
    try:
        subprocess.run(
            [str(script), "-q"],
            timeout=30,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            start_new_session=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _verify_suite_closed(base: Path, first_wave: list[int]) -> list[int]:
    """Confirm nothing MP is still running, escalating once if it is.

    This is the part that makes Close All trustworthy on every platform. The
    kill script matches command-line patterns, and those patterns miss a
    window launched through a wrapper, a renamed tree, or a different
    interpreter. Reading the tray's own role table afterwards and killing
    whatever is left closes the gap between "we asked for a close" and "the
    windows are actually gone".
    """
    report = list(first_wave)
    deadline = time.time() + 4.0
    while True:
        remaining = suite_pids(read_proc_table(), {os.getpid()})
        if not remaining:
            return report
        if time.time() >= deadline:
            break
        time.sleep(0.2)
    # Second pass: re-run the script (it also clears pid/state files) and
    # SIGKILL every role pid still visible.
    _run_kill_script(base)
    _signal_pids(remaining, signal.SIGKILL)
    report.extend(pid for pid in remaining if pid not in report)
    deadline = time.time() + 2.0
    while remaining and time.time() < deadline:
        time.sleep(0.15)
        remaining = suite_pids(read_proc_table(), {os.getpid()})
    return report


def spawn_argv(argv: list[str], base: Path, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "ab")
    env = os.environ.copy()
    env.pop("MP_SYSTEM_TRAY_CHILD", None)
    try:
        subprocess.Popen(
            argv,
            cwd=str(base),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )
    finally:
        log_file.close()


def write_desktop_entry(base: Path, applications_dir: Path) -> Path:
    applications_dir.mkdir(parents=True, exist_ok=True)
    desktop = applications_dir / "mp-mix-manager-tray.desktop"
    script = base / "scripts" / "mp_system_tray.sh"
    icon = icon_file(base)
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=MP Mix Manager\n"
        "Comment=Shared tray icon for MP Mix Manager, MP Gui, and MP Liquid Player\n"
        f"Exec={script} --stay\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Audio;\n"
        "NoDisplay=true\n"
    )
    if not desktop.is_file() or desktop.read_text(encoding="utf-8") != content:
        desktop.write_text(content, encoding="utf-8")
    return desktop


def autostart_dir() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if config_home:
        return Path(config_home) / "autostart"
    return Path.home() / ".config" / "autostart"


def write_autostart_entry(base: Path, autostart: Path) -> Path:
    autostart.mkdir(parents=True, exist_ok=True)
    desktop = autostart / "mp-mix-manager-tray.desktop"
    script = base / "scripts" / "mp_system_tray.sh"
    icon = icon_file(base)
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=MP Mix Manager\n"
        "Comment=Shared tray icon for MP Mix Manager, MP Gui, and MP Liquid Player\n"
        f"Exec={script} --stay\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Audio;\n"
        "X-GNOME-Autostart-enabled=true\n"
        "StartupNotify=false\n"
    )
    if not desktop.is_file() or desktop.read_text(encoding="utf-8") != content:
        desktop.write_text(content, encoding="utf-8")
    return desktop


def _write_kded_autoload_ini(rc_path: Path, module: str, enabled: bool) -> None:
    section = f"[Module-{module}]"
    value = "true" if enabled else "false"
    rc_path.parent.mkdir(parents=True, exist_ok=True)
    lines = rc_path.read_text(encoding="utf-8").splitlines(True) if rc_path.is_file() else []
    out: list[str] = []
    index = 0
    found = False
    while index < len(lines):
        if lines[index].strip() == section:
            found = True
            out.append(lines[index] if lines[index].endswith("\n") else lines[index] + "\n")
            index += 1
            wrote = False
            while index < len(lines) and not lines[index].startswith("["):
                stripped = lines[index].lstrip()
                if stripped.startswith("autoload=") or stripped.startswith("autoload "):
                    if not wrote:
                        out.append(f"autoload={value}\n")
                        wrote = True
                else:
                    out.append(lines[index])
                index += 1
            if not wrote:
                out.append(f"autoload={value}\n")
            continue
        out.append(lines[index])
        index += 1
    if not found:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        if out and out[-1].strip():
            out.append("\n")
        out.append(f"{section}\n")
        out.append(f"autoload={value}\n")
    rc_path.write_text("".join(out), encoding="utf-8")


def set_kded_autoload(module: str, enabled: bool, rc_path: Path | None = None) -> Path:
    path = rc_path or (Path.home() / ".config" / "kded5rc")
    _write_kded_autoload_ini(path, module, enabled)
    return path


def load_kded_module(module: str) -> bool:
    calls = (
        (
            "busctl",
            [
                "busctl",
                "--user",
                "--timeout=5",
                "call",
                "org.kde.kded6",
                "/kded",
                "org.kde.kded6",
                "loadModule",
                "s",
                module,
            ],
        ),
        (
            "qdbus",
            ["qdbus", "org.kde.kded6", "/kded", "org.kde.kded6.loadModule", module],
        ),
        (
            "busctl",
            [
                "busctl",
                "--user",
                "--timeout=5",
                "call",
                "org.kde.kded5",
                "/kded",
                "org.kde.kded5",
                "loadModule",
                "s",
                module,
            ],
        ),
    )
    for name, argv in calls:
        if not _which(name):
            continue
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                timeout=6,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return True
    return False


def enable_status_notifier_watcher(rc_paths: list[Path] | None = None) -> None:
    """KDE shows no tray icons unless statusnotifierwatcher is loaded."""
    paths = rc_paths or [
        Path.home() / ".config" / "kded5rc",
        Path.home() / ".config" / "kded6rc",
    ]
    for path in paths:
        set_kded_autoload("statusnotifierwatcher", True, path)
    load_kded_module("statusnotifierwatcher")


def wait_for_system_tray(seconds: float = 6.0) -> bool:
    from PySide6.QtWidgets import QSystemTrayIcon

    deadline = time.time() + seconds
    available = QSystemTrayIcon.isSystemTrayAvailable()
    while not available and time.time() < deadline:
        time.sleep(0.25)
        available = QSystemTrayIcon.isSystemTrayAvailable()
    return available


def stop_tray_holder(lock_path: Path, self_pid: int | None = None) -> None:
    if not lock_path.is_file():
        return
    try:
        pid = int(lock_path.read_text(encoding="utf-8").strip().split()[0])
    except (OSError, ValueError):
        return
    if pid <= 1 or pid == (self_pid or os.getpid()):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return


MENU_BAR_LABEL = "com.mplanetarian.mp-mix-manager-tray"
_MENU_ACTIONS = {ident for ident, _label in MENU_ENTRIES}


def announce_limit(role: str) -> None:
    """Tell the user four windows are already open."""
    if instance_limit is not None:
        cap_role = {ROLE_GUI: "gui", ROLE_LIQUID: "player"}.get(role)
        if cap_role is not None:
            try:
                instance_limit.notify_limit(cap_role)
                return
            except Exception:
                pass
    print(
        f"{FOCUS_TITLES[role]} is already open {MAX_CAPPED_INSTANCES} times.",
        file=sys.stderr,
    )


def _launch_role(base: Path, role: str) -> int:
    processes = read_proc_table()
    self_pids = {os.getpid()}
    running = roles_present(processes, self_pids)
    plan = launch_plan(role, base, running, processes=processes, self_pids=self_pids)
    if plan.get("limit"):
        announce_limit(role)
    if plan["action"] == "focus":
        focus_window(str(plan["title"]))
        return 0
    spawn_argv(list(plan["argv"]), base, config_dir() / "mp_system_tray.log")
    return 0


def web_audio_label() -> str:
    """Live menu label: Stop while serving, Start while not."""
    if web_service is None:
        return MENU_WEB_AUDIO
    try:
        return web_service.menu_label()
    except Exception:
        return MENU_WEB_AUDIO


def web_audio_running() -> bool:
    if web_service is None:
        return False
    try:
        return bool(web_service.companion_running())
    except Exception:
        return False


def toggle_web_audio() -> dict:
    """Start or stop the MP Web Companion. Linux only for now."""
    if web_service is None:
        return {"supported": False, "action": "unavailable"}
    try:
        return web_service.toggle_companion()
    except Exception as exc:
        return {"supported": True, "action": "error", "detail": str(exc)}


# ------------------------------------------------------------------------------
# Desktop notifications
# ------------------------------------------------------------------------------
def notify(title: str, message: str, icon: str = "audio-x-generic") -> bool:
    """Post a desktop notification through the desktop's own service.

    Linux and BSD go through `notify-send`, which is the freedesktop client for
    the notification daemon the tray icon itself lives beside; the window
    manager paints it as a normal popup. macOS uses Notification Center through
    `osascript`. The call is best effort: a missing daemon must never fail the
    rescan that just finished.
    """
    if sys.platform == "darwin":
        def _quote(value: str) -> str:
            return value.replace("\\", "\\\\").replace('"', '\\"')
        script = (
            f'display notification "{_quote(message)}" '
            f'with title "{_quote(title)}"'
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
    sender = _which("notify-send")
    if not sender:
        return False
    argv = [sender, "-a", "MP Mix Manager", "-i", icon, title, message]
    try:
        subprocess.run(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


# ------------------------------------------------------------------------------
# MP Audio Player mute
# ------------------------------------------------------------------------------
# The player answers its own IPC socket, and the same socket the headset
# buttons and the Web Companion use. The tray does not import the player (it
# pulls in PortAudio and the whole engine) so it speaks that small JSON
# protocol directly. Its runtime files live in the player's own config
# directory, which is independent of MP_TRAY_CONFIG_DIR (that one only moves
# the tray's lock and log).
def player_config_dir() -> Path:
    override = os.environ.get("MP_PLAYER_CONFIG_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".config" / "mix-manager"


def _player_file(name: str) -> Path:
    return player_config_dir() / name


def player_state_file() -> Path:
    return _player_file("mp_player_state.json")


def player_state() -> dict:
    try:
        data = json.loads(player_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def player_muted() -> bool:
    return bool(player_state().get("muted"))


def mute_menu_label() -> str:
    """Mute while audible, Unmute while muted: the label is the next action."""
    return MENU_UNMUTE_AUDIO if player_muted() else MENU_MUTE_AUDIO


def player_ipc(cmd: str, timeout: float = 2.0) -> dict | None:
    """Send one command to the running player, or None when it is not there."""
    payload = json.dumps({"cmd": cmd}).encode("utf-8")
    sock_path = _player_file("mp_audio_player.sock")
    port_file = _player_file("mp_audio_player.port")
    attempts: list[tuple] = []
    if hasattr(socket, "AF_UNIX") and sock_path.exists():
        attempts.append((socket.AF_UNIX, str(sock_path)))
    if port_file.exists():
        try:
            attempts.append((socket.AF_INET, ("127.0.0.1", int(port_file.read_text().strip()))))
        except (OSError, ValueError):
            pass
    for family, address in attempts:
        try:
            with socket.socket(family, socket.SOCK_STREAM) as conn:
                conn.settimeout(timeout)
                conn.connect(address)
                conn.sendall(payload)
                raw = conn.recv(65536).decode("utf-8", "replace")
        except (OSError, ValueError):
            continue
        if not raw:
            continue
        try:
            answer = json.loads(raw)
        except ValueError:
            continue
        if isinstance(answer, dict):
            return answer
    return None


def toggle_player_mute() -> dict:
    """Flip the player's mute, using live IPC and the state file as backup."""
    was_muted = player_muted()
    answer = player_ipc("mute")
    if answer is None:
        return {"supported": False, "running": False, "muted": was_muted}
    muted = answer.get("muted")
    return {
        "supported": True,
        "running": True,
        "muted": was_muted if muted is None else bool(muted),
    }


# ------------------------------------------------------------------------------
# Rescans
# ------------------------------------------------------------------------------
ARCHIVE_RESCAN_TIMEOUT = 1800
MUSIC_RESCAN_TIMEOUT = 3600
_ANSI_RE = None
_NUMBER_RE = None


def _strip_ansi(text: str) -> str:
    global _ANSI_RE
    if _ANSI_RE is None:
        import re

        _ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
    return _ANSI_RE.sub("", text)


def _parse_count(value) -> int:
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0


def _rescan_env() -> dict:
    env = os.environ.copy()
    env.pop("MP_SYSTEM_TRAY_CHILD", None)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


def _run_capture(argv: list[str], base: Path, timeout: float, env: dict) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            argv,
            cwd=str(base),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"Timed out after {int(timeout)} seconds."
    except OSError as exc:
        return 1, "", str(exc)
    return result.returncode, result.stdout or "", result.stderr or ""


def rescan_archive(base: Path) -> dict:
    """Rescan every configured Mix Archive folder.

    This runs the manager's own `--rescan-archive`, which is the same code the
    Storage Management menu's "Rescan Mix Archive Folders" entry uses, so the
    tray cannot disagree with the console about what a rescan does.
    """
    script = Path(base) / "Mix_Archive_Manager.sh"
    if not script.is_file():
        return {"ok": False, "detail": f"{script.name} was not found in {base}."}
    bash = _which("bash") or "/bin/bash"
    code, stdout, stderr = _run_capture(
        [bash, str(script), "--rescan-archive"],
        Path(base),
        ARCHIVE_RESCAN_TIMEOUT,
        _rescan_env(),
    )
    fields: dict[str, str] = {}
    for line in _strip_ansi(stdout).splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and key.startswith("RESCAN_"):
            fields[key[len("RESCAN_"):].lower()] = value.strip()
    if fields.get("status") != "ok":
        detail = fields.get("detail") or _last_line(stderr) or _last_line(stdout)
        return {"ok": False, "detail": detail or f"Rescan exited with code {code}."}
    return {
        "ok": True,
        "folders": _parse_count(fields.get("folders")),
        "flacs": _parse_count(fields.get("flacs")),
        "wavs": _parse_count(fields.get("wavs")),
        "pending_wavs": _parse_count(fields.get("pending_wavs")),
        "missing_tracklists": _parse_count(fields.get("missing_tracklists")),
    }


def _last_line(text: str) -> str:
    lines = [line.strip() for line in _strip_ansi(text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


def archive_rescan_message(result: dict) -> str:
    if not result.get("ok"):
        detail = result.get("detail") or "The archive rescan did not finish."
        return f"Archive rescan failed. {detail}"
    folders = int(result.get("folders") or 0)
    flacs = int(result.get("flacs") or 0)
    wavs = int(result.get("wavs") or 0)
    pending = int(result.get("pending_wavs") or 0)
    missing = int(result.get("missing_tracklists") or 0)
    parts = [
        f"{folders} archive folder{'s' if folders != 1 else ''} scanned",
        f"{flacs:,} FLACs",
        f"{wavs:,} converted WAVs",
    ]
    if pending:
        parts.append(f"{pending:,} WAVs waiting for conversion")
    if missing:
        parts.append(f"{missing:,} missing tracklists")
    return " · ".join(parts)


def music_manager_script(base: Path) -> Path | None:
    for candidate in (
        Path(base) / "scripts" / "music_folders_manager.py",
        Path(base) / "music_folders_manager.py",
    ):
        if candidate.is_file():
            return candidate
    return None


def rescan_music_folders(base: Path) -> dict:
    """Refresh the MUSIC folder playlists.

    Option 2 > Option 3 > A in the manager calls the music manager's
    `generate_advance_playlists(ALL)`; `--generate ALL` is that same call on
    the command line, so the tray and the console rebuild one playlist set.
    """
    script = music_manager_script(base)
    if script is None:
        return {"ok": False, "detail": f"music_folders_manager.py was not found in {base}."}
    python = sys.executable or _which("python3") or "python3"
    code, stdout, stderr = _run_capture(
        [python, str(script), "--generate", "ALL"],
        Path(base),
        MUSIC_RESCAN_TIMEOUT,
        _rescan_env(),
    )
    text = _strip_ansi(stdout)
    if "No music folders configured" in text:
        return {"ok": False, "detail": "No music folders are configured."}
    saved = len(_saved_playlist_lines(text))
    total = 0
    for line in text.splitlines():
        marker = "Playlist Generation Complete ("
        if marker in line:
            number = line.split(marker, 1)[1].split(" total tracks", 1)[0]
            total = _parse_count(number)
            break
    else:
        detail = _last_line(stderr) or _last_line(text)
        return {"ok": False, "detail": detail or f"Playlist refresh exited with code {code}."}
    return {
        "ok": True,
        "folders": saved,
        "tracks": total,
        "failed": text.count("Could not list"),
    }


def _saved_playlist_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if "Saved " in line and " Playlist:" in line]


def music_rescan_message(result: dict) -> str:
    if not result.get("ok"):
        detail = result.get("detail") or "The music folder rescan did not finish."
        return f"Music folder rescan failed. {detail}"
    folders = int(result.get("folders") or 0)
    tracks = int(result.get("tracks") or 0)
    failed = int(result.get("failed") or 0)
    parts = [
        f"{folders} music folder{'s' if folders != 1 else ''} refreshed",
        f"{tracks:,} tracks in ALL",
    ]
    if failed:
        parts.append(f"{failed} folder{'s' if failed != 1 else ''} unreachable")
    return " · ".join(parts)


RESCAN_ACTIONS = ("rescan-archive", "rescan-music")
RESCAN_BUSY_LABELS = {
    "rescan-archive": "Rescanning Mix Archive Folders…",
    "rescan-music": "Rescanning Music Folders…",
}
RESCAN_ICONS = {
    "rescan-archive": "folder-music",
    "rescan-music": "folder-music",
}


def run_rescan(action_id: str, base: Path) -> dict:
    """Run one rescan and return its result dict. Never raises."""
    try:
        if action_id == "rescan-archive":
            return rescan_archive(base)
        if action_id == "rescan-music":
            return rescan_music_folders(base)
    except Exception as exc:  # a rescan must never take the icon down
        return {"ok": False, "detail": str(exc)}
    return {"ok": False, "detail": f"Unknown rescan action: {action_id}"}


def rescan_message(action_id: str, result: dict) -> str:
    if action_id == "rescan-archive":
        return archive_rescan_message(result)
    return music_rescan_message(result)


def perform_menu_action(base: Path, action_id: str) -> int:
    if action_id not in _MENU_ACTIONS:
        return 2
    component = ENTRY_COMPONENTS.get(action_id)
    if component is not None and component not in installed_components():
        print(
            f"{FOCUS_TITLES[{'launch-gui': ROLE_GUI, 'launch-liquid': ROLE_LIQUID, 'launch-manager': ROLE_MANAGER}[action_id]]} "
            "was not installed by this setup.",
            file=sys.stderr,
        )
        return 1
    if action_id == "close-all":
        shutdown_suite(base)
        return 0
    if action_id == "web-audio":
        result = toggle_web_audio()
        if not result.get("supported"):
            print(
                f"Start/Stop MP Audio Player (Web) is not available on {result.get('platform', sys.platform)}.",
                file=sys.stderr,
            )
        return 0 if result.get("supported") else 1
    if action_id == "mute-audio":
        result = toggle_player_mute()
        if not result.get("supported"):
            print("MP Audio Player is not running.", file=sys.stderr)
            return 1
        return 0
    if action_id in RESCAN_ACTIONS:
        result = run_rescan(action_id, base)
        title = MENU_RESCAN_ARCHIVE if action_id == "rescan-archive" else MENU_RESCAN_MUSIC
        notify(title, rescan_message(action_id, result), RESCAN_ICONS[action_id])
        if not result.get("ok"):
            print(rescan_message(action_id, result), file=sys.stderr)
        return 0 if result.get("ok") else 1
    role = {
        "launch-gui": ROLE_GUI,
        "launch-liquid": ROLE_LIQUID,
        "launch-manager": ROLE_MANAGER,
    }[action_id]
    return _launch_role(base, role)


def format_roles(roles: set[str]) -> str:
    return " ".join(sorted(roles))


def action_label(action_id: str) -> str | None:
    """The live title of a menu entry whose label follows the app state.

    The macOS menu bar is its own process, so it asks the tray module for the
    mute title instead of keeping a second copy of the "muted?" read.
    """
    if action_id == "mute-audio":
        return mute_menu_label()
    return None


def macos_menu_arguments(base: Path, icon: Path, lock_path: Path, python: str, stay: bool) -> list[str]:
    argv = [
        "--root", str(base),
        "--icon", str(icon),
        "--lock", str(lock_path),
        "--python", python,
    ]
    if stay:
        argv.append("--stay")
    return argv


def macos_open_argv(bundle: Path, base: Path, icon: Path, lock_path: Path, python: str, stay: bool) -> list[str]:
    """Launch Services starts the agent. A raw executable gets no menu-bar slot."""
    return [
        "/usr/bin/open",
        "-g",
        str(bundle),
        "--args",
        *macos_menu_arguments(base, icon, lock_path, python, stay),
    ]


def macos_launch_agent_plist(
    bundle: Path,
    base: Path,
    icon: Path,
    lock: Path,
    python: str,
    stay: bool = True,
) -> bytes:
    arguments = macos_open_argv(bundle, base, icon, lock, python, stay)
    payload = {
        "Label": MENU_BAR_LABEL,
        "ProgramArguments": arguments,
        "RunAtLoad": True,
        "LimitLoadToSessionType": "Aqua",
        "ProcessType": "Interactive",
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "StandardOutPath": str(lock.parent / "macos_menu_bar.log"),
        "StandardErrorPath": str(lock.parent / "macos_menu_bar.log"),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML)


def write_macos_launch_agent(home: Path, plist: bytes) -> Path:
    directory = home / "Library" / "LaunchAgents"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{MENU_BAR_LABEL}.plist"
    path.write_bytes(plist)
    return path


def _swiftc_path() -> str | None:
    found = _which("swiftc")
    if found:
        return found
    xcrun = _which("xcrun")
    if not xcrun:
        return None
    try:
        result = subprocess.run(
            [xcrun, "--find", "swiftc"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = result.stdout.strip()
    if result.returncode == 0 and path:
        return path
    return None


def _macos_sdk_path() -> str | None:
    xcrun = _which("xcrun")
    if not xcrun:
        return None
    try:
        result = subprocess.run(
            [xcrun, "--show-sdk-path"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = result.stdout.strip()
    if result.returncode == 0 and path:
        return path
    return None


def compile_macos_menu_bar(base: Path, dest: Path) -> Path | None:
    src = base / "scripts" / "macos_menu_bar.swift"
    if not src.is_file():
        return None
    if dest.is_file() and os.access(dest, os.X_OK) and dest.stat().st_mtime >= src.stat().st_mtime:
        return dest
    swiftc = _swiftc_path()
    if not swiftc:
        return dest if dest.is_file() and os.access(dest, os.X_OK) else None
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".new")
    log_path = dest.parent / "macos_menu_bar.compile.log"
    argv = [swiftc]
    sdk = _macos_sdk_path()
    if sdk:
        argv.extend(["-sdk", sdk])
    argv.extend(["-o", str(tmp), str(src)])
    print("Building the macOS menu bar icon...", flush=True)
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log_path.write_text(str(exc), encoding="utf-8")
        tmp.unlink(missing_ok=True)
        return dest if dest.is_file() and os.access(dest, os.X_OK) else None
    log_path.write_text((result.stdout or "") + (result.stderr or ""), encoding="utf-8")
    if result.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        print(f"Could not compile the menu bar icon. See {log_path}", file=sys.stderr)
        return dest if dest.is_file() and os.access(dest, os.X_OK) else None
    tmp.chmod(0o755)
    os.replace(tmp, dest)
    codesign = _which("codesign")
    if codesign:
        try:
            subprocess.run(
                [codesign, "--force", "--sign", "-", str(dest)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    return dest


def macos_tray_bundle(config: Path) -> Path:
    return config / "MP Mix Manager Tray.app"


def macos_tray_bundle_plist() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "\t<key>CFBundleExecutable</key>\n"
        "\t<string>macos_menu_bar</string>\n"
        "\t<key>CFBundleIdentifier</key>\n"
        f"\t<string>{MENU_BAR_LABEL}</string>\n"
        "\t<key>CFBundleName</key>\n"
        "\t<string>MP Mix Manager</string>\n"
        "\t<key>CFBundleDisplayName</key>\n"
        "\t<string>MP Mix Manager</string>\n"
        "\t<key>CFBundlePackageType</key>\n"
        "\t<string>APPL</string>\n"
        "\t<key>CFBundleInfoDictionaryVersion</key>\n"
        "\t<string>6.0</string>\n"
        "\t<key>CFBundleShortVersionString</key>\n"
        "\t<string>0.5.4</string>\n"
        "\t<key>LSMinimumSystemVersion</key>\n"
        "\t<string>11.0</string>\n"
        "\t<key>LSUIElement</key>\n"
        "\t<true/>\n"
        "\t<key>NSHighResolutionCapable</key>\n"
        "\t<true/>\n"
        "</dict>\n"
        "</plist>\n"
    )


def stage_macos_tray_app(binary: Path, bundle: Path) -> Path:
    """A raw executable does not get a menu-bar slot. The same binary inside
    an agent app bundle does.
    """
    exe = bundle / "Contents" / "MacOS" / "macos_menu_bar"
    info = bundle / "Contents" / "Info.plist"
    if (
        exe.is_file()
        and info.is_file()
        and os.access(exe, os.X_OK)
        and exe.stat().st_mtime >= binary.stat().st_mtime
        and "LSUIElement" in info.read_text(encoding="utf-8")
    ):
        return exe
    if bundle.exists():
        shutil.rmtree(bundle)
    exe.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, exe)
    exe.chmod(0o755)
    info.write_text(macos_tray_bundle_plist(), encoding="utf-8")
    codesign = _which("codesign")
    if codesign and sys.platform == "darwin":
        try:
            subprocess.run(
                [codesign, "--force", "--sign", "-", str(bundle)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    return exe


def start_macos_launch_agent(plist: Path) -> bool:
    if sys.platform != "darwin":
        return False
    uid = os.getuid()
    target = f"gui/{uid}/{MENU_BAR_LABEL}"
    domain = f"gui/{uid}"
    try:
        subprocess.run(
            ["launchctl", "bootout", target],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        boot = subprocess.run(
            ["launchctl", "bootstrap", domain, str(plist)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        subprocess.run(
            ["launchctl", "enable", target],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if boot.returncode != 0:
            kick = subprocess.run(
                ["launchctl", "kickstart", "-k", target],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if kick.returncode != 0:
                detail = (boot.stderr or kick.stderr or "").strip()
                if detail:
                    print(detail, file=sys.stderr)
                return False
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return False
    return True


def _wait_for_lock(lock_path: Path, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if TrayLock.held(lock_path):
            return True
        time.sleep(0.1)
    return TrayLock.held(lock_path)


def _register_macos_tray_app(bundle: Path) -> None:
    if sys.platform != "darwin" or not bundle.is_dir():
        return
    tool = Path(
        "/System/Library/Frameworks/CoreServices.framework"
        "/Frameworks/LaunchServices.framework/Support/lsregister"
    )
    if not tool.is_file():
        return
    try:
        subprocess.run(
            [str(tool), "-f", str(bundle)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return


def _open_macos_tray(bundle: Path, base: Path, lock_path: Path, stay: bool) -> None:
    icon = icon_file(base)
    argv = macos_open_argv(bundle, base, icon, lock_path, sys.executable, stay)
    if sys.platform == "darwin":
        argv = ["launchctl", "asuser", str(os.getuid()), *argv]
    try:
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return


def install_macos_tray(base: Path, home: Path | None = None) -> int:
    """Show the menu-bar icon now, and again at each login."""
    base = Path(base)
    home = Path(home) if home is not None else Path.home()
    config = config_dir()
    config.mkdir(parents=True, exist_ok=True)
    binary = compile_macos_menu_bar(base, config / "macos_menu_bar")
    if binary is None:
        print("The menu bar icon needs the Xcode command line tools (swiftc).", file=sys.stderr)
        return 1
    lock_path = config / "mp_system_tray.lock"
    stop_tray_holder(lock_path)
    bundle = macos_tray_bundle(config)
    stage_macos_tray_app(binary, bundle)
    _register_macos_tray_app(bundle)
    plist = macos_launch_agent_plist(
        bundle,
        base,
        icon_file(base),
        lock_path,
        sys.executable,
        stay=True,
    )
    agent = write_macos_launch_agent(home, plist)
    started = start_macos_launch_agent(agent)
    if not _wait_for_lock(lock_path, 4.0):
        _open_macos_tray(bundle, base, lock_path, stay=True)
        started = True
    if _wait_for_lock(lock_path, 6.0):
        return 0
    if not started:
        print("The menu bar icon did not start.", file=sys.stderr)
    return 1


def run_macos_tray(base: Path, config: Path, stay: bool = False) -> int:
    lock_path = config / "mp_system_tray.lock"
    if TrayLock.held(lock_path):
        return 0
    binary = compile_macos_menu_bar(base, config / "macos_menu_bar")
    if binary is None:
        print("The menu bar icon needs the Xcode command line tools (swiftc).", file=sys.stderr)
        return 1
    bundle = macos_tray_bundle(config)
    stage_macos_tray_app(binary, bundle)
    _register_macos_tray_app(bundle)
    _open_macos_tray(bundle, base, lock_path, stay)
    if _wait_for_lock(lock_path, 6.0):
        return 0
    return 1


def applications_dir() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    if data_home:
        return Path(data_home) / "applications"
    return Path.home() / ".local" / "share" / "applications"


def install_tray(base: Path) -> int:
    if os.name == "nt":
        from install_desktop_shortcuts import run_windows_user_integration

        return run_windows_user_integration(base, "Install")
    if sys.platform == "darwin":
        return install_macos_tray(base)
    enable_status_notifier_watcher()
    write_desktop_entry(base, applications_dir())
    write_autostart_entry(base, autostart_dir())
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return 0
    lock_path = config_dir() / "mp_system_tray.lock"
    stop_tray_holder(lock_path)
    script = base / "scripts" / "mp_system_tray.sh"
    spawn_argv([str(script), "--stay"], base, config_dir() / "mp_system_tray.log")
    deadline = time.time() + 5
    while time.time() < deadline:
        if TrayLock.held(lock_path):
            return 0
        time.sleep(0.1)
    return 0


def display_server_ready(timeout: float = 30.0) -> bool:
    """Wait until the desktop's socket exists before Qt starts.

    The tray is autostarted at login, often before the Wayland compositor has
    created its socket. Qt treats a failed platform plugin as fatal: the whole
    process aborts with SIGABRT (Qt's "could not connect to display" path calls
    qAbort), which systemd records as a crash and restarts forever. Waiting for
    the socket turns that crash loop into a short delay.
    """
    if sys.platform == "darwin" or os.name == "nt":
        return True
    wayland = os.environ.get("WAYLAND_DISPLAY", "")
    display = os.environ.get("DISPLAY", "")
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    deadline = time.time() + timeout
    while True:
        if wayland:
            candidates = [wayland]
            if not wayland.startswith("/") and runtime:
                candidates.append(os.path.join(runtime, wayland))
            if any(os.path.exists(c) for c in candidates):
                return True
        if display:
            # Anything accepting connections on the X socket is good enough;
            # Qt will finish the handshake itself.
            number = display.split(":")[-1].split(".")[0]
            if number.isdigit() and os.path.exists(f"/tmp/.X11-unix/X{number}"):
                return True
        if not wayland and not display:
            return False
        if time.time() >= deadline:
            return False
        time.sleep(0.25)


def run_tray(base: Path, config: Path, stay: bool = False) -> int:
    os.environ.setdefault("QT_QPA_PLATFORMTHEME", "kde")
    enable_status_notifier_watcher()
    write_desktop_entry(base, applications_dir())
    write_autostart_entry(base, autostart_dir())

    if not display_server_ready():
        # No usable desktop yet. Say so and exit cleanly instead of letting Qt
        # abort: a clean exit is not retried as a crash, and the next login or
        # the next app launch starts the tray when the session is up.
        print("mp_system_tray: no display server yet; not starting the tray",
              file=sys.stderr)
        return 0

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QAction, QCursor, QIcon
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("MP Mix Manager")
    app.setDesktopFileName("mp-mix-manager-tray")
    tray_available = wait_for_system_tray()

    icon = QIcon(str(icon_file(base)))
    if icon.isNull():
        icon = QIcon.fromTheme("audio-x-generic")
    tray = QSystemTrayIcon(icon)
    tray.setToolTip("MP Mix Manager")
    log_path = config / "mp_system_tray.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as log_file:
        log_file.write(
            f"ready pid={os.getpid()} tray={tray_available}\n"
        )

    menu = QMenu()
    state = {"busy_until": 0.0, "absent_since": None, "closing": False}

    def remember_launch() -> None:
        state["busy_until"] = time.time() + LAUNCH_GRACE_SECONDS
        state["absent_since"] = None

    def _run_close_all(state_box: dict) -> None:
        """Play the movie and tear the suite down, without blocking the icon.

        This used to run inline in the Qt slot. `shutdown_suite()` waits for
        `Exit_MP_Video.mp4` (about 40 seconds) with `time.sleep()`, so the Qt
        event loop was frozen for the whole movie: the menu stayed painted, the
        icon stopped answering, and the desktop treated the tray as hung. On
        Plasma the frozen StatusNotifier process is what made "Close" appear to
        do nothing and leave every window open.

        The teardown runs on this worker thread. It reports back through a
        plain dict, and the main-thread timer started by `dispatch` performs
        the quit. `QTimer.singleShot` cannot be used here: a timer created on a
        thread without an event loop never fires, so the tray would never quit.
        """
        try:
            shutdown_suite(base)
        except Exception as exc:  # never leave the tray without an icon
            state_box["error"] = str(exc)
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(log_path, "a", encoding="utf-8") as log_file:
                    log_file.write(f"close-all failed: {exc}\n")
            except OSError:
                pass
        finally:
            state_box["done"] = True

    close_state = {"done": False, "error": ""}
    rescan_states: dict[str, dict] = {}
    rescan_timers: dict[str, tuple] = {}

    def _run_rescan(state_box: dict, action_id: str) -> None:
        """Scan on a worker thread so the icon keeps answering while it runs.

        A large archive or a slow SMB music share takes minutes. Running that
        in the Qt slot froze the menu exactly the way the inline Close All used
        to, so the scan runs here and the notice is posted from the main
        thread once the result is in.
        """
        try:
            state_box["result"] = run_rescan(action_id, base)
        except Exception as exc:  # never leave the entry stuck on "Rescanning…"
            state_box["result"] = {"ok": False, "detail": str(exc)}
        finally:
            state_box["done"] = True

    def start_rescan(action_id: str) -> None:
        if action_id in rescan_states and not rescan_states[action_id]["done"]:
            return
        box = {"done": False, "result": {}}
        rescan_states[action_id] = box
        action = rescan_actions.get(action_id)
        if action is not None:
            action.setEnabled(False)
            action.setText(RESCAN_BUSY_LABELS.get(action_id, "Rescanning…"))
        worker = threading.Thread(
            target=_run_rescan,
            args=(box, action_id),
            name=f"mp-tray-{action_id}",
            daemon=True,
        )
        worker.start()
        watcher = QTimer()
        watcher.setInterval(200)
        # A local QTimer with no parent is collected as soon as this slot
        # returns, and its timeout never fires again: a long scan would then
        # leave the entry stuck on "Rescanning…" with no notice. Hold the
        # timer and the worker on the action until the result is in.
        rescan_timers[action_id] = (watcher, worker)

        def _check_finished() -> None:
            if not box["done"]:
                return
            watcher.stop()
            rescan_timers.pop(action_id, None)
            result = box.get("result") or {}
            if action is not None:
                action.setEnabled(True)
                action.setText(
                    MENU_RESCAN_ARCHIVE if action_id == "rescan-archive" else MENU_RESCAN_MUSIC
                )
            title = MENU_RESCAN_ARCHIVE if action_id == "rescan-archive" else MENU_RESCAN_MUSIC
            notify(title, rescan_message(action_id, result), RESCAN_ICONS[action_id])
            try:
                with open(log_path, "a", encoding="utf-8") as log_file:
                    log_file.write(
                        f"{action_id}: "
                        f"{'ok' if result.get('ok') else 'failed'} "
                        f"{rescan_message(action_id, result)}\n"
                    )
            except OSError:
                pass

        watcher.timeout.connect(_check_finished)
        watcher.start()

    def dispatch(action_id: str) -> None:
        if state["closing"] and action_id != "web-audio":
            return
        if action_id in RESCAN_ACTIONS:
            start_rescan(action_id)
            return
        if action_id == "close-all":
            state["closing"] = True
            # Disable the menu item so a second click cannot start a second
            # movie while the first is still playing.
            close_action.setEnabled(False)
            close_action.setText("Closing MP Mix Manager…")
            worker = threading.Thread(
                target=_run_close_all, args=(close_state,), name="mp-tray-close", daemon=True
            )
            worker.start()
            # Watch the worker from the GUI thread. This timer lives in the
            # thread that owns the event loop, so it really ticks.
            watcher = QTimer()
            watcher.setInterval(200)

            def _check_closed() -> None:
                if not close_state["done"]:
                    return
                watcher.stop()
                if close_state["error"]:
                    try:
                        log_path.parent.mkdir(parents=True, exist_ok=True)
                        with open(log_path, "a", encoding="utf-8") as log_file:
                            log_file.write(f"close-all finished with: {close_state['error']}\n")
                    except OSError:
                        pass
                app.quit()

            watcher.timeout.connect(_check_closed)
            watcher.start()
            return
        if action_id == "web-audio":
            # The label is refreshed in aboutToShow, so this is always the
            # action the user just read.
            toggle_web_audio()
            return
        if action_id == "mute-audio":
            result = toggle_player_mute()
            if mute_action is not None:
                # The IPC answer is authoritative: the player writes its state
                # file from its own thread, so a read right after the toggle
                # could still return the previous value.
                if result.get("supported"):
                    mute_action.setText(
                        MENU_UNMUTE_AUDIO if result.get("muted") else MENU_MUTE_AUDIO
                    )
                else:
                    mute_action.setText(mute_menu_label())
            if not result.get("supported"):
                notify(
                    MENU_MUTE_AUDIO,
                    "MP Audio Player is not running, so there is no audio to mute.",
                    "audio-volume-muted",
                )
            return
        role = {
            "launch-gui": ROLE_GUI,
            "launch-liquid": ROLE_LIQUID,
            "launch-manager": ROLE_MANAGER,
        }[action_id]
        processes = read_proc_table()
        self_pids = {os.getpid()}
        running = roles_present(processes, self_pids)
        plan = launch_plan(role, base, running, processes=processes, self_pids=self_pids)
        remember_launch()
        if plan.get("limit"):
            announce_limit(role)
        if plan["action"] == "focus":
            focus_window(str(plan["title"]))
            return
        spawn_argv(list(plan["argv"]), base, log_path)

    web_audio_action = None
    close_action = None
    mute_action = None
    rescan_actions: dict[str, object] = {}
    for action_id, label in menu_entries():
        if action_id == "close-all":
            menu.addSeparator()
        action = QAction(label, menu)
        action.triggered.connect(lambda checked=False, ident=action_id: dispatch(ident))
        menu.addAction(action)
        if action_id == "web-audio":
            web_audio_action = action
        elif action_id == "close-all":
            close_action = action
        elif action_id == "mute-audio":
            mute_action = action
        elif action_id in RESCAN_ACTIONS:
            rescan_actions[action_id] = action

    def refresh_web_audio_label() -> None:
        # Read the state when the menu opens, so "Stop" is shown only while the
        # companion is actually serving.
        if web_audio_action is not None:
            web_audio_action.setText(web_audio_label())

    def refresh_mute_label() -> None:
        # The player may have been muted from the player window, a headset
        # button, or the Web Companion since the menu was last shown.
        if mute_action is not None:
            mute_action.setText(mute_menu_label())

    def refresh_menu_labels() -> None:
        refresh_web_audio_label()
        refresh_mute_label()

    menu.aboutToShow.connect(refresh_menu_labels)
    refresh_menu_labels()

    tray.setContextMenu(menu)

    def on_activated(reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.MiddleClick,
        ):
            menu.popup(QCursor.pos())

    tray.activated.connect(on_activated)
    tray.show()

    def poll() -> None:
        if state["closing"] or stay or time.time() < state["busy_until"]:
            return
        running = roles_present(read_proc_table(), {os.getpid()})
        if running:
            state["absent_since"] = None
            return
        # A Close All from the Web Companion closes the apps on purpose and
        # wants the icon to remain for relaunching them.
        if keepalive_active(config):
            state["absent_since"] = None
            return
        now = time.time()
        if state["absent_since"] is None:
            state["absent_since"] = now
            return
        if now - state["absent_since"] >= ABSENT_QUIT_SECONDS:
            app.quit()

    timer = QTimer()
    timer.setInterval(2000)
    timer.timeout.connect(poll)
    timer.start()

    def request_quit(*_args) -> None:
        app.quit()

    signal.signal(signal.SIGTERM, request_quit)
    signal.signal(signal.SIGINT, request_quit)
    return app.exec()


def main_darwin(args: list[str]) -> int:
    base = default_base_dir()
    config = config_dir()
    lock_path = config / "mp_system_tray.lock"
    if "--status" in args:
        return 0 if TrayLock.held(lock_path) else 1
    if "--roles" in args:
        print(format_roles(roles_present(read_proc_table(), {os.getpid()})))
        return 0
    if "--action" in args:
        index = args.index("--action")
        action_id = args[index + 1] if index + 1 < len(args) else ""
        return perform_menu_action(base, action_id)
    if "--action-label" in args:
        index = args.index("--action-label")
        action_id = args[index + 1] if index + 1 < len(args) else ""
        label = action_label(action_id)
        if label is None:
            return 1
        print(label)
        return 0
    if "--install" in args:
        return install_macos_tray(base)
    if "--keepalive" in args:
        request_keepalive(config)
        return 0
    return run_macos_tray(base, config, stay="--stay" in args)


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if os.name == "nt":
        from install_desktop_shortcuts import run_windows_tray, run_windows_user_integration

        base = default_base_dir()
        if "--status" in args:
            return run_windows_user_integration(base, "Status")
        if "--install" in args:
            return run_windows_user_integration(base, "Install")
        return run_windows_tray(base, stay="--stay" in args)
    if sys.platform == "darwin":
        return main_darwin(args)
    config = config_dir()
    lock_path = config / "mp_system_tray.lock"
    if "--status" in args:
        return 0 if TrayLock.held(lock_path) else 1
    if "--install" in args:
        return install_tray(default_base_dir())
    if "--keepalive" in args:
        request_keepalive(config)
        return 0
    if "--action" in args:
        index = args.index("--action")
        action_id = args[index + 1] if index + 1 < len(args) else ""
        return perform_menu_action(default_base_dir(), action_id)
    if "--action-label" in args:
        index = args.index("--action-label")
        action_id = args[index + 1] if index + 1 < len(args) else ""
        label = action_label(action_id)
        if label is None:
            return 1
        print(label)
        return 0
    lock = TrayLock(lock_path)
    if not lock.acquire():
        return 0
    try:
        return run_tray(default_base_dir(), config, stay="--stay" in args)
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
