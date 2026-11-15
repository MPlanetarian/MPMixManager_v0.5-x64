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
import time
from typing import Any, Dict, Optional

try:
    from visualizer_monitor_span import ROLE_SPAWNED
except ImportError:
    from scripts.visualizer_monitor_span import ROLE_SPAWNED

# Title prefixes of the two Liquid Glass hosts a companion can belong behind.
PLAYER_TITLE = "MP Audio Player (Liquid Glass"
GUI_TITLE = "MP Mix Manager (Liquid Glass"


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
        if not _IS_LINUX:
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

    def _bond_host_title(self) -> str:
        """Title prefix of the window this bond must stack against."""
        return GUI_TITLE if self._bond_targets_gui() else PLAYER_TITLE

    def _bond_read_geometry(self) -> Optional[tuple]:
        """Published (x, y, width, height) for the host this bond follows."""
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
        if not self._bonding_applies():
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
