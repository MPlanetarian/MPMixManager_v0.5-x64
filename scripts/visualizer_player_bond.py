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

import shutil
import subprocess
import time
from typing import Any, Dict, Optional

try:
    from visualizer_monitor_span import ROLE_SPAWNED
except ImportError:
    from scripts.visualizer_monitor_span import ROLE_SPAWNED


class PlayerBondMixin:
    """Follow the Liquid Player's published geometry and stack below it.

    The renderer must provide ``window_pos()``, ``set_window_pos()``,
    ``toggle_fullscreen()``, ``switch_preset()``, ``preset``, ``title``,
    ``is_fullscreen``, ``_windowed_size`` and
    ``_set_windowed_size(width, height, apply=True)``.
    """

    _player_bonded: bool
    _player_bonded_at: float
    _windowed_size: tuple

    # -- state ------------------------------------------------------------
    def _init_player_bond(self) -> None:
        """Create the bond state. Called from each renderer's __init__."""
        self._player_bonded = False
        self._player_bonded_at = 0.0
        self._unbonded_window_pos: Optional[tuple] = None
        self._last_player_geometry: Optional[tuple] = None
        self._restore_size: Optional[tuple] = None

    # -- eligibility ------------------------------------------------------
    def _bonding_applies(self) -> bool:
        """True when this window may bond to the Liquid Player.

        Only the Linux desktop has the separate companion window. A VR
        backdrop places itself, and a spawned F10/F11 window is pinned to its
        own monitor, so neither bonds.
        """
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

    # -- stacking ---------------------------------------------------------
    def _set_bonded_stack(self, bonded: bool) -> None:
        """Ask the window manager to keep this window below the player."""
        if not shutil.which("wmctrl"):
            return
        action = "add,below" if bonded else "remove,below"
        try:
            subprocess.run(
                ["wmctrl", "-r", self.title, "-b", action],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

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
        try:
            if not read_player_visualizer_state().get("active"):
                return None
            geometry = read_published_player_window_geometry()
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
                read_published_player_window_geometry,
                visualizer_presets,
            )
        except ImportError:
            from scripts.liquid_player_visualizer import (
                read_player_visualizer_state,
                read_published_player_window_geometry,
                visualizer_presets,
            )

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
            self._unbonded_window_pos = self.window_pos()
            size = getattr(self, "_windowed_size", None)
            self._restore_size = tuple(size) if size else None
            self._player_bonded = True
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

        geometry = read_published_player_window_geometry()
        if geometry:
            x, y, width, height = geometry
            if geometry != self._last_player_geometry:
                self._bond_apply_geometry(x, y, width, height)
                self._last_player_geometry = geometry
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
            try:
                from liquid_player_visualizer import read_published_player_window_size
            except ImportError:
                from scripts.liquid_player_visualizer import read_published_player_window_size
            size = read_published_player_window_size()
            if size and size != tuple(getattr(self, "_windowed_size", ())):
                self._set_windowed_size(size[0], size[1], apply=True)

        self._sync_bonded_preset(state, visualizer_presets)

    def _sync_bonded_preset(self, state: Dict[str, Any], presets: Any) -> None:
        """Use the same preset the Liquid Player is showing."""
        try:
            preset_id = int(state.get("preset_id", 0))
            rows = presets() if callable(presets) else presets
            preset = next((row for row in rows if int(row.get("id", -1)) == preset_id), None)
            if preset is not None and int(self.preset.get("id", -1)) != preset_id:
                self.switch_preset(preset)
        except Exception:
            pass
