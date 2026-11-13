"""The glass-window transparency slider, drawn as a GTK overlay.

The OpenGL host drew this bar inside its own GL frame, so only that backend
had it. The Vulkan host draws the themed pane through its own pipeline, and had
no bar at all, so MP Liquid Player lost the slider once Vulkan became the
default backend.

This overlay is a thin borderless GTK window pinned to the strip the terminal
well reserves at the bottom of the player. It draws the same track and thumb,
follows the active theme colour, and reports drag input, so both hosts share
one implementation and one visual result regardless of the GPU path.
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
    """

    def __init__(self, opacity: float = DEFAULT_OPACITY, on_change=None):
        self.opacity = max(MIN_OPACITY, min(MAX_OPACITY, float(opacity)))
        self._on_change = on_change
        self._win = None
        self._area = None
        self._Gdk = None
        self._theme = ((0, 229, 255), (247, 37, 133), (102, 102, 255))
        self._dragging = False

    # -- lifecycle --------------------------------------------------------
    def attach(self, Gtk, Gdk, parent=None) -> bool:
        """Create the overlay window. Returns False when GTK is unavailable.

        ``parent`` should be the player's terminal window. A GTK popup with no
        parent is a temporary window that the display server refuses to
        position, which would leave the bar off-screen.
        """
        if Gtk is None or Gdk is None:
            return False
        try:
            win = Gtk.Window(type=Gtk.WindowType.POPUP)
            win.set_decorated(False)
            win.set_skip_taskbar_hint(True)
            win.set_skip_pager_hint(True)
            win.set_accept_focus(False)
            if parent is not None:
                try:
                    win.set_transient_for(parent)
                except Exception:
                    pass
            try:
                win.set_type_hint(Gdk.WindowTypeHint.UTILITY)
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
            win.show_all()
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

    def place(self, x: int, y: int, width: int, height: int) -> None:
        """Pin the overlay to the reserved strip at the bottom of ``height``."""
        if self._win is None:
            return
        width = max(64, int(width))
        strip_y = int(y) + max(0, int(height) - SLIDER_STRIP)
        try:
            self._win.move(int(x), strip_y)
            self._win.resize(width, SLIDER_STRIP)
            self._area.set_size_request(width, SLIDER_STRIP)
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
