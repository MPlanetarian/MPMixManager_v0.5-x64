"""The glass-window transparency slider, drawn as a GTK overlay.

The OpenGL host drew this bar inside its own GL frame, so only that backend
had it. The Vulkan host draws the themed pane through its own pipeline, and had
no bar at all, so MP Liquid Player lost the slider once Vulkan became the
default backend.

This overlay is a thin borderless GTK window pinned to the strip the terminal
well reserves at the bottom of the player. It draws the same track and thumb,
follows the active theme colour, and reports drag input, so both hosts share
one implementation and one visual result regardless of the GPU path.

The overlay is a *managed* toplevel, never a GTK popup window. A popup is
created override-redirect, so the window manager never restacks it: the bar
stayed at the top of the X stack for its whole life and kept floating over any
other application the user switched to. A managed toplevel made transient for
the player is restacked with the player instead.
"""
from __future__ import annotations

import sys

DEFAULT_OPACITY = 0.70
MIN_OPACITY = 0.20
MAX_OPACITY = 0.80

# Height of the strip the terminal well keeps free at the bottom of the window.
SLIDER_STRIP = 44
# Horizontal inset of the bar from the window edges.
SLIDER_LEFT = 40
SLIDER_RIGHT_PAD = 40
SLIDER_HEIGHT = 8

# Smallest strip the bar still fits in: the thumb is SLIDER_HEIGHT + 6 tall.
MIN_SLIDER_STRIP = SLIDER_HEIGHT + 6


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
    """Map a pointer x inside the window to a glass opacity in [0.20, 0.80]."""
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


class GlassSlider:
    """A GTK overlay that draws the transparency bar for a glass window.

    The overlay is a separate borderless window, so it appears above both the
    GPU-drawn glass and the VTE terminal on either backend. ``place()`` keeps it
    on the reserved strip, ``set_theme()`` recolours it, and a press or drag on
    it reports a new opacity through ``on_change``.

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
            area.add_events(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.BUTTON_RELEASE_MASK
                | Gdk.EventMask.BUTTON1_MOTION_MASK
                | Gdk.EventMask.POINTER_MOTION_MASK
            )
            area.connect("draw", self._on_draw)
            area.connect("button-press-event", self._on_press)
            area.connect("button-release-event", self._on_release)
            area.connect("motion-notify-event", self._on_motion)
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
        try:
            if self._win is not None:
                self._win.destroy()
        except Exception:
            pass
        self._win = self._area = None
        self._placed = None

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
        cr.set_source_rgba(trough[0] / 255.0, trough[1] / 255.0, trough[2] / 255.0, 0.28)
        cr.rectangle(left, bar_top, max(1.0, right - left), bar_h)
        cr.fill()

        # The filled portion, in the theme accent, still translucent.
        thumb_x = slider_thumb_x(self.opacity, width)
        if thumb_x > left:
            cr.set_source_rgba(accent[0] / 255.0, accent[1] / 255.0, accent[2] / 255.0, 0.45)
            cr.rectangle(left, bar_top, max(1.0, thumb_x - left), bar_h)
            cr.fill()

        # The thumb: a small bright glass knob.
        knob = tuple(int(c + (255 - c) * 0.35) for c in accent)
        cr.set_source_rgba(knob[0] / 255.0, knob[1] / 255.0, knob[2] / 255.0, 0.80)
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

    def _on_press(self, widget, event) -> bool:
        if getattr(event, "button", 1) != 1:
            return False
        self._dragging = True
        self._apply_from_event(widget, event)
        return True

    def _on_motion(self, widget, event) -> bool:
        if not self._dragging:
            return False
        self._apply_from_event(widget, event)
        return True

    def _on_release(self, widget, event) -> bool:
        if getattr(event, "button", 1) != 1:
            return False
        self._dragging = False
        self._apply_from_event(widget, event)
        return True
