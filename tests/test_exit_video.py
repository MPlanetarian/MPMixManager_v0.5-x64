#!/usr/bin/env python3
"""The MP exit movie plays once, for the last MP Gui / MP Liquid Player window.

`Exit_MP_Video.mp4` is shown by the same player script as the startup logo, so
the two behave identically. These tests pin the four things that make the
feature correct:

  * the MP4 ships next to MP_loader.mp4 and the loader resolves it,
  * the movie is played with the loader's own window flags,
  * only the last window of *either* role is elected (closing one of two open
    windows stays silent), and
  * every Liquid Glass host closes its own window first, then runs the movie.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import exit_video as exit_video_mod
import mp_instance_limit as limiter

SCRIPT = ROOT / "scripts" / "display_loader_video.sh"

HOSTS = (
    "MP_Audio_Player_Liquid_Glass_OpenGL.py",
    "MP_Audio_Player_Liquid_Glass.py",
    "MP_Audio_Player_Liquid_Glass_macOS.py",
    "MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py",
)


class LimiterCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(
            setattr, os.environ, "MP_INSTANCE_STATE_DIR",
            os.environ.get("MP_INSTANCE_STATE_DIR", ""),
        )
        os.environ["MP_INSTANCE_STATE_DIR"] = self._tmp.name

    def table(self, *cmdlines):
        return [(100 + index, cmdline) for index, cmdline in enumerate(cmdlines)]

    def close_all_markers(self):
        for path in (Path(self._tmp.name) / "closing").glob("*.closing"):
            path.unlink()


class TestExitVideoAsset(unittest.TestCase):
    def test_the_mp4_sits_beside_the_loader_video(self):
        video = ROOT / "Exit_MP_Video.mp4"
        self.assertTrue(video.is_file())
        self.assertGreater(video.stat().st_size, 1024)
        # MP4 magic: a size word followed by the ftyp box.
        self.assertEqual(video.read_bytes()[4:8], b"ftyp")

    def test_the_mp4_is_a_real_h264_movie(self):
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name",
             "-of", "default=nw=1:nk=1", str(ROOT / "Exit_MP_Video.mp4")],
            capture_output=True, text=True,
        )
        if probe.returncode != 0:
            self.skipTest("ffprobe is not installed")
        self.assertIn("h264", probe.stdout)

    def test_the_loader_script_resolves_the_exit_movie(self):
        res = subprocess.run(
            ["bash", str(SCRIPT), "--exit-path"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(res.stdout.strip().endswith("Exit_MP_Video.mp4"))

    def test_config_example_documents_the_switch(self):
        text = (ROOT / "config.env.example").read_text(encoding="utf-8")
        self.assertIn('SHOW_EXIT_VIDEO="true"', text)
        self.assertIn('EXIT_VIDEO_FILE="Exit_MP_Video.mp4"', text)
        self.assertIn("EXIT_VIDEO_MAX_SECONDS", text)


class TestLoaderReuse(unittest.TestCase):
    def test_the_exit_movie_uses_the_loader_player_flags(self):
        """Same borderless, always-on-top window as MP_loader.mp4."""
        fake = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", fake]))
        log = Path(fake) / "args.txt"
        player = Path(fake) / "ffplay"
        player.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> "{log}"\n'
            "sleep 0.2\n"
            "exit 0\n",
            encoding="utf-8",
        )
        player.chmod(0o755)
        res = subprocess.run(
            ["bash", str(SCRIPT), "--exit"],
            capture_output=True, text=True, cwd=str(ROOT),
            env={**os.environ, "PATH": f"{fake}:/usr/bin:/bin",
                 "MP_EXIT_VIDEO_NO_TTY": "1", "EXIT_VIDEO_MAX_SECONDS": "5"},
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        args = log.read_text(encoding="utf-8") if log.exists() else ""
        self.assertIn("-noborder", args)
        self.assertIn("-alwaysontop", args)
        self.assertIn("-autoexit", args)
        self.assertIn("Exit_MP_Video.mp4", args)

    def test_the_exit_switch_turns_the_movie_off(self):
        fake = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", fake]))
        log = Path(fake) / "args.txt"
        player = Path(fake) / "ffplay"
        player.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\n', encoding="utf-8")
        player.chmod(0o755)
        res = subprocess.run(
            ["bash", str(SCRIPT), "--exit"],
            capture_output=True, text=True, cwd=str(ROOT),
            env={**os.environ, "PATH": f"{fake}:/usr/bin:/bin",
                 "SHOW_EXIT_VIDEO": "false", "MP_EXIT_VIDEO_NO_TTY": "1"},
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertFalse(log.exists(), "the movie started although SHOW_EXIT_VIDEO=false")

    def test_the_loader_video_still_plays(self):
        res = subprocess.run(
            ["bash", str(SCRIPT), "--path"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(res.stdout.strip().endswith("MP_loader.mp4"))


class TestLastWindowElection(LimiterCase):
    def test_one_of_two_open_windows_stays_silent(self):
        hosts = self.table(
            "python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py --manager",
            "python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py",
        )
        with patch.object(limiter, "process_table", return_value=hosts), \
             patch.object(limiter, "_pid_alive", return_value=True):
            self.assertFalse(limiter.is_last_window("gui", 100))

    def test_the_second_of_two_windows_plays_the_movie(self):
        hosts = self.table(
            "python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py --manager",
            "python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py",
        )
        with patch.object(limiter, "process_table", return_value=hosts), \
             patch.object(limiter, "_pid_alive", return_value=True):
            self.assertFalse(limiter.is_last_window("gui", 100))
            # The Gui marked itself; only the Player is left.
            self.assertTrue(limiter.is_last_window("player", 101))

    def test_gui_and_player_are_counted_together(self):
        """Closing MP Gui must not play while MP Liquid Player is still open."""
        hosts = self.table("python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py")
        with patch.object(limiter, "process_table", return_value=hosts), \
             patch.object(limiter, "_pid_alive", return_value=True):
            self.assertFalse(limiter.is_last_window("gui", 900))

    def test_an_unrelated_window_does_not_count(self):
        hosts = self.table(
            "python3 /opt/MP_Liquid_Visualizer.py --preset random",
            "bash /opt/Mix_Archive_Manager.sh",
            "python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py --tracklist /tmp/a.txt",
        )
        with patch.object(limiter, "process_table", return_value=hosts), \
             patch.object(limiter, "_pid_alive", return_value=True):
            self.assertTrue(limiter.is_last_window("player", 500))

    def test_the_marker_is_cleared_after_the_movie(self):
        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True):
            self.assertTrue(limiter.is_last_window("player", 4321))
        marker = Path(os.environ["MP_INSTANCE_STATE_DIR"]) / "closing" / "4321.closing"
        self.assertTrue(marker.exists())
        limiter.end_closing("player", 4321)
        self.assertFalse(marker.exists())

    def test_a_dead_marker_does_not_block_a_later_window(self):
        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True):
            self.assertTrue(limiter.is_last_window("player", 111))
        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=False):
            # 111 is gone, so its marker is dropped rather than blocking 222.
            self.assertTrue(limiter.is_last_window("player", 222))
        markers = {p.stem for p in (Path(os.environ["MP_INSTANCE_STATE_DIR"]) / "closing").glob("*.closing")}
        self.assertNotIn("111", markers)


class TestHostsCloseFirstThenPlayIt(unittest.TestCase):
    def test_every_host_runs_the_movie_in_its_shutdown_path(self):
        for name in HOSTS:
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("from exit_video import ExitVideo", text, name)
            # Three hosts close and then play through one helper; the macOS
            # OpenGL host tears down inline and plays through its own.
            self.assertTrue(
                "_close_then_exit_video" in text or "_play_exit_video_after_close" in text,
                f"{name} has no close-then-play path",
            )
            self.assertIn("video.finish()", text, name)

    def test_the_window_is_destroyed_before_the_movie_starts(self):
        """Closing the player must disappear at once, not wait out the movie."""
        for name in ("MP_Audio_Player_Liquid_Glass_OpenGL.py", "MP_Audio_Player_Liquid_Glass.py"):
            text = (ROOT / name).read_text(encoding="utf-8")
            fn = text.split("def _close_then_exit_video", 1)[1].split("\n    def ", 1)[0]
            self.assertIn("video.start()", fn, name)
            self.assertLess(fn.index("self.close()"), fn.index("video.start()"), name)

    def test_the_macos_glass_host_tears_down_before_the_movie(self):
        """The macOS host tears down inline, so the order is in its finally."""
        text = (ROOT / "MP_Audio_Player_Liquid_Glass_macOS.py").read_text(encoding="utf-8")
        fn = text.split("def _close_then_exit_video", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("video.start()", fn)
        self.assertLess(fn.index("self.close("), fn.index("video.start()"))

    def test_the_macos_opengl_host_tears_down_before_the_movie(self):
        text = (ROOT / "MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py").read_text(encoding="utf-8")
        body = text.split("finally:", 1)[1].split("_play_exit_video_after_close()", 1)[0]
        # Teardown is complete, including the renderer, before the movie runs.
        self.assertIn("self.renderer.close()", body)
        self.assertIn("self.renderer = None", body)
        self.assertLess(body.index("self.renderer.close()"), len(body))

    def test_no_host_pumps_its_own_frames_during_the_movie(self):
        """The glass is gone by then, so nothing should be drawing a frame."""
        for name in HOSTS:
            text = (ROOT / name).read_text(encoding="utf-8")
            fn = text.split("_close_then_exit_video", 1)[-1] if "def _close_then_exit_video" in text else ""
            with self.subTest(host=name):
                self.assertNotIn("_pump_exit_frame", text, name)
                self.assertNotIn("video.wait(lambda", fn, name)

    def test_tracklist_and_embed_check_stay_silent(self):
        for name in HOSTS:
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn('"MANAGER"', text, name)
            self.assertNotIn('mode in ("TRACKLIST", "MANAGER")', text, name)


class TestExitVideoModule(LimiterCase):
    def test_manager_mode_counts_as_gui(self):
        self.assertEqual(exit_video_mod.role_for_mode("MANAGER"), "gui")
        self.assertEqual(exit_video_mod.role_for_mode("NORMAL"), "player")
        self.assertEqual(exit_video_mod.role_for_mode(None), "player")

    def test_the_movie_runs_without_a_host_pump(self):
        """The window is already gone, so `wait()` must stand alone.

        Passing a pump would draw frames for a destroyed window, so the hosts
        call `video.wait()` with no argument. A movie that is still running
        must then be waited on, and a finished one must return at once.
        """
        state = {"polls": 0}

        class FakeProc:
            pid = 888

            def poll(self):
                state["polls"] += 1
                # Still running for two polls, then finished.
                return None if state["polls"] < 3 else 0

            def terminate(self):
                pass

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass

        def fake_popen(_argv, **_kwargs):
            return FakeProc()

        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True), \
             patch.object(subprocess, "Popen", side_effect=fake_popen):
            video = exit_video_mod.ExitVideo("NORMAL", timeout=20)
            self.assertTrue(video.start())
            video.wait()
            video.finish()
        # Three polls to see the movie finish, plus one from finish()/stop().
        self.assertGreaterEqual(state["polls"], 3)

    def test_the_movie_is_launched_through_the_loader_script(self):
        calls = []

        class FakeProc:
            pid = 777

            def poll(self):
                return 0

            def terminate(self):
                pass

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass

        def fake_popen(argv, **kwargs):
            calls.append((argv, kwargs))
            return FakeProc()

        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True), \
             patch.object(subprocess, "Popen", side_effect=fake_popen):
            video = exit_video_mod.ExitVideo("MANAGER", timeout=20)
            self.assertTrue(video.start())
            video.finish()

        self.assertEqual(len(calls), 1)
        argv, kwargs = calls[0]
        self.assertEqual(Path(argv[1]), SCRIPT)
        self.assertIn("--exit", argv)
        self.assertEqual(kwargs["env"]["MP_EXIT_VIDEO_NO_TTY"], "1")
        self.assertEqual(kwargs["env"]["EXIT_VIDEO_MAX_SECONDS"], "20")

    def test_a_second_window_is_not_elected(self):
        hosts = self.table("python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py")
        with patch.object(limiter, "process_table", return_value=hosts), \
             patch.object(limiter, "_pid_alive", return_value=True):
            video = exit_video_mod.ExitVideo("NORMAL")
            self.assertFalse(video.start())
            self.assertIsNone(video.process)

    def test_the_switch_is_read_without_sourcing_the_file(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", tmp]))
        cfg = Path(tmp) / "config.env"
        cfg.write_text('SHOW_EXIT_VIDEO="false"\n', encoding="utf-8")
        self.assertFalse(exit_video_mod.exit_video_enabled(Path(tmp)))
        cfg.write_text('SHOW_EXIT_VIDEO="true"\n', encoding="utf-8")
        self.assertTrue(exit_video_mod.exit_video_enabled(Path(tmp)))

    def test_an_environment_value_wins_over_the_file(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", tmp]))
        (Path(tmp) / "config.env").write_text('SHOW_EXIT_VIDEO="false"\n', encoding="utf-8")
        with patch.dict(os.environ, {"SHOW_EXIT_VIDEO": "true"}):
            self.assertTrue(exit_video_mod.exit_video_enabled(Path(tmp)))

    def test_the_timeout_has_a_sane_floor(self):
        with patch.dict(os.environ, {"EXIT_VIDEO_MAX_SECONDS": "1"}):
            self.assertGreaterEqual(exit_video_mod.exit_video_timeout(), 5)
        with patch.dict(os.environ, {"EXIT_VIDEO_MAX_SECONDS": "nonsense"}):
            self.assertEqual(exit_video_mod.exit_video_timeout(), 90)


if __name__ == "__main__":
    unittest.main()


class TestClosingTheLastWindowStopsTheAudio(unittest.TestCase):
    """The daemon outlived the window that owned it, so the mix played on.

    The Liquid Player host runs `MP_Audio_Player.py`, which opens the audio
    device and keeps it open. Destroying the glass never told it to stop, so
    closing the player left the mix playing to an empty desktop. The closing
    movie is silent, so once the last window is gone there is nothing left to
    play to.
    """

    def test_a_read_only_probe_exists_and_marks_nothing(self):
        """It must be askable before the movie election, which marks."""
        source = (ROOT / "scripts" / "exit_video.py").read_text(encoding="utf-8")
        body = source.split("def is_last_window_open", 1)[1].split("\ndef ", 1)[0]
        # Check the code, not the prose that explains it.
        code = body.split('"""', 2)[-1]
        self.assertNotIn("_closing_file(", code)
        self.assertNotIn("should_play_exit_video", code)

    def test_the_probe_answers_both_ways(self):
        me = os.getpid()
        cmd = "python3 MP_Audio_Player_Liquid_Glass_OpenGL.py"
        real = limiter.process_table
        try:
            limiter.process_table = lambda: [(me, cmd)]
            self.assertTrue(exit_video_mod.is_last_window_open("NORMAL"))
            limiter.process_table = lambda: [(me, cmd), (4242, cmd)]
            self.assertFalse(exit_video_mod.is_last_window_open("NORMAL"))
        finally:
            limiter.process_table = real

    def test_every_host_stops_the_audio_after_the_movie(self):
        for name in HOSTS:
            text = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(host=name):
                self.assertIn("stop_player_audio", text, name)
                self.assertIn("is_last_window_open", text, name)

    def test_the_audio_stop_runs_after_the_movie_not_before(self):
        """The movie is silent, but the mix should stop when it is over."""
        for name in HOSTS:
            text = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(host=name):
                movie = text.index("video.finish()")
                stop = text.index("stop_player_audio()")
                self.assertLess(movie, stop, f"{name} stops the audio too early")

    def test_the_last_window_probe_is_read_before_the_election(self):
        """Electing marks the window closing, so asking afterwards always says last."""
        for name in HOSTS:
            text = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(host=name):
                if "is_last_window_open" not in text:
                    continue
                probe = text.index("is_last_window_open(")
                start = text.index("video.start()", probe)
                self.assertLess(probe, start, f"{name} probes after electing")

    def test_a_second_open_window_keeps_playing(self):
        """Closing one of two player windows must leave the other's audio up."""
        for name in HOSTS:
            text = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(host=name):
                # The stop is gated, never unconditional.
                self.assertIn("if last:", text, name)

    def test_the_tracklist_viewer_never_stops_the_audio(self):
        """It shares the player's audio and must not silence it."""
        for name in HOSTS:
            text = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(host=name):
                for line in text.splitlines():
                    if "stop_player_audio()" in line:
                        context = text[:text.index(line)].rsplit("\n", 2)[-2:]
                        joined = " ".join(context)
                        self.assertNotIn('"TRACKLIST"', joined, name)

    def test_stop_player_audio_sends_the_daemons_own_stop(self):
        """`stop` saves the resume point and closes the stream; a bare kill does not."""
        source = (ROOT / "scripts" / "liquid_visualizer_session.py").read_text(encoding="utf-8")
        body = source.split("def stop_player_audio", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_send_player_stop()", body)
        # The daemon answers `stop` by retiring itself; the pid sweep is a
        # backstop for a wedged or older daemon.
        self.assertIn("_playback_engine_pids()", body)
