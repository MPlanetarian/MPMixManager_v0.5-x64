#!/usr/bin/env python3
"""Export the open playlist as any type, and open a playlist file.

Two behaviours are pinned here:

  * the exporter writes M3U, M3U8, XSPF, PLS and JSON, and a filtered search
    exports only the rows that search had on screen when the export started;
  * opening a playlist replaces the open one (never appends), starts the first
    track once the list is loaded, and leaves the old list alone when the file
    cannot be read.
"""

import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import MP_Audio_Player as player
import playlist_formats as formats


def _touch(folder, name):
    path = Path(folder) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF" + b"\x00" * 64)
    return path


def _track(path):
    return {
        "name": os.path.basename(path),
        "path": str(path),
        "ext": os.path.splitext(path)[1].lstrip(".").upper(),
        "size_mb": 1.0,
        "mtime": 0.0,
        "duration_fmt": "03:00",
    }


class PlaylistFormatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.a = _touch(self.dir, "Set A/One.flac")
        self.b = _touch(self.dir, "Set A/Two.mp3")
        self.tracks = [_track(self.a), _track(self.b)]

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_container_round_trips(self):
        for ext in formats.PLAYLIST_EXTENSIONS:
            with self.subTest(ext=ext):
                out = formats.write_playlist(self.dir / f"set{ext}", self.tracks)
                self.assertEqual(out.suffix, ext)
                rows = formats.read_playlist(out)
                self.assertEqual([r["path"] for r in rows], [str(self.a), str(self.b)])
                self.assertEqual(rows[0]["name"], "One.flac")

    def test_xspf_is_real_xml_with_file_locations(self):
        out = formats.write_playlist(self.dir / "set.xspf", self.tracks)
        root = ET.parse(out).getroot()
        locations = [n.text for n in root.iter() if n.tag.endswith("location")]
        self.assertEqual(len(locations), 2)
        self.assertTrue(all(loc.startswith("file://") for loc in locations))
        self.assertIn("%20", locations[0])

    def test_pls_keeps_its_numbered_entries(self):
        out = formats.write_playlist(self.dir / "set.pls", self.tracks)
        text = out.read_text(encoding="utf-8")
        self.assertIn("NumberOfEntries=2", text)
        self.assertIn("File1=", text)
        self.assertIn("Title2=Two", text)

    def test_json_export_is_readable_by_the_player(self):
        out = formats.write_playlist(self.dir / "set.json", self.tracks)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["path"], str(self.a))

    def test_relative_and_file_uri_entries_resolve(self):
        m3u = self.dir / "relative.m3u8"
        m3u.write_text(
            "#EXTM3U\n#EXTINF:180,One\nSet A/One.flac\n"
            f"#EXTINF:60,Two\nfile://{self.b}\n", encoding="utf-8")
        rows = formats.read_playlist(m3u)
        self.assertEqual(rows[0]["path"], str(self.a))
        self.assertEqual(rows[1]["path"], str(self.b))
        self.assertEqual(rows[0]["duration_fmt"], "03:00")

    def test_explicit_format_wins_over_the_suffix(self):
        out = formats.write_playlist(self.dir / "typed.txt", self.tracks, "pls")
        self.assertEqual(out.suffix, ".pls")
        self.assertTrue(out.read_text(encoding="utf-8").startswith("[playlist]"))

    def test_unsupported_extension_is_refused_on_read(self):
        bogus = self.dir / "list.ogg"
        bogus.write_text("/music/a.flac\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            formats.read_playlist(bogus)


class _Scanner:
    def __init__(self, mixes, playlist_path=""):
        self.mixes = list(mixes)
        self.playlist_path = playlist_path
        self.base_dir = Path("/tmp")

    def get_mixes(self, query=""):
        if not query:
            return list(self.mixes)
        q = query.lower()
        return [m for m in self.mixes if q in m["name"].lower()]

    def get_latest_mix(self):
        return self.mixes[0] if self.mixes else None


def _ui(mixes, playlist_path=""):
    ui = player.TerminalPlayerUI.__new__(player.TerminalPlayerUI)
    ui.mode = "MIX_ARCHIVE"
    ui.ui_size_mode = "NORMAL"
    ui.folder_name = "ALL"
    ui.keep_library = False
    ui._user_library_mode = None
    ui.is_remote = True
    ui.engine = None
    ui.scanner = _Scanner(mixes, playlist_path)
    ui.export_mode = False
    ui.export_path = ""
    ui.export_format = ".m3u8"
    ui.export_format_mode = False
    ui.export_tracks = None
    ui.open_playlist_mode = False
    ui.open_playlist_path = ""
    ui.eq_naming_mode = False
    ui.search_mode = False
    ui.search_query = ""
    ui.selected_row = 0
    ui.scroll_offset = 0
    ui.cached_status = {"mode": "MIX_ARCHIVE", "title": "", "path": ""}
    ui.notification_msg = ""
    ui.notification_time = 0.0
    ui._playlist_clear_armed = False
    ui.equalizer_hud_active = False
    ui.inspector_hud_active = False
    ui.favorites_clear_confirm = False
    ui.fav_export_mode = ""
    ui.dj_offer_paths = None
    ui.dj_input_mode = ""
    ui._last_playlist_dir = ""
    return ui


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.tracks = []
        for name in ("Opening Anthem.flac", "Midnight Drive.flac", "Closing Track.mp3"):
            path = _touch(self.dir, name)
            self.tracks.append(_track(path))

    def tearDown(self):
        self.tmp.cleanup()

    def test_filtered_search_exports_only_the_visible_rows(self):
        ui = _ui(self.tracks)
        ui.search_query = "midnight"
        dest = self.dir / "filtered.xspf"
        with patch.object(player, "choose_export_m3u_gui", return_value=str(dest)):
            ui._export_playlist()
        rows = formats.read_playlist(dest)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["path"], self.tracks[1]["path"])

    def test_unfiltered_export_writes_the_whole_playlist(self):
        ui = _ui(self.tracks)
        dest = self.dir / "all.pls"
        with patch.object(player, "choose_export_m3u_gui", return_value=str(dest)):
            ui._export_playlist()
        self.assertEqual(len(formats.read_playlist(dest)), 3)

    def test_typed_path_export_uses_the_chosen_container(self):
        ui = _ui(self.tracks)
        with patch.object(player, "choose_export_m3u_gui", return_value=None):
            ui._export_playlist()
        self.assertTrue(ui.export_format_mode)
        ui._process_key(b"3")
        self.assertTrue(ui.export_mode)
        self.assertFalse(ui.export_format_mode)
        self.assertTrue(ui.export_path.endswith(".xspf"))
        ui.export_path = str(self.dir / "typed.xspf")
        ui._process_key(b"\r")
        self.assertFalse(ui.export_mode)
        root = ET.parse(self.dir / "typed.xspf").getroot()
        self.assertEqual(len([n for n in root.iter() if n.tag.endswith("location")]), 3)

    def test_export_rows_are_frozen_when_the_prompt_opens(self):
        ui = _ui(self.tracks)
        ui.search_query = "closing"
        with patch.object(player, "choose_export_m3u_gui", return_value=None):
            ui._export_playlist()
        # Changing the filter now must not change what gets written.
        ui.search_query = ""
        ui._process_key(b"3")
        ui.export_path = str(self.dir / "frozen.xspf")
        ui._process_key(b"\r")
        self.assertEqual(len(formats.read_playlist(self.dir / "frozen.xspf")), 1)

    def test_export_cancel_writes_nothing(self):
        ui = _ui(self.tracks)
        with patch.object(player, "choose_export_m3u_gui", return_value=""):
            ui._export_playlist()
        self.assertFalse(ui.export_mode)
        self.assertIsNone(ui.export_tracks)
        self.assertIn("cancelled", ui.notification_msg)

    def test_empty_playlist_does_not_open_the_exporter(self):
        ui = _ui([])
        with patch.object(player, "choose_export_m3u_gui") as dialog:
            ui._export_playlist()
        dialog.assert_not_called()
        self.assertIn("No mixes", ui.notification_msg)


class OpenPlaylistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.old = []
        for name in ("Old A.flac", "Old B.flac"):
            self.old.append(_track(_touch(self.dir, "old/" + name)))
        self.new = []
        for name in ("New A.flac", "New B.wav"):
            self.new.append(_track(_touch(self.dir, "new/" + name)))
        self.ui = _ui(self.old)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, ext, tracks):
        return formats.write_playlist(self.dir / f"import{ext}", tracks)

    def test_every_type_opens_and_replaces_the_playlist(self):
        for ext in formats.PLAYLIST_EXTENSIONS:
            with self.subTest(ext=ext):
                ui = _ui(self.old)
                path = self._write(ext, self.new)
                with patch.object(player, "send_ipc_command", return_value={"status": "ok"}) as send:
                    self.assertTrue(ui._load_playlist_file(str(path)))
                self.assertIsInstance(ui.scanner, player.ImportedPlaylistScanner)
                self.assertEqual(ui.mode, "PLAYLIST")
                self.assertEqual([r["path"] for r in ui.scanner.mixes],
                                 [r["path"] for r in self.new])
                self.assertFalse(any(r["path"] in {o["path"] for o in self.old}
                                     for r in ui.scanner.mixes))
                self.assertEqual(ui.selected_row, 0)
                self.assertFalse(ui.search_query)
                self.assertEqual(send.call_args.args[0], "open_playlist")
                self.assertEqual(send.call_args.kwargs["playlist_path"], str(path))

    def test_open_clears_an_active_search_filter(self):
        ui = _ui(self.old)
        ui.search_query = "old a"
        path = self._write(".m3u8", self.new)
        with patch.object(player, "send_ipc_command", return_value={}):
            ui._load_playlist_file(str(path))
        self.assertEqual(ui.search_query, "")
        self.assertEqual(len(ui._get_active_tracks()), 2)

    def test_open_waits_for_a_daemon_that_is_still_starting(self):
        ui = _ui(self.old)
        path = self._write(".m3u8", self.new)
        calls = {"n": 0}

        def flaky(cmd, **kwargs):
            calls["n"] += 1
            return None if calls["n"] < 3 else {"status": "ok"}

        with patch.object(player, "send_ipc_command", side_effect=flaky), \
                patch.object(player.time, "sleep", return_value=None):
            self.assertTrue(ui._load_playlist_file(str(path)))
        self.assertEqual(calls["n"], 3)
        self.assertEqual(ui.mode, "PLAYLIST")

    def test_open_reports_a_daemon_that_never_answers(self):
        ui = _ui(self.old)
        path = self._write(".m3u8", self.new)
        with patch.object(player, "send_ipc_command", return_value=None), \
                patch.object(player.time, "sleep", return_value=None), \
                patch.object(player.time, "monotonic", side_effect=[0.0, 99.0]):
            ui._load_playlist_file(str(path))
        self.assertIn("not answering", ui.notification_msg)

    def test_opened_playlist_records_its_source_file(self):
        ui = _ui(self.old)
        path = self._write(".xspf", self.new)
        with patch.object(player, "send_ipc_command", return_value={}):
            ui._load_playlist_file(str(path))
        self.assertEqual(ui.scanner.playlist_path, str(path))
        self.assertEqual(ui._opened_playlist_name(), "import.xspf")
        self.assertEqual(ui._entry_noun(), "tracks")

    def test_unreadable_playlist_leaves_the_old_list_alone(self):
        ui = _ui(self.old)
        with patch.object(player, "send_ipc_command") as send, \
                patch.object(formats, "read_playlist", side_effect=OSError("nope")):
            self.assertFalse(ui._load_playlist_file(str(self._write(".m3u8", self.new))))
        send.assert_not_called()
        self.assertEqual([r["path"] for r in ui.scanner.mixes], [r["path"] for r in self.old])
        self.assertEqual(ui.mode, "MIX_ARCHIVE")
        self.assertIn("Could not read", ui.notification_msg)

    def test_empty_playlist_leaves_the_old_list_alone(self):
        ui = _ui(self.old)
        empty = self.dir / "empty.m3u8"
        empty.write_text("#EXTM3U\n", encoding="utf-8")
        with patch.object(player, "send_ipc_command") as send:
            self.assertFalse(ui._load_playlist_file(str(empty)))
        send.assert_not_called()
        self.assertEqual(len(ui.scanner.mixes), 2)
        self.assertIn("empty", ui.notification_msg)

    def test_missing_file_is_reported_and_changes_nothing(self):
        ui = _ui(self.old)
        with patch.object(player, "send_ipc_command") as send:
            self.assertFalse(ui._load_playlist_file(str(self.dir / "gone.m3u8")))
        send.assert_not_called()
        self.assertEqual(len(ui.scanner.mixes), 2)
        self.assertIn("not found", ui.notification_msg)

    def test_wrong_type_is_refused_by_name(self):
        ui = _ui(self.old)
        bogus = self.dir / "notes.txt"
        bogus.write_text("hello", encoding="utf-8")
        self.assertFalse(ui._load_playlist_file(str(bogus)))
        self.assertIn("Not a supported playlist", ui.notification_msg)
        self.assertEqual(len(ui.scanner.mixes), 2)

    def test_tracks_that_are_not_on_disk_are_counted_and_skipped(self):
        ui = _ui(self.old)
        playlist = self.dir / "partial.m3u8"
        playlist.write_text(
            "#EXTM3U\n"
            f"#EXTINF:-1,{self.new[0]['name']}\n{self.new[0]['path']}\n"
            "#EXTINF:-1,Missing\n/music/does/not/exist.flac\n", encoding="utf-8")
        with patch.object(player, "send_ipc_command", return_value={}) as send:
            self.assertTrue(ui._load_playlist_file(str(playlist)))
        # Both rows stay in the list, but only the real file is sent to play.
        self.assertEqual(len(ui.scanner.mixes), 2)
        self.assertEqual([r["path"] for r in send.call_args.kwargs["tracks"]],
                         [self.new[0]["path"]])
        self.assertIn("1 not found", ui.notification_msg)

    def test_a_playlist_with_no_playable_tracks_is_not_installed(self):
        ui = _ui(self.old)
        playlist = self.dir / "ghosts.m3u8"
        playlist.write_text("#EXTM3U\n/music/one.flac\n/music/two.flac\n", encoding="utf-8")
        with patch.object(player, "send_ipc_command") as send:
            self.assertFalse(ui._load_playlist_file(str(playlist)))
        send.assert_not_called()
        self.assertEqual(len(ui.scanner.mixes), 2)
        self.assertIn("none are on this machine", ui.notification_msg)

    def test_hand_typed_path_prompt_accepts_any_location(self):
        ui = _ui(self.old)
        outside = Path(self.tmp.name) / "elsewhere" / "far.pls"
        formats.write_playlist(outside, self.new, "pls")
        with patch.object(player, "choose_playlist_file_gui", return_value=None), \
                patch.object(player, "send_ipc_command", return_value={}):
            ui._process_key(b"O")
            self.assertTrue(ui.open_playlist_mode)
            for chunk in str(outside):
                ui._process_key(chunk.encode())
            ui._process_key(b"\r")
        self.assertFalse(ui.open_playlist_mode)
        self.assertEqual([r["path"] for r in ui.scanner.mixes], [r["path"] for r in self.new])

    def test_escape_leaves_the_open_prompt_without_loading(self):
        ui = _ui(self.old)
        with patch.object(player, "choose_playlist_file_gui", return_value=None), \
                patch.object(player, "send_ipc_command") as send:
            ui._process_key(b"O")
            ui._process_key(b"/tmp/whatever.m3u8")
            ui._process_key(b"\x1b")
        send.assert_not_called()
        self.assertFalse(ui.open_playlist_mode)
        self.assertEqual(len(ui.scanner.mixes), 2)

    def test_tab_completes_a_folder(self):
        target = self.dir / "new"
        typed = str(self.dir / "ne")
        self.assertTrue(player._complete_playlist_path(typed).rstrip(os.sep).endswith("new"))
        self.assertTrue(os.path.isdir(player._complete_playlist_path(typed)))
        self.assertEqual(target.name, os.path.basename(
            player._complete_playlist_path(typed).rstrip(os.sep)))


class EngineReplaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.old = [_touch(self.dir, f"old{i}.flac") for i in range(2)]
        self.new = [_touch(self.dir, f"new{i}.flac") for i in range(3)]
        self.engine = player.AudioEngine.__new__(player.AudioEngine)
        self.engine.playlist = [_track(p) for p in self.old]
        self.engine.play_queue = []
        self.engine._queue_lock = __import__("threading").Lock()
        self.engine.current_index = 0
        self.engine.current_track = self.engine.playlist[0]
        self.engine.duration_sec = 10.0
        self.engine.playlist_path = "old.m3u"
        self.engine.mode = "MIX_ARCHIVE"
        self.engine.stop_playback = MagicMock()
        self.engine.load_track = MagicMock(return_value=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_replace_wipes_then_installs_and_plays_the_first_track(self):
        rows = [_track(p) for p in self.new]
        installed = self.engine.replace_playlist(rows, playlist_path="/tmp/new.xspf",
                                                 mode="PLAYLIST")
        self.assertEqual(installed, 3)
        self.assertEqual([r["path"] for r in self.engine.playlist], [t["path"] for t in rows])
        self.assertEqual(self.engine.playlist_path, "/tmp/new.xspf")
        self.assertEqual(self.engine.mode, "PLAYLIST")
        self.engine.stop_playback.assert_called_once()
        self.engine.load_track.assert_called_once_with(0, True)

    def test_replace_never_appends_to_the_old_list(self):
        self.engine.replace_playlist([_track(self.new[0])])
        self.assertEqual(len(self.engine.playlist), 1)
        self.assertNotIn(str(self.old[0]), [r["path"] for r in self.engine.playlist])


class DaemonExportTests(unittest.TestCase):
    """The menu export is served by the running daemon, not by a fresh scan."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.tracks = []
        for name in ("Opening.flac", "Midnight.mp3", "Closing.wav"):
            path = _touch(self.dir, name)
            row = _track(path)
            row["duration_fmt"] = "05:00"
            self.tracks.append(row)
        self.engine = MagicMock()
        self.engine.playlist = [dict(t) for t in self.tracks]
        self.engine.mode = "PLAYLIST"
        self.engine.get_status_dict.return_value = {"state": "paused", "path": ""}
        self.server = player.PlayerIPCServer(self.engine)

    def tearDown(self):
        self.tmp.cleanup()

    def _request(self, **payload):
        import io
        import json as jsonlib
        import socket
        left, right = socket.socketpair()
        try:
            right.sendall(jsonlib.dumps(payload).encode("utf-8"))
            right.shutdown(socket.SHUT_WR)
            self.server._handle_client(left)
            blob = b""
            while True:
                chunk = right.recv(65536)
                if not chunk:
                    break
                blob += chunk
            return jsonlib.loads(blob.decode("utf-8")) if blob else {}
        finally:
            left.close()
            right.close()

    def test_export_writes_the_daemons_own_list(self):
        dest = self.dir / "daemon.json"
        reply = self._request(cmd="export_playlist", path=str(dest), format="json")
        self.assertTrue(reply.get("success"), reply)
        self.assertEqual(reply.get("exported"), 3)
        self.assertEqual(len(formats.read_playlist(dest)), 3)

    def test_export_filter_narrows_to_the_search_results(self):
        dest = self.dir / "daemon.xspf"
        reply = self._request(cmd="export_playlist", path=str(dest),
                              format="xspf", filter="midnight")
        self.assertTrue(reply.get("success"), reply)
        self.assertEqual(reply.get("exported"), 1)
        self.assertEqual(reply.get("filter"), "midnight")
        rows = formats.read_playlist(dest)
        self.assertEqual([Path(r["path"]).name for r in rows], ["Midnight.mp3"])

    def test_export_reports_a_write_failure(self):
        reply = self._request(cmd="export_playlist", path="/proc/nope/out.m3u8",
                              format="m3u8")
        self.assertFalse(reply.get("success"))
        self.assertEqual(reply.get("status"), "error")
        self.assertTrue(reply.get("message"))

    def test_get_playlist_returns_the_whole_list(self):
        reply = self._request(cmd="get_playlist")
        self.assertEqual(len(reply.get("playlist", [])), 3)

    def test_open_playlist_replaces_the_engine_list_and_plays(self):
        fresh = [{"name": "Fresh.flac", "path": str(_touch(self.dir, "Fresh.flac")),
                  "ext": "FLAC"}]
        reply = self._request(cmd="open_playlist", tracks=fresh,
                              playlist_path=str(self.dir / "fresh.m3u8"),
                              mode="PLAYLIST")
        self.assertEqual(reply.get("status"), "ok", reply)
        self.assertEqual(int(reply.get("loaded", 0)), 1)
        self.engine.replace_playlist.assert_called_once()
        args, kwargs = self.engine.replace_playlist.call_args
        self.assertEqual(args[0], fresh)
        self.assertEqual(kwargs.get("mode"), "PLAYLIST")


class PlaylistArgumentTests(unittest.TestCase):
    """`--playlist <file>` accepts every container, not just the generated pair."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.tracks = [_track(_touch(self.dir, name)) for name in ("One.flac", "Two.mp3")]

    def tearDown(self):
        self.tmp.cleanup()

    def test_music_scanner_opens_every_type(self):
        for ext in formats.PLAYLIST_EXTENSIONS:
            with self.subTest(ext=ext):
                src = formats.write_playlist(self.dir / f"start{ext}", self.tracks)
                with patch.object(player, "get_base_dir", return_value=self.dir):
                    scanner = player.MusicFolderPlaylistScanner("ALL", str(src))
                self.assertEqual([Path(r["path"]).name for r in scanner.mixes],
                                 ["One.flac", "Two.mp3"])

    def test_a_music_playlist_with_a_relative_entry_still_resolves(self):
        src = self.dir / "relative.m3u8"
        src.write_text("#EXTM3U\n#EXTINF:90,One\nOne.flac\n", encoding="utf-8")
        with patch.object(player, "get_base_dir", return_value=self.dir):
            scanner = player.MusicFolderPlaylistScanner("ALL", str(src))
        self.assertEqual(Path(scanner.mixes[0]["path"]), self.dir / "One.flac")
        self.assertEqual(scanner.mixes[0]["duration_fmt"], "01:30")


class PromptRowTests(unittest.TestCase):
    """The prompt row must say what the player is waiting for."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.tracks = [_track(_touch(self.dir, "One.flac"))]
        self.ui = _ui(self.tracks)

    def tearDown(self):
        self.tmp.cleanup()

    def test_format_prompt_names_all_five_types(self):
        self.ui.export_tracks = list(self.tracks)
        self.ui.export_format_mode = True
        line = self.ui._playlist_prompt_line("")
        for word in ("M3U", "M3U8", "XSPF", "PLS", "JSON", "Cancel"):
            self.assertIn(word, line)

    def test_path_prompt_names_the_chosen_container(self):
        self.ui.export_format = ".xspf"
        self.ui.export_path = "/tmp/set.xspf"
        self.ui.export_mode = True
        line = self.ui._playlist_prompt_line("")
        self.assertIn("XSPF", line)
        self.assertIn("/tmp/set.xspf", line)
        self.assertIn("Save", line)

    def test_open_prompt_shows_the_typed_path_and_tab_hint(self):
        self.ui.open_playlist_mode = True
        self.ui.open_playlist_path = "~/sets/late"
        line = self.ui._playlist_prompt_line("")
        self.assertIn("~/sets/late", line)
        self.assertIn("Tab", line)

    def test_no_prompt_when_the_player_is_idle(self):
        self.assertEqual(self.ui._playlist_prompt_line(""), "")

    def test_a_favorites_window_takes_the_row_first(self):
        self.ui.favorites_clear_confirm = True
        self.ui.open_playlist_mode = True
        self.assertIn("Clear Favorites", self.ui._playlist_prompt_line(""))


class OpenedPlaylistRenderingTests(unittest.TestCase):
    """An opened playlist is its own library: durations, title, footer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.tracks = []
        for name in ("One.flac", "Two.mp3"):
            path = _touch(self.dir, name)
            row = _track(path)
            row["duration_fmt"] = "04:05"
            self.tracks.append(row)
        self.ui = _ui(self.tracks)
        self.ui.scanner = player.ImportedPlaylistScanner(self.tracks, str(self.dir / "set.m3u8"))
        self.ui.mode = "PLAYLIST"

    def tearDown(self):
        self.tmp.cleanup()

    def test_entry_noun_is_tracks_not_mixes(self):
        self.assertEqual(self.ui._entry_noun(), "tracks")

    def test_opened_name_comes_from_the_playlist_file(self):
        self.assertEqual(self.ui._opened_playlist_name(), "set.m3u8")

    def test_clearing_reports_tracks(self):
        self.ui._arm_playlist_clear()
        self.assertIn("tracks", self.ui.notification_msg)

    def test_the_panel_title_names_the_opened_file(self):
        title = self.ui._list_panel_title(len(self.tracks))
        self.assertIn("OPENED PLAYLIST", title)
        self.assertIn("set.m3u8", title)
        self.assertIn("2 Tracks", title)

    def test_the_other_libraries_keep_their_titles(self):
        self.ui.mode = "FAVORITES"
        self.assertIn("FAVORITES PLAYLIST", self.ui._list_panel_title(3))
        self.ui.mode = "MUSIC"
        self.assertIn("MUSIC LIBRARY", self.ui._list_panel_title(3))
        self.ui.mode = "MIX_ARCHIVE"
        self.ui.scanner = _Scanner(self.tracks)
        self.assertIn("MP MIX ARCHIVE PLAYLIST", self.ui._list_panel_title(3))

    def test_the_rows_show_playlist_durations(self):
        row = self.ui.scanner.mixes[0]
        self.assertEqual(row.get("duration_fmt"), "04:05")
        self.assertEqual(row.get("duration_sec", 0.0) >= 0.0, True)

    def test_an_opened_playlist_offers_export_and_open_in_its_footer(self):
        # The footer text is built inline with the panel; the strings the two
        # keys use are pinned here so a rename cannot silently drop them.
        source = Path(player.__file__).read_text(encoding="utf-8")
        self.assertIn("OPENED PLAYLIST", source)
        self.assertIn("[O] Open Playlist • [w] Export", source)


class CommandLineTests(unittest.TestCase):
    """`--open-playlist` and `--export-format` are the headless equivalents."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.tracks = []
        for name in ("One.flac", "Two.mp3"):
            path = _touch(self.dir, name)
            self.tracks.append(_track(path))

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, argv):
        import io
        import contextlib
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with patch.object(player, "is_player_daemon_running", return_value=False), \
                patch.object(player.MixArchiveScanner, "get_mixes", return_value=self.tracks), \
                patch.object(sys, "argv", ["MP_Audio_Player.py"] + argv), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                player.main()
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 0
        return code, out.getvalue(), err.getvalue()

    def test_export_format_flag_writes_the_requested_container(self):
        for ext in (".m3u", ".m3u8", ".xspf", ".pls", ".json"):
            with self.subTest(ext=ext):
                dest = self.dir / f"cli{ext}"
                code, out, err = self._run(["--export-playlist", str(dest),
                                            "--export-format", ext.lstrip(".")])
                self.assertEqual(code, 0, err)
                self.assertTrue(dest.is_file())
                self.assertEqual(len(formats.read_playlist(dest)), 2)

    def test_a_suffix_without_the_format_flag_is_honoured(self):
        dest = self.dir / "by-suffix.pls"
        code, out, err = self._run(["--export-playlist", str(dest)])
        self.assertEqual(code, 0, err)
        self.assertTrue(dest.read_text(encoding="utf-8").startswith("[playlist]"))

    def test_open_playlist_reports_a_valid_file_without_a_daemon(self):
        src = formats.write_playlist(self.dir / "wanted.m3u8", self.tracks)
        code, out, err = self._run(["--open-playlist", str(src)])
        self.assertEqual(code, 0, err)
        self.assertIn("wanted.m3u8", out)
        self.assertIn("2 track(s)", out)

    def test_open_playlist_sends_the_list_to_a_running_daemon(self):
        src = formats.write_playlist(self.dir / "wanted.xspf", self.tracks)
        sent = {}

        def fake_send(cmd, **kwargs):
            sent["cmd"] = cmd
            sent.update(kwargs)
            return {"status": "ok", "loaded": 2}

        with patch.object(player, "is_player_daemon_running", return_value=True), \
                patch.object(player, "send_ipc_command", side_effect=fake_send), \
                patch.object(sys, "argv", ["MP_Audio_Player.py", "--open-playlist", str(src)]):
            import contextlib, io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                try:
                    player.main()
                except SystemExit as exc:
                    self.assertEqual(exc.code, 0)
        self.assertEqual(sent.get("cmd"), "open_playlist")
        self.assertEqual(sent.get("playlist_path"), str(src))
        self.assertEqual([row["path"] for row in sent.get("tracks", [])],
                         [row["path"] for row in self.tracks])

    def test_a_filter_that_matches_nothing_exports_nothing(self):
        dest = self.dir / "empty-filter.xspf"
        code, out, err = self._run(["--export-playlist", str(dest),
                                    "--export-format", "xspf", "--filter", "zzz-no-match"])
        self.assertEqual(code, 1)
        self.assertFalse(dest.exists())
        self.assertIn("matched no tracks", err)

    def test_a_filter_exports_only_the_matching_rows(self):
        dest = self.dir / "filtered.m3u8"
        code, out, err = self._run(["--export-playlist", str(dest), "--filter", "Two"])
        self.assertEqual(code, 0, err)
        rows = formats.read_playlist(dest)
        self.assertEqual([Path(r["path"]).name for r in rows], ["Two.mp3"])

    def test_open_playlist_refuses_an_empty_list(self):
        empty = self.dir / "empty.m3u8"
        empty.write_text("#EXTM3U\n", encoding="utf-8")
        code, out, err = self._run(["--open-playlist", str(empty)])
        self.assertEqual(code, 1)
        self.assertIn("no tracks that exist", err)


if __name__ == "__main__":
    unittest.main()
