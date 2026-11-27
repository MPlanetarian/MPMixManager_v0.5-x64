"""Close the MP Liquid Visualizer when the Liquid Player window exits.

The visualizer is a separate process. Launchers start it with nohup, and the
player starts it in a new session, so closing the player used to leave the
companion window on screen. On macOS that window matches the Liquid Player.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

_VISUALIZER_MARK = "MP_Liquid_Visualizer.py|mp_liquid_visualizer_vulkan.py"
_PLAYER_MARK = "MP_Audio_Player_Liquid_Glass"
# The most MP Visualizer windows that may be open at once, across the Liquid
# Player, MP Gui, the Liquid Mixer and the console manager. Sixteen covers a
# wall of screens and a few detached windows without letting a held key spawn
# processes until the machine runs out of memory.
MAX_VISUALIZER_WINDOWS = 16
_CONFIG_ENV = Path.home() / ".config" / "mix-manager" / "config.env"


def _config_value(key: str, path: Optional[Path] = None) -> str:
    """Read ``KEY=value`` from config.env without importing the manager."""
    target = Path(path) if path is not None else Path(
        os.environ.get("MP_CONFIG_ENV") or _CONFIG_ENV)
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip("'\"")
    return ""


def companion_visualizer_enabled(path: Optional[Path] = None) -> bool:
    """True when the player and MP Gui should open their background visualizer.

    ``MP_VISUALIZER_COMPANION_ENABLED=0`` in config.env turns the automatic
    companion off for both hosts, for a slow GPU or a system the user wants
    to keep quiet. The environment wins (launchers and tests use it), and any
    missing or unrecognised value keeps the historical default: enabled.
    """
    raw = (os.environ.get("MP_VISUALIZER_COMPANION_ENABLED") or "").strip()
    if not raw:
        raw = _config_value("MP_VISUALIZER_COMPANION_ENABLED", path)
    return raw.strip().lower() not in ("0", "no", "false", "off", "disabled", "none")


def _quiet_kwargs() -> dict:
    """Keep helper consoles invisible on Windows.

    These run while a glass window is closing, from a pythonw process that
    has no console of its own. Without CREATE_NO_WINDOW every PowerShell or
    taskkill call allocates a console window that flashes on the desktop.
    """
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}
    return {}


def _pgrep(pattern: str) -> list[int]:
    me = os.getpid()
    found: list[int] = []
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["powershell.exe", "-NoProfile", "-Command",
                 f'Get-CimInstance Win32_Process | Where-Object {{ $_.Name -like "python*" -and ($_.CommandLine -like "*{pattern.split("|")[0]}*" -or $_.CommandLine -like "*{pattern.split("|")[-1]}*") }} | Select-Object -ExpandProperty ProcessId'],
                stderr=subprocess.DEVNULL, text=True, timeout=4, **_quiet_kwargs(),
            )
        except (OSError, subprocess.SubprocessError):
            return found
        for line in out.split():
            if line.isdigit() and int(line) != me:
                found.append(int(line))
        return found
    if shutil.which("pgrep"):
        try:
            res = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=2)
        except (OSError, subprocess.SubprocessError):
            return found
        for line in (res.stdout or "").split():
            if line.isdigit() and int(line) != me:
                found.append(int(line))
        return found
    proc = Path("/proc")
    if not proc.is_dir():
        return found
    needles = pattern.split("|")
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "ignore")
        except OSError:
            continue
        if any(needle in cmd for needle in needles):
            found.append(int(entry.name))
    return found


def _windows_visualizer_window_pids() -> list[int]:
    """PIDs of visible MP Liquid Visualizer windows.

    The command-line probe can be slow on a cold Windows 10 machine: the CIM
    query may exceed its timeout, and then the closing host found no window to
    stop. Asking the window manager is immediate and cannot be missed.
    """
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return []
    user32 = ctypes.windll.user32
    try:
        user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user32.IsWindowVisible.restype = wintypes.BOOL
    except Exception:
        return []
    found: list[int] = []

    def _visit(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = int(user32.GetWindowTextLengthW(hwnd))
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if "MP Liquid Visualizer" not in buf.value:
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value and int(pid.value) not in found:
                found.append(int(pid.value))
        except Exception:
            pass
        return True

    try:
        callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(_visit)
        user32.EnumWindows(callback, 0)
    except Exception:
        return found
    return found


def _cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "ignore")
    except OSError:
        return ""


def _ppid(pid: int) -> int:
    try:
        text = Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    for line in text.splitlines():
        if line.startswith("PPid:"):
            parts = line.split()
            if len(parts) > 1 and parts[1].isdigit():
                return int(parts[1])
    return 0


def _is_descendant(pid: int, ancestor: int) -> bool:
    seen: set[int] = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        parent = _ppid(pid)
        if parent == ancestor:
            return True
        pid = parent
    return False


def _proc_visualizer_pids() -> Optional[list[int]]:
    """Visualizer pids straight from /proc, or None when /proc is unavailable.

    Reading ``/proc`` avoids a ``pgrep`` subprocess in the launch path. That
    matters because the Z handler runs while a caller may be patching
    ``subprocess.Popen`` (the spawn tests do), and because the cap check must
    not block for the pgrep timeout on a loaded machine.
    """
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    pids: list[int] = []
    try:
        entries = list(proc.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        cmd = _cmdline(pid)
        if not cmd:
            continue
        if "MP_Liquid_Visualizer.py" not in cmd and "mp_liquid_visualizer_vulkan.py" not in cmd:
            continue
        argv0 = cmd.split(" ", 1)[0]
        if not os.path.basename(argv0).lower().startswith("python"):
            continue
        if "pgrep" in cmd or "pkill" in cmd:
            continue
        pids.append(pid)
    return pids


def _normalised_commandline(value: str) -> str:
    return " ".join((value or "").split())


def _windows_visualizer_process_rows() -> list[dict]:
    """Python visualizer processes with their parent PIDs on Windows.

    A Windows venv ``pythonw.exe`` is a redirector: it starts the base
    interpreter with the same command line. Counting both processes made one
    visible window consume two of the sixteen slots.
    """
    if sys.platform != "win32":
        return []
    command = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -like 'python*' -and "
        "($_.CommandLine -like '*MP_Liquid_Visualizer.py*' -or "
        "$_.CommandLine -like '*mp_liquid_visualizer_vulkan.py*') } | "
        "Select-Object ProcessId,ParentProcessId,CommandLine | "
        "ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.check_output(
            ["powershell.exe", "-NoProfile", "-Command", command],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=4,
            **_quiet_kwargs(),
        )
        rows = json.loads(out or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _dedupe_windows_process_rows(rows: list[dict]) -> list[int]:
    """Keep the real interpreter and drop identical venv redirector parents."""
    by_pid: dict[int, dict] = {}
    children: dict[int, list[str]] = {}
    for row in rows:
        try:
            pid = int(row.get("ProcessId") or 0)
            parent = int(row.get("ParentProcessId") or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 1:
            continue
        by_pid[pid] = row
        children.setdefault(parent, []).append(
            _normalised_commandline(str(row.get("CommandLine") or ""))
        )
    kept = []
    for pid, row in by_pid.items():
        command = _normalised_commandline(str(row.get("CommandLine") or ""))
        if command and command in children.get(pid, []):
            continue
        kept.append(pid)
    return sorted(set(kept))


def visualizer_pids() -> list[int]:
    """PIDs of running MP Visualizer processes.

    ``_VISUALIZER_MARK`` matches any command line that merely *mentions* the
    script, which includes the shell wrapper that launches it. A wrapper is not
    a window: signalling it would only kill a launcher, and counting it made
    the shutdown logic think a second window was open. Only processes whose
    executable is a Python interpreter are kept.
    """
    me = os.getpid()
    if sys.platform == "win32":
        rows = _windows_visualizer_process_rows()
        if rows:
            pids = _dedupe_windows_process_rows(rows)
            # A window may already be visible before CIM reflects its final
            # interpreter. The HWND owns the window the user sees, so keep it.
            pids.extend(_windows_visualizer_window_pids())
            return sorted({pid for pid in pids if pid > 1 and pid != me})
        visible = _windows_visualizer_window_pids()
        if visible:
            return sorted({pid for pid in visible if pid > 1 and pid != me})
    direct = _proc_visualizer_pids()
    if direct is not None:
        return [pid for pid in direct if pid != me]
    pids = []
    for pid in _pgrep(_VISUALIZER_MARK):
        cmd = _cmdline(pid)
        if not cmd:
            pids.append(pid)          # no /proc: keep the historical behaviour
            continue
        argv0 = cmd.split(" ", 1)[0]
        name = os.path.basename(argv0).lower()
        if not name.startswith("python"):
            continue
        if "pgrep" in cmd or "pkill" in cmd:
            continue
        pids.append(pid)
    return sorted({pid for pid in pids if pid != me})


def visualizer_window_count(exclude_pid: Optional[int] = None) -> int:
    """How many visualizer windows are open, ignoring ``exclude_pid``.

    The process calling this is usually itself a visualizer, so the caller
    passes its own pid to count only the *other* windows.
    """
    return len([pid for pid in visualizer_pids() if pid != exclude_pid])


def visualizer_window_limit_reached(exclude_pid: Optional[int] = None,
                                    limit: int = MAX_VISUALIZER_WINDOWS) -> bool:
    """True when opening one more visualizer would exceed ``limit`` windows."""
    return visualizer_window_count(exclude_pid=exclude_pid) >= max(1, int(limit))


def _visualizer_is_gui_companion(pid: int) -> bool:
    """True when ``pid`` is the companion MP Gui launched.

    MP Gui starts its visualizer with ``MP_VISUALIZER_MATCH_GUI=1``; the Liquid
    Player's companion does not set it. The distinction matters at shutdown:
    closing a player used to signal every visualizer process, which took MP
    Gui's companion down with it (and the GUI's forced close did the same to
    the player's). Each host must only retire the companion it owns.
    """
    try:
        blob = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return False
    return b"MP_VISUALIZER_MATCH_GUI=1" in blob.split(b"\x00")


def _has_proc_environ() -> bool:
    """True when per-process environments can be read (Linux ``/proc``)."""
    return Path("/proc/self/environ").is_file()


def companion_visualizer_pids(for_gui: bool) -> list[int]:
    """Visualizers belonging to the MP Gui (``for_gui``) or the player.

    Without ``/proc`` the owner cannot be read, so every visualizer is
    returned for either host. That is the old behaviour, and it is the safe
    direction: a companion is still retired on macOS and Windows.
    """
    pids = visualizer_pids()
    if sys.platform == "win32":
        # The visible window is the authoritative answer. The command-line
        # probe also matches the short-lived python launcher that `start`
        # creates, but that process has no window; the window's own PID always
        # closes the thing the user is looking at.
        for pid in _windows_visualizer_window_pids():
            if pid not in pids:
                pids.append(pid)
    if not _has_proc_environ():
        return pids
    return [pid for pid in pids
            if _visualizer_is_gui_companion(pid) == bool(for_gui)]


def _other_host_window_running(marker: str, manager: Optional[bool] = None) -> bool:
    """True when another Liquid Glass window with ``marker`` is still open.

    ``manager`` distinguishes MP Gui from the MP Liquid Player: both run the
    same host module and differ only by ``--manager`` on the command line.
    """
    me = os.getpid()
    parent = os.getppid()
    for pid in _pgrep(marker):
        if pid in (me, parent) or pid <= 1:
            continue
        cmd = _cmdline(pid).lower()
        if "pgrep" in cmd or "pkill" in cmd:
            continue
        if marker.lower() not in cmd:
            continue
        if _is_descendant(pid, me):
            continue
        if manager is not None and ("--manager" in cmd) != bool(manager):
            continue
        return True
    return False


def other_liquid_player_running() -> bool:
    """True when another Liquid Glass host window is still open.

    A shell launcher whose command line contains the player script, and this
    process's own parent, are not another window. Counting those used to skip
    the visualizer shutdown.
    """
    return _other_host_window_running(_PLAYER_MARK)


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # os.kill(pid, 0) fails with WinError 87 on Windows, which made every
        # companion look dead and forced a needless taskkill after the wait.
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
        return True
    except OSError:
        return False


def _kill_signal() -> int:
    """SIGKILL where the platform has it. Windows only has SIGTERM."""
    return getattr(signal, "SIGKILL", signal.SIGTERM)


def _signal_visualizer(pid: int, sig: int) -> None:
    if pid <= 1 or pid == os.getpid():
        return
    if sys.platform == "win32":
        # taskkill /F is the Windows equivalent of SIGKILL. signal.SIGKILL
        # does not exist here, so the stop path must not read that attribute.
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_quiet_kwargs())
        return
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return
    try:
        # Signal the visualizer's own group only when it is the leader.
        # killpg(pid) fails when the process is not a group leader, and
        # killpg of a shared shell group would stop the player too.
        if pgid == pid and pgid != os.getpgrp():
            os.killpg(pgid, sig)
        else:
            os.kill(pid, sig)
    except OSError:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def stop_companion_visualizer(force: bool = False, for_gui: bool = False) -> None:
    """Close visualizer windows opened for this Liquid Glass window.

    Leave the window up when another Liquid Glass host of the *same* kind is
    still open, unless force is set.

    ``for_gui`` selects MP Gui's companion (started with
    ``MP_VISUALIZER_MATCH_GUI=1``) instead of the Liquid Player's. Only that
    host's companion is signalled. The two windows are separate processes with
    separate owners, and closing one host must not blank the other's
    background.
    """
    if not force:
        # Another MP Gui window keeps the GUI's companion alive; another player
        # window keeps the player's. A window of the other kind is not a reason
        # to keep this one up.
        if _other_host_window_running(_PLAYER_MARK, manager=for_gui):
            return
    pids = companion_visualizer_pids(for_gui)
    if not pids:
        return
    for pid in pids:
        _signal_visualizer(pid, signal.SIGTERM)
    deadline = time.monotonic() + 0.6
    while time.monotonic() < deadline:
        if not any(_alive(pid) for pid in pids):
            return
        time.sleep(0.05)
    for pid in pids:
        if _alive(pid):
            _signal_visualizer(pid, _kill_signal())


def stop_companion_for_mode(mode: Optional[str], player_gui: bool = True) -> None:
    """Close the companion window belonging to the host that is closing.

    The host calls this as soon as its own window is destroyed, which is
    *before* the exit movie plays. The companion used to be retired only after
    the movie, so MP Gui's background window sat alone on the desktop for the
    whole clip — exactly the "the visualizer is still there" report.

    ``MP Gui`` also stops playback, because the menu is the thing that started
    it. The Liquid Player and the tracklist viewer stop their own companion
    only.

    The stop is *not* forced. Up to four MP Gui windows share one companion
    (``MP_Gui.sh`` only spawns a second when none is running), so closing one
    of several must leave the background of the others alone —
    ``stop_companion_visualizer`` already skips the kill while another window
    of the same kind is up, and this host is excluded from that count.
    Forcing it here blanked the background of every other MP Gui window.
    """
    if (mode or "").upper() == "MANAGER":
        stop_companion_playback()
        stop_companion_visualizer(for_gui=True)
    elif player_gui:
        stop_companion_visualizer()


def _playback_engine_pids() -> list[int]:
    """MP Audio Player engines. Liquid Glass host windows are not engines."""
    me = os.getpid()
    found: list[int] = []
    for pid in _pgrep("MP_Audio_Player.py"):
        if pid == me or pid <= 1:
            continue
        cmd = _cmdline(pid)
        if "Liquid_Glass" in cmd or "liquid_glass" in cmd:
            continue
        if "MP_Audio_Player.py" not in cmd and "mp_audio_player.py" not in cmd.lower():
            continue
        found.append(pid)
    return found


def _send_player_stop() -> None:
    """Ask a running player daemon to stop before its process is signalled."""
    config = Path.home() / ".config" / "mix-manager"
    sock_path = config / "mp_audio_player.sock"
    port_path = config / "mp_audio_player.port"
    try:
        import socket
        if sock_path.is_socket() or sock_path.exists():
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(0.4)
                sock.connect(str(sock_path))
                sock.sendall(b'{"cmd":"stop"}')
                try:
                    sock.recv(256)
                except OSError:
                    pass
                sock.close()
                return
            except OSError:
                pass
        port = 0
        try:
            port = int(port_path.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            port = 0
        if port > 0:
            sock = socket.create_connection(("127.0.0.1", port), 0.4)
            sock.settimeout(0.4)
            sock.sendall(b'{"cmd":"stop"}')
            try:
                sock.recv(256)
            except OSError:
                pass
            sock.close()
    except OSError:
        pass


def stop_companion_playback() -> None:
    """Stop music started from the Mix Manager GUI. The glass host stays up."""
    stop_player_audio()


def stop_player_audio() -> None:
    """Silence the audio engine and clear the daemon, leaving windows alone.

    The Liquid Player host owns a child ``MP_Audio_Player.py`` that runs the
    daemon, and the daemon keeps its output stream open after the window that
    started it is gone. Closing the last MP Liquid Player therefore left the
    mix playing to an empty desktop. The exit movie is silent, so there is
    nothing to keep the audio device open for once the glass has gone.

    ``stop`` is the daemon's own shutdown command: it saves the resume point,
    closes the stream and retires the process. The worker pids are signalled
    afterwards as a backstop, for a daemon that is wedged or was started by an
    older build that did not answer ``stop``.
    """
    _send_player_stop()
    pids = _playback_engine_pids()
    for pid in pids:
        _signal_visualizer(pid, signal.SIGTERM)
    deadline = time.monotonic() + 0.45
    while time.monotonic() < deadline:
        if not any(_alive(pid) for pid in pids):
            return
        time.sleep(0.05)
    for pid in pids:
        if _alive(pid):
            _signal_visualizer(pid, _kill_signal())
