"""The transparency bar must not float over other applications.

The reported bug: while MP Liquid Player was running, switching to any other
window left the transparency bar visible on top of that window.

Cause: the bar is a GTK overlay, and it was created as
``Gtk.WindowType.POPUP``. GTK makes a popup **override-redirect**, which means
the window manager never restacks it. It therefore sat at the very top of the
X stack for its entire life, above every other application, no matter what the
user focused. Verified on KWin/XWayland: with an unrelated window focused the
popup was at stack index 23 while that window was at index 4.

The fix is to make the overlay a *managed* toplevel made transient for the
player. The window manager then keeps it stacked with the player: above the
glass, and behind anything else that is focused.

These tests drive a fake Gtk/Gdk pair, so they need no display and assert the
exact window type and hints the fix depends on.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from liquid_glass_slider import (  # noqa: E402
    DEFAULT_OPACITY,
    MAX_OPACITY,
    MIN_OPACITY,
    SLIDER_STRIP,
    GlassSlider,
    opacity_from_x,
    slider_thumb_x,
)


class FakeWidget:
    """Records the calls the slider makes on windows and drawing areas."""

    def __init__(self):
        self.calls = []
        self.style_context = MagicMock()

    def __getattr__(self, name):
        def recorder(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None

        return recorder

    def called(self, name):
        return [c for c in self.calls if c[0] == name]


class FakeDrawArea(FakeWidget):
    def __init__(self):
        super().__init__()
        self.handlers = {}

    def connect(self, signal, handler):
        self.handlers[signal] = handler
        return 1

    def get_allocated_width(self):
        return 1000

    def get_allocated_height(self):
        return SLIDER_STRIP


class FakeWindow(FakeWidget):
    def __init__(self, *args, **kwargs):
        super().__init__()
        self.type = kwargs.get("type")
        self.children = []

    def add(self, child):
        self.children.append(child)

    def get_screen(self):
        screen = MagicMock()
        screen.get_rgba_visual.return_value = "rgba-visual"
        return screen

    def get_style_context(self):
        return MagicMock()


class FakeGdk:
    class WindowTypeHint:
        UTILITY = "utility"

    class EventMask:
        BUTTON_PRESS_MASK = 1 << 2
        BUTTON_RELEASE_MASK = 1 << 3
        BUTTON1_MOTION_MASK = 1 << 8
        POINTER_MOTION_MASK = 1 << 6


def fake_gtk(window_types):
    gtk = MagicMock()
    gtk.WindowType.POPUP = "popup"
    gtk.WindowType.TOPLEVEL = "toplevel"

    class _Win:
        def __init__(self, *args, **kwargs):
            # Gtk.Window(type=...) is keyword-only in the real toolkit.
            if "type" in kwargs:
                window_types.append(kwargs["type"])
            self._w = FakeWindow(type=kwargs.get("type"))
            self.window = self._w

        def __getattr__(self, name):
            return getattr(self._w, name)

    def _make_window(*args, **kwargs):
        return _Win(*args, **kwargs)

    gtk.Window.side_effect = _make_window
    gtk.Window = _make_window
    gtk.DrawingArea = FakeDrawArea
    gtk.CssProvider = MagicMock
    gtk.STYLE_PROVIDER_PRIORITY_APPLICATION = 600
    gtk.StyleContext = MagicMock()
    gtk.StyleContext.add_provider = MagicMock()
    return gtk


class OverlayIsManagedTest(unittest.TestCase):
    """A popup is override-redirect, so it can never be restacked by the WM."""

    def test_the_overlay_is_not_a_popup(self):
        window_types = []
        gtk = fake_gtk(window_types)
        slider = GlassSlider()
        self.assertTrue(slider.attach(gtk, FakeGdk, parent=MagicMock()))
        self.assertNotIn(
            "popup", window_types,
            "a Gtk.WindowType.POPUP is override-redirect and floats above every "
            "other application; it must be a managed toplevel",
        )
        self.assertIn("toplevel", window_types)

    def test_the_overlay_is_transient_for_the_player(self):
        window_types = []
        gtk = fake_gtk(window_types)
        parent = MagicMock()
        slider = GlassSlider()
        self.assertTrue(slider.attach(gtk, FakeGdk, parent=parent))
        transient = slider._win.called("set_transient_for")
        self.assertTrue(transient, "the overlay must be transient for the player")
        self.assertIs(transient[0][1][0], parent)

    def test_the_overlay_never_takes_keyboard_focus(self):
        """The bar takes drags; it must not steal the terminal's keys."""
        window_types = []
        gtk = fake_gtk(window_types)
        slider = GlassSlider()
        self.assertTrue(slider.attach(gtk, FakeGdk, parent=MagicMock()))
        accept = slider._win.called("set_accept_focus")
        self.assertTrue(accept and accept[0][1][0] is False)
        on_map = slider._win.called("set_focus_on_map")
        self.assertTrue(on_map and on_map[0][1][0] is False)

    def test_the_overlay_stays_off_the_taskbar_and_out_of_the_way(self):
        window_types = []
        gtk = fake_gtk(window_types)
        slider = GlassSlider()
        self.assertTrue(slider.attach(gtk, FakeGdk, parent=MagicMock()))
        win = slider._win
        self.assertTrue(win.called("set_skip_taskbar_hint"))
        self.assertTrue(win.called("set_skip_pager_hint"))
        self.assertTrue(win.called("set_decorated"))

    def test_the_overlay_is_mapped_only_after_it_is_positioned(self):
        """A managed window is mapped by the WM; show before place() flashes."""
        window_types = []
        gtk = fake_gtk(window_types)
        slider = GlassSlider()
        self.assertTrue(slider.attach(gtk, FakeGdk, parent=MagicMock()))
        self.assertFalse(
            slider._win.called("show_all"),
            "the toplevel must not be shown before place() positions it",
        )
        self.assertTrue(slider._win.called("realize"),
                        "the widgets must still be realised so draw has a surface")

        slider.place(100, 200, 1000, 700)
        self.assertTrue(slider._win.called("show"),
                        "place() must map the overlay once it has geometry")

    def test_place_does_not_flood_the_window_manager(self):
        """place() runs every frame; managed moves are WM requests."""
        window_types = []
        gtk = fake_gtk(window_types)
        slider = GlassSlider()
        self.assertTrue(slider.attach(gtk, FakeGdk, parent=MagicMock()))
        slider.place(100, 200, 1000, 700)
        moves = len(slider._win.called("move"))
        for _ in range(30):
            slider.place(100, 200, 1000, 700)
        self.assertEqual(len(slider._win.called("move")), moves,
                         "an unchanged geometry must not be submitted again")

        # A real change still gets through.
        slider.place(140, 200, 1000, 700)
        self.assertEqual(len(slider._win.called("move")), moves + 1)

    def test_place_puts_the_bar_in_the_reserved_bottom_strip(self):
        window_types = []
        gtk = fake_gtk(window_types)
        slider = GlassSlider()
        self.assertTrue(slider.attach(gtk, FakeGdk, parent=MagicMock()))
        slider.place(300, 400, 1000, 700)
        move = slider._win.called("move")[0][1]
        self.assertEqual(move[0], 300)
        self.assertEqual(move[1], 400 + 700 - SLIDER_STRIP)

    def test_detach_resets_the_geometry_memo(self):
        window_types = []
        gtk = fake_gtk(window_types)
        slider = GlassSlider()
        self.assertTrue(slider.attach(gtk, FakeGdk, parent=MagicMock()))
        slider.place(100, 200, 1000, 700)
        slider.detach()
        self.assertIsNone(slider._placed)
        self.assertFalse(slider.available)


class MappingTest(unittest.TestCase):
    """The bar still maps pointer x to the documented opacity range."""

    def test_left_edge_is_minimum_opacity(self):
        self.assertAlmostEqual(opacity_from_x(40, 1000), MIN_OPACITY, places=3)

    def test_right_edge_is_maximum_opacity(self):
        self.assertAlmostEqual(opacity_from_x(960, 1000), MAX_OPACITY, places=3)

    def test_thumb_round_trips(self):
        for opacity in (0.20, 0.35, 0.50, 0.70, 0.80):
            x = slider_thumb_x(opacity, 1000)
            self.assertAlmostEqual(opacity_from_x(x, 1000), opacity, places=3)

    def test_default_is_inside_the_range(self):
        self.assertGreater(DEFAULT_OPACITY, MIN_OPACITY)
        self.assertLess(DEFAULT_OPACITY, MAX_OPACITY)

    def test_dragging_right_raises_opacity(self):
        seen = [opacity_from_x(x, 1000) for x in range(40, 961, 100)]
        self.assertEqual(seen, sorted(seen))
        self.assertGreater(seen[-1], seen[0])


class SourceGuardTest(unittest.TestCase):
    """Static guard so the popup cannot be reinstated later."""

    def test_the_module_never_builds_a_popup_window(self):
        source = (ROOT / "scripts" / "liquid_glass_slider.py").read_text(encoding="utf-8")
        for line in source.splitlines():
            code = line.split("#", 1)[0]
            if "Gtk.Window(" not in code or "POPUP" not in code:
                continue
            self.fail(
                "Gtk.WindowType.POPUP makes the overlay override-redirect, so the "
                "window manager never restacks it and the bar floats over every "
                f"other application: {line.strip()}"
            )


if __name__ == "__main__":
    unittest.main()
