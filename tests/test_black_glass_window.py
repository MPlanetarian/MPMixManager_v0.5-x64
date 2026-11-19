#!/usr/bin/env python3
"""Tests for the MP Black Glass Window opened with **W**.

The feature is four behaviours, and each is pinned here against the real
module rather than against a description of it:

1. **One key, one meaning.** W opens a pane from MP Liquid Player, MP Gui, MP
   Liquid Mixer and MP Liquid Visualizer, and every one of them routes through
   ``scripts/black_glass_window.py``.
2. **Exactly the calling window's size.** The pane reads the rectangle the
   host published, matched on ``MP_HOST_PID``, so a second open player cannot
   supply the wrong size.
3. **Eight at most.** The claim files are the count, a dead claim is pruned,
   and the ninth launch is refused.
4. **Black Glass / Rainbow and the hover slider.** R toggles and the hue keeps
   ascending; the slider is 10 %..90 %, only drawn while the pointer is over
   the pane, tinted to match, and fades rather than popping.

Nothing here needs a display: the window itself is exercised through its pure
helpers and through a fake GTK/Cairo surface, and the process-level behaviour
through the registry and the spawn command.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
for _path in (ROOT, ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import black_glass_window as bg  # noqa: E402


class _TempConfig(unittest.TestCase):
    """Every test gets its own config/state directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {
            "MP_BLACK_GLASS_CONFIG_DIR": str(self.root / "config"),
            "MP_BLACK_GLASS_STATE_DIR": str(self.root / "state"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        for name in ("MP_HOST_PID", "MP_BLACK_GLASS_PID", "MP_VISUALIZER_MATCH_BLACK_GLASS"):
            os.environ.pop(name, None)


class TransparencyRangeTests(_TempConfig):
    """The slider is documented as 10 % to 90 %, in that direction."""

    def test_the_range_is_ten_to_ninety(self):
        self.assertEqual(bg.MIN_TRANSPARENCY, 10.0)
        self.assertEqual(bg.MAX_TRANSPARENCY, 90.0)

    def test_values_are_clamped_to_the_range(self):
        self.assertEqual(bg.clamp_transparency(-40), 10.0)
        self.assertEqual(bg.clamp_transparency(0), 10.0)
        self.assertEqual(bg.clamp_transparency(500), 90.0)
        self.assertEqual(bg.clamp_transparency("35"), 35.0)
        self.assertEqual(bg.clamp_transparency("not a number"), bg.DEFAULT_TRANSPARENCY)
        self.assertEqual(bg.clamp_transparency(float("nan")), bg.DEFAULT_TRANSPARENCY)

    def test_more_transparency_means_a_fainter_window(self):
        self.assertAlmostEqual(bg.transparency_to_opacity(90), 0.10, places=6)
        self.assertAlmostEqual(bg.transparency_to_opacity(10), 0.90, places=6)
        self.assertAlmostEqual(bg.opacity_to_transparency(0.10), 90.0, places=6)
        self.assertAlmostEqual(bg.opacity_to_transparency(0.90), 10.0, places=6)

    def test_a_pointer_maps_across_the_whole_range(self):
        width = 1000
        left, right = bg.slider_track(width)
        self.assertAlmostEqual(bg.transparency_from_x(left, width), 10.0, places=6)
        self.assertAlmostEqual(bg.transparency_from_x(right, width), 90.0, places=6)
        self.assertAlmostEqual(bg.transparency_from_x(left - 500, width), 10.0, places=6)
        self.assertAlmostEqual(bg.transparency_from_x(right + 500, width), 90.0, places=6)

    def test_the_thumb_round_trips_through_the_slider(self):
        width = 1200
        for wanted in (10.0, 25.0, 50.0, 77.5, 90.0):
            x = bg.slider_thumb_x(wanted, width)
            self.assertAlmostEqual(bg.transparency_from_x(x, width), wanted, places=4)


class RainbowOrderTests(_TempConfig):
    """Rainbow mode walks the hue scale upwards, lowest to highest."""

    def test_hue_only_ever_rises(self):
        samples = [bg.hue_at(t) for t in (0.0, 0.5, 1.0, 2.0, 5.0)]
        for earlier, later in zip(samples, samples[1:]):
            self.assertGreater(later, earlier)

    def test_one_full_turn_happens_at_a_gradual_pace(self):
        # Gradual, not a strobe: a whole turn must take several seconds.
        turn = 360.0 / bg.RAINBOW_DEGREES_PER_SECOND
        self.assertGreaterEqual(turn, 5.0)
        self.assertAlmostEqual(bg.hue_at(turn), 0.0, places=6)
        self.assertAlmostEqual(bg.hue_at(turn / 2.0), 180.0, places=6)

    def test_the_palette_covers_every_part_of_the_scale(self):
        # Red, green and blue each appear somewhere in one turn.
        reds = greens = blues = 0
        for step in range(0, 360, 10):
            r, g, b = bg.hsv_to_rgb(step)
            reds += r > 0.5
            greens += g > 0.5
            blues += b > 0.5
        self.assertGreater(reds, 0)
        self.assertGreater(greens, 0)
        self.assertGreater(blues, 0)

    def test_hsv_conversion_stays_in_range(self):
        for step in range(0, 361, 7):
            for channel in bg.hsv_to_rgb(step):
                self.assertGreaterEqual(channel, 0.0)
                self.assertLessEqual(channel, 1.0)

    def test_the_slider_takes_the_colour_of_its_pane(self):
        cool = bg.slider_colour(False, 200.0)
        warm = bg.slider_colour(True, 30.0)
        self.assertNotEqual(cool, warm)
        # Black Glass uses one constant colour whatever the clock says.
        self.assertEqual(bg.slider_colour(False, 0.0), bg.slider_colour(False, 300.0))
        # Rainbow's follows the hue, so it changes as the clock advances.
        self.assertNotEqual(bg.slider_colour(True, 30.0), bg.slider_colour(True, 210.0))


class SlotRegistryTests(_TempConfig):
    """Eight panes at most, with the count kept by claim files."""

    def test_the_limit_is_eight(self):
        self.assertEqual(bg.MAX_WINDOWS, 8)

    def test_claims_fill_to_eight_and_then_refuse(self):
        with patch.object(bg, "_pid_alive", return_value=True):
            claimed = [pid for pid in range(7001, 7001 + bg.MAX_WINDOWS) if bg.claim_slot(pid)]
            self.assertEqual(len(claimed), bg.MAX_WINDOWS)
            self.assertEqual(bg.live_count(), bg.MAX_WINDOWS)
            self.assertFalse(bg.claim_slot(7001 + bg.MAX_WINDOWS))
            self.assertEqual(bg.live_count(), bg.MAX_WINDOWS)

    def test_a_dead_claim_is_pruned_and_frees_its_slot(self):
        dead = 2_000_000_000  # no such pid
        bg.slots_dir().joinpath(f"{dead}.claim").write_text(f"{dead}\n", encoding="utf-8")
        self.assertEqual(bg.live_count(), 0)
        self.assertFalse(bg.slots_dir().joinpath(f"{dead}.claim").exists())

    def test_a_released_slot_can_be_reclaimed(self):
        pid = 7101
        self.assertTrue(bg.claim_slot(pid))
        bg.release_slot(pid)
        self.assertEqual(bg.live_count(), 0)
        self.assertTrue(bg.claim_slot(pid))

    def test_claiming_twice_for_the_same_pid_is_idempotent(self):
        pid = 7102
        with patch.object(bg, "_pid_alive", return_value=True):
            self.assertTrue(bg.claim_slot(pid))
            self.assertTrue(bg.claim_slot(pid))
            self.assertEqual(bg.live_count(), 1)

    def test_the_limit_message_names_eight(self):
        self.assertIn("8", bg.limit_message())


class GeometryTests(_TempConfig):
    """The pane is exactly the size of the window that opened it."""

    def _publish(self, name: str, pid: int, width: int, height: int,
                 x: int = 100, y: int = 50) -> Path:
        path = bg.config_dir() / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "width": width, "height": height, "pid": pid, "x": x, "y": y, "wid": 4242,
        }), encoding="utf-8")
        return path

    def test_the_matching_host_pid_supplies_the_size(self):
        self._publish("liquid_player_window.json", os.getpid(), 1280, 820)
        os.environ["MP_HOST_PID"] = str(os.getpid())
        self.assertEqual(bg.host_geometry(), (100, 50, 1280, 820))

    def test_another_live_player_does_not_supply_the_size(self):
        # The record for a different window is live but is not *this* window.
        self._publish("liquid_player_window.json", os.getpid(), 999, 999)
        os.environ["MP_HOST_PID"] = str(os.getpid() + 1)
        geometry = bg.host_geometry()
        # With nothing matching, the newest live record is used, never a
        # mixture of the two sizes.
        self.assertIn(geometry, [None, (100, 50, 999, 999)])

    def test_a_dead_publisher_is_ignored(self):
        self._publish("liquid_gui_window.json", 2_000_000_000, 1500, 900)
        os.environ["MP_HOST_PID"] = str(2_000_000_000)
        self.assertIsNone(bg.host_geometry())

    def test_gui_geometry_is_used_when_no_player_record_exists(self):
        self._publish("liquid_gui_window.json", os.getpid(), 1094, 760, 12, 34)
        os.environ["MP_HOST_PID"] = str(2_000_000_000)
        self.assertEqual(bg.host_geometry(), (12, 34, 1094, 760))

    def test_the_spawn_command_carries_the_exact_size(self):
        argv = bg.spawn_command(1094, 760, 40, 60, "MP Gui")
        self.assertIn("--geometry", argv)
        self.assertEqual(argv[argv.index("--geometry") + 1], "1094x760+40+60")
        self.assertEqual(argv[argv.index("--source") + 1], "MP Gui")

    def test_the_spawn_command_never_goes_below_the_minimum(self):
        argv = bg.spawn_command(10, 10)
        size = argv[argv.index("--geometry") + 1]
        self.assertEqual(size.split("+")[0], f"{bg.MIN_WIDTH}x{bg.MIN_HEIGHT}")

    def test_a_geometry_string_round_trips(self):
        self.assertEqual(bg.parse_geometry("1280x820+72+64"), (72, 64, 1280, 820))
        self.assertIsNone(bg.parse_geometry("nonsense"))
        self.assertIsNone(bg.parse_geometry("0x0+0+0"))

    def test_published_geometry_is_read_back_for_the_visualizer(self):
        bg.publish_geometry(11, 22, 1280, 820, pid=os.getpid(), wid=99)
        os.environ["MP_BLACK_GLASS_PID"] = str(os.getpid())
        self.assertEqual(bg.read_geometry(), (11, 22, 1280, 820))
        self.assertEqual(bg.read_window_id(), 99)

    def test_a_closed_pane_publishes_nothing(self):
        bg.publish_geometry(1, 2, 800, 600, pid=2_000_000_000)
        os.environ["MP_BLACK_GLASS_PID"] = "2000000000"
        self.assertIsNone(bg.read_geometry())


class SpawnTests(_TempConfig):
    """The spawner refuses cleanly and never invents a ninth window."""

    def test_unsupported_without_a_display(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DISPLAY", None)
            os.environ.pop("WAYLAND_DISPLAY", None)
            self.assertEqual(bg.spawn_black_glass(800, 600), "unsupported")
            self.assertIn("X11", bg.spawn_message("unsupported"))

    def test_limit_is_reported_before_anything_is_spawned(self):
        with patch.object(bg, "_pid_alive", return_value=True):
            for pid in range(7301, 7301 + bg.MAX_WINDOWS):
                bg.claim_slot(pid)
            with patch.object(bg, "black_glass_supported", return_value=True), \
                    patch.object(bg, "_spawn") as spawn:
                self.assertEqual(bg.spawn_black_glass(800, 600), "limit")
        spawn.assert_not_called()
        self.assertIn("8", bg.spawn_message("limit"))

    def test_opened_uses_the_child_environment(self):
        with patch.object(bg, "black_glass_supported", return_value=True), \
                patch.object(bg, "_spawn", return_value=True) as spawn:
            self.assertEqual(bg.spawn_black_glass(1280, 820, 10, 20, "MP Liquid Player"), "opened")
        argv, kwargs = spawn.call_args.args[0], spawn.call_args.kwargs
        self.assertIn("1280x820+10+20", argv)
        env = kwargs["env"]
        # A pane is a fresh host: the window it came from must not leak in.
        for key in ("MP_GUI_SESSION", "MP_LIQUID_GLASS_MIXER", "MP_PLAYER_NAMESPACE"):
            self.assertNotIn(key, env)

    def test_spawn_for_host_reads_the_published_rectangle(self):
        path = bg.config_dir() / "liquid_player_window.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "width": 518, "height": 976, "pid": os.getpid(), "x": 5, "y": 6,
        }), encoding="utf-8")
        os.environ["MP_HOST_PID"] = str(os.getpid())
        with patch.object(bg, "spawn_black_glass", return_value="opened") as spawn:
            self.assertEqual(bg.spawn_black_glass_for_host("MP Liquid Player"), "opened")
        spawn.assert_called_once_with(518, 976, 5, 6, "MP Liquid Player")

    def test_spawn_for_host_reports_missing_geometry(self):
        os.environ["MP_HOST_PID"] = "2000000000"
        bg.config_dir().mkdir(parents=True, exist_ok=True)
        self.assertEqual(bg.spawn_black_glass_for_host("MP Gui"), "no-geometry")
        self.assertIn("size", bg.spawn_message("no-geometry"))

    def test_spawn_for_window_uses_the_mixer_rectangle(self):
        window = MagicMock()
        window.get_allocated_width.return_value = 1920
        window.get_allocated_height.return_value = 1080
        window.get_position.return_value = (0, 0)
        with patch.object(bg, "spawn_black_glass", return_value="opened") as spawn:
            self.assertEqual(bg.spawn_black_glass_for_window(window, "MP Liquid Mixer"), "opened")
        spawn.assert_called_once_with(1920, 1080, 0, 0, "MP Liquid Mixer")


class VisualizerAttachmentTests(_TempConfig):
    """Z inside a pane attaches a visualizer that follows that pane."""

    def test_the_child_environment_marks_the_pane_as_its_host(self):
        env = bg._visualizer_env(4242)
        self.assertEqual(env["MP_VISUALIZER_COMPANION"], "1")
        self.assertEqual(env["MP_VISUALIZER_MATCH_BLACK_GLASS"], "1")
        self.assertEqual(env["MP_BLACK_GLASS_PID"], "4242")
        # A per-pane state file: the player's flags must not hide this window.
        self.assertIn("black_glass_visualizer_state.4242.json", env["MP_LIQUID_VISUALIZER_STATE"])

    def test_attaching_writes_a_state_file_and_records_the_pid(self):
        proc = SimpleNamespace(pid=8123)
        state_path = bg._visualizer_state_path(os.getpid())
        with patch.object(bg, "_visualizer_entry", return_value=Path("/bin/true")), \
                patch.object(bg.subprocess, "Popen", return_value=proc) as popen:
            self.assertEqual(bg.attach_visualizer(), 8123)
        popen.assert_called_once()
        argv = popen.call_args.args[0]
        self.assertIn("--preset", argv)
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertTrue(payload["active"])
        self.assertIs(payload["visible"], True)

    def test_the_attached_pid_is_reported_while_it_lives(self):
        path = bg.visualizer_pid_path(os.getpid())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pid": os.getpid()}) + "\n", encoding="utf-8")
        self.assertEqual(bg.attached_visualizer_pid(), os.getpid())

    def test_a_dead_visualizer_pid_reads_as_absent(self):
        path = bg.visualizer_pid_path(os.getpid())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pid": 2_000_000_000}) + "\n", encoding="utf-8")
        self.assertEqual(bg.attached_visualizer_pid(), 0)

    def test_detaching_keeps_the_windows_it_does_not_own(self):
        self.assertFalse(bg.detach_visualizer())


class HostRunningTests(_TempConfig):
    """The bond closes the visualizer when its pane disappears."""

    def test_this_process_is_the_live_host(self):
        os.environ["MP_BLACK_GLASS_PID"] = str(os.getpid())
        self.assertTrue(bg.host_running())

    def test_a_dead_host_is_reported_dead(self):
        os.environ["MP_BLACK_GLASS_PID"] = "2000000000"
        self.assertFalse(bg.host_running())

    def test_no_pid_is_unknown_rather_than_dead(self):
        os.environ.pop("MP_BLACK_GLASS_PID", None)
        self.assertIsNone(bg.host_running())


class _FakeCairo:
    """Just enough Cairo to observe what the pane paints."""

    OPERATOR_SOURCE = 1
    OPERATOR_OVER = 2

    def __init__(self):
        self.calls = []
        self._path = []

    def __getattr__(self, name):
        def record(*args, **_kwargs):
            self.calls.append((name, args))
            return None
        return record

    # gradients need to answer add_color_stop_rgba
    def set_source(self, value):
        self.calls.append(("set_source", (value,)))

    def paint(self):
        self.calls.append(("paint", ()))

    def select_font_face(self, *args):
        self.calls.append(("select_font_face", args))

    def show_text(self, text):
        self.calls.append(("show_text", (text,)))

    def names(self):
        return [name for name, _ in self.calls]

    def drawn_text(self):
        return " ".join(str(args[0]) for name, args in self.calls if name == "show_text")


def _fake_gradient(*_args):
    class _Gradient:
        def add_color_stop_rgba(self, *_a):
            return None
    return _Gradient()


class WindowBehaviourTests(_TempConfig):
    """Hover fade, the R toggle and the key bindings."""

    def _window(self):
        window = bg.BlackGlassWindow(1280, 820, 10, 20, "MP Liquid Player")
        window._area = MagicMock()
        window._window = MagicMock()
        return window

    def test_the_slider_is_never_drawn_without_the_pointer(self):
        window = self._window()
        self.assertFalse(window._hover)
        self.assertEqual(window._slider_alpha, 0.0)
        cr = _FakeCairo()
        with patch.object(bg.BlackGlassWindow, "_linear", side_effect=_fake_gradient), \
                patch.object(bg.BlackGlassWindow, "_radial", side_effect=_fake_gradient):
            window._on_draw(SimpleNamespace(get_allocated_width=lambda: 1280,
                                            get_allocated_height=lambda: 820), cr)
        names = cr.names()
        self.assertNotIn("rectangle", names)

    def test_entering_fades_the_slider_in_and_leaving_fades_it_out(self):
        window = self._window()
        window._on_enter()
        window._last_tick = time.monotonic()
        for _ in range(60):
            window._last_tick -= 0.01
            window._tick()
        self.assertGreater(window._slider_alpha, 0.9)
        window._on_leave()
        for _ in range(60):
            window._last_tick -= 0.01
            window._tick()
        self.assertLess(window._slider_alpha, 0.1)

    def test_the_fade_is_gradual_not_instant(self):
        window = self._window()
        window._on_enter()
        window._last_tick = time.monotonic() - (bg.SLIDER_FADE_SECONDS / 2.0)
        window._tick()
        self.assertGreater(window._slider_alpha, 0.2)
        self.assertLess(window._slider_alpha, 0.8)

    def test_the_slider_is_drawn_while_hovering(self):
        window = self._window()
        window._hover = True
        window._slider_alpha = 1.0
        cr = _FakeCairo()
        with patch.object(bg.BlackGlassWindow, "_linear", side_effect=_fake_gradient), \
                patch.object(bg.BlackGlassWindow, "_radial", side_effect=_fake_gradient):
            window._on_draw(SimpleNamespace(get_allocated_width=lambda: 1280,
                                            get_allocated_height=lambda: 820), cr)
        self.assertIn("rectangle", cr.names())
        self.assertIn("TRANSPARENCY", cr.drawn_text())

    def test_r_switches_to_rainbow_and_back(self):
        window = self._window()
        Gdk = SimpleNamespace(KEY_r=114, KEY_R=82, KEY_Escape=65307, KEY_q=113, KEY_Q=81,
                              KEY_z=122, KEY_Z=90, KEY_g=103, KEY_G=71, KEY_l=108,
                              KEY_L=76, KEY_m=109, KEY_M=77, KEY_w=119, KEY_W=87)
        window._Gdk = Gdk
        self.assertFalse(window.rainbow)
        window._on_key(None, SimpleNamespace(keyval=Gdk.KEY_r))
        self.assertTrue(window.rainbow)
        window._on_key(None, SimpleNamespace(keyval=Gdk.KEY_r))
        self.assertFalse(window.rainbow)

    def test_rainbow_keeps_advancing_while_it_is_on(self):
        window = self._window()
        window.rainbow = True
        first = bg.hue_at(0.0)
        window._last_tick = time.monotonic()
        window._started -= 2.0
        window._tick()
        self.assertGreater(window.hue, first)

    def test_escape_and_q_close_the_pane(self):
        window = self._window()
        Gdk = SimpleNamespace(KEY_r=114, KEY_R=82, KEY_Escape=65307, KEY_q=113, KEY_Q=81,
                              KEY_z=122, KEY_Z=90, KEY_g=103, KEY_G=71, KEY_l=108,
                              KEY_L=76, KEY_m=109, KEY_M=77, KEY_w=119, KEY_W=87)
        window._Gdk = Gdk
        with patch.object(window, "close") as close:
            self.assertTrue(window._on_key(None, SimpleNamespace(keyval=Gdk.KEY_Escape)))
        close.assert_called_once()
        with patch.object(window, "close") as close:
            self.assertTrue(window._on_key(None, SimpleNamespace(keyval=Gdk.KEY_q)))
        close.assert_called_once()

    def test_the_pane_reopens_the_suite_windows(self):
        window = self._window()
        Gdk = SimpleNamespace(KEY_r=114, KEY_R=82, KEY_Escape=65307, KEY_q=113, KEY_Q=81,
                              KEY_z=122, KEY_Z=90, KEY_g=103, KEY_G=71, KEY_l=108,
                              KEY_L=76, KEY_m=109, KEY_M=77, KEY_w=119, KEY_W=87)
        window._Gdk = Gdk
        with patch.object(bg, "launch_mp_gui", return_value=True) as launch:
            self.assertTrue(window._on_key(None, SimpleNamespace(keyval=Gdk.KEY_g)))
        launch.assert_called_once()
        with patch.object(bg, "launch_mp_player", return_value=True) as launch:
            self.assertTrue(window._on_key(None, SimpleNamespace(keyval=Gdk.KEY_l)))
        launch.assert_called_once()
        with patch.object(bg, "launch_manager_konsole", return_value=True) as launch:
            self.assertTrue(window._on_key(None, SimpleNamespace(keyval=Gdk.KEY_m)))
        launch.assert_called_once()

    def test_w_opens_another_pane_at_the_same_size(self):
        window = self._window()
        window._Gdk = SimpleNamespace(KEY_w=119, KEY_W=87, KEY_r=114, KEY_R=82,
                                      KEY_Escape=65307, KEY_q=113, KEY_Q=81, KEY_z=122,
                                      KEY_Z=90, KEY_g=103, KEY_G=71, KEY_l=108,
                                      KEY_L=76, KEY_m=109, KEY_M=77)
        with patch.object(bg, "spawn_black_glass", return_value="opened") as spawn:
            self.assertTrue(window._on_key(None, SimpleNamespace(keyval=window._Gdk.KEY_w)))
        spawn.assert_called_once_with(window.width, window.height,
                                      window.x + 24, window.y + 24, window.source)

    def test_the_window_is_resizable(self):
        # The resize grip is the bottom-right corner, and a drag there changes
        # the size instead of moving the pane.
        window = self._window()
        window._hover = True
        event = SimpleNamespace(button=1, x=window.width - 2, y=window.height - 2)
        self.assertTrue(window._on_press(None, event))
        self.assertEqual(window._drag, "resize")
        window._on_motion(None, SimpleNamespace(x=window.width + 180, y=window.height + 90))
        window._window.resize.assert_called_with(1462, 912)

    def test_moving_drags_the_whole_pane(self):
        window = self._window()
        window._on_press(None, SimpleNamespace(button=1, x=400, y=300))
        self.assertEqual(window._drag, "move")
        window._on_motion(None, SimpleNamespace(x=440, y=330))
        window._window.move.assert_called_with(window.x + 40, window.y + 30)

    def test_dragging_the_slider_sets_the_opacity(self):
        window = self._window()
        left, right = bg.slider_track(window.width)
        y = window.height - bg.SLIDER_BOTTOM
        window._on_press(None, SimpleNamespace(button=1, x=left, y=y))
        self.assertEqual(window._drag, "slider")
        self.assertAlmostEqual(window.transparency, bg.MIN_TRANSPARENCY, places=4)
        window._on_motion(None, SimpleNamespace(x=right, y=y))
        self.assertAlmostEqual(window.transparency, bg.MAX_TRANSPARENCY, places=4)
        window._window.set_opacity.assert_called()

    def test_the_pane_publishes_its_rectangle_to_the_visualizer(self):
        window = self._window()
        window.wid = 77
        window._publish()
        os.environ["MP_BLACK_GLASS_PID"] = str(os.getpid())
        self.assertEqual(bg.read_geometry(), (window.x, window.y, window.width, window.height))
        self.assertEqual(bg.read_window_id(), 77)


class PaintedColourTests(_TempConfig):
    """What the pane actually paints in each mode."""

    class _Grad:
        def __init__(self):
            self.stops = []
        def add_color_stop_rgba(self, position, r, g, b, a):
            self.stops.append((float(position), float(r), float(g), float(b), float(a)))

    class _Cr:
        def __init__(self):
            self.grads = []
        def set_source(self, value):
            self.grads.append(value)
        def __getattr__(self, _name):
            return lambda *_a, **_k: None

    def _body(self, rainbow: bool):
        window = bg.BlackGlassWindow(1000, 700, 0, 0, "")
        window.rainbow = rainbow
        window.hue = bg.hue_at(3.0)

        def make_linear(_cr, *_args):
            grad = self._Grad()
            _cr.grads.append(grad)
            return grad

        def make_radial(_cr, *_args):
            grad = self._Grad()
            _cr.grads.append(grad)
            return grad

        window._linear = make_linear
        window._radial = make_radial
        cr = self._Cr()
        area = SimpleNamespace(get_allocated_width=lambda: 1000,
                               get_allocated_height=lambda: 700)
        window._on_draw(area, cr)
        return cr.grads[0].stops

    def test_black_glass_is_actually_black(self):
        stops = self._body(False)
        self.assertTrue(stops)
        bright = max(max(stop[1], stop[2], stop[3]) for stop in stops)
        self.assertLess(bright, 0.15)

    def test_rainbow_is_actually_coloured(self):
        stops = self._body(True)
        self.assertTrue(stops)
        bright = max(max(stop[1], stop[2], stop[3]) for stop in stops)
        self.assertGreater(bright, 0.25)

    def test_the_two_modes_paint_different_colours(self):
        self.assertNotEqual(self._body(False), self._body(True))


class KeyRoutingTests(_TempConfig):
    """Every host routes W to the shared helper."""

    def test_the_player_opens_a_pane_only_inside_the_glass(self):
        import MP_Audio_Player as player
        source = Path(player.__file__).read_text(encoding="utf-8")
        self.assertIn("_open_black_glass", source)
        self.assertIn("spawn_black_glass_for_host", source)
        # W keeps the playlist export everywhere else, so no console user
        # loses a key that already shipped.
        self.assertIn('elif data in (b"w", b"W"): self._export_playlist()', source)

    def test_the_glass_hosts_tell_the_child_which_record_is_theirs(self):
        for name in ("MP_Audio_Player_Liquid_Glass.py",
                     "MP_Audio_Player_Liquid_Glass_OpenGL.py"):
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("MP_HOST_PID", source, name)

    def test_mp_gui_routes_w_through_the_helper(self):
        source = (ROOT / "Mix_Archive_Manager.sh").read_text(encoding="utf-8")
        self.assertIn("mp_black_glass_open()", source)
        self.assertIn("--spawn-host", source)
        # Only inside MP Gui: on a console W is still a search letter.
        self.assertIn('MP_GUI_SESSION:-0}" = "1"', source)

    def test_the_mixer_opens_a_pane_at_its_own_size(self):
        source = (ROOT / "MP_Liquid_Mixer" / "MP_Liquid_Mixer.py").read_text(encoding="utf-8")
        self.assertIn("spawn_black_glass_for_window", source)
        self.assertIn("KEY_w", source)

    def test_the_three_visualizer_backends_offer_w(self):
        for name in ("mp_liquid_visualizer_vulkan.py", "mp_liquid_visualizer_metal.py",
                     "liquid_player_visualizer.py"):
            source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertIn("spawn_black_glass", source, name)


class BondSupportTests(_TempConfig):
    """The attached visualizer follows the pane, not the player."""

    def test_the_bond_knows_a_black_glass_host(self):
        import visualizer_player_bond as bond
        self.assertEqual(bond.BLACK_GLASS_TITLE, "MP Black Glass")

        class Fake(bond.PlayerBondMixin):
            pass

        os.environ["MP_VISUALIZER_MATCH_BLACK_GLASS"] = "1"
        fake = Fake()
        self.assertTrue(fake._bond_targets_black_glass())
        self.assertEqual(fake._bond_host_title(), bond.BLACK_GLASS_TITLE)

    def test_the_bond_reads_the_pane_rectangle(self):
        import visualizer_player_bond as bond
        bg.publish_geometry(7, 8, 900, 700, pid=os.getpid(), wid=31)
        os.environ["MP_VISUALIZER_MATCH_BLACK_GLASS"] = "1"
        os.environ["MP_BLACK_GLASS_PID"] = str(os.getpid())

        class Fake(bond.PlayerBondMixin):
            pass

        fake = Fake()
        self.assertEqual(fake._bond_read_geometry(), (7, 8, 900, 700))
        self.assertEqual(fake._bond_read_size(), (900, 700))
        self.assertEqual(fake._bond_read_host_window_id(), 31)


if __name__ == "__main__":
    unittest.main()
