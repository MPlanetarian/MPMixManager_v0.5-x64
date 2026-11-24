"""The glass-window transparency slider, drawn as a GTK overlay.

The OpenGL host drew this bar inside its own GL frame, so only that backend
had it. The Vulkan host draws the themed pane through its own pipeline, and had
no bar at all, so MP Liquid Player lost the slider once Vulkan became the
default backend.

This overlay is a thin borderless GTK window pinned to the strip the terminal
well reserves at the bottom of the player. It draws the same track and thumb,
follows the active theme colour, and reports drag input, so both hosts share
one implementation and one visual result regardless of the GPU path.

The bar is hover-only: it stays transparent until the pointer enters the
reserved strip, then fades in, and fades out again when the pointer leaves.
The overlay itself remains mapped at alpha 0 so it can still receive the
enter event that brings the bar back.

The overlay is a *managed* toplevel, never a GTK popup window. A popup is
created override-redirect, so the window manager never restacks it: the bar
stayed at the top of the X stack for its whole life and kept floating over any
other application the user switched to. A managed toplevel made transient for
the player is restacked with the player instead.
"""
from __future__ import annotations

import sys
import time

DEFAULT_OPACITY = 0.70
# The bar now spans the full useful range: 10 % (almost transparent) to
# 100 % (fully opaque). The old 20 %–80 % window was too constricted to read
# as a transparency control.
MIN_OPACITY = 0.10
MAX_OPACITY = 1.00

# Height of the strip the terminal well keeps free at the bottom of the window.
SLIDER_STRIP = 44
# Horizontal inset of the bar from the window edges.
SLIDER_LEFT = 40
SLIDER_RIGHT_PAD = 40
SLIDER_HEIGHT = 8

# Smallest strip the bar still fits in: the thumb is SLIDER_HEIGHT + 6 tall.
MIN_SLIDER_STRIP = SLIDER_HEIGHT + 6

# The bar is hover-only and fades in and out. The same timing is used by the
# OpenGL host, which draws its own bar, so both backends feel identical.
SLIDER_FADE_IN_SECONDS = 0.20
SLIDER_FADE_OUT_SECONDS = 0.35
SLIDER_FRAME_MS = 16
# Extra horizontal room around the track that still counts as the bar area.
SLIDER_HOVER_PAD = 16


def player_slider_strip(cell_height: float = 0.0) -> int:
    """Strip under the *player*, one text row shorter than ``SLIDER_STRIP``.

    The player's last two text rows are the status line and the key hints.
    At the classic 44 px they filled the terminal but still stopped a whole
    text row above the glass edge, which read as an empty line between the
    hints and the transparency bar. Handing one text row back to the terminal
    puts those two lines one line lower, directly above the bar. The window
    keeps the size it publishes: the row comes out of the strip, not the
    frame. MP Gui and the tracklist viewer keep the full strip.
    """
    try:
        ch = int(round(float(cell_height)))
    except (TypeError, ValueError):
        ch = 0
    if ch <= 0:
        return SLIDER_STRIP
    return max(MIN_SLIDER_STRIP, SLIDER_STRIP - ch)


def opacity_from_x(x: float, width: float) -> float:
    """Map a pointer x inside the window to a glass opacity in [0.10, 1.00]."""
    left = float(SLIDER_LEFT)
    right = max(float(SLIDER_LEFT) + 40.0, float(width) - float(SLIDER_RIGHT_PAD))
    t = (float(x) - left) / (right - left)
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return MIN_OPACITY + t * (MAX_OPACITY - MIN_OPACITY)


def slider_thumb_x(opacity: float, width: float) -> float:
    """Pointer x for a given opacity, the inverse of ``opacity_from_x``."""
    left = float(SLIDER_LEFT)
    right = max(float(SLIDER_LEFT) + 40.0, float(width) - float(SLIDER_RIGHT_PAD))
    span = max(1e-6, MAX_OPACITY - MIN_OPACITY)
    t = (float(opacity) - MIN_OPACITY) / span
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return left + t * (right - left)


def published_window_opacity(base: float, factor: float = 1.0) -> float:
    """Window opacity for a slider value and a theme's oscillation factor.

    The right end of the bar is fully opaque. A dynamic theme breathes the
    pane by a few percent; that breath must not open the glass again once
    the slider is parked at that end.
    """
    base = float(base)
    factor = float(factor)
    if base >= 0.999:
        return 1.0
    return base * factor


def bezel_premultiplied(premultiplied: bool, opacity: float) -> bool:
    """Whether the shader keeps its translucent glass body.

    On a premultiplied swapchain the body is about half opaque, and the
    window opacity is applied on top of that. Leaving the body on at the
    right end of the bar still shows the desktop. The opaque fragment path
    is what makes that end zero transparency.
    """
    try:
        if float(opacity) >= 0.999:
            return False
    except (TypeError, ValueError):
        return bool(premultiplied)
    return bool(premultiplied)


class GlassSlider:
    """A GTK overlay that draws the transparency bar for a glass window.

    The overlay is a separate borderless window, so it appears above both the
    GPU-drawn glass and the VTE terminal on either backend. ``place()`` keeps it
    on the reserved strip, ``set_theme()`` recolours it, and a press or drag on
    it reports a new opacity through ``on_change``. The bar is hidden until the
    pointer enters that strip, then fades in; leaving fades it out again.

    The window must be *managed* by the window manager, not a
    ``Gtk.WindowType.POPUP``. A GTK popup is created override-redirect, which
    the window manager never restacks: it stays at the very top of the X stack
    for its whole life, so after the user switched to any other application
    the transparency bar was still floating over that application. A managed
    toplevel made transient for the player is restacked with the player, so it
    is visible over the glass and disappears behind anything else that is
    focused.
    """

    def __init__(self, opacity: float = DEFAULT_OPACITY, on_change=None):
        self.opacity = max(MIN_OPACITY, min(MAX_OPACITY, float(opacity)))
        self._on_change = on_change
        self._win = None
        self._area = None
        self._Gdk = None
        self._theme = ((0, 229, 255), (247, 37, 133), (102, 102, 255))
        self._dragging = False
        self._placed = None
        # Hover-only fade state. The overlay window stays mapped at alpha 0 so
        # it can still receive the enter event that brings the bar back.
        self._alpha = 0.0
        self._alpha_target = 0.0
        self._fade_from = 0.0
        self._fade_started = 0.0
        self._fade_source = 0
        self._fade_glib = None

    # -- lifecycle --------------------------------------------------------
    def attach(self, Gtk, Gdk, parent=None) -> bool:
        """Create the overlay window. Returns False when GTK is unavailable.

        ``parent`` should be the player's terminal window. The overlay is a
        decorated-by-us-but-borderless *managed* toplevel made transient for
        that parent, so the window manager stacks it with the player. A
        ``Gtk.WindowType.POPUP`` would be override-redirect and would stay
        above every other application after a window switch.
        """
        if Gtk is None or Gdk is None:
            return False
        self._placed = None
        try:
            win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            win.set_decorated(False)
            win.set_skip_taskbar_hint(True)
            win.set_skip_pager_hint(True)
            win.set_accept_focus(False)
            win.set_focus_on_map(False)
            if parent is not None:
                try:
                    win.set_transient_for(parent)
                except Exception:
                    pass
            try:
                win.set_type_hint(Gdk.WindowTypeHint.UTILITY)
            except Exception:
                pass
            try:
                # The bar takes drags, but it must never steal keyboard focus
                # from the terminal it belongs to.
                win.set_modal(False)
            except Exception:
                pass
            # Without an RGBA visual and a paintable window the overlay paints
            # an opaque default background, which is what showed as a black bar
            # under the player instead of clear glass.
            try:
                win.set_app_paintable(True)
                screen = win.get_screen()
                visual = screen.get_rgba_visual() if screen is not None else None
                if visual is not None:
                    win.set_visual(visual)
            except Exception:
                pass
            area = Gtk.DrawingArea()
            area.set_size_request(1, SLIDER_STRIP)
            masks = (
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.BUTTON_RELEASE_MASK
                | Gdk.EventMask.BUTTON1_MOTION_MASK
                | Gdk.EventMask.POINTER_MOTION_MASK
            )
            # Older Gdk versions in the suite's supported range still have
            # these; the fallback keeps a stripped-down fake Gdk usable.
            masks |= int(getattr(Gdk.EventMask, "ENTER_NOTIFY_MASK", 0))
            masks |= int(getattr(Gdk.EventMask, "LEAVE_NOTIFY_MASK", 0))
            area.add_events(masks)
            area.connect("draw", self._on_draw)
            area.connect("button-press-event", self._on_press)
            area.connect("button-release-event", self._on_release)
            area.connect("motion-notify-event", self._on_motion)
            area.connect("enter-notify-event", self._on_enter)
            area.connect("leave-notify-event", self._on_leave)
            win.add(area)
            # A managed window is mapped by the window manager at a position it
            # chooses, so showing the toplevel here would flash the bar at that
            # spot before `place()` moves it onto the player's reserved strip.
            # Realising creates the GdkWindow that `_on_draw` paints on, and
            # showing the child readies it; the toplevel itself is only mapped
            # once `place()` knows where it belongs.
            win.realize()
            area.show()
            # GTK paints the theme background on both the window and the drawing
            # area. With an RGBA visual that becomes opaque black, which is the
            # black strip that was showing under the player. Clear the CSS
            # background on both so only our bar is painted.
            try:
                provider = Gtk.CssProvider()
                provider.load_from_data(b"window, drawingarea { background-color: transparent; }")
                for widget in (win, area):
                    Gtk.StyleContext.add_provider(
                        widget.get_style_context(),
                        provider,
                        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                    )
                self._css = provider
            except Exception:
                pass
            self._win, self._area, self._Gdk = win, area, Gdk
            return True
        except Exception as exc:
            print(f"MP slider overlay unavailable: {exc}", file=sys.stderr)
            self._win = self._area = None
            return False

    def detach(self) -> None:
        self._stop_fade()
        try:
            if self._win is not None:
                self._win.destroy()
        except Exception:
            pass
        self._win = self._area = None
        self._placed = None
        self._alpha = self._alpha_target = 0.0

    @property
    def available(self) -> bool:
        return self._win is not None

    # -- appearance -------------------------------------------------------
    def set_theme(self, colors) -> None:
        """Recolour the bar from the active theme palette."""
        try:
            self._theme = tuple(tuple(int(c) for c in col) for col in colors[:3])
        except Exception:
            return
        self._redraw()

    def place(self, x: int, y: int, width: int, height: int, strip: int = 0) -> None:
        """Pin the overlay to the reserved strip at the bottom of ``height``.

        ``strip`` is that strip's height. The player passes the shorter strip
        it handed one row of back to the terminal, so the bar stays directly
        under the moved footer; MP Gui and the tracklist viewer omit it and
        keep the classic ``SLIDER_STRIP``.

        A managed window's move and resize are window-manager requests, not
        bare X calls, so repeating them at 60 fps would flood the compositor
        with configure requests. The geometry is therefore only submitted when
        it actually changes.
        """
        if self._win is None:
            return
        strip = max(MIN_SLIDER_STRIP, int(strip or SLIDER_STRIP))
        width = max(64, int(width))
        strip_y = int(y) + max(0, int(height) - strip)
        geometry = (int(x), strip_y, width, strip)
        if geometry == getattr(self, "_placed", None):
            return
        self._placed = geometry
        try:
            self._win.move(int(x), strip_y)
            self._win.resize(width, strip)
            self._area.set_size_request(width, strip)
            # Map the window only now that it has a real position, so it never
            # appears wherever the window manager would have put it first.
            self._win.show()
        except Exception:
            pass

    # -- drawing ----------------------------------------------------------
    def _on_draw(self, area, cr):
        width = max(1, area.get_allocated_width())
        height = max(1, area.get_allocated_height())

        # Clear to fully transparent first. The overlay sits on top of the
        # player's glass, so anything it paints becomes part of that look; an
        # opaque fill is what made the bar read as a black strip.
        cr.set_operator(1)  # cairo.OPERATOR_SOURCE
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(2)  # cairo.OPERATOR_OVER

        fade = max(0.0, min(1.0, self._alpha))
        if fade <= 0.001:
            return False

        track = self._theme[0] if self._theme else (255, 255, 255)
        accent = self._theme[1] if len(self._theme) > 1 else track

        left = float(SLIDER_LEFT)
        right = max(left + 40.0, float(width) - float(SLIDER_RIGHT_PAD))
        bar_h = float(SLIDER_HEIGHT)
        bar_top = max(0.0, (height - bar_h) / 2.0)

        # A soft glass trough: the theme hue at low alpha, brightened towards
        # white so it reads as frosted glass on top of the pane rather than a
        # solid coloured bar.
        trough = tuple(int(c + (255 - c) * 0.55) for c in track)
        cr.set_source_rgba(trough[0] / 255.0, trough[1] / 255.0, trough[2] / 255.0,
                           0.28 * fade)
        cr.rectangle(left, bar_top, max(1.0, right - left), bar_h)
        cr.fill()

        # The filled portion, in the theme accent, still translucent.
        thumb_x = slider_thumb_x(self.opacity, width)
        if thumb_x > left:
            cr.set_source_rgba(accent[0] / 255.0, accent[1] / 255.0, accent[2] / 255.0,
                               0.45 * fade)
            cr.rectangle(left, bar_top, max(1.0, thumb_x - left), bar_h)
            cr.fill()

        # The thumb: a small bright glass knob.
        knob = tuple(int(c + (255 - c) * 0.35) for c in accent)
        cr.set_source_rgba(knob[0] / 255.0, knob[1] / 255.0, knob[2] / 255.0,
                           0.80 * fade)
        cr.rectangle(thumb_x - 5.0, bar_top - 3.0, 10.0, bar_h + 6.0)
        cr.fill()
        return False

    def _redraw(self) -> None:
        try:
            if self._area is not None:
                self._area.queue_draw()
        except Exception:
            pass

    # -- input ------------------------------------------------------------
    def set_opacity(self, opacity: float, notify: bool = True) -> None:
        new = max(MIN_OPACITY, min(MAX_OPACITY, float(opacity)))
        if abs(new - self.opacity) < 1e-4:
            return
        self.opacity = new
        self._redraw()
        if notify and self._on_change is not None:
            try:
                self._on_change(new)
            except Exception:
                pass

    def _apply_from_event(self, widget, event) -> None:
        width = max(1, widget.get_allocated_width())
        self.set_opacity(opacity_from_x(event.x, width))

    def _pointer_in_hover(self, widget, event) -> bool:
        """Whether the pointer is over the track (plus a little padding).

        The overlay is only the height of the bottom strip, so the vertical
        test is implicit. The horizontal test keeps the far ends of the strip
        - which are not part of the track - from waking the bar.
        """
        try:
            x = float(event.x)
            width = max(1, int(widget.get_allocated_width()))
        except (AttributeError, TypeError, ValueError):
            return False
        left = float(SLIDER_LEFT) - SLIDER_HOVER_PAD
        right = float(width) - float(SLIDER_RIGHT_PAD) + SLIDER_HOVER_PAD
        return left <= x <= right

    def _on_enter(self, widget, event) -> bool:
        if self._pointer_in_hover(widget, event):
            self._set_shown(True)
        return False

    def _on_leave(self, _widget, _event) -> bool:
        # While the button is held the bar stays up, even if the pointer leaves
        # the strip; a drag gesture keeps working until release.
        if not self._dragging:
            self._set_shown(False)
        return False

    def _on_press(self, widget, event) -> bool:
        if getattr(event, "button", 1) != 1:
            return False
        self._dragging = True
        self._set_shown(True)
        self._apply_from_event(widget, event)
        return True

    def _on_motion(self, widget, event) -> bool:
        self._set_shown(bool(self._dragging or self._pointer_in_hover(widget, event)))
        if not self._dragging:
            return False
        self._apply_from_event(widget, event)
        return True

    def _on_release(self, widget, event) -> bool:
        if getattr(event, "button", 1) != 1:
            return False
        self._dragging = False
        self._apply_from_event(widget, event)
        self._set_shown(self._pointer_in_hover(widget, event))
        return True

    # -- hover fade -------------------------------------------------------
    def _glib(self):
        """The GLib module for the fade clock, or ``False`` when unavailable."""
        if self._fade_glib is None:
            try:
                from gi.repository import GLib
            except Exception:
                GLib = False
            self._fade_glib = GLib
        return self._fade_glib

    def _stop_fade(self) -> None:
        if not self._fade_source:
            return
        glib = self._glib()
        if glib:
            try:
                glib.source_remove(self._fade_source)
            except Exception:
                pass
        self._fade_source = 0

    def _set_shown(self, shown: bool) -> None:
        target = 1.0 if shown else 0.0
        if target == self._alpha_target:
            return
        self._alpha_target = target
        self._fade_from = self._alpha
        self._fade_started = time.monotonic()
        if self._fade_source:
            # A fade is already running; it reads the new target next tick.
            return
        glib = self._glib()
        if glib:
            try:
                self._fade_source = glib.timeout_add(SLIDER_FRAME_MS, self._fade_tick)
                return
            except Exception:
                pass
        # No running GTK loop (unit tests, or GTK unavailable): settle at once.
        self._alpha = target
        self._redraw()

    def _fade_tick(self) -> bool:
        if self._win is None:
            self._fade_source = 0
            return False
        now = time.monotonic()
        target = self._alpha_target
        duration = (SLIDER_FADE_IN_SECONDS
                    if target >= self._fade_from
                    else SLIDER_FADE_OUT_SECONDS)
        progress = (now - self._fade_started) / max(0.001, duration)
        progress = 0.0 if progress < 0.0 else (1.0 if progress > 1.0 else progress)
        eased = progress * progress * (3.0 - 2.0 * progress)
        if progress >= 1.0:
            self._alpha = target
            self._fade_source = 0
            self._redraw()
            return False
        self._alpha = self._fade_from + (target - self._fade_from) * eased
        self._redraw()
        return True
