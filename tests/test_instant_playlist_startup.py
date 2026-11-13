#!/usr/bin/env python3
"""Large playlists must open without a loading screen.

The desktop shortcut used to show "Loading library..." for seconds while the
55k-track DJ_Tracks_ALL_MUSIC_FOLDERS.json was parsed. These tests pin down the
two halves of the fix:

  * the playlist sidecar cache decodes the same rows faster than JSON, and
    refuses to serve anything that does not match the playlist it came from;
  * the interactive player paints its real first frame straight away, with no
    "Loading library..." splash and without waiting for the daemon socket.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import playlist_cache  # noqa: E402
import MP_Audio_Player as player  # noqa: E402


def _rows(count: int) -> list:
    return [
        {
            "name": f"Track {i}.flac",
            "title": f"Track {i}",
            "path": f"/music/folder/Track {i}.flac",
            "folder": "MUSIC001",
            "subpath": f"Track {i}.flac",
            "ext": "FLAC",
            "size_mb": 12.5 + i,
            "duration_fmt": "03:45",
            "mtime": 1700000000.0 + i,
            "_extra": "ignored by the cache" if i == 0 else "",
        }
        for i in range(count)
    ]


class PlaylistCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.playlist = self.dir / "DJ_Tracks_ALL_MUSIC_FOLDERS.json"
        self.rows = _rows(500)
        self.playlist.write_text(json.dumps(self.rows), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_cache_round_trip_keeps_every_field(self):
        self.assertTrue(playlist_cache.write(self.playlist, self.rows))
        cached = playlist_cache.read(self.playlist)
        self.assertIsNotNone(cached)
        self.assertEqual(len(cached), len(self.rows))
        for original, restored in zip(self.rows, cached):
            for field in playlist_cache.CACHE_FIELDS:
                self.assertEqual(
                    original.get(field, ""), restored.get(field, ""),
                    f"{field} changed across the cache",
                )
            self.assertIsInstance(restored["size_mb"], float)
            self.assertIsInstance(restored["mtime"], float)

    def test_cache_is_ignored_when_the_playlist_changes(self):
        playlist_cache.write(self.playlist, self.rows)
        # Same name, different content: the sidecar must not be reused.
        self.playlist.write_text(json.dumps(_rows(3)), encoding="utf-8")
        self.assertIsNone(playlist_cache.read(self.playlist))

    def test_truncated_cache_is_rejected(self):
        playlist_cache.write(self.playlist, self.rows)
        cache = playlist_cache.cache_path(self.playlist)
        blob = cache.read_bytes()
        cache.write_bytes(blob[: len(blob) // 2])
        self.assertIsNone(playlist_cache.read(self.playlist))

    def test_version_bump_invalidates_old_cache(self):
        playlist_cache.write(self.playlist, self.rows)
        with patch.object(playlist_cache, "CACHE_VERSION", playlist_cache.CACHE_VERSION + 1):
            self.assertIsNone(playlist_cache.read(self.playlist))

    def test_load_json_falls_back_and_refreshes_the_cache(self):
        loaded = playlist_cache.load_json(self.playlist)
        self.assertEqual(len(loaded), len(self.rows))
        self.assertTrue(all("duration_fmt" in row for row in loaded))
        cache = playlist_cache.cache_path(self.playlist)
        self.assertTrue(cache.is_file(), "the first open should write the sidecar")
        # A warm load must not need the JSON parser at all.
        with patch.object(playlist_cache.json, "loads", side_effect=AssertionError("json.loads used")):
            warm = playlist_cache.load_json(self.playlist)
        self.assertEqual(len(warm), len(self.rows))

    def test_reader_and_writer_agree_on_the_playlist_signature(self):
        size, mtime = playlist_cache.signature(self.playlist)
        self.assertEqual(size, self.playlist.stat().st_size)
        self.assertGreater(mtime, 0)


class PlaylistScannerCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.playlist = self.dir / "DJ_Tracks_MUSIC001.json"
        self.playlist.write_text(json.dumps(_rows(50)), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_music_folder_scanner_uses_the_cached_rows(self):
        with patch.object(player, "get_base_dir", return_value=self.dir):
            first = player.MusicFolderPlaylistScanner("MUSIC001", playlist_path=self.playlist)
            self.assertEqual(len(first.mixes), 50)
            self.assertTrue(playlist_cache.cache_path(self.playlist).is_file())
            with patch.object(player.json, "loads", side_effect=AssertionError("json.loads used")):
                second = player.MusicFolderPlaylistScanner("MUSIC001", playlist_path=self.playlist)
        self.assertEqual(len(second.mixes), 50)
        self.assertEqual(second.mixes[0]["title"], first.mixes[0]["title"])

    def test_search_cleanup_matches_the_old_regex(self):
        import re
        old = re.compile(r"[^a-z0-9\s]")
        samples = [
            "01-morphology-a1_darkstar_(the_hacker_remix).flac",
            "MPlanetarian — Ecology of the Earth",
            "Track 42 (Extended Mix) [FLAC]",
            "Ünïcode Álbum — السحر",
            "a" * 40 + "   spaces\tand\ttabs",
        ]
        table = player._search_clean_table()
        for text in samples:
            self.assertEqual(
                old.sub(" ", text.lower()).translate(table),
                text.lower().translate(table),
                f"cleanup drifted for {text!r}",
            )
            self.assertEqual(text.lower().translate(table), old.sub(" ", text.lower()))

    def test_repeat_searches_reuse_the_index(self):
        with patch.object(player, "get_base_dir", return_value=self.dir):
            scanner = player.MusicFolderPlaylistScanner("MUSIC001", playlist_path=self.playlist)
        with patch.object(player, "_search_index", wraps=player._search_index) as index:
            scanner.get_mixes("track 4")
            scanner.get_mixes("track 4")
            scanner.get_mixes("track 49")
        self.assertEqual(index.call_count, 1, "the search index was rebuilt per keystroke")


class InstantStartupTests(unittest.TestCase):
    """The interactive player must not paint a loading screen."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / ".config" / "mix-manager").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, argv, daemon=False, status=None):
        result_path = self.home / "result.json"
        audio = self.home / "mix.flac"
        audio.write_bytes(b"not really audio")
        scenario = {
            "argv": ["MP_Audio_Player.py"] + [part.replace("{audio}", str(audio)) for part in argv],
            "daemon": daemon,
            "status": status,
        }
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["MP_INSTANT_STARTUP_CHILD"] = "1"
        env["MP_SCENARIO"] = json.dumps(scenario)
        env["MP_RESULT"] = str(result_path)
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve())],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0 or not result_path.is_file():
            raise AssertionError(f"harness failed ({proc.returncode})\n{proc.stdout}\n{proc.stderr}")
        return json.loads(result_path.read_text(encoding="utf-8"))

    def test_interactive_startup_paints_no_loading_screen(self):
        data = self._run(["--interactive", "--music-mode"])
        self.assertTrue(data["ui"], data)
        self.assertNotIn(
            "Loading library", data["painted"],
            "the player painted the old Loading library splash",
        )
        # The UI must be built with a playlist already loaded: that is what makes
        # the very first frame the real player screen instead of a placeholder.
        self.assertEqual(data["ui_tracks"], 1, data)
        self.assertEqual(data["ui_mode"], "MUSIC", data)

    def test_startup_does_not_wait_for_the_daemon_socket(self):
        data = self._run(["--interactive"])
        self.assertTrue(data["ui"], data)
        # The old path slept in a 75 x 40ms loop waiting for the socket.
        self.assertLess(data["slept"], 0.2, f"startup slept {data['slept']:.2f}s waiting for the daemon")

    def test_daemon_is_still_spawned_for_playback(self):
        data = self._run(["--interactive"])
        self.assertIsNotNone(data["spawn"], "the playback engine was not started")
        self.assertTrue(data["spawn"]["file"], "the engine was started without a track")


def _child():
    """Run main() in a scratch HOME and report what it painted."""
    scenario = json.loads(os.environ["MP_SCENARIO"])
    painted = []
    slept = []

    original_write = sys.stdout.write

    class _Tee:
        def write(self, text):
            painted.append(text)
            return len(text)

        def flush(self):
            pass

        def isatty(self):
            return True

    class _Scanner:
        def __init__(self, *args, **kwargs):
            self.mixes = [{"name": "Mix.flac", "path": "/music/Mix.flac", "ext": "FLAC",
                           "size_mb": 10.0, "mtime": 0.0, "duration_fmt": "00:00"}]
            self.base_dir = Path("/tmp")
            self.folder_name = "ALL"
            self.playlist_path = None

        def get_mixes(self, *args, **kwargs):
            return self.mixes

        def get_latest_mix(self):
            return self.mixes[0] if self.mixes else None

    result = {"ui": False, "spawn": None, "painted": "", "slept": 0.0}
    player.MusicFolderPlaylistScanner = _Scanner
    player.MixArchiveScanner = _Scanner

    real_sleep = time.sleep

    def slow_sleep(seconds, *args, **kwargs):
        slept.append(seconds)
        return None

    def fake_spawn(file_path=None, **kwargs):
        result["spawn"] = {"file": file_path, **kwargs}
        return 4242

    player._spawn_player_daemon = fake_spawn
    player.is_player_daemon_running = lambda: bool(scenario.get("daemon"))
    player._retire_extra_player_daemons = lambda *a, **k: None

    def fake_ipc(cmd, **kwargs):
        if cmd == "status":
            return scenario.get("status")
        return {"status": "ok"}

    player.send_ipc_command = fake_ipc

    class FakeUI:
        def __init__(self, *args, **kwargs):
            result["ui_mode"] = kwargs.get("mode")
            scanner = args[1] if len(args) > 1 else None
            result["ui_tracks"] = len(getattr(scanner, "mixes", []) or [])

        def run(self):
            result["ui"] = True

    player.TerminalPlayerUI = FakeUI
    sys.stdout = _Tee()
    player.time.sleep = slow_sleep
    sys.argv = scenario["argv"]
    code = 0
    try:
        player.main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        sys.stdout = sys.__stdout__
        player.time.sleep = real_sleep
    result["slept"] = float(sum(slept))
    result["painted"] = "".join(painted)
    result["exit"] = code
    Path(os.environ["MP_RESULT"]).write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    if os.environ.get("MP_INSTANT_STARTUP_CHILD") == "1":
        _child()
    else:
        unittest.main()
