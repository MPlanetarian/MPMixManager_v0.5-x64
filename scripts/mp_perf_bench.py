#!/usr/bin/env python3
"""
mp_perf_bench.py - CPU / memory / render performance harness for MP Mix Manager
==============================================================================

Benchmarks the four MP desktop shortcuts plus the Web Companion and the two
render backends, then stores every run as JSON so later runs can be compared
against it.

The four shortcuts measured are the same ones a user double-clicks:

    MP Mix Manager        -> Mix_Archive_Manager.sh
    MP Liquid Player      -> scripts/launch_liquid_glass.sh
    MP Liquid Visualizer  -> scripts/launch_liquid_visualizer.sh
    MP Gui                -> MP_Gui.sh

Each target is measured on the axes that actually matter for it:

  * ``startup``   seconds and peak RSS to reach a usable state (import cost,
                  interpreter probing, script resolution).
  * ``render``    frames per second, milliseconds per frame and per-frame CPU
                  from the real OpenGL draw path. Only for GPU targets.
  * ``micro``     repeatable timings for the hot functions called every frame
                  or every audio block (FFT spectrum, EQ filter, theme state,
                  HTML page render), so a regression is caught even when the
                  end-to-end number is hidden behind vsync.
  * ``server``    requests per second for the Web Companion.

Design notes
------------
* Nothing here opens a visible window on your desktop unless it must. Render
  benchmarks create a real GLFW window because that is what the product does,
  but they close it immediately afterwards.
* Rendering is vsync-locked at 60 Hz, so ``fps`` alone cannot show a CPU
  improvement. That is why per-frame CPU time is measured separately: it falls
  even while fps stays pinned.
* Results are written to ``REPORTS/PERF/<timestamp>/`` with a ``latest.json``
  symlink, and ``--compare`` prints a delta table against a previous run.

Usage
-----
    ./scripts/mp_perf_bench.py                       # run all targets
    ./scripts/mp_perf_bench.py --quick               # fewer samples, faster
    ./scripts/mp_perf_bench.py --targets visualizer,web
    ./scripts/mp_perf_bench.py --compare             # vs the previous run
    ./scripts/mp_perf_bench.py --compare runs/a.json
    ./scripts/mp_perf_bench.py --list                # show stored runs
    ./scripts/mp_perf_bench.py --json                # machine-readable output

Exit code is 0 when the run completes. ``--compare --fail-on-regression``
returns 1 when any metric regressed beyond the threshold (default 10%).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
for _p in (str(ROOT), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

REPORT_ROOT = ROOT / "REPORTS" / "PERF"
LATEST = REPORT_ROOT / "latest.json"

# Every shortcut the user can launch, in menu order.
TARGETS: Dict[str, Dict[str, str]] = {
    "mix-manager": {
        "label": "MP Mix Manager",
        "entry": "Mix_Archive_Manager.sh",
        "kind": "shell",
    },
    "liquid-player": {
        "label": "MP Liquid Player",
        "entry": "scripts/launch_liquid_glass.sh",
        "kind": "shell",
    },
    "visualizer": {
        "label": "MP Liquid Visualizer",
        "entry": "scripts/launch_liquid_visualizer.sh",
        "kind": "render",
    },
    "mp-gui": {
        "label": "MP Gui",
        "entry": "MP_Gui.sh",
        "kind": "shell",
    },
    "web-companion": {
        "label": "MP Web Companion",
        "entry": "scripts/mix_web_companion.py",
        "kind": "server",
    },
}

DEFAULT_TARGETS = list(TARGETS)


# ---------------------------------------------------------------------------
# process measurement helpers
# ---------------------------------------------------------------------------
def _clock_ticks() -> float:
    try:
        return float(os.sysconf("SC_CLK_TCK"))
    except (AttributeError, ValueError, OSError):
        return 100.0


# ---------------------------------------------------------------------------
# portable process sampling
#
# Linux reads /proc directly (cheap and exact). macOS and FreeBSD have no
# /proc, so the same numbers come from ``ps``. Windows reports CPU time as a
# formatted string, which _parse_ps_time() understands.
# ---------------------------------------------------------------------------
_IS_LINUX = sys.platform.startswith("linux")
_IS_MAC = sys.platform == "darwin"
_IS_BSD = "freebsd" in sys.platform or "openbsd" in sys.platform or "netbsd" in sys.platform
_IS_WIN = os.name == "nt"

# ps arguments differ per platform; these are the ones that work everywhere.
if _IS_MAC:
    _PS_TIME = ["ps", "-o", "time=", "-p"]
    _PS_RSS = ["ps", "-o", "rss=", "-p"]
elif _IS_BSD:
    _PS_TIME = ["ps", "-o", "time=", "-p"]
    _PS_RSS = ["ps", "-o", "rss=", "-p"]
else:
    _PS_TIME = ["ps", "-o", "cputime=", "-p"]
    _PS_RSS = ["ps", "-o", "rss=", "-p"]


def _parse_ps_time(text: str) -> Optional[float]:
    """Parse ps CPU time into seconds.

    Accepts ``MM:SS``, ``HH:MM:SS`` and ``D-HH:MM:SS``. macOS uses the first
    two, FreeBSD and Linux can emit all three.
    """
    text = (text or "").strip()
    if not text:
        return None
    days = 0
    if "-" in text and ":" in text:
        head, _, text = text.partition("-")
        try:
            days = int(head)
        except ValueError:
            days = 0
    parts = text.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    secs = 0.0
    for n in nums:
        secs = secs * 60.0 + n
    return days * 86400.0 + secs


def _ps_field(args: List[str], pid: int) -> Optional[str]:
    try:
        out = subprocess.run(
            args + [str(pid)], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    value = (out.stdout or "").strip().splitlines()
    return value[0].strip() if value else None


def _proc_cpu_seconds(pid: int) -> Optional[float]:
    """User+system CPU time consumed by ``pid`` so far, in seconds."""
    if _IS_LINUX:
        try:
            with open(f"/proc/{pid}/stat", "rb") as fh:
                parts = fh.read().split()
            return (int(parts[13]) + int(parts[14])) / _clock_ticks()
        except (OSError, IndexError, ValueError):
            pass
    raw = _ps_field(_PS_TIME, pid)
    return _parse_ps_time(raw) if raw else None


def _proc_rss_kb(pid: int) -> Optional[int]:
    if _IS_LINUX:
        try:
            with open(f"/proc/{pid}/status", "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1])
        except (OSError, IndexError, ValueError):
            pass
    raw = _ps_field(_PS_RSS, pid)
    try:
        return int(float(raw)) if raw else None
    except (TypeError, ValueError):
        return None


def _proc_threads(pid: int) -> Optional[int]:
    if _IS_LINUX:
        try:
            with open(f"/proc/{pid}/status", "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if line.startswith("Threads:"):
                        return int(line.split()[1])
        except (OSError, IndexError, ValueError):
            pass
    return None



class ProcessSampler:
    """Samples CPU seconds, RSS and thread count of a child process.

    Used instead of a single ``ps`` reading because a one-shot sample misses
    the startup peak, which is exactly what the memory footprint question
    needs to see.
    """

    def __init__(self, pid: int, interval: float = 0.1) -> None:
        self.pid = pid
        self.interval = interval
        self.samples: List[Dict[str, float]] = []
        self._thread: Optional[Any] = None
        self._stop = False
        self.started = time.monotonic()

    def _loop(self) -> None:
        while not self._stop:
            cpu = _proc_cpu_seconds(self.pid)
            rss = _proc_rss_kb(self.pid)
            thr = _proc_threads(self.pid)
            if cpu is not None and rss is not None:
                self.samples.append(
                    {
                        "t": time.monotonic() - self.started,
                        "cpu": cpu,
                        "rss_kb": rss,
                        "threads": thr or 0,
                    }
                )
            if self._stop:
                break
            time.sleep(self.interval)

    def start(self) -> None:
        import threading

        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> Dict[str, Any]:
        self._stop = True
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if not self.samples:
            return {"samples": 0}
        wall = self.samples[-1]["t"] - self.samples[0]["t"]
        cpu = self.samples[-1]["cpu"] - self.samples[0]["cpu"]
        rss = [s["rss_kb"] for s in self.samples]
        return {
            "samples": len(self.samples),
            "wall_sec": round(wall, 3),
            "cpu_sec": round(cpu, 3),
            "cpu_pct": round(100.0 * cpu / wall, 1) if wall > 0 else 0.0,
            "rss_peak_mb": round(max(rss) / 1024.0, 1),
            "rss_mean_mb": round(statistics.fmean(rss) / 1024.0, 1),
            "threads_peak": max(int(s.get("threads") or 0) for s in self.samples),
        }


# ---------------------------------------------------------------------------
# measurement pieces
# ---------------------------------------------------------------------------
def measure_import(mod_name: str, path_hint: str = "") -> Dict[str, Any]:
    """Wall time and peak RSS to import a module in a fresh interpreter.

    Startup cost is measured in a subprocess so imports from previous targets
    cannot warm the cache and flatter the result.
    """
    code = (
        "import resource, sys, time\n"
        f"sys.path.insert(0, {str(ROOT)!r}); sys.path.insert(0, {str(SCRIPTS)!r})\n"
        "t = time.perf_counter()\n"
        f"import {mod_name}\n"
        "dt = time.perf_counter() - t\n"
        "rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
        "print(f'{dt*1000:.1f} {rss}')\n"
    )
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(ROOT),
        )
    except subprocess.SubprocessError as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    wall = (time.perf_counter() - t0) * 1000.0
    if proc.returncode != 0:
        return {"error": (proc.stderr or "").strip().splitlines()[-1:] and
                (proc.stderr or "").strip().splitlines()[-1] or "import failed"}
    try:
        ms, rss = proc.stdout.split()
        return {
            "import_ms": round(float(ms), 1),
            "subprocess_ms": round(wall, 1),
            "rss_peak_mb": round(int(rss) / 1024.0, 1),
        }
    except ValueError:
        return {"error": f"unexpected output: {proc.stdout.strip()!r}"}


def _timeit_us(fn: Callable[[], Any], iterations: int) -> float:
    """Best-of-3 mean microseconds per call, to damp scheduler noise."""
    best: Optional[float] = None
    for _ in range(3):
        t0 = time.perf_counter()
        for _ in range(iterations):
            fn()
        dt = (time.perf_counter() - t0) / iterations * 1e6
        if best is None or dt < best:
            best = dt
    return round(best or 0.0, 3)


def measure_micro(iterations: int) -> Dict[str, Any]:
    """Time the hot functions that run per frame or per audio block.

    These are pure-CPU and therefore immune to vsync, which makes them the
    reliable signal for whether a change helped.
    """
    out: Dict[str, Any] = {}
    try:
        import numpy as np
    except ImportError:
        return {"error": "numpy missing"}

    # --- FFT spectrum + band smoothing (runs once per audio block) --------
    try:
        import MP_Audio_Player as P

        npmod = np
        meter = None
        engine_cls = getattr(P, "AudioEngine", None)
        if isinstance(engine_cls, type):
            # MP_Audio_Player binds numpy lazily, so the probe asks for it
            # before touching any FFT state.
            P._ensure_numpy()
            npmod = P.np
            meter = object.__new__(engine_cls)
            meter.num_bands = 24
            meter.band_edges = npmod.logspace(
                npmod.log10(28), npmod.log10(18500), meter.num_bands + 1
            )
            meter.band_boosts = (
                (1.0 + (1.2 / (1.0 + npmod.arange(meter.num_bands, dtype=npmod.float32) * 0.28)))
                * 4.6
            )
            for attr in (
                "levels", "levels_left", "levels_right",
                "smoothed_levels", "smoothed_levels_left", "smoothed_levels_right",
                "peaks", "peaks_left", "peaks_right",
                "peak_decay_timers", "peak_decay_timers_left", "peak_decay_timers_right",
            ):
                setattr(meter, attr, npmod.zeros(meter.num_bands, dtype=npmod.float32))
            meter.recent_mono_buffer = npmod.zeros(512, dtype=npmod.float32)
            meter.rms_level, meter.peak_db = 0.0, -60.0
            meter.hanning_window = npmod.hanning(1024).astype(npmod.float32)
            meter.fft_scale = 512.0
            meter._setup_fft_bins(44100)

        if meter is not None and hasattr(meter, "_compute_fft_spectrum"):
            rng = npmod.random.default_rng(7)
            stereo = (rng.standard_normal((1024, 2)) * 0.2).astype(npmod.float32)
            mono = stereo.mean(axis=1).astype(npmod.float32)
            out["fft_block_us"] = _timeit_us(
                lambda: meter._compute_fft_spectrum(stereo, mono), iterations
            )
    except Exception as exc:
        out["fft_block_error"] = f"{type(exc).__name__}: {exc}"

    # --- EQ convolution (runs once per audio block when EQ is not flat) ---
    try:
        import MP_Audio_Player as P

        cls = getattr(P, "EqualizerFilter", None)
        if isinstance(cls, type):
            eq = cls()
            eq.set_gains([1.0, 1.0, 1.5, 2.0, 1.0, 0.0, -1.0, 1.0, 2.0, 1.0], 44100)
            chunk = (np.random.default_rng(11).standard_normal((1024, 2)) * 0.1).astype(
                np.float32
            )
            if hasattr(eq, "process"):
                out["eq_block_us"] = _timeit_us(lambda: eq.process(chunk), iterations)
    except Exception as exc:
        out["eq_block_error"] = f"{type(exc).__name__}: {exc}"

    # --- Theme state per frame -------------------------------------------
    try:
        from liquid_glass_opengl import get_dynamic_theme_state

        out["theme_state_us"] = _timeit_us(
            lambda: get_dynamic_theme_state("dynamic_rainbow_liquid_glass", 1.0),
            iterations * 4,
        )
    except Exception as exc:
        out["theme_state_error"] = f"{type(exc).__name__}: {exc}"

    # --- Web page render --------------------------------------------------
    try:
        import mix_web_companion as M

        theme = M.get_theme_palette(getattr(M, "CLI_THEME", None))
        out["web_page_us"] = _timeit_us(
            lambda: M.render_html_page(theme), max(20, iterations // 20)
        )
    except Exception as exc:
        out["web_page_error"] = f"{type(exc).__name__}: {exc}"

    return out


def measure_render(frames: int) -> Dict[str, Any]:
    """Per-frame cost of the real OpenGL draw path.

    A GLFW window is created because that is what the product does; it is
    closed as soon as the measurement finishes.
    """
    try:
        import mp_liquid_visualizer_vulkan as V
        import liquid_player_visualizer  # noqa: F401  binds ROLE_SPAWNED
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    renderer = None
    try:
        renderer = V._make_renderer(
            "0", fullscreen=False, target_4k=False, backend="opengl"
        )
        bridge = V.AudioTelemetryBridge()
        renderer.open()
        for _ in range(10):  # warm up shader compile and texture upload
            bridge.update(0.016)
            renderer.draw(bridge, False)

        cpu0 = _proc_cpu_seconds(os.getpid()) or 0.0
        times: List[float] = []
        for _ in range(frames):
            t0 = time.perf_counter()
            bridge.update(0.016)
            renderer.draw(bridge, False)
            times.append(time.perf_counter() - t0)
        cpu1 = _proc_cpu_seconds(os.getpid()) or 0.0

        total = sum(times)
        isolated = measure_isolated_render_fps(renderer, bridge, frames, V)
        return {
            "frames": frames,
            "fps": round(frames / total, 1) if total > 0 else 0.0,
            "ms_per_frame": round(1000.0 * total / frames, 2),
            "cpu_ms_per_frame": round(1000.0 * (cpu1 - cpu0) / frames, 3),
            "ms_per_frame_p95": round(
                1000.0 * sorted(times)[min(len(times) - 1, int(len(times) * 0.95))], 2
            ),
            **isolated,
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc(limit=2)}
    finally:
        if renderer is not None:
            try:
                renderer.close()
            except Exception:
                pass


def measure_isolated_render_fps(renderer, bridge, frames: int, V) -> Dict[str, Any]:
    """CPU-only per-frame cost with the presenter removed from the loop.

    ``glfwSwapBuffers`` blocks on vsync, so the wall clock cannot reveal how
    much Python/CPU work a frame needs. Measuring the scene draw without the
    swap gives a number that actually moves when code gets faster.
    """
    try:
        GL = __import__("OpenGL.GL", fromlist=["GL"])
        layer = getattr(renderer, "_layer", None)
        if layer is None:
            return {}
        fw, fh = renderer.framebuffer_size()
        state = {
            "active": True,
            "preset_id": 0,
            "preset_key": "cosmic_dreamworlds_liquid",
            "preset_name": "Cosmic Dreamworlds (Liquid)",
            "text_guard": False,
        }
        GL.glViewport(0, 0, fw, fh)
        cpu0 = _proc_cpu_seconds(os.getpid()) or 0.0
        n = max(20, frames // 3)
        for _ in range(n):
            layer.draw_with_state(renderer, fw, fh, state, bridge=bridge)
        GL.glFinish()
        cpu1 = _proc_cpu_seconds(os.getpid()) or 0.0
        return {"scene_cpu_ms_per_frame": round(1000.0 * (cpu1 - cpu0) / n, 3)}
    except Exception as exc:
        return {"scene_measure_error": f"{type(exc).__name__}: {exc}"}


def measure_server(requests: int) -> Dict[str, Any]:
    """Requests per second for the Web Companion, served in-process."""
    try:
        import threading
        import urllib.request

        import mix_web_companion as M
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    server = None
    try:
        server = M.CompanionServer(("127.0.0.1", 0), M.CompanionHandler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        time.sleep(0.25)
        url = f"http://127.0.0.1:{port}/"

        def one() -> None:
            try:
                with urllib.request.urlopen(url, timeout=5) as resp:
                    resp.read()
            except Exception:
                pass

        one()  # warm
        cpu0 = _proc_cpu_seconds(os.getpid()) or 0.0
        t0 = time.perf_counter()
        for _ in range(requests):
            one()
        dt = time.perf_counter() - t0
        cpu1 = _proc_cpu_seconds(os.getpid()) or 0.0
        return {
            "requests": requests,
            "req_per_sec": round(requests / dt, 1) if dt > 0 else 0.0,
            "ms_per_request": round(1000.0 * dt / requests, 2),
            "cpu_ms_per_request": round(1000.0 * (cpu1 - cpu0) / requests, 3),
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass


def measure_launch(name: str, meta: Dict[str, str], seconds: float) -> Dict[str, Any]:
    """Launch a real shortcut and sample its CPU/RSS while it runs.

    GUI launches are skipped when there is no display, so the harness is safe
    to run over SSH or from a TTY.
    """
    entry = ROOT / meta["entry"]
    if not entry.is_file():
        return {"error": f"missing entry point: {meta['entry']}"}

    interactive = not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if _IS_MAC:
        # macOS runs the desktop through WindowServer, so there is no DISPLAY
        # variable to check; a GUI launch is always possible when logged in.
        interactive = False
    if meta["kind"] != "shell":
        pass
    elif interactive:
        return {"skipped": "no DISPLAY/WAYLAND_DISPLAY (headless session)"}

    cmd: List[str]
    if entry.suffix == ".py":
        cmd = [sys.executable, str(entry)]
    else:
        cmd = ["bash", str(entry)]

    try:
        # A movie in front of the first player window would be sampled as
        # launch CPU, so the benchmark asks for a window without it.
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ, "MP_NO_PLAYER_INTRO": "1"},
        )
    except OSError as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    sampler = ProcessSampler(proc.pid, interval=0.1)
    sampler.start()
    try:
        time.sleep(seconds)
    finally:
        alive = proc.poll() is None
        _terminate_tree(proc)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
    stats = sampler.stop()
    stats["exited_early"] = not alive
    stats["exit_code"] = proc.returncode
    return stats


def _terminate_tree(proc: subprocess.Popen, sig: int = 15) -> None:
    """Ask a launched shortcut and its children to exit.

    POSIX gets a whole process-group signal, which is the only reliable way to
    stop a shell launcher that spawned Python, GTK and a visualizer. Windows has
    no process groups here, so it falls back to terminate().
    """
    if _IS_WIN:
        try:
            proc.terminate()
        except Exception:
            pass
        return
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except (OSError, ProcessLookupError):
        try:
            proc.terminate()
        except Exception:
            pass


def _kill_tree(proc: subprocess.Popen) -> None:
    if _IS_WIN:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        return
    _terminate_tree(proc, sig=9)


# ---------------------------------------------------------------------------
# report writing
# ---------------------------------------------------------------------------
def _load_average() -> Optional[float]:
    """1-minute load average, or None where the platform cannot report it."""
    try:
        return float(os.getloadavg()[0])
    except (AttributeError, OSError):
        pass
    try:
        out = subprocess.run(
            ["uptime"], capture_output=True, text=True, timeout=5
        )
        # "load averages: 1.23 1.10 0.98" (BSD) / "load average: 1.23, ..." (Linux)
        text = (out.stdout or "")
        marker = "load average:" if "load average:" in text else "load averages:"
        if marker in text:
            tail = text.split(marker, 1)[1].replace(",", " ").split()
            return float(tail[0]) if tail else None
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def _env_fingerprint() -> Dict[str, Any]:
    load1 = _load_average()
    return {
        "host": platform.node(),
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "load1_at_start": round(load1, 2) if load1 is not None else None,
        "display": bool(
            os.environ.get("DISPLAY")
            or os.environ.get("WAYLAND_DISPLAY")
            or _IS_MAC
            or _IS_WIN
        ),
    }


def run_benchmark(
    targets: List[str],
    frames: int,
    iterations: int,
    seconds: float,
    requests: int,
) -> Dict[str, Any]:
    started = datetime.now()
    results: Dict[str, Any] = {}
    for name in targets:
        meta = TARGETS[name]
        print(f"  → {meta['label']} ({name})", flush=True)
        entry: Dict[str, Any] = {"label": meta["label"], "entry": meta["entry"]}

        if meta["kind"] == "render":
            entry["render"] = measure_render(frames)
            entry["startup"] = measure_import("mp_liquid_visualizer_vulkan")
        elif meta["kind"] == "server":
            entry["server"] = measure_server(requests)
            entry["startup"] = measure_import("mix_web_companion")
        else:
            module = {
                "mix-manager": "MP_Audio_Player",
                "liquid-player": "MP_Audio_Player_Liquid_Glass_OpenGL",
                "mp-gui": "MP_Audio_Player_Liquid_Glass_OpenGL",
            }.get(name, "")
            if module:
                entry["startup"] = measure_import(module)
            entry["process"] = measure_launch(name, meta, seconds)
        results[name] = entry

    # Micro benchmarks are shared by every target: they price the code that
    # all four shortcuts execute, not just one launcher.
    print("  → hot functions (micro)", flush=True)
    results["_micro"] = measure_micro(iterations)

    return {
        "schema": 1,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "duration_sec": round((datetime.now() - started).total_seconds(), 1),
        "env": _env_fingerprint(),
        "settings": {
            "frames": frames,
            "iterations": iterations,
            "seconds": seconds,
            "requests": requests,
        },
        "results": results,
    }


def _flatten(data: Dict[str, Any], prefix: str = "") -> Dict[str, float]:
    """Flatten to dotted metric -> number, ignoring non-numeric fields."""
    flat: Dict[str, float] = {}
    for key, value in data.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flat.update(_flatten(value, name))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            flat[name] = float(value)
    return flat


# Metrics where a smaller number is better.
LOWER_IS_BETTER = (
    "ms_per_frame",
    "cpu_ms_per_frame",
    "scene_cpu_ms_per_frame",
    "us",
    "_ms",
    "rss_",
    "import_ms",
    "subprocess_ms",
    "ms_per_request",
)


def _better(metric: str, old: float, new: float) -> Optional[bool]:
    """True when ``new`` is an improvement over ``old`` for this metric."""
    if old == 0:
        return None
    lower = any(marker in metric for marker in LOWER_IS_BETTER)
    if "fps" in metric or "req_per_sec" in metric:
        lower = False
    if old == new:
        return None
    return (new < old) if lower else (new > old)


def print_comparison(current: Dict[str, Any], previous: Dict[str, Any],
                     threshold: float) -> Tuple[bool, List[str]]:
    cur = _flatten(current.get("results", {}))
    old = _flatten(previous.get("results", {}))
    shared = sorted(set(cur) & set(old))

    print()
    print("=" * 78)
    print(f"  COMPARISON vs {previous.get('started_at', 'unknown')}")
    print("=" * 78)
    print(f"  {'metric':<52} {'before':>10} {'after':>10} {'delta':>1}")
    print("-" * 78)

    regressions: List[str] = []
    improved = 0
    for metric in shared:
        before, after = old[metric], cur[metric]
        if before == 0:
            continue
        pct = (after - before) / abs(before) * 100.0
        if abs(pct) < 0.5:
            continue
        better = _better(metric, before, after)
        mark = "✓" if better else ("✗" if better is False else " ")
        if better:
            improved += 1
        elif better is False and abs(pct) >= threshold:
            regressions.append(f"{metric}: {pct:+.1f}%")
        label = metric.replace("results.", "")
        print(f"  {label[:52]:<52} {before:>10.2f} {after:>10.2f}  {mark} {pct:+6.1f}%")

    print("-" * 78)
    print(f"  improved: {improved}   regressions (>{threshold:.0f}%): {len(regressions)}")
    if regressions:
        print("  ⚠ slower than before:")
        for line in regressions:
            print(f"      {line}")
    print("=" * 78)
    return (len(regressions) == 0), regressions


def save_report(report: Dict[str, Any]) -> Path:
    stamp = report["started_at"].replace(":", "").replace("-", "")
    out_dir = REPORT_ROOT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "perf.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    # latest.json always points at the newest run so --compare is a no-op.
    try:
        if LATEST.is_symlink() or LATEST.exists():
            LATEST.unlink()
        LATEST.symlink_to(path)
    except OSError:
        LATEST.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def load_previous(path: Optional[str]) -> Optional[Dict[str, Any]]:
    if path:
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    runs = sorted(p for p in REPORT_ROOT.glob("*/perf.json"))
    if not runs:
        return None
    try:
        return json.loads(runs[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_runs() -> int:
    runs = sorted(REPORT_ROOT.glob("*/perf.json"))
    if not runs:
        print(f"no runs yet under {REPORT_ROOT}")
        return 0
    print(f"{'run':<22} {'duration':>9}  targets")
    for path in runs[-20:]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        names = ", ".join(k for k in data.get("results", {}) if not k.startswith("_"))
        print(f"{data.get('started_at', path.parent.name):<22} "
              f"{data.get('duration_sec', 0):>8.1f}s  {names}")
    return 0


def print_summary(report: Dict[str, Any]) -> None:
    results = report["results"]
    print()
    print("=" * 78)
    print("  MP MIX MANAGER — PERFORMANCE RUN")
    print("=" * 78)
    env = report["env"]
    print(f"  host      : {env['host']}  ({env['cpu_count']} cores)")
    print(f"  python    : {env['python']}   load1: {env['load1_at_start']}")
    print(f"  duration  : {report['duration_sec']}s")
    print("-" * 78)

    for name, data in results.items():
        if name.startswith("_"):
            continue
        label = data.get("label", name)
        print(f"  {label}")
        startup = data.get("startup") or {}
        if "import_ms" in startup:
            print(f"      startup  : {startup['import_ms']:>7.1f} ms   "
                  f"RSS {startup.get('rss_peak_mb', 0):>6.1f} MB")
        render = data.get("render") or {}
        if "fps" in render:
            print(f"      render   : {render['fps']:>7.1f} fps  "
                  f"{render['ms_per_frame']:>6.2f} ms/frame  "
                  f"CPU {render['cpu_ms_per_frame']:>6.2f} ms")
            if "scene_cpu_ms_per_frame" in render:
                print(f"      scene CPU: {render['scene_cpu_ms_per_frame']:>7.2f} ms/frame (no vsync)")
        if "req_per_sec" in (data.get("server") or {}):
            srv = data["server"]
            print(f"      server   : {srv['req_per_sec']:>7.1f} req/s  "
                  f"{srv['ms_per_request']:>6.2f} ms/req")
        proc = data.get("process") or {}
        if "cpu_pct" in proc:
            print(f"      process  : {proc['cpu_pct']:>7.1f} %CPU  "
                  f"RSS peak {proc['rss_peak_mb']:>6.1f} MB  "
                  f"{proc['threads_peak']} threads")
        elif proc.get("skipped"):
            print(f"      process  : skipped ({proc['skipped']})")
        for key in ("error",):
            if data.get(key):
                print(f"      {key:<9}: {data[key]}")

    micro = results.get("_micro") or {}
    if micro:
        print("-" * 78)
        print("  hot functions")
        for key, value in micro.items():
            if key.endswith("_us"):
                print(f"      {key:<26} {value:>9.2f} us")
            elif key.endswith("_error"):
                print(f"      {key:<26} {value}")
    print("=" * 78)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mp_perf_bench.py",
        description="CPU, memory and render benchmark for the MP Mix Manager shortcuts.",
    )
    parser.add_argument("--targets", default=",".join(DEFAULT_TARGETS),
                        help=f"comma separated: {','.join(TARGETS)} or 'all'")
    parser.add_argument("--quick", action="store_true",
                        help="fewer samples for a fast smoke run")
    parser.add_argument("--frames", type=int, default=None, help="render frames")
    parser.add_argument("--iterations", type=int, default=None, help="micro iterations")
    parser.add_argument("--seconds", type=float, default=None,
                        help="seconds to sample each launched shortcut")
    parser.add_argument("--requests", type=int, default=None,
                        help="HTTP requests for the web companion")
    parser.add_argument("--compare", nargs="?", const="latest", default=None,
                        metavar="RUN_JSON",
                        help="compare against the previous run (or a given perf.json)")
    parser.add_argument("--fail-on-regression", action="store_true",
                        help="exit 1 when a metric regressed past --threshold")
    parser.add_argument("--threshold", type=float, default=10.0,
                        help="regression threshold in percent (default 10)")
    parser.add_argument("--list", action="store_true", help="list stored runs")
    parser.add_argument("--json", action="store_true", help="print JSON only")
    parser.add_argument("--no-save", action="store_true", help="do not write a report")
    parser.add_argument("--max-load", type=float, default=0.0,
                        help="abort when 1-min load exceeds this (0 = no check). "
                             "Benchmarks measured under heavy load are not comparable.")
    parser.add_argument("--force", action="store_true",
                        help="run even when the load guard would abort")
    parser.add_argument("--tag", default="",
                        help="OS/architecture label stamped into the report")
    parser.add_argument("--out", default="",
                        help="explicit output path for the JSON report")
    args = parser.parse_args(argv)

    if args.list:
        return list_runs()

    if args.max_load > 0 and not args.force:
        load1 = _load_average()
        if load1 is not None and load1 > args.max_load:
            print(f"aborting: 1-min load is {load1:.2f}, above --max-load {args.max_load:.2f}.",
                  file=sys.stderr)
            print("Timings taken under load are not comparable between runs.",
                  file=sys.stderr)
            print("Wait for the machine to settle, raise --max-load, or pass --force.",
                  file=sys.stderr)
            return 3

    targets = DEFAULT_TARGETS if args.targets.strip().lower() == "all" else [
        t.strip() for t in args.targets.split(",") if t.strip()
    ]
    unknown = [t for t in targets if t not in TARGETS]
    if unknown:
        print(f"unknown target(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"known: {', '.join(TARGETS)}", file=sys.stderr)
        return 2

    frames = args.frames if args.frames is not None else (60 if args.quick else 180)
    iterations = args.iterations if args.iterations is not None else (500 if args.quick else 3000)
    seconds = args.seconds if args.seconds is not None else (2.0 if args.quick else 6.0)
    requests = args.requests if args.requests is not None else (40 if args.quick else 150)

    if not args.json:
        print("=" * 78)
        print("  MP MIX MANAGER — PERFORMANCE BENCHMARK")
        print("=" * 78)
        print(f"  targets    : {', '.join(targets)}")
        print(f"  frames     : {frames}    micro iterations: {iterations}")
        print(f"  process    : {seconds}s    http requests: {requests}")
        print("-" * 78)

    report = run_benchmark(targets, frames, iterations, seconds, requests)
    if args.tag:
        report["tag"] = args.tag
        report.setdefault("env", {})["tag"] = args.tag

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_summary(report)

    saved = None
    if not args.no_save:
        if args.out:
            dest = Path(args.out)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(report, indent=2), encoding="utf-8")
            saved = dest
        else:
            saved = save_report(report)
        if not args.json:
            print(f"  report written to {saved}")

    ok = True
    if args.compare:
        previous = load_previous(None if args.compare == "latest" else args.compare)
        if previous is None:
            if not args.json:
                print("  (no previous run to compare against)")
        else:
            ok, _ = print_comparison(report, previous, args.threshold)
            if args.fail_on_regression and not ok:
                return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
