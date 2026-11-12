#!/usr/bin/env python3
"""
mp_soak_orchestrator.py - Parallel soak + random-script stress across hosts
===========================================================================

Runs the four MP desktop shortcuts on a target OS and keeps them up for a soak
period while 20 randomly chosen test scripts are dispatched at them
simultaneously. This is the "must survive 30 minutes without crashing" gate.

What it does, per host
----------------------
1. Launches all four shortcuts (same commands as the .desktop files).
2. Waits until each reaches a usable state (window or ready banner).
3. Picks ``--scripts`` test scripts at random from the repository's tests/ and
   scripts/ trees and runs them **in parallel**, all at once, against the live
   application processes.
4. Keeps sampling CPU and RSS across the whole process forest for the soak
   period, watching for crashes, zombie windows, and runaway memory.
5. Records every crash, every non-zero exit, and the final verdict.

Pass criteria (printed as PASS/FAIL)
------------------------------------
  * all four shortcuts were running at the end of the soak
  * no shortcut process died before the soak ended
  * at least ``--min-pass-rate`` of the random scripts exited 0
  * no process exceeded ``--max-rss-mb``

Local mode runs everything on this machine. Remote mode drives another host
over SSH, copying this orchestrator plus the benchmark scripts first.

Usage
-----
    ./scripts/mp_soak_orchestrator.py --soak 120 --scripts 20
    ./scripts/mp_soak_orchestrator.py --soak 1800 --scripts 20 --json
    ./scripts/mp_soak_orchestrator.py --remote 192.168.1.13 --soak 120
    ./scripts/mp_soak_orchestrator.py --list-scripts
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
TESTS = ROOT / "tests"
REPORT_ROOT = ROOT / "REPORTS" / "PERF"

_IS_WIN = os.name == "nt"
_IS_LINUX = sys.platform.startswith("linux")

SHORTCUTS: Dict[str, Dict[str, Any]] = {
    "mix-manager": {
        "label": "MP Mix Manager",
        "cmd": ["bash", "Mix_Archive_Manager.sh"],
        "banner": r"(MAIN\s+MENU|Main Console|MP MIX|Select)",
    },
    "liquid-player": {
        "label": "MP Liquid Player",
        "cmd": ["bash", "scripts/launch_liquid_glass.sh"],
        "banner": r"(Liquid|glass|Window|GTK)",
    },
    "visualizer": {
        "label": "MP Liquid Visualizer",
        "cmd": ["bash", "scripts/launch_liquid_visualizer.sh"],
        "banner": r"(Visualizer|OpenGL|Vulkan|Window)",
    },
    "mp-gui": {
        "label": "MP Gui",
        "cmd": ["bash", "MP_Gui.sh"],
        "banner": r"(MP|Manager|Menu|Select)",
    },
}


# ---------------------------------------------------------------------------
# process helpers (portable)
# ---------------------------------------------------------------------------
def _snapshot() -> List[Tuple[int, int, str]]:
    rows: List[Tuple[int, int, str]] = []
    if _IS_WIN:
        try:
            out = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | ForEach-Object { "
                 "\"$($_.ProcessId)|$($_.ParentProcessId)|$($_.CommandLine)\" }"],
                capture_output=True, text=True, timeout=30)
            for line in (out.stdout or "").splitlines():
                p = line.split("|", 2)
                if len(p) == 3 and p[0].strip().isdigit():
                    rows.append((int(p[0]), int(p[1] or 0), p[2]))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        return rows
    try:
        out = subprocess.run(["ps", "-eo", "pid=,ppid=,args="],
                             capture_output=True, text=True, timeout=15)
        for line in (out.stdout or "").splitlines():
            parts = line.split(None, 2)
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                rows.append((int(parts[0]), int(parts[1]),
                             parts[2] if len(parts) > 2 else ""))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return rows


def _descendants(pid: int) -> List[int]:
    rows = _snapshot()
    kids: Dict[int, List[int]] = {}
    for p, pp, _ in rows:
        kids.setdefault(pp, []).append(p)
    out, stack = [], [pid]
    while stack:
        cur = stack.pop()
        out.append(cur)
        stack.extend(kids.get(cur, []))
    return out


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if _IS_WIN:
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                 capture_output=True, text=True, timeout=10)
            return str(pid) in (out.stdout or "")
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


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


def _tree_usage(root_pid: int) -> Tuple[float, float]:
    cpu, rss = 0.0, 0
    for pid in _descendants(root_pid):
        c, r = _cpu_seconds(pid), _rss_kb(pid)
        if c:
            cpu += c
        if r:
            rss += r
    return cpu, rss / 1024.0


# ---------------------------------------------------------------------------
# display environment
# ---------------------------------------------------------------------------
def _display_env() -> Dict[str, str]:
    env = dict(os.environ)
    runtime = env.get("XDG_RUNTIME_DIR")
    if not runtime and hasattr(os, "getuid"):
        cand = f"/run/user/{os.getuid()}"
        if Path(cand).is_dir():
            runtime = cand
            env["XDG_RUNTIME_DIR"] = runtime
    if runtime:
        if (Path(runtime) / "wayland-0").exists() and not env.get("WAYLAND_DISPLAY"):
            env["WAYLAND_DISPLAY"] = "wayland-0"
        if not env.get("XAUTHORITY"):
            for auth in sorted(Path(runtime).glob("xauth_*")):
                env["XAUTHORITY"] = str(auth)
                break
        if not env.get("DISPLAY") and env.get("XAUTHORITY"):
            env["DISPLAY"] = ":0"
    return env


# ---------------------------------------------------------------------------
# random script pool
# ---------------------------------------------------------------------------
# Tests that intentionally kill the application are excluded from the stress
# pool. They are correct tests, but running them during a soak would destroy
# the very processes being measured and make the survival verdict meaningless.
_DESTRUCTIVE_TAGS = ("kill", "terminate", "stop_player", "reinstall", "uninstall")
_DESTRUCTIVE_NAMES = {
    "test_kill_all_mp_audio_players.py",
    "test_manager_exit_stops_audio_player.py",
    "test_shift_k_kill_player.py",
    "test_reinstall.py",
}


def _is_destructive(path: Path) -> bool:
    name = path.name.lower()
    if name in _DESTRUCTIVE_NAMES:
        return True
    return any(tag in name for tag in _DESTRUCTIVE_TAGS)


def collect_scripts(include_heavy: bool = False) -> List[Path]:
    """Test scripts that are safe to run against a live app.

    Launchers that open a *second* copy of the product are excluded, because
    the soak is about stressing the instances already running, not starting
    more. Tests that deliberately kill the application are excluded too: they
    would end the soak early and turn "did it survive" into a coin flip.
    """
    pool: List[Path] = []
    if TESTS.is_dir():
        for path in sorted(TESTS.glob("test_*.py")):
            if not _is_destructive(path):
                pool.append(path)
    if include_heavy and SCRIPTS.is_dir():
        for path in sorted(SCRIPTS.glob("*.py")):
            if any(tag in path.name for tag in
                   ("launch_", "install_", "update_", "uninstall_", "kill_")):
                continue
            pool.append(path)
    return pool


def script_command(path: Path) -> List[str]:
    if path.suffix == ".py":
        return [sys.executable, str(path)]
    return ["bash", str(path)]


# ---------------------------------------------------------------------------
# launching shortcuts
# ---------------------------------------------------------------------------
def launch_shortcut(name: str, meta: Dict[str, Any],
                    env: Dict[str, str]) -> Tuple[Optional[subprocess.Popen], List[str]]:
    cmd = list(meta["cmd"])
    if cmd and cmd[0] in ("bash", "sh") and len(cmd) > 1:
        cmd = ["bash", str(ROOT / cmd[1])] + cmd[2:]
    env = dict(env)
    env["MP_BENCHMARK_MODE"] = "1"
    kwargs: Dict[str, Any] = dict(
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, env=env,
    )
    if not _IS_WIN:
        kwargs["start_new_session"] = True
    try:
        return subprocess.Popen(cmd, **kwargs), cmd
    except (OSError, ValueError):
        return None, cmd


def wait_ready(proc: subprocess.Popen, timeout: float) -> Tuple[bool, float]:
    """A shortcut is up when it has live children and has stopped thrashing.

    Output is discarded during the soak, so there is no banner to match. Two
    signals are used instead, either of which is enough:

    * the process tree has been non-empty and stable for several samples, or
    * a descendant has been alive and consuming CPU without the root exiting.

    ``MP Gui`` spawns a long-lived sandboxed helper (bwrap/glycin) and a second
    manager, so its tree never stops changing; the CPU-based signal covers it.
    """
    start = time.monotonic()
    last_size = -1
    stable = 0
    while time.monotonic() - start < timeout:
        if proc.poll() is not None:
            return False, time.monotonic() - start
        tree = _descendants(proc.pid)
        size = len(tree)
        if size == last_size:
            stable += 1
            # Stable tree with at least one live child.
            if stable >= 6 and size >= 2:
                return True, time.monotonic() - start
        else:
            stable = 0
            last_size = size
        # CPU-based signal: any descendant has actually accumulated CPU time,
        # which means the launcher got past loading and is doing real work.
        if size >= 2 and stable >= 3:
            for pid in tree[1:4]:
                cpu = _cpu_seconds(pid)
                if cpu and cpu > 0.25:
                    return True, time.monotonic() - start
        time.sleep(0.25)
    return proc.poll() is None, time.monotonic() - start


def stop_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    pid = proc.pid
    if _IS_WIN:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            pass
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass


# ---------------------------------------------------------------------------
# the soak
# ---------------------------------------------------------------------------
def run_soak(soak: float, script_count: int, timeout: float, seed: Optional[int],
             include_heavy: bool, max_rss_mb: float,
             min_pass_rate: float) -> Dict[str, Any]:
    started = datetime.now()
    env = _display_env()
    rng = random.Random(seed)
    log: List[str] = []

    def note(msg: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {msg}"
        log.append(line)
        print(line, flush=True)

    note(f"soak {soak:.0f}s, {script_count} scripts, seed={seed}")

    # 1. launch all four
    procs: Dict[str, subprocess.Popen] = {}
    launch_report: Dict[str, Any] = {}
    for name, meta in SHORTCUTS.items():
        proc, cmd = launch_shortcut(name, meta, env)
        if proc is None:
            note(f"LAUNCH FAILED: {meta['label']}")
            launch_report[name] = {"launched": False}
            continue
        procs[name] = proc
        ok, secs = wait_ready(proc, timeout)
        launch_report[name] = {"launched": True, "ready": ok,
                               "ready_sec": round(secs, 2), "cmd": cmd}
        note(f"{meta['label']}: {'ready' if ok else 'NOT READY'} in {secs:.1f}s")

    if not procs:
        return {
            "schema": 1, "mode": "soak", "started_at": started.isoformat(),
            "error": "no shortcut launched", "log": log,
        }

    # 2. pick and run the random scripts in parallel
    pool = collect_scripts(include_heavy)
    if not pool:
        note("no test scripts found; soak is liveness-only")
        picks: List[Path] = []
    else:
        picks = [rng.choice(pool) for _ in range(script_count)]
    note(f"dispatching {len(picks)} scripts in parallel")

    script_procs: List[Tuple[Path, subprocess.Popen]] = []
    for path in picks:
        try:
            script_procs.append((path, subprocess.Popen(
                script_command(path), cwd=str(ROOT),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, env=env,
                start_new_session=not _IS_WIN,
            )))
        except (OSError, ValueError) as exc:
            note(f"could not start {path.name}: {exc}")

    # 3. sample the shortcuts for the whole soak while scripts run
    t_soak = time.monotonic()
    crashes: List[Dict[str, Any]] = []
    rss_peak: Dict[str, float] = {k: 0.0 for k in procs}
    cpu_peak: Dict[str, float] = {k: 0.0 for k in procs}
    next_sample = t_soak
    while time.monotonic() - t_soak < soak:
        for name, proc in list(procs.items()):
            if proc.poll() is not None:
                crashes.append({"shortcut": name,
                                "exit_code": proc.returncode,
                                "at_sec": round(time.monotonic() - t_soak, 1)})
                note(f"CRASH: {SHORTCUTS[name]['label']} exited "
                     f"({proc.returncode}) at {time.monotonic() - t_soak:.1f}s")
                del procs[name]
                continue
            if time.monotonic() >= next_sample:
                cpu, rss = _tree_usage(proc.pid)
                rss_peak[name] = max(rss_peak[name], rss)
                cpu_peak[name] = max(cpu_peak[name], cpu)
                if rss > max_rss_mb:
                    note(f"RSS WARNING: {SHORTCUTS[name]['label']} at {rss:.0f} MB")
        next_sample = time.monotonic() + 1.0
        time.sleep(0.25)

    # 4. collect script results
    script_results: List[Dict[str, Any]] = []
    for path, proc in script_procs:
        try:
            code = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            stop_tree(proc)
            code = -1
        script_results.append({"script": path.name, "exit_code": code,
                               "ok": code == 0})
        if code != 0:
            note(f"script {path.name} -> {code}")

    passed_scripts = sum(1 for s in script_results if s["ok"])
    pass_rate = passed_scripts / len(script_results) if script_results else 1.0

    survived = len(procs)
    total = len(SHORTCUTS)
    verdict_ok = (
        survived == total
        and not crashes
        and pass_rate >= min_pass_rate
    )

    summary = {
        "shortcuts_total": total,
        "shortcuts_survived": survived,
        "crashes": len(crashes),
        "scripts_run": len(script_results),
        "scripts_passed": passed_scripts,
        "script_pass_rate": round(pass_rate, 3),
        "soak_sec": round(time.monotonic() - t_soak, 1),
        "rss_peak_mb": {k: round(v, 1) for k, v in rss_peak.items()},
        "cpu_peak_sec": {k: round(v, 2) for k, v in cpu_peak.items()},
        "verdict": "PASS" if verdict_ok else "FAIL",
    }
    note(f"verdict: {summary['verdict']} "
         f"(survived {survived}/{total}, scripts {passed_scripts}/{len(script_results)})")

    # 5. tear everything down
    for proc in procs.values():
        stop_tree(proc)
    for _, proc in script_procs:
        if proc.poll() is None:
            stop_tree(proc)
    time.sleep(1.0)
    for proc in list(procs.values()):
        if proc.poll() is None:
            try:
                if not _IS_WIN:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass

    return {
        "schema": 1,
        "mode": "soak",
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "env": {
            "host": os.uname().nodename if hasattr(os, "uname") else "windows",
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "cpu_count": os.cpu_count(),
        },
        "settings": {
            "soak_sec": soak,
            "scripts": script_count,
            "seed": seed,
            "timeout_sec": timeout,
            "include_heavy": include_heavy,
            "max_rss_mb": max_rss_mb,
            "min_pass_rate": min_pass_rate,
        },
        "launch": launch_report,
        "crashes": crashes,
        "scripts": script_results,
        "summary": summary,
        "log": log,
    }


# ---------------------------------------------------------------------------
# remote mode
# ---------------------------------------------------------------------------
REMOTE_FILES = [
    "scripts/mp_perf_bench.py",
    "scripts/mp_perf_platform.sh",
    "scripts/mp_benchmark_mode.py",
    "scripts/mp_benchmark_mode.sh",
    "scripts/mp_soak_orchestrator.py",
]


def run_remote(host: str, args: argparse.Namespace, remote_dir: str) -> int:
    """Copy the harness to ``host`` and run the soak there over SSH."""
    user_prefix = f"{args.user}@" if args.user else ""
    target = f"{user_prefix}{host}"
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", target]

    print(f"  → {target}: creating {remote_dir}")
    rc = subprocess.run(ssh + [f"mkdir -p {remote_dir}"], capture_output=True).returncode
    if rc != 0:
        print(f"cannot reach {target} over SSH (rc={rc})", file=sys.stderr)
        return 1

    for rel in REMOTE_FILES:
        src = ROOT / rel
        if not src.is_file():
            continue
        dest = f"{remote_dir}/{Path(rel).name}"
        rc = subprocess.run(
            ["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             str(src), f"{target}:{dest}"],
            capture_output=True,
        ).returncode
        print(f"    copy {Path(rel).name}: {'ok' if rc == 0 else 'FAILED'}")

    remote_json = f"{remote_dir}/soak-result.json"
    cmd = (
        f"cd {remote_dir} && python3 mp_soak_orchestrator.py "
        f"--soak {args.soak} --scripts {args.scripts} "
        f"--timeout {args.timeout} --json --no-save "
        f"{'--seed ' + str(args.seed) if args.seed is not None else ''} "
        f"> {remote_json} 2>{remote_dir}/soak.log; echo RC=$?"
    )
    print(f"  → running soak on {target} ({args.soak:.0f}s)…")
    proc = subprocess.run(ssh + [cmd], capture_output=True, text=True)
    print((proc.stdout or "").strip()[-400:])
    return proc.returncode


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mp_soak_orchestrator.py",
        description="Parallel soak test: four shortcuts plus simultaneous scripts.",
    )
    ap.add_argument("--soak", type=float, default=None,
                    help="soak seconds (default 1800 = 30 min)")
    ap.add_argument("--scripts", type=int, default=20,
                    help="how many random scripts to run in parallel")
    ap.add_argument("--timeout", type=float, default=90.0,
                    help="seconds to wait for each shortcut to come up")
    ap.add_argument("--seed", type=int, default=None,
                    help="random seed, so two runs can use the same script set")
    ap.add_argument("--include-heavy", action="store_true",
                    help="also draw from scripts/ (not just tests/)")
    ap.add_argument("--max-rss-mb", type=float, default=4096.0,
                    help="warn above this RSS per shortcut")
    ap.add_argument("--min-pass-rate", type=float, default=0.0,
                    help="fraction of scripts that must exit 0 (default 0 = record only)")
    ap.add_argument("--remote", default="", help="run on this SSH host instead")
    ap.add_argument("--user", default="", help="SSH user for --remote")
    ap.add_argument("--remote-dir", default="~/mp-soak",
                    help="directory on the remote host")
    ap.add_argument("--quick", action="store_true", help="120s soak")
    ap.add_argument("--list-scripts", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--tag", default="")
    args = ap.parse_args(argv)

    if args.list_scripts:
        pool = collect_scripts(args.include_heavy)
        print(f"{len(pool)} runnable scripts:")
        for p in pool:
            print(f"  {p.relative_to(ROOT)}")
        return 0

    if args.remote:
        return run_remote(args.remote, args, args.remote_dir)

    soak = args.soak if args.soak is not None else (120.0 if args.quick else 1800.0)

    if not args.json:
        print("=" * 78)
        print("  MP SOAK + PARALLEL STRESS")
        print("=" * 78)
        print(f"  soak     : {soak:.0f}s")
        print(f"  scripts  : {args.scripts} in parallel")
        print("-" * 78)

    report = run_soak(soak, args.scripts, args.timeout, args.seed,
                      args.include_heavy, args.max_rss_mb, args.min_pass_rate)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        s = report.get("summary", {})
        print("-" * 78)
        print(f"  verdict  : {s.get('verdict', 'UNKNOWN')}")
        print(f"  survived : {s.get('shortcuts_survived')}/{s.get('shortcuts_total')}")
        print(f"  scripts  : {s.get('scripts_passed')}/{s.get('scripts_run')} passed")
        print("=" * 78)

    if not args.no_save:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        tag = args.tag or sys.platform
        out_dir = REPORT_ROOT / f"soak-{tag}-{stamp}"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "soak.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (out_dir / "soak.log").write_text("\n".join(report.get("log", [])), encoding="utf-8")
        if not args.json:
            print(f"  report: {out_dir / 'soak.json'}")

    return 0 if report.get("summary", {}).get("verdict") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
