#!/usr/bin/env python3
"""The first MP Gui and the first MP Liquid Player play Exit_MP_Video.mp4.

The movie is the same one the last window plays on the way out, shown in the
same borderless always-on-top window. It plays once and that window closes
before the glass appears. These tests pin the contract:

  * the **first** window of each kind plays it before the glass exists,
  * windows **two, three and four** of that kind open straight away and never
    show it,
  * the intro is **once per session, not once per install**, so coming back
    later shows it again,
  * the tracklist viewer, the embed check, VR Mode and the benchmark and soak
    harnesses never show it, and
  * every host wires it in before the window (and its companion visualizer).
"""

import os
import subprocess
import sys
import tempfile
import unittest
from tests.mp_test_path import system_path
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import exit_video as intro_mod
import mp_instance_limit as limiter

SCRIPT = ROOT / "scripts" / "display_loader_video.sh"

HOSTS = (
    "MP_Audio_Player_Liquid_Glass_OpenGL.py",
    "MP_Audio_Player_Liquid_Glass.py",
    "MP_Audio_Player_Liquid_Glass_macOS.py",
    "MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py",
)


def _main_body(text: str) -> str:
    """The body of main(), whether it is `def main(` or `def main():`."""
    for marker in ("def main(", "def main:"):
        if marker in text:
            return text.split(marker, 1)[1]
    raise AssertionError("host has no main()")


class LimiterCase(unittest.TestCase):
    """Every case gets its own claim directory, so sessions never overlap."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(
            setattr, os.environ, "MP_INSTANCE_STATE_DIR",
            os.environ.get("MP_INSTANCE_STATE_DIR", ""),
        )
        os.environ["MP_INSTANCE_STATE_DIR"] = self._tmp.name
        # An environment switch in the developer's own shell must not decide
        # these assertions.
        for key in ("MP_NO_PLAYER_INTRO", "MP_NO_GUI_INTRO", "MP_BENCHMARK_MODE",
                    "MP_VR_MODE", "SHOW_PLAYER_INTRO_VIDEO", "MP_PLAYER_INTRO_DONE",
                    "MP_GUI_INTRO_DONE"):
            self.addCleanup(os.environ.pop, key, None)
            os.environ.pop(key, None)
        self.addCleanup(setattr, os, "environ", os.environ)

    def table(self, *cmdlines):
        return [(100 + index, cmdline) for index, cmdline in enumerate(cmdlines)]


class TestFirstWindowElection(LimiterCase):
    def test_the_first_window_is_chosen_when_no_other_player_is_open(self):
        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True):
            self.assertTrue(intro_mod.is_first_player_window(pid=4242))

    def test_a_second_window_is_not_the_first(self):
        hosts = self.table("python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py")
        with patch.object(limiter, "process_table", return_value=hosts), \
             patch.object(limiter, "_pid_alive", return_value=True):
            self.assertFalse(intro_mod.is_first_player_window(pid=4242))

    def test_three_open_windows_still_leave_the_first_window_flag_off(self):
        hosts = self.table(*["python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py"] * 3)
        with patch.object(limiter, "process_table", return_value=hosts), \
             patch.object(limiter, "_pid_alive", return_value=True):
            self.assertFalse(intro_mod.is_first_player_window(pid=4242))

    def test_the_callers_own_claim_is_not_counted_against_it(self):
        """A launcher claims a slot before exec, so the host must not see two."""
        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True):
            self.assertTrue(limiter.claim_instance("player", pid=4242))
            # The claim is this process's own slot, not a sibling window.
            self.assertTrue(intro_mod.is_first_player_window(pid=4242))
            limiter.release_instance("player", 4242)

    def test_a_gui_window_does_not_stop_the_player_intro(self):
        hosts = self.table("python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py --manager")
        with patch.object(limiter, "process_table", return_value=hosts), \
             patch.object(limiter, "_pid_alive", return_value=True):
            self.assertTrue(intro_mod.is_first_player_window(pid=4242))

    def test_a_live_player_claim_counts_as_a_window(self):
        """A second shortcut pressed during the intro is already a window."""
        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True):
            self.assertTrue(limiter.claim_instance("player", pid=5151))
            self.assertFalse(intro_mod.is_first_player_window(pid=4242))

    def test_the_intro_comes_back_on_a_later_session(self):
        """Once per session, not once per install: all windows closed, so again."""
        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True):
            self.assertTrue(intro_mod.is_first_player_window(pid=6001))
            # The session's windows have all closed; nothing remembers them.
            limiter.release_all(6001)
            self.assertTrue(intro_mod.is_first_player_window(pid=6002))


class TestFirstGuiWindowElection(LimiterCase):
    def test_the_first_gui_is_chosen_when_no_other_gui_is_open(self):
        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True):
            self.assertTrue(intro_mod.is_first_gui_window(pid=4242))

    def test_a_second_gui_is_not_the_first(self):
        hosts = self.table("python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py --manager")
        with patch.object(limiter, "process_table", return_value=hosts), \
             patch.object(limiter, "_pid_alive", return_value=True):
            self.assertFalse(intro_mod.is_first_gui_window(pid=4242))

    def test_three_open_gui_windows_still_leave_the_flag_off(self):
        hosts = self.table(*["python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py --manager"] * 3)
        with patch.object(limiter, "process_table", return_value=hosts), \
             patch.object(limiter, "_pid_alive", return_value=True):
            self.assertFalse(intro_mod.is_first_gui_window(pid=4242))

    def test_the_callers_own_gui_claim_is_not_counted_against_it(self):
        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True):
            self.assertTrue(limiter.claim_instance("gui", pid=4242))
            self.assertTrue(intro_mod.is_first_gui_window(pid=4242))
            limiter.release_instance("gui", 4242)

    def test_a_player_window_does_not_stop_the_gui_intro(self):
        hosts = self.table("python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py")
        with patch.object(limiter, "process_table", return_value=hosts), \
             patch.object(limiter, "_pid_alive", return_value=True):
            self.assertTrue(intro_mod.is_first_gui_window(pid=4242))

    def test_a_live_gui_claim_counts_as_a_window(self):
        """A second shortcut pressed during the intro is already a window."""
        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True):
            self.assertTrue(limiter.claim_instance("gui", pid=5151))
            self.assertFalse(intro_mod.is_first_gui_window(pid=4242))

    def test_the_gui_intro_comes_back_on_a_later_session(self):
        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True):
            self.assertTrue(intro_mod.is_first_gui_window(pid=6001))
            limiter.release_all(6001)
            self.assertTrue(intro_mod.is_first_gui_window(pid=6002))


class TestWhichLaunchesGetTheIntro(LimiterCase):
    def test_a_plain_player_window_wants_the_intro(self):
        for mode in ("NORMAL", "MINI", "MICRO"):
            self.assertTrue(intro_mod.player_intro_wanted(mode), mode)

    def test_the_tracklist_and_helpers_do_not(self):
        for mode in ("MANAGER", "TRACKLIST", "", None, "EMBED"):
            self.assertFalse(intro_mod.player_intro_wanted(mode), mode)

    def test_the_manager_window_wants_the_gui_intro(self):
        self.assertTrue(intro_mod.gui_intro_wanted("MANAGER"))
        for mode in ("NORMAL", "MINI", "MICRO", "TRACKLIST", "", None):
            self.assertFalse(intro_mod.gui_intro_wanted(mode), mode)

    def test_a_launcher_that_already_played_the_gui_movie_is_not_repeated(self):
        with patch.dict(os.environ, {"MP_GUI_INTRO_DONE": "1"}):
            self.assertFalse(intro_mod.gui_intro_wanted("MANAGER"))

    def test_the_switch_turns_it_off(self):
        with patch.dict(os.environ, {"MP_NO_PLAYER_INTRO": "1"}):
            self.assertFalse(intro_mod.player_intro_wanted("NORMAL"))

    def test_benchmark_and_vr_runs_skip_it(self):
        """A movie would offset the timing harness and cover the VR canvas."""
        with patch.dict(os.environ, {"MP_BENCHMARK_MODE": "1"}):
            self.assertFalse(intro_mod.player_intro_wanted("NORMAL"))
        with patch.dict(os.environ, {"MP_VR_MODE": "1"}):
            self.assertFalse(intro_mod.player_intro_wanted("NORMAL"))

    def test_the_config_switch_is_read_without_sourcing_the_file(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", tmp]))
        cfg = Path(tmp) / "config.env"
        cfg.write_text('SHOW_PLAYER_INTRO_VIDEO="false"\n', encoding="utf-8")
        self.assertFalse(intro_mod.intro_video_enabled(Path(tmp)))
        cfg.write_text('SHOW_PLAYER_INTRO_VIDEO="true"\n', encoding="utf-8")
        self.assertTrue(intro_mod.intro_video_enabled(Path(tmp)))

    def test_an_environment_value_wins_over_the_file(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", tmp]))
        (Path(tmp) / "config.env").write_text('SHOW_PLAYER_INTRO_VIDEO="false"\n', encoding="utf-8")
        with patch.dict(os.environ, {"SHOW_PLAYER_INTRO_VIDEO": "true"}):
            self.assertTrue(intro_mod.intro_video_enabled(Path(tmp)))

    def test_the_timeout_has_a_sane_floor(self):
        with patch.dict(os.environ, {"PLAYER_INTRO_VIDEO_MAX_SECONDS": "1"}):
            self.assertGreaterEqual(intro_mod.intro_video_timeout(), 5)
        with patch.dict(os.environ, {"PLAYER_INTRO_VIDEO_MAX_SECONDS": "nonsense"}):
            self.assertEqual(intro_mod.intro_video_timeout(), 90)
        with patch.dict(os.environ, {"PLAYER_INTRO_VIDEO_MAX_SECONDS": "12"}):
            self.assertEqual(intro_mod.intro_video_timeout(), 12)


class TestPlayerIntroRunsTheMovie(LimiterCase):
    class FakeProc:
        def __init__(self):
            self.returncode = 0

        def poll(self):
            return 0

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    def test_the_intro_uses_the_loader_script_and_the_exit_movie(self):
        calls = []

        def fake_popen(argv, **kwargs):
            calls.append((argv, kwargs))
            return self.FakeProc()

        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True), \
             patch.object(subprocess, "Popen", side_effect=fake_popen):
            played = intro_mod.play_player_intro("NORMAL", timeout=20)

        self.assertTrue(played)
        self.assertEqual(len(calls), 1)
        argv, kwargs = calls[0]
        self.assertEqual(Path(argv[1]), SCRIPT)
        self.assertIn(intro_mod.INTRO_SCRIPT_ARG, argv)
        self.assertEqual(kwargs["env"]["PLAYER_INTRO_VIDEO_MAX_SECONDS"], "20")

    def test_a_second_window_never_starts_the_movie(self):
        hosts = self.table("python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py")
        with patch.object(limiter, "process_table", return_value=hosts), \
             patch.object(limiter, "_pid_alive", return_value=True), \
             patch.object(subprocess, "Popen") as popen:
            self.assertFalse(intro_mod.play_player_intro("NORMAL"))
        popen.assert_not_called()

    def test_the_switch_stops_the_movie_from_starting(self):
        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True), \
             patch.dict(os.environ, {"SHOW_PLAYER_INTRO_VIDEO": "false"}), \
             patch.object(subprocess, "Popen") as popen:
            self.assertFalse(intro_mod.play_player_intro("NORMAL"))
        popen.assert_not_called()

    def test_the_tracklist_never_starts_the_movie(self):
        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True), \
             patch.object(subprocess, "Popen") as popen:
            self.assertFalse(intro_mod.play_player_intro("TRACKLIST"))
        popen.assert_not_called()

    def test_the_first_gui_starts_the_exit_movie_once(self):
        calls = []

        def fake_popen(argv, **kwargs):
            calls.append((argv, kwargs))
            return self.FakeProc()

        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True), \
             patch.object(subprocess, "Popen", side_effect=fake_popen):
            played = intro_mod.play_player_intro("MANAGER", timeout=20)

        self.assertTrue(played)
        self.assertEqual(len(calls), 1)
        argv, kwargs = calls[0]
        self.assertEqual(Path(argv[1]), SCRIPT)
        self.assertIn(intro_mod.INTRO_SCRIPT_ARG, argv)
        self.assertEqual(kwargs["env"]["PLAYER_INTRO_VIDEO_MAX_SECONDS"], "20")

    def test_a_second_gui_never_starts_the_movie(self):
        hosts = self.table("python3 /opt/MP_Audio_Player_Liquid_Glass_OpenGL.py --manager")
        with patch.object(limiter, "process_table", return_value=hosts), \
             patch.object(limiter, "_pid_alive", return_value=True), \
             patch.object(subprocess, "Popen") as popen:
            self.assertFalse(intro_mod.play_player_intro("MANAGER"))
        popen.assert_not_called()

    def test_a_later_gui_claim_never_starts_the_movie(self):
        """Instance three is the same as instance two: no movie, ever."""
        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True), \
             patch.object(subprocess, "Popen") as popen:
            self.assertTrue(limiter.claim_instance("gui", pid=5151))
            self.assertTrue(limiter.claim_instance("gui", pid=5152))
            self.assertFalse(intro_mod.play_player_intro("MANAGER"))
        popen.assert_not_called()

    def test_the_intro_is_not_replayed_by_a_second_call(self):
        """One window, one movie, even if a host asks twice."""
        calls = []

        def fake_popen(argv, **kwargs):
            calls.append(argv)
            return self.FakeProc()

        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True), \
             patch.object(subprocess, "Popen", side_effect=fake_popen):
            intro = intro_mod.PlayerIntro("NORMAL", timeout=20)
            self.assertTrue(intro.play())
            self.assertTrue(intro.play())
        self.assertEqual(len(calls), 1)


class TestLoaderScriptIntroMode(unittest.TestCase):
    def test_the_loader_resolves_a_real_movie_for_the_intro(self):
        res = subprocess.run(
            ["bash", str(SCRIPT), "--exit-path"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(res.stdout.strip().endswith("Exit_MP_Video.mp4"))

    @unittest.skipIf(sys.platform == "darwin",
                     "macOS uses the AVFoundation borderless helper")
    def test_the_intro_mode_uses_the_borderless_player_flags(self):
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
            ["bash", str(SCRIPT), "--intro"],
            capture_output=True, text=True, cwd=str(ROOT),
            env={**os.environ, "PATH": system_path(fake),
                 "DISPLAY": ":99",
                 "PLAYER_INTRO_VIDEO_MAX_SECONDS": "5"},
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        args = log.read_text(encoding="utf-8") if log.exists() else ""
        self.assertIn("-noborder", args)
        self.assertIn("-alwaysontop", args)
        self.assertIn("-autoexit", args)
        self.assertIn("Exit_MP_Video.mp4", args)

    def test_the_intro_switch_turns_the_movie_off(self):
        fake = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", fake]))
        log = Path(fake) / "args.txt"
        player = Path(fake) / "ffplay"
        player.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\n', encoding="utf-8")
        player.chmod(0o755)
        res = subprocess.run(
            ["bash", str(SCRIPT), "--intro"],
            capture_output=True, text=True, cwd=str(ROOT),
            env={**os.environ, "PATH": system_path(fake),
                 "SHOW_PLAYER_INTRO_VIDEO": "false"},
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertFalse(log.exists(), "the movie started although the switch is false")

    def test_the_exit_movie_and_the_intro_share_one_file(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("display_player_intro_video", text)
        self.assertIn("display_exit_video", text)
        intro = text.split("display_player_intro_video()", 1)[1].split("\ndisplay_exit_video", 1)[0]
        self.assertIn("find_exit_video_path", intro)
        self.assertIn("_launch_borderless_video", intro)


class TestStalledMovieCannotHoldTheWindow(unittest.TestCase):
    """The window used to wait for the 90 s ceiling when ffplay stalled.

    On this machine `ffplay -autoexit` sometimes never exits at the end of the
    movie (a stalled PipeWire/Bluetooth sink), and sometimes drops out after a
    second. Waiting for the ceiling made the first launch appear to do nothing
    for a minute and a half after a 40 s movie had finished.
    """

    def _probe(self, script: str) -> str:
        res = subprocess.run(
            ["bash", "-c", script], cwd=str(ROOT),
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        return res.stdout

    def test_the_wait_is_bounded_by_the_movie_not_the_ceiling(self):
        out = self._probe(r"""
            source scripts/display_loader_video.sh
            vid="$SCRIPT_DIR/Exit_MP_Video.mp4"
            echo "DUR=$(_video_duration_seconds "$vid")"
            echo "LIMIT=$(_video_wait_limit "$vid" 90)"
        """)
        duration = int(dict(line.split("=") for line in out.split() if "=" in line)["DUR"])
        limit = int(dict(line.split("=") for line in out.split() if "=" in line)["LIMIT"])
        # The clip is 10 s since 0.5.3; the point of this test is that the wait
        # tracks the movie rather than the 90 s ceiling, so only sanity-check
        # that a real duration was measured.
        self.assertGreater(duration, 0, out)
        self.assertLessEqual(duration, 60, out)
        # Movie plus a small grace, not the full 90 s ceiling.
        self.assertGreaterEqual(limit, duration)
        self.assertLess(limit, 90, out)
        self.assertLessEqual(limit, duration + 15, out)

    def test_a_smaller_ceiling_still_wins(self):
        # The clip is 10 s and the grace is 8 s, so the movie-derived limit is
        # 18 s. A ceiling below that must still win; the original test used 30 s
        # when the movie was 39 s long.
        out = self._probe(r"""
            source scripts/display_loader_video.sh
            echo "LIMIT=$(_video_wait_limit "$SCRIPT_DIR/Exit_MP_Video.mp4" 12)"
        """)
        self.assertIn("LIMIT=12", out)

    def test_a_missing_movie_falls_back_instead_of_crashing(self):
        out = self._probe(r"""
            source scripts/display_loader_video.sh
            echo "DUR=$(_video_duration_seconds /nonexistent.mp4)"
            echo "LIMIT=$(_video_wait_limit /nonexistent.mp4 90)"
        """)
        self.assertIn("DUR=45", out)
        self.assertIn("LIMIT=53", out)

    def test_a_stalled_player_is_stopped_after_the_movie(self):
        """A player that never exits must not be waited on for the ceiling."""
        start = __import__("time").monotonic()
        out = self._probe(r"""
            source scripts/display_loader_video.sh
            sleep 200 & hung=$!
            started=$SECONDS
            _wait_for_video_player "$hung" "$(_video_wait_limit "$SCRIPT_DIR/Exit_MP_Video.mp4" 90)"
            echo "WAITED=$(( SECONDS - started ))"
            kill -9 $hung 2>/dev/null || true
        """)
        waited = int(out.split("WAITED=")[1].split()[0])
        elapsed = __import__("time").monotonic() - start
        self.assertLess(elapsed, 80, "the stalled player was waited on for the ceiling")
        self.assertLess(waited, 90, out)

    def test_the_intro_and_the_exit_movie_both_use_the_bounded_wait(self):
        text = SCRIPT.read_text(encoding="utf-8")
        for fn in ("display_player_intro_video", "display_exit_video"):
            body = text.split(f"{fn}()", 1)[1].split("\n}", 1)[0]
            self.assertIn("_video_wait_limit", body, f"{fn} still waits for the ceiling")
            self.assertIn("_close_video_player", body, f"{fn} leaves a stalled player running")

    def test_a_skipped_movie_cannot_delay_the_window(self):
        """Esc/Space/Enter/q still dismiss the intro immediately."""
        started = __import__("time").monotonic()
        res = subprocess.run(
            ["bash", "-c",
             'source scripts/display_loader_video.sh; '
             '_loader_skip_requested() { return 0; }; '
             'sleep 300 & p=$!; '
             '_wait_for_video_player "$p" 90; '
             'echo SKIPPED; kill -9 $p 2>/dev/null || true'],
            cwd=str(ROOT), capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("SKIPPED", res.stdout)
        self.assertLess(__import__("time").monotonic() - started, 30)

    def test_no_tty_launch_does_not_spam_the_log(self):
        """Probing /dev/tty every tick wrote hundreds of errors per launch."""
        res = subprocess.run(
            ["bash", "-c",
             'source scripts/display_loader_video.sh; '
             'for i in 1 2 3; do _loader_skip_requested; done; echo DONE'],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30,
            stdin=subprocess.DEVNULL,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("DONE", res.stdout)
        self.assertNotIn("/dev/tty", res.stderr)


class TestHostsWireTheIntroFirst(unittest.TestCase):
    def test_every_host_imports_the_intro(self):
        for name in HOSTS:
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("play_player_intro", text, name)

    def test_every_host_has_a_stub_for_a_copied_tree(self):
        for name in HOSTS:
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("def play_player_intro(", text, name)

    def test_the_intro_runs_before_the_window_and_the_visualizer(self):
        """The movie must be the first thing on screen, not a sibling window."""
        for name in HOSTS:
            text = (ROOT / name).read_text(encoding="utf-8")
            fn = _main_body(text)
            self.assertIn("play_player_intro(", fn, name)
            # The three hosts that spawn their own companion visualizer must do
            # it after the movie; the macOS OpenGL host never starts one.
            if "ensure_random_visualizer_started()" in fn:
                self.assertLess(
                    fn.index("play_player_intro("),
                    fn.index("ensure_random_visualizer_started()"),
                    f"{name} starts the visualizer before the intro",
                )

    def test_the_manager_window_asks_for_the_intro(self):
        """MP Gui is a --manager host and the first one plays the movie."""
        for name in HOSTS:
            text = (ROOT / name).read_text(encoding="utf-8")
            fn = _main_body(text)
            intro_at = fn.index("play_player_intro(")
            window = fn[max(0, intro_at - 220):intro_at]
            self.assertNotIn("not manager", window, name)

    def test_the_config_example_documents_the_switch(self):
        text = (ROOT / "config.env.example").read_text(encoding="utf-8")
        self.assertIn('SHOW_PLAYER_INTRO_VIDEO="true"', text)
        self.assertIn("PLAYER_INTRO_VIDEO_MAX_SECONDS", text)


class TestLaunchersPlayItBeforeTheVisualizer(unittest.TestCase):
    """The Linux launchers run the movie before they start the backdrop."""

    LAUNCHERS = (
        "scripts/launch_liquid_glass.sh",
        "scripts/launch-liquid-glass",
    )

    def test_each_launcher_plays_the_intro(self):
        for name in self.LAUNCHERS:
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("mp_instance_is_first_player", text, name)
            self.assertIn('"$_mp_intro_sh" --intro', text, name)
            self.assertIn("display_loader_video.sh", text, name)

    def test_the_movie_runs_before_the_companion_visualizer(self):
        for name in self.LAUNCHERS:
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertLess(
                text.index('"$_mp_intro_sh" --intro'),
                text.index('"$VIS_PY"'),
                f"{name} starts the visualizer before the movie",
            )

    def test_the_launcher_tells_the_host_the_movie_has_played(self):
        """Otherwise the host's own election would show it a second time."""
        for name in self.LAUNCHERS:
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("MP_PLAYER_INTRO_DONE=1", text, name)

    def test_the_windows_launcher_does_the_same(self):
        text = (ROOT / "scripts" / "launch_liquid_glass.bat").read_text(encoding="utf-8")
        self.assertIn("play_player_intro", text)
        self.assertIn("MP_PLAYER_INTRO_DONE=1", text)
        self.assertLess(
            text.index("play_player_intro"),
            text.index('start "" "%PYTHONW_BIN%" "%VIS_PY%" --preset random'),
            "the .bat starts the visualizer before the movie",
        )

    def test_the_shell_helper_exposes_the_election(self):
        helper = (ROOT / "scripts" / "mp_instance_limit.sh").read_text(encoding="utf-8")
        self.assertIn("mp_instance_is_first_player()", helper)
        self.assertIn("mp_instance_is_first_gui()", helper)
        self.assertIn("--exclude-pid", helper)

    def test_helper_flags_get_no_movie(self):
        """`--self-test` and friends draw no window, so they must not wait."""
        for name in self.LAUNCHERS:
            text = (ROOT / name).read_text(encoding="utf-8")
            for flag in ("--self-test", "--embed-check", "--tracklist", "--set-font",
                         "--list-fonts", "--manager"):
                self.assertIn(flag, text, f"{name} would show the movie for {flag}")
            self.assertIn("_mp_intro_wanted", text, name)


class TestOverlappingWindowsInARealSession(LimiterCase):
    """Four real, simultaneously open windows: only the first gets the movie."""

    def _holding_processes(self, count: int):
        procs = [subprocess.Popen(["sleep", "60"]) for _ in range(count)]

        def stop():
            for proc in procs:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass

        self.addCleanup(stop)
        return procs

    def test_one_movie_for_four_overlapping_windows(self):
        procs = self._holding_processes(4)
        played = []
        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True):
            for index, proc in enumerate(procs):
                # Each launcher claims its own slot before exec, exactly as
                # scripts/mp_instance_limit.sh does.
                self.assertTrue(limiter.claim_instance("player", pid=proc.pid))
                if intro_mod.is_first_player_window(pid=proc.pid):
                    played.append(index + 1)
            # The fifth launch has no slot left.
            self.assertFalse(limiter.claim_instance("player", pid=os.getpid()))
        self.assertEqual(played, [1], "the movie played for a window other than the first")

    def test_the_movie_returns_after_every_window_closes(self):
        procs = self._holding_processes(2)
        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True):
            for proc in procs:
                self.assertTrue(limiter.claim_instance("player", pid=proc.pid))
            self.assertFalse(intro_mod.is_first_player_window(pid=procs[1].pid))
            for proc in procs:
                limiter.release_all(proc.pid)
            self.assertTrue(intro_mod.is_first_player_window(pid=procs[0].pid))

    def test_one_movie_for_four_overlapping_gui_windows(self):
        procs = self._holding_processes(4)
        played = []
        with patch.object(limiter, "process_table", return_value=[]), \
             patch.object(limiter, "_pid_alive", return_value=True):
            for index, proc in enumerate(procs):
                self.assertTrue(limiter.claim_instance("gui", pid=proc.pid))
                if intro_mod.is_first_gui_window(pid=proc.pid):
                    played.append(index + 1)
            self.assertFalse(limiter.claim_instance("gui", pid=os.getpid()))
        self.assertEqual(played, [1], "the movie played for a GUI other than the first")


class TestGuiLaunchersPlayItBeforeTheVisualizer(unittest.TestCase):
    def test_mp_gui_plays_the_intro_before_the_visualizer(self):
        text = (ROOT / "MP_Gui.sh").read_text(encoding="utf-8")
        self.assertIn("mp_instance_is_first_gui", text)
        self.assertIn('bash "$_mp_intro_sh" --intro', text)
        self.assertIn("MP_GUI_INTRO_DONE=1", text)
        self.assertLess(
            text.index('bash "$_mp_intro_sh" --intro'),
            text.index('"$VIS_PY"'),
            "MP Gui starts the visualizer before the movie",
        )

    def test_the_windows_gui_launcher_does_the_same(self):
        text = (ROOT / "scripts" / "launch_mp_gui.bat").read_text(encoding="utf-8")
        self.assertIn("play_player_intro('MANAGER')", text)
        self.assertIn("MP_GUI_INTRO_DONE=1", text)
        self.assertLess(
            text.index("play_player_intro('MANAGER')"),
            text.index("MP_Liquid_Visualizer.py"),
            "the .bat starts the visualizer before the movie",
        )


if __name__ == "__main__":
    unittest.main()
