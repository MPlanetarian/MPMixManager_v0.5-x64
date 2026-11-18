#!/usr/bin/env python3
"""MP Liquid Mixer 0.0.1 - a full-screen four-channel mixer over two players.

One 1920x1080 liquid-glass window holds two real MP Liquid Player engines (left
and right) and a four-channel mixer with a crossfader in the wide gap between
them. Channels A and B drive the two decks; C and D are present but inactive in
v0.0.1. An MP Liquid Visualizer window can run behind the mixer and is toggled
with V (or the on-screen button); transparency uses the player's own slider
range, and C cycles the same themes as every other MP window.

The class inherits GlassHost so the theme resolver, child environment, player
argv, font handling and companion-visualizer plumbing are the Liquid Player's
own - not a reimplementation.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import threading
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

MIXER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MIXER_DIR.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
for path in (str(MIXER_DIR), str(PROJECT_ROOT), str(SCRIPTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import mixer_core as core  # noqa: E402
import mixer_evdev as mixer_evdev  # noqa: E402
import mixer_recording as mixer_recording  # noqa: E402

# The host forces GDK_BACKEND=x11 and requires GdkX11 - the window needs an X11
# id so the companion visualizer can stack itself directly underneath.
from MP_Audio_Player_Liquid_Glass_OpenGL import (  # noqa: E402
    GlassHost,
    _child_env,
    _import_gtk,
    _visualizer_env,
    get_liquid_glass_font,
    player_argv,
)

CONFIG_DIR = core.CONFIG_DIR


def _rgba(color: Tuple[int, int, int], alpha: float = 1.0) -> Tuple[float, float, float, float]:
    return (color[0] / 255.0, color[1] / 255.0, color[2] / 255.0, max(0.0, min(1.0, alpha)))


def _mix(a: Tuple[int, int, int], b: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))  # type: ignore[return-value]


def _darken(color: Tuple[int, int, int], amount: float = 0.35) -> Tuple[int, int, int]:
    return tuple(int(round(c * (1.0 - amount))) for c in color)  # type: ignore[return-value]


def _light(color: Tuple[int, int, int], toward: Tuple[int, int, int] = (255, 255, 255), amount: float = 0.5) -> Tuple[int, int, int]:
    return _mix(color, toward, amount)


def sync_pitch(master_pitch: float, master_bpm: float = 0.0, target_bpm: float = 0.0) -> float:
    """Pitch for the target deck to match the master's effective tempo.

    With BPM tags, that is the true beat match: master BPM adjusted by the
    master's own pitch, divided by the target's original BPM. Without tags it
    falls back to copying the master's pitch percentage (tempo match).
    """
    if master_bpm > 0 and target_bpm > 0:
        effective = master_bpm * (1.0 + float(master_pitch) / 100.0)
        required = (effective / target_bpm - 1.0) * 100.0
        return max(-20.0, min(20.0, required))
    return max(-20.0, min(20.0, float(master_pitch)))


def master_after_status(current: str, channel: str, previous: Dict[str, Any],
                        status: Dict[str, Any], other_status: Dict[str, Any]) -> str:
    """Which deck should own the sync master after one status poll.

    A deck that starts playing takes the master (the newly mixed-in track).
    When the master's track changes or it leaves "playing" while the other deck
    is playing, the other deck becomes master.
    """
    prev_state = str((previous or {}).get("state") or "").lower()
    new_state = str((status or {}).get("state") or "").lower()
    other_state = str((other_status or {}).get("state") or "").lower()
    if new_state == "playing" and prev_state != "playing":
        return channel
    if channel != current:
        return current
    other = "B" if channel == "A" else "A"
    if other_state == "playing":
        prev_path = (previous or {}).get("path")
        new_path = (status or {}).get("path")
        if prev_path and new_path and prev_path != new_path:
            return other
        if prev_state == "playing" and new_state != "playing":
            return other
    return current


def browse_action(status: Dict[str, Any], dirty: bool) -> str:
    """What the browse-knob push should do for one deck.

    Playing -> pause/stop. Paused after browsing -> load/play the selected row.
    Paused without browsing -> resume the current track. An empty deck has
    nothing to resume, so it loads the selection.
    """
    state = str((status or {}).get("state") or "").lower()
    if state == "playing":
        return "toggle"
    if dirty or not (status or {}).get("path"):
        return "load"
    return "play"


def deck_argv(theme: str) -> list:
    """The player command for one mixer deck.

    Decks are for mixing *tracks*, so they open the Music Folders library
    (Music mode), never the mix-archive list. --music-mode is passed to both
    the UI and, via the UI, to the engine it spawns. --no-autoplay keeps the
    first track paused until the DJ starts it.

    The theme is mapped through ``core.deck_theme`` because the player's
    --theme table is smaller than the mixer's cycle; an id it cannot parse
    makes argparse exit and the deck never starts.
    """
    return player_argv("NORMAL", extra_args=[
        "--music-mode",
        "--music-folder", "ALL",
        "--no-autoplay",
        "--no-bluetooth-auto",
        "--theme", core.deck_theme(theme),
    ])


def _luma(color: Tuple[int, int, int]) -> float:
    return (0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]) / 255.0


def _ink_accent(color: Tuple[int, int, int], dark_ui: bool) -> Tuple[int, int, int]:
    """Keep a theme accent readable as text on this field.

    Into Darkness and The Matrix use black accents. As a fill or a border that
    is correct; as a label it disappears into the field, so brighten it toward
    white just for text.
    """
    if dark_ui and _luma(color) < 0.28:
        return _light(color, (255, 255, 255), 0.72)
    return color


def _log(message: str) -> None:
    try:
        with open(CONFIG_DIR / "liquid_mixer.log", "a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


class LiquidMixer(GlassHost):
    """Full-screen mixer host. Reuses the Liquid Glass host's plumbing."""

    TITLE = "MP Liquid Mixer"

    def __init__(self, theme: Optional[str] = None, fullscreen: bool = True,
                 with_visualizer: bool = True, font: Optional[str] = None) -> None:
        super().__init__(argv=[], embed_check=False, mode="NORMAL", cli_theme=theme, font=font)
        self.model = core.MixerModel.load()
        self.theme = theme or core.read_theme()
        if theme:
            # Publish it: both decks and the visualizer follow the shared file,
            # and the 1s poll would otherwise revert the window to the old one.
            core.write_theme(theme)
            self.theme = core.read_theme()
        self.start_fullscreen = bool(fullscreen)
        self.with_visualizer = bool(with_visualizer)
        self.window = None
        self.area = None
        self.fixed = None
        self.boxes: Dict[str, Tuple[float, float, float, float]] = core.layout()
        self.decks: Dict[str, Dict[str, Any]] = {}
        self.drag: Optional[Tuple[str, str]] = None
        self._vol_sent: Dict[str, int] = {}
        self._vol_dirty = True
        self._deck_status: Dict[str, Dict[str, Any]] = {}
        self._viz_owned = False
        self._viz_visible = True
        self._theme_seen = self.theme
        self._closing = False
        self._ui_scale = 1.0
        self.s4_listener = None
        self.browse_target = "A"
        self.sync_master = "A"
        self._shift = {"A": False, "B": False}
        self._pitch_sent: Dict[str, int] = {}
        self._pitch_dirty = True
        self._bpm_cache: Dict[str, float] = {}
        self._browse_dirty = {"A": False, "B": False}
        self.recorder = mixer_recording.MasterRecorder()
        self._eq_sent: Dict[str, list] = {}
        self._eq_dirty = True
        self._knob_drag = None
        self._last_knob = ("", 0.0)
        self._window_drag = None
        self._window_resize = None
        self._window_target = None
        self._publish_pending = 0
        self._viz_relaunch_at = 0.0
        self._viz_proc = None
        self._last_published_geom = None
        self._geom_poll_at = 0.0
        self._rec_second = -1
        self._s4_last_seen = 0.0
        self._s4_status_text = "S4: starting"

    # ------------------------------------------------------------------
    # window / lifecycle
    # ------------------------------------------------------------------
    def run(self) -> int:
        GLib, Gtk, Vte = _import_gtk()
        import gi
        gi.require_version("Pango", "1.0")
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import Gdk as Gdk, Pango, PangoCairo
        self.Gdk, self.Gtk, self.Pango, self.PangoCairo = Gdk, Gtk, Pango, PangoCairo

        window = Gtk.Window()
        window.set_title(self.TITLE)
        window.set_decorated(False)
        window.set_app_paintable(True)
        window.set_default_size(core.DESIGN_W, core.DESIGN_H)
        window.set_position(Gtk.WindowPosition.CENTER)
        # A fullscreen allocation becomes the child-driven minimum and the WM
        # will not shrink the window afterwards. Pin a small explicit minimum.
        try:
            hints = Gdk.Geometry()
            hints.min_width, hints.min_height = 640, 360
            hints.max_width, hints.max_height = 16384, 16384
            window.set_geometry_hints(None, hints, Gdk.WindowHints.MIN_SIZE | Gdk.WindowHints.MAX_SIZE)
        except Exception:
            pass
        screen = window.get_screen()
        visual = screen.get_rgba_visual() if screen is not None else None
        if visual is not None:
            window.set_visual(visual)
        window.connect("delete-event", self._on_delete)
        window.connect("key-press-event", self._on_key)
        window.connect("size-allocate", self._on_allocate)
        window.connect("configure-event", self._on_configure)
        # Mouse events originate on the toplevel's GdkWindow; the drawing area
        # and the Fixed have no window of their own. The deck VTE children do,
        # so a click inside a deck stays there.
        window.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        window.connect("button-press-event", self._on_press)
        window.connect("button-release-event", self._on_release)
        window.connect("motion-notify-event", self._on_motion)
        window.set_opacity(self.model.opacity)

        self.window = window
        self.area = Gtk.DrawingArea()
        self.area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.area.set_can_focus(True)
        self.area.connect("draw", self._on_draw)
        self.area.connect("button-press-event", self._on_press)
        self.area.connect("button-release-event", self._on_release)
        self.area.connect("motion-notify-event", self._on_motion)

        self.fixed = Gtk.Fixed()
        self.fixed.put(self.area, 0, 0)
        # The Fixed sits above the DrawingArea, so chrome clicks land here, not
        # on the area. Give both the same handlers; the VTEs keep their own.
        self.fixed.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.fixed.connect("button-press-event", self._on_press)
        self.fixed.connect("button-release-event", self._on_release)
        self.fixed.connect("motion-notify-event", self._on_motion)
        window.add(self.fixed)
        window.show_all()

        if self.start_fullscreen:
            window.fullscreen()
        else:
            GLib.timeout_add(100, self._apply_windowed_size)

        self._spawn_decks(Vte, GLib)
        self._start_s4()
        if self.with_visualizer:
            GLib.timeout_add(500, self._start_visualizer)
        GLib.timeout_add(120, self._tick)
        # Font metrics need a realized VTE and a real allocation. Re-fit a few
        # times so a slow engine start or a WM resize cannot leave the decks
        # at the fallback face.
        for delay in (600, 1800, 3500):
            GLib.timeout_add(delay, self._fit_deck_fonts)

        signal.signal(signal.SIGINT, lambda *_: self._quit_from_signal())
        signal.signal(signal.SIGTERM, lambda *_: self._quit_from_signal())

        try:
            Gtk.main()
        finally:
            self._shutdown()
        return 0

    def _quit_from_signal(self) -> None:
        try:
            _import_gtk()
            from gi.repository import GLib as _GLib
            _GLib.idle_add(self.quit)
        except Exception:
            self.quit()

    def _on_delete(self, *_args) -> bool:
        self.quit()
        return True

    def quit(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            from gi.repository import Gtk
            Gtk.main_quit()
        except Exception:
            pass

    def _shutdown(self) -> None:
        # The decks spawn detached engines. Without this they keep playing with
        # no window attached, so both are asked to stop before the host exits.
        for channel, deck in self.decks.items():
            namespace = deck.get("namespace")
            if not namespace:
                continue
            core.send_ipc(namespace, "transport_stop", timeout=0.4)
            core.send_ipc(namespace, "stop", timeout=0.4)
        if self.recorder.is_recording:
            self.recorder.stop()
        if self.s4_listener is not None:
            self.s4_listener.stop()
        if self._viz_owned:
            self._stop_visualizer()
        if self._viz_proc is not None:
            try:
                self._viz_proc.poll()
            except Exception:
                pass
        self.model.save()

    # ------------------------------------------------------------------
    # decks
    # ------------------------------------------------------------------
    def _spawn_decks(self, Vte, GLib) -> None:
        from gi.repository import Gdk as Gdk, Pango
        Gtk = self.Gtk
        window = self.window
        for channel in ("A", "B"):
            namespace = core.DECK_NAMESPACES[channel]
            term = Vte.Terminal()
            term.set_audible_bell(False)
            if hasattr(term, "set_scrollback_lines"):
                term.set_scrollback_lines(0)
            if hasattr(term, "set_cursor_blink_mode"):
                try:
                    term.set_cursor_blink_mode(Vte.CursorBlinkMode.OFF)
                except Exception:
                    pass
            # VTE paints an opaque black pane unless it is given the RGBA
            # visual itself; the window's visual does not reach the child.
            try:
                rgba = self.window.get_screen().get_rgba_visual()
                if rgba is not None:
                    term.set_visual(rgba)
                    term.set_app_paintable(True)
            except Exception:
                pass
            self._style_deck_terminal(term)
            if hasattr(term, "set_bold_is_bright"):
                term.set_bold_is_bright(False)
            try:
                desc = Pango.FontDescription.from_string(self.font_name or get_liquid_glass_font())
                term.set_font(desc)
            except Exception:
                pass
            term.connect("key-press-event", self._on_key)
            term.connect("child-exited", self._on_deck_exit, channel)
            term.connect("focus-in-event", self._on_deck_focus, channel)
            term.connect("button-press-event", self._on_deck_click, channel)
            self.fixed.put(term, 0, 0)
            # show_all() ran before the decks existed. A widget added afterwards
            # stays unrealized (and therefore invisible) until shown.
            term.show()
            self.decks[channel] = {"vte": term, "namespace": namespace, "online": False}

        self._on_allocate(window)

        for channel, deck in self.decks.items():
            term = deck["vte"]
            argv = deck_argv(self.theme)
            env = _child_env(as_dict=True)
            env["MP_PLAYER_NAMESPACE"] = deck["namespace"]
            env["MP_LIQUID_GLASS_MIXER"] = "1"
            env_list = [f"{key}={value}" for key, value in env.items()]
            try:
                term.spawn_async(
                    Vte.PtyFlags.DEFAULT, str(PROJECT_ROOT), argv, env_list,
                    GLib.SpawnFlags.DEFAULT, None, None, -1, None, None,
                )
            except Exception as exc:
                print(f"MP Liquid Mixer: deck {channel} failed to start: {exc}", file=sys.stderr)

    def _on_deck_focus(self, _term, _event, channel: str) -> bool:
        self._set_browse_target(channel)
        return False

    def _on_deck_click(self, _term, _event, channel: str) -> bool:
        self._set_browse_target(channel)
        return False

    def _set_browse_target(self, channel: str) -> None:
        if channel in ("A", "B") and channel != self.browse_target:
            self.browse_target = channel
            if self.area is not None:
                self.area.queue_draw()

    def _on_deck_exit(self, _term, _status, channel: str) -> None:
        deck = self.decks.get(channel)
        if deck:
            deck["online"] = False
        if self.area is not None and not self._closing:
            self.area.queue_draw()

    # The player's own layout caps its content at 74 columns
    # (MP_Audio_Player: iw = max(24, min(w - 4, 74))). The deck font is chosen
    # so those 74 columns still fit across the card.
    DECK_COLS = 74

    def _deck_term_rect(self, channel: str) -> Tuple[float, float, float, float]:
        return self.boxes[f"deck_{channel.lower()}_term"]

    def _place_decks(self) -> None:
        for channel, deck in self.decks.items():
            term = deck.get("vte")
            if term is None:
                continue
            box = self._deck_term_rect(channel)
            width, height = max(32, int(box[2])), max(32, int(box[3]))
            term.set_size_request(width, height)
            self.fixed.move(term, int(box[0]), int(box[1]))
            # A VTE added after show_all() is not allocated until the window
            # changes size. Force its rectangle so it is never a 1x1 cursor.
            if self.Gdk is not None:
                try:
                    rect = self.Gdk.Rectangle()
                    rect.x, rect.y, rect.width, rect.height = int(box[0]), int(box[1]), width, height
                    term.size_allocate(rect)
                except Exception:
                    pass

    def _fit_deck_fonts(self) -> bool:
        """Fit a 74-column player grid into each card at the largest size."""
        from gi.repository import Pango
        for channel, deck in self.decks.items():
            term = deck.get("vte")
            if term is None:
                continue
            card = self.boxes.get(f"deck_{channel.lower()}")
            header = self.boxes.get(f"deck_{channel.lower()}_header")
            if not card or not header:
                continue
            scale = self._ui_scale
            max_w = card[2] - 24 * scale
            max_h = card[3] - header[3] - 24 * scale
            chosen = None
            cell = (0, 0)
            for size in (16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6):
                try:
                    term.set_font(Pango.FontDescription.from_string(f"Monospace {size}"))
                    width = int(term.get_char_width() or 0)
                    height = int(term.get_char_height() or 0)
                except Exception:
                    continue
                if width <= 0 or height <= 0:
                    continue
                # The player caps its content at 74 columns; the card height is
                # then consumed by whatever row count that font produces.
                if width * self.DECK_COLS <= max_w and height * 30 <= max_h:
                    chosen, cell = size, (width, height)
                    break
            if chosen is None:
                continue
            term.set_font(Pango.FontDescription.from_string(f"Monospace {chosen}"))
            deck["font_size"] = chosen
            if deck.get("font_logged") != chosen:
                deck["font_logged"] = chosen
                _log(f"deck {channel}: Monospace {chosen}, cell {cell[0]}x{cell[1]}, "
                     f"card {card[2]:.0f}x{card[3]:.0f}")
        return False

    def _on_allocate(self, widget, _allocation=None) -> None:
        try:
            width = widget.get_allocated_width()
            height = widget.get_allocated_height()
        except Exception:
            return
        # During the fullscreen -> windowed transition, an intermediate
        # allocation (still fullscreen) must not rebuild the layout: doing so
        # puts the old big size request back and the WM refuses to shrink.
        if self._window_target is not None and (width, height) != self._window_target:
            return
        self._apply_layout_size(width, height, fit=not bool(self._window_resize))

    def _apply_layout_size(self, width: int, height: int, fit: bool = True) -> None:
        """Rebuild the design grid for a real window size."""
        width, height = max(64, int(width)), max(64, int(height))
        self.boxes = core.layout(width, height)
        canvas = self.boxes["canvas"]
        self._ui_scale = max(0.4, canvas[2] / core.DESIGN_W)
        if self.area is not None:
            self.area.set_size_request(width, height)
            self.fixed.move(self.area, 0, 0)
            # A Gtk.Fixed may keep the old allocation across the fullscreen
            # configure. Force the drawing surface to the new rectangle so the
            # chrome is never left as a 1x1 strip behind the terminals.
            if self.Gdk is not None:
                try:
                    rect = self.Gdk.Rectangle()
                    rect.x, rect.y, rect.width, rect.height = 0, 0, int(width), int(height)
                    self.area.size_allocate(rect)
                except Exception:
                    pass
        self._place_decks()
        if fit:
            self._fit_deck_fonts()
        if self.area is not None:
            self.area.queue_draw()
        self._publish_geometry()

    def _fit_scheduled(self) -> bool:
        self._fit_pending = False
        return self._fit_deck_fonts()

    def _host_wid(self) -> int:
        try:
            window = self.window.get_window()
            return int(window.get_xid()) if window is not None else 0
        except Exception:
            return 0

    def _on_configure(self, _widget, _event) -> bool:
        self._schedule_publish()
        return False

    def _schedule_publish(self) -> None:
        if self._publish_pending:
            return
        try:
            from gi.repository import GLib as _GLib
            self._publish_pending = _GLib.timeout_add(60, self._publish_now)
        except Exception:
            self._publish_geometry()

    def _publish_now(self) -> bool:
        self._publish_geometry()
        return False

    def _publish_geometry(self) -> None:
        """Publish the mixer rectangle so the bonded visualizer follows it."""
        try:
            from liquid_player_visualizer import publish_liquid_player_window_geometry
        except ImportError:
            try:
                from scripts.liquid_player_visualizer import publish_liquid_player_window_geometry
            except ImportError:
                return
        if self.window is None:
            return
        try:
            width = self.window.get_allocated_width()
            height = self.window.get_allocated_height()
            if width < 64 or height < 64:
                return
            try:
                origin = self.window.get_position()
            except Exception:
                origin = (0, 0)
            geometry = (int(origin[0]), int(origin[1]), int(width), int(height), self._host_wid())
            if geometry == self._last_published_geom:
                return
            self._last_published_geom = geometry
            _log(f"publish mixer rect {geometry[0]},{geometry[1]} {geometry[2]}x{geometry[3]}")
            publish_liquid_player_window_geometry(geometry[0], geometry[1], geometry[2], geometry[3], wid=geometry[4])
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Traktor Kontrol S4
    # ------------------------------------------------------------------
    def _start_s4(self) -> None:
        """Read the S4's native fader axes (no MIDI mode required)."""
        self._s4_started_at = time.time()
        self.s4_listener = mixer_evdev.S4AxisListener(on_change=self._on_s4_change)
        self.s4_listener.start()

    def _on_s4_change(self, action: str, value: float) -> None:
        # Called on the evdev thread; hand the value to the GTK loop.
        try:
            from gi.repository import GLib as _GLib
            _GLib.idle_add(self._apply_s4, action, value)
        except Exception:
            pass

    def _apply_s4(self, action: str, value: float) -> bool:
        if action.startswith("pitch:"):
            channel = action.split(":", 1)[1]
            self.model.set_pitch(channel, value)
            self._pitch_dirty = True
            self._s4_last_seen = time.time()
            self._s4_last_action = f"PITCH {channel} {value:+.1f}%"
            if self.area is not None:
                self.area.queue_draw()
            return False
        if action.startswith("shift:"):
            channel = action.split(":", 1)[1]
            if channel in self._shift:
                self._shift[channel] = value > 0
            return False
        if action.startswith("sync:"):
            channel = action.split(":", 1)[1]
            self._sync_deck(channel, manual=self._shift.get(channel, False))
            self._s4_last_seen = time.time()
            if self.area is not None:
                self.area.queue_draw()
            return False
        if action == "browse":
            self._browse_step(value)
            self._s4_last_seen = time.time()
            self._s4_last_action = f"BROWSE {self.browse_target}"
            if self.area is not None:
                self.area.queue_draw()
            return False
        if action == "load":
            self._browse_push()
            self._s4_last_seen = time.time()
            self._s4_last_action = f"BROWSE {self.browse_target}"
            if self.area is not None:
                self.area.queue_draw()
            return False
        if action.startswith("play:"):
            channel = action.split(":", 1)[1]
            self._set_browse_target(channel)
            self._toggle_deck(channel)
            self._s4_last_seen = time.time()
            self._s4_last_action = f"PLAY {channel}"
            if self.area is not None:
                self.area.queue_draw()
            return False
        if mixer_evdev.apply_action(self.model, action, value):
            self._vol_dirty = True
            self._s4_last_seen = time.time()
            label = action.split(":", 1)[1] if action.startswith("channel:") else "CROSSFADE"
            self._s4_last_action = f"{label} {int(round(value * 100))}%"
            if self.area is not None:
                self.area.queue_draw()
        return False

    def _set_master(self, channel: str) -> None:
        if channel not in ("A", "B") or channel == self.sync_master:
            return
        self.sync_master = channel
        _log(f"sync master -> {channel}")
        if self.area is not None:
            self.area.queue_draw()

    def _sync_deck(self, channel: str, manual: bool = False) -> None:
        """Match this deck's tempo to the master, or make it master with Shift."""
        if channel not in ("A", "B"):
            return
        if manual:
            self._set_master(channel)
            self._s4_last_action = f"MASTER {channel}"
            return
        if channel == self.sync_master:
            self._s4_last_action = f"{channel} MASTER"
            return
        master = self.sync_master
        master_status = dict(self._deck_status.get(master) or {})
        target_status = dict(self._deck_status.get(channel) or {})
        master_pitch = float(master_status.get("pitch_percent", self.model.pitch(master)) or 0.0)

        def worker():
            master_bpm = self._track_bpm(master_status.get("path"))
            target_bpm = self._track_bpm(target_status.get("path"))
            required = sync_pitch(master_pitch, master_bpm, target_bpm)
            try:
                from gi.repository import GLib as _GLib
                _GLib.idle_add(self._apply_sync_result, channel, master,
                               required, master_bpm, target_bpm)
            except Exception:
                pass

        threading.Thread(target=worker, name=f"sync-{channel}", daemon=True).start()

    def _apply_sync_result(self, channel: str, master: str, pitch: float,
                           master_bpm: float = 0.0, target_bpm: float = 0.0) -> bool:
        self.model.set_pitch(channel, pitch)
        self._pitch_dirty = True
        if master_bpm and target_bpm:
            self._s4_last_action = f"SYNC {channel}->{master} {master_bpm:.1f} BPM {pitch:+.1f}%"
            _log(f"sync {channel} -> {master}: {master_bpm:.1f} -> {target_bpm:.1f} BPM, pitch {pitch:+.1f}%")
        else:
            self._s4_last_action = f"SYNC {channel}->{master} {pitch:+.1f}%"
            _log(f"sync {channel} -> {master}: pitch {pitch:+.1f}% (no BPM tags)")
        if self.area is not None:
            self.area.queue_draw()
        return False

    def _track_bpm(self, path: Optional[str]) -> float:
        """BPM from the track tags, cached. 0 when absent."""
        if not path:
            return 0.0
        if path in self._bpm_cache:
            return self._bpm_cache[path]
        bpm = 0.0
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)],
                capture_output=True, text=True, timeout=2.0,
            )
            if result.returncode == 0:
                payload = json.loads(result.stdout or "{}")
                tags = (payload.get("format", {}) or {}).get("tags", {}) or {}
                for key, value in tags.items():
                    if str(key).lower() in ("bpm", "tbpm"):
                        bpm = float(str(value).replace(",", "."))
                        break
        except Exception:
            bpm = 0.0
        if not (30.0 <= bpm <= 300.0):
            bpm = 0.0
        self._bpm_cache[path] = bpm
        return bpm

    def _browse_keys(self) -> Optional[Any]:
        deck = self.decks.get(self.browse_target) or {}
        return deck.get("vte")

    def _browse_step(self, direction: float) -> None:
        """Rotate the browse knob: move the target deck's playlist selection."""
        term = self._browse_keys()
        if term is None:
            return
        self._browse_dirty[self.browse_target] = True
        data = b"\x1b[B" if direction > 0 else b"\x1b[A"
        self._feed_deck(term, data)

    def _browse_push(self) -> None:
        """Browse-knob push: stop when playing, load/play when paused.

        A rotation marks that deck's selection as new. Pushing while playing
        pauses and clears the mark, so the next push resumes the same track.
        Pushing a paused deck with a new selection loads and plays that row;
        without a new selection it simply resumes.
        """
        target = self.browse_target
        namespace = core.DECK_NAMESPACES.get(target)
        if not namespace:
            return
        dirty = bool(self._browse_dirty.get(target))
        term = self._browse_keys()

        def worker():
            status = core.deck_status(namespace)
            action = browse_action(status, dirty)
            if action == "load":
                try:
                    from gi.repository import GLib as _GLib
                    _GLib.idle_add(self._browse_load, target)
                except Exception:
                    pass
            else:
                core.send_ipc(namespace, "toggle" if action == "toggle" else "play", timeout=0.6)
            self._browse_dirty[target] = False
            _log(f"browse push {target}: {action}")

        threading.Thread(target=worker, name=f"browse-push-{target}", daemon=True).start()

    def _browse_load(self, channel: Optional[str] = None) -> bool:
        """Feed Enter to the target deck so its selected row loads and plays."""
        target = channel or self.browse_target
        deck = self.decks.get(target) or {}
        term = deck.get("vte")
        if term is not None:
            self._feed_deck(term, b"\r")
        self._browse_dirty[target] = False
        return False

    def _feed_deck(self, term, data: bytes) -> None:
        # VTE feeds the child's PTY; the player maps arrows to its list and
        # Enter to "play the selected row".
        try:
            term.feed_child_binary(data)
            return
        except Exception:
            pass
        try:
            term.feed_child(data)
        except Exception as exc:
            _log(f"could not feed deck key {data!r}: {exc!r}")

    def _toggle_deck(self, channel: str) -> None:
        """Left/right Play toggles that deck's engine over its own IPC socket."""
        namespace = core.DECK_NAMESPACES.get(channel)
        if not namespace:
            return

        def worker():
            result = core.send_ipc(namespace, "toggle", timeout=0.6)
            _log(f"Play {channel}: {'ok' if result is not None else 'engine offline'}")

        threading.Thread(target=worker, name=f"play-{channel}", daemon=True).start()

    def _s4_status(self) -> Tuple[str, Tuple[int, int, int]]:
        listener = self.s4_listener
        if listener is None:
            return "S4: off", (150, 160, 168)
        status = str(listener.status or "")
        if status.startswith("native"):
            last = float(listener.last_event or 0.0)
            path = status.split(" ", 1)[1] if " " in status else ""
            if last and time.time() - last < 6.0:
                return f"S4 {path}  {getattr(self, '_s4_last_action', '')}", (80, 230, 140)
            if last:
                return f"S4 {path}  idle", (250, 205, 90)
            return f"S4 {path}  ready", (250, 205, 90)
        if "waiting" in status:
            return "S4: not found - check the USB cable", (250, 205, 90)
        return f"S4: {status}", (250, 205, 90)

    # ------------------------------------------------------------------
    # visualizer
    # ------------------------------------------------------------------
    def _visualizer_preset(self) -> Dict[str, Any]:
        try:
            from liquid_player_visualizer import read_player_visualizer_state
        except ImportError:
            from scripts.liquid_player_visualizer import read_player_visualizer_state
        state = read_player_visualizer_state()
        return {
            "id": int(state.get("preset_id") or 0),
            "key": state.get("preset_key") or "",
            "name": state.get("preset_name") or "",
        }

    def _start_visualizer(self) -> bool:
        try:
            from liquid_player_visualizer import (
                mark_player_visualizer_attached,
                write_player_visualizer_state,
            )
        except ImportError:
            from scripts.liquid_player_visualizer import (
                mark_player_visualizer_attached,
                write_player_visualizer_state,
            )
        if core.visualizer_pid_alive():
            self._viz_owned = True
            self._viz_visible = True
            try:
                write_player_visualizer_state(True, self._visualizer_preset(), visible=True)
            except Exception:
                pass
            self._publish_geometry()
            return False
        script = next((p for p in (PROJECT_ROOT / "MP_Liquid_Visualizer.py",
                                   PROJECT_ROOT / "scripts" / "mp_liquid_visualizer_vulkan.py")
                       if p.is_file()), None)
        if script is None:
            return False
        try:
            mark_player_visualizer_attached(visible=True)
            log = open(CONFIG_DIR / "liquid_mixer_visualizer.log", "a", encoding="utf-8")
            proc = subprocess.Popen(
                [sys.executable, str(script), "--preset", "random", "--backend", "opengl"],
                cwd=str(PROJECT_ROOT), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                start_new_session=True, env=_visualizer_env(companion=True),
            )
            log.close()
            self._viz_proc = proc
            core.write_visualizer_pid(proc.pid)
            self._viz_owned = True
            self._viz_visible = True
            write_player_visualizer_state(True, self._visualizer_preset(), visible=True)
            self.window.present()
        except Exception:
            self._viz_owned = False
        return False

    def _stop_visualizer(self) -> None:
        try:
            from liquid_player_visualizer import write_player_visualizer_state
        except ImportError:
            from scripts.liquid_player_visualizer import write_player_visualizer_state
        core.stop_owned_visualizer()
        try:
            write_player_visualizer_state(False, self._visualizer_preset())
        except Exception:
            pass

    def _toggle_visualizer(self) -> None:
        try:
            from liquid_player_visualizer import (
                read_player_visualizer_state,
                write_player_visualizer_state,
            )
        except ImportError:
            from scripts.liquid_player_visualizer import (
                read_player_visualizer_state,
                write_player_visualizer_state,
            )
        if not self._viz_owned:
            self._start_visualizer()
            return
        state = read_player_visualizer_state()
        wanted = state.get("visible") is False
        write_player_visualizer_state(True, self._visualizer_preset(), visible=wanted)
        self._viz_visible = wanted
        if wanted:
            self._publish_geometry()
            self.window.present()
        self.area.queue_draw()

    # ------------------------------------------------------------------
    # input
    # ------------------------------------------------------------------
    def _event_from_deck(self, widget) -> bool:
        return any(deck.get("vte") is widget for deck in self.decks.values())

    def _on_key(self, widget, event) -> bool:
        Gdk = self.Gdk
        key = event.keyval
        from_deck = self._event_from_deck(widget)
        # A deck's player already cycles themes on C and writes the shared
        # theme file, so C is left alone there: search can still type "c" and
        # the mixer picks the new theme up within a second. The mixer's own
        # handler covers the chrome and the background.
        if key == Gdk.KEY_c and not from_deck:
            self._cycle_theme()
            return True
        if key == Gdk.KEY_v and not from_deck:
            self._toggle_visualizer()
            return True
        if key == Gdk.KEY_r and not from_deck:
            self._toggle_recording()
            return True
        if key == Gdk.KEY_x and not from_deck:
            self.model.toggle_mode()
            self._vol_dirty = True
            self.model.save()
            self.area.queue_draw()
            return True
        if key == Gdk.KEY_F11:
            self._toggle_fullscreen()
            return True
        if key == Gdk.KEY_Escape:
            self.quit()
            return True
        return False

    def _toggle_fullscreen(self) -> None:
        self._toggle_window_mode()

    def _toggle_window_mode(self) -> None:
        """Full screen <-> the 1920x1080 window from the reference screenshot."""
        if self.window is None:
            return
        _log(f"window mode toggle: fullscreen={self.start_fullscreen}")
        if self.start_fullscreen:
            self.window.unfullscreen()
            self.start_fullscreen = False
            try:
                from gi.repository import GLib as _GLib
                _GLib.timeout_add(90, self._apply_windowed_size)
            except Exception:
                self._apply_windowed_size()
        else:
            self._window_target = None
            self.window.fullscreen()
            self.start_fullscreen = True
            try:
                from gi.repository import GLib as _GLib
                _GLib.timeout_add(220, self._after_fullscreen)
            except Exception:
                pass

    def _after_windowed_size(self, x: int, y: int, width: int, height: int, attempt: int = 0) -> bool:
        if self.window is None or self.start_fullscreen:
            return False
        try:
            actual = (self.window.get_allocated_width(), self.window.get_allocated_height())
        except Exception:
            actual = (0, 0)
        if actual != (width, height):
            # The WM's unfullscreen animation can swallow the first resize.
            # Keep asking until the real allocation matches, then lay out.
            try:
                self.window.resize(width, height)
            except Exception:
                pass
            if attempt < 6:
                try:
                    from gi.repository import GLib as _GLib
                    _GLib.timeout_add(160, self._after_windowed_size, x, y, width, height, attempt + 1)
                except Exception:
                    pass
            else:
                # Give up on the exact size but never leave a 1x1 surface.
                self._window_target = None
                self._apply_layout_size(width, height)
        else:
            self._window_target = None
            self._apply_layout_size(width, height)
        try:
            if self._window_drag is None:
                self.window.move(int(x), int(y))
        except Exception:
            pass
        self._schedule_publish()
        return False

    def _after_fullscreen(self) -> bool:
        if self.window is not None:
            self._apply_layout_size(self.window.get_allocated_width(), self.window.get_allocated_height())
        return False

    def _apply_windowed_size(self) -> bool:
        if self.window is None:
            return False
        try:
            display = self.window.get_display()
            gdk_window = self.window.get_window()
            monitor = (display.get_monitor_at_window(gdk_window)
                       if gdk_window is not None else display.get_primary_monitor())
            geometry = monitor.get_geometry()
            width, height = core.windowed_size(geometry.width, geometry.height)
            _log(f"windowed size {width}x{height} on {geometry.width}x{geometry.height}")
            self._window_target = (width, height)
            current = (self.window.get_allocated_width(), self.window.get_allocated_height())
            if current == (width, height):
                # Already at the windowed size (a --windowed launch or a
                # repeat call): never clear the requests at this size.
                self._window_target = None
                self._apply_layout_size(width, height)
                self.window.move(
                    geometry.x + max(0, (geometry.width - width) // 2),
                    geometry.y + max(0, (geometry.height - height) // 2),
                )
                return False
            # The DrawingArea and the deck terminals request the *current*
            # allocation, which pins the window minimum. Drop those requests
            # so the configure can actually shrink, then let size-allocate
            # rebuild them for the new window.
            if self.area is not None:
                self.area.set_size_request(1, 1)
            for deck in self.decks.values():
                term = deck.get("vte")
                if term is not None:
                    term.set_size_request(1, 1)
            self.window.resize(width, height)
            self.window.move(
                geometry.x + max(0, (geometry.width - width) // 2),
                geometry.y + max(0, (geometry.height - height) // 2),
            )
            # The window is already at the target size on a --windowed launch,
            # so no configure arrives. The retry below notices the matching
            # allocation and builds the layout then.
            try:
                from gi.repository import GLib as _GLib
                center_x = geometry.x + max(0, (geometry.width - width) // 2)
                center_y = geometry.y + max(0, (geometry.height - height) // 2)
                _GLib.timeout_add(180, self._after_windowed_size, center_x, center_y, width, height)
            except Exception:
                pass
            if self.fixed is not None:
                self.fixed.queue_resize()
            self._publish_geometry()
        except Exception as exc:
            _log(f"windowed resize failed: {exc!r}")
        return False

    def _on_press(self, _widget, event) -> bool:
        # Keyboard shortcuts should reach the mixer after a click on its chrome,
        # while a click inside a deck hands the keyboard to that player.
        self.area.grab_focus()
        x, y = float(event.x), float(event.y)
        try:
            window_w = self.window.get_allocated_width()
            window_h = self.window.get_allocated_height()
        except Exception:
            window_w = window_h = 0
        if not self.start_fullscreen and x > window_w - 30 and y > window_h - 30:
            self._window_resize = (window_w, window_h, event.x_root, event.y_root)
            return True
        if y < self.boxes["top_bar"][3]:
            if core.inside(x, y, self.boxes["visualizer_button"]):
                self._toggle_visualizer()
                self.area.queue_draw()
                return True
            if core.inside(x, y, self.boxes["close_button"]):
                self.quit()
                return True
            if event.type == self.Gdk.EventType._2BUTTON_PRESS:
                self._window_drag = None
                self._toggle_window_mode()
                return True
            if not self.start_fullscreen and self.window is not None:
                try:
                    wx, wy = self.window.get_position()
                    self._window_drag = (event.x_root - wx, event.y_root - wy)
                except Exception:
                    self._window_drag = None
            return True
        for channel in ("A", "B"):
            if core.inside(x, y, self.boxes[f"pitch_{channel.lower()}"]):
                self.drag = ("pitch", channel)
                self._drag_pitch(channel, y)
                return True
            if core.inside(x, y, self.boxes[f"sync_{channel.lower()}"]):
                manual = bool(int(getattr(event, "state", 0)) & int(self.Gdk.ModifierType.SHIFT_MASK))
                self._sync_deck(channel, manual=manual)
                self._s4_last_seen = time.time()
                self.area.queue_draw()
                return True
        for group in ("gain", "high", "mid", "low"):
            for channel in core.CHANNELS:
                if not core.inside(x, y, self.boxes[f"knob_{group}_{channel}"]):
                    continue
                key = f"{group}:{channel}"
                now = time.monotonic()
                if self._last_knob[0] == key and now - self._last_knob[1] < 0.35:
                    self._reset_knob(group, channel)
                    self._last_knob = ("", 0.0)
                else:
                    self._last_knob = (key, now)
                    low, high = self._knob_range(group)
                    self._knob_drag = (group, channel, y, self._knob_value(group, channel), low, high)
                    self.drag = ("knob", key)
                return True
        for channel in core.CHANNELS:
            if core.inside(x, y, self.boxes[f"strip_{channel}"]):
                self.drag = ("channel", channel)
                self._drag_channel(channel, y)
                return True
        if core.inside(x, y, self.boxes["xfader"]):
            self.drag = ("xfader", "")
            self._drag_xfader(x)
            return True
        if core.inside(x, y, self.boxes["opacity"]):
            self.drag = ("opacity", "")
            self._drag_opacity(x)
            return True
        if core.inside(x, y, self.boxes["browse_button"]):
            self.browse_target = "B" if self.browse_target == "A" else "A"
            self.area.queue_draw()
            return True
        if core.inside(x, y, self.boxes["mode_button"]):
            self.model.toggle_mode()
            self._vol_dirty = True
            self.model.save()
            self.area.queue_draw()
            return True
        if core.inside(x, y, self.boxes["record_button"]):
            self._toggle_recording()
            return True
        if core.inside(x, y, self.boxes["visualizer_button"]):
            self._toggle_visualizer()
            self.area.queue_draw()
            return True
        if core.inside(x, y, self.boxes["close_button"]):
            self.quit()
            return True
        return False

    def _on_motion(self, _widget, event) -> bool:
        if self._window_resize is not None and self.window is not None:
            start_w, start_h, start_x, start_y = self._window_resize
            width = max(960, int(start_w + (event.x_root - start_x)))
            height = max(540, int(start_h + (event.y_root - start_y)))
            self.window.resize(width, height)
            return True
        if self._window_drag is not None and self.window is not None:
            if int(getattr(event, "state", 0)) & int(self.Gdk.ModifierType.BUTTON1_MASK):
                offset_x, offset_y = self._window_drag
                self.window.move(int(event.x_root - offset_x), int(event.y_root - offset_y))
                self._schedule_publish()
                return True
            return False
        if not self.drag:
            return False
        kind, channel = self.drag
        x, y = float(event.x), float(event.y)
        if kind == "channel":
            self._drag_channel(channel, y)
        elif kind == "xfader":
            self._drag_xfader(x)
        elif kind == "opacity":
            self._drag_opacity(x)
        elif kind == "pitch":
            self._drag_pitch(channel, y)
        elif kind == "knob" and self._knob_drag is not None:
            group, channel, start_y, start_value, low, high = self._knob_drag
            delta = (start_y - y) / max(1.0, 140.0 * self._ui_scale) * (high - low)
            self._set_knob(group, channel, start_value + delta)
        return True

    def _on_release(self, _widget, _event) -> bool:
        if self._window_resize is not None:
            self._window_resize = None
            if self.window is not None:
                self._apply_layout_size(self.window.get_allocated_width(),
                                        self.window.get_allocated_height())
            self._schedule_publish()
            return True
        if self._window_drag is not None:
            self._window_drag = None
            self._schedule_publish()
            return True
        if self.drag:
            kind, value = self.drag
            self.drag = None
            self._knob_drag = None
            self.model.save()
            if kind == "knob":
                group = value.split(":", 1)[0]
                if group == "gain":
                    self._vol_dirty = True
                else:
                    self._eq_dirty = True
                self.area.queue_draw()
            elif kind == "pitch":
                self._pitch_dirty = True
                self.area.queue_draw()
            else:
                self._vol_dirty = True
        return False

    def _set_knob(self, group: str, channel: str, value: float) -> None:
        if group == "gain":
            if self.model.set_trim(channel, value):
                self._vol_dirty = True
        else:
            if self.model.set_eq(channel, group, value):
                self._eq_dirty = True
        self.area.queue_draw()

    def _reset_knob(self, group: str, channel: str) -> None:
        for key, _label, _low, _high, default in self.KNOB_ROWS:
            if key == group:
                self._set_knob(group, channel, default)
                self.model.save()
                return

    def _toggle_recording(self) -> None:
        if self.recorder.is_recording:
            self.recorder.stop()
        else:
            self.recorder.start()
        self._rec_second = -1
        self.area.queue_draw()

    def _drag_channel(self, channel: str, y: float) -> None:
        box = self.boxes[f"strip_{channel}"]
        value = core.fader_value_from_y(y, box)
        if self.model.set_channel(channel, value):
            self._vol_dirty = True
            self.area.queue_draw()

    def _drag_xfader(self, x: float) -> None:
        self.model.set_crossfade(core.xfader_value_from_x(x, self.boxes["xfader"]))
        self._vol_dirty = True
        self.area.queue_draw()

    def _drag_pitch(self, channel: str, y: float) -> None:
        box = self.boxes[f"pitch_{channel.lower()}"]
        top = box[1] + 32 * self._ui_scale
        bottom = box[1] + box[3] - 32 * self._ui_scale
        t = (float(y) - top) / max(1.0, bottom - top)
        t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
        pitch = core.PITCH_MAX - t * (core.PITCH_MAX - core.PITCH_MIN)
        if abs(pitch) < 0.4:
            pitch = 0.0
        self.model.set_pitch(channel, pitch)
        self._pitch_dirty = True
        self.area.queue_draw()

    def _drag_opacity(self, x: float) -> None:
        value = core.opacity_from_x(x, self.boxes["opacity"])
        self.model.opacity = value
        if self.window is not None:
            self.window.set_opacity(value)
        self.area.queue_draw()

    def _style_deck_terminal(self, term) -> None:
        """Paint a deck pane with the active theme's console colours."""
        if term is None or self.Gdk is None:
            return
        background = core.theme_console(self.theme)
        ink = core.deck_pane_ink(self.theme)
        term.set_color_background(self.Gdk.RGBA(background[0] / 255.0, background[1] / 255.0,
                                                background[2] / 255.0, 1.0))
        term.set_color_foreground(self.Gdk.RGBA(ink[0] / 255.0, ink[1] / 255.0, ink[2] / 255.0, 1.0))

    def _apply_deck_theme(self) -> None:
        for deck in self.decks.values():
            self._style_deck_terminal(deck.get("vte"))

    def _cycle_theme(self) -> None:
        self.theme = core.next_theme(self.theme)
        core.write_theme(self.theme)
        self._theme_seen = self.theme
        self._apply_deck_theme()
        self.area.queue_draw()

    # ------------------------------------------------------------------
    # periodic work
    # ------------------------------------------------------------------
    def _tick(self) -> bool:
        if self._closing:
            return False
        was_recording = self.recorder.is_recording
        self.recorder.tick()
        if self.recorder.is_recording:
            second = int(self.recorder.elapsed())
            if second != self._rec_second:
                self._rec_second = second
                self.area.queue_draw()
        elif was_recording:
            self.area.queue_draw()
        now = time.monotonic()
        if now - getattr(self, "_theme_poll_at", 0.0) > 1.0:
            self._theme_poll_at = now
            shared = core.read_theme()
            if shared != self.theme:
                self.theme = shared
                self._apply_deck_theme()
                self.area.queue_draw()
        if now - getattr(self, "_status_poll_at", 0.0) > 1.2:
            self._status_poll_at = now
            for channel, deck in self.decks.items():
                namespace = deck["namespace"]
                sock = core.player_socket(namespace)
                res = core.send_ipc(namespace, "status", timeout=0.4) if sock.exists() else None
                was_online = bool(deck.get("online"))
                previous = self._deck_status.get(channel) or {}
                online = res is not None
                status = res.get("data") if (res and isinstance(res.get("data"), dict)) else {}
                deck["online"] = online
                self._deck_status[channel] = status
                if online and not was_online:
                    self._vol_dirty = True
                    self._eq_dirty = True
                    self._pitch_dirty = True
                other = "B" if channel == "A" else "A"
                new_master = master_after_status(self.sync_master, channel, previous,
                                                 status, self._deck_status.get(other) or {})
                if new_master != self.sync_master:
                    self._set_master(new_master)
                # Self-heal: a deck restarted by its own UI comes back at the
                # player default volume, so re-send when the engine disagrees.
                actual = status.get("volume")
                if online and actual is not None and int(actual) != self.model.volume_for(channel):
                    self._vol_dirty = True
            self.area.queue_draw()
        if now - getattr(self, "_geom_poll_at", 0.0) > 0.5:
            self._geom_poll_at = now
            self._publish_geometry()
        if self._viz_proc is not None:
            self._viz_proc.poll()
        if self.with_visualizer and self._viz_owned and not self._closing:
            if core.visualizer_pid_alive() is None and now - getattr(self, "_viz_relaunch_at", 0.0) > 5.0:
                self._viz_relaunch_at = now
                _log("visualizer disappeared; relaunching it behind the mixer")
                self._viz_owned = False
                self._start_visualizer()
        s4_text, _color = self._s4_status()
        if s4_text != self._s4_status_text:
            self._s4_status_text = s4_text
            self.area.queue_draw()
        if self._vol_dirty:
            self._apply_volumes()
        if self._eq_dirty:
            self._apply_eq()
        if self._pitch_dirty:
            self._apply_pitch()
        return True

    def _apply_volumes(self) -> None:
        pending = False
        for channel, namespace in core.DECK_NAMESPACES.items():
            volume = self.model.volume_for(channel)
            if self._vol_sent.get(channel) == volume:
                continue
            if not core.player_socket(namespace).exists():
                pending = True
                continue
            res = core.send_ipc(namespace, "set_volume", timeout=0.2, volume=volume)
            if res is None:
                pending = True
            else:
                self._vol_sent[channel] = volume
        self._vol_dirty = pending
        self.area.queue_draw()

    def _apply_pitch(self) -> None:
        pending = False
        for channel, namespace in core.DECK_NAMESPACES.items():
            percent = int(round(self.model.pitch(channel)))
            if self._pitch_sent.get(channel) == percent:
                continue
            if not core.player_socket(namespace).exists():
                pending = True
                continue
            res = core.send_ipc(namespace, "set_pitch", timeout=0.25, percent=percent)
            if res is None:
                pending = True
            else:
                self._pitch_sent[channel] = percent
        self._pitch_dirty = pending

    def _apply_eq(self) -> None:
        pending = False
        for channel, namespace in core.DECK_NAMESPACES.items():
            bands = self.model.eq_bands(channel)
            if self._eq_sent.get(channel) == bands:
                continue
            if not core.player_socket(namespace).exists():
                pending = True
                continue
            res = core.send_ipc(namespace, "set_eq_bands", timeout=0.25, bands=bands)
            if res is None:
                pending = True
            else:
                self._eq_sent[channel] = bands
        self._eq_dirty = pending

    # ------------------------------------------------------------------
    # painting
    # ------------------------------------------------------------------
    def _on_draw(self, _widget, cr) -> bool:
        import cairo
        width = self.area.get_allocated_width()
        height = self.area.get_allocated_height()
        self._paint(cr, width, height)
        return False

    def _text(self, cr, x: float, y: float, text: str, size: float, color: Tuple[int, int, int],
              alpha: float = 1.0, bold: bool = False, anchor: str = "start",
              max_width: float = 0.0) -> Tuple[int, int]:
        Pango = self.Pango
        PangoCairo = self.PangoCairo
        layout = PangoCairo.create_layout(cr)
        desc = Pango.FontDescription.from_string(f"Sans {max(6.0, size):.1f}")
        desc.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
        layout.set_font_description(desc)
        layout.set_text(str(text), -1)
        if max_width > 0:
            layout.set_width(int(max_width * Pango.SCALE))
            layout.set_ellipsize(Pango.EllipsizeMode.END)
        pw, ph = layout.get_pixel_size()
        if anchor == "middle":
            x -= pw / 2.0
        elif anchor == "end":
            x -= pw
        cr.move_to(x, y)
        cr.set_source_rgba(*_rgba(color, alpha))
        PangoCairo.show_layout(cr, layout)
        return pw, ph

    def _paint(self, cr, width: int, height: int) -> None:
        s = self._ui_scale
        accent, secondary, tertiary = core.theme_colors(self.theme)
        field = core.theme_field(self.theme)
        ink = core.theme_ink(self.theme)
        dark_ui = _luma(field) < 0.55
        glass_ink = (245, 248, 252) if dark_ui else (20, 24, 30)
        # Labels need contrast even when a theme's accent is black.
        accent = _ink_accent(accent, dark_ui)
        secondary = _ink_accent(secondary, dark_ui)
        tertiary = _ink_accent(tertiary, dark_ui)

        # Window field + theme glow. The GTK window opacity lets the bonded
        # visualizer show through this paint.
        cr.set_source_rgba(*_rgba(field, 0.94))
        cr.paint()
        try:
            import cairo as _cairo
            glow = _cairo.RadialGradient(width * 0.28, height * 0.18, 10, width * 0.28, height * 0.18, width * 0.7)
            glow.add_color_stop_rgba(0.0, *_rgba(accent, 0.30))
            glow.add_color_stop_rgba(0.55, *_rgba(secondary, 0.10))
            glow.add_color_stop_rgba(1.0, *_rgba(field, 0.0))
            cr.set_source(glow)
            cr.paint()
        except Exception:
            pass
        try:
            import cairo as _cairo
            cool = _cairo.RadialGradient(width * 0.82, height * 0.92, 10, width * 0.82, height * 0.92, width * 0.6)
            cool.add_color_stop_rgba(0.0, *_rgba(tertiary, 0.22))
            cool.add_color_stop_rgba(1.0, *_rgba(field, 0.0))
            cr.set_source(cool)
            cr.paint()
        except Exception:
            pass

        self._paint_top_bar(cr, s, accent, secondary, ink, glass_ink)
        self._paint_deck(cr, "A", s, accent, glass_ink, dark_ui)
        self._paint_deck(cr, "B", s, secondary, glass_ink, dark_ui)
        self._paint_pitch(cr, s, accent, secondary, glass_ink)
        self._paint_mixer(cr, s, accent, secondary, tertiary, glass_ink, dark_ui)
        self._paint_bottom_bar(cr, s, accent, ink, glass_ink, dark_ui)

    def _rounded(self, cr, x, y, w, h, r):
        import math as _math
        r = max(0.0, min(r, w / 2.0, h / 2.0))
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -_math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, _math.pi / 2)
        cr.arc(x + r, y + h - r, r, _math.pi / 2, _math.pi)
        cr.arc(x + r, y + r, r, _math.pi, 3 * _math.pi / 2)
        cr.close_path()

    def _paint_top_bar(self, cr, s, accent, secondary, ink, glass_ink) -> None:
        box = self.boxes["top_bar"]
        cr.set_source_rgba(*_rgba(_darken(core.theme_field(self.theme), 0.55), 0.72))
        cr.rectangle(*box)
        cr.fill()
        cr.set_source_rgba(*_rgba(accent, 0.65))
        cr.rectangle(box[0], box[1] + box[3] - max(1.0, 1.2 * s), box[2], max(1.0, 1.2 * s))
        cr.fill()
        self._text(cr, box[0] + 28 * s, box[1] + 18 * s, "MP LIQUID MIXER", 21 * s, glass_ink, 0.96, bold=True)
        self._text(cr, box[0] + 320 * s, box[1] + 25 * s, f"v{core.VERSION}", 12 * s, glass_ink, 0.55)
        self._text(cr, box[0] + 960 * s, box[1] + 22 * s, core.theme_label(self.theme), 14 * s, accent, 0.95, bold=True, anchor="middle")

        # S4 fader status sits just left of the visualizer button.
        s4_text, s4_color = self._s4_status()
        vx = self.boxes["visualizer_button"]
        self._text(cr, vx[0] - 18 * s, box[1] + 23 * s, s4_text, 10.5 * s,
                   s4_color, 0.94, bold=True, anchor="end")

        # Visualizer button.
        active = self._viz_owned and self._viz_visible
        cr.set_source_rgba(*_rgba(accent if active else _darken(core.theme_field(self.theme), 0.35), 0.30 if active else 0.35))
        self._rounded(cr, vx[0], vx[1], vx[2], vx[3], 9 * s)
        cr.fill()
        cr.set_source_rgba(*_rgba(accent if active else glass_ink, 0.80))
        cr.set_line_width(max(1.0, 1.2 * s))
        self._rounded(cr, vx[0], vx[1], vx[2], vx[3], 9 * s)
        cr.stroke()
        self._text(cr, vx[0] + vx[2] / 2, vx[1] + 9 * s,
                   f"VISUALIZER  {'ON' if active else 'OFF'}", 12 * s, glass_ink, 0.92, bold=True, anchor="middle")

        # Close button.
        cx = self.boxes["close_button"]
        cr.set_source_rgba(*_rgba(glass_ink, 0.10))
        self._rounded(cr, cx[0], cx[1], cx[2], cx[3], 9 * s)
        cr.fill()
        cr.set_source_rgba(*_rgba(glass_ink, 0.75))
        cr.set_line_width(max(1.4, 1.6 * s))
        pad = 14 * s
        cr.move_to(cx[0] + pad, cx[1] + pad)
        cr.line_to(cx[0] + cx[2] - pad, cx[1] + cx[3] - pad)
        cr.move_to(cx[0] + cx[2] - pad, cx[1] + pad)
        cr.line_to(cx[0] + pad, cx[1] + cx[3] - pad)
        cr.stroke()

    def _paint_deck(self, cr, channel: str, s, accent, glass_ink, dark_ui) -> None:
        key = channel.lower()
        card = self.boxes[f"deck_{key}"]
        header = self.boxes[f"deck_{key}_header"]
        panel = _light(_darken(core.theme_field(self.theme), 0.25), (255, 255, 255), 0.08) if dark_ui else _light(core.theme_field(self.theme), (255, 255, 255), 0.45)
        cr.set_source_rgba(*_rgba(panel, 0.38))
        self._rounded(cr, card[0], card[1], card[2], card[3], 18 * s)
        cr.fill()
        cr.set_source_rgba(*_rgba(accent, 0.55))
        cr.set_line_width(max(1.0, 1.2 * s))
        self._rounded(cr, card[0], card[1], card[2], card[3], 18 * s)
        cr.stroke()

        cr.set_source_rgba(*_rgba(_darken(core.theme_field(self.theme), 0.45), 0.52))
        self._rounded(cr, header[0], header[1], header[2], header[3], 18 * s)
        cr.fill()

        status = self._deck_status.get(channel) or {}
        online = bool(self.decks.get(channel, {}).get("online"))
        playing = str(status.get("state") or "").lower() == "playing"
        led = (80, 230, 140) if playing else ((250, 205, 90) if online else (150, 160, 168))
        cr.set_source_rgba(*_rgba(led, 0.95))
        cr.arc(header[0] + 26 * s, header[1] + header[3] / 2, 5.5 * s, 0, 6.2832)
        cr.fill()
        self._text(cr, header[0] + 42 * s, header[1] + 11 * s, f"DECK {channel}", 14 * s, glass_ink, 0.95, bold=True)
        sync_box = self.boxes[f"sync_{key}"]
        title = status.get("name") or status.get("title") or ("Engine starting..." if online else "Waiting for engine...")
        self._text(cr, header[0] + 140 * s, header[1] + 12 * s, str(title), 11.5 * s, glass_ink, 0.72,
                   max_width=max(40.0, sync_box[0] - (header[0] + 140 * s) - 10 * s))
        right = "LIVE" if playing else ("READY" if online else "OFFLINE")
        self._text(cr, header[0] + header[2] - 18 * s, header[1] + 12 * s, right, 10.5 * s, led, 0.92,
                   bold=True, anchor="end")

        master = self.sync_master == channel
        # Deck B is drawn with its secondary hue passed as `accent`.
        sync_color = accent
        cr.set_source_rgba(*_rgba(sync_color, 0.34 if master else 0.10))
        self._rounded(cr, sync_box[0], sync_box[1], sync_box[2], sync_box[3], 7 * s)
        cr.fill()
        cr.set_source_rgba(*_rgba(sync_color, 0.90 if master else 0.55))
        cr.set_line_width(max(1.0, 1.1 * s))
        self._rounded(cr, sync_box[0], sync_box[1], sync_box[2], sync_box[3], 7 * s)
        cr.stroke()
        self._text(cr, sync_box[0] + sync_box[2] / 2, sync_box[1] + 8 * s,
                   "MASTER" if master else "SYNC", 9 * s, glass_ink,
                   0.98 if master else 0.85, bold=True, anchor="middle")

    def _paint_mixer(self, cr, s, accent, secondary, tertiary, glass_ink, dark_ui) -> None:
        panel = self.boxes["mixer"]
        base = _darken(core.theme_field(self.theme), 0.55) if dark_ui else _light(core.theme_field(self.theme), (255, 255, 255), 0.55)
        cr.set_source_rgba(*_rgba(base, 0.68))
        self._rounded(cr, panel[0], panel[1], panel[2], panel[3], 20 * s)
        cr.fill()
        cr.set_source_rgba(*_rgba(glass_ink, 0.26))
        cr.set_line_width(max(1.0, 1.1 * s))
        self._rounded(cr, panel[0], panel[1], panel[2], panel[3], 20 * s)
        cr.stroke()

        self._text(cr, panel[0] + panel[2] / 2, panel[1] + 18 * s, "MIXER", 15 * s, glass_ink, 0.85, bold=True, anchor="middle")
        self._text(cr, panel[0] + panel[2] / 2, panel[1] + 40 * s, self.model.mode, 10 * s,
                   accent if not self.model.external else (250, 205, 90), 0.95, bold=True, anchor="middle")

        self._paint_knobs(cr, s, accent, secondary, glass_ink)
        self._paint_strip(cr, "A", s, accent, secondary, glass_ink)
        self._paint_strip(cr, "B", s, accent, secondary, glass_ink)
        self._paint_strip(cr, "C", s, accent, secondary, glass_ink)
        self._paint_strip(cr, "D", s, accent, secondary, glass_ink)
        self._paint_xfader(cr, s, accent, secondary, glass_ink)
        self._paint_browse_button(cr, s, accent, secondary, glass_ink)
        self._paint_mode_button(cr, s, accent, glass_ink)
        self._paint_record_button(cr, s, accent, secondary, glass_ink)

    KNOB_ROWS = (
        ("gain", "GAIN", 0.0, core.TRIM_MAX, core.DEFAULT_TRIM),
        ("high", "TREBLE", core.EQ_MIN, core.EQ_MAX, 0.0),
        ("mid", "MID", core.EQ_MIN, core.EQ_MAX, 0.0),
        ("low", "LOW", core.EQ_MIN, core.EQ_MAX, 0.0),
    )

    def _knob_value(self, group: str, channel: str) -> float:
        return self.model.trim(channel) if group == "gain" else self.model.eq_value(channel, group)

    def _knob_range(self, group: str):
        for key, _label, low, high, _default in self.KNOB_ROWS:
            if key == group:
                return low, high
        return 0.0, 1.0

    def _paint_pitch(self, cr, s, accent, secondary, glass_ink) -> None:
        import math as _math
        for channel in ("A", "B"):
            box = self.boxes[f"pitch_{channel.lower()}"]
            hue = accent if channel == "A" else secondary
            cr.set_source_rgba(*_rgba(_darken(core.theme_field(self.theme), 0.58), 0.55))
            self._rounded(cr, box[0], box[1], box[2], box[3], 10 * s)
            cr.fill()
            cr.set_source_rgba(*_rgba(hue, 0.55))
            cr.set_line_width(max(1.0, s))
            self._rounded(cr, box[0], box[1], box[2], box[3], 10 * s)
            cr.stroke()
            self._text(cr, box[0] + box[2] / 2, box[1] + 4 * s, "PITCH", 7 * s, glass_ink, 0.55,
                       bold=True, anchor="middle")
            top = box[1] + 32 * s
            bottom = box[1] + box[3] - 32 * s
            cx = box[0] + box[2] / 2
            cr.set_source_rgba(*_rgba((8, 10, 14), 0.50))
            cr.set_line_width(max(4.0, 5.0 * s))
            cr.set_line_cap(1)
            cr.move_to(cx, top)
            cr.line_to(cx, bottom)
            cr.stroke()
            centre = (top + bottom) / 2.0
            cr.set_source_rgba(*_rgba(glass_ink, 0.40))
            cr.set_line_width(max(1.0, s))
            cr.move_to(cx - 6 * s, centre)
            cr.line_to(cx + 6 * s, centre)
            cr.stroke()
            pitch = self.model.pitch(channel)
            t = (core.PITCH_MAX - pitch) / (core.PITCH_MAX - core.PITCH_MIN)
            thumb_y = top + max(0.0, min(1.0, t)) * (bottom - top)
            cr.set_source_rgba(*_rgba(_light(hue, (255, 255, 255), 0.25), 0.98))
            self._rounded(cr, cx - 9 * s, thumb_y - 6 * s, 18 * s, 12 * s, 4 * s)
            cr.fill()
            cr.set_source_rgba(*_rgba((10, 12, 16), 0.60))
            cr.set_line_width(max(0.8, 0.9 * s))
            self._rounded(cr, cx - 9 * s, thumb_y - 6 * s, 18 * s, 12 * s, 4 * s)
            cr.stroke()
            self._text(cr, cx, top - 12 * s, "+20", 6.5 * s, glass_ink, 0.55, anchor="middle")
            self._text(cr, cx, centre - 3 * s, "0", 6.5 * s, glass_ink, 0.55, anchor="middle")
            self._text(cr, cx, bottom + 2 * s, "-20", 6.5 * s, glass_ink, 0.55, anchor="middle")
            self._text(cr, cx, box[1] + box[3] + 2 * s, f"{pitch:+.1f}%", 7.5 * s,
                       hue, 0.90, bold=True, anchor="middle")

    def _paint_knobs(self, cr, s, accent, secondary, glass_ink) -> None:
        import math as _math
        for group, label, low, high, _default in self.KNOB_ROWS:
            label_box = self.boxes[f"knob_{group}_label"]
            self._text(cr, label_box[0] + label_box[2], label_box[1] + 3 * s, label,
                       8.6 * s, glass_ink, 0.75, bold=True, anchor="end")
            for channel in core.CHANNELS:
                box = self.boxes[f"knob_{group}_{channel}"]
                cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
                radius = max(11.0, 16.0 * s)
                hue = accent if channel == "A" else (secondary if channel == "B" else (140, 148, 156))
                active = channel in ("A", "B")
                value = self._knob_value(group, channel)
                t = (value - low) / max(1e-6, high - low)
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                start = _math.radians(135.0)
                end = start + _math.radians(270.0) * t
                cr.set_line_cap(1)
                cr.set_line_width(max(3.0, 3.2 * s))
                cr.set_source_rgba(*_rgba(glass_ink, 0.16))
                cr.arc(cx, cy, radius, _math.radians(135.0), _math.radians(405.0))
                cr.stroke()
                cr.set_source_rgba(*_rgba(hue, 0.92 if active else 0.35))
                cr.arc(cx, cy, radius, start, end)
                cr.stroke()
                cr.set_source_rgba(*_rgba(_darken(core.theme_field(self.theme), 0.45), 0.58))
                cr.arc(cx, cy, radius * 0.60, 0, 2 * _math.pi)
                cr.fill()
                cr.set_source_rgba(*_rgba(glass_ink, 0.35))
                cr.set_line_width(max(0.8, s))
                cr.arc(cx, cy, radius * 0.60, 0, 2 * _math.pi)
                cr.stroke()
                cr.set_source_rgba(*_rgba(hue, 0.95 if active else 0.40))
                cr.set_line_width(max(1.4, 1.6 * s))
                cr.move_to(cx, cy)
                cr.line_to(cx + _math.cos(end) * radius * 0.55, cy + _math.sin(end) * radius * 0.55)
                cr.stroke()
                value_box = self.boxes[f"knob_{group}_{channel}_value"]
                if group == "gain":
                    text = f"{int(round(value * 100))}"
                else:
                    text = f"{value:+.0f}" if abs(value) >= 0.5 else "0"
                self._text(cr, value_box[0] + value_box[2] / 2, value_box[1], text,
                           8.8 * s, glass_ink, 0.85 if active else 0.45, anchor="middle")

    def _paint_record_button(self, cr, s, accent, secondary, glass_ink) -> None:
        box = self.boxes["record_button"]
        recording = self.recorder.is_recording
        if recording:
            color = (235, 64, 72)
            label = f"STOP   {self.recorder.elapsed_label()}"
        elif self.recorder.error:
            color = (250, 205, 90)
            label = "REC UNAVAILABLE"
        else:
            color = _mix(accent, secondary, 0.5)
            label = "REC"
        cr.set_source_rgba(*_rgba(color, 0.24 if recording else 0.16))
        self._rounded(cr, box[0], box[1], box[2], box[3], 9 * s)
        cr.fill()
        cr.set_source_rgba(*_rgba(color, 0.90))
        cr.set_line_width(max(1.0, 1.2 * s))
        self._rounded(cr, box[0], box[1], box[2], box[3], 9 * s)
        cr.stroke()
        label_width, _height = self._text(cr, box[0] + box[2] / 2, box[1] + 13 * s, label, 10.5 * s,
                                          glass_ink, 0.95, bold=True, anchor="middle")
        cr.set_source_rgba(*_rgba(color, 0.98))
        cr.arc(box[0] + box[2] / 2 - label_width / 2 - 10 * s, box[1] + 21 * s, 4 * s, 0, 6.2832)
        cr.fill()
        self._text(cr, box[0] + box[2] / 2, box[1] + box[3] + 6 * s,
                   "master output -> WAV" if not recording else self._short_recording_name(),
                   7.6 * s, glass_ink, 0.50, anchor="middle")

    def _short_recording_name(self) -> str:
        path = self.recorder.path
        return path.name if path is not None else "recording"

    def _paint_strip(self, cr, channel: str, s, accent, secondary, glass_ink) -> None:
        box = self.boxes[f"strip_{channel}"]
        active = self.model.channel_active(channel)
        hue = accent if channel == "A" else (secondary if channel == "B" else (140, 148, 156))
        gain = self.model.channel_gain(channel)
        label_box = self.boxes[f"strip_{channel}_label"]
        value_box = self.boxes[f"strip_{channel}_value"]

        self._text(cr, label_box[0] + label_box[2] / 2, label_box[1], channel, 20 * s,
                   hue if active else (120, 128, 136), 0.98 if active else 0.55, bold=True, anchor="middle")

        cx = box[0] + box[2] / 2
        track_w = max(5.0, 7.0 * s)
        thumb_h = max(12.0, 15.0 * s)
        thumb_w = max(30.0, 38.0 * s)
        cr.set_source_rgba(*_rgba((8, 10, 14), 0.42))
        cr.set_line_width(track_w)
        cr.set_line_cap(1)  # round
        cr.move_to(cx, box[1] + thumb_h / 2)
        cr.line_to(cx, box[1] + box[3] - thumb_h / 2)
        cr.stroke()

        if active:
            thumb_y = core.y_from_fader_value(gain, (box[0], box[1] + thumb_h / 2, box[2], box[3] - thumb_h))
            cr.set_source_rgba(*_rgba(hue, 0.75))
            cr.set_line_width(track_w)
            cr.move_to(cx, thumb_y)
            cr.line_to(cx, box[1] + box[3] - thumb_h / 2)
            cr.stroke()

            # Tick marks.
            cr.set_source_rgba(*_rgba(glass_ink, 0.20))
            cr.set_line_width(max(1.0, s))
            for step in range(1, 10):
                ty = box[1] + thumb_h / 2 + (box[3] - thumb_h) * step / 10.0
                cr.move_to(cx + 14 * s, ty)
                cr.line_to(cx + 22 * s, ty)
                cr.stroke()

            cr.set_source_rgba(*_rgba(_light(hue, (255, 255, 255), 0.25), 0.98))
            self._rounded(cr, cx - thumb_w / 2, thumb_y - thumb_h / 2, thumb_w, thumb_h, 4 * s)
            cr.fill()
            cr.set_source_rgba(*_rgba((8, 10, 14), 0.65))
            cr.set_line_width(max(0.8, 0.9 * s))
            self._rounded(cr, cx - thumb_w / 2, thumb_y - thumb_h / 2, thumb_w, thumb_h, 4 * s)
            cr.stroke()
            self._text(cr, value_box[0] + value_box[2] / 2, value_box[1], f"{int(round(gain * 100))}%",
                       10.5 * s, glass_ink, 0.78, anchor="middle")
        else:
            cr.set_source_rgba(*_rgba(glass_ink, 0.28))
            cr.set_line_width(track_w)
            cr.move_to(cx, box[1] + 18 * s)
            cr.line_to(cx, box[1] + box[3] - 18 * s)
            cr.stroke()
            self._text(cr, value_box[0] + value_box[2] / 2, value_box[1], "SOON",
                       9.5 * s, (150, 158, 166), 0.75, bold=True, anchor="middle")

    def _paint_xfader(self, cr, s, accent, secondary, glass_ink) -> None:
        box = self.boxes["xfader"]
        cy = box[1] + box[3] / 2
        cr.set_source_rgba(*_rgba((8, 10, 14), 0.45))
        cr.set_line_width(max(6.0, 8.0 * s))
        cr.set_line_cap(1)
        cr.move_to(box[0] + 18 * s, cy)
        cr.line_to(box[0] + box[2] - 18 * s, cy)
        cr.stroke()
        try:
            import cairo as _cairo
            grad = _cairo.LinearGradient(box[0] + 18 * s, cy, box[0] + box[2] - 18 * s, cy)
            grad.add_color_stop_rgba(0.0, *_rgba(accent, 0.85))
            grad.add_color_stop_rgba(0.5, *_rgba(_mix(accent, secondary, 0.5), 0.85))
            grad.add_color_stop_rgba(1.0, *_rgba(secondary, 0.85))
            cr.set_source(grad)
            cr.set_line_width(max(5.0, 6.0 * s))
            cr.move_to(box[0] + 18 * s, cy)
            cr.line_to(box[0] + box[2] - 18 * s, cy)
            cr.stroke()
        except Exception:
            pass

        thumb_x = box[0] + 18 * s + self.model.crossfade * (box[2] - 36 * s)
        cr.set_source_rgba(*_rgba((245, 248, 252), 0.98))
        self._rounded(cr, thumb_x - 7 * s, cy - 20 * s, 14 * s, 40 * s, 5 * s)
        cr.fill()
        cr.set_source_rgba(*_rgba((10, 12, 16), 0.55))
        cr.set_line_width(max(0.9, s))
        self._rounded(cr, thumb_x - 7 * s, cy - 20 * s, 14 * s, 40 * s, 5 * s)
        cr.stroke()

        self._text(cr, box[0] + 4 * s, cy + 5 * s, "A", 12 * s, accent, 0.95, bold=True)
        self._text(cr, box[0] + box[2] - 4 * s, cy + 5 * s, "B", 12 * s, secondary, 0.95, bold=True, anchor="end")
        self._text(cr, box[0] + box[2] / 2, box[1] + box[3] - 2 * s,
                   f"CROSSFADER {int(round(self.model.crossfade * 100))}%", 9 * s, glass_ink, 0.55, anchor="middle")

    def _paint_browse_button(self, cr, s, accent, secondary, glass_ink) -> None:
        box = self.boxes["browse_button"]
        color = accent if self.browse_target == "A" else secondary
        cr.set_source_rgba(*_rgba(color, 0.20))
        self._rounded(cr, box[0], box[1], box[2], box[3], 7 * s)
        cr.fill()
        cr.set_source_rgba(*_rgba(color, 0.80))
        cr.set_line_width(max(1.0, 1.1 * s))
        self._rounded(cr, box[0], box[1], box[2], box[3], 7 * s)
        cr.stroke()
        self._text(cr, box[0] + box[2] / 2, box[1] + 8 * s,
                   f"BROWSE  {self.browse_target}", 9.5 * s, glass_ink, 0.92, bold=True, anchor="middle")

    def _paint_mode_button(self, cr, s, accent, glass_ink) -> None:
        box = self.boxes["mode_button"]
        color = (250, 205, 90) if self.model.external else accent
        cr.set_source_rgba(*_rgba(color, 0.22))
        self._rounded(cr, box[0], box[1], box[2], box[3], 8 * s)
        cr.fill()
        cr.set_source_rgba(*_rgba(color, 0.85))
        cr.set_line_width(max(1.0, 1.2 * s))
        self._rounded(cr, box[0], box[1], box[2], box[3], 8 * s)
        cr.stroke()
        self._text(cr, box[0] + box[2] / 2, box[1] + 12 * s, self.model.mode, 10 * s, glass_ink, 0.95, bold=True, anchor="middle")
        self._text(cr, box[0] + box[2] / 2, box[1] + 26 * s,
                   "unity / external mixer" if self.model.external else "software faders live",
                   7.6 * s, glass_ink, 0.55, anchor="middle")

    def _paint_bottom_bar(self, cr, s, accent, ink, glass_ink, dark_ui) -> None:
        box = self.boxes["bottom_bar"]
        cr.set_source_rgba(*_rgba(_darken(core.theme_field(self.theme), 0.55), 0.72))
        cr.rectangle(*box)
        cr.fill()
        cr.set_source_rgba(*_rgba(accent, 0.45))
        cr.rectangle(box[0], box[1], box[2], max(1.0, 1.0 * s))
        cr.fill()

        track = self.boxes["opacity"]
        cy = track[1] + track[3] / 2
        cr.set_source_rgba(*_rgba(glass_ink, 0.22))
        cr.set_line_width(max(5.0, 6.0 * s))
        cr.set_line_cap(1)
        cr.move_to(track[0], cy)
        cr.line_to(track[0] + track[2], cy)
        cr.stroke()
        thumb_x = core.x_from_opacity(self.model.opacity, track)
        cr.set_source_rgba(*_rgba(accent, 0.85))
        cr.set_line_width(max(5.0, 6.0 * s))
        cr.move_to(track[0], cy)
        cr.line_to(thumb_x, cy)
        cr.stroke()
        cr.set_source_rgba(*_rgba((245, 248, 252), 0.98))
        cr.arc(thumb_x, cy, max(7.0, 8.0 * s), 0, 6.2832)
        cr.fill()
        self._text(cr, track[0] - 16 * s, cy - 7 * s, "TRANSPARENCY", 10 * s, glass_ink, 0.60, anchor="end", bold=True)
        self._text(cr, track[0] + track[2] + 16 * s, cy - 7 * s, f"{int(round(self.model.opacity * 100))}%",
                   10 * s, glass_ink, 0.75, bold=True)

        if not self.start_fullscreen and self.area is not None:
            width, height = self.area.get_allocated_width(), self.area.get_allocated_height()
            cr.set_source_rgba(*_rgba(glass_ink, 0.45))
            cr.set_line_width(max(1.0, 1.4 * s))
            for step in range(3):
                cr.move_to(width - 8 * s - step * 6 * s, height - 4 * s)
                cr.line_to(width - 4 * s, height - 8 * s - step * 6 * s)
                cr.stroke()

        hints = "C  THEME    V  VISUALIZER    X  MIX MODE    DBL-CLICK TOP / F11  ESC  QUIT"
        self._text(cr, box[0] + box[2] - 28 * s, box[1] + 5 * s, hints, 8.5 * s, glass_ink, 0.55, anchor="end")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="MP Liquid Mixer 0.0.1 - four channels, two players, one glass window")
    parser.add_argument("--version", "-v", action="version", version=f"MP Liquid Mixer {core.VERSION}")
    parser.add_argument("--windowed", action="store_true", help="1600x900 window instead of fullscreen (development)")
    parser.add_argument("--no-visualizer", action="store_true", help="Do not start the visualizer behind the mixer")
    parser.add_argument("--theme", default=None, help="Theme id or label (default: the shared theme file)")
    parser.add_argument("--font", default=None, help="Terminal font (default: the Liquid Glass font)")
    args = parser.parse_args(argv)

    if not os.environ.get("DISPLAY") and not sys.platform.startswith("win"):
        print("MP Liquid Mixer needs an X11 display (the Liquid Glass host runs under XWayland).", file=sys.stderr)
        return 2
    lock = core.acquire_mixer_lock()
    if lock is None:
        print("MP Liquid Mixer is already open. One mixer owns the deck engines.", file=sys.stderr)
        return 1
    mixer = LiquidMixer(
        theme=args.theme,
        fullscreen=not args.windowed,
        with_visualizer=not args.no_visualizer,
        font=args.font,
    )
    return mixer.run()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
