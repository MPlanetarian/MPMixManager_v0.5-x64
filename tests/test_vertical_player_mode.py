#!/usr/bin/env python3
"""
Unit tests for MP Audio Player VERTICAL mode:
- M cycles Full -> Vertical -> Mini -> Micro -> Full in the glass window
- A compact window walks the compact ring (Vertical, Mini, Micro) and never
  returns to the full frame, because it has no full frame to draw
- The tall deck renders with a block clock, a seek bar and the analyser
- The glass hosts resize to the published VERTICAL grid, and their panel
  rectangles stay inside the window
- The published size file accepts VERTICAL, so the host follows it
"""

import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import MP_Audio_Player
import MP_Audio_Player_Liquid_Glass as lg_vulkan
import MP_Audio_Player_Liquid_Glass_OpenGL as lg_opengl
from liquid_ui_size import UI_SIZES, publish_ui_size, read_ui_size

FLAG_DIR = Path.home() / ".config" / "mix-manager"


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def drawn_lines(ui, width=None, height=None):
    """The deck's frame, captured instead of painted."""
    width = width or MP_Audio_Player.VERTICAL_COLS
    height = height or MP_Audio_Player.VERTICAL_ROWS
    captured = []

    def _fake_present(lines, _w, _h):
        captured.extend(strip_ansi(line) for line in lines)

    with patch.object(MP_Audio_Player, "_present_frame", side_effect=_fake_present), \
            patch.object(MP_Audio_Player, "_win_visible_size", return_value=(width, height)), \
            patch.object(MP_Audio_Player, "_set_terminal_title"), \
            patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}, clear=False):
        ui.ui_size_mode = "VERTICAL"
        ui._draw_frame()
    return captured


class TestVerticalPlayerMode(unittest.TestCase):
    def setUp(self):
        self.flag = FLAG_DIR / f"liquid_ui_size.{os.getpid()}"
        self.flag.unlink(missing_ok=True)
        self.scanner = MagicMock()
        self.scanner.get_mixes.return_value = []

    def tearDown(self):
        self.flag.unlink(missing_ok=True)

    def _ui(self, size="NORMAL"):
        return MP_Audio_Player.TerminalPlayerUI(
            engine=None, scanner=self.scanner, is_remote=True, ui_size_mode=size
        )

    def test_m_walks_vertical_before_mini(self):
        """M steps Full -> Vertical -> Mini -> Micro -> Full, and never skips the deck."""
        ui = self._ui()
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}), \
                patch("MP_Audio_Player.launch_mini_player_window") as launch:
            seen = []
            for _ in range(4):
                ui._process_key(b"m")
                seen.append(ui.ui_size_mode)
        self.assertEqual(seen, ["VERTICAL", "MINI", "MICRO", "NORMAL"])
        launch.assert_not_called()

    def test_compact_ring_wraps_to_vertical(self):
        """A compact window never steps to the full frame it cannot draw."""
        for start in ("VERTICAL", "MINI", "MICRO"):
            ui = self._ui(start)
            with patch("MP_Audio_Player.launch_mini_player_window") as launch, \
                    patch("MP_Audio_Player.resize_host_terminal") as resize:
                sizes = []
                for _ in range(3):
                    ui._process_key(b"m")
                    sizes.append(ui.ui_size_mode)
            self.assertNotIn("NORMAL", sizes, f"{start} must stay compact")
            self.assertEqual(set(sizes), {"VERTICAL", "MINI", "MICRO"}, start)
            self.assertNotIn("NORMAL", str(resize.call_args), start)
            launch.assert_not_called()

    def test_step_helper_agrees_with_the_key(self):
        self.assertEqual(MP_Audio_Player.ui_size_step("NORMAL"), "VERTICAL")
        self.assertEqual(MP_Audio_Player.ui_size_step("VERTICAL"), "MINI")
        self.assertEqual(MP_Audio_Player.ui_size_step("MINI"), "MICRO")
        self.assertEqual(MP_Audio_Player.ui_size_step("MICRO"), "NORMAL")
        # Unknown input starts the ring at the top instead of raising.
        self.assertEqual(MP_Audio_Player.ui_size_step("nonsense"), "VERTICAL")
        # Without a full frame the last strip wraps to the deck.
        self.assertEqual(MP_Audio_Player.ui_size_step("MICRO", allow_normal=False), "VERTICAL")

    def test_vertical_grid_is_taller_than_it_is_wide(self):
        cols, rows = MP_Audio_Player.ui_size_grid("VERTICAL")
        self.assertEqual((cols, rows), (MP_Audio_Player.VERTICAL_COLS, MP_Audio_Player.VERTICAL_ROWS))
        # At the 9x18 cell the glass window is portrait: taller than it is wide.
        self.assertGreater(rows * 18, cols * 9)
        self.assertEqual(MP_Audio_Player.ui_size_grid("MINI"), (74, 14))
        self.assertEqual(MP_Audio_Player.ui_size_grid("MICRO"), (74, 7))

    def test_clock_rows_use_block_digits(self):
        rows = MP_Audio_Player.lcd_clock_rows("12:34", "\033[38;2;0;229;255m")
        self.assertEqual(len(rows), 5)
        plain = [strip_ansi(row) for row in rows]
        # Five glyphs of three cells, one separating space between each.
        self.assertTrue(all(len(row) == 5 * 3 + 4 for row in plain), plain)
        # "12:34" row by row, straight from the glyph table.
        self.assertEqual(plain[0], "  █ ███     ███ █ █")
        self.assertEqual(plain[2], "  █ ███     ███ ███")
        self.assertNotIn("1", plain[0], "digits must be drawn as blocks")

    def test_deck_frame_draws_clock_progress_and_analyser(self):
        st = {
            "title": "Stream of Frequency 165", "path": "/mixes/sof165.flac",
            "state": "playing", "sample_rate": 96000, "channels": 2,
            "position_fmt": "12:34", "duration_fmt": "59:59", "progress_pct": 42.0,
            "volume": 85, "pitch_percent": 0, "levels": [0.4] * 24,
        }
        ui = self._ui()
        with patch.object(ui, "_get_status", return_value=st):
            lines = drawn_lines(ui)
        joined = "\n".join(lines)
        self.assertIn("MP VERTICAL PLAYER", joined)
        self.assertIn("Stream of Frequency 165", joined)
        self.assertIn("███", joined, "the block clock must be drawn")
        self.assertIn("42%", joined)
        self.assertIn("[M] Mini", joined, "the deck must say what M does next")

    def test_deck_stacks_main_window_equaliser_and_playlist(self):
        """The three panels sit in order: player on top, EQ under it, playlist last."""
        mix_list = [{"name": f"Mix {n:02d}.flac", "path": f"/mixes/{n}.flac", "ext": "FLAC"}
                    for n in range(1, 13)]
        scanner = MagicMock()
        scanner.get_mixes.return_value = mix_list
        ui = MP_Audio_Player.TerminalPlayerUI(
            engine=None, scanner=scanner, is_remote=True, ui_size_mode="VERTICAL")
        st = {"title": "Mix 03", "path": "/mixes/3.flac", "position_fmt": "01:00",
              "duration_fmt": "02:00", "progress_pct": 50.0,
              "levels": [0.3] * 24, "peaks": [0.7] * 24}
        with patch.object(ui, "_get_status", return_value=st):
            lines = drawn_lines(ui, height=MP_Audio_Player.VERTICAL_ROWS)
        joined = "\n".join(lines)
        clock_at = joined.index("███")
        eq_at = joined.index("EQUALIZER")
        list_at = joined.index("PLAYLIST")
        self.assertLess(clock_at, eq_at, "the main window must be on top")
        self.assertLess(eq_at, list_at, "the equaliser must sit above the playlist")
        self.assertIn("Mix 03.flac", joined, "the playlist must list the tracks")
        # The panels are the three named ones, in that order, exactly once each.
        self.assertEqual(joined.count("EQUALIZER"), 1)
        self.assertEqual(joined.count("PLAYLIST"), 1)

    def test_equaliser_edit_survives_the_daemon_status(self):
        """A band set with the arrows must not be erased by the next draw.

        The status file lags a frame behind the IPC write, so adopting it
        unconditionally made the deck equaliser look frozen: the value moved
        for one frame and the stale one came straight back.
        """
        ui = self._ui()
        ui.ui_size_mode = "VERTICAL"
        ui.eq_manager.current_bands = [0.0] * 10
        stale = {"eq_preset": "Flat", "eq_bands": [0.0] * 10,
                 "levels": [0.2] * 24, "peaks": [0.5] * 24}
        with patch.object(ui, "_send_cmd"), patch.object(ui, "_get_status", return_value=stale):
            ui._process_key(b"E")
            ui._process_key(b"\x1b[A")
            ui._process_key(b"\x1b[A")
            self.assertEqual(ui.eq_manager.current_bands[0], 1.0)
            # Every frame re-syncs from the daemon; the edit must survive.
            for _ in range(3):
                ui._get_status()
                self.assertEqual(ui.eq_manager.current_bands[0], 1.0,
                                 "the daemon's stale bands erased a local edit")
        # Once the daemon echoes the value, an external change lands again.
        ui._sync_eq_from_status({"eq_preset": "Trance", "eq_bands": [1.0] + [0.0] * 9})
        ui._sync_eq_from_status({"eq_preset": "Trance", "eq_bands": [-4.0] + [0.0] * 9})
        self.assertEqual(ui.eq_manager.current_bands[0], -4.0)

    def test_equaliser_focus_is_visible_in_the_panel_title(self):
        """The panel says whether the arrows drive the sliders or the tracks."""
        scanner = MagicMock()
        scanner.get_mixes.return_value = [{"name": "Mix 1", "path": "/m1.flac", "ext": "FLAC"}]
        ui = MP_Audio_Player.TerminalPlayerUI(
            engine=None, scanner=scanner, is_remote=True, ui_size_mode="VERTICAL")
        st = {"path": "/m1.flac", "position_fmt": "00:10", "levels": [0.2] * 24,
              "peaks": [0.5] * 24}
        with patch.object(ui, "_get_status", return_value=st):
            self.assertIn("TRACKS", "\n".join(drawn_lines(ui)))
            ui._vertical_eq_focus = True
            self.assertIn("SLIDERS", "\n".join(drawn_lines(ui)))

    def test_equaliser_takes_the_arrow_keys_when_it_has_focus(self):
        """E hands the arrows to the EQ sliders; a second press gives them back."""
        ui = self._ui()
        ui.ui_size_mode = "VERTICAL"
        ui.eq_manager.current_bands = [0.0] * 10
        with patch("MP_Audio_Player._present_frame"), patch.object(ui, "_get_status",
                  return_value={"levels": [0.2] * 24, "peaks": [0.5] * 24}):
            ui._process_key(b"E")
            self.assertTrue(ui._vertical_eq_focus)
            ui._process_key(b"\x1b[C")
            self.assertEqual(ui.eq_selected_band, 1)
            ui._process_key(b"\x1b[A")
            self.assertEqual(ui.eq_manager.current_bands[1], 0.5)
            ui._process_key(b"\x1b[B")
            ui._process_key(b"\x1b[B")
            self.assertEqual(ui.eq_manager.current_bands[1], -0.5)
            # The playlist cursor must not move while the EQ has the keys.
            ui._process_key(b"E")
            self.assertFalse(ui._vertical_eq_focus)

    def test_deck_equaliser_never_opens_the_full_width_hud(self):
        """A 40-column deck cannot draw the full-screen HUD, so E never opens it."""
        ui = self._ui()
        ui.ui_size_mode = "VERTICAL"
        with patch("MP_Audio_Player._present_frame"), patch.object(ui, "_get_status",
                  return_value={"levels": [0.2] * 24, "peaks": [0.5] * 24}):
            ui._process_key(b"E")
        self.assertFalse(ui.equalizer_hud_active)

    def test_deck_fills_exactly_the_window_height(self):
        """Every panel fits: the frame is neither short nor clipped."""
        scanner = MagicMock()
        scanner.get_mixes.return_value = [
            {"name": f"Mix {n}", "path": f"/m{n}.flac", "ext": "FLAC"} for n in range(6)]
        ui = MP_Audio_Player.TerminalPlayerUI(
            engine=None, scanner=scanner, is_remote=True, ui_size_mode="VERTICAL")
        st = {"path": "/m1.flac", "position_fmt": "00:30", "levels": [0.2] * 24,
              "peaks": [0.5] * 24}
        with patch.object(ui, "_get_status", return_value=st):
            lines = drawn_lines(ui, height=MP_Audio_Player.VERTICAL_ROWS)
        self.assertEqual(len(lines), MP_Audio_Player.VERTICAL_ROWS)
        for line in lines:
            self.assertLessEqual(len(line), MP_Audio_Player.VERTICAL_COLS, line)

    def test_deck_keeps_all_three_panels_when_short(self):
        """A short window shrinks the panels; it never drops one or overflows."""
        scanner = MagicMock()
        scanner.get_mixes.return_value = [
            {"name": f"Mix {n}", "path": f"/m{n}.flac", "ext": "FLAC"} for n in range(20)]
        st = {"path": "/m9.flac", "position_fmt": "01:00",
              "levels": [0.4] * 24, "peaks": [0.8] * 24}
        for height in (44, 40, 34, 28, 24, 20, 16):
            ui = MP_Audio_Player.TerminalPlayerUI(
                engine=None, scanner=scanner, is_remote=True, ui_size_mode="VERTICAL")
            with patch.object(ui, "_get_status", return_value=st):
                lines = drawn_lines(ui, height=height)
            joined = "\n".join(lines)
            self.assertEqual(len(lines), height, height)
            self.assertIn("EQUALIZER", joined, f"equaliser lost at {height} rows")
            self.assertIn("PLAYLIST", joined, f"playlist lost at {height} rows")
            for line in lines:
                self.assertLessEqual(len(line), MP_Audio_Player.VERTICAL_COLS, height)

    def test_deck_seek_bar_is_a_click_target(self):
        st = {"title": "t", "path": "/a.flac", "position_fmt": "01:00",
              "duration_fmt": "02:00", "progress_pct": 50.0, "levels": [0.2] * 24}
        ui = self._ui()
        with patch.object(ui, "_get_status", return_value=st):
            drawn_lines(ui)
        row, (start, end) = ui._timeline_row, ui._timeline_span
        self.assertGreater(row, 0)
        self.assertLess(start, end)
        self.assertLessEqual(end, MP_Audio_Player.VERTICAL_COLS)

    def test_all_eq_styles_render_in_vertical_mode(self):
        """Every equaliser style fits the deck, including the wide stereo ones."""
        ui = self._ui()
        st = {"title": "t", "path": "/a.flac", "position_fmt": "01:00",
              "duration_fmt": "02:00", "progress_pct": 50.0,
              "levels": [0.4] * 24, "peaks": [0.8] * 24, "state": "playing"}
        for style in MP_Audio_Player.EqualizerVisualizer.STYLES:
            ui.visualizer.set_style(style)
            self.assertEqual(ui.visualizer.style, style)
            with patch.object(ui, "_get_status", return_value=st):
                lines = drawn_lines(ui)
            self.assertTrue(lines, style)
            self.assertLessEqual(len(lines), MP_Audio_Player.VERTICAL_ROWS, style)
            for line in lines:
                self.assertLessEqual(len(line), MP_Audio_Player.VERTICAL_COLS, style)

    def test_wide_stereo_styles_use_the_portrait_spectrum(self):
        """The fixed two-channel blocks are replaced, not clipped to their left edge."""
        st = {"levels": [0.1 * (i % 9) for i in range(24)], "peaks": [0.9] * 24}
        rows = MP_Audio_Player.vertical_spectrum_rows(st, 36, 12)
        self.assertEqual(len(rows), 12)
        plain = [strip_ansi(row) for row in rows]
        self.assertTrue(all(len(row) <= 36 for row in plain), plain)
        self.assertIn("█", "\n".join(plain), "the portrait spectrum must draw bars")
        self.assertNotIn("CHANNEL", "\n".join(plain))
        self.assertIn("neon_prism_eq", MP_Audio_Player.WIDE_STEREO_STYLES)
        self.assertIn("master_spectrum", MP_Audio_Player.WIDE_STEREO_STYLES)

    def test_published_size_accepts_vertical(self):
        self.assertIn("VERTICAL", UI_SIZES)
        publish_ui_size(FLAG_DIR, 987654, "VERTICAL")
        self.assertEqual(read_ui_size(FLAG_DIR, 987654), "VERTICAL")
        (FLAG_DIR / "liquid_ui_size.987654").unlink(missing_ok=True)

    def test_hosts_resize_to_the_vertical_grid(self):
        """Both glass hosts adopt the deck's grid and keep their panels inside it."""
        for mod in (lg_vulkan, lg_opengl):
            host = mod.GlassHost(["test"], mode="NORMAL")
            host.renderer = MagicMock()
            host.renderer.bezel_px = 7
            host._place_terminal = MagicMock()
            host.set_mode("VERTICAL")
            self.assertEqual(host.mode, "VERTICAL")
            self.assertEqual((host._target_cols, host._target_rows),
                             (MP_Audio_Player.VERTICAL_COLS, MP_Audio_Player.VERTICAL_ROWS))
            host.terminal = MagicMock()
            host.terminal.get_char_width.return_value = 9
            host.terminal.get_char_height.return_value = 18
            host.set_mode("VERTICAL")
            host.terminal.set_size.assert_called_with(
                MP_Audio_Player.VERTICAL_COLS, MP_Audio_Player.VERTICAL_ROWS
            )
            panels = mod.player_panel_rects(
                MP_Audio_Player.VERTICAL_COLS, MP_Audio_Player.VERTICAL_ROWS, 9, 18, 0, 0, mode="VERTICAL"
            )
            for rect in panels:
                x, y, w, h = rect
                self.assertGreaterEqual(x, 0)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(x + w, MP_Audio_Player.VERTICAL_COLS * 9)
                self.assertLessEqual(y + h, MP_Audio_Player.VERTICAL_ROWS * 18)
            # The main-window panel starts at the top, the lower panel carries
            # the equaliser and the playlist beneath it.
            header, body = panels
            self.assertEqual(header[1], 0)
            self.assertEqual(body[1], header[1] + header[3])
            self.assertGreater(body[3], 0)
            # The panels must stay inside the window at every deck height, not
            # just the default one. A hardcoded 23-row header overflowed as
            # soon as the deck was shortened.
            for rows in (44, 40, 30, 20, 12):
                top, bottom = mod.player_panel_rects(
                    40, rows, 9, 18, 7, 7, mode="VERTICAL")
                self.assertEqual(top[1], 7)
                self.assertAlmostEqual(top[1] + top[3], bottom[1])
                self.assertLessEqual(bottom[1] + bottom[3], rows * 18 + 7 + 0.5, rows)
                self.assertGreaterEqual(top[3], 9 * 18, rows)

    def test_host_launcher_forwards_the_vertical_flag(self):
        for mod in (lg_vulkan, lg_opengl):
            argv = mod.player_argv(mode="VERTICAL")
            self.assertIn("--vertical", argv, mod.__name__)
            self.assertNotIn("--mini", argv, mod.__name__)

    def test_vertical_is_a_player_mode_for_the_intro_video(self):
        """The deck is a player window, so the intro election treats it as one."""
        from exit_video import PLAYER_MODES, intro_role
        self.assertIn("VERTICAL", PLAYER_MODES)
        self.assertEqual(intro_role("VERTICAL"), "player")


if __name__ == "__main__":
    unittest.main()
