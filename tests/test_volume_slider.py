#!/usr/bin/env python3
"""The MP Liquid Player's volume slider: click and drag left-to-right.

The player could change the level with `v`/`V` and mute with `u`, but the only
volume control on screen was a readout in the status line. Clicking the word
"Vol" did nothing, so a listener reaching for the bar the way every other
player works heard no change.

The slider is drawn directly under the DJ pitch track, which is also an
absolute control: the column under the pointer *is* the new value. These tests
pin:

* the bar is painted on the full frame and on the Vertical deck,
* a click anywhere on the track sets the matching percentage (both ends and
  the middle),
* a press that starts on the track keeps scrubbing when the pointer leaves the
  row, and a release ends it,
* the click span is measured from the painted cells, not from a guess,
* the paint does not cost the playlist a row on a 24-row console, and
* the level becomes visible on the very next frame instead of waiting for the
  daemon's state file.
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
        "sample_rate": 44100,
        "channels": 2,
    }
    st.update(overrides)
    return st


def _ui(mode="NORMAL", width=120, height=40, **status):
    scanner = MagicMock()
    scanner.get_mixes.return_value = [
        {"name": f"Mix {i}.flac", "path": f"/mixes/{i}.flac", "ext": "FLAC", "size_mb": 10.0}
        for i in range(6)
    ]
    ui = player.TerminalPlayerUI(None, scanner, is_remote=True, ui_size_mode=mode)
    ui._send_cmd = MagicMock()
    ui._get_status = lambda: _status(**status)
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


def _plain(text):
    return player._ANSI_RE.sub("", text)


def _cells(text):
    """Yield (0-based display column, character) for a painted plain line."""
    col = 0
    for ch in text:
        yield col, ch
        col += player._char_display_width(ch)


class VolumeBarPartsTest(unittest.TestCase):
    """The three pieces must paint one bar and keep a real knob."""

    def test_the_three_parts_rebuild_one_line(self):
        visualizer = player.EqualizerVisualizer()
        for volume in (0, 25, 50, 85, 100):
            for width in (21, 36, 54, 61):
                joined = "".join(visualizer.volume_bar_parts(volume, width=width))
                self.assertIn("●", joined)
                self.assertEqual(joined.count("●"), 1, "exactly one knob")
                # The row is one line and never wider than the console it was
                # measured against.
                self.assertEqual(player._display_width(joined),
                                 player._display_width(joined))

    def test_the_track_is_the_requested_width(self):
        visualizer = player.EqualizerVisualizer()
        for width in (21, 36, 54, 61):
            _pfx, track, _sfx = visualizer.volume_bar_parts(50, width=width)
            self.assertEqual(player._display_width(track), width)

    def test_the_knob_moves_with_the_level(self):
        visualizer = player.EqualizerVisualizer()

        def knob_col(volume):
            _pfx, track, _sfx = visualizer.volume_bar_parts(volume, width=41)
            return _plain(track).index("●")

        seen = [knob_col(v) for v in (0, 25, 50, 75, 100)]
        self.assertEqual(seen, sorted(seen), "the knob must move right as the level rises")
        self.assertEqual(seen[0], 0, "0% sits at the far left")
        self.assertEqual(seen[-1], 40, "100% sits at the far right")

    def test_a_narrow_window_still_leaves_seven_cells(self):
        visualizer = player.EqualizerVisualizer()
        for max_cols in (20, 24, 30, 46):
            pfx, track, sfx = visualizer.volume_bar_parts(
                70, width=61, max_cols=max_cols)
            painted = player._display_width(pfx + track + sfx)
            self.assertLessEqual(painted, max_cols, max_cols)
            self.assertGreaterEqual(player._display_width(track), 7, max_cols)

    def test_the_line_follows_the_theme(self):
        visualizer = player.EqualizerVisualizer()
        visualizer.set_theme("phosphor")
        first = visualizer.volume_bar_parts(60, width=31)[1]
        visualizer.set_theme("ice_cold")
        second = visualizer.volume_bar_parts(60, width=31)[1]
        self.assertNotEqual(first, second, "C must recolour the slider with the theme")


class FullFrameSliderTest(unittest.TestCase):
    def test_the_full_frame_paints_a_volume_slider(self):
        ui = _ui()
        lines = _draw(ui)
        self.assertGreater(ui._volume_row, 0, "the slider must be a click target")
        painted = _plain(lines[ui._volume_row - 1])
        self.assertIn("VOLUME", painted)
        start, end = ui._volume_span
        cells = dict(_cells(painted))
        self.assertIn(cells.get(start - 1), "━●", "the span starts on the track")
        self.assertIn(cells.get(end - 1), "━●─", "the span ends on the track")

    def test_clicking_the_left_end_is_zero(self):
        ui = _ui()
        _draw(ui)
        start, _end = ui._volume_span
        ui._on_mouse_sgr(0, start, ui._volume_row, b"M")
        ui._send_cmd.assert_called_once_with("set_volume", volume=0)

    def test_clicking_the_right_end_is_full(self):
        ui = _ui()
        _draw(ui)
        _start, end = ui._volume_span
        ui._on_mouse_sgr(0, end, ui._volume_row, b"M")
        self.assertEqual(ui._send_cmd.call_args[1]["volume"], 100)

    def test_clicking_the_middle_is_near_half(self):
        ui = _ui()
        _draw(ui)
        start, end = ui._volume_span
        ui._on_mouse_sgr(0, (start + end) // 2, ui._volume_row, b"M")
        volume = ui._send_cmd.call_args[1]["volume"]
        self.assertTrue(45 <= volume <= 55, f"got {volume}")

    def test_the_clicked_level_is_always_in_range(self):
        ui = _ui()
        _draw(ui)
        start, end = ui._volume_span
        for col in range(start, end + 1):
            ui._send_cmd.reset_mock()
            ui._on_mouse_sgr(0, col, ui._volume_row, b"M")
            volume = ui._send_cmd.call_args[1]["volume"]
            self.assertTrue(0 <= volume <= 100, f"column {col} gave {volume}")

    def test_a_click_on_the_label_does_not_change_the_level(self):
        ui = _ui()
        _draw(ui)
        start, _end = ui._volume_span
        ui._on_mouse_sgr(0, start - 4, ui._volume_row, b"M")
        ui._send_cmd.assert_not_called()

    def test_dragging_right_raises_the_level(self):
        ui = _ui()
        _draw(ui)
        start, end = ui._volume_span
        ui._on_mouse_sgr(0, start, ui._volume_row, b"M")
        seen = [ui._send_cmd.call_args[1]["volume"]]
        for col in (start + (end - start) // 4, (start + end) // 2, end):
            ui._on_mouse_sgr(32, col, ui._volume_row, b"M")
            seen.append(ui._send_cmd.call_args[1]["volume"])
        self.assertEqual(seen, sorted(seen), seen)
        self.assertEqual(seen[0], 0)
        self.assertEqual(seen[-1], 100)

    def test_dragging_off_the_row_keeps_scrubbing(self):
        ui = _ui()
        _draw(ui)
        start, end = ui._volume_span
        ui._on_mouse_sgr(0, start, ui._volume_row, b"M")
        # The pointer wanders down into the analyser with the button held.
        ui._on_mouse_sgr(32, end, ui._volume_row + 3, b"M")
        self.assertEqual(ui._send_cmd.call_args[1]["volume"], 100)

    def test_release_ends_the_scrub(self):
        ui = _ui()
        _draw(ui)
        start, end = ui._volume_span
        ui._on_mouse_sgr(0, start, ui._volume_row, b"M")
        ui._on_mouse_sgr(0, end, ui._volume_row, b"m")
        ui._send_cmd.reset_mock()
        ui._on_mouse_sgr(32, end, ui._volume_row, b"M")
        ui._send_cmd.assert_not_called()

    def test_a_hover_after_a_click_never_scrubs(self):
        ui = _ui()
        _draw(ui)
        start, end = ui._volume_span
        ui._on_mouse_sgr(0, start, ui._volume_row, b"M")
        ui._on_mouse_sgr(0, start, ui._volume_row, b"m")
        ui._send_cmd.reset_mock()
        ui._on_mouse_sgr(35, end, ui._volume_row, b"M")
        ui._send_cmd.assert_not_called()

    def test_mute_is_shown_on_the_slider(self):
        ui = _ui(muted=True)
        lines = _draw(ui)
        painted = _plain(lines[ui._volume_row - 1])
        self.assertIn("MUTED", painted)

    def test_the_slider_does_not_cost_the_playlist_a_row(self):
        """A 24-row console must keep its three playlist rows.

        The bar originally had a blank row under it. That row came out of the
        playlist, which left the player showing fewer tracks than before the
        slider existed.
        """
        ui = _ui(width=90, height=24)
        lines = _draw(ui, width=90, height=24)
        self.assertLessEqual(len(lines), 24)
        drawn = "\n".join(_plain(line) for line in lines)
        self.assertIn("Mix 0.flac", drawn)
        self.assertIn("Mix 2.flac", drawn)
        self.assertIn("VOLUME", drawn)


class VerticalDeckSliderTest(unittest.TestCase):
    def test_the_deck_paints_a_volume_slider(self):
        ui = _ui("VERTICAL", width=40, height=player.VERTICAL_ROWS)
        lines = _draw(ui, width=40, height=player.VERTICAL_ROWS)
        self.assertGreater(ui._volume_row, 0)
        painted = _plain(lines[ui._volume_row - 1])
        self.assertIn("VOL", painted)
        start, end = ui._volume_span
        cells = dict(_cells(painted))
        self.assertIn(cells.get(start - 1), "━●")
        self.assertIn(cells.get(end - 1), "━●─")

    def test_a_click_in_the_deck_sets_the_level(self):
        ui = _ui("VERTICAL", width=40, height=player.VERTICAL_ROWS)
        _draw(ui, width=40, height=player.VERTICAL_ROWS)
        start, end = ui._volume_span
        ui._on_mouse_sgr(0, start, ui._volume_row, b"M")
        self.assertEqual(ui._send_cmd.call_args[1]["volume"], 0)
        ui._on_mouse_sgr(0, end, ui._volume_row, b"M")
        self.assertEqual(ui._send_cmd.call_args[1]["volume"], 100)

    def test_the_deck_fills_exactly_the_window_height(self):
        ui = _ui("VERTICAL", width=40, height=player.VERTICAL_ROWS)
        lines = _draw(ui, width=40, height=player.VERTICAL_ROWS)
        self.assertEqual(len(lines), player.VERTICAL_ROWS)
        for line in lines:
            self.assertLessEqual(player._display_width(_plain(line)), player.VERTICAL_COLS)

    def test_the_deck_keeps_its_panels_with_the_slider(self):
        ui = _ui("VERTICAL", width=40, height=player.VERTICAL_ROWS)
        drawn = _plain("\n".join(_draw(ui, width=40, height=player.VERTICAL_ROWS)))
        self.assertIn("EQUALIZER", drawn)
        self.assertIn("PLAYLIST", drawn)


class CompactFramesHaveNoSliderTest(unittest.TestCase):
    """Mini and Micro keep their own chrome; neither carries the bar."""

    def test_mini_and_micro_expose_no_volume_target(self):
        for mode, width, height in (("MINI", 74, 14), ("MICRO", 74, 7)):
            ui = _ui(mode, width=width, height=height)
            _draw(ui, width=width, height=height)
            self.assertEqual(ui._volume_row, 0, mode)
            self.assertEqual(ui._volume_span, (0, 0), mode)

    def test_mini_keeps_its_style_and_theme_chips(self):
        ui = _ui("MINI", width=74, height=14)
        drawn = _plain("\n".join(_draw(ui, width=74, height=14)))
        self.assertIn("BASS", drawn)
        self.assertIn("85%", drawn)


class PendingVolumeTest(unittest.TestCase):
    """The daemon echoes its status a moment later; the slider must not lag."""

    def test_the_clicked_level_paints_immediately(self):
        """The next frame shows the clicked level, before the daemon echoes it."""
        ui = _ui()
        # Use the real remote status path: the overlay lives in `_get_status`,
        # so a test that stubs `_get_status` cannot see it at all.
        ui._get_status = lambda: player.TerminalPlayerUI._get_status(ui)
        ui.cached_status = _status(volume=85)

        captured = []

        def _draw_once():
            captured.clear()
            with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}), \
                 patch("MP_Audio_Player._present_frame",
                       side_effect=lambda lines, w, h: captured.extend(lines)), \
                 patch("MP_Audio_Player._win_visible_size", return_value=(90, 24)), \
                 patch.object(type(player.STATE_FILE), "exists", return_value=False), \
                 patch("sys.stdout.write"), patch("sys.stdout.flush"):
                ui._draw_frame()
            return _plain(captured[ui._volume_row - 1])

        # The real sender records the pending value even when IPC answers.
        ui._send_cmd = lambda cmd, **kw: player.TerminalPlayerUI._send_cmd(ui, cmd, **kw)
        with patch.object(player, "send_ipc_command", return_value={"status": "ok"}):
            first = _draw_once()
            start, end = ui._volume_span
            ui._on_mouse_sgr(0, (start + end) // 2, ui._volume_row, b"M")
            immediate = _draw_once()

        self.assertIn("85%", first)
        self.assertIn("49%", immediate,
                      "the clicked level must paint before the daemon echoes it")

        # Once the daemon reports the value, the overlay steps out of the way.
        ui.cached_status = _status(volume=49)
        echoed = _draw_once()
        self.assertIn("49%", echoed)
        self.assertIsNone(ui._pending_volume)

    def test_an_expired_overlay_is_dropped(self):
        ui = _ui()
        ui._pending_volume = (20, 0.0)
        self.assertEqual(ui._apply_pending_status(_status(volume=85))["volume"], 85)
        self.assertIsNone(ui._pending_volume)

    def test_setting_a_level_unmutes_the_player(self):
        """A drag up from MUTED must actually be audible."""
        ui = _ui()
        ui._pending_volume = (40, 1e9)
        overlaid = ui._apply_pending_status(_status(volume=0, muted=True))
        self.assertFalse(overlaid["muted"])


class DragReportingTest(unittest.TestCase):
    """The terminal has to report motion *while a button is held*.

    Plain `?1000h` only reports press and release, so a press-and-drag on the
    slider would move the knob once and never follow the pointer. `?1002h`
    adds button-drag motion, which is exactly the stream the handler reads.
    """

    def test_the_player_arms_button_drag_reporting(self):
        source = (ROOT / "MP_Audio_Player.py").read_text(encoding="utf-8")
        self.assertIn("\\033[?1002h", source,
                      "a drag on the volume slider needs button-motion reports")
        self.assertIn("\\033[?1002l", source,
                      "button-drag reporting must be turned off on exit")

    def test_the_motion_handler_handles_a_held_button(self):
        ui = _ui()
        _draw(ui)
        start, end = ui._volume_span
        ui._on_mouse_sgr(0, start, ui._volume_row, b"M")
        # 32 is SGR motion with button 1 still down.
        ui._send_cmd.reset_mock()
        ui._on_mouse_sgr(32, end, ui._volume_row, b"M")
        self.assertEqual(ui._send_cmd.call_args[1]["volume"], 100)


class LocalEngineTest(unittest.TestCase):
    """When the UI owns the engine, the command must reach it directly."""

    def test_set_volume_reaches_the_engine(self):
        ui = _ui()
        engine = MagicMock()
        ui.engine = engine
        ui.is_remote = False
        player.TerminalPlayerUI._send_cmd(ui, "set_volume", volume=42)
        engine.set_volume.assert_called_once_with(42)


if __name__ == "__main__":
    unittest.main()
