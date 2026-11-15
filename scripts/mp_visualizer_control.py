"""Control channel for the one MP Visualizer window the Web Companion owns.

The dashboard's **MP Visualizer** button is a three-state control: the first
press opens a window, the second takes that same window fullscreen, and the
third closes it again. To do that the companion has to reach one specific
visualizer process without touching any other visualizer the user may have
opened from the tray, a desktop shortcut, or the Liquid Player.

This module is the contract between the two processes:

``~/.config/mix-manager/mp_web_visualizer.json``
    Written by the visualizer after its window opens and whenever it enters or
    leaves fullscreen. Holds the instance token and the pid, so the companion
    knows both what to signal and what mode the window is really in (the user
    can press Z inside the window, so the companion cannot just count presses).

Signals
    ``SIGUSR1`` toggles fullscreen, ``SIGUSR2``/``SIGTERM`` close the window.
    Every signal is sent to the recorded pid only — never to a process group
    and never by name — so another visualizer on the machine is never touched.

The module is deliberately dependency free and cheap to import: the companion
loads it on every status poll, and the visualizer loads it before GLFW or
Vulkan are touched. It also works on the plain text console, where signals are
all that is available.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

ENV_INSTANCE = "MP_WEB_VISUALIZER_INSTANCE"
ENV_STATE = "MP_WEB_VISUALIZER_STATE_FILE"
STATE_FILE_NAME = "mp_web_visualizer.json"
# The visualizer publishes on open and on every mode change, not per frame.
PUBLISH_MIN_INTERVAL = 0.0
# A pending record (written while the launcher starts) is only trusted this
# long. A visualizer that never opens must not leave the button stuck. The
# window's own publish replaces it long before this expires; the margin covers
# a cold Vulkan/GLFW start and a player-size wait.
PENDING_TIMEOUT_SECONDS = 45.0
# How long a commanded mode is trusted before the window's own publish is
# believed instead. A fullscreen toggle is applied on the render thread on the
# next frame and then republished, so this only has to cover the couple of
# frames between the signal and the write. It exists so a *lost* command (a
# wedged window, a signal that never arrived) cannot latch the button in the
# wrong state forever -- the window's truthful publish wins once it expires.
COMMAND_GRACE_SECONDS = 2.0

# Phases the companion may record for a window it owns. ``pending`` means the
# launcher was started and the window has not published yet.
PHASE_PENDING = "pending"
PHASE_WINDOWED = "windowed"
PHASE_FULLSCREEN = "fullscreen"


def config_dir() -> Path:
    override = os.environ.get("MP_TRAY_CONFIG_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".config" / "mix-manager"


def state_path() -> Path:
    """Where both processes agree to meet. The companion passes it explicitly."""
    override = os.environ.get(ENV_STATE, "").strip()
    if override:
        return Path(override)
    return config_dir() / STATE_FILE_NAME


# ------------------------------------------------------------------------------
# STATE FILE
# ------------------------------------------------------------------------------
def read_state(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The last published state, or ``None`` when there is no usable file."""
    candidate = Path(path) if path is not None else state_path()
    try:
        raw = candidate.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    instance = str(data.get("instance", "")).strip()
    try:
        pid = int(data.get("pid", 0))
    except (TypeError, ValueError):
        return None
    pending = bool(data.get("pending", False))
    # A pending record is written by the companion the instant it spawns the
    # window, before the window has opened. It carries the launcher's pid (the
    # launcher ``exec``s Python, so the pid survives), which is what stops a
    # second tap from opening a second window during the startup delay.
    if not instance or pid < 0 or (pid <= 1 and not pending):
        return None
    if pending:
        try:
            updated = float(data.get("updated", 0.0))
        except (TypeError, ValueError):
            updated = 0.0
        if (time.time() - updated) > PENDING_TIMEOUT_SECONDS:
            # The window never opened. Remove the record so it cannot keep the
            # dashboard button latched; the next press starts a fresh window.
            try:
                candidate.unlink()
            except OSError:
                pass
            return None
    data["instance"] = instance
    data["pid"] = pid
    data["pending"] = pending
    data["fullscreen"] = bool(data.get("fullscreen", False))
    data["phase"] = _phase_from_record(data, pending)
    return data


def _phase_from_record(data: Dict[str, Any], pending: bool) -> str:
    """The mode the button should act on: the command it sent, else reality.

    The companion writes ``command`` the instant it asks the window to change
    mode, because the window can only apply a toggle on its next render frame
    and then republish. Without this an impatient second tap re-read the old
    mode and sent a *second* fullscreen toggle instead of a close -- the window
    stayed open and looked like the button did nothing.

    The command is trusted only for ``COMMAND_GRACE_SECONDS``. After that the
    window's own publish is authoritative again, so a command that was never
    delivered cannot latch the button in the wrong mode.
    """
    if pending:
        return PHASE_PENDING
    command = str(data.get("command", "")).strip()
    if command in (PHASE_WINDOWED, PHASE_FULLSCREEN):
        try:
            sent = float(data.get("command_at", 0.0))
        except (TypeError, ValueError):
            sent = 0.0
        if 0.0 <= (time.time() - sent) <= COMMAND_GRACE_SECONDS:
            return command
    return PHASE_FULLSCREEN if bool(data.get("fullscreen", False)) else PHASE_WINDOWED


def record_command(state: Dict[str, Any], phase: str, path: Optional[Path] = None) -> bool:
    """Remember the mode the companion just asked for, before the window reports.

    Merged into the existing record rather than replacing it, so the pid and
    instance token that identify the window survive. Returns False when the
    record could not be written, in which case the caller must not pretend the
    command took effect.
    """
    candidate = Path(path) if path is not None else state_path()
    current = read_state(candidate)
    if current is None:
        return False
    payload = dict(current)
    payload["command"] = phase
    payload["command_at"] = time.time()
    write_state(payload, candidate)
    return True


def write_state(state: Dict[str, Any], path: Optional[Path] = None) -> None:
    """Publish atomically so a reader never sees a half-written file."""
    candidate = Path(path) if path is not None else state_path()
    payload = dict(state)
    payload["updated"] = time.time()
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        temp = candidate.with_name(candidate.name + ".tmp")
        temp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(temp, candidate)
    except OSError:
        pass


def clear_state(instance: Optional[str] = None, path: Optional[Path] = None) -> None:
    """Remove the state file, but only when it still belongs to ``instance``.

    A window that is closing must never delete the record a *newer* window has
    already published under the same path.
    """
    candidate = Path(path) if path is not None else state_path()
    if instance is not None:
        current = read_state(candidate)
        if current is not None and current["instance"] != instance:
            return
    try:
        candidate.unlink()
    except OSError:
        pass


# ------------------------------------------------------------------------------
# PROCESS IDENTITY
# ------------------------------------------------------------------------------
def pid_alive(pid: int) -> bool:
    """True when the process exists and has not already finished.

    On Linux a process the companion started can stay in the table as a
    zombie until it is reaped. ``os.kill(pid, 0)`` succeeds for a zombie, so
    the state is checked too; otherwise "close" would wait out its whole
    escalation deadline on a window that has already gone.
    """
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        # The state character follows the (parenthesised) command name.
        close = stat.rfind(")")
        if close != -1:
            fields = stat[close + 1:].split()
            if fields and fields[0] in ("Z", "X", "x"):
                return False
    return True


def process_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", "replace")


def instance_is_live(state: Optional[Dict[str, Any]]) -> bool:
    """True only when the recorded pid is alive *and* still our instance.

    The command line carries the instance token, so a recycled pid belonging
    to some unrelated program can never be mistaken for the visualizer (and
    can never be signalled or closed by this module).
    """
    if not state:
        return False
    pid = int(state.get("pid", 0))
    if not pid_alive(pid):
        return False
    if sys.platform.startswith("linux"):
        cmdline = process_cmdline(pid)
        if cmdline and state["instance"] not in cmdline:
            return False
    return True


# ------------------------------------------------------------------------------
# SIGNALLING
# ------------------------------------------------------------------------------
def signal_for(name: str) -> Optional[int]:
    value = getattr(signal, name, None)
    return int(value) if value is not None else None


def request_toggle_fullscreen(state: Dict[str, Any]) -> bool:
    sig = signal_for("SIGUSR1")
    if sig is None or not instance_is_live(state):
        return False
    try:
        os.kill(int(state["pid"]), sig)
    except OSError:
        return False
    return True


def request_close(state: Dict[str, Any]) -> bool:
    """Ask the window to close, then insist if it is still there.

    Only the recorded pid is ever signalled. The escalation exists because a
    wedged GL/Vulkan window can ignore SIGTERM, and the button must still work.
    """
    if not instance_is_live(state):
        return False
    pid = int(state["pid"])
    sig = signal_for("SIGUSR2") or signal_for("SIGTERM")
    if sig is None:
        return False
    try:
        os.kill(pid, sig)
    except OSError:
        return False
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    kill = signal_for("SIGKILL")
    if kill is not None and instance_is_live(state):
        try:
            os.kill(pid, kill)
        except OSError:
            return False
    return True


# ------------------------------------------------------------------------------
# VISUALIZER SIDE
# ------------------------------------------------------------------------------
class WindowControl:
    """The visualizer's half of the channel.

    Install it once the renderer exists. ``pump()`` is called every frame: it
    applies a pending toggle or close on the render loop's own thread (never in
    the signal handler, where touching GLFW would be unsafe) and republishes the
    state file whenever the real window mode changes, whichever way it changed.
    """

    def __init__(self, instance: str = "") -> None:
        self.instance = (instance or os.environ.get(ENV_INSTANCE, "")).strip()
        self.enabled = bool(self.instance) and not sys.platform.startswith("win")
        self._toggle_pending = False
        self._close_pending = False
        self._published_fullscreen: Optional[bool] = None
        self._installed = False
        self._started = 0.0
        self._opened = False

    # -- signals (handlers only set flags) --------------------------------
    def _on_toggle(self, _signum, _frame) -> None:
        self._toggle_pending = True

    def _on_close(self, _signum, _frame) -> None:
        self._close_pending = True
        if not self._opened:
            # Still creating the window: there is no render loop to honour the
            # flag yet, so stop now rather than lingering with a half-built
            # window the companion can no longer reach.
            os._exit(0)

    def install(self) -> bool:
        """Replace the visualizer's default SIGTERM exit with a clean stop."""
        if not self.enabled or self._installed:
            return False
        installed = False
        for name, handler in (
            ("SIGUSR1", self._on_toggle),
            ("SIGUSR2", self._on_close),
            ("SIGTERM", self._on_close),
        ):
            sig = signal_for(name)
            if sig is None:
                continue
            try:
                signal.signal(sig, handler)
            except (OSError, ValueError):
                continue
            installed = True
        self._installed = installed
        return installed

    # -- publishing -------------------------------------------------------
    def _publish(self, fullscreen: bool, title: str = "") -> None:
        write_state({
            "instance": self.instance,
            "pid": os.getpid(),
            "fullscreen": bool(fullscreen),
            "title": title or "MP Visualizer",
            "started": self._started,
        })
        self._published_fullscreen = bool(fullscreen)

    def opened(self, renderer: Any) -> None:
        """Called once the window is on screen, before the first frame."""
        if not self.enabled:
            return
        self._started = time.time()
        self._opened = True
        # The dashboard decides this window's mode (windowed first, then
        # fullscreen), so the 4K auto-fullscreen and the player-size follow
        # must not take that decision away on the very first frame.
        for flag in ("_user_toggled_windowed", "_user_selected_window_mode"):
            try:
                setattr(renderer, flag, True)
            except Exception:
                pass
        self._publish(
            bool(getattr(renderer, "is_fullscreen", False)),
            str(getattr(renderer, "title", "") or ""),
        )

    def pump(self, renderer: Any) -> None:
        """Apply pending commands and keep the published mode truthful."""
        if not self.enabled:
            return
        if self._close_pending:
            try:
                renderer.request_close()
            except Exception:
                pass
            return
        if self._toggle_pending:
            self._toggle_pending = False
            try:
                renderer.toggle_fullscreen()
            except Exception:
                pass
            # The dashboard owns this window's mode. Mark the choice as the
            # user's so neither the 4K auto-fullscreen rule nor the player-size
            # follow pulls the window back out of the state just requested.
            for flag in ("_user_toggled_windowed", "_user_selected_window_mode"):
                try:
                    setattr(renderer, flag, True)
                except Exception:
                    pass
        current = bool(getattr(renderer, "is_fullscreen", False))
        if current != self._published_fullscreen:
            self._publish(current, str(getattr(renderer, "title", "") or ""))

    def closed(self) -> None:
        if self.enabled:
            clear_state(self.instance)
