#!/usr/bin/env python3
"""Clicking the timeline seeks, and clicking the pitch track retunes.

Both controls used to be paint-only: the timeline and the DJ pitch bar were
drawn as text and nothing hit-tested them, so a user who clicked the bar the
way every other player works heard the mix keep playing from where it was, and
a click on the pitch track did nothing at all.

These tests pin:

* a click anywhere on the timeline jumps to that fraction of the track and
  keeps playback running,
* a click on the pitch track lands on the matching percent, including the
  centre detent and both ends,
* the painted row numbers survive `_fit_frame` trimming the equaliser band,
* a press that starts on one of them and drags keeps scrubbing, and
* clicking elsewhere in the frame still does what it did before.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import MP_Audio_Player as player  # noqa: E402


def _status(**overrides):
    st = {
        "title": "Mix.flac",
        "path": "/mixes/Mix.flac",
        "state": "playing",
        "index": 0,
        "position_sec": 300.0,
        "duration_sec": 3600.0,
        "position_fmt": "05:00",
        "duration_fmt": "60:00",
        "progress_pct": 8.3,
        "volume": 85,
        "pitch_percent": 0,
        "levels": [0.0] * 24,
        "peaks": [0.0] * 24,
        "levels_left": [0.0] * 24,
        "levels_right": [0.0] * 24,
        "peaks_left": [0.0] * 24,
        "peaks_right": [0.0] * 24,
    }
    st.update(overrides)
    return st


def _ui(width=120, height=40):
    scanner = MagicMock()
    scanner.get_mixes.return_value = [
        {"name": f"Mix {i}.flac", "path": f"/mixes/{i}.flac", "ext": "FLAC", "size_mb": 10.0}
        for i in range(4)
    ]
    ui = player.TerminalPlayerUI(None, scanner, is_remote=True, ui_size_mode="NORMAL")
    ui._send_cmd = MagicMock()
    ui._get_status = lambda: _status()
    ui._win_size = (width, height)
    return ui


def _draw(ui, width=120, height=40):
    """Paint one frame and return the lines that were presented."""
    captured = []
    with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}), \
         patch("MP_Audio_Player._present_frame",
               side_effect=lambda lines, w, h: captured.extend(lines)), \
         patch("MP_Audio_Player._win_visible_size", return_value=(width, height)), \
         patch("sys.stdout.write"), patch("sys.stdout.flush"):
        ui._draw_frame()
    return captured


class TimelineClickTests(unittest.TestCase):
    def test_the_timeline_records_a_click_target_span(self):
        ui = _ui()
        _draw(ui)
        self.assertGreater(ui._timeline_row, 0)
        start, end = ui._timeline_span
        self.assertGreater(end, start)

    def test_a_click_in_the_middle_seeks_to_the_middle(self):
        ui = _ui()
        _draw(ui)
        start, end = ui._timeline_span
        ui._on_mouse_sgr(0, (start + end) // 2, ui._timeline_row, b"M")
        ui._send_cmd.assert_called_once()
        cmd, kwargs = ui._send_cmd.call_args[0][0], ui._send_cmd.call_args[1]
        self.assertEqual(cmd, "seek_to")
        self.assertAlmostEqual(kwargs["position"], 1800.0, delta=60.0)
        # The click is a "play from here", not a pause-and-seek.
        self.assertTrue(kwargs.get("resume"))

    def test_a_click_at_the_left_end_seeks_to_the_start(self):
        ui = _ui()
        _draw(ui)
        start, _end = ui._timeline_span
        ui._on_mouse_sgr(0, start, ui._timeline_row, b"M")
        self.assertAlmostEqual(ui._send_cmd.call_args[1]["position"], 0.0, delta=1.0)

    def test_a_click_at_the_right_end_seeks_to_the_end(self):
        ui = _ui()
        _draw(ui)
        _start, end = ui._timeline_span
        ui._on_mouse_sgr(0, end, ui._timeline_row, b"M")
        self.assertAlmostEqual(ui._send_cmd.call_args[1]["position"], 3600.0, delta=1.0)

    def test_a_click_beside_the_track_does_not_seek(self):
        """The time labels and the percentage are not part of the track."""
        ui = _ui()
        _draw(ui)
        start, end = ui._timeline_span
        ui._on_mouse_sgr(0, start - 3, ui._timeline_row, b"M")
        ui._on_mouse_sgr(0, end + 3, ui._timeline_row, b"M")
        ui._send_cmd.assert_not_called()

    def test_a_seek_waits_for_a_known_duration(self):
        ui = _ui()
        ui._get_status = lambda: _status(duration_sec=0.0)
        _draw(ui)
        start, end = ui._timeline_span
        ui._on_mouse_sgr(0, (start + end) // 2, ui._timeline_row, b"M")
        ui._send_cmd.assert_not_called()


class PitchTrackClickTests(unittest.TestCase):
    def test_clicking_the_centre_lands_on_the_detent(self):
        ui = _ui()
        _draw(ui)
        start, end = ui._pitch_span
        ui._on_mouse_sgr(0, (start + end) // 2, ui._pitch_row, b"M")
        ui._send_cmd.assert_called_once_with("set_pitch", percent=0)

    def test_clicking_the_right_end_is_plus_20(self):
        ui = _ui()
        _draw(ui)
        _start, end = ui._pitch_span
        ui._on_mouse_sgr(0, end, ui._pitch_row, b"M")
        self.assertEqual(ui._send_cmd.call_args[1]["percent"], 20)

    def test_clicking_the_left_end_is_minus_20(self):
        ui = _ui()
        _draw(ui)
        start, _end = ui._pitch_span
        ui._on_mouse_sgr(0, start, ui._pitch_row, b"M")
        self.assertEqual(ui._send_cmd.call_args[1]["percent"], -20)

    def test_clicking_a_quarter_in_is_about_minus_ten(self):
        ui = _ui()
        _draw(ui)
        start, end = ui._pitch_span
        ui._on_mouse_sgr(0, start + (end - start) // 4, ui._pitch_row, b"M")
        percent = ui._send_cmd.call_args[1]["percent"]
        self.assertTrue(-12 <= percent <= -8, f"got {percent}")

    def test_the_clicked_percent_is_always_in_range(self):
        ui = _ui()
        _draw(ui)
        start, end = ui._pitch_span
        for col in range(start, end + 1):
            ui._send_cmd.reset_mock()
            ui._on_mouse_sgr(0, col, ui._pitch_row, b"M")
            percent = ui._send_cmd.call_args[1]["percent"]
            self.assertTrue(-20 <= percent <= 20)

    def test_a_click_on_the_pitch_label_does_not_retune(self):
        ui = _ui()
        _draw(ui)
        start, _end = ui._pitch_span
        ui._on_mouse_sgr(0, start - 4, ui._pitch_row, b"M")
        ui._send_cmd.assert_not_called()


class TransportDragTests(unittest.TestCase):
    def test_dragging_the_timeline_keeps_scrubbing(self):
        ui = _ui()
        _draw(ui)
        start, end = ui._timeline_span
        # Press at the left end, then move to the middle with the button down.
        ui._on_mouse_sgr(0, start, ui._timeline_row, b"M")
        ui._on_mouse_sgr(32, (start + end) // 2, ui._timeline_row, b"M")
        self.assertEqual(ui._send_cmd.call_count, 2)
        self.assertAlmostEqual(ui._send_cmd.call_args[1]["position"], 1800.0, delta=60.0)

    def test_dragging_off_the_timeline_row_still_scrubs(self):
        ui = _ui()
        _draw(ui)
        start, end = ui._timeline_span
        ui._on_mouse_sgr(0, start, ui._timeline_row, b"M")
        # The pointer wanders onto the visualiser, several rows below.
        ui._on_mouse_sgr(32, end, ui._timeline_row + 3, b"M")
        self.assertAlmostEqual(ui._send_cmd.call_args[1]["position"], 3600.0, delta=1.0)

    def test_release_ends_the_scrub(self):
        ui = _ui()
        _draw(ui)
        start, end = ui._timeline_span
        ui._on_mouse_sgr(0, start, ui._timeline_row, b"M")
        ui._on_mouse_sgr(0, end, ui._timeline_row, b"m")
        ui._send_cmd.reset_mock()
        ui._on_mouse_sgr(32, end, ui._timeline_row, b"M")
        ui._send_cmd.assert_not_called()

    def test_a_hover_after_a_click_never_scrubs(self):
        """Plain hover reports 35 (motion with no button); it is not a drag."""
        ui = _ui()
        _draw(ui)
        start, end = ui._pitch_span
        ui._on_mouse_sgr(0, start, ui._pitch_row, b"M")
        ui._on_mouse_sgr(0, start, ui._pitch_row, b"m")
        ui._send_cmd.reset_mock()
        ui._on_mouse_sgr(35, end, ui._pitch_row, b"M")
        ui._send_cmd.assert_not_called()


class MiniTimelineClickTests(unittest.TestCase):
    """Mini paints its own compact bar, so it carries its own seek target."""

    def _mini(self):
        scanner = MagicMock()
        scanner.get_mixes.return_value = [
            {"name": "Mix 0.flac", "path": "/mixes/0.flac", "ext": "FLAC", "size_mb": 10.0}
        ]
        ui = player.TerminalPlayerUI(None, scanner, is_remote=True, ui_size_mode="MINI")
        ui._send_cmd = MagicMock()
        ui._get_status = lambda: _status()
        return ui

    def _draw_mini(self, ui):
        captured = []
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}), \
             patch("MP_Audio_Player._present_frame",
                   side_effect=lambda lines, w, h: captured.extend(lines)), \
             patch("MP_Audio_Player._win_visible_size", return_value=(74, 14)), \
             patch("sys.stdout.write"), patch("sys.stdout.flush"):
            ui._draw_frame()
        return captured

    def test_the_span_covers_exactly_the_painted_bar(self):
        ui = self._mini()
        lines = self._draw_mini(ui)
        row = _plain(lines[ui._timeline_row - 1])
        start, end = ui._timeline_span
        at = dict(_display_cells(row))
        self.assertIn(at.get(start - 1), "━●", "the span starts before the bar")
        self.assertIn(at.get(end - 1), "━●─", "the span ends after the bar")

    def test_a_click_selects_the_matching_position(self):
        ui = self._mini()
        self._draw_mini(ui)
        start, end = ui._timeline_span
        ui._on_mouse_sgr(0, start, ui._timeline_row, b"M")
        self.assertAlmostEqual(ui._send_cmd.call_args[1]["position"], 0.0, delta=1.0)
        ui._on_mouse_sgr(0, end, ui._timeline_row, b"M")
        self.assertAlmostEqual(ui._send_cmd.call_args[1]["position"], 3600.0, delta=1.0)

    def test_micro_has_no_timeline_target(self):
        """Micro is seven rows and prints no bar, so it must expose no target."""
        scanner = MagicMock()
        scanner.get_mixes.return_value = []
        ui = player.TerminalPlayerUI(None, scanner, is_remote=True, ui_size_mode="MICRO")
        ui._send_cmd = MagicMock()
        ui._get_status = lambda: _status()
        captured = []
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}), \
             patch("MP_Audio_Player._present_frame",
                   side_effect=lambda lines, w, h: captured.extend(lines)), \
             patch("MP_Audio_Player._win_visible_size", return_value=(74, 7)), \
             patch("sys.stdout.write"), patch("sys.stdout.flush"):
            ui._draw_frame()
        self.assertEqual(ui._timeline_row, 0)
        self.assertEqual(ui._pitch_row, 0)


class TrimmedFrameClickTests(unittest.TestCase):
    """A short console drops the middle band, and the rows must move with it."""
    def test_the_timeline_row_matches_the_painted_row_after_trimming(self):
        ui = _ui(height=24)
        lines = _draw(ui, height=24)
        self.assertEqual(len(lines), 24)
        row = ui._timeline_row
        self.assertGreater(row, 0)
        self.assertLessEqual(row, len(lines))
        painted = _plain(lines[row - 1])
        # The painted row must be the transport line, not a shifted equaliser row.
        self.assertIn("05:00", painted)
        self.assertIn("60:00", painted)

    def test_the_pitch_row_matches_the_painted_row_after_trimming(self):
        ui = _ui(height=24)
        lines = _draw(ui, height=24)
        row = ui._pitch_row
        self.assertGreater(row, 0)
        painted = _plain(lines[row - 1])
        self.assertIn("DJ PITCH", painted)
        start, end = ui._pitch_span
        at = dict(_display_cells(painted))
        self.assertIn(at.get(start - 1), "─━●", "the pitch span starts before the track")
        self.assertIn(at.get(end - 1), "─━●", "the pitch span ends after the track")
        self.assertEqual(at.get(end), "]")

    def test_a_click_still_seeks_on_a_short_frame(self):
        ui = _ui(height=24)
        _draw(ui, height=24)
        start, end = ui._timeline_span
        ui._on_mouse_sgr(0, (start + end) // 2, ui._timeline_row, b"M")
        self.assertEqual(ui._send_cmd.call_args[0][0], "seek_to")

    def test_rows_are_unchanged_when_nothing_is_dropped(self):
        plan = player._fit_frame_plan(10, 40)
        self.assertIsNone(plan)
        for index in range(10):
            self.assertEqual(player._fit_frame_row(index, plan, 10), index + 1)

    def test_rows_after_the_dropped_band_move_up(self):
        # 40 lines into 24 rows: keep 16 head + 8 tail, drop 16 in the middle.
        plan = player._fit_frame_plan(40, 24)
        keep_head, keep_tail = plan
        self.assertEqual(keep_head + keep_tail, 24)
        self.assertEqual(player._fit_frame_row(0, plan, 40), 1)
        self.assertEqual(player._fit_frame_row(keep_head - 1, plan, 40), keep_head)
        # The first dropped row is gone, not remapped onto a painted row.
        self.assertEqual(player._fit_frame_row(keep_head, plan, 40), 0)
        # The tail is contiguous with the head band.
        self.assertEqual(player._fit_frame_row(40 - keep_tail, plan, 40), keep_head + 1)
        self.assertEqual(player._fit_frame_row(39, plan, 40), 24)


class PitchBarPartsTests(unittest.TestCase):
    """The split bar must paint exactly what the single string used to."""

    def test_the_three_parts_rebuild_the_single_line(self):
        visualizer = player.EqualizerVisualizer()
        for pitch in (-20, -7, 0, 3, 20):
            for width in (20, 37, 54, 61):
                single = visualizer.render_horizontal_pitch_bar(pitch, width=width)
                joined = "".join(visualizer.pitch_bar_parts(pitch, width=width))
                self.assertEqual(joined, single)

    def test_the_track_is_as_wide_as_the_requested_bar(self):
        """The click span is measured from the painted track, so it must match.

        The track is always an odd number of columns so it has a true centre
        detent; an even request is reduced by one.
        """
        visualizer = player.EqualizerVisualizer()
        for width in (37, 54, 61):
            expected = width if width % 2 else width - 1
            _prefix, track, _suffix = visualizer.pitch_bar_parts(0, width=width)
            self.assertEqual(player._display_width(track), expected)


def _ink_before(text, glyph):
    """The SGR sequence sitting immediately in front of the first `glyph`."""
    import re
    at = text.index(glyph)
    marks = list(re.finditer(r"\033\[[0-9;]*m", text[:at]))
    return marks[-1].group(0) if marks else ""


class PitchControlFollowsThemeTests(unittest.TestCase):
    """The DJ pitch knob and its line take the active theme, in both hosts.

    Pressing C cycles the theme. The knob used to stay traffic-green, amber,
    or indigo, so the control did not move with the rest of the chrome.
    """

    def _viz(self, theme):
        visualizer = player.EqualizerVisualizer()
        visualizer.set_theme(theme)
        return visualizer

    def test_knob_and_line_use_the_theme_and_drop_the_traffic_inks(self):
        visualizer = self._viz("phosphor")
        ink = visualizer.pitch_control_colors()
        _prefix, track, _suffix = visualizer.pitch_bar_parts(12, width=21)
        self.assertEqual(_ink_before(track, "●"), ink["knob"])
        self.assertEqual(_ink_before(track, "━"), ink["fill"])
        self.assertEqual(_ink_before(track, "─"), ink["track"])
        self.assertNotIn(player.COLOR_TRAFFIC_GREEN, track)
        self.assertNotIn(player.COLOR_TRAFFIC_AMBER, track)
        self.assertNotIn(player.COLOR_INDIGO_BLUE, track)
        self.assertIn("38;2;0;255;102", ink["knob"])

    def test_pressing_c_changes_the_knob_and_the_line(self):
        visualizer = self._viz("phosphor")
        before = visualizer.pitch_control_colors()
        first = visualizer.pitch_bar_parts(8, width=21)[1]
        visualizer.set_theme("ice_cold")
        after = visualizer.pitch_control_colors()
        second = visualizer.pitch_bar_parts(8, width=21)[1]
        self.assertNotEqual(before["knob"], after["knob"])
        self.assertNotEqual(before["fill"], after["fill"])
        self.assertNotEqual(_ink_before(first, "●"), _ink_before(second, "●"))
        self.assertNotEqual(_ink_before(first, "━"), _ink_before(second, "━"))
        self.assertEqual(_ink_before(second, "●"), after["knob"])
        self.assertIn("38;2;129;161;193", after["knob"])

    def test_every_selectable_theme_paints_its_own_knob(self):
        for theme in player.EqualizerVisualizer.THEMES:
            visualizer = self._viz(theme)
            ink = visualizer.pitch_control_colors()
            track = visualizer.pitch_bar_parts(-9, width=21)[1]
            self.assertEqual(_ink_before(track, "●"), ink["knob"], theme)
            self.assertNotIn("\033[48", track, theme)
            self.assertNotIn(player.COLOR_TRAFFIC_AMBER, track, theme)

    def test_a_light_theme_keeps_a_foreground_knob(self):
        visualizer = self._viz("the_whitest_white")
        ink = visualizer.pitch_control_colors()
        track = visualizer.pitch_bar_parts(6, width=21)[1]
        self.assertIn("38;2;0;0;0", ink["knob"])
        self.assertIn("38;2;0;0;0", ink["fill"])
        self.assertNotIn("48;", track)
        self.assertEqual(_ink_before(track, "●"), ink["knob"])

    def test_liquid_player_keeps_the_theme_color_without_bold(self):
        visualizer = self._viz("cyber_neon")
        previous = os.environ.get("MP_LIQUID_GLASS")
        os.environ["MP_LIQUID_GLASS"] = "1"
        try:
            track = visualizer.pitch_bar_parts(4, width=21)[1]
        finally:
            if previous is None:
                os.environ.pop("MP_LIQUID_GLASS", None)
            else:
                os.environ["MP_LIQUID_GLASS"] = previous
        self.assertNotIn("\033[1m", track)
        self.assertEqual(_ink_before(track, "●"), visualizer.pitch_control_colors()["knob"])
        self.assertIn("38;2;255;0;85", track)

    def test_the_wide_fader_knob_and_stem_follow_the_theme(self):
        visualizer = self._viz("retro_studio")
        ink = visualizer.pitch_control_colors()
        painted = "\n".join(visualizer.render_vertical_pitch_fader(10, total_lines=12))
        self.assertIn(f"{ink['knob']}[▓███▓]", painted)
        self.assertIn(f"{ink['track']}│", painted)
        self.assertNotIn(player.COLOR_TRAFFIC_AMBER, painted)
        self.assertNotIn(player.COLOR_INDIGO_BLUE, painted)


class FrameRowMappingTests(unittest.TestCase):
    """`_fit_frame_row` must agree with what `_fit_frame` actually paints."""

    def test_every_mapped_row_holds_the_line_it_claims(self):
        for total in (30, 34, 40, 52):
            for height in (20, 24, 26, 30, 40):
                lines = [f"line-{i}" for i in range(total)]
                plan = player._fit_frame_plan(total, height)
                painted = player._fit_frame(lines, height)
                self.assertLessEqual(len(painted), max(height, total))
                for index in range(total):
                    row = player._fit_frame_row(index, plan, total)
                    if row:
                        self.assertEqual(painted[row - 1], lines[index],
                                         f"{total}->{height}: index {index} mapped to row {row}")

    def test_every_painted_row_is_reachable(self):
        total, height = 40, 24
        plan = player._fit_frame_plan(total, height)
        painted = player._fit_frame([f"line-{i}" for i in range(total)], height)
        reachable = {player._fit_frame_row(i, plan, total) for i in range(total)}
        self.assertTrue(all(row in reachable for row in range(1, len(painted) + 1)))


def _plain(text):
    return player._ANSI_RE.sub("", text)


def _display_cells(text):
    """Yield (display column start, character) for a plain string.

    Wide glyphs (the emoji in `🎚 DJ PITCH:`) occupy two columns, so a byte or
    character index is not a terminal column. Tests that check a recorded span
    must walk display columns the way the click handler does.
    """
    col = 0
    for ch in text:
        yield col, ch
        col += player._char_display_width(ch)


if __name__ == "__main__":
    unittest.main()
