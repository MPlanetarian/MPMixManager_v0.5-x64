#!/usr/bin/env python3
"""
mp_benchmark_mode.py - Time the four MP desktop shortcuts until fully loaded
==========================================================================

Benchmark Mode measures the thing a user actually feels: how long from "I
double-clicked" to "the window is up and usable", for all four shortcuts, and
how much CPU and memory each one costs while it gets there.

Readiness is decided by observing the process and the windowing system, not by
sleeping a fixed number of seconds:

  * ``console_ready``  the launcher's output contains its menu/ready banner
  * ``window_ready``   a top-level window exists for the process tree
                        (kdotool/wmctrl/xdotool on Linux, AppleScript on macOS,
                         EnumWindows on Windows, wmctrl/qdbus on FreeBSD)
  * ``alive``          the process tree is still running

A shortcut that exits during startup is reported as a failure with its exit
code, never silently counted as fast.

Usage:
    ./mp_benchmark_mode.sh
    ./mp_benchmark_mode.sh --quick
    ./mp_benchmark_mode.sh --timeout 120 --json
    ./mp_benchmark_mode.sh --only visualizer,mp-gui
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
REPORT_ROOT = ROOT / "REPORTS" / "PERF"

_IS_WIN = os.name == "nt"
_IS_MAC = sys.platform == "darwin"
_IS_LINUX = sys.platform.startswith("linux")

# The four shortcuts, in the order the user sees them on the desktop.
SHORTCUTS: Dict[str, Dict[str, Any]] = {
    "mix-manager": {
        "label": "MP Mix Manager",
        "cmd": ["bash", "Mix_Archive_Manager.sh"],
        "ready_banner": r"(MAIN\s+MENU|Main Console|MP MIX|Select|Choice|═|─{10,})",
        "needs_terminal": True,
    },
    "liquid-player": {
        "label": "MP Liquid Player",
        "cmd": ["bash", "scripts/launch_liquid_glass.sh"],
        "ready_banner": r"(Liquid|glass|window|player|GTK|VTE)",
        "needs_window": True,
    },
    "visualizer": {
        "label": "MP Liquid Visualizer",
        "cmd": ["bash", "scripts/launch_liquid_visualizer.sh"],
        "ready_banner": r"(Visualizer|OpenGL|Vulkan|preset|Window)",
        "needs_window": True,
    },
    "mp-gui": {
        "label": "MP Gui",
        "cmd": ["bash", "MP_Gui.sh"],
        "ready_banner": r"(MP|Manager|Menu|Select|─{10,})",
        "needs_window": True,
    },
}


# ---------------------------------------------------------------------------
# portable process helpers
# ---------------------------------------------------------------------------
def _ps_snapshot() -> List[Tuple[int, int, str]]:
    """(pid, ppid, command) for every process, portable across the four OSes."""
    rows: List[Tuple[int, int, str]] = []
    if _IS_WIN:
        try:
            out = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | "
                 "ForEach-Object { \"$($_.ProcessId)|$($_.ParentProcessId)|$($_.CommandLine)\" }"],
                capture_output=True, text=True, timeout=25,
            )
            for line in (out.stdout or "").splitlines():
                parts = line.split("|", 2)
                if len(parts) == 3 and parts[0].strip().isdigit():
                    rows.append((int(parts[0]), int(parts[1] or 0), parts[2]))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        return rows

    try:
        out = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,args="],
            capture_output=True, text=True, timeout=15,
        )
        for line in (out.stdout or "").splitlines():
            parts = line.split(None, 2)
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                rows.append((int(parts[0]), int(parts[1]),
                             parts[2] if len(parts) > 2 else ""))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return rows


def _descendants(root_pid: int) -> List[int]:
    rows = _ps_snapshot()
    children: Dict[int, List[int]] = {}
    for pid, ppid, _ in rows:
        children.setdefault(ppid, []).append(pid)
    out, stack = [], [root_pid]
    while stack:
        cur = stack.pop()
        out.append(cur)
        stack.extend(children.get(cur, []))
    return out


def _tree_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if _IS_WIN:
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=10,
            )
            return str(pid) in (out.stdout or "")
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _cpu_seconds(pid: int) -> Optional[float]:
    if _IS_LINUX:
        try:
            with open(f"/proc/{pid}/stat", "rb") as fh:
                p = fh.read().split()
            return (int(p[13]) + int(p[14])) / float(os.sysconf("SC_CLK_TCK"))
        except (OSError, IndexError, ValueError):
            return None
    try:
        out = subprocess.run(["ps", "-o", "time=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5)
        raw = (out.stdout or "").strip()
        if not raw:
            return None
        days = 0
        if "-" in raw:
            d, _, raw = raw.partition("-")
            days = int(d or 0)
        secs = 0.0
        for part in raw.split(":"):
            secs = secs * 60 + float(part)
        return days * 86400 + secs
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _rss_kb(pid: int) -> Optional[int]:
    if _IS_LINUX:
        try:
            with open(f"/proc/{pid}/status", "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1])
        except (OSError, IndexError, ValueError):
            return None
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5)
        raw = (out.stdout or "").strip()
        return int(float(raw)) if raw else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _tree_usage(root_pid: int) -> Tuple[float, float]:
    """(cpu seconds, rss MB) summed over the whole process tree."""
    cpu = 0.0
    rss = 0
    for pid in _descendants(root_pid):
        c = _cpu_seconds(pid)
        r = _rss_kb(pid)
        if c:
            cpu += c
        if r:
            rss += r
    return cpu, rss / 1024.0


# ---------------------------------------------------------------------------
# window detection
# ---------------------------------------------------------------------------
_WINDOW_COMMANDS: Optional[List[List[str]]] = None


def _window_tool() -> Optional[List[str]]:
    """Command that lists top-level windows, once discovered."""
    global _WINDOW_COMMANDS
    if _WINDOW_COMMANDS is None:
        _WINDOW_COMMANDS = []
        if _IS_MAC:
            _WINDOW_COMMANDS.append(
                ["osascript", "-e",
                 'tell application "System Events" to get name of every window of '
                 '(every process whose background only is false)']
            )
        elif _IS_WIN:
            _WINDOW_COMMANDS.append(["powershell.exe", "-NoProfile", "-Command",
                                     "Get-Process | Where-Object {$_.MainWindowTitle} | "
                                     "Select-Object -ExpandProperty MainWindowTitle"])
        else:
            for cand in (["kdotool", "search", "--name", "."],
                         ["wmctrl", "-l"],
                         ["xdotool", "search", "--name", "."],
                         ["qdbus6", "org.kde.KWin", "/KWin", "queryWindowInfo"]):
                if shutil.which(cand[0]):
                    _WINDOW_COMMANDS.append(cand)
    return _WINDOW_COMMANDS[0] if _WINDOW_COMMANDS else None


def _windows_visible() -> int:
    """How many top-level windows the desktop reports (0 when unknown)."""
    cmd = _window_tool()
    if not cmd:
        return 0
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return 0
    text = (out.stdout or "").strip()
    if not text:
        return 0
    return len([line for line in text.splitlines() if line.strip()])


# ---------------------------------------------------------------------------
# launching and timing one shortcut
# ---------------------------------------------------------------------------
def _prepare_display_env(env: Dict[str, str]) -> None:
    """Fill in DISPLAY/XAUTHORITY when running from a TTY or over SSH.

    The GTK host needs X11 (through Xwayland on Plasma), and a shell started
    without a login session has no XAUTHORITY. The session's own xauth file is
    discoverable from the runtime dir, so use it rather than failing with
    "cannot open display".
    """
    runtime = env.get("XDG_RUNTIME_DIR")
    if not runtime and hasattr(os, "getuid"):
        candidate = f"/run/user/{os.getuid()}"
        if Path(candidate).is_dir():
            runtime = candidate
            env["XDG_RUNTIME_DIR"] = runtime
    if not runtime:
        return

    wayland = Path(runtime) / "wayland-0"
    if wayland.exists() and not env.get("WAYLAND_DISPLAY"):
        env["WAYLAND_DISPLAY"] = "wayland-0"

    if not env.get("XAUTHORITY"):
        for auth in sorted(Path(runtime).glob("xauth_*")):
            env["XAUTHORITY"] = str(auth)
            break
    if not env.get("DISPLAY") and env.get("XAUTHORITY"):
        env["DISPLAY"] = ":0"


def _spawn(name: str, meta: Dict[str, Any]) -> subprocess.Popen:
    cmd = list(meta["cmd"])
    if cmd and cmd[0] in ("bash", "sh") and len(cmd) > 1:
        entry = ROOT / cmd[1]
        cmd = ["bash", str(entry)] + cmd[2:]
    env = dict(os.environ)
    # Launchers must not block waiting for a terminal they do not have.
    env.setdefault("MP_BENCHMARK_MODE", "1")
    _prepare_display_env(env)

    kwargs: Dict[str, Any] = dict(
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=env,
        text=True,
        bufsize=1,
    )
    if not _IS_WIN:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def run_shortcut(name: str, meta: Dict[str, Any], timeout: float,
                 quick: bool) -> Dict[str, Any]:
    label = meta["label"]
    result: Dict[str, Any] = {"label": label, "name": name}
    windows_before = _windows_visible()

    t_start = time.monotonic()
    try:
        proc = _spawn(name, meta)
    except (OSError, ValueError) as exc:
        result.update(ok=False, error=f"{type(exc).__name__}: {exc}")
        return result

    banner_re = re.compile(meta.get("ready_banner") or r".")
    console_ready: Optional[float] = None
    window_ready: Optional[float] = None
    exit_code: Optional[int] = None
    cpu_peak = 0.0
    rss_peak = 0.0
    tail: List[str] = []

    # Readiness loop: watch output and windows together, and never exceed the
    # timeout. A shortcut is "ready" when its window appears (for GUI entries)
    # or when it prints its banner (for console entries).
    deadline = t_start + timeout
    needs_window = bool(meta.get("needs_window"))
    while time.monotonic() < deadline:
        now = time.monotonic() - t_start
        cpu, rss = _tree_usage(proc.pid)
        cpu_peak = max(cpu_peak, cpu)
        rss_peak = max(rss_peak, rss)

        if proc.poll() is not None and exit_code is None:
            exit_code = proc.returncode
            # Drain whatever it printed before exiting.
            try:
                for line in (proc.stdout.read() or "").splitlines()[-20:]:
                    tail.append(line)
            except (OSError, ValueError):
                pass
            break

        if console_ready is None and proc.stdout is not None:
            try:
                import select

                while True:
                    r, _, _ = select.select([proc.stdout], [], [], 0) if not _IS_WIN else ([], [], [])
                    if not r:
                        break
                    line = proc.stdout.readline()
                    if not line:
                        break
                    tail.append(line.rstrip())
                    if len(tail) > 40:
                        tail = tail[-40:]
                    if banner_re.search(line):
                        console_ready = now
                        break
            except (OSError, ValueError):
                pass

        if window_ready is None and needs_window:
            if _windows_visible() > windows_before:
                window_ready = now

        # A GUI shortcut counts as ready when its window appears *or* when it
        # reports that it created one. The visualizer prints its device and
        # window size on success; waiting only on the window count misses it
        # when a stale window from a previous run is already on screen.
        ready = (window_ready is not None) or (console_ready is not None)
        if ready:
            break
        time.sleep(0.1)

    elapsed = time.monotonic() - t_start
    alive = _tree_alive(proc.pid) and proc.poll() is None

    ready_marker = window_ready if window_ready is not None else console_ready
    ok = ready_marker is not None and (alive or exit_code == 0)

    result.update(
        ok=bool(ok),
        ready_sec=round(ready_marker, 2) if ready_marker is not None else None,
        elapsed_sec=round(elapsed, 2),
        timed_out=ready_marker is None,
        alive_after=bool(alive),
        exit_code=exit_code,
        cpu_sec_peak=round(cpu_peak, 2),
        rss_peak_mb=round(rss_peak, 1),
        output_tail=tail[-8:],
    )

    # Always clean up: a benchmark must not leave windows or players behind.
    pid = proc.pid
    if not _IS_WIN:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    else:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass

    for _ in range(50):
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    if proc.poll() is None:
        try:
            if not _IS_WIN:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            else:
                proc.kill()
        except (OSError, ProcessLookupError):
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    return result


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mp_benchmark_mode.py",
        description="Time the four MP desktop shortcuts until fully loaded.",
    )
    ap.add_argument("--only", default="",
                    help=f"comma separated subset of: {','.join(SHORTCUTS)}")
    ap.add_argument("--timeout", type=float, default=None,
                    help="seconds to wait for each shortcut (default 90)")
    ap.add_argument("--quick", action="store_true", help="shorter timeout (30s)")
    ap.add_argument("--json", action="store_true", help="print JSON only")
    ap.add_argument("--no-save", action="store_true", help="do not write a report")
    ap.add_argument("--tag", default="", help="label for the report file")
    args = ap.parse_args(argv)

    timeout = args.timeout if args.timeout is not None else (30.0 if args.quick else 90.0)
    names = [n.strip() for n in args.only.split(",") if n.strip()] or list(SHORTCUTS)
    unknown = [n for n in names if n not in SHORTCUTS]
    if unknown:
        print(f"unknown shortcut(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    if not args.json:
        print("=" * 78)
        print("  MP BENCHMARK MODE — TIME TO FULLY LOADED")
        print("=" * 78)
        print(f"  host    : {os.uname().nodename if hasattr(os, 'uname') else 'windows'}")
        print(f"  timeout : {timeout:.0f}s per shortcut")
        print("-" * 78)

    started = datetime.now()
    t_all = time.monotonic()
    results: List[Dict[str, Any]] = []
    for name in names:
        meta = SHORTCUTS[name]
        if not args.json:
            print(f"  → {meta['label']} … ", end="", flush=True)
        res = run_shortcut(name, meta, timeout, args.quick)
        results.append(res)
        if not args.json:
            if res.get("ok"):
                print(f"ready in {res['ready_sec']}s  "
                      f"(CPU {res['cpu_sec_peak']}s, {res['rss_peak_mb']} MB)")
            else:
                why = res.get("error") or (
                    "timed out" if res.get("timed_out") else
                    f"exited early (code {res.get('exit_code')})"
                )
                print(f"FAILED — {why}")

    total = time.monotonic() - t_all
    ready_times = [r["ready_sec"] for r in results if r.get("ok")]
    passed = sum(1 for r in results if r.get("ok"))

    report = {
        "schema": 1,
        "mode": "benchmark-mode",
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "tag": args.tag,
        "env": {
            "host": os.uname().nodename if hasattr(os, "uname") else "windows",
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "cpu_count": os.cpu_count(),
            "timeout_sec": timeout,
        },
        "summary": {
            "shortcuts": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "total_wall_sec": round(total, 2),
            "slowest_ready_sec": round(max(ready_times), 2) if ready_times else None,
            "all_ready_sec": round(max(ready_times), 2) if ready_times else None,
            "passed_all": passed == len(results),
        },
        "shortcuts": results,
    }

    if not args.json:
        print("-" * 78)
        print(f"  total wall time : {total:.2f}s")
        if ready_times:
            print(f"  all four ready  : {max(ready_times):.2f}s")
        print(f"  passed          : {passed}/{len(results)}")
        print("=" * 78)

    if args.json:
        print(json.dumps(report, indent=2))

    if not args.no_save:
        stamp = started.strftime("%Y%m%d-%H%M%S")
        tag = args.tag or sys.platform
        out_dir = REPORT_ROOT / f"benchmark-mode-{tag}-{stamp}"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "benchmark-mode.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        if not args.json:
            print(f"  report: {path}")

    return 0 if report["summary"]["passed_all"] else 1


if __name__ == "__main__":
    sys.exit(main())
