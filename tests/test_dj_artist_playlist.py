#!/usr/bin/env python3
"""Artist playlists written into a DJ collection.nml.

The Tracklist menu asks for an artist name and a playlist name, then reuses
insert_playlist_into_collection. These tests cover that scan, plus rename,
remove, and the folder tree the submenu walks.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "generate_dj_playlist_from_history.py"
MANAGER = REPO / "Mix_Archive_Manager.sh"
sys.path.insert(0, str(REPO))

from scripts import generate_dj_playlist_from_history as dj  # noqa: E402


def _entry(filename: str, artist: str, title: str) -> str:
    def _xml(value: str) -> str:
        return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")

    safe_artist = _xml(artist)
    safe_title = _xml(title)
    safe_file = _xml(filename)
    return (
        f'<ENTRY TITLE="{safe_title}" ARTIST="{safe_artist}">'
        f'<LOCATION DIR="/:music/:" FILE="{safe_file}" VOLUME=""/>'
        '<INFO KEY="8m" PLAYTIME="180"/>'
        '<TEMPO BPM="138.0"/>'
        '<MUSICAL_KEY VALUE="21"/>'
        "</ENTRY>"
    )


def _collection_xml() -> str:
    tracks = (
        _entry("Airbase - Escape.flac", "Airbase", "Escape")
        + _entry("Airbase - Zed.flac", "Airbase", "Zed")
        + _entry("Airbase - Alpha.flac", "Airbase", "Alpha")
        + _entry("Airbase - Club.flac", "Airbase", "Club (Airbase Extended Mix)")
        + _entry("Someone - Horizon.flac", "Someone", "Horizon (Airbase Remix)")
        + _entry("Guest - Night.flac", "Guest", "Night (Luminosity Remix)")
        + _entry("Other - Airbaseball.flac", "Other Artist", "Airbaseball")
        + _entry("Airbase feat. Someone.flac", "Airbase feat. Someone", "Together")
        + _entry("Eloquentia & Airbase - Hemisphere.flac", "Eloquentia & Airbase", "Hemisphere (Original Mix)")
        + _entry("Airbaseball - Game.flac", "Airbaseball", "Game")
        + _entry("Above & Beyond - Sun.flac", "Above & Beyond", "Sun & Moon")
        + _entry("Other - Track.flac", "Other Artist", "Track")
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<NML VERSION="19">'
        f'<COLLECTION ENTRIES="12">{tracks}</COLLECTION>'
        "<PLAYLISTS>"
        '<NODE TYPE="FOLDER" NAME="$ROOT">'
        '<SUBNODES COUNT="2">'
        '<NODE TYPE="FOLDER" NAME="House">'
        '<SUBNODES COUNT="1">'
        '<NODE TYPE="PLAYLIST" NAME="Friday">'
        '<PLAYLIST ENTRIES="1" TYPE="LIST" UUID="friday">'
        '<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="/:music/:Airbase - Escape.flac"/></ENTRY>'
        "</PLAYLIST></NODE>"
        "</SUBNODES></NODE>"
        '<NODE TYPE="PLAYLIST" NAME="All">'
        '<PLAYLIST ENTRIES="0" TYPE="LIST" UUID="all"/>'
        "</NODE>"
        "</SUBNODES></NODE>"
        "</PLAYLISTS></NML>"
    )


class ArtistPlaylistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.collection = self.root / "collection.nml"
        self.collection.write_text(_collection_xml(), encoding="utf-8")
        self.env = patch.dict(os.environ, {"DJ_COLLECTION_PATH": str(self.collection)})
        self.env.start()
        self.running = patch.object(dj, "is_dj_running", return_value=False)
        self.running.start()

    def tearDown(self):
        self.running.stop()
        self.env.stop()
        self.tmp.cleanup()

    def _names(self):
        root = ET.parse(self.collection).getroot()
        return [
            node.attrib.get("NAME", "")
            for node in root.findall(".//NODE")
            if node.attrib.get("TYPE") == "PLAYLIST"
        ]

    def _playlist(self, name: str):
        root = ET.parse(self.collection).getroot()
        for node in root.findall(".//NODE"):
            if node.attrib.get("TYPE") == "PLAYLIST" and node.attrib.get("NAME") == name:
                return node
        return None

    def test_exact_artist_is_case_insensitive_and_title_sorted(self):
        result = dj.create_artist_playlist("  airbase ", "Airbase Live", str(self.collection))
        self.assertTrue(result["ok"], result)
        # Artist-field hits include the exact name and collaborations that
        # contain it. Horizon comes from the title. Club matches both and is
        # kept once.
        self.assertEqual(result["matched"], 7)
        self.assertEqual(result["removed_duplicates"], 1)
        self.assertIn("Removed 1 duplicate", result["message"])
        node = self._playlist("Airbase Live")
        self.assertIsNotNone(node)
        self.assertEqual(node.find("PLAYLIST").attrib["ENTRIES"], "7")
        keys = [item.attrib["KEY"] for item in node.findall(".//PRIMARYKEY")]
        self.assertEqual(
            keys,
            [
                "/:music/:Airbase - Alpha.flac",
                "/:music/:Airbase - Club.flac",
                "/:music/:Airbase - Escape.flac",
                "/:music/:Eloquentia & Airbase - Hemisphere.flac",
                "/:music/:Someone - Horizon.flac",
                "/:music/:Airbase feat. Someone.flac",
                "/:music/:Airbase - Zed.flac",
            ],
        )
        self.assertEqual(len(keys), len(set(keys)))
        self.assertNotIn("/:music/:Airbaseball - Game.flac", keys)
        self.assertNotIn("/:music/:Other - Airbaseball.flac", keys)
        self.assertNotIn("/:music/:Other - Track.flac", keys)
        self.assertNotIn("/:music/:Guest - Night.flac", keys)
        self.assertTrue(list(self.root.glob("collection.nml.bak_*")))

    def test_title_only_credit_is_collected_without_a_duplicate(self):
        result = dj.create_artist_playlist("luminosity", "Luminosity Remixes", str(self.collection))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["removed_duplicates"], 0)
        node = self._playlist("Luminosity Remixes")
        self.assertEqual(
            node.find(".//PRIMARYKEY").attrib["KEY"],
            "/:music/:Guest - Night.flac",
        )
        self.assertEqual(node.find("PLAYLIST").attrib["ENTRIES"], "1")

    def test_ampersand_artist_matches_the_collection_text(self):
        result = dj.create_artist_playlist(
            "above & beyond", "Anjuna", str(self.collection)
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["matched"], 1)
        node = self._playlist("Anjuna")
        self.assertEqual(
            node.find(".//PRIMARYKEY").attrib["KEY"],
            "/:music/:Above & Beyond - Sun.flac",
        )

    def test_missing_artist_writes_nothing_and_offers_a_close_name(self):
        before = self.collection.read_bytes()
        result = dj.create_artist_playlist("Airb", "Nope", str(self.collection))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_match")
        self.assertEqual(self.collection.read_bytes(), before)
        names = [row["artist"] for row in result["suggestions"]]
        self.assertIn("Airbase", names)

    def test_empty_names_and_duplicate_playlist_are_refused(self):
        before = self.collection.read_bytes()
        blank = dj.create_artist_playlist("", "Name", str(self.collection))
        self.assertEqual(blank["reason"], "artist")
        unnamed = dj.create_artist_playlist("Airbase", "   ", str(self.collection))
        self.assertEqual(unnamed["reason"], "name")
        self.assertEqual(self.collection.read_bytes(), before)
        first = dj.create_artist_playlist("Airbase", "Friday", str(self.collection))
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["reason"], "updated")
        self.assertEqual(first["added"], 6)
        self.assertEqual(first["matched"], 7)
        self.assertEqual(self._names().count("Friday"), 1)
        node = self._playlist("Friday")
        keys = [item.attrib["KEY"] for item in node.findall(".//PRIMARYKEY")]
        self.assertEqual(keys[0], "/:music/:Airbase - Escape.flac")
        self.assertEqual(
            keys,
            [
                "/:music/:Airbase - Escape.flac",
                "/:music/:Airbase - Alpha.flac",
                "/:music/:Airbase - Club.flac",
                "/:music/:Eloquentia & Airbase - Hemisphere.flac",
                "/:music/:Someone - Horizon.flac",
                "/:music/:Airbase feat. Someone.flac",
                "/:music/:Airbase - Zed.flac",
            ],
        )
        self.assertEqual(node.find("PLAYLIST").attrib["ENTRIES"], "7")
        snapshot = self.collection.read_bytes()
        second = dj.create_artist_playlist("Airbase", "Friday", str(self.collection))
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["reason"], "unchanged")
        self.assertEqual(second["added"], 0)
        self.assertEqual(second["matched"], 7)
        self.assertEqual(self.collection.read_bytes(), snapshot)

    def test_running_dj_blocks_the_live_collection_only(self):
        with patch.object(dj, "is_dj_running", return_value=True):
            blocked = dj.create_artist_playlist("Airbase", "Blocked", str(self.collection))
        self.assertEqual(blocked["reason"], "running")
        self.assertIsNone(self._playlist("Blocked"))
        other = self.root / "copy.nml"
        other.write_text(_collection_xml(), encoding="utf-8")
        with patch.object(dj, "is_dj_running", return_value=True):
            allowed = dj.create_artist_playlist("Airbase", "Copy List", str(other))
        self.assertTrue(allowed["ok"], allowed)
        self.assertIsNone(self._playlist("Copy List"))

    def test_close_dj_allows_the_write(self):
        with patch.object(dj, "is_dj_running", return_value=True), patch.object(
            dj, "quit_dj", return_value=True
        ) as quit_dj:
            result = dj.create_artist_playlist(
                "Airbase", "After Quit", str(self.collection), close_dj=True
            )
        self.assertTrue(result["ok"], result)
        quit_dj.assert_called_once()

    def test_folder_tree_lists_nested_playlists(self):
        tree = dj.load_playlist_tree(str(self.collection))
        folders = {row["name"]: row for row in tree["folders"]}
        playlists = {row["path"]: row for row in tree["playlists"]}
        self.assertEqual(folders["House"]["parent"], "")
        self.assertEqual(folders["House"]["playlists"], 1)
        friday = f"House{dj.PLAYLIST_PATH_SEP}Friday"
        self.assertEqual(playlists[friday]["entries"], 1)
        self.assertEqual(playlists[friday]["folder"], "House")
        self.assertEqual(playlists["All"]["folder"], "")
        self.assertEqual(playlists["All"]["entries"], 0)
        text = dj.format_playlist_tree_tsv(tree["folders"], tree["playlists"])
        self.assertIn("FOLDER", text)
        self.assertIn("PLAYLIST", text)
        self.assertIn(friday, text)

    def test_rename_and_remove_keep_the_audio_entries(self):
        friday = f"House{dj.PLAYLIST_PATH_SEP}Friday"
        renamed = dj.rename_playlist(friday, "Saturday Night", str(self.collection))
        self.assertTrue(renamed["ok"], renamed)
        node = self._playlist("Saturday Night")
        self.assertIsNotNone(node)
        self.assertEqual(
            node.find(".//PRIMARYKEY").attrib["KEY"],
            "/:music/:Airbase - Escape.flac",
        )
        self.assertIsNone(self._playlist("Friday"))
        clash = dj.rename_playlist(
            f"House{dj.PLAYLIST_PATH_SEP}Saturday Night", "all", str(self.collection)
        )
        self.assertEqual(clash["reason"], "exists")
        self.assertIsNotNone(self._playlist("Saturday Night"))
        removed = dj.remove_playlist(
            f"House{dj.PLAYLIST_PATH_SEP}Saturday Night", str(self.collection)
        )
        self.assertTrue(removed["ok"], removed)
        root = ET.parse(self.collection).getroot()
        self.assertIsNone(self._playlist("Saturday Night"))
        self.assertIsNotNone(self._playlist("All"))
        house = next(
            node for node in root.findall(".//NODE") if node.attrib.get("NAME") == "House"
        )
        self.assertEqual(house.find("SUBNODES").attrib["COUNT"], "0")
        artists = [entry.attrib.get("ARTIST") for entry in root.find("COLLECTION").findall("ENTRY")]
        self.assertIn("Airbase", artists)
        missing = dj.remove_playlist("Does Not Exist", str(self.collection))
        self.assertEqual(missing["reason"], "missing")

    def test_show_playlist_resolves_collection_titles(self):
        details = dj.playlist_entries(
            str(self.collection), f"House{dj.PLAYLIST_PATH_SEP}Friday"
        )
        self.assertTrue(details["ok"])
        self.assertEqual(details["tracks"][0]["artist"], "Airbase")
        self.assertEqual(details["tracks"][0]["title"], "Escape")
        shown = dj.format_playlist_entries(details)
        self.assertIn("Escape", shown)
        self.assertIn("House", details["folder"])

    def test_cli_json_round_trip(self):
        created = subprocess.run(
            [
                sys.executable, str(SCRIPT),
                "--artist", "Airbase",
                "--playlist-name", "CLI Airbase",
                "--collection", str(self.collection),
                "--json",
            ],
            capture_output=True, text=True, timeout=20,
            env=dict(os.environ, DJ_COLLECTION_PATH=str(self.collection)),
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        payload = json.loads(created.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["matched"], 7)
        self.assertEqual(payload["removed_duplicates"], 1)
        listed = subprocess.run(
            [
                sys.executable, str(SCRIPT),
                "--list-playlist-tree", "--json",
                "--collection", str(self.collection),
            ],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(listed.returncode, 0, listed.stderr)
        tree = json.loads(listed.stdout)
        names = [row["name"] for row in tree["playlists"]]
        self.assertIn("CLI Airbase", names)
        self.assertIn("Friday", names)


class ArtistPlaylistMenuTests(unittest.TestCase):
    def test_tracklist_menu_routes_the_artist_tool(self):
        text = MANAGER.read_text(encoding="utf-8", errors="replace")
        start = text.index("manage_tracklist_suite() {")
        body = text[start:start + 8000]
        self.assertIn(
            '"10|Generate DJ Playlist from Music Artist|',
            body,
        )
        self.assertIn(
            "10|artist-playlist|dj-artist|generate-dj-artist) generate_dj_artist_playlist_menu",
            body,
        )
        menu = text.index("generate_dj_artist_playlist_menu() {")
        menu_body = text[menu:menu + 2500]
        self.assertIn(
            'run_interactive_submenu "Generate DJ Playlist from Music Artist"',
            menu_body,
        )
        self.assertIn("Create Playlist from Artist Name", menu_body)
        self.assertIn("Rename a DJ Playlist", menu_body)
        self.assertIn("Remove a DJ Playlist", menu_body)
        self.assertIn("View Playlist Folders", menu_body)
        helpers = text[text.index("_dj_json_field() {"):start]
        self.assertIn("_dj_browse_playlist_level", helpers)
        self.assertIn("generate_dj_playlist_from_history --artist", helpers)
        self.assertIn("generate_dj_playlist_from_history --list-playlist-tree", helpers)
        self.assertIn("generate_dj_playlist_from_history --rename-playlist", helpers)
        self.assertIn("generate_dj_playlist_from_history --remove-playlist", helpers)
        self.assertNotIn('python3 "$py"', helpers)
        py_text = (REPO / "scripts" / "generate_dj_playlist_from_history.py").read_text(encoding="utf-8")
        self.assertIn("insert_playlist_into_collection", py_text)
        create = py_text.index("def create_artist_playlist(")
        self.assertIn("insert_playlist_into_collection", py_text[create:create + 1800])

    def test_manager_script_syntax(self):
        result = subprocess.run(["bash", "-n", str(MANAGER)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
