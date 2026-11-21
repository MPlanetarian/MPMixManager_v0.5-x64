"""Bond the standalone MP Visualizer window behind the MP Liquid Player.

The standalone visualizer is a separate GLFW process. The Liquid Player
publishes its own window geometry (``liquid_player_window.json``) and whether
the visualizer is wanted (``liquid_player_visualizer.json``). This mixin reads
those files and keeps the visualizer on the player's exact rectangle.

Both standalone backends mix this in — ``LiquidVisualizerVulkanRenderer`` and
``LiquidVisualizerOpenGLRenderer`` — so the window behind the player behaves
identically whichever GPU path was selected. Before this was shared, only the
Vulkan renderer bonded, and the OpenGL twin (the Linux default) opened as a
free-floating square away from the player.

The window is also stacked directly below the player, so the player's rounded
corners and terminal text stay in front. Releasing the bond (Shift+V, or
closing the player) restores the size and position the window had on its own.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from visualizer_monitor_span import ROLE_SPAWNED
except ImportError:
    from scripts.visualizer_monitor_span import ROLE_SPAWNED

# Title prefixes of the Liquid Glass hosts a companion can belong behind. The
# Black Glass pane is the third: Z inside one of those panes attaches a
# visualizer behind *that* pane, not behind whichever player is also open.
PLAYER_TITLE = "MP Audio Player (Liquid Glass"
GUI_TITLE = "MP Mix Manager (Liquid Glass"
BLACK_GLASS_TITLE = "MP Black Glass"

# How long a locked companion waits for the host it has *never* seen before
# closing itself.
#
# The host is a separate process, so nothing in this one is guaranteed to hear
# the host exit when it was killed rather than closed: the tray's hard close, a
# signal from a script, a crash. The window then sat behind nothing at all. The
# grace covers the gap between a launcher spawning the visualizer and exec'ing
# the host (MP_Gui.sh starts the child first, and a Pi can take several seconds
# to map the glass), so a normal slow start is never mistaken for a dead host.
HOST_START_GRACE_SECONDS = 15.0

# How long a companion that has already been behind its host waits after the
# host disappears. Nothing has to start up any more, so this only needs to
# cover the host's own teardown and one probe interval.
HOST_GONE_GRACE_SECONDS = 2.0

# How often the companion re-probes for its host. The probe walks /proc, so it
# must not run once per frame; a second is far below the time a user could
# notice, and the grace period means the exact last poll does not matter.
HOST_CHECK_INTERVAL = 1.0

# How often the owned companion re-reads the player's ``visible`` flag. The
# flag only changes when the Visualization button is pressed, and the file is
# tiny, but a stat per frame is still pointless at 60 fps.
VISIBILITY_CHECK_INTERVAL = 0.1


class PlayerBondMixin:
    """Follow the Liquid Player's published geometry and stack below it.

    The renderer must provide ``window_pos()``, ``set_window_pos()``,
    ``toggle_fullscreen()``, ``switch_preset()``, ``preset``, ``title``,
    ``is_fullscreen``, ``_windowed_size`` and
    ``_set_windowed_size(width, height, apply=True)``.

    A companion started *by* the player (``MP_VISUALIZER_COMPANION=1``) sets
    ``_bond_locked``. A locked companion is attached for its whole life: the
    bond comes from the process the player launched, not from a state file the
    user could flip, and Z/double-click cannot detach it or take it fullscreen.
    Any other visualizer window is free to move, resize and fullscreen.

    A locked companion is also only ever a background: it closes itself once
    the host it belongs behind has been gone for a moment, so a host that is
    killed without running its own cleanup cannot leave the window stranded
    (see ``_close_if_host_is_gone``).

    The same mixin serves two hosts. The Liquid Player publishes to
    ``liquid_player_window.json``; MP Gui publishes to
    ``liquid_gui_window.json`` and starts its companion with
    ``MP_VISUALIZER_MATCH_GUI=1``. That flag is what selects which record and
    which host window this bond follows, so a GUI companion sits behind MP Gui
    rather than behind a Liquid Player that happens to be open.
    """

    _player_bonded: bool
    _player_bonded_at: float
    _windowed_size: tuple
    _bond_locked: bool

    # -- state ------------------------------------------------------------
    def _init_player_bond(self) -> None:
        """Create the bond state. Called from each renderer's __init__."""
        self._player_bonded = False
        self._player_bonded_at = 0.0
        self._unbonded_window_pos: Optional[tuple] = None
        self._last_player_geometry: Optional[tuple] = None
        self._restore_size: Optional[tuple] = None
        self._bond_locked = os.environ.get("MP_VISUALIZER_COMPANION") == "1"
        # A window opened later (Z) is explicitly detached: it must not adopt
        # the player's rectangle just because a companion is already running.
        self._bond_detached = os.environ.get("MP_VISUALIZER_DETACHED") == "1"
        # Map/unmap state of the companion the player owns. The player's
        # Visualization button writes ``visible`` into the shared state file;
        # this window is mapped while it is true and unmapped while it is
        # false. Only that one owned window follows the flag - see
        # ``_sync_companion_visibility``.
        self._companion_visible = True
        self._visibility_check_at = 0.0
        # When this companion first noticed that no host was running, and
        # whether it has already closed itself for that reason. See
        # ``_close_if_host_is_gone``.
        self._host_gone_since = 0.0
        self._host_gone_closed = False
        self._host_check_at = 0.0
        self._host_was_seen = False

    # -- eligibility ------------------------------------------------------
    def _bonding_applies(self) -> bool:
        """True when this window may bond to the Liquid Player.

        Only the Linux desktop has the separate companion window. A VR
        backdrop places itself, and a spawned F10/F11 window is pinned to its
        own monitor, so neither bonds. A window the user opened separately is
        detached on purpose and never bonds.
        """
        if getattr(self, "_bond_detached", False):
            return False
        try:
            from mp_liquid_visualizer_vulkan import _IS_LINUX, _vr_visualizer_side
        except ImportError:
            from scripts.mp_liquid_visualizer_vulkan import _IS_LINUX, _vr_visualizer_side
        # Windows uses the same separate companion window as Linux. macOS has
        # its own placement path, so it stays out of this bond.
        if not _IS_LINUX and sys.platform != "win32":
            return False
        try:
            if _vr_visualizer_side():
                return False
        except Exception:
            return False
        if getattr(self, "_span_role", "") == ROLE_SPAWNED:
            return False
        return True

    def _bond_is_locked(self) -> bool:
        """True for the companion the player launched, which never detaches."""
        return bool(getattr(self, "_bond_locked", False))

    # -- host selection ---------------------------------------------------
    def _bond_targets_gui(self) -> bool:
        """True when this companion belongs behind MP Gui, not the player.

        MP_Gui.sh launches its companion with ``MP_VISUALIZER_MATCH_GUI=1`` and
        publishes to ``liquid_gui_window.json``. Reading only the player record
        made that window bond to whichever Liquid Player was open (or to
        nothing at all when only the GUI was up).
        """
        try:
            from liquid_player_visualizer import visualizer_matches_gui
        except ImportError:
            from scripts.liquid_player_visualizer import visualizer_matches_gui
        try:
            return bool(visualizer_matches_gui())
        except Exception:
            return False

    def _bond_targets_black_glass(self) -> bool:
        """True when this companion belongs behind an MP Black Glass pane.

        A pane started with **W** attaches its own visualizer with
        ``MP_VISUALIZER_MATCH_BLACK_GLASS=1`` and records the pane's pid in
        ``MP_BLACK_GLASS_PID``. The pane publishes its own rectangle, so the
        bond follows that pane and not the player behind it.
        """
        return os.environ.get("MP_VISUALIZER_MATCH_BLACK_GLASS") == "1"

    def _bond_host_title(self) -> str:
        """Title prefix of the window this bond must stack against."""
        if self._bond_targets_black_glass():
            return BLACK_GLASS_TITLE
        return GUI_TITLE if self._bond_targets_gui() else PLAYER_TITLE

    def _bond_host_running(self) -> Optional[bool]:
        """True/False for the host this companion belongs behind, None if unknown.

        A companion is started before its host in two of the launch paths
        (``MP_Gui.sh`` spawns the visualizer, then execs the host), so "no host
        yet" is normal for the first moment of a launch. The caller pairs this
        with ``HOST_START_GRACE_SECONDS`` so a slow start is never read as a
        dead host.

        Only the host *kind* this window was started for counts: a GUI
        companion must not be kept alive by a Liquid Player, and the reverse
        holds too. Windows and macOS have no ``/proc``, so the probe cannot see
        another process there and returns None: an unverifiable host is never
        treated as a dead one, because that would close a working window.
        """
        if self._bond_targets_black_glass():
            try:
                from black_glass_window import host_running
            except ImportError:
                from scripts.black_glass_window import host_running
            try:
                return host_running()
            except Exception:
                return None
        if not Path("/proc").is_dir():
            return None
        try:
            from liquid_player_visualizer import (
                liquid_gui_host_running,
                liquid_player_host_running,
            )
        except ImportError:
            from scripts.liquid_player_visualizer import (
                liquid_gui_host_running,
                liquid_player_host_running,
            )
        try:
            if self._bond_targets_gui():
                running = liquid_gui_host_running()
            else:
                running = liquid_player_host_running()
        except Exception:
            return None
        # None means "not observable here"; keep it that way instead of
        # collapsing it to False via bool().
        return None if running is None else bool(running)

    # -- visibility -------------------------------------------------------
    def _companion_follows_visibility(self) -> bool:
        """True for the one companion window this host launched, and only that.

        The Visualization button maps and unmaps the companion its own host
        launched: the Liquid Player's button drives the player's companion and
        MP Gui's button drives the menu's. A window the user opened with Z,
        from the desktop shortcut, or a spawned F10/F11 window, is never
        touched: those are their own windows and hiding them would close
        something the user is watching.

        The two hosts read different keys of the shared state file
        (``visible`` and ``gui_visible``), so pressing one button cannot blank
        the other window's background.
        """
        if not self._bond_is_locked() or getattr(self, "_bond_detached", False):
            return False
        return True

    def _companion_visibility_key(self) -> str:
        """The state-file key this companion's host button writes."""
        try:
            return "gui_visible" if self._bond_targets_gui() else "visible"
        except Exception:
            return "visible"

    def _set_companion_visible(self, visible: bool) -> None:
        """Map or unmap this window without ending the render loop.

        ``glfwHideWindow`` leaves the GLFW window, its GL context and the bond
        state alive, so the loop keeps running and can map the window again on
        the next button press. That is the difference from closing it: closing
        would need a new process, and the bond would have to be re-established
        from scratch.
        """
        g, w = getattr(self, "_glfw", None), getattr(self, "_window", None)
        if g is None or not w:
            return
        function = getattr(g, "glfwShowWindow" if visible else "glfwHideWindow", None)
        if not callable(function):
            return
        try:
            function(w)
        except Exception:
            return
        self._companion_visible = bool(visible)
        if visible and getattr(self, "_player_bonded", False):
            # Showing a window can raise it. Put it back under the player at
            # once instead of waiting for the next half-second re-lower.
            self._next_relower_at = 0.0
            self._lower_below_player()

    def _sync_companion_visibility(self) -> None:
        """Follow the player's Visualization button for the owned companion.

        The button writes ``visible`` to the shared state file; ``active`` says
        whether the window belongs behind the player at all. This maps the
        window while ``visible`` is true and unmaps it while it is false, so
        pressing the button twice restores exactly what was there before. The
        file is read at most ``VISIBILITY_CHECK_INTERVAL`` apart because every
        frame would stat it for a value that only changes on a key press.
        """
        if not self._companion_follows_visibility():
            return
        now = time.monotonic()
        if now < getattr(self, "_visibility_check_at", 0.0):
            return
        self._visibility_check_at = now + VISIBILITY_CHECK_INTERVAL
        try:
            from liquid_player_visualizer import read_player_visualizer_state
        except ImportError:
            from scripts.liquid_player_visualizer import read_player_visualizer_state
        try:
            state = read_player_visualizer_state()
        except Exception:
            return
        # An older state file has no such key; it reads as True so an
        # upgraded build never hides a window the user did not turn off.
        wanted = state.get(self._companion_visibility_key()) is not False
        if wanted == bool(getattr(self, "_companion_visible", True)):
            return
        self._set_companion_visible(wanted)

    def _bond_should_start_hidden(self) -> bool:
        """True when the owned companion must open unmapped.

        The Visualization button can be off in the state file before the
        window is created (the player spawns the companion on launch and the
        user has turned it off, or a previous session left it off). Opening
        with ``GLFW_VISIBLE=0`` avoids a frame of scene flashing on screen
        before the first poll hides it again.
        """
        if not self._companion_follows_visibility():
            return False
        try:
            from liquid_player_visualizer import read_player_visualizer_state
        except ImportError:
            from scripts.liquid_player_visualizer import read_player_visualizer_state
        try:
            return read_player_visualizer_state().get(
                self._companion_visibility_key()) is False
        except Exception:
            return False

    def _close_if_host_is_gone(self) -> bool:
        """Close a locked companion once the window behind it has exited.

        The companion is only ever a background for its host, so when the host
        goes away the companion must not stay on screen as a free-floating
        window. ``stop_companion_visualizer`` already closes it on an orderly
        exit; this covers the paths where the host never runs its cleanup —
        ``SIGKILL``, the tray's hard close, a crash, a logout.

        The probe walks /proc, so it runs at most once every
        ``HOST_CHECK_INTERVAL``. Between probes only a grace period that is
        already running can expire, which is what closes the window. A host
        that was never seen gets the longer ``HOST_START_GRACE_SECONDS``, so a
        slow launch is not mistaken for a dead one; once the companion has been
        behind its host, ``HOST_GONE_GRACE_SECONDS`` applies.

        Returns True when this call closed the window.
        """
        if not self._bond_is_locked() or getattr(self, "_bond_detached", False):
            return False
        if getattr(self, "_host_gone_closed", False):
            return False
        now = time.monotonic()
        if now >= getattr(self, "_host_check_at", 0.0):
            self._host_check_at = now + HOST_CHECK_INTERVAL
            running = self._bond_host_running()
            if running is None or running:
                # A host that is up, or cannot be observed at all, clears the
                # timer: the next disappearance starts a fresh grace period.
                self._host_gone_since = 0.0
                if running:
                    self._host_was_seen = True
                return False
            if self._host_gone_since <= 0.0:
                self._host_gone_since = now
                return False
        seen_at = getattr(self, "_host_gone_since", 0.0)
        if seen_at <= 0.0:
            return False
        grace = (HOST_GONE_GRACE_SECONDS if getattr(self, "_host_was_seen", False)
                 else HOST_START_GRACE_SECONDS)
        if now - seen_at < grace:
            return False
        self._host_gone_closed = True
        try:
            self.request_close()
        except Exception:
            pass
        return True

    def _bond_read_geometry(self) -> Optional[tuple]:
        """Published (x, y, width, height) for the host this bond follows."""
        if self._bond_targets_black_glass():
            try:
                from black_glass_window import read_geometry
            except ImportError:
                from scripts.black_glass_window import read_geometry
            try:
                return read_geometry()
            except Exception:
                return None
        try:
            from liquid_player_visualizer import (
                read_published_gui_window_geometry,
                read_published_player_window_geometry,
            )
        except ImportError:
            from scripts.liquid_player_visualizer import (
                read_published_gui_window_geometry,
                read_published_player_window_geometry,
            )
        if self._bond_targets_gui():
            return read_published_gui_window_geometry()
        return read_published_player_window_geometry()

    def _bond_read_size(self) -> Optional[tuple]:
        """Published (width, height) for the host this bond follows."""
        if self._bond_targets_black_glass():
            geometry = self._bond_read_geometry()
            return (geometry[2], geometry[3]) if geometry else None
        try:
            from liquid_player_visualizer import (
                read_published_gui_window_size,
                read_published_player_window_size,
            )
        except ImportError:
            from scripts.liquid_player_visualizer import (
                read_published_gui_window_size,
                read_published_player_window_size,
            )
        if self._bond_targets_gui():
            return read_published_gui_window_size()
        return read_published_player_window_size()

    def _bond_read_host_window_id(self) -> int:
        """The host's own X11 window id, or 0 when it did not publish one."""
        if self._bond_targets_black_glass():
            try:
                from black_glass_window import read_window_id
            except ImportError:
                from scripts.black_glass_window import read_window_id
            try:
                return int(read_window_id() or 0)
            except Exception:
                return 0
        try:
            from liquid_player_visualizer import (
                read_published_gui_window_id,
                read_published_player_window_id,
            )
        except ImportError:
            from scripts.liquid_player_visualizer import (
                read_published_gui_window_id,
                read_published_player_window_id,
            )
        try:
            if self._bond_targets_gui():
                return int(read_published_gui_window_id() or 0)
            return int(read_published_player_window_id() or 0)
        except Exception:
            return 0

    # -- stacking ---------------------------------------------------------
    def _set_bonded_stack(self, bonded: bool) -> None:
        """Put this window directly below the Liquid Player.

        Do **not** use ``_NET_WM_STATE_BELOW`` (or ``wmctrl -b add,below``).
        KWin reads that as "below every normal window", so the visualizer sank
        beneath whatever else was on the desktop: it showed on an empty area,
        but behind a maximised console the player blended with the console
        instead of with the visualizer. Ordering the two windows against each
        other keeps them adjacent, which is what "behind the player" means.
        """
        if not bonded:
            self._clear_below_hint()
            return
        self._lower_below_player()

    def _clear_below_hint(self) -> None:
        """Drop any _NET_WM_STATE_BELOW left by an earlier build."""
        title = getattr(self, "title", "") or ""
        if not title or not shutil.which("wmctrl"):
            return
        try:
            subprocess.run(
                ["wmctrl", "-r", title, "-b", "remove,below"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _lower_below_player(self) -> None:
        """Restack this window directly below the Liquid Player.

        ``XLowerWindow`` would send the window to the very bottom of the stack,
        under every other application, so the player would blend with whatever
        else happened to be beneath it instead of with the visualizer. Stack it
        against the player as a sibling instead, which keeps the two adjacent.
        """
        if sys.platform == "win32":
            # Place this HWND directly behind the host HWND. Passing the host
            # as hWndInsertAfter keeps the two adjacent; HWND_BOTTOM would put
            # the visualizer under every other application. No move, resize or
            # activation: the geometry pass and the player keep their state.
            try:
                import ctypes
                host = int(self._bond_read_host_window_id() or 0)
                mine = int(self.win32_window() or 0)
            except Exception:
                return
            if host and mine:
                try:
                    ctypes.windll.user32.SetWindowPos(
                        mine, host, 0, 0, 0, 0,
                        0x0001 | 0x0002 | 0x0010,  # NOSIZE | NOMOVE | NOACTIVATE
                    )
                except Exception:
                    pass
            return
        try:
            import ctypes
            from ctypes import c_char_p, c_int, c_uint, c_uint64, c_void_p
        except Exception:
            return
        window = 0
        try:
            window = int(self.x11_window() or 0)
        except Exception:
            window = int(getattr(self, "_x11_handle", 0) or 0)
        if window <= 0:
            return
        try:
            x11 = ctypes.CDLL("libX11.so.6")
            x11.XOpenDisplay.restype = c_void_p
            x11.XOpenDisplay.argtypes = [c_char_p]
            display = x11.XOpenDisplay(None)
            if not display:
                return
            try:
                player = self._find_player_window(x11)
                if player:
                    # XConfigureWindow(display, window, mask, values) with
                    # CWSibling | CWStackMode and StackModeBelow.
                    class XWindowChanges(ctypes.Structure):
                        _fields_ = [
                            ("x", c_int), ("y", c_int),
                            ("width", c_int), ("height", c_int),
                            ("border_width", c_int),
                            ("sibling", c_uint64),
                            ("stack_mode", c_int),
                        ]
                    CW_SIBLING = 1 << 6
                    CW_STACK_MODE = 1 << 7
                    STACK_MODE_BELOW = 1
                    changes = XWindowChanges()
                    changes.sibling = c_uint64(int(player)).value
                    changes.stack_mode = STACK_MODE_BELOW
                    x11.XConfigureWindow.argtypes = [
                        c_void_p, c_uint64, c_uint, c_void_p,
                    ]
                    x11.XConfigureWindow(
                        display, c_uint64(window),
                        c_uint(CW_SIBLING | CW_STACK_MODE),
                        ctypes.byref(changes),
                    )
                else:
                    x11.XLowerWindow.argtypes = [c_void_p, c_uint64]
                    x11.XLowerWindow(display, c_uint64(window))
                x11.XFlush.argtypes = [c_void_p]
                x11.XFlush(display)
            finally:
                x11.XCloseDisplay.argtypes = [c_void_p]
                x11.XCloseDisplay(display)
        except Exception:
            pass

    def _find_player_window(self, x11) -> int:
        """Window id of the host this visualizer sits behind.

        The published ``wid`` is preferred: it names the exact window the host
        owns, so a GUI companion stacks against MP Gui even while a Liquid
        Player is also open. The title lookup stays as the fallback for a host
        built before that field existed.
        """
        published = self._bond_read_host_window_id()
        if published > 0:
            return int(published)
        try:
            import ctypes
            from ctypes import POINTER, byref, c_char_p, c_int, c_uint, c_uint64, c_void_p
        except Exception:
            return 0
        display = x11.XOpenDisplay(None)
        if not display:
            return 0
        children = None
        try:
            x11.XDefaultRootWindow.restype = c_uint64
            x11.XDefaultRootWindow.argtypes = [c_void_p]
            root = x11.XDefaultRootWindow(display)
            x11.XQueryTree.restype = c_int
            x11.XQueryTree.argtypes = [
                c_void_p, c_uint64, POINTER(c_uint64), POINTER(c_uint64),
                POINTER(POINTER(c_uint64)), POINTER(c_uint),
            ]
            x11.XFree.restype = c_int
            x11.XFree.argtypes = [c_void_p]
            rr, parent = c_uint64(), c_uint64()
            children = POINTER(c_uint64)()
            count = c_uint()
            if not x11.XQueryTree(display, root, byref(rr), byref(parent),
                                   byref(children), byref(count)):
                return 0
            x11.XFetchName.restype = c_int
            x11.XFetchName.argtypes = [c_void_p, c_uint64, POINTER(c_char_p)]
            mine = 0
            try:
                mine = int(self.x11_window() or 0)
            except Exception:
                mine = 0
            # The host this companion was started for wins; the other host is
            # only a last resort, so a GUI companion is never sent behind a
            # Liquid Player just because the player window came first.
            wanted = self._bond_host_title()
            other = PLAYER_TITLE if wanted == GUI_TITLE else GUI_TITLE
            fallback = 0
            for i in range(count.value):
                wid = children[i]
                if int(wid) == mine:
                    continue
                name = c_char_p()
                if not x11.XFetchName(display, wid, byref(name)) or not name:
                    continue
                try:
                    title = name.value.decode("utf-8", "replace") if name.value else ""
                finally:
                    x11.XFree(name)
                if title.startswith(wanted):
                    return int(wid)
                if not fallback and title.startswith(other):
                    fallback = int(wid)
            if fallback:
                return fallback
        except Exception:
            return 0
        finally:
            if children:
                try:
                    x11.XFree(children)
                except Exception:
                    pass
            try:
                x11.XCloseDisplay.argtypes = [c_void_p]
                x11.XCloseDisplay(display)
            except Exception:
                pass
        return 0

    # -- geometry ---------------------------------------------------------
    def _bond_apply_geometry(self, x: int, y: int, width: int, height: int) -> None:
        """Move and resize this window onto the player's rectangle."""
        self._set_windowed_size(width, height, apply=True)
        self.set_window_pos(int(x), int(y))

    def _bond_initial_geometry(self) -> Optional[tuple]:
        """Published player geometry to open on, when this window will bond.

        Returns ``(x, y, width, height)`` when the visualizer is wanted and the
        player has published where it is. ``open()`` uses it before creating
        the window so a bonded companion appears directly behind the player
        instead of flashing in the top-left corner first.
        """
        if not self._bonding_applies():
            return None
        try:
            from liquid_player_visualizer import (
                read_player_visualizer_state,
                read_published_player_window_geometry,
            )
        except ImportError:
            from scripts.liquid_player_visualizer import (
                read_player_visualizer_state,
                read_published_player_window_geometry,
            )
        # A locked companion always bonds; it does not consult the state file,
        # because the file is written by the player *after* this process starts.
        if not self._bond_is_locked():
            try:
                if not read_player_visualizer_state().get("active"):
                    return None
            except Exception:
                return None
        try:
            geometry = self._bond_read_geometry()
        except Exception:
            return None
        if not geometry:
            return None
        x, y, width, height = (int(v) for v in geometry)
        if width < 64 or height < 64:
            return None
        return x, y, width, height

    def _bond_restore_geometry(self) -> None:
        """Put the window back where and how it was before the bond."""
        restore = getattr(self, "_restore_size", None)
        if restore:
            self._set_windowed_size(restore[0], restore[1], apply=True)
        if self._unbonded_window_pos is not None:
            self.set_window_pos(*self._unbonded_window_pos)

    # -- main entry point -------------------------------------------------
    def _sync_player_bond(self) -> None:
        """Keep the standalone visualizer directly behind the Liquid Player."""
        # The Visualization button owns the map state of the companion the
        # player launched. It is checked first because the visibility gate has
        # its own eligibility test (owned companion, not detached, not GUI)
        # and must not depend on the bonding rules below.
        self._sync_companion_visibility()
        if not self._bonding_applies():
            return
        # The host can disappear without ever running its own shutdown (tray
        # hard close, SIGKILL, a crash). Check that first: a companion with no
        # window behind it must close instead of hovering over the desktop.
        if self._close_if_host_is_gone():
            return
        now = time.monotonic()
        if now < getattr(self, "_player_bonded_at", 0.0):
            return
        self._player_bonded_at = now + 0.10

        try:
            from liquid_player_visualizer import (
                read_player_visualizer_state,
                visualizer_presets,
            )
        except ImportError:
            from scripts.liquid_player_visualizer import (
                read_player_visualizer_state,
                visualizer_presets,
            )

        locked = self._bond_is_locked()
        if locked:
            # The player launched this window to sit behind it. Stay attached
            # even if the state file says otherwise (it can lag the launch, or
            # be rewritten by another host), and never let it sit fullscreen
            # covering the player.
            bonded = True
            if self.is_fullscreen:
                self.toggle_fullscreen()
        else:
            state = read_player_visualizer_state()
            bonded = bool(state.get("active"))
            # An explicit Z choice owns the window; do not bond it back to the
            # player. Z is the user saying "show me this window on its own".
            if getattr(self, "_user_selected_window_mode", False):
                return
            if bonded and self.is_fullscreen:
                self.toggle_fullscreen()
        if self.is_fullscreen:
            return

        if bonded and not self._player_bonded:
            if not locked:
                self._unbonded_window_pos = self.window_pos()
                size = getattr(self, "_windowed_size", None)
                self._restore_size = tuple(size) if size else None
            self._player_bonded = True
            # Clear any below hint first: it would push the window under every
            # other application instead of just under the player.
            self._clear_below_hint()
            self._set_bonded_stack(True)
        elif not bonded and self._player_bonded:
            self._player_bonded = False
            self._set_bonded_stack(False)
            # While bonded the window took the player's Mini/Micro size and
            # pinned its size limits to it. Put the unbonded window back at
            # the size it owned before the bond.
            self._bond_restore_geometry()
            self._unbonded_window_pos = None
            self._last_player_geometry = None
            return
        if not bonded:
            return

        # Re-lower on a slow cadence. The player is raised whenever it is
        # focused or lifted above another window, and the visualizer must stay
        # behind it, so a one-off lowering at bond time is not enough.
        now_relower = time.monotonic()
        if now_relower >= getattr(self, "_next_relower_at", 0.0):
            self._next_relower_at = now_relower + 0.5
            self._lower_below_player()

        geometry = self._bond_read_geometry()
        if geometry:
            x, y, width, height = geometry
            if geometry != self._last_player_geometry:
                self._bond_apply_geometry(x, y, width, height)
                self._last_player_geometry = geometry
                if locked and hasattr(self, "_apply_bonded_size_limits"):
                    self._apply_bonded_size_limits(width, height)
            else:
                # The window manager can move a window while it resizes.
                # Keep the bonded window on the player's corner.
                try:
                    current = self.window_pos()
                except Exception:
                    current = None
                if current is not None and (
                    abs(int(current[0]) - int(x)) > 2 or abs(int(current[1]) - int(y)) > 2
                ):
                    self.set_window_pos(x, y)
        else:
            # A host that publishes only its size still keeps the visualizer
            # on that size; the position stays where the bond put it.
            size = self._bond_read_size()
            if size and size != tuple(getattr(self, "_windowed_size", ())):
                self._set_windowed_size(size[0], size[1], apply=True)

        self._sync_bonded_preset(read_player_visualizer_state(), visualizer_presets)

    def _sync_bonded_preset(self, state: Dict[str, Any], presets: Any) -> None:
        """Use the same preset the Liquid Player is showing.

        An explicit preset request wins. ``MP_Liquid_Visualizer.py --preset N``
        (and the MP Visualizer menu rows) ask for one specific preset, but the
        player publishes its own ``preset_id`` — so the sync used to drag every
        window back to the player's preset on the first frame. Picking any
        preset except the player's then showed someone else's scene: the
        reported "presets 11 and 12 look wrong" was every preset rendering the
        saved P2 river, because that was the only id the sync allowed.
        """
        try:
            if getattr(self, "_preset_pinned", False):
                return
            preset_id = int(state.get("preset_id", 0))
            rows = presets() if callable(presets) else presets
            preset = next((row for row in rows if int(row.get("id", -1)) == preset_id), None)
            if preset is not None and int(self.preset.get("id", -1)) != preset_id:
                self.switch_preset(preset)
        except Exception:
            pass

    def _publish_bonded_preset(self, preset: Dict[str, Any]) -> None:
        """Write a preset chosen in this window back to the player's state.

        A bonded window and the Liquid Player share one preset. Both write the
        same state file, so a key pressed here (Left/Right, 1-9, + or -) is
        persisted for the player and is not undone by the next bond poll,
        which would otherwise still see the player's older ``preset_id``.

        Only a window that is already bonded publishes: a free window must not
        mark the visualizer active, because that would ask the player to bond a
        window the user keeps on its own.
        """
        if getattr(self, "screensaver", False):
            return
        try:
            if not self._bonding_applies():
                return
        except Exception:
            return
        if not (getattr(self, "_player_bonded", False) or self._bond_is_locked()):
            return
        try:
            from liquid_player_visualizer import write_player_visualizer_state
        except ImportError:
            from scripts.liquid_player_visualizer import write_player_visualizer_state
        try:
            write_player_visualizer_state(True, preset)
        except Exception:
            pass
