#!/usr/bin/env python3
"""The installer plays the theme track and loops the exit movie until it exits.

Both have to stop when the installer finishes, including when the player
ignores SIGTERM and when the installer's EXIT trap is not what ends the
watcher. A player that dies at once must not be restarted forever.
"""

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from tests.mp_test_path import system_path
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "install.sh"
WATCH = ROOT / "scripts" / "install_accompaniment.sh"
THEME = "MPlanetarian - The Orbits of Planets (Cosmic Dreamworlds Remake).flac"


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_until(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _log_lines(path):
    if not path.is_file():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _pids(lines):
    found = []
    for line in lines:
        head = line.split(" ", 1)[0]
        if head.isdigit():
            found.append(int(head))
    return found


def _write_fake(path, log, mode):
    if mode == "ignore-term":
        body = (
            "#!/bin/sh\n"
            f'printf "%s\\n" "$$ $*" >> "{log}"\n'
            "trap '' TERM\n"
            "while true; do sleep 30; done\n"
        )
    elif mode == "exit":
        body = (
            "#!/bin/sh\n"
            f'printf "%s\\n" "$$ $*" >> "{log}"\n'
            "exit 0\n"
        )
    else:
        body = (
            "#!/bin/sh\n"
            f'printf "%s\\n" "$$ $*" >> "{log}"\n'
            "sleep 60\n"
        )
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


class AccompanimentCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.log = self.dir / "players.log"
        self.pidfile = self.dir / "accomp.pid"
        self.player = self.dir / "ffplay"
        self.installer = None
        self.watcher = None

    def tearDown(self):
        for proc in (self.watcher, self.installer):
            if proc is None or proc.poll() is not None:
                continue
            proc.send_signal(signal.SIGKILL)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        for pid in _pids(_log_lines(self.log)):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def start_installer(self):
        self.installer = subprocess.Popen(
            ["sleep", "60"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return self.installer.pid

    def start_watcher(self, display=":99"):
        env = os.environ.copy()
        env["PATH"] = system_path(self.dir)
        env["MP_INSTALL_PLAYER_DIR"] = str(self.dir)
        env["TMPDIR"] = str(self.dir)
        env.pop("WAYLAND_DISPLAY", None)
        if display is None:
            env.pop("DISPLAY", None)
        else:
            env["DISPLAY"] = display
        self.watcher = subprocess.Popen(
            [
                "bash", str(WATCH),
                "--watch", str(self.installer.pid),
                "--pidfile", str(self.pidfile),
            ],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def video_lines(self):
        return [line for line in _log_lines(self.log) if "Exit_MP_Video.mp4" in line]

    def audio_lines(self):
        return [line for line in _log_lines(self.log) if "Orbits of Planets" in line]


class TestWatchClosesWhenTheInstallerExits(AccompanimentCase):
    @unittest.skipIf(sys.platform == "darwin",
                     "macOS plays the movie through AVFoundation, not ffplay")
    def test_theme_and_movie_start_and_both_die_with_the_installer(self):
        _write_fake(self.player, self.log, "sleep")
        self.start_installer()
        self.start_watcher()
        self.assertTrue(
            _wait_until(lambda: len(self.video_lines()) >= 1 and len(self.audio_lines()) >= 1, 5),
            _log_lines(self.log),
        )
        video = self.video_lines()[0].split()
        audio = self.audio_lines()[0].split()
        self.assertIn("-loop", video)
        self.assertIn("0", video)
        self.assertIn("-volume", video)
        self.assertIn("-noborder", video)
        self.assertIn("-alwaysontop", video)
        self.assertNotIn("-an", video)
        self.assertNotIn("-autoexit", video)
        self.assertIn("-nodisp", audio)
        self.assertIn("-vn", audio)
        self.assertIn("-loop", audio)
        self.assertNotIn("-volume", audio)
        before = len(_log_lines(self.log))
        pids = _pids(_log_lines(self.log))
        self.installer.send_signal(signal.SIGKILL)
        self.installer.wait(timeout=2)
        self.assertTrue(
            _wait_until(lambda: all(not _alive(pid) for pid in pids), 4),
            f"players still alive: {[pid for pid in pids if _alive(pid)]}",
        )
        self.assertTrue(
            _wait_until(lambda: self.watcher.poll() is not None, 4),
            "watcher kept running after the installer died",
        )
        time.sleep(0.4)
        self.assertEqual(len(_log_lines(self.log)), before, "a player was started after the installer died")
        self.assertTrue(all(not _alive(pid) for pid in pids))

    @unittest.skipIf(sys.platform == "darwin",
                     "macOS plays the movie through AVFoundation, not ffplay")
    def test_a_player_that_ignores_term_is_killed(self):
        _write_fake(self.player, self.log, "ignore-term")
        self.start_installer()
        self.start_watcher()
        self.assertTrue(_wait_until(lambda: len(_log_lines(self.log)) >= 2, 5), _log_lines(self.log))
        pids = _pids(_log_lines(self.log))
        self.assertTrue(pids)
        self.watcher.send_signal(signal.SIGTERM)
        self.assertTrue(
            _wait_until(lambda: self.watcher.poll() is not None and all(not _alive(pid) for pid in pids), 4),
            f"watcher={self.watcher.poll()} alive={[pid for pid in pids if _alive(pid)]}",
        )

    def test_a_player_that_exits_immediately_is_not_restarted_forever(self):
        _write_fake(self.player, self.log, "exit")
        self.start_installer()
        self.start_watcher()
        self.assertTrue(_wait_until(lambda: len(_log_lines(self.log)) >= 2, 5), _log_lines(self.log))

        def settled():
            count = len(_log_lines(self.log))
            time.sleep(1.0)
            return len(_log_lines(self.log)) == count and count >= 2

        self.assertTrue(_wait_until(settled, 8), _log_lines(self.log))
        count = len(_log_lines(self.log))
        self.assertLessEqual(count, 24, "the watcher kept relaunching a dead player")
        self.installer.send_signal(signal.SIGKILL)
        self.installer.wait(timeout=2)
        self.assertTrue(_wait_until(lambda: self.watcher.poll() is not None, 4))

    def test_without_a_display_the_theme_still_plays_and_no_window_opens(self):
        _write_fake(self.player, self.log, "sleep")
        self.start_installer()
        self.start_watcher(display=None)
        self.assertTrue(_wait_until(lambda: len(self.audio_lines()) >= 1, 5), _log_lines(self.log))
        self.assertEqual(self.video_lines(), [])
        pids = _pids(self.audio_lines())
        self.installer.send_signal(signal.SIGKILL)
        self.installer.wait(timeout=2)
        self.assertTrue(_wait_until(lambda: all(not _alive(pid) for pid in pids), 4))


class TestInstallerWiring(unittest.TestCase):
    def test_scripts_parse(self):
        for path in (INSTALL, WATCH, ROOT / "scripts" / "display_loader_video.sh"):
            result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_help_does_not_start_playback(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        log = Path(work.name) / "players.log"
        player = Path(work.name) / "ffplay"
        _write_fake(player, log, "sleep")
        env = os.environ.copy()
        env.update({
            "PATH": system_path(work.name),
            "MP_INSTALL_PLAYER_DIR": work.name,
            "MP_INSTALL_ACCOMPANIMENT": "1",
            "DISPLAY": ":99",
            "TMPDIR": work.name,
        })
        env.pop("WAYLAND_DISPLAY", None)
        result = subprocess.run(
            ["bash", str(INSTALL), "--help"],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MP_INSTALL_ACCOMPANIMENT=0", result.stdout)
        self.assertEqual(_log_lines(log), [])

    @unittest.skipIf(sys.platform == "darwin",
                     "macOS plays the movie through AVFoundation, not ffplay")
    def test_the_installer_stops_playback_when_it_exits(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        log = Path(work.name) / "players.log"
        player = Path(work.name) / "ffplay"
        _write_fake(player, log, "ignore-term")
        env = os.environ.copy()
        env.update({
            "PATH": system_path(work.name),
            "MP_INSTALL_PLAYER_DIR": work.name,
            "MP_INSTALL_ACCOMPANIMENT": "1",
            "MP_INSTALL_ACCOMPANIMENT_SELF_TEST": "1",
            "MP_INSTALL_ACCOMPANIMENT_SELF_TEST_SLEEP": "4",
            "DISPLAY": ":99",
            "TMPDIR": work.name,
        })
        env.pop("WAYLAND_DISPLAY", None)
        proc = subprocess.Popen(
            ["bash", str(INSTALL)],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        started = _wait_until(
            lambda: len([line for line in _log_lines(log) if "Exit_MP_Video.mp4" in line]) >= 1
            and len([line for line in _log_lines(log) if "Orbits of Planets" in line]) >= 1,
            8,
        )
        self.assertTrue(started, _log_lines(log))
        pids = _pids(_log_lines(log))
        stdout, stderr = proc.communicate(timeout=15)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertIn("Theme track playing", stdout)
        self.assertIn("Exit movie looping", stdout)
        self.assertNotIn("Detecting Host", stdout)
        self.assertTrue(
            _wait_until(lambda: all(not _alive(pid) for pid in pids), 4),
            f"installer exited and left players: {[pid for pid in pids if _alive(pid)]}",
        )

    def test_silence_switch_skips_the_track_and_the_movie(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        log = Path(work.name) / "players.log"
        player = Path(work.name) / "ffplay"
        _write_fake(player, log, "sleep")
        env = os.environ.copy()
        env.update({
            "PATH": system_path(work.name),
            "MP_INSTALL_PLAYER_DIR": work.name,
            "MP_INSTALL_ACCOMPANIMENT": "0",
            "MP_INSTALL_ACCOMPANIMENT_SELF_TEST": "1",
            "MP_INSTALL_ACCOMPANIMENT_SELF_TEST_SLEEP": "0.2",
            "DISPLAY": ":99",
            "TMPDIR": work.name,
        })
        result = subprocess.run(
            ["bash", str(INSTALL)],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Theme track playing", result.stdout)
        self.assertEqual(_log_lines(log), [])

    def test_the_theme_file_is_the_release_track(self):
        self.assertTrue((ROOT / "MP_SAMPLES" / THEME).is_file())
        text = WATCH.read_text(encoding="utf-8")
        self.assertIn(THEME, text)
        self.assertIn("-volume 0", (ROOT / "scripts" / "display_loader_video.sh").read_text(encoding="utf-8"))
        self.assertNotIn('"-an"', text)


@unittest.skipUnless(
    os.environ.get("MP_INSTALL_ACCOMPANIMENT_LIVE") == "1" and os.environ.get("DISPLAY"),
    "set MP_INSTALL_ACCOMPANIMENT_LIVE=1 on a machine with a display",
)
class TestLivePlayersClose(unittest.TestCase):
    def test_real_ffplay_window_and_track_stop_when_the_installer_dies(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        pidfile = Path(work.name) / "accomp.pid"
        installer = subprocess.Popen(["sleep", "60"])
        self.addCleanup(lambda: installer.poll() is None and installer.kill())
        env = os.environ.copy()
        env["TMPDIR"] = work.name
        env.pop("MP_INSTALL_PLAYER_DIR", None)
        watcher = subprocess.Popen(
            ["bash", str(WATCH), "--watch", str(installer.pid), "--pidfile", str(pidfile)],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(lambda: watcher.poll() is None and watcher.kill())

        def pids_ready():
            if not pidfile.is_file():
                return False
            fields = {}
            for line in pidfile.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    fields[key] = value
            video = fields.get("video", "")
            audio = fields.get("audio", "")
            return video.isdigit() and audio.isdigit() and _alive(int(video)) and _alive(int(audio))

        self.assertTrue(_wait_until(pids_ready, 6), pidfile.read_text(encoding="utf-8") if pidfile.is_file() else "no pidfile")
        fields = {}
        for line in pidfile.read_text(encoding="utf-8").splitlines():
            key, value = line.split("=", 1)
            fields[key] = value
        video = int(fields["video"])
        audio = int(fields["audio"])
        video_cmd = Path(f"/proc/{video}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        audio_cmd = Path(f"/proc/{audio}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        self.assertIn("-loop", video_cmd)
        self.assertIn("-volume", video_cmd)
        self.assertIn("Exit_MP_Video.mp4", video_cmd)
        self.assertIn("-nodisp", audio_cmd)
        self.assertIn("-vn", audio_cmd)
        self.assertIn("Orbits of Planets", audio_cmd)
        installer.send_signal(signal.SIGKILL)
        installer.wait(timeout=2)
        self.assertTrue(_wait_until(lambda: not _alive(video) and not _alive(audio), 4))
        self.assertTrue(_wait_until(lambda: watcher.poll() is not None, 4))


if __name__ == "__main__":
    unittest.main()
