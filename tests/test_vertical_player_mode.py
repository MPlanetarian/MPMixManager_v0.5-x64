#!/usr/bin/env python3
"""
Unit tests for MP Audio Player VERTICAL mode.

Vertical is not a separate player. It is the MP Liquid Player's own frame drawn
into a portrait column: the same header block, the same playing line, the same
timeline / DJ pitch / volume sliders, the same analyser, the same action row, the
same playlist and the same two footers. These tests pin that sameness, and the
narrow-width behaviour that makes it fit 56 columns.
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


def frame(size="VERTICAL", width=None, height=None, mixes=12, title="Stream of Frequency 165"):
    """The player's frame for one size, captured instead of painted."""
    scanner = MagicMock()
    scanner.get_mixes.return_value = [
        {"name": f"Stream_of_Frequency_{n:03d}_THE_CELEBRATION.flac",
         "path": f"/mixes/{n}.flac", "ext": "FLAC", "size_mb": 650}
        for n in range(1, mixes + 1)
    ]
    ui = MP_Audio_Player.TerminalPlayerUI(
        engine=None, scanner=scanner, is_remote=True, ui_size_mode=size)
    width = width or (56 if size == "VERTICAL" else 120)
    height = height or (52 if size == "VERTICAL" else 40)
    status = {
        "title": title, "path": "/mixes/4.flac", "state": "playing",
        "sample_rate": 96000, "channels": 2,
        "position_fmt": "42:34", "duration_fmt": "59:59", "progress_pct": 42.0,
        "volume": 85, "pitch_percent": 0,
        "levels": [0.05, 0.2, 0.45, 0.7, 0.9, 0.6, 0.3, 0.8,
                   0.5, 0.2, 0.9, 0.4, 0.7, 0.3, 0.6, 0.9,
                   0.2, 0.5, 0.8, 0.3, 0.6, 0.4, 0.7, 0.5],
        "peaks": [0.85] * 24,
    }
    captured = []
    with patch.object(MP_Audio_Player, "_present_frame",
                      side_effect=lambda lines, w, h: captured.extend(lines)), \
            patch.object(MP_Audio_Player, "_win_visible_size", return_value=(width, height)), \
            patch.object(MP_Audio_Player, "_set_terminal_title"), \
            patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}, clear=False), \
            patch.object(ui, "_get_status", return_value=status):
        ui._draw_frame()
    return ui, [strip_ansi(line) for line in captured]


class TestVerticalPlayerMode(unittest.TestCase):
    def setUp(self):
        self.flag = FLAG_DIR / f"liquid_ui_size.{os.getpid()}"
        self.flag.unlink(missing_ok=True)

    def tearDown(self):
        self.flag.unlink(missing_ok=True)

    def _ui(self, size="NORMAL"):
        return MP_Audio_Player.TerminalPlayerUI(
            engine=None, scanner=MagicMock(), is_remote=True, ui_size_mode=size)

    # --- the M ring -------------------------------------------------------

    def test_m_walks_vertical_before_mini(self):
        ui = self._ui()
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}), \
                patch("MP_Audio_Player.launch_mini_player_window") as launch:
            seen = []
            for _ in range(5):
                ui._process_key(b"m")
                seen.append(ui.ui_size_mode)
        # The ring closes: portrait, the two strips, then the full frame.
        self.assertEqual(seen, ["VERTICAL", "MINI", "MICRO", "NORMAL", "VERTICAL"])
        launch.assert_not_called()

    def test_the_ring_always_closes(self):
        """M from any size reaches NORMAL again within four presses."""
        self.addCleanup(os.environ.pop, "MP_LIQUID_GLASS", None)
        os.environ["MP_LIQUID_GLASS"] = "1"
        for start in ("MINI", "MICRO"):
            ui = self._ui(start)
            with patch("MP_Audio_Player.launch_mini_player_window") as launch, \
                    patch("MP_Audio_Player.resize_host_terminal") as resize:
                sizes = []
                for _ in range(4):
                    ui._process_key(b"m")
                    sizes.append(ui.ui_size_mode)
            launch.assert_not_called()
            self.assertIn("NORMAL", sizes,
                          f"from {start} the ring must come back to NORMAL")

    def test_step_helper(self):
        self.assertEqual(MP_Audio_Player.ui_size_step("NORMAL"), "VERTICAL")
        self.assertEqual(MP_Audio_Player.ui_size_step("VERTICAL"), "MINI")
        self.assertEqual(MP_Audio_Player.ui_size_step("MINI"), "MICRO")
        self.assertEqual(MP_Audio_Player.ui_size_step("MICRO"), "NORMAL")
        # `allow_normal=False` must still close the ring: making NORMAL
        # unreachable left the window stuck in the compact sizes.
        self.assertEqual(MP_Audio_Player.ui_size_step("MICRO", allow_normal=False), "NORMAL")

    # --- the geometry -----------------------------------------------------

    def test_vertical_is_the_normal_window_turned_portrait(self):
        """Same shape as NORMAL, rotated: use the same aspect, not a new one."""
        cols, rows = MP_Audio_Player.ui_size_grid("VERTICAL")
        self.assertEqual((cols, rows), (MP_Audio_Player.VERTICAL_COLS, MP_Audio_Player.VERTICAL_ROWS))
        # Portrait on the glass.
        self.assertGreater(rows * 18, cols * 9)
        # The NORMAL window is 120x40. Vertical should be recognisably the same
        # player: a comparable cell count, not a cramped strip.
        self.assertGreaterEqual(cols * rows, 0.5 * 120 * 40)

    def test_vertical_is_not_a_compact_size(self):
        """Vertical is a full frame, so the compact-only keys must not apply."""
        self.assertNotIn("VERTICAL", MP_Audio_Player.COMPACT_UI_SIZES)
        self.assertIn("MINI", MP_Audio_Player.COMPACT_UI_SIZES)
        self.assertIn("MICRO", MP_Audio_Player.COMPACT_UI_SIZES)

    # --- it is the same player -------------------------------------------

    def test_vertical_draws_the_liquid_player_frame(self):
        """Every landmark of the landscape frame is present in the portrait one."""
        _, lines = frame("VERTICAL")
        joined = "\n".join(lines)
        for landmark in (
            "✦ MP AUDIO PLAYER (LIQUID GLASS)",   # header title
            "Liquid Glass",                        # header subtitle
            "▶ PLAYING",                           # playing badge
            "[MIX ARCHIVE]",                       # mode chip
            "♫",                                   # title bullet
            "Format: FLAC",                        # format line
            "DJ PITCH",                            # pitch slider
            "FAVORITES",                           # action row
            "BASS BOOST",                          # action row
            "PLAYLIST",                            # playlist panel
            "↑/↓: Scroll",                         # playlist footer
            "Pitch",                               # status footer
        ):
            self.assertIn(landmark, joined, f"the portrait frame is missing {landmark!r}")

    def test_vertical_and_normal_share_the_same_renderer(self):
        """There is no separate vertical player: the same method draws both."""
        ui = self._ui()
        self.assertFalse(hasattr(ui, "_draw_vertical_frame"),
                         "a separate deck renderer must not exist")
        source = Path(MP_Audio_Player.__file__).read_text(encoding="utf-8")
        self.assertNotIn("_draw_vertical_frame", source)
        self.assertNotIn("def _draw_vertical_eq", source)
        self.assertNotIn("def _draw_vertical_playlist", source)

    def test_vertical_has_no_oversized_block_clock(self):
        """The portrait frame uses the player's own typography, not new chrome."""
        _, lines = frame("VERTICAL")
        joined = "\n".join(lines)
        self.assertNotIn("███", joined, "the deck's block clock must be gone")
        self.assertNotIn("EQUALIZER ·", joined, "the deck's inline EQ panel must be gone")

    # --- it fits the column ----------------------------------------------

    def test_every_line_fits_the_portrait_column(self):
        for height in (52, 48, 44, 40, 36, 30):
            _, lines = frame("VERTICAL", height=height)
            self.assertEqual(len(lines), height)
            for line in lines:
                self.assertLessEqual(len(line), MP_Audio_Player.VERTICAL_COLS, line)

    def test_landscape_frame_is_unchanged(self):
        """The portrait work must not reflow the landscape player."""
        _, lines = frame("NORMAL", width=120, height=40)
        joined = "\n".join(lines)
        self.assertIn("♫", joined)
        self.assertIn("PLAYLIST", joined)
        for line in lines:
            self.assertLessEqual(len(line), 120)

    def test_action_row_fits_and_keeps_the_visualization_button(self):
        """The row fits the column, and the VISUALIZATION toggle survives it.

        The toggle has no other way in from the deck, so it shortens with the
        other chips instead of being dropped for want of room.
        """
        ui, lines = frame("VERTICAL")
        row = next((line for line in lines if "FAV" in line), "")
        self.assertLessEqual(len(row), MP_Audio_Player.VERTICAL_COLS)
        self.assertIn("VIS", row, "the Visualization button must be drawn")
        self.assertNotEqual(ui._visualizer_button_span, (0, 0),
                            "the Visualization button must be clickable")
        start, end = ui._visualizer_button_span
        self.assertIn("VIS", row[start - 1:end])
        # Favorites is drawn too, with a span pointing at its own label.
        self.assertGreater(ui._favorites_button_span[0], 0)
        start, end = ui._favorites_button_span
        self.assertIn("FAV", row[start - 1:end])

    def test_the_visualization_button_works_in_vertical(self):
        """Clicking it toggles the visualizer state, exactly as in Normal."""
        ui, lines = frame("VERTICAL")
        row = next((line for line in lines if "FAV" in line), "")
        start, end = ui._visualizer_button_span
        self.assertNotEqual((start, end), (0, 0))
        with patch.object(ui, "_toggle_mp_visualizer") as toggle:
            ui._on_mouse_sgr(0, start, ui._visualizer_button_row, b"M")
        toggle.assert_called_once()

    def test_header_never_clips_mid_word(self):
        _, lines = frame("VERTICAL", title="A Very Long Mix Title That Would Overflow")
        header = lines[1]
        self.assertLessEqual(len(header), MP_Audio_Player.VERTICAL_COLS)
        self.assertIn("MP AUDIO PLAYER", header)

    # --- the equaliser still works ---------------------------------------

    def test_e_opens_the_real_equaliser_in_vertical(self):
        """Vertical is a full player, so E opens the HUD it always did."""
        ui = self._ui("VERTICAL")
        with patch("MP_Audio_Player._present_frame"), \
                patch.object(ui, "_get_status", return_value={"levels": [0.2] * 24}):
            ui._process_key(b"E")
        self.assertTrue(ui.equalizer_hud_active)

    def test_equaliser_edit_survives_the_daemon_status(self):
        """A band set from this window must not be erased by the next draw."""
        ui = self._ui("VERTICAL")
        ui.eq_manager.current_bands = [0.0] * 10
        stale = {"eq_preset": "Flat", "eq_bands": [0.0] * 10,
                 "levels": [0.2] * 24, "peaks": [0.5] * 24}
        with patch.object(ui, "_send_cmd"), patch.object(ui, "_get_status", return_value=stale):
            ui.equalizer_hud_active = True
            ui._process_key(b"\x1b[A")
            ui._process_key(b"\x1b[A")
            self.assertEqual(ui.eq_manager.current_bands[0], 1.0)
            for _ in range(3):
                ui._get_status()
                self.assertEqual(ui.eq_manager.current_bands[0], 1.0,
                                 "the daemon's stale bands erased a local edit")
        ui._sync_eq_from_status({"eq_preset": "Trance", "eq_bands": [1.0] + [0.0] * 9})
        ui._sync_eq_from_status({"eq_preset": "Trance", "eq_bands": [-4.0] + [0.0] * 9})
        self.assertEqual(ui.eq_manager.current_bands[0], -4.0)

    # --- the glass host ---------------------------------------------------

    def test_published_size_accepts_vertical(self):
        self.assertIn("VERTICAL", UI_SIZES)
        publish_ui_size(FLAG_DIR, 987654, "VERTICAL")
        self.assertEqual(read_ui_size(FLAG_DIR, 987654), "VERTICAL")
        (FLAG_DIR / "liquid_ui_size.987654").unlink(missing_ok=True)

    def test_hosts_resize_to_the_vertical_grid(self):
        for mod in (lg_vulkan, lg_opengl):
            host = mod.GlassHost(["test"], mode="NORMAL")
            host.renderer = MagicMock()
            host.renderer.bezel_px = 7
            host.renderer.window_pos.return_value = (0, 0)
            host.renderer.window_size.return_value = (518, 976)
            host._place_terminal = MagicMock()
            host.terminal = MagicMock()
            host.terminal.get_char_width.return_value = 9
            host.terminal.get_char_height.return_value = 18
            host.set_mode("VERTICAL")
            self.assertEqual((host._target_cols, host._target_rows),
                             (MP_Audio_Player.VERTICAL_COLS, MP_Audio_Player.VERTICAL_ROWS))
            host.terminal.set_size.assert_called_with(
                MP_Audio_Player.VERTICAL_COLS, MP_Audio_Player.VERTICAL_ROWS)

    def test_host_panels_stay_inside_the_window(self):
        """The glass covers the header box and the playlist box, in bounds."""
        for mod in (lg_vulkan, lg_opengl):
            for rows in (52, 44, 36, 30):
                panels = mod.player_panel_rects(
                    MP_Audio_Player.VERTICAL_COLS, rows, 9, 18, 7, 7, mode="VERTICAL")
                header, playlist = panels
                self.assertEqual(header[1], 7)
                self.assertGreaterEqual(playlist[1], header[1] + header[3])
                for x, y, w, h in panels:
                    self.assertGreaterEqual(x, 0)
                    self.assertGreaterEqual(y, 0)
                    self.assertLessEqual(x + w, MP_Audio_Player.VERTICAL_COLS * 9 + 14)
                # The playlist box must start on the row the player draws it.
                self.assertGreater(playlist[1], header[1] + 4 * 18)
                expected_top = {52: 28, 44: 25, 36: 22, 30: 20}[rows]
                self.assertEqual(playlist[1], 7 + expected_top * 18,
                                 f"{mod.__name__} playlist starts off its drawn rule at {rows} rows")

    def test_host_launcher_forwards_the_vertical_flag(self):
        for mod in (lg_vulkan, lg_opengl):
            argv = mod.player_argv(mode="VERTICAL")
            self.assertIn("--vertical", argv, mod.__name__)
            self.assertNotIn("--mini", argv, mod.__name__)

    def test_vertical_is_a_player_mode_for_the_intro_video(self):
        from exit_video import PLAYER_MODES, intro_role
        self.assertIn("VERTICAL", PLAYER_MODES)
        self.assertEqual(intro_role("VERTICAL"), "player")


if __name__ == "__main__":
    unittest.main()
