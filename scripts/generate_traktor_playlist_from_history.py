#!/usr/bin/env python3
"""
scripts/generate_traktor_playlist_from_history.py - Traktor Playlist Generator

Builds a Traktor playlist from a Traktor Pro History (.nml) session and inserts
it into collection.nml under the default $ROOT playlist folder.

The same helpers are imported by MP_Fav_Playlist_Track_Exporter.py so the
Favorites exporter can create a playlist for the exact tracks it just copied.
The Tracklist menu's artist playlist tool uses them too: it scans the
collection artist field for one name, then the title field, and inserts a
playlist. A field matches when it contains the name as a whole phrase, so a
collaboration or remix credit is included. A track found by both searches is
written once. When the named playlist already exists, only tracks that are
not already in it are added. Duplicate rows are removed after that write.

Interactive use (Manager menu option 17 > 3):
    python3 scripts/generate_traktor_playlist_from_history.py

Headless use:
    python3 scripts/generate_traktor_playlist_from_history.py \
        --history-id history_2026y07m20d_08h01m28s.nml \
        --playlist-name "Favourites 2026-11-12"

Artist playlist (Manager Tracklist menu):
    python3 scripts/generate_traktor_playlist_from_history.py \
        --artist "Airbase" --playlist-name "Airbase — Collection"
"""

from __future__ import annotations

import argparse
import copy
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
NC = "\033[0m"

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_CANDIDATES = (
    APP_DIR / "config.env",
    Path(__file__).resolve().parent / "config.env",
    Path.cwd() / "config.env",
    Path.home() / ".config" / "mix-manager" / "config.env",
)


# ------------------------------------------------------------------------------
# Configuration and Traktor discovery
# ------------------------------------------------------------------------------
def load_config() -> Dict[str, str]:
    """Read config.env values, then let real environment variables win."""
    cfg: Dict[str, str] = {}
    for candidate in CONFIG_CANDIDATES:
        if not candidate.is_file():
            continue
        try:
            for raw in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                key, value = line.split("=", 1)
                cfg[key.strip()] = value.strip().strip('"').strip("'")
        except OSError:
            continue
        break
    cfg.update({key: value for key, value in os.environ.items() if value})
    return cfg


def _expand(value: Optional[str]) -> str:
    return os.path.expandvars(os.path.expanduser(str(value or "").strip().strip('"').strip("'")))


def _candidate_dirs(cfg: Dict[str, str]) -> List[str]:
    """Likely roots that hold a Traktor user folder or synced History folder."""
    candidates: List[str] = []
    for key in (
        "TRAKTOR_HISTORY_DIR",
        "TRAKTOR_AUDIO_ROOT",
        "MIX_ARCHIVE_DIR",
        "TRAKTOR_SMB_MOUNT",
        "TRAKTOR_NFS_MOUNT",
    ):
        value = _expand(cfg.get(key, ""))
        if value:
            candidates.append(value)
    candidates.extend(
        [
            str(Path.home() / "Documents" / "Native Instruments"),
            str(Path.home() / "Library" / "Application Support" / "Native Instruments"),
            str(Path.home() / "Music" / "Native Instruments"),
            str(APP_DIR / "MIX_ARCHIVE"),
            "/Volumes/MIX_ARCHIVE",
            "/run/media/mplanetarian/WD BLACK B/MIX_ARCHIVE",
        ]
    )
    unique: List[str] = []
    for value in candidates:
        if value and os.path.isdir(value) and value not in unique:
            unique.append(value)
    return unique


def find_collection_path(cfg: Optional[Dict[str, str]] = None) -> Optional[Path]:
    cfg = cfg or load_config()
    configured = _expand(cfg.get("TRAKTOR_COLLECTION_PATH", ""))
    if configured and os.path.isfile(configured):
        return Path(configured).resolve()

    patterns = [
        "Traktor*/collection.nml",
        "Native Instruments/Traktor*/collection.nml",
        "*/History/../collection.nml",
    ]
    found: List[Path] = []
    for root in _candidate_dirs(cfg):
        for pattern in patterns:
            for match in glob.glob(os.path.join(root, pattern)):
                if os.path.isfile(match):
                    found.append(Path(match).resolve())
    if not found:
        return None
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return found[0]


def find_traktor_user_dir(cfg: Optional[Dict[str, str]] = None) -> Optional[Path]:
    collection = find_collection_path(cfg)
    if collection:
        return collection.parent
    dirs: List[Path] = []
    for root in _candidate_dirs(cfg or load_config()):
        for match in glob.glob(os.path.join(root, "Traktor*")):
            path = Path(match)
            if path.is_dir() and (path / "History").is_dir():
                dirs.append(path)
    if not dirs:
        return None
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return dirs[0]


def find_history_dirs(
    cfg: Optional[Dict[str, str]] = None,
    extra: Optional[Iterable[str]] = None,
) -> List[Path]:
    cfg = cfg or load_config()
    candidates: List[str] = []
    configured = _expand(cfg.get("TRAKTOR_HISTORY_DIR", ""))
    if configured:
        candidates.append(configured)
    user_dir = find_traktor_user_dir(cfg)
    if user_dir:
        candidates.append(str(user_dir / "History"))
    for root in _candidate_dirs(cfg):
        candidates.extend(glob.glob(os.path.join(root, "Traktor*/History")))
        candidates.extend(glob.glob(os.path.join(root, "**", "Traktor*/History"), recursive=False))
    candidates.extend(_expand(value) for value in (extra or []))
    unique: List[Path] = []
    for value in candidates:
        if not value:
            continue
        path = Path(value)
        if path.is_dir():
            resolved = path.resolve()
            if resolved not in unique:
                unique.append(resolved)
    return unique


def _search_dirs(
    history_dirs: Optional[Iterable[str]], cfg: Optional[Dict[str, str]]
) -> List[Path]:
    """Explicit directories are authoritative; otherwise every known folder."""
    if history_dirs is not None:
        explicit: List[Path] = []
        for value in history_dirs:
            path = Path(_expand(str(value)))
            if path.is_dir() and path.resolve() not in explicit:
                explicit.append(path.resolve())
        return explicit
    return find_history_dirs(cfg)


def list_history_files(
    limit: int = 30,
    history_dirs: Optional[Iterable[str]] = None,
    cfg: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    cfg = cfg or load_config()
    files: List[Dict[str, Any]] = []
    for directory in _search_dirs(history_dirs, cfg):
        for path in sorted(Path(directory).glob("history_*.nml")):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            match = re.search(
                r"history_(\d{4})y(\d{2})m(\d{2})d_(\d{2})h(\d{2})m(\d{2})s", path.name
            )
            stamp = (
                f"{match.group(1)}-{match.group(2)}-{match.group(3)} "
                f"{match.group(4)}:{match.group(5)}:{match.group(6)}"
                if match
                else "Unknown Date"
            )
            files.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "id": path.stem,
                    "mtime": stat.st_mtime,
                    "size": stat.st_size,
                    "datetime": stamp,
                    "dir": str(Path(directory)),
                }
            )
    files.sort(key=lambda row: row["mtime"], reverse=True)
    return files[:limit]


def preferred_history_dirs(
    collection_path: Optional[Path], cfg: Optional[Dict[str, str]] = None
) -> List[str]:
    """History folders that belong to the Traktor collection being written.

    When the collection has a sibling History folder (a normal Traktor user
    folder, or the folder a synced history tree was copied into) that folder is
    authoritative. Otherwise every discovered History folder is used.
    """
    if collection_path:
        sibling = Path(collection_path).parent / "History"
        if sibling.is_dir():
            return [str(sibling)]
    return [str(path) for path in find_history_dirs(cfg)]


def resolve_history_file(
    history_id: Optional[str],
    history_dirs: Optional[Iterable[str]] = None,
    cfg: Optional[Dict[str, str]] = None,
) -> Optional[Path]:
    """Resolve a history file from an index, full path, ID or filename."""
    files = list_history_files(limit=500, history_dirs=history_dirs, cfg=cfg)
    if not files:
        return None
    raw = (history_id or "").strip().strip('"').strip("'")
    if not raw:
        return Path(files[0]["path"])
    if raw.isdigit():
        index = int(raw) - 1
        if 0 <= index < len(files):
            return Path(files[index]["path"])
        return None
    expanded = Path(_expand(raw))
    if expanded.is_file():
        return expanded.resolve()
    lowered = raw.lower()
    basename = os.path.basename(lowered)
    for row in files:
        if row["name"].lower() == basename or row["id"].lower() == basename:
            return Path(row["path"])
    for row in files:
        if lowered in row["name"].lower() or lowered in row["id"].lower():
            return Path(row["path"])
    for directory in _search_dirs(history_dirs, cfg):
        candidate = Path(directory) / raw
        if candidate.is_file():
            return candidate.resolve()
        if not raw.endswith(".nml") and candidate.with_suffix(".nml").is_file():
            return candidate.with_suffix(".nml").resolve()
    return None


def is_traktor_running() -> bool:
    if not sys.platform.startswith(("darwin", "linux", "freebsd")):
        return False
    try:
        return bool(subprocess.check_output(["pgrep", "-x", "Traktor"], stderr=subprocess.DEVNULL).strip())
    except Exception:
        return False


def quit_traktor() -> bool:
    """Close Traktor cleanly so it flushes collection.nml before we write it."""
    if not is_traktor_running():
        return True
    if sys.platform == "darwin":
        try:
            subprocess.run(["osascript", "-e", 'tell application "Traktor" to quit'], check=False)
        except Exception:
            pass
        for _ in range(20):
            if not is_traktor_running():
                return True
            time.sleep(0.5)
    return not is_traktor_running()


# ------------------------------------------------------------------------------
# NML parsing
# ------------------------------------------------------------------------------
def _decode_name(value: str) -> str:
    try:
        return urllib.parse.unquote(str(value or ""))
    except Exception:
        return str(value or "")


def traktor_fs_path(volume: str, directory: str, filename: str) -> str:
    """Turn a Traktor LOCATION into a filesystem path. Nothing is deleted."""
    filename = _decode_name(filename)
    directory = _decode_name(directory)
    folder = directory.replace("/:", "/")
    folder = folder.replace(":", "")
    if folder and not folder.startswith("/"):
        folder = "/" + folder
    folder = os.path.normpath(folder or "/")
    path = os.path.join(folder, filename) if filename else folder
    if os.path.isfile(path):
        return path
    label = _decode_name(volume).strip().strip("/")
    if label:
        user = os.environ.get("USER") or ""
        roots = [os.path.join("/Volumes", label), os.path.join("/media", label)]
        if user:
            roots.append(os.path.join("/run/media", user, label))
        for root in roots:
            candidate = os.path.join(root, folder.lstrip("/"), filename)
            if os.path.isfile(candidate):
                return candidate
    return path


def _make_track(entry: ET.Element) -> Dict[str, Any]:
    location = entry.find("LOCATION")
    volume = location.attrib.get("VOLUME", "") if location is not None else ""
    directory = location.attrib.get("DIR", "") if location is not None else ""
    filename = location.attrib.get("FILE", "") if location is not None else ""
    pkey = f"{volume}{directory}{filename}"
    info = entry.find("INFO")
    musical_key = entry.find("MUSICAL_KEY")
    tempo = entry.find("TEMPO")

    key_value = ""
    if info is not None:
        key_value = str(info.attrib.get("KEY", "") or "")
    musical_value = -1
    if musical_key is not None:
        try:
            musical_value = int(musical_key.attrib.get("VALUE", -1))
        except (TypeError, ValueError):
            musical_value = -1
    bpm = 0.0
    if tempo is not None:
        try:
            bpm = float(tempo.attrib.get("BPM", 0.0) or 0.0)
        except (TypeError, ValueError):
            bpm = 0.0
    duration = 0
    if info is not None:
        try:
            duration = int(float(info.attrib.get("PLAYTIME", 0) or 0))
        except (TypeError, ValueError):
            duration = 0
    return {
        "title": str(entry.attrib.get("TITLE", "") or "").strip(),
        "artist": str(entry.attrib.get("ARTIST", "") or "").strip(),
        "key": key_value,
        "mval": musical_value,
        "bpm": bpm,
        "duration": duration,
        "pkey": pkey,
        "entry_elem": entry,
        "file": _decode_name(filename),
        "basename": os.path.basename(_decode_name(filename)).casefold(),
        "fs_path": traktor_fs_path(volume, directory, filename),
    }


def playlist_name_exists(collection_path: str, playlist_name: str) -> bool:
    """True when collection.nml already has a playlist with this exact name."""
    name = str(playlist_name or "").strip()
    if not name or not collection_path or not os.path.isfile(collection_path):
        return False
    try:
        root = ET.parse(collection_path).getroot()
    except Exception:
        return False
    for node in root.findall(".//NODE"):
        if node.attrib.get("TYPE") == "PLAYLIST" and node.attrib.get("NAME") == name:
            return True
    return False


def metadata_agrees(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    """Artist, title and file name agree when both sides actually have them."""
    for field in ("artist", "title"):
        a = str(left.get(field) or "").strip().casefold()
        b = str(right.get(field) or "").strip().casefold()
        if a and b and a != b:
            return False
    left_base = str(left.get("basename") or os.path.basename(str(left.get("file") or ""))).casefold()
    right_base = str(right.get("basename") or os.path.basename(str(right.get("file") or ""))).casefold()
    if left_base and right_base and left_base != right_base:
        return False
    return True


def _index_tracks(root: ET.Element) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    by_pkey: Dict[str, Dict[str, Any]] = {}
    by_basename: Dict[str, Dict[str, Any]] = {}
    collection = root.find("COLLECTION")
    scope = collection if collection is not None else root
    for entry in scope.findall("ENTRY"):
        track = _make_track(entry)
        if not track["pkey"]:
            continue
        by_pkey[track["pkey"]] = track
        if track["basename"] and track["basename"] not in by_basename:
            by_basename[track["basename"]] = track
    return by_pkey, by_basename


def parse_history_file(history_path: str) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Return the session tracks in play order plus the session lookup tables."""
    tree = ET.parse(history_path)
    root = tree.getroot()
    by_pkey, by_basename = _index_tracks(root)
    ordered: List[Dict[str, Any]] = []
    seen: set = set()
    playlist = root.find(".//PLAYLIST")
    if playlist is not None:
        for entry in playlist.findall("ENTRY"):
            primary = entry.find("PRIMARYKEY")
            if primary is None:
                continue
            key = primary.attrib.get("KEY", "")
            track = by_pkey.get(key)
            if track is None:
                decoded = _decode_name(key)
                basename = os.path.basename(decoded).casefold()
                track = by_basename.get(basename)
                if track is None:
                    track = {
                        "title": os.path.basename(decoded),
                        "artist": "",
                        "key": "",
                        "mval": -1,
                        "bpm": 0.0,
                        "duration": 0,
                        "pkey": key,
                        "entry_elem": None,
                        "file": basename,
                        "basename": basename,
                    }
            if track["pkey"] in seen:
                continue
            seen.add(track["pkey"])
            ordered.append(track)
    return ordered, by_basename


def load_collection_index(collection_path: str) -> Dict[str, Dict[str, Any]]:
    tree = ET.parse(collection_path)
    _, by_basename = _index_tracks(tree.getroot())
    return by_basename


def key_sort_rank(track: Dict[str, Any]) -> Tuple:
    """Key Field Ascending: Camelot/Open Key first, then musical key value."""
    key = str(track.get("key") or "").strip()
    match = re.match(r"^(\d{1,2})([a-zA-Z]?)$", key)
    if match:
        return (0, int(match.group(1)), match.group(2).lower(), track.get("title", ""))
    value = int(track.get("mval", -1) or -1)
    if value >= 0:
        return (1, value, "", track.get("title", ""))
    if key:
        return (2, key.lower(), "", track.get("title", ""))
    return (3, 999, "", track.get("title", ""))


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


# ------------------------------------------------------------------------------
# Collection writing
# ------------------------------------------------------------------------------
def insert_playlist_into_collection(
    collection_path: str,
    playlist_name: str,
    sorted_tracks: Sequence[Dict[str, Any]],
) -> Tuple[bool, str]:
    """Insert or replace a playlist in collection.nml and back up the original."""
    path = Path(collection_path)
    if not path.is_file():
        return False, f"collection.nml not found at {path}"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(path.name + f".bak_{stamp}")
    try:
        shutil.copy2(path, backup)
    except OSError as exc:
        return False, f"Could not back up collection.nml: {exc}"

    try:
        tree = ET.parse(path)
    except Exception as exc:
        return False, f"Could not parse collection.nml: {exc}"
    root = tree.getroot()

    playlists_node = root.find("PLAYLISTS")
    if playlists_node is None:
        playlists_node = ET.SubElement(root, "PLAYLISTS")
    root_folder = None
    for node in playlists_node.findall("NODE"):
        if node.attrib.get("TYPE") == "FOLDER" and node.attrib.get("NAME") == "$ROOT":
            root_folder = node
            break
    if root_folder is None:
        root_folder = ET.SubElement(playlists_node, "NODE", {"TYPE": "FOLDER", "NAME": "$ROOT"})
    subnodes = root_folder.find("SUBNODES")
    if subnodes is None:
        subnodes = ET.SubElement(root_folder, "SUBNODES")

    for node in list(subnodes.findall("NODE")):
        if node.attrib.get("TYPE") == "PLAYLIST" and node.attrib.get("NAME") == playlist_name:
            subnodes.remove(node)

    playlist_node = ET.SubElement(subnodes, "NODE", {"TYPE": "PLAYLIST", "NAME": playlist_name})
    playlist_content = ET.SubElement(
        playlist_node,
        "PLAYLIST",
        {"ENTRIES": str(len(sorted_tracks)), "TYPE": "LIST", "UUID": uuid.uuid4().hex},
    )

    collection_node = root.find("COLLECTION")
    existing_keys: set = set()
    if collection_node is not None:
        for entry in collection_node.findall("ENTRY"):
            location = entry.find("LOCATION")
            if location is not None:
                existing_keys.add(
                    f"{location.attrib.get('VOLUME', '')}"
                    f"{location.attrib.get('DIR', '')}"
                    f"{location.attrib.get('FILE', '')}"
                )

    added_to_collection = 0
    for track in sorted_tracks:
        entry = ET.SubElement(playlist_content, "ENTRY")
        ET.SubElement(entry, "PRIMARYKEY", {"TYPE": "TRACK", "KEY": track["pkey"]})
        if (
            collection_node is not None
            and track["pkey"] not in existing_keys
            and track.get("entry_elem") is not None
        ):
            collection_node.append(copy.deepcopy(track["entry_elem"]))
            existing_keys.add(track["pkey"])
            added_to_collection += 1

    if collection_node is not None:
        collection_node.attrib["ENTRIES"] = str(len(collection_node.findall("ENTRY")))
    if subnodes is not None:
        subnodes.attrib["COUNT"] = str(len(subnodes.findall("NODE")))

    temp = path.with_name(path.name + ".tmp")
    try:
        tree.write(temp, encoding="utf-8", xml_declaration=True)
        temp.replace(path)
    except Exception as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            shutil.copy2(backup, path)
        except OSError:
            pass
        return False, f"Could not write collection.nml: {exc}"

    return True, (
        f"Playlist '{playlist_name}' written with {len(sorted_tracks)} tracks "
        f"({added_to_collection} added to the collection). Backup: {backup.name}"
    )


# ------------------------------------------------------------------------------
# Artist playlists, folder tree, rename and remove
# ------------------------------------------------------------------------------
# Folder and playlist paths use this separator so a name that itself contains
# "/" still round-trips. The menu prints it as " / ".
PLAYLIST_PATH_SEP = "\x1f"


def _clean_label(value: str) -> str:
    return " ".join(str(value or "").replace("\t", " ").replace("\r", " ").replace("\n", " ").split())


def _join_playlist_path(parent: str, name: str) -> str:
    parent = str(parent or "")
    name = str(name or "")
    return f"{parent}{PLAYLIST_PATH_SEP}{name}" if parent else name


def display_playlist_path(path: str) -> str:
    return str(path or "").replace(PLAYLIST_PATH_SEP, " / ")


def _collection_target(
    collection_path: Optional[str], cfg: Optional[Dict[str, str]]
) -> Optional[Path]:
    if collection_path:
        path = Path(os.path.expanduser(str(collection_path)))
        return path if path.is_file() else None
    return find_collection_path(cfg)


def _same_file(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return str(left) == str(right)


def _blocked_by_running_traktor(
    path: Path, cfg: Optional[Dict[str, str]], close_traktor: bool
) -> Optional[str]:
    """Refuse to rewrite the live collection while Traktor still has it open."""
    live = find_collection_path(cfg)
    if live is None or not _same_file(path, live) or not is_traktor_running():
        return None
    if close_traktor and quit_traktor():
        return None
    if close_traktor:
        return "Could not close Traktor. Close it manually and try again."
    return (
        "Traktor is running. Close Traktor cleanly so collection.nml is not "
        "overwritten, then try again."
    )


def _save_nml(path: Path, tree: ET.ElementTree) -> Tuple[bool, str]:
    """Back up collection.nml, write the tree, and restore the backup on failure.

    This is the same backup-then-replace sequence insert_playlist_into_collection
    uses, so a failed rename or remove cannot leave a half-written collection.
    """
    if not path.is_file():
        return False, f"collection.nml not found at {path}"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(path.name + f".bak_{stamp}")
    suffix = 2
    while backup.exists():
        backup = path.with_name(path.name + f".bak_{stamp}_{suffix}")
        suffix += 1
    try:
        shutil.copy2(path, backup)
    except OSError as exc:
        return False, f"Could not back up collection.nml: {exc}"
    temp = path.with_name(path.name + ".tmp")
    try:
        tree.write(temp, encoding="utf-8", xml_declaration=True)
        temp.replace(path)
    except Exception as exc:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass
        try:
            shutil.copy2(backup, path)
        except OSError:
            pass
        return False, f"Could not write collection.nml: {exc}"
    return True, backup.name


def _refresh_subnode_count(parent: Optional[ET.Element]) -> None:
    if parent is None:
        return
    if parent.tag == "SUBNODES" or "COUNT" in parent.attrib:
        parent.attrib["COUNT"] = str(len(list(parent.findall("NODE"))))


def _parse_playlist_index(
    root: ET.Element,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Folders and playlists, including playlists nested inside folders.

    Each playlist row keeps the XML node so rename and remove can edit it.
    ``path`` is unique. ``folder`` is empty for playlists that sit in $ROOT.
    """
    folders: List[Dict[str, Any]] = []
    playlists: List[Dict[str, Any]] = []

    def add_playlist(node: ET.Element, parent_path: str, parent: ET.Element) -> None:
        name = str(node.attrib.get("NAME", "") or "").strip() or "(untitled)"
        content = node.find("PLAYLIST")
        entries = content.findall("ENTRY") if content is not None else []
        playlists.append(
            {
                "path": _join_playlist_path(parent_path, name),
                "name": name,
                "folder": parent_path,
                "entries": len(entries),
                "uuid": content.attrib.get("UUID", "") if content is not None else "",
                "node": node,
                "parent": parent,
            }
        )

    def walk(subnodes: Optional[ET.Element], parent_path: str) -> None:
        if subnodes is None:
            return
        for child in list(subnodes.findall("NODE")):
            kind = child.attrib.get("TYPE", "")
            name = str(child.attrib.get("NAME", "") or "").strip()
            if kind == "PLAYLIST":
                add_playlist(child, parent_path, subnodes)
                continue
            if kind != "FOLDER":
                continue
            if name == "$ROOT":
                walk(child.find("SUBNODES"), parent_path)
                continue
            name = name or "(untitled folder)"
            path = _join_playlist_path(parent_path, name)
            nested = child.find("SUBNODES")
            kids = list(nested.findall("NODE")) if nested is not None else []
            folders.append(
                {
                    "path": path,
                    "name": name,
                    "parent": parent_path,
                    "playlists": sum(1 for kid in kids if kid.attrib.get("TYPE") == "PLAYLIST"),
                    "folders": sum(
                        1
                        for kid in kids
                        if kid.attrib.get("TYPE") == "FOLDER" and kid.attrib.get("NAME") != "$ROOT"
                    ),
                }
            )
            walk(nested, path)

    playlists_node = root.find("PLAYLISTS")
    if playlists_node is None:
        return folders, playlists
    for top in list(playlists_node.findall("NODE")):
        kind = top.attrib.get("TYPE", "")
        name = str(top.attrib.get("NAME", "") or "").strip()
        if kind == "FOLDER" and name == "$ROOT":
            walk(top.find("SUBNODES"), "")
        elif kind == "FOLDER":
            # A folder that is not $ROOT still belongs in the tree. walk()
            # records children only, so the top-level folder is recorded here.
            label = name or "(untitled folder)"
            nested = top.find("SUBNODES")
            kids = list(nested.findall("NODE")) if nested is not None else []
            folders.append(
                {
                    "path": label,
                    "name": label,
                    "parent": "",
                    "playlists": sum(1 for kid in kids if kid.attrib.get("TYPE") == "PLAYLIST"),
                    "folders": sum(
                        1
                        for kid in kids
                        if kid.attrib.get("TYPE") == "FOLDER" and kid.attrib.get("NAME") != "$ROOT"
                    ),
                }
            )
            walk(nested, label)
        elif kind == "PLAYLIST":
            add_playlist(top, "", playlists_node)
    seen: Dict[str, int] = {}
    for row in playlists:
        base = str(row["path"])
        seen[base] = seen.get(base, 0) + 1
        if seen[base] > 1:
            row["path"] = f"{base} #{seen[base]}"
    return folders, playlists


def _public_tree(
    folders: Sequence[Dict[str, Any]], playlists: Sequence[Dict[str, Any]]
) -> Dict[str, Any]:
    return {
        "folders": [
            {
                "path": row["path"],
                "name": row["name"],
                "parent": row["parent"],
                "playlists": row["playlists"],
                "folders": row["folders"],
                "display": display_playlist_path(str(row["path"])),
            }
            for row in folders
        ],
        "playlists": [
            {
                "path": row["path"],
                "name": row["name"],
                "folder": row["folder"],
                "entries": row["entries"],
                "uuid": row["uuid"],
                "display": display_playlist_path(str(row["path"])),
            }
            for row in playlists
        ],
    }


def _tsv_field(value: Any) -> str:
    """One TSV column. The folder separator inside a path is kept."""
    text = str(value or "")
    return text.replace("\t", " ").replace("\r", " ").replace("\n", " ")


def format_playlist_tree_tsv(
    folders: Sequence[Dict[str, Any]], playlists: Sequence[Dict[str, Any]]
) -> str:
    """Tab rows the Tracklist menu reads. Names never contain a tab."""
    lines: List[str] = []
    for row in folders:
        lines.append(
            "\t".join(
                [
                    "FOLDER",
                    _tsv_field(row["path"]),
                    _clean_label(row["name"]),
                    _tsv_field(row["parent"]),
                    str(row["playlists"]),
                    str(row["folders"]),
                ]
            )
        )
    for row in playlists:
        lines.append(
            "\t".join(
                [
                    "PLAYLIST",
                    _tsv_field(row["path"]),
                    _clean_label(row["name"]),
                    _tsv_field(row["folder"]),
                    str(row["entries"]),
                    "",
                ]
            )
        )
    return "\n".join(lines)


def load_playlist_tree(collection_path: str) -> Dict[str, Any]:
    root = ET.parse(collection_path).getroot()
    folders, playlists = _parse_playlist_index(root)
    return _public_tree(folders, playlists)


def _artist_name_pattern(artist_name: str) -> Optional[re.Pattern]:
    """Match the artist name as a whole phrase inside a cleaned field."""
    wanted = _clean_label(artist_name).casefold()
    if not wanted:
        return None
    return re.compile(r"(?<![\w])" + re.escape(wanted) + r"(?![\w])")


def _track_sort_key(track: Dict[str, Any]) -> Tuple:
    return (
        _clean_label(str(track.get("title") or "")).casefold(),
        _clean_label(str(track.get("file") or "")).casefold(),
        str(track.get("pkey") or ""),
    )


def tracks_for_artist(collection_path: str, artist_name: str) -> List[Dict[str, Any]]:
    """Tracks for this artist from the artist field, then from the title.

    Each pass keeps a track when the field contains the artist name as a
    whole phrase, ignoring case and repeated spaces. That includes an exact
    artist, a collaboration such as "Airbase feat. Someone" or
    "Eloquentia & Airbase", and a title credit such as
    "Horizon (Airbase Remix)". A longer word that merely starts with the
    name, such as "Airbaseball", is left out. A track that matches both
    fields is listed twice. create_artist_playlist removes that duplicate
    from the playlist after the playlist has been written.
    """
    wanted = _clean_label(artist_name).casefold()
    if not wanted:
        return []
    name_pattern = _artist_name_pattern(artist_name)
    by_pkey, _ = _index_tracks(ET.parse(collection_path).getroot())
    from_artist: List[Dict[str, Any]] = []
    from_title: List[Dict[str, Any]] = []
    for track in by_pkey.values():
        artist_text = _clean_label(str(track.get("artist") or "")).casefold()
        title = _clean_label(str(track.get("title") or "")).casefold()
        if name_pattern is not None and artist_text and name_pattern.search(artist_text):
            from_artist.append(track)
        if name_pattern is not None and title and name_pattern.search(title):
            from_title.append(track)
    matched = from_artist + from_title
    matched.sort(key=_track_sort_key)
    return matched


def _playlist_node_by_name(root: ET.Element, playlist_name: str) -> Optional[ET.Element]:
    """The first playlist whose name matches, ignoring case and repeated spaces."""
    wanted = _clean_label(playlist_name).casefold()
    if not wanted:
        return None
    for candidate in root.findall(".//NODE"):
        if candidate.attrib.get("TYPE") != "PLAYLIST":
            continue
        if _clean_label(str(candidate.attrib.get("NAME") or "")).casefold() == wanted:
            return candidate
    return None


def _playlist_primary_keys(content: Optional[ET.Element]) -> List[str]:
    if content is None:
        return []
    keys: List[str] = []
    for entry in content.findall("ENTRY"):
        primary = entry.find("PRIMARYKEY")
        keys.append(str(primary.attrib.get("KEY", "") if primary is not None else "").strip())
    return keys


def add_missing_tracks_to_playlist(
    collection_path: str,
    playlist_name: str,
    tracks: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Append tracks whose PRIMARYKEY is not already in this playlist.

    Existing rows stay in place. The file is rewritten only when a track is added.
    """
    name = _clean_label(playlist_name)
    path = Path(collection_path)
    if not name:
        return {"ok": False, "added": 0, "kept": 0, "message": "A playlist name is required."}
    try:
        tree = ET.parse(path)
    except Exception as exc:
        return {"ok": False, "added": 0, "kept": 0, "message": f"Could not read collection.nml: {exc}"}
    node = _playlist_node_by_name(tree.getroot(), name)
    if node is None:
        return {"ok": False, "added": 0, "kept": 0, "message": f"Playlist '{name}' was not found."}
    content = node.find("PLAYLIST")
    if content is None:
        content = ET.SubElement(
            node,
            "PLAYLIST",
            {"ENTRIES": "0", "TYPE": "LIST", "UUID": uuid.uuid4().hex},
        )
    existing = {key for key in _playlist_primary_keys(content) if key}
    added = 0
    for track in tracks:
        key = str(track.get("pkey") or "").strip()
        if not key or key in existing:
            continue
        entry = ET.SubElement(content, "ENTRY")
        ET.SubElement(entry, "PRIMARYKEY", {"TYPE": "TRACK", "KEY": key})
        existing.add(key)
        added += 1
    kept = len(list(content.findall("ENTRY")))
    if added == 0:
        return {
            "ok": True,
            "added": 0,
            "kept": kept,
            "message": f"Playlist '{name}' already contains every matching track.",
        }
    content.attrib["ENTRIES"] = str(kept)
    saved, detail = _save_nml(path, tree)
    if not saved:
        return {"ok": False, "added": 0, "kept": kept, "message": detail}
    return {
        "ok": True,
        "added": added,
        "kept": kept,
        "backup": detail,
        "message": f"Added {added} new track(s) to playlist '{name}'. {kept} tracks now. Backup: {detail}",
    }


def remove_duplicate_playlist_entries(collection_path: str, playlist_name: str) -> Dict[str, Any]:
    """Remove later copies of the same track from a playlist that was just written.

    Identity is the Traktor PRIMARYKEY. The first row stays. The collection
    file is rewritten only when at least one duplicate is removed.
    """
    name = _clean_label(playlist_name)
    path = Path(collection_path)
    if not name:
        return {"ok": False, "removed": 0, "kept": 0, "message": "A playlist name is required."}
    try:
        tree = ET.parse(path)
    except Exception as exc:
        return {"ok": False, "removed": 0, "kept": 0, "message": f"Could not read collection.nml: {exc}"}
    node = None
    for candidate in tree.getroot().findall(".//NODE"):
        if candidate.attrib.get("TYPE") == "PLAYLIST" and candidate.attrib.get("NAME") == name:
            node = candidate
            break
    if node is None:
        return {"ok": False, "removed": 0, "kept": 0, "message": f"Playlist '{name}' was not found."}
    content = node.find("PLAYLIST")
    if content is None:
        return {"ok": True, "removed": 0, "kept": 0, "message": f"Playlist '{name}' has no entries."}
    seen: set = set()
    removed = 0
    for entry in list(content.findall("ENTRY")):
        primary = entry.find("PRIMARYKEY")
        key = str(primary.attrib.get("KEY", "") if primary is not None else "").strip()
        if not key or key not in seen:
            if key:
                seen.add(key)
            continue
        content.remove(entry)
        removed += 1
    kept = len(list(content.findall("ENTRY")))
    if removed == 0:
        return {
            "ok": True,
            "removed": 0,
            "kept": kept,
            "message": f"Playlist '{name}' has no duplicate tracks.",
        }
    content.attrib["ENTRIES"] = str(kept)
    saved, detail = _save_nml(path, tree)
    if not saved:
        return {"ok": False, "removed": 0, "kept": kept, "message": detail}
    return {
        "ok": True,
        "removed": removed,
        "kept": kept,
        "backup": detail,
        "message": (
            f"Removed {removed} duplicate track(s) from playlist '{name}'. "
            f"{kept} tracks remain. Backup: {detail}"
        ),
    }


def suggest_artists(
    collection_path: str, artist_name: str, limit: int = 8
) -> List[Dict[str, Any]]:
    """Nearby artist names when an exact search finds nothing."""
    query = _clean_label(artist_name).casefold()
    if len(query) < 2 or not os.path.isfile(collection_path):
        return []
    by_pkey, _ = _index_tracks(ET.parse(collection_path).getroot())
    counts: Dict[str, Dict[str, Any]] = {}
    for track in by_pkey.values():
        raw = _clean_label(str(track.get("artist") or ""))
        if not raw:
            continue
        key = raw.casefold()
        if key == query:
            continue
        contained = query in key or (len(key) >= 3 and key in query)
        if not contained:
            continue
        row = counts.setdefault(key, {"artist": raw, "tracks": 0})
        row["tracks"] += 1
    rows = list(counts.values())
    rows.sort(
        key=lambda row: (
            0 if str(row["artist"]).casefold().startswith(query) else 1,
            -int(row["tracks"]),
            str(row["artist"]).casefold(),
        )
    )
    return rows[: max(0, int(limit))]


def _playlist_name_taken(collection_path: str, playlist_name: str, except_path: str = "") -> bool:
    """True when some other playlist already uses this name, ignoring case."""
    wanted = _clean_label(playlist_name).casefold()
    if not wanted:
        return False
    if except_path == "" and playlist_name_exists(collection_path, _clean_label(playlist_name)):
        return True
    try:
        _, playlists = _parse_playlist_index(ET.parse(collection_path).getroot())
    except Exception:
        return False
    for row in playlists:
        if except_path and row["path"] == except_path:
            continue
        if _clean_label(str(row["name"])).casefold() == wanted:
            return True
    return False


def _prepare_collection_write(
    collection_path: Optional[str],
    cfg: Optional[Dict[str, str]],
    close_traktor: bool,
) -> Tuple[Optional[Path], Optional[Dict[str, Any]]]:
    cfg = cfg or load_config()
    path = _collection_target(collection_path, cfg)
    if path is None:
        return None, {
            "ok": False,
            "reason": "collection",
            "message": (
                "collection.nml was not found. Set TRAKTOR_COLLECTION_PATH in "
                "config.env, or open Traktor once so the collection is created."
            ),
        }
    blocked = _blocked_by_running_traktor(path, cfg, close_traktor)
    if blocked:
        return None, {
            "ok": False,
            "reason": "running",
            "message": blocked,
            "collection": str(path),
        }
    return path, None


def create_artist_playlist(
    artist_name: str,
    playlist_name: str,
    collection_path: Optional[str] = None,
    cfg: Optional[Dict[str, str]] = None,
    close_traktor: bool = False,
) -> Dict[str, Any]:
    """Scan the collection and write one playlist for this artist.

    Tracks come from an artist field that contains the name, then from titles
    that contain the name. A new playlist is inserted with
    insert_playlist_into_collection, under the same $ROOT folder the history
    generator uses. When that playlist name already exists, tracks already in
    it stay, and only missing matches are added. Duplicate tracks are then
    removed from that playlist.
    """
    artist = _clean_label(artist_name)
    playlist = _clean_label(playlist_name)
    if not artist:
        return {"ok": False, "reason": "artist", "message": "An artist name is required."}
    if not playlist or playlist == "$ROOT":
        return {"ok": False, "reason": "name", "message": "A playlist name is required."}
    if len(artist) > 200 or len(playlist) > 200:
        return {
            "ok": False,
            "reason": "name",
            "message": "Artist and playlist names must be 200 characters or fewer.",
        }
    path, blocked = _prepare_collection_write(collection_path, cfg, close_traktor)
    if blocked is not None or path is None:
        return blocked or {"ok": False, "reason": "collection", "message": "collection.nml was not found."}
    exists = _playlist_name_taken(str(path), playlist)
    try:
        matched = tracks_for_artist(str(path), artist)
    except Exception as exc:
        return {"ok": False, "reason": "parse", "message": f"Could not read collection.nml: {exc}"}
    if not matched:
        suggestions = []
        try:
            suggestions = suggest_artists(str(path), artist)
        except Exception:
            suggestions = []
        if exists:
            return {
                "ok": True,
                "reason": "unchanged",
                "message": (
                    f"Playlist '{playlist}' was left as it is. "
                    f"No tracks in the Traktor collection use the artist name '{artist}'."
                ),
                "artist": artist,
                "playlist": playlist,
                "matched": 0,
                "added": 0,
                "removed_duplicates": 0,
                "suggestions": suggestions,
                "collection": str(path),
            }
        return {
            "ok": False,
            "reason": "no_match",
            "message": f"No tracks in the Traktor collection use the artist name '{artist}'.",
            "artist": artist,
            "playlist": playlist,
            "matched": 0,
            "added": 0,
            "suggestions": suggestions,
            "collection": str(path),
        }
    if exists:
        added_result = add_missing_tracks_to_playlist(str(path), playlist, matched)
        if not added_result.get("ok"):
            return {
                "ok": False,
                "reason": "write",
                "message": str(added_result.get("message") or "Could not update the playlist."),
                "artist": artist,
                "playlist": playlist,
                "matched": 0,
                "added": 0,
                "removed_duplicates": 0,
                "suggestions": [],
                "collection": str(path),
            }
        added = int(added_result.get("added") or 0)
        kept = int(added_result.get("kept") or 0)
        if added == 0:
            return {
                "ok": True,
                "reason": "unchanged",
                "message": (
                    f"Playlist '{playlist}' already contains every matching track. "
                    f"{kept} tracks."
                ),
                "artist": artist,
                "playlist": playlist,
                "matched": kept,
                "added": 0,
                "removed_duplicates": 0,
                "suggestions": [],
                "collection": str(path),
            }
        message = (
            f"Added {added} new track(s) to '{playlist}'. "
            f"Artist filter: {artist} (artist field or title contains the name)."
        )
    else:
        ok, message = insert_playlist_into_collection(str(path), playlist, matched)
        if not ok:
            return {
                "ok": False,
                "reason": "write",
                "message": message,
                "artist": artist,
                "playlist": playlist,
                "matched": 0,
                "added": 0,
                "removed_duplicates": 0,
                "suggestions": [],
                "collection": str(path),
            }
        added = 0
        kept = 0
        message = (
            f"{message} Artist filter: {artist} "
            "(artist field or title contains the name), sorted by title."
        )
    deduped = remove_duplicate_playlist_entries(str(path), playlist)
    if not deduped.get("ok"):
        return {
            "ok": False,
            "reason": "write",
            "message": (
                f"{message} The playlist was written, but duplicate tracks "
                f"could not be removed: {deduped.get('message')}"
            ),
            "artist": artist,
            "playlist": playlist,
            "matched": 0,
            "added": added,
            "removed_duplicates": 0,
            "suggestions": [],
            "collection": str(path),
        }
    removed = int(deduped.get("removed") or 0)
    kept = int(deduped.get("kept") or 0)
    if exists:
        message = (
            f"Added {added} new track(s) to '{playlist}'. Playlist now has {kept} tracks. "
            f"Artist filter: {artist} (artist field or title contains the name)."
        )
    if removed:
        message += f" Removed {removed} duplicate track(s) from '{playlist}'."
    if not exists:
        added = kept
    return {
        "ok": True,
        "reason": "updated" if exists else "written",
        "message": message,
        "artist": artist,
        "playlist": playlist,
        "matched": kept,
        "added": added,
        "removed_duplicates": removed,
        "suggestions": [],
        "collection": str(path),
    }


def _find_playlist_row(
    playlists: Sequence[Dict[str, Any]], playlist_path: str
) -> Optional[Dict[str, Any]]:
    target = str(playlist_path or "")
    for row in playlists:
        if row["path"] == target:
            return row
    displayed = target.replace(" / ", PLAYLIST_PATH_SEP)
    for row in playlists:
        if row["path"] == displayed or display_playlist_path(str(row["path"])) == target:
            return row
    return None


def rename_playlist(
    playlist_path: str,
    new_name: str,
    collection_path: Optional[str] = None,
    cfg: Optional[Dict[str, str]] = None,
    close_traktor: bool = False,
) -> Dict[str, Any]:
    """Rename one playlist node. Audio files in the collection are not touched."""
    updated = _clean_label(new_name)
    if not updated or updated == "$ROOT":
        return {"ok": False, "reason": "name", "message": "A new playlist name is required."}
    if len(updated) > 200:
        return {
            "ok": False,
            "reason": "name",
            "message": "Playlist names must be 200 characters or fewer.",
        }
    path, blocked = _prepare_collection_write(collection_path, cfg, close_traktor)
    if blocked is not None or path is None:
        return blocked or {"ok": False, "reason": "collection", "message": "collection.nml was not found."}
    try:
        tree = ET.parse(str(path))
        _, playlists = _parse_playlist_index(tree.getroot())
    except Exception as exc:
        return {"ok": False, "reason": "parse", "message": f"Could not read collection.nml: {exc}"}
    row = _find_playlist_row(playlists, playlist_path)
    if row is None:
        return {
            "ok": False,
            "reason": "missing",
            "message": f"Playlist '{display_playlist_path(playlist_path)}' was not found.",
            "collection": str(path),
        }
    if _clean_label(str(row["name"])) == updated:
        return {
            "ok": True,
            "reason": "unchanged",
            "message": f"Playlist '{updated}' already has that name.",
            "playlist": updated,
            "path": row["path"],
            "collection": str(path),
        }
    if _playlist_name_taken(str(path), updated, except_path=str(row["path"])):
        return {
            "ok": False,
            "reason": "exists",
            "message": f"A playlist named '{updated}' already exists.",
            "collection": str(path),
        }
    row["node"].attrib["NAME"] = updated
    ok, detail = _save_nml(path, tree)
    if not ok:
        return {"ok": False, "reason": "write", "message": detail, "collection": str(path)}
    return {
        "ok": True,
        "reason": "renamed",
        "message": (
            f"Renamed '{row['name']}' to '{updated}'. Backup: {detail}"
        ),
        "playlist": updated,
        "previous": row["name"],
        "collection": str(path),
    }


def remove_playlist(
    playlist_path: str,
    collection_path: Optional[str] = None,
    cfg: Optional[Dict[str, str]] = None,
    close_traktor: bool = False,
) -> Dict[str, Any]:
    """Remove one playlist node. Tracks stay in the Traktor collection."""
    path, blocked = _prepare_collection_write(collection_path, cfg, close_traktor)
    if blocked is not None or path is None:
        return blocked or {"ok": False, "reason": "collection", "message": "collection.nml was not found."}
    try:
        tree = ET.parse(str(path))
        _, playlists = _parse_playlist_index(tree.getroot())
    except Exception as exc:
        return {"ok": False, "reason": "parse", "message": f"Could not read collection.nml: {exc}"}
    row = _find_playlist_row(playlists, playlist_path)
    if row is None:
        return {
            "ok": False,
            "reason": "missing",
            "message": f"Playlist '{display_playlist_path(playlist_path)}' was not found.",
            "collection": str(path),
        }
    parent = row.get("parent")
    if isinstance(parent, ET.Element):
        parent.remove(row["node"])
        _refresh_subnode_count(parent)
    ok, detail = _save_nml(path, tree)
    if not ok:
        return {"ok": False, "reason": "write", "message": detail, "collection": str(path)}
    return {
        "ok": True,
        "reason": "removed",
        "message": f"Removed playlist '{row['name']}'. The audio files are still in the collection. Backup: {detail}",
        "playlist": row["name"],
        "collection": str(path),
    }


def playlist_entries(collection_path: str, playlist_path: str) -> Dict[str, Any]:
    """Tracks inside one playlist, resolved against the collection when possible."""
    tree = ET.parse(collection_path)
    root = tree.getroot()
    by_pkey, _ = _index_tracks(root)
    _, playlists = _parse_playlist_index(root)
    row = _find_playlist_row(playlists, playlist_path)
    if row is None:
        return {
            "ok": False,
            "reason": "missing",
            "message": f"Playlist '{display_playlist_path(playlist_path)}' was not found.",
        }
    content = row["node"].find("PLAYLIST")
    tracks: List[Dict[str, Any]] = []
    if content is not None:
        for entry in content.findall("ENTRY"):
            primary = entry.find("PRIMARYKEY")
            key = primary.attrib.get("KEY", "") if primary is not None else ""
            known = by_pkey.get(key)
            if known is not None:
                tracks.append(
                    {
                        "artist": known.get("artist") or "",
                        "title": known.get("title") or "",
                        "file": known.get("file") or "",
                        "duration": known.get("duration") or 0,
                    }
                )
            else:
                decoded = _decode_name(key)
                tracks.append(
                    {
                        "artist": "",
                        "title": os.path.basename(decoded) or decoded or "(missing track)",
                        "file": decoded,
                        "duration": 0,
                    }
                )
    return {
        "ok": True,
        "reason": "listed",
        "name": row["name"],
        "path": row["path"],
        "folder": display_playlist_path(str(row["folder"])) or "Root",
        "message": f"{row['name']}: {len(tracks)} tracks",
        "tracks": tracks,
    }


def format_playlist_entries(details: Dict[str, Any], limit: int = 60) -> str:
    tracks = list(details.get("tracks") or [])
    title = str(details.get("name") or "Playlist")
    folder = str(details.get("folder") or "Root")
    lines = [
        f"{title}",
        f"Folder: {folder}",
        f"Tracks: {len(tracks)}",
        f"{'-' * 72}",
    ]
    shown = tracks[: max(0, int(limit))]
    for index, track in enumerate(shown, 1):
        artist = _clean_label(str(track.get("artist") or "")) or "-"
        name = _clean_label(str(track.get("title") or "")) or "-"
        lines.append(
            f"{index:4d}  {format_duration(int(track.get('duration') or 0))}  "
            f"{artist[:28]:<28}  {name[:36]}"
        )
    hidden = len(tracks) - len(shown)
    if hidden > 0:
        lines.append(f"  ... {hidden} more tracks")
    lines.append(f"{'-' * 72}")
    return "\n".join(lines)


def collection_status(cfg: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    cfg = cfg or load_config()
    path = find_collection_path(cfg)
    return {
        "ok": path is not None,
        "path": str(path) if path else "",
        "running": is_traktor_running(),
    }


def _emit_result(result: Dict[str, Any], as_json: bool) -> int:
    if as_json:
        clean: Dict[str, Any] = {}
        for key, value in result.items():
            if key in ("node", "parent", "entry_elem"):
                continue
            try:
                json.dumps(value)
            except TypeError:
                continue
            clean[key] = value
        print(json.dumps(clean))
    else:
        print(result.get("message", ""))
        suggestions = result.get("suggestions") or []
        if suggestions and not result.get("ok"):
            print("Close artist names in the collection:")
            for row in suggestions:
                print(f"  {row.get('artist')} ({row.get('tracks')} tracks)")
    return 0 if result.get("ok") else 1


def _dispatch_artist_tools(args: argparse.Namespace) -> Optional[int]:
    """Handle artist-playlist and folder commands. None lets the history tool run."""
    wanted = any(
        [
            bool(getattr(args, "collection_status", False)),
            bool(getattr(args, "list_playlist_tree", False)),
            getattr(args, "suggest_artists", None) is not None,
            getattr(args, "show_playlist", None) is not None,
            getattr(args, "artist", None) is not None,
            getattr(args, "rename_playlist", None) is not None,
            getattr(args, "remove_playlist", None) is not None,
        ]
    )
    if not wanted:
        return None
    cfg = load_config()
    if args.collection_status:
        status = collection_status(cfg)
        if args.json:
            print(json.dumps(status))
        else:
            print(f"PATH: {status['path']}")
            print(f"RUNNING: {'yes' if status['running'] else 'no'}")
        return 0 if status["ok"] else 1

    path = _collection_target(args.collection, cfg)
    if path is None:
        message = "collection.nml was not found."
        if args.json:
            print(json.dumps({"ok": False, "reason": "collection", "message": message}))
        else:
            print(message, file=sys.stderr)
        return 1

    if args.list_playlist_tree:
        try:
            tree = ET.parse(str(path))
            folders, playlists = _parse_playlist_index(tree.getroot())
        except Exception as exc:
            print(f"Could not read collection.nml: {exc}", file=sys.stderr)
            return 1
        if args.tsv:
            text = format_playlist_tree_tsv(folders, playlists)
            if text:
                print(text)
            return 0
        public = _public_tree(folders, playlists)
        if args.json:
            print(json.dumps(public))
        else:
            print(f"{len(public['folders'])} playlist folders, {len(public['playlists'])} playlists")
            for folder in public["folders"]:
                print(
                    f"  [Folder] {folder['display']} "
                    f"({folder['playlists']} playlists, {folder['folders']} folders)"
                )
            for playlist in public["playlists"]:
                where = display_playlist_path(str(playlist["folder"])) or "Root"
                print(f"  [Playlist] {playlist['name']} ({playlist['entries']} tracks, {where})")
        return 0

    if args.suggest_artists is not None:
        try:
            rows = suggest_artists(str(path), args.suggest_artists)
        except Exception as exc:
            print(f"Could not read collection.nml: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(rows))
        elif args.tsv:
            for row in rows:
                artist = _clean_label(str(row.get("artist") or ""))
                if artist:
                    print(f"{artist}\t{int(row.get('tracks') or 0)}")
        else:
            for row in rows:
                print(f"{row.get('artist')} ({row.get('tracks')} tracks)")
        return 0

    if args.show_playlist is not None:
        try:
            details = playlist_entries(str(path), args.show_playlist)
        except Exception as exc:
            print(f"Could not read collection.nml: {exc}", file=sys.stderr)
            return 1
        if args.json:
            return _emit_result(details, True)
        if not details.get("ok"):
            print(details.get("message", "Playlist was not found."), file=sys.stderr)
            return 1
        print(format_playlist_entries(details))
        return 0

    if args.artist is not None:
        result = create_artist_playlist(
            args.artist,
            args.playlist_name or "",
            collection_path=str(path),
            cfg=cfg,
            close_traktor=bool(args.close_traktor),
        )
        return _emit_result(result, bool(args.json))

    if args.rename_playlist is not None:
        result = rename_playlist(
            args.rename_playlist,
            args.playlist_name or "",
            collection_path=str(path),
            cfg=cfg,
            close_traktor=bool(args.close_traktor),
        )
        return _emit_result(result, bool(args.json))

    if args.remove_playlist is not None:
        result = remove_playlist(
            args.remove_playlist,
            collection_path=str(path),
            cfg=cfg,
            close_traktor=bool(args.close_traktor),
        )
        return _emit_result(result, bool(args.json))
    return None


def build_traktor_playlist(
    favorite_paths: Sequence[str],
    history_id: Optional[str] = None,
    playlist_name: Optional[str] = None,
    collection_path: Optional[str] = None,
    history_dirs: Optional[Iterable[str]] = None,
    cfg: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Create a Traktor playlist for the files that exist in Traktor.

    The history file is the session the user prepared before exporting, so its
    key/BPM metadata wins. Favorites that are not in that session are looked up
    in the full collection instead. Returns a result dictionary for CLI/UI use.
    """
    cfg = cfg or load_config()
    collection = Path(collection_path) if collection_path else find_collection_path(cfg)
    if not collection or not collection.is_file():
        return {
            "ok": False,
            "reason": "collection",
            "message": "collection.nml was not found. Run Traktor once and choose the collection path first.",
        }
    history = resolve_history_file(
        history_id,
        history_dirs=history_dirs or preferred_history_dirs(collection, cfg),
        cfg=cfg,
    )
    if not history or not history.is_file():
        return {
            "ok": False,
            "reason": "history",
            "message": "No Traktor History (.nml) files are available. Sync your Traktor history first.",
        }
    if is_traktor_running():
        return {
            "ok": False,
            "reason": "running",
            "message": "Traktor is running. Close Traktor cleanly, then create the playlist.",
        }

    try:
        _, history_index = parse_history_file(str(history))
        collection_index = load_collection_index(str(collection))
    except Exception as exc:
        return {"ok": False, "reason": "parse", "message": f"Could not parse Traktor data: {exc}"}

    matched: List[Dict[str, Any]] = []
    missing: List[str] = []
    seen: set = set()
    for file_path in favorite_paths:
        basename = os.path.basename(str(file_path)).casefold()
        stem = os.path.splitext(os.path.basename(str(file_path)))[0]
        artist, title = "", ""
        parts = re.match(r"^(.+?)\s+-\s+(.+)$", stem)
        if parts:
            artist, title = parts.group(1).strip(), parts.group(2).strip()
        wanted = {"artist": artist, "title": title, "basename": basename, "file": basename}
        track = history_index.get(basename) or collection_index.get(basename)
        if track is None or not metadata_agrees(wanted, track):
            track = None
            for candidate in collection_index.values():
                if metadata_agrees(wanted, candidate):
                    track = candidate
                    break
        if not track:
            missing.append(os.path.basename(str(file_path)))
            continue
        if track["pkey"] in seen:
            continue
        seen.add(track["pkey"])
        matched.append(track)

    if not matched:
        return {
            "ok": False,
            "reason": "no_match",
            "message": (
                f"None of the favorites were found in {history.name} or the Traktor collection. "
                "Choose a history file from the session where those tracks were imported."
            ),
            "missing": missing,
        }

    matched.sort(key=key_sort_rank)
    name = (playlist_name or "").strip() or f"MP Favorites {time.strftime('%Y-%m-%d')}"
    ok, message = insert_playlist_into_collection(str(collection), name, matched)
    return {
        "ok": ok,
        "reason": "written" if ok else "write",
        "message": message,
        "playlist": name,
        "history": history.name,
        "collection": str(collection),
        "matched": len(matched),
        "missing": missing,
        "tracks": matched,
    }


# ------------------------------------------------------------------------------
# Interactive generator (Manager menu option 17 > 3)
# ------------------------------------------------------------------------------
def _print_session(tracks: Sequence[Dict[str, Any]]) -> None:
    print(f" {BOLD}{'#':<4}{'KEY':<6}{'BPM':<8}{'TIME':<8}{'ARTIST':<30}{'TITLE':<40}{NC}")
    print(f"{BLUE}{'-' * 96}{NC}")
    for index, track in enumerate(tracks, 1):
        key = track.get("key") or (f"K{track['mval']}" if track.get("mval", -1) >= 0 else "-")
        bpm = f"{track['bpm']:.1f}" if track.get("bpm", 0) > 0 else "-"
        print(
            f" {index:<4}{key:<6}{bpm:<8}{format_duration(track.get('duration', 0)):<8}"
            f"{(track.get('artist') or '-')[:28]:<30}{(track.get('title') or '-')[:38]:<40}"
        )
    print(f"{BLUE}{'-' * 96}{NC}")


def interactive_main() -> int:
    cfg = load_config()
    collection = find_collection_path(cfg)
    history_dirs = find_history_dirs(cfg)
    history_files = list_history_files(30, [str(d) for d in history_dirs], cfg=cfg)

    os.system("clear 2>/dev/null || true")
    print(f"{BOLD}{MAGENTA}{'=' * 83}{NC}")
    print(f"{BOLD}{MAGENTA}     TRAKTOR PLAYLIST FROM HISTORY FILES - KEY SORTED / DECKS READY        {NC}")
    print(f"{BOLD}{MAGENTA}{'=' * 83}{NC}")
    print(f"  {BOLD}Collection:{NC}   {collection or 'Not found'}")
    for directory in history_dirs:
        print(f"  {BOLD}History:{NC}      {directory}")
    print(f"  {BOLD}History files:{NC} {len(history_files)}")
    print(f"{BLUE}{'-' * 83}{NC}\n")

    if not collection:
        print(f"{YELLOW}No collection.nml found. Set TRAKTOR_COLLECTION_PATH in config.env.{NC}")
        return 1
    if not history_files:
        print(f"{YELLOW}No Traktor History (.nml) files found. Sync the Traktor history first.{NC}")
        return 1

    for index, row in enumerate(history_files, 1):
        print(f"  {BOLD}{CYAN}{index:2d}){NC} {row['datetime']}  {DIM}{row['name']}{NC}")

    print(f"\n  {BOLD}[Q]{NC} Return")
    choice = input(f"Select a history file [1-{len(history_files)}, Q]: ").strip()
    if choice.lower() in ("q", "", "0"):
        return 0
    history = resolve_history_file(choice, [str(d) for d in history_dirs], cfg=cfg)
    if not history:
        print(f"{RED}Invalid history selection.{NC}")
        return 1

    try:
        session, _ = parse_history_file(str(history))
    except Exception as exc:
        print(f"{RED}Could not read {history.name}: {exc}{NC}")
        return 1
    if not session:
        print(f"{RED}No tracks found in {history.name}.{NC}")
        return 1

    sorted_session = sorted(session, key=key_sort_rank)
    os.system("clear 2>/dev/null || true")
    print(f"{BOLD}{CYAN}History: {history.name}  ({len(session)} tracks){NC}\n")
    _print_session(sorted_session)
    print(f"\n{BOLD}{YELLOW}First four tracks in Key Ascending order are ready for Decks A-D.{NC}")
    commit = input("Generate this playlist in Traktor now? [Y/n]: ").strip().lower()
    if commit not in ("", "y", "yes"):
        print(f"{YELLOW}Cancelled.{NC}")
        return 0
    default_name = f"SOF Mix {time.strftime('%Y-%m-%d')}"
    match = re.search(r"history_(\d{4})y(\d{2})m(\d{2})d_(\d{2})h(\d{2})m", history.name)
    if match:
        default_name = (
            f"SOF Mix {match.group(1)}-{match.group(2)}-{match.group(3)} "
            f"({match.group(4)}h{match.group(5)}m)"
        )
    name = input(f"Traktor playlist name [{default_name}]: ").strip() or default_name

    if is_traktor_running():
        print(f"{YELLOW}Traktor is running. It must be closed so collection.nml is not overwritten.{NC}")
        if input("Close Traktor now? [Y/n]: ").strip().lower() in ("", "y", "yes"):
            if not quit_traktor():
                print(f"{RED}Could not close Traktor. Close it manually and try again.{NC}")
                return 1
        else:
            return 1

    import copy as _copy

    ok, message = insert_playlist_into_collection(
        str(collection), name, [_copy.deepcopy(track) for track in sorted_session]
    )
    if ok:
        print(f"\n{BOLD}{GREEN}{message}{NC}")
        print(f"{GREEN}Open Traktor and the playlist will be in the default Playlists folder.{NC}")
        return 0
    print(f"\n{RED}{message}{NC}")
    return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a Traktor playlist from a History .nml session.")
    parser.add_argument("--version-only", action="store_true", help="Print the detected Traktor version and exit")
    parser.add_argument("--list-history", action="store_true", help="List available history .nml files and exit")
    parser.add_argument("--json", action="store_true", help="Machine-readable output for --list-history")
    parser.add_argument("--history-id", "-H", help="History index, file name, ID or path")
    parser.add_argument("--playlist-name", "-n", help="Name of the Traktor playlist")
    parser.add_argument("--collection", help="Path to collection.nml")
    parser.add_argument("--history-dir", action="append", default=[], help="Extra History directory (repeatable)")
    parser.add_argument("--track", "-t", action="append", default=[], help="File to include (repeatable)")
    parser.add_argument("--artist", default=None, help="Create a playlist containing only this artist's tracks")
    parser.add_argument("--list-playlist-tree", action="store_true", help="List playlist folders and the playlists inside them")
    parser.add_argument("--tsv", action="store_true", help="Write --list-playlist-tree or --suggest-artists as tab-separated rows")
    parser.add_argument("--rename-playlist", default=None, help="Path of the playlist to rename (from --list-playlist-tree)")
    parser.add_argument("--remove-playlist", default=None, help="Path of the playlist to remove")
    parser.add_argument("--show-playlist", default=None, help="Print the tracks in one playlist")
    parser.add_argument("--suggest-artists", default=None, help="List collection artist names close to this text")
    parser.add_argument("--collection-status", action="store_true", help="Print the collection path and whether Traktor is running")
    parser.add_argument("--close-traktor", action="store_true", help="Ask Traktor to quit before writing the live collection")
    args = parser.parse_args(argv)

    if args.version_only:
        user_dir = find_traktor_user_dir()
        match = re.search(r"Traktor\s+([0-9][0-9.]*)", str(user_dir or ""))
        print(match.group(1) if match else "3")
        return 0

    if args.list_history:
        rows = list_history_files(500, args.history_dir)
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            for index, row in enumerate(rows, 1):
                print(f"{index:2d}) {row['datetime']}  {row['name']}  ({row['size'] // 1024} KB)")
        return 0 if rows else 1

    artist_result = _dispatch_artist_tools(args)
    if artist_result is not None:
        return artist_result

    if args.track or args.playlist_name or args.history_id:
        if not args.track:
            history = resolve_history_file(args.history_id, args.history_dir)
            if not history:
                print(
                    "A Traktor History file is required. Pass --history-id or sync the history first.",
                    file=sys.stderr,
                )
                return 1
            try:
                session, _ = parse_history_file(str(history))
            except Exception as exc:
                print(f"Could not read {history.name}: {exc}", file=sys.stderr)
                return 1
            paths = [track.get("file") or track.get("basename") for track in session]
            paths = [value for value in paths if value]
        else:
            paths = list(args.track)
            history = resolve_history_file(args.history_id, args.history_dir)
            if args.history_id and not history:
                print(f"Traktor history '{args.history_id}' was not found.", file=sys.stderr)
                return 1

        result = build_traktor_playlist(
            paths,
            history_id=str(history) if history else args.history_id,
            playlist_name=args.playlist_name,
            collection_path=args.collection,
            history_dirs=args.history_dir,
        )
        if args.json:
            serializable = {key: value for key, value in result.items() if key != "tracks"}
            print(json.dumps(serializable, indent=2))
        else:
            print(result["message"])
        return 0 if result.get("ok") else 1

    try:
        return interactive_main()
    except (KeyboardInterrupt, EOFError):
        print("")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
