#!/usr/bin/env python3
"""The mixer's decks must be able to play the Music Folders library.

Reported symptom: in MP Liquid Mixer, loading a track on deck A or B "does
nothing" and no audio plays. The decks browse the Music Folders library, which
is generated from ``~/MUSIC_SMB/<id>/...`` paths. Those paths are rclone FUSE
mounts that nothing starts on a normal desktop login, so every row failed
``os.path.isfile`` inside ``AudioEngine.load_track`` and the deck stayed silent
with no message.

These tests pin the three parts of the fix:

* ``mixer_core.ensure_music_folders_mounted`` mounts the shares and reports
  what is reachable, without ever raising or hanging.
* ``mixer_core.library_available`` is the single answer to "can this row play".
* ``MP_Audio_Player._play_selected_row`` attempts the mount for an
  unreachable row and explains an offline library instead of doing nothing.
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIXER_DIR = PROJECT_ROOT / "MP_Liquid_Mixer"
for path in (str(MIXER_DIR), str(PROJECT_ROOT), str(PROJECT_ROOT / "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

import mixer_core  # noqa: E402


def _folder(fid="DATAMAC1", share="DATAMAC1", host="192.0.2.20"):
    return {
        "type": "smb", "id": fid, "name": fid, "host": host,
        "share": share, "subpath": "", "user": "",
    }


def _config(mount_root):
    return {
        "smb_user": "", "smb_password": "", "legacy_host": "",
        "mount_root": Path(mount_root), "playlists_dir": Path(mount_root),
        "base_dir": Path(mount_root),
    }


class _FakeMusicModule:
    """Stands in for music_folders_manager with a controllable share."""

    def __init__(self, folders, mount_root, reachable):
        self._folders = folders
        self._config = _config(mount_root)
        self._reachable = reachable
        self.connect_calls = []

    def load_music_config(self):
        return self._config

    def load_folders(self, _config=None):
        return [dict(f) for f in self._folders]

    def connect_folder(self, folder, _config, interactive=False):
        self.connect_calls.append((folder.get("id"), interactive))
        return (bool(self._reachable), "mounted" if self._reachable else "offline")


class TestMountHelper(unittest.TestCase):
    def setUp(self):
        # The helper caches its last attempt for 30s; a test must start clean.
        mixer_core._MUSIC_MOUNT_STATE.update(
            attempted_at=0.0, online=0, total=0, detail="")

    def test_reports_online_shares(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = _FakeMusicModule([_folder(), _folder("DATAMAC2")], tmp, True)
            with patch.object(mixer_core, "_music_folders_module", return_value=fake):
                status = mixer_core.ensure_music_folders_mounted(force=True)
        self.assertEqual(status["online"], 2)
        self.assertEqual(status["total"], 2)
        self.assertIn("2/2", status["detail"])
        self.assertEqual(len(fake.connect_calls), 2)
        # Never ask rclone's mount path to prompt during a window launch.
        self.assertTrue(all(interactive is False for _fid, interactive in fake.connect_calls))

    def test_offline_shares_are_named_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = _FakeMusicModule([_folder(), _folder("MUSIC003")], tmp, False)
            with patch.object(mixer_core, "_music_folders_module", return_value=fake):
                status = mixer_core.ensure_music_folders_mounted(force=True)
        self.assertEqual(status["online"], 0)
        self.assertEqual(status["total"], 2)
        self.assertIn("offline", status["detail"])
        self.assertIn("DATAMAC1", status["detail"])

    def test_no_configured_folders_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = _FakeMusicModule([], tmp, True)
            with patch.object(mixer_core, "_music_folders_module", return_value=fake):
                status = mixer_core.ensure_music_folders_mounted(force=True)
        self.assertEqual(status["online"], 0)
        self.assertEqual(status["total"], 0)
        self.assertIn("no smb", status["detail"].lower())

    def test_a_missing_helper_module_degrades_quietly(self):
        with patch.object(mixer_core, "_music_folders_module", return_value=None):
            status = mixer_core.ensure_music_folders_mounted(force=True)
        self.assertEqual(status["online"], 0)
        self.assertIn("not found", status["detail"])

    def test_a_broken_config_is_reported_not_raised(self):
        broken = MagicMock()
        broken.load_music_config.side_effect = OSError("config.env unreadable")
        with patch.object(mixer_core, "_music_folders_module", return_value=broken):
            status = mixer_core.ensure_music_folders_mounted(force=True)
        self.assertEqual(status["online"], 0)
        self.assertIn("could not read", status["detail"])

    def test_result_is_cached_between_launch_polls(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = _FakeMusicModule([_folder()], tmp, True)
            with patch.object(mixer_core, "_music_folders_module", return_value=fake):
                mixer_core.ensure_music_folders_mounted(force=True)
                mixer_core.ensure_music_folders_mounted()
        # The status poll calls this often; a second rclone mount per call would
        # be a process storm.
        self.assertEqual(len(fake.connect_calls), 1)

    def test_mounting_is_bounded_by_the_deadline(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            fake = _FakeMusicModule([_folder()], tmp, True)

            def slow_connect(folder, config, interactive=False):
                time.sleep(3.0)
                return True, "mounted"

            fake.connect_folder = slow_connect
            started = time.monotonic()
            with patch.object(mixer_core, "_music_folders_module", return_value=fake):
                status = mixer_core.ensure_music_folders_mounted(force=True, deadline_sec=0.3)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 2.0)
        self.assertEqual(status["total"], 1)
        self.assertEqual(status["online"], 0)


class TestLibraryAvailable(unittest.TestCase):
    def test_a_real_file_is_available(self):
        with tempfile.NamedTemporaryFile(suffix=".flac") as handle:
            self.assertTrue(mixer_core.library_available(handle.name))

    def test_a_missing_file_is_not(self):
        self.assertFalse(mixer_core.library_available("/nonexistent/track.flac"))

    def test_an_empty_path_is_not_available(self):
        self.assertFalse(mixer_core.library_available(""))
        self.assertFalse(mixer_core.library_available(None))


class TestPlayerRowSelection(unittest.TestCase):
    """Enter on an unreachable row must mount or explain, never go quiet."""

    def setUp(self):
        os.environ.setdefault("MP_DISABLE_AUDIO_OUTPUT", "1")
        os.environ.setdefault("MP_DISABLE_BLUETOOTH_MONITOR", "1")
        import MP_Audio_Player as player
        self.player = player

    def _ui(self, path):
        ui = self.player.TerminalPlayerUI.__new__(self.player.TerminalPlayerUI)
        ui.selected_row = 0
        ui.search_query = ""
        ui.is_remote = False
        ui.engine = MagicMock()
        ui.engine.playlist = []
        ui.notifications = []
        ui._notify = lambda msg, dur=3.0: ui.notifications.append(msg)
        ui._send_cmd = MagicMock()
        ui.scanner = MagicMock()
        ui.scanner.get_mixes.return_value = [{"name": os.path.basename(path), "path": path}]
        ui._get_active_tracks = lambda: ui.scanner.get_mixes()
        return ui

    def test_an_unreachable_row_states_that_the_file_is_missing(self):
        path = "/nonexistent/MUSIC/track.flac"
        ui = self._ui(path)
        with patch.object(self.player, "music_share_for_path", return_value=None), \
             patch.object(self.player, "ensure_music_share_for_path") as mount:
            ui._play_selected_row()
        mount.assert_not_called()
        self.assertTrue(ui.notifications, "the deck must say why nothing played")
        self.assertIn("Cannot play", ui.notifications[-1])
        ui._send_cmd.assert_not_called()

    def test_an_unreachable_row_on_a_known_share_tries_the_mount(self):
        path = "/home/user/MUSIC_SMB/DATAMAC1/MUSIC/track.flac"
        ui = self._ui(path)
        folder = _folder()
        # The file stays missing after the attempt, so nothing is played.
        with patch.object(self.player, "music_share_for_path", return_value=folder), \
             patch.object(self.player, "ensure_music_share_for_path", return_value=False) as mount:
            ui._play_selected_row()
            deadline = time.time() + 2.0
            while time.time() < deadline and not mount.called:
                time.sleep(0.01)
        mount.assert_called_once_with(path)
        self.assertTrue(any("Mounting" in message for message in ui.notifications))
        self.assertIn("offline", ui.notifications[-1].lower())
        ui._send_cmd.assert_not_called()

    def test_the_mount_attempt_does_not_block_the_deck(self):
        """A wedged share can take 45s; Enter must return immediately."""
        path = "/home/user/MUSIC_SMB/DATAMAC1/MUSIC/track.flac"
        ui = self._ui(path)
        release = threading.Event()

        def slow_mount(_target):
            release.wait(5.0)
            return False

        with patch.object(self.player, "music_share_for_path", return_value=_folder()), \
             patch.object(self.player, "ensure_music_share_for_path", side_effect=slow_mount):
            started = time.monotonic()
            ui._play_selected_row()
            elapsed = time.monotonic() - started
            release.set()
        self.assertLess(elapsed, 1.0)
        self.assertTrue(any("Mounting" in message for message in ui.notifications))

    def test_a_mount_that_succeeds_plays_the_track(self):
        with tempfile.NamedTemporaryFile(suffix=".flac") as handle:
            ui = self._ui(handle.name)
            ui._play_selected_row()
        ui._send_cmd.assert_called_once()
        args, kwargs = ui._send_cmd.call_args
        self.assertEqual(args[0], "play")
        self.assertEqual(kwargs.get("file"), handle.name)

    def test_a_deferred_mount_that_succeeds_plays_the_track(self):
        path = "/home/user/MUSIC_SMB/DATAMAC1/MUSIC/track.flac"
        ui = self._ui(path)
        with patch.object(self.player, "music_share_for_path", return_value=_folder()), \
             patch.object(self.player, "ensure_music_share_for_path", return_value=True):
            ui._play_selected_row()
            deadline = time.time() + 2.0
            while time.time() < deadline and not ui._send_cmd.called:
                time.sleep(0.01)
        ui._send_cmd.assert_called_once()
        args, kwargs = ui._send_cmd.call_args
        self.assertEqual(args[0], "play")
        self.assertEqual(kwargs.get("file"), path)

    def test_a_reachable_row_is_not_delayed_by_a_mount(self):
        with tempfile.NamedTemporaryFile(suffix=".flac") as handle:
            ui = self._ui(handle.name)
            with patch.object(self.player, "ensure_music_share_for_path") as mount:
                ui._play_selected_row()
        mount.assert_not_called()
        ui._send_cmd.assert_called_once()

    def test_the_mount_hint_names_the_folder_and_the_fix(self):
        with patch.object(self.player, "music_share_for_path",
                          return_value=_folder("DATAMAC3")):
            hint = self.player.unavailable_media_hint(
                "/home/user/MUSIC_SMB/DATAMAC3/MUSIC/track.flac")
        self.assertIn("DATAMAC3", hint)
        self.assertIn("offline", hint)
        self.assertIn("Music Folders", hint)


class TestEngineReport(unittest.TestCase):
    """A refused track must leave a reason for the UI, not just a False."""

    def setUp(self):
        os.environ.setdefault("MP_DISABLE_AUDIO_OUTPUT", "1")
        os.environ.setdefault("MP_DISABLE_BLUETOOTH_MONITOR", "1")
        import MP_Audio_Player as player
        self.player = player

    def test_a_missing_file_records_an_actionable_reason(self):
        engine = self.player.AudioEngine(
            [{"name": "gone.flac", "path": "/nonexistent/gone.flac"}],
            auto_bluetooth=False, mode="MUSIC")
        try:
            self.assertFalse(engine.load_track(0, True))
            self.assertTrue(engine.last_load_error)
            self.assertIn("Cannot play", engine.last_load_error)
            self.assertEqual(engine.get_status_dict()["last_load_error"],
                             engine.last_load_error)
        finally:
            engine.shutdown()

    def test_a_good_load_clears_the_previous_reason(self):
        with tempfile.NamedTemporaryFile(suffix=".wav") as handle:
            import wave
            with wave.open(handle.name, "wb") as wav:
                wav.setnchannels(2)
                wav.setsampwidth(2)
                wav.setframerate(8000)
                wav.writeframes(b"\x00\x00" * 2 * 800)
            engine = self.player.AudioEngine(
                [{"name": os.path.basename(handle.name), "path": handle.name}],
                auto_bluetooth=False, mode="MUSIC")
            try:
                engine.last_load_error = "stale reason"
                self.assertTrue(engine.load_track(0, False))
                self.assertEqual(engine.last_load_error, "")
            finally:
                engine.shutdown()

    def test_a_zero_byte_file_is_refused_not_played_silently(self):
        """A 0-byte file passes isfile() and "opens" as 44100 Hz / 0 frames.

        That made load_track return True and the deck report "playing" at
        00:00 forever, which is indistinguishable from the reported symptom.
        """
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".mp3") as handle:
            engine = self.player.AudioEngine(
                [{"name": os.path.basename(handle.name), "path": handle.name}],
                auto_bluetooth=False, mode="MUSIC")
            try:
                self.assertFalse(engine.load_track(0, True))
                self.assertEqual(engine.state, "stopped")
                self.assertIn("empty", engine.last_load_error.lower())
                self.assertIn("0 bytes", engine.last_load_error)
            finally:
                engine.shutdown()


class TestMixerMountWiring(unittest.TestCase):
    """The mixer must mount the library and recover the decks' rows."""

    def setUp(self):
        sys.path.insert(0, str(MIXER_DIR))
        import MP_Liquid_Mixer as app
        self.app = app

    def _mixer(self):
        mixer = self.app.LiquidMixer()
        mixer.area = None
        mixer.decks = {
            "A": {"namespace": "mixerdeck_a", "online": True},
            "B": {"namespace": "mixerdeck_b", "online": True},
        }
        return mixer

    def test_a_launch_kicks_off_the_mount_on_a_worker(self):
        mixer = self._mixer()
        calls = []

        def fake_mount(force=False, deadline_sec=20.0):
            calls.append(force)
            return {"online": 2, "total": 6, "detail": "2/6 music shares mounted"}

        with patch.object(self.app.core, "ensure_music_folders_mounted", side_effect=fake_mount), \
             patch("gi.repository.GLib.idle_add", return_value=False):
            mixer._mount_music_library()
            deadline = time.time() + 2.0
            while time.time() < deadline and not calls:
                time.sleep(0.01)
        self.assertEqual(calls, [True])

    def test_a_successful_mount_reloads_both_deck_playlists(self):
        mixer = self._mixer()
        mixer._library_status = {"online": 2, "total": 6, "detail": "2/6 mounted"}
        sent = []
        with patch("mixer_core.send_ipc",
                   side_effect=lambda ns, cmd, **kw: sent.append((ns, cmd, kw)) or {"status": "ok"}):
            mixer._on_library_mounted()
        reloads = [(ns, kw) for ns, cmd, kw in sent if cmd == "load_playlist"]
        self.assertEqual(len(reloads), 2)
        self.assertEqual({ns for ns, _kw in reloads}, {"mixerdeck_a", "mixerdeck_b"})
        for _ns, kw in reloads:
            self.assertEqual(kw.get("mode"), "MUSIC")
            self.assertFalse(kw.get("play"))
        self.assertEqual(mixer._library_error, "")

    def test_a_fully_offline_library_is_reported_on_the_decks(self):
        mixer = self._mixer()
        mixer._library_status = {
            "online": 0, "total": 2,
            "detail": "0/2 music shares mounted (offline: DATAMAC1, DATAMAC2)",
        }
        sent = []
        with patch("mixer_core.send_ipc",
                   side_effect=lambda ns, cmd, **kw: sent.append((ns, cmd)) or {"status": "ok"}):
            mixer._on_library_mounted()
        self.assertEqual(mixer._library_error, mixer._library_status["detail"])
        self.assertNotIn("load_playlist", [cmd for _ns, cmd in sent])

    def test_no_configuration_is_not_an_error_on_screen(self):
        mixer = self._mixer()
        mixer._library_status = {"online": 0, "total": 0,
                                 "detail": "no SMB Music Folders configured"}
        with patch("mixer_core.send_ipc", return_value={"status": "ok"}):
            mixer._on_library_mounted()
        # A local-only library has nothing to mount; it must not be labelled
        # "offline", which would confuse a user whose tracks are all local.
        self.assertEqual(mixer._library_error, "")


class TestMusicShareMapping(unittest.TestCase):
    def test_a_row_under_a_share_maps_to_that_folder(self):
        import MP_Audio_Player as player
        with tempfile.TemporaryDirectory() as tmp:
            folders = [_folder("DATAMAC1"), _folder("MUSIC003", "EXT_004_P1")]
            fake = _FakeMusicModule(folders, tmp, True)
            path = os.path.join(tmp, "MUSIC003", "MUSIC", "track.flac")
            with patch.dict(sys.modules, {"music_folders_manager": fake}):
                found = player.music_share_for_path(path)
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], "MUSIC003")

    def test_a_local_row_maps_to_no_share(self):
        import MP_Audio_Player as player
        with tempfile.TemporaryDirectory() as tmp:
            fake = _FakeMusicModule([_folder()], tmp, True)
            with patch.dict(sys.modules, {"music_folders_manager": fake}):
                self.assertIsNone(player.music_share_for_path(os.path.join(tmp, "local.flac")))


if __name__ == "__main__":
    unittest.main()
