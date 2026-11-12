#!/usr/bin/env python3
"""Tests for the two Stream of Frequency — Dynamic Waves presets.

Covers:
  1. Both presets are registered, resolvable by key / alias / number, and sit
     at the ids the shader branches on.
  2. They render on the OpenGL backend (the shader compiles and links).
  3. The PID event source fires one stream per new Linux PID, ignores the
     baseline scan, and folds a storm of new PIDs into one hot burst.
  4. The stream driver's audio mode fires from beats with a floor between
     events, so the player path stays musical.
  5. The screen saver defaults to the Dynamic Waves pair, not the whole
     library.
  6. The shader itself states the design contract: 82% coverage, the 20%
     pixel reduction, the slow arc, and the PID event vector.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import mp_liquid_visualizer_vulkan as vis
import pid_stream
import mp_visualizer_idle_saver as saver


SHADER = ROOT / "shaders" / "mp_liquid_visualizer.frag"


class TestPresetRegistration(unittest.TestCase):
    def test_horizontal_preset(self):
        p = vis.get_active_preset("stream_of_frequency_dynamic_waves")
        self.assertEqual(p["key"], "stream_of_frequency_dynamic_waves")
        self.assertEqual(p["id"], vis.STREAM_WAVES_HORIZONTAL_ID)
        self.assertIn("Dynamic Waves", p["name"])
        self.assertNotIn("P2", p["name"])

    def test_vertical_preset(self):
        p = vis.get_active_preset("stream_of_frequency_dynamic_waves_p2")
        self.assertEqual(p["key"], "stream_of_frequency_dynamic_waves_p2")
        self.assertEqual(p["id"], vis.STREAM_WAVES_VERTICAL_ID)
        self.assertIn("P2", p["name"])

    def test_aliases_resolve(self):
        for alias in ("dynamic waves", "dynamic_waves", "waves", "12",
                      "stream", "sof", "stream of frequency", "frequency"):
            self.assertEqual(vis.get_active_preset(alias)["key"],
                             "stream_of_frequency_dynamic_waves", alias)
        for alias in ("p2", "matrix", "matrix rain", "vertical", "13"):
            self.assertEqual(vis.get_active_preset(alias)["key"],
                             "stream_of_frequency_dynamic_waves_p2", alias)

    def test_ids_are_at_the_end_and_distinct(self):
        ids = [p["id"] for p in vis.VISUALIZER_PRESETS]
        self.assertEqual(len(ids), len(set(ids)), "preset ids must be unique")
        self.assertEqual(ids[-2], vis.STREAM_WAVES_HORIZONTAL_ID)
        self.assertEqual(ids[-1], vis.STREAM_WAVES_VERTICAL_ID)

    def test_both_are_reachable_by_cycling(self):
        keys = {p["key"] for p in vis.VISUALIZER_PRESETS}
        seen = set()
        cur = vis.VISUALIZER_PRESETS[0]
        for _ in range(len(vis.VISUALIZER_PRESETS)):
            cur = vis.cycle_preset(cur, 1)
            seen.add(cur["key"])
        self.assertEqual(seen, keys)

    def test_both_appear_in_list_presets(self):
        res = subprocess.run(
            [sys.executable, str(ROOT / "MP_Liquid_Visualizer.py"), "--list-presets"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("dynamic_waves", res.stdout)
        self.assertIn("Dynamic Waves P2", res.stdout)


class TestCoverArt(unittest.TestCase):
    @unittest.skipUnless(
        (lambda: __import__("importlib").util.find_spec("PIL") is not None)(),
        "Pillow is not installed for this interpreter",
    )
    def test_each_preset_has_a_cover(self):
        for key in ("stream_of_frequency_dynamic_waves",
                    "stream_of_frequency_dynamic_waves_p2"):
            preset = vis.get_active_preset(key)
            self.assertTrue(
                any(Path(p).is_file() for p in preset["cover_paths"]),
                f"{key} has no cover art on disk",
            )

    @unittest.skipUnless(
        (lambda: __import__("importlib").util.find_spec("PIL") is not None)(),
        "Pillow is not installed for this interpreter",
    )
    def test_missing_cover_falls_back_to_paper_white(self):
        """The rivers never sample the texture, so a missing cover must not be fatal."""
        preset = {"key": "stream_of_frequency_dynamic_waves", "name": "x", "cover_paths": []}
        w, h, pixels = vis.load_preset_cover_rgba(preset, tex_size=8)
        self.assertEqual((w, h), (8, 8))
        self.assertEqual(set(pixels), {0xFF})

    @unittest.skipUnless(
        (lambda: __import__("importlib").util.find_spec("PIL") is not None)(),
        "Pillow is not installed for this interpreter",
    )
    def test_other_presets_still_raise_when_art_is_missing(self):
        preset = {"key": "cosmic_dreamworlds_liquid", "name": "x", "cover_paths": []}
        with self.assertRaises(FileNotFoundError):
            vis.load_preset_cover_rgba(preset)


class TestShaderContract(unittest.TestCase):
    def setUp(self):
        self.src = SHADER.read_text(encoding="utf-8")

    def test_coverage_is_82_percent(self):
        self.assertIn("SOF_COVERAGE = 0.82", self.src)

    def test_stream_uniform_is_declared(self):
        self.assertIn("vec4 stream;", self.src)
        self.assertIn("u.stream.y", self.src)   # PID phase
        self.assertIn("u.stream.z", self.src)   # PID heat
        self.assertIn("u.stream.w", self.src)   # PID serial

    def test_both_rivers_branch_on_the_preset_id(self):
        self.assertIn("u.preset.x > 10.5", self.src)
        self.assertIn("u.preset.x < 11.5", self.src)

    def test_arc_bend_is_slow_and_band_fanned(self):
        self.assertIn("sofArcOffset", self.src)
        # The phase creep must stay in the "slow wave" range, not per-cell.
        self.assertIn("sin(along * PI + t * 0.071", self.src)

    def test_pid_fireworks_composite_over_white(self):
        """A white page needs coverage/alpha mixing, not additive light."""
        self.assertIn("vec4 sofPidBursts", self.src)
        self.assertIn("return mix(col, burst.rgb, burst.a);", self.src)

    def test_vertical_mode_is_top_to_bottom(self):
        self.assertIn("float head = fract(t * speed + hCol * 7.0);", self.src)
        self.assertIn("float drop = head - along;", self.src)

    def test_shipped_spirv_has_the_new_presets(self):
        for preset_id in (vis.STREAM_WAVES_HORIZONTAL_ID, vis.STREAM_WAVES_VERTICAL_ID):
            spv = ROOT / "shaders" / "presets" / f"p{preset_id}.frag.spv"
            self.assertTrue(spv.is_file(), spv.name)
            self.assertEqual(spv.read_bytes()[:4], b"\x03\x02#\x07")

    def test_manifest_lists_every_preset(self):
        manifest = json.loads((ROOT / "shaders" / "presets" / "manifest.json").read_text())
        for preset in vis.VISUALIZER_PRESETS:
            self.assertIn(int(preset["id"]), manifest["presets"])


class TestPidStreamSource(unittest.TestCase):
    def _source(self, readings, **kwargs):
        """A source driven by a fixed list of /proc snapshots.

        `step()` advances a fake clock and re-runs the scan, so a test can be
        explicit about "this is the frame where the PID appeared".
        """
        it = iter(readings)

        def read():
            try:
                return next(it)
            except StopIteration:
                return None

        src = pid_stream.PidStreamSource(scan_interval=0.0, source=read, **kwargs)
        clock = {"t": 0.0}

        def step(dt=0.05):
            clock["t"] += dt
            return src.update(dt, now=clock["t"])

        return src, step

    def test_baseline_scan_fires_nothing(self):
        src, step = self._source([{1, 2, 3}])
        state = step()
        self.assertEqual(state.heat, 0.0)
        self.assertEqual(state.serial, 0.0)

    def test_new_pid_fires_one_event(self):
        src, step = self._source([{1, 2}, {1, 2}, {1, 2, 3}])
        step()                     # learn baseline
        step()                     # no change
        state = step()             # PID 3 appeared
        self.assertGreater(state.heat, 0.0)
        self.assertEqual(state.serial, 1.0)
        self.assertEqual(state.last_pid, 3)

    def test_event_sweeps_and_then_ends(self):
        src, step = self._source([{1}, {1, 2}], event_seconds=0.5)
        step()                     # baseline
        step()                     # PID appears; the front starts at 0
        phases = [step(0.05).phase for _ in range(3)]
        self.assertTrue(all(p > 0.0 for p in phases), phases)
        self.assertTrue(all(b >= a for a, b in zip(phases, phases[1:])), phases)
        end = None
        for _ in range(30):
            end = step(0.05)
        self.assertEqual(end.heat, 0.0)
        self.assertEqual(end.phase, 0.0)

    def test_a_storm_of_pids_makes_one_hot_burst(self):
        src, step = self._source([set(range(10)), set(range(20))])
        step()                     # baseline
        state = step()             # ten new processes at once
        self.assertEqual(state.serial, 1.0, "a storm must not stack fronts")
        self.assertGreaterEqual(state.heat, 0.6)

    def test_a_steady_pid_drip_does_not_pin_the_front(self):
        """A live desktop creates PIDs constantly; the sweep must still travel."""
        current = {1}
        next_pid = {"v": 2}
        scan = {"n": 0}

        def read():
            # One new PID every third scan, like a desktop quietly spawning.
            scan["n"] += 1
            if scan["n"] % 3 == 0:
                current.add(next_pid["v"])
                next_pid["v"] += 1
            return set(current)

        src = pid_stream.PidStreamSource(scan_interval=0.0, source=read, event_seconds=1.0)
        clock = {"t": 0.0}
        seen_serial = 0.0
        max_phase = 0.0
        for _ in range(12):
            clock["t"] += 0.05
            st = src.update(0.05, now=clock["t"])
            if st.serial <= seen_serial and st.serial > 0:
                max_phase = max(max_phase, st.phase)
            seen_serial = max(seen_serial, st.serial)
        # Progress inside a single front must exceed a few percent, otherwise
        # the river never sweeps on a machine that is creating processes.
        self.assertGreater(max_phase, 0.05)

    def test_disabled_source_is_idle(self):
        src, step = self._source([{1}, {1, 2}], enabled=False)
        step()
        self.assertEqual(step().heat, 0.0)

    def test_non_linux_reports_no_pids(self):
        """The source must degrade to 'nothing happened', never raise."""
        src = pid_stream.PidStreamSource(source=lambda: None)
        state = src.update(0.016, now=1.0)
        self.assertEqual(state.heat, 0.0)

    def test_read_pids_returns_ints_on_linux_or_none(self):
        pids = pid_stream.read_pids()
        if pids is None:
            self.skipTest("no /proc on this platform")
        self.assertTrue(pids)
        self.assertTrue(all(isinstance(p, int) for p in pids))


class TestStreamDriverAudioMode(unittest.TestCase):
    class _Bridge:
        def __init__(self):
            self.beat_pulse = 0.0
            self.rms_energy = 0.0

    def test_silence_fires_nothing(self):
        driver = pid_stream.StreamDriver(mode="audio")
        self.assertEqual(driver.update(0.016, 10.0, bridge=self._Bridge()), (0.0, 0.0, 0.0, 0.0))

    def test_a_beat_fires_a_stream(self):
        driver = pid_stream.StreamDriver(mode="audio")
        bridge = self._Bridge()
        driver.update(0.016, 10.0, bridge=bridge)
        bridge.beat_pulse = 0.9
        _, phase, heat, serial = driver.update(0.016, 10.05, bridge=bridge)
        self.assertEqual(serial, 1.0)
        self.assertGreater(heat, 0.0)

    def test_events_respect_the_floor(self):
        driver = pid_stream.StreamDriver(mode="audio", min_audio_gap=0.5)
        bridge = self._Bridge()
        driver.update(0.016, 10.0, bridge=bridge)
        bridge.beat_pulse = 0.9
        driver.update(0.016, 10.02, bridge=bridge)
        first_serial = driver._serial
        bridge.beat_pulse = 0.2
        driver.update(0.016, 10.10, bridge=bridge)
        bridge.beat_pulse = 1.0
        _, _, _, serial = driver.update(0.016, 10.20, bridge=bridge)
        self.assertEqual(serial, first_serial, "a second beat inside the floor must not re-fire")

    def test_demo_mode_sweeps_without_a_bridge(self):
        driver = pid_stream.StreamDriver(mode="demo")
        a = driver.update(0.016, 100.0)
        b = driver.update(0.016, 101.0)
        self.assertNotEqual(a[1], b[1])


class TestSharedStreamVector(unittest.TestCase):
    """One helper serves the Vulkan renderer, the OpenGL layer and the glass."""

    def setUp(self):
        pid_stream.reset_stream_drivers()

    def tearDown(self):
        pid_stream.reset_stream_drivers()

    def test_other_presets_are_free(self):
        for preset_id in (0, 5, 10):
            self.assertEqual(
                pid_stream.stream_vector("pid", preset_id, 10.0, 0.016),
                (0.0, 0.0, 0.0, 0.0),
            )

    def test_ids_select_the_river(self):
        horizontal = pid_stream.stream_vector("demo", vis.STREAM_WAVES_HORIZONTAL_ID, 10.0, 0.016)
        vertical = pid_stream.stream_vector("demo", vis.STREAM_WAVES_VERTICAL_ID, 10.0, 0.016)
        self.assertEqual(horizontal[0], 0.0)
        self.assertEqual(vertical[0], 1.0)

    def test_each_key_keeps_its_own_clock(self):
        """Two windows at different clocks must not share one serial."""
        pid_stream.stream_vector("demo", vis.STREAM_WAVES_HORIZONTAL_ID, 10.0, 0.016, key="a")
        later = pid_stream.stream_vector("demo", vis.STREAM_WAVES_HORIZONTAL_ID, 40.0, 0.016, key="b")
        earlier = pid_stream.stream_vector("demo", vis.STREAM_WAVES_HORIZONTAL_ID, 20.0, 0.016, key="a")
        self.assertNotEqual(later[3], earlier[3], "window 'a' must not inherit window 'b'")

    def test_mode_switch_resets_the_driver(self):
        """A window reopened as a saver must not inherit the player's heat."""
        pid_stream.stream_vector("audio", vis.STREAM_WAVES_HORIZONTAL_ID, 10.0, 0.016,
                                 bridge=TestStreamDriverAudioMode._Bridge(), key="k")
        driver = pid_stream._DRIVERS["k"]
        driver._event_at = 10.0
        driver._event_heat = 0.9
        driver._serial = 7.0
        pid_stream.stream_vector("pid", vis.STREAM_WAVES_HORIZONTAL_ID, 10.1, 0.016, key="k")
        self.assertEqual(pid_stream._DRIVERS["k"].mode, "pid")
        self.assertEqual(pid_stream._DRIVERS["k"]._serial, 0.0)
        self.assertEqual(pid_stream._DRIVERS["k"]._event_heat, 0.0)


class TestSaverDefaults(unittest.TestCase):
    def test_default_pool_is_the_dynamic_waves_pair(self):
        self.assertEqual(
            tuple(saver.DEFAULT_SAVER_PRESETS),
            ("stream_of_frequency_dynamic_waves", "stream_of_frequency_dynamic_waves_p2"),
        )

    def test_default_pool_presets_exist(self):
        for key in saver.DEFAULT_SAVER_PRESETS:
            self.assertEqual(vis.get_active_preset(key)["key"], key)

    def test_saver_launches_the_pool(self):
        cmd = []
        from unittest.mock import patch
        with patch.object(saver, "_visualizer_entry", return_value=Path("/tmp/vis.py")), \
             patch.object(saver.subprocess, "Popen") as popen:
            popen.return_value = object()
            saver._launch_saver(45.0, None, True, saver.DEFAULT_SAVER_PRESETS)
            args = popen.call_args[0][0]
        self.assertIn("--saver-presets", args)
        pool = args[args.index("--saver-presets") + 1]
        self.assertEqual(pool.split(","), list(saver.DEFAULT_SAVER_PRESETS))

    def test_single_preset_still_locks(self):
        from unittest.mock import patch
        with patch.object(saver, "_visualizer_entry", return_value=Path("/tmp/vis.py")), \
             patch.object(saver.subprocess, "Popen") as popen:
            popen.return_value = object()
            saver._launch_saver(45.0, "cosmic_dreamworlds_liquid", False,
                                saver.DEFAULT_SAVER_PRESETS)
            args = popen.call_args[0][0]
        self.assertIn("--saver-preset", args)
        self.assertNotIn("--saver-presets", args)

    def test_cli_accepts_the_pool_flag(self):
        res = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "mp_visualizer_idle_saver.py"), "--help"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("--saver-pools", res.stdout)

    def test_visualizer_cli_accepts_saver_presets(self):
        res = subprocess.run(
            [sys.executable, str(ROOT / "MP_Liquid_Visualizer.py"), "--help"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("--saver-presets", res.stdout)


class TestSaverPoolGuards(unittest.TestCase):
    """The saver must tolerate every shape of "no pool supplied"."""

    def test_run_screensaver_accepts_a_none_pool(self):
        """A windowed saver with no --saver-presets used to raise TypeError."""
        from unittest.mock import patch
        sentinel = {"ran": False}

        def fake_loop(*_a, **_k):
            sentinel["ran"] = True
            return 0

        with patch.object(vis, "_make_saver_renderer") as make, \
             patch.object(vis, "saver_screen_plan", return_value=[]), \
             patch.object(vis, "_saver_loop", side_effect=fake_loop):
            make.return_value.open = lambda: None
            make.return_value.preset = {"key": "x", "name": "x"}
            for pool in (None, [], (), [""], ["  "]):
                sentinel["ran"] = False
                vis.run_screensaver(dwell=1.0, all_screens=False, preset_pool=pool)
                self.assertTrue(sentinel["ran"], f"pool={pool!r} did not reach the loop")

    def test_run_screensaver_uses_a_supplied_pool(self):
        from unittest.mock import patch
        captured = {}

        def fake_loop(_renderer, _dwell, _cycle, **kwargs):
            captured.update(kwargs)
            return 0

        with patch.object(vis, "_make_saver_renderer") as make, \
             patch.object(vis, "saver_screen_plan", return_value=[]), \
             patch.object(vis, "_saver_loop", side_effect=fake_loop):
            make.return_value.open = lambda: None
            make.return_value.preset = {"key": "x", "name": "x"}
            vis.run_screensaver(dwell=1.0, all_screens=False,
                                preset_pool=["stream_of_frequency_dynamic_waves"])
        self.assertEqual(captured.get("preset_pool"), ["stream_of_frequency_dynamic_waves"])


class TestSaverSessionOwnership(unittest.TestCase):
    """Two overlapping savers must not tear each other down.

    The session file is shared, so a second launch (or a preview while the
    daemon is up) overwrites it. Before this was fixed, the loser's cleanup
    deleted the file and the winner's wall exited on the next frame.
    """

    class _Renderer:
        screensaver = True

        def __init__(self, owner_pid=0, started_at=None, wake=False, is_owner=False):
            self._saver_owner_pid = owner_pid
            self._saver_started_at = started_at
            self._saver_is_owner = is_owner
            self._wake_event = wake
            self.should_close = False
            self.preset = {"key": "stream_of_frequency_dynamic_waves", "name": "x"}
            self.closed = False

        def open(self):
            return None

        def poll(self):
            return True

        def draw(self, _bridge, _capture=False):
            return None

        def close(self):
            self.closed = True

        def switch_preset(self, _preset):
            pass

    def _run(self, renderer, session, frames=3, monkey_session=None):
        import time as _time
        ticks = {"n": 0}

        def fake_read():
            ticks["n"] += 1
            if ticks["n"] > frames:
                renderer.should_close = True
                return None
            return monkey_session if monkey_session is not None else session

        with unittest.mock.patch.object(vis, "read_span_session", side_effect=fake_read), \
             unittest.mock.patch.object(vis, "pid_alive", return_value=True), \
             unittest.mock.patch.object(vis.time, "sleep", lambda _s: None):
            vis._saver_loop(renderer, 90.0, True, shared_session=session,
                            preset_pool=["stream_of_frequency_dynamic_waves"],
                            already_open=True)

    def test_owner_survives_while_its_own_session_is_live(self):
        session = {"owner_pid": 111, "started_at": 1000.0}
        r = self._Renderer(owner_pid=111, started_at=1000.0, is_owner=True)
        self._run(r, session)
        self.assertTrue(r.closed)

    def test_owner_hands_over_to_a_living_newer_wall(self):
        """A live second saver takes over; the older wall steps down."""
        session = {"owner_pid": 111, "started_at": 1000.0}
        r = self._Renderer(owner_pid=111, started_at=1000.0, is_owner=True)
        newer = {"owner_pid": 222, "started_at": 2000.0}

        def alive(pid):
            return int(pid) == 222          # the newer owner is running

        with unittest.mock.patch.object(vis, "read_span_session", return_value=newer), \
             unittest.mock.patch.object(vis, "pid_alive", side_effect=alive), \
             unittest.mock.patch.object(vis.time, "sleep", lambda _s: None):
            r.should_close = True
            vis._saver_loop(r, 90.0, True, shared_session=session,
                            preset_pool=["stream_of_frequency_dynamic_waves"],
                            already_open=True)
        self.assertTrue(r.closed)

    def test_owner_reclaims_the_file_from_a_dead_newer_wall(self):
        """A second launch that exits immediately must not leave no saver."""
        session = {"owner_pid": 111, "started_at": 1000.0}
        r = self._Renderer(owner_pid=111, started_at=1000.0, is_owner=True)
        dead_newer = {"owner_pid": 222, "started_at": 2000.0}
        rewritten = []
        frames = {"n": 0}

        def fake_read():
            frames["n"] += 1
            if frames["n"] > 4:
                r.should_close = True
                return None
            # The dead wall owns the file until we take it back.
            return dead_newer if not rewritten else session

        with unittest.mock.patch.object(vis, "read_span_session", side_effect=fake_read), \
             unittest.mock.patch.object(vis, "pid_alive", return_value=False), \
             unittest.mock.patch.object(vis, "write_span_session",
                                        side_effect=lambda d: rewritten.append(d)), \
             unittest.mock.patch.object(vis.time, "sleep", lambda _s: None):
            vis._saver_loop(r, 90.0, True, shared_session=session,
                            preset_pool=["stream_of_frequency_dynamic_waves"],
                            already_open=True)
        self.assertTrue(rewritten, "the surviving owner must reclaim the session file")
        self.assertEqual(rewritten[0]["owner_pid"], 111)

    def test_spawned_window_follows_its_own_owner(self):
        """A spawned copy dies when a different wall's session is published."""
        session = {"owner_pid": 111, "started_at": 1000.0}
        r = self._Renderer(owner_pid=111, started_at=1000.0, is_owner=False)
        other = {"owner_pid": 999, "started_at": 3000.0}
        self._run(r, session, monkey_session=other)
        self.assertTrue(r.closed)

    def test_cleanup_only_clears_its_own_session(self):
        session = {"owner_pid": 111, "started_at": 1000.0}
        newer = {"owner_pid": 222, "started_at": 2000.0}
        r = self._Renderer(owner_pid=111, started_at=1000.0, wake=True, is_owner=True)
        cleared = []
        with unittest.mock.patch.object(vis, "read_span_session", return_value=newer), \
             unittest.mock.patch.object(vis, "clear_span_session",
                                        side_effect=lambda: cleared.append(True)), \
             unittest.mock.patch.object(vis.time, "sleep", lambda _s: None):
            r.should_close = True
            vis._saver_loop(r, 90.0, True, shared_session=session,
                            preset_pool=["stream_of_frequency_dynamic_waves"],
                            already_open=True)
        self.assertEqual(cleared, [], "a newer wall's session must not be deleted")


class TestSaverTeardownDoesNotStealTheWall(unittest.TestCase):
    """A standing-down saver must not delete the newer wall's session.

    ``run_screensaver``'s finally block used to call ``clear_span_session()``
    unconditionally. When a second launch took over, the older process exited
    and deleted the file the newer wall was watching, so both walls died.
    """

    def test_old_owner_leaves_a_newer_session_alone(self):
        session = {"owner_pid": 111, "started_at": 1000.0}
        newer = {"owner_pid": 222, "started_at": 2000.0}
        cleared = []
        with unittest.mock.patch.object(vis, "read_span_session", return_value=newer), \
             unittest.mock.patch.object(vis, "clear_span_session",
                                        side_effect=lambda: cleared.append(True)):
            live = vis.read_span_session()
            ours = (
                live is not None
                and int(live.get("owner_pid", 0) or 0) == int(session["owner_pid"])
                and live.get("started_at") == session["started_at"]
            )
            if ours:
                vis.clear_span_session()
        self.assertEqual(cleared, [], "a newer wall's session must survive us")

    def test_owner_clears_its_own_session(self):
        session = {"owner_pid": 111, "started_at": 1000.0}
        same = {"owner_pid": 111, "started_at": 1000.0}
        cleared = []
        with unittest.mock.patch.object(vis, "read_span_session", return_value=same), \
             unittest.mock.patch.object(vis, "clear_span_session",
                                        side_effect=lambda: cleared.append(True)):
            live = vis.read_span_session()
            ours = (
                live is not None
                and int(live.get("owner_pid", 0) or 0) == int(session["owner_pid"])
                and live.get("started_at") == session["started_at"]
            )
            if ours:
                vis.clear_span_session()
        self.assertEqual(cleared, [True])

    def test_teardown_guard_is_present_in_the_source(self):
        src = Path(vis.__file__).read_text(encoding="utf-8")
        block = src[src.index("        for proc in children:"):
                    src.index("def _stop_saver_child")]
        self.assertIn("ours", block)
        self.assertIn("clear_span_session()", block)


class TestWaylandPlacement(unittest.TestCase):
    """Wayland needs the compositor to move windows; the saver must ask KWin."""

    def setUp(self):
        import wayland_window_place as place
        self.place = place

    def test_wayland_detection(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=False):
            self.assertTrue(self.place.is_wayland())
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "", "XDG_SESSION_TYPE": "x11"}, clear=False):
            self.assertFalse(self.place.is_wayland())

    def test_placement_is_a_no_op_off_wayland(self):
        with unittest.mock.patch.object(self.place, "is_wayland", return_value=False):
            self.assertEqual(self.place.assign([{"title": "x", "output": "DP-1"}]), 0)
            self.assertFalse(self.place.place_on_output("x", "DP-1"))

    def test_script_places_every_window_in_one_pass(self):
        src = self.place._script_source([
            ("MP Liquid Visualizer [0,0]", "DP-1"),
            ("MP Liquid Visualizer [3840,0]", "DP-3"),
        ])
        # Both assignments must be inside the single script: running one script
        # per window let them fight over KWin's active window.
        self.assertEqual(src.count("title:"), 2)
        self.assertIn("DP-1", src)
        self.assertIn("DP-3", src)
        self.assertIn("slotWindowToNextScreen", src)

    def test_script_matches_captions_by_prefix(self):
        """KWin can append the app name, so an exact match alone is not enough."""
        src = self.place._script_source([("MP Liquid Visualizer [0,0]", "DP-1")])
        self.assertIn("indexOf(title) === 0", src)

    def test_script_verifies_then_corrects(self):
        src = self.place._script_source([("a", "DP-1")])
        self.assertIn("pass < 2", src, "a second verification pass is required")

    def test_placement_never_raises_without_dbus(self):
        with unittest.mock.patch.object(self.place, "is_wayland", return_value=True), \
             unittest.mock.patch.object(self.place, "_dbus_tool", return_value=None):
            self.assertEqual(self.place.assign([{"title": "x", "output": "DP-1"}]), 0)


class TestSaverFullscreenSafety(unittest.TestCase):
    """Per-monitor fullscreen must be skipped where it would crash GLFW."""

    def test_wayland_skips_the_fullscreen_switch(self):
        from unittest.mock import patch
        import liquid_player_visualizer as lpv
        with patch("wayland_window_place.is_wayland", return_value=True):
            self.assertFalse(
                lpv.LiquidVisualizerOpenGLRenderer._saver_fullscreen_supported()
            )

    def test_x11_uses_the_fullscreen_switch(self):
        from unittest.mock import patch
        import liquid_player_visualizer as lpv
        with patch("wayland_window_place.is_wayland", return_value=False):
            self.assertTrue(
                lpv.LiquidVisualizerOpenGLRenderer._saver_fullscreen_supported()
            )


class TestPixelReduction(unittest.TestCase):
    def test_saver_marches_80_percent_of_the_cap(self):
        capped = (1920, 1200)
        got = vis.saver_target_resolution(*capped)
        ratio = (got[0] * got[1]) / float(capped[0] * capped[1])
        self.assertAlmostEqual(ratio, vis.SAVER_PIXEL_SCALE, delta=0.02)

    def test_pixel_scale_constant_is_80_percent(self):
        self.assertAlmostEqual(vis.SAVER_PIXEL_SCALE, 0.80)

    def test_console_river_skips_one_column_in_five(self):
        src = (ROOT / "MP_Audio_Player.py").read_text(encoding="utf-8")
        self.assertIn("SOF_CELL_BUDGET = 0.80", src)
        self.assertIn("skip_mod = 5", src)


if __name__ == "__main__":
    unittest.main()
