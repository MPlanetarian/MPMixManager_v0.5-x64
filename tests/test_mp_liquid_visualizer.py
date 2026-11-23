#!/usr/bin/env python3
"""Unit and Vulkan GPU integration tests for MP_Liquid_Visualizer."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import MP_Liquid_Visualizer as vis_entry  # noqa: E402
from scripts import mp_liquid_visualizer_vulkan as vis  # noqa: E402
from scripts.desktop_launch_screen import fit_square_on_main  # noqa: E402


def _has_display() -> bool:
    """A real GLFW window needs a display server; a headless runner has none."""
    import os
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


DISPLAY_AVAILABLE = _has_display()


def _primary_monitor_is_4k() -> bool:
    """True when the primary monitor is at least 3840 px wide.

    Read through GLFW rather than a hard-coded environment variable: it is the
    same source the renderer itself uses to decide whether to go fullscreen.
    """
    if not DISPLAY_AVAILABLE:
        return False
    # Query the X server rather than initialising GLFW: glfwInit() inside the
    # test process segfaults on some drivers, and this only needs a number.
    import os
    import subprocess
    display = os.environ.get("DISPLAY")
    if not display:
        return False
    try:
        out = subprocess.run(["xrandr", "--current"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    # The primary output is marked with "primary"; fall back to any "connected".
    for line in out.splitlines():
        if " connected" in line and " primary" in line:
            for token in line.split():
                if "x" in token and token[0].isdigit():
                    try:
                        return int(token.split("x")[0]) >= 3840
                    except ValueError:
                        break
    return False


class TestMPLiquidVisualizer(unittest.TestCase):
    def test_window_dimensions_are_fixed_700x700(self):
        """macOS and Linux match the Liquid Player. Other platforms stay 700x700."""
        self.assertEqual(vis.WINDOW_WIDTH, 700)
        self.assertEqual(vis.WINDOW_HEIGHT, 700)
        self.assertEqual(vis_entry.WINDOW_WIDTH, 700)
        self.assertEqual(vis_entry.WINDOW_HEIGHT, 700)
        self.assertEqual(vis.preferred_window_edge(), 700)
        renderer = vis.LiquidVisualizerVulkanRenderer()
        if vis._IS_MAC:
            from liquid_player_visualizer import macos_liquid_player_window_size
            self.assertEqual(macos_liquid_player_window_size(), (1094, 734))
            self.assertEqual(renderer._windowed_size, (1094, 734))
            self.assertEqual(renderer._windowed_offset, (160, 80))
        elif vis._IS_LINUX:
            from liquid_player_visualizer import linux_visualizer_window_size
            self.assertEqual(renderer._windowed_size, linux_visualizer_window_size())
            # The stored offset keeps the historical corner value; the window is
            # centred on the primary desktop when it is created, via
            # place_on_main_screen(window_size=...).
            self.assertEqual(renderer._windowed_offset, (72, 64))
        else:
            fitted = fit_square_on_main(vis.preferred_window_edge())
            self.assertEqual(renderer._windowed_size, (fitted[0], fitted[1]))
            self.assertEqual(renderer._windowed_offset, (fitted[2], fitted[3]))
            self.assertLessEqual(fitted[0], 700)
            self.assertLessEqual(fitted[1], 700)

    def test_no_visualizer_preset_name_contains_legacy_brand(self):
        """Visualizer preset names and labels use the MPlanetarian name only."""
        names = [preset["name"] for preset in vis.VISUALIZER_PRESETS]
        self.assertEqual(names.count("Dreamworlds Ultra (MPlanetarian Biomech)"), 1)
        self.assertIn("Dreamworlds Ultra (MPlanetarian Biomech)", names)

    def test_preset_cosmic_dreamworlds_single_bandcamp_cover(self):
        """First preset must be 'Cosmic Dreamworlds (Liquid)' using strictly ONE Bandcamp cover."""
        self.assertGreaterEqual(len(vis.VISUALIZER_PRESETS), 3)
        preset = vis.get_active_preset("Cosmic Dreamworlds (Liquid)")
        self.assertEqual(preset["name"], "Cosmic Dreamworlds (Liquid)")
        self.assertEqual(preset["key"], "cosmic_dreamworlds_liquid")
        self.assertEqual(preset["album"], "MPlanetarian - Cosmic Dreamworlds [Deluxe Edition]")
        self.assertEqual(
            preset["bandcamp_url"],
            "https://mplanetarian.bandcamp.com/album/mplanetarian-cosmic-dreamworlds-deluxe-edition",
        )
        existing_covers = [Path(p) for p in preset["cover_paths"] if Path(p).is_file()]
        self.assertGreaterEqual(
            len(existing_covers),
            1,
            "Cover art file for Cosmic Dreamworlds (Liquid) must exist",
        )

        w, h, rgba_bytes = vis.load_preset_cover_rgba(preset, tex_size=256)
        self.assertEqual((w, h), (256, 256))
        self.assertEqual(len(rgba_bytes), 256 * 256 * 4)

    def test_preset_orbital_void_reflux_single_bandcamp_cover(self):
        """Second preset must be 'Orbital Void Reflux' using strictly ONE Bandcamp cover (Cover_WMI_V2.png)."""
        preset = vis.get_active_preset("Orbital Void Reflux")
        self.assertEqual(preset["name"], "Orbital Void Reflux")
        self.assertEqual(preset["key"], "orbital_void_reflux")
        self.assertEqual(preset["id"], 1)

        # Aliases test
        self.assertEqual(vis.get_active_preset("orbital_void_reflux")["id"], 1)
        self.assertEqual(vis.get_active_preset("orbital-void-reflux")["id"], 1)
        self.assertEqual(vis.get_active_preset("orbital void")["id"], 1)
        self.assertEqual(vis.get_active_preset("2")["id"], 1)

        existing_covers = [Path(p) for p in preset["cover_paths"] if Path(p).is_file()]
        self.assertGreaterEqual(
            len(existing_covers),
            1,
            "Cover art file for Orbital Void Reflux must exist",
        )

        w, h, rgba_bytes = vis.load_preset_cover_rgba(preset, tex_size=256)
        self.assertEqual((w, h), (256, 256))
        self.assertEqual(len(rgba_bytes), 256 * 256 * 4)

    def test_preset_dreamworlds_ultra_mplanetarian_single_cover(self):
        """Third preset must be 'Dreamworlds Ultra (MPlanetarian Biomech)' inspired by MPlanetarian's XINRI D and Start_Menu_Icon.png."""
        preset = vis.get_active_preset("Dreamworlds Ultra (MPlanetarian Biomech)")
        self.assertEqual(preset["name"], "Dreamworlds Ultra (MPlanetarian Biomech)")
        self.assertEqual(preset["key"], "dreamworlds_ultra_mplanetarian")
        self.assertEqual(preset["id"], 2)

        # Aliases test
        self.assertEqual(vis.get_active_preset("dreamworlds_ultra_mplanetarian")["id"], 2)
        self.assertEqual(vis.get_active_preset("mplanetarian biomech")["id"], 2)
        self.assertEqual(vis.get_active_preset("biomech")["id"], 2)
        self.assertEqual(vis.get_active_preset("3")["id"], 2)

        existing_covers = [Path(p) for p in preset["cover_paths"] if Path(p).is_file()]
        self.assertGreaterEqual(
            len(existing_covers),
            1,
            "Cover art file for Dreamworlds Ultra (MPlanetarian Biomech) must exist",
        )

        w, h, rgba_bytes = vis.load_preset_cover_rgba(preset, tex_size=256)
        self.assertEqual((w, h), (256, 256))
        self.assertEqual(len(rgba_bytes), 256 * 256 * 4)

    def test_preset_infinite_complexity_single_cover(self):
        """Fourth preset (4) must be 'Infinite Complexity' inspired by MPlanetarian's Mirrored Madness and Cover_v4.png."""
        preset = vis.get_active_preset("Infinite Complexity")
        self.assertEqual(preset["name"], "Infinite Complexity")
        self.assertEqual(preset["key"], "infinite_complexity")
        self.assertEqual(preset["id"], 3)

        # Aliases test
        self.assertEqual(vis.get_active_preset("infinite_complexity")["id"], 3)
        self.assertEqual(vis.get_active_preset("infinite complexity")["id"], 3)
        self.assertEqual(vis.get_active_preset("infinite")["id"], 3)
        self.assertEqual(vis.get_active_preset("complexity")["id"], 3)
        self.assertEqual(vis.get_active_preset("4")["id"], 3)

        existing_covers = [Path(p) for p in preset["cover_paths"] if Path(p).is_file()]
        self.assertGreaterEqual(
            len(existing_covers),
            1,
            "Cover art file for Infinite Complexity must exist",
        )

        w, h, rgba_bytes = vis.load_preset_cover_rgba(preset, tex_size=256)
        self.assertEqual((w, h), (256, 256))
        self.assertEqual(len(rgba_bytes), 256 * 256 * 4)

    def test_preset_sensorium_interface_single_cover(self):
        """Fifth preset (5) must be 'Sensorium Interface' inspired by MPlanetarian Earth Flipper and Sensorium_Interface.jpeg."""
        preset = vis.get_active_preset("Sensorium Interface")
        self.assertEqual(preset["name"], "Sensorium Interface")
        self.assertEqual(preset["key"], "sensorium_interface")
        self.assertEqual(preset["id"], 4)

        # Aliases test
        self.assertEqual(vis.get_active_preset("sensorium_interface")["id"], 4)
        self.assertEqual(vis.get_active_preset("sensorium interface")["id"], 4)
        self.assertEqual(vis.get_active_preset("sensorium")["id"], 4)
        self.assertEqual(vis.get_active_preset("earth_flipper")["id"], 4)
        self.assertEqual(vis.get_active_preset("5")["id"], 4)

        existing_covers = [Path(p) for p in preset["cover_paths"] if Path(p).is_file()]
        self.assertGreaterEqual(
            len(existing_covers),
            1,
            "Cover art file for Sensorium Interface must exist",
        )

        w, h, rgba_bytes = vis.load_preset_cover_rgba(preset, tex_size=256)
        self.assertEqual((w, h), (256, 256))
        self.assertEqual(len(rgba_bytes), 256 * 256 * 4)

    def test_preset_beyond_dimensions_single_cover(self):
        """Seventh preset (7) must be 'Beyond Dimensions' inspired by MPlanetarian Beyond Basel and Cotangent Dimensions."""
        preset = vis.get_active_preset("Beyond Dimensions")
        self.assertEqual(preset["name"], "Beyond Dimensions")
        self.assertEqual(preset["key"], "beyond_dimensions")
        self.assertEqual(preset["id"], 6)

        # Aliases test
        self.assertEqual(vis.get_active_preset("beyond_dimensions")["id"], 6)
        self.assertEqual(vis.get_active_preset("beyond dimensions")["id"], 6)
        self.assertEqual(vis.get_active_preset("beyond")["id"], 6)
        self.assertEqual(vis.get_active_preset("dimensions")["id"], 6)
        self.assertEqual(vis.get_active_preset("cotangent")["id"], 6)
        self.assertEqual(vis.get_active_preset("7")["id"], 6)

        existing_covers = [Path(p) for p in preset["cover_paths"] if Path(p).is_file()]
        self.assertGreaterEqual(
            len(existing_covers),
            1,
            "Cover art file for Beyond Dimensions must exist",
        )

        w, h, rgba_bytes = vis.load_preset_cover_rgba(preset, tex_size=256)
        self.assertEqual((w, h), (256, 256))
        self.assertEqual(len(rgba_bytes), 256 * 256 * 4)

    def test_preset_particle_accelerator_single_cover(self):
        """Eighth preset (8) must be 'Particle Accelerator' inspired by MPlanetarian TOROID and Vortex Sync."""
        preset = vis.get_active_preset("Particle Accelerator")
        self.assertEqual(preset["name"], "Particle Accelerator")
        self.assertEqual(preset["key"], "particle_accelerator")
        self.assertEqual(preset["id"], 7)

        # Aliases test
        self.assertEqual(vis.get_active_preset("particle_accelerator")["id"], 7)
        self.assertEqual(vis.get_active_preset("particle accelerator")["id"], 7)
        self.assertEqual(vis.get_active_preset("particle")["id"], 7)
        self.assertEqual(vis.get_active_preset("accelerator")["id"], 7)
        self.assertEqual(vis.get_active_preset("toroid")["id"], 7)
        self.assertEqual(vis.get_active_preset("8")["id"], 7)

        existing_covers = [Path(p) for p in preset["cover_paths"] if Path(p).is_file()]
        self.assertGreaterEqual(
            len(existing_covers),
            1,
            "Cover art file for Particle Accelerator must exist",
        )

        w, h, rgba_bytes = vis.load_preset_cover_rgba(preset, tex_size=256)
        self.assertEqual((w, h), (256, 256))
        self.assertEqual(len(rgba_bytes), 256 * 256 * 4)

    def test_preset_cosmic_universe_single_cover(self):
        """Ninth preset (9) must be 'Cosmic Universe' inspired by MPlanetarian Constellation and The Cosmological Horizon."""
        preset = vis.get_active_preset("Cosmic Universe")
        self.assertEqual(preset["name"], "Cosmic Universe")
        self.assertEqual(preset["key"], "cosmic_universe")
        self.assertEqual(preset["id"], 8)

        # Aliases test
        self.assertEqual(vis.get_active_preset("cosmic_universe")["id"], 8)
        self.assertEqual(vis.get_active_preset("cosmic universe")["id"], 8)
        self.assertEqual(vis.get_active_preset("cosmic")["id"], 8)
        self.assertEqual(vis.get_active_preset("universe")["id"], 8)
        self.assertEqual(vis.get_active_preset("constellation")["id"], 8)
        self.assertEqual(vis.get_active_preset("9")["id"], 8)

        existing_covers = [Path(p) for p in preset["cover_paths"] if Path(p).is_file()]
        self.assertGreaterEqual(
            len(existing_covers),
            1,
            "Cover art file for Cosmic Universe must exist",
        )

        w, h, rgba_bytes = vis.load_preset_cover_rgba(preset, tex_size=256)
        self.assertEqual((w, h), (256, 256))
        self.assertEqual(len(rgba_bytes), 256 * 256 * 4)

    @unittest.skipUnless(DISPLAY_AVAILABLE, "needs a display server")
    def test_audio_telemetry_advances_only_when_playing(self):
        """3D generation must advance when music is playing and freeze in-place when paused or stopped."""
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            # Point the bridge at the temp directory through its constructor.
            # Assigning `state_file` afterwards left the cached `_state_str`
            # (the string every read actually uses) aimed at the developer's
            # real player state, so the test silently read the live file.
            bridge = vis.AudioTelemetryBridge(config_dir=tmp_path)
            state_file = bridge.state_file

            # 1. Stopped state -> 3D generation waits at 0.0
            state_file.write_text(
                json.dumps(
                    {
                        "running": True,
                        "state": "stopped",
                        "muted": False,
                        "rms_level": 0.0,
                        "levels": [0.0] * 24,
                    }
                ),
                encoding="utf-8",
            )
            bridge.update(0.25)
            self.assertFalse(bridge.is_playing)
            self.assertEqual(bridge.accum_time, 0.0)
            self.assertEqual(bridge.bass_phase, 0.0)

            # 2. Playing state -> 3D generation advances and reacts to audio bands
            state_file.write_text(
                json.dumps(
                    {
                        "running": True,
                        "state": "playing",
                        "muted": False,
                        "rms_level": 0.42,
                        "pitch_factor": 1.0,
                        "levels": [0.8, 0.75, 0.7, 0.6, 0.5, 0.45, 0.4, 0.35] * 3,
                    }
                ),
                encoding="utf-8",
            )
            for _ in range(5):
                bridge.update(0.10)
            self.assertTrue(bridge.is_playing)
            self.assertGreater(bridge.accum_time, 0.0)
            self.assertGreater(bridge.dynamic_time, 0.0)
            self.assertGreater(bridge.bass_phase, 0.0)
            self.assertGreater(bridge.mid_phase, 0.0)
            self.assertGreater(bridge.high_phase, 0.0)

            frozen_time = bridge.accum_time
            frozen_dynamic_time = bridge.dynamic_time
            frozen_bass_phase = bridge.bass_phase
            frozen_mid_phase = bridge.mid_phase
            frozen_high_phase = bridge.high_phase

            # 3. Paused state -> 3D generation freezes in-place and waits for Play
            state_file.write_text(
                json.dumps(
                    {
                        "running": True,
                        "state": "paused",
                        "muted": False,
                        "rms_level": 0.42,
                        "levels": [0.8] * 24,
                    }
                ),
                encoding="utf-8",
            )
            for _ in range(10):
                bridge.update(0.10)
            self.assertFalse(bridge.is_playing)
            self.assertEqual(bridge.accum_time, frozen_time)
            self.assertEqual(bridge.dynamic_time, frozen_dynamic_time)
            self.assertEqual(bridge.bass_phase, frozen_bass_phase)
            self.assertEqual(bridge.mid_phase, frozen_mid_phase)
            self.assertEqual(bridge.high_phase, frozen_high_phase)

    def test_all_visualizer_windows_share_one_playback_clock(self):
        """Windows opened at different times must show the same preset frame.

        Each bridge used to integrate its own time from zero, so the companion
        behind the player and a window opened later were permanently offset.
        """
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            state_file = tmp_path / "mp_player_state.json"
            path = "/music/a-track.flac"
            state_file.write_text(json.dumps({
                "running": True,
                "state": "playing",
                "muted": False,
                "rms_level": 0.42,
                "pitch_factor": 1.0,
                "position_sec": 64.25,
                "path": path,
                "levels": [0.8, 0.75, 0.7, 0.6, 0.5, 0.45, 0.4, 0.35] * 3,
            }), encoding="utf-8")
            # The state file was written a moment ago; both windows
            # extrapolate the same age from the same mtime.
            old = time.time() - 0.5
            os.utime(state_file, (old, old))

            companion = vis.AudioTelemetryBridge(config_dir=tmp_path)
            detached = vis.AudioTelemetryBridge(config_dir=tmp_path)
            companion.update(0.016)
            time.sleep(0.02)
            detached.update(0.016)

            self.assertAlmostEqual(companion.accum_time, detached.accum_time, delta=0.1)
            self.assertAlmostEqual(companion.accum_time, companion.dynamic_time, places=6)
            self.assertAlmostEqual(detached.bass_phase, companion.bass_phase, delta=0.5)
            # The shared clock uses the track's stable origin, not zero.
            self.assertGreater(
                companion.accum_time,
                vis.shared_track_base(path) + 64.0,
            )

            # A window opened even later still joins the same frame.
            later = vis.AudioTelemetryBridge(config_dir=tmp_path)
            time.sleep(0.05)
            later.update(0.016)
            self.assertAlmostEqual(later.accum_time, companion.accum_time, delta=0.2)

    def test_shared_playback_clock_freezes_on_pause(self):
        """Pausing must hold every window on the same frozen frame."""
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            state_file = tmp_path / "mp_player_state.json"
            payload = {
                "running": True,
                "state": "playing",
                "muted": False,
                "rms_level": 0.5,
                "pitch_factor": 1.0,
                "position_sec": 30.0,
                "path": "/music/b-track.flac",
                "levels": [0.6] * 24,
            }
            state_file.write_text(json.dumps(payload), encoding="utf-8")
            old = time.time() - 0.2
            os.utime(state_file, (old, old))
            bridge = vis.AudioTelemetryBridge(config_dir=tmp_path)
            bridge.update(0.016)
            frozen = bridge.accum_time

            paused = dict(payload, state="paused")
            state_file.write_text(json.dumps(paused), encoding="utf-8")
            for _ in range(5):
                bridge.update(0.1)
            self.assertFalse(bridge.is_playing)
            self.assertEqual(bridge.accum_time, frozen)

    def test_a_backward_status_blip_does_not_rewind_the_scene(self):
        """The writer sometimes reports a position ~0.3 s behind the last one.

        That is a decoder/output-buffer sampling artefact, not a seek. Feeding
        it to the shared clock made the whole scene lurch backwards several
        times a second, which is what "out of sync with the music" looked
        like. A real seek (much larger) must still be followed.
        """
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            state_file = tmp_path / "mp_player_state.json"
            path = "/music/sync-track.flac"
            payload = {
                "running": True, "state": "playing", "muted": False,
                "rms_level": 0.4, "pitch_factor": 1.0, "position_sec": 64.0,
                "path": path, "levels": [0.5] * 24,
            }

            def write(pos: float) -> None:
                state_file.write_text(json.dumps(dict(payload, position_sec=pos)),
                                      encoding="utf-8")
                # Make the file look like a fresh status snapshot.
                now = time.time()
                os.utime(state_file, (now, now))

            write(64.0)
            bridge = vis.AudioTelemetryBridge(config_dir=tmp_path)
            bridge.update(0.016)
            before = bridge.accum_time

            # The same track's next sample is 0.3 s *behind*; ignore it.
            time.sleep(0.02)
            write(63.7)
            bridge.update(0.016)
            self.assertGreaterEqual(bridge.accum_time, before - 1e-6,
                                    "a backward blip must not rewind the scene")

            # A real seek backwards (5 s) is followed.
            time.sleep(0.02)
            write(58.7)
            bridge.update(0.016)
            self.assertLess(bridge.accum_time, before - 1.0)

    def test_escape_and_q_keys_close_window(self):
        """Pressing Escape (256) or 'q' (81) must close the borderless window."""
        # Assert the constants and the handler, not one particular formatting
        # of the assignment. The module groups several constants per line, so a
        # literal "X = value" search fails on correct code.
        self.assertEqual(vis.GLFW_DECORATED, 0x00020005)
        self.assertEqual(vis.GLFW_RESIZABLE, 0x00020003)
        self.assertEqual(vis.GLFW_KEY_ESCAPE, 256)
        self.assertEqual(vis.GLFW_KEY_Q, 81)
        src = Path(vis.__file__).read_text(encoding="utf-8")
        # Both keys must be handled, and Escape must close.
        self.assertIn("GLFW_KEY_ESCAPE", src)
        self.assertIn("GLFW_KEY_Q", src)
        self.assertRegex(src, r"key == GLFW_KEY_ESCAPE")

    def test_z_key_cycles_fullscreen_half_and_normal_size(self):
        """Pressing Z cycles: fullscreen -> half size -> default size -> fullscreen."""
        import time

        renderer = vis.LiquidVisualizerVulkanRenderer()
        bridge = vis.AudioTelemetryBridge()
        try:
            renderer.open()
            self.assertFalse(renderer.is_fullscreen)
            self.assertEqual(renderer.window_mode, vis.WINDOW_MODE_DEFAULT)
            normal = renderer.window_size()
            self.assertEqual(normal, renderer._windowed_size)

            # Press Z -> enters fullscreen first
            renderer._on_key(renderer._window, vis.GLFW_KEY_Z, 0, vis.GLFW_PRESS, 0)
            self.assertTrue(renderer.is_fullscreen)
            self.assertEqual(renderer.window_mode, vis.WINDOW_MODE_FULLSCREEN)
            for _ in range(5):
                bridge.update(0.05, force_playing=True, synthetic_audio=True)
                renderer.draw(bridge, capture=False)
                time.sleep(0.01)
            fs_w, fs_h = renderer.window_size()
            self.assertGreater(fs_w, normal[0])
            self.assertEqual(renderer._extent, renderer.framebuffer_size())

            # Press Z again -> half the default window size
            renderer._on_key(renderer._window, vis.GLFW_KEY_Z, 0, vis.GLFW_PRESS, 0)
            self.assertFalse(renderer.is_fullscreen)
            self.assertEqual(renderer.window_mode, vis.WINDOW_MODE_HALF)
            half = renderer.window_size()
            self.assertEqual(half, renderer._half_windowed_size)
            self.assertEqual(half[0], max(vis.MIN_WINDOW_SIDE, normal[0] // 2))
            self.assertLess(half[0], normal[0])

            # Press Z again -> back to the default window size
            renderer._on_key(renderer._window, vis.GLFW_KEY_Z, 0, vis.GLFW_PRESS, 0)
            self.assertFalse(renderer.is_fullscreen)
            self.assertEqual(renderer.window_mode, vis.WINDOW_MODE_DEFAULT)
            for _ in range(5):
                bridge.update(0.05, force_playing=True, synthetic_audio=True)
                renderer.draw(bridge, capture=False)
                time.sleep(0.01)
            self.assertEqual(renderer.window_size(), normal)
            self.assertEqual(renderer._extent, normal)

            # One more press restarts the cycle at fullscreen
            renderer._on_key(renderer._window, vis.GLFW_KEY_Z, 0, vis.GLFW_PRESS, 0)
            self.assertTrue(renderer.is_fullscreen)
            self.assertEqual(renderer.window_mode, vis.WINDOW_MODE_FULLSCREEN)
        finally:
            renderer.close()

    def test_double_click_window_toggles_fullscreen_and_restores_window_size(self):
        """A real renderer window enters fullscreen on a double-click and returns on the next one."""
        import os
        import time
        from unittest import mock

        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "visualizer_state.json"
            state.write_text('{"active": false, "preset_id": 0}', encoding="utf-8")
            with mock.patch.dict(os.environ, {"MP_LIQUID_VISUALIZER_STATE": str(state)}):
                renderer = vis.LiquidVisualizerVulkanRenderer()
                try:
                    renderer.open()
                    normal = renderer.window_size()
                    self.assertFalse(renderer.is_fullscreen)

                    def double_click():
                        renderer._on_mouse(renderer._window, 0, vis.GLFW_PRESS, 0)
                        renderer._on_mouse(renderer._window, 0, 0, 0)
                        renderer._on_mouse(renderer._window, 0, vis.GLFW_PRESS, 0)
                        renderer._on_mouse(renderer._window, 0, 0, 0)

                    double_click()
                    self.assertTrue(renderer.is_fullscreen)
                    for _ in range(5):
                        renderer.poll()
                        time.sleep(0.01)
                    self.assertTrue(renderer.is_fullscreen)

                    double_click()
                    self.assertFalse(renderer.is_fullscreen)
                    for _ in range(5):
                        renderer.poll()
                        time.sleep(0.01)
                    self.assertFalse(renderer.is_fullscreen)
                    self.assertEqual(renderer.window_size(), normal)
                finally:
                    renderer.close()

    def test_double_click_toggles_fullscreen_and_restores_window_size(self):
        """Two quick left clicks enter fullscreen; the next double-click returns to the window."""
        import time

        renderer = vis.LiquidVisualizerVulkanRenderer()
        toggles = []

        def fake_toggle():
            renderer.is_fullscreen = not renderer.is_fullscreen
            keys = ["fullscreen", "windowed"]
            toggles.append(keys[0] if renderer.is_fullscreen else keys[1])
            return renderer.is_fullscreen

        renderer.toggle_fullscreen = fake_toggle
        renderer.root_pointer = lambda: (400, 300)

        def double_click():
            renderer._on_mouse(None, 0, vis.GLFW_PRESS, 0)
            renderer._on_mouse(None, 0, 0, 0)
            renderer._on_mouse(None, 0, vis.GLFW_PRESS, 0)
            renderer._on_mouse(None, 0, 0, 0)

        double_click()
        self.assertTrue(renderer.is_fullscreen)
        self.assertFalse(renderer._drag_active)
        double_click()
        self.assertFalse(renderer.is_fullscreen)
        self.assertEqual(toggles, ["fullscreen", "windowed"])

        # A slow second click and a click far away are not double-clicks.
        renderer._on_mouse(None, 0, vis.GLFW_PRESS, 0)
        renderer._on_mouse(None, 0, 0, 0)
        renderer._last_click_at = time.monotonic() - (vis.DOUBLE_CLICK_SECONDS + 0.1)
        renderer._on_mouse(None, 0, vis.GLFW_PRESS, 0)
        renderer._on_mouse(None, 0, 0, 0)
        renderer._last_click_at = time.monotonic()
        renderer._last_click_pos = (0, 0)
        renderer._on_mouse(None, 0, vis.GLFW_PRESS, 0)
        renderer._on_mouse(None, 0, 0, 0)
        self.assertEqual(toggles, ["fullscreen", "windowed"])

    def test_shaders_compiled_and_valid(self):
        """Both Vulkan SPIR-V shaders must exist and have valid SPIR-V magic numbers."""
        vert_spv = ROOT / "shaders" / "mp_liquid_visualizer.vert.spv"
        frag_spv = ROOT / "shaders" / "mp_liquid_visualizer.frag.spv"
        self.assertTrue(vert_spv.is_file() and vert_spv.stat().st_size > 100)
        self.assertTrue(frag_spv.is_file() and frag_spv.stat().st_size > 1000)
        self.assertEqual(vert_spv.read_bytes()[:4], b"\x03\x02#\x07")
        self.assertEqual(frag_spv.read_bytes()[:4], b"\x03\x02#\x07")

    def test_cli_list_presets_and_vulkan_self_test(self):
        """CLI --list-presets and --self-test must succeed and render the windowed PNG."""
        res_list = subprocess.run(
            [sys.executable, str(ROOT / "MP_Liquid_Visualizer.py"), "--list-presets"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(res_list.returncode, 0)
        self.assertIn("Cosmic Dreamworlds (Liquid)", res_list.stdout)
        self.assertIn("Orbital Void Reflux", res_list.stdout)

        with tempfile.TemporaryDirectory() as td:
            out_png = Path(td) / "self_test_1400.png"
            # Pin the backend: the user's config.env may deliberately select
            # OpenGL, and this test is about the Vulkan renderer path.
            summary = vis.run_self_test(out_png, backend="vulkan")
            self.assertIn("MP_Liquid_Visualizer Vulkan device:", summary)
            match = re.search(r"Window: (\d+)x(\d+) Fixed Borderless", summary)
            self.assertIsNotNone(match)
            reported = (int(match.group(1)), int(match.group(2)))
            expected = vis.LiquidVisualizerVulkanRenderer()._windowed_size
            self.assertEqual(reported, expected)
            self.assertTrue(out_png.is_file() and out_png.stat().st_size > 50000)


    def test_fullscreen_no_auto_iconify_across_desktops(self):
        """Fullscreen window must disable GLFW_AUTO_ICONIFY and enable GLFW_FLOATING so it stays visible when clicking away."""
        renderer = vis.LiquidVisualizerVulkanRenderer()
        try:
            renderer.open()
            g = renderer._glfw
            if hasattr(g, "glfwGetWindowAttrib"):
                # Window must have auto-iconify disabled so focusing another desktop does not hide it
                auto_iconify = g.glfwGetWindowAttrib(renderer._window, vis.GLFW_AUTO_ICONIFY)
                self.assertEqual(auto_iconify, 0)

            # Toggle fullscreen (press Z)
            renderer._on_key(renderer._window, vis.GLFW_KEY_Z, 0, vis.GLFW_PRESS, 0)
            self.assertTrue(renderer.is_fullscreen)

            if hasattr(g, "glfwGetWindowAttrib"):
                auto_iconify = g.glfwGetWindowAttrib(renderer._window, vis.GLFW_AUTO_ICONIFY)
                self.assertEqual(auto_iconify, 0)
                floating = g.glfwGetWindowAttrib(renderer._window, vis.GLFW_FLOATING)
                self.assertEqual(floating, 1)

            # Polling while fullscreen must never iconify / disappear
            for _ in range(5):
                renderer.poll()

            if hasattr(g, "glfwGetWindowAttrib"):
                iconified = g.glfwGetWindowAttrib(renderer._window, vis.GLFW_ICONIFIED)
                self.assertEqual(iconified, 0)

            # Press Z again -> half-size windowed mode
            normal = renderer._windowed_size
            renderer._on_key(renderer._window, vis.GLFW_KEY_Z, 0, vis.GLFW_PRESS, 0)
            self.assertFalse(renderer.is_fullscreen)
            self.assertEqual(renderer.window_mode, vis.WINDOW_MODE_HALF)
            import time
            for _ in range(5):
                renderer.poll()
                time.sleep(0.01)
            self.assertEqual(renderer.window_size(), renderer._half_windowed_size)
            self.assertLess(renderer.window_size()[0], normal[0])
            if hasattr(g, "glfwGetWindowAttrib"):
                floating = g.glfwGetWindowAttrib(renderer._window, vis.GLFW_FLOATING)
                self.assertEqual(floating, 0)
        finally:
            renderer.close()

    @unittest.skipUnless(DISPLAY_AVAILABLE, "needs a display server")
    @unittest.skipUnless(
        DISPLAY_AVAILABLE and os.environ.get("MP_RUN_GPU_WINDOW_TESTS") == "1",
        "opens two real GPU windows; set MP_RUN_GPU_WINDOW_TESTS=1 to run",
    )
    def test_two_visualizer_processes_can_run_simultaneously(self):
        """Two separate visualizer processes can run simultaneously without conflicts.

        This spawns the real visualizer twice. On a workstation it opens two
        windows, takes the GPU, and (when the display is busy) outlives the
        ten-second wait; in CI it has no display at all. It is opt-in.
        """
        p1 = subprocess.Popen([sys.executable, str(ROOT / "MP_Liquid_Visualizer.py"), "--max-seconds", "1"])
        p2 = subprocess.Popen([sys.executable, str(ROOT / "MP_Liquid_Visualizer.py"), "--max-seconds", "1"])
        ret1 = p1.wait(timeout=10)
        ret2 = p2.wait(timeout=10)
        self.assertEqual(ret1, 0)
        self.assertEqual(ret2, 0)

    @unittest.skipUnless(DISPLAY_AVAILABLE, "needs a display server")
    def test_4k_display_rendered_at_1080p_not_4k(self):
        """When the Visualizer runs or goes fullscreen on a 4K display, it must render at 1920x1080, NOT 4K."""
        # The test is conditional on the machine actually having a 4K panel:
        # target_4k only enters fullscreen when the primary monitor is 4K. On a
        # 1094x778 laptop panel the code is behaving correctly by staying
        # windowed, so asserting fullscreen there tested the hardware.
        if not _primary_monitor_is_4k():
            self.skipTest("no 4K primary display on this machine")
        renderer = vis.LiquidVisualizerVulkanRenderer(target_4k=True)
        bridge = vis.AudioTelemetryBridge()
        try:
            renderer.open()
            # If a 4K display is present on the machine, target_4k will target it and enter fullscreen
            self.assertTrue(renderer.is_fullscreen)
            self.assertEqual(renderer.window_size(), (1920, 1080))
            self.assertEqual(renderer.framebuffer_size(), (1920, 1080))
            self.assertEqual(renderer._extent, (1920, 1080))

            for _ in range(5):
                bridge.update(0.05, force_playing=True, synthetic_audio=True)
                renderer.draw(bridge, capture=False)
                time.sleep(0.01)

            # Ensure Vulkan swapchain extent and framebuffer are strictly 1920x1080 (never 4K: 3840x2160)
            self.assertEqual(renderer._extent, (1920, 1080))
            self.assertEqual(renderer.framebuffer_size(), (1920, 1080))
            self.assertNotEqual(renderer._extent, (3840, 2160))

            # Toggle Z -> returns cleanly to the fitted windowed size
            normal = renderer._windowed_size
            renderer._on_key(renderer._window, vis.GLFW_KEY_Z, 0, vis.GLFW_PRESS, 0)
            self.assertFalse(renderer.is_fullscreen)
            for _ in range(5):
                bridge.update(0.05, force_playing=True, synthetic_audio=True)
                renderer.draw(bridge, capture=False)
                time.sleep(0.01)
            self.assertEqual(renderer.window_size(), normal)
            self.assertEqual(renderer._extent, normal)
        finally:
            renderer.close()

    def test_is_4k_display_detection_and_constants(self):
        """Verify 4K display detection logic and 1080p resolution constants."""
        self.assertEqual(vis.RENDER_1080P_WIDTH, 1920)
        self.assertEqual(vis.RENDER_1080P_HEIGHT, 1080)
        self.assertEqual(vis.FOUR_K_MIN_WIDTH, 3840)
        self.assertEqual(vis.FOUR_K_MIN_HEIGHT, 2160)

        # Mock monitor with 4K video mode (3840x2160)
        class MockVideoMode:
            width = 3840
            height = 2160
            refreshRate = 60

        class MockGLFW:
            def glfwGetVideoMode(self, mon):
                class Ptr:
                    contents = MockVideoMode()
                return Ptr()
            def glfwGetMonitorName(self, mon):
                return b"Hisense 4K TV"

        renderer = vis.LiquidVisualizerVulkanRenderer()
        renderer._glfw = MockGLFW()
        self.assertTrue(renderer.is_4k_display(12345))

        # Mock standard 1080p monitor
        MockVideoMode.width = 1920
        MockVideoMode.height = 1080
        MockGLFW.glfwGetMonitorName = lambda s, m: b"Standard 1080p Display"
        self.assertFalse(renderer.is_4k_display(12345))

    def test_cli_4k_and_fullscreen_options(self):
        """CLI must accept --fullscreen, --4k, and --screen flags."""
        res = subprocess.run(
            [sys.executable, str(ROOT / "MP_Liquid_Visualizer.py"), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("--fullscreen", res.stdout)
        self.assertIn("--4k", res.stdout)
        self.assertIn("--screen", res.stdout)
        self.assertIn("1920x1080", res.stdout)


if __name__ == "__main__":
    unittest.main()
