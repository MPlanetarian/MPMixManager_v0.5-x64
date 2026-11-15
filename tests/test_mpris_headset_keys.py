#!/usr/bin/env python3
"""Headset play/pause reaches MP Liquid Player and does not open a video.

The physical button on a Bluetooth headset is a media key. These tests pin
the mapping from that key onto the audio engine, the refusal of a video
URI, and the collapse of a press that is delivered twice.
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import mp_mpris  # noqa: E402


class FakeEngine:
    def __init__(self):
        self.state = "playing"
        self.current_track = {"name": "Mix One", "path": "/music/mix one.flac"}
        self.duration_sec = 10.5
        self.position_sec = 1.25
        self.volume = 0.8
        self.muted = False
        self.shuffle = False
        self.repeat_mode = "all"
        self.playlist = [dict(self.current_track)]
        self.current_index = 0
        self.calls = []

    def play(self):
        self.calls.append("play")
        self.state = "playing"

    def pause(self):
        self.calls.append("pause")
        self.state = "paused"

    def stop_playback(self, reset_position=False):
        self.calls.append(("stop", reset_position))
        self.state = "stopped"

    def next_track(self):
        self.calls.append("next")

    def prev_track(self):
        self.calls.append("prev")

    def seek_relative(self, delta):
        self.calls.append(("seek_relative", delta))
        self.position_sec += float(delta)

    def seek(self, target, force=False):
        self.calls.append(("seek", target, force))
        self.position_sec = float(target)

    def set_volume(self, percent):
        self.calls.append(("volume", percent))
        self.volume = percent / 100.0
        self.muted = False

    def load_track(self, index, start_playing=True):
        self.calls.append(("load", index, self.playlist[index]["path"]))
        self.current_index = index
        self.current_track = self.playlist[index]
        self.state = "playing" if start_playing else "paused"
        return True

    def _sync_state_file(self):
        self.calls.append("sync")


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.debounce = Path(self.tmp.name) / "debounce.json"
        self.now = [1000.0]
        self.engine = FakeEngine()
        self.controller = mp_mpris.MprisController(
            self.engine,
            debounce_path=self.debounce,
            clock=lambda: self.now[0],
        )

    def test_play_pause_pauses_a_playing_mix_once(self):
        self.controller.play_pause()
        self.controller.play_pause()
        self.assertEqual(self.engine.calls, ["pause"])
        self.assertEqual(self.engine.state, "paused")

    def test_a_later_press_plays_again(self):
        self.controller.play_pause()
        self.now[0] += 1.0
        self.controller.play_pause()
        self.assertEqual(self.engine.calls, ["pause", "play"])
        self.assertEqual(self.engine.state, "playing")

    def test_pause_and_playpause_for_one_press_do_not_undo_each_other(self):
        # The desktop sends Pause because the mix is playing, and the
        # Bluetooth bridge sends PlayPause for the same press.
        self.controller.pause()
        self.controller.play_pause()
        self.assertEqual(self.engine.calls, ["pause"])
        self.assertEqual(self.engine.state, "paused")

    def test_play_while_paused_starts_playback(self):
        self.engine.state = "paused"
        self.controller.play()
        self.assertEqual(self.engine.calls, ["play"])

    def test_play_while_already_playing_pauses(self):
        # The headset button is a Play command, including while the mix
        # is already going. That is the press that used to start a video.
        self.controller.play()
        self.assertEqual(self.engine.calls, ["pause"])
        self.assertEqual(self.engine.state, "paused")

    def test_play_then_pause_for_one_press_stays_paused(self):
        self.controller.play()
        self.controller.pause()
        self.assertEqual(self.engine.calls, ["pause"])
        self.assertEqual(self.engine.state, "paused")

    def test_next_and_previous_are_separate_from_play(self):
        self.controller.next()
        self.controller.previous()
        self.controller.next()
        self.assertEqual(self.engine.calls, ["next", "prev"])

    def test_seek_moves_five_seconds_once(self):
        self.controller.seek_us(5_000_000)
        self.controller.seek_us(5_000_000)
        self.assertEqual(self.engine.calls, [("seek_relative", 5.0)])

    def test_stop_does_not_reset_the_position_flag_to_quit(self):
        self.controller.stop()
        self.assertEqual(self.engine.calls, [("stop", False)])
        self.assertEqual(self.engine.state, "stopped")

    def test_video_uri_is_refused(self):
        opened = self.controller.open_uri("file:///videos/clip.mp4")
        self.assertFalse(opened)
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(len(self.engine.playlist), 1)
        self.assertFalse(self.controller.open_uri("file:///videos/clip.mkv"))
        self.assertFalse(self.controller.open_uri("https://example.com/watch.mp4"))

    def test_audio_uri_loads_the_file(self):
        folder = Path(self.tmp.name)
        audio = folder / "new mix.flac"
        audio.write_bytes(b"not really flac")
        opened = self.controller.open_uri(audio.as_uri())
        self.assertTrue(opened)
        self.assertEqual(self.engine.calls[-1][0], "load")
        self.assertEqual(self.engine.current_track["path"], str(audio))

    def test_metadata_uses_microseconds_and_the_mix_title(self):
        snap = self.controller.snapshot()
        self.assertEqual(snap["PlaybackStatus"], "Playing")
        self.assertEqual(snap["LoopStatus"], "Playlist")
        self.assertEqual(snap["Metadata"]["xesam:title"], "Mix One")
        self.assertEqual(snap["Metadata"]["mpris:length"], 10_500_000)
        self.assertTrue(snap["Metadata"]["xesam:url"].endswith("mix%20one.flac"))
        self.assertEqual(snap["Volume"], 0.8)
        self.assertTrue(snap["CanPause"])
        self.assertTrue(snap["CanPlay"])

    def test_paused_and_stopped_status_names(self):
        self.assertEqual(mp_mpris.playback_status("paused"), "Paused")
        self.assertEqual(mp_mpris.playback_status("stopped"), "Stopped")
        self.assertEqual(mp_mpris.loop_status("one"), "Track")
        self.assertEqual(mp_mpris.loop_status("off"), "None")


class HeadsetKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.debounce = Path(self.tmp.name) / "debounce.json"
        self.sent = []
        self.now = [2000.0]

    def _send(self, cmd, **extra):
        self.sent.append((cmd, extra))
        return True

    def _press(self, keyval):
        return mp_mpris.dispatch_headset_key(
            keyval,
            debounce_path=self.debounce,
            sender=self._send,
            now=self.now[0],
        )

    def test_play_key_toggles_and_a_duplicate_does_not(self):
        self.assertTrue(self._press(mp_mpris.KEY_AUDIO_PLAY))
        self.assertTrue(self._press(mp_mpris.KEY_AUDIO_PLAY))
        self.assertEqual(self.sent, [("toggle", {})])

    def test_pause_next_and_seek_keys(self):
        self.assertTrue(self._press(mp_mpris.KEY_AUDIO_PAUSE))
        self.assertTrue(self._press(mp_mpris.KEY_AUDIO_NEXT))
        self.assertTrue(self._press(mp_mpris.KEY_AUDIO_REWIND))
        self.assertEqual(
            self.sent,
            [("pause", {}), ("next", {}), ("seek", {"seconds": -5.0})],
        )

    def test_a_letter_is_not_a_headset_key(self):
        self.assertFalse(self._press(ord("z")))
        self.assertFalse(self._press(ord(" ")))
        self.assertEqual(self.sent, [])

    def test_stop_uses_the_command_that_leaves_the_daemon_running(self):
        self.assertTrue(self._press(mp_mpris.KEY_AUDIO_STOP))
        self.assertEqual(self.sent, [("transport_stop", {})])


class WiringTests(unittest.TestCase):
    def test_the_daemon_publishes_headset_controls_after_the_track_loads(self):
        text = (ROOT / "MP_Audio_Player.py").read_text(encoding="utf-8")
        start = text.index("PlayerIPCServer(engine).start()")
        window = text[start:start + 400]
        self.assertIn("engine.load_track", window)
        self.assertLess(window.index("engine.load_track"), window.index("_start_headset_controls(engine)"))
        self.assertIn('cmd == "transport_stop"', text)
        for host in ("MP_Audio_Player_Liquid_Glass.py", "MP_Audio_Player_Liquid_Glass_OpenGL.py"):
            body = (ROOT / host).read_text(encoding="utf-8")
            self.assertIn("dispatch_headset_key", body)


class SessionBusTests(unittest.TestCase):
    def test_play_pause_over_dbus_pauses_the_engine(self):
        if not Path("/usr/bin/dbus-daemon").is_file():
            self.skipTest("dbus-daemon is not installed")
        daemon = subprocess.Popen(
            ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self._stop_private_bus, daemon)
        address = daemon.stdout.readline().decode().strip()
        self.assertTrue(address.startswith("unix:"))
        previous = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = address
        self.addCleanup(self._restore_bus, previous)

        engine = FakeEngine()
        debounce = Path(tempfile.mkdtemp(prefix="mp_mpris_bus_")) / "debounce.json"
        service = mp_mpris.start_mpris_service(
            engine,
            bus_name="org.mpris.MediaPlayer2.mp_liquid_player_test",
            debounce_path=debounce,
        )
        self.addCleanup(service.stop)
        self.assertTrue(service.ready.wait(3), "MPRIS thread did not start")
        self.assertIsNone(service.error, f"MPRIS failed: {service.error}")

        import dbus
        bus = dbus.bus.BusConnection(address)
        deadline = time.time() + 3
        while time.time() < deadline and not bus.name_has_owner(service._claimed_name):
            time.sleep(0.05)
        self.assertTrue(bus.name_has_owner(service._claimed_name))

        proxy = bus.get_object(service._claimed_name, mp_mpris.OBJECT_PATH)
        player = dbus.Interface(proxy, mp_mpris.PLAYER_IFACE)
        props = dbus.Interface(proxy, mp_mpris.PROP_IFACE)
        status = props.Get(mp_mpris.PLAYER_IFACE, "PlaybackStatus")
        self.assertEqual(str(status), "Playing")
        identity = props.Get(mp_mpris.ROOT_IFACE, "Identity")
        self.assertEqual(str(identity), "MP Liquid Player")
        player.PlayPause()
        self.assertEqual(engine.state, "paused")
        self.assertEqual(engine.calls, ["pause"])
        # The same press delivered again must not start the mix back up.
        player.PlayPause()
        self.assertEqual(engine.state, "paused")
        player.OpenUri("file:///tmp/not-a-real-video.mp4")
        self.assertEqual(engine.calls, ["pause"])
        bus.close()

    @staticmethod
    def _restore_bus(previous):
        if previous is None:
            os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)
        else:
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = previous

    @staticmethod
    def _stop_private_bus(daemon):
        if daemon.poll() is None:
            daemon.terminate()
            try:
                daemon.wait(timeout=2)
            except subprocess.TimeoutExpired:
                daemon.kill()
                try:
                    daemon.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        stdout = daemon.stdout
        if stdout is not None and not stdout.closed:
            stdout.close()


if __name__ == "__main__":
    unittest.main()
