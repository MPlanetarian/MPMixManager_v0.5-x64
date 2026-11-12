#!/usr/bin/env python3
"""
traktor_playlist_importer.py - Import Traktor playlists into the MP Audio Player

Traktor stores every playlist inside collection.nml. This module reads that
database and turns each Traktor playlist into an MP Audio Player playlist
(.json + .m3u8) inside PLAYLISTS_GENERATED, so the player can open it in
MUSIC mode.

Rules enforced here:
  * Music Folders must be configured first (local drives or SMB shares).
  * Every Traktor playlist is pre-generated in advance; a playlist that has
    not been generated cannot be used by the player.
  * Every file path is resolved against the configured Music Folders and
    validated before it is written to a playlist. Missing tracks are counted
    and never added.
  * Traktor records the volume and directory it saw. When the library lives
    on a different drive or mount point, the user must map the Music Folder
    to the folder that holds the same audio tree. Files are matched by their
    relative path (and then by file name), so a renamed mount point still
    resolves.

Interactive:
    python3 scripts/traktor_playlist_importer.py

Headless:
    python3 scripts/traktor_playlist_importer.py --generate-all
    python3 scripts/traktor_playlist_importer.py --list
    python3 scripts/traktor_playlist_importer.py --generate "My Playlist"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

BOLD, DIM, NC = "\033[1m", "\033[2m", "\033[0m"
RED = "\033[38;2;255;64;64m"
GREEN = "\033[38;2;0;255;127m"
AMBER = "\033[38;2;255;191;0m"
CYAN = "\033[38;2;0;229;255m"
PURPLE = "\033[38;2;176;88;255m"
LINE = "  --------------------------------------------------------------------"
BANNER = "======================================================================"
PREVIEW_PREFIX = "TRAKTOR_"
CATALOG_NAME = "TRAKTOR_PLAYLISTS.json"


def _app_dir() -> Path:
    here = Path(__file__).resolve()
    return here.parent.parent if here.parent.name == "scripts" else here.parent


def _scripts_dir() -> Path:
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    return here


APP_DIR = _app_dir()
SCRIPT_DIR = _scripts_dir()

# The Traktor discovery helpers already exist for the history generator and
# are reused here so one config file drives both features.
try:
    import generate_traktor_playlist_from_history as traktor_history
except Exception:  # pragma: no cover - the module is always shipped
    traktor_history = None

try:
    import music_folders_manager as mfm
except Exception:  # pragma: no cover
    mfm = None


# ---------------------------------------------------------------------------
# Music Folders and generated playlist locations
# ---------------------------------------------------------------------------
def playlists_dir() -> Path:
    if mfm is not None:
        try:
            return Path(mfm.load_music_config()["playlists_dir"])
        except Exception:
            pass
    target = APP_DIR / "PLAYLISTS_GENERATED"
    target.mkdir(parents=True, exist_ok=True)
    return target


def load_folders() -> List[Dict[str, Any]]:
    if mfm is None:
        return []
    try:
        return list(mfm.load_folders(mfm.load_music_config()))
    except Exception:
        return []


def music_folders_ready() -> Tuple[bool, str]:
    """The import must not run until the user has mapped their music folders."""
    if mfm is None:
        return False, "The Music Folders module is unavailable. Reinstall MP Mix Manager."
    folders = load_folders()
    if not folders:
        return False, (
            "No Music Folders are configured. Open MP Mix Manager > Play Tracks from "
            "MUSIC Folders (option 2) > Add Music Folder (option 4) first, then run the "
            "import again."
        )
    return True, f"{len(folders)} Music Folder(s) configured."


def music_folder_index_ready() -> Tuple[bool, List[str]]:
    """A Music Folder needs its scanned playlist before files can be matched."""
    missing: List[str] = []
    for folder in load_folders():
        if mfm is None:
            break
        try:
            status = mfm.check_playlist_status(folder["id"], mfm.load_music_config())
        except Exception:
            status = {"exists": False}
        if not status.get("exists"):
            missing.append(str(folder.get("name") or folder.get("id")))
    return (not missing), missing


def ensure_music_folder_indexes(interactive: bool = True, auto_scan: bool = False) -> bool:
    """Run the existing scanning suite to pre-cache folder indexes when needed."""
    ready, missing = music_folder_index_ready()
    if ready:
        return True
    print(f"\n{AMBER}The following Music Folders have not been scanned yet:{NC}")
    for name in missing:
        print(f"  {DIM}• {name}{NC}")
    print(
        "\nScanned folder indexes are required to match Traktor files to your "
        "library. Generating them now uses the normal Music Folders scan."
    )
    if interactive and not ask_yes("Generate the Music Folder playlists now? [Y/n]: "):
        return False
    if not interactive and not auto_scan:
        return False
    if mfm is None:
        return False
    print(f"\n{BOLD}{CYAN}Pre-scanning configured Music Folders...{NC}\n", flush=True)
    try:
        mfm.generate_advance_playlists(
            mfm.ALL_TARGET,
            config=mfm.load_music_config(),
            interactive=interactive,
            folders=load_folders(),
        )
    except Exception as exc:
        print(f"{RED}Music Folder scan failed: {exc}{NC}")
        return False
    ready, _missing = music_folder_index_ready()
    return ready


def generated_playlists() -> List[Dict[str, Any]]:
    """Playlists that were generated in advance and can be used by the player."""
    catalog = playlists_dir() / CATALOG_NAME
    rows: List[Dict[str, Any]] = []
    if not catalog.is_file():
        return rows
    try:
        data = json.loads(catalog.read_text(encoding="utf-8"))
    except Exception:
        return rows
    items = data.get("playlists") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return rows
    for item in items:
        if not isinstance(item, dict):
            continue
        json_path = playlists_dir() / str(item.get("json", ""))
        m3u_path = playlists_dir() / str(item.get("m3u8", ""))
        if not json_path.is_file() or not m3u_path.is_file():
            continue
        row = dict(item)
        row["json_path"] = str(json_path)
        row["m3u8_path"] = str(m3u_path)
        rows.append(row)
    rows.sort(key=lambda r: str(r.get("name", "")).casefold())
    return rows


# ---------------------------------------------------------------------------
# Small terminal helpers
# ---------------------------------------------------------------------------
def clear_screen() -> None:
    sys.stdout.write("\033[2J\033[3J\033[H")
    sys.stdout.flush()


def ask(prompt: str) -> str:
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def ask_yes(prompt: str, default: bool = True) -> bool:
    answer = ask(prompt).strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def pause(message: str = "Press Enter to continue...") -> None:
    ask(f"\n{BOLD}{message}{NC}")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(name or "")).strip("_")
    return slug or "PLAYLIST"


# ---------------------------------------------------------------------------
# Traktor collection parsing
# ---------------------------------------------------------------------------
def find_collection_path(cfg: Optional[Dict[str, str]] = None) -> Optional[Path]:
    if traktor_history is not None:
        try:
            found = traktor_history.find_collection_path(cfg)
            if found:
                return Path(found)
        except Exception:
            pass
    # Fall back to the configured path without the history helper.
    for key in ("TRAKTOR_COLLECTION_PATH",):
        value = os.path.expanduser(os.path.expandvars(os.environ.get(key, "")))
        if value and Path(value).is_file():
            return Path(value).resolve()
    search_roots = [
        Path.home() / "Documents" / "Native Instruments",
        Path.home() / "Library" / "Application Support" / "Native Instruments",
        APP_DIR / "MIX_ARCHIVE",
    ]
    for root in search_roots:
        if not root.is_dir():
            continue
        for match in root.glob("Traktor*/collection.nml"):
            return match.resolve()
    return None


def _decode(value: str) -> str:
    try:
        return urllib.parse.unquote(str(value or ""))
    except Exception:
        return str(value or "")


def _track_metadata(entry: ET.Element) -> Dict[str, Any]:
    location = entry.find("LOCATION")
    volume = _decode(location.attrib.get("VOLUME", "")) if location is not None else ""
    directory = _decode(location.attrib.get("DIR", "")) if location is not None else ""
    filename = _decode(location.attrib.get("FILE", "")) if location is not None else ""
    info = entry.find("INFO")
    duration = 0
    if info is not None:
        try:
            duration = int(float(info.attrib.get("PLAYTIME", 0) or 0))
        except (TypeError, ValueError):
            duration = 0
    key = str(info.attrib.get("KEY", "") or "") if info is not None else ""
    return {
        "title": str(entry.attrib.get("TITLE", "") or "").strip(),
        "artist": str(entry.attrib.get("ARTIST", "") or "").strip(),
        "volume": volume,
        "dir": directory,
        "file": filename,
        "basename": os.path.basename(filename.replace("\\", "/")).casefold(),
        "duration_sec": duration,
        "key": key,
        "pkey": f"{volume}{directory}{filename}",
    }


def _track_from_pkey(key: str) -> Dict[str, Any]:
    """Rebuild a LOCATION record from a PRIMARYKEY when it is not in COLLECTION.

    Traktor writes keys as VOLUME + DIR + FILE, where DIR uses '/:' separators.
    An empty volume leaves a leading '/:' (macOS), while a Windows drive is
    'C:/:...'. Splitting on '/:' therefore keeps one volume chunk whenever the
    key does not start with the separator.
    """
    raw = str(key or "")
    chunks = raw.split("/:")
    volume = ""
    if chunks and chunks[0] == "":
        # No volume: the key starts with "/:" (macOS system drive).
        parts = [chunk for chunk in chunks[1:] if chunk]
    elif len(chunks) > 1:
        volume = chunks[0].rstrip("/")
        parts = [chunk for chunk in chunks[1:] if chunk]
    else:
        parts = [chunk for chunk in chunks if chunk]
    if not parts:
        return {}
    filename = parts[-1]
    directory = "/" + "/".join(parts[:-1]) + "/" if len(parts) > 1 else "/"
    return {
        "title": os.path.splitext(filename.replace("\\", "/"))[0],
        "artist": "",
        "volume": volume,
        "dir": directory,
        "file": filename,
        "basename": os.path.basename(filename.replace("\\", "/")).casefold(),
        "duration_sec": 0,
        "key": "",
        "pkey": raw,
    }


def _dir_parts(traktor: Dict[str, Any]) -> List[str]:
    """Traktor writes DIR with '/:' separators on macOS and Windows."""
    directory = str(traktor.get("dir") or "").replace("/:", "/").replace("\\:", "/")
    directory = directory.replace("\\", "/")
    return [part for part in directory.split("/") if part and part != ":"]


def _direct_candidates(traktor: Dict[str, Any]) -> List[str]:
    """Filesystem paths Traktor itself would use on this platform."""
    volume = str(traktor.get("volume") or "")
    parts = _dir_parts(traktor)
    filename = str(traktor.get("file") or "")
    if not filename:
        return []
    tail = os.path.join(*parts) if parts else ""
    candidates: List[str] = []

    if re.match(r"^[A-Za-z]:$", volume):
        candidates.append(os.path.join(volume + os.sep, tail, filename))
        candidates.append(volume + "/" + tail.replace(os.sep, "/") + "/" + filename)
    elif volume and volume not in ("Macintosh HD", "Macintosh HD/", "/"):
        candidates.append(os.path.join("/Volumes", volume, tail, filename))
        candidates.append(os.path.join(volume, tail, filename))
    if parts:
        candidates.append(os.path.join(os.sep, tail, filename))
    candidates.append(os.path.join(tail, filename) if tail else filename)
    return list(dict.fromkeys(os.path.expanduser(path) for path in candidates if path))


def _playlist_names(root: ET.Element) -> List[Tuple[str, List[str]]]:
    """Every playlist in the database, including playlists inside folders."""
    found: List[Tuple[str, List[str]]] = []
    playlists = root.find("PLAYLISTS")
    if playlists is None:
        return found

    def walk(node: ET.Element, parents: List[str]) -> None:
        if node.attrib.get("TYPE") == "PLAYLIST":
            name = str(node.attrib.get("NAME", "") or "").strip()
            content = node.find("PLAYLIST")
            keys: List[str] = []
            if content is not None:
                for entry in content.findall("ENTRY"):
                    primary = entry.find("PRIMARYKEY")
                    if primary is not None and primary.attrib.get("KEY"):
                        keys.append(primary.attrib["KEY"])
            if name:
                found.append(("/".join(parents + [name]), keys))
            return
        subnodes = node.find("SUBNODES")
        children = subnodes.findall("NODE") if subnodes is not None else node.findall("NODE")
        folder_name = str(node.attrib.get("NAME", "") or "").strip()
        next_parents = parents + ([folder_name] if folder_name and folder_name != "$ROOT" else [])
        for child in children:
            walk(child, next_parents)

    for top in playlists.findall("NODE"):
        walk(top, [])
    return found


def load_traktor_database(collection_path: Path) -> Tuple[Dict[str, Dict[str, Any]], List[Tuple[str, List[str]]]]:
    root = ET.parse(str(collection_path)).getroot()
    by_key: Dict[str, Dict[str, Any]] = {}
    collection = root.find("COLLECTION")
    if collection is not None:
        for entry in collection.findall("ENTRY"):
            track = _track_metadata(entry)
            if track["pkey"]:
                by_key[track["pkey"]] = track
    return by_key, _playlist_names(root)


def list_traktor_playlists(collection_path: Path) -> List[Dict[str, Any]]:
    by_key, playlists = load_traktor_database(collection_path)
    rows: List[Dict[str, Any]] = []
    for name, keys in playlists:
        resolved_keys = [key for key in keys if key in by_key]
        rows.append(
            {
                "name": name,
                "slug": _slugify(name),
                "tracks": len(keys),
                "in_collection": len(resolved_keys),
                "keys": keys,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Library indexing and path matching
# ---------------------------------------------------------------------------
def _folder_index_tracks() -> List[Dict[str, Any]]:
    """Tracks from every scanned Music Folder playlist (the live library)."""
    tracks: List[Dict[str, Any]] = []
    seen: set = set()
    directory = playlists_dir()
    if not directory.is_dir():
        return tracks
    files = sorted(directory.glob("DJ_Tracks_*.json"))
    per_folder = [p for p in files if not p.name.upper().startswith("DJ_TRACKS_ALL_MUSIC_FOLDERS")]
    # Per-folder indexes carry the subpath used for matching. A library built
    # before those existed only has the combined playlist, so fall back to it.
    for path in (per_folder or files):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for row in data:
            if not isinstance(row, dict) or not row.get("path"):
                continue
            full = str(row["path"])
            key = os.path.normcase(full)
            if key in seen:
                continue
            seen.add(key)
            rel = str(row.get("subpath") or row.get("name") or "")
            parts = [part for part in rel.replace("\\", "/").split("/") if part]
            base_name = os.path.basename(full)
            if parts and parts[-1].casefold() == base_name.casefold():
                parts = parts[:-1]
            tracks.append(
                {
                    "path": full,
                    "name": str(row.get("name") or base_name),
                    "title": str(row.get("title") or os.path.splitext(base_name)[0]),
                    "folder": str(row.get("folder") or ""),
                    "ext": str(row.get("ext") or os.path.splitext(full)[1].lstrip(".")).upper(),
                    "duration_fmt": str(row.get("duration_fmt") or "00:00"),
                    "size_mb": row.get("size_mb") or 0.0,
                    "mtime": row.get("mtime") or time.time(),
                    "subpath": rel,
                    "parts": parts,
                    "basename": base_name.casefold(),
                }
            )
    return tracks


def _suffix_score(traktor_parts: Sequence[str], library_parts: Sequence[str]) -> int:
    score = 0
    for left, right in zip(reversed(list(traktor_parts)), reversed(list(library_parts))):
        if left.casefold() != right.casefold():
            break
        score += 1
    return score


def _is_remote_path(path: str) -> bool:
    raw = str(path or "")
    if raw.startswith(("\\\\", "//", "smb://")):
        return True
    if mfm is not None:
        try:
            mount_root = str(mfm.load_music_config()["mount_root"])
            return raw.startswith(mount_root)
        except Exception:
            pass
    return False


def validate_track_path(path: str) -> bool:
    """True only when the audio file exists and is a non-empty regular file."""
    if not path:
        return False
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def ensure_track_playable(path: str) -> bool:
    """Validate a path, mounting its SMB Music Folder when it is offline."""
    if validate_track_path(path):
        return True
    if not _is_remote_path(path) or mfm is None:
        return False
    try:
        config = mfm.load_music_config()
        for folder in load_folders():
            if folder.get("type") != "smb":
                continue
            mount_dir = str(mfm.smb_mount_dir(folder, config))
            if not str(path).startswith(mount_dir):
                continue
            ok, _message = mfm.connect_folder(folder, config, interactive=False)
            if ok and validate_track_path(path):
                return True
    except Exception:
        pass
    return False


class TraktorMatcher:
    """Resolve Traktor LOCATION records to files that exist on this machine."""

    def __init__(self, library: Sequence[Dict[str, Any]]):
        self.library = list(library)
        self.by_basename: Dict[str, List[Dict[str, Any]]] = {}
        for track in self.library:
            self.by_basename.setdefault(track["basename"], []).append(track)

    def resolve(self, traktor: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
        traktor_parts = _dir_parts(traktor)
        basename = str(traktor.get("basename") or "")
        candidates = self.by_basename.get(basename, [])

        best: Optional[Dict[str, Any]] = None
        best_score = -1
        for track in candidates:
            score = _suffix_score(traktor_parts, track["parts"])
            if score > best_score:
                best, best_score = track, score
        if best is not None and (best_score > 0 or len(candidates) == 1):
            if validate_track_path(best["path"]):
                return best, "library-index"
            if _is_remote_path(best["path"]):
                # The folder index came from a successful share scan, so the
                # file exists on the share even while the mount is offline.
                return best, "library-index-remote"

        for candidate in _direct_candidates(traktor):
            if validate_track_path(candidate):
                return self._library_row_for(candidate, traktor), "direct-path"
        return None, "not-found"

    def _library_row_for(self, path: str, traktor: Dict[str, Any]) -> Dict[str, Any]:
        for track in self.library:
            if os.path.normcase(track["path"]) == os.path.normcase(path):
                return track
        name = os.path.basename(path)
        return {
            "path": path,
            "name": name,
            "title": str(traktor.get("title") or os.path.splitext(name)[0]),
            "folder": "",
            "ext": os.path.splitext(name)[1].lstrip(".").upper(),
            "duration_fmt": "00:00",
            "size_mb": 0.0,
            "mtime": time.time(),
            "subpath": name,
            "parts": [name],
            "basename": name.casefold(),
        }


def _player_track(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": row.get("name") or os.path.basename(str(row.get("path") or "")),
        "title": row.get("title") or os.path.splitext(os.path.basename(str(row.get("path") or "")))[0],
        "path": str(row.get("path") or ""),
        "folder": row.get("folder") or "",
        "subpath": row.get("subpath") or "",
        "ext": row.get("ext") or os.path.splitext(str(row.get("path") or ""))[1].lstrip(".").upper(),
        "size_mb": row.get("size_mb") or 0.0,
        "duration_fmt": row.get("duration_fmt") or "00:00",
        "duration_sec": row.get("duration_sec") or 0,
        "mtime": row.get("mtime") or time.time(),
    }


def _write_playlist(name: str, slug: str, tracks: Sequence[Dict[str, Any]]) -> Tuple[Path, Path]:
    target = playlists_dir()
    target.mkdir(parents=True, exist_ok=True)
    stem = f"{PREVIEW_PREFIX}{slug}"
    json_path, m3u_path = target / f"{stem}.json", target / f"{stem}.m3u8"
    tmp = json_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(list(tracks), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(json_path)
    lines = ["#EXTM3U", f"#PLAYLIST:Traktor - {name}"]
    for track in tracks:
        lines.append(f"#EXTINF:-1,{track.get('title') or track.get('name')}")
        lines.append(str(track.get("path") or ""))
    m3u_tmp = m3u_path.with_suffix(".m3u8.tmp")
    m3u_tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    m3u_tmp.replace(m3u_path)
    return json_path, m3u_path


def _save_catalog(rows: Iterable[Dict[str, Any]]) -> None:
    catalog = playlists_dir() / CATALOG_NAME
    payload = {
        "version": 1,
        "updated": time.time(),
        "playlists": list(rows),
    }
    tmp = catalog.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(catalog)


def _load_catalog_rows() -> List[Dict[str, Any]]:
    catalog = playlists_dir() / CATALOG_NAME
    if not catalog.is_file():
        return []
    try:
        data = json.loads(catalog.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = data.get("playlists") if isinstance(data, dict) else data
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
def generate_all(collection_path: Optional[Path] = None, interactive: bool = True) -> Dict[str, Any]:
    ready, reason = music_folders_ready()
    if not ready:
        print(f"\n{RED}✖ Traktor import is disabled.{NC}\n  {reason}")
        return {"ok": False, "reason": "no_music_folders", "message": reason}

    collection = Path(collection_path) if collection_path else find_collection_path()
    if not collection or not Path(collection).is_file():
        message = (
            "Traktor collection.nml was not found. Set TRAKTOR_COLLECTION_PATH in "
            "config.env to the collection.nml that Traktor uses."
        )
        print(f"\n{RED}✖ {message}{NC}")
        return {"ok": False, "reason": "no_collection", "message": message}

    if not ensure_music_folder_indexes(interactive=interactive, auto_scan=not interactive):
        message = "Music Folder playlists were not generated, so Traktor files cannot be matched."
        print(f"\n{AMBER}{message}{NC}")
        return {"ok": False, "reason": "no_folder_index", "message": message}

    try:
        by_key, playlists = load_traktor_database(collection)
    except Exception as exc:
        message = f"Could not parse {collection}: {exc}"
        print(f"\n{RED}✖ {message}{NC}")
        return {"ok": False, "reason": "parse", "message": message}

    print(f"\n{BOLD}{PURPLE}{BANNER}{NC}")
    print(f"{BOLD}{PURPLE}     IMPORTING EVERY TRAKTOR PLAYLIST INTO MP AUDIO PLAYER        {NC}")
    print(f"{BOLD}{PURPLE}{BANNER}{NC}")
    print(f"  {BOLD}Collection:{NC} {collection}")
    print(f"  {BOLD}Playlists :{NC} {len(playlists)}")
    print(f"  {BOLD}Output    :{NC} {playlists_dir()}")
    print(f"{CYAN}{LINE}{NC}\n", flush=True)

    library = _folder_index_tracks()
    matcher = TraktorMatcher(library)
    # A full regeneration rebuilds the catalog from the current collection, so a
    # playlist deleted in Traktor stops being offered by the player.
    by_slug: Dict[str, Dict[str, Any]] = {}

    generated = 0
    totals = {"tracks": 0, "resolved": 0, "missing": 0}
    used_slugs: Dict[str, int] = {}
    run_slugs: set = set()
    for index, (name, keys) in enumerate(playlists, 1):
        # Two Traktor playlists can normalise to the same slug ("House/Dub"
        # and "House Dub"). Keep both files instead of overwriting one.
        base_slug = _slugify(name)
        count = used_slugs.get(base_slug, 0) + 1
        used_slugs[base_slug] = count
        slug = base_slug if count == 1 else f"{base_slug}_{count}"
        run_slugs.add(slug)
        resolved: List[Dict[str, Any]] = []
        seen: set = set()
        missing = 0
        for key in keys:
            traktor = by_key.get(key) or _track_from_pkey(key)
            if not traktor:
                missing += 1
                continue
            row, _how = matcher.resolve(traktor)
            if row is None:
                missing += 1
                continue
            path = str(row["path"])
            norm = os.path.normcase(path)
            if norm in seen:
                continue
            seen.add(norm)
            track = _player_track(row)
            track["title"] = str(traktor.get("title") or track["title"])
            track["name"] = os.path.basename(path)
            if traktor.get("duration_sec") and not track.get("duration_sec"):
                track["duration_sec"] = traktor["duration_sec"]
            resolved.append(track)

        totals["tracks"] += len(keys)
        totals["resolved"] += len(resolved)
        totals["missing"] += missing
        print(
            f"  {BOLD}{CYAN}[{index:2d}/{len(playlists)}]{NC} {name[:52]:<54} "
            f"{GREEN}{len(resolved):4d} ready{NC}  "
            f"{RED if missing else DIM}{missing:4d} missing{NC}",
            flush=True,
        )
        if not resolved:
            print(f"        {DIM}Not generated: no validated audio files were found.{NC}")
            continue
        json_path, m3u_path = _write_playlist(name, slug, resolved)
        by_slug[slug] = {
            "name": name,
            "slug": slug,
            "json": json_path.name,
            "m3u8": m3u_path.name,
            "tracks": len(resolved),
            "missing": missing,
            "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        generated += 1

    rows = sorted(by_slug.values(), key=lambda r: str(r.get("name", "")).casefold())
    _save_catalog(rows)
    # Remove playlists that no longer exist in Traktor. A playlist that exists
    # but produced no valid files this run keeps its previous files: its share
    # may simply be offline. Only files with the importer's own TRAKTOR_ prefix
    # are considered, so nothing else in PLAYLISTS_GENERATED is touched.
    keep = {row.get("json") for row in rows} | {row.get("m3u8") for row in rows}
    for path in playlists_dir().glob(f"{PREVIEW_PREFIX}*"):
        if path.name in keep or path.name == CATALOG_NAME:
            continue
        suffix = path.suffix.lower()
        if suffix not in (".json", ".m3u8", ".m3u"):
            continue
        stem = path.stem
        # Slugs from this run, including playlists with no valid files today.
        if any(stem == f"{PREVIEW_PREFIX}{slug}" or stem.startswith(f"{PREVIEW_PREFIX}{slug}")
               for slug in run_slugs):
            continue
        try:
            path.unlink()
        except OSError:
            pass
    print(f"\n{GREEN}✔ Generated {generated} playlist(s).{NC}")
    print(
        f"  {totals['resolved']:,} files validated, {totals['missing']:,} track(s) "
        f"skipped because they were not found under the configured Music Folders."
    )
    if totals["missing"]:
        print(
            f"  {AMBER}If a whole playlist is missing, map the Music Folder that holds the "
            f"same audio tree Traktor used. Traktor expects the same volume and folder "
            f"path; a different drive or mount point must be added as that Music Folder.{NC}"
        )
    return {
        "ok": True,
        "reason": "written",
        "collection": str(collection),
        "playlists": generated,
        "resolved": totals["resolved"],
        "missing": totals["missing"],
        "catalog": str(playlists_dir() / CATALOG_NAME),
    }


def generate_one(name: str, collection_path: Optional[Path] = None) -> Dict[str, Any]:
    ready, reason = music_folders_ready()
    if not ready:
        print(f"\n{RED}✖ Traktor import is disabled.{NC}\n  {reason}")
        return {"ok": False, "reason": "no_music_folders", "message": reason}
    collection = Path(collection_path) if collection_path else find_collection_path()
    if not collection or not Path(collection).is_file():
        return {"ok": False, "reason": "no_collection", "message": "collection.nml was not found."}
    by_key, playlists = load_traktor_database(collection)
    wanted = str(name or "").strip().casefold()
    match = next(((n, k) for n, k in playlists if n.casefold() == wanted or _slugify(n) == _slugify(name)), None)
    if match is None:
        return {"ok": False, "reason": "not_found", "message": f"Playlist '{name}' was not found in Traktor."}
    if not ensure_music_folder_indexes(interactive=True):
        return {"ok": False, "reason": "no_folder_index", "message": "Music Folders were not scanned."}
    playlist_name, keys = match
    matcher = TraktorMatcher(_folder_index_tracks())
    resolved: List[Dict[str, Any]] = []
    missing = 0
    seen: set = set()
    for key in keys:
        traktor = by_key.get(key) or _track_from_pkey(key)
        if not traktor:
            missing += 1
            continue
        row, _how = matcher.resolve(traktor)
        if row is None:
            missing += 1
            continue
        if os.path.normcase(row["path"]) in seen:
            continue
        seen.add(os.path.normcase(row["path"]))
        track = _player_track(row)
        track["title"] = str(traktor.get("title") or track["title"])
        track["name"] = os.path.basename(row["path"])
        resolved.append(track)
    if not resolved:
        return {"ok": False, "reason": "no_match", "message": "No validated audio files were found for that playlist."}
    slug = _slugify(playlist_name)
    json_path, m3u_path = _write_playlist(playlist_name, slug, resolved)
    rows = [row for row in _load_catalog_rows() if str(row.get("slug")) != slug]
    rows.append(
        {
            "name": playlist_name,
            "slug": slug,
            "json": json_path.name,
            "m3u8": m3u_path.name,
            "tracks": len(resolved),
            "missing": missing,
            "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    _save_catalog(sorted(rows, key=lambda r: str(r.get("name", "")).casefold()))
    return {
        "ok": True,
        "playlist": playlist_name,
        "json_path": str(json_path),
        "m3u8_path": str(m3u_path),
        "tracks": len(resolved),
        "missing": missing,
    }


# ---------------------------------------------------------------------------
# Interactive menu
# ---------------------------------------------------------------------------
def _print_warning_banner(collection: Optional[Path]) -> None:
    print(f"{BOLD}{PURPLE}{BANNER}{NC}")
    print(f"{BOLD}{PURPLE}      IMPORT TRAKTOR PLAYLISTS INTO MP AUDIO PLAYER              {NC}")
    print(f"{BOLD}{PURPLE}{BANNER}{NC}")
    print(f"  {BOLD}Traktor collection:{NC} {collection or 'not found'}")
    folders = load_folders()
    print(f"  {BOLD}Music Folders     :{NC} {len(folders)} configured")
    for folder in folders:
        kind = "SMB" if folder.get("type") == "smb" else "LOCAL"
        try:
            shown = mfm.display_path(folder) if mfm is not None else folder.get("path", "")
        except Exception:
            shown = folder.get("path", "")
        print(f"    {DIM}[{kind:<5}] {str(folder.get('name') or folder.get('id'))[:18]:<20} {shown}{NC}")
    generated = generated_playlists()
    print(f"  {BOLD}Generated         :{NC} {len(generated)} Traktor playlist(s) ready in the player")
    print(f"{CYAN}{LINE}{NC}")
    print(
        f"  {AMBER}Important:{NC} Traktor records the volume and folder path it used. The audio "
        f"files must\n  exist under the configured Music Folders. If they live on a different "
        f"drive or\n  mount point, add that folder as a Music Folder so the same audio tree is "
        f"mapped."
    )
    print(f"{CYAN}{LINE}{NC}\n")


def launch_generated_playlist(row: Dict[str, Any]) -> bool:
    """Open one generated Traktor playlist in the MP Audio Player."""
    json_path = str(row.get("json_path") or "")
    if not json_path or not Path(json_path).is_file():
        print(f"{RED}✖ That playlist has not been generated yet.{NC}")
        return False
    # Re-check every file before the player loads the playlist.
    try:
        tracks = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"{RED}✖ Could not read the generated playlist: {exc}{NC}")
        return False
    missing = [t for t in tracks if not validate_track_path(str(t.get("path") or ""))]
    if missing:
        print(
            f"{AMBER}⚠ {len(missing)} of {len(tracks)} file(s) are not available right now. "
            f"Regenerate this playlist after mounting its Music Folders.{NC}"
        )
        if not ask_yes("Open it with only the available files? [y/N]: ", default=False):
            return False
        tracks = [t for t in tracks if t not in missing]
        if not tracks:
            print(f"{RED}✖ No playable files remain.{NC}")
            return False
    player = APP_DIR / "MP_Audio_Player.py"
    if not player.is_file():
        player = APP_DIR / "scripts" / "MP_Audio_Player.py"
    if not player.is_file():
        print(f"{RED}✖ MP_Audio_Player.py was not found.{NC}")
        return False
    import subprocess

    subprocess.run(
        [sys.executable, str(player), "--interactive", "--music-mode",
         "--music-folder", "ALL", "--playlist", json_path]
    )
    return True


def interactive_main() -> int:
    collection = find_collection_path()
    while True:
        folders = load_folders()
        generated = generated_playlists()
        clear_screen()
        _print_warning_banner(collection)
        if not folders:
            print(f"{RED}✖ No Music Folders are configured.{NC}")
            print(
                "  Add them in MP Mix Manager > Play Tracks from MUSIC Folders > "
                "Add Music Folder.\n  The import cannot run until at least one Music "
                "Folder exists."
            )
            pause()
            return 1
        print(f"{BOLD}Choose an option:{NC}")
        print(f"  {BOLD}{CYAN}1){NC} Pre-generate ALL Traktor playlists in advance {DIM}(recommended){NC}")
        print(f"  {BOLD}{CYAN}2){NC} Generate one Traktor playlist")
        print(f"  {BOLD}{CYAN}3){NC} List playlists already generated for the player")
        print(f"  {BOLD}{CYAN}4){NC} Change / detect the Traktor collection.nml")
        print(f"  {BOLD}{CYAN}r){NC} Rescan Music Folders, then regenerate every playlist")
        print(f"  {BOLD}{CYAN}0){NC} Return\n")
        choice = ask("Enter choice: ").strip()
        if choice in ("0", "q", "Q", ""):
            return 0
        if choice == "1":
            generate_all(collection)
            pause()
        elif choice == "2":
            if not collection:
                pause("Set the Traktor collection path first.")
                continue
            try:
                playlists = list_traktor_playlists(collection)
            except Exception as exc:
                print(f"{RED}Could not read the collection: {exc}{NC}")
                pause()
                continue
            if not playlists:
                pause("Traktor has no playlists in this collection.")
                continue
            for index, row in enumerate(playlists, 1):
                ready = any(g.get("name") == row["name"] for g in generated)
                badge = f"{GREEN}generated{NC}" if ready else f"{DIM}not generated{NC}"
                print(f"  {BOLD}{CYAN}{index:2d}){NC} {row['name'][:48]:<50} {row['tracks']:4d} tracks  {badge}")
            pick = ask("\nPlaylist number or name (Enter cancels): ").strip()
            if not pick:
                continue
            if pick.isdigit() and 1 <= int(pick) <= len(playlists):
                pick = playlists[int(pick) - 1]["name"]
            result = generate_one(pick, collection)
            print(
                f"\n{GREEN}✔ Generated {result['playlist']} ({result['tracks']} validated tracks, "
                f"{result['missing']} missing).{NC}"
                if result.get("ok")
                else f"\n{RED}✖ {result.get('message')}{NC}"
            )
            pause()
        elif choice == "3":
            if not generated:
                pause("No Traktor playlists have been generated yet. Choose option 1 first.")
                continue
            print()
            for index, row in enumerate(generated, 1):
                print(
                    f"  {BOLD}{CYAN}{index:2d}){NC} {str(row.get('name'))[:48]:<50} "
                    f"{int(row.get('tracks') or 0):4d} tracks  {DIM}{row.get('generated', '')}{NC}"
                )
            print(f"\n  {DIM}Only generated playlists are listed. Generate all first (option 1).{NC}")
            pick = ask("\nOpen a playlist in MP Audio Player (number, Enter cancels): ").strip()
            if pick.isdigit() and 1 <= int(pick) <= len(generated):
                launch_generated_playlist(generated[int(pick) - 1])
                pause()
            else:
                pause()
        elif choice == "4":
            typed = ask("Path to collection.nml (empty = auto-detect): ").strip().strip('"').strip("'")
            if typed:
                candidate = Path(os.path.expanduser(typed))
                if candidate.is_file():
                    collection = candidate.resolve()
                    print(f"{GREEN}✔ Using {collection}{NC}")
                else:
                    print(f"{RED}✖ Not found: {candidate}{NC}")
            else:
                collection = find_collection_path()
                print(f"{GREEN}✔ Detected: {collection or 'nothing'}{NC}")
            pause()
        elif choice.lower() == "r":
            if mfm is not None:
                mfm.generate_advance_playlists(
                    mfm.ALL_TARGET,
                    config=mfm.load_music_config(),
                    interactive=True,
                    folders=load_folders(),
                )
            generate_all(collection)
            pause()


def mutating_main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Import Traktor playlists into MP Audio Player")
    parser.add_argument("--generate-all", action="store_true", help="Pre-generate every Traktor playlist")
    parser.add_argument("--generate", metavar="NAME", help="Generate one Traktor playlist by name")
    parser.add_argument("--list", action="store_true", help="List generated Traktor playlists as JSON")
    parser.add_argument("--list-traktor", action="store_true", help="List playlists in collection.nml as JSON")
    parser.add_argument("--collection", metavar="PATH", help="Path to collection.nml")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)
    collection = Path(os.path.expanduser(args.collection)).resolve() if args.collection else None

    if args.list:
        rows = generated_playlists()
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            for row in rows:
                print(f"{row.get('name')}\t{row.get('tracks')} tracks\t{row.get('json_path')}")
        return 0
    if args.list_traktor:
        path = collection or find_collection_path()
        if not path or not Path(path).is_file():
            print("collection.nml was not found.", file=sys.stderr)
            return 1
        rows = list_traktor_playlists(Path(path))
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            for row in rows:
                print(f"{row['name']}\t{row['tracks']} tracks")
        return 0
    if args.generate:
        result = generate_one(args.generate, collection)
        print(json.dumps(result, indent=2) if args.json else result.get("message", ""))
        return 0 if result.get("ok") else 1
    if args.generate_all:
        result = generate_all(collection, interactive=sys.stdin.isatty())
        print(json.dumps(result, indent=2) if args.json else "")
        return 0 if result.get("ok") else 1
    try:
        return interactive_main()
    except (KeyboardInterrupt, EOFError):
        print("")
        return 130


if __name__ == "__main__":
    raise SystemExit(mutating_main())
