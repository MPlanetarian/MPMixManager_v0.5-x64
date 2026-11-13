"""Main-screen placement skips the Hisense TV at the desktop origin."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from desktop_launch_screen import (  # noqa: E402
    centered_window_origin,
    fit_square_on_main,
    main_desktop_origin,
    main_desktop_rect,
    main_window_origin,
    parse_kscreen,
    parse_xrandr_monitors,
    parse_xwininfo_origin,
    placement_adjustment,
    select_main,
    to_x11_position,
    unscale_reported_position,
    window_launch_origin,
    x11_coordinate_scale,
)


HISENSE_AT_ORIGIN = """
Output: 1 HDMI-A-1 aaaa
	enabled
	connected
	priority 2
	Geometry: 0,0 2648x1490
Output: 2 DP-3 bbbb
	enabled
	connected
	priority 1
	Geometry: 2649,0 1920x1200
Output: 3 DP-1 cccc
	enabled
	connected
	priority 3
	Geometry: 4569,0 1920x1200
"""

HISENSE_IS_PRIORITY = """
Output: 1 HDMI-A-1 aaaa
	enabled
	connected
	priority 1
	Geometry: 0,0 2648x1490
Output: 2 DP-3 bbbb
	enabled
	connected
	priority 2
	Geometry: 4569,0 1920x1200
"""

CURRENT_DESKTOP = """
Output: 1 DP-3 aaaa
	enabled
	connected
	priority 1
	Geometry: 1920,0 1920x1200
Output: 2 DP-1 bbbb
	enabled
	connected
	priority 2
	Geometry: 0,0 1920x1200
Output: 3 DP-2 cccc
	enabled
	connected
	priority 3
	Geometry: 3840,0 1920x1200
"""


class TestDesktopLaunchScreen(unittest.TestCase):
    def test_hisense_at_origin_uses_priority_desktop(self):
        outputs = parse_kscreen(HISENSE_AT_ORIGIN)
        self.assertEqual(select_main(outputs)["name"], "DP-3")
        self.assertEqual(main_desktop_origin(72, 64, outputs), (2721, 64))

    def test_hisense_primary_still_uses_a_desktop_monitor(self):
        outputs = parse_kscreen(HISENSE_IS_PRIORITY)
        self.assertEqual(select_main(outputs)["name"], "DP-3")
        self.assertEqual(main_desktop_origin(160, 80, outputs), (4729, 80))

    def test_live_three_monitor_layout_uses_center_screen(self):
        outputs = parse_kscreen(CURRENT_DESKTOP)
        self.assertEqual(main_desktop_origin(72, 64, outputs), (1992, 64))

    def test_colored_kscreen_output_parses(self):
        colored = "\x1b[01;32mOutput: \x1b[0;0m1 DP-3 uuid\n\t\x1b[01;32menabled\x1b[0;0m\n\t\x1b[01;32mconnected\x1b[0;0m\n\t\x1b[01;32mpriority 1\x1b[0;0m\n\x1b[01;33m\tGeometry: \x1b[0;0m1920,0 1920x1200\n"
        outputs = parse_kscreen(colored)
        self.assertEqual(outputs[0]["name"], "DP-3")
        self.assertEqual(outputs[0]["geo"], (1920, 0, 1920, 1200))
        self.assertTrue(outputs[0]["enabled"])

    def test_xrandr_primary_skips_hisense(self):
        text = "\n".join([
            "Monitors: 2",
            " 0: +*HDMI-A-1 3840/1200x2160/680+0+0  HDMI-A-1",
            " 1: +DP-3 1920/546x1200/352+2649+0  DP-3",
        ])
        monitors = parse_xrandr_monitors(text)
        self.assertEqual(monitors[0]["name"], "HDMI-A-1")
        self.assertTrue(monitors[0]["primary"])
        self.assertEqual(monitors[1]["geo"][:2], (2649, 0))

    def test_square_shrinks_to_the_1200p_work_area(self):
        outputs = parse_kscreen(CURRENT_DESKTOP)
        fitted = fit_square_on_main(1400, outputs=outputs, workarea=(0, 0, 5760, 1138))
        self.assertEqual(fitted, (1114, 1114, 403, 12))
        _width, height, offset_x, offset_y = fitted
        self.assertLessEqual(offset_x + _width, 1920)
        self.assertLessEqual(offset_y + height, 1138)

    def test_square_shrinks_when_the_panel_size_is_unknown(self):
        outputs = parse_kscreen(CURRENT_DESKTOP)
        self.assertEqual(
            fit_square_on_main(1400, outputs=outputs),
            (1176, 1176, 372, 12),
        )

    def test_xwininfo_origin_is_the_real_position(self):
        text = "\n".join([
            "xwininfo: Window id: 0x2600007 \"MP Audio Player (Liquid Glass - OpenGL)\"",
            "  Absolute upper-left X:  1992",
            "  Absolute upper-left Y:  64",
            "  Width: 1094",
            "  Height: 898",
        ])
        self.assertEqual(parse_xwininfo_origin(text), (1992, 64))

    def test_wmctrl_list_is_twice_the_real_position(self):
        # wmctrl -lG reported 4200,160 while xwininfo and KWin showed 2100,80.
        self.assertEqual(unscale_reported_position((4200, 160), (2100, 80)), (2100, 80))
        self.assertEqual(unscale_reported_position((1992, 64), (1992, 64)), (1992, 64))

    def test_matching_real_position_is_not_moved_off_the_main_screen(self):
        # Requesting the main screen at 1992 must stay there. The old correction
        # treated wmctrl's doubled 3984 as the landing spot and requested 996,
        # which is the left monitor.
        self.assertIsNone(placement_adjustment((1992, 64), (1992, 64)))
        self.assertEqual(placement_adjustment((1992, 64), (3984, 128)), (996, 32))

    def test_xwayland_scale_puts_the_request_on_the_primary(self):
        outputs = parse_kscreen(
            """
Output: 1 DP-2 aaaa
	enabled
	connected
	priority 1
	Geometry: 1920,0 1920x1200
Output: 2 DP-1 bbbb
	enabled
	connected
	priority 2
	Geometry: 0,120 1920x1200
Output: 3 HDMI-A-1 cccc
	enabled
	connected
	priority 3
	Geometry: 1579,1200 2649x1490
Output: 4 DP-3 dddd
	enabled
	connected
	priority 4
	Geometry: 3840,120 1920x1200
"""
        )
        monitors = parse_xrandr_monitors("\n".join([
            "Monitors: 4",
            " 0: +*DP-2 2784/546x1740/352+2784+0  DP-2",
            " 1: +HDMI-A-1 3840/698x2160/392+2290+1740  HDMI-A-1",
            " 2: +DP-1 2784/519x1740/324+0+174  DP-1",
            " 3: +DP-3 2784/519x1740/324+5568+174  DP-3",
        ]))
        self.assertEqual(select_main(outputs)["name"], "DP-2")
        self.assertAlmostEqual(x11_coordinate_scale(outputs, monitors), 1.45, places=4)
        # KWin (1992, 64) is on DP-2. The same numbers in X11 land on DP-1.
        self.assertEqual(main_desktop_origin(72, 64, outputs), (1992, 64))
        self.assertEqual(window_launch_origin(72, 64, outputs, monitors), (2888, 93))

    def test_matching_x11_coordinates_stay_on_the_kwin_position(self):
        outputs = parse_kscreen(CURRENT_DESKTOP)
        monitors = parse_xrandr_monitors("\n".join([
            "Monitors: 2",
            " 0: +*DP-2 1920/546x1200/352+1920+0  DP-2",
            " 1: +DP-1 1920/519x1200/324+0+120  DP-1",
        ]))
        self.assertEqual(x11_coordinate_scale(outputs, monitors), 1.0)
        self.assertEqual(to_x11_position(1992, 64, 1.0), (1992, 64))
        self.assertEqual(window_launch_origin(72, 64, outputs, monitors), (1992, 64))

    def test_preferred_square_stays_on_a_large_screen(self):
        outputs = parse_kscreen(
            """
Output: 1 DP-3 aaaa
	enabled
	connected
	priority 1
	Geometry: 0,0 3840x2160
"""
        )
        self.assertEqual(
            fit_square_on_main(1400, outputs=outputs, workarea=(0, 0, 3840, 2160)),
            (1400, 1400, 160, 80),
        )


class TestCenteredPlacement(unittest.TestCase):
    """MP windows must open centred on the primary screen, never in the corner.

    Regression: the Liquid Player and MP Gui opened at a fixed (72, 64) corner
    offset. Their finished size (1574x1098) then ran past the right and bottom
    edges of the 1920x1200 primary monitor, so the windows appeared to hang off
    the top-right corner instead of being centred.
    """

    # The centre screen (DP-3) is primary and sits at logical x 1920. Under the
    # default 1.0 scale the X11 origin is the same as the KWin origin here, so
    # the expected numbers stay readable.
    MONITORS = "\n".join([
        "Monitors: 3",
        " 0: +*DP-3 1920/519x1200/324+1920+0  DP-3",
        " 1: +DP-1 1920/519x1200/324+0+0  DP-1",
        " 2: +DP-2 1920/546x1200/352+3840+0  DP-2",
    ])

    def test_centred_window_sits_in_the_middle_of_the_primary_monitor(self):
        outputs = parse_kscreen(CURRENT_DESKTOP)
        monitors = parse_xrandr_monitors(self.MONITORS)
        self.assertEqual(x11_coordinate_scale(outputs, monitors), 1.0)
        x, y = centered_window_origin(1574, 1098, outputs, monitors, panel_inset=0)
        self.assertEqual(x, 1920 + (1920 - 1574) // 2)
        self.assertEqual(y, (1200 - 1098) // 2)

    def test_main_window_origin_is_centred_not_cornered(self):
        outputs = parse_kscreen(CURRENT_DESKTOP)
        monitors = parse_xrandr_monitors(self.MONITORS)
        corner = window_launch_origin(72, 64, outputs, monitors)
        centred = main_window_origin(1574, 1098, 72, 64, outputs, monitors)
        self.assertNotEqual(centred, corner)
        self.assertEqual(corner, (1992, 64))
        self.assertEqual(centred, centered_window_origin(1574, 1098, outputs, monitors))

    def test_window_stays_inside_the_primary_monitor(self):
        outputs = parse_kscreen(CURRENT_DESKTOP)
        monitors = parse_xrandr_monitors(self.MONITORS)
        width, height = 1574, 1098
        x, y = main_window_origin(width, height, 72, 64, outputs, monitors)
        rect_x, rect_y, rect_w, rect_h = main_desktop_rect(outputs, monitors, panel_inset=0)
        self.assertGreaterEqual(x, rect_x)
        self.assertGreaterEqual(y, rect_y)
        self.assertLessEqual(x + width, rect_x + rect_w)
        self.assertLessEqual(y + height, rect_y + rect_h)

    def test_main_window_origin_matches_centred_origin(self):
        outputs = parse_kscreen(CURRENT_DESKTOP)
        monitors = parse_xrandr_monitors(self.MONITORS)
        origin = main_window_origin(1574, 1098, 72, 64, outputs, monitors)
        expected = centered_window_origin(1574, 1098, outputs, monitors)
        self.assertEqual(origin, expected)

    def test_scaled_xwayland_layout_centres_on_the_scaled_primary(self):
        """At a 1.45 Xwayland scale the centre point must use X11 pixels."""
        outputs = parse_kscreen(CURRENT_DESKTOP)
        monitors = parse_xrandr_monitors("\n".join([
            "Monitors: 3",
            " 0: +*DP-3 2784/519x1740/324+2784+0  DP-3",
            " 1: +DP-1 2784/519x1740/324+0+0  DP-1",
            " 2: +DP-2 2784/546x1740/352+5568+0  DP-2",
        ]))
        self.assertAlmostEqual(x11_coordinate_scale(outputs, monitors), 1.45, places=2)
        rect_x, rect_y, rect_w, rect_h = main_desktop_rect(outputs, monitors, panel_inset=0)
        self.assertEqual((rect_x, rect_w), (2784, 2784))
        x, y = centered_window_origin(1574, 1098, outputs, monitors, panel_inset=0)
        self.assertEqual(x, rect_x + (rect_w - 1574) // 2)
        self.assertEqual(y, rect_y + (rect_h - 1098) // 2)
        self.assertLessEqual(x + 1574, rect_x + rect_w)
        self.assertLessEqual(y + 1098, rect_y + rect_h)


if __name__ == "__main__":
    unittest.main()
