#!/usr/bin/env python3
"""Import DJ playlists into the MP Audio Player playlist.

The importer must:
  * refuse to run until Music Folders are mapped
  * read every playlist from collection.nml, including nested folders
  * resolve DJ LOCATION records against the scanned Music Folders even
    when the drive or mount point changed
  * validate every file path before writing a playlist
  * keep generated playlists separate from the Music Folder indexes
"""

import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import dj_playlist_importer as importer


def _collection_xml(entries):
    root = ET.Element("NML", Version="19")
    collection = ET.SubElement(root, "COLLECTION", Entries=str(len(entries)))
    for row in entries:
        entry = ET.SubElement(collection, "ENTRY", TITLE=row["title"], ARTIST=row.get("artist", ""))
        ET.SubElement(entry, "LOCATION", DIR=row["dir"], FILE=row["file"], VOLUME=row.get("volume", "/:"))

    playlists = ET.SubElement(root, "PLAYLISTS")
    root_node = ET.SubElement(playlists, "NODE", TYPE="FOLDER", NAME="$ROOT")
    subnodes = ET.SubElement(root_node, "SUBNODES", COUNT="2")

    house = ET.SubElement(subnodes, "NODE", TYPE="FOLDER", NAME="House")
    house_subs = ET.SubElement(house, "SUBNODES", COUNT="1")
    playlist = ET.SubElement(house_subs, "NODE", TYPE="PLAYLIST", NAME="Deep House")
    content = ET.SubElement(playlist, "PLAYLIST", ENTRIES="2", TYPE="LIST", UUID="abc")
    for key in (entries[0]["key"], entries[1]["key"]):
        item = ET.SubElement(content, "ENTRY")
        ET.SubElement(item, "PRIMARYKEY", TYPE="TRACK", KEY=key)

    second = ET.SubElement(subnodes, "NODE", TYPE="PLAYLIST", NAME="Warm Up")
    second_content = ET.SubElement(second, "PLAYLIST", ENTRIES="1", TYPE="LIST", UUID="def")
    item = ET.SubElement(second_content, "ENTRY")
    ET.SubElement(item, "PRIMARYKEY", TYPE="TRACK", KEY=entries[2]["key"])
    return ET.tostring(root, encoding="utf-8")


class TestDJImporter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.playlists = self.root / "PLAYLISTS_GENERATED"
        self.playlists.mkdir()
        self.music = self.root / "MUSIC"

    def tearDown(self):
        self.tmp.cleanup()

    def _patches(self, folders, catalog=None):
        return [
            patch.object(importer, "playlists_dir", return_value=self.playlists),
            patch.object(importer, "load_folders", return_value=folders),
        ]

    def test_refuses_to_run_without_music_folders(self):
        with patch.object(importer, "playlists_dir", return_value=self.playlists), \
             patch.object(importer, "load_folders", return_value=[]):
            ok, reason = importer.music_folders_ready()
            self.assertFalse(ok)
            self.assertIn("No Music Folders", reason)
            result = importer.generate_all(self.root / "missing.nml", interactive=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_music_folders")
        self.assertFalse((self.playlists / importer.CATALOG_NAME).exists())

    def test_lists_every_playlist_including_nested_folders(self):
        collection = self.root / "collection.nml"
        collection.write_bytes(_collection_xml([
            {"title": "A", "file": "a.flac", "dir": "/:Users/:dj/:Music/:", "key": "/:Users/:dj/:Music/:a.flac"},
            {"title": "B", "file": "b.flac", "dir": "/:Users/:dj/:Music/:", "key": "/:Users/:dj/:Music/:b.flac"},
            {"title": "C", "file": "c.flac", "dir": "/:Users/:dj/:Music/:", "key": "/:Users/:dj/:Music/:c.flac"},
        ]))
        rows = importer.list_dj_playlists(collection)
        self.assertEqual([row["name"] for row in rows], ["House/Deep House", "Warm Up"])
        self.assertEqual(rows[0]["tracks"], 2)

    def test_moved_mount_resolves_through_the_music_folder_index(self):
        self.music.mkdir()
        (self.music / "a.flac").write_bytes(b"x")
        (self.music / "b.flac").write_bytes(b"y")
        (self.music / "c.flac").write_bytes(b"z")
        index = [
            {"name": "a.flac", "title": "A", "path": str(self.music / "a.flac"), "folder": "MUSIC",
             "subpath": "a.flac", "ext": "FLAC"},
            {"name": "b.flac", "title": "B", "path": str(self.music / "b.flac"), "folder": "MUSIC",
             "subpath": "b.flac", "ext": "FLAC"},
            {"name": "c.flac", "title": "C", "path": str(self.music / "c.flac"), "folder": "MUSIC",
             "subpath": "c.flac", "ext": "FLAC"},
        ]
        (self.playlists / "DJ_Tracks_MUSIC.json").write_text(json.dumps(index), encoding="utf-8")
        collection = self.root / "collection.nml"
        collection.write_bytes(_collection_xml([
            {"title": "A", "file": "a.flac", "dir": "/:Users/:dj/:Music/:", "key": "/:Users/:dj/:Music/:a.flac"},
            {"title": "B", "file": "b.flac", "dir": "/:Users/:dj/:Music/:", "key": "/:Users/:dj/:Music/:b.flac"},
            {"title": "C", "file": "c.flac", "dir": "/:Users/:dj/:Music/:", "key": "/:Users/:dj/:Music/:c.flac"},
        ]))
        folders = [{"id": "MUSIC", "type": "local", "name": "MUSIC", "path": str(self.music)}]
        with patch.object(importer, "playlists_dir", return_value=self.playlists), \
             patch.object(importer, "load_folders", return_value=folders), \
             patch.object(importer, "ensure_music_folder_indexes", return_value=True), \
             patch.object(importer, "music_folder_index_ready", return_value=(True, [])):
            result = importer.generate_all(collection, interactive=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["resolved"], 3)
        catalog = json.loads((self.playlists / importer.CATALOG_NAME).read_text(encoding="utf-8"))
        names = sorted(item["name"] for item in catalog["playlists"])
        self.assertEqual(names, ["House/Deep House", "Warm Up"])
        generated = json.loads((self.playlists / "DJAPP_House_Deep_House.json").read_text(encoding="utf-8"))
        self.assertEqual([t["path"] for t in generated],
                         [str(self.music / "a.flac"), str(self.music / "b.flac")])
        self.assertTrue((self.playlists / "DJAPP_House_Deep_House.m3u8").is_file())

    def test_missing_files_are_never_written(self):
        self.music.mkdir()
        (self.music / "a.flac").write_bytes(b"x")
        index = [{"name": "a.flac", "title": "A", "path": str(self.music / "a.flac"),
                  "folder": "MUSIC", "subpath": "a.flac", "ext": "FLAC"}]
        (self.playlists / "DJ_Tracks_MUSIC.json").write_text(json.dumps(index), encoding="utf-8")
        collection = self.root / "collection.nml"
        collection.write_bytes(_collection_xml([
            {"title": "A", "file": "a.flac", "dir": "/:Users/:dj/:Music/:", "key": "/:Users/:dj/:Music/:a.flac"},
            {"title": "B", "file": "gone.flac", "dir": "/:Users/:dj/:Music/:", "key": "/:Users/:dj/:Music/:gone.flac"},
            {"title": "C", "file": "c.flac", "dir": "/:Users/:dj/:Music/:", "key": "/:Users/:dj/:Music/:c.flac"},
        ]))
        folders = [{"id": "MUSIC", "type": "local", "name": "MUSIC", "path": str(self.music)}]
        with patch.object(importer, "playlists_dir", return_value=self.playlists), \
             patch.object(importer, "load_folders", return_value=folders), \
             patch.object(importer, "ensure_music_folder_indexes", return_value=True):
            result = importer.generate_all(collection, interactive=False)
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["missing"], 2)
        generated = json.loads((self.playlists / "DJAPP_House_Deep_House.json").read_text(encoding="utf-8"))
        self.assertEqual([t["path"] for t in generated], [str(self.music / "a.flac")])

    def test_generated_playlists_require_their_files_to_exist(self):
        (self.playlists / importer.CATALOG_NAME).write_text(json.dumps({
            "version": 1,
            "playlists": [
                {"name": "Ready", "slug": "Ready", "json": "DJ_Ready.json",
                 "m3u8": "DJ_Ready.m3u8", "tracks": 1},
                {"name": "Stale", "slug": "Stale", "json": "DJ_Stale.json",
                 "m3u8": "DJ_Stale.m3u8", "tracks": 1},
            ],
        }), encoding="utf-8")
        (self.playlists / "DJ_Ready.json").write_text("[]", encoding="utf-8")
        (self.playlists / "DJ_Ready.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
        with patch.object(importer, "playlists_dir", return_value=self.playlists):
            rows = importer.generated_playlists()
        self.assertEqual([row["name"] for row in rows], ["Ready"])

    def test_validate_rejects_missing_and_empty_files(self):
        empty = self.root / "empty.flac"
        empty.write_bytes(b"")
        real = self.root / "real.flac"
        real.write_bytes(b"audio")
        self.assertFalse(importer.validate_track_path(str(self.root / "nope.flac")))
        self.assertFalse(importer.validate_track_path(str(empty)))
        self.assertTrue(importer.validate_track_path(str(real)))

    def test_playlist_keys_survive_a_missing_collection_entry(self):
        collection = self.root / "collection.nml"
        collection.write_bytes(_collection_xml([
            {"title": "A", "file": "a.flac", "dir": "/:Users/:dj/:Music/:", "key": "/:Users/:dj/:Music/:a.flac"},
            {"title": "B", "file": "b.flac", "dir": "/:Users/:dj/:Music/:", "key": "/:Users/:dj/:Music/:b.flac"},
            {"title": "C", "file": "c.flac", "dir": "/:Users/:dj/:Music/:", "key": "/:Users/:dj/:Music/:c.flac"},
        ]))
        rebuilt = importer._track_from_pkey("/:Users/:dj/:Music/:a.flac")
        self.assertEqual(rebuilt["file"], "a.flac")
        self.assertEqual(rebuilt["basename"], "a.flac")
        self.assertEqual(importer._dir_parts(rebuilt), ["Users", "dj", "Music"])

    def test_stale_generated_files_are_removed_but_other_playlists_are_kept(self):
        self.music.mkdir()
        (self.music / "a.flac").write_bytes(b"x")
        index = [{"name": "a.flac", "title": "A", "path": str(self.music / "a.flac"),
                  "folder": "MUSIC", "subpath": "a.flac", "ext": "FLAC"}]
        (self.playlists / "DJ_Tracks_MUSIC.json").write_text(json.dumps(index), encoding="utf-8")
        stale_json = self.playlists / "DJAPP_Old_Set.json"
        stale_json.write_text("[]", encoding="utf-8")
        offline_json = self.playlists / "DJAPP_Warm_Up.json"
        offline_json.write_text("[]", encoding="utf-8")
        keep_json = self.playlists / "DJ_Tracks_ALL_MUSIC_FOLDERS.json"
        keep_json.write_text("[]", encoding="utf-8")
        collection = self.root / "collection.nml"
        collection.write_bytes(_collection_xml([
            {"title": "A", "file": "a.flac", "dir": "/:Users/:dj/:Music/:", "key": "/:Users/:dj/:Music/:a.flac"},
            {"title": "B", "file": "b.flac", "dir": "/:Users/:dj/:Music/:", "key": "/:Users/:dj/:Music/:b.flac"},
            {"title": "C", "file": "c.flac", "dir": "/:Users/:dj/:Music/:", "key": "/:Users/:dj/:Music/:c.flac"},
        ]))
        folders = [{"id": "MUSIC", "type": "local", "name": "MUSIC", "path": str(self.music)}]
        with patch.object(importer, "playlists_dir", return_value=self.playlists), \
             patch.object(importer, "load_folders", return_value=folders), \
             patch.object(importer, "ensure_music_folder_indexes", return_value=True):
            importer.generate_all(collection, interactive=False)
        self.assertFalse(stale_json.exists())
        self.assertTrue(keep_json.exists())
        # Warm Up still exists in DJ but none of its files were available,
        # so its last generated copy is kept rather than deleted.
        self.assertTrue(offline_json.exists())


if __name__ == "__main__":
    unittest.main()
