#!/usr/bin/env python3
"""Favorites DJ set exporter: copy, verify, tracklist, m3u8 and Traktor playlist."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch

import MP_Audio_Player as player
from scripts import MP_Fav_Playlist_Track_Exporter as exporter


def _ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _make_audio(path: Path, frequency: int = 440) -> None:
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-f", "lavfi",
            "-i", f"sine=frequency={frequency}:duration=1",
            "-c:a", "flac", str(path), "-y",
        ],
        check=True,
    )


def _nml_entry(filename: str, artist: str, title: str, key: str = "8m", mval: str = "21") -> str:
    return (
        f'<ENTRY TITLE="{title}" ARTIST="{artist}">'
        f'<LOCATION DIR="/:music/:" FILE="{filename}" VOLUME=""/>'
        f'<INFO KEY="{key}" PLAYTIME="300"/>'
        f'<TEMPO BPM="138.0"/>'
        f'<MUSICAL_KEY VALUE="{mval}"/>'
        "</ENTRY>"
    )


class ExporterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.src = self.root / "src"
        self.src.mkdir()
        self.audio = [self.src / "Airbase - Escape.flac", self.src / "Gai Barone - Lonely Keys.flac"]
        if _ffmpeg():
            _make_audio(self.audio[0], 440)
            _make_audio(self.audio[1], 330)
        else:
            for path in self.audio:
                path.write_bytes(b"fLaC" + b"\x00" * 4096)
        self.favorites = self.root / "favorites.json"
        self.favorites.write_text(
            json.dumps(
                {
                    "version": 1,
                    "name": "Favorites",
                    "tracks": [
                        {"name": path.name, "path": str(path), "ext": "FLAC"} for path in self.audio
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.env = patch.dict(
            os.environ,
            {"MP_FAVORITES_FILE": str(self.favorites), "FAV_EXPORT_SMB_HOST": "", "FAV_EXPORT_SMB_MOUNT": ""},
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_empty_favorites_refuses_to_export(self):
        result = exporter.export_favorites([], destination=str(self.root / "out"))
        self.assertFalse(result["ok"])
        self.assertIn("empty", " ".join(result["errors"]).lower())
        self.assertFalse((self.root / "out").exists())

    def test_playlist_name_uses_the_date_and_twelve_characters(self):
        name = exporter.new_playlist_name()
        self.assertRegex(name, r"^MP_Fav_Set_\d{4}-\d{2}-\d{2}_[A-Z0-9]{12}$")
        self.assertEqual(exporter.new_playlist_name("My Set"), "My Set")

    def test_local_export_copies_verifies_and_writes_the_tracklist(self):
        result = exporter.export_favorites(self.audio, destination=str(self.root / "out"))
        self.assertTrue(result["ok"], result.get("errors"))
        self.assertEqual(len(result["copied"]), 2)
        folder = Path(result["dest"])
        self.assertTrue((folder / self.audio[0].name).is_file())
        self.assertEqual(
            (folder / self.audio[0].name).stat().st_size, self.audio[0].stat().st_size
        )
        self.assertGreaterEqual(result["playable_files"], 1)
        tracklist = Path(result["tracklist"])
        self.assertEqual(tracklist.parent, folder)
        content = tracklist.read_text(encoding="utf-8")
        self.assertIn("RELEASE INFO & SOURCE MANIFEST", content)
        self.assertIn("TRACKLIST", content)
        self.assertIn("Airbase - Escape", content)
        self.assertIn("Gai Barone - Lonely Keys", content)
        self.assertRegex(content, r"(?m)^01\. ")
        m3u8 = Path(result["m3u8"])
        self.assertTrue(m3u8.is_file())
        self.assertIn("#EXTM3U", m3u8.read_text(encoding="utf-8"))

    def test_mounted_smb_export_writes_the_tracklist_into_the_mount(self):
        mount = self.root / "mount"
        mount.mkdir()
        settings = {
            "host": "nas.local",
            "share": "Music",
            "user": "u",
            "password": "p",
            "path": "DJ Sets",
            "mount": str(mount),
            "kind": "smb",
        }
        result = exporter.export_favorites(self.audio, smb=settings, playlist_name="Mount Night")
        self.assertTrue(result["ok"], result.get("errors"))
        folder = mount / "DJ Sets" / "Mount Night"
        self.assertTrue((folder / "Mount Night.txt").is_file())
        self.assertTrue((folder / "Mount Night.m3u8").is_file())
        self.assertEqual(Path(result["tracklist"]).parent, folder)
        self.assertTrue(result["dest"].startswith("smb://nas.local/Music/"))

    def test_custom_tracklist_name_is_used_for_file_and_folder(self):
        result = exporter.export_favorites(
            self.audio, destination=str(self.root / "out"), tracklist_name="Friday Warmup"
        )
        self.assertTrue(result["ok"], result.get("errors"))
        self.assertEqual(Path(result["tracklist"]).name, "Friday Warmup.txt")
        self.assertEqual(Path(result["dest"]).name, "Friday Warmup")

    def test_missing_source_is_reported_and_does_not_stop_the_export(self):
        missing = {"name": "Gone.flac", "path": str(self.src / "Gone.flac"), "ext": "FLAC"}
        result = exporter.export_favorites(
            [self.audio[0], missing], destination=str(self.root / "out")
        )
        self.assertTrue(result["ok"], result.get("errors"))
        self.assertEqual(len(result["copied"]), 1)
        self.assertEqual(result["failed"][0]["name"], "Gone.flac")

    def test_truncated_copy_fails_verification(self):
        destination = self.root / "out"
        with patch.object(exporter.shutil, "copy2", lambda src, dst: Path(dst).write_bytes(b"x" * 10)):
            result = exporter.export_favorites(self.audio, destination=str(destination))
        self.assertFalse(result["ok"])
        self.assertTrue(result["failed"])

    def test_unplayable_copies_are_treated_as_failed(self):
        bogus = self.src / "Not Audio.flac"
        bogus.write_bytes(b"not audio at all")
        with patch.object(exporter, "probe_audio", lambda path: {"playable": False}):
            result = exporter.export_favorites([bogus], destination=str(self.root / "out"))
        self.assertFalse(result["ok"])
        self.assertIn("could be opened as audio", " ".join(result["errors"]))


class TraktorPlaylistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.history_dir = self.root / "History"
        self.history_dir.mkdir()
        self.collection = self.root / "collection.nml"
        self.collection.write_text(
            '<?xml version="1.0" encoding="UTF-8"?><NML VERSION="19">'
            "<COLLECTION ENTRIES=\"2\">"
            + _nml_entry("Airbase - Escape.flac", "Airbase", "Escape", "8m", "21")
            + _nml_entry("Gai Barone - Lonely Keys.flac", "Gai Barone", "Lonely Keys", "9d", "0")
            + "</COLLECTION>"
            '<PLAYLISTS><NODE TYPE="FOLDER" NAME="$ROOT"><SUBNODES COUNT="0"/></NODE></PLAYLISTS>'
            "</NML>",
            encoding="utf-8",
        )
        self.history = self.history_dir / "history_2026y11m12d_01h00m00s.nml"
        self.history.write_text(
            '<?xml version="1.0" encoding="UTF-8"?><NML VERSION="19">'
            "<COLLECTION ENTRIES=\"2\">"
            + _nml_entry("Gai Barone - Lonely Keys.flac", "Gai Barone", "Lonely Keys", "9d", "0")
            + _nml_entry("Airbase - Escape.flac", "Airbase", "Escape", "8m", "21")
            + "</COLLECTION>"
            '<PLAYLISTS><NODE TYPE="PLAYLIST" NAME="History"><PLAYLIST ENTRIES="2" TYPE="LIST">'
            '<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="/:music/:Gai Barone - Lonely Keys.flac"/></ENTRY>'
            '<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="/:music/:Airbase - Escape.flac"/></ENTRY>'
            "</PLAYLIST></NODE></PLAYLISTS></NML>",
            encoding="utf-8",
        )
        self.env = patch.dict(
            os.environ,
            {
                "TRAKTOR_COLLECTION_PATH": str(self.collection),
                "TRAKTOR_HISTORY_DIR": str(self.history_dir),
                "TRAKTOR_ENABLED": "true",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_ready_reports_collection_and_history(self):
        ready, reason = exporter.traktor_ready()
        self.assertTrue(ready, reason)

    def test_playlist_is_written_key_sorted_with_a_backup(self):
        result = exporter.create_traktor_playlist(
            ["Airbase - Escape.flac", "Gai Barone - Lonely Keys.flac"],
            "Fav Test",
            "history_2026y11m12d_01h00m00s.nml",
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["matched"], 2)
        backups = list(self.root.glob("collection.nml.bak_*"))
        self.assertTrue(backups)
        root = ET.parse(self.collection).getroot()
        nodes = [n for n in root.findall(".//PLAYLISTS//NODE") if n.attrib.get("TYPE") == "PLAYLIST"]
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].attrib["NAME"], "Fav Test")
        keys = [pk.attrib["KEY"] for pk in nodes[0].findall(".//PRIMARYKEY")]
        # Key ascending: 8m (Airbase) before 9d (Gai Barone).
        self.assertEqual(keys[0], "/:music/:Airbase - Escape.flac")
        self.assertEqual(keys[1], "/:music/:Gai Barone - Lonely Keys.flac")

    def test_missing_history_is_reported(self):
        ready, _ = exporter.traktor_ready()
        self.assertTrue(ready)
        (self.history).unlink()
        ready, reason = exporter.traktor_ready()
        self.assertFalse(ready)
        self.assertIn("History", reason)

    def test_favorites_outside_the_session_fall_back_to_the_collection(self):
        self.history.write_text(
            '<?xml version="1.0" encoding="UTF-8"?><NML VERSION="19">'
            + '<COLLECTION ENTRIES="1">'
            + _nml_entry("Other Track.flac", "Someone", "Other", "1m", "0")
            + "</COLLECTION>"
            + '<PLAYLISTS><NODE TYPE="PLAYLIST" NAME="History"><PLAYLIST ENTRIES="1" TYPE="LIST">'
            + '<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="/:music/:Other Track.flac"/></ENTRY>'
            + "</PLAYLIST></NODE></PLAYLISTS></NML>",
            encoding="utf-8",
        )
        result = exporter.create_traktor_playlist(["Airbase - Escape.flac"], "Fallback")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["matched"], 1)

    def test_track_in_neither_session_nor_collection_is_reported(self):
        result = exporter.create_traktor_playlist(["Unknown - Track.flac"], "No Match")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_match")
        self.assertIn("Unknown - Track.flac", result["missing"])


def _track(name, path):
    return {"name": name, "path": path, "ext": "FLAC", "size_mb": 1.0}


class _Scanner:
    def __init__(self, mixes):
        self.mixes = list(mixes)
        self.base_dir = Path("/tmp/mix-archive")

    def get_mixes(self, query=""):
        return list(self.mixes)

    def get_latest_mix(self):
        return self.mixes[0] if self.mixes else None


class FavoritesPlayerExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = player.FavoritesStore(Path(self.tmp.name) / "favorites.json")
        self.ui = player.TerminalPlayerUI.__new__(player.TerminalPlayerUI)
        self.ui.mode = "FAVORITES"
        self.ui.folder_name = "ALL"
        self.ui.keep_library = True
        self.ui._user_library_mode = "FAVORITES"
        self.ui.is_remote = False
        self.ui.engine = MagicMock()
        self.ui.engine.current_track = None
        self.ui.engine.playlist = []
        self.ui.engine.current_index = 0
        self.ui.engine.get_status_dict.return_value = {"path": "", "title": "", "mode": "FAVORITES"}
        self.ui.scanner = _Scanner([])
        self.ui.export_mode = False
        self.ui.eq_naming_mode = False
        self.ui.search_mode = False
        self.ui.search_query = ""
        self.ui.selected_row = self.ui.scroll_offset = 0
        self.ui.cached_status = {"mode": "FAVORITES"}
        self.ui.notification_msg = ""
        self.ui.notification_time = 0.0
        self.ui.equalizer_hud_active = False
        self.ui.inspector_hud_active = False
        self.ui.ui_size_mode = "NORMAL"
        self.ui._playlist_clear_armed = False
        self.ui._inbuf = b""
        self.ui.favorites = self.store
        self.ui.favorites_clear_confirm = False
        self.ui.fav_export_mode = ""
        self.ui.fav_export_dest = ""
        self.ui.fav_export_name = ""
        self.ui.fav_export_kind = "local"
        self.ui.traktor_offer_paths = None
        self.ui.traktor_offer_name = ""
        self.ui.traktor_input_mode = ""
        self.ui.traktor_playlist_name = ""
        self.ui.traktor_history_id = ""
        self.ui._reset_click_targets()

    def tearDown(self):
        self.tmp.cleanup()

    def test_export_requires_at_least_one_favorite(self):
        self.ui._favorites_export_begin()
        self.assertEqual(self.ui.fav_export_mode, "")
        self.assertIn("empty", self.ui.notification_msg.lower())

    def test_j_opens_the_dj_export_decision_window(self):
        self.store.toggle(_track("Mix A.flac", "/mixes/a.flac"))
        with patch.object(player.TerminalPlayerUI, "_fav_exporter_module") as module:
            module.return_value.traktor_ready.return_value = (False, "not configured")
            self.ui._process_key(b"J")
        self.assertEqual(self.ui.fav_export_mode, "choose")
        self.assertIn("DJ export", self.ui.notification_msg)

    def test_lowercase_j_still_scrolls_the_favorites_list(self):
        self.store.toggle(_track("Mix A.flac", "/mixes/a.flac"))
        self.ui.scanner = _Scanner([_track("Mix A.flac", "/mixes/a.flac")])
        self.ui._process_key(b"j")
        self.assertEqual(self.ui.fav_export_mode, "")

    def test_clicking_the_footer_export_label_opens_the_window(self):
        self.store.toggle(_track("Mix A.flac", "/mixes/a.flac"))
        self.ui._fav_export_button = (20, 30, 44)
        with patch.object(player.TerminalPlayerUI, "_fav_exporter_module") as module:
            module.return_value.traktor_ready.return_value = (False, "not configured")
            self.ui._process_key(b"\x1b[<0;36;20M")
        self.assertEqual(self.ui.fav_export_mode, "choose")

    def test_export_flow_prompts_for_the_tracklist_name_then_the_folder(self):
        self.store.toggle(_track("Mix A.flac", "/mixes/a.flac"))
        with patch.object(player.TerminalPlayerUI, "_fav_exporter_module") as module:
            module.return_value.traktor_ready.return_value = (False, "not configured")
            module.return_value.DEFAULT_LOCAL_DIR = Path("/tmp/fav-default")
            self.ui.fav_export_mode = "choose"
            self.ui._process_key(b"1")
            self.assertEqual(self.ui.fav_export_mode, "tracklist_name")
            for char in b"My Set":
                self.ui._process_key(bytes([char]))
            self.ui._process_key(b"\r")
        self.assertEqual(self.ui.fav_export_mode, "dest_path")
        self.assertEqual(self.ui.fav_export_name, "My Set")
        self.assertTrue(self.ui.fav_export_dest)

    def test_clear_in_favorites_opens_a_decision_window(self):
        self.store.toggle(_track("Mix A.flac", "/mixes/a.flac"))
        self.ui._arm_playlist_clear()
        self.assertTrue(self.ui.favorites_clear_confirm)
        self.assertIn("Clear the Favorites", self.ui.notification_msg)
        self.assertEqual(len(self.store), 1)

    def test_clear_on_an_empty_favorites_list_says_so(self):
        self.ui._arm_playlist_clear()
        self.assertFalse(self.ui.favorites_clear_confirm)
        self.assertIn("already empty", self.ui.notification_msg)

    def test_clear_is_cancelled_by_n_or_escape(self):
        self.store.toggle(_track("Mix A.flac", "/mixes/a.flac"))
        self.ui.favorites_clear_confirm = True
        self.ui._process_key(b"n")
        self.assertFalse(self.ui.favorites_clear_confirm)
        self.assertEqual(len(self.store), 1)
        self.ui.favorites_clear_confirm = True
        self.ui._process_key(b"\x1b")
        self.assertEqual(len(self.store), 1)

    def test_y_confirms_and_clears_only_the_playlist(self):
        self.store.toggle(_track("Mix A.flac", "/mixes/a.flac"))
        self.ui.favorites_clear_confirm = True
        with patch.object(player.TerminalPlayerUI, "_fav_exporter_module") as module:
            module.return_value.traktor_ready.return_value = (False, "not configured")
            self.ui._process_key(b"y")
        self.assertFalse(self.ui.favorites_clear_confirm)
        self.assertEqual(len(self.store), 0)
        self.assertIn("cleared", self.ui.notification_msg)

    def test_clear_then_offers_a_traktor_playlist_when_ready(self):
        self.store.toggle(_track("Mix A.flac", "/mixes/a.flac"))
        self.ui.favorites_clear_confirm = True
        with patch.object(player.TerminalPlayerUI, "_fav_exporter_module") as module:
            module.return_value.traktor_ready.return_value = (True, "ready")
            self.ui._process_key(b"y")
        self.assertEqual(len(self.store), 0)
        self.assertEqual(self.ui.traktor_offer_paths, ["/mixes/a.flac"])
        self.assertIn("Traktor playlist", self.ui.notification_msg)

    def test_clear_key_uses_the_decision_window_in_favorites(self):
        self.store.toggle(_track("Mix A.flac", "/mixes/a.flac"))
        self.ui._process_key(b"D")
        self.assertTrue(self.ui.favorites_clear_confirm)
        self.assertFalse(self.ui._playlist_clear_armed)

    def test_clear_key_keeps_the_two_step_arm_in_other_libraries(self):
        self.ui.mode = "MUSIC"
        self.ui.scanner = _Scanner([_track("Track A.flac", "/music/a.flac")])
        self.ui._process_key(b"D")
        self.assertTrue(self.ui._playlist_clear_armed)
        self.assertFalse(self.ui.favorites_clear_confirm)

    def test_traktor_offer_is_only_shown_when_ready(self):
        self.ui.traktor_offer_paths = ["/mixes/a.flac"]
        self.ui.traktor_playlist_name = "Favs"
        self.assertIn("Traktor is ready", self.ui._favorites_prompt_line())
        self.ui.traktor_offer_paths = None
        self.assertEqual(self.ui._favorites_prompt_line(), "")

    def test_decline_traktor_keeps_the_export(self):
        self.ui.traktor_offer_paths = ["/mixes/a.flac"]
        self.ui._process_key(b"n")
        self.assertIsNone(self.ui.traktor_offer_paths)
        self.assertIn("skipped", self.ui.notification_msg)

    def test_accept_traktor_asks_for_name_then_history(self):
        self.ui.traktor_offer_paths = ["/mixes/a.flac"]
        self.ui.traktor_offer_name = "Set 1"
        self.ui._process_key(b"y")
        self.assertEqual(self.ui.traktor_input_mode, "playlist_name")
        self.ui._process_key(b"\r")
        self.assertEqual(self.ui.traktor_input_mode, "history_id")
        self.assertEqual(self.ui.traktor_playlist_name, "Set 1")


class PreparationFolderTests(unittest.TestCase):
    def setUp(self):
        if not _ffmpeg():
            self.skipTest("ffmpeg and ffprobe are required")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.src = self.root / "src"
        self.src.mkdir()
        self.audio = self.src / "Airbase - Escape.flac"
        _make_audio(self.audio, 440)
        self.env = patch.dict(os.environ, {"MIX_ARCHIVE_CONFIGURED": "false", "MIX_ARCHIVE_DIR": ""}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_blank_folder_uses_the_preparation_name_under_the_given_path(self):
        parent = self.root / "drops"
        result = exporter.export_favorites(
            [self.audio], destination=str(parent), folder_name="", preparation=True, when="2026-11-15"
        )
        self.assertTrue(result["ok"], result.get("errors"))
        self.assertEqual(Path(result["dest"]).name, "DJ Set Mix Preparation Folder")
        self.assertEqual(Path(result["dest"]).parent, parent)
        self.assertTrue((Path(result["dest"]) / "Favorites.m3u").is_file())
        self.assertIn("#EXTM3U", (Path(result["dest"]) / "Favorites.m3u").read_text(encoding="utf-8"))
        text = Path(result["tracklist"]).read_text(encoding="utf-8")
        self.assertIn("generate_favorites_tracklist", text)
        self.assertTrue(self.audio.is_file())
        self.assertGreater(result["elapsed_sec"], 0)
        self.assertEqual(result["track_count"], 1)

    def test_custom_folder_name_is_kept(self):
        result = exporter.export_favorites(
            [self.audio],
            destination=str(self.root / "drops"),
            folder_name="Night Bus",
            preparation=True,
        )
        self.assertTrue(result["ok"], result.get("errors"))
        self.assertEqual(Path(result["dest"]).name, "Night Bus")

    def test_no_primary_archive_uses_the_dated_program_folder(self):
        with patch.object(exporter, "APP_DIR", self.root / "program"):
            result = exporter.export_favorites(
                [self.audio], destination=None, folder_name="", preparation=True, when="2026-11-15"
            )
        self.assertTrue(result["ok"], result.get("errors"))
        self.assertEqual(Path(result["dest"]).name, "FAVORITES_EXPORTED_2026-11-15")
        self.assertEqual(Path(result["dest"]).parent, self.root / "program")
        self.assertEqual(self.audio.stat().st_size, (Path(result["dest"]) / self.audio.name).stat().st_size)

    def test_primary_archive_root_receives_the_default_folder(self):
        archive = self.root / "archive"
        archive.mkdir()
        with patch.dict(os.environ, {"MIX_ARCHIVE_CONFIGURED": "true", "MIX_ARCHIVE_DIR": str(archive)}):
            result = exporter.export_favorites(
                [self.audio], destination=None, folder_name=None, preparation=True
            )
        self.assertTrue(result["ok"], result.get("errors"))
        self.assertEqual(result["dest"], str(archive / "DJ Set Mix Preparation Folder"))

    def test_the_same_destination_can_be_exported_again(self):
        parent = self.root / "drops"
        first = exporter.export_favorites(
            [self.audio], destination=str(parent), folder_name="DJ Set Mix Preparation Folder", preparation=True
        )
        second = exporter.export_favorites(
            [self.audio], destination=str(parent), folder_name="DJ Set Mix Preparation Folder", preparation=True
        )
        self.assertTrue(first["ok"] and second["ok"], (first, second))
        self.assertEqual(first["dest"], second["dest"])
        self.assertEqual(self.audio.stat().st_size, (Path(second["dest"]) / self.audio.name).stat().st_size)

    def test_progress_log_names_the_deck_and_the_manager_tracklist(self):
        log = []
        result = exporter.export_favorites(
            [self.audio],
            destination=str(self.root / "drops"),
            folder_name="",
            preparation=True,
            progress=log.append,
        )
        self.assertTrue(result["ok"], result.get("errors"))
        text = "\n".join(log)
        self.assertIn("DECK OPEN", text)
        self.assertIn("delete=never", text)
        self.assertIn("generate_favorites_tracklist", text)
        self.assertIn("Favorites.m3u", text)


class TraktorHistoryFavoritesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.audio = self.root / "music" / "Airbase - Escape.flac"
        self.audio.parent.mkdir()
        self.audio.write_bytes(b"fLaC" + b"\x00" * 64)
        self.other = self.root / "music" / "Kept.flac"
        self.other.write_bytes(b"fLaC" + b"\x00" * 32)
        parts = [part for part in self.audio.parent.as_posix().split("/") if part]
        directory = "/:" + "/:".join(parts) + "/:"
        entry = (
            f'<ENTRY TITLE="Escape" ARTIST="Airbase">'
            f'<LOCATION DIR="{directory}" FILE="{self.audio.name}" VOLUME=""/>'
            f'<INFO KEY="8m" PLAYTIME="300"/>'
            f"</ENTRY>"
        )
        self.collection = self.root / "collection.nml"
        self.collection.write_text(
            '<?xml version="1.0" encoding="UTF-8"?><NML VERSION="19">'
            f'<COLLECTION ENTRIES="1">{entry}</COLLECTION>'
            "<PLAYLISTS><NODE TYPE=\"FOLDER\" NAME=\"$ROOT\"><SUBNODES COUNT=\"0\"/>"
            "</NODE></PLAYLISTS></NML>",
            encoding="utf-8",
        )
        self.history_dir = self.root / "History"
        self.history_dir.mkdir()
        self.history = self.history_dir / "history_2026y11m15d_01h00m00s.nml"
        self.history.write_text(
            '<?xml version="1.0" encoding="UTF-8"?><NML VERSION="19">'
            f'<COLLECTION ENTRIES="1">{entry}</COLLECTION>'
            '<PLAYLISTS><NODE TYPE="PLAYLIST" NAME="HISTORY"><PLAYLIST ENTRIES="1" TYPE="LIST">'
            f'<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="{directory}{self.audio.name}"/></ENTRY>'
            "</PLAYLIST></NODE></PLAYLISTS></NML>",
            encoding="utf-8",
        )
        self.env = patch.dict(
            os.environ,
            {
                "TRAKTOR_COLLECTION_PATH": str(self.collection),
                "TRAKTOR_HISTORY_DIR": str(self.history_dir),
                "TRAKTOR_ENABLED": "true",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_history_id_resolves_the_collection_path(self):
        loaded = exporter.favorites_from_history(self.history.name)
        self.assertTrue(loaded["ok"], loaded)
        self.assertEqual(loaded["tracks"][0]["path"], str(self.audio))
        self.assertTrue(self.audio.is_file())

    def test_duplicate_traktor_playlist_name_is_rejected(self):
        first = exporter.create_traktor_playlist([self.audio.name], "Fav Test", self.history.name)
        self.assertTrue(first["ok"], first)
        second = exporter.create_traktor_playlist([self.audio.name], "Fav Test", self.history.name)
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason"], "duplicate_name")
        root = ET.parse(self.collection).getroot()
        nodes = [n for n in root.findall(".//PLAYLISTS//NODE") if n.attrib.get("TYPE") == "PLAYLIST"]
        self.assertEqual(len(nodes), 1)


class ExportTracksUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = player.FavoritesStore(Path(self.tmp.name) / "favorites.json")
        self.ui = player.TerminalPlayerUI.__new__(player.TerminalPlayerUI)
        self.ui.mode = "FAVORITES"
        self.ui.folder_name = "ALL"
        self.ui.keep_library = True
        self.ui._user_library_mode = "FAVORITES"
        self.ui.is_remote = False
        self.ui.engine = MagicMock()
        self.ui.engine.current_track = None
        self.ui.engine.playlist = []
        self.ui.engine.current_index = 0
        self.ui.scanner = _Scanner([])
        self.ui.export_mode = False
        self.ui.eq_naming_mode = False
        self.ui.search_mode = False
        self.ui.search_query = ""
        self.ui.selected_row = self.ui.scroll_offset = 0
        self.ui.cached_status = {"mode": "FAVORITES"}
        self.ui.notification_msg = ""
        self.ui.notification_time = 0.0
        self.ui.equalizer_hud_active = False
        self.ui.inspector_hud_active = False
        self.ui.ui_size_mode = "NORMAL"
        self.ui._playlist_clear_armed = False
        self.ui._inbuf = b""
        self.ui.favorites = self.store
        self.ui._ensure_favorites_decision_state()
        self.ui._reset_click_targets()

    def tearDown(self):
        self.tmp.cleanup()

    def test_cli_and_liquid_show_export_tracks(self):
        self.store.toggle(_track("Mix A.flac", "/mixes/a.flac"))
        lines = []
        self.ui._append_favorites_button(lines, 140, whole_row=True)
        self.assertIn("Export Tracks", lines[0])
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}):
            glass = []
            self.ui._append_liquid_action_row(glass, 180)
        self.assertIn("EXPORT TRACKS", glass[0])
        self.assertGreater(self.ui._export_tracks_span[1], self.ui._export_tracks_span[0])

    def test_export_tracks_is_absent_outside_the_favorites_playlist(self):
        """The button belongs to Favorites only; Mix and Music must not show it."""
        self.store.toggle(_track("Mix A.flac", "/mixes/a.flac"))
        for mode in ("MIX_ARCHIVE", "MUSIC"):
            self.ui.mode = mode
            with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}):
                glass = []
                self.ui._append_liquid_action_row(glass, 200)
            self.assertNotIn("EXPORT TRACKS", glass[0], mode)
            # No click target either, so a stale span cannot fire the export.
            self.assertEqual(self.ui._export_tracks_row, 0, mode)
            self.assertEqual(self.ui._export_tracks_span, (0, 0), mode)

            cli = []
            self.ui._append_favorites_button(cli, 200, whole_row=True)
            self.assertNotIn("Export Tracks", cli[0], mode)

    def test_export_is_refused_when_favorites_is_not_open(self):
        """A stale click or Shift+J must not open the deck from another library."""
        self.store.toggle(_track("Mix A.flac", "/mixes/a.flac"))
        self.ui.mode = "MUSIC"
        self.ui._favorites_export_begin()
        self.assertEqual(self.ui.fav_export_mode, "")
        self.assertIn("only available in the Favorites", self.ui.notification_msg)

    def test_empty_folder_name_follows_the_destination(self):
        self.store.toggle(_track("Mix A.flac", "/mixes/a.flac"))
        self.ui.fav_export_mode = "dest_path"
        self.ui.fav_export_kind = "local"
        self.ui.fav_export_dest = "/tmp/drops"
        self.ui._process_key(b"\r")
        self.assertEqual(self.ui.fav_export_mode, "folder_name")
        with patch.object(player.threading, "Thread") as thread:
            thread.return_value.start.return_value = None
            self.ui._process_key(b"\r")
        self.assertEqual(self.ui.fav_export_mode, "progress")
        self.assertEqual(self.ui.fav_export_folder, "")

    def test_summary_can_export_again_and_rejects_a_used_traktor_name(self):
        self.ui.fav_export_mode = "summary"
        self.ui.fav_export_result = {"ok": True, "copied": [{}], "elapsed_sec": 1.25, "dest": "/tmp/set", "playlist_name": "Set"}
        self.ui.fav_export_traktor_ready = True
        self.ui._process_key(b"y")
        self.assertEqual(self.ui.fav_export_mode, "choose")
        self.ui.fav_export_mode = ""
        self.ui.traktor_input_mode = "playlist_name"
        self.ui.traktor_playlist_name = "Set"
        self.ui._traktor_used_names = ["Set"]
        self.ui._process_key(b"\r")
        self.assertEqual(self.ui.traktor_input_mode, "playlist_name")
        self.assertIn("another name", self.ui.notification_msg.lower())

    def test_history_import_asks_to_add_or_start_new(self):
        self.store.toggle(_track("Kept.flac", "/music/kept.flac"))
        self.ui.fav_export_mode = "history_id"
        self.ui.traktor_history_id = "history_2026"
        self.ui._process_key(b"\r")
        self.assertEqual(self.ui.fav_export_mode, "history_merge")
        with patch.object(player.TerminalPlayerUI, "_fav_exporter_module") as module:
            module.return_value.favorites_from_history.return_value = {
                "ok": True,
                "history": "history_2026.nml",
                "missing": [],
                "tracks": [_track("Air.flac", "/music/air.flac")],
            }
            module.return_value.primary_mix_archive.return_value = None
            module.return_value.APP_DIR = Path("/tmp/mp")
            self.ui._process_key(b"a")
        self.assertEqual([row["path"] for row in self.store.tracks()], ["/music/kept.flac", "/music/air.flac"])
        self.assertTrue(Path("/music/kept.flac"))
        self.assertEqual(self.ui.fav_export_mode, "dest_path")

    def test_history_import_can_start_a_new_favorites_playlist(self):
        kept = self.root_audio = self.tmp.name
        self.store.toggle(_track("Kept.flac", str(Path(kept) / "Kept.flac")))
        Path(kept, "Kept.flac").write_bytes(b"fLaC")
        self.ui.fav_export_mode = "history_merge"
        self.ui.traktor_history_id = "history_2026"
        with patch.object(player.TerminalPlayerUI, "_fav_exporter_module") as module:
            module.return_value.favorites_from_history.return_value = {
                "ok": True,
                "history": "history_2026.nml",
                "missing": [],
                "tracks": [_track("Air.flac", str(Path(kept) / "Air.flac"))],
            }
            self.ui._process_key(b"n")
        self.assertEqual([row["path"] for row in self.store.tracks()], [str(Path(kept) / "Air.flac")])
        self.assertTrue(Path(kept, "Kept.flac").is_file())

    def test_liquid_drag_reorders_favorites(self):
        self.store.toggle(_track("First.flac", "/music/a.flac"))
        self.store.toggle(_track("Second.flac", "/music/b.flac"))
        self.ui.scanner = _Scanner(self.store.tracks())
        self.ui._playlist_star_rows = {10: 0, 11: 1}
        self.ui._playlist_star_col = 14
        with patch.dict(os.environ, {"MP_LIQUID_GLASS": "1"}):
            self.ui._process_key(b"\x1b[<0;30;10M")
            self.ui._process_key(b"\x1b[<32;30;11M")
        self.assertEqual([row["path"] for row in self.store.tracks()], ["/music/b.flac", "/music/a.flac"])


if __name__ == "__main__":
    unittest.main()
