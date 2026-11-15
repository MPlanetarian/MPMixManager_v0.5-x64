"""The Z key window cycle shared by every MP Visualizer backend.

A window the user owns (the one **Z** opens from the MP Liquid Player, the
desktop shortcut, or a plain ``MP_Liquid_Visualizer.py`` run) cycles through
three shapes on successive **Z** presses:

1. the size it had when the user first pressed Z  ->  fullscreen
2. fullscreen                                     ->  half the MP Liquid Player
3. half the MP Liquid Player                      ->  the original size again

The "original" geometry is captured from the live GLFW window on the first
press, not guessed from the constructor. That matters because the window
starts at the Liquid Player's *published* size, which the player refits after
measuring its font, and because a user may have dragged the window first.
Restoring the captured size **and** position puts the window back exactly
where and how it was before the cycle started.

The half step is half of the **MP Liquid Player's** published window, which is
what the user sees the visualizer matching. When no player is running (a
standalone window) it falls back to half of the window's own original size.

The companion window the Liquid Player starts on its own is *never* cycled.
It is owned by the player, sits behind it at the player's exact rectangle, and
must not be taken fullscreen or shrunk. ``_cycle_window_mode_for_user``
returns the current mode untouched for that window, which is the same rule the
renderers used before; this module only replaces the three drifting copies of
the cycle itself.

Each renderer supplies a handful of trivial primitives:

``is_fullscreen``, ``window_mode``
    Plain state attributes it already had.
``toggle_fullscreen()``
    The existing fullscreen switch (``--fullscreen``, 4K, double-click).
``_z_window_size()`` / ``_z_window_pos()``
    The live windowed geometry, used to capture the original shape.
``_z_apply_windowed(width, height, x, y)``
    Leave fullscreen (if needed) and place the window at that geometry.
``_player_bonded`` / ``_bond_is_locked()``
    Already provided by ``PlayerBondMixin``; they keep the companion out of
    the cycle.

The renderer must also keep ``_default_windowed_size`` /
``_half_windowed_size`` in step: the Vulkan renderer's per-frame size lock
reads them to decide whether the window has to be forced back to a size. This
mixin writes both on every press for that reason.
"""
from __future__ import annotations

from typing import Optional, Tuple

# Z cycles the window through these modes: default -> fullscreen -> half -> default.
WINDOW_MODE_FULLSCREEN, WINDOW_MODE_HALF, WINDOW_MODE_DEFAULT = "fullscreen", "half", "default"

# The smallest window either backend will place.
MIN_WINDOW_SIDE = 64


class ZWindowCycleMixin:
    """The three-step Z cycle. Mixed in by Vulkan, Metal and OpenGL renderers."""

    # -- renderer primitives (overridden by each backend) ------------------
    def _z_window_size(self) -> Tuple[int, int]:
        return int(self.width), int(self.height)

    def _z_window_pos(self) -> Tuple[int, int]:
        return int(self._cur_pos[0]), int(self._cur_pos[1])

    def _z_apply_windowed(self, width: int, height: int, x: int, y: int) -> None:
        raise NotImplementedError("renderer must implement _z_apply_windowed")

    # -- the cycle --------------------------------------------------------
    def _cycle_window_mode_for_user(self) -> str:
        """Z from inside the window.

        The player-owned companion and a window currently bonded to the player
        own no geometry of their own: Z has to leave them exactly as they are.
        """
        if getattr(self, "_player_bonded", False) or self._bond_is_locked():
            return getattr(self, "window_mode", WINDOW_MODE_DEFAULT)
        return self.cycle_window_mode()

    def _bond_is_locked(self) -> bool:
        # PlayerBondMixin provides this; the fallback keeps a bare renderer
        # (the unit tests build some without opening a window) working.
        return False

    def cycle_window_mode(self) -> str:
        """Advance the cycle: original -> fullscreen -> half -> original."""
        # Remember the windowed shape before anything moves. This covers the
        # first press of the cycle (including a window the user had already put
        # fullscreen with a double-click or --fullscreen), and it refreshes the
        # shape when the user has dragged the window between cycles. A press in
        # the middle of a cycle never re-captures, so the third press returns
        # to exactly the shape the first press saw.
        at_default = (not getattr(self, "is_fullscreen", False)
                      and getattr(self, "window_mode", WINDOW_MODE_DEFAULT) == WINDOW_MODE_DEFAULT)
        self._z_capture_original(force=at_default)

        if getattr(self, "is_fullscreen", False) or \
                getattr(self, "window_mode", WINDOW_MODE_DEFAULT) == WINDOW_MODE_FULLSCREEN:
            size, pos = self._z_half_geometry()
            self._half_windowed_size = size
            self._half_windowed_offset = pos
            self.window_mode = WINDOW_MODE_HALF
            self._user_selected_window_mode = True
            self._z_apply_windowed(size[0], size[1], pos[0], pos[1])
            return self.window_mode

        if getattr(self, "window_mode", WINDOW_MODE_DEFAULT) == WINDOW_MODE_HALF:
            size, pos = self._z_original_geometry()
            self.window_mode = WINDOW_MODE_DEFAULT
            self._user_selected_window_mode = True
            self._z_apply_windowed(size[0], size[1], pos[0], pos[1])
            return self.window_mode

        self.window_mode = WINDOW_MODE_FULLSCREEN
        self._user_selected_window_mode = True
        self.toggle_fullscreen()
        return self.window_mode

    # -- geometry ---------------------------------------------------------
    def _z_capture_original(self, force: bool = False) -> None:
        """Remember the live windowed size and position from the first Z press.

        ``force`` replaces an earlier capture, which is how a window dragged
        between cycles keeps working: the shape is re-read at the start of the
        next cycle instead of jumping back to a stale position.
        """
        if getattr(self, "_z_original_size", None) is not None and not force:
            return
        try:
            width, height = self._z_window_size()
            x, y = self._z_saved_pos() if self.is_fullscreen else self._z_window_pos()
        except Exception:
            return
        size = (max(MIN_WINDOW_SIDE, int(width)), max(MIN_WINDOW_SIDE, int(height)))
        pos = (int(x), int(y))
        self._z_original_size = size
        self._z_original_pos = pos
        # The per-frame size lock reads these, so the chosen shape survives the
        # next poll instead of being forced back to the constructor guess.
        self._default_windowed_size = size
        self._default_windowed_offset = pos

    def _z_saved_pos(self) -> Tuple[int, int]:
        """The position the window had before it went fullscreen."""
        for name in ("_saved_window_pos", "_saved_pos"):
            pos = getattr(self, name, None)
            if isinstance(pos, (tuple, list)) and len(pos) == 2:
                return int(pos[0]), int(pos[1])
        return self._z_window_pos()

    def _z_original_geometry(self) -> Tuple[Tuple[int, int], Tuple[int, int]]:
        """The shape captured on the first Z press, or the windowed default."""
        size = getattr(self, "_z_original_size", None)
        if size is None:
            size = getattr(self, "_default_windowed_size", (self.width, self.height))
            pos = getattr(self, "_default_windowed_offset", self._z_window_pos())
        else:
            pos = getattr(self, "_z_original_pos", self._z_window_pos())
        return (
            (max(MIN_WINDOW_SIDE, int(size[0])), max(MIN_WINDOW_SIDE, int(size[1]))),
            (int(pos[0]), int(pos[1])),
        )

    def _z_half_geometry(self) -> Tuple[Tuple[int, int], Tuple[int, int]]:
        """Half the Liquid Player, centred on the original window."""
        (orig_w, orig_h), (orig_x, orig_y) = self._z_original_geometry()
        player = self._z_player_size() or (orig_w, orig_h)
        half_w = max(MIN_WINDOW_SIDE, int(player[0]) // 2)
        half_h = max(MIN_WINDOW_SIDE, int(player[1]) // 2)
        centre_x = orig_x + orig_w // 2
        centre_y = orig_y + orig_h // 2
        return (half_w, half_h), (centre_x - half_w // 2, centre_y - half_h // 2)

    def _z_player_size(self) -> Optional[Tuple[int, int]]:
        """The Liquid Player's published window size, while that process lives.

        Read lazily: ``liquid_player_visualizer`` imports this package's
        renderers inside its own functions, and importing it at module load
        here would make that cycle load-order dependent.
        """
        try:
            try:
                from liquid_player_visualizer import (
                    read_published_player_window_geometry,
                    read_published_player_window_size,
                )
            except ImportError:
                from scripts.liquid_player_visualizer import (
                    read_published_player_window_geometry,
                    read_published_player_window_size,
                )
            geometry = read_published_player_window_geometry()
            if geometry:
                return int(geometry[2]), int(geometry[3])
            size = read_published_player_window_size()
            if size:
                return int(size[0]), int(size[1])
        except Exception:
            pass
        return None


__all__ = [
    "MIN_WINDOW_SIDE",
    "WINDOW_MODE_DEFAULT",
    "WINDOW_MODE_FULLSCREEN",
    "WINDOW_MODE_HALF",
    "ZWindowCycleMixin",
]
