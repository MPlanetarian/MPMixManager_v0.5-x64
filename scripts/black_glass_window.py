#!/usr/bin/env python3
"""MP Black Glass Window — the dark glass panel opened with **W**.

Pressing **W** inside MP Liquid Player, MP Gui, MP Liquid Mixer or the MP
Liquid Visualizer opens one of these windows. It is a borderless GTK window
drawn with Cairo, at exactly the size of the window that opened it, and it is
a window in its own right: it can be moved, resized, and closed on its own.

What the window does
--------------------
* **Black Glass mode** is the default: near-black glass with a thin rim and a
  faint sheen, matching the rest of the suite.
* **R** switches it to **Rainbow mode**. The whole pane walks the hue scale
  upwards — red, orange, yellow, green, cyan, blue, violet, red — and keeps
  going until **R** is pressed again, which returns it to Black Glass.
* A **transparency slider** sits along the bottom edge. It runs from **10 %**
  to **90 %** and changes the window's opacity. The slider is only ever drawn
  while the pointer is over the window: it fades in when the pointer enters
  and fades out when it leaves, and it is tinted the same colour as the pane
  it belongs to.
* **Esc** or **Q** closes the window, **Z** attaches an MP Liquid Visualizer
  behind it and takes it away again, **G** opens MP Gui, **L** opens MP Liquid
  Player, **M** opens MP Mix Manager in a Konsole window and a second **W**
  opens another Black Glass window.

Eight windows at most
---------------------
A claim file per live window in ``~/.config/mix-manager/black_glass/slots``
makes the count atomic, the same scheme ``mp_instance_limit`` uses for the
four MP Gui / MP Liquid Player windows. The windows are separate processes, so
the claim is taken by the window itself before it draws anything: a launch
that skips the caller's early check still cannot become a ninth window.

Why a separate process
----------------------
The four hosts already run their own event loops (GTK+VTE, GLFW+Vulkan, GLFW+
OpenGL, GTK+Cairo). A second GTK main loop inside any of them would fight the
first one for the display connection. One process per window keeps every host
untouched, and it is what makes "open up to eight" a countable thing.

GTK is imported inside ``run()`` and not at module import time: the MP Liquid
Visualizer imports this module to read its \"is GTK available\" helpers from a
process that must never pull GTK in before GLFW has made its context.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

CONFIG_DIR = Path(os.environ.get("MP_BLACK_GLASS_CONFIG_DIR", "") or (Path.home() / ".config" / "mix-manager"))
TITLE = "MP Black Glass"
SCRIPT = Path(__file__).resolve()

# Eight is the documented limit. It is high enough to build a wall of glass
# panels and low enough that a stuck key cannot spawn hundreds of processes.
MAX_WINDOWS = 8

# The slider runs over this range, in percent of transparency.
MIN_TRANSPARENCY = 10.0
MAX_TRANSPARENCY = 90.0
DEFAULT_TRANSPARENCY = 35.0

# Black Glass -> Rainbow. Degrees of hue per second: one full turn every 15 s
# reads as "gradually" rather than a strobe.
RAINBOW_DEGREES_PER_SECOND = 24.0
# Seconds the hover fade takes in either direction.
SLIDER_FADE_SECONDS = 0.22
# Frame interval for the animation timer (60 fps).
FRAME_MS = 16

# Slider and grip geometry, in pixels from the edges of the pane.
SLIDER_LEFT = 46
SLIDER_RIGHT = 46
SLIDER_HEIGHT = 9
SLIDER_BOTTOM = 34
SLIDER_HIT_TOP = 62
SLIDER_HIT_BOTTOM = 14
RESIZE_GRIP = 26

MIN_WIDTH, MIN_HEIGHT = 320, 200
CORNER_RADIUS = 30


# ------------------------------------------------------------------------------
# pure helpers — no GTK
# ------------------------------------------------------------------------------
def black_glass_supported() -> bool:
    """True when this machine can actually draw a Black Glass window.

    The window is a GTK/X11 pane, which is the same requirement the Liquid
    Mixer has. ``find_spec`` is used instead of importing ``gi`` so a caller
    (the visualizer) can ask the question without pulling GTK into a process
    that has already created a GLFW/OpenGL context.
    """
    if not sys.platform.startswith(("linux", "freebsd")):
        return False
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        return importlib.util.find_spec("gi") is not None
    except (ImportError, ValueError):
        return False


def clamp_transparency(value: float) -> float:
    """Clamp a slider value to the documented 10 %..90 % range."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TRANSPARENCY
    if math.isnan(number):
        return DEFAULT_TRANSPARENCY
    return max(MIN_TRANSPARENCY, min(MAX_TRANSPARENCY, number))


def transparency_to_opacity(transparency: float) -> float:
    """Window opacity for a transparency percentage.

    90 % transparency is a window you can barely see, so it maps to 0.10
    opacity; 10 % transparency is almost solid and maps to 0.90.
    """
    return 1.0 - (clamp_transparency(transparency) / 100.0)


def opacity_to_transparency(opacity: float) -> float:
    """Inverse of :func:`transparency_to_opacity`."""
    try:
        number = float(opacity)
    except (TypeError, ValueError):
        return DEFAULT_TRANSPARENCY
    return clamp_transparency((1.0 - number) * 100.0)


def slider_track(width: int) -> Tuple[float, float]:
    """Left and right x of the slider track inside a pane ``width`` wide."""
    left = float(SLIDER_LEFT)
    right = float(width) - float(SLIDER_RIGHT)
    return left, max(left + 40.0, right)


def transparency_from_x(x: float, width: int) -> float:
    """Slider value for a pointer at ``x`` inside a pane ``width`` wide."""
    left, right = slider_track(width)
    span = max(1e-6, right - left)
    return clamp_transparency(MIN_TRANSPARENCY + ((float(x) - left) / span) * (MAX_TRANSPARENCY - MIN_TRANSPARENCY))


def slider_thumb_x(transparency: float, width: int) -> float:
    """Pointer x that selects ``transparency``; the inverse of the above."""
    left, right = slider_track(width)
    span = max(1e-6, MAX_TRANSPARENCY - MIN_TRANSPARENCY)
    fraction = (clamp_transparency(transparency) - MIN_TRANSPARENCY) / span
    return left + max(0.0, min(1.0, fraction)) * (right - left)


def hue_at(seconds: float) -> float:
    """Hue in degrees for a running clock, always ascending through the scale.

    The value wraps at 360 because hue is circular; the *order* is still
    lowest to highest, which is what "through all the colours of the rainbow
    in the order from lowest to highest colour" asks for.
    """
    try:
        clock = float(seconds)
    except (TypeError, ValueError):
        clock = 0.0
    if clock < 0.0:
        clock = 0.0
    return (clock * RAINBOW_DEGREES_PER_SECOND) % 360.0


def hsv_to_rgb(hue: float, saturation: float = 0.95, value: float = 0.60) -> Tuple[float, float, float]:
    """HSV (hue in degrees) to RGB in 0..1, for Cairo."""
    h = (float(hue) % 360.0) / 60.0
    s = max(0.0, min(1.0, float(saturation)))
    v = max(0.0, min(1.0, float(value)))
    c = v * s
    x = c * (1.0 - abs((h % 2.0) - 1.0))
    m = v - c
    if h < 1:
        r, g, b = c, x, 0.0
    elif h < 2:
        r, g, b = x, c, 0.0
    elif h < 3:
        r, g, b = 0.0, c, x
    elif h < 4:
        r, g, b = 0.0, x, c
    elif h < 5:
        r, g, b = x, 0.0, c
    else:
        r, g, b = c, 0.0, x
    return r + m, g + m, b + m


def slider_colour(rainbow: bool, hue: float) -> Tuple[float, float, float]:
    """Colour of the slider for the pane it sits in.

    Black Glass uses the cool frosted white the glass rim uses; Rainbow uses
    the hue the pane is showing at that instant, nudged forward so the bar
    reads against the fill behind it.
    """
    if rainbow:
        return hsv_to_rgb(hue + 26.0, 0.55, 0.95)
    return 0.80, 0.86, 0.94


# ------------------------------------------------------------------------------
# the eight-window registry
# ------------------------------------------------------------------------------
def state_dir() -> Path:
    override = os.environ.get("MP_BLACK_GLASS_STATE_DIR", "")
    path = Path(override) if override else (CONFIG_DIR / "black_glass")
    path.mkdir(parents=True, exist_ok=True)
    return path


def slots_dir() -> Path:
    path = state_dir() / "slots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pid_alive(pid: int) -> bool:
    if int(pid or 0) <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


@contextmanager
def _slot_lock():
    """Atomic mkdir lock, portable across the platforms the suite ships on."""
    lock = state_dir() / "slots.lock"
    deadline = time.monotonic() + 3.0
    while True:
        try:
            os.mkdir(lock)
            break
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                age = 0.0
            if age > 10.0:
                try:
                    os.rmdir(lock)
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"black glass slot lock is busy: {lock}")
            time.sleep(0.02)
    try:
        yield
    finally:
        try:
            os.rmdir(lock)
        except OSError:
            pass


def live_slots() -> set:
    """Pids of Black Glass windows that are still alive, dead claims pruned."""
    alive: set = set()
    try:
        entries = list(slots_dir().glob("*.claim"))
    except OSError:
        return alive
    for path in entries:
        try:
            pid = int(path.stem)
        except ValueError:
            continue
        if _pid_alive(pid):
            alive.add(pid)
            continue
        try:
            path.unlink()
        except OSError:
            pass
    return alive


def live_count(exclude_pid: Optional[int] = None) -> int:
    count = len(live_slots())
    if exclude_pid and int(exclude_pid) in live_slots():
        count -= 1
    return max(0, count)


def claim_slot(pid: Optional[int] = None) -> bool:
    """Reserve one of the eight windows for ``pid``. False when full."""
    owner = int(pid or os.getpid())
    try:
        with _slot_lock():
            alive = live_slots()
            alive.discard(owner)
            if len(alive) >= MAX_WINDOWS:
                return False
            (slots_dir() / f"{owner}.claim").write_text(f"{owner}\n", encoding="utf-8")
            return True
    except (TimeoutError, OSError):
        # A broken lock must not stop a window the user asked for. Fall back
        # to a plain count, which is only wrong under a simultaneous launch.
        return live_count(exclude_pid=owner) < MAX_WINDOWS


def release_slot(pid: Optional[int] = None) -> None:
    owner = int(pid or os.getpid())
    try:
        (slots_dir() / f"{owner}.claim").unlink()
    except OSError:
        pass


def limit_message() -> str:
    return f"MP Black Glass is already open {MAX_WINDOWS} times. Close one window first."


# ------------------------------------------------------------------------------
# geometry: exactly the size of the window that opened it
# ------------------------------------------------------------------------------
def _record_candidates() -> Tuple[Path, ...]:
    return (
        CONFIG_DIR / "liquid_player_window.json",
        CONFIG_DIR / "liquid_gui_window.json",
    )


def _read_record(path: Path) -> Optional[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        width = int(payload.get("width") or 0)
        height = int(payload.get("height") or 0)
        pid = int(payload.get("pid") or 0)
    except (TypeError, ValueError):
        return None
    if width < 64 or height < 64 or not _pid_alive(pid):
        return None
    try:
        x = int(payload["x"]) if "x" in payload else None
        y = int(payload["y"]) if "y" in payload else None
    except (TypeError, ValueError):
        x = y = None
    return {"width": width, "height": height, "pid": pid, "x": x, "y": y}


def host_geometry() -> Optional[Tuple[int, int, int, int]]:
    """``(x, y, width, height)`` of the window that pressed **W**.

    The glass hosts put their own pid in ``MP_HOST_PID`` for the child they
    run, so the record whose ``pid`` matches is *this* window's rectangle and
    not the one belonging to another open player. Without the variable (a
    launch path that did not set it) the newest live record is used.
    """
    wanted = 0
    try:
        wanted = int(os.environ.get("MP_HOST_PID") or 0)
    except (TypeError, ValueError):
        wanted = 0
    fallback = None
    for path in _record_candidates():
        record = _read_record(path)
        if not record:
            continue
        if wanted and record["pid"] == wanted:
            return _place(record)
        if fallback is None:
            fallback = record
    return _place(fallback) if fallback else None


def _place(record: dict) -> Tuple[int, int, int, int]:
    """Centre a new pane on the host rectangle when no position was published."""
    width, height = int(record["width"]), int(record["height"])
    x, y = record.get("x"), record.get("y")
    if x is None or y is None:
        x, y = _centred(width, height)
    return int(x), int(y), width, height


def _centred(width: int, height: int) -> Tuple[int, int]:
    try:
        from desktop_launch_screen import main_window_origin
        return tuple(int(v) for v in main_window_origin(int(width), int(height), 72, 64))
    except Exception:
        return 72, 64


def parse_geometry(text: str) -> Optional[Tuple[int, int, int, int]]:
    """Parse ``WxH+X+Y`` (the form the spawner passes on the command line)."""
    try:
        size, rest = str(text).split("+", 1)
        width, height = (int(v) for v in size.lower().split("x", 1))
        x, y = (int(v) for v in rest.split("+", 1))
    except (TypeError, ValueError):
        return None
    if width < 1 or height < 1:
        return None
    return x, y, width, height


# ------------------------------------------------------------------------------
# geometry publication for the attached visualizer
# ------------------------------------------------------------------------------
def record_path(pid: int) -> Path:
    return CONFIG_DIR / f"black_glass_window.{int(pid)}.json"


def publish_geometry(x: int, y: int, width: int, height: int,
                     pid: Optional[int] = None, wid: Optional[int] = None) -> None:
    """Record this pane so the visualizer behind it can follow it.

    The publisher's pid is written into the record: the bonded visualizer
    validates it before using the geometry, so a closed pane's stale record is
    ignored and the visualizer closes itself with it.
    """
    owner = int(pid or os.getpid())
    try:
        width_i, height_i = int(width), int(height)
    except (TypeError, ValueError):
        return
    if width_i < 64 or height_i < 64:
        return
    payload = {"width": width_i, "height": height_i, "pid": owner, "x": int(x), "y": int(y)}
    if wid:
        payload["wid"] = int(wid)
    path = record_path(owner)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass


def read_geometry(pid: Optional[int] = None) -> Optional[Tuple[int, int, int, int]]:
    owner = int(pid or _target_pid() or 0)
    if owner <= 0:
        return None
    record = _read_record(record_path(owner))
    if not record:
        return None
    if record.get("x") is None or record.get("y") is None:
        return None
    return int(record["x"]), int(record["y"]), int(record["width"]), int(record["height"])


def read_window_id(pid: Optional[int] = None) -> int:
    owner = int(pid or _target_pid() or 0)
    if owner <= 0:
        return 0
    try:
        payload = json.loads(record_path(owner).read_text(encoding="utf-8"))
        return int(payload.get("wid") or 0)
    except (OSError, ValueError, TypeError):
        return 0


def _target_pid() -> int:
    try:
        return int(os.environ.get("MP_BLACK_GLASS_PID") or 0)
    except (TypeError, ValueError):
        return 0


def host_running(pid: Optional[int] = None) -> Optional[bool]:
    """True/False for the pane this window belongs to, None when unknown."""
    owner = int(pid or _target_pid() or 0)
    if owner <= 0:
        return None
    return _pid_alive(owner)


def visualizer_pid_path(pid: int) -> Path:
    return CONFIG_DIR / f"black_glass_visualizer.{int(pid)}.json"


def attached_visualizer_pid(pid: Optional[int] = None) -> int:
    """Pid of the visualizer attached to pane ``pid``, or 0."""
    owner = int(pid or os.getpid())
    try:
        payload = json.loads(visualizer_pid_path(owner).read_text(encoding="utf-8"))
        child = int(payload.get("pid") or 0)
    except (OSError, ValueError, TypeError):
        return 0
    return child if _pid_alive(child) else 0


def _visualizer_state_path(pid: int) -> Path:
    """A per-pane state file, so the shared player flags cannot hide it.

    The companion reads ``visible`` from the state file. Pointing an attached
    visualizer at the player's file would let the player's Visualization
    button unmap a window it does not own, so each pane gets its own.
    """
    return CONFIG_DIR / f"black_glass_visualizer_state.{int(pid)}.json"


def _write_visualizer_state(pid: int) -> None:
    path = _visualizer_state_path(pid)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "active": True,
            "visible": True,
            "gui_visible": True,
            "preset_id": 0,
            "preset_key": "cosmic_dreamworlds_liquid",
            "preset_name": "Cosmic Dreamworlds (Liquid)",
        }), encoding="utf-8")
    except OSError:
        pass


def _visualizer_env(pid: int) -> dict:
    """Environment for the attached visualizer.

    The companion flag is what makes it locked: it follows the published
    rectangle for its whole life and closes itself when the pane disappears.
    ``MP_VISUALIZER_MATCH_BLACK_GLASS`` tells the bond which record to read.
    """
    drop = {
        "GDK_BACKEND",
        "MP_GUI_SESSION",
        "MP_LIQUID_GLASS_MANAGER",
        "MP_LIQUID_GLASS_MIXER",
        "MP_PLAYER_NAMESPACE",
        "MP_LIQUID_VISUALIZER_STATE",
        "MP_VISUALIZER_COMPANION",
        "MP_VISUALIZER_DETACHED",
        "MP_VISUALIZER_MATCH_GUI",
        "MP_VISUALIZER_MATCH_BLACK_GLASS",
        "MP_BLACK_GLASS_PID",
    }
    env = {key: value for key, value in os.environ.items() if key not in drop}
    env["MP_VISUALIZER_COMPANION"] = "1"
    env["MP_VISUALIZER_MATCH_BLACK_GLASS"] = "1"
    env["MP_BLACK_GLASS_PID"] = str(int(pid))
    env["MP_LIQUID_VISUALIZER_STATE"] = str(_visualizer_state_path(pid))
    return env


def _visualizer_entry() -> Optional[Path]:
    for candidate in (ROOT / "MP_Liquid_Visualizer.py", SCRIPTS / "mp_liquid_visualizer_vulkan.py"):
        if candidate.is_file():
            return candidate
    return None


def attach_visualizer(pid: Optional[int] = None) -> int:
    """Start the visualizer behind this pane. Returns its pid, or 0."""
    owner = int(pid or os.getpid())
    entry = _visualizer_entry()
    if entry is None:
        return 0
    if attached_visualizer_pid(owner):
        return attached_visualizer_pid(owner)
    _write_visualizer_state(owner)
    kwargs = {"creationflags": 0x00000208} if sys.platform == "win32" else {"start_new_session": True}
    try:
        log_dir = CONFIG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "liquid_visualizer.log").open("a", encoding="utf-8") as log:
            proc = subprocess.Popen(
                [sys.executable, str(entry), "--preset", "random", "--backend", "auto"],
                cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                env=_visualizer_env(owner), **kwargs,
            )
    except Exception:
        return 0
    try:
        visualizer_pid_path(owner).write_text(json.dumps({"pid": int(proc.pid)}) + "\n", encoding="utf-8")
    except OSError:
        pass
    return int(proc.pid)


def detach_visualizer(pid: Optional[int] = None) -> bool:
    """Take the attached visualizer down. True when one was running."""
    owner = int(pid or os.getpid())
    child = attached_visualizer_pid(owner)
    try:
        visualizer_pid_path(owner).unlink()
    except OSError:
        pass
    try:
        _visualizer_state_path(owner).unlink()
    except OSError:
        pass
    if not child:
        return False
    try:
        os.kill(child, getattr(signal, "SIGTERM", signal.SIGINT))
    except OSError:
        return False
    return True


# ------------------------------------------------------------------------------
# launching the other MP windows from inside a pane
# ------------------------------------------------------------------------------
def _spawn(argv, env: Optional[dict] = None) -> bool:
    kwargs = {"creationflags": 0x00000208} if sys.platform == "win32" else {"start_new_session": True}
    try:
        subprocess.Popen(
            argv, cwd=str(ROOT), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=env, **kwargs,
        )
        return True
    except Exception:
        return False


def launch_mp_gui() -> bool:
    """**G**: MP Gui, through the same launcher the desktop shortcut uses."""
    launcher = ROOT / "MP_Gui.sh"
    if not launcher.is_file():
        return False
    return _spawn(["bash", str(launcher)])


def launch_mp_player() -> bool:
    """**L**: MP Liquid Player, in its own process."""
    for candidate in (SCRIPTS / "launch_liquid_glass.sh", ROOT / "MP_Audio_Player_Liquid_Glass_OpenGL.py"):
        if not candidate.is_file():
            continue
        if candidate.suffix == ".sh":
            return _spawn(["bash", str(candidate)])
        return _spawn([sys.executable, str(candidate)])
    return False


def launch_manager_konsole() -> bool:
    """**M**: the console MP Mix Manager in a dedicated Konsole window.

    Konsole is the terminal the suite documents for the manager. The fallback
    list is the same order the rest of the project uses, so M still does
    something on a desktop without Konsole.
    """
    script = ROOT / "Mix_Archive_Manager.sh"
    if not script.is_file():
        return False
    terminals = (
        ("konsole", ["konsole", "-e", "bash", str(script)]),
        ("ptyxis", ["ptyxis", "--", "bash", str(script)]),
        ("gnome-terminal", ["gnome-terminal", "--", "bash", str(script)]),
        ("xfce4-terminal", ["xfce4-terminal", "-e", f"bash '{script}'"]),
        ("alacritty", ["alacritty", "-e", "bash", str(script)]),
        ("kitty", ["kitty", "bash", str(script)]),
        ("xterm", ["xterm", "-e", "bash", str(script)]),
    )
    for name, argv in terminals:
        if shutil.which(name):
            return _spawn(argv)
    return False


# ------------------------------------------------------------------------------
# spawning a pane
# ------------------------------------------------------------------------------
def _child_env() -> dict:
    """Environment for a pane started from another MP process.

    Each pane is a fresh host: the flags that identify the window it was
    opened from (a deck namespace, a manager session) must not follow it.
    GDK is pinned to X11 where an X display exists, like every other glass
    window, so the pane has an X11 id for the attached visualizer to stack
    against.
    """
    drop = {
        "MP_GUI_SESSION",
        "MP_LIQUID_GLASS_MANAGER",
        "MP_LIQUID_GLASS_MIXER",
        "MP_PLAYER_NAMESPACE",
        "MP_VISUALIZER_COMPANION",
        "MP_VISUALIZER_DETACHED",
        "MP_VISUALIZER_MATCH_GUI",
        "MP_VISUALIZER_MATCH_BLACK_GLASS",
        "MP_BLACK_GLASS_PID",
    }
    env = {key: value for key, value in os.environ.items() if key not in drop}
    if env.get("DISPLAY"):
        env["GDK_BACKEND"] = "x11"
    return env


def spawn_command(width: int, height: int, x: Optional[int] = None, y: Optional[int] = None,
                  source: str = "") -> list:
    """The argv used to open one pane, kept separate so a test can read it."""
    width = max(MIN_WIDTH, int(width))
    height = max(MIN_HEIGHT, int(height))
    if x is None or y is None:
        x, y = _centred(width, height)
    argv = [sys.executable, str(SCRIPT), "--geometry", f"{width}x{height}+{int(x)}+{int(y)}"]
    if source:
        argv.extend(["--source", str(source)])
    return argv


def spawn_black_glass(width: int, height: int, x: Optional[int] = None,
                      y: Optional[int] = None, source: str = "") -> str:
    """Open one pane at exactly ``width`` x ``height``.

    Returns ``opened``, ``limit`` (eight already open), ``unsupported`` (no
    display or no GTK) or ``failed``.
    """
    if not black_glass_supported():
        return "unsupported"
    if live_count() >= MAX_WINDOWS:
        return "limit"
    argv = spawn_command(width, height, x, y, source)
    if not _spawn(argv, env=_child_env()):
        return "failed"
    return "opened"


def spawn_black_glass_for_host(source: str = "") -> str:
    """Open a pane at the size of the calling window.

    The caller is the child of a glass host, so its rectangle is read from the
    record the host publishes, matched on ``MP_HOST_PID``.
    """
    geometry = host_geometry()
    if geometry is None:
        return "no-geometry"
    x, y, width, height = geometry
    return spawn_black_glass(width, height, x, y, source)


def spawn_black_glass_for_window(window, source: str = "") -> str:
    """Open a pane at a live GTK window's rectangle (the mixer)."""
    try:
        width = int(window.get_allocated_width())
        height = int(window.get_allocated_height())
        try:
            x, y = window.get_position()
        except Exception:
            x, y = _centred(width, height)
    except Exception:
        return "no-geometry"
    return spawn_black_glass(width, height, int(x), int(y), source)


def spawn_message(result: str) -> str:
    """User-facing line for a spawn result, shared by every caller."""
    if result == "limit":
        return limit_message()
    if result == "unsupported":
        return "MP Black Glass needs a Linux/X11 desktop with GTK."
    if result == "no-geometry":
        return "MP Black Glass could not read this window's size."
    if result == "failed":
        return "MP Black Glass could not be started."
    return ""


# ------------------------------------------------------------------------------
# the window
# ------------------------------------------------------------------------------
def _import_gtk():
    """Import GTK3 and Cairo. Kept inside the process that draws the pane."""
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("Pango", "1.0")
    from gi.repository import Gdk, GLib, Gtk, Pango
    return GLib, Gtk, Gdk, Pango


class BlackGlassWindow:
    """One borderless black (or rainbow) glass pane."""

    def __init__(self, width: int, height: int, x: int, y: int, source: str = "") -> None:
        self.width = max(MIN_WIDTH, int(width))
        self.height = max(MIN_HEIGHT, int(height))
        self.x = int(x)
        self.y = int(y)
        self.source = str(source or "").strip()

        self.transparency = DEFAULT_TRANSPARENCY
        self.rainbow = False
        self.hue = 0.0

        self._started = time.monotonic()
        self._last_tick = self._started
        self._last_draw: Optional[Tuple] = None
        self._dirty = True

        # Hover fade: the slider is only ever drawn while the pointer is over
        # the pane, and it eases in and out rather than popping.
        self._hover = False
        self._slider_alpha = 0.0

        self._drag = ""          # "", "move", "resize", "slider"
        self._press = (0.0, 0.0)
        self._press_origin = (0, 0)
        self._press_size = (0, 0)
        self._cursor = (0.0, 0.0)

        self._GLib = self._Gtk = self._Gdk = self._Pango = None
        self._window = self._area = None
        self._timer = 0
        self._closing = False
        self.wid = 0

    # -- lifecycle --------------------------------------------------------
    def run(self) -> int:
        GLib, Gtk, Gdk, Pango = _import_gtk()
        self._GLib, self._Gtk, self._Gdk, self._Pango = GLib, Gtk, Gdk, Pango
        window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        window.set_title(TITLE)
        window.set_decorated(False)
        window.set_app_paintable(True)
        window.set_resizable(True)
        window.set_default_size(self.width, self.height)
        window.set_position(Gtk.WindowPosition.NONE)
        window.set_border_width(0)
        # A pane belongs to the window that opened it, so it stays above the
        # desktop rather than disappearing behind the next click.
        try:
            window.set_keep_above(True)
        except Exception:
            pass
        try:
            hints = Gdk.Geometry()
            hints.min_width, hints.min_height = MIN_WIDTH, MIN_HEIGHT
            window.set_geometry_hints(None, hints, Gdk.WindowHints.MIN_SIZE)
        except Exception:
            pass
        screen = window.get_screen()
        visual = screen.get_rgba_visual() if screen is not None else None
        if visual is not None:
            window.set_visual(visual)
        self._clear_css(Gtk, window)

        area = Gtk.DrawingArea()
        area.set_can_focus(True)
        area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.BUTTON1_MOTION_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
            | Gdk.EventMask.STRUCTURE_MASK
        )
        area.connect("draw", self._on_draw)
        area.connect("button-press-event", self._on_press)
        area.connect("button-release-event", self._on_release)
        area.connect("motion-notify-event", self._on_motion)
        area.connect("enter-notify-event", self._on_enter)
        area.connect("leave-notify-event", self._on_leave)
        window.add(area)
        window.connect("key-press-event", self._on_key)
        window.connect("configure-event", self._on_configure)
        window.connect("delete-event", self._on_delete)
        window.connect("destroy", self._on_destroy)

        self._window, self._area = window, area
        window.show_all()
        window.resize(self.width, self.height)
        window.move(self.x, self.y)
        area.grab_focus()
        self.wid = self._window_id()
        self._apply_opacity()
        self._publish()

        self._timer = GLib.timeout_add(FRAME_MS, self._tick)
        area.queue_draw()
        try:
            Gtk.main()
        finally:
            if self._timer:
                try:
                    GLib.source_remove(self._timer)
                except Exception:
                    pass
                self._timer = 0
        return 0

    def _clear_css(self, Gtk, window) -> None:
        """Let Cairo paint the whole surface; GTK's theme must stay out of it."""
        try:
            provider = Gtk.CssProvider()
            provider.load_from_data(
                b"window, drawingarea { background-color: transparent; background-image: none; }"
            )
            for widget in (window,):
                Gtk.StyleContext.add_provider(
                    widget.get_style_context(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )
        except Exception:
            pass

    def _window_id(self) -> int:
        try:
            gdk_window = self._window.get_window()
            return int(gdk_window.get_xid()) if gdk_window is not None else 0
        except Exception:
            return 0

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            detach_visualizer(os.getpid())
        except Exception:
            pass
        release_slot(os.getpid())
        try:
            (record_path(os.getpid())).unlink()
        except OSError:
            pass
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception:
            pass

    def _on_delete(self, *_args) -> bool:
        self.close()
        return True

    def _on_destroy(self, *_args) -> None:
        release_slot(os.getpid())
        try:
            self._GLib.main_quit()
        except Exception:
            pass

    # -- geometry ---------------------------------------------------------
    def _on_configure(self, _window, event) -> bool:
        self.x, self.y = int(event.x), int(event.y)
        self.width, self.height = max(MIN_WIDTH, int(event.width)), max(MIN_HEIGHT, int(event.height))
        self.wid = self._window_id() or self.wid
        self._dirty = True
        self._publish()
        return False

    def _publish(self) -> None:
        publish_geometry(self.x, self.y, self.width, self.height, pid=os.getpid(), wid=self.wid)

    def _apply_opacity(self) -> None:
        try:
            self._window.set_opacity(transparency_to_opacity(self.transparency))
        except Exception:
            pass

    # -- input ------------------------------------------------------------
    def _slider_zone(self) -> Tuple[float, float]:
        top = float(self.height) - SLIDER_HIT_TOP
        bottom = float(self.height) - SLIDER_HIT_BOTTOM
        return top, bottom

    def _in_slider(self, x: float, y: float) -> bool:
        top, bottom = self._slider_zone()
        if not (top <= y <= bottom):
            return False
        left, right = slider_track(self.width)
        # Widen the hit area a little at each end so the 10 % and 90 % stops
        # are not a pixel hunt.
        return (left - 12.0) <= x <= (right + 12.0)

    def _on_press(self, _area, event) -> bool:
        if getattr(event, "button", 1) != 1:
            return False
        x, y = float(event.x), float(event.y)
        self._press = (x, y)
        self._cursor = (x, y)
        if self._in_slider(x, y):
            self._drag = "slider"
            self.transparency = transparency_from_x(x, self.width)
            self._apply_opacity()
            self._dirty = True
            self._area.queue_draw()
            return True
        if x >= float(self.width) - RESIZE_GRIP and y >= float(self.height) - RESIZE_GRIP:
            self._drag = "resize"
            self._press_size = (self.width, self.height)
            return True
        self._drag = "move"
        self._press_origin = (self.x, self.y)
        return True

    def _on_motion(self, _area, event) -> bool:
        x, y = float(event.x), float(event.y)
        self._cursor = (x, y)
        if self._drag == "slider":
            self.transparency = transparency_from_x(x, self.width)
            self._apply_opacity()
            self._dirty = True
            self._area.queue_draw()
            return True
        if self._drag == "resize":
            width = max(MIN_WIDTH, int(self._press_size[0] + (x - self._press[0])))
            height = max(MIN_HEIGHT, int(self._press_size[1] + (y - self._press[1])))
            self._window.resize(width, height)
            return True
        if self._drag == "move":
            dx = int(x - self._press[0])
            dy = int(y - self._press[1])
            self._window.move(self._press_origin[0] + dx, self._press_origin[1] + dy)
            return True
        return False

    def _on_release(self, _area, event) -> bool:
        if getattr(event, "button", 1) == 1:
            if self._drag == "resize":
                self._window.resize(self.width, self.height)
            self._drag = ""
        return False

    def _on_enter(self, *_args) -> bool:
        self._hover = True
        return False

    def _on_leave(self, *_args) -> bool:
        self._hover = False
        return False

    def _on_key(self, _window, event) -> bool:
        Gdk = self._Gdk
        key = int(getattr(event, "keyval", 0))
        if key in (Gdk.KEY_Escape, Gdk.KEY_q, Gdk.KEY_Q):
            self.close()
            return True
        if key in (Gdk.KEY_r, Gdk.KEY_R):
            self.rainbow = not self.rainbow
            self._started = time.monotonic()
            self.hue = hue_at(0.0)
            self._dirty = True
            self._area.queue_draw()
            return True
        if key in (Gdk.KEY_z, Gdk.KEY_Z):
            if attached_visualizer_pid(os.getpid()):
                detach_visualizer(os.getpid())
            else:
                attach_visualizer(os.getpid())
            self._dirty = True
            self._area.queue_draw()
            return True
        if key in (Gdk.KEY_g, Gdk.KEY_G):
            launch_mp_gui()
            return True
        if key in (Gdk.KEY_l, Gdk.KEY_L):
            launch_mp_player()
            return True
        if key in (Gdk.KEY_m, Gdk.KEY_M):
            launch_manager_konsole()
            return True
        if key in (Gdk.KEY_w, Gdk.KEY_W):
            spawn_black_glass(self.width, self.height, self.x + 24, self.y + 24, self.source)
            return True
        return False

    # -- animation --------------------------------------------------------
    def _tick(self) -> bool:
        if self._window is None:
            return False
        now = time.monotonic()
        dt = max(0.0, now - self._last_tick)
        self._last_tick = now

        target = 1.0 if (self._hover or self._drag == "slider") else 0.0
        if abs(self._slider_alpha - target) <= 0.001:
            self._slider_alpha = target
        else:
            step = dt / max(0.001, SLIDER_FADE_SECONDS)
            if self._slider_alpha < target:
                self._slider_alpha = min(target, self._slider_alpha + step)
            else:
                self._slider_alpha = max(target, self._slider_alpha - step)
            self._dirty = True

        if self.rainbow:
            # Ascending hue: the pane keeps walking the scale while it is on.
            self.hue = hue_at(now - self._started)
            self._dirty = True

        if self._dirty:
            self._dirty = False
            self._area.queue_draw()
        return True

    # -- drawing ----------------------------------------------------------
    def _rounded_path(self, cr, width: float, height: float, radius: float) -> None:
        radius = max(0.0, min(radius, width / 2.0, height / 2.0))
        cr.new_sub_path()
        cr.arc(width - radius, radius, radius, -math.pi / 2.0, 0.0)
        cr.arc(width - radius, height - radius, radius, 0.0, math.pi / 2.0)
        cr.arc(radius, height - radius, radius, math.pi / 2.0, math.pi)
        cr.arc(radius, radius, radius, math.pi, 3.0 * math.pi / 2.0)
        cr.close_path()

    def _on_draw(self, area, cr) -> bool:
        width = max(1.0, float(area.get_allocated_width()))
        height = max(1.0, float(area.get_allocated_height()))

        # Clear to fully transparent so the rounded corners show the desktop.
        cr.set_operator(1)  # cairo.OPERATOR_SOURCE
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.0)
        cr.paint()
        cr.set_operator(2)  # cairo.OPERATOR_OVER

        self._rounded_path(cr, width, height, CORNER_RADIUS)
        cr.save()
        cr.clip()

        if self.rainbow:
            head = hsv_to_rgb(self.hue, 0.92, 0.62)
            tail = hsv_to_rgb(self.hue + 62.0, 0.92, 0.34)
            gradient = self._linear(cr, 0.0, 0.0, width * 0.25, height)
            gradient.add_color_stop_rgba(0.0, head[0], head[1], head[2], 1.0)
            gradient.add_color_stop_rgba(1.0, tail[0], tail[1], tail[2], 1.0)
            cr.set_source(gradient)
        else:
            gradient = self._linear(cr, 0.0, 0.0, 0.35 * width, height)
            gradient.add_color_stop_rgba(0.0, 0.055, 0.065, 0.090, 1.0)
            gradient.add_color_stop_rgba(0.55, 0.025, 0.030, 0.044, 1.0)
            gradient.add_color_stop_rgba(1.0, 0.008, 0.010, 0.016, 1.0)
            cr.set_source(gradient)
        cr.paint()

        # A soft sheen across the top-left corner: the "glass" in black glass.
        sheen = self._radial(cr, width * 0.22, 0.0, max(width, height) * 0.75)
        sheen.add_color_stop_rgba(0.0, 1.0, 1.0, 1.0, 0.075)
        sheen.add_color_stop_rgba(1.0, 1.0, 1.0, 1.0, 0.0)
        cr.set_source(sheen)
        cr.paint()
        cr.restore()

        # Rim.
        self._rounded_path(cr, width - 1.0, height - 1.0, CORNER_RADIUS)
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.13 if not self.rainbow else 0.28)
        cr.set_line_width(1.0)
        cr.stroke()

        self._draw_labels(cr, width, height)
        if self._slider_alpha > 0.01:
            self._draw_slider(cr, width, height, self._slider_alpha)
        return False

    def _linear(self, cr, x0: float, y0: float, x1: float, y1: float):
        import cairo  # provided by PyGObject's cairo bindings
        return cairo.LinearGradient(x0, y0, x1, y1)

    def _radial(self, cr, x0: float, y0: float, radius: float):
        import cairo
        return cairo.RadialGradient(x0, y0, 0.0, x0, y0, max(1.0, radius))

    def _text(self, cr, x: float, y: float, text: str, size: float,
              rgba: Tuple[float, float, float, float], bold: bool = False) -> None:
        cr.select_font_face(
            "Sans", 0, 1 if bold else 0  # cairo.FONT_SLANT_NORMAL, WEIGHT_BOLD/NORMAL
        )
        cr.set_font_size(size)
        cr.set_source_rgba(*rgba)
        cr.move_to(x, y)
        cr.show_text(text)

    def _ink(self) -> Tuple[float, float, float]:
        """Text colour that stays readable on both panes."""
        if self.rainbow:
            return 1.0, 1.0, 1.0
        return 0.86, 0.91, 0.98

    def _draw_labels(self, cr, width: float, height: float) -> None:
        ink = self._ink()
        title = "BLACK GLASS" if not self.rainbow else "RAINBOW GLASS"
        self._text(cr, 26.0, 38.0, title, 13.0, (*ink, 0.72), bold=True)
        if self.source:
            self._text(cr, 26.0, 58.0, self.source.upper(), 10.0, (*ink, 0.38))

        chips = []
        if self.rainbow:
            chips.append("R  BLACK GLASS")
        else:
            chips.append("R  RAINBOW")
        if attached_visualizer_pid(os.getpid()):
            chips.append("Z  VISUALIZER ON")
        else:
            chips.append("Z  VISUALIZER")
        chip_line = "     ".join(chips)
        self._text(cr, 26.0, height - 58.0, chip_line, 10.5, (*ink, 0.52))

        hint = "G  MP GUI    L  LIQUID PLAYER    M  MIX MANAGER    W  NEW GLASS    Q / ESC  CLOSE"
        self._text(cr, 26.0, height - 40.0, hint, 10.0, (*ink, 0.34))

        # Faint resize grip, so the corner reads as draggable.
        cr.set_source_rgba(*ink, 0.20)
        cr.set_line_width(1.0)
        for offset in (6.0, 12.0, 18.0):
            cr.move_to(width - 24.0 + (offset - 6.0), height - 8.0)
            cr.line_to(width - 8.0, height - 24.0 + (offset - 6.0))
            cr.stroke()

    def _draw_slider(self, cr, width: float, height: float, alpha: float) -> None:
        left, right = slider_track(int(width))
        bar_y = height - SLIDER_BOTTOM
        bar_h = float(SLIDER_HEIGHT)
        colour = slider_colour(self.rainbow, self.hue)

        # Track.
        cr.set_source_rgba(colour[0], colour[1], colour[2], 0.18 * alpha)
        cr.rectangle(left, bar_y, right - left, bar_h)
        cr.fill()

        # Filled portion up to the thumb.
        thumb_x = slider_thumb_x(self.transparency, int(width))
        if thumb_x > left:
            cr.set_source_rgba(colour[0], colour[1], colour[2], 0.52 * alpha)
            cr.rectangle(left, bar_y, thumb_x - left, bar_h)
            cr.fill()

        # Thumb.
        cr.set_source_rgba(colour[0], colour[1], colour[2], 0.92 * alpha)
        cr.rectangle(thumb_x - 4.5, bar_y - 3.5, 9.0, bar_h + 7.0)
        cr.fill()

        # Label above the bar, in the same colour as the bar.
        label = f"TRANSPARENCY  {int(round(self.transparency))}%"
        ink = self._ink()
        self._text(cr, left, bar_y - 12.0, label, 9.5, (*ink, 0.58 * alpha))
        self._text(cr, right - 64.0, bar_y - 12.0,
                   f"{int(MIN_TRANSPARENCY)}% — {int(MAX_TRANSPARENCY)}%", 9.0,
                   (*ink, 0.34 * alpha))


# ------------------------------------------------------------------------------
# command line
# ------------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="black_glass_window.py",
        description="Open an MP Black Glass window (pressed with W in the MP windows).",
    )
    parser.add_argument("--geometry", default=None, help="WIDTHxHEIGHT+X+Y")
    parser.add_argument("--source", default="", help="Name of the window this pane was opened from")
    parser.add_argument("--spawn-host", action="store_true",
                        help="Open a pane at the calling window's published size and exit")
    parser.add_argument("--spawn-size", default=None,
                        help="Open a pane at WIDTHxHEIGHT and exit (used by the mixer)")
    parser.add_argument("--status", action="store_true", help="Print the live window count")
    args = parser.parse_args(argv)

    if args.status:
        print(json.dumps({"running": live_count(), "maximum": MAX_WINDOWS}))
        return 0

    if args.spawn_host:
        result = spawn_black_glass_for_host(args.source)
        message = spawn_message(result)
        if message:
            print(message, file=sys.stderr)
        return 0 if result == "opened" else 1

    if args.spawn_size:
        parsed = parse_geometry(args.spawn_size + "+0+0" if args.spawn_size.count("+") == 0 else args.spawn_size)
        if not parsed:
            print("black glass: --spawn-size wants WIDTHxHEIGHT", file=sys.stderr)
            return 2
        result = spawn_black_glass(parsed[2], parsed[3], None, None, args.source)
        return 0 if result == "opened" else 1

    if not black_glass_supported():
        print("MP Black Glass needs a Linux/X11 desktop with GTK.", file=sys.stderr)
        return 1
    geometry = parse_geometry(args.geometry) if args.geometry else None
    if geometry is None:
        fallback = host_geometry()
        if fallback:
            geometry = fallback
        else:
            width, height = 1100, 760
            x, y = _centred(width, height)
            geometry = (x, y, width, height)
    x, y, width, height = geometry

    # The window claims its own slot before drawing anything. A ninth launch
    # that slipped past the caller's count exits here with a message instead
    # of opening a window the limit forbids.
    if not claim_slot(os.getpid()):
        print(limit_message(), file=sys.stderr)
        return 1
    try:
        window = BlackGlassWindow(width, height, x, y, args.source)
        return window.run()
    finally:
        release_slot(os.getpid())


if __name__ == "__main__":
    raise SystemExit(main())
