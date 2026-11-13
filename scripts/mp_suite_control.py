#!/usr/bin/env python3
"""Launch, detect, and close the MP desktop apps from the Web Companion.

The companion usually runs as a systemd user service, so the phone may be the
only interface in front of the user. These helpers let the dashboard start
**MP Gui**, the **MP Mix Manager (CLI)**, **MP Liquid Player**, and the
**MP Liquid Visualizer**, report which of them are live (so the dashboard can
paint a pressed button), and close all four without stopping the shared tray
icon or the companion itself.

Linux only: the process table comes from ``/proc`` and the launch commands are
the same shell launchers the tray menu and desktop shortcuts use. Process
classification is delegated to :mod:`mp_system_tray` when it is importable, so
the dashboard cannot disagree with the icon about what is running (in
particular, a manager hosted inside the MP Gui window is not the CLI).

There is also a fifth control, the **MP Visualizer** button at the bottom of
the dashboard. It is not a launch/focus toggle like the four suite buttons: it
owns exactly one window for its whole life — press once to open it, again to
take that same window fullscreen, and again to close it. See
:mod:`mp_visualizer_control` for the state file and the signals, and
:func:`web_visualizer_action` for the three-state decision.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

try:
    import mp_visualizer_control as visualizer_control
except ImportError:  # imported as scripts.mp_suite_control
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import mp_visualizer_control as visualizer_control  # type: ignore[no-redef]
    except ImportError:
        visualizer_control = None  # type: ignore[assignment]

ROLE_GUI = "gui"
ROLE_MANAGER = "manager"
ROLE_LIQUID = "liquid"
ROLE_VISUALIZER = "visualizer"

ROLE_ORDER = (ROLE_GUI, ROLE_MANAGER, ROLE_LIQUID, ROLE_VISUALIZER)

APP_LABELS = {
    ROLE_GUI: "MP Gui",
    ROLE_MANAGER: "MP Mix Manager (CLI)",
    ROLE_LIQUID: "MP Liquid Player",
    ROLE_VISUALIZER: "MP Liquid Visualizer",
}

APP_ICONS = {
    ROLE_GUI: "🖥",
    ROLE_MANAGER: "⌨",
    ROLE_LIQUID: "🎵",
    ROLE_VISUALIZER: "🌌",
}

# Window titles used for "already running, bring it forward".
FOCUS_TITLES = {
    ROLE_GUI: "MP Mix Manager (Liquid Glass",
    ROLE_MANAGER: "MP Mix Archive Manager",
    ROLE_LIQUID: "MP Audio Player (Liquid Glass",
    ROLE_VISUALIZER: "MP Liquid Visualizer",
}

# The visualizer is not one of the tray's three roles, so it is matched here.
_VISUALIZER_MARKERS = (
    "MP_Liquid_Visualizer",
    "mp_liquid_visualizer_vulkan",
    "launch_liquid_visualizer",
)

# Last-resort classification when mp_system_tray cannot be imported.
_FALLBACK_MARKERS = {
    ROLE_GUI: ("MP_Gui.sh",),
    ROLE_MANAGER: ("Mix_Archive_Manager.sh", "mix-archive-manager", "launch-manager"),
    ROLE_LIQUID: ("MP_Audio_Player_Liquid_Glass", "launch-liquid-glass", "launch_liquid_glass"),
    ROLE_VISUALIZER: _VISUALIZER_MARKERS,
}

_CLOSE_TIMEOUT_SECONDS = 3.0
_STATUS_CACHE_SECONDS = 1.5
# Refresh the tray's keepalive marker at most this often while a dashboard is
# watching, so the icon outlives a Close All but still expires once the phone
# stops polling and nothing is open.
_KEEPALIVE_REFRESH_SECONDS = 30.0

_status_cache: Optional[tuple[float, dict]] = None
_keepalive_touched_at = 0.0


def _scripts_dir() -> Path:
    return Path(__file__).resolve().parent


def base_dir() -> Path:
    """Repository root: the directory that holds MP_Gui.sh and bin/."""
    here = _scripts_dir()
    return here.parent if here.name == "scripts" else here


def is_supported() -> bool:
    """The dashboard buttons exist on Linux only."""
    return sys.platform.startswith("linux") and Path("/proc").is_dir()


def tty_mode_active() -> bool:
    """True while MP Mix Manager is running in TTY Audio Mode.

    In that mode the desktop is left for a text console and every MP desktop
    window (MP Gui, the CLI manager, the Liquid Player and the visualizer) is
    deliberately kept closed, so the dashboard must not offer to open one. The
    flag file survives the logout and is what a background companion sees; the
    environment variable covers a manager started in the same shell.
    """
    if os.environ.get("MP_MIX_MANAGER_TTY_MODE", "").strip() == "1":
        return True
    try:
        return (config_dir() / "tty_audio_mode").is_file()
    except OSError:
        return False


def app_definitions() -> list[dict[str, str]]:
    """Static button metadata, in launch order. Never touches the process table."""
    return [
        {"id": role, "label": APP_LABELS[role], "icon": APP_ICONS[role]}
        for role in ROLE_ORDER
    ]


# ------------------------------------------------------------------------------
# PROCESS DETECTION
# ------------------------------------------------------------------------------
def _tray_module():
    """Import the shared tray helpers, which own the classification rules."""
    try:
        scripts = str(_scripts_dir())
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import mp_system_tray  # noqa: WPS433 - optional sibling module

        return mp_system_tray
    except Exception:
        return None


def _process_table() -> list:
    tray = _tray_module()
    if tray is None:
        return []
    try:
        return tray.read_proc_table()
    except Exception:
        return []


def _fallback_roles(processes: Iterable) -> set[str]:
    found: set[str] = set()
    for proc in processes:
        cmdline = getattr(proc, "cmdline", "") or ""
        for role, markers in _FALLBACK_MARKERS.items():
            if role in found:
                continue
            if any(marker in cmdline for marker in markers):
                found.add(role)
    return found


def visualizer_running(processes: Iterable) -> bool:
    for proc in processes:
        cmdline = getattr(proc, "cmdline", "") or ""
        if any(marker in cmdline for marker in _VISUALIZER_MARKERS):
            return True
    return False


def running_roles(processes: Optional[Sequence] = None) -> set[str]:
    """Which of the four apps are live right now."""
    if not is_supported():
        return set()
    table = list(processes) if processes is not None else _process_table()
    tray = _tray_module()
    roles: set[str] = set()
    if tray is not None:
        try:
            roles |= {r for r in tray.roles_present(table, {os.getpid()}) if r in ROLE_ORDER}
        except Exception:
            roles |= _fallback_roles(table)
    else:
        roles |= _fallback_roles(table)
    if visualizer_running(table):
        roles.add(ROLE_VISUALIZER)
    return roles


def tray_running() -> bool:
    """The shared icon is a separate process and is never closed by this module."""
    tray = _tray_module()
    if tray is None:
        return False
    try:
        return tray.TrayLock.held(tray.config_dir() / "mp_system_tray.lock")
    except Exception:
        return False


def _touch_keepalive() -> None:
    """Keep the icon alive while a dashboard is actively polling."""
    global _keepalive_touched_at
    now = time.time()
    if (now - _keepalive_touched_at) < _KEEPALIVE_REFRESH_SECONDS:
        return
    tray = _tray_module()
    if tray is None:
        return
    try:
        if tray.TrayLock.held(tray.config_dir() / "mp_system_tray.lock"):
            tray.request_keepalive(tray.config_dir())
            _keepalive_touched_at = now
    except Exception:
        return


def app_status() -> dict:
    """Process table walking is expensive; several phones may poll at once."""
    global _status_cache
    now = time.time()
    if _status_cache is not None and (now - _status_cache[0]) < _STATUS_CACHE_SECONDS:
        return dict(_status_cache[1])
    supported = is_supported()
    tty_mode = supported and tty_mode_active()
    roles = running_roles() if supported else set()
    if supported and not tty_mode:
        _touch_keepalive()
    payload = {
        "supported": supported,
        "platform": sys.platform,
        "tty_mode": tty_mode,
        "launch_disabled": (not supported) or tty_mode,
        "apps": [
            {
                "id": role,
                "label": APP_LABELS[role],
                "icon": APP_ICONS[role],
                "running": role in roles,
            }
            for role in ROLE_ORDER
        ],
        "tray_running": tray_running() if supported else False,
        "visualizer": web_visualizer_status() if supported else {"running": False, "fullscreen": False},
    }
    _status_cache = (now, payload)
    return dict(payload)


def reset_status_cache() -> None:
    global _status_cache
    _status_cache = None


# ------------------------------------------------------------------------------
# LAUNCH
# ------------------------------------------------------------------------------
def config_dir() -> Path:
    tray = _tray_module()
    if tray is not None:
        try:
            return tray.config_dir()
        except Exception:
            pass
    override = os.environ.get("MP_TRAY_CONFIG_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".config" / "mix-manager"


def launch_argv(role: str, base: Optional[Path] = None) -> list[str]:
    """The same launchers the tray menu and the desktop shortcuts use."""
    base = Path(base) if base is not None else base_dir()
    if role == ROLE_GUI:
        return ["bash", str(base / "MP_Gui.sh")]
    if role == ROLE_MANAGER:
        for candidate in (base / "bin" / "launch-manager", base / "bin" / "mix-archive-manager"):
            if candidate.is_file():
                return [str(candidate)]
        return ["bash", str(base / "Mix_Archive_Manager.sh")]
    if role == ROLE_LIQUID:
        for candidate in (
            base / "scripts" / "launch-liquid-glass",
            base / "scripts" / "launch_liquid_glass.sh",
        ):
            if candidate.is_file():
                return [str(candidate)]
        return [sys.executable, str(base / "MP_Audio_Player_Liquid_Glass_OpenGL.py")]
    if role == ROLE_VISUALIZER:
        launcher = base / "scripts" / "launch_liquid_visualizer.sh"
        if launcher.is_file():
            return ["bash", str(launcher)]
        return [sys.executable, str(base / "MP_Liquid_Visualizer.py")]
    raise ValueError(f"unknown app role: {role}")


_SESSION_ENV_KEYS = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
)

_session_env_cache: Optional[dict[str, str]] = None


def session_environment() -> dict[str, str]:
    """The graphical session's variables, even from a systemd user service.

    A unit does not inherit ``DISPLAY``/``WAYLAND_DISPLAY`` from the desktop,
    so a launcher started from the phone would otherwise have no display to
    open on. ``systemctl --user show-environment`` reports what the session
    actually uses; the values are cached because launching is not frequent.
    """
    global _session_env_cache
    if _session_env_cache is not None:
        return dict(_session_env_cache)

    found: dict[str, str] = {}
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                key, _, value = line.partition("=")
                if key in _SESSION_ENV_KEYS and value:
                    found[key] = value
    except (OSError, subprocess.TimeoutExpired):
        pass

    _session_env_cache = found
    return dict(found)


def _spawn(argv: list[str], base: Path, extra_env: Optional[dict[str, str]] = None) -> Optional[int]:
    """Start a launcher detached from this service; returns its pid."""
    config = config_dir()
    config.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    # A systemd user service does not always inherit the graphical session.
    for key, value in session_environment().items():
        env.setdefault(key, value)
    env.setdefault("DISPLAY", ":0")
    env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env.pop("MP_SYSTEM_TRAY_CHILD", None)
    for key, value in (extra_env or {}).items():
        env[key] = value
    log_path = config / "mp_web_launcher.log"
    with open(log_path, "ab") as log_file:
        proc = subprocess.Popen(
            argv,
            cwd=str(base),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )
    return int(proc.pid)


def _focus(role: str) -> bool:
    tray = _tray_module()
    if tray is not None:
        try:
            return bool(tray.focus_window(FOCUS_TITLES[role]))
        except Exception:
            return False
    return False


def launch_app(role: str) -> dict:
    """Focus the app when it is already open, otherwise start a new window."""
    reset_status_cache()
    if role not in ROLE_ORDER:
        return {"success": False, "action": "unknown", "app": role}
    if not is_supported():
        return {"success": False, "action": "unsupported", "app": role}
    if tty_mode_active():
        # TTY Audio Mode keeps every desktop window closed on purpose.
        return {"success": False, "action": "tty_mode", "app": role}
    base = base_dir()
    if role in running_roles():
        focused = _focus(role)
        return {
            "success": True,
            "action": "focus" if focused else "already_running",
            "app": role,
            "label": APP_LABELS[role],
        }
    try:
        _spawn(launch_argv(role, base), base)
    except OSError as error:
        return {"success": False, "action": "error", "app": role, "detail": str(error)}
    return {"success": True, "action": "launch", "app": role, "label": APP_LABELS[role]}


# ------------------------------------------------------------------------------
# MP VISUALIZER (the Web Companion's own window)
#
# The four suite buttons focus what is already open; this one is a three-state
# control over exactly one window. The window is identified by a random
# instance token that is passed in its environment and echoed in its command
# line, and the published state file carries the pid. Every signal is sent to
# that pid only, so a visualizer the user opened from the tray or a shortcut is
# never toggled and never closed.
# ------------------------------------------------------------------------------
def _visualizer_state() -> Optional[dict]:
    if visualizer_control is None:
        return None
    try:
        state = visualizer_control.read_state()
    except Exception:
        return None
    if state is None:
        return None
    if not visualizer_control.instance_is_live(state):
        # The window is gone (crashed, killed, or the machine restarted).
        # Clear the stale record so the next press opens a fresh window.
        try:
            visualizer_control.clear_state(state["instance"])
        except Exception:
            pass
        return None
    return state


def web_visualizer_status() -> dict:
    """What the MP Visualizer button should look like right now."""
    state = _visualizer_state()
    if state is None:
        return {"running": False, "fullscreen": False}
    return {
        "running": True,
        "fullscreen": bool(state.get("fullscreen")),
        "pending": bool(state.get("pending")),
        "instance": state.get("instance", ""),
    }


def web_visualizer_action() -> dict:
    """Open, go fullscreen, or close the one Web Companion visualizer window."""
    if not is_supported():
        return {"success": False, "action": "unsupported"}
    if visualizer_control is None:
        return {"success": False, "action": "error", "detail": "control module unavailable"}
    if tty_mode_active():
        # TTY Audio Mode has no desktop to draw a window on.
        return {"success": False, "action": "tty_mode"}

    state = _visualizer_state()
    if state is None:
        base = base_dir()
        instance = uuid.uuid4().hex
        env = {
            visualizer_control.ENV_INSTANCE: instance,
            visualizer_control.ENV_STATE: str(visualizer_control.state_path()),
            # A window the dashboard owns stands on its own. It must not bond
            # behind the Liquid Player, which would pin it to the player's
            # rectangle and refuse the fullscreen toggle.
            "MP_VISUALIZER_DETACHED": "1",
        }
        try:
            pid = _spawn(launch_argv(ROLE_VISUALIZER, base) + ["--web-instance", instance],
                         base, extra_env=env)
        except OSError as error:
            return {"success": False, "action": "error", "detail": str(error)}
        # Claim the button immediately. The window takes a moment to open, and
        # without this record a second tap would start a second window — the
        # one thing this button must never do. The launcher ``exec``s Python,
        # so the pid we record is the visualizer's own pid.
        if pid:
            try:
                visualizer_control.write_state({
                    "instance": instance,
                    "pid": int(pid),
                    "pending": True,
                    "fullscreen": False,
                    "title": APP_LABELS[ROLE_VISUALIZER],
                })
            except Exception:
                pass
        reset_status_cache()
        return {
            "success": True,
            "action": "launch",
            "label": APP_LABELS[ROLE_VISUALIZER],
            "state": "windowed",
        }

    if state.get("pending"):
        return {
            "success": True,
            "action": "already_running",
            "label": APP_LABELS[ROLE_VISUALIZER],
            "state": "windowed",
        }

    if not state.get("fullscreen"):
        toggled = visualizer_control.request_toggle_fullscreen(state)
        if not toggled:
            return {"success": False, "action": "error",
                    "detail": "the visualizer window did not accept the command"}
        return {
            "success": True,
            "action": "fullscreen",
            "label": APP_LABELS[ROLE_VISUALIZER],
            "state": "fullscreen",
        }

    closed = visualizer_control.request_close(state)
    reset_status_cache()
    if not closed:
        return {"success": False, "action": "error",
                "detail": "the visualizer window did not close"}
    return {
        "success": True,
        "action": "close",
        "label": APP_LABELS[ROLE_VISUALIZER],
        "state": "closed",
    }


# ------------------------------------------------------------------------------
# CLOSE ALL
# ------------------------------------------------------------------------------
def _ensure_tray(base: Path) -> None:
    """Bring the icon up when Close All is pressed with none running.

    The user asked for Close All to leave the tray icon in place, so pressing
    it must not depend on which app happened to start the tray first.
    """
    tray = _tray_module()
    if tray is None:
        return
    try:
        if tray.TrayLock.held(tray.config_dir() / "mp_system_tray.lock"):
            return
    except Exception:
        return
    launch = base / "scripts" / "mp_system_tray.sh"
    if not launch.is_file():
        return
    try:
        _spawn(["bash", str(launch), "--ensure"], base)
    except OSError:
        pass


def _fallback_close(base: Path) -> None:
    """Close the four apps when the tray helpers are unavailable."""
    script = base / "scripts" / "kill_all_mp_audio_players.sh"
    if not script.is_file():
        script = base / "kill_all_mp_audio_players.sh"
    if script.is_file():
        try:
            subprocess.run(
                ["bash", str(script), "-q"],
                timeout=30,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                start_new_session=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    for pattern in ("Mix_Archive_Manager\\.sh", "mix-archive-manager", "launch-manager"):
        try:
            subprocess.run(
                ["pkill", "-TERM", "-f", pattern],
                timeout=5,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def close_all_apps(base: Optional[Path] = None) -> dict:
    """Close MP Gui, the CLI manager, the player, and the visualizer.

    The shared tray icon is deliberately left alone: it is not one of the four
    roles, so ``shutdown_suite`` never signals it. The dashboard also asks the
    tray to stay, because a tray started by a shortcut (rather than autostart)
    otherwise quits a few seconds after the last window closes.
    """
    if not is_supported():
        return {
            "success": False,
            "action": "unsupported",
            "closed": [],
            "remaining": [],
            "tray_running": False,
        }
    if tty_mode_active():
        return {
            "success": False,
            "action": "tty_mode",
            "closed": [],
            "remaining": [],
            "tray_running": tray_running(),
        }
    base = Path(base) if base is not None else base_dir()
    before = running_roles()
    reset_status_cache()
    tray = _tray_module()
    if tray is not None:
        # Claim the icon *before* the windows disappear, otherwise a tray that
        # was started by a shortcut can begin its 8-second absent countdown.
        try:
            tray.request_keepalive(tray.config_dir())
        except Exception:
            pass
        _ensure_tray(base)
        try:
            tray.shutdown_suite(base)
        except Exception:
            _fallback_close(base)
    else:
        _fallback_close(base)

    deadline = time.time() + _CLOSE_TIMEOUT_SECONDS
    remaining = running_roles()
    while remaining and time.time() < deadline:
        time.sleep(0.15)
        remaining = running_roles()

    return {
        "success": not remaining,
        "action": "close_all",
        "closed": sorted(before - remaining),
        "remaining": sorted(remaining),
        "tray_running": tray_running(),
    }
